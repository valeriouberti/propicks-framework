"""Test del budget cap Anthropic.

``conftest.py`` ha già un autouse fixture che redirige ``config.AI_CACHE_DIR``
su tmp, quindi questi test NON scrivono mai sul ``data/ai_cache/`` reale.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from propicks import config
from propicks.ai import thesis_validator
from propicks.ai.budget import (
    AIBudgetExceeded,
    check_budget,
    current_usage,
    record_call,
)


def _usage_file() -> Path:
    from datetime import date
    return Path(config.AI_CACHE_DIR) / f"usage_{date.today().isoformat()}.json"


def _full_analysis() -> dict:
    """Analysis dict abbastanza completa da soddisfare render_user_prompt."""
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
        # regime=None = campo assente, gate regime passa silenziosamente
        "regime": None,
    }


def test_check_budget_empty_state_passes():
    """Prima chiamata del giorno — nessun file usage — non deve bloccare."""
    usage = check_budget()
    assert usage["calls"] == 0
    assert usage["est_cost_usd"] == 0.0


def test_record_call_increments_and_persists():
    record_call(est_cost_usd=0.05)
    record_call(est_cost_usd=0.07)

    on_disk = json.loads(_usage_file().read_text())
    assert on_disk["calls"] == 2
    assert on_disk["est_cost_usd"] == pytest.approx(0.12, abs=0.001)


def test_check_budget_raises_when_calls_exceeded(monkeypatch):
    monkeypatch.setattr(config, "AI_MAX_CALLS_PER_DAY", 2)
    record_call(est_cost_usd=0.01)
    record_call(est_cost_usd=0.01)

    with pytest.raises(AIBudgetExceeded, match="daily call limit"):
        check_budget()


def test_check_budget_raises_when_cost_exceeded(monkeypatch):
    monkeypatch.setattr(config, "AI_MAX_COST_USD_PER_DAY", 0.10)
    record_call(est_cost_usd=0.06)
    record_call(est_cost_usd=0.06)

    with pytest.raises(AIBudgetExceeded, match="daily cost limit"):
        check_budget()


def test_corrupted_usage_file_resets_to_zero():
    """File corrotto viene resettato invece di bloccare la CLI."""
    _usage_file().write_text("not json {")
    usage = current_usage()
    assert usage["calls"] == 0
    assert usage["est_cost_usd"] == 0.0


def test_validator_skips_call_when_budget_exhausted(monkeypatch):
    """Integrazione: validate_thesis non chiama Claude se budget esaurito.

    Verifica anche che il return sia None (non eccezione), così la CLI
    continua a funzionare come su AIValidationError.
    """
    monkeypatch.setattr(config, "AI_MAX_CALLS_PER_DAY", 1)
    record_call()  # satura il budget

    analysis = {
        "ticker": "AAPL",
        "score_composite": 85.0,
        "regime": {"entry_allowed": True},
    }

    with patch.object(thesis_validator, "call_validation") as mocked:
        result = thesis_validator.validate_thesis(analysis, force=True)
        assert mocked.call_count == 0

    assert result is None


def test_validator_records_call_on_success(monkeypatch):
    """Chiamata reale (non cache hit) incrementa il counter."""
    from propicks.ai.claude_client import ConfidenceByDimension, ThesisVerdict

    verdict = ThesisVerdict(
        verdict="CONFIRM",
        conviction_score=8,
        thesis_summary="ok",
        bull_case=["a"],
        bear_case=["b"],
        key_catalysts=["c"],
        key_risks=["d"],
        invalidation_triggers=["e"],
        invalidation_deadline="2026-12-31",
        time_horizon="3-6M",
        alignment_with_technicals="STRONG",
        entry_tactic="MARKET_NOW",
        reward_risk_ratio=2.5,
        stop_rationale="below support",
        target_rationale="prior resistance",
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

    analysis = _full_analysis()

    before = current_usage()["calls"]
    with patch.object(thesis_validator, "call_validation", return_value=verdict):
        thesis_validator.validate_thesis(analysis, force=True)
    after = current_usage()["calls"]
    assert after == before + 1


def test_cache_hit_does_not_increment_budget(monkeypatch, tmp_path):
    """Cache hit non deve toccare il budget — l'API non viene colpita."""
    from propicks.ai.claude_client import ConfidenceByDimension, ThesisVerdict

    # thesis_validator scrive la cache su thesis_validator.AI_CACHE_DIR:
    # patcho QUELLO (modulo locale), NON config.AI_CACHE_DIR che è già
    # isolato dalla autouse fixture ma riutilizzato anche dal budget.
    monkeypatch.setattr(thesis_validator, "AI_CACHE_DIR", str(tmp_path))

    verdict = ThesisVerdict(
        verdict="CONFIRM",
        conviction_score=8,
        thesis_summary="ok",
        bull_case=["a"],
        bear_case=["b"],
        key_catalysts=["c"],
        key_risks=["d"],
        invalidation_triggers=["e"],
        invalidation_deadline="2026-12-31",
        time_horizon="3-6M",
        alignment_with_technicals="STRONG",
        entry_tactic="MARKET_NOW",
        reward_risk_ratio=2.5,
        stop_rationale="x",
        target_rationale="y",
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

    analysis = _full_analysis()

    # Prima chiamata: miss cache, record_call avviene
    with patch.object(thesis_validator, "call_validation", return_value=verdict):
        thesis_validator.validate_thesis(analysis)
    calls_after_first = current_usage()["calls"]
    assert calls_after_first == 1

    # Seconda chiamata: cache hit, record_call NON deve avvenire
    with patch.object(thesis_validator, "call_validation") as mocked:
        thesis_validator.validate_thesis(analysis)
        assert mocked.call_count == 0
    calls_after_second = current_usage()["calls"]
    assert calls_after_second == 1  # immutato


def test_gate_skip_does_not_increment_budget():
    """Score sotto soglia → skip senza spendere budget."""
    analysis = {"ticker": "LOW", "score_composite": 30.0}
    before = current_usage()["calls"]
    thesis_validator.validate_thesis(analysis)
    after = current_usage()["calls"]
    assert after == before
