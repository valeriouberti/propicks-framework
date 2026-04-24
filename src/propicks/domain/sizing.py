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
    ETF_MAX_POSITION_SIZE_PCT,
    HIGH_CONVICTION_SIZE_PCT,
    MAX_LOSS_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MEDIUM_CONVICTION_SIZE_PCT,
    MIN_CASH_RESERVE_PCT,
    MIN_SCORE_CLAUDE,
    MIN_SCORE_TECH,
)
from propicks.domain.validation import validate_scores

AssetTypeLiteral = Literal["STOCK", "SECTOR_ETF"]
StrategyBucket = Literal["momentum", "contrarian"]


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
    elif asset_type == "SECTOR_ETF":
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
    # Soglia warning stop: contrarian accetta stop più larghi (fino a 12%)
    # perché lo stop è ancorato al recent_low - 3×ATR, non a entry - 2×ATR.
    loss_threshold = (
        CONTRA_MAX_LOSS_PER_TRADE_PCT
        if strategy_bucket == "contrarian"
        else MAX_LOSS_PER_TRADE_PCT
    )
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
