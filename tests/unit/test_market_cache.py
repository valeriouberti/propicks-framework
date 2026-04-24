"""Test del cache layer OHLCV (Phase 2).

Strategy: mock yfinance at the `_yf_fetch_*` helper level. I test verificano:
- Cache hit: dopo il primo fetch, il secondo call NON chiama yfinance
- Cache miss: forza fetch e UPSERT
- Staleness: righe fuori TTL vengono ignorate
- Round-trip: DataFrame caricato → UPSERT → read → ricostruito correttamente
- Meta (sector/beta): stesso pattern, TTL 7gg
- Clear: selettivo per ticker, interval, stale

Zero rete grazie ai mock dei ``_yf_fetch_*``.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from propicks.config import MARKET_MIN_DAILY_BARS, REGIME_MIN_WEEKLY_BARS


def _synthetic_daily_df(n_bars: int = 200) -> pd.DataFrame:
    """DataFrame sintetico stile yfinance con colonne corrette."""
    idx = pd.date_range(end="2026-04-24", periods=n_bars, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0 + i * 0.5 for i in range(n_bars)],
            "High": [101.0 + i * 0.5 for i in range(n_bars)],
            "Low": [99.0 + i * 0.5 for i in range(n_bars)],
            "Close": [100.5 + i * 0.5 for i in range(n_bars)],
            "Adj Close": [100.5 + i * 0.5 for i in range(n_bars)],
            "Volume": [1_000_000 + i * 1000 for i in range(n_bars)],
        },
        index=idx,
    )


def _synthetic_weekly_df(n_bars: int = 80) -> pd.DataFrame:
    idx = pd.date_range(end="2026-04-20", periods=n_bars, freq="W-MON")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n_bars)],
            "High": [101.0 + i for i in range(n_bars)],
            "Low": [99.0 + i for i in range(n_bars)],
            "Close": [100.5 + i for i in range(n_bars)],
            "Adj Close": [100.5 + i for i in range(n_bars)],
            "Volume": [5_000_000 + i * 10000 for i in range(n_bars)],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Round-trip: DataFrame → UPSERT → read → DataFrame
# ---------------------------------------------------------------------------
def test_round_trip_preserves_daily_bars():
    from propicks.io.db import market_ohlcv_read, market_ohlcv_upsert
    from propicks.market.yfinance_client import _cache_rows_to_yf_df, _yf_df_to_cache_rows

    df_in = _synthetic_daily_df(50)
    rows = _yf_df_to_cache_rows(df_in, "date")
    market_ohlcv_upsert("AAPL", "daily", rows)

    read_back = market_ohlcv_read("AAPL", "daily")
    df_out = _cache_rows_to_yf_df(read_back)

    assert len(df_out) == 50
    assert list(df_out.columns) == ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    # Valori preservati entro float tolerance
    assert df_out["Close"].iloc[-1] == pytest.approx(df_in["Close"].iloc[-1])
    assert df_out["Volume"].iloc[0] == df_in["Volume"].iloc[0]


def test_round_trip_skips_nan_close():
    from propicks.io.db import market_ohlcv_read, market_ohlcv_upsert
    from propicks.market.yfinance_client import _yf_df_to_cache_rows

    df_in = _synthetic_daily_df(10)
    # Pianta un NaN nel Close
    df_in.iloc[5, df_in.columns.get_loc("Close")] = float("nan")

    rows = _yf_df_to_cache_rows(df_in, "date")
    market_ohlcv_upsert("TST", "daily", rows)

    read_back = market_ohlcv_read("TST", "daily")
    # 9 invece di 10 — la riga con NaN è stata skippata
    assert len(read_back) == 9


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------
def test_download_history_caches_first_call(monkeypatch):
    """Primo call fetch+upsert, secondo call solo read dal cache (no yfinance)."""
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(MARKET_MIN_DAILY_BARS + 10)
    fetch_calls = []

    def _mock_fetch(ticker, period):
        fetch_calls.append((ticker, period))
        return df_synth

    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", _mock_fetch)

    # 1° call: miss → fetch
    df1 = yfinance_client.download_history("AAPL")
    assert len(fetch_calls) == 1
    assert len(df1) >= MARKET_MIN_DAILY_BARS

    # 2° call: hit cache → NO fetch
    df2 = yfinance_client.download_history("AAPL")
    assert len(fetch_calls) == 1  # immutato
    assert len(df2) >= MARKET_MIN_DAILY_BARS


def test_download_history_raises_if_insufficient(monkeypatch):
    """Meno di MARKET_MIN_DAILY_BARS → DataUnavailable."""
    from propicks.market import yfinance_client

    df_short = _synthetic_daily_df(50)  # sotto MIN (155)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_short)

    with pytest.raises(yfinance_client.DataUnavailable, match="storia insufficiente"):
        yfinance_client.download_history("SHRT")


def test_download_weekly_history_caches(monkeypatch):
    from propicks.market import yfinance_client

    df_synth = _synthetic_weekly_df(REGIME_MIN_WEEKLY_BARS + 10)
    fetch_calls = []

    def _mock_fetch(ticker, period):
        fetch_calls.append((ticker, period))
        return df_synth

    monkeypatch.setattr(yfinance_client, "_yf_fetch_weekly", _mock_fetch)

    yfinance_client.download_weekly_history("AAPL")
    yfinance_client.download_weekly_history("AAPL")
    assert len(fetch_calls) == 1  # cache hit sulla seconda


# ---------------------------------------------------------------------------
# Staleness (TTL)
# ---------------------------------------------------------------------------
def test_stale_cache_triggers_refetch(monkeypatch):
    """Se cache è stale (fetched_at > TTL), il fetch avviene di nuovo.

    Simuliamo stale manipolando ``fetched_at`` direttamente in DB.
    """
    from propicks.io.db import connect
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(MARKET_MIN_DAILY_BARS + 10)
    fetch_calls = []

    def _mock_fetch(ticker, period):
        fetch_calls.append((ticker, period))
        return df_synth

    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", _mock_fetch)

    # Primo fetch: popola cache
    yfinance_client.download_history("AAPL")
    assert len(fetch_calls) == 1

    # Forza staleness: setta fetched_at a 24h fa (TTL default = 8h)
    conn = connect()
    try:
        conn.execute(
            "UPDATE market_ohlcv_daily SET fetched_at = datetime('now', '-24 hours') WHERE ticker='AAPL'"
        )
    finally:
        conn.close()

    # Secondo fetch: stale → miss → fetch nuovamente
    yfinance_client.download_history("AAPL")
    assert len(fetch_calls) == 2


# ---------------------------------------------------------------------------
# market_ohlcv_clear (invalidation)
# ---------------------------------------------------------------------------
def test_clear_by_ticker(monkeypatch):
    from propicks.io.db import market_ohlcv_clear, market_ohlcv_read
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(MARKET_MIN_DAILY_BARS + 10)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_synth)

    yfinance_client.download_history("AAPL")
    yfinance_client.download_history("MSFT")

    # Clear solo AAPL
    n = market_ohlcv_clear(ticker="AAPL")
    assert n > 0
    assert market_ohlcv_read("AAPL", "daily") == []
    # MSFT non toccato
    assert len(market_ohlcv_read("MSFT", "daily")) > 0


def test_clear_by_interval(monkeypatch):
    from propicks.io.db import market_ohlcv_clear, market_ohlcv_read
    from propicks.market import yfinance_client

    df_d = _synthetic_daily_df(MARKET_MIN_DAILY_BARS + 10)
    df_w = _synthetic_weekly_df(REGIME_MIN_WEEKLY_BARS + 10)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_d)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_weekly", lambda t, p: df_w)

    yfinance_client.download_history("AAPL")
    yfinance_client.download_weekly_history("AAPL")

    # Clear solo daily
    market_ohlcv_clear(interval="daily")
    assert market_ohlcv_read("AAPL", "daily") == []
    assert len(market_ohlcv_read("AAPL", "weekly")) > 0


def test_clear_stale_only(monkeypatch):
    """Solo le righe con fetched_at < ttl_hours fa vengono rimosse."""
    from propicks.io.db import connect, market_ohlcv_clear, market_ohlcv_read
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(MARKET_MIN_DAILY_BARS + 10)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_synth)

    yfinance_client.download_history("AAPL")

    # Setta fetched_at a 48h fa per metà delle righe
    conn = connect()
    try:
        conn.execute(
            """UPDATE market_ohlcv_daily
               SET fetched_at = datetime('now', '-48 hours')
               WHERE ticker='AAPL' AND date < '2026-01-01'"""
        )
    finally:
        conn.close()

    # Clear con TTL 24h: rimuove solo le righe vecchie 48h
    n = market_ohlcv_clear(stale_ttl_hours=24.0)
    remaining = market_ohlcv_read("AAPL", "daily")
    # Restano righe recenti (fetched al step sopra)
    assert len(remaining) > 0
    # Alcune cancellate
    assert n > 0


# ---------------------------------------------------------------------------
# Ticker meta (sector, beta)
# ---------------------------------------------------------------------------
def test_sector_cached_after_first_call(monkeypatch):
    """Primo call yf.Ticker.info, secondo call cache."""
    from propicks.market import yfinance_client

    info_calls = []

    def _mock_info(ticker):
        info_calls.append(ticker)
        return {"sector": "Technology", "beta": 1.2, "shortName": "Apple Inc."}

    monkeypatch.setattr(yfinance_client, "_yf_fetch_info", _mock_info)

    s1 = yfinance_client.get_ticker_sector("AAPL")
    s2 = yfinance_client.get_ticker_sector("AAPL")
    assert s1 == "Technology"
    assert s2 == "Technology"
    assert len(info_calls) == 1  # cache hit


def test_beta_cached_with_sector_same_call(monkeypatch):
    """Una singola fetch popola sia sector che beta. Chiamate successive cache-hit."""
    from propicks.market import yfinance_client

    info_calls = []

    def _mock_info(ticker):
        info_calls.append(ticker)
        return {"sector": "Energy", "beta": 0.85}

    monkeypatch.setattr(yfinance_client, "_yf_fetch_info", _mock_info)

    # Primo call: sector → fetch
    yfinance_client.get_ticker_sector("XOM")
    assert len(info_calls) == 1
    # Secondo call: beta → cache hit (popolato dalla stessa info call)
    beta = yfinance_client.get_ticker_beta("XOM")
    assert beta == 0.85
    assert len(info_calls) == 1


def test_meta_returns_none_on_fetch_fail(monkeypatch):
    from propicks.market import yfinance_client

    monkeypatch.setattr(yfinance_client, "_yf_fetch_info", lambda t: None)

    assert yfinance_client.get_ticker_sector("XYZ") is None
    assert yfinance_client.get_ticker_beta("XYZ") is None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def test_stats_counts_after_warm(monkeypatch):
    from propicks.io.db import market_ohlcv_stats
    from propicks.market import yfinance_client

    df_d = _synthetic_daily_df(200)
    df_w = _synthetic_weekly_df(100)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_d)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_weekly", lambda t, p: df_w)

    yfinance_client.download_history("AAPL")
    yfinance_client.download_history("MSFT")
    yfinance_client.download_weekly_history("AAPL")

    stats = market_ohlcv_stats()
    assert stats["daily"]["n_tickers"] == 2
    assert stats["daily"]["total_rows"] == 400  # 200 × 2
    assert stats["weekly"]["n_tickers"] == 1
    assert stats["weekly"]["total_rows"] == 100


# ---------------------------------------------------------------------------
# get_current_prices cache-aware
# ---------------------------------------------------------------------------
def test_get_current_prices_uses_cache(monkeypatch):
    """Se tutti i ticker sono nel cache fresh, no yf.download call."""
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(200)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_synth)

    # Popola cache
    yfinance_client.download_history("AAPL")
    yfinance_client.download_history("MSFT")

    # Mock yf.download per verificare che NON viene chiamato
    download_calls = []

    def _mock_download(**kwargs):
        download_calls.append(kwargs)
        return pd.DataFrame()

    with patch("propicks.market.yfinance_client.yf.download", side_effect=_mock_download):
        prices = yfinance_client.get_current_prices(["AAPL", "MSFT"])

    assert "AAPL" in prices
    assert "MSFT" in prices
    assert len(download_calls) == 0  # cache sufficient


def test_get_current_prices_fallback_for_missing(monkeypatch):
    """Se un ticker è in cache e uno no, solo il missing va in yf.download."""
    from propicks.market import yfinance_client

    df_synth = _synthetic_daily_df(200)
    monkeypatch.setattr(yfinance_client, "_yf_fetch_daily", lambda t, p: df_synth)

    # Popola cache solo per AAPL
    yfinance_client.download_history("AAPL")

    def _mock_download(**kwargs):
        # Ritorna un DataFrame MultiIndex stile yfinance per NVDA
        idx = pd.date_range(end="2026-04-24", periods=5, freq="B")
        return pd.DataFrame({"Close": [200.0, 201, 202, 203, 204]}, index=idx)

    with patch("propicks.market.yfinance_client.yf.download", side_effect=_mock_download):
        prices = yfinance_client.get_current_prices(["AAPL", "NVDA"])

    assert "AAPL" in prices  # dalla cache
    assert "NVDA" in prices  # dal fetch
