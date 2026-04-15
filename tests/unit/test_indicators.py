"""Test indicatori tecnici puri."""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi, pct_change


def test_ema_constant_series_equals_constant():
    s = pd.Series([10.0] * 50)
    ema = compute_ema(s, 20)
    assert ema.iloc[-1] == 10.0


def test_rsi_all_gains_returns_100():
    # Serie strettamente crescente → avg_loss = 0 → RSI = 100 (non NaN)
    s = pd.Series(range(1, 100), dtype=float)
    rsi = compute_rsi(s, period=14)
    assert rsi.iloc[-1] == 100.0
    assert not np.isnan(rsi.iloc[-1])


def test_rsi_range_is_0_to_100():
    np.random.seed(42)
    s = pd.Series(np.cumsum(np.random.randn(200)) + 100)
    rsi = compute_rsi(s, period=14).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_atr_positive_for_volatile_series():
    high = pd.Series([10, 12, 11, 13, 14, 12, 15, 16] * 5, dtype=float)
    low = pd.Series([8, 10, 9, 11, 12, 10, 13, 14] * 5, dtype=float)
    close = pd.Series([9, 11, 10, 12, 13, 11, 14, 15] * 5, dtype=float)
    atr = compute_atr(high, low, close, period=14)
    assert atr.iloc[-1] > 0


def test_pct_change_handles_short_series():
    s = pd.Series([100.0, 101.0, 102.0])
    assert pct_change(s, bars=10) is None


def test_pct_change_basic():
    s = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
    # bars=5 → confronto iloc[-6] = 100 con iloc[-1] = 110 → +10%
    assert pct_change(s, bars=5) == 0.1
