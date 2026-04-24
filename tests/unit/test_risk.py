"""Test domain/risk.py — Kelly, VaR, vol, correlation penalty."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propicks.domain.risk import (
    KELLY_MAX,
    MIN_TRADES_FOR_KELLY,
    correlation_adjusted_size,
    kelly_fractional,
    portfolio_var_95,
    portfolio_vol_annualized,
    risk_snapshot,
    strategy_kelly_from_trades,
    vol_target_scale,
)


# ---------------------------------------------------------------------------
# kelly_fractional
# ---------------------------------------------------------------------------
def test_kelly_basic_profitable_edge():
    """P(win)=0.6, W/L=1.5 → full = 0.333, fractional 25% = 0.0833."""
    out = kelly_fractional(win_rate=0.6, win_loss_ratio=1.5, fraction=0.25)
    assert out == pytest.approx(0.0833, abs=0.001)


def test_kelly_negative_edge_returns_zero():
    """P(win)=0.4, W/L=1.0 → edge negativo → 0."""
    assert kelly_fractional(win_rate=0.4, win_loss_ratio=1.0) == 0.0


def test_kelly_breakeven_returns_zero():
    """P(win)=0.5, W/L=1.0 → full Kelly = 0."""
    assert kelly_fractional(win_rate=0.5, win_loss_ratio=1.0) == 0.0


def test_kelly_invalid_input_returns_zero():
    assert kelly_fractional(None, 1.5) == 0.0
    assert kelly_fractional(0.6, None) == 0.0
    assert kelly_fractional(0.0, 1.5) == 0.0
    assert kelly_fractional(1.5, 1.5) == 0.0  # win_rate > 1
    assert kelly_fractional(0.6, 0.0) == 0.0  # zero ratio


def test_kelly_capped_at_max():
    """Anche con edge estremo, fractional non supera KELLY_MAX."""
    # P=0.95, W/L=5 → full_kelly quasi 1.0 (full bet). Fractional 0.25 = 0.25.
    # Dovrebbe essere cappato a KELLY_MAX.
    out = kelly_fractional(win_rate=0.95, win_loss_ratio=5.0, fraction=0.25)
    assert out == KELLY_MAX


def test_kelly_fraction_parameter():
    """fraction=1.0 = full Kelly, 0.25 = quarter Kelly.

    Uso edge moderato (P=0.55, W/L=1.2 → full=0.175) sotto KELLY_MAX per
    evitare che il cap morda solo su uno dei due (nega la proporzionalità).
    """
    full = kelly_fractional(0.55, 1.2, fraction=1.0)
    quarter = kelly_fractional(0.55, 1.2, fraction=0.25)
    assert full < KELLY_MAX, "edge deve essere sotto cap per il test di proporzionalità"
    assert quarter == pytest.approx(full * 0.25, abs=0.001)


# ---------------------------------------------------------------------------
# strategy_kelly_from_trades
# ---------------------------------------------------------------------------
def _make_trade(pnl_pct: float, strategy: str = "X", tid: int = 1) -> dict:
    return {
        "id": tid,
        "ticker": "T",
        "status": "closed",
        "pnl_pct": pnl_pct,
        "strategy": strategy,
    }


def test_strategy_kelly_insufficient_trades():
    trades = [_make_trade(5.0, tid=i) for i in range(5)]
    k = strategy_kelly_from_trades(trades, "X")
    assert k["usable"] is False
    assert k["kelly_pct"] == 0.0
    assert "min" in k["reason"]


def test_strategy_kelly_all_wins_degenerate():
    """15 wins, 0 losses → degenerate, no Kelly estimate."""
    trades = [_make_trade(5.0, tid=i) for i in range(15)]
    k = strategy_kelly_from_trades(trades, "X")
    assert k["usable"] is False
    assert "Degenerate" in k["reason"]


def test_strategy_kelly_realistic_computation():
    """10 wins +5%, 5 losses -2% → win_rate 0.667, W/L=2.5.

    full_kelly = (0.667 × 2.5 - 0.333) / 2.5 = 0.533
    fractional 25% = 0.133 (13.3%), cap KELLY_MAX = 0.20.
    """
    trades = (
        [_make_trade(5.0, tid=i) for i in range(10)]
        + [_make_trade(-2.0, tid=i + 10) for i in range(5)]
    )
    k = strategy_kelly_from_trades(trades, "X", fraction=0.25)
    assert k["usable"]
    assert k["n_trades"] == 15
    assert k["win_rate"] == pytest.approx(0.667, abs=0.01)
    assert k["win_loss_ratio"] == pytest.approx(2.5, abs=0.01)
    assert k["kelly_pct"] == pytest.approx(0.133, abs=0.005)


def test_strategy_kelly_case_insensitive_match():
    trades = (
        [_make_trade(5.0, strategy="Contrarian", tid=i) for i in range(10)]
        + [_make_trade(-2.0, strategy="CONTRARIAN", tid=i + 10) for i in range(5)]
    )
    k = strategy_kelly_from_trades(trades, "contrarian")
    assert k["n_trades"] == 15


def test_strategy_kelly_filters_open_trades():
    trades = [_make_trade(5.0, tid=i) for i in range(15)]
    trades[0]["status"] = "open"  # uno aperto
    k = strategy_kelly_from_trades(trades, "X")
    assert k["n_trades"] == 14  # aperto escluso


# ---------------------------------------------------------------------------
# portfolio_vol_annualized
# ---------------------------------------------------------------------------
def _synthetic_returns(n_days: int = 60, vol: float = 0.01) -> pd.DataFrame:
    """Synthetic returns 3 ticker con vol diverse."""
    rng = np.random.default_rng(seed=42)
    idx = pd.date_range(end="2026-04-24", periods=n_days, freq="B")
    return pd.DataFrame(
        {
            "A": rng.normal(0.0005, vol, n_days),
            "B": rng.normal(0.0008, vol * 1.5, n_days),
            "C": rng.normal(0.0003, vol * 0.8, n_days),
        },
        index=idx,
    )


def test_vol_annualized_basic():
    df = _synthetic_returns()
    weights = {"A": 0.3, "B": 0.3, "C": 0.4}
    out = portfolio_vol_annualized(df, weights)
    assert out["vol_annualized"] > 0
    assert out["n_tickers_used"] == 3
    assert out["warnings"] == []


def test_vol_annualized_missing_ticker_warns():
    df = _synthetic_returns()
    weights = {"A": 0.5, "Z": 0.5}  # Z missing
    out = portfolio_vol_annualized(df, weights)
    assert out["n_tickers_used"] == 1
    assert any("Z" in w for w in out["warnings"])


def test_vol_annualized_empty_returns():
    out = portfolio_vol_annualized(pd.DataFrame(), {"A": 1.0})
    assert out["vol_annualized"] == 0.0


# ---------------------------------------------------------------------------
# portfolio_var_95
# ---------------------------------------------------------------------------
def test_var_basic_bootstrap():
    df = _synthetic_returns(n_days=100, vol=0.01)
    weights = {"A": 0.5, "B": 0.5}
    out = portfolio_var_95(df, weights, n_bootstrap=200, horizon_days=1)
    assert out["var_95_pct"] is not None
    assert out["var_95_pct"] > 0  # è una loss potenziale (positiva)
    assert out["expected_shortfall_pct"] >= out["var_95_pct"]  # ES >= VaR
    assert out["n_bootstrap"] == 200


def test_var_horizon_scales():
    """VaR 5-day > VaR 1-day (volatility scales con sqrt(t))."""
    df = _synthetic_returns(n_days=100, vol=0.01)
    weights = {"A": 1.0}
    var_1d = portfolio_var_95(df, weights, n_bootstrap=300, horizon_days=1)
    var_5d = portfolio_var_95(df, weights, n_bootstrap=300, horizon_days=5)
    # 5d VaR più grande (ma stocastico — testiamo solo >)
    assert var_5d["var_95_pct"] > var_1d["var_95_pct"]


def test_var_insufficient_history():
    """<30 giorni di dati → None."""
    df = _synthetic_returns(n_days=20)
    out = portfolio_var_95(df, {"A": 1.0})
    assert out["var_95_pct"] is None
    assert "insufficient" in out["note"]


# ---------------------------------------------------------------------------
# correlation_adjusted_size
# ---------------------------------------------------------------------------
def _build_corr_matrix(pairs: dict[tuple[str, str], float]) -> pd.DataFrame:
    """Helper: costruisce matrice di correlazione da dict di pair."""
    tickers = set()
    for a, b in pairs:
        tickers.add(a)
        tickers.add(b)
    tickers = sorted(tickers)
    df = pd.DataFrame(
        np.eye(len(tickers)),
        index=tickers,
        columns=tickers,
    )
    for (a, b), v in pairs.items():
        df.at[a, b] = v
        df.at[b, a] = v
    return df


def test_corr_penalty_no_matrix():
    out = correlation_adjusted_size(
        base_size_pct=0.10,
        new_ticker="NEW",
        existing_weights={"X": 0.5},
        corr_matrix=None,
    )
    assert out["adjusted_size_pct"] == 0.10
    assert out["scale_factor"] == 1.0


def test_corr_penalty_highly_correlated_reduces_size():
    """Nuovo ticker correlato 0.9 con posizione al 50% → reduction significativa."""
    corr = _build_corr_matrix({("NEW", "X"): 0.9})
    out = correlation_adjusted_size(
        base_size_pct=0.10,
        new_ticker="NEW",
        existing_weights={"X": 0.5},
        corr_matrix=corr,
        penalty_factor=0.5,
    )
    # effective_exposure = 0.5 × 0.9 = 0.45
    # scale_factor = 1 - 0.45 × 0.5 = 0.775
    assert out["scale_factor"] == pytest.approx(0.775, abs=0.01)
    assert out["adjusted_size_pct"] == pytest.approx(0.10 * 0.775, abs=0.001)
    assert len(out["correlated_pairs"]) == 1


def test_corr_penalty_low_correlation_no_impact():
    """Corr 0.3 < threshold 0.7 → no penalty."""
    corr = _build_corr_matrix({("NEW", "X"): 0.3})
    out = correlation_adjusted_size(
        base_size_pct=0.10,
        new_ticker="NEW",
        existing_weights={"X": 0.5},
        corr_matrix=corr,
    )
    assert out["scale_factor"] == 1.0
    assert out["correlated_pairs"] == []


def test_corr_penalty_multiple_correlated_compound():
    """Due posizioni correlate 0.8 a 25% ciascuna → compound penalty."""
    corr = _build_corr_matrix({
        ("NEW", "X"): 0.8,
        ("NEW", "Y"): 0.8,
    })
    out = correlation_adjusted_size(
        base_size_pct=0.10,
        new_ticker="NEW",
        existing_weights={"X": 0.25, "Y": 0.25},
        corr_matrix=corr,
        penalty_factor=0.5,
    )
    # effective = 0.25×0.8 + 0.25×0.8 = 0.4
    # scale = 1 - 0.4 × 0.5 = 0.8
    assert out["scale_factor"] == pytest.approx(0.8, abs=0.01)


def test_corr_penalty_new_ticker_not_in_matrix():
    corr = _build_corr_matrix({("A", "B"): 0.9})
    out = correlation_adjusted_size(
        base_size_pct=0.10,
        new_ticker="UNKNOWN",
        existing_weights={"A": 0.5},
        corr_matrix=corr,
    )
    assert out["scale_factor"] == 1.0


# ---------------------------------------------------------------------------
# vol_target_scale
# ---------------------------------------------------------------------------
def test_vol_target_scale_down_when_above():
    """Current vol 20%, target 10% → scale down to 50%."""
    out = vol_target_scale(current_portfolio_vol=0.20, target_vol=0.10)
    assert out["scale_factor"] == pytest.approx(0.5, abs=0.01)
    assert out["recommendation"] == "scale_down"


def test_vol_target_scale_up_when_below():
    """Current 5%, target 10% → scale up to 1.5× (clamped at ceiling)."""
    out = vol_target_scale(current_portfolio_vol=0.05, target_vol=0.10)
    assert out["scale_factor"] == 1.5  # ceiling clamp
    assert out["recommendation"] == "scale_up"
    assert out["clamped"] is True


def test_vol_target_scale_hold_when_close():
    """Vols similar → no change."""
    out = vol_target_scale(current_portfolio_vol=0.10, target_vol=0.10)
    assert out["recommendation"] == "hold"


def test_vol_target_clamped_to_floor():
    """Current 100%, target 10% → raw 0.1, clamped to floor 0.5."""
    out = vol_target_scale(
        current_portfolio_vol=1.0,
        target_vol=0.10,
        floor_scale=0.5,
    )
    assert out["scale_factor"] == 0.5
    assert out["clamped"] is True


# ---------------------------------------------------------------------------
# risk_snapshot (integration)
# ---------------------------------------------------------------------------
def test_risk_snapshot_empty_portfolio():
    """Portfolio vuoto → snapshot valido con n_positions=0."""
    portfolio = {"positions": {}, "cash": 10000.0}
    snap = risk_snapshot(portfolio)
    assert snap["n_positions"] == 0
    assert snap["cash_weight"] == 1.0


def test_risk_snapshot_with_returns_and_trades():
    """Snapshot completo con returns + trades."""
    portfolio = {
        "cash": 5000.0,
        "positions": {
            "A": {"shares": 10, "entry_price": 100.0},
            "B": {"shares": 10, "entry_price": 100.0},
        },
    }
    df = _synthetic_returns(n_days=100)
    trades = (
        [_make_trade(5.0, strategy="X", tid=i) for i in range(10)]
        + [_make_trade(-2.0, strategy="X", tid=i + 10) for i in range(5)]
    )
    snap = risk_snapshot(portfolio, returns_df=df, trades=trades, target_vol=0.15)

    assert snap["n_positions"] == 2
    assert "vol" in snap
    assert "var" in snap
    assert "kelly" in snap
    assert "X" in snap["kelly"]
    assert snap["kelly"]["X"]["usable"] is True


# ---------------------------------------------------------------------------
# MIN_TRADES_FOR_KELLY sanity
# ---------------------------------------------------------------------------
def test_min_trades_is_sensible():
    assert MIN_TRADES_FOR_KELLY >= 10
    assert MIN_TRADES_FOR_KELLY <= 30
