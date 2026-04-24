"""Test watchlist_store su tmp_path. Zero rete, zero data/ reale."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from propicks.config import DATE_FMT


@pytest.fixture
def watchlist_tmp():
    """No-op: l'isolation DB è già autouse via conftest._isolate_db."""
    return None


def test_load_creates_default_if_missing(watchlist_tmp):
    from propicks.io.watchlist_store import load_watchlist

    wl = load_watchlist()
    assert wl["tickers"] == {}
    # last_updated può essere None su DB vuoto — diversamente dal legacy JSON
    # dove il primo load scatenava un save stamp. Su DB il meta field esiste
    # solo dopo la prima mutazione.


def test_add_new_ticker(watchlist_tmp):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    wl = load_watchlist()
    entry, is_new = add_to_watchlist(
        wl, "AAPL",
        target_entry=185.50,
        note="pullback EMA20",
        score_at_add=72.3,
        regime_at_add="BULL",
        classification_at_add="B — WATCHLIST",
        source="auto_scan",
    )
    assert is_new is True
    assert entry["target_entry"] == 185.50
    assert entry["note"] == "pullback EMA20"
    assert entry["score_at_add"] == 72.3
    assert entry["source"] == "auto_scan"
    assert entry["added_date"]  # auto-stamped


def test_add_duplicate_preserves_added_date_and_source(watchlist_tmp):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    wl = load_watchlist()
    add_to_watchlist(
        wl, "AAPL",
        target_entry=185.50,
        added_date="2025-01-01",
        source="manual",
    )
    entry, is_new = add_to_watchlist(
        wl, "AAPL",
        target_entry=190.00,  # updated
        source="auto_scan",   # must NOT overwrite — entry not new
    )
    assert is_new is False
    assert entry["added_date"] == "2025-01-01"  # preserved
    assert entry["target_entry"] == 190.00  # updated
    assert entry["source"] == "manual"  # preserved


def test_add_duplicate_none_preserves_existing(watchlist_tmp):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=185.50, note="original")
    entry, is_new = add_to_watchlist(wl, "AAPL", target_entry=None, note=None)
    assert is_new is False
    assert entry["target_entry"] == 185.50
    assert entry["note"] == "original"


def test_ticker_uppercased(watchlist_tmp):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    wl = load_watchlist()
    add_to_watchlist(wl, "aapl", target_entry=185.50)
    assert "AAPL" in wl["tickers"]
    assert "aapl" not in wl["tickers"]


def test_remove_existing(watchlist_tmp):
    from propicks.io.watchlist_store import (
        add_to_watchlist,
        load_watchlist,
        remove_from_watchlist,
    )

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=185.50)
    removed = remove_from_watchlist(wl, "aapl")
    assert removed["target_entry"] == 185.50
    assert "AAPL" not in wl["tickers"]


def test_remove_missing_raises(watchlist_tmp):
    from propicks.io.watchlist_store import load_watchlist, remove_from_watchlist

    wl = load_watchlist()
    with pytest.raises(ValueError, match="non è in watchlist"):
        remove_from_watchlist(wl, "AAPL")


def test_update_entry(watchlist_tmp):
    from propicks.io.watchlist_store import (
        add_to_watchlist,
        load_watchlist,
        update_watchlist_entry,
    )

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=185.50, note="old")
    updated = update_watchlist_entry(wl, "AAPL", target_entry=200.00, note="new")
    assert updated["target_entry"] == 200.00
    assert updated["note"] == "new"


def test_update_no_fields_raises(watchlist_tmp):
    from propicks.io.watchlist_store import (
        add_to_watchlist,
        load_watchlist,
        update_watchlist_entry,
    )

    wl = load_watchlist()
    add_to_watchlist(wl, "AAPL", target_entry=185.50)
    with pytest.raises(ValueError, match="almeno un campo"):
        update_watchlist_entry(wl, "AAPL")


# Test di migrazione JSON legacy rimossi: post Phase 1 la migrazione è
# one-shot via ``propicks-migrate`` (script one-time). Il parsing dei
# formati legacy è coperto dai test della migration script stessa.


def test_is_stale(watchlist_tmp):
    from propicks.io.watchlist_store import is_stale

    fresh = {"added_date": datetime.now().strftime(DATE_FMT)}
    old = {"added_date": (datetime.now() - timedelta(days=90)).strftime(DATE_FMT)}
    borderline = {"added_date": (datetime.now() - timedelta(days=59)).strftime(DATE_FMT)}
    assert is_stale(fresh) is False
    assert is_stale(old) is True
    assert is_stale(borderline) is False
    assert is_stale({"added_date": None}) is False


# test_corrupted_json_raises rimosso: post Phase 1 lo storage è SQLite —
# "corruzione file JSON" non è più uno scenario. SQLite ha le sue modalità
# di corruzione (pragma integrity_check) gestite dalla libreria sqlite3.
