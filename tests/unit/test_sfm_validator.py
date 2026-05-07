"""Test sul layer validate_sfm_thesis (gate + cache key + render prompt).

Le chiamate Anthropic SDK sono mockate — qui si testa solo logica deterministica:
- passenger gate (peer-RS dead → skip senza spesa)
- cache key versioning (sector_key incluso)
- render prompt produce stringa con i field SFM-specifici
"""

from __future__ import annotations

from unittest.mock import patch

from propicks.ai.sfm_prompts import render_sfm_user_prompt
from propicks.ai.sfm_validator import (
    _cache_key,
    _peer_rs_gate_fails,
    validate_sfm_thesis,
)


# ---------------------------------------------------------------------------
# _peer_rs_gate_fails
# ---------------------------------------------------------------------------
def test_passenger_gate_dead_signal_fails():
    """Score < 60 AND slope ≤ 0 → fail (passenger trade)."""
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 50.0, "rs_slope": -0.001}}
    ) is True
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 30.0, "rs_slope": 0.0}}
    ) is True


def test_passenger_gate_leader_passes():
    """Score >= 60 OR slope > 0 → not dead."""
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 90.0, "rs_slope": 0.001}}
    ) is False
    # Borderline: low score but rising slope = not dead (recovering)
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 50.0, "rs_slope": 0.0005}}
    ) is False
    # High score but flat slope = leader stanco, ma non passenger
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 75.0, "rs_slope": -0.0001}}
    ) is False


def test_passenger_gate_missing_rs_returns_false():
    """rs_vs_sector None (non-US ticker) ≠ dead — il prompt gestisce missing."""
    assert _peer_rs_gate_fails({"rs_vs_sector": None}) is False
    assert _peer_rs_gate_fails({}) is False


def test_passenger_gate_invalid_types_returns_false():
    """Score / slope non numerici → fail-open (non bloccare AI per dati corrotti)."""
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": "n/a", "rs_slope": -0.001}}
    ) is False
    assert _peer_rs_gate_fails(
        {"rs_vs_sector": {"score": 50.0}}
    ) is False  # slope missing


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------
def test_cache_key_includes_sector_and_version():
    k = _cache_key("AAPL", "technology", "2026-05-05")
    assert "AAPL" in k
    assert "technology" in k
    assert "sfm-v1" in k
    assert "2026-05-05" in k


def test_cache_key_different_sector_different_key():
    """GICS reshuffle (es. META Tech→Communications) invalida cache."""
    k1 = _cache_key("META", "technology", "2026-05-05")
    k2 = _cache_key("META", "communications", "2026-05-05")
    assert k1 != k2


def test_cache_key_different_day_different_key():
    k1 = _cache_key("AAPL", "technology", "2026-05-05")
    k2 = _cache_key("AAPL", "technology", "2026-05-06")
    assert k1 != k2


def test_cache_key_none_sector_uses_unknown():
    k = _cache_key("AAPL", None, "2026-05-05")
    assert "unknown" in k


def test_cache_key_normalizes_case():
    """Ticker uppercase + sector lowercase per consistency."""
    k = _cache_key("aapl", "TECHNOLOGY", "2026-05-05")
    assert "AAPL" in k
    assert "technology" in k


# ---------------------------------------------------------------------------
# render_sfm_user_prompt
# ---------------------------------------------------------------------------
def _sample_analysis() -> dict:
    return {
        "ticker": "AAPL",
        "price": 195.50,
        "score_composite": 82.0,
        "score_sfm": 84.5,
        "classification": "A — AZIONE IMMEDIATA",
        "sector_key": "technology",
        "peer_etf": "XLK",
        "sector_score": 85.2,
        "scores": {
            "trend": 90, "momentum": 75, "volume": 80,
            "distance_high": 92, "volatility": 75, "ma_cross": 80,
        },
        "rs_vs_sector": {
            "score": 92.0, "rs_ratio": 1.045, "rs_slope": 0.0012, "peer_etf": "XLK",
        },
        "regime": {
            "regime": "BULL", "regime_code": 4, "entry_allowed": True,
            "trend": "up", "trend_strength": "strong",
            "adx": 28, "rsi": 62, "momentum": "positive",
        },
        "ema_fast": 192.0, "ema_slow": 185.0, "rsi": 58.5,
        "atr": 4.2, "atr_pct": 0.021, "volume_ratio": 1.45,
        "high_52w": 198.0, "distance_from_high_pct": 0.013,
        "stop_suggested": 187.10, "stop_pct": -0.043,
        "perf_1w": 0.025, "perf_1m": 0.052, "perf_3m": 0.18,
        "next_earnings_date": "2026-07-30", "days_to_earnings": 86,
    }


def test_render_includes_sfm_specific_fields():
    out = render_sfm_user_prompt(
        _sample_analysis(), as_of_date="2026-05-05", overlay_weight=0.20
    )
    assert "AAPL" in out
    assert "technology" in out
    assert "XLK" in out
    assert "Sector composite" in out
    assert "85.2" in out  # sector_score
    assert "84.5" in out  # score_sfm
    # Peer-RS section
    assert "Peer-RS" in out or "peer-RS" in out
    assert "1.045" in out  # rs_ratio
    # Regime block
    assert "BULL" in out
    # Earnings
    assert "2026-07-30" in out
    # Task section with passenger gate language
    assert "passenger gate" in out
    assert "regime gate" in out


