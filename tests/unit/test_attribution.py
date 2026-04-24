"""Test domain/attribution.py — decomposition + aggregates + gate status."""

from __future__ import annotations

import pandas as pd
import pytest

from propicks.domain.attribution import (
    GATE_THRESHOLDS,
    _max_drawdown,
    _period_return,
    _price_at_date,
    _profit_factor,
    _sharpe_trade_level,
    aggregate_by_regime,
    aggregate_by_strategy,
    decompose_trade,
    filter_trades_by_period,
    portfolio_vs_benchmark,
    strategy_gate_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _synth_bench_series(start_price: float = 100.0, bars: int = 60, daily_ret: float = 0.001) -> pd.Series:
    """Synthetic SPX close series con drift lineare."""
    idx = pd.date_range(end="2026-04-24", periods=bars, freq="B")
    prices = [start_price * (1 + daily_ret) ** i for i in range(bars)]
    return pd.Series(prices, index=idx)


def _make_closed_trade(ticker="AAPL", entry_date="2026-03-01", exit_date="2026-03-15",
                      pnl_pct=10.0, strategy="TechTitans", tid=1) -> dict:
    return {
        "id": tid,
        "ticker": ticker,
        "status": "closed",
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": 100.0,
        "exit_price": 100.0 * (1 + pnl_pct / 100),
        "pnl_pct": pnl_pct,
        "duration_days": 14,
        "strategy": strategy,
        "direction": "long",
    }


# ---------------------------------------------------------------------------
# _price_at_date / _period_return
# ---------------------------------------------------------------------------
def test_price_at_date_exact_match():
    s = _synth_bench_series()
    target = s.index[10].strftime("%Y-%m-%d")
    assert _price_at_date(s, target) == float(s.iloc[10])


def test_price_at_date_weekend_skips_to_monday():
    """Data nel weekend → primo trading day successivo."""
    idx = pd.to_datetime(["2026-03-02", "2026-03-03", "2026-03-04"])  # lun-mar-mer
    s = pd.Series([100.0, 101.0, 102.0], index=idx)
    # Target: sabato 2026-02-28 → primo ≥ è lunedì 2026-03-02
    price = _price_at_date(s, "2026-02-28")
    assert price == 100.0


def test_price_at_date_beyond_range_returns_none():
    s = _synth_bench_series()
    assert _price_at_date(s, "2030-01-01") is None


def test_period_return_basic():
    idx = pd.to_datetime(["2026-03-01", "2026-03-15"])
    s = pd.Series([100.0, 110.0], index=idx)
    ret = _period_return(s, "2026-03-01", "2026-03-15")
    assert ret == pytest.approx(0.10)


def test_period_return_none_if_insufficient():
    s = pd.Series([100.0], index=pd.to_datetime(["2026-03-01"]))
    assert _period_return(s, "2026-03-01", "2026-03-15") is None


# ---------------------------------------------------------------------------
# decompose_trade
# ---------------------------------------------------------------------------
def test_decompose_trade_basic_no_sector():
    """Trade +10%, SPX +5% in periodo, beta 1.0 → market=+5%, alpha=+5%."""
    idx = pd.to_datetime(["2026-03-01", "2026-03-15"])
    bench = pd.Series([100.0, 105.0], index=idx)

    trade = _make_closed_trade(pnl_pct=10.0, entry_date="2026-03-01", exit_date="2026-03-15")
    result = decompose_trade(trade, benchmark_series=bench, beta=1.0)

    assert result["_decomposable"] is True
    assert result["market_pct"] == pytest.approx(5.0, abs=0.01)
    assert result["sector_pct"] == 0.0  # no sector_series
    assert result["alpha_pct"] == pytest.approx(5.0, abs=0.01)


def test_decompose_trade_with_beta_scales_market():
    """Beta 2.0 + SPX +5% → market component = +10% (doppio beta)."""
    idx = pd.to_datetime(["2026-03-01", "2026-03-15"])
    bench = pd.Series([100.0, 105.0], index=idx)

    trade = _make_closed_trade(pnl_pct=12.0)
    result = decompose_trade(trade, benchmark_series=bench, beta=2.0)
    assert result["market_pct"] == pytest.approx(10.0, abs=0.01)
    assert result["alpha_pct"] == pytest.approx(2.0, abs=0.01)


def test_decompose_trade_with_sector_extracts_rotation():
    """Sector ETF outperforma SPX di 3% → sector component = +3%."""
    idx = pd.to_datetime(["2026-03-01", "2026-03-15"])
    bench = pd.Series([100.0, 105.0], index=idx)     # SPX +5%
    sector = pd.Series([100.0, 108.0], index=idx)    # Sector ETF +8%

    trade = _make_closed_trade(pnl_pct=12.0)
    result = decompose_trade(
        trade,
        benchmark_series=bench,
        sector_series=sector,
        beta=1.0,
    )
    assert result["market_pct"] == pytest.approx(5.0, abs=0.01)
    assert result["sector_pct"] == pytest.approx(3.0, abs=0.01)  # 8% - 5%
    assert result["alpha_pct"] == pytest.approx(4.0, abs=0.01)   # 12 - 5 - 3 - 0


def test_decompose_trade_not_closed_returns_non_decomposable():
    trade = {"status": "open", "ticker": "AAPL"}
    result = decompose_trade(trade, _synth_bench_series())
    assert result["_decomposable"] is False


def test_decompose_trade_missing_benchmark_data():
    """Se benchmark series non copre il periodo, _decomposable False."""
    bench = pd.Series([100.0], index=pd.to_datetime(["2026-01-01"]))
    trade = _make_closed_trade(entry_date="2026-03-01", exit_date="2026-03-15")
    result = decompose_trade(trade, benchmark_series=bench)
    assert result["_decomposable"] is False


# ---------------------------------------------------------------------------
# aggregate_by_strategy
# ---------------------------------------------------------------------------
def test_aggregate_by_strategy_basic_metrics():
    trades = [
        _make_closed_trade(ticker="A", pnl_pct=10.0, strategy="X", tid=1),
        _make_closed_trade(ticker="B", pnl_pct=-5.0, strategy="X", tid=2),
        _make_closed_trade(ticker="C", pnl_pct=8.0, strategy="X", tid=3),
    ]
    out = aggregate_by_strategy(trades)
    assert "X" in out
    stats = out["X"]
    assert stats["n_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate"] == pytest.approx(2 / 3, abs=0.01)
    # PF = (10+8) / 5 = 3.6
    assert stats["profit_factor"] == pytest.approx(3.6, abs=0.01)


def test_aggregate_by_strategy_excludes_open_trades():
    trades = [
        _make_closed_trade(pnl_pct=10.0, strategy="X"),
        {"id": 2, "ticker": "B", "status": "open", "pnl_pct": None, "strategy": "X"},
    ]
    out = aggregate_by_strategy(trades)
    assert out["X"]["n_trades"] == 1


def test_aggregate_by_strategy_groups_correctly():
    trades = [
        _make_closed_trade(pnl_pct=5.0, strategy="X", tid=1),
        _make_closed_trade(pnl_pct=5.0, strategy="Y", tid=2),
    ]
    out = aggregate_by_strategy(trades)
    assert set(out.keys()) == {"X", "Y"}


# ---------------------------------------------------------------------------
# strategy_gate_status (Phase 7)
# ---------------------------------------------------------------------------
def test_gate_insufficient_trades_fails():
    """n=5 < min_trades 15 → fail."""
    trades = [_make_closed_trade(pnl_pct=10.0, strategy="X", tid=i) for i in range(5)]
    aggs = aggregate_by_strategy(trades)
    gate = strategy_gate_status(aggs)
    assert gate["X"]["passed"] is False
    assert any("n_trades" in f for f in gate["X"]["failures"])


def test_gate_all_criteria_pass():
    """15 trade con win rate 70%, avg +5%, losses avg -2% → pass."""
    trades = []
    # 11 wins +5%, 4 losses -2% → win rate 73%, PF = 55/8 = 6.875, sharpe alto
    for i in range(11):
        trades.append(_make_closed_trade(pnl_pct=5.0, strategy="X", tid=i))
    for i in range(11, 15):
        trades.append(_make_closed_trade(pnl_pct=-2.0, strategy="X", tid=i))

    aggs = aggregate_by_strategy(trades)
    gate = strategy_gate_status(aggs)
    assert gate["X"]["passed"] is True, f"failures: {gate['X']['failures']}"


def test_gate_contrarian_higher_win_rate_threshold():
    """Strategia 'Contrarian' richiede win_rate >= 0.55 vs 0.50 momentum."""
    # 8 wins + 7 losses = 53% win rate → fail per contra, pass per momentum
    trades_contra = []
    for i in range(8):
        trades_contra.append(_make_closed_trade(pnl_pct=5.0, strategy="Contrarian", tid=i))
    for i in range(8, 15):
        trades_contra.append(_make_closed_trade(pnl_pct=-2.0, strategy="Contrarian", tid=i))

    aggs = aggregate_by_strategy(trades_contra)
    gate = strategy_gate_status(aggs)
    failures = gate["Contrarian"]["failures"]
    assert any("win_rate" in f for f in failures)


# ---------------------------------------------------------------------------
# aggregate_by_regime
# ---------------------------------------------------------------------------
def test_aggregate_by_regime_groups_by_entry_date():
    trades = [
        _make_closed_trade(pnl_pct=5.0, entry_date="2026-03-01", tid=1),
        _make_closed_trade(pnl_pct=-3.0, entry_date="2026-03-01", tid=2),
        _make_closed_trade(pnl_pct=8.0, entry_date="2026-04-15", tid=3),
    ]
    regime_map = {
        "2026-03-01": 3,  # NEUTRAL
        "2026-04-15": 4,  # BULL
    }
    out = aggregate_by_regime(trades, regime_map)
    assert "NEUTRAL" in out
    assert "BULL" in out
    assert out["NEUTRAL"]["n_trades"] == 2
    assert out["BULL"]["n_trades"] == 1


def test_aggregate_by_regime_unknown_date():
    trades = [_make_closed_trade(pnl_pct=5.0, entry_date="2026-03-01")]
    out = aggregate_by_regime(trades, regime_map={})  # no match
    assert "UNKNOWN" in out


# ---------------------------------------------------------------------------
# portfolio_vs_benchmark
# ---------------------------------------------------------------------------
def test_portfolio_vs_benchmark_computes_alpha():
    snapshots = [
        {"date": "2026-03-01", "total_value": 10000.0, "benchmark_spx": 5000.0, "mtd_return": None, "ytd_return": None},
        {"date": "2026-03-15", "total_value": 10800.0, "benchmark_spx": 5250.0, "mtd_return": 0.08, "ytd_return": 0.08},
    ]
    result = portfolio_vs_benchmark(snapshots, benchmark_key="benchmark_spx")
    assert result["_ok"]
    assert result["portfolio_return_pct"] == pytest.approx(8.0, abs=0.01)
    assert result["benchmark_return_pct"] == pytest.approx(5.0, abs=0.01)
    assert result["alpha_pct"] == pytest.approx(3.0, abs=0.01)


def test_portfolio_vs_benchmark_insufficient_snapshots():
    result = portfolio_vs_benchmark([{"date": "2026-03-01", "total_value": 10000}])
    assert result["_ok"] is False


# ---------------------------------------------------------------------------
# filter_trades_by_period
# ---------------------------------------------------------------------------
def test_filter_by_period_days():
    from datetime import date as dt_date
    from datetime import timedelta
    today = dt_date.today()
    trades = [
        _make_closed_trade(exit_date=(today - timedelta(days=5)).isoformat(), tid=1),
        _make_closed_trade(exit_date=(today - timedelta(days=45)).isoformat(), tid=2),
    ]
    filtered = filter_trades_by_period(trades, period_days=30)
    assert len(filtered) == 1
    assert filtered[0]["id"] == 1


def test_filter_excludes_open():
    trades = [
        _make_closed_trade(exit_date="2026-04-20", tid=1),
        {"id": 2, "ticker": "B", "status": "open"},
    ]
    filtered = filter_trades_by_period(trades, period_days=365)
    assert len(filtered) == 1


# ---------------------------------------------------------------------------
# _profit_factor, _sharpe, _max_drawdown edge cases
# ---------------------------------------------------------------------------
def test_profit_factor_all_wins_returns_inf():
    assert _profit_factor([5.0, 3.0, 2.0]) == float("inf")


def test_profit_factor_empty():
    assert _profit_factor([]) is None


def test_sharpe_insufficient_samples():
    assert _sharpe_trade_level([5.0]) is None  # need >= 2


def test_sharpe_zero_stdev():
    assert _sharpe_trade_level([5.0, 5.0, 5.0]) is None  # stdev 0


def test_max_drawdown_simple():
    # 100 → 110 → 90 → 95  : peak 110, min 90, dd = (90-110)/110 = -18.2%
    returns = [10.0, -18.18, 5.56]
    dd = _max_drawdown(returns)
    assert dd is not None
    assert dd < 0  # è un drawdown


def test_max_drawdown_no_loss():
    assert _max_drawdown([5.0, 3.0, 2.0]) == 0.0


# ---------------------------------------------------------------------------
# GATE_THRESHOLDS sanity
# ---------------------------------------------------------------------------
def test_gate_thresholds_sensible():
    """Le soglie di Phase 7 devono essere realistiche."""
    assert GATE_THRESHOLDS["min_trades"] >= 10
    assert GATE_THRESHOLDS["min_profit_factor"] >= 1.0
    assert GATE_THRESHOLDS["max_drawdown_pct"] < 0  # è un limite negativo
    assert GATE_THRESHOLDS["min_win_rate_contrarian"] > GATE_THRESHOLDS["min_win_rate_momentum"]
