"""Scoring tecnico 0-100 per singoli ticker.

Sei sotto-score indipendenti pesati dalle costanti WEIGHT_* in config.
Ogni funzione ``score_*`` è pura: riceve numeri, ritorna un float 0-100.

``analyze_ticker`` orchestra download → calcolo indicatori → scoring e
ritorna un dict pronto per la CLI o la API.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from propicks.config import (
    ATR_PERIOD,
    EMA_FAST,
    EMA_SLOW,
    MIN_SCORE_TECH,
    RSI_PERIOD,
    VOLUME_AVG_PERIOD,
    WEIGHT_DISTANCE_HIGH,
    WEIGHT_MA_CROSS,
    WEIGHT_MOMENTUM,
    WEIGHT_TREND,
    WEIGHT_VOLATILITY,
    WEIGHT_VOLUME,
)
from propicks.domain.calendar import days_to_earnings as _days_to_earnings
from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi, pct_change
from propicks.domain.regime import classify_regime
from propicks.domain.stock_rs import (
    is_us_ticker,
    peer_etf_for,
    score_rs_vs_sector,
)
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_benchmark_weekly,
    download_history,
    download_weekly_history,
    get_next_earnings_date,
    get_ticker_sector,
)


# ---------------------------------------------------------------------------
# Sub-score: ognuno ritorna un float 0-100
# ---------------------------------------------------------------------------
def score_trend(close: float, ema_fast: float, ema_slow: float) -> float:
    if any(pd.isna(x) for x in (close, ema_fast, ema_slow)):
        return 0.0
    above_fast = close > ema_fast
    above_slow = close > ema_slow
    fast_above_slow = ema_fast > ema_slow

    if above_fast and above_slow and fast_above_slow:
        return 100.0
    if above_fast and above_slow:
        return 80.0
    if above_fast and not above_slow:
        return 60.0
    if not above_fast and above_slow:
        return 40.0
    if not fast_above_slow:
        return 0.0
    return 20.0


def score_momentum(rsi: float) -> float:
    if pd.isna(rsi):
        return 0.0
    if 50 <= rsi <= 65:
        return 100.0
    if 65 < rsi <= 70:
        return 75.0
    if 40 <= rsi < 50:
        return 60.0
    if 30 <= rsi < 40:
        return 45.0
    if 70 < rsi <= 80:
        return 40.0
    if rsi < 30:
        return 20.0
    return 15.0


def score_volume(
    current_volume: float,
    avg_volume: float,
    direction: float | None = None,
) -> float:
    """Score volume relativo, asimmetrico per direzione del prezzo.

    ``direction`` è il segno del movimento giornaliero (close - prev_close,
    o equivalente). Quando passato, volume alto su up-day = conviction, su
    down-day = distribuzione/panic e viene penalizzato. Senza direction
    (default) il comportamento è simmetrico (back-compat).

    Calibrazione intuitiva: ratio 5× su breakout green-day = high conviction
    breakout (score 100), su capitulation red-day = panic selling (score 15).
    Lo scoring vecchio trattava entrambi a 60 — segnale ambiguo.
    """
    if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume <= 0:
        return 50.0
    ratio = current_volume / avg_volume

    if 1.2 <= ratio < 2.0:
        base = 100.0
    elif 2.0 <= ratio < 3.0:
        base = 80.0
    elif 1.0 <= ratio < 1.2:
        base = 70.0
    elif ratio >= 3.0:
        base = 60.0
    elif 0.7 <= ratio < 1.0:
        base = 50.0
    elif 0.5 <= ratio < 0.7:
        base = 30.0
    else:
        base = 15.0

    if direction is None or ratio < 1.2:
        return base

    if direction > 0:
        if ratio >= 3.0:
            return 100.0
        return base
    if direction < 0:
        if 1.2 <= ratio < 2.0:
            return 35.0
        if 2.0 <= ratio < 3.0:
            return 25.0
        return 15.0
    return base


_DIST_FROM_HIGH_X = (0.00, 0.03, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25, 0.30, 0.35, 0.50)
_DIST_FROM_HIGH_Y = (75.0, 75.0, 85.0, 100.0, 95.0, 90.0, 80.0, 65.0, 50.0, 40.0, 30.0, 10.0)


def score_distance_from_high(close: float, high_52w: float) -> float:
    """Score sweet-spot 5-10% sotto l'ATH (peak a ~7.5% dal massimo).

    Interpolazione lineare a tratti tra i control point (vedi
    ``_DIST_FROM_HIGH_X``/``_DIST_FROM_HIGH_Y``). Smussa i jump dei vecchi
    tier discreti che facevano oscillare la classificazione A/B su titoli
    che attraversavano i boundary giornalmente (es. dist 0.099 vs 0.101 →
    100 vs 80, swing di 3 pt sul composite).
    """
    if pd.isna(close) or pd.isna(high_52w) or high_52w <= 0:
        return 0.0
    dist = (high_52w - close) / high_52w
    return float(np.interp(dist, _DIST_FROM_HIGH_X, _DIST_FROM_HIGH_Y))


def score_volatility(atr: float, close: float) -> float:
    if pd.isna(atr) or pd.isna(close) or close <= 0:
        return 0.0
    atr_pct = atr / close
    if 0.01 <= atr_pct < 0.03:
        return 100.0
    if 0.03 <= atr_pct <= 0.05:
        return 70.0
    if 0.005 <= atr_pct < 0.01:
        return 60.0
    if atr_pct < 0.005:
        return 40.0
    return 30.0


def score_ma_cross(
    ema_fast: float,
    ema_slow: float,
    prev_ema_fast: float,
    prev_ema_slow: float,
) -> float:
    values = (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow)
    if any(pd.isna(x) for x in values) or ema_slow <= 0 or prev_ema_slow <= 0:
        return 0.0

    now_fast_above = ema_fast > ema_slow
    prev_fast_above = prev_ema_fast > prev_ema_slow
    spread = abs(ema_fast - ema_slow) / ema_slow

    if now_fast_above and not prev_fast_above:
        return 100.0
    if not now_fast_above and prev_fast_above:
        return 5.0

    if now_fast_above:
        if spread > 0.02:
            return 80.0
        return 70.0

    if spread > 0.02:
        return 15.0
    return 30.0


# ---------------------------------------------------------------------------
# Classificazione
# ---------------------------------------------------------------------------
def classify(score: float) -> str:
    if score >= 75:
        return "A — AZIONE IMMEDIATA"
    if score >= MIN_SCORE_TECH:
        return "B — WATCHLIST"
    if score >= 45:
        return "C — NEUTRALE"
    return "D — SKIP"


# ---------------------------------------------------------------------------
# Multi-lookback momentum (Fase C.6 SIGNAL_ROADMAP)
# ---------------------------------------------------------------------------
def score_multi_lookback_momentum(avg_log_return: float | None) -> float:
    """Score [0, 100] da average log-return ensemble multi-lookback.

    Mappatura: log-return 0 → 50, +0.20 (~+22% media) → 100, -0.20 → 0.
    Saturazione lineare ±0.20 (≈ ±22% media annua su lookback misti).

    Args:
        avg_log_return: output di ``indicators.compute_multi_lookback_momentum``.

    Returns:
        Float [0, 100]. 50 se input None.
    """
    if avg_log_return is None:
        return 50.0
    val = float(avg_log_return)
    saturated = max(-0.20, min(0.20, val))
    return max(0.0, min(100.0, 50.0 + saturated * 250.0))


# ---------------------------------------------------------------------------
# Earnings revision overlay (Fase B.2 SIGNAL_ROADMAP)
# ---------------------------------------------------------------------------
def combine_with_earnings_revision(
    base_score: float,
    earnings_score: float | None,
    *,
    weight: float = 0.20,
) -> float:
    """Combina composite score classic con earnings revision score.

    Pattern overlay non-breaking: il classic composite (6 sub-score) resta
    invariato in produzione (Pine sync, signal validation). Questo overlay
    è puro additivo, attivabile via flag config / CLI flag.

    Formula:
        if earnings_score is None: return base_score (no signal — neutral)
        else: return base_score * (1 - weight) + earnings_score * weight

    Args:
        base_score: composite score classico [0, 100].
        earnings_score: score earnings revision [0, 100], None se feature
            non disponibile per ticker (es. ETF, IPO recente).
        weight: peso earnings overlay. Default 0.20 = 20% earnings + 80%
            classic. Range [0, 1]. SIGNAL_ROADMAP B.2 raccomanda 0.15-0.20.

    Returns:
        Composite combined [0, 100].
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight {weight} must be in [0, 1]")
    if earnings_score is None:
        return base_score
    if not (0.0 <= earnings_score <= 100.0):
        # Out-of-range → no contribution (defensive)
        return base_score
    return base_score * (1.0 - weight) + earnings_score * weight


