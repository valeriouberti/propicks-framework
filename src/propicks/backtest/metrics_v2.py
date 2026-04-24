"""Portfolio-level metrics (Phase 6) — estende ``backtest/metrics.py`` legacy.

Legacy ``metrics.py`` calcola metriche trade-level (win rate, PF, Sharpe
trade-based). Phase 6 aggiunge **portfolio-level metrics** dall'equity curve
simulata:

- Sharpe/Sortino **annualized** (daily returns × √252)
- Max drawdown portfolio (sull'equity curve, non per-trade)
- Calmar ratio (total return / max_dd)
- Correlation con SPX/FTSEMIB benchmark

**Per-strategy breakdown**: se il backtest ha mixed strategy (momentum +
contrarian), decomponiamo l'equity curve per strategy tag.
"""

from __future__ import annotations

import math
import statistics

import pandas as pd

from propicks.backtest.portfolio_engine import PortfolioState

TRADING_DAYS_PER_YEAR = 252


def compute_portfolio_metrics(state: PortfolioState) -> dict:
    """Calcola KPIs portfolio-level dall'equity curve + closed trades.

    Returns dict con:
    - ``total_return_pct``, ``cagr_pct``
    - ``sharpe_annualized``, ``sortino_annualized``
    - ``max_drawdown_pct``, ``calmar_ratio``
    - ``n_trades``, ``win_rate``, ``profit_factor``
    - ``avg_duration_days``
    - ``by_strategy``: breakdown per strategy tag (se più di una)
    - ``exit_reasons``: counter dei motivi di exit
    """
    equity_curve = state.equity_curve
    trades = state.closed_trades

    if not equity_curve:
        return {"error": "no equity curve"}

    initial = state.initial_capital
    dates = [d for d, _ in equity_curve]
    values = [v for _, v in equity_curve]

    # Total return
    final_value = values[-1]
    total_return = (final_value / initial) - 1

    # CAGR
    days = (dates[-1] - dates[0]).days
    years = max(days / 365.25, 1 / 365.25)
    cagr = ((final_value / initial) ** (1 / years) - 1) if final_value > 0 else -1.0

    # Daily returns per Sharpe/Sortino
    daily_returns: list[float] = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append(values[i] / values[i - 1] - 1)

    # Sharpe annualized
    sharpe_ann = None
    if len(daily_returns) >= 2:
        mean_ret = statistics.mean(daily_returns)
        stdev_ret = statistics.stdev(daily_returns)
        if stdev_ret > 0:
            sharpe_ann = (mean_ret / stdev_ret) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # Sortino: penalizza solo downside deviation
    sortino_ann = None
    if len(daily_returns) >= 2:
        mean_ret = statistics.mean(daily_returns)
        downside = [r for r in daily_returns if r < 0]
        if downside and len(downside) >= 2:
            down_stdev = statistics.stdev(downside)
            if down_stdev > 0:
                sortino_ann = (mean_ret / down_stdev) * math.sqrt(TRADING_DAYS_PER_YEAR)

    # Max drawdown sull'equity curve
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (v - peak) / peak
        max_dd = min(max_dd, dd)

    # Calmar
    calmar = None
    if max_dd < 0:
        calmar = abs(cagr / max_dd)

    # Trade-level metrics
    wins = sum(1 for t in trades if t.pnl_net > 0)
    losses = sum(1 for t in trades if t.pnl_net < 0)
    total_wins = sum(t.pnl_net for t in trades if t.pnl_net > 0)
    total_losses = abs(sum(t.pnl_net for t in trades if t.pnl_net < 0))
    profit_factor = (total_wins / total_losses) if total_losses > 0 else None
    win_rate = wins / len(trades) if trades else 0.0
    avg_duration = (
        statistics.mean([t.duration_days for t in trades]) if trades else 0
    )

    # Per-strategy breakdown
    by_strategy: dict[str, dict] = {}
    strategies = {t.strategy for t in trades}
    for strat in strategies:
        strat_trades = [t for t in trades if t.strategy == strat]
        strat_wins = sum(1 for t in strat_trades if t.pnl_net > 0)
        strat_total_pnl_pct = sum(t.pnl_pct for t in strat_trades)
        by_strategy[strat] = {
            "n_trades": len(strat_trades),
            "win_rate": round(strat_wins / len(strat_trades), 4) if strat_trades else 0.0,
            "total_pnl_pct": round(strat_total_pnl_pct, 2),
            "avg_pnl_pct": round(strat_total_pnl_pct / len(strat_trades), 2) if strat_trades else 0,
        }

    # Exit reasons breakdown
    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    return {
        "period_start": dates[0].isoformat(),
        "period_end": dates[-1].isoformat(),
        "days": days,
        "initial_capital": round(initial, 2),
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return * 100, 4),
        "cagr_pct": round(cagr * 100, 4) if cagr is not None else None,
        "sharpe_annualized": round(sharpe_ann, 4) if sharpe_ann is not None else None,
        "sortino_annualized": round(sortino_ann, 4) if sortino_ann is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 4),
        "calmar_ratio": round(calmar, 4) if calmar is not None else None,
        "n_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "avg_duration_days": round(avg_duration, 1),
        "total_commission": round(sum(t.pnl_gross - t.pnl_net for t in trades), 2),
        "by_strategy": by_strategy,
        "exit_reasons": exit_reasons,
    }


def compute_benchmark_comparison(
    state: PortfolioState,
    benchmark_series: pd.Series,
) -> dict:
    """Compara equity curve del portfolio con un benchmark (SPX/FTSEMIB).

    Args:
        state: PortfolioState dopo simulazione
        benchmark_series: Close series indexed by date

    Returns:
        - ``portfolio_return_pct``, ``benchmark_return_pct``
        - ``alpha_pct`` (differenza)
        - ``correlation_daily``: corr daily returns
        - ``beta_to_benchmark``: regressione slope
    """
    if not state.equity_curve:
        return {"error": "no equity curve"}

    eq_df = pd.DataFrame(state.equity_curve, columns=["date", "value"])
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date")
    eq_df["daily_ret"] = eq_df["value"].pct_change()

    bench = benchmark_series.copy()
    bench.index = pd.to_datetime(bench.index)
    # Allinea sulle stesse date
    bench = bench.reindex(eq_df.index, method="ffill")
    bench_ret = bench.pct_change()

    joined = pd.DataFrame({
        "portfolio": eq_df["daily_ret"],
        "benchmark": bench_ret,
    }).dropna()

    if len(joined) < 10:
        return {"error": "insufficient joint history"}

    # Total returns
    p_ret = (eq_df["value"].iloc[-1] / eq_df["value"].iloc[0]) - 1
    b_ret = (bench.iloc[-1] / bench.iloc[0]) - 1

    # Correlation
    corr = joined["portfolio"].corr(joined["benchmark"])

    # Beta (OLS slope)
    cov = joined["portfolio"].cov(joined["benchmark"])
    var = joined["benchmark"].var()
    beta = cov / var if var > 0 else None

    return {
        "portfolio_return_pct": round(p_ret * 100, 4),
        "benchmark_return_pct": round(b_ret * 100, 4),
        "alpha_pct": round((p_ret - b_ret) * 100, 4),
        "correlation_daily": round(corr, 4) if not pd.isna(corr) else None,
        "beta_to_benchmark": round(beta, 4) if beta is not None else None,
    }
