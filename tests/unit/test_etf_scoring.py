"""Test puri sullo scoring ETF.

Lo scoring è puro (sub-score prendono numeri/Series, ritornano dict).
L'orchestratore ``analyze_etf`` fa I/O — lo lasciamo fuori dai unit test
(coperto in modo integrativo da smoke test manuale della CLI).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from propicks.domain.etf_scoring import (
    apply_regime_cap,
    classify_etf,
    score_abs_momentum,
    score_etf_trend,
    score_regime_fit,
    score_rs,
    suggest_allocation,
)


def _geom_series(n: int, step: float, start: float = 100.0) -> pd.Series:
    """Serie moltiplicativa: start * (1+step)^i."""
    idx = pd.date_range("2022-01-01", periods=n, freq="W")
    values = start * np.power(1 + step, np.arange(n))
    return pd.Series(values, index=idx)


# ---------------------------------------------------------------------------
# score_rs
# ---------------------------------------------------------------------------
def test_rs_leader_accelerating_scores_max():
    # ETF +0.5%/week, benchmark +0.1%/week → outperform sostenuto
    etf = _geom_series(60, 0.005)
    bench = _geom_series(60, 0.001)
    out = score_rs(etf, bench)
    assert out["score"] == 100.0
    assert out["rs_ratio"] > 1.05
    assert out["rs_slope"] > 0


def test_rs_underperformer_distributing_scores_low():
    # ETF -0.3%/week, benchmark +0.2%/week → lagger in distribuzione
    etf = _geom_series(60, -0.003)
    bench = _geom_series(60, 0.002)
    out = score_rs(etf, bench)
    assert out["score"] == 10.0
    assert out["rs_ratio"] < 0.95


def test_rs_insufficient_history_neutral():
    etf = _geom_series(10, 0.01)
    bench = _geom_series(10, 0.005)
    out = score_rs(etf, bench)
    assert out["score"] == 50.0
    assert out["rs_ratio"] is None


def test_rs_benchmark_none_returns_neutral():
    etf = _geom_series(60, 0.01)
    out = score_rs(etf, None)
    assert out["score"] == 50.0


def test_rs_index_intersection_handles_misaligned():
    # ETF con 60 barre weekly, benchmark con 40 barre che coprono un sottoinsieme
    etf = _geom_series(60, 0.005)
    bench = _geom_series(60, 0.001)
    # Bench con meno storia
    bench = bench.iloc[-40:]
    out = score_rs(etf, bench)
    # Non deve crashare, ma sufficienti barre comuni per ritornare score reale
    assert out["score"] != 50.0 or out.get("note", "").startswith("storia")


def test_rs_cross_timezone_alignment():
    # yfinance tz-localizza l'indice nel fuso dell'exchange: Xetra (Europe/Berlin)
    # per gli ETF EU/WORLD, NYSE (America/New_York) per il benchmark ^GSPC/URTH.
    # Senza strip del tz l'inner join produce 0 righe anche su date identiche.
    etf = _geom_series(60, 0.005)
    bench = _geom_series(60, 0.001)
    etf.index = etf.index.tz_localize("Europe/Berlin")
    bench.index = bench.index.tz_localize("America/New_York")
    out = score_rs(etf, bench)
    assert out["rs_ratio"] is not None
    assert out["score"] == 100.0


def test_rs_mixed_tz_naive_and_aware():
    # Caso misto: ETF naïve, benchmark tz-aware. Deve comunque allineare.
    etf = _geom_series(60, 0.005)
    bench = _geom_series(60, 0.001)
    bench.index = bench.index.tz_localize("America/New_York")
    out = score_rs(etf, bench)
    assert out["rs_ratio"] is not None
    assert out["score"] == 100.0


# ---------------------------------------------------------------------------
# score_regime_fit
# ---------------------------------------------------------------------------
def test_regime_fit_favored_in_current():
    # technology favorito in STRONG_BULL (5)
    assert score_regime_fit("technology", 5) == 100.0


def test_regime_fit_favored_in_adjacent():
    # materials è favored in BULL (4) ma non in STRONG_BULL (5)
    # → adjacent → 60
    assert score_regime_fit("materials", 5) == 60.0


def test_regime_fit_not_favored_out_of_regime():
    # technology non è favorito in STRONG_BEAR (1), e neanche in BEAR (2)
    assert score_regime_fit("technology", 1) == 20.0


def test_regime_fit_neutral_when_regime_unknown():
    assert score_regime_fit("technology", None) == 50.0
    assert score_regime_fit(None, 5) == 50.0


# ---------------------------------------------------------------------------
# score_abs_momentum
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("perf,expected", [
    (0.20, 100.0),
    (0.10, 80.0),
    (0.05, 60.0),
    (0.02, 40.0),
    (-0.02, 25.0),
    (-0.10, 10.0),
    (None, 40.0),
])
def test_abs_momentum_buckets(perf, expected):
    assert score_abs_momentum(perf) == expected


# ---------------------------------------------------------------------------
# score_etf_trend
# ---------------------------------------------------------------------------
def test_trend_uptrend_scores_max():
    # Series in trend deciso: EMA in salita, prezzo sopra EMA
    close = _geom_series(60, 0.005)
    out = score_etf_trend(close)
    assert out["score"] == 100.0
    assert out["above_ema"] is True
    assert out["ema_slope"] > 0.005


def test_trend_downtrend_scores_floor():
    close = _geom_series(60, -0.005)
    out = score_etf_trend(close)
    assert out["score"] == 10.0
    assert out["above_ema"] is False


def test_trend_insufficient_history_neutral():
    close = _geom_series(10, 0.01)
    out = score_etf_trend(close)
    assert out["score"] == 50.0


# ---------------------------------------------------------------------------
# Regime hard-gate
# ---------------------------------------------------------------------------
def test_cap_zeroes_non_favored_in_strong_bear():
    # technology NON favored in STRONG_BEAR → cap a 0
    assert apply_regime_cap(85.0, "technology", 1) == 0.0


def test_cap_limits_non_favored_in_bear():
    # consumer_discretionary NON favored in BEAR → cap a 50
    assert apply_regime_cap(85.0, "consumer_discretionary", 2) == 50.0
    # Se già sotto 50, non viene alzato
    assert apply_regime_cap(30.0, "consumer_discretionary", 2) == 30.0


def test_cap_preserves_favored_in_bear():
    # utilities favored in BEAR → nessun cap
    assert apply_regime_cap(85.0, "utilities", 2) == 85.0


def test_cap_noop_in_bullish_regimes():
    for regime in (3, 4, 5):
        assert apply_regime_cap(85.0, "technology", regime) == 85.0


def test_cap_noop_when_regime_unknown():
    assert apply_regime_cap(85.0, "technology", None) == 85.0


# ---------------------------------------------------------------------------
# Classificazione
# ---------------------------------------------------------------------------
def test_classify_etf_thresholds():
    assert classify_etf(80).startswith("A")
    assert classify_etf(60).startswith("B")
    assert classify_etf(45).startswith("C")
    assert classify_etf(20).startswith("D")


# ---------------------------------------------------------------------------
# suggest_allocation
# ---------------------------------------------------------------------------
def _mk_ranked(*entries: tuple[str, float, str, int]) -> list[dict]:
    """Entry: (ticker, score, classification_prefix, regime_code)."""
    return [
        {
            "ticker": t,
            "sector_key": "technology",
            "score_composite": s,
            "classification": f"{cl} — FOO",
            "regime_code": rc,
            "price": 100.0,
            "stop_suggested": 95.0,
        }
        for t, s, cl, rc in entries
    ]


def test_allocation_strong_bear_goes_flat():
    ranked = _mk_ranked(("XLP", 85.0, "A", 1), ("XLU", 80.0, "A", 1))
    out = suggest_allocation(ranked, top_n=3)
    assert out["positions"] == []
    assert out["aggregate_pct"] == 0.0
    assert "STRONG_BEAR" in out["note"]


def test_allocation_bear_reduces_to_top_one():
    ranked = _mk_ranked(("XLP", 85.0, "A", 2), ("XLU", 78.0, "A", 2), ("XLV", 72.0, "A", 2))
    out = suggest_allocation(ranked, top_n=3)
    assert len(out["positions"]) == 1
    assert out["positions"][0]["ticker"] == "XLP"
    assert out["effective_top_n"] == 1


def test_allocation_neutral_picks_top_n():
    ranked = _mk_ranked(
        ("XLK", 85.0, "A", 3),
        ("XLF", 75.0, "A", 3),
        ("XLI", 70.0, "A", 3),
        ("XLV", 60.0, "B", 3),  # ancora eligible ma oltre top-3
    )
    out = suggest_allocation(ranked, top_n=3)
    tickers = [p["ticker"] for p in out["positions"]]
    assert tickers == ["XLK", "XLF", "XLI"]
    # Equal weight, 3 posizioni, cap aggregato 60% → 20% ciascuno ma cap per ETF 15% lo blocca
    assert all(p["allocation_pct"] == 0.15 for p in out["positions"])
    assert out["aggregate_pct"] == 0.45


def test_allocation_excludes_avoid_and_neutral_classes():
    ranked = _mk_ranked(
        ("XLK", 85.0, "A", 3),
        ("XLF", 45.0, "C", 3),  # neutrale — escluso
        ("XLE", 20.0, "D", 3),  # avoid — escluso
    )
    out = suggest_allocation(ranked, top_n=3)
    assert [p["ticker"] for p in out["positions"]] == ["XLK"]


def test_allocation_empty_when_no_eligible():
    ranked = _mk_ranked(("XLE", 20.0, "D", 3), ("XLU", 30.0, "D", 3))
    out = suggest_allocation(ranked, top_n=3)
    assert out["positions"] == []
    assert "Wait-and-see" in out["note"]


def test_allocation_empty_ranked():
    out = suggest_allocation([], top_n=3)
    assert out["positions"] == []
    assert out["aggregate_pct"] == 0.0
