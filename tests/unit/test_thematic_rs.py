"""Test RS theme/parent (puro, no rete)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.domain.thematic_rs import compute_correlation, compute_rs_vs_parent


def _series(values: list[float], freq: str = "W") -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


def test_rs_score_high_when_theme_outperforms_with_positive_slope():
    # Theme cresce più del parent, accelera negli ultimi mesi
    n = 80
    parent = _series([100.0 * (1 + 0.001 * i) for i in range(n)])
    theme = _series([100.0 * (1 + 0.003 * i + (0.0005 * i if i > 60 else 0)) for i in range(n)])
    out = compute_rs_vs_parent(theme, parent)
    assert out["rs_ratio"] is not None
    assert out["rs_ratio"] > 1.0
    assert out["rs_slope"] > 0
    assert out["score"] >= 70.0


def test_rs_score_low_when_theme_lags_with_negative_slope():
    n = 80
    parent = _series([100.0 * (1 + 0.002 * i) for i in range(n)])
    # Theme underperforms and slope negative
    theme = _series([100.0 * (1 + 0.001 * i - (0.0005 * i if i > 60 else 0)) for i in range(n)])
    out = compute_rs_vs_parent(theme, parent)
    assert out["rs_ratio"] < 1.0
    assert out["rs_slope"] <= 0
    assert out["score"] <= 25.0


def test_rs_returns_neutral_on_short_history():
    parent = _series([100.0] * 10)
    theme = _series([101.0] * 10)
    out = compute_rs_vs_parent(theme, parent)
    assert out["score"] == 50.0
    assert out["rs_ratio"] is None


def test_rs_neutral_when_parent_none():
    out = compute_rs_vs_parent(None, None)
    assert out["score"] == 50.0


def test_correlation_high_for_proportional_series():
    n = 200
    rng = np.random.default_rng(42)
    parent_returns = rng.normal(0, 0.01, n)
    parent_close = 100 * np.cumprod(1 + parent_returns)
    # Theme = parent * 1.3 + small idiosyncratic noise → corr ~0.99
    theme_returns = parent_returns * 1.3 + rng.normal(0, 0.0005, n)
    theme_close = 100 * np.cumprod(1 + theme_returns)
    p = pd.Series(parent_close, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    t = pd.Series(theme_close, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    corr = compute_correlation(t, p, lookback_days=60)
    assert corr is not None
    assert corr > 0.9


def test_correlation_low_for_independent_series():
    n = 200
    rng = np.random.default_rng(7)
    p = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    t = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    p_s = pd.Series(p, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    t_s = pd.Series(t, index=pd.date_range("2020-01-01", periods=n, freq="B"))
    corr = compute_correlation(t_s, p_s, lookback_days=60)
    assert corr is not None
    assert abs(corr) < 0.5


def test_correlation_none_on_short_history():
    p = _series([100.0] * 20, freq="B")
    t = _series([101.0] * 20, freq="B")
    assert compute_correlation(t, p, lookback_days=60) is None
