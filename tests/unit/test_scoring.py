"""Test delle funzioni di scoring (sub-score puri)."""

from __future__ import annotations

import math

from propicks.domain.scoring import (
    classify,
    score_distance_from_high,
    score_ma_cross,
    score_momentum,
    score_trend,
    score_volatility,
    score_volume,
)


def test_score_trend_full_uptrend():
    # close > ema_fast > ema_slow → trend forte
    assert score_trend(close=110, ema_fast=105, ema_slow=100) == 100.0


def test_score_trend_full_downtrend():
    # close < ema_fast < ema_slow (death cross state) → 0
    assert score_trend(close=90, ema_fast=95, ema_slow=100) == 0.0


def test_score_momentum_sweet_spot():
    assert score_momentum(55) == 100.0


def test_score_momentum_overbought():
    assert score_momentum(85) == 15.0


def test_score_momentum_nan_returns_zero():
    assert score_momentum(float("nan")) == 0.0


def test_score_volume_neutral_when_no_data():
    assert score_volume(current_volume=0, avg_volume=0) == 50.0


def test_score_distance_sweet_spot():
    # ~8% dal massimo → 100
    assert score_distance_from_high(close=92, high_52w=100) == 100.0


def test_score_volatility_optimal():
    # atr/close ~ 2% → 100
    assert score_volatility(atr=2, close=100) == 100.0


def test_score_ma_cross_golden():
    # prev: fast < slow; ora: fast > slow
    assert score_ma_cross(
        ema_fast=105, ema_slow=100, prev_ema_fast=98, prev_ema_slow=100
    ) == 100.0


def test_classify_thresholds():
    assert classify(80).startswith("A")
    assert classify(65).startswith("B")
    assert classify(50).startswith("C")
    assert classify(30).startswith("D")


def test_score_all_in_range():
    """Tutti i sub-score devono ritornare valori in [0, 100]."""
    samples = [
        score_trend(100, 100, 100),
        score_momentum(50),
        score_volume(1000, 1000),
        score_distance_from_high(95, 100),
        score_volatility(1.5, 100),
        score_ma_cross(100, 99, 99, 100),
    ]
    for s in samples:
        assert 0 <= s <= 100 and not math.isnan(s)
