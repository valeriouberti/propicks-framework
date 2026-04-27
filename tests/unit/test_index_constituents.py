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
    FTSEMIB_FALLBACK,
    INDEX_NAME_FTSEMIB,
    INDEX_NAME_SP500,
    INDEX_NAME_STOXX600,
    SP500_FALLBACK,
    STOXX600_FALLBACK,
    SUPPORTED_INDEXES,
    _fetch_ftsemib_from_wikipedia,
    _fetch_sp500_from_wikipedia,
    _fetch_stoxx600_from_wikipedia,
    _normalize_ftsemib_ticker,
    _normalize_yf_ticker,
    get_ftsemib_universe,
    get_index_universe,
    get_sp500_universe,
    get_sp500_universe_detailed,
    get_stoxx600_universe,
    index_label,
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
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]):
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
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]):
        with pytest.raises(ValueError, match="only 50 constituents"):
            _fetch_sp500_from_wikipedia()


def test_fetch_wikipedia_missing_columns_raises():
    """Schema diverso (rename Wikipedia) → ValueError esplicito."""
    df = pd.DataFrame({"Ticker": ["AAPL"], "Name": ["Apple"]})  # wrong columns
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]):
        with pytest.raises(ValueError, match="missing expected columns"):
            _fetch_sp500_from_wikipedia()


def test_fetch_wikipedia_network_error_raises():
    """L'helper _read_wikipedia_tables converte network errors in ValueError."""
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        side_effect=ValueError("Wikipedia HTTP error 503: Service Unavailable"),
    ):
        with pytest.raises(ValueError, match="Wikipedia HTTP error"):
            _fetch_sp500_from_wikipedia()


# ---------------------------------------------------------------------------
# get_sp500_universe — cache + fallback chain
# ---------------------------------------------------------------------------
def test_get_universe_first_call_fetches_and_caches():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]):
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
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]) as mock_read:
        get_sp500_universe()
        first_call_count = mock_read.call_count
        # Secondo call: cache fresh → no network
        get_sp500_universe()
        assert mock_read.call_count == first_call_count


def test_get_universe_force_refresh_bypasses_cache():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]) as mock_read:
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
            "propicks.market.index_constituents._read_wikipedia_tables",
            side_effect=ValueError("Wikipedia URL error: DNS fail"),
        ):
            tickers = get_sp500_universe()

    # Fallback su cache esistente (anche se stale)
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_get_universe_wikipedia_fail_empty_cache_uses_hardcoded():
    """Wikipedia errore + cache vuota → snapshot hardcoded SP500_FALLBACK."""
    # Cache vuota (test isolation garantisce DB fresco)
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        side_effect=ValueError("Wikipedia URL error: DNS fail"),
    ):
        tickers = get_sp500_universe()

    # Fallback su snapshot hardcoded
    fallback_tickers = {r["ticker"] for r in SP500_FALLBACK}
    assert set(tickers) == fallback_tickers


def test_get_universe_too_few_from_wiki_uses_fallback():
    """Sanity check trigger: Wikipedia ritorna troppi pochi → fallback."""
    short_df = _make_wiki_df(50)
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[short_df]):
        tickers = get_sp500_universe()

    # Cache vuota → snapshot hardcoded
    fallback_tickers = {r["ticker"] for r in SP500_FALLBACK}
    assert set(tickers) == fallback_tickers


def test_get_universe_detailed_returns_full_metadata():
    df = _make_wiki_df(503)
    with patch("propicks.market.index_constituents._read_wikipedia_tables", return_value=[df]):
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


# ---------------------------------------------------------------------------
# FTSE MIB tests
# ---------------------------------------------------------------------------
def _make_ftsemib_df(n: int = 40) -> pd.DataFrame:
    """Tabella sintetica FTSE MIB. Schema: Ticker, Company, ICB Sector."""
    base = [
        ("ENI", "Eni", "Oil & Gas"),
        ("ENEL", "Enel", "Utilities"),
        ("ISP", "Intesa Sanpaolo", "Banks"),
        ("UCG", "UniCredit", "Banks"),
        ("STLAM", "Stellantis", "Auto"),
    ]
    rows = list(base)
    for i in range(len(rows), n):
        rows.append((f"ITN{i:03d}", f"Italian Co. {i}", "Industrials"))
    return pd.DataFrame(rows, columns=["Ticker", "Company", "ICB Sector"])


def test_normalize_ftsemib_adds_mi_suffix():
    """Ticker senza suffisso → aggiunge .MI per yfinance."""
    assert _normalize_ftsemib_ticker("ENI") == "ENI.MI"
    assert _normalize_ftsemib_ticker("UCG") == "UCG.MI"


def test_normalize_ftsemib_preserves_existing_suffix():
    """Ticker già con .MI → preservato."""
    assert _normalize_ftsemib_ticker("ENI.MI") == "ENI.MI"


def test_fetch_ftsemib_parses_components_table():
    """Wikipedia FTSEMIB ha più tabelle; trovo quella con ≥35 righe e Ticker."""
    infobox = pd.DataFrame({"Key": ["Index"], "Value": ["FTSE MIB"]})  # decoy
    components = _make_ftsemib_df(40)
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[infobox, components],
    ):
        rows = _fetch_ftsemib_from_wikipedia()

    assert len(rows) == 40
    tickers = [r["ticker"] for r in rows]
    assert "ENI.MI" in tickers
    assert "ENI" not in tickers  # già normalizzato
    eni = next(r for r in rows if r["ticker"] == "ENI.MI")
    assert eni["company_name"] == "Eni"
    assert eni["sector"] == "Oil & Gas"


