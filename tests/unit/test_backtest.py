"""Test backtest engine + metrics. Usa DataFrame sintetici (no rete)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from propicks.backtest.engine import (
    MIN_WARMUP_BARS,
    Trade,
    backtest_ticker,
)
from propicks.backtest.metrics import (
    aggregate_metrics,
    avg_bars_held,
    avg_win_loss,
    cagr,
    compute_metrics,
    exit_reason_breakdown,
    expectancy,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from propicks.market.yfinance_client import DataUnavailable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_history(n_bars: int = 300, trend: str = "up", seed: int = 0) -> pd.DataFrame:
    """Genera OHLCV sintetico. trend='up' produce uptrend pulito, 'flat' randomwalk."""
    rng = np.random.default_rng(seed)
    if trend == "up":
        # Drift positivo + noise
        steps = rng.normal(0.002, 0.01, n_bars)
    elif trend == "down":
        steps = rng.normal(-0.002, 0.01, n_bars)
    else:  # flat
        steps = rng.normal(0.0, 0.01, n_bars)

    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_bars)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_bars)))
    open_ = close * (1 + rng.normal(0, 0.002, n_bars))
    volume = rng.integers(1_000_000, 5_000_000, n_bars).astype(float)

    idx = pd.date_range("2024-01-01", periods=n_bars, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def _make_trade(pnl_pct: float, reason: str = "target", bars: int = 10) -> Trade:
    t = Trade(
        ticker="TST",
        entry_date=date(2026, 1, 1),
        entry_price=100.0,
        stop_price=90.0,
        target_price=120.0,
        shares=10.0,
        entry_score=70.0,
    )
    t.exit_date = date(2026, 1, 1 + bars)
    t.exit_price = 100.0 * (1 + pnl_pct)
    t.exit_reason = reason
    t.pnl_pct = pnl_pct
    t.bars_held = bars
    return t


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def test_backtest_raises_on_short_history():
    short = _make_history(n_bars=MIN_WARMUP_BARS + 5)
    with pytest.raises(DataUnavailable):
        backtest_ticker("TST", history=short)


def test_backtest_uptrend_produces_trades():
    """Uptrend pulito → almeno qualche trade dovrebbe scattare con threshold basso."""
    hist = _make_history(n_bars=300, trend="up", seed=1)
    result = backtest_ticker("TST", history=hist, threshold=40.0)
    assert result.ticker == "TST"
    assert result.signals_taken == len(result.trades)
    # Equity curve presente
    assert not result.equity_curve.empty


def test_backtest_threshold_too_high_no_trades():
    hist = _make_history(n_bars=300, trend="up", seed=2)
    result = backtest_ticker("TST", history=hist, threshold=200.0)  # impossibile
    assert result.trades == []
    assert result.signals_generated == 0


def test_backtest_trade_pnl_consistency():
    """Per ogni trade chiuso: pnl_pct == (exit-entry)/entry."""
    hist = _make_history(n_bars=400, trend="up", seed=3)
    result = backtest_ticker("TST", history=hist, threshold=40.0)
    for t in result.trades:
        if t.exit_price is not None:
            expected = (t.exit_price - t.entry_price) / t.entry_price
            assert t.pnl_pct == pytest.approx(expected, abs=1e-9)


def test_backtest_exit_reasons_in_known_set():
    hist = _make_history(n_bars=400, trend="flat", seed=4)
    result = backtest_ticker("TST", history=hist, threshold=40.0)
    valid = {"stop", "target", "time", "eod"}
    for t in result.trades:
        if t.exit_reason is not None:
            assert t.exit_reason in valid


def test_backtest_handles_tz_aware_index():
    """yfinance ritorna DatetimeIndex tz-aware. L'engine deve normalizzarlo:
    altrimenti hist.index.get_loc(pd.Timestamp(entry_date)) esplode con
    KeyError perché entry_date round-trip via .date() è tz-naive."""
    hist = _make_history(n_bars=400, trend="up", seed=7)
    hist.index = hist.index.tz_localize("America/New_York")
    # Non deve sollevare TypeError/KeyError
    result = backtest_ticker("TST", history=hist, threshold=40.0)
    assert result.ticker == "TST"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_win_rate_basic():
    trades = [_make_trade(0.05), _make_trade(-0.02), _make_trade(0.10)]
    assert win_rate(trades) == pytest.approx(2 / 3)


def test_win_rate_empty_returns_none():
    assert win_rate([]) is None


def test_profit_factor_basic():
    trades = [_make_trade(0.10), _make_trade(0.05), _make_trade(-0.05)]
    # gains 0.15 / |losses| 0.05 = 3.0
    assert profit_factor(trades) == pytest.approx(3.0)


def test_profit_factor_no_losses_returns_none():
    """Senza losers il profit factor è infinito → None per evitare divisione."""
    trades = [_make_trade(0.10), _make_trade(0.05)]
    assert profit_factor(trades) is None


def test_avg_win_loss():
    trades = [_make_trade(0.10), _make_trade(0.04), _make_trade(-0.05), _make_trade(-0.03)]
    aw, al = avg_win_loss(trades)
    assert aw == pytest.approx(0.07)
    assert al == pytest.approx(-0.04)


def test_max_drawdown_simple():
    eq = pd.Series([100, 120, 80, 110, 90], index=pd.date_range("2026-01-01", periods=5))
    # Running max: 100,120,120,120,120 → drawdowns: 0, 0, -0.333, -0.083, -0.25
    assert max_drawdown(eq) == pytest.approx(-1 / 3, abs=1e-6)


def test_max_drawdown_empty_returns_none():
    assert max_drawdown(pd.Series(dtype=float)) is None


def test_cagr_one_year_doubling():
    eq = pd.Series(
        [100.0, 200.0],
        index=pd.to_datetime(["2025-01-01", "2026-01-01"]),
    )
    assert cagr(eq) == pytest.approx(1.0, rel=1e-3)  # +100% in 1 anno


def test_cagr_too_short_returns_none():
    eq = pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-01-01", "2026-01-15"]))
    assert cagr(eq) is None


def test_sharpe_too_few_obs():
    eq = pd.Series(range(10), index=pd.date_range("2026-01-01", periods=10), dtype=float)
    assert sharpe_ratio(eq) is None


def test_sharpe_constant_equity_returns_none():
    """Equity piatta → std=0 → None (no division by zero)."""
    eq = pd.Series([100.0] * 50, index=pd.date_range("2026-01-01", periods=50))
    assert sharpe_ratio(eq) is None


def test_expectancy_basic():
    trades = [_make_trade(0.10), _make_trade(0.10), _make_trade(-0.05)]
    # wr=2/3, aw=0.10, al=-0.05
    # exp = 2/3 * 0.10 + 1/3 * (-0.05) = 0.0667 - 0.0167 = 0.05
    assert expectancy(trades) == pytest.approx(0.05, rel=1e-3)


def test_exit_reason_breakdown_counts():
    trades = [
        _make_trade(0.10, reason="target"),
        _make_trade(0.10, reason="target"),
        _make_trade(-0.05, reason="stop"),
    ]
    breakdown = exit_reason_breakdown(trades)
    assert breakdown == {"target": 2, "stop": 1}


def test_avg_bars_held():
    trades = [_make_trade(0.05, bars=10), _make_trade(-0.02, bars=20)]
    assert avg_bars_held(trades) == 15.0


# ---------------------------------------------------------------------------
# compute_metrics / aggregate_metrics
# ---------------------------------------------------------------------------


def test_compute_metrics_smoke():
    hist = _make_history(n_bars=400, trend="up", seed=5)
    result = backtest_ticker("TST", history=hist, threshold=40.0)
    m = compute_metrics(result)
    # Chiavi minime garantite
    for k in [
        "ticker", "n_trades", "signals_generated", "win_rate", "profit_factor",
        "avg_win_pct", "avg_loss_pct", "expectancy_pct", "max_drawdown_pct",
        "cagr_pct", "sharpe", "sortino", "exit_reasons",
    ]:
        assert k in m
    assert m["ticker"] == "TST"


def test_aggregate_metrics_merges_trades():
    h1 = _make_history(n_bars=300, trend="up", seed=10)
    h2 = _make_history(n_bars=300, trend="up", seed=11)
    r1 = backtest_ticker("AAA", history=h1, threshold=40.0)
    r2 = backtest_ticker("BBB", history=h2, threshold=40.0)
    agg = aggregate_metrics({"AAA": r1, "BBB": r2})
    assert agg["n_tickers"] == 2
    assert agg["n_trades"] == len(r1.trades) + len(r2.trades)
