"""Test del layer di schema versioning."""

from __future__ import annotations

import json

import pytest

from propicks.io.migrations import (
    CURRENT_VERSIONS,
    SchemaMigrationError,
    migrate,
    stamp_version,
)


def test_stamp_version_writes_current():
    payload: dict = {"positions": {}}
    stamp_version(payload, "portfolio")
    assert payload["schema_version"] == CURRENT_VERSIONS["portfolio"]


def test_migrate_unknown_store_raises():
    with pytest.raises(SchemaMigrationError):
        migrate({}, "nonexistent")


def test_migrate_legacy_without_version_is_treated_as_v1():
    # File senza schema_version (legacy) va trattato come v1 baseline e
    # promosso alla corrente (attualmente v1 = no-op).
    legacy = {"positions": {}, "cash": 10000}
    result = migrate(legacy, "portfolio")
    assert result["schema_version"] == CURRENT_VERSIONS["portfolio"]
    assert result["positions"] == {}
    assert result["cash"] == 10000


def test_migrate_idempotent_on_current_version():
    payload = {"positions": {}, "schema_version": CURRENT_VERSIONS["portfolio"]}
    result = migrate(payload, "portfolio")
    assert result == payload


def test_migrate_rejects_future_version():
    # Binario vecchio che incontra file scritto da binario nuovo deve
    # fallire rumorosamente — altrimenti perde campi in write successivo.
    payload = {"positions": {}, "schema_version": 999}
    with pytest.raises(SchemaMigrationError):
        migrate(payload, "portfolio")


def test_migrate_rejects_invalid_version_type():
    payload = {"positions": {}, "schema_version": "v1"}
    with pytest.raises(SchemaMigrationError):
        migrate(payload, "portfolio")


def test_migrate_rejects_negative_version():
    payload = {"positions": {}, "schema_version": 0}
    with pytest.raises(SchemaMigrationError):
        migrate(payload, "portfolio")


def test_roundtrip_portfolio_store(tmp_path, monkeypatch):
    """Load di file legacy senza schema_version → save riscrive con version."""
    pf_file = tmp_path / "portfolio.json"
    legacy = {
        "positions": {"AAPL": {"entry_price": 150, "shares": 10, "stop_loss": 140}},
        "cash": 5000,
        "last_updated": None,
    }
    pf_file.write_text(json.dumps(legacy))

    monkeypatch.setattr("propicks.io.portfolio_store.PORTFOLIO_FILE", str(pf_file))
    from propicks.io.portfolio_store import load_portfolio

    pf = load_portfolio()
    # load_portfolio chiama save_portfolio solo se il file non esisteva;
    # qui esisteva, quindi verifico che il payload caricato abbia version.
    assert pf["schema_version"] == CURRENT_VERSIONS["portfolio"]

    # Salvando di nuovo, il file on-disk deve avere schema_version.
    from propicks.io.portfolio_store import save_portfolio
    save_portfolio(pf)
    on_disk = json.loads(pf_file.read_text())
    assert on_disk["schema_version"] == CURRENT_VERSIONS["portfolio"]
    assert "AAPL" in on_disk["positions"]


def test_roundtrip_journal_store_legacy_array(tmp_path, monkeypatch):
    """Journal legacy come array puro → promosso a wrapper con schema_version."""
    jf = tmp_path / "journal.json"
    legacy_trades = [
        {"id": 1, "ticker": "AAPL", "status": "open", "entry_price": 150},
    ]
    jf.write_text(json.dumps(legacy_trades))

    monkeypatch.setattr("propicks.io.journal_store.JOURNAL_FILE", str(jf))
    from propicks.io.journal_store import _save_journal, load_journal

    trades = load_journal()
    assert isinstance(trades, list)
    assert trades[0]["ticker"] == "AAPL"

    _save_journal(trades)
    on_disk = json.loads(jf.read_text())
    assert isinstance(on_disk, dict)
    assert on_disk["schema_version"] == CURRENT_VERSIONS["journal"]
    assert on_disk["trades"][0]["ticker"] == "AAPL"


def test_roundtrip_watchlist_store(tmp_path, monkeypatch):
    wl_file = tmp_path / "watchlist.json"
    legacy = {"tickers": {"AAPL": {"added_date": "2026-04-01", "source": "manual"}}}
    wl_file.write_text(json.dumps(legacy))

    monkeypatch.setattr("propicks.io.watchlist_store.WATCHLIST_FILE", str(wl_file))
    from propicks.io.watchlist_store import load_watchlist, save_watchlist

    wl = load_watchlist()
    assert wl["schema_version"] == CURRENT_VERSIONS["watchlist"]

    save_watchlist(wl)
    on_disk = json.loads(wl_file.read_text())
    assert on_disk["schema_version"] == CURRENT_VERSIONS["watchlist"]
    assert "AAPL" in on_disk["tickers"]
