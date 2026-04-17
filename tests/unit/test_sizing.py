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


def test_sizing_stock_uses_15_pct_cap():
    # Conviction HIGH normalmente userebbe 12% target, ma se lo alziamo
    # al massimo dobbiamo vedere il cap stock al 15%.
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=10, score_tech=100,
        portfolio=_empty_portfolio(10_000),
        asset_type="STOCK",
    )
    assert r["ok"] is True
    assert r["position_cap_pct"] == 0.15


def test_sizing_etf_uses_20_pct_cap():
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=10, score_tech=100,
        portfolio=_empty_portfolio(10_000),
        asset_type="SECTOR_ETF",
    )
    assert r["ok"] is True
    assert r["asset_type"] == "SECTOR_ETF"
    assert r["position_cap_pct"] == 0.20
    # target 12% = 1200, cap 20% = 2000 → target vince → 12 shares
    assert r["shares"] == 12


def test_sizing_etf_cap_larger_than_stock_under_pressure():
    # Portfolio pieno di cash, conviction massima: il cap ETF deve permettere
    # 20% = 2000, mentre lo stock sarebbe bloccato a 15% = 1500.
    # Usiamo un prezzo che rende la differenza osservabile.
    pf = _empty_portfolio(10_000)
    r_stock = calculate_position_size(
        entry_price=50, stop_price=47,
        score_claude=10, score_tech=100,
        portfolio=pf, asset_type="STOCK",
    )
    r_etf = calculate_position_size(
        entry_price=50, stop_price=47,
        score_claude=10, score_tech=100,
        portfolio=pf, asset_type="SECTOR_ETF",
    )
    # Entrambi limitati dal target 12% = 1200 → 24 shares uguali.
    # Il test serve a confermare che il cap non abbassa il target sotto 12%.
    assert r_stock["shares"] == 24
    assert r_etf["shares"] == 24
    assert r_etf["max_value"] > r_stock["max_value"]