def test_fetch_ftsemib_too_few_constituents_raises():
    short_df = _make_ftsemib_df(20)  # < 35 soglia
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[short_df],
    ):
        with pytest.raises(ValueError, match="no table found"):
            _fetch_ftsemib_from_wikipedia()


def test_get_ftsemib_universe_uses_cache_after_first_fetch():
    df = _make_ftsemib_df(40)
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[df],
    ) as mock:
        get_ftsemib_universe()
        first = mock.call_count
        get_ftsemib_universe()  # cache hit
        assert mock.call_count == first


def test_get_ftsemib_universe_fallback_on_wiki_fail():
    """Wikipedia fail + cache vuota → fallback hardcoded FTSEMIB."""
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        side_effect=ValueError("Wikipedia URL error: fail"),
    ):
        tickers = get_ftsemib_universe()

    fallback_tickers = {r["ticker"] for r in FTSEMIB_FALLBACK}
    assert set(tickers) == fallback_tickers


# ---------------------------------------------------------------------------
# STOXX 600 tests
# ---------------------------------------------------------------------------
def _make_stoxx_df(n: int = 600) -> pd.DataFrame:
    """STOXX 600 components con suffisso exchange già nei ticker."""
    base = [
        ("ASML.AS", "ASML Holding", "Technology"),
        ("SAP.DE", "SAP", "Technology"),
        ("NESN.SW", "Nestle", "Food"),
        ("MC.PA", "LVMH", "Luxury"),
        ("AZN.L", "AstraZeneca", "Healthcare"),
    ]
    rows = list(base)
    for i in range(len(rows), n):
        suffix = ["L", "DE", "PA", "AS", "MI", "SW"][i % 6]
        rows.append((f"EUSYN{i:03d}.{suffix}", f"European Co. {i}", "Industrials"))
    return pd.DataFrame(rows, columns=["Ticker", "Company", "ICB Industry"])


def test_fetch_stoxx_parses_largest_table():
    """STOXX 600 fetcher seleziona la tabella più grande."""
    infobox = pd.DataFrame({"Key": ["Index"], "Value": ["STOXX 600"]})
    components = _make_stoxx_df(600)
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[infobox, components],
    ):
        rows = _fetch_stoxx600_from_wikipedia()

    assert len(rows) == 600
    tickers = [r["ticker"] for r in rows]
    assert "ASML.AS" in tickers
    assert "MC.PA" in tickers


def test_fetch_stoxx_preserves_exchange_suffixes():
    """STOXX 600 ticker hanno suffissi exchange (.L .DE .PA ...) — NON normalizzati."""
    components = _make_stoxx_df(600)
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[components],
    ):
        rows = _fetch_stoxx600_from_wikipedia()

    # Verifica preservation dei suffix exchange
    suffixes = {r["ticker"].split(".")[-1] for r in rows if "." in r["ticker"]}
    assert {"L", "DE", "PA", "AS", "MI", "SW"} <= suffixes


def test_fetch_stoxx_too_few_constituents_raises():
    short_df = _make_stoxx_df(100)  # < 550 soglia
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        return_value=[short_df],
    ):
        with pytest.raises(ValueError, match="only 100 rows"):
            _fetch_stoxx600_from_wikipedia()


def test_get_stoxx_universe_fallback_on_wiki_fail():
    with patch(
        "propicks.market.index_constituents._read_wikipedia_tables",
        side_effect=ValueError("Wikipedia URL error: fail"),
    ):
        tickers = get_stoxx600_universe()

    fallback_tickers = {r["ticker"] for r in STOXX600_FALLBACK}
    assert set(tickers) == fallback_tickers


# ---------------------------------------------------------------------------
# Generic dispatcher
# ---------------------------------------------------------------------------
def test_supported_indexes_registered():
    assert INDEX_NAME_SP500 in SUPPORTED_INDEXES
    assert INDEX_NAME_FTSEMIB in SUPPORTED_INDEXES
    assert INDEX_NAME_STOXX600 in SUPPORTED_INDEXES


def test_get_index_universe_dispatcher_routes_correctly():
    """Il dispatcher chiama il fetcher giusto in base al name."""
    sp_df = _make_wiki_df(503)
    mib_df = _make_ftsemib_df(40)

    # Per SP500 il fetcher legge tables[0]; per FTSEMIB cerca la tabella con
    # Ticker col. Mocchiamo separatamente per ogni call.
    with patch("propicks.market.index_constituents._read_wikipedia_tables") as mock:
        mock.side_effect = [
            [sp_df],   # primo call: sp500
            [mib_df],  # secondo call: ftsemib
        ]
        sp_tickers = get_index_universe(INDEX_NAME_SP500)
        mib_tickers = get_index_universe(INDEX_NAME_FTSEMIB)

    assert "AAPL" in sp_tickers
    assert "ENI.MI" in mib_tickers


def test_get_index_universe_unknown_name_raises():
    with pytest.raises(ValueError, match="non supportato"):
        get_index_universe("nikkei225")


def test_index_label_returns_human_readable():
    assert index_label(INDEX_NAME_SP500) == "S&P 500"
    assert index_label(INDEX_NAME_FTSEMIB) == "FTSE MIB"
    assert index_label(INDEX_NAME_STOXX600) == "STOXX Europe 600"


def test_index_label_unknown_returns_name():
    """Index sconosciuto → ritorna il name stesso (no crash)."""
    assert index_label("unknown") == "unknown"
