"""Stationary bootstrap (Fase E.2 SIGNAL_ROADMAP).

Politis-Romano stationary bootstrap (1994). Generalizza Monte Carlo i.i.d.
preservando autocorrelazione delle returns via blocchi geometric-distributed.

## Razionale

Monte Carlo bootstrap classico (già in `backtest/walkforward.py`) campiona
i.i.d. con sostituzione → assume independence dei trade. Realtà: returns
hanno autocorrelazione (volatility clustering, momentum/reversion patterns).

Stationary bootstrap (Politis-Romano 1994):
- Sample blocco di lunghezza geometric-distributed (mean L)
- Concatenare blocchi finché si raggiunge sample size desiderato
- Wrap-around per evitare bias di posizione

Output: distribuzione metriche (Sharpe, max DD) più realistic vs
i.i.d. — CI tipicamente più ampi (acknowledge serial dependence).

## Reference

- Politis & Romano (1994), "The Stationary Bootstrap", *JASA* 89(428)
- Davison & Hinkley (1997), *Bootstrap Methods and Their Application*

## API

- ``stationary_bootstrap_sample(returns, mean_block_len, seed) -> list[float]``
- ``bootstrap_sharpe_distribution(returns, n_samples, mean_block_len) -> dict``
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

import numpy as np


def stationary_bootstrap_sample(
    returns: Sequence[float],
    *,
    mean_block_len: int = 5,
    seed: int | None = None,
) -> list[float]:
    """Single sample stationary bootstrap.

    Args:
        returns: original sequence (es. per-trade pnl_pct frazionali).
        mean_block_len: average block length L. Geometric distribution
            con p = 1/L. Default 5 trade.
        seed: riproducibilità.

    Returns:
        Lista lunga ``len(returns)`` campionata via blocchi geometrici.
    """
    n = len(returns)
    if n == 0:
        return []
    if mean_block_len <= 0:
        raise ValueError(f"mean_block_len must be > 0, got {mean_block_len}")

    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block_len

    out: list[float] = []
    while len(out) < n:
        # Random start
        start = int(rng.integers(0, n))
        # Geometric block length (1, 2, 3, ...)
        block_len = max(1, int(rng.geometric(p)))
        for k in range(block_len):
            if len(out) >= n:
                break
            idx = (start + k) % n  # wrap-around per stationarity
            out.append(float(returns[idx]))
    return out


def bootstrap_sharpe_distribution(
    returns: Sequence[float],
    *,
    n_samples: int = 1000,
    mean_block_len: int = 5,
    seed: int = 42,
) -> dict:
    """Distribuzione Sharpe via stationary bootstrap.

    Args:
        returns: per-trade returns (frazionali).
        n_samples: bootstrap iterations (default 1000).
        mean_block_len: average block size (default 5).
        seed: riproducibilità.

    Returns:
        Dict con sharpe_mean, sharpe_ci_lower (2.5 pct), sharpe_ci_upper (97.5),
        sharpe_observed, n_samples, mean_block_len, n_obs.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {"error": "insufficient samples", "n_obs": len(arr)}

    # Observed Sharpe
    m = sum(arr) / len(arr)
    var = sum((x - m) ** 2 for x in arr) / (len(arr) - 1)
    sr_observed = m / math.sqrt(var) if var > 0 else 0.0

    # Bootstrap iterations
    sharpes = []
    rng = np.random.default_rng(seed)
    for _ in range(n_samples):
        sample = stationary_bootstrap_sample(
            arr, mean_block_len=mean_block_len,
            seed=int(rng.integers(0, 2**31 - 1)),
        )
        if len(sample) < 2:
            continue
        sm = sum(sample) / len(sample)
        sv = sum((x - sm) ** 2 for x in sample) / (len(sample) - 1)
        if sv > 0:
            sharpes.append(sm / math.sqrt(sv))

    if not sharpes:
        return {"error": "no valid samples", "n_obs": len(arr)}

    sharpes_sorted = sorted(sharpes)
    lo_idx = int(0.025 * len(sharpes_sorted))
    hi_idx = int(0.975 * len(sharpes_sorted))

    return {
        "sharpe_observed": round(sr_observed, 4),
        "sharpe_mean": round(statistics.mean(sharpes), 4),
        "sharpe_median": round(statistics.median(sharpes), 4),
        "sharpe_std": round(statistics.stdev(sharpes), 4) if len(sharpes) > 1 else 0.0,
        "sharpe_ci_lower": round(sharpes_sorted[lo_idx], 4),
        "sharpe_ci_upper": round(sharpes_sorted[hi_idx], 4),
        "n_samples": n_samples,
        "mean_block_len": mean_block_len,
        "n_obs": len(arr),
    }


def bootstrap_metric_distribution(
    returns: Sequence[float],
    metric_fn,
    *,
    n_samples: int = 1000,
    mean_block_len: int = 5,
    seed: int = 42,
) -> dict:
    """Generic distribuzione metric via bootstrap.

    Args:
        returns: original returns.
        metric_fn: callable ``list[float] -> float`` (es. lambda r: max_dd(r)).
        n_samples, mean_block_len, seed: come sopra.

    Returns:
        Dict con observed, mean, median, std, ci_lower, ci_upper, n_samples.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {"error": "insufficient samples", "n_obs": len(arr)}
    try:
        observed = float(metric_fn(arr))
    except Exception as exc:
        return {"error": f"metric_fn observed failed: {exc}", "n_obs": len(arr)}

    values = []
    rng = np.random.default_rng(seed)
    for _ in range(n_samples):
        sample = stationary_bootstrap_sample(
            arr, mean_block_len=mean_block_len,
            seed=int(rng.integers(0, 2**31 - 1)),
        )
        try:
            v = float(metric_fn(sample))
            if math.isfinite(v):
                values.append(v)
        except Exception:
            continue

    if not values:
        return {"error": "no valid metric samples", "n_obs": len(arr)}

    sorted_v = sorted(values)
    lo_idx = int(0.025 * len(sorted_v))
    hi_idx = int(0.975 * len(sorted_v))
    return {
        "observed": round(observed, 4),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "ci_lower": round(sorted_v[lo_idx], 4),
        "ci_upper": round(sorted_v[hi_idx], 4),
        "n_samples": len(values),
        "mean_block_len": mean_block_len,
        "n_obs": len(arr),
    }
