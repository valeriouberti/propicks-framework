"""Test dello scoring contrarian (sub-score puri + classify + gates).

Nessuna dipendenza da rete: tutti i test operano su numeri/fixture locali.
Il test dell'orchestratore ``analyze_contra_ticker`` è delegato a smoke test
integrativi (richiede yfinance).
"""

from __future__ import annotations

import math

import pandas as pd

from propicks.config import (
    CONTRA_MAX_AGGREGATE_EXPOSURE_PCT,
    CONTRA_MAX_POSITION_SIZE_PCT,
    CONTRA_MAX_POSITIONS,
    CONTRA_REGIME_FIT,
    CONTRA_VIX_COMPLACENT,
    CONTRA_VIX_SPIKE,
)
from propicks.domain.contrarian_scoring import (
    _consecutive_down_bars,
    _drawdown_5d_atr,
    apply_regime_cap,
    classify_contra,
    score_market_context,
    score_oversold,
    score_quality_gate,
    score_reversion_potential,
)


# ---------------------------------------------------------------------------
# score_oversold
# ---------------------------------------------------------------------------
def test_oversold_full_max():
    """RSI <30 + 3+ ATR sotto EMA50 + 5+ barre rosse → score 100."""
    r = score_oversold(
        rsi=28.0, close=95.0, ema_slow=110.0, atr=4.0, consecutive_down=5
    )
    # ATR distance = (110 - 95) / 4 = 3.75 → 40 pts
    # RSI 28 → 40 pts
    # consecutive 5 → 20 pts
    # total = 100
    assert r["score"] == 100.0
    assert r["atr_distance_from_ema"] == 3.75


def test_oversold_rsi_threshold_warm():
    """RSI tra 30 e 35 → 25 pts (warm, non strict oversold)."""
    r = score_oversold(
        rsi=33.0, close=100.0, ema_slow=110.0, atr=5.0, consecutive_down=3
    )
    # ATR dist = 2.0 → 30 pts
    # RSI 33 → 25 pts
    # consec 3 → 15 pts
    # total = 70
    assert r["score"] == 70.0


def test_oversold_price_above_ema_zero_atr_pts():
    """Price sopra EMA50 → ATR component azzerato (non è oversold)."""
    r = score_oversold(
        rsi=28.0, close=115.0, ema_slow=110.0, atr=5.0, consecutive_down=3
    )
    # ATR dist = (110 - 115) / 5 = -1.0 → 0 pts
    # RSI → 40 pts, consec → 15
    assert r["score"] == 55.0
    assert r["atr_distance_from_ema"] == -1.0


def test_oversold_nan_returns_zero():
    r = score_oversold(
        rsi=float("nan"), close=100.0, ema_slow=110.0, atr=5.0, consecutive_down=3
    )
    assert r["score"] == 0.0


def test_oversold_atr_zero_safe():
    """ATR=0 (degenere) non deve dividere per zero."""
    r = score_oversold(
        rsi=28.0, close=100.0, ema_slow=110.0, atr=0.0, consecutive_down=3
    )
    assert r["score"] == 0.0


# ---------------------------------------------------------------------------
# score_quality_gate
# ---------------------------------------------------------------------------
def test_quality_gate_below_ema200w_zero():
    """Price sotto EMA200w → quality score = 0 (gate broken)."""
    r = score_quality_gate(
        close=90.0, ema_200_weekly=100.0, distance_from_high=-0.30
    )
    assert r["score"] == 0.0
    assert r["above_ema200w"] is False


def test_quality_gate_sweet_spot_depth():
    """Sopra EMA200w + distanza 10-25% dal 52w high → score 100."""
    r = score_quality_gate(
        close=105.0, ema_200_weekly=100.0, distance_from_high=-0.18
    )
    assert r["score"] == 100.0
    assert r["above_ema200w"] is True


def test_quality_gate_too_shallow():
    """Sopra EMA200w ma solo -3% dal max → 30 (non abbastanza oversold)."""
    r = score_quality_gate(
        close=105.0, ema_200_weekly=100.0, distance_from_high=-0.03
    )
    assert r["score"] == 30.0


def test_quality_gate_too_deep():
    """Sopra EMA200w ma -50% dal max → 20 (rischio downtrend)."""
    r = score_quality_gate(
        close=105.0, ema_200_weekly=100.0, distance_from_high=-0.50
    )
    assert r["score"] == 20.0


