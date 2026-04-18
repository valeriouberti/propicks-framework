"""Test esposizione: settori, beta-weighted, correlazioni."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propicks.domain.exposure import (
    compute_beta_weighted_exposure,
    compute_concentration_warnings,
    compute_correlation_matrix,
    compute_sector_exposure,
    find_correlated_pairs,
)

# ---------------------------------------------------------------------------
# compute_sector_exposure
# ---------------------------------------------------------------------------


def test_sector_exposure_aggregates_same_sector():
    """Due tech stocks finiscono nello stesso bucket."""
    positions = {
        "AAPL": {"shares": 10, "entry_price": 100.0},
        "MSFT": {"shares": 5, "entry_price": 200.0},
    }
    prices = {"AAPL": 150.0, "MSFT": 300.0}
    sectors = {"AAPL": "technology", "MSFT": "technology"}
    # MV: AAPL=1500, MSFT=1500, total_capital=10000 → tech=30%
    exp = compute_sector_exposure(positions, prices, sectors, total_capital=10_000.0)
    assert exp == {"technology": 0.30}


def test_sector_exposure_unknown_sector_bucket():
    positions = {"XYZ": {"shares": 10, "entry_price": 50.0}}
    prices = {"XYZ": 60.0}
    sectors = {"XYZ": None}
    exp = compute_sector_exposure(positions, prices, sectors, total_capital=10_000.0)
    assert exp == {"unknown": 0.06}


def test_sector_exposure_skips_missing_price():
    """Posizioni senza prezzo corrente vengono skippate (no crash)."""
    positions = {
        "AAPL": {"shares": 10, "entry_price": 100.0},
        "DELISTED": {"shares": 5, "entry_price": 200.0},
    }
    prices = {"AAPL": 150.0}  # DELISTED non ha prezzo
    sectors = {"AAPL": "technology", "DELISTED": "energy"}
    exp = compute_sector_exposure(positions, prices, sectors, total_capital=10_000.0)
    assert "technology" in exp
    assert "energy" not in exp


def test_sector_exposure_zero_capital_returns_empty():
    positions = {"AAPL": {"shares": 10, "entry_price": 100.0}}
    exp = compute_sector_exposure(positions, {"AAPL": 150.0}, {"AAPL": "technology"}, 0.0)
    assert exp == {}


# ---------------------------------------------------------------------------
# compute_concentration_warnings
# ---------------------------------------------------------------------------


def test_warnings_above_default_cap():
    warnings = compute_concentration_warnings({"technology": 0.35, "energy": 0.10})
    assert len(warnings) == 1
    assert "technology" in warnings[0]
    assert "35.0%" in warnings[0]


def test_warnings_empty_when_below_cap():
    assert compute_concentration_warnings({"technology": 0.20}) == []


def test_warnings_custom_cap():
    warnings = compute_concentration_warnings({"technology": 0.18}, single_sector_cap=0.15)
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# compute_beta_weighted_exposure
# ---------------------------------------------------------------------------


def test_beta_weighted_basic():
    positions = {
        "AAPL": {"shares": 10, "entry_price": 100.0},  # MV 1500
        "MSFT": {"shares": 5, "entry_price": 200.0},   # MV 1500
    }
    prices = {"AAPL": 150.0, "MSFT": 300.0}
    betas = {"AAPL": 1.2, "MSFT": 0.8}
    info = compute_beta_weighted_exposure(positions, prices, betas, total_capital=10_000.0)
    # gross = 0.30; bw = 0.15*1.2 + 0.15*0.8 = 0.30
    assert info["gross_long"] == 0.30
    assert info["beta_weighted"] == 0.30
    assert info["n_positions_with_beta"] == 2
    assert info["default_used_for"] == []


def test_beta_weighted_uses_default_when_missing():
    positions = {"NEWIPO": {"shares": 10, "entry_price": 100.0}}
    prices = {"NEWIPO": 100.0}
    betas = {"NEWIPO": None}  # IPO recente, beta non disponibile
    info = compute_beta_weighted_exposure(
        positions, prices, betas, total_capital=10_000.0, default_beta=1.0
    )
    # weight 0.10, beta=1.0 default → bw=0.10
    assert info["beta_weighted"] == 0.10
    assert info["n_positions_with_beta"] == 0
    assert info["default_used_for"] == ["NEWIPO"]


def test_beta_weighted_skips_no_price():
    positions = {"AAPL": {"shares": 10, "entry_price": 100.0}}
    prices = {}
    betas = {"AAPL": 1.2}
    info = compute_beta_weighted_exposure(positions, prices, betas, total_capital=10_000.0)
    assert info["gross_long"] == 0.0
    assert info["beta_weighted"] == 0.0


def test_beta_weighted_zero_capital():
    info = compute_beta_weighted_exposure(
        {"AAPL": {"shares": 10, "entry_price": 100.0}},
        {"AAPL": 150.0},
        {"AAPL": 1.0},
        total_capital=0.0,
    )
    assert info["gross_long"] == 0.0
    assert info["beta_weighted"] == 0.0


# ---------------------------------------------------------------------------
# compute_correlation_matrix / find_correlated_pairs
# ---------------------------------------------------------------------------


def _build_returns_df(n_obs: int = 60, seed: int = 42) -> pd.DataFrame:
    """Costruisce returns DataFrame con coppie correlate note."""
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 0.01, n_obs)
    return pd.DataFrame({
        "A": base,                                   # corr(A,B)=1.0
        "B": base,
        "C": -base,                                  # corr(A,C)=-1.0
        "D": rng.normal(0, 0.01, n_obs),             # corr ~ 0
    })


def test_correlation_matrix_returns_dataframe():
    returns = _build_returns_df()
    corr = compute_correlation_matrix(returns)
    assert corr is not None
    assert corr.shape == (4, 4)
    assert corr.loc["A", "A"] == pytest.approx(1.0)


def test_correlation_matrix_none_below_min_obs():
    short = pd.DataFrame({"A": [0.01, 0.02], "B": [0.01, 0.03]})
    assert compute_correlation_matrix(short, min_observations=30) is None


def test_correlation_matrix_none_with_single_column():
    returns = pd.DataFrame({"A": [0.01] * 50})
    assert compute_correlation_matrix(returns) is None


def test_find_correlated_pairs_extracts_high_corr():
    returns = _build_returns_df()
    corr = compute_correlation_matrix(returns)
    pairs = find_correlated_pairs(corr, threshold=0.7)
    # (A,B)=+1.0 e (A,C)=-1.0 sopra threshold; (B,C) anche
    pair_keys = {(a, b) for a, b, _ in pairs}
    assert ("A", "B") in pair_keys
    assert ("A", "C") in pair_keys
    # Ordinato per |corr| desc
    assert abs(pairs[0][2]) >= abs(pairs[-1][2])


def test_find_correlated_pairs_skips_diagonal_and_lower():
    """Solo upper triangle, no duplicati (A,B) e (B,A), no (A,A)."""
    returns = _build_returns_df()
    corr = compute_correlation_matrix(returns)
    pairs = find_correlated_pairs(corr, threshold=0.0)
    n_tickers = 4
    expected_pairs = n_tickers * (n_tickers - 1) // 2  # C(4,2) = 6
    assert len(pairs) == expected_pairs


def test_find_correlated_pairs_empty_below_threshold():
    """Threshold alto + correlazioni basse → lista vuota."""
    rng = np.random.default_rng(0)
    returns = pd.DataFrame({
        "A": rng.normal(0, 0.01, 60),
        "B": rng.normal(0, 0.01, 60),
    })
    corr = compute_correlation_matrix(returns)
    pairs = find_correlated_pairs(corr, threshold=0.99)
    assert pairs == []
