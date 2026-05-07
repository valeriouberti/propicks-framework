"""Test puri sul layer SFM (sector-filtered momentum).

Tutto offline:
- ``normalize_sector_to_key`` / ``filter_universe_by_sector`` / overlay sono
  funzioni pure.
- La pipeline ``discover_sector_momentum_candidates`` riceve fetcher
  iniettabile e mocka ``analyze_ticker`` per saltare lo stage 2 yfinance.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from propicks.domain.sector_momentum import (
    INSTRUMENT_STOCK,
    INSTRUMENT_SUBETF,
    SECTOR_KEY_ALIASES,
    VALID_INSTRUMENTS,
    apply_peer_rs_overlay,
    discover_sector_momentum_candidates,
    enrich_with_sfm_score,
    filter_universe_by_sector,
    normalize_sector_to_key,
    peer_etf_for_sector_key,
    score_sub_etfs,
    sector_key_for_peer_etf,
    select_top_sectors,
)


# ---------------------------------------------------------------------------
# Synthetic OHLCV (riusato dal pattern di test_momentum_discovery)
# ---------------------------------------------------------------------------
def _bullish_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end="2026-04-24", periods=n, freq="B")
    closes = [base_price + i * 0.5 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": [c - 0.2 for c in closes],
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# normalize_sector_to_key — alias GICS + Yahoo
# ---------------------------------------------------------------------------
def test_normalize_gics_canonical():
    assert normalize_sector_to_key("Information Technology") == "technology"
    assert normalize_sector_to_key("Health Care") == "healthcare"
    assert normalize_sector_to_key("Consumer Discretionary") == "consumer_discretionary"
    assert normalize_sector_to_key("Consumer Staples") == "consumer_staples"
    assert normalize_sector_to_key("Financials") == "financials"
    assert normalize_sector_to_key("Communication Services") == "communications"
    assert normalize_sector_to_key("Materials") == "materials"


def test_normalize_yahoo_variants():
    assert normalize_sector_to_key("Technology") == "technology"
    assert normalize_sector_to_key("Healthcare") == "healthcare"
    assert normalize_sector_to_key("Consumer Cyclical") == "consumer_discretionary"
    assert normalize_sector_to_key("Consumer Defensive") == "consumer_staples"
    assert normalize_sector_to_key("Financial Services") == "financials"
    assert normalize_sector_to_key("Basic Materials") == "materials"


def test_normalize_case_insensitive_and_whitespace():
    assert normalize_sector_to_key("  technology  ") == "technology"
    assert normalize_sector_to_key("INFORMATION TECHNOLOGY") == "technology"
    assert normalize_sector_to_key("hEaLtH cArE") == "healthcare"


def test_normalize_unknown_or_empty_returns_none():
    assert normalize_sector_to_key(None) is None
    assert normalize_sector_to_key("") is None
    assert normalize_sector_to_key("   ") is None
    assert normalize_sector_to_key("Crypto Hype Sector") is None
    assert normalize_sector_to_key(123) is None  # type: ignore


def test_every_sector_key_has_at_least_one_alias():
    """Sanity: ogni sector_key in SECTOR_KEY_ALIASES ha almeno un alias."""
    for key, aliases in SECTOR_KEY_ALIASES.items():
        assert aliases, f"sector_key '{key}' senza alias"


# ---------------------------------------------------------------------------
# peer_etf_for_sector_key + sector_key_for_peer_etf (round-trip)
# ---------------------------------------------------------------------------
def test_peer_etf_round_trip_us_sectors():
    """Per ogni sector_key in SECTOR_KEY_ALIASES, il roundtrip sector→etf→sector è identità."""
    for key in SECTOR_KEY_ALIASES.keys():
        etf = peer_etf_for_sector_key(key)
        assert etf is not None, f"{key} non mappa su un peer ETF"
        back = sector_key_for_peer_etf(etf)
        assert back == key, f"round-trip {key} → {etf} → {back}"


def test_peer_etf_unknown_returns_none():
    assert peer_etf_for_sector_key("not_a_sector") is None
    assert sector_key_for_peer_etf("ZZZZ") is None


def test_peer_etf_lookup_case_insensitive():
    assert sector_key_for_peer_etf("xlk") == "technology"
    assert sector_key_for_peer_etf("XLK") == "technology"


# ---------------------------------------------------------------------------
# filter_universe_by_sector
# ---------------------------------------------------------------------------
def _sample_universe() -> list[dict]:
    return [
        {"ticker": "AAPL", "sector": "Information Technology"},  # GICS
        {"ticker": "MSFT", "sector": "Technology"},               # Yahoo
        {"ticker": "JPM", "sector": "Financials"},                # GICS
        {"ticker": "BAC", "sector": "Financial Services"},        # Yahoo
        {"ticker": "XOM", "sector": "Energy"},
        {"ticker": "NOSEC", "sector": None},                      # missing
        {"ticker": "WEIRD", "sector": "Unknown Sector"},          # unknown
    ]


def test_filter_tech_picks_both_aliases():
    out = filter_universe_by_sector(_sample_universe(), "technology")
    assert set(out) == {"AAPL", "MSFT"}


def test_filter_financials_picks_both_aliases():
    out = filter_universe_by_sector(_sample_universe(), "financials")
    assert set(out) == {"JPM", "BAC"}


def test_filter_skips_missing_or_unknown_sector():
    out = filter_universe_by_sector(_sample_universe(), "energy")
    assert out == ["XOM"]
    # NOSEC e WEIRD non sono mai inclusi


def test_filter_invalid_sector_key_returns_empty():
    out = filter_universe_by_sector(_sample_universe(), "not_a_real_key")
    assert out == []


def test_filter_empty_universe_returns_empty():
    assert filter_universe_by_sector([], "technology") == []


def test_filter_uppercases_tickers():
    universe = [{"ticker": "aapl", "sector": "Technology"}]
    assert filter_universe_by_sector(universe, "technology") == ["AAPL"]


# ---------------------------------------------------------------------------
# apply_peer_rs_overlay
# ---------------------------------------------------------------------------
def test_overlay_with_strong_peer_rs_lifts_score():
    out = apply_peer_rs_overlay(70.0, {"score": 90.0}, weight=0.20)
    assert out == pytest.approx(74.0)


def test_overlay_with_weak_peer_rs_drags_score():
    out = apply_peer_rs_overlay(70.0, {"score": 50.0}, weight=0.20)
    assert out == pytest.approx(66.0)


def test_overlay_none_rs_returns_base_unchanged():
    """Ticker non US (rs_vs_sector=None) non subisce overlay."""
    assert apply_peer_rs_overlay(70.0, None, weight=0.20) == 70.0


def test_overlay_missing_score_field_returns_base():
    assert apply_peer_rs_overlay(70.0, {"peer_etf": "XLK"}, weight=0.20) == 70.0


def test_overlay_out_of_range_score_returns_base():
    """Score fuori [0, 100] (defensive) → no contribution."""
    assert apply_peer_rs_overlay(70.0, {"score": -5.0}, weight=0.20) == 70.0
    assert apply_peer_rs_overlay(70.0, {"score": 150.0}, weight=0.20) == 70.0


def test_overlay_weight_bounds_validated():
    with pytest.raises(ValueError):
        apply_peer_rs_overlay(70.0, {"score": 80.0}, weight=-0.1)
    with pytest.raises(ValueError):
        apply_peer_rs_overlay(70.0, {"score": 80.0}, weight=1.5)


def test_overlay_zero_weight_returns_base():
    """Edge case: weight=0 disabilita overlay."""
    assert apply_peer_rs_overlay(70.0, {"score": 100.0}, weight=0.0) == 70.0


def test_overlay_full_weight_returns_pure_rs():
    """Edge case: weight=1 ignora composite base."""
    assert apply_peer_rs_overlay(70.0, {"score": 90.0}, weight=1.0) == 90.0


# ---------------------------------------------------------------------------
# enrich_with_sfm_score
# ---------------------------------------------------------------------------
def test_enrich_preserves_base_composite():
    analysis = {
        "ticker": "AAPL",
        "score_composite": 80.0,
        "rs_vs_sector": {"score": 90.0, "peer_etf": "XLK"},
    }
    out = enrich_with_sfm_score(analysis, weight=0.20)
    assert out["score_composite"] == 80.0  # invariato (Pine sync)
    assert out["score_sfm"] == pytest.approx(82.0)
    assert out["sfm_overlay_weight"] == 0.20


def test_enrich_does_not_mutate_input():
    analysis = {"ticker": "AAPL", "score_composite": 80.0, "rs_vs_sector": None}
    out = enrich_with_sfm_score(analysis)
    assert out is not analysis
    assert "score_sfm" not in analysis


# ---------------------------------------------------------------------------
# select_top_sectors
# ---------------------------------------------------------------------------
def _ranked_etfs_sample() -> list[dict]:
    return [
        {"ticker": "XLK", "sector_key": "technology", "score_composite": 85.0},
        {"ticker": "XLF", "sector_key": "financials", "score_composite": 72.0},
        {"ticker": "XLV", "sector_key": "healthcare", "score_composite": 68.0},
        {"ticker": "XLE", "sector_key": "energy", "score_composite": 55.0},
        {"ticker": "XLU", "sector_key": "utilities", "score_composite": 30.0},
    ]


def test_select_top_sectors_filters_by_score_and_top_n():
    out = select_top_sectors(_ranked_etfs_sample(), top_n=2, min_score=70.0)
    tickers = [r["ticker"] for r in out]
    assert tickers == ["XLK", "XLF"]


def test_select_top_sectors_returns_empty_below_threshold():
    out = select_top_sectors(_ranked_etfs_sample(), top_n=3, min_score=95.0)
    assert out == []


def test_select_top_sectors_caps_at_eligible_count():
    """top_n=5 ma solo 2 settori sopra threshold → ritorna 2."""
    out = select_top_sectors(_ranked_etfs_sample(), top_n=5, min_score=70.0)
    assert len(out) == 2


def test_select_top_sectors_empty_input():
    assert select_top_sectors([], top_n=2) == []


# ---------------------------------------------------------------------------
# discover_sector_momentum_candidates — pipeline end-to-end
# ---------------------------------------------------------------------------
def test_discover_requires_exactly_one_input_mode():
    with pytest.raises(ValueError):
        discover_sector_momentum_candidates([])  # né ranked_etfs né sector_keys
    with pytest.raises(ValueError):
        discover_sector_momentum_candidates(
            [],
            ranked_etfs=_ranked_etfs_sample(),
            sector_keys=["technology"],
        )


def test_discover_rotate_driven_mode_picks_top_sectors():
    """Mode A: passa ranked_etfs → seleziona settori OVERWEIGHT internamente."""
    universe = [
        {"ticker": "AAPL", "sector": "Technology"},
        {"ticker": "MSFT", "sector": "Information Technology"},
        {"ticker": "JPM", "sector": "Financials"},
        {"ticker": "XOM", "sector": "Energy"},
    ]
    fetch = lambda t: _bullish_df()
    fake_analysis = lambda ticker, strategy=None: {
        "ticker": ticker,
        "score_composite": 80.0,
        "classification": "A — AZIONE IMMEDIATA",
        "rs_vs_sector": {"score": 85.0, "peer_etf": "XLK"},
        "scores": {},
    }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analysis,
    ):
        out = discover_sector_momentum_candidates(
            universe,
            ranked_etfs=_ranked_etfs_sample(),
            top_sectors=2,
            top_stocks_per_sector=3,
            min_sector_score=70.0,
            min_stock_score=60.0,
            fetch_fn=fetch,
        )

    sectors = [s["sector_key"] for s in out["sectors_evaluated"]]
    assert sectors == ["technology", "financials"]  # XLK + XLF (>=70)
    cand_tickers = {c["ticker"] for c in out["candidates"]}
    assert cand_tickers == {"AAPL", "MSFT", "JPM"}
    # Tutti i candidati hanno score_sfm enriched
    for c in out["candidates"]:
        assert "score_sfm" in c
        assert c["sector_key"] in {"technology", "financials"}


def test_discover_explicit_sector_mode_skips_rotation():
    """Mode B: passa sector_keys esplicito → niente rotation gating."""
    universe = [
        {"ticker": "AAPL", "sector": "Technology"},
        {"ticker": "JPM", "sector": "Financials"},
    ]
    fetch = lambda t: _bullish_df()
    fake_analysis = lambda ticker, strategy=None: {
        "ticker": ticker,
        "score_composite": 80.0,
        "rs_vs_sector": {"score": 85.0, "peer_etf": "XLK"},
        "scores": {},
    }
    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analysis,
    ):
        out = discover_sector_momentum_candidates(
            universe,
            sector_keys=["technology"],
            top_stocks_per_sector=3,
            min_stock_score=60.0,
            fetch_fn=fetch,
        )
    assert len(out["sectors_evaluated"]) == 1
    assert out["sectors_evaluated"][0]["sector_key"] == "technology"
    assert out["sectors_evaluated"][0]["sector_score"] is None
    cand = {c["ticker"] for c in out["candidates"]}
    assert cand == {"AAPL"}


def test_discover_returns_empty_when_no_sector_qualifies():
    """ranked_etfs tutti sotto threshold → nessun settore evaluated."""
    weak_ranked = [
        {"ticker": "XLK", "sector_key": "technology", "score_composite": 30.0},
    ]
    out = discover_sector_momentum_candidates(
        [{"ticker": "AAPL", "sector": "Technology"}],
        ranked_etfs=weak_ranked,
        min_sector_score=70.0,
    )
    assert out["sectors_evaluated"] == []
    assert out["candidates"] == []


def test_discover_handles_invalid_sector_key_gracefully():
    """Sector_key non mappato → skip senza crash."""
    out = discover_sector_momentum_candidates(
        [{"ticker": "AAPL", "sector": "Technology"}],
        sector_keys=["not_a_real_sector"],
    )
    assert out["sectors_evaluated"] == []
    assert out["candidates"] == []


def test_discover_records_universe_size_per_sector():
    """sectors_evaluated tiene traccia di n_universe e n_candidates."""
    universe = [
        {"ticker": "AAPL", "sector": "Technology"},
        {"ticker": "MSFT", "sector": "Technology"},
        {"ticker": "JPM", "sector": "Financials"},
    ]
    # Nessuno passa il prefilter (df vuoto / fetch fail)
    fetch = lambda t: None
    out = discover_sector_momentum_candidates(
        universe,
        sector_keys=["technology", "financials"],
        fetch_fn=fetch,
    )
    n_uni_by_sector = {s["sector_key"]: s["n_universe"] for s in out["sectors_evaluated"]}
    assert n_uni_by_sector == {"technology": 2, "financials": 1}
    n_cand_by_sector = {s["sector_key"]: s["n_candidates"] for s in out["sectors_evaluated"]}
    assert n_cand_by_sector == {"technology": 0, "financials": 0}


def test_discover_ranks_candidates_cross_sector_by_sfm_score():
    """Candidati ordinati desc by score_sfm su tutti i settori, non per sector."""
    universe = [
        {"ticker": "TECH1", "sector": "Technology"},
        {"ticker": "FIN1", "sector": "Financials"},
    ]
    fetch = lambda t: _bullish_df()

    def fake_analysis(ticker, strategy=None):
        # FIN1 ha score base più alto, TECH1 ha rs più alto → SFM ranking
        # dipende dall'overlay weight.
        if ticker == "FIN1":
            return {
                "ticker": "FIN1",
                "score_composite": 85.0,
                "rs_vs_sector": {"score": 60.0, "peer_etf": "XLF"},
                "scores": {},
            }
        return {
            "ticker": "TECH1",
            "score_composite": 80.0,
            "rs_vs_sector": {"score": 95.0, "peer_etf": "XLK"},
            "scores": {},
        }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analysis,
    ):
        out = discover_sector_momentum_candidates(
            universe,
            sector_keys=["technology", "financials"],
            min_stock_score=0.0,
            rs_overlay_weight=0.20,
            fetch_fn=fetch,
        )

    # FIN1 SFM = 85 * 0.8 + 60 * 0.2 = 68 + 12 = 80
    # TECH1 SFM = 80 * 0.8 + 95 * 0.2 = 64 + 19 = 83 → TECH1 first
    assert len(out["candidates"]) == 2
    assert out["candidates"][0]["ticker"] == "TECH1"
    assert out["candidates"][0]["score_sfm"] > out["candidates"][1]["score_sfm"]


# ---------------------------------------------------------------------------
# instrument toggle (stock | subetf)
# ---------------------------------------------------------------------------
def test_valid_instruments_set_contains_both_modes():
    assert INSTRUMENT_STOCK in VALID_INSTRUMENTS
    assert INSTRUMENT_SUBETF in VALID_INSTRUMENTS


def test_discover_rejects_invalid_instrument():
    with pytest.raises(ValueError, match="instrument="):
        discover_sector_momentum_candidates(
            [{"ticker": "AAPL", "sector": "Technology"}],
            sector_keys=["technology"],
            instrument="bonds",
        )


def test_discover_stock_mode_requires_detailed_universe():
    with pytest.raises(ValueError, match="detailed_universe"):
        discover_sector_momentum_candidates(
            None,
            sector_keys=["technology"],
            instrument=INSTRUMENT_STOCK,
        )


def test_discover_returns_instrument_echo_in_output():
    """Output dict contiene `instrument` per il caller (CLI / dashboard)."""
    out = discover_sector_momentum_candidates(
        [{"ticker": "AAPL", "sector": "Technology"}],
        sector_keys=["technology"],
        instrument=INSTRUMENT_STOCK,
        fetch_fn=lambda t: None,
    )
    assert out["instrument"] == INSTRUMENT_STOCK


# ---------------------------------------------------------------------------
# score_sub_etfs — Stage 3 alternativo per subetf mode
# ---------------------------------------------------------------------------
def test_score_sub_etfs_filters_by_min_score_and_sorts():
    def fake(ticker):
        scores = {"SOXX": 90.0, "IGV": 65.0, "CIBR": 78.0}
        return {
            "ticker": ticker,
            "score_composite": scores[ticker],
            "rs_vs_sector": None,
            "scores": {},
        }

    out = score_sub_etfs(
        ["SOXX", "IGV", "CIBR"],
        top_n=3,
        min_score=70.0,
        analyze_fn=fake,
    )
    tickers = [r["ticker"] for r in out]
    assert tickers == ["SOXX", "CIBR"]  # IGV scartato (<70), ranking desc


def test_score_sub_etfs_top_n_truncates():
    fake = lambda t: {"ticker": t, "score_composite": 80.0, "rs_vs_sector": None, "scores": {}}
    out = score_sub_etfs(["A", "B", "C", "D"], top_n=2, min_score=0.0, analyze_fn=fake)
    assert len(out) == 2


def test_score_sub_etfs_skips_none_results():
    """analyze_fn ritorna None (data unavailable) → skip senza crash."""
    def fake(ticker):
        if ticker == "DEAD":
            return None
        return {"ticker": ticker, "score_composite": 80.0, "rs_vs_sector": None, "scores": {}}

    out = score_sub_etfs(["DEAD", "OK"], top_n=5, min_score=0.0, analyze_fn=fake)
    assert [r["ticker"] for r in out] == ["OK"]


def test_score_sub_etfs_swallows_exceptions_and_continues():
    def fake(ticker):
        if ticker == "BOOM":
            raise RuntimeError("network down")
        return {"ticker": ticker, "score_composite": 75.0, "rs_vs_sector": None, "scores": {}}

    out = score_sub_etfs(["BOOM", "SAFE"], top_n=5, min_score=0.0, analyze_fn=fake)
    assert [r["ticker"] for r in out] == ["SAFE"]


def test_score_sub_etfs_empty_universe_returns_empty():
    assert score_sub_etfs([], top_n=3, min_score=70.0, analyze_fn=lambda t: None) == []


# ---------------------------------------------------------------------------
# discover pipeline subetf mode end-to-end
# ---------------------------------------------------------------------------
def test_discover_subetf_uses_curated_universe_no_detailed_needed():
    """Mode subetf: detailed_universe può essere None (universe da PARENT_TO_SUB_ETFS)."""
    def fake(ticker):
        # Tutti i sub-ETF di XLK simulati con score uniforme
        return {
            "ticker": ticker,
            "score_composite": 80.0,
            "rs_vs_sector": None,
            "scores": {},
        }

    out = discover_sector_momentum_candidates(
        None,
        instrument=INSTRUMENT_SUBETF,
        sector_keys=["technology"],
        top_stocks_per_sector=3,
        min_stock_score=70.0,
        analyze_fn=fake,
    )
    assert out["instrument"] == INSTRUMENT_SUBETF
    assert len(out["sectors_evaluated"]) == 1
    sec = out["sectors_evaluated"][0]
    assert sec["sector_key"] == "technology"
    assert sec["peer_etf"] == "XLK"
    assert sec["n_universe"] >= 3  # PARENT_TO_SUB_ETFS["XLK"] ha >= 3 sub-ETF
    assert sec["n_candidates"] == 3  # top_stocks_per_sector cap

    # Tutti i candidati taggati instrument=subetf + sector context
    for c in out["candidates"]:
        assert c["instrument"] == INSTRUMENT_SUBETF
        assert c["sector_key"] == "technology"
        assert c["peer_etf"] == "XLK"
        # rs_vs_sector None → score_sfm = base composite (no overlay)
        assert c["score_sfm"] == c["score_composite"]


def test_discover_subetf_rotate_driven_picks_parent_then_sub_etfs():
    """Rotate-driven + subetf: top settori → loop sub-ETF di ognuno."""
    fake = lambda t: {
        "ticker": t, "score_composite": 80.0, "rs_vs_sector": None, "scores": {},
    }
    out = discover_sector_momentum_candidates(
        None,
        instrument=INSTRUMENT_SUBETF,
        ranked_etfs=_ranked_etfs_sample(),
        top_sectors=2,
        top_stocks_per_sector=2,
        min_sector_score=70.0,
        min_stock_score=0.0,
        analyze_fn=fake,
    )
    sectors = [s["sector_key"] for s in out["sectors_evaluated"]]
    assert sectors == ["technology", "financials"]  # XLK + XLF (≥70)
    # 2 settori × 2 sub-ETF max = 4 candidati totali
    assert len(out["candidates"]) <= 4
    # Tutti devono essere sub-ETF noti del parent corrispondente
    from propicks.domain.subetf_universe import sub_etfs_for_parent
    for c in out["candidates"]:
        assert c["ticker"] in sub_etfs_for_parent(c["peer_etf"])


def test_discover_subetf_empty_when_no_sector_qualifies():
    weak = [{"ticker": "XLK", "sector_key": "technology", "score_composite": 30.0}]
    out = discover_sector_momentum_candidates(
        None,
        instrument=INSTRUMENT_SUBETF,
        ranked_etfs=weak,
        min_sector_score=70.0,
        analyze_fn=lambda t: None,
    )
    assert out["sectors_evaluated"] == []
    assert out["candidates"] == []


def test_discover_subetf_min_score_filter():
    """min_stock_score taglia sub-ETF deboli."""
    def fake(t):
        scores = {"SOXX": 90.0, "SMH": 78.0, "IGV": 65.0, "CIBR": 50.0}
        return {
            "ticker": t,
            "score_composite": scores.get(t, 60.0),
            "rs_vs_sector": None,
            "scores": {},
        }

    out = discover_sector_momentum_candidates(
        None,
        instrument=INSTRUMENT_SUBETF,
        sector_keys=["technology"],
        top_stocks_per_sector=10,
        min_stock_score=70.0,
        analyze_fn=fake,
    )
    tickers = {c["ticker"] for c in out["candidates"]}
    assert "SOXX" in tickers
    assert "SMH" in tickers
    assert "IGV" not in tickers  # 65 < 70
    assert "CIBR" not in tickers  # 50 < 70


def test_discover_stock_mode_tags_instrument_in_candidates():
    """Stock mode (default) deve taggare candidati con instrument=stock."""
    universe = [{"ticker": "AAPL", "sector": "Technology"}]
    fetch = lambda t: _bullish_df()
    fake = lambda ticker, strategy=None: {
        "ticker": ticker,
        "score_composite": 80.0,
        "rs_vs_sector": None,
        "scores": {},
    }
    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake,
    ):
        out = discover_sector_momentum_candidates(
            universe,
            sector_keys=["technology"],
            min_stock_score=0.0,
            fetch_fn=fetch,
        )
    assert out["candidates"][0]["instrument"] == INSTRUMENT_STOCK