def test_quality_gate_no_ema_fail_closed():
    """Senza EMA200w disponibile → score 0 (fail-closed per sicurezza).

    Bug fix #5: il gate è hard filter, non soft proxy. Un IPO <60 settimane
    non passa il quality gate — il trader valuta a mano via altri strumenti.
    """
    r = score_quality_gate(
        close=105.0, ema_200_weekly=None, distance_from_high=-0.15
    )
    assert r["score"] == 0.0
    assert "fail-closed" in r["note"]


# ---------------------------------------------------------------------------
# score_market_context
# ---------------------------------------------------------------------------
def test_market_context_neutral_sweet_spot():
    """NEUTRAL regime (code 3) + VIX neutro (20) → 100 (regime fit max)."""
    r = score_market_context(regime_code=3, vix=20.0)
    assert r["score"] == 100.0
    assert r["regime_fit"] == CONTRA_REGIME_FIT[3]


def test_market_context_strong_bull_kills_edge():
    """STRONG_BULL (code 5) → regime_fit basso, anche con VIX paura non salva."""
    r = score_market_context(regime_code=5, vix=28.0)
    # regime_fit 25 + VIX bonus 20 = 45
    assert r["score"] == 45.0


def test_market_context_strong_bear_zero_fit():
    """STRONG_BEAR (code 1) → regime_fit=0 (falling knives, no mean reversion)."""
    r = score_market_context(regime_code=1, vix=35.0)
    # regime_fit 0 + VIX bonus 20 = 20 (comunque basso — il regime non compensa)
    assert r["score"] == 20.0


def test_market_context_vix_euphoria_penalty():
    """VIX < 14 (euforia) → penalty -30 dal regime_fit base."""
    r = score_market_context(regime_code=3, vix=12.0)
    # regime_fit 100 - 30 = 70
    assert r["score"] == 70.0
    assert r["vix_adjustment"] == -30.0


def test_market_context_vix_spike_bonus():
    """VIX ≥ 25 (paura) → bonus +20."""
    r = score_market_context(regime_code=3, vix=27.0)
    # regime_fit 100 + 20 capped a 100
    assert r["score"] == 100.0


def test_market_context_no_regime_no_vix():
    """Nessun dato → score neutro 50 (regime fit default)."""
    r = score_market_context(regime_code=None, vix=None)
    assert r["score"] == 50.0


# ---------------------------------------------------------------------------
# score_reversion_potential
# ---------------------------------------------------------------------------
def test_reversion_potential_excellent_rr():
    """R/R ≥ 3.0 → 100."""
    # price 90, target (EMA50) 100, stop 87 → reward 10, risk 3 → R/R 3.33
    r = score_reversion_potential(close=90.0, ema_slow=100.0, atr=1.0, stop_price=87.0)
    assert r["score"] == 100.0
    assert r["rr_ratio"] == 3.33


def test_reversion_potential_threshold_ok():
    """R/R ≥ 2.0 → 80."""
    # reward 10, risk 5 → R/R 2.0
    r = score_reversion_potential(close=90.0, ema_slow=100.0, atr=1.0, stop_price=85.0)
    assert r["score"] == 80.0
    assert r["rr_ratio"] == 2.0


def test_reversion_potential_broken_setup():
    """R/R < 1.0 (stop più lontano del target) → 10."""
    # reward 10, risk 15 → R/R 0.67
    r = score_reversion_potential(close=90.0, ema_slow=100.0, atr=1.0, stop_price=75.0)
    assert r["score"] == 10.0


def test_reversion_potential_target_below_price_invalid():
    """Se target è sotto prezzo (reward negativo) → score 0."""
    r = score_reversion_potential(close=100.0, ema_slow=95.0, atr=1.0, stop_price=90.0)
    assert r["score"] == 0.0


# ---------------------------------------------------------------------------
# classify_contra
# ---------------------------------------------------------------------------
def test_classify_a_ready():
    assert classify_contra(80).startswith("A")


def test_classify_b_incubating():
    assert classify_contra(65).startswith("B")


def test_classify_c_marginal():
    assert classify_contra(50).startswith("C")


def test_classify_d_skip():
    assert classify_contra(30).startswith("D")


# ---------------------------------------------------------------------------
# _consecutive_down_bars
# ---------------------------------------------------------------------------
def test_consecutive_down_bars_streak():
    s = pd.Series([100, 99, 98, 97, 96])
    assert _consecutive_down_bars(s) == 4


def test_consecutive_down_bars_break():
    # Streak interrotta: solo l'ultima è down, prima green
    s = pd.Series([100, 95, 98, 97])  # 100→95 down, 95→98 up, 98→97 down
    assert _consecutive_down_bars(s) == 1


def test_consecutive_down_bars_no_down():
    s = pd.Series([100, 101, 102])
    assert _consecutive_down_bars(s) == 0


