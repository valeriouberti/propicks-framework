"""Test scoring puro thematic (sub-score + gates)."""

from __future__ import annotations

import pandas as pd

from propicks.domain.thematic_scoring import (
    apply_corr_kill_switch,
    apply_regime_gate,
    classify_thematic,
    score_abs_momentum,
    score_parent_regime_fit,
    score_thematic_trend,
)


# ─── abs_momentum ──────────────────────────────────────────────────────────
def test_abs_momentum_15pct_returns_100():
    assert score_abs_momentum(0.15) == 100.0
    assert score_abs_momentum(0.25) == 100.0


def test_abs_momentum_negative_low_score():
    assert score_abs_momentum(-0.10) == 10.0
    assert score_abs_momentum(-0.03) == 25.0


def test_abs_momentum_none_returns_neutral():
    assert score_abs_momentum(None) == 40.0


# ─── trend ─────────────────────────────────────────────────────────────────
def test_trend_neutral_on_short_history():
    s = pd.Series([100.0] * 10)
    out = score_thematic_trend(s)
    assert out["score"] == 50.0


def test_trend_perfect_above_rising():
    # Trend salita costante → above EMA + EMA rising
    s = pd.Series([100.0 + i for i in range(60)])
    out = score_thematic_trend(s)
    assert out["score"] == 100.0
    assert out["above_ema"] is True


def test_trend_below_falling():
    s = pd.Series([200.0 - i * 1.5 for i in range(60)])
    out = score_thematic_trend(s)
    assert out["score"] == 10.0


# ─── parent regime fit ─────────────────────────────────────────────────────
def test_parent_regime_fit_tech_in_strong_bull():
    assert score_parent_regime_fit("technology", 5) == 100.0


def test_parent_regime_fit_tech_in_bear_adjacent():
    # Tech non favored in BEAR (2), ma favored in NEUTRAL (3) → adjacent
    assert score_parent_regime_fit("technology", 2) == 60.0


def test_parent_regime_fit_tech_in_strong_bear_distant():
    # Tech non favored in STRONG_BEAR (1), nemmeno in BEAR (2) → not adjacent
    assert score_parent_regime_fit("technology", 1) == 20.0


def test_parent_regime_fit_unknown():
    assert score_parent_regime_fit(None, 3) == 50.0
    assert score_parent_regime_fit("technology", None) == 50.0


# ─── corr kill switch ─────────────────────────────────────────────────────
def test_corr_kill_triggers_above_threshold():
    composite, killed = apply_corr_kill_switch(80.0, 0.90, threshold=0.85)
    assert killed
    assert composite == 0.0


def test_corr_kill_pass_below_threshold():
    composite, killed = apply_corr_kill_switch(80.0, 0.50, threshold=0.85)
    assert not killed
    assert composite == 80.0


def test_corr_kill_pass_on_none_corr():
    """Storia insufficiente → conservativo: pass-through."""
    composite, killed = apply_corr_kill_switch(80.0, None)
    assert not killed
    assert composite == 80.0


# ─── regime gate ─────────────────────────────────────────────────────────
def test_regime_gate_strong_bear_zeros():
    composite, triggered = apply_regime_gate(80.0, 1)
    assert triggered
    assert composite == 0.0


def test_regime_gate_bear_caps_at_40():
    composite, triggered = apply_regime_gate(80.0, 2)
    assert triggered
    assert composite == 40.0


def test_regime_gate_bear_pass_below_cap():
    composite, triggered = apply_regime_gate(35.0, 2)
    assert not triggered
    assert composite == 35.0


def test_regime_gate_neutral_passthrough():
    composite, triggered = apply_regime_gate(80.0, 3)
    assert not triggered
    assert composite == 80.0


def test_regime_gate_strong_bull_passthrough():
    composite, triggered = apply_regime_gate(80.0, 5)
    assert not triggered
    assert composite == 80.0


def test_regime_gate_none_passthrough():
    composite, triggered = apply_regime_gate(80.0, None)
    assert not triggered
    assert composite == 80.0


# ─── classification ──────────────────────────────────────────────────────
def test_classify_thematic_tiers():
    assert classify_thematic(75.0).startswith("A")
    assert classify_thematic(60.0).startswith("B")
    assert classify_thematic(45.0).startswith("C")
    assert classify_thematic(30.0).startswith("D")
    assert classify_thematic(0.0).startswith("D")
