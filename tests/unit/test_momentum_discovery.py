"""Test della pipeline discovery momentum a 3 stadi.

Strategy: il prefilter accetta un fetch_fn iniettabile, quindi i test
girano completamente offline iniettando DataFrame sintetici. Lo stage 2
(``analyze_ticker``) richiede yfinance reale, quindi è patchato nei test
end-to-end del modulo discovery.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from propicks.domain.momentum_discovery import (
    DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    DISCOVERY_PREFILTER_RSI_MIN,
    discover_momentum_candidates,
    prefilter_momentum,
)


# ---------------------------------------------------------------------------
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------
def _bullish_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Trend up steady — RSI alto, price sopra EMA50/EMA20, near 52w-high."""
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


def _broken_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Trend up + crollo finale: price sotto EMA50, lontano dal 52w-high.

    180 barre rally 100→190, poi 20 barre crash a ~140. Il prefilter
    momentum deve scartarlo (price < EMA50 e RSI basso).
    """
    idx = pd.date_range(end="2026-04-24", periods=n, freq="B")
    rally_n = n - 20
    rally = [base_price + i * 0.5 for i in range(rally_n)]
    crash_start = rally[-1]
    crash = [crash_start - (i + 1) * 2.5 for i in range(20)]
    closes = rally + crash

    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_500_000] * n,
        },
        index=idx,
    )


def _far_from_high_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Trend up ma con vecchio peak molto alto: dist_from_high > 35%.

    Pattern: 80 barre sparate 100→250 (peak), 80 barre crash 250→100,
    poi 40 barre rally 100→130. 52w-high = 250, price = 130 →
    dist ~48% → ben oltre il 35% threshold.
    """
    idx = pd.date_range(end="2026-04-24", periods=n, freq="B")
    rally_up = [base_price + i * 1.875 for i in range(80)]       # 100 → 250
    crash_down = [250 - (i + 1) * 1.875 for i in range(80)]      # 250 → 100
    rally_back = [100 + (i + 1) * 0.75 for i in range(n - 160)]   # 100 → 130
    closes = rally_up + crash_down + rally_back
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_200_000] * len(closes),
        },
        index=idx,
    )


def _short_df(n: int = 30) -> pd.DataFrame:
    """Storia troppo corta — il prefilter deve scartarla."""
    return _bullish_df(n)


# ---------------------------------------------------------------------------
# prefilter_momentum
# ---------------------------------------------------------------------------
def test_prefilter_passes_bullish_ticker():
    """Trend up steady → RSI alto, price sopra EMAs, near 52w-high → PASS."""
    fetch = lambda t: _bullish_df()
    result = prefilter_momentum(["BULL"], fetch_fn=fetch)
    assert len(result) == 1
    assert result[0]["ticker"] == "BULL"
    assert result[0]["rsi"] >= DISCOVERY_PREFILTER_RSI_MIN
    assert result[0]["dist_from_high"] <= DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH
    # EMA fast > slow (alignment momentum)
    assert result[0]["ema_fast"] > result[0]["ema_slow"]


def test_prefilter_rejects_broken_ticker():
    """Crollo finale → price < EMA50 → NO pass."""
    fetch = lambda t: _broken_df()
    result = prefilter_momentum(["BROKEN"], fetch_fn=fetch)
    assert result == []


def test_prefilter_rejects_far_from_high():
    """52w-high lontano (>35%) → NO pass anche se trend up corto."""
    fetch = lambda t: _far_from_high_df()
    result = prefilter_momentum(["FARFH"], fetch_fn=fetch)
    assert result == []


def test_prefilter_rejects_short_history():
    """Ticker con storia < EMA_SLOW + RSI_PERIOD → skip silenzioso."""
    fetch = lambda t: _short_df()
    result = prefilter_momentum(["SHORT"], fetch_fn=fetch)
    assert result == []


def test_prefilter_handles_fetch_failure():
    """fetch_fn ritorna None → ticker skippato senza crash."""
    fetch = lambda t: None
    result = prefilter_momentum(["FAILED"], fetch_fn=fetch)
    assert result == []


def test_prefilter_sorts_by_dist_from_high_ascending():
    """Output sortato per distanza da 52w-high asc (più ready first)."""
    # BULL_NEAR è puro trend up: dist_from_high ~ 0
    df_near = _bullish_df()
    # BULL_PULL: trend up con piccola correzione finale: dist_from_high ~5%
    n = 200
    idx = pd.date_range(end="2026-04-24", periods=n, freq="B")
    rally = [100 + i * 0.5 for i in range(n - 5)]
    pullback = [rally[-1] - (i + 1) * 1.5 for i in range(5)]  # piccolo pullback
    closes = rally + pullback
    df_pull = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )

    fetch_map = {"NEAR": df_near, "PULL": df_pull}
    fetch = lambda t: fetch_map.get(t)
    result = prefilter_momentum(["PULL", "NEAR"], fetch_fn=fetch)

    if len(result) == 2:
        # NEAR (dist ~0) deve venire prima di PULL (dist ~5%)
        assert result[0]["dist_from_high"] <= result[1]["dist_from_high"]


def test_prefilter_respects_custom_thresholds():
    """Soglie più strict → meno candidati."""
    fetch = lambda t: _bullish_df()
    pass_default = prefilter_momentum(["X"], fetch_fn=fetch)
    # Strict max_dist=0 → bullish_df ha price = 52w-high (last bar è il
    # peak) quindi dist = 0; usiamo un threshold negativo (impossibile)
    # per garantire scarto.
    pass_strict = prefilter_momentum(["X"], max_dist_from_high=-0.01, fetch_fn=fetch)
    assert len(pass_default) >= len(pass_strict)
    assert pass_strict == []


