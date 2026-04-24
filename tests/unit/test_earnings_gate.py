"""Test earnings hard gate integration in add_position (Phase 8).

Mock yfinance.get_next_earnings_date per testare senza rete.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _in_n_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def test_earnings_gate_blocks_when_within_threshold(monkeypatch):
    """Earnings entro 5gg → add_position raises ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    # Mock get_next_earnings_date: earnings dopodomani (2gg)
    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_next_earnings_date",
        lambda t, force_refresh=False: _in_n_days(2),
    )

    pf = load_portfolio()
    with pytest.raises(ValueError, match="Earnings gate"):
        add_position(
            pf,
            ticker="AAPL",
            entry_price=100.0,
            shares=10,
            stop_loss=92.0,
            target=110.0,
            strategy="TechTitans",
            score_claude=7,
            score_tech=70,
            catalyst=None,
        )


def test_earnings_gate_allows_beyond_threshold(monkeypatch):
    """Earnings tra 20gg → gate passa → add_position proceeds."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_next_earnings_date",
        lambda t, force_refresh=False: _in_n_days(20),
    )

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="AAPL",
        entry_price=100.0,
        shares=10,
        stop_loss=92.0,
        target=110.0,
        strategy="TechTitans",
        score_claude=7,
        score_tech=70,
        catalyst=None,
    )
    assert pos["shares"] == 10


def test_earnings_gate_bypassed_by_ignore_flag(monkeypatch):
    """ignore_earnings=True bypassa il gate anche se earnings imminenti."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_next_earnings_date",
        lambda t, force_refresh=False: _tomorrow(),
    )

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="AAPL",
        entry_price=100.0,
        shares=10,
        stop_loss=92.0,
        target=110.0,
        strategy="TechTitans",
        score_claude=7,
        score_tech=70,
        catalyst="post-earnings flush play",
        ignore_earnings=True,
    )
    assert pos["shares"] == 10


def test_earnings_gate_fail_open_on_yfinance_error(monkeypatch):
    """Se yfinance fallisce, l'earnings check fallisce silenziosamente
    e l'add_position procede (fail-open per non bloccare operatività)."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    def _raise(t, force_refresh=False):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_next_earnings_date",
        _raise,
    )

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="AAPL",
        entry_price=100.0,
        shares=10,
        stop_loss=92.0,
        target=110.0,
        strategy="TechTitans",
        score_claude=7,
        score_tech=70,
        catalyst=None,
    )
    # Non solleva, passa (earnings_date = None → not blocked)
    assert pos["shares"] == 10


def test_earnings_gate_no_earnings_date_passes(monkeypatch):
    """Ticker senza earnings publiche (es. ETF) → gate passa."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_next_earnings_date",
        lambda t, force_refresh=False: None,
    )

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="SPY",  # ETF
        entry_price=500.0,
        shares=2,
        stop_loss=460.0,
        target=550.0,
        strategy="ETF_Rotation",
        score_claude=7,
        score_tech=70,
        catalyst=None,
    )
    assert pos["shares"] == 2
