"""Metriche di performance sul risultato di un backtest.

Tutte le funzioni sono pure: prendono trades/equity series, ritornano scalari.
Convenzione: returns come frazione (0.1 = 10%), non percentuali.

Annualizzazione: 252 bar/anno per Sharpe/Sortino (standard US equity). Per
ticker EU/IT servirebbe 256-258 ma la differenza è trascurabile su orizzonti
lunghi — preferita la convenzione standard per comparabilità.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import pandas as pd

from propicks.backtest.engine import BacktestResult, Trade

TRADING_DAYS_PER_YEAR = 252


def win_rate(trades: Iterable[Trade]) -> float | None:
    closed = [t for t in trades if t.pnl_pct is not None]
    if not closed:
        return None
    wins = sum(1 for t in closed if t.pnl_pct > 0)
    return wins / len(closed)


def profit_factor(trades: Iterable[Trade]) -> float | None:
    """Somma winners / |somma losers|. None se niente loss (infinito)."""
    closed = [t for t in trades if t.pnl_pct is not None]
    if not closed:
        return None
    gains = sum(t.pnl_pct for t in closed if t.pnl_pct > 0)
    losses = sum(t.pnl_pct for t in closed if t.pnl_pct < 0)
    if losses == 0:
        return None
    return gains / abs(losses)


def avg_win_loss(trades: Iterable[Trade]) -> tuple[float | None, float | None]:
    closed = [t for t in trades if t.pnl_pct is not None]
    wins = [t.pnl_pct for t in closed if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in closed if t.pnl_pct < 0]
    avg_w = sum(wins) / len(wins) if wins else None
    avg_l = sum(losses) / len(losses) if losses else None
    return avg_w, avg_l


def max_drawdown(equity: pd.Series) -> float | None:
    """Massimo drawdown come frazione negativa (-0.15 = -15%).

    Calcolato sull'equity curve: (equity - running_max) / running_max.
    """
    if equity.empty:
        return None
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return float(dd.min())


def cagr(equity: pd.Series) -> float | None:
    """CAGR = (final / initial)^(1/years) - 1."""
    if len(equity) < 2:
        return None
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    if initial <= 0:
        return None
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return None
    years = days / 365.25
    if years < 0.1:
        return None
    return (final / initial) ** (1.0 / years) - 1.0


def sharpe_ratio(equity: pd.Series, rf: float = 0.0) -> float | None:
    """Sharpe annualizzato sui daily returns dell'equity curve.

    rf è il risk-free rate *annualizzato* (es. 0.04 = 4%).
    """
    if len(equity) < 30:
        return None
    daily_ret = equity.pct_change().dropna()
    if daily_ret.std() == 0:
        return None
    daily_rf = rf / TRADING_DAYS_PER_YEAR
    excess = daily_ret - daily_rf
    return float(
        excess.mean() / daily_ret.std() * math.sqrt(TRADING_DAYS_PER_YEAR)
    )


def sortino_ratio(equity: pd.Series, rf: float = 0.0) -> float | None:
    """Sortino: usa downside deviation invece di std totale."""
    if len(equity) < 30:
        return None
    daily_ret = equity.pct_change().dropna()
    daily_rf = rf / TRADING_DAYS_PER_YEAR
    excess = daily_ret - daily_rf
    downside = daily_ret[daily_ret < 0]
    if len(downside) == 0 or downside.std() == 0:
        return None
    return float(
        excess.mean() / downside.std() * math.sqrt(TRADING_DAYS_PER_YEAR)
    )


def expectancy(trades: Iterable[Trade]) -> float | None:
    """Expected P&L per trade (frazione). wr*avg_win + (1-wr)*avg_loss."""
    wr = win_rate(trades)
    aw, al = avg_win_loss(trades)
    if wr is None or aw is None or al is None:
        if wr is not None and aw is not None and al is None:
            return wr * aw
        if wr is not None and al is not None and aw is None:
            return (1 - wr) * al
        return None
    return wr * aw + (1 - wr) * al


def exit_reason_breakdown(trades: Iterable[Trade]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for t in trades:
        if t.exit_reason:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return reasons


def avg_bars_held(trades: Iterable[Trade]) -> float | None:
    closed = [t for t in trades if t.bars_held is not None]
    if not closed:
        return None
    return sum(t.bars_held for t in closed) / len(closed)


def compute_metrics(result: BacktestResult) -> dict:
    """Raccoglie tutte le metriche in un dict pronto per CLI/JSON."""
    trades = result.trades
    equity = result.equity_curve

    aw, al = avg_win_loss(trades)
    return {
        "ticker": result.ticker,
        "period_start": result.period_start.isoformat(),
        "period_end": result.period_end.isoformat(),
        "n_trades": len(trades),
        "signals_generated": result.signals_generated,
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "avg_win_pct": aw,
        "avg_loss_pct": al,
        "expectancy_pct": expectancy(trades),
        "max_drawdown_pct": max_drawdown(equity),
        "cagr_pct": cagr(equity),
        "sharpe": sharpe_ratio(equity),
        "sortino": sortino_ratio(equity),
        "avg_bars_held": avg_bars_held(trades),
        "exit_reasons": exit_reason_breakdown(trades),
        "final_equity": float(equity.iloc[-1]) if not equity.empty else None,
        "initial_equity": float(equity.iloc[0]) if not equity.empty else None,
    }


def aggregate_metrics(results: dict[str, BacktestResult]) -> dict:
    """Metriche aggregate su più ticker. Unifica trades in un unico pool.

    Nota: l'equity curve aggregata è una semplice media delle equity
    normalizzate — NON riflette un portfolio gestito. Serve a vedere la
    forma media, non a proiettare P&L di portfolio.
    """
    all_trades: list[Trade] = []
    for r in results.values():
        all_trades.extend(r.trades)

    # Normalizza ogni equity curve a initial=1.0 e fa la media per data
    # (solo date comuni). Se i ticker hanno storie diverse, la media è sparse.
    normalized = []
    for r in results.values():
        if r.equity_curve.empty:
            continue
        e = r.equity_curve
        initial = float(e.iloc[0])
        if initial > 0:
            normalized.append(e / initial)

    if normalized:
        avg_eq = pd.concat(normalized, axis=1).mean(axis=1)
    else:
        avg_eq = pd.Series(dtype=float)

    aw, al = avg_win_loss(all_trades)
    return {
        "n_tickers": len(results),
        "n_trades": len(all_trades),
        "win_rate": win_rate(all_trades),
        "profit_factor": profit_factor(all_trades),
        "avg_win_pct": aw,
        "avg_loss_pct": al,
        "expectancy_pct": expectancy(all_trades),
        "avg_bars_held": avg_bars_held(all_trades),
        "exit_reasons": exit_reason_breakdown(all_trades),
        "avg_equity_final": float(avg_eq.iloc[-1]) if not avg_eq.empty else None,
        "avg_equity_max_dd": max_drawdown(avg_eq) if not avg_eq.empty else None,
    }
