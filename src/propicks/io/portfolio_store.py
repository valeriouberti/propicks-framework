"""Persistenza e mutazioni del portafoglio — backend SQLite.

Source of truth: tabelle ``positions`` + ``portfolio_meta`` in SQLite. Le
API pubbliche restano identiche a quelle dei file JSON precedenti:

- ``load_portfolio()`` → dict con stessa forma ``{positions, cash, initial_capital, last_updated}``
- ``add_position(portfolio, ...)`` → accetta + muta il dict in-process per
  compatibilità con pattern load→mutate→save dei caller; persiste al DB
- ``close_position``, ``remove_position``, ``update_position``, ``unrealized_pl``
  stesse firme.

Differenza concettuale vs JSON: **ogni mutazione persiste subito** al DB via
transazione. Il dict in memoria è una view che può essere ricaricata con
``load_portfolio()``. I test che fanno multiple mutazioni sullo stesso dict
devono sincronizzare il dict con il DB, o ri-caricare dopo ogni chiamata.

``initial_capital`` è il capitale di riferimento per i display/metrics
(header dashboard, sidebar invariants). Non influisce sui calcoli di sizing,
che usano ``portfolio_value(portfolio) = cash + sum(shares*entry)`` come
denominatore. Se assente viene inizializzato a ``config.CAPITAL``.
"""

from __future__ import annotations

from datetime import datetime

from propicks.config import (
    CAPITAL,
    CONTRA_MAX_AGGREGATE_EXPOSURE_PCT,
    CONTRA_MAX_LOSS_PER_TRADE_PCT,
    CONTRA_MAX_POSITION_SIZE_PCT,
    CONTRA_MAX_POSITIONS,
    ETF_MAX_AGGREGATE_EXPOSURE_PCT,
    ETF_MAX_POSITION_SIZE_PCT,
    STOCK_MAX_AGGREGATE_EXPOSURE_PCT,
    THEMATIC_ETFS,
    THEMATIC_MAX_POSITION_SIZE_PCT,
    THEMATIC_MAX_POSITIONS,
    THEMATIC_PARENT_AGGREGATE_CAP_PCT,
    THEMATIC_STOP_LOSS_PCT,
    DATE_FMT,
    EARNINGS_HARD_GATE_DAYS,
    MAX_LOSS_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
    MIN_SCORE_CLAUDE,
    MIN_SCORE_TECH,
)
from propicks.domain.sizing import (
    contrarian_aggregate_exposure,
    contrarian_position_count,
    etf_aggregate_exposure,
    is_contrarian_position,
    is_etf_position,
    is_etf_rotation_position,
    is_stock_position,
    is_thematic_position,
    stock_aggregate_exposure,
    thematic_parent_aggregate,
    thematic_position_count,
    portfolio_value,
)
from propicks.domain.validation import validate_scores
from propicks.io.db import connect, meta_set_many, transaction

# ---------------------------------------------------------------------------
# Row ↔ dict converters
# ---------------------------------------------------------------------------
_POSITION_FIELDS = (
    "entry_price", "entry_date", "shares", "stop_loss", "target",
    "highest_price_since_entry", "trailing_enabled",
    "strategy", "score_claude", "score_tech", "catalyst",
)


