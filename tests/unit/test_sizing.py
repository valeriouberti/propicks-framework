"""Test del position sizing. Puro: non tocca filesystem, passa portfolio inline."""

from __future__ import annotations

from propicks.domain.sizing import (
    calculate_position_size,
    contrarian_aggregate_exposure,
    contrarian_position_count,
    is_contrarian_position,
    portfolio_market_value,
    portfolio_value,
)


def test_is_contrarian_position_matches_prefix():
    """Case-insensitive prefix match su 'contra'."""
    assert is_contrarian_position({"strategy": "Contrarian"}) is True
    assert is_contrarian_position({"strategy": "contrarian"}) is True
    assert is_contrarian_position({"strategy": "CONTRA — macro_flush"}) is True
    assert is_contrarian_position({"strategy": "contrarian-pullback"}) is True
    # Non-match
    assert is_contrarian_position({"strategy": "TechTitans"}) is False
    assert is_contrarian_position({"strategy": None}) is False
    assert is_contrarian_position({}) is False


def test_contrarian_position_count_mixed_portfolio():
    pf = {
        "positions": {
            "A": {"strategy": "Contrarian"},
            "B": {"strategy": "TechTitans"},
            "C": {"strategy": "contra — flush"},
            "D": {"strategy": "DominaDow"},
        }
    }
    assert contrarian_position_count(pf) == 2


def test_contrarian_aggregate_exposure_mixed_portfolio():
    pf = {
        "cash": 5_000.0,
        "positions": {
            "A": {"shares": 10, "entry_price": 100, "strategy": "Contrarian"},  # 1000
            "B": {"shares": 10, "entry_price": 100, "strategy": "TechTitans"},  # 1000 (non contra)
        },
    }
    # total = 5000 + 1000 + 1000 = 7000; contra = 1000 → ~14.28%
    expo = contrarian_aggregate_exposure(pf)
    assert round(expo, 4) == round(1000 / 7000, 4)



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


def test_portfolio_market_value_uses_current_prices():
    pf = {
        "positions": {"AAPL": {"shares": 10, "entry_price": 100.0}},
        "cash": 0.0,
    }
    # Winner: cost-basis = 1000, mark-to-market = 1200
    assert portfolio_value(pf) == 1_000.0
    assert portfolio_market_value(pf, {"AAPL": 120.0}) == 1_200.0


def test_portfolio_market_value_skips_missing_prices():
    # Ticker senza prezzo corrente → escluso dal totale (match con exposure).
    pf = {
        "positions": {
            "AAPL": {"shares": 10, "entry_price": 100.0},
            "XYZ": {"shares": 5, "entry_price": 50.0},  # no price
        },
        "cash": 500.0,
    }
    total = portfolio_market_value(pf, {"AAPL": 110.0})
    assert total == 500.0 + 1100.0  # solo AAPL + cash


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


def test_sizing_rejects_low_score_claude_high_tech():
    # Media = (30+90)/2 = 60 passerebbe un gate su media, ma score_claude=3
    # viola MIN_SCORE_CLAUDE=6 (add_position fallirebbe). Sizing deve bloccare
    # qui per coerenza, così il trader non vede "ok" per poi fallire su add.
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=3, score_tech=90,
        portfolio=_empty_portfolio(),
    )
    assert r["ok"] is False
    assert "score_claude" in r["error"]


def test_sizing_rejects_high_claude_low_tech():
    # Analogo simmetrico: score_tech=50 < MIN_SCORE_TECH=60 → blocco.
    r = calculate_position_size(
        entry_price=100, stop_price=95, score_claude=8, score_tech=50,
        portfolio=_empty_portfolio(),
    )
    assert r["ok"] is False
    assert "score_tech" in r["error"]


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


def test_sizing_contrarian_cap_is_8pct():
    r = calculate_position_size(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=_empty_portfolio(10_000),
        asset_type="STOCK",
        strategy_bucket="contrarian",
    )
    assert r["ok"] is True
    assert r["strategy_bucket"] == "contrarian"
    # cap 8% = 800, a 100€ → 8 shares
    assert r["shares"] == 8
    assert r["position_cap_pct"] == 0.08


def test_sizing_contrarian_blocks_at_max_positions():
    """Un portfolio con 3 contrarian già aperte blocca il 4°."""
    pf = {
        "cash": 5_000.0,
        "positions": {
            "A": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
            "B": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
            "C": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
        },
    }
    r = calculate_position_size(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=pf, strategy_bucket="contrarian",
    )
    assert r["ok"] is False
    assert "Bucket contrarian pieno" in r["error"]


def test_sizing_contrarian_aggregate_cap():
    """Bucket aggregato al 20% blocca entry aggiuntivi."""
    pf = {
        "cash": 5_000.0,
        "positions": {
            # 2 contrarian già al 10% ciascuna → 20% totali = al cap
            "A": {"shares": 10, "entry_price": 100, "strategy": "Contrarian"},
            "B": {"shares": 10, "entry_price": 100, "strategy": "Contrarian"},
        },
    }
    # portfolio_value = 5000 cash + 1000 + 1000 = 7000
    # contra exposure = 2000 / 7000 ≈ 28.6% → già sopra il cap 20%
    r = calculate_position_size(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=pf, strategy_bucket="contrarian",
    )
    assert r["ok"] is False
    assert "cap aggregato" in r["error"]


def test_sizing_contrarian_explicit_headroom_error_message():
    """Bug fix #3: quando il binder è l'headroom contrarian, l'errore lo dice
    esplicitamente invece di 'cash insufficient'.
    """
    # Portfolio a ~10k con expo contrarian JUST BELOW 20% → headroom ~0.03% = 3€.
    # Entry price 100 > headroom → shares=0, ma l'errore deve dire "bucket quasi al cap".
    pf2 = {
        "cash": 8_010.0,
        "positions": {
            "X": {"shares": 19, "entry_price": 100, "strategy": "Contrarian"},
            "Y": {"shares": 1, "entry_price": 99, "strategy": "Contrarian"},
        },
    }
    # portfolio_value = 8010 + 1900 + 99 = 10009; contra_value = 1999; expo ~ 19.97% < 20%
    # Headroom ~ 0.03% = 3€. Entry price 100 > headroom → shares=0.
    r = calculate_position_size(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=pf2, strategy_bucket="contrarian",
    )
    assert r["ok"] is False
    # Deve dire "bucket quasi al cap", NON "cash insufficient"
    assert "Bucket contrarian" in r["error"]
    assert "headroom" in r["error"].lower()


def test_sizing_momentum_ignores_contrarian_bucket_rules():
    """Un trade momentum su un portfolio che ha contrarian resta libero."""
    pf = {
        "cash": 5_000.0,
        "positions": {
            "A": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
            "B": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
            "C": {"shares": 10, "entry_price": 50, "strategy": "Contrarian"},
        },
    }
    r = calculate_position_size(
        entry_price=100, stop_price=92,
        score_claude=10, score_tech=100,
        portfolio=pf, strategy_bucket="momentum",
    )
    # momentum non vede il cap contrarian: deve passare
    assert r["ok"] is True
    assert r["strategy_bucket"] == "momentum"


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