def test_render_handles_missing_peer_rs():
    """Ticker non-US (rs_vs_sector=None) → render con caveat esplicito."""
    analysis = _sample_analysis()
    analysis["rs_vs_sector"] = None
    out = render_sfm_user_prompt(analysis, as_of_date="2026-05-05")
    assert "n/a" in out  # no peer mapping → fmt n/a
    # Il prompt deve comunque rendersi senza crash


def test_render_dead_peer_rs_flags_passenger_risk():
    """RS score < 60 e slope ≤ 0 → flag visivo nel sector_block."""
    analysis = _sample_analysis()
    analysis["rs_vs_sector"] = {
        "score": 50.0, "rs_ratio": 0.98, "rs_slope": -0.002, "peer_etf": "XLK",
    }
    out = render_sfm_user_prompt(analysis, as_of_date="2026-05-05")
    # Il helper _fmt_peer_rs_block include il warning passenger
    assert "passenger" in out.lower() or "🚨" in out


def test_render_overlay_weight_in_formula_label():
    """La formula score_sfm nel template mostra il peso corrente."""
    out = render_sfm_user_prompt(
        _sample_analysis(), as_of_date="2026-05-05", overlay_weight=0.30
    )
    assert "70%" in out  # 1 - 0.30 = 0.70
    assert "30%" in out  # peer-RS weight


# ---------------------------------------------------------------------------
# validate_sfm_thesis — gate behavior (no real Claude call)
# ---------------------------------------------------------------------------
def test_validate_skips_below_score_threshold():
    """score_sfm < SFM_MIN_STOCK_SCORE → None senza chiamare AI."""
    analysis = _sample_analysis()
    analysis["score_sfm"] = 50.0  # sotto threshold default 75

    with patch("propicks.ai.sfm_validator.call_sfm_validation") as mock_call:
        out = validate_sfm_thesis(analysis, gate=True, force=False)

    assert out is None
    mock_call.assert_not_called()


def test_validate_skips_when_passenger_gate_fails():
    """peer-RS dead → None senza chiamare AI (passenger trade)."""
    analysis = _sample_analysis()
    analysis["rs_vs_sector"] = {
        "score": 40.0, "rs_ratio": 0.95, "rs_slope": -0.003, "peer_etf": "XLK",
    }

    with patch("propicks.ai.sfm_validator.call_sfm_validation") as mock_call:
        out = validate_sfm_thesis(analysis, gate=True, force=False)

    assert out is None
    mock_call.assert_not_called()


def test_validate_skips_when_regime_blocks_entry():
    """Regime entry_allowed=False → skip (es. STRONG_BEAR)."""
    analysis = _sample_analysis()
    analysis["regime"]["entry_allowed"] = False
    analysis["regime"]["regime"] = "STRONG_BEAR"

    with patch("propicks.ai.sfm_validator.call_sfm_validation") as mock_call:
        out = validate_sfm_thesis(analysis, gate=True, force=False)

    assert out is None
    mock_call.assert_not_called()


def test_validate_force_bypasses_all_gates():
    """force=True → chiama AI anche con score basso e regime no-entry."""
    analysis = _sample_analysis()
    analysis["score_sfm"] = 30.0
    analysis["regime"]["entry_allowed"] = False
    analysis["rs_vs_sector"] = {"score": 30.0, "rs_slope": -0.005}

    fake_verdict = type("V", (), {
        "model_dump": lambda self: {
            "verdict": "REJECT",
            "conviction_score": 2,
            "thesis_summary": "Forced check",
            "bull_case": [],
            "bear_case": ["Score too low"],
            "key_catalysts": [],
            "key_risks": [],
            "invalidation_triggers": [],
            "invalidation_deadline": "2026-06-01",
            "time_horizon": "1-3M",
            "alignment_with_technicals": "CONTRADICTORY",
            "entry_tactic": "WAIT_VOLUME_CONFIRMATION",
            "reward_risk_ratio": 1.0,
            "stop_rationale": "n/a",
            "target_rationale": "n/a",
            "confidence_by_dimension": {
                "business_quality": 5, "narrative_catalysts": 3,
                "sector_macro_fit": 2, "crowding_sentiment": 5,
                "risk_asymmetry": 3, "technicals_alignment": 2,
            },
            "suggested_adjustments": {},
        }
    })()

    with patch(
        "propicks.ai.sfm_validator.call_sfm_validation",
        return_value=fake_verdict,
    ), patch(
        "propicks.ai.sfm_validator.check_budget"
    ), patch(
        "propicks.ai.sfm_validator.record_call"
    ), patch(
        "propicks.ai.sfm_validator.ai_verdict_cache_get",
        return_value=None,
    ), patch(
        "propicks.ai.sfm_validator.ai_verdict_cache_put"
    ):
        out = validate_sfm_thesis(analysis, gate=True, force=True)

    assert out is not None
    assert out["verdict"] == "REJECT"


def test_validate_returns_cached_verdict_on_hit():
    """Cache hit → ritorna payload con _cache_hit=True senza chiamare AI."""
    analysis = _sample_analysis()
    cached_payload = {
        "verdict": "CONFIRM",
        "conviction_score": 8,
        "thesis_summary": "from cache",
    }

    with patch(
        "propicks.ai.sfm_validator.ai_verdict_cache_get",
        return_value=cached_payload,
    ), patch(
        "propicks.ai.sfm_validator.call_sfm_validation"
    ) as mock_call:
        out = validate_sfm_thesis(analysis, gate=True, force=False)

    assert out is not None
    assert out["_cache_hit"] is True
    assert out["verdict"] == "CONFIRM"
    mock_call.assert_not_called()