def _row_to_position_dict(row) -> dict:
    """Converte una riga della tabella positions nel dict legacy-compatibile."""
    # Currency safety: column potrebbe essere assente in DB pre-migration o
    # NULL su row legacy → fallback EUR.
    try:
        currency = row["currency"] or "EUR"
    except (IndexError, KeyError):
        currency = "EUR"
    return {
        "entry_price": row["entry_price"],
        "entry_date": row["entry_date"],
        "shares": row["shares"],
        "stop_loss": row["stop_loss"],
        "target": row["target"],
        "highest_price_since_entry": row["highest_price_since_entry"],
        "trailing_enabled": bool(row["trailing_enabled"]),
        "strategy": row["strategy"],
        "score_claude": row["score_claude"],
        "score_tech": row["score_tech"],
        "catalyst": row["catalyst"],
        "currency": currency,
    }


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------
def load_portfolio() -> dict:
    """Carica il portafoglio dal DB e ritorna il dict legacy-compatibile.

    Schema ritornato:
        {"positions": {TICKER: {...}}, "cash": float, "initial_capital": float,
         "last_updated": str|None}

    Se il DB è vuoto (prima esecuzione post-migration o nuovo install), ritorna
    un portfolio default con ``cash = initial_capital = config.CAPITAL``.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY ticker"
        ).fetchall()
        meta_rows = conn.execute(
            "SELECT key, value FROM portfolio_meta"
        ).fetchall()
    finally:
        conn.close()

    meta = {r["key"]: r["value"] for r in meta_rows}
    cash = float(meta.get("cash") or CAPITAL)
    initial_capital = float(meta.get("initial_capital") or CAPITAL)
    last_updated = meta.get("last_updated") or None

    positions = {row["ticker"]: _row_to_position_dict(row) for row in rows}

    return {
        "positions": positions,
        "cash": cash,
        "initial_capital": initial_capital,
        "last_updated": last_updated,
    }


def get_initial_capital(portfolio: dict) -> float:
    """Capitale di riferimento. Fallback su ``config.CAPITAL`` per edge case."""
    return float(portfolio.get("initial_capital") or CAPITAL)


def set_initial_capital(
    portfolio: dict,
    value: float,
    *,
    reset_cash: bool = False,
) -> dict:
    """Aggiorna il capitale di riferimento (campo informativo).

    Con ``reset_cash=True`` azzera anche il ``cash`` corrente a ``value`` —
    consentito solo se non ci sono posizioni aperte, per evitare di rompere
    il cash accounting di un portfolio live.
    """
    if value <= 0:
        raise ValueError(f"initial_capital deve essere > 0 (ricevuto {value}).")
    if reset_cash and portfolio.get("positions"):
        raise ValueError(
            "Reset cash consentito solo con portfolio vuoto "
            f"({len(portfolio['positions'])} posizioni aperte). "
            "Chiudi o rimuovi le posizioni prima del reset."
        )
    new_value = round(float(value), 2)
    updates: dict[str, str] = {
        "initial_capital": str(new_value),
        "last_updated": datetime.now().strftime(DATE_FMT),
    }
    if reset_cash:
        updates["cash"] = str(new_value)
    meta_set_many(updates)

    portfolio["initial_capital"] = new_value
    if reset_cash:
        portfolio["cash"] = new_value
    portfolio["last_updated"] = updates["last_updated"]
    return portfolio


def save_portfolio(portfolio: dict) -> None:
    """Sincronizza il dict in-memory con il DB.

    Utile quando un caller ha mutato il dict direttamente (raro ma ammesso
    dal pattern legacy). Fa un upsert completo di tutte le positions + meta.
    Normalmente le API mutanti (``add_position``, ``close_position``, etc.)
    persistono direttamente — non serve chiamare questa funzione.
    """
    cash = float(portfolio.get("cash") or 0)
    initial_capital = float(portfolio.get("initial_capital") or CAPITAL)
    last_updated = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        # Sync positions: delete + insert (più semplice che UPSERT con n colonne)
        conn.execute("DELETE FROM positions")
        for ticker, pos in portfolio.get("positions", {}).items():
            conn.execute(
                """INSERT INTO positions (
                    ticker, strategy, entry_price, entry_date, shares,
                    stop_loss, target, highest_price_since_entry, trailing_enabled,
                    score_claude, score_tech, catalyst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker.upper(),
                    pos.get("strategy"),
                    float(pos["entry_price"]),
                    pos.get("entry_date"),
                    int(pos.get("shares") or 0),
                    pos.get("stop_loss"),
                    pos.get("target"),
                    pos.get("highest_price_since_entry"),
                    1 if pos.get("trailing_enabled") else 0,
                    pos.get("score_claude"),
                    pos.get("score_tech"),
                    pos.get("catalyst"),
                ),
            )
        for key, value in (
            ("cash", str(cash)),
            ("initial_capital", str(initial_capital)),
            ("last_updated", last_updated),
        ):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
    portfolio["last_updated"] = last_updated


