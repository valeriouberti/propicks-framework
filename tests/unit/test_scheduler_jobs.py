"""Test dei job functions (jobs.py).

Strategy: mock yfinance al livello cache e scoring al livello domain
per non colpire la rete. I test verificano:
- idempotenza (2 run stesso giorno → UPSERT corretto)
- alert generation (regime change, watchlist ready, trailing update)
- scheduler_runs audit log (1 row per call, success/error)
"""

from __future__ import annotations

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
def _synthetic_weekly_df(n_bars: int = 80) -> pd.DataFrame:
    idx = pd.date_range(end="2026-04-20", periods=n_bars, freq="W-MON")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n_bars)],
            "High": [101.0 + i for i in range(n_bars)],
            "Low": [99.0 + i for i in range(n_bars)],
            "Close": [100.5 + i for i in range(n_bars)],
            "Adj Close": [100.5 + i for i in range(n_bars)],
            "Volume": [5_000_000] * n_bars,
        },
        index=idx,
    )


def _mock_regime(code: int, label: str) -> dict:
    return {
        "regime": label,
        "regime_code": code,
        "entry_allowed": code >= 3,
        "adx": 20.0,
        "rsi": 50.0,
        "macd_hist": 0.1,
        "ema_fast": 100,
        "ema_slow": 95,
        "ema_200d": 90,
        "trend": "BULL",
        "trend_strength": "STRONG",
        "momentum": "BULL",
        "price": 100.0,
    }


# ---------------------------------------------------------------------------
# record_regime
# ---------------------------------------------------------------------------
def test_record_regime_writes_row(monkeypatch):
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    monkeypatch.setattr(jobs, "download_weekly_history", lambda t: _synthetic_weekly_df())
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(4, "BULL"))

    result = jobs.record_regime(record_date="2026-04-24")
    assert result["n_items"] == 1

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM regime_history WHERE date = ?", ("2026-04-24",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["regime_code"] == 4
    assert row["regime_label"] == "BULL"


def test_record_regime_idempotent_upsert(monkeypatch):
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    monkeypatch.setattr(jobs, "download_weekly_history", lambda t: _synthetic_weekly_df())
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(4, "BULL"))

    jobs.record_regime(record_date="2026-04-24")
    # Ricalcola con regime diverso stesso giorno → UPSERT, una sola riga
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(5, "STRONG_BULL"))
    jobs.record_regime(record_date="2026-04-24")

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM regime_history WHERE date = ?", ("2026-04-24",)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["regime_code"] == 5  # ultimo vince


def test_record_regime_change_creates_alert(monkeypatch):
    from propicks.scheduler import jobs
    from propicks.scheduler.alerts import list_pending_alerts

    monkeypatch.setattr(jobs, "download_weekly_history", lambda t: _synthetic_weekly_df())

    # Giorno 1: regime NEUTRAL (3)
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(3, "NEUTRAL"))
    jobs.record_regime(record_date="2026-04-23")

    # Nessun alert ancora (non c'è un "previous")
    alerts = list_pending_alerts()
    assert all(a["type"] != "regime_change" for a in alerts)

    # Giorno 2: regime BULL (4) → change detection
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(4, "BULL"))
    jobs.record_regime(record_date="2026-04-24")

    alerts = list_pending_alerts()
    change_alerts = [a for a in alerts if a["type"] == "regime_change"]
    assert len(change_alerts) == 1
    assert change_alerts[0]["metadata"]["from_code"] == 3
    assert change_alerts[0]["metadata"]["to_code"] == 4


# ---------------------------------------------------------------------------
# snapshot_portfolio
# ---------------------------------------------------------------------------
def test_snapshot_portfolio_empty(monkeypatch):
    """Portfolio vuoto → snapshot con 0 positions, cash = initial capital."""
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    monkeypatch.setattr(jobs, "get_current_prices", lambda ts: {})

    result = jobs.snapshot_portfolio(snapshot_date="2026-04-24")
    assert result["n_items"] == 0

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE date = ?", ("2026-04-24",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["n_positions"] == 0
    assert row["cash"] > 0  # default CAPITAL = 10k


