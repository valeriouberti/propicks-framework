"""Test del fetcher S&P 500 + cache + fallback chain.

Strategy: mock ``pd.read_html`` per evitare network. Test coprono:
- Normalizzazione ticker (BRK.B → BRK-B per yfinance)
- Sanity check su < INDEX_MIN_CONSTITUENTS → ValueError
- Cache hit dopo primo fetch
- Cache stale → re-fetch
- Wikipedia fail → fallback su cache stale
- Wikipedia fail + cache vuota → fallback hardcoded SP500_FALLBACK
- force_refresh bypass cache
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from propicks.io.db import (
    index_constituents_is_fresh,
    index_constituents_read,
    index_constituents_replace,
)
from propicks.market.index_constituents import (
    INDEX_NAME_SP500,
    SP500_FALLBACK,
    _fetch_sp500_from_wikipedia,
    _normalize_yf_ticker,
    get_sp500_universe,
    get_sp500_universe_detailed,
)


# ---------------------------------------------------------------------------
# Synthetic Wikipedia DataFrame (valid)
# ---------------------------------------------------------------------------
def _make_wiki_df(n: int = 503) -> pd.DataFrame:
    """Tabella sintetica nel formato Wikipedia S&P 500.

    Schema: Symbol, Security, GICS Sector, Date added.
    Include un BRK.B per testare la normalizzazione del dot.
    """
    base = [
        ("AAPL", "Apple Inc.", "Information Technology", "1982-11-30"),
        ("MSFT", "Microsoft Corp.", "Information Technology", "1994-06-01"),
        ("BRK.B", "Berkshire Hathaway B", "Financials", "2010-02-16"),
        ("BF.B", "Brown-Forman Class B", "Consumer Staples", "1982-06-30"),
    ]
    rows = list(base)
    # Pad fino a n con ticker fittizi per superare il sanity check
    for i in range(len(rows), n):
        rows.append((f"SYN{i:04d}", f"Synthetic Co. {i}", "Industrials", "2020-01-01"))
    return pd.DataFrame(rows, columns=["Symbol", "Security", "GICS Sector", "Date added"])


# ---------------------------------------------------------------------------
# _normalize_yf_ticker
# ---------------------------------------------------------------------------
def test_normalize_dot_to_dash():
    assert _normalize_yf_ticker("BRK.B") == "BRK-B"
    assert _normalize_yf_ticker("BF.B") == "BF-B"


def test_normalize_no_change_for_simple_ticker():
    assert _normalize_yf_ticker("AAPL") == "AAPL"


def test_normalize_strips_whitespace():
    assert _normalize_yf_ticker("  AAPL  ") == "AAPL"


# ---------------------------------------------------------------------------
# _fetch_sp500_from_wikipedia
# ---------------------------------------------------------------------------
def test_fetch_wikipedia_parses_and_normalizes():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]):
        rows = _fetch_sp500_from_wikipedia()

    assert len(rows) == 503
    tickers = [r["ticker"] for r in rows]
    assert "AAPL" in tickers
    assert "BRK-B" in tickers  # normalizzato dal dot
    assert "BRK.B" not in tickers
    # Sector preservato
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["sector"] == "Information Technology"
    assert aapl["company_name"] == "Apple Inc."


def test_fetch_wikipedia_too_few_constituents_raises():
    """Sanity check: una tabella con < INDEX_MIN_CONSTITUENTS = format change."""
    df = _make_wiki_df(50)  # ben sotto soglia 480
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]):
        with pytest.raises(ValueError, match="only 50 constituents"):
            _fetch_sp500_from_wikipedia()


def test_fetch_wikipedia_missing_columns_raises():
    """Schema diverso (rename Wikipedia) → ValueError esplicito."""
    df = pd.DataFrame({"Ticker": ["AAPL"], "Name": ["Apple"]})  # wrong columns
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]):
        with pytest.raises(ValueError, match="missing expected columns"):
            _fetch_sp500_from_wikipedia()


def test_fetch_wikipedia_network_error_raises():
    with patch(
        "propicks.market.index_constituents.pd.read_html",
        side_effect=ConnectionError("DNS fail"),
    ):
        with pytest.raises(ValueError, match="Wikipedia fetch failed"):
            _fetch_sp500_from_wikipedia()


# ---------------------------------------------------------------------------
# get_sp500_universe — cache + fallback chain
# ---------------------------------------------------------------------------
def test_get_universe_first_call_fetches_and_caches():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]):
        tickers = get_sp500_universe()

    assert len(tickers) == 503
    assert "AAPL" in tickers
    # Verifica che la cache sia stata popolata
    cached = index_constituents_read(INDEX_NAME_SP500)
    assert len(cached) == 503
    assert index_constituents_is_fresh(INDEX_NAME_SP500, ttl_hours=1.0)


def test_get_universe_second_call_uses_cache():
    """Dopo il primo fetch, il secondo call NON deve chiamare pd.read_html."""
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]) as mock_read:
        get_sp500_universe()
        first_call_count = mock_read.call_count
        # Secondo call: cache fresh → no network
        get_sp500_universe()
        assert mock_read.call_count == first_call_count


def test_get_universe_force_refresh_bypasses_cache():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]) as mock_read:
        get_sp500_universe()  # popola cache
        first_count = mock_read.call_count
        get_sp500_universe(force_refresh=True)  # bypassa
        assert mock_read.call_count == first_count + 1


def test_get_universe_wikipedia_fail_falls_back_to_stale_cache():
    """Wikipedia errore + cache popolata (anche se stale) → usa cache."""
    # Pre-popola la cache manualmente (simula vecchio fetch)
    seed_rows = [
        {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Tech"},
        {"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Tech"},
    ]
    index_constituents_replace(INDEX_NAME_SP500, seed_rows)

    # Simula cache stale + Wikipedia fail
    with patch(
        "propicks.market.index_constituents.index_constituents_is_fresh",
        return_value=False,
    ):
        with patch(
            "propicks.market.index_constituents.pd.read_html",
            side_effect=ConnectionError("DNS fail"),
        ):
            tickers = get_sp500_universe()

    # Fallback su cache esistente (anche se stale)
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_get_universe_wikipedia_fail_empty_cache_uses_hardcoded():
    """Wikipedia errore + cache vuota → snapshot hardcoded SP500_FALLBACK."""
    # Cache vuota (test isolation garantisce DB fresco)
    with patch(
        "propicks.market.index_constituents.pd.read_html",
        side_effect=ConnectionError("DNS fail"),
    ):
        tickers = get_sp500_universe()

    # Fallback su snapshot hardcoded
    fallback_tickers = {r["ticker"] for r in SP500_FALLBACK}
    assert set(tickers) == fallback_tickers


def test_get_universe_too_few_from_wiki_uses_fallback():
    """Sanity check trigger: Wikipedia ritorna troppi pochi → fallback."""
    short_df = _make_wiki_df(50)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[short_df]):
        tickers = get_sp500_universe()

    # Cache vuota → snapshot hardcoded
    fallback_tickers = {r["ticker"] for r in SP500_FALLBACK}
    assert set(tickers) == fallback_tickers


def test_get_universe_detailed_returns_full_metadata():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents.pd.read_html", return_value=[df]):
        rows = get_sp500_universe_detailed()

    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["company_name"] == "Apple Inc."
    assert aapl["sector"] == "Information Technology"


# ---------------------------------------------------------------------------
# DB helpers — direct test
# ---------------------------------------------------------------------------
def test_index_constituents_replace_is_atomic():
    """Replace deve sostituire completamente la lista (DELETE + INSERT)."""
    initial = [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "GOOGL"}]
    index_constituents_replace(INDEX_NAME_SP500, initial)
    assert {r["ticker"] for r in index_constituents_read(INDEX_NAME_SP500)} == {
        "AAPL", "MSFT", "GOOGL",
    }

    # Replace con lista diversa: i vecchi spariscono
    new_list = [{"ticker": "TSLA"}, {"ticker": "NVDA"}]
    index_constituents_replace(INDEX_NAME_SP500, new_list)
    assert {r["ticker"] for r in index_constituents_read(INDEX_NAME_SP500)} == {
        "TSLA", "NVDA",
    }


def test_index_constituents_replace_empty_is_noop():
    """Replace con lista vuota non deve cancellare il dato esistente
    (safe behavior per fallback fail)."""
    initial = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
    index_constituents_replace(INDEX_NAME_SP500, initial)
    n = index_constituents_replace(INDEX_NAME_SP500, [])
    assert n == 0
    # I dati originali devono essere ancora lì
    assert len(index_constituents_read(INDEX_NAME_SP500)) == 2


def test_index_constituents_is_fresh_after_replace():
    index_constituents_replace(INDEX_NAME_SP500, [{"ticker": "AAPL"}])
    assert index_constituents_is_fresh(INDEX_NAME_SP500, ttl_hours=1.0) is True


def test_index_constituents_is_fresh_false_when_empty():
    assert index_constituents_is_fresh(INDEX_NAME_SP500, ttl_hours=1.0) is False
