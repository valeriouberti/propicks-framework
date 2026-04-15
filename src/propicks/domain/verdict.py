"""Metriche qualitative sulla strategia.

Logica pura, testabile con liste di pnl_pct senza dipendenze esterne.
"""

from __future__ import annotations


def max_drawdown(pnls_pct: list[float]) -> float:
    """Max drawdown peak-to-trough su equity curve composta.

    Parte da equity=1.0 e moltiplica per (1 + pnl_pct/100) per ogni trade.
    Ritorna il drawdown percentuale massimo osservato.
    """
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in pnls_pct:
        equity *= 1 + p / 100
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


def verdict(wr: float, pf: float, n: int) -> str:
    """Verdetto qualitativo della strategia basato su win rate e profit factor."""
    if n < 20:
        return "DATI INSUFFICIENTI (< 20 trade chiusi)"
    if wr >= 0.50 and pf >= 1.5:
        return "PROFITTEVOLE"
    if wr >= 0.40 and pf >= 1.2:
        return "MARGINALE"
    return "PERDENTE"
