"""Scoring engine per Thematic ETF (sub-industry, cross-sector tilts).

Bucket satellite parallelo a momentum/contrarian/rotation. Il problema è
discriminare alfa tematico genuino da leveraged sector bet camuffato da
diversificazione.

Formula composite (pesi in config):
    composite = rs_vs_parent*50% + abs_momentum*25% + trend*15% + parent_regime_fit*10%

**Kill-switch correlation**: se corr_60d(theme, parent) >= THEMATIC_CORR_KILL_THRESHOLD
(default 0.85) → composite forzato a 0. Razionale: a quella correlazione il
tematico non porta alfa — è solo concentration più alta del parent. Comprare
SMH a corr 0.92 con XLK è leverage 1.3x XLK senza alfa proprio.

**Regime hard-gate**: skip BEAR/STRONG_BEAR (parallelo a momentum). I tematici
sono growth/cyclical-tilt — non hanno senso in capital preservation regime.
"""

from __future__ import annotations

import sys

import pandas as pd

from propicks.config import (
    ETF_BENCHMARK,
    REGIME_FAVORED_SECTORS,
    REGIME_WEEKLY_EMA_SLOW,
    THEMATIC_CORR_KILL_THRESHOLD,
    THEMATIC_MOMENTUM_LOOKBACK_DAYS,
    THEMATIC_SCORE_HOLD,
    THEMATIC_SCORE_NEUTRAL,
    THEMATIC_SCORE_OVERWEIGHT,
    THEMATIC_STOP_LOSS_PCT,
    THEMATIC_WEIGHT_ABS_MOMENTUM,
    THEMATIC_WEIGHT_PARENT_REGIME_FIT,
    THEMATIC_WEIGHT_RS_VS_PARENT,
    THEMATIC_WEIGHT_TREND,
)
from propicks.domain.indicators import compute_ema, pct_change
from propicks.domain.regime import classify_regime
from propicks.domain.thematic_rs import compute_correlation, compute_rs_vs_parent
from propicks.domain.thematic_universe import (
    get_thematic_info,
    list_universe,
)
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_history,
    download_weekly_history,
)


# ---------------------------------------------------------------------------
# Sub-score: ognuno ritorna float 0-100 (eccetto rs/trend dict)
# ---------------------------------------------------------------------------
def score_abs_momentum(perf: float | None) -> float:
    """Stessa scala dell'ETF rotation (perf 3M)."""
    if perf is None:
        return 40.0
    if perf >= 0.15:
        return 100.0
    if perf >= 0.08:
        return 80.0
    if perf >= 0.03:
        return 60.0
    if perf >= 0.0:
        return 40.0
    if perf >= -0.05:
        return 25.0
    return 10.0


def score_thematic_trend(close_weekly: pd.Series, ema_span: int = REGIME_WEEKLY_EMA_SLOW) -> dict:
    """Trend score price vs EMA30 weekly + slope EMA su 4 weeks (parallelo ETF)."""
    if close_weekly is None or len(close_weekly) < ema_span + 4:
        return {"score": 50.0, "above_ema": None, "ema_slope": None}

    ema = compute_ema(close_weekly, ema_span)
    price = float(close_weekly.iloc[-1])
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-5])

    above = price > ema_now
    slope = (ema_now - ema_prev) / ema_prev if ema_prev > 0 else 0.0
    rising = slope > 0.005
    flat = -0.005 <= slope <= 0.005

    if above and rising:
        score = 100.0
    elif above and flat:
        score = 75.0
    elif above:
        score = 55.0
    elif rising:
        score = 35.0
    else:
        score = 10.0

    return {
        "score": score,
        "above_ema": above,
        "ema_slope": round(slope, 4),
        "ema_value": round(ema_now, 2),
        "price": round(price, 2),
    }


def score_parent_regime_fit(parent_sector_key: str | None, regime_code: int | None) -> float:
    """Regime fit del PARENT sector (non del tematico).

    Il tematico eredita il regime fit dal parent: se XLK è favorito, SMH ha
    edge regime. Sub-industry non mappa GICS, quindi non ha senso lookup
    diretto sul tematico.

    Stessa scala di etf_scoring.score_regime_fit:
        favored regime corrente   → 100
        favored regime adiacente  → 60
        non favored               → 20
        regime ignoto             → 50
    """
    if parent_sector_key is None or regime_code is None:
        return 50.0
    if parent_sector_key in REGIME_FAVORED_SECTORS.get(regime_code, ()):
        return 100.0
    for adj in (regime_code - 1, regime_code + 1):
        if adj in REGIME_FAVORED_SECTORS and parent_sector_key in REGIME_FAVORED_SECTORS[adj]:
            return 60.0
    return 20.0


