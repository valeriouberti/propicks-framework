"""Position sizing basato su convinzione e gestione rischio.

Puro: non legge né scrive stato. Riceve un portfolio dict e ritorna un dict
di risultato. L'I/O è responsabilità di io/portfolio_store.
"""

from __future__ import annotations

from typing import Optional

from propicks.config import (
    HIGH_CONVICTION_SIZE_PCT,
    MAX_LOSS_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MEDIUM_CONVICTION_SIZE_PCT,
    MIN_CASH_RESERVE_PCT,
)
from propicks.domain.validation import validate_scores


def portfolio_value(portfolio: dict) -> float:
    """Valore totale del portafoglio = cash + sum(shares * entry_price).

    Usa i prezzi di entry (non i correnti): è una misura contabile,
    non di mark-to-market.
    """
    cash = float(portfolio.get("cash") or 0)
    invested = sum(
        float(p.get("shares", 0)) * float(p.get("entry_price", 0))
        for p in portfolio.get("positions", {}).values()
    )
    return cash + invested


def _convictions_level(avg_score: float) -> Optional[tuple[str, float]]:
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
    portfolio: Optional[dict] = None,
) -> dict:
    """Calcola quante azioni comprare dati entry, stop e score.

    Logica:
    - risk_per_share = entry - stop (long only; errore se stop >= entry)
    - avg_score = media tra score_claude*10 e score_tech (entrambi su 100)
    - >=80 → HIGH (12% cap), >=60 → MEDIUM (8% cap), sotto → errore
    - position_value = min(target_value, max_value, cash_disponibile)
    - Verifica MAX_POSITIONS e riserva cash MIN_CASH_RESERVE_PCT
    - Warning se risk_pct_trade > MAX_LOSS_PER_TRADE_PCT
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

    avg_score = (score_claude * 10 + score_tech) / 2
    conv = _convictions_level(avg_score)
    if conv is None:
        return {
            "ok": False,
            "error": f"Score troppo basso (avg {avg_score:.1f}, minimo 60).",
            "avg_score": avg_score,
        }
    conviction_level, conviction_pct = conv

    target_value = total_capital * conviction_pct
    max_value = total_capital * MAX_POSITION_SIZE_PCT
    reserve = total_capital * MIN_CASH_RESERVE_PCT
    cash_available = max(0.0, cash - reserve)

    position_value = min(target_value, max_value, cash_available)
    shares = int(position_value // entry_price)
    actual_value = shares * entry_price

    if shares <= 0:
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
    if risk_pct_trade > MAX_LOSS_PER_TRADE_PCT:
        warnings.append(
            f"Stop distante {risk_pct_trade*100:.2f}% (> soglia "
            f"{MAX_LOSS_PER_TRADE_PCT*100:.0f}% per trade)."
        )
    if actual_value < target_value * 0.9:
        warnings.append(
            "Size effettiva inferiore al target: cash o max_value bindante."
        )

    return {
        "ok": True,
        "shares": shares,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "risk_per_share": round(risk_per_share, 2),
        "position_value": round(actual_value, 2),
        "position_pct": round(actual_value / total_capital, 4) if total_capital else 0.0,
        "target_value": round(target_value, 2),
        "max_value": round(max_value, 2),
        "cash_available": round(cash_available, 2),
        "avg_score": round(avg_score, 1),
        "conviction": conviction_level,
        "conviction_pct": conviction_pct,
        "risk_total": round(risk_total, 2),
        "risk_pct_trade": round(risk_pct_trade, 4),
        "risk_pct_capital": round(risk_pct_capital, 4),
        "warnings": warnings,
    }
