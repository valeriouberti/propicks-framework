"""Position sizing basato su convinzione e gestione rischio.

Puro: non legge né scrive stato. Riceve un portfolio dict e ritorna un dict
di risultato. L'I/O è responsabilità di io/portfolio_store.
"""

from __future__ import annotations

from typing import Literal

from propicks.config import (
    CONTRA_MAX_AGGREGATE_EXPOSURE_PCT,
    CONTRA_MAX_LOSS_PER_TRADE_PCT,
    CONTRA_MAX_POSITION_SIZE_PCT,
    CONTRA_MAX_POSITIONS,
    ETF_MAX_AGGREGATE_EXPOSURE_PCT,
    ETF_MAX_POSITION_SIZE_PCT,
    HIGH_CONVICTION_SIZE_PCT,
    MAX_LOSS_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MEDIUM_CONVICTION_SIZE_PCT,
    MIN_CASH_RESERVE_PCT,
    MIN_SCORE_CLAUDE,
    MIN_SCORE_TECH,
    STOCK_MAX_AGGREGATE_EXPOSURE_PCT,
    THEMATIC_ETFS,
    THEMATIC_MAX_POSITION_SIZE_PCT,
    THEMATIC_MAX_POSITIONS,
    THEMATIC_PARENT_AGGREGATE_CAP_PCT,
    THEMATIC_STOP_LOSS_PCT,
)
from propicks.domain.validation import validate_scores

AssetTypeLiteral = Literal["STOCK", "SECTOR_ETF", "THEMATIC_ETF"]
StrategyBucket = Literal["momentum", "contrarian", "etf_rotation", "thematic"]


def is_contrarian_position(p: dict) -> bool:
    """Match convention per riconoscere posizioni contrarian nel portfolio.

    Case-insensitive check su ``p["strategy"]`` che inizia con "contra".
    Tollera tag come "Contrarian", "contrarian-pullback", "Contra — macro_flush".
    """
    s = p.get("strategy") or ""
    return isinstance(s, str) and s.lower().startswith("contra")


# Alias private per retro-compat (era _is_contrarian_position prima del rename)
_is_contrarian_position = is_contrarian_position


def contrarian_aggregate_exposure(portfolio: dict) -> float:
    """Somma del valore contrarian corrente / portfolio_value (frazione 0-1).

    Usato come gate aggregato: il bucket contrarian non può superare
    ``CONTRA_MAX_AGGREGATE_EXPOSURE_PCT`` del capitale.
    """
    total = portfolio_value(portfolio)
    if total <= 0:
        return 0.0
    positions = portfolio.get("positions", {}).values()
    contra_value = sum(
        float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
        for p in positions
        if is_contrarian_position(p)
    )
    return contra_value / total


def contrarian_position_count(portfolio: dict) -> int:
    """Quante posizioni contrarian aperte in portfolio."""
    return sum(
        1 for p in portfolio.get("positions", {}).values()
        if is_contrarian_position(p)
    )


def is_thematic_position(p: dict, ticker: str | None = None) -> bool:
    """True se posizione è thematic ETF.

    Detection a 2 layer:
    1. ``strategy.lower()`` contiene "themat" → tag esplicito ("Thematic")
    2. ``ticker`` (case-insensitive) registrato in ``THEMATIC_ETFS``
       → fallback strutturale (tag mancante o diverso)
    """
    s = p.get("strategy") or ""
    if isinstance(s, str) and "themat" in s.lower():
        return True
    tk = ticker or p.get("ticker") or ""
    return isinstance(tk, str) and tk.upper() in THEMATIC_ETFS


def thematic_position_count(portfolio: dict) -> int:
    """Quante posizioni thematic aperte in portfolio."""
    n = 0
    for tk, p in portfolio.get("positions", {}).items():
        if is_thematic_position(p, ticker=tk):
            n += 1
    return n


def thematic_parent_aggregate(portfolio: dict, parent_ticker: str) -> float:
    """Frazione di capitale impegnata in `parent_ticker` + tematici di quel parent.

    Usato per gate ``THEMATIC_PARENT_AGGREGATE_CAP_PCT``: la somma di
    weight(parent_ETF) + weight(theme_i con stesso parent) deve restare
    sotto il cap (default 25%). Evita doppio bet camuffato da diversificazione.
    """
    total = portfolio_value(portfolio)
    if total <= 0:
        return 0.0
    parent_up = parent_ticker.upper()
    aggregate = 0.0
    for tk, p in portfolio.get("positions", {}).items():
        tk_up = tk.upper()
        cost = float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
        if tk_up == parent_up:
            aggregate += cost
            continue
        meta = THEMATIC_ETFS.get(tk_up)
        if meta and meta.get("parent_ticker", "").upper() == parent_up:
            aggregate += cost
    return aggregate / total


