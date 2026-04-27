"""Discovery automatico di candidati contrarian su universi ampi (S&P 500).

Pipeline a **3 stadi a costo decrescente** per evitare di pagare 500× la
pipeline completa del scoring contrarian:

1. **Prefilter cheap** (universe → ~30): solo daily history (1 cache lookup
   per ticker), calcolo veloce di RSI(14) + distanza price/EMA50 in ATR.
   No weekly, no regime, no VIX. Threshold più larghi del scoring finale
   per non escludere candidati borderline (RSI < 35, distance ≥ 1×ATR).
2. **Full scoring** (~30 → top N): gira ``analyze_contra_ticker`` solo sui
   sopravvissuti. Aggiunge weekly + EMA200w + regime + reversion R/R.
3. **Ranking + classification**: top N per ``score_composite`` (default 10).
   AI validation resta separata, opt-in via CLI flag.

## Perché stage separati

Lo scoring completo è ~200-400ms per ticker (fetch weekly + indicators +
regime classification). Su S&P 500 = 100-200s totali. Il prefilter è
~5-15ms per ticker (solo daily, cache-hit) = 5-10s totali. Lo stage 1
elimina tipicamente l'80-90% dei nomi (la maggior parte non è oversold)
prima di pagare il costo dello stage 2.

## Tradeoff: false negatives nel prefilter

Soglie del prefilter sono **più larghe** di quelle del scoring finale:
- RSI prefilter < 35 (vs CONTRA_RSI_OVERSOLD=30)
- Distance ≥ 1×ATR (vs CONTRA_ATR_DISTANCE_MIN=2)

Ratio: il prefilter deve massimizzare **recall** (no false negatives), il
scoring finale ottimizza **precision**. Un ticker con RSI=33 e distance=1.5×
ATR non passerà classe A nel scoring finale, ma dobbiamo lasciarlo entrare
per evitare di scartare setup ai bordi del threshold.

## Pure functions

Il modulo è puro **rispetto alla logica**: il prefilter accetta un fetcher
iniettabile (default ``download_history``), così i test possono iniettare
DataFrame sintetici senza rete. La sezione network-bound è esplicitamente
isolata in ``_default_fetch_daily``.
"""

from __future__ import annotations

import sys
from typing import Callable

import pandas as pd

from propicks.config import (
    ATR_PERIOD,
    CONTRA_RSI_WARM,
    EMA_SLOW,
    RSI_PERIOD,
)
from propicks.domain.contrarian_scoring import analyze_contra_ticker
from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi
from propicks.market.yfinance_client import DataUnavailable, download_history


# Soglie prefilter — più larghe del scoring finale per massimizzare recall.
# Vedi docstring del modulo per il razionale.
DISCOVERY_PREFILTER_RSI_MAX: float = CONTRA_RSI_WARM  # 35
DISCOVERY_PREFILTER_ATR_DISTANCE_MIN: float = 1.0     # vs CONTRA_ATR_DISTANCE_MIN=2.0

# Default top-N risultati dopo full scoring.
DISCOVERY_DEFAULT_TOP_N: int = 10


# ---------------------------------------------------------------------------
# Stage 1: Prefilter cheap
# ---------------------------------------------------------------------------
def _default_fetch_daily(ticker: str) -> pd.DataFrame | None:
    """Fetch daily history. Network-bound; isolato per testabilità."""
    try:
        return download_history(ticker)
    except DataUnavailable:
        return None
    except Exception:
        # Robustness: il discovery non deve crashare per 1 ticker bad data
        return None


