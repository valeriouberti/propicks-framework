"""Test del position sizing. Puro: non tocca filesystem, passa portfolio inline."""

from __future__ import annotations

from propicks.domain.sizing import calculate_position_size, portfolio_value


def _empty_portfolio(cash: float = 10_000.0) -> dict:
    return {"positions": {}, "cash": cash, "last_updated": None}


def test_portfolio_value_empty():
    assert portfolio_value(_empty_portfolio()) == 10_000.0


def test_portfolio_value_with_positions():
    pf = {
        "positions": {"AAPL": {"shares": 10, "entry_price": 200.0}},
        "cash": 8_000.0,
    }
    assert portfolio_value(pf) == 10_000.0


def test_sizing_rejects_stop_above_entry():
    r = calculate_position_size(
        entry_price=100, stop_price=105, portfolio=_empty_portfolio()
    )
    assert r["ok"] is False
    assert "Stop" in r["error"]


def test_sizing_rejects_low_score():
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=3, score_tech=40,
        portfolio=_empty_portfolio(),
    )
    assert r["ok"] is False


def test_sizing_high_conviction_uses_12_pct():
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=9, score_tech=85,
        portfolio=_empty_portfolio(10_000),
    )
    assert r["ok"] is True
    assert r["conviction"] == "ALTA"
    # target = 12% di 10k = 1200 → 12 shares @ 100
    assert r["shares"] == 12
    assert r["conviction_pct"] == 0.12


def test_sizing_medium_conviction_uses_8_pct():
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=7, score_tech=65,
        portfolio=_empty_portfolio(10_000),
    )
    assert r["ok"] is True
    assert r["conviction"] == "MEDIA"
    assert r["shares"] == 8


def test_sizing_respects_cash_reserve():
    # Cash basso → cash_available ridotto dalla riserva 20%
    pf = _empty_portfolio(cash=3_000)
    pf["positions"] = {"X": {"shares": 70, "entry_price": 100.0}}  # total=10k
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=9, score_tech=85,
        portfolio=pf,
    )
    # riserva 20% di 10k = 2000, cash available = 3000-2000 = 1000
    # target 1200 ma cash available 1000 → 10 shares
    assert r["ok"] is True
    assert r["shares"] == 10
