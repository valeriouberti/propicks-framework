"""Test dell'universo ETF settoriali e dei helper di query."""

from __future__ import annotations

from propicks.config import (
    SECTOR_ETFS_US,
    SECTOR_ETFS_WORLD,
    get_etf_benchmark,
)
from propicks.domain.etf_universe import (
    favored_sectors_for_regime,
    get_asset_type,
    get_etf_info,
    get_sector_key,
    is_favored,
    list_universe,
)


# ---------------------------------------------------------------------------
# Asset type detection
# ---------------------------------------------------------------------------
def test_stock_ticker_detected_as_stock():
    assert get_asset_type("AAPL") == "STOCK"
    assert get_asset_type("NVDA") == "STOCK"
    assert get_asset_type("ENI.MI") == "STOCK"


def test_us_sector_etf_detected():
    assert get_asset_type("XLK") == "SECTOR_ETF"
    assert get_asset_type("xlk") == "SECTOR_ETF"  # case-insensitive


def test_world_sector_etf_detected():
    # Xtrackers MSCI World sector series (.DE Xetra)
    assert get_asset_type("XDW0.DE") == "SECTOR_ETF"  # Energy
    assert get_asset_type("xdwt.de") == "SECTOR_ETF"  # Technology (case-insensitive)
    assert get_asset_type("XWTS.DE") == "SECTOR_ETF"  # Communication Services (outlier)
    assert get_asset_type("IQQ6.DE") == "SECTOR_ETF"  # Real Estate (separate series)
    # Borsa Italiana .MI listings (same UCITS funds)
    assert get_asset_type("XDWT.MI") == "SECTOR_ETF"
    assert get_asset_type("XDWF.MI") == "SECTOR_ETF"


# ---------------------------------------------------------------------------
# Sector key lookup
# ---------------------------------------------------------------------------
def test_sector_key_for_us_etf():
    assert get_sector_key("XLK") == "technology"
    assert get_sector_key("XLF") == "financials"
    assert get_sector_key("XLU") == "utilities"


def test_sector_key_for_world_etf():
    assert get_sector_key("XDW0.DE") == "energy"
    assert get_sector_key("XDWT.DE") == "technology"
    assert get_sector_key("XWTS.DE") == "communications"
    assert get_sector_key("IQQ6.DE") == "real_estate"
    # .MI listings
    assert get_sector_key("XDWT.MI") == "technology"
    assert get_sector_key("XDWH.MI") == "healthcare"


def test_sector_key_none_for_stock():
    assert get_sector_key("AAPL") is None


# ---------------------------------------------------------------------------
# Regime → favored sectors
# ---------------------------------------------------------------------------
def test_strong_bull_favors_risk_on():
    favored = favored_sectors_for_regime(5)
    assert "technology" in favored
    assert "consumer_discretionary" in favored
    assert "utilities" not in favored
    assert "consumer_staples" not in favored


def test_strong_bear_favors_defensives():
    favored = favored_sectors_for_regime(1)
    assert "consumer_staples" in favored
    assert "utilities" in favored
    assert "technology" not in favored
    assert "consumer_discretionary" not in favored


def test_neutral_has_quality_tilt():
    favored = favored_sectors_for_regime(3)
    assert "healthcare" in favored
    assert "industrials" in favored


def test_unknown_regime_returns_empty():
    assert favored_sectors_for_regime(99) == ()
    assert favored_sectors_for_regime(0) == ()


# ---------------------------------------------------------------------------
# is_favored combina ticker + regime
# ---------------------------------------------------------------------------
def test_xlk_favored_in_strong_bull():
    assert is_favored("XLK", 5) is True


def test_xlp_favored_in_bear():
    assert is_favored("XLP", 2) is True
    assert is_favored("XLP", 5) is False


def test_world_etf_respects_same_regime_lookup():
    # XDWT.MI = listing BIt dello stesso fondo XDWT.DE → stesso sector → stesso regime fit
    assert is_favored("XDWT.MI", 5) is True
    assert is_favored("XDWT.DE", 1) is False


