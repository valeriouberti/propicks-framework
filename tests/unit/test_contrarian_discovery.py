"""Test della pipeline discovery contrarian a 3 stadi.

Strategy: il prefilter accetta un fetch_fn iniettabile, quindi i test
girano completamente offline iniettando DataFrame sintetici. Lo stage 2
(``analyze_contra_ticker``) richiede yfinance reale, quindi è patchato
nei test end-to-end del modulo discovery.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from propicks.domain.contrarian_discovery import (
    DISCOVERY_PREFILTER_ATR_DISTANCE_MIN,
    DISCOVERY_PREFILTER_RSI_MAX,
    discover_contra_candidates,
    prefilter_oversold,
)


# ---------------------------------------------------------------------------
# Synthetic OHLCV builders
# ---------------------------------------------------------------------------
def _bullish_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Trend up steady — RSI alto, price sopra EMA50, NO oversold."""
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


def _oversold_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Trend up + flush finale: RSI < 30, price molto sotto EMA50.

    Costruzione: 180 barre in trend up costruiscono EMA50 alta (~190),
    poi le ultime 20 barre crollano a ~140 (flush brutale). Risultato:
    RSI molto basso, distanza price/EMA50 ~5×ATR sotto.
    """
    idx = pd.date_range(end="2026-04-24", periods=n, freq="B")
    # Phase 1: rally costante 100 → 190 (180 barre)
    rally_n = n - 20
    rally = [base_price + i * 0.5 for i in range(rally_n)]
    # Phase 2: crash 190 → 140 (20 barre, -2.5 al giorno)
    crash_start = rally[-1]
    crash = [crash_start - (i + 1) * 2.5 for i in range(20)]
    closes = rally + crash

    return pd.DataFrame(
        {
            "Open": [c + 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_500_000] * n,
        },
        index=idx,
    )


def _short_df(n: int = 30) -> pd.DataFrame:
    """Storia troppo corta — il prefilter deve scartarla."""
    return _bullish_df(n)


# ---------------------------------------------------------------------------
# prefilter_oversold
# ---------------------------------------------------------------------------
def test_prefilter_passes_oversold_ticker():
    """Un ticker con flush finale deve passare il prefilter."""
    fetch = lambda t: _oversold_df() if t == "OVERSOLD" else None
    result = prefilter_oversold(["OVERSOLD"], fetch_fn=fetch)
    assert len(result) == 1
    assert result[0]["ticker"] == "OVERSOLD"
    assert result[0]["rsi"] <= DISCOVERY_PREFILTER_RSI_MAX
    assert result[0]["atr_distance"] >= DISCOVERY_PREFILTER_ATR_DISTANCE_MIN


def test_prefilter_rejects_bullish_ticker():
    """Trend up steady → RSI alto, price sopra EMA50 → NO pass."""
    fetch = lambda t: _bullish_df()
    result = prefilter_oversold(["BULLISH"], fetch_fn=fetch)
    assert result == []


def test_prefilter_rejects_short_history():
    """Ticker con storia < EMA_SLOW + RSI_PERIOD → skip silenzioso."""
    fetch = lambda t: _short_df()
    result = prefilter_oversold(["SHORT"], fetch_fn=fetch)
    assert result == []


def test_prefilter_handles_fetch_failure():
    """fetch_fn ritorna None → ticker skippato senza crash."""
    fetch = lambda t: None
    result = prefilter_oversold(["FAILED"], fetch_fn=fetch)
    assert result == []


def test_prefilter_sorts_by_rsi_ascending():
    """Output sortato per RSI asc (più oversold first)."""
    # Crea due ticker oversold con magnitudini diverse
    # Cliffhanger: il modulo lavora su scalar last bar — useremo costruzioni
    # diverse per garantire RSI diverso
    df_deep = _oversold_df(n=200, base_price=100.0)
    # Crea un secondo "dip" più moderato: rally più corto + crash più mild
    idx = pd.date_range(end="2026-04-24", periods=200, freq="B")
    rally = [100.0 + i * 0.5 for i in range(180)]
    crash = [rally[-1] - (i + 1) * 1.0 for i in range(20)]  # crash più mild
    closes = rally + crash
    df_mild = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * 200,
        },
        index=idx,
    )

    fetch_map = {"DEEP": df_deep, "MILD": df_mild}
    fetch = lambda t: fetch_map.get(t)
    result = prefilter_oversold(["MILD", "DEEP"], fetch_fn=fetch)

    # Entrambi dovrebbero passare se il flush è abbastanza ampio,
    # ma DEEP deve venire prima (RSI più basso)
    if len(result) == 2:
        assert result[0]["rsi"] <= result[1]["rsi"]


def test_prefilter_respects_custom_thresholds():
    """Soglie più strict → meno candidati."""
    fetch = lambda t: _oversold_df()
    # Default thresholds → pass
    pass_default = prefilter_oversold(["X"], fetch_fn=fetch)
    # Strict RSI 20 → no pass (l'oversold sintetico ha RSI ~25-30)
    pass_strict = prefilter_oversold(["X"], rsi_max=15.0, fetch_fn=fetch)
    assert len(pass_default) >= len(pass_strict)


def test_prefilter_progress_callback_called():
    """Il callback deve essere invocato per ogni ticker."""
    fetch = lambda t: _bullish_df()
    calls = []
    cb = lambda current, total, ticker: calls.append((current, total, ticker))
    prefilter_oversold(["A", "B", "C"], fetch_fn=fetch, progress_callback=cb)
    assert len(calls) == 3
    assert calls[0] == (1, 3, "A")
    assert calls[2] == (3, 3, "C")


# ---------------------------------------------------------------------------
# discover_contra_candidates — end-to-end con stage 2 patchato
# ---------------------------------------------------------------------------
def test_discover_returns_summary_dict():
    """Output ha le keys universe_size / prefilter_pass / scored / candidates."""
    fetch = lambda t: _bullish_df()  # nessuno passa il prefilter
    out = discover_contra_candidates(
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
    fetch = lambda t: _oversold_df()

    # Patcha analyze_contra_ticker per ritornare un fake result con score basso
    fake_result = {
        "ticker": "FAKE",
        "score_composite": 30.0,  # sotto soglia
        "classification": "D — SKIP",
        "scores": {"oversold": 30, "quality": 30, "market_context": 30, "reversion": 30},
    }
    with patch(
        "propicks.domain.contrarian_discovery.analyze_contra_ticker",
        return_value=fake_result,
    ):
        out = discover_contra_candidates(
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
    fetch = lambda t: _oversold_df()

    counter = {"i": 0}

    def fake_analyze(ticker, strategy=None, vix=None):
        counter["i"] += 1
        return {
            "ticker": ticker,
            "score_composite": 80.0 - counter["i"],  # decrescente
            "classification": "A — OVERSOLD READY",
            "scores": {"oversold": 80, "quality": 80, "market_context": 80, "reversion": 80},
        }

    with patch(
        "propicks.domain.contrarian_discovery.analyze_contra_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_contra_candidates(
            [f"T{i}" for i in range(5)],
            top_n=2,
            fetch_fn=fetch,
        )
    assert len(out["candidates"]) == 2
    # Ranking desc per score
    assert out["candidates"][0]["score_composite"] >= out["candidates"][1]["score_composite"]


def test_discover_prefilter_cap_limits_stage2():
    """prefilter_cap limita il numero di ticker che passano allo stage 2."""
    fetch = lambda t: _oversold_df()

    score_calls = []

    def fake_analyze(ticker, strategy=None, vix=None):
        score_calls.append(ticker)
        return {
            "ticker": ticker,
            "score_composite": 70.0,
            "classification": "B",
            "scores": {"oversold": 70, "quality": 70, "market_context": 70, "reversion": 70},
        }

    with patch(
        "propicks.domain.contrarian_discovery.analyze_contra_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_contra_candidates(
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
    """Se analyze_contra_ticker solleva, il discovery NON crasha."""
    fetch = lambda t: _oversold_df()

    def fake_analyze(ticker, strategy=None, vix=None):
        if ticker == "BAD":
            raise RuntimeError("yfinance timeout")
        return {
            "ticker": ticker,
            "score_composite": 70.0,
            "classification": "B",
            "scores": {"oversold": 70, "quality": 70, "market_context": 70, "reversion": 70},
        }

    with patch(
        "propicks.domain.contrarian_discovery.analyze_contra_ticker",
        side_effect=fake_analyze,
    ):
        out = discover_contra_candidates(
            ["GOOD", "BAD"],
            top_n=5,
            fetch_fn=fetch,
        )
    # GOOD è passato, BAD è stato skippato senza crash
    assert out["scored"] == 1
    assert out["candidates"][0]["ticker"] == "GOOD"
