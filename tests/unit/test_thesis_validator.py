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
