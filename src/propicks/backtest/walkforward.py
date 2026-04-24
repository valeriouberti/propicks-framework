"""Walk-forward OOS split + Monte Carlo bootstrap (Phase 6).

## Walk-forward

Split temporale train/test per validare la calibrazione dei pesi scoring:

```
  train window (70%)    test window (30%)
  [-------------------][--------]
  2020-01            2024-06    2026-04
```

In-sample (train): calibri i pesi se fitting è necessario. Out-of-sample
(test): misuri performance SU DATI MAI VISTI dal fitter. Differenza tra
train e test Sharpe = overfitting indicator.

**Nota**: in questo framework i pesi scoring sono FISSATI in ``config.py``
(non fitted). Il walk-forward serve principalmente per:
1. Confermare che la performance è stabile in finestre temporali diverse
2. Detectare regime shift (edge degrada in BEAR vs BULL)
3. Provide robustness evidence prima del gate Phase 7

## Monte Carlo bootstrap

Bootstrap con sostituzione su trade sequence. Dato un set di N trade con
P&L storici, campiona N trade con replacement 500-1000 volte e ricalcola
metriche (Sharpe, WinRate, MaxDD). Distribuzione risultante → CI 95%.

**Output**: per ogni metrica, (mean, lower_95, upper_95). Se l'intervallo è
stretto, il risultato è robusto. Se largo, il punto è dominato dal luck.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from propicks.backtest.portfolio_engine import (
    BacktestConfig,
    ClosedTrade,
    PortfolioState,
    simulate_portfolio,
)


# ---------------------------------------------------------------------------
# Walk-forward split
# ---------------------------------------------------------------------------
@dataclass
class WalkForwardResult:
    train_window: tuple[date, date]
    test_window: tuple[date, date]
    train_state: PortfolioState
    test_state: PortfolioState
    train_metrics: dict
    test_metrics: dict
    degradation_score: float  # test_sharpe - train_sharpe


def walk_forward_split(
    universe: dict[str, pd.DataFrame],
    scoring_fn,
    *,
    split_ratio: float = 0.70,
    regime_series: pd.Series | None = None,
    earnings_dates: dict[str, str] | None = None,
    config: BacktestConfig | None = None,
) -> WalkForwardResult:
    """Esegue backtest in-sample (train) + out-of-sample (test).

    Args:
        split_ratio: frazione del totale dedicata al train (default 0.70).
            Il test va dal boundary fino a end.

    Returns: ``WalkForwardResult`` con stati + metriche + degradation_score.
        ``degradation_score > 0`` = test performa meglio di train (probabile
        random); ``< 0`` = overfitting evidence (train > test).
    """
    if split_ratio <= 0 or split_ratio >= 1:
        raise ValueError(f"split_ratio {split_ratio} deve essere in (0, 1)")

    config = config or BacktestConfig()

    # Trova union dates
    all_dates = sorted({d for df in universe.values() for d in df.index})
    if len(all_dates) < 100:
        raise ValueError(f"Dati insufficienti: {len(all_dates)} bar (min 100 per split)")

    split_idx = int(len(all_dates) * split_ratio)
    train_end = _as_date(all_dates[split_idx - 1])
    test_start = _as_date(all_dates[split_idx])

    train_start = _as_date(all_dates[0])
    test_end = _as_date(all_dates[-1])

    # Train backtest
    train_state = simulate_portfolio(
        universe=universe,
        scoring_fn=scoring_fn,
        regime_series=regime_series,
        earnings_dates=earnings_dates,
        config=config,
        start_date=train_start,
        end_date=train_end,
    )

    # Test backtest (parte da capital originale — no carryover)
    test_state = simulate_portfolio(
        universe=universe,
        scoring_fn=scoring_fn,
        regime_series=regime_series,
        earnings_dates=earnings_dates,
        config=config,
        start_date=test_start,
        end_date=test_end,
    )

    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    train_metrics = compute_portfolio_metrics(train_state)
    test_metrics = compute_portfolio_metrics(test_state)

    train_sharpe = train_metrics.get("sharpe_annualized") or 0.0
    test_sharpe = test_metrics.get("sharpe_annualized") or 0.0
    degradation = test_sharpe - train_sharpe

    return WalkForwardResult(
        train_window=(train_start, train_end),
        test_window=(test_start, test_end),
        train_state=train_state,
        test_state=test_state,
        train_metrics=train_metrics,
        test_metrics=test_metrics,
        degradation_score=round(degradation, 4),
    )


# ---------------------------------------------------------------------------
# Monte Carlo bootstrap
# ---------------------------------------------------------------------------
@dataclass
class MonteCarloResult:
    n_samples: int
    sharpe_mean: float
    sharpe_ci: tuple[float, float]   # (lower_95, upper_95)
    win_rate_mean: float
    win_rate_ci: tuple[float, float]
    total_return_mean: float
    total_return_ci: tuple[float, float]
    max_dd_mean: float
    max_dd_ci: tuple[float, float]
    robustness_score: float  # higher = more robust


def monte_carlo_bootstrap(
    closed_trades: list[ClosedTrade],
    *,
    n_samples: int = 500,
    seed: int = 42,
) -> MonteCarloResult:
    """Bootstrap su trade sequence → CI 95% delle metriche.

    Args:
        closed_trades: lista trade chiusi dal backtest
        n_samples: simulazioni (500-1000 tipico)
        seed: riproducibilità

    Returns: CI 95% per Sharpe / WinRate / TotalReturn / MaxDD.
        ``robustness_score`` ∈ [0, 1]: rapporto
        ``(sharpe_lower_95 / sharpe_mean)``. >0.7 = robusto.
    """
    if not closed_trades:
        return MonteCarloResult(
            n_samples=0,
            sharpe_mean=0.0, sharpe_ci=(0.0, 0.0),
            win_rate_mean=0.0, win_rate_ci=(0.0, 0.0),
            total_return_mean=0.0, total_return_ci=(0.0, 0.0),
            max_dd_mean=0.0, max_dd_ci=(0.0, 0.0),
            robustness_score=0.0,
        )

    returns_pct = [t.pnl_pct for t in closed_trades]
    n_trades = len(returns_pct)
    rng = np.random.default_rng(seed)

    sharpes = []
    win_rates = []
    total_rets = []
    max_dds = []

    for _ in range(n_samples):
        # Resample N trade with replacement
        idx = rng.integers(0, n_trades, size=n_trades)
        sample = [returns_pct[i] for i in idx]

        # Sharpe trade-level (mean / stdev)
        if len(sample) >= 2:
            mean = statistics.mean(sample)
            std = statistics.stdev(sample)
            sharpes.append(mean / std if std > 0 else 0.0)

        # Win rate
        wins = sum(1 for r in sample if r > 0)
        win_rates.append(wins / len(sample) if sample else 0.0)

        # Total return (compounded)
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in sample:
            equity *= (1 + r / 100)
            peak = max(peak, equity)
            dd = (equity - peak) / peak
            max_dd = min(max_dd, dd)
        total_rets.append(equity - 1.0)
        max_dds.append(max_dd)

    def _ci(values: list[float]) -> tuple[float, float]:
        if not values:
            return (0.0, 0.0)
        sorted_v = sorted(values)
        lower = sorted_v[int(0.025 * len(sorted_v))]
        upper = sorted_v[int(0.975 * len(sorted_v))]
        return (round(lower, 4), round(upper, 4))

    sharpe_mean = round(statistics.mean(sharpes), 4) if sharpes else 0.0
    win_rate_mean = round(statistics.mean(win_rates), 4) if win_rates else 0.0
    total_ret_mean = round(statistics.mean(total_rets), 4) if total_rets else 0.0
    max_dd_mean = round(statistics.mean(max_dds), 4) if max_dds else 0.0

    sharpe_ci = _ci(sharpes)
    robustness = 0.0
    if sharpe_mean != 0:
        robustness = round(max(0.0, min(1.0, sharpe_ci[0] / sharpe_mean)), 4)

    return MonteCarloResult(
        n_samples=n_samples,
        sharpe_mean=sharpe_mean,
        sharpe_ci=sharpe_ci,
        win_rate_mean=win_rate_mean,
        win_rate_ci=_ci(win_rates),
        total_return_mean=total_ret_mean,
        total_return_ci=_ci(total_rets),
        max_dd_mean=max_dd_mean,
        max_dd_ci=_ci(max_dds),
        robustness_score=robustness,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _as_date(t) -> date:
    if isinstance(t, date) and not hasattr(t, "time"):
        return t
    if hasattr(t, "date"):
        return t.date()
    return t
