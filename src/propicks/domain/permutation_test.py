"""Permutation test null hypothesis (Fase E.3 SIGNAL_ROADMAP).

Test if observed Sharpe è statisticamente significativo vs distribuzione
under H0 = "signal random". Per ognuna di N permutation:

1. Shuffle i signal/positions (mantenendo i prezzi)
2. Re-run "trade" con shuffled signal
3. Compute Sharpe della shuffled sequence

Distribution dei Sharpe random → percentile dell'observed Sharpe = p-value.

## ⚠ Limitazioni

**Sharpe è permutation-INVARIANT** (shuffle preserva mean + stdev →
Sharpe identico). NON usare permutation test su Sharpe — uso path-
dependent metric (Max DD, Calmar, autocorrelazione).

Per test signal-level proper serve shuffle al livello PRICE returns +
re-simulate strategy con prezzi shufflati (random walk hypothesis). Questo
modulo opera al livello per-trade returns già computed.

## API

- ``permutation_test_max_drawdown(returns, n_permutations) -> dict`` — path-dependent
- ``permutation_test_metric(returns, metric_fn, n_permutations) -> dict`` — generic

## Reference

- Aronson (2007), *Evidence-Based Technical Analysis* — permutation tests
  for trading strategies
- Lopez de Prado (2018), AFML cap.13 "Backtesting on Synthetic Data"
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

import numpy as np


def _sharpe(arr: list[float]) -> float | None:
    """Sharpe per-trade non-annualized."""
    if len(arr) < 2:
        return None
    m = sum(arr) / len(arr)
    var = sum((x - m) ** 2 for x in arr) / (len(arr) - 1)
    if var <= 0:
        return None
    return m / math.sqrt(var)


def _max_drawdown_pct(returns: list[float]) -> float:
    """Max drawdown % (path-dependent). Compounded equity curve."""
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        dd = (equity - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
    return max_dd * 100.0


def _calmar_ratio(returns: list[float]) -> float:
    """Calmar = total return / |max DD| (path-dependent)."""
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        dd = (equity - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
    total_ret = equity - 1.0
    return total_ret / abs(max_dd) if max_dd < 0 else 999.0


def permutation_test_max_drawdown(
    returns: Sequence[float],
    *,
    n_permutations: int = 1000,
    seed: int = 42,
) -> dict:
    """Permutation test su MAX DRAWDOWN — path-dependent metric.

    **Razionale**: Sharpe = mean/stdev è permutation-INVARIANT (shuffle
    preserva mean + stdev). NON è valid per permutation testing.
    Max DD è path-dependent: ordine cluster di losses determina DD severity.
    Permuting → distribuzione DD shuffled, observed può essere "lucky"
    (clusters scattered) o "unlucky" (clusters consecutive).

    H0: ordine returns è random.
    H1 (less): observed DD è migliore di random — strategia produce
    drawdown geometrically smaller della random walk con stessa distribution.

    Args:
        returns: per-trade returns frazionali.
        n_permutations: shuffles.
        seed: riproducibilità.

    Returns:
        Dict con observed, null distribution, p_value (frazione shuffle con
        DD ≥ observed, cioè DD MENO severo). decision.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {"error": "insufficient samples", "n_obs": len(arr)}

    observed_dd = _max_drawdown_pct(arr)

    rng = np.random.default_rng(seed)
    arr_np = np.asarray(arr, dtype=float)

    null_dds = []
    n_better = 0  # shuffle con DD meno severo (closer to 0)
    for _ in range(n_permutations):
        rng.shuffle(arr_np)
        dd = _max_drawdown_pct(arr_np.tolist())
        null_dds.append(dd)
        if dd >= observed_dd:  # less negative = better
            n_better += 1

    if not null_dds:
        return {"error": "no valid permutations"}

    p_value = n_better / len(null_dds)
    decision = (
        "SIGNIFICANT_p005" if p_value < 0.05
        else "MARGINAL_p010" if p_value < 0.10
        else "NOT_SIGNIFICANT"
    )

    return {
        "observed_max_dd_pct": round(observed_dd, 4),
        "null_mean_dd_pct": round(statistics.mean(null_dds), 4),
        "null_std_dd_pct": (
            round(statistics.stdev(null_dds), 4)
            if len(null_dds) > 1 else 0.0
        ),
        "null_min_dd_pct": round(min(null_dds), 4),  # most severe shuffle DD
        "null_max_dd_pct": round(max(null_dds), 4),  # least severe shuffle DD
        "p_value_one_sided_better": round(p_value, 4),
        "decision": decision,
        "interpretation": (
            "observed DD migliore di random (signal aggrega losses meglio)"
            if p_value < 0.10 else
            "observed DD non distinguibile da random walk con stessa distribution"
        ),
        "n_permutations": len(null_dds),
        "n_obs": len(arr),
    }


def permutation_test_metric(
    returns: Sequence[float],
    metric_fn,
    *,
    n_permutations: int = 1000,
    seed: int = 42,
    direction: str = "greater",
) -> dict:
    """Generic permutation test su metric.

    Args:
        returns: original returns.
        metric_fn: callable ``list[float] -> float`` (es. profit factor).
        n_permutations: shuffles.
        seed: riproducibilità.
        direction: ``'greater'`` (default — H1: metric > null) o ``'less'``
            (H1: metric < null, es. drawdown should be smaller than random).

    Returns:
        Dict con observed, null distribution stats, p_value, decision.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {"error": "insufficient samples", "n_obs": len(arr)}

    try:
        observed = float(metric_fn(arr))
    except Exception as exc:
        return {"error": f"metric_fn failed: {exc}"}

    if not math.isfinite(observed):
        return {"error": "metric_fn returned non-finite", "observed": observed}

    rng = np.random.default_rng(seed)
    arr_np = np.asarray(arr, dtype=float)

    null_values = []
    n_more_extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(arr_np)
        try:
            v = float(metric_fn(arr_np.tolist()))
            if not math.isfinite(v):
                continue
            null_values.append(v)
            if direction == "greater" and v >= observed:
                n_more_extreme += 1
            elif direction == "less" and v <= observed:
                n_more_extreme += 1
        except Exception:
            continue

    if not null_values:
        return {"error": "no valid permutations", "observed": observed}

    p_value = n_more_extreme / len(null_values)
    decision = (
        "SIGNIFICANT_p005" if p_value < 0.05
        else "MARGINAL_p010" if p_value < 0.10
        else "NOT_SIGNIFICANT"
    )

    return {
        "observed": round(observed, 4),
        "null_mean": round(statistics.mean(null_values), 4),
        "null_std": (
            round(statistics.stdev(null_values), 4)
            if len(null_values) > 1 else 0.0
        ),
        "p_value": round(p_value, 4),
        "decision": decision,
        "direction": direction,
        "n_permutations": len(null_values),
        "n_obs": len(arr),
    }
