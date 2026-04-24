"""Test sizing_v2.py — advanced sizing con Kelly + vol target + corr penalty.

**Safety check**: verifichiamo che in OGNI caso ``final_shares ≤ base_shares``
— il layer advanced non può mai aumentare la size oltre la sizing classica.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.domain.sizing_v2 import (
    apply_correlation_penalty,
    calculate_position_size_advanced,
)


def _empty_portfolio(cash: float = 10_000.0) -> dict:
    return {"positions": {}, "cash": cash}


def _profitable_strategy_trades(strategy: str = "TechTitans", n: int = 20) -> list[dict]:
    """n trades, win_rate 70%, W/L 2.0 — edge chiaro."""
    trades = []
    # 14 wins (70%), 6 losses
    for i in range(14):
        trades.append({
            "id": i, "ticker": "T", "status": "closed",
            "pnl_pct": 5.0, "strategy": strategy,
        })
    for i in range(6):
        trades.append({
            "id": i + 14, "ticker": "T", "status": "closed",
            "pnl_pct": -2.5, "strategy": strategy,
        })
    return trades


# ---------------------------------------------------------------------------
# Advanced sizing — base case
# ---------------------------------------------------------------------------
def test_advanced_no_features_matches_base():
    """Senza features attive, il risultato dovrebbe matchare il base."""
    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=_empty_portfolio(10_000),
        use_kelly=False, use_corr_penalty=False, use_vol_target=False,
    )
    assert r["ok"]
    assert r["shares"] == r["base_shares"]
    assert r["binding_constraint"] == "base_cap"


def test_advanced_rejected_by_base_gate():
    """Score troppo basso → base sizing reject → advanced propaga."""
    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=3, score_tech=30,  # sotto soglia
        portfolio=_empty_portfolio(10_000),
    )
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# Kelly downscaling
# ---------------------------------------------------------------------------
def test_kelly_scales_down_when_lower_than_cap():
    """Kelly suggerisce 6% mentre cap momentum è 15% → scale to 6%."""
    portfolio = _empty_portfolio(10_000)
    # 15 trades: 8 wins +3%, 7 losses -3% → win_rate 0.533, W/L 1.0
    # full_kelly = (0.533 × 1.0 - 0.467) / 1.0 = 0.066
    # fractional 25% = 0.0165 = 1.65%
    trades = []
    for i in range(8):
        trades.append({"id": i, "ticker": "T", "status": "closed", "pnl_pct": 3.0, "strategy": "TechTitans"})
    for i in range(7):
        trades.append({"id": i + 8, "ticker": "T", "status": "closed", "pnl_pct": -3.0, "strategy": "TechTitans"})

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        strategy_name="TechTitans",
        use_kelly=True,
        trades=trades,
    )
    assert r["ok"]
    # Safety: shares ridotti rispetto al base
    assert r["shares"] <= r["base_shares"]
    assert r["breakdown"]["kelly"]["usable"]


def test_kelly_skipped_if_insufficient_trades():
    """<15 trade → Kelly non usabile → no scale down."""
    portfolio = _empty_portfolio(10_000)
    trades = _profitable_strategy_trades(n=5)  # too few
    trades = trades[:5]

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        strategy_name="TechTitans",
        use_kelly=True,
        trades=trades,
    )
    assert r["ok"]
    # Shares = base (nessun downscale Kelly perché non usable)
    assert r["shares"] == r["base_shares"]
    assert r["breakdown"]["kelly"]["usable"] is False


def test_kelly_zero_edge_warns_but_doesnt_zero_size():
    """Kelly = 0% (edge negativo) → warn, ma base cap resta valido."""
    portfolio = _empty_portfolio(10_000)
    # 15 trade con edge negativo: 5 wins, 10 losses
    trades = []
    for i in range(5):
        trades.append({"id": i, "ticker": "T", "status": "closed", "pnl_pct": 3.0, "strategy": "X"})
    for i in range(10):
        trades.append({"id": i + 5, "ticker": "T", "status": "closed", "pnl_pct": -3.0, "strategy": "X"})

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        strategy_name="X",
        use_kelly=True,
        trades=trades,
    )
    # kelly_pct = 0. Il binding resta base_cap (non scaliamo a 0 shares).
    # Ma c'è warning
    assert r["ok"]
    assert any("Kelly suggerisce 0" in w for w in r["warnings"])


# ---------------------------------------------------------------------------
# Vol target
# ---------------------------------------------------------------------------
def test_vol_target_scales_down_when_vol_high():
    """Portfolio con posizioni ad alta vol → scale down proposto."""
    # Portfolio con 1 posizione esistente + 50% pesato
    portfolio = {
        "cash": 5000.0,
        "positions": {
            "X": {"shares": 50, "entry_price": 100.0, "strategy": "TechTitans"},
        },
    }
    # Returns molto volatili per X
    rng = np.random.default_rng(42)
    idx = pd.date_range(end="2026-04-24", periods=100, freq="B")
    returns_df = pd.DataFrame({"X": rng.normal(0.001, 0.05, 100)}, index=idx)

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        use_vol_target=True,
        returns_df=returns_df,
        target_vol=0.10,
    )
    assert r["ok"]
    # Vol portfolio daily sarà alta (vol 0.05 daily = ~79% annualized)
    # → scale down
    vt = r["breakdown"].get("vol_target")
    assert vt is not None
    # Scale dovrebbe essere < 1.0
    assert vt["scale_factor"] < 1.0


def test_vol_target_no_scaling_when_below():
    """Vol corrente < target → no scaling (scale_factor ≥ 1 → safety skip)."""
    portfolio = {
        "cash": 5000.0,
        "positions": {
            "X": {"shares": 50, "entry_price": 100.0, "strategy": "TechTitans"},
        },
    }
    # Returns bassa vol
    rng = np.random.default_rng(42)
    idx = pd.date_range(end="2026-04-24", periods=100, freq="B")
    returns_df = pd.DataFrame({"X": rng.normal(0.001, 0.002, 100)}, index=idx)

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        use_vol_target=True,
        returns_df=returns_df,
        target_vol=0.15,
    )
    # Shares = base (no scaling up via vol target per safety)
    assert r["shares"] == r["base_shares"]


# ---------------------------------------------------------------------------
# Safety invariant: final_shares ≤ base_shares
# ---------------------------------------------------------------------------
def test_safety_invariant_final_le_base_with_all_features():
    """Con TUTTE le features attivate, il final non supera mai il base."""
    portfolio = {
        "cash": 5000.0,
        "positions": {
            "X": {"shares": 50, "entry_price": 100.0, "strategy": "TechTitans"},
        },
    }
    rng = np.random.default_rng(42)
    idx = pd.date_range(end="2026-04-24", periods=100, freq="B")
    returns_df = pd.DataFrame({"X": rng.normal(0.001, 0.02, 100)}, index=idx)
    trades = _profitable_strategy_trades(n=20)

    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=portfolio,
        strategy_name="TechTitans",
        trades=trades,
        returns_df=returns_df,
        use_kelly=True,
        use_vol_target=True,
    )
    assert r["shares"] <= r["base_shares"]


# ---------------------------------------------------------------------------
# apply_correlation_penalty
# ---------------------------------------------------------------------------
def test_apply_corr_penalty_reduces_when_correlated():
    base_result = {
        "ok": True,
        "shares": 100,
        "base_shares": 100,
        "entry_price": 100.0,
        "final_size_pct": 0.10,
        "breakdown": {},
    }
    # Correlation matrix: NEW correlato 0.9 con X
    corr = pd.DataFrame(
        [[1.0, 0.9], [0.9, 1.0]],
        index=["NEW", "X"], columns=["NEW", "X"],
    )
    result = apply_correlation_penalty(
        base_result,
        new_ticker="NEW",
        existing_weights={"X": 0.5},
        corr_matrix=corr,
    )
    assert result["shares"] < 100  # reduced
    assert result["binding_constraint"] == "corr_penalty"


def test_apply_corr_penalty_no_reduction_when_uncorrelated():
    base_result = {
        "ok": True,
        "shares": 100,
        "base_shares": 100,
        "entry_price": 100.0,
        "final_size_pct": 0.10,
        "breakdown": {},
    }
    corr = pd.DataFrame(
        [[1.0, 0.2], [0.2, 1.0]],
        index=["NEW", "X"], columns=["NEW", "X"],
    )
    result = apply_correlation_penalty(
        base_result,
        new_ticker="NEW",
        existing_weights={"X": 0.5},
        corr_matrix=corr,
    )
    assert result["shares"] == 100  # no reduction


def test_apply_corr_penalty_propagates_not_ok():
    """Se base_result è NOT ok, ritorna invariato."""
    base_result = {"ok": False, "error": "insufficient cash"}
    result = apply_correlation_penalty(
        base_result,
        new_ticker="NEW",
        existing_weights={},
        corr_matrix=None,
    )
    assert result == base_result


# ---------------------------------------------------------------------------
# Breakdown structure
# ---------------------------------------------------------------------------
def test_breakdown_contains_base_info():
    r = calculate_position_size_advanced(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=_empty_portfolio(10_000),
    )
    breakdown = r["breakdown"]
    assert "base" in breakdown
    assert breakdown["base"]["shares"] == r["base_shares"]
    assert "source" in breakdown["base"]
