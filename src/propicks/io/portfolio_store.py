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
    DATE_FMT,
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
    is_contrarian_position,
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
) -> dict:
    """Apre una posizione con tutti i gate di business.

    Muta il dict ``portfolio`` in-place AND scrive su DB (transazione unica
    positions + cash meta).

    Gate contrarian: size 8%, max 3 pos, 20% aggregate, loss 12%. Riconosce
    il bucket da ``strategy.lower().startswith("contra")``.
    """
    ticker = ticker.upper()
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

    cost = shares * entry_price
    cash = float(portfolio.get("cash") or 0)
    if cost > cash:
        raise ValueError(
            f"Cash insufficiente: servono {cost:.2f}, disponibili {cash:.2f}."
        )

    total = portfolio_value(portfolio)

    is_contra = isinstance(strategy, str) and strategy.lower().startswith("contra")
    if is_contra:
        size_cap_pct = CONTRA_MAX_POSITION_SIZE_PCT
        loss_cap_pct = CONTRA_MAX_LOSS_PER_TRADE_PCT
    else:
        size_cap_pct = MAX_POSITION_SIZE_PCT
        loss_cap_pct = MAX_LOSS_PER_TRADE_PCT

    if cost > total * size_cap_pct:
        bucket_label = "contrarian" if is_contra else "standard"
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

    new_cash = cash - cost
    if new_cash < total * MIN_CASH_RESERVE_PCT:
        raise ValueError(
            f"Apertura violerebbe la riserva cash minima "
            f"({MIN_CASH_RESERVE_PCT*100:.0f}%): cash residuo {new_cash:.2f} "
            f"< {total * MIN_CASH_RESERVE_PCT:.2f}."
        )
    risk_pct_trade = (entry_price - stop_loss) / entry_price
    if risk_pct_trade > loss_cap_pct:
        bucket_label = "contrarian" if is_contra else "standard"
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
    }
    new_cash = round(cash - cost, 2)
    now = datetime.now().strftime(DATE_FMT)

    with transaction() as conn:
        conn.execute(
            """INSERT INTO positions (
                ticker, strategy, entry_price, entry_date, shares,
                stop_loss, target, score_claude, score_tech, catalyst
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
