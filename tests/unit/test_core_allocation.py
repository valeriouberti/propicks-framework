"""Unit test domain.core_allocation — funzioni pure su dict.

Tutti i test costruiscono holdings dict in-memory (NO DB, NO rete).
"""

from __future__ import annotations

import pytest

from propicks.domain import core_allocation as ca


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _h(
    ticker: str,
    *,
    shares: float = 10,
    avg_cost: float = 100,
    currency: str = "EUR",
    asset_class: str | None = "EQUITY_ETF",
    region: str | None = "WORLD",
    sector_key: str | None = None,
    target_weight: float | None = None,
) -> dict:
    """Costruisce un holding dict come ritornato da core_store."""
    return {
        "ticker": ticker,
        "shares": shares,
        "avg_cost": avg_cost,
        "currency": currency,
        "asset_class": asset_class,
        "region": region,
        "sector_key": sector_key,
        "target_weight": target_weight,
        "name": None, "notes": None,
    }


# ---------------------------------------------------------------------------
# compute_holding_values
# ---------------------------------------------------------------------------
class TestHoldingValues:
    def test_computes_value_pnl_and_pct(self):
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=10, avg_cost=100)}
        prices = {"VWCE.MI": 120}
        v = ca.compute_holding_values(holdings, prices)["VWCE.MI"]
        assert v["current_value"] == 1200.0
        assert v["cost_basis"] == 1000.0
        assert v["pnl"] == 200.0
        assert v["pnl_pct"] == 0.20

    def test_skips_zero_shares(self):
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=0)}
        prices = {"VWCE.MI": 120}
        assert ca.compute_holding_values(holdings, prices) == {}

    def test_skips_missing_price(self):
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=10)}
        assert ca.compute_holding_values(holdings, {}) == {}

    def test_negative_shares_skipped(self):
        # data entry error guard
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=-5)}
        prices = {"VWCE.MI": 120}
        assert ca.compute_holding_values(holdings, prices) == {}

    def test_zero_cost_basis_pnl_pct_zero(self):
        # avg_cost=0 → no division by zero
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=10, avg_cost=0)}
        prices = {"VWCE.MI": 120}
        v = ca.compute_holding_values(holdings, prices)["VWCE.MI"]
        assert v["pnl_pct"] == 0.0


# ---------------------------------------------------------------------------
# total_core_value
# ---------------------------------------------------------------------------
class TestTotal:
    def test_sums_all_values(self):
        values = {
            "A": {"current_value": 1000.0},
            "B": {"current_value": 500.0},
        }
        assert ca.total_core_value(values) == 1500.0

    def test_empty_returns_zero(self):
        assert ca.total_core_value({}) == 0.0


# ---------------------------------------------------------------------------
# compute_drift
# ---------------------------------------------------------------------------
class TestDrift:
    def test_actual_equals_target_no_rebalance(self):
        holdings = {
            "VWCE.MI": _h("VWCE.MI", shares=10, target_weight=0.60),
            "AGGH.MI": _h("AGGH.MI", shares=10, target_weight=0.40),
        }
        values = {
            "VWCE.MI": {"current_value": 600.0},
            "AGGH.MI": {"current_value": 400.0},
        }
        drift = ca.compute_drift(holdings, values, total_value=1000.0)
        assert drift["VWCE.MI"]["drift"] == 0.0
        assert drift["VWCE.MI"]["needs_rebalance"] is False
        assert drift["VWCE.MI"]["rebalance_eur"] == 0.0

    def test_overweight_flags_rebalance_sell(self):
        # target 60%, actual 70% → drift +10%, rebalance -100€ (vendi)
        holdings = {"VWCE.MI": _h("VWCE.MI", shares=7, target_weight=0.60)}
        values = {"VWCE.MI": {"current_value": 700.0}}
        drift = ca.compute_drift(holdings, values, total_value=1000.0)
        assert drift["VWCE.MI"]["actual_weight"] == 0.70
        assert drift["VWCE.MI"]["drift"] == pytest.approx(0.10)
        assert drift["VWCE.MI"]["rebalance_eur"] == pytest.approx(-100.0)
        assert drift["VWCE.MI"]["needs_rebalance"] is True

    def test_underweight_flags_rebalance_buy(self):
        # target 60%, actual 50% → drift -10%, rebalance +100€ (compra)
        holdings = {"VWCE.MI": _h("VWCE.MI", target_weight=0.60)}
        values = {"VWCE.MI": {"current_value": 500.0}}
        drift = ca.compute_drift(holdings, values, total_value=1000.0)
        assert drift["VWCE.MI"]["drift"] == pytest.approx(-0.10)
        assert drift["VWCE.MI"]["rebalance_eur"] == pytest.approx(100.0)

    def test_skips_holdings_without_target(self):
        holdings = {"VWCE.MI": _h("VWCE.MI", target_weight=None)}
        values = {"VWCE.MI": {"current_value": 500.0}}
        assert ca.compute_drift(holdings, values, total_value=1000.0) == {}

    def test_under_threshold_no_rebalance(self):
        # drift 3% < threshold 5%
        holdings = {"VWCE.MI": _h("VWCE.MI", target_weight=0.60)}
        values = {"VWCE.MI": {"current_value": 630.0}}
        drift = ca.compute_drift(
            holdings, values, total_value=1000.0, rebalance_threshold=0.05
        )
        assert drift["VWCE.MI"]["needs_rebalance"] is False

    def test_zero_total_returns_empty(self):
        holdings = {"VWCE.MI": _h("VWCE.MI", target_weight=0.60)}
        values = {"VWCE.MI": {"current_value": 0.0}}
        assert ca.compute_drift(holdings, values, total_value=0.0) == {}