def test_snapshot_portfolio_mark_to_market(monkeypatch):
    """Posizione aperta + current price → invested_value = shares * price."""
    from propicks.io.db import connect
    from propicks.io.portfolio_store import add_position, load_portfolio
    from propicks.scheduler import jobs

    pf = load_portfolio()
    add_position(
        pf, ticker="AAPL", entry_price=100.0, shares=10,
        stop_loss=92.0, target=115.0,
        strategy="TechTitans", score_claude=7, score_tech=70, catalyst=None,
    )

    # AAPL a 110 (mark-to-market +10%)
    monkeypatch.setattr(
        jobs, "get_current_prices",
        lambda ts: {t: 110.0 if t == "AAPL" else None for t in ts},
    )

    jobs.snapshot_portfolio(snapshot_date="2026-04-24")

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE date = ?", ("2026-04-24",)
        ).fetchone()
    finally:
        conn.close()
    assert row["invested_value"] == 1100.0  # 10 × 110
    assert row["n_positions"] == 1


def test_snapshot_portfolio_contrarian_exposure_breakdown(monkeypatch):
    """Il bucket contrarian è isolato nel breakdown exposure."""
    from propicks.io.db import connect
    from propicks.io.portfolio_store import add_position, load_portfolio
    from propicks.scheduler import jobs

    pf = load_portfolio()
    add_position(
        pf, ticker="AAPL", entry_price=100.0, shares=5,
        stop_loss=92.0, target=115.0,
        strategy="TechTitans", score_claude=7, score_tech=70, catalyst=None,
    )
    add_position(
        pf, ticker="MSFT", entry_price=100.0, shares=3,
        stop_loss=92.0, target=115.0,
        strategy="Contrarian", score_claude=7, score_tech=65, catalyst="flush",
    )

    monkeypatch.setattr(
        jobs, "get_current_prices",
        lambda ts: {t: 100.0 for t in ts},
    )
    jobs.snapshot_portfolio(snapshot_date="2026-04-24")

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE date = ?", ("2026-04-24",)
        ).fetchone()
    finally:
        conn.close()
    # AAPL 500€ → momentum, MSFT 300€ → contrarian
    assert row["momentum_exposure_pct"] > row["contra_exposure_pct"]


def test_snapshot_portfolio_idempotent(monkeypatch):
    """2 run stesso giorno → 1 riga (UPSERT)."""
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    monkeypatch.setattr(jobs, "get_current_prices", lambda ts: {})

    jobs.snapshot_portfolio(snapshot_date="2026-04-24")
    jobs.snapshot_portfolio(snapshot_date="2026-04-24")

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE date = ?", ("2026-04-24",)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# scan_watchlist
# ---------------------------------------------------------------------------
def test_scan_watchlist_empty(monkeypatch):
    from propicks.scheduler import jobs
    result = jobs.scan_watchlist()
    assert result["n_items"] == 0


def test_scan_watchlist_ready_alert(monkeypatch):
    """Ticker con score >= 60 e distanza <= 2% → alert READY."""
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
    from propicks.scheduler import jobs
    from propicks.scheduler.alerts import list_pending_alerts

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=100.0, source="manual")

    # Mock analyze_ticker: score 75, price 100.5 (1% sotto target)
    def _mock_analyze(ticker, strategy=None):
        return {
            "ticker": ticker.upper(),
            "score_composite": 75.0,
            "classification": "A — AZIONE IMMEDIATA",
            "price": 100.5,
            "rsi": 55.0,
            "atr": 2.0,
            "scores": {"trend": 80, "momentum": 70, "volume": 60, "distance_high": 85, "volatility": 60, "ma_cross": 75},
            "regime": {"regime_code": 4, "regime": "BULL"},
        }

    monkeypatch.setattr(jobs, "analyze_ticker", _mock_analyze)

    result = jobs.scan_watchlist(ready_distance_pct=0.02)
    assert result["n_items"] == 1

    alerts = list_pending_alerts()
    ready = [a for a in alerts if a["type"] == "watchlist_ready"]
    assert len(ready) == 1
    assert ready[0]["ticker"] == "AAPL"