def test_stock_never_favored():
    assert is_favored("AAPL", 5) is False
    assert is_favored("NVDA", 3) is False


# ---------------------------------------------------------------------------
# list_universe
# ---------------------------------------------------------------------------
def test_list_universe_all_contains_us_and_world():
    rows = list_universe("ALL")
    tickers = {r["ticker"] for r in rows}
    assert "XLK" in tickers  # US
    assert "XDWT.DE" in tickers  # WORLD .DE
    assert "XDWT.MI" in tickers  # WORLD .MI
    expected = len(SECTOR_ETFS_US) + len(SECTOR_ETFS_WORLD)
    assert len(rows) == expected


def test_list_universe_us_only():
    rows = list_universe("US")
    assert all(r["region"] == "US" for r in rows)
    assert len(rows) == len(SECTOR_ETFS_US)


def test_list_universe_world_only():
    rows = list_universe("WORLD")
    assert all(r["region"] == "WORLD" for r in rows)
    assert len(rows) == len(SECTOR_ETFS_WORLD)
    sectors = {r["sector_key"] for r in rows}
    assert "real_estate" in sectors
    assert "communications" in sectors
    assert "technology" in sectors


def test_list_universe_default_is_world():
    """Default region è WORLD (operational reality retail EU)."""
    rows = list_universe()
    assert all(r["region"] == "WORLD" for r in rows)


def test_list_universe_sorted_deterministically():
    rows = list_universe("ALL")
    keys = [(r["sector_key"], r["ticker"]) for r in rows]
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# get_etf_info
# ---------------------------------------------------------------------------
def test_etf_info_full_payload():
    info = get_etf_info("XLK")
    assert info is not None
    assert info["ticker"] == "XLK"
    assert info["region"] == "US"
    assert info["sector_key"] == "technology"


def test_etf_info_none_for_stock():
    assert get_etf_info("AAPL") is None


def test_etf_info_world_payload():
    info = get_etf_info("XDW0.DE")
    assert info is not None
    assert info["ticker"] == "XDW0.DE"
    assert info["region"] == "WORLD"
    assert info["sector_key"] == "energy"
    assert info["isin"] == "IE00BM67HM91"


# ---------------------------------------------------------------------------
# WORLD universe: coverage e ISIN
# ---------------------------------------------------------------------------
def test_world_universe_covers_all_11_gics_sectors():
    sectors = {meta["sector_key"] for meta in SECTOR_ETFS_WORLD.values()}
    expected = {
        "technology",
        "financials",
        "energy",
        "healthcare",
        "industrials",
        "consumer_discretionary",
        "consumer_staples",
        "utilities",
        "real_estate",
        "materials",
        "communications",
    }
    assert sectors == expected


def test_world_etfs_all_have_isin():
    for ticker, meta in SECTOR_ETFS_WORLD.items():
        assert "isin" in meta, f"{ticker} manca ISIN"
        assert meta["isin"].startswith("IE"), f"{ticker} ISIN non IE-domiciled"


# ---------------------------------------------------------------------------
# Benchmark per region
# ---------------------------------------------------------------------------
def test_us_benchmark_is_sp500():
    assert get_etf_benchmark("US") == "^GSPC"


def test_world_uses_msci_world_benchmark():
    assert get_etf_benchmark("WORLD") == "URTH"


def test_all_default_to_world_benchmark():
    """ALL bucket ora usa URTH come default (operational reality)."""
    assert get_etf_benchmark("ALL") == "URTH"


def test_benchmark_case_insensitive():
    assert get_etf_benchmark("world") == "URTH"
    assert get_etf_benchmark("us") == "^GSPC"


def test_unknown_region_falls_back_to_us():
    """Region non riconosciuta → fallback ^GSPC."""
    assert get_etf_benchmark("UNKNOWN") == "^GSPC"
