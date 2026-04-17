"""Test dell'universo ETF settoriali e dei helper di query."""

from __future__ import annotations

import pytest

from propicks.config import SECTOR_ETFS_EU, SECTOR_ETFS_US
from propicks.domain.etf_universe import (
    favored_sectors_for_regime,
    get_asset_type,
    get_etf_info,
    get_eu_equivalent,
    get_sector_key,
    get_us_equivalent,
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


def test_eu_sector_etf_detected():
    assert get_asset_type("ZPDT.DE") == "SECTOR_ETF"
    assert get_asset_type("zpdt.de") == "SECTOR_ETF"


# ---------------------------------------------------------------------------
# Sector key lookup
# ---------------------------------------------------------------------------
def test_sector_key_for_us_etf():
    assert get_sector_key("XLK") == "technology"
    assert get_sector_key("XLF") == "financials"
    assert get_sector_key("XLU") == "utilities"


def test_sector_key_for_eu_etf():
    assert get_sector_key("ZPDT.DE") == "technology"
    assert get_sector_key("ZPDU.DE") == "utilities"


def test_sector_key_none_for_stock():
    assert get_sector_key("AAPL") is None


# ---------------------------------------------------------------------------
# US ↔ EU mapping simmetrico
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("us_ticker,eu_ticker", [
    ("XLK", "ZPDT.DE"),
    ("XLF", "ZPDF.DE"),
    ("XLE", "ZPDE.DE"),
    ("XLV", "ZPDH.DE"),
    ("XLI", "ZPDI.DE"),
    ("XLY", "ZPDD.DE"),
    ("XLP", "ZPDS.DE"),
    ("XLU", "ZPDU.DE"),
    ("XLB", "ZPDM.DE"),
    ("XLC", "ZPDX.DE"),
])
def test_us_eu_mapping_roundtrip(us_ticker, eu_ticker):
    assert get_eu_equivalent(us_ticker) == eu_ticker
    assert get_us_equivalent(eu_ticker) == us_ticker


def test_xlre_has_no_eu_equivalent():
    # Real Estate non ha sector UCITS STOXX 600 puro — mappatura volutamente None
    assert get_eu_equivalent("XLRE") is None


def test_equivalent_none_for_unknown_ticker():
    assert get_eu_equivalent("AAPL") is None
    assert get_us_equivalent("FOO.XX") is None


def test_eu_sector_match_us_counterpart():
    # Ogni ETF EU deve avere lo stesso sector_key del suo equivalente US
    for eu_ticker, eu_meta in SECTOR_ETFS_EU.items():
        us_ticker = eu_meta["us_equivalent"]
        us_meta = SECTOR_ETFS_US[us_ticker]
        assert eu_meta["sector_key"] == us_meta["sector_key"], (
            f"Sector mismatch: {eu_ticker} ({eu_meta['sector_key']}) "
            f"vs {us_ticker} ({us_meta['sector_key']})"
        )


# ---------------------------------------------------------------------------
# Regime → favored sectors
# ---------------------------------------------------------------------------
def test_strong_bull_favors_risk_on():
    favored = favored_sectors_for_regime(5)
    assert "technology" in favored
    assert "consumer_discretionary" in favored
    assert "utilities" not in favored  # difensivi fuori
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
    assert is_favored("XLP", 5) is False  # staples fuori in bull


def test_eu_etf_respects_same_regime_lookup():
    # ZPDT.DE = UCITS wrapper di XLK → stessa esposizione US, stesso regime fit
    assert is_favored("ZPDT.DE", 5) is True
    assert is_favored("ZPDT.DE", 1) is False


def test_stock_never_favored():
    # is_favored è una funzione ETF-specific: gli stock ritornano sempre False
    assert is_favored("AAPL", 5) is False
    assert is_favored("NVDA", 3) is False


# ---------------------------------------------------------------------------
# list_universe
# ---------------------------------------------------------------------------
def test_list_universe_all_contains_us_and_eu():
    rows = list_universe("ALL")
    tickers = {r["ticker"] for r in rows}
    assert "XLK" in tickers
    assert "ZPDT.DE" in tickers
    assert len(rows) == len(SECTOR_ETFS_US) + len(SECTOR_ETFS_EU)


def test_list_universe_us_only():
    rows = list_universe("US")
    assert all(r["region"] == "US" for r in rows)
    assert len(rows) == len(SECTOR_ETFS_US)


def test_list_universe_eu_only():
    rows = list_universe("EU")
    assert all(r["region"] == "EU" for r in rows)
    assert len(rows) == len(SECTOR_ETFS_EU)


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
    assert info["eu_equivalent"] == "ZPDT.DE"


def test_etf_info_none_for_stock():
    assert get_etf_info("AAPL") is None