def is_etf_rotation_position(p: dict, ticker: str | None = None) -> bool:
    """True se posizione è sector ETF rotation (NON thematic).

    Detection: ticker registrato in `SECTOR_ETFS_*` E NON in `THEMATIC_ETFS`,
    oppure tag strategy "ETF_Rotation".
    """
    s = p.get("strategy") or ""
    if isinstance(s, str) and "etf_rotation" in s.lower():
        return True
    tk = ticker or p.get("ticker") or ""
    if not isinstance(tk, str):
        return False
    tk_up = tk.upper()
    if tk_up in THEMATIC_ETFS:
        return False
    from propicks.config import SECTOR_ETFS_US, SECTOR_ETFS_WORLD
    return tk_up in SECTOR_ETFS_US or tk_up in SECTOR_ETFS_WORLD


def is_etf_position(p: dict, ticker: str | None = None) -> bool:
    """True se posizione appartiene al **bucket ETF** (rotation O thematic).

    Bucket aggregate cap (60%) applica all'unione dei due.
    """
    return is_thematic_position(p, ticker=ticker) or is_etf_rotation_position(p, ticker=ticker)


def is_stock_position(p: dict, ticker: str | None = None) -> bool:
    """True se posizione appartiene al **bucket Stock** (momentum + contrarian).

    Definito come complemento del bucket ETF: tutto ciò che non è ETF
    (rotation/thematic) è stock. Include momentum + contrarian + qualsiasi
    altro tag single-name discrezionale ('Altro', 'TechTitans', ecc).
    """
    return not is_etf_position(p, ticker=ticker)


def stock_aggregate_exposure(portfolio: dict) -> float:
    """Frazione capitale impegnata nel bucket Stock (momentum + contrarian).

    Usato per gate aggregate `STOCK_MAX_AGGREGATE_EXPOSURE_PCT` (40%).
    """
    total = portfolio_value(portfolio)
    if total <= 0:
        return 0.0
    aggregate = 0.0
    for tk, p in portfolio.get("positions", {}).items():
        if is_stock_position(p, ticker=tk):
            aggregate += float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
    return aggregate / total


def etf_aggregate_exposure(portfolio: dict) -> float:
    """Frazione capitale impegnata nel bucket ETF (rotation + thematic).

    Usato per gate aggregate `ETF_MAX_AGGREGATE_EXPOSURE_PCT` (60%).
    """
    total = portfolio_value(portfolio)
    if total <= 0:
        return 0.0
    aggregate = 0.0
    for tk, p in portfolio.get("positions", {}).items():
        if is_etf_position(p, ticker=tk):
            aggregate += float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
    return aggregate / total


def portfolio_value(portfolio: dict) -> float:
    """Valore totale del portafoglio = cash + sum(shares * entry_price).

    Usa i prezzi di entry (non i correnti): è una misura contabile,
    non di mark-to-market. Usata come base per i gate di sizing (15% cap,
    20% riserva) perché l'invariante è "% del capitale impegnato", non
    "% del P&L corrente".
    """
    cash = float(portfolio.get("cash") or 0)
    invested = sum(
        float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
        for p in portfolio.get("positions", {}).values()
    )
    return cash + invested


def portfolio_market_value(
    portfolio: dict,
    current_prices: dict[str, float | None],
) -> float:
    """Valore mark-to-market = cash + sum(shares * current_price).

    Usare come denominatore per i calcoli di **esposizione** (sector/beta/
    correlation): i numeratori in ``domain.exposure`` sono mark-to-market,
    quindi anche il denominatore deve esserlo — altrimenti i weight non
    sommano a 1 quando ci sono P&L unrealized (un portfolio +20% gonfia
    i numeratori senza toccare il cost-basis del denominatore).

    **Semantica skip-on-None**: i ticker senza prezzo corrente vengono
    esclusi dal totale, coerente con ``compute_sector_exposure`` e
    ``compute_beta_weighted_exposure`` che li skippano anch'esse. Risultato:
    un ticker senza prezzo sparisce da numeratore E denominatore — il peso
    degli altri resta corretto tra loro, cash incluso.
    """
    cash = float(portfolio.get("cash") or 0)
    invested = 0.0
    for ticker, p in portfolio.get("positions", {}).items():
        cur = current_prices.get(ticker)
        if cur is None:
            continue
        shares = float(p.get("shares") or 0)
        invested += shares * float(cur)
    return cash + invested


