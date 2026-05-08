"""Test universo Thematic ETF."""

from __future__ import annotations

import pytest

from propicks.config import THEMATIC_ETFS
from propicks.domain.thematic_universe import (
    get_parent_sector_key,
    get_parent_ticker,
    get_theme_label,
    get_thematic_info,
    is_thematic,
    list_themes,
    list_universe,
    list_universe_by_theme,
    parent_exists_in_universe,
)


def test_known_thematics_detected():
    assert is_thematic("SMH")
    assert is_thematic("LOCK.MI")
    assert is_thematic("smh")  # case-insensitive
    assert not is_thematic("AAPL")
    assert not is_thematic("XLK")  # parent sector ETF, NON thematic


def test_thematic_info_payload():
    info = get_thematic_info("SMH")
    assert info is not None
    assert info["ticker"] == "SMH"
    assert info["parent_ticker"] == "XLK"
    assert info["parent_sector_key"] == "technology"
    assert info["theme_label"] == "semiconductors"


def test_lockmi_parent_world():
    info = get_thematic_info("LOCK.MI")
    assert info is not None
    assert info["parent_ticker"] == "XDWT.MI"
    assert info["region"] == "WORLD"
    assert info["theme_label"] == "cybersecurity"


def test_parent_helpers_consistency():
    for ticker in THEMATIC_ETFS:
        assert get_parent_ticker(ticker) is not None
        assert get_parent_sector_key(ticker) is not None
        assert get_theme_label(ticker) is not None


def test_thematic_info_none_for_unknown():
    assert get_thematic_info("AAPL") is None
    assert get_parent_ticker("XLK") is None  # XLK è parent, non thematic


@pytest.mark.parametrize("ticker", list(THEMATIC_ETFS.keys()))
def test_every_thematic_has_valid_parent(ticker):
    """Ogni thematic deve avere parent registrato in SECTOR_ETFS_*."""
    assert parent_exists_in_universe(ticker), (
        f"Thematic {ticker} ha parent {get_parent_ticker(ticker)} non registrato"
    )


def test_list_universe_all():
    rows = list_universe("ALL")
    assert len(rows) == len(THEMATIC_ETFS)
    tickers = {r["ticker"] for r in rows}
    assert "SMH" in tickers
    assert "LOCK.MI" in tickers


def test_list_universe_filter_world():
    rows = list_universe("WORLD")
    tickers = {r["ticker"] for r in rows}
    assert "LOCK.MI" in tickers
    assert "SMH" not in tickers  # SMH è US listing


def test_list_universe_filter_us():
    rows = list_universe("US")
    tickers = {r["ticker"] for r in rows}
    assert "SMH" in tickers
    assert "LOCK.MI" not in tickers


def test_list_themes_distinct_sorted():
    themes = list_themes()
    assert "biotech" in themes
    assert "cybersecurity" in themes
    assert "semiconductors" in themes
    assert themes == sorted(themes)


def test_list_universe_by_theme_biotech():
    rows = list_universe_by_theme("biotech")
    tickers = {r["ticker"] for r in rows}
    # Include SBIO.MI (Borsa Italiana listing, parent XDWH.MI)
    assert tickers == {"XBI", "IBB", "SBIO.MI"}


def test_list_universe_by_theme_unknown_empty():
    assert list_universe_by_theme("nonexistent_theme") == []


def test_universe_sorted_deterministically():
    rows = list_universe("ALL")
    keys = [(r.get("theme_label", ""), r["ticker"]) for r in rows]
    assert keys == sorted(keys)