def test_scan_watchlist_no_ready_when_score_low(monkeypatch):
    """Score < 60 → NO alert anche se distanza è piccola."""
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
    from propicks.scheduler import jobs
    from propicks.scheduler.alerts import list_pending_alerts

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=100.0)

    def _mock(t, strategy=None):
        return {
            "ticker": t.upper(), "score_composite": 55.0,  # SOTTO 60
            "classification": "C", "price": 100.1, "rsi": 55,
            "atr": 2, "scores": {}, "regime": {"regime_code": 3, "regime": "NEUTRAL"},
        }

    monkeypatch.setattr(jobs, "analyze_ticker", _mock)
    jobs.scan_watchlist()

    alerts = list_pending_alerts()
    assert not any(a["type"] == "watchlist_ready" for a in alerts)


def test_scan_watchlist_populates_strategy_runs(monkeypatch):
    """Ogni analisi produce una riga in strategy_runs (historicization)."""
    from propicks.io.db import connect
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
    from propicks.scheduler import jobs

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=100.0)
    add_to_watchlist(wl, "MSFT", target_entry=200.0)

    def _mock(t, strategy=None):
        return {
            "ticker": t.upper(), "score_composite": 70.0,
            "classification": "B", "price": 50.0, "rsi": 55,
            "atr": 2, "scores": {"trend": 70}, "regime": {"regime_code": 4, "regime": "BULL"},
        }

    monkeypatch.setattr(jobs, "analyze_ticker", _mock)
    jobs.scan_watchlist()

    conn = connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM strategy_runs WHERE action_taken='watchlist_scan'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2


# ---------------------------------------------------------------------------
# cleanup_stale_watchlist
# ---------------------------------------------------------------------------
def test_cleanup_stale_watchlist_emits_alert_when_stale():
    from datetime import datetime, timedelta

    from propicks.config import DATE_FMT
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
    from propicks.scheduler import jobs
    from propicks.scheduler.alerts import list_pending_alerts

    old_date = (datetime.now() - timedelta(days=90)).strftime(DATE_FMT)

    wl = load_watchlist()
    # Aggiungi stale manualmente per simulare
    add_to_watchlist(
        wl, "OLD", target_entry=100.0,
        added_date=old_date, source="manual",
    )
    add_to_watchlist(wl, "FRESH", target_entry=100.0, source="manual")

    result = jobs.cleanup_stale_watchlist(days=60)
    assert result["n_items"] == 1

    alerts = list_pending_alerts()
    stale = [a for a in alerts if a["type"] == "stale_watchlist"]
    assert len(stale) == 1
    assert "OLD" in stale[0]["metadata"]["tickers"]


# ---------------------------------------------------------------------------
# scheduler_runs audit log (@run_job decorator)
# ---------------------------------------------------------------------------
def test_run_job_logs_success(monkeypatch):
    """Job con successo → riga in scheduler_runs con status=success."""
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    monkeypatch.setattr(jobs, "download_weekly_history", lambda t: _synthetic_weekly_df())
    monkeypatch.setattr(jobs, "classify_regime", lambda w: _mock_regime(3, "NEUTRAL"))

    jobs.record_regime(record_date="2026-04-24")

    conn = connect()
    try:
        row = conn.execute(
            """SELECT * FROM scheduler_runs
               WHERE job_name='record_regime'
               ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "success"
    assert row["duration_ms"] is not None
    assert row["finished_at"] is not None


def test_run_job_logs_error(monkeypatch):
    """Job che solleva → riga con status=error, error popolato, re-raise."""
    from propicks.io.db import connect
    from propicks.scheduler import jobs

    def _boom(t):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(jobs, "download_weekly_history", _boom)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        jobs.record_regime(record_date="2026-04-24")

    conn = connect()
    try:
        row = conn.execute(
            """SELECT * FROM scheduler_runs
               WHERE job_name='record_regime'
               ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "error"
    assert "synthetic failure" in row["error"]
