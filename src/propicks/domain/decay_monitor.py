"""Strategy decay monitor (Fase D.4 SIGNAL_ROADMAP).

Una strategia in produzione può degradare silenziosamente: edge accademico
arbitraggiato (overcrowding momentum 2018-19), regime change, o fattori
non-stationari. Decay monitor genera early warning prima che drawdown
materiale.

## Tecniche implementate

1. **Rolling Sharpe**: confronta Sharpe ultimi N trade con Sharpe expected
   da backtest. Drift > 1 stdev = warning.
2. **CUSUM** (Page 1954): cumulative sum of deviations from expected mean.
   Segnala drift cumulativo. Più sensibile di rolling per change graduali.
3. **SPRT** (Wald 1945): sequential probability ratio test. Decision boundary
   per "edge alive" vs "edge dead" hypothesis. Useful per stopping decision.

## API

- ``rolling_sharpe(returns, window) -> pd.Series``
- ``cusum_decay_detector(returns, expected_mean, sensitivity) -> dict``
- ``sprt_test(returns, h0_mean, h1_mean, alpha=0.05, beta=0.20) -> dict``
- ``decay_alert_summary(returns, expected_sharpe) -> dict``

Pure functions. Input list/numpy/pd.Series. No I/O.

## Reference

- Page (1954), "Continuous inspection schemes" (CUSUM)
- Wald (1945), "Sequential tests of statistical hypotheses" (SPRT)
- Lopez de Prado (2018), AFML cap.13 "Backtesting on Synthetic Data"
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def rolling_sharpe(
    returns: Sequence[float],
    window: int = 30,
) -> list[float | None]:
    """Rolling Sharpe non-annualizzato. Mean / stdev su window mobile.

    Args:
        returns: per-trade or per-bar returns frazionali.
        window: lookback (default 30 trade).

    Returns:
        Lista lunghezza ``len(returns)``. Primi ``window-1`` = None.
    """
    arr = list(returns)
    out: list[float | None] = []
    for i in range(len(arr)):
        if i + 1 < window:
            out.append(None)
            continue
        slice_ = arr[i + 1 - window : i + 1]
        m = sum(slice_) / window
        var = sum((x - m) ** 2 for x in slice_) / (window - 1) if window > 1 else 0
        if var <= 0:
            out.append(0.0)
        else:
            out.append(m / math.sqrt(var))
    return out


def cusum_decay_detector(
    returns: Sequence[float],
    expected_mean: float,
    *,
    sensitivity: float = 0.5,
    threshold_h: float = 5.0,
) -> dict:
    """CUSUM per rilevare drift downward del mean returns.

    Algoritmo (Page 1954, one-sided lower CUSUM):
        S[0] = 0
        S[t] = max(0, S[t-1] - (returns[t] - expected_mean) - K)

    dove ``K = sensitivity × σ_estimated``. Se ``S[t] > threshold_h × σ``
    → decay detected.

    Args:
        returns: lista returns observed.
        expected_mean: mean expected da backtest (es. ~0.005 per 0.5%/trade).
        sensitivity: K reference value (default 0.5σ slack).
        threshold_h: alarm threshold in σ units (default 5σ).

    Returns:
        Dict con cusum_series, alarm_index (None se no alarm),
        decay_detected (bool), n_obs.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {
            "cusum_series": [], "alarm_index": None,
            "decay_detected": False, "n_obs": len(arr),
            "note": "insufficient samples (< 5)",
        }
    sigma = float(np.std(arr, ddof=1))
    if sigma <= 0:
        return {
            "cusum_series": [0.0] * len(arr), "alarm_index": None,
            "decay_detected": False, "n_obs": len(arr),
            "note": "zero variance",
        }
    k = sensitivity * sigma
    threshold = threshold_h * sigma

    s = 0.0
    series = []
    alarm_idx = None
    for i, r in enumerate(arr):
        # Lower CUSUM: detect downward drift (returns < expected)
        s = max(0.0, s - (r - expected_mean) - k)
        series.append(round(s, 6))
        if s > threshold and alarm_idx is None:
            alarm_idx = i

    return {
        "cusum_series": series,
        "alarm_index": alarm_idx,
        "decay_detected": alarm_idx is not None,
        "sigma_estimated": round(sigma, 6),
        "k_slack": round(k, 6),
        "threshold": round(threshold, 6),
        "n_obs": len(arr),
    }