def test_consecutive_down_bars_too_short():
    s = pd.Series([100])
    assert _consecutive_down_bars(s) == 0


# ---------------------------------------------------------------------------
# Config invariants — assicura che le costanti rispettino vincoli di business
# ---------------------------------------------------------------------------
def test_contra_size_cap_lt_momentum():
    """Size cap contrarian < momentum (hit rate più basso → size più piccola)."""
    from propicks.config import MAX_POSITION_SIZE_PCT
    assert CONTRA_MAX_POSITION_SIZE_PCT < MAX_POSITION_SIZE_PCT


def test_contra_aggregate_cap_reasonable():
    """20% default — abbastanza da contare, non abbastanza da dominare."""
    assert 0.10 <= CONTRA_MAX_AGGREGATE_EXPOSURE_PCT <= 0.30


def test_contra_max_positions_strict():
    assert CONTRA_MAX_POSITIONS <= 5


def test_contra_regime_fit_inverse_shape():
    """Regime fit inverso: estremi (1, 5) più bassi di mezzo (3)."""
    assert CONTRA_REGIME_FIT[3] > CONTRA_REGIME_FIT[4]
    assert CONTRA_REGIME_FIT[3] > CONTRA_REGIME_FIT[2]
    assert CONTRA_REGIME_FIT[5] < CONTRA_REGIME_FIT[4]
    assert CONTRA_REGIME_FIT[1] < CONTRA_REGIME_FIT[2]


def test_contra_vix_thresholds_ordered():
    assert CONTRA_VIX_COMPLACENT < CONTRA_VIX_SPIKE


# ---------------------------------------------------------------------------
# apply_regime_cap (fix #4)
# ---------------------------------------------------------------------------
def test_regime_cap_strong_bull_zeroes_composite():
    """Composite in STRONG_BULL (5) → 0 per evitare Class A fuorvianti."""
    assert apply_regime_cap(85.0, regime_code=5) == 0.0


def test_regime_cap_strong_bear_zeroes_composite():
    """Composite in STRONG_BEAR (1) → 0 (falling knife, no mean reversion)."""
    assert apply_regime_cap(80.0, regime_code=1) == 0.0


def test_regime_cap_neutral_no_cap():
    """In NEUTRAL (3) il composite è invariato."""
    assert apply_regime_cap(85.0, regime_code=3) == 85.0


def test_regime_cap_bull_bear_no_cap():
    """BULL (4) e BEAR (2) sono range operativi, nessun cap."""
    assert apply_regime_cap(75.0, regime_code=4) == 75.0
    assert apply_regime_cap(75.0, regime_code=2) == 75.0


def test_regime_cap_none_no_cap():
    """Regime ignoto → composite invariato (non si penalizza alla cieca)."""
    assert apply_regime_cap(70.0, regime_code=None) == 70.0


# ---------------------------------------------------------------------------
# drawdown_5d_atr (fix #8)
# ---------------------------------------------------------------------------
def test_drawdown_5d_atr_single_big_red():
    """1 big red candle: peak alto, current basso, dd/atr grande."""
    high = pd.Series([100, 100, 100, 100, 100])
    close = pd.Series([100, 100, 100, 100, 94])
    # peak_5d = 100, current = 94, atr = 2 → dd = 6/2 = 3.0
    assert _drawdown_5d_atr(high, close, atr=2.0) == 3.0


def test_drawdown_5d_atr_slow_bleed():
    """5 small reds: peak all'inizio, trend continuo al ribasso."""
    high = pd.Series([100, 99, 98, 97, 96])
    close = pd.Series([100, 99, 98, 97, 96])
    # peak = 100, current = 96, atr = 2 → dd = 2.0
    assert _drawdown_5d_atr(high, close, atr=2.0) == 2.0


def test_drawdown_5d_atr_no_drawdown():
    """Price al peak → dd = 0."""
    high = pd.Series([95, 96, 97, 98, 100])
    close = pd.Series([95, 96, 97, 98, 100])
    assert _drawdown_5d_atr(high, close, atr=2.0) == 0.0


def test_drawdown_5d_atr_invalid_atr():
    """ATR <= 0 → None."""
    high = pd.Series([100, 100, 100, 100, 100])
    close = pd.Series([100, 100, 100, 100, 94])
    assert _drawdown_5d_atr(high, close, atr=0.0) is None


def test_oversold_with_drawdown_primary():
    """Un flush verticale (dd=3 ATR) ma solo 1 bar rossa → capitulation via drawdown."""
    r = score_oversold(
        rsi=28.0, close=95.0, ema_slow=110.0, atr=4.0,
        consecutive_down=1, drawdown_5d_atr=3.0,
    )
    # RSI 40 + ATR distance (3.75) 40 + capitulation max(20 dd, 0 consec) = 100
    assert r["score"] == 100.0
    assert r["capitulation_source"] == "drawdown"


