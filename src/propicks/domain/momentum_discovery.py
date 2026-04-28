"""Discovery automatico di candidati MOMENTUM su universi ampi (S&P 500,
FTSE MIB, STOXX 600).

Pipeline a **3 stadi a costo decrescente** parallela a
``contrarian_discovery``: lo stage 1 elimina la maggior parte del rumore con
controlli su daily history (trend basico + RSI alive + within 52w-high
range), così lo stage 2 (full ``analyze_ticker`` con weekly + regime + RS
settoriale) viene pagato solo sui sopravvissuti.

1. **Prefilter cheap** (universe → ~30-100): solo daily history (1 cache
   lookup per ticker). Filtra:
   - ``Close > EMA_SLOW`` — trend primario in essere (no titoli rotti)
   - ``EMA_FAST > EMA_SLOW`` — alignment momentum (no titoli reversal)
   - ``RSI ≥ DISCOVERY_PREFILTER_RSI_MIN`` — momentum vivo (no morti tecnici)
   - ``distance_from_52w_high ≤ DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH`` —
     entro range del sweet-spot 5-10% sotto ATH
   No weekly, no regime, no RS settoriale — quelli costano e si pagano
   solo sui sopravvissuti.
2. **Full scoring** (~30 → top N): gira ``analyze_ticker`` solo sui
   sopravvissuti. Aggiunge weekly + regime + RS settoriale.
3. **Ranking + classification**: top N per ``score_composite`` (default 10),
   filtrato opzionalmente da ``min_score`` (default ``MIN_SCORE_TECH=60``
   per filtrare classe C/D).

## Perché stage separati

Lo scoring completo è ~200-400ms per ticker (fetch weekly + RS + indicators
+ regime classification). Su S&P 500 = 100-200s totali, su STOXX 600 ~300s.
Il prefilter è ~5-15ms per ticker (solo daily, cache-hit) = 5-10s totali.
Lo stage 1 elimina tipicamente l'80%+ dei nomi (la maggior parte non è in
trend up con momentum vivo) prima di pagare il costo dello stage 2.

## Tradeoff: false negatives nel prefilter

Soglie del prefilter sono **più larghe** di quelle del scoring finale:
- RSI prefilter ≥ 45 (vs sweet-spot 50-65 nel score finale: lasciamo
  passare anche RSI 45-50 che daranno score momentum ~60 ma potrebbero
  comunque qualificare in classe B/A se trend + volume + dist_high sono
  forti).
- Distance from 52w high ≤ 35% (vs peak score a 7.5%: lasciamo passare
  anche titoli a 25-30% di distanza che potrebbero comunque scorare 50+
  se gli altri sub-score sono buoni).

Ratio: il prefilter deve massimizzare **recall** (no false negatives), il
scoring finale ottimizza **precision**.

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
    EMA_FAST,
    EMA_SLOW,
    MIN_SCORE_TECH,
    RSI_PERIOD,
)
from propicks.domain.indicators import compute_ema, compute_rsi
from propicks.domain.scoring import analyze_ticker
from propicks.market.yfinance_client import DataUnavailable, download_history


# Soglie prefilter — più larghe del scoring finale per massimizzare recall.
# Vedi docstring del modulo per il razionale.
DISCOVERY_PREFILTER_RSI_MIN: float = 45.0
DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH: float = 0.35

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


def prefilter_momentum(
    universe: list[str],
    *,
    rsi_min: float = DISCOVERY_PREFILTER_RSI_MIN,
    max_dist_from_high: float = DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    fetch_fn: Callable[[str], pd.DataFrame | None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Stadio 1: filter veloce su daily history per trend + momentum + range.

    Gates (tutti devono passare):
    - ``Close > EMA_SLOW``: trend primario non rotto
    - ``EMA_FAST > EMA_SLOW``: alignment di momentum (no titoli in reversal)
    - ``RSI ≥ rsi_min``: momentum vivo
    - distanza da 52w-high ``≤ max_dist_from_high``: dentro range del
      sweet-spot di scoring

    Args:
        universe: lista ticker da scansionare.
        rsi_min: RSI minimo per qualificare (default 45 — più permissivo
            del sweet-spot 50-65 del score finale per non perdere setup
            borderline).
        max_dist_from_high: distanza massima da 52w-high (frazione, default
            0.35 = 35% — più permissivo del peak 7.5% del score finale).
        fetch_fn: iniettabile per test. Default: ``download_history``.
        progress_callback: callback(current_idx, total, ticker) per UI/logging.

    Returns:
        list[dict] con keys
        ``{ticker, rsi, dist_from_high, price, ema_fast, ema_slow}`` per
        i ticker che superano il prefilter. Ordinato per ``dist_from_high``
        ascendente (più vicini all'ATH first — i candidati più "ready").
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
            ema_fast = float(compute_ema(close, EMA_FAST).iloc[-1])
            ema_slow = float(compute_ema(close, EMA_SLOW).iloc[-1])
            rsi = float(compute_rsi(close, RSI_PERIOD).iloc[-1])
            price = float(close.iloc[-1])
            high_52w = float(high.tail(min(252, len(high))).max())
        except (KeyError, ValueError, IndexError):
            continue

        if (
            pd.isna(price)
            or pd.isna(ema_fast)
            or pd.isna(ema_slow)
            or pd.isna(rsi)
            or high_52w <= 0
        ):
            continue

        # Trend gate: price sopra EMA50
        if price <= ema_slow:
            continue

        # Alignment gate: EMA20 > EMA50 (momentum non in reversal)
        if ema_fast <= ema_slow:
            continue

        # Momentum gate: RSI vivo
        if rsi < rsi_min:
            continue

        # Range gate: dentro la zona del sweet-spot scoring
        dist_from_high = (high_52w - price) / high_52w
        if dist_from_high > max_dist_from_high:
            continue

        candidates.append({
            "ticker": ticker,
            "rsi": round(rsi, 2),
            "dist_from_high": round(dist_from_high, 4),
            "price": round(price, 2),
            "ema_fast": round(ema_fast, 2),
            "ema_slow": round(ema_slow, 2),
        })

    # Sort: i più vicini all'ATH first — utile per il caller che vuole
    # cap top-N per limitare anche il costo dello stage 2 mantenendo i
    # candidati con maggiore probabilità di scorare alto sul sub-score
    # distance_from_high.
    candidates.sort(key=lambda x: x["dist_from_high"])
    return candidates


# ---------------------------------------------------------------------------
# Stage 2 + 3: Full scoring + ranking
# ---------------------------------------------------------------------------
def discover_momentum_candidates(
    universe: list[str],
    *,
    top_n: int = DISCOVERY_DEFAULT_TOP_N,
    rsi_min: float = DISCOVERY_PREFILTER_RSI_MIN,
    max_dist_from_high: float = DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    min_score: float = float(MIN_SCORE_TECH),
    strategy: str | None = None,
    prefilter_cap: int | None = None,
    fetch_fn: Callable[[str], pd.DataFrame | None] | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict:
    """Pipeline completa: prefilter → full scoring → ranking top N.

    Args:
        universe: lista ticker (es. da ``get_index_universe("sp500")``).
        top_n: massimo numero di candidati da ritornare dopo full scoring.
        rsi_min: soglia RSI prefilter.
        max_dist_from_high: soglia max distance from 52w-high prefilter.
        min_score: ``score_composite`` minimo per inclusione (default
            ``MIN_SCORE_TECH=60`` per filtrare classe C/D — il discovery di
            default ritorna solo classe A+B). Usa 0 per nessun filtro, 75
            per solo classe A.
        strategy: tag strategy passato a ``analyze_ticker`` (es.
            ``"TechTitans"``). Default: None.
        prefilter_cap: se non None, taglia i candidati prefilter a questo
            numero prima dello stage 2. Utile per limitare costo full
            scoring quando il prefilter passa molti nomi (regime BULL
            ampio: 100+ candidati su S&P 500).
        fetch_fn: iniettabile per test prefilter.
        progress_callback: ``cb(stage, current, total, ticker)`` con
            ``stage`` in ``{"prefilter", "scoring"}``.

    Returns:
        dict con keys:
        - ``universe_size``: int (input universe size)
        - ``prefilter_pass``: int (n. ticker dopo stage 1)
        - ``scored``: int (n. ticker passati allo stage 2 + filtro min_score)
        - ``candidates``: list[dict] (top N analysis dict ranked desc by score)
    """
    # Stage 1: prefilter cheap
    def _stage1_cb(idx: int, total: int, ticker: str) -> None:
        if progress_callback:
            progress_callback("prefilter", idx, total, ticker)

    prefiltered = prefilter_momentum(
        universe,
        rsi_min=rsi_min,
        max_dist_from_high=max_dist_from_high,
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
            result = analyze_ticker(candidate["ticker"], strategy=strategy)
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