# ---------------------------------------------------------------------------
# Cross-sectional ranking (Fase B.1 SIGNAL_ROADMAP)
# ---------------------------------------------------------------------------
def auto_percentile_for_universe(
    universe_size: int,
    *,
    target_n_winners: int = 6,
    min_pct: float = 50.0,
    max_pct: float = 90.0,
) -> float:
    """Tuna percentile threshold cross-sectional in funzione dell'universe size.

    Razionale (Fase C.0 SIGNAL_ROADMAP): B.6 ha mostrato che P80 fixed scala
    male — top 30 OK (Sharpe 0.62), top 50 collassa (Sharpe 0.07). P80 con
    50 ticker = top 10 ticker, ma combined con max_positions cap si finisce
    su 5-6 ticker correlati = concentration risk + diversification persa.

    Strategia auto-tuning: tieni il numero atteso di "winners" stabile
    (default 6 ticker) variando il percentile con la dimensione universe.

        winners ≈ universe_size × (1 - pct/100)
        pct = 100 × (1 - target_n_winners / universe_size)

    Args:
        universe_size: ticker totali eligibili nell'universe.
        target_n_winners: numero target di ticker sopra threshold (default 6
            = ~max_positions configurato in BacktestConfig).
        min_pct, max_pct: clamp range. min 50 (top half) prevent overfit.

    Returns:
        Percentile [min_pct, max_pct] auto-tuned.

    Examples:
        >>> auto_percentile_for_universe(30)   # 30 ticker → P80
        80.0
        >>> auto_percentile_for_universe(60)   # 60 ticker → P90 (clamp)
        90.0
        >>> auto_percentile_for_universe(100)  # 100 ticker → P94→P90 (clamp)
        90.0
        >>> auto_percentile_for_universe(20)   # 20 ticker → P70
        70.0
        >>> auto_percentile_for_universe(10)   # 10 ticker → P40→P50 (clamp)
        50.0
    """
    if universe_size < 2:
        return min_pct
    raw = 100.0 * (1.0 - target_n_winners / universe_size)
    return max(min_pct, min(max_pct, raw))