def prefilter_oversold(
    universe: list[str],
    *,
    rsi_max: float = DISCOVERY_PREFILTER_RSI_MAX,
    atr_distance_min: float = DISCOVERY_PREFILTER_ATR_DISTANCE_MIN,
    fetch_fn: Callable[[str], pd.DataFrame | None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Stadio 1: filter veloce su daily history per RSI + distance EMA50.

    Args:
        universe: lista ticker da scansionare.
        rsi_max: RSI massimo per qualificare (default 35 — più permissivo
            del CONTRA_RSI_OVERSOLD=30 per non perdere setup borderline).
        atr_distance_min: distanza minima da EMA50 in multipli di ATR
            (default 1.0 — più permissivo del CONTRA_ATR_DISTANCE_MIN=2.0).
        fetch_fn: iniettabile per test. Default: ``download_history``.
        progress_callback: callback(current_idx, total, ticker) per UI/logging.

    Returns:
        list[dict] con keys ``{ticker, rsi, atr_distance, price, ema_slow}``
        per i ticker che superano il prefilter. Ordinato per RSI ascendente
        (più oversold first).
    """
    fetch = fetch_fn or _default_fetch_daily
    candidates: list[dict] = []
    total = len(universe)

    for idx, ticker in enumerate(universe):
        if progress_callback:
            progress_callback(idx + 1, total, ticker)

        hist = fetch(ticker)
        if hist is None or hist.empty or len(hist) < EMA_SLOW + RSI_PERIOD:
            continue

        try:
            close = hist["Close"]
            high = hist["High"]
            low = hist["Low"]
            ema_slow = float(compute_ema(close, EMA_SLOW).iloc[-1])
            rsi = float(compute_rsi(close, RSI_PERIOD).iloc[-1])
            atr = float(compute_atr(high, low, close, ATR_PERIOD).iloc[-1])
            price = float(close.iloc[-1])
        except (KeyError, ValueError, IndexError):
            continue

        if pd.isna(rsi) or pd.isna(atr) or atr <= 0 or pd.isna(ema_slow):
            continue

        # RSI gate
        if rsi > rsi_max:
            continue

        # Distance gate (positive = price below EMA50)
        atr_distance = (ema_slow - price) / atr
        if atr_distance < atr_distance_min:
            continue

        candidates.append({
            "ticker": ticker,
            "rsi": round(rsi, 2),
            "atr_distance": round(atr_distance, 2),
            "price": round(price, 2),
            "ema_slow": round(ema_slow, 2),
        })

    # Sort: più oversold (RSI più basso) first — utile per il caller che
    # vuole cap top-N per limitare anche il costo dello stage 2.
    candidates.sort(key=lambda x: x["rsi"])
    return candidates


# ---------------------------------------------------------------------------
# Stage 2 + 3: Full scoring + ranking
# ---------------------------------------------------------------------------
def discover_contra_candidates(
    universe: list[str],
    *,
    top_n: int = DISCOVERY_DEFAULT_TOP_N,
    rsi_max: float = DISCOVERY_PREFILTER_RSI_MAX,
    atr_distance_min: float = DISCOVERY_PREFILTER_ATR_DISTANCE_MIN,
    min_score: float = 0.0,
    vix: float | None = None,
    prefilter_cap: int | None = None,
    fetch_fn: Callable[[str], pd.DataFrame | None] | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict:
    """Pipeline completa: prefilter → full scoring → ranking top N.

    Args:
        universe: lista ticker (es. da ``get_sp500_universe()``).
        top_n: massimo numero di candidati da ritornare dopo full scoring.
        rsi_max: soglia RSI prefilter.
        atr_distance_min: soglia distanza ATR prefilter.
        min_score: ``score_composite`` minimo per inclusione (default 0).
            Usa es. 60 per filtrare solo classe A+B.
        vix: VIX corrente (iniettabile per batch — evita N download).
        prefilter_cap: se non None, taglia i candidati prefilter a questo
            numero prima dello stage 2. Utile per limitare costo full
            scoring quando il prefilter passa molti nomi (regime BEAR
            con washout: 50+ candidati).
        fetch_fn: iniettabile per test prefilter.
        progress_callback: ``cb(stage, current, total, ticker)`` con
            ``stage`` in ``{"prefilter", "scoring"}``.

    Returns:
        dict con keys:
        - ``universe_size``: int (input universe size)
        - ``prefilter_pass``: int (n. ticker dopo stage 1)
        - ``scored``: int (n. ticker passati allo stage 2)
        - ``candidates``: list[dict] (top N analysis dict ranked desc by score)
    """
    # Stage 1: prefilter cheap
    def _stage1_cb(idx: int, total: int, ticker: str) -> None:
        if progress_callback:
            progress_callback("prefilter", idx, total, ticker)

    prefiltered = prefilter_oversold(
        universe,
        rsi_max=rsi_max,
        atr_distance_min=atr_distance_min,
        fetch_fn=fetch_fn,
        progress_callback=_stage1_cb,
    )

    # Cap opzionale prima dello stage 2 (per limitare costo)
    to_score = prefiltered
    if prefilter_cap is not None and len(prefiltered) > prefilter_cap:
        to_score = prefiltered[:prefilter_cap]

    # Stage 2: full scoring sui sopravvissuti
    analyzed: list[dict] = []
    for idx, candidate in enumerate(to_score):
        if progress_callback:
            progress_callback("scoring", idx + 1, len(to_score), candidate["ticker"])
        try:
            result = analyze_contra_ticker(
                candidate["ticker"],
                strategy="Contrarian",
                vix=vix,
            )
        except Exception as exc:
            print(
                f"[discovery] errore su {candidate['ticker']}: {exc}",
                file=sys.stderr,
            )
            continue
        if result is None:
            continue
        if result.get("score_composite", 0) < min_score:
            continue
        analyzed.append(result)

    # Stage 3: ranking by composite desc, top N
    analyzed.sort(key=lambda x: x.get("score_composite", 0), reverse=True)
    top = analyzed[:top_n]

    return {
        "universe_size": len(universe),
        "prefilter_pass": len(prefiltered),
        "scored": len(analyzed),
        "candidates": top,
    }