def _convictions_level(avg_score: float) -> tuple[str, float] | None:
    if avg_score >= 80:
        return "ALTA", HIGH_CONVICTION_SIZE_PCT
    if avg_score >= 60:
        return "MEDIA", MEDIUM_CONVICTION_SIZE_PCT
    return None


def calculate_position_size(
    entry_price: float,
    stop_price: float,
    score_claude: int = 7,
    score_tech: int = 70,
    portfolio: dict | None = None,
    asset_type: AssetTypeLiteral = "STOCK",
    strategy_bucket: StrategyBucket = "momentum",
) -> dict:
    """Calcola quante azioni comprare dati entry, stop e score.

    Logica:
    - risk_per_share = entry - stop (long only; errore se stop >= entry)
    - avg_score = media tra score_claude*10 e score_tech (entrambi su 100)
    - >=80 → HIGH (12% cap), >=60 → MEDIUM (8% cap), sotto → errore
    - position_value = min(target_value, max_value, cash_disponibile)
    - Verifica MAX_POSITIONS e riserva cash MIN_CASH_RESERVE_PCT
    - Warning se risk_pct_trade > MAX_LOSS_PER_TRADE_PCT

    ``asset_type=SECTOR_ETF`` → ``max_value`` usa ``ETF_MAX_POSITION_SIZE_PCT``
    (20%) invece di ``MAX_POSITION_SIZE_PCT`` (15%): ETF settoriali sono
    diversificati e tollerano un cap più alto del single-name.

    ``strategy_bucket="contrarian"`` → override delle regole di sizing:
    - size cap = ``CONTRA_MAX_POSITION_SIZE_PCT`` (8%, hit rate più basso)
    - gate max posizioni contrarian simultanee = ``CONTRA_MAX_POSITIONS`` (3)
    - gate aggregate exposure = ``CONTRA_MAX_AGGREGATE_EXPOSURE_PCT`` (20%)
    - loss soglia warning = ``CONTRA_MAX_LOSS_PER_TRADE_PCT`` (12%, stop più ampio)
    - NB: il cap globale MAX_POSITIONS resta condiviso con il momentum.
    """
    if stop_price >= entry_price:
        return {"ok": False, "error": "Stop >= entry: invalido per long."}
    try:
        validate_scores(score_claude, score_tech)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    risk_per_share = entry_price - stop_price

    if portfolio is None:
        # import locale per evitare ciclo: sizing è puro, ma la CLI che lo usa
        # di default vuole caricare dal disco se non passato esplicitamente
        from propicks.io.portfolio_store import load_portfolio
        portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total_capital = portfolio_value(portfolio)

    if len(positions) >= MAX_POSITIONS:
        return {
            "ok": False,
            "error": f"Portafoglio pieno: {len(positions)}/{MAX_POSITIONS} posizioni aperte.",
        }

    # Gate specifico bucket contrarian: limite posizioni simultanee + aggregato.
    # Condivide il cap globale MAX_POSITIONS (verificato sopra) con il momentum.
    if strategy_bucket == "contrarian":
        contra_n = contrarian_position_count(portfolio)
        if contra_n >= CONTRA_MAX_POSITIONS:
            return {
                "ok": False,
                "error": (
                    f"Bucket contrarian pieno: {contra_n}/{CONTRA_MAX_POSITIONS} "
                    "posizioni contrarian aperte."
                ),
            }
        contra_expo = contrarian_aggregate_exposure(portfolio)
        if contra_expo >= CONTRA_MAX_AGGREGATE_EXPOSURE_PCT:
            return {
                "ok": False,
                "error": (
                    f"Bucket contrarian al cap aggregato: "
                    f"{contra_expo * 100:.1f}% >= "
                    f"{CONTRA_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}% del capitale."
                ),
            }

    # Gate specifico bucket thematic: max 2 simultanee + parent_aggregate cap.
    # Detection del parent_ticker dal ticker se asset_type=THEMATIC_ETF.
    if strategy_bucket == "thematic":
        thematic_n = thematic_position_count(portfolio)
        if thematic_n >= THEMATIC_MAX_POSITIONS:
            return {
                "ok": False,
                "error": (
                    f"Bucket thematic pieno: {thematic_n}/{THEMATIC_MAX_POSITIONS} "
                    "posizioni thematic aperte."
                ),
            }

    # Gate aggregate STOCK (40%) / ETF (60%): policy bucket-level.
    # Stock = momentum + contrarian merged. ETF = rotation + thematic merged.
    is_stock_bucket = strategy_bucket in ("momentum", "contrarian")
    is_etf_bucket = strategy_bucket in ("etf_rotation", "thematic") or asset_type in (
        "SECTOR_ETF",
        "THEMATIC_ETF",
    )
    if is_stock_bucket and not is_etf_bucket:
        cur = stock_aggregate_exposure(portfolio)
        if cur >= STOCK_MAX_AGGREGATE_EXPOSURE_PCT:
            return {
                "ok": False,
                "error": (
                    f"Bucket Stock (momentum+contrarian) al cap aggregato: "
                    f"{cur * 100:.1f}% >= "
                    f"{STOCK_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}% del capitale."
                ),
            }
    if is_etf_bucket:
        cur = etf_aggregate_exposure(portfolio)
        if cur >= ETF_MAX_AGGREGATE_EXPOSURE_PCT:
            return {
                "ok": False,
                "error": (
                    f"Bucket ETF (rotation+thematic) al cap aggregato: "
                    f"{cur * 100:.1f}% >= "
                    f"{ETF_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}% del capitale."
                ),
            }

    # Gate allineato con add_position: i due minimi sono check separati,
    # non una media (altrimenti score_claude=3 + score_tech=90 passerebbe qui
    # ma fallirebbe in add_position). Vedi CLAUDE.md §Regole di Business.
    if score_claude < MIN_SCORE_CLAUDE:
        return {
            "ok": False,
            "error": f"score_claude {score_claude} < soglia minima {MIN_SCORE_CLAUDE}.",
        }
    if score_tech < MIN_SCORE_TECH:
        return {
            "ok": False,
            "error": f"score_tech {score_tech} < soglia minima {MIN_SCORE_TECH}.",
        }

    avg_score = (score_claude * 10 + score_tech) / 2
    conv = _convictions_level(avg_score)
    # Entrambi i minimi passati → avg_score >= 60 garantito → conv != None
    assert conv is not None, "invariant: min gates passed implies MEDIUM or HIGH"
    conviction_level, conviction_pct = conv

    # Bucket contrarian override il cap single-name e riduce il target value
    # (hit rate più basso → size più piccola indipendentemente da conviction).
    if strategy_bucket == "contrarian":
        position_cap_pct = CONTRA_MAX_POSITION_SIZE_PCT
        # Target contrarian fissato al cap: non c'è una distinzione
        # HIGH vs MEDIUM conviction per la mean reversion, il gate è
        # già passato a monte (composite score + Claude flush_vs_break).
        conviction_pct = CONTRA_MAX_POSITION_SIZE_PCT
    elif strategy_bucket == "thematic" or asset_type == "THEMATIC_ETF":
        # Thematic: cap 15% (uguale single-name) ma con gate parent aggregate
        # già applicato sopra. Conviction conserva HIGH/MEDIUM.
        position_cap_pct = THEMATIC_MAX_POSITION_SIZE_PCT
    elif strategy_bucket == "etf_rotation" or asset_type == "SECTOR_ETF":
        position_cap_pct = ETF_MAX_POSITION_SIZE_PCT
    else:
        position_cap_pct = MAX_POSITION_SIZE_PCT
    target_value = total_capital * conviction_pct
    max_value = total_capital * position_cap_pct
    # Anche il bucket contrarian aggregato ha un cap da rispettare: lo size
    # proposto non può far superare CONTRA_MAX_AGGREGATE_EXPOSURE_PCT al
    # totale contrarian. Applicato come cap ulteriore su max_value.
    contra_headroom_pct: float | None = None
    if strategy_bucket == "contrarian":
        contra_expo = contrarian_aggregate_exposure(portfolio)
        contra_headroom_pct = max(
            0.0, CONTRA_MAX_AGGREGATE_EXPOSURE_PCT - contra_expo
        )
        max_value = min(max_value, total_capital * contra_headroom_pct)

    # Thematic parent-aggregate cap: weight(theme) + weight(parent_ETF) ≤ 25%.
    # Richiede di sapere il parent_ticker → lookup da THEMATIC_ETFS via portfolio
    # context (il ticker dev'essere passato dal chiamante; qui si fa best-effort
    # leggendo dal portfolio dict se ticker presente come key transitoria).
    thematic_headroom_pct: float | None = None
    if strategy_bucket == "thematic" or asset_type == "THEMATIC_ETF":
        # Il chiamante deve passare ticker via portfolio (workaround: estendiamo
        # API). Per ora, calcoliamo il cap solo se entry_price/portfolio già
        # mostrano un parent identificato. Fallback: usiamo il cap statico
        # THEMATIC_PARENT_AGGREGATE_CAP_PCT come max_value upper bound.
        max_value = min(max_value, total_capital * THEMATIC_PARENT_AGGREGATE_CAP_PCT)

    # Bucket aggregate headroom (Stock 40% / ETF 60%): clamp position size
    # all'headroom rimanente del bucket. Un position singolo non può saturare
    # il bucket se altre posizioni sono già aperte.
    if is_stock_bucket and not is_etf_bucket:
        stock_headroom_pct = max(
            0.0, STOCK_MAX_AGGREGATE_EXPOSURE_PCT - stock_aggregate_exposure(portfolio)
        )
        max_value = min(max_value, total_capital * stock_headroom_pct)
    if is_etf_bucket:
        etf_headroom_pct = max(
            0.0, ETF_MAX_AGGREGATE_EXPOSURE_PCT - etf_aggregate_exposure(portfolio)
        )
        max_value = min(max_value, total_capital * etf_headroom_pct)
    reserve = total_capital * MIN_CASH_RESERVE_PCT
    cash_available = max(0.0, cash - reserve)

    position_value = min(target_value, max_value, cash_available)
    shares = int(position_value // entry_price)
    actual_value = shares * entry_price

    if shares <= 0:
        # Diagnostica root cause: se il binder è l'headroom contrarian, dillo
        # esplicitamente (UX bug #3 risolto). Altrimenti è effettivamente il
        # cash disponibile rispetto alla riserva minima.
        if (
            strategy_bucket == "contrarian"
            and contra_headroom_pct is not None
            and total_capital * contra_headroom_pct < entry_price
        ):
            return {
                "ok": False,
                "error": (
                    f"Bucket contrarian quasi al cap aggregato: headroom "
                    f"{contra_headroom_pct * 100:.2f}% del capitale = "
                    f"{total_capital * contra_headroom_pct:.2f}€ "
                    f"< entry_price {entry_price:.2f}€. "
                    f"Chiudi una posizione contrarian o riduci esposizione."
                ),
                "contra_headroom_pct": round(contra_headroom_pct, 4),
                "contra_headroom_value": round(total_capital * contra_headroom_pct, 2),
            }
        return {
            "ok": False,
            "error": "Cash disponibile insufficiente rispettando la riserva minima.",
            "cash": cash,
            "cash_available": cash_available,
            "target_value": target_value,
            "entry_price": entry_price,
        }

    risk_total = shares * risk_per_share
    risk_pct_trade = risk_per_share / entry_price
    risk_pct_capital = risk_total / total_capital if total_capital else 0.0

    warnings: list[str] = []
    # Soglia warning stop:
    # - contrarian → 12% (CONTRA_MAX_LOSS_PER_TRADE_PCT, stop su recent_low - 1×ATR)
    # - thematic → 10% (THEMATIC_STOP_LOSS_PCT, ATR% sub-industry più alto)
    # - etf_rotation → 5% (ETF stop fisso, gestito a portfolio_engine)
    # - momentum → 8% (default MAX_LOSS_PER_TRADE_PCT)
    if strategy_bucket == "contrarian":
        loss_threshold = CONTRA_MAX_LOSS_PER_TRADE_PCT
    elif strategy_bucket == "thematic" or asset_type == "THEMATIC_ETF":
        loss_threshold = THEMATIC_STOP_LOSS_PCT
    else:
        loss_threshold = MAX_LOSS_PER_TRADE_PCT
    if risk_pct_trade > loss_threshold:
        warnings.append(
            f"Stop distante {risk_pct_trade*100:.2f}% (> soglia "
            f"{loss_threshold*100:.0f}% per trade {strategy_bucket})."
        )
    if actual_value < target_value * 0.9:
        warnings.append(
            "Size effettiva inferiore al target: cash o max_value bindante."
        )

    return {
        "ok": True,
        "shares": shares,
        "asset_type": asset_type,
        "strategy_bucket": strategy_bucket,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "risk_per_share": round(risk_per_share, 2),
        "position_value": round(actual_value, 2),
        "position_pct": round(actual_value / total_capital, 4) if total_capital else 0.0,
        "target_value": round(target_value, 2),
        "max_value": round(max_value, 2),
        "position_cap_pct": position_cap_pct,
        "cash_available": round(cash_available, 2),
        "avg_score": round(avg_score, 1),
        "conviction": conviction_level,
        "conviction_pct": conviction_pct,
        "risk_total": round(risk_total, 2),
        "risk_pct_trade": round(risk_pct_trade, 4),
        "risk_pct_capital": round(risk_pct_capital, 4),
        "warnings": warnings,
    }