def rank_universe(scores: dict[str, float]) -> dict[str, float]:
    """Converte score assoluti in percentile rank [0, 100] cross-sectional.

    Razionale (Jegadeesh-Titman 1993): l'edge momentum classico è top quintile
    vs bottom quintile, non score assoluto. Score 65 in un BULL market dove la
    media universe è 70 = sotto-mediana. Score 65 in BEAR dove la media è 40 =
    top quintile. ``rank_universe`` rende il threshold relativo allo stato del
    mercato, non assoluto.

    Args:
        scores: dict {ticker: score 0-100}. Score può venire da
            ``analyze_ticker``, ``analyze_contra_ticker``, ecc.

    Returns:
        Dict {ticker: percentile_rank 0-100} dove 100 = miglior score
        nell'universo, 0 = peggiore. Tie handling: average rank
        ("rankdata method='average'").

    Edge cases:
        - dict vuoto → {}
        - 1 elemento → {ticker: 50.0}
        - tutti score uguali → tutti a 50.0
        - NaN nei score: trattati come -inf (rank 0)

    Convention:
        Compatible con threshold percentile: ``rank >= 80`` = top quintile
        (P80+, top 20% dell'universe). Threshold absolute (60) e percentile
        (60) hanno semantica diversa — chi usa rank_universe deve adeguare
        soglie (vedi BacktestConfig.use_cross_sectional_rank).
    """
    if not scores:
        return {}
    if len(scores) == 1:
        return {next(iter(scores)): 50.0}

    import math
    items = list(scores.items())
    # Sostituisci NaN con -inf per rankizzazione consistente
    cleaned = [
        (t, s if (s is not None and isinstance(s, (int, float)) and not math.isnan(s))
         else float("-inf"))
        for t, s in items
    ]
    # Sort ASC per score; rank corrispondente è index+1
    sorted_items = sorted(cleaned, key=lambda x: x[1])
    n = len(sorted_items)

    # Calcola rank con tie-handling 'average'
    ranks_by_ticker: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        # Trova run di ticker con score uguale
        while j + 1 < n and sorted_items[j + 1][1] == sorted_items[i][1]:
            j += 1
        # Rank medio (1-indexed) per il run [i, j]
        avg_rank = (i + j + 2) / 2  # ((i+1) + (j+1)) / 2
        for k in range(i, j + 1):
            ranks_by_ticker[sorted_items[k][0]] = avg_rank
        i = j + 1

    # Normalizza rank → percentile [0, 100]: rank 1 = pct 0, rank n = pct 100
    if n == 1:
        return {next(iter(ranks_by_ticker)): 50.0}
    out: dict[str, float] = {}
    for ticker, rank in ranks_by_ticker.items():
        pct = (rank - 1) / (n - 1) * 100.0
        out[ticker] = round(pct, 4)
    return out


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------
def analyze_ticker(ticker: str, strategy: str | None = None) -> dict | None:
    """Analizza un ticker e ritorna il dict completo di score.

    In caso di errore (ticker non trovato, timeout, dati vuoti) stampa un
    warning su stderr e ritorna None.
    """
    ticker = ticker.upper()
    try:
        hist = download_history(ticker)
    except DataUnavailable as err:
        print(f"[errore] {err}", file=sys.stderr)
        return None

    regime: dict | None = None
    weekly: pd.DataFrame | None = None
    try:
        weekly = download_weekly_history(ticker)
        regime = classify_regime(weekly)
    except DataUnavailable as err:
        print(f"[warning] regime weekly non disponibile per {ticker}: {err}", file=sys.stderr)

    rs_vs_sector: dict | None = None
    if weekly is not None and is_us_ticker(ticker):
        yf_sector = get_ticker_sector(ticker)
        peer = peer_etf_for(yf_sector)
        if peer is not None:
            sector_weekly = download_benchmark_weekly(peer)
            if sector_weekly is not None:
                rs_vs_sector = score_rs_vs_sector(
                    weekly["Close"], sector_weekly, peer_etf=peer
                )

    # Earnings calendar: surface upcoming earnings per warning + hard gate.
    # Fail-open su yfinance error (non blocca lo scoring se data source giù).
    next_earnings_date: str | None = None
    try:
        next_earnings_date = get_next_earnings_date(ticker)
    except Exception:
        next_earnings_date = None
    days_to_earnings = _days_to_earnings(next_earnings_date)

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]

    ema_fast_s = compute_ema(close, EMA_FAST)
    ema_slow_s = compute_ema(close, EMA_SLOW)
    rsi_s = compute_rsi(close, RSI_PERIOD)
    atr_s = compute_atr(high, low, close, ATR_PERIOD)

    price = float(close.iloc[-1])
    ema_fast = float(ema_fast_s.iloc[-1])
    ema_slow = float(ema_slow_s.iloc[-1])
    rsi = float(rsi_s.iloc[-1])
    atr = float(atr_s.iloc[-1])
    # avg_vol = media delle N barre PRECEDENTI (esclude la barra corrente).
    # Includere la barra corrente nella media biasa il volume_ratio verso 1.0
    # (≈ -5% bias su VOLUME_AVG_PERIOD=20): `cur_vol / mean_incl_self` < `cur_vol / mean_prev_only`.
    cur_vol = float(volume.iloc[-1])
    prev_window = volume.iloc[-VOLUME_AVG_PERIOD - 1 : -1]
    avg_vol = float(prev_window.mean()) if not prev_window.empty else cur_vol
    high_52w = float(high.tail(min(252, len(high))).max())

    # Direction = segno del close-to-close giornaliero. Serve a discriminare
    # volume su breakout green-day (conviction) vs panic red-day (distribuzione).
    if len(close) >= 2:
        prev_close = float(close.iloc[-2])
        direction = price - prev_close if prev_close > 0 else None
    else:
        direction = None

    prev_ema_fast = float(ema_fast_s.iloc[-6]) if len(ema_fast_s) >= 6 else float("nan")
    prev_ema_slow = float(ema_slow_s.iloc[-6]) if len(ema_slow_s) >= 6 else float("nan")

    sub = {
        "trend": score_trend(price, ema_fast, ema_slow),
        "momentum": score_momentum(rsi),
        "volume": score_volume(cur_vol, avg_vol, direction=direction),
        "distance_high": score_distance_from_high(price, high_52w),
        "volatility": score_volatility(atr, price),
        "ma_cross": score_ma_cross(ema_fast, ema_slow, prev_ema_fast, prev_ema_slow),
    }

    composite = (
        sub["trend"] * WEIGHT_TREND
        + sub["momentum"] * WEIGHT_MOMENTUM
        + sub["volume"] * WEIGHT_VOLUME
        + sub["distance_high"] * WEIGHT_DISTANCE_HIGH
        + sub["volatility"] * WEIGHT_VOLATILITY
        + sub["ma_cross"] * WEIGHT_MA_CROSS
    )
    composite = max(0.0, min(100.0, composite))

    stop_suggested = price - (atr * 2)

    return {
        "ticker": ticker,
        "strategy": strategy,
        "price": round(price, 2),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr / price, 4) if price else None,
        "avg_volume": int(avg_vol),
        "current_volume": int(cur_vol),
        # Quando avg_vol = 0 il sub-score "volume" è neutralizzato a 50
        # (vedi score_volume): esponiamo None come sentinel di
        # "ratio non calcolabile" — il display layer deve mostrare la
        # neutralizzazione, non un'assenza di segnale.
        "volume_ratio": round(cur_vol / avg_vol, 2) if avg_vol else None,
        "volume_neutralized": avg_vol == 0,
        "high_52w": round(high_52w, 2),
        "distance_from_high_pct": round((high_52w - price) / high_52w, 4) if high_52w else None,
        "scores": {k: round(v, 1) for k, v in sub.items()},
        "score_composite": round(composite, 1),
        "classification": classify(composite),
        "stop_suggested": round(stop_suggested, 2),
        "stop_pct": round((stop_suggested - price) / price, 4) if price else None,
        "perf_1w": pct_change(close, 5),
        "perf_1m": pct_change(close, 21),
        "perf_3m": pct_change(close, 63),
        "regime": regime,
        "rs_vs_sector": rs_vs_sector,
        "next_earnings_date": next_earnings_date,
        "days_to_earnings": days_to_earnings,
    }