# ---------------------------------------------------------------------------
# Kill-switch correlation
# ---------------------------------------------------------------------------
def apply_corr_kill_switch(
    composite: float, corr: float | None, threshold: float = THEMATIC_CORR_KILL_THRESHOLD
) -> tuple[float, bool]:
    """Forza composite a 0 se corr(theme, parent) >= threshold.

    Returns: (composite_post_kill, kill_triggered).
    Se corr è None (storia insufficiente), conservativo: passa invariato.
    """
    if corr is None:
        return composite, False
    if corr >= threshold:
        return 0.0, True
    return composite, False


# ---------------------------------------------------------------------------
# Regime hard-gate (parallelo momentum: skip BEAR/STRONG_BEAR)
# ---------------------------------------------------------------------------
def apply_regime_gate(composite: float, regime_code: int | None) -> tuple[float, bool]:
    """STRONG_BEAR (1) → 0. BEAR (2) → cap a 40 (max class C NEUTRAL).

    Tematici sono growth/cyclical-tilt: in capital preservation regime non
    hanno edge. NEUTRAL+ pass-through.
    """
    if regime_code is None:
        return composite, False
    if regime_code == 1:
        return 0.0, True
    if regime_code == 2:
        capped = min(composite, 40.0)
        return capped, capped < composite
    return composite, False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify_thematic(score: float) -> str:
    if score >= THEMATIC_SCORE_OVERWEIGHT:
        return "A — OVERWEIGHT"
    if score >= THEMATIC_SCORE_HOLD:
        return "B — HOLD"
    if score >= THEMATIC_SCORE_NEUTRAL:
        return "C — NEUTRAL"
    return "D — AVOID"


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------
def analyze_theme(
    ticker: str,
    *,
    parent_weekly: pd.Series | None = None,
    parent_daily: pd.Series | None = None,
    regime_code: int | None = None,
    regime: dict | None = None,
) -> dict | None:
    """Analizza un singolo thematic ETF.

    ``parent_weekly`` / ``parent_daily`` / ``regime_code`` iniettabili per
    batch efficiente. Se non passati, scarica da yfinance.

    Ritorna None con warning su stderr se dati insufficienti.
    """
    ticker = ticker.upper()
    info = get_thematic_info(ticker)
    if info is None:
        print(f"[errore] {ticker}: non è un thematic ETF mappato", file=sys.stderr)
        return None

    parent_ticker = info["parent_ticker"]
    parent_sector_key = info["parent_sector_key"]

    try:
        daily = download_history(ticker)
        weekly = download_weekly_history(ticker)
    except DataUnavailable as err:
        print(f"[errore] {err}", file=sys.stderr)
        return None

    if parent_weekly is None or parent_daily is None:
        try:
            parent_daily_df = download_history(parent_ticker)
            parent_weekly_df = download_weekly_history(parent_ticker)
            parent_daily = parent_daily_df["Close"]
            parent_weekly = parent_weekly_df["Close"]
        except DataUnavailable as err:
            print(f"[errore] parent {parent_ticker} non disponibile: {err}", file=sys.stderr)
            return None

    if regime_code is None:
        try:
            bench_weekly = download_weekly_history(ETF_BENCHMARK)
            regime = classify_regime(bench_weekly)
            regime_code = regime["regime_code"] if regime else None
        except DataUnavailable:
            regime_code = None

    perf_3m = pct_change(daily["Close"], THEMATIC_MOMENTUM_LOOKBACK_DAYS)
    rs = compute_rs_vs_parent(weekly["Close"], parent_weekly)
    abs_mom = score_abs_momentum(perf_3m)
    trend = score_thematic_trend(weekly["Close"])
    parent_fit = score_parent_regime_fit(parent_sector_key, regime_code)
    corr = compute_correlation(daily["Close"], parent_daily)

    composite_raw = (
        rs["score"] * THEMATIC_WEIGHT_RS_VS_PARENT
        + abs_mom * THEMATIC_WEIGHT_ABS_MOMENTUM
        + trend["score"] * THEMATIC_WEIGHT_TREND
        + parent_fit * THEMATIC_WEIGHT_PARENT_REGIME_FIT
    )
    composite_raw = max(0.0, min(100.0, composite_raw))

    composite_post_corr, corr_kill = apply_corr_kill_switch(composite_raw, corr)
    composite_final, regime_gate_triggered = apply_regime_gate(composite_post_corr, regime_code)

    price = float(daily["Close"].iloc[-1])

    # Stop hard 10% (più largo del 5% ETF e dell'8% momentum — tematici hanno
    # ATR% tipicamente più alto).
    stop = round(price * (1 - THEMATIC_STOP_LOSS_PCT), 2)

    return {
        "ticker": ticker,
        "name": info["name"],
        "theme_label": info["theme_label"],
        "parent_ticker": parent_ticker,
        "parent_sector_key": parent_sector_key,
        "region": info.get("region"),
        "asset_type": "THEMATIC_ETF",
        "price": round(price, 2),
        "perf_1w": pct_change(daily["Close"], 5),
        "perf_1m": pct_change(daily["Close"], 21),
        "perf_3m": perf_3m,
        "rs_vs_parent": rs,
        "abs_momentum_score": abs_mom,
        "trend": trend,
        "parent_regime_fit_score": parent_fit,
        "correlation_with_parent": round(corr, 4) if corr is not None else None,
        "score_composite_raw": round(composite_raw, 1),
        "score_composite_post_corr": round(composite_post_corr, 1),
        "score_composite": round(composite_final, 1),
        "corr_kill_applied": corr_kill,
        "regime_gate_applied": regime_gate_triggered,
        "classification": classify_thematic(composite_final),
        "regime": regime,
        "regime_code": regime_code,
        "stop_suggested": stop,
        "scores": {
            "rs_vs_parent": rs["score"],
            "abs_momentum": abs_mom,
            "trend": trend["score"],
            "parent_regime_fit": parent_fit,
        },
    }


