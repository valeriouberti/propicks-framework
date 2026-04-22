"""Scoring tecnico 0-100 per singoli ticker.

Sei sotto-score indipendenti pesati dalle costanti WEIGHT_* in config.
Ogni funzione ``score_*`` è pura: riceve numeri, ritorna un float 0-100.

``analyze_ticker`` orchestra download → calcolo indicatori → scoring e
ritorna un dict pronto per la CLI o la API.
"""

from __future__ import annotations

import sys

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


def score_volume(current_volume: float, avg_volume: float) -> float:
    if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume <= 0:
        return 50.0
    ratio = current_volume / avg_volume
    if 1.2 <= ratio < 2.0:
        return 100.0
    if 2.0 <= ratio < 3.0:
        return 80.0
    if 1.0 <= ratio < 1.2:
        return 70.0
    if ratio >= 3.0:
        return 60.0
    if 0.7 <= ratio < 1.0:
        return 50.0
    if 0.5 <= ratio < 0.7:
        return 30.0
    return 15.0


def score_distance_from_high(close: float, high_52w: float) -> float:
    if pd.isna(close) or pd.isna(high_52w) or high_52w <= 0:
        return 0.0
    dist = (high_52w - close) / high_52w
    if dist < 0.03:
        return 75.0
    if dist < 0.05:
        return 85.0
    if dist < 0.10:
        return 100.0
    if dist < 0.15:
        return 80.0
    if dist < 0.25:
        return 50.0
    if dist < 0.35:
        return 30.0
    return 10.0


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

    prev_ema_fast = float(ema_fast_s.iloc[-6]) if len(ema_fast_s) >= 6 else float("nan")
    prev_ema_slow = float(ema_slow_s.iloc[-6]) if len(ema_slow_s) >= 6 else float("nan")

    sub = {
        "trend": score_trend(price, ema_fast, ema_slow),
        "momentum": score_momentum(rsi),
        "volume": score_volume(cur_vol, avg_vol),
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
        "volume_ratio": round(cur_vol / avg_vol, 2) if avg_vol else None,
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
    }