def test_prefilter_progress_callback_called():
    """Il callback deve essere invocato per ogni ticker."""
    fetch = lambda t: _bullish_df()
    calls = []
    cb = lambda current, total, ticker: calls.append((current, total, ticker))
    prefilter_momentum(["A", "B", "C"], fetch_fn=fetch, progress_callback=cb)
    assert len(calls) == 3
    assert calls[0] == (1, 3, "A")
    assert calls[2] == (3, 3, "C")


# ---------------------------------------------------------------------------
# discover_momentum_candidates — end-to-end con stage 2 patchato
# ---------------------------------------------------------------------------
def test_discover_returns_summary_dict():
    """Output ha le keys universe_size / prefilter_pass / scored / candidates."""
    fetch = lambda t: _broken_df()  # nessuno passa il prefilter
    out = discover_momentum_candidates(
        ["A", "B", "C"],
        top_n=5,
        fetch_fn=fetch,
    )
    assert out["universe_size"] == 3
    assert out["prefilter_pass"] == 0
    assert out["scored"] == 0
    assert out["candidates"] == []


def test_discover_filters_by_min_score():
    """min_score scarta candidati sotto threshold dopo full scoring."""
    fetch = lambda t: _bullish_df()

    fake_result = {
        "ticker": "FAKE",
        "score_composite": 30.0,  # sotto soglia
        "classification": "D — SKIP",
        "scores": {"trend": 30, "momentum": 30, "volume": 30, "distance_high": 30,
                   "volatility": 30, "ma_cross": 30},
    }
    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        return_value=fake_result,
    ):
        out = discover_momentum_candidates(
            ["FAKE"],
            top_n=5,
            min_score=60.0,
            fetch_fn=fetch,
        )
    assert out["prefilter_pass"] == 1
    assert out["scored"] == 0  # filtrato dal min_score
    assert out["candidates"] == []


def test_discover_respects_top_n():
    """Top N taglia i risultati anche se ci sono più candidati validi."""
    fetch = lambda t: _bullish_df()

    counter = {"i": 0}

    def fake_analyze(ticker, strategy=None):
        counter["i"] += 1
        return {
            "ticker": ticker,
            "score_composite": 90.0 - counter["i"],  # decrescente
            "classification": "A — AZIONE IMMEDIATA",
            "scores": {"trend": 90, "momentum": 80, "volume": 80, "distance_high": 90,
                       "volatility": 80, "ma_cross": 80},
        }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_momentum_candidates(
            [f"T{i}" for i in range(5)],
            top_n=2,
            min_score=0.0,
            fetch_fn=fetch,
        )
    assert len(out["candidates"]) == 2
    # Ranking desc per score
    assert out["candidates"][0]["score_composite"] >= out["candidates"][1]["score_composite"]


def test_discover_prefilter_cap_limits_stage2():
    """prefilter_cap limita il numero di ticker che passano allo stage 2."""
    fetch = lambda t: _bullish_df()

    score_calls = []

    def fake_analyze(ticker, strategy=None):
        score_calls.append(ticker)
        return {
            "ticker": ticker,
            "score_composite": 70.0,
            "classification": "B",
            "scores": {"trend": 70, "momentum": 70, "volume": 70, "distance_high": 70,
                       "volatility": 70, "ma_cross": 70},
        }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_momentum_candidates(
            [f"T{i}" for i in range(10)],
            top_n=5,
            prefilter_cap=3,
            fetch_fn=fetch,
        )
    # Solo 3 hanno raggiunto lo stage 2 grazie al cap
    assert len(score_calls) == 3
    assert out["prefilter_pass"] == 10  # ma il prefilter aveva passato tutti
    assert out["scored"] == 3


def test_discover_handles_stage2_exception():
    """Se analyze_ticker solleva, il discovery NON crasha."""
    fetch = lambda t: _bullish_df()

    def fake_analyze(ticker, strategy=None):
        if ticker == "BAD":
            raise RuntimeError("yfinance timeout")
        return {
            "ticker": ticker,
            "score_composite": 70.0,
            "classification": "B",
            "scores": {"trend": 70, "momentum": 70, "volume": 70, "distance_high": 70,
                       "volatility": 70, "ma_cross": 70},
        }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_momentum_candidates(
            ["GOOD", "BAD"],
            top_n=5,
            fetch_fn=fetch,
        )
    # GOOD è passato, BAD è stato skippato senza crash
    assert out["scored"] == 1
    assert out["candidates"][0]["ticker"] == "GOOD"


def test_discover_passes_strategy_to_analyze():
    """Il flag --strategy deve essere propagato a analyze_ticker."""
    fetch = lambda t: _bullish_df()

    captured = {}

    def fake_analyze(ticker, strategy=None):
        captured["strategy"] = strategy
        return {
            "ticker": ticker,
            "score_composite": 80.0,
            "classification": "A",
            "scores": {"trend": 80, "momentum": 80, "volume": 80, "distance_high": 80,
                       "volatility": 80, "ma_cross": 80},
        }

    with patch(
        "propicks.domain.momentum_discovery.analyze_ticker",
        side_effect=fake_analyze,
    ):
        discover_momentum_candidates(
            ["AAPL"],
            top_n=5,
            strategy="TechTitans",
            fetch_fn=fetch,
        )
    assert captured["strategy"] == "TechTitans"