# ---------------------------------------------------------------------------
# Breakdown asset_class / region / sector
# ---------------------------------------------------------------------------
class TestBreakdowns:
    def test_asset_class_split(self):
        holdings = {
            "VWCE.MI": _h("VWCE.MI", asset_class="EQUITY_ETF"),
            "AGGH.MI": _h("AGGH.MI", asset_class="BOND_ETF"),
        }
        values = {
            "VWCE.MI": {"current_value": 700.0},
            "AGGH.MI": {"current_value": 300.0},
        }
        bd = ca.compute_asset_class_breakdown(holdings, values, total_value=1000.0)
        assert bd == {"EQUITY_ETF": 0.70, "BOND_ETF": 0.30}

    def test_region_split(self):
        holdings = {
            "VWCE.MI": _h("VWCE.MI", region="WORLD"),
            "EIMI.MI": _h("EIMI.MI", region="EM"),
        }
        values = {
            "VWCE.MI": {"current_value": 800.0},
            "EIMI.MI": {"current_value": 200.0},
        }
        bd = ca.compute_region_breakdown(holdings, values, total_value=1000.0)
        assert bd == {"WORLD": 0.80, "EM": 0.20}

    def test_sector_none_becomes_broad(self):
        # ETF broad senza sector_key → finisce in "broad" non "unknown"
        holdings = {
            "VWCE.MI": _h("VWCE.MI", sector_key=None),
            "SMH": _h("SMH", sector_key="technology"),
        }
        values = {
            "VWCE.MI": {"current_value": 700.0},
            "SMH": {"current_value": 300.0},
        }
        bd = ca.compute_core_sector_breakdown(holdings, values, total_value=1000.0)
        assert bd == {"broad": 0.70, "technology": 0.30}

    def test_missing_field_becomes_unknown(self):
        # asset_class None
        holdings = {"X": _h("X", asset_class=None)}
        values = {"X": {"current_value": 1000.0}}
        bd = ca.compute_asset_class_breakdown(holdings, values, total_value=1000.0)
        assert bd == {"unknown": 1.0}

    def test_zero_total_returns_empty(self):
        holdings = {"V": _h("V")}
        assert ca.compute_asset_class_breakdown(holdings, {}, total_value=0.0) == {}


