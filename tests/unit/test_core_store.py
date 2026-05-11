"""Unit test del Core Portfolio store (PIC/PAC bucket isolato).

Copre:
- add_holding (PIC iniziale)
- add_contribution (PAC successive) → ricalcolo weighted avg_cost
- SELL parziale (shares negativo) — non altera avg_cost
- remove_holding (soft delete con keep_history)
- list_contributions con filtri ticker/since/kind
- total_contributed, total_core_value_eur
- update_holding_meta
- error paths (ticker inesistente, shares negativo per buy, kind invalido, ecc.)

Tutti i test usano la fixture ``_isolate_db`` autouse → DB tmp_path fresco
ad ogni test, nessuna rete.
"""

from __future__ import annotations

import pytest

from propicks.io import core_store


# ---------------------------------------------------------------------------
# add_holding (creazione)
# ---------------------------------------------------------------------------
class TestAddHolding:
    def test_creates_holding_with_pic(self):
        h = core_store.add_holding(
            "VWCE.MI",
            shares=10,
            price=100.0,
            asset_class="EQUITY_ETF",
            region="WORLD",
            currency="EUR",
            kind="PIC",
            date="2025-01-15",
        )
        assert h["ticker"] == "VWCE.MI"
        assert h["shares"] == 10.0
        assert h["avg_cost"] == 100.0
        assert h["asset_class"] == "EQUITY_ETF"
        assert h["region"] == "WORLD"
        assert h["currency"] == "EUR"

    def test_avg_cost_includes_fees(self):
        h = core_store.add_holding(
            "VWCE.MI", shares=10, price=100.0, fees=5.0, kind="PIC"
        )
        # (10*100 + 5) / 10 = 100.5
        assert h["avg_cost"] == pytest.approx(100.5)

    def test_normalizes_ticker_to_uppercase(self):
        h = core_store.add_holding("vwce.mi", shares=1, price=100.0)
        assert h["ticker"] == "VWCE.MI"

    def test_infers_currency_from_suffix(self):
        # .MI → EUR via infer_currency
        h = core_store.add_holding("ENI.MI", shares=10, price=15.0)
        assert h["currency"] == "EUR"

    def test_rejects_zero_shares(self):
        with pytest.raises(ValueError, match="shares deve essere > 0"):
            core_store.add_holding("VWCE.MI", shares=0, price=100.0)

    def test_rejects_negative_price(self):
        with pytest.raises(ValueError, match="price deve essere > 0"):
            core_store.add_holding("VWCE.MI", shares=10, price=-100.0)

    def test_rejects_negative_fees(self):
        with pytest.raises(ValueError, match="fees deve essere >= 0"):
            core_store.add_holding("VWCE.MI", shares=10, price=100.0, fees=-1)

    def test_rejects_kind_sell(self):
        with pytest.raises(ValueError, match="kind=SELL"):
            core_store.add_holding("VWCE.MI", shares=10, price=100.0, kind="SELL")

    def test_rejects_kind_invalid(self):
        with pytest.raises(ValueError, match="non valido"):
            core_store.add_holding("VWCE.MI", shares=10, price=100.0, kind="FOO")

    def test_rejects_duplicate_active(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        with pytest.raises(ValueError, match="già presente"):
            core_store.add_holding("VWCE.MI", shares=5, price=110.0)


# ---------------------------------------------------------------------------
# add_contribution (PAC successive)
# ---------------------------------------------------------------------------
class TestAddContribution:
    def test_pac_increments_shares_and_recomputes_avg(self):
        core_store.add_holding(
            "VWCE.MI", shares=10, price=100.0, kind="PIC", date="2025-01-15"
        )
        h = core_store.add_contribution(
            "VWCE.MI", shares=10, price=120.0, kind="PAC", date="2025-02-15"
        )
        # shares totali = 20
        assert h["shares"] == 20.0
        # avg_cost weighted = (10*100 + 10*120) / 20 = 110
        assert h["avg_cost"] == pytest.approx(110.0)

    def test_multiple_pac_accumulates_correctly(self):
        core_store.add_holding(
            "VWCE.MI", shares=10, price=100.0, date="2025-01-15"
        )
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC", date="2025-02-15"
        )
        h = core_store.add_contribution(
            "VWCE.MI", shares=5, price=130.0, kind="PAC", date="2025-03-15"
        )
        # shares=20, cost=10*100 + 5*110 + 5*130 = 1000+550+650 = 2200, avg=110
        assert h["shares"] == 20.0
        assert h["avg_cost"] == pytest.approx(110.0)

    def test_dividend_reinvest_kind(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        h = core_store.add_contribution(
            "VWCE.MI", shares=0.5, price=120.0, kind="DIVIDEND_REINVEST"
        )
        assert h["shares"] == 10.5
        # avg = (1000 + 60) / 10.5 = ~100.95
        assert h["avg_cost"] == pytest.approx((1000 + 60) / 10.5, rel=1e-4)

    def test_rejects_on_unknown_ticker(self):
        with pytest.raises(ValueError, match="non esiste"):
            core_store.add_contribution(
                "NEVER.MI", shares=10, price=100.0, kind="PAC"
            )

    def test_buy_kind_requires_positive_shares(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        with pytest.raises(ValueError, match="shares > 0"):
            core_store.add_contribution(
                "VWCE.MI", shares=-5, price=100.0, kind="PAC"
            )

    def test_persists_fees(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        h = core_store.add_contribution(
            "VWCE.MI", shares=10, price=120.0, kind="PAC", fees=3.0
        )
        # avg_cost include fees: (10*100 + 10*120 + 3) / 20 = 110.15
        assert h["avg_cost"] == pytest.approx(110.15)


# ---------------------------------------------------------------------------
# SELL (parziale)
# ---------------------------------------------------------------------------
class TestSell:
    def test_partial_sell_decrements_shares(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        h = core_store.add_contribution(
            "VWCE.MI", shares=-3, price=130.0, kind="SELL"
        )
        assert h["shares"] == 7.0

    def test_sell_does_not_change_avg_cost(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        h = core_store.add_contribution(
            "VWCE.MI", shares=-3, price=130.0, kind="SELL"
        )
        # avg_cost basato solo sui BUY → resta 100
        assert h["avg_cost"] == pytest.approx(100.0)

    def test_sell_requires_negative_shares(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        with pytest.raises(ValueError, match="shares negativo"):
            core_store.add_contribution(
                "VWCE.MI", shares=3, price=130.0, kind="SELL"
            )

    def test_sell_cannot_exceed_holdings(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        with pytest.raises(ValueError, match="supera il posseduto"):
            core_store.add_contribution(
                "VWCE.MI", shares=-15, price=130.0, kind="SELL"
            )


# ---------------------------------------------------------------------------
# remove_holding
# ---------------------------------------------------------------------------
class TestRemoveHolding:
    def test_soft_delete_keeps_row_with_zero_shares(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.remove_holding("VWCE.MI", keep_history=True)
        assert core_store.load_core() == {}
        # Ma get_holding mostra ancora la riga con shares=0
        h = core_store.get_holding("VWCE.MI")
        assert h is not None
        assert h["shares"] == 0.0

    def test_soft_delete_preserves_contributions(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.remove_holding("VWCE.MI", keep_history=True)
        contribs = core_store.list_contributions(ticker="VWCE.MI")
        assert len(contribs) == 1

    def test_hard_delete_cascades_contributions(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC"
        )
        core_store.remove_holding("VWCE.MI", keep_history=False)
        assert core_store.get_holding("VWCE.MI") is None
        assert core_store.list_contributions(ticker="VWCE.MI") == []

    def test_remove_unknown_raises(self):
        with pytest.raises(ValueError, match="non esiste"):
            core_store.remove_holding("NEVER.MI")


# ---------------------------------------------------------------------------
# load_core, get_holding
# ---------------------------------------------------------------------------
class TestLoad:
    def test_load_returns_only_active(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.add_holding("AGGH.MI", shares=20, price=50.0)
        core_store.remove_holding("AGGH.MI", keep_history=True)
        loaded = core_store.load_core()
        assert "VWCE.MI" in loaded
        assert "AGGH.MI" not in loaded
        assert len(loaded) == 1

    def test_load_empty_returns_empty_dict(self):
        assert core_store.load_core() == {}

    def test_get_holding_returns_none_for_unknown(self):
        assert core_store.get_holding("NEVER.MI") is None


# ---------------------------------------------------------------------------
# list_contributions
# ---------------------------------------------------------------------------
class TestListContributions:
    def test_filter_by_ticker(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0, date="2025-01-15")
        core_store.add_holding("AGGH.MI", shares=20, price=50.0, date="2025-01-20")
        contribs = core_store.list_contributions(ticker="VWCE.MI")
        assert len(contribs) == 1
        assert contribs[0]["ticker"] == "VWCE.MI"

    def test_filter_by_since(self):
        core_store.add_holding(
            "VWCE.MI", shares=10, price=100.0, date="2025-01-15"
        )
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC", date="2025-02-15"
        )
        contribs = core_store.list_contributions(since="2025-02-01")
        assert len(contribs) == 1
        assert contribs[0]["date"] == "2025-02-15"

    def test_filter_by_kind(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0, kind="PIC")
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC"
        )
        pacs = core_store.list_contributions(kind="PAC")
        assert len(pacs) == 1
        assert pacs[0]["kind"] == "PAC"

    def test_orders_by_date_asc(self):
        core_store.add_holding(
            "VWCE.MI", shares=10, price=100.0, date="2025-03-01"
        )
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC", date="2025-01-01"
        )
        contribs = core_store.list_contributions()
        assert contribs[0]["date"] == "2025-01-01"
        assert contribs[1]["date"] == "2025-03-01"


# ---------------------------------------------------------------------------
# total_contributed / total_core_value_eur
# ---------------------------------------------------------------------------
class TestAggregates:
    def test_total_contributed_sums_buy_amounts_plus_fees(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0, fees=2.0)
        core_store.add_contribution(
            "VWCE.MI", shares=5, price=110.0, kind="PAC", fees=1.0
        )
        # (1000 + 2) + (550 + 1) = 1553
        assert core_store.total_contributed() == pytest.approx(1553.0)

    def test_total_contributed_excludes_sells(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.add_contribution(
            "VWCE.MI", shares=-3, price=130.0, kind="SELL"
        )
        # Solo il buy: 1000 (i SELL non contano)
        assert core_store.total_contributed() == pytest.approx(1000.0)

    def test_total_contributed_filtered_by_ticker(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.add_holding("AGGH.MI", shares=20, price=50.0)
        assert core_store.total_contributed(ticker="AGGH.MI") == pytest.approx(1000.0)

    def test_total_core_value_eur(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0, currency="EUR")
        core_store.add_holding("AGGH.MI", shares=20, price=50.0, currency="EUR")
        prices = {"VWCE.MI": 120.0, "AGGH.MI": 55.0}
        # 10*120 + 20*55 = 1200 + 1100 = 2300
        assert core_store.total_core_value_eur(prices) == pytest.approx(2300.0)

    def test_total_core_value_skips_missing_price(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        core_store.add_holding("AGGH.MI", shares=20, price=50.0)
        prices = {"VWCE.MI": 120.0}  # AGGH manca
        assert core_store.total_core_value_eur(prices) == pytest.approx(1200.0)


# ---------------------------------------------------------------------------
# update_holding_meta
# ---------------------------------------------------------------------------
class TestUpdateMeta:
    def test_updates_target_weight(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        h = core_store.update_holding_meta("VWCE.MI", target_weight=0.60)
        assert h["target_weight"] == 0.60

    def test_partial_update_preserves_other_fields(self):
        core_store.add_holding(
            "VWCE.MI",
            shares=10,
            price=100.0,
            name="Vanguard FTSE All-World",
            asset_class="EQUITY_ETF",
            region="WORLD",
        )
        h = core_store.update_holding_meta("VWCE.MI", target_weight=0.60)
        assert h["name"] == "Vanguard FTSE All-World"
        assert h["region"] == "WORLD"
        assert h["target_weight"] == 0.60

    def test_no_fields_raises(self):
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        with pytest.raises(ValueError, match="almeno un campo"):
            core_store.update_holding_meta("VWCE.MI")

    def test_unknown_ticker_raises(self):
        with pytest.raises(ValueError, match="non esiste"):
            core_store.update_holding_meta("NEVER.MI", target_weight=0.5)


# ---------------------------------------------------------------------------
# Integration: portfolio satellite NON impattato
# ---------------------------------------------------------------------------
class TestIsolationFromSatellite:
    def test_core_does_not_appear_in_load_portfolio(self):
        from propicks.io.portfolio_store import load_portfolio
        core_store.add_holding("VWCE.MI", shares=10, price=100.0)
        portfolio = load_portfolio()
        assert "VWCE.MI" not in portfolio["positions"]
        assert portfolio["positions"] == {}