def sprt_test(
    returns: Sequence[float],
    *,
    h0_mean: float = 0.0,
    h1_mean: float | None = None,
    alpha: float = 0.05,
    beta: float = 0.20,
    sigma: float | None = None,
) -> dict:
    """Sequential Probability Ratio Test per "edge alive vs dead".

    H0: edge dead (mean = h0_mean, default 0)
    H1: edge alive (mean = h1_mean)

    Calcola log-likelihood ratio incrementalmente. Decision boundaries:
        A = log((1-beta)/alpha) → reject H0 (edge alive)
        B = log(beta/(1-alpha)) → accept H0 (edge dead)

    Tra A e B: continue sampling.

    Args:
        returns: per-trade returns.
        h0_mean: null hypothesis mean.
        h1_mean: alternative mean. Default = mean osservato (ottimistic).
        alpha: false positive rate (default 0.05).
        beta: false negative rate (default 0.20).
        sigma: known σ. Se None, stimato da returns.

    Returns:
        Dict con log_lr_series, final_log_lr, decision (alive/dead/continue),
        decision_index (when boundary crossed), boundaries.
    """
    arr = list(returns)
    if len(arr) < 5:
        return {
            "decision": "INSUFFICIENT_DATA",
            "n_obs": len(arr),
        }
    if sigma is None or sigma <= 0:
        sigma = float(np.std(arr, ddof=1))
    if sigma <= 0:
        return {"decision": "ZERO_VARIANCE", "n_obs": len(arr)}
    if h1_mean is None:
        h1_mean = float(np.mean(arr))
    if h1_mean <= h0_mean:
        # Test degenerato: alternative non favorevole
        return {
            "decision": "NULL_DOMINATES",
            "n_obs": len(arr),
            "h0_mean": h0_mean,
            "h1_mean": h1_mean,
        }

    A = math.log((1 - beta) / alpha)
    B = math.log(beta / (1 - alpha))

    # Log-LR per gaussian: sum_i [(r_i - h0)^2 - (r_i - h1)^2] / (2 σ²)
    log_lr = 0.0
    series = []
    decision = "CONTINUE"
    decision_idx = None
    for i, r in enumerate(arr):
        log_lr += ((r - h0_mean) ** 2 - (r - h1_mean) ** 2) / (2 * sigma ** 2)
        series.append(round(log_lr, 4))
        if decision == "CONTINUE":
            if log_lr >= A:
                decision = "EDGE_ALIVE"
                decision_idx = i
            elif log_lr <= B:
                decision = "EDGE_DEAD"
                decision_idx = i

    return {
        "log_lr_series": series,
        "final_log_lr": round(log_lr, 4),
        "decision": decision,
        "decision_index": decision_idx,
        "boundary_a": round(A, 4),
        "boundary_b": round(B, 4),
        "h0_mean": h0_mean,
        "h1_mean": h1_mean,
        "sigma": round(sigma, 6),
        "n_obs": len(arr),
    }


def decay_alert_summary(
    returns: Sequence[float],
    expected_sharpe_per_trade: float,
    *,
    rolling_window: int = 30,
    cusum_threshold_h: float = 5.0,
) -> dict:
    """Composite alert: rolling Sharpe + CUSUM + SPRT in single call.

    Args:
        returns: per-trade returns observed.
        expected_sharpe_per_trade: Sharpe atteso da backtest baseline_v2.
        rolling_window: window per rolling Sharpe (default 30).
        cusum_threshold_h: threshold CUSUM in σ (default 5).

    Returns:
        Dict con tutti i 3 detector + decision finale combinata.
    """
    arr = list(returns)
    if not arr:
        return {"decision": "NO_DATA", "n_obs": 0}

    arr_np = np.asarray(arr, dtype=float)
    sigma = float(np.std(arr_np, ddof=1)) if len(arr_np) > 1 else 0.0
    expected_mean = expected_sharpe_per_trade * sigma

    # Rolling Sharpe (latest)
    rs = rolling_sharpe(arr, rolling_window)
    rs_latest = next((x for x in reversed(rs) if x is not None), None)
    rolling_warning = (
        rs_latest is not None
        and rs_latest < expected_sharpe_per_trade * 0.5  # < 50% expected
    )

    # CUSUM
    cusum = cusum_decay_detector(
        arr, expected_mean, threshold_h=cusum_threshold_h,
    )

    # SPRT
    sprt = sprt_test(arr, h0_mean=0.0)

    # Composite decision
    if cusum["decay_detected"] or sprt["decision"] == "EDGE_DEAD":
        decision = "ALERT_DECAY"
    elif rolling_warning:
        decision = "WARNING"
    elif sprt["decision"] == "EDGE_ALIVE":
        decision = "ALIVE"
    else:
        decision = "MONITOR"

    return {
        "decision": decision,
        "rolling_sharpe_latest": rs_latest,
        "rolling_sharpe_threshold_warn": expected_sharpe_per_trade * 0.5,
        "cusum_decay_detected": cusum["decay_detected"],
        "cusum_alarm_index": cusum.get("alarm_index"),
        "sprt_decision": sprt["decision"],
        "sprt_decision_index": sprt.get("decision_index"),
        "n_obs": len(arr),
        "expected_mean": expected_mean,
        "expected_sharpe_per_trade": expected_sharpe_per_trade,
    }