def unrealized_pl(portfolio: dict) -> tuple[float, dict[str, float]]:
    """Ritorna (P&L unrealized totale, mappa ticker→prezzo corrente).

    Le posizioni senza ``shares`` (legacy pre-sync) o senza prezzo corrente
    vengono skippate senza contribuire al totale.
    """
    from propicks.market.yfinance_client import get_current_prices

    positions = portfolio.get("positions", {})
    if not positions:
        return 0.0, {}
    prices = get_current_prices(list(positions.keys()))
    total = 0.0
    for ticker, p in positions.items():
        cur = prices.get(ticker)
        shares = p.get("shares")
        if cur is None or shares is None:
            continue
        total += (cur - p["entry_price"]) * shares
    return total, prices


# ---------------------------------------------------------------------------
# Mutating API
# ---------------------------------------------------------------------------
def add_position(
    portfolio: dict,
    ticker: str,
    entry_price: float,
    shares: int,
    stop_loss: float,
    target: float | None,
    strategy: str | None,
    score_claude: int | None,
    score_tech: int | None,
    catalyst: str | None,
    entry_date: str | None = None,
    *,
    ignore_earnings: bool = False,
    currency: str | None = None,
) -> dict:
    """Apre una posizione con tutti i gate di business.

    Muta il dict ``portfolio`` in-place AND scrive su DB (transazione unica
    positions + cash meta).

    Gate contrarian: size 8%, max 3 pos, 20% aggregate, loss 12%. Riconosce
    il bucket da ``strategy.lower().startswith("contra")``.

    Phase 8 gate: hard block se earnings entro ``EARNINGS_HARD_GATE_DAYS``
    (default 5). Override con ``ignore_earnings=True`` per trade contrarian
    intentional post-earnings.
    """
    ticker = ticker.upper()

    # Earnings hard gate (Phase 8) — first check per fail-fast prima di validazioni
    # costose (sizing, cash). Skippato se ignore_earnings=True.
    if not ignore_earnings:
        from propicks.domain.calendar import earnings_gate_check
        from propicks.market.yfinance_client import get_next_earnings_date
        try:
            earnings_date = get_next_earnings_date(ticker)
        except Exception:
            earnings_date = None  # fail-open se yfinance giù
        check = earnings_gate_check(ticker, earnings_date, EARNINGS_HARD_GATE_DAYS)
        if check["blocked"]:
            raise ValueError(
                f"Earnings gate: {ticker} ha earnings in {check['days_to_earnings']}gg "
                f"({earnings_date}). Usa ignore_earnings=True per trade intentional "
                f"(contrarian post-earnings flush), oppure aspetta che passi l'evento."
            )
    positions = portfolio.setdefault("positions", {})

    if ticker in positions:
        raise ValueError(f"Posizione già aperta su {ticker}.")
    if len(positions) >= MAX_POSITIONS:
        raise ValueError(f"Portafoglio pieno: {MAX_POSITIONS} posizioni.")
    if shares <= 0:
        raise ValueError(f"shares deve essere > 0 (ricevuto {shares}).")
    if stop_loss >= entry_price:
        raise ValueError(
            f"stop_loss {stop_loss:.2f} >= entry {entry_price:.2f}: invalido per long."
        )
    validate_scores(score_claude, score_tech)

    # Currency: auto-infer da ticker suffix se non passata esplicitamente
    if currency is None:
        from propicks.domain.currency import infer_currency
        currency = infer_currency(ticker)
    currency = currency.upper()

    cost = shares * entry_price
    cash = float(portfolio.get("cash") or 0)
    if cost > cash:
        raise ValueError(
            f"Cash insufficiente: servono {cost:.2f}, disponibili {cash:.2f}."
        )

    total = portfolio_value(portfolio)

    # Bucket detection — precedenza: contrarian (tag) > thematic (ticker o tag)
    # > etf_rotation (ticker o tag) > standard.
    is_contra = isinstance(strategy, str) and strategy.lower().startswith("contra")
    is_thematic = (not is_contra) and (
        ticker in THEMATIC_ETFS
        or (isinstance(strategy, str) and "themat" in strategy.lower())
    )
    is_etf_rot = (
        (not is_contra)
        and (not is_thematic)
        and is_etf_rotation_position(
            {"strategy": strategy}, ticker=ticker
        )
    )

    if is_contra:
        size_cap_pct = CONTRA_MAX_POSITION_SIZE_PCT
        loss_cap_pct = CONTRA_MAX_LOSS_PER_TRADE_PCT
        bucket_label = "contrarian"
    elif is_thematic:
        size_cap_pct = THEMATIC_MAX_POSITION_SIZE_PCT
        loss_cap_pct = THEMATIC_STOP_LOSS_PCT
        bucket_label = "thematic"
    elif is_etf_rot:
        size_cap_pct = ETF_MAX_POSITION_SIZE_PCT
        loss_cap_pct = MAX_LOSS_PER_TRADE_PCT  # ETF stop fisso 5% gestito a portfolio_engine
        bucket_label = "etf_rotation"
    else:
        size_cap_pct = MAX_POSITION_SIZE_PCT
        loss_cap_pct = MAX_LOSS_PER_TRADE_PCT
        bucket_label = "standard"

    if cost > total * size_cap_pct:
        raise ValueError(
            f"Size {cost/total*100:.1f}% supera il limite "
            f"{size_cap_pct*100:.0f}% per posizione ({bucket_label})."
        )

    if is_contra:
        contra_n = contrarian_position_count(portfolio)
        if contra_n >= CONTRA_MAX_POSITIONS:
            raise ValueError(
                f"Bucket contrarian pieno: {contra_n}/{CONTRA_MAX_POSITIONS} "
                f"posizioni contrarian aperte."
            )
        new_contra_value = sum(
            float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
            for p in positions.values()
            if is_contrarian_position(p)
        ) + cost
        new_contra_pct = new_contra_value / total if total > 0 else 0.0
        if new_contra_pct > CONTRA_MAX_AGGREGATE_EXPOSURE_PCT:
            current_expo = contrarian_aggregate_exposure(portfolio)
            raise ValueError(
                f"Aggiungere {ticker} porterebbe l'esposizione contrarian a "
                f"{new_contra_pct*100:.1f}% (da {current_expo*100:.1f}%), "
                f"sopra il cap {CONTRA_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )

    if is_thematic:
        thematic_n = thematic_position_count(portfolio)
        if thematic_n >= THEMATIC_MAX_POSITIONS:
            raise ValueError(
                f"Bucket thematic pieno: {thematic_n}/{THEMATIC_MAX_POSITIONS} "
                f"posizioni thematic aperte."
            )
        # Parent aggregate cap: weight(theme) + weight(parent_ETF) ≤ 25%
        parent_ticker = THEMATIC_ETFS.get(ticker, {}).get("parent_ticker")
        if parent_ticker:
            current_parent_pct = thematic_parent_aggregate(portfolio, parent_ticker)
            new_parent_pct = current_parent_pct + (cost / total if total > 0 else 0)
            if new_parent_pct > THEMATIC_PARENT_AGGREGATE_CAP_PCT:
                raise ValueError(
                    f"Aggiungere {ticker} porterebbe l'esposizione "
                    f"theme + parent({parent_ticker}) a "
                    f"{new_parent_pct*100:.1f}% (da {current_parent_pct*100:.1f}%), "
                    f"sopra il cap {THEMATIC_PARENT_AGGREGATE_CAP_PCT*100:.0f}%."
                )

    # ─── Bucket aggregate gates (Stock 40% / ETF 60%) ────────────────────
    # STOCK = momentum + contrarian merged. ETF = rotation + thematic merged.
    # I sub-cap (contrarian 20%, thematic parent 25%) restano applicati sopra.
    if is_contra or (not is_thematic and not is_etf_rot):
        # bucket Stock
        current_stock_pct = stock_aggregate_exposure(portfolio)
        new_stock_pct = current_stock_pct + (cost / total if total > 0 else 0)
        if new_stock_pct > STOCK_MAX_AGGREGATE_EXPOSURE_PCT:
            raise ValueError(
                f"Aggiungere {ticker} porterebbe il bucket Stock (momentum+contrarian) a "
                f"{new_stock_pct*100:.1f}% (da {current_stock_pct*100:.1f}%), "
                f"sopra il cap aggregato {STOCK_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )
    elif is_thematic or is_etf_rot:
        # bucket ETF
        current_etf_pct = etf_aggregate_exposure(portfolio)
        new_etf_pct = current_etf_pct + (cost / total if total > 0 else 0)
        if new_etf_pct > ETF_MAX_AGGREGATE_EXPOSURE_PCT:
            raise ValueError(
                f"Aggiungere {ticker} porterebbe il bucket ETF (rotation+thematic) a "
                f"{new_etf_pct*100:.1f}% (da {current_etf_pct*100:.1f}%), "
                f"sopra il cap aggregato {ETF_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )

    new_cash = cash - cost
    if new_cash < total * MIN_CASH_RESERVE_PCT:
        raise ValueError(
            f"Apertura violerebbe la riserva cash minima "
            f"({MIN_CASH_RESERVE_PCT*100:.0f}%): cash residuo {new_cash:.2f} "
            f"< {total * MIN_CASH_RESERVE_PCT:.2f}."
        )
    risk_pct_trade = (entry_price - stop_loss) / entry_price
    if risk_pct_trade > loss_cap_pct:
        raise ValueError(
            f"Stop distante {risk_pct_trade*100:.2f}% > limite "
            f"{loss_cap_pct*100:.0f}% per trade ({bucket_label})."
        )
    if score_claude is not None and score_claude < MIN_SCORE_CLAUDE:
        raise ValueError(
            f"score_claude {score_claude} < soglia minima {MIN_SCORE_CLAUDE}."
        )
    if score_tech is not None and score_tech < MIN_SCORE_TECH:
        raise ValueError(
            f"score_tech {score_tech} < soglia minima {MIN_SCORE_TECH}."
        )

    entry_date = entry_date or datetime.now().strftime(DATE_FMT)
    new_position = {
        "entry_price": round(entry_price, 2),
        "entry_date": entry_date,
        "shares": int(shares),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2) if target is not None else None,
        "strategy": strategy,
        "score_claude": score_claude,
        "score_tech": score_tech,
        "catalyst": catalyst,
        "currency": currency,
    }
    new_cash = round(cash - cost, 2)
    now = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        conn.execute(
            """INSERT INTO positions (
                ticker, strategy, entry_price, entry_date, shares,
                stop_loss, target, score_claude, score_tech, catalyst, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                strategy,
                new_position["entry_price"],
                new_position["entry_date"],
                new_position["shares"],
                new_position["stop_loss"],
                new_position["target"],
                score_claude,
                score_tech,
                catalyst,
                currency,
            ),
        )
        for key, value in (("cash", str(new_cash)), ("last_updated", now)):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )

    # Sync in-process dict
    positions[ticker] = new_position
    portfolio["cash"] = new_cash
    portfolio["last_updated"] = now
    return new_position


def remove_position(portfolio: dict, ticker: str) -> dict:
    """Rimuove una posizione rimborsando il costo d'entrata (undo di add_position).

    Usalo per correggere errori di data entry. Per chiudere un trade reale
    con P&L usa invece ``close_position(exit_price)``.
    """
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(f"Nessuna posizione aperta su {ticker}.")
    pos = positions.pop(ticker)
    refund = pos["shares"] * pos["entry_price"]
    new_cash = round(float(portfolio.get("cash") or 0) + refund, 2)
    now = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
        for key, value in (("cash", str(new_cash)), ("last_updated", now)):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
    portfolio["cash"] = new_cash
    portfolio["last_updated"] = now
    return pos


def increase_position(
    portfolio: dict,
    ticker: str,
    add_shares: int,
    add_price: float,
    *,
    new_stop: float | None = None,
    new_target: float | None = None,
    ignore_earnings: bool = False,
) -> dict:
    """Incrementa (pyramid) una posizione esistente con entry medio pesato.

    Differenza da remove+re-add: NON rimbalza il cash a entry vecchio, NON
    tocca il journal, NON resetta ``entry_date`` (continuità time-stop /
    trade_mgmt). Muta ``portfolio`` in-place AND scrive su DB (UPDATE positions
    + cash meta, transazione unica).

    Entry risultante = media pesata::

        entry_avg = (sh_old*entry_old + add_shares*add_price)
                    / (sh_old + add_shares)

    Re-applica TUTTI i gate di business sulla NUOVA size totale: cash
    sufficiency, size cap per-posizione, bucket aggregate (Stock 40% / ETF
    60%), sub-cap contrarian/thematic, min cash reserve, loss cap per-trade
    contro il nuovo entry medio, earnings hard gate.

    Lo stop esistente viene rivalidato contro il nuovo entry medio (mediando
    al rialzo il rischio% sale a stop fisso): se sfora il loss cap, ``new_stop``
    diventa obbligatorio. ``new_target``, se passato, deve stare sopra il nuovo
    entry medio.
    """
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(
            f"Nessuna posizione aperta su {ticker}. Usa `add` per aprirla."
        )
    if add_shares <= 0:
        raise ValueError(f"add_shares deve essere > 0 (ricevuto {add_shares}).")
    if add_price <= 0:
        raise ValueError(f"add_price deve essere > 0 (ricevuto {add_price}).")

    pos = positions[ticker]
    strategy = pos.get("strategy")

    # Earnings hard gate (Phase 8) — incrementare è aggiungere esposizione,
    # stesso gate di add_position. Override esplicito per add intentional.
    if not ignore_earnings:
        from propicks.domain.calendar import earnings_gate_check
        from propicks.market.yfinance_client import get_next_earnings_date
        try:
            earnings_date = get_next_earnings_date(ticker)
        except Exception:
            earnings_date = None
        check = earnings_gate_check(ticker, earnings_date, EARNINGS_HARD_GATE_DAYS)
        if check["blocked"]:
            raise ValueError(
                f"Earnings gate: {ticker} ha earnings in {check['days_to_earnings']}gg "
                f"({earnings_date}). Usa ignore_earnings=True per add intentional, "
                f"oppure aspetta che passi l'evento."
            )

    sh_old = int(pos["shares"])
    entry_old = float(pos["entry_price"])
    add_cost = add_shares * add_price
    cash = float(portfolio.get("cash") or 0)
    if add_cost > cash:
        raise ValueError(
            f"Cash insufficiente: servono {add_cost:.2f}, disponibili {cash:.2f}."
        )

    sh_new = sh_old + int(add_shares)
    entry_avg = (sh_old * entry_old + add_shares * add_price) / sh_new
    entry_avg = round(entry_avg, 2)
    new_cost_basis = sh_new * entry_avg

    total = portfolio_value(portfolio)  # invariato: cash -add_cost, pos +add_cost

    # Bucket detection — stessa precedenza di add_position
    # (contrarian tag > thematic ticker/tag > etf_rotation > standard).
    is_contra = isinstance(strategy, str) and strategy.lower().startswith("contra")
    is_thematic = (not is_contra) and (
        ticker in THEMATIC_ETFS
        or (isinstance(strategy, str) and "themat" in strategy.lower())
    )
    is_etf_rot = (
        (not is_contra)
        and (not is_thematic)
        and is_etf_rotation_position({"strategy": strategy}, ticker=ticker)
    )
    if is_contra:
        size_cap_pct = CONTRA_MAX_POSITION_SIZE_PCT
        loss_cap_pct = CONTRA_MAX_LOSS_PER_TRADE_PCT
        bucket_label = "contrarian"
    elif is_thematic:
        size_cap_pct = THEMATIC_MAX_POSITION_SIZE_PCT
        loss_cap_pct = THEMATIC_STOP_LOSS_PCT
        bucket_label = "thematic"
    elif is_etf_rot:
        size_cap_pct = ETF_MAX_POSITION_SIZE_PCT
        loss_cap_pct = MAX_LOSS_PER_TRADE_PCT
        bucket_label = "etf_rotation"
    else:
        size_cap_pct = MAX_POSITION_SIZE_PCT
        loss_cap_pct = MAX_LOSS_PER_TRADE_PCT
        bucket_label = "standard"

    # Size cap sulla NUOVA size totale della posizione (non solo l'aggiunta).
    if new_cost_basis > total * size_cap_pct:
        raise ValueError(
            f"Size post-incremento {new_cost_basis/total*100:.1f}% supera il "
            f"limite {size_cap_pct*100:.0f}% per posizione ({bucket_label})."
        )

    # Bucket aggregate: l'esposizione corrente include già la posizione al
    # costo vecchio; il delta sul bucket è esattamente ``add_cost``.
    if is_contra:
        new_contra_pct = contrarian_aggregate_exposure(portfolio) + (
            add_cost / total if total > 0 else 0
        )
        if new_contra_pct > CONTRA_MAX_AGGREGATE_EXPOSURE_PCT:
            raise ValueError(
                f"Incrementare {ticker} porterebbe l'esposizione contrarian a "
                f"{new_contra_pct*100:.1f}%, sopra il cap "
                f"{CONTRA_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )
    if is_thematic:
        parent_ticker = THEMATIC_ETFS.get(ticker, {}).get("parent_ticker")
        if parent_ticker:
            cur_parent = thematic_parent_aggregate(portfolio, parent_ticker)
            new_parent = cur_parent + (add_cost / total if total > 0 else 0)
            if new_parent > THEMATIC_PARENT_AGGREGATE_CAP_PCT:
                raise ValueError(
                    f"Incrementare {ticker} porterebbe theme + "
                    f"parent({parent_ticker}) a {new_parent*100:.1f}%, sopra il "
                    f"cap {THEMATIC_PARENT_AGGREGATE_CAP_PCT*100:.0f}%."
                )
    if is_contra or (not is_thematic and not is_etf_rot):
        new_stock = stock_aggregate_exposure(portfolio) + (
            add_cost / total if total > 0 else 0
        )
        if new_stock > STOCK_MAX_AGGREGATE_EXPOSURE_PCT:
            raise ValueError(
                f"Incrementare {ticker} porterebbe il bucket Stock a "
                f"{new_stock*100:.1f}%, sopra il cap "
                f"{STOCK_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )
    else:
        new_etf = etf_aggregate_exposure(portfolio) + (
            add_cost / total if total > 0 else 0
        )
        if new_etf > ETF_MAX_AGGREGATE_EXPOSURE_PCT:
            raise ValueError(
                f"Incrementare {ticker} porterebbe il bucket ETF a "
                f"{new_etf*100:.1f}%, sopra il cap "
                f"{ETF_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%."
            )

    new_cash = round(cash - add_cost, 2)
    if new_cash < total * MIN_CASH_RESERVE_PCT:
        raise ValueError(
            f"Incremento violerebbe la riserva cash minima "
            f"({MIN_CASH_RESERVE_PCT*100:.0f}%): cash residuo {new_cash:.2f} "
            f"< {total * MIN_CASH_RESERVE_PCT:.2f}."
        )

    # Stop: rivalida contro il nuovo entry medio. Mediando al rialzo il
    # rischio% a stop fisso cresce → può sforare il loss cap.
    eff_stop = new_stop if new_stop is not None else float(pos["stop_loss"])
    if eff_stop <= 0:
        raise ValueError(f"stop deve essere > 0 (ricevuto {eff_stop}).")
    if eff_stop >= entry_avg:
        raise ValueError(
            f"stop {eff_stop:.2f} >= entry medio {entry_avg:.2f}: invalido per long."
        )
    risk_pct = (entry_avg - eff_stop) / entry_avg
    if risk_pct > loss_cap_pct:
        raise ValueError(
            f"Stop {eff_stop:.2f} dista {risk_pct*100:.2f}% dal nuovo entry "
            f"medio {entry_avg:.2f} > limite {loss_cap_pct*100:.0f}% "
            f"({bucket_label}). Passa un new_stop più vicino all'entry medio."
        )

    eff_target = new_target if new_target is not None else pos.get("target")
    if eff_target is not None:
        if eff_target <= entry_avg:
            raise ValueError(
                f"target {eff_target:.2f} <= entry medio {entry_avg:.2f}: "
                f"un long con target sotto entry non ha senso."
            )
        eff_target = round(eff_target, 2)

    eff_stop = round(eff_stop, 2)
    now = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        conn.execute(
            """UPDATE positions
               SET shares = ?, entry_price = ?, stop_loss = ?, target = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE ticker = ?""",
            (sh_new, entry_avg, eff_stop, eff_target, ticker),
        )
        for key, value in (("cash", str(new_cash)), ("last_updated", now)):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )

    pos["shares"] = sh_new
    pos["entry_price"] = entry_avg
    pos["stop_loss"] = eff_stop
    pos["target"] = eff_target
    portfolio["cash"] = new_cash
    portfolio["last_updated"] = now
    return pos


def close_position(portfolio: dict, ticker: str, exit_price: float) -> dict:
    """Chiude una posizione con cash accounting corretto (exit_price reali)."""
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(f"Nessuna posizione aperta su {ticker}.")
    if exit_price <= 0:
        raise ValueError(f"exit_price deve essere > 0 (ricevuto {exit_price}).")
    pos = positions.pop(ticker)
    proceeds = pos["shares"] * exit_price
    new_cash = round(float(portfolio.get("cash") or 0) + proceeds, 2)
    now = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
        for key, value in (("cash", str(new_cash)), ("last_updated", now)):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
    portfolio["cash"] = new_cash
    portfolio["last_updated"] = now
    return pos


def update_position(
    portfolio: dict,
    ticker: str,
    stop_loss: float | None = None,
    target: float | None = None,
    highest_price: float | None = None,
    trailing_enabled: bool | None = None,
) -> dict:
    """Aggiorna uno o più campi di una posizione esistente."""
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(f"Nessuna posizione aperta su {ticker}.")
    fields = (stop_loss, target, highest_price, trailing_enabled)
    if all(f is None for f in fields):
        raise ValueError("Specificare almeno un campo da aggiornare.")
    pos = positions[ticker]
    entry = float(pos["entry_price"])

    # Validazioni identiche alla versione JSON:
    if stop_loss is not None:
        if stop_loss <= 0:
            raise ValueError(f"stop_loss deve essere > 0 (ricevuto {stop_loss}).")
        pos["stop_loss"] = round(stop_loss, 2)
    if target is not None:
        if target <= entry:
            raise ValueError(
                f"target {target:.2f} <= entry {entry:.2f}: un long con target "
                f"sotto entry non ha senso. Correggi o usa `remove`."
            )
        pos["target"] = round(target, 2)
    if highest_price is not None:
        pos["highest_price_since_entry"] = round(highest_price, 2)
    if trailing_enabled is not None:
        pos["trailing_enabled"] = bool(trailing_enabled)

    now = datetime.now().strftime(DATE_FMT)
    with transaction() as conn:
        # Aggiorna SOLO i campi non-None per non azzerare accidentalmente altri
        setters: list[str] = []
        params: list = []
        if stop_loss is not None:
            setters.append("stop_loss = ?")
            params.append(pos["stop_loss"])
        if target is not None:
            setters.append("target = ?")
            params.append(pos["target"])
        if highest_price is not None:
            setters.append("highest_price_since_entry = ?")
            params.append(pos["highest_price_since_entry"])
        if trailing_enabled is not None:
            setters.append("trailing_enabled = ?")
            params.append(1 if pos["trailing_enabled"] else 0)
        setters.append("updated_at = CURRENT_TIMESTAMP")
        params.append(ticker)
        conn.execute(
            f"UPDATE positions SET {', '.join(setters)} WHERE ticker = ?",
            params,
        )
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES ('last_updated', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (now,),
        )
    portfolio["last_updated"] = now
    return pos