# ---------------------------------------------------------------------------
# Consolidated sector exposure + overlap
# ---------------------------------------------------------------------------
class TestConsolidatedExposure:
    def test_satellite_only_when_core_empty(self):
        sat_positions = {"NVDA": {"shares": 10}}
        sat_prices = {"NVDA": 100}
        sat_sectors = {"NVDA": "technology"}
        out = ca.compute_consolidated_sector_exposure(
            core_holdings={}, core_values={},
            satellite_positions=sat_positions,
            satellite_prices=sat_prices,
            satellite_sector_map=sat_sectors,
            total_capital_eur=10_000.0,
        )
        # 10 * 100 / 10000 = 0.10
        assert out == {"technology": 0.10}

    def test_core_with_sector_aggregated_with_satellite(self):
        # Core: SMH (tech ETF) 1200€ + satellite NVDA 1000€ = 2200€ tech
        # Capitale totale 10k → 22%
        core_holdings = {"SMH": _h("SMH", sector_key="technology")}
        core_values = {"SMH": {"current_value": 1200.0}}
        sat_positions = {"NVDA": {"shares": 10}}
        sat_prices = {"NVDA": 100}
        sat_sectors = {"NVDA": "technology"}
        out = ca.compute_consolidated_sector_exposure(
            core_holdings=core_holdings, core_values=core_values,
            satellite_positions=sat_positions,
            satellite_prices=sat_prices,
            satellite_sector_map=sat_sectors,
            total_capital_eur=10_000.0,
        )
        assert out["technology"] == pytest.approx(0.22)

    def test_broad_etf_skipped_in_consolidated(self):
        # VWCE (broad, sector_key=None) NON contribuisce
        core_holdings = {"VWCE.MI": _h("VWCE.MI", sector_key=None)}
        core_values = {"VWCE.MI": {"current_value": 6000.0}}
        out = ca.compute_consolidated_sector_exposure(
            core_holdings=core_holdings, core_values=core_values,
            satellite_positions={}, satellite_prices={}, satellite_sector_map={},
            total_capital_eur=10_000.0,
        )
        assert out == {}

    def test_unknown_satellite_sector(self):
        sat_positions = {"FOO": {"shares": 5}}
        sat_prices = {"FOO": 100}
        sat_sectors = {"FOO": None}
        out = ca.compute_consolidated_sector_exposure(
            core_holdings={}, core_values={},
            satellite_positions=sat_positions,
            satellite_prices=sat_prices,
            satellite_sector_map=sat_sectors,
            total_capital_eur=10_000.0,
        )
        assert out == {"unknown": 0.05}

    def test_zero_capital_returns_empty(self):
        out = ca.compute_consolidated_sector_exposure(
            core_holdings={}, core_values={},
            satellite_positions={}, satellite_prices={}, satellite_sector_map={},
            total_capital_eur=0.0,
        )
        assert out == {}


class TestOverlapWarnings:
    def test_flags_over_threshold(self):
        exposure = {"technology": 0.42, "healthcare": 0.20}
        warns = ca.detect_overlap_warnings(exposure, warn_threshold=0.35)
        assert len(warns) == 1
        assert warns[0]["sector"] == "technology"
        assert warns[0]["pct"] == 0.42
        assert warns[0]["over_by"] == pytest.approx(0.07)

    def test_under_threshold_no_warning(self):
        exposure = {"technology": 0.30}
        assert ca.detect_overlap_warnings(exposure, warn_threshold=0.35) == []

    def test_ignores_unknown_bucket(self):
        exposure = {"unknown": 0.50}
        assert ca.detect_overlap_warnings(exposure, warn_threshold=0.35) == []

    def test_orders_by_pct_desc(self):
        exposure = {"technology": 0.40, "healthcare": 0.50}
        warns = ca.detect_overlap_warnings(exposure, warn_threshold=0.35)
        assert warns[0]["sector"] == "healthcare"
        assert warns[1]["sector"] == "technology"


# ---------------------------------------------------------------------------
# summarize_core (helper end-to-end)
# ---------------------------------------------------------------------------
class TestSummarize:
    def test_returns_full_summary(self):
        holdings = {
            "VWCE.MI": _h(
                "VWCE.MI", shares=10, avg_cost=100,
                asset_class="EQUITY_ETF", region="WORLD",
                sector_key=None, target_weight=0.70,
            ),
            "AGGH.MI": _h(
                "AGGH.MI", shares=20, avg_cost=50,
                asset_class="BOND_ETF", region="EU",
                sector_key=None, target_weight=0.30,
            ),
        }
        prices = {"VWCE.MI": 120, "AGGH.MI": 55}
        s = ca.summarize_core(holdings, prices)
        # 10*120 + 20*55 = 1200 + 1100 = 2300
        assert s["total_value_eur"] == 2300.0
        assert s["n_holdings"] == 2
        assert s["n_missing_price"] == 0
        assert s["asset_class"]["EQUITY_ETF"] == pytest.approx(1200 / 2300, abs=1e-3)
        assert s["region"]["WORLD"] == pytest.approx(1200 / 2300, abs=1e-3)
        assert s["sector"]["broad"] == 1.0
        # drift: VWCE actual 1200/2300=52%, target 70% → underweight 18%
        assert s["drift"]["VWCE.MI"]["needs_rebalance"] is True

    def test_empty_holdings(self):
        s = ca.summarize_core({}, {})
        assert s["total_value_eur"] == 0.0
        assert s["holdings"] == {}
        assert s["drift"] == {}
        assert s["n_holdings"] == 0

    def test_counts_missing_prices(self):
        holdings = {
            "VWCE.MI": _h("VWCE.MI", shares=10),
            "AGGH.MI": _h("AGGH.MI", shares=20),
        }
        prices = {"VWCE.MI": 120}  # AGGH manca
        s = ca.summarize_core(holdings, prices)
        assert s["n_holdings"] == 1
        assert s["n_missing_price"] == 1