def test_oversold_with_consecutive_fallback():
    """Slow bleed (dd=0.5 ATR) ma 5 bar rosse → capitulation via consecutive."""
    r = score_oversold(
        rsi=28.0, close=95.0, ema_slow=110.0, atr=4.0,
        consecutive_down=5, drawdown_5d_atr=0.5,
    )
    # max(3 dd pts, 20 consec pts) = 20
    assert r["capitulation_pts"] == 20.0
    assert r["capitulation_source"] == "consecutive"


def test_oversold_no_double_counting():
    """Con drawdown=3 E consecutive=5, capitulation è max() non sum() — no double count."""
    r = score_oversold(
        rsi=28.0, close=95.0, ema_slow=110.0, atr=4.0,
        consecutive_down=5, drawdown_5d_atr=3.0,
    )
    # max(20, 20) = 20, NOT 40. Total score = 40 + 40 + 20 = 100
    assert r["score"] == 100.0
    assert r["capitulation_pts"] == 20.0


# ---------------------------------------------------------------------------
# Claude verdict sanity enforcement (fix #6, #7)
# ---------------------------------------------------------------------------
def test_enforce_contrarian_sanity_rr_downgrade_confirm():
    """CONFIRM con R/R < CONTRA_RR_CONFIRM_FLOOR (1.5) → CAUTION."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 103.0,  # reward 3
        "invalidation_price": 97.5,  # risk 2.5 → R/R 1.20
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["verdict"] == "CAUTION"
    assert payload["_rr_computed"] == 1.20


def test_enforce_contrarian_sanity_rr_below_1_rejected():
    """R/R < 1 → REJECT (setup strutturalmente rotto)."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 103.0,  # reward 3
        "invalidation_price": 96.0,  # risk 4 → R/R 0.75
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["verdict"] == "REJECT"


def test_enforce_contrarian_sanity_target_below_price():
    """Target <= current price (assurdo per long) → REJECT."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 98.0,  # sotto price!
        "invalidation_price": 95.0,
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["verdict"] == "REJECT"
    assert payload["_sanity_override"] == "target_below_price"


def test_enforce_contrarian_sanity_invalidation_above_price():
    """Invalidation >= current price (stop sopra entry) → REJECT."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 105.0,
        "invalidation_price": 102.0,  # sopra price!
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["verdict"] == "REJECT"
    assert payload["_sanity_override"] == "invalidation_above_price"


def test_enforce_contrarian_sanity_valid_confirm_unchanged():
    """CONFIRM valido (R/R >= CONTRA_RR_CONFIRM_FLOOR=1.5) resta CONFIRM."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 106.0,  # reward 6
        "invalidation_price": 97.0,  # risk 3 → R/R 2.0
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["verdict"] == "CONFIRM"
    assert payload["_rr_computed"] == 2.0


def test_enforce_contrarian_sanity_horizon_clamped():
    """time_horizon_days oltre CONTRA_TIME_STOP_DAYS viene clampato (no reject)."""
    from propicks.ai.contrarian_validator import _enforce_contrarian_sanity
    from propicks.config import CONTRA_TIME_STOP_DAYS
    analysis = {"ticker": "TST", "price": 100.0}
    payload = {
        "verdict": "CONFIRM",
        "reversion_target": 106.0,  # R/R 2.0 → no rr override
        "invalidation_price": 97.0,
        "time_horizon_days": 25,  # > 15
    }
    _enforce_contrarian_sanity(analysis, payload)
    assert payload["time_horizon_days"] == CONTRA_TIME_STOP_DAYS
    assert payload["_horizon_clamped"] == 25
    assert payload["verdict"] == "CONFIRM"  # nessun downgrade


def test_contrarian_scoring_weights_sum_to_one():
    """Sanity: il composite è ben calibrato (redundante con assert in config
    ma utile se un futuro refactor lo salta)."""
    from propicks.config import (
        CONTRA_WEIGHT_MARKET_CONTEXT,
        CONTRA_WEIGHT_OVERSOLD,
        CONTRA_WEIGHT_QUALITY,
        CONTRA_WEIGHT_REVERSION,
    )
    total = (
        CONTRA_WEIGHT_OVERSOLD
        + CONTRA_WEIGHT_QUALITY
        + CONTRA_WEIGHT_MARKET_CONTEXT
        + CONTRA_WEIGHT_REVERSION
    )
    assert math.isclose(total, 1.0, abs_tol=1e-9)
