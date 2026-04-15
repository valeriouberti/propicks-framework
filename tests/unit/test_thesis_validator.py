"""Test del validator AI — l'SDK Anthropic è interamente mockato."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from propicks.ai import thesis_validator
from propicks.ai.claude_client import ConfidenceByDimension, ThesisVerdict


@pytest.fixture
def sample_analysis() -> dict:
    return {
        "ticker": "AAPL",
        "strategy": "TechTitans",
        "price": 185.50,
        "ema_fast": 182.10,
        "ema_slow": 175.20,
        "rsi": 62.3,
        "atr": 3.80,
        "atr_pct": 0.0205,
        "avg_volume": 50_000_000,
        "current_volume": 72_000_000,
        "volume_ratio": 1.44,
        "high_52w": 199.62,
        "distance_from_high_pct": 0.0708,
        "scores": {
            "trend": 100.0,
            "momentum": 100.0,
            "volume": 100.0,
            "distance_high": 100.0,
            "volatility": 70.0,
            "ma_cross": 80.0,
        },
        "score_composite": 92.5,
        "classification": "A — AZIONE IMMEDIATA",
        "stop_suggested": 177.90,
        "stop_pct": -0.041,
        "perf_1w": 0.021,
        "perf_1m": 0.058,
        "perf_3m": 0.124,
    }


@pytest.fixture
def mock_verdict() -> ThesisVerdict:
    return ThesisVerdict(
        verdict="CONFIRM",
        conviction_score=8,
        thesis_summary="Strong setup backed by fundamentals.",
        bull_case=["Services growth", "Buyback"],
        bear_case=["China exposure"],
        key_catalysts=["Q4 earnings"],
        key_risks=["Regulatory"],
        invalidation_triggers=["Close below 50-EMA for 3 sessions"],
        invalidation_deadline="2026-07-15",
        time_horizon="3-6M",
        alignment_with_technicals="STRONG",
        entry_tactic="MARKET_NOW",
        reward_risk_ratio=2.3,
        stop_rationale="Below prior swing low at 171.50, structural support.",
        target_rationale="Prior resistance band 208-212 from Oct 2025 rejection.",
        confidence_by_dimension=ConfidenceByDimension(
            business_quality=8,
            narrative_catalysts=7,
            sector_macro_fit=7,
            crowding_sentiment=6,
            risk_asymmetry=8,
            technicals_alignment=8,
        ),
        suggested_adjustments={"stop": None, "target": None, "size_multiplier": 1.0},
    )


def test_validate_thesis_gate_skips_low_scores(sample_analysis, tmp_path, monkeypatch):
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))
    low = {**sample_analysis, "score_composite": 30.0}
    assert thesis_validator.validate_thesis(low) is None


def test_validate_thesis_calls_claude_and_caches(
    sample_analysis, mock_verdict, tmp_path, monkeypatch
):
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))

    with patch.object(thesis_validator, "call_validation", return_value=mock_verdict) as mocked:
        result = thesis_validator.validate_thesis(sample_analysis)
        assert mocked.call_count == 1

    assert result is not None
    assert result["verdict"] == "CONFIRM"
    assert result["_cache_hit"] is False

    with patch.object(thesis_validator, "call_validation") as mocked_again:
        cached = thesis_validator.validate_thesis(sample_analysis)
        assert mocked_again.call_count == 0
    assert cached["_cache_hit"] is True
    assert cached["verdict"] == "CONFIRM"


def test_force_bypasses_cache_and_gate(sample_analysis, mock_verdict, tmp_path, monkeypatch):
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))
    low = {**sample_analysis, "score_composite": 10.0}

    with patch.object(thesis_validator, "call_validation", return_value=mock_verdict):
        result = thesis_validator.validate_thesis(low, force=True, gate=False)

    assert result is not None
    assert result["verdict"] == "CONFIRM"


def _verdict_with(
    base: ThesisVerdict,
    *,
    verdict: str | None = None,
    reward_risk_ratio: float | None = None,
    suggested_adjustments: dict | None = None,
) -> ThesisVerdict:
    data = base.model_dump()
    if verdict is not None:
        data["verdict"] = verdict
    if reward_risk_ratio is not None:
        data["reward_risk_ratio"] = reward_risk_ratio
    if suggested_adjustments is not None:
        data["suggested_adjustments"] = suggested_adjustments
    return ThesisVerdict.model_validate(data)


def test_rr_overwritten_when_model_miscomputes(
    sample_analysis, mock_verdict, tmp_path, monkeypatch
):
    """NEM-style bug: Claude reports 2.34 but (entry, stop, target) gives 1.61."""
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))
    nem = {**sample_analysis, "ticker": "NEM", "price": 119.30, "stop_suggested": 109.62}
    bad = _verdict_with(
        mock_verdict,
        verdict="CONFIRM",
        reward_risk_ratio=2.34,
        suggested_adjustments={"stop": 109.62, "target": 134.88, "size_multiplier": 0.75},
    )

    with patch.object(thesis_validator, "call_validation", return_value=bad):
        result = thesis_validator.validate_thesis(nem, force=True, gate=False)

    assert result is not None
    assert result["reward_risk_ratio"] == pytest.approx(1.61, abs=0.02)
    assert result["verdict"] == "CAUTION"


def test_rr_consistent_verdict_preserved(
    sample_analysis, mock_verdict, tmp_path, monkeypatch
):
    """When R/R is correct and >= 2.0, CONFIRM must survive."""
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))
    good = _verdict_with(
        mock_verdict,
        verdict="CONFIRM",
        reward_risk_ratio=2.5,
        suggested_adjustments={"stop": 175.00, "target": 211.75, "size_multiplier": 1.0},
    )

    with patch.object(thesis_validator, "call_validation", return_value=good):
        result = thesis_validator.validate_thesis(sample_analysis, force=True, gate=False)

    assert result is not None
    assert result["verdict"] == "CONFIRM"
    assert result["reward_risk_ratio"] == pytest.approx(2.5, abs=0.02)


def test_rr_guard_skips_when_target_missing(
    sample_analysis, mock_verdict, tmp_path, monkeypatch
):
    """If Claude declines to set a target, guard leaves verdict untouched."""
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))
    no_target = _verdict_with(
        mock_verdict,
        verdict="CONFIRM",
        reward_risk_ratio=2.3,
        suggested_adjustments={"stop": None, "target": None, "size_multiplier": 1.0},
    )

    with patch.object(thesis_validator, "call_validation", return_value=no_target):
        result = thesis_validator.validate_thesis(sample_analysis, force=True, gate=False)

    assert result is not None
    assert result["verdict"] == "CONFIRM"
    assert result["reward_risk_ratio"] == pytest.approx(2.3, abs=0.001)
