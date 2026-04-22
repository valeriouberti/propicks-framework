"""Test coordinator trade_sync (journal + portfolio).

Isola JOURNAL_FILE e PORTFOLIO_FILE su tmp_path. Zero rete.
"""

from __future__ import annotations

import json

import pytest

from propicks.config import CAPITAL


@pytest.fixture
def stores_tmp(tmp_path, monkeypatch):
    """Redirige entrambi gli store su tmp_path."""
    journal = tmp_path / "journal.json"
    portfolio = tmp_path / "portfolio.json"
    monkeypatch.setattr("propicks.config.JOURNAL_FILE", str(journal))
    monkeypatch.setattr("propicks.config.PORTFOLIO_FILE", str(portfolio))
    monkeypatch.setattr("propicks.io.journal_store.JOURNAL_FILE", str(journal))
    monkeypatch.setattr("propicks.io.portfolio_store.PORTFOLIO_FILE", str(portfolio))
    return journal, portfolio


def test_open_trade_writes_both_stores(stores_tmp):
    _journal_file, portfolio_file = stores_tmp
    from propicks.io.trade_sync import open_trade

    trade, pos, warnings = open_trade(
        ticker="AAPL",
        direction="long",
        entry_price=150.0,
        entry_date="2026-04-20",
        shares=10,
        stop_loss=140.0,
        target=170.0,
        score_claude=7,
        score_tech=70,
        strategy="TechTitans",
        catalyst="test",
    )

    # Journal written
    assert trade["ticker"] == "AAPL"
    assert trade["shares"] == 10
    assert trade["status"] == "open"

    # Portfolio written
    assert pos is not None
    assert pos["shares"] == 10
    assert pos["entry_price"] == 150.0
    assert warnings == []

    # Cash debited correttamente
    with open(portfolio_file) as f:
        portfolio = json.load(f)
    assert portfolio["cash"] == round(CAPITAL - 1500.0, 2)
    assert "AAPL" in portfolio["positions"]


def test_close_trade_uses_exit_proceeds_not_entry_cost(stores_tmp):
    """Il bug storico: remove_position rimborsava entry_price*shares, perdendo il P&L.
    close_trade deve rimborsare exit_price*shares (proventi veri).
    """
    _, portfolio_file = stores_tmp
    from propicks.io.trade_sync import close_trade, open_trade

    open_trade(
        ticker="AAPL",
        direction="long",
        entry_price=150.0,
        entry_date="2026-04-20",
        shares=10,
        stop_loss=140.0,
        target=170.0,
        score_claude=7,
        score_tech=70,
        strategy="TechTitans",
        catalyst="test",
    )
    with open(portfolio_file) as f:
        cash_after_open = json.load(f)["cash"]

    trade, removed, warnings = close_trade(
        ticker="AAPL",
        exit_price=170.0,
        exit_date="2026-05-01",
        reason="Target",
    )

    assert trade["status"] == "closed"
    assert trade["pnl_pct"] == pytest.approx(13.3333, abs=1e-3)
    assert removed is not None
    assert warnings == []

    with open(portfolio_file) as f:
        portfolio = json.load(f)
    # Cash = cash_after_open + 10*170 (proventi), NON + 10*150 (cost)
    expected_cash = round(cash_after_open + 10 * 170.0, 2)
    assert portfolio["cash"] == expected_cash
    assert "AAPL" not in portfolio["positions"]


def test_open_trade_position_already_exists_warns(stores_tmp):
    """Se portfolio ha già la posizione, journal viene scritto ma portfolio non duplicato."""
    from propicks.io.portfolio_store import add_position, load_portfolio
    from propicks.io.trade_sync import open_trade

    pf = load_portfolio()
    add_position(
        pf, ticker="AAPL", entry_price=150.0, shares=10, stop_loss=140.0,
        target=170.0, strategy=None, score_claude=7, score_tech=70, catalyst=None,
    )

    trade, pos, warnings = open_trade(
        ticker="AAPL",
        direction="long",
        entry_price=150.0,
        entry_date="2026-04-20",
        shares=10,
        stop_loss=140.0,
        target=170.0,
        score_claude=7,
        score_tech=70,
        strategy=None,
        catalyst=None,
    )

    assert trade["status"] == "open"  # journal scritto comunque
    assert pos is None  # portfolio non toccato
    assert len(warnings) == 1
    assert "già in portfolio" in warnings[0]


def test_close_trade_position_not_in_portfolio_warns(stores_tmp):
    """Se chiudi un trade ma la posizione non è nel portfolio, journal viene
    comunque aggiornato con warning informativo."""
    from propicks.io.journal_store import add_trade
    from propicks.io.trade_sync import close_trade

    # Apri trade SOLO nel journal (bypassa sync)
    add_trade(
        ticker="AAPL", direction="long", entry_price=150.0,
        entry_date="2026-04-20", stop_loss=140.0, target=170.0,
        score_claude=7, score_tech=70, strategy=None, catalyst=None,
        shares=10,
    )

    trade, removed, warnings = close_trade(
        ticker="AAPL", exit_price=170.0, exit_date="2026-05-01", reason="Test",
    )

    assert trade["status"] == "closed"
    assert trade["pnl_pct"] == pytest.approx(13.3333, abs=1e-3)
    assert removed is None
    assert len(warnings) == 1
    assert "non in portfolio" in warnings[0]


def test_open_trade_portfolio_violation_keeps_journal(stores_tmp):
    """Se add_position viola risk check, il journal resta scritto."""
    from propicks.io.journal_store import load_journal
    from propicks.io.trade_sync import open_trade

    # Size > 15% del capitale → violazione MAX_POSITION_SIZE_PCT
    huge_shares = int(CAPITAL * 0.5 / 150.0)

    _trade, pos, warnings = open_trade(
        ticker="AAPL",
        direction="long",
        entry_price=150.0,
        entry_date="2026-04-20",
        shares=huge_shares,
        stop_loss=140.0,
        target=170.0,
        score_claude=7,
        score_tech=70,
        strategy=None,
        catalyst=None,
    )

    # Journal c'è
    journal = load_journal()
    assert len(journal) == 1
    assert journal[0]["ticker"] == "AAPL"
    # Portfolio no
    assert pos is None
    assert len(warnings) == 1
    assert "Portfolio non aggiornato" in warnings[0]