def rank_universe(
    region: str = "ALL",
    *,
    theme_label: str | None = None,
) -> list[dict]:
    """Scarica e scora l'intero universo tematico, ordinato per composite.

    ``theme_label`` filtra per tema (es. 'biotech' ritorna solo XBI/IBB).
    Errori per singolo ticker non abortiscono il batch — listing illiquidi
    su yfinance possono fallire indipendentemente.
    """
    regime_code: int | None = None
    regime: dict | None = None
    try:
        bench_weekly = download_weekly_history(ETF_BENCHMARK)
        regime = classify_regime(bench_weekly)
        regime_code = regime["regime_code"] if regime else None
    except DataUnavailable as err:
        print(f"[warning] regime non disponibile: {err}", file=sys.stderr)

    universe = list_universe(region=region)  # type: ignore[arg-type]
    if theme_label:
        universe = [r for r in universe if r.get("theme_label") == theme_label]

    # Cache parent series per evitare ri-download dello stesso parent N volte
    # (es. se ci sono 3 tematici tech con parent XLK).
    parent_cache: dict[str, tuple[pd.Series, pd.Series]] = {}

    results: list[dict] = []
    for row in universe:
        parent_ticker = row.get("parent_ticker")
        if parent_ticker and parent_ticker not in parent_cache:
            try:
                parent_cache[parent_ticker] = (
                    download_history(parent_ticker)["Close"],
                    download_weekly_history(parent_ticker)["Close"],
                )
            except DataUnavailable as err:
                print(f"[warning] parent {parent_ticker} skip: {err}", file=sys.stderr)
                continue
        parent_daily = parent_cache.get(parent_ticker, (None, None))[0] if parent_ticker else None
        parent_weekly = parent_cache.get(parent_ticker, (None, None))[1] if parent_ticker else None

        r = analyze_theme(
            row["ticker"],
            parent_weekly=parent_weekly,
            parent_daily=parent_daily,
            regime_code=regime_code,
            regime=regime,
        )
        if r is not None:
            results.append(r)

    results.sort(key=lambda x: x["score_composite"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results
