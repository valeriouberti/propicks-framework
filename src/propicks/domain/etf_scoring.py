"""Scoring per rotazione settoriale ETF.

Layer parallelo a ``domain.scoring``: stesso pattern (sub-score puri +
orchestratore), ma formula diversa perché il problema è diverso. Gli ETF
settoriali non si scelgono come single-name sul pullback — si scelgono
per leadership relativa e fit col regime macro.

Formula composite (pesi in config):
    composite = RS*40% + regime_fit*30% + abs_momentum*20% + trend*10%

**Regime come hard gate + componente** (decisione architetturale — vedi
CLAUDE.md Fase 2): oltre al peso 30% nella formula, il regime applica un
cap superiore allo score dei settori non favoriti:

    STRONG_BEAR: non-favored → score forzato a 0  (flat o solo difensivi)
    BEAR:        non-favored → score capped a 50  (no overweight cicliche)
    NEUTRAL+:    nessun cap  (ranking libero)

Questo evita che un XLK con momentum forte esca top-ranked in un regime
di drawdown — coerente con il gate regime già usato in ``validate_thesis``.
"""

from __future__ import annotations

import sys
from typing import Literal, Optional

import pandas as pd

from propicks.config import (
    ETF_BENCHMARK,
    ETF_MOMENTUM_LOOKBACK_DAYS,
    ETF_RS_EMA_WEEKS,
    ETF_RS_LOOKBACK_WEEKS,
    ETF_SCORE_HOLD,
    ETF_SCORE_NEUTRAL,
    ETF_SCORE_OVERWEIGHT,
    ETF_WEIGHT_ABS_MOMENTUM,
    ETF_WEIGHT_REGIME_FIT,
    ETF_WEIGHT_RS,
    ETF_WEIGHT_TREND,
    REGIME_FAVORED_SECTORS,
    REGIME_WEEKLY_EMA_SLOW,
)
from propicks.domain.etf_universe import (
    favored_sectors_for_regime,
    get_etf_info,
    get_sector_key,
    is_favored,
    list_universe,
)
from propicks.domain.indicators import compute_ema, pct_change
from propicks.domain.regime import classify_regime
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_benchmark_weekly,
    download_history,
    download_weekly_history,
)


Region = Literal["US", "EU", "ALL"]


# ---------------------------------------------------------------------------
# Sub-score: ognuno ritorna un float 0-100
# ---------------------------------------------------------------------------
def score_rs(
    close_etf_weekly: pd.Series,
    close_benchmark_weekly: pd.Series,
    lookback: int = ETF_RS_LOOKBACK_WEEKS,
    ema_span: int = ETF_RS_EMA_WEEKS,
) -> dict:
    """Relative Strength vs benchmark — level × slope.

    Ritorna un dict con il dettaglio (per il layer di presentazione) e la
    chiave ``score`` 0-100. Logica:

    - ``rs_ratio_now`` = close(ETF) / close(benchmark), normalizzato dal
      valore ``lookback`` settimane fa → 1.0 = performance uguale, >1.0 =
      outperform, <1.0 = underperform.
    - ``rs_slope`` = rs_ratio / EMA(rs_ratio, ema_span) − 1 → positivo se
      la leadership sta accelerando, negativo se rallenta.
    - Score = combinazione di level e slope:
        * level >= 1.05 (outperform 5%+) E slope > 0  → 100 (leader in accelerazione)
        * level >= 1.02 E slope > 0                  → 85
        * level >= 1.0 E slope > 0                   → 70
        * level >= 1.0 E slope <= 0                  → 55 (leader stanco — watch)
        * level < 1.0 E slope > 0                    → 45 (underperformer in recupero)
        * level < 1.0 E slope <= 0                   → 20 (lagger in distribuzione)
        * level < 0.95 E slope <= 0                  → 10

    Il level prevale sullo slope quando sono in conflitto: un ex-leader
    (level alto, slope negativo) è comunque meglio di un lagger acceso.
    """
    if close_etf_weekly is None or close_benchmark_weekly is None:
        return {"score": 50.0, "rs_ratio": None, "rs_slope": None, "note": "no benchmark"}

    # Allinea gli indici (intersezione date)
    joined = pd.concat(
        [close_etf_weekly.rename("etf"), close_benchmark_weekly.rename("bench")],
        axis=1,
        join="inner",
    ).dropna()

    if len(joined) < lookback + ema_span:
        return {
            "score": 50.0,
            "rs_ratio": None,
            "rs_slope": None,
            "note": f"storia insufficiente: {len(joined)} barre",
        }

    rs = joined["etf"] / joined["bench"]
    # Normalizza al valore di ``lookback`` settimane fa → 1.0 = pari benchmark
    base = rs.iloc[-lookback - 1]
    if base <= 0:
        return {"score": 50.0, "rs_ratio": None, "rs_slope": None, "note": "base invalida"}
    rs_norm = rs / base
    rs_ratio = float(rs_norm.iloc[-1])

    rs_ema = compute_ema(rs_norm, ema_span)
    ema_now = float(rs_ema.iloc[-1])
    rs_slope = (rs_ratio / ema_now - 1.0) if ema_now > 0 else 0.0

    if rs_ratio >= 1.05 and rs_slope > 0:
        score = 100.0
    elif rs_ratio >= 1.02 and rs_slope > 0:
        score = 85.0
    elif rs_ratio >= 1.0 and rs_slope > 0:
        score = 70.0
    elif rs_ratio >= 1.0:  # slope <= 0
        score = 55.0
    elif rs_ratio >= 0.95 and rs_slope > 0:
        score = 45.0
    elif rs_ratio >= 0.95:
        score = 25.0
    elif rs_slope > 0:
        score = 20.0
    else:
        score = 10.0

    return {
        "score": score,
        "rs_ratio": round(rs_ratio, 4),
        "rs_slope": round(rs_slope, 4),
    }


def score_regime_fit(sector_key: Optional[str], regime_code: Optional[int]) -> float:
    """Fit del settore col regime weekly corrente (0-100).

    Favored in regime corrente                  → 100
    Favored in regime adiacente (5↔4 o 2↔1)    → 60 (zona di transizione)
    Non favored                                 → 20
    Regime ignoto                               → 50 (neutrale: non so)
    """
    if sector_key is None or regime_code is None:
        return 50.0

    if sector_key in favored_sectors_for_regime(regime_code):
        return 100.0

    # Transizione: un settore favorito nel regime "vicino" è ancora plausibile
    for adj in (regime_code - 1, regime_code + 1):
        if adj in REGIME_FAVORED_SECTORS and sector_key in REGIME_FAVORED_SECTORS[adj]:
            return 60.0

    return 20.0


def score_abs_momentum(perf: Optional[float]) -> float:
    """Momentum assoluto (perf 3M) mappato su 0-100.

    +15%+    → 100  (trend acceso)
    +8..15%  → 80
    +3..8%   → 60
    0..3%    → 40
    -5..0%   → 25
    <-5%     → 10
    """
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


def score_etf_trend(close_weekly: pd.Series, ema_span: int = REGIME_WEEKLY_EMA_SLOW) -> dict:
    """Trend score dal rapporto close weekly vs EMA slow weekly.

    Usa EMA 30 weekly (== ``REGIME_WEEKLY_EMA_SLOW``) per coerenza col
    regime classifier: è il livello che il Pine weekly watcha come trend
    guide di medio termine.

    - price > EMA AND EMA in salita (ultime 4 weeks)  → 100
    - price > EMA AND EMA flat                        → 75
    - price > EMA AND EMA in discesa                  → 55
    - price < EMA AND EMA in salita                   → 35
    - price < EMA AND EMA flat o in discesa           → 10
    """
    if close_weekly is None or len(close_weekly) < ema_span + 4:
        return {"score": 50.0, "above_ema": None, "ema_slope": None}

    ema = compute_ema(close_weekly, ema_span)
    price = float(close_weekly.iloc[-1])
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-5])  # 4 settimane fa

    above = price > ema_now
    slope = (ema_now - ema_prev) / ema_prev if ema_prev > 0 else 0.0
    rising = slope > 0.005       # +0.5% in 4w ≈ trend solido
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


# ---------------------------------------------------------------------------
# Regime hard-gate cap
# ---------------------------------------------------------------------------
def apply_regime_cap(composite: float, sector_key: str, regime_code: Optional[int]) -> float:
    """Applica il cap superiore da regime hard-gate.

    Non-favored in STRONG_BEAR → 0  (no long ciclicali in crisi)
    Non-favored in BEAR        → min(composite, 50)  (no overweight)
    Altrimenti                 → composite invariato

    Se il regime non è disponibile, lascia lo score invariato — non si può
    penalizzare in cieco.
    """
    if regime_code is None:
        return composite
    if regime_code in (1, 2):  # BEAR / STRONG_BEAR
        favored = sector_key in favored_sectors_for_regime(regime_code)
        if not favored:
            if regime_code == 1:
                return 0.0
            return min(composite, 50.0)
    return composite


# ---------------------------------------------------------------------------
# Classificazione
# ---------------------------------------------------------------------------
def classify_etf(score: float) -> str:
    if score >= ETF_SCORE_OVERWEIGHT:
        return "A — OVERWEIGHT"
    if score >= ETF_SCORE_HOLD:
        return "B — HOLD"
    if score >= ETF_SCORE_NEUTRAL:
        return "C — NEUTRAL"
    return "D — AVOID"


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------
def analyze_etf(
    ticker: str,
    benchmark_weekly: Optional[pd.Series] = None,
    regime_code: Optional[int] = None,
    regime: Optional[dict] = None,
) -> Optional[dict]:
    """Analizza un singolo ETF settoriale.

    ``benchmark_weekly`` e ``regime_code`` sono iniettabili per permettere
    batch scan efficiente (scarica il benchmark e classifica il regime
    UNA volta, poi passa il risultato ad ogni analyze). Se non passati,
    vengono scaricati da yfinance qui.

    Ritorna None con warning su stderr in caso di dati insufficienti.
    """
    ticker = ticker.upper()
    info = get_etf_info(ticker)
    if info is None:
        print(f"[errore] {ticker}: non è un ETF settoriale mappato", file=sys.stderr)
        return None

    sector_key = info["sector_key"]

    try:
        daily = download_history(ticker)
        weekly = download_weekly_history(ticker)
    except DataUnavailable as err:
        print(f"[errore] {err}", file=sys.stderr)
        return None

    if benchmark_weekly is None:
        benchmark_weekly = download_benchmark_weekly(ETF_BENCHMARK)

    if regime_code is None:
        try:
            bench_weekly_for_regime = download_weekly_history(ETF_BENCHMARK)
            regime = classify_regime(bench_weekly_for_regime)
            regime_code = regime["regime_code"] if regime else None
        except DataUnavailable:
            regime_code = None

    perf_3m = pct_change(daily["Close"], ETF_MOMENTUM_LOOKBACK_DAYS)

    rs = score_rs(weekly["Close"], benchmark_weekly)
    regime_fit = score_regime_fit(sector_key, regime_code)
    abs_mom = score_abs_momentum(perf_3m)
    trend = score_etf_trend(weekly["Close"])

    composite_raw = (
        rs["score"] * ETF_WEIGHT_RS
        + regime_fit * ETF_WEIGHT_REGIME_FIT
        + abs_mom * ETF_WEIGHT_ABS_MOMENTUM
        + trend["score"] * ETF_WEIGHT_TREND
    )
    composite_raw = max(0.0, min(100.0, composite_raw))
    composite = apply_regime_cap(composite_raw, sector_key, regime_code)
    cap_triggered = composite < composite_raw

    price = float(daily["Close"].iloc[-1])

    return {
        "ticker": ticker,
        "name": info["name"],
        "region": info["region"],
        "sector_key": sector_key,
        "asset_type": "SECTOR_ETF",
        "price": round(price, 2),
        "perf_1w": pct_change(daily["Close"], 5),
        "perf_1m": pct_change(daily["Close"], 21),
        "perf_3m": perf_3m,
        "rs": rs,
        "regime_fit_score": regime_fit,
        "favored_in_regime": is_favored(ticker, regime_code) if regime_code else False,
        "abs_momentum_score": abs_mom,
        "trend": trend,
        "score_composite_raw": round(composite_raw, 1),
        "score_composite": round(composite, 1),
        "regime_cap_applied": cap_triggered,
        "classification": classify_etf(composite),
        "regime": regime,
        "regime_code": regime_code,
        "stop_suggested": round(price * (1 - 0.05), 2),  # -5% hard stop
        "scores": {
            "rs": rs["score"],
            "regime_fit": regime_fit,
            "abs_momentum": abs_mom,
            "trend": trend["score"],
        },
    }


def rank_universe(
    region: Region = "US",
    regime_code: Optional[int] = None,
    benchmark_weekly: Optional[pd.Series] = None,
) -> list[dict]:
    """Scarica e scora l'intero universo, ritorna lista ordinata per score.

    Il regime e il benchmark vengono fetchati UNA volta e propagati a tutti
    gli analyze — evita 11+ download del benchmark. Errori per singolo
    ticker non abortiscono il batch.
    """
    if benchmark_weekly is None:
        benchmark_weekly = download_benchmark_weekly(ETF_BENCHMARK)

    regime: Optional[dict] = None
    if regime_code is None:
        try:
            bench_weekly_for_regime = download_weekly_history(ETF_BENCHMARK)
            regime = classify_regime(bench_weekly_for_regime)
            regime_code = regime["regime_code"] if regime else None
        except DataUnavailable as err:
            print(f"[warning] regime macro non disponibile: {err}", file=sys.stderr)

    universe = list_universe(region)
    results: list[dict] = []
    for row in universe:
        r = analyze_etf(
            row["ticker"],
            benchmark_weekly=benchmark_weekly,
            regime_code=regime_code,
            regime=regime,
        )
        if r is not None:
            results.append(r)

    results.sort(key=lambda x: x["score_composite"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


def suggest_allocation(
    ranked: list[dict],
    top_n: int = 3,
    max_per_etf_pct: float = 0.15,
    max_aggregate_pct: float = 0.60,
) -> dict:
    """Propone l'allocazione dai top-N ranked ETF.

    Logica:
    - Selezione: top-N per score_composite, esclusi D (AVOID)
    - Equal-weight tra i selezionati, cap ``max_per_etf_pct`` per singolo
    - Cap aggregato ``max_aggregate_pct`` sul totale sector ETF
    - In BEAR: N ridotto a 1 (solo top difensivo)
    - In STRONG_BEAR: allocazione vuota (flat)
    """
    if not ranked:
        return {"positions": [], "aggregate_pct": 0.0, "note": "universo vuoto"}

    regime_code = ranked[0].get("regime_code")

    if regime_code == 1:  # STRONG_BEAR
        return {
            "positions": [],
            "aggregate_pct": 0.0,
            "note": "STRONG_BEAR: flat. Nessuna esposizione sector ETF suggerita.",
        }

    effective_top = 1 if regime_code == 2 else top_n
    eligible = [r for r in ranked if r["classification"].startswith(("A", "B"))]
    selected = eligible[:effective_top]

    if not selected:
        return {
            "positions": [],
            "aggregate_pct": 0.0,
            "note": "Nessun ETF con score >= HOLD. Wait-and-see.",
        }

    per_etf = min(max_per_etf_pct, max_aggregate_pct / len(selected))
    aggregate = per_etf * len(selected)

    positions = [
        {
            "ticker": r["ticker"],
            "sector_key": r["sector_key"],
            "score": r["score_composite"],
            "classification": r["classification"],
            "allocation_pct": round(per_etf, 4),
            "price": r["price"],
            "stop_suggested": r["stop_suggested"],
        }
        for r in selected
    ]

    return {
        "positions": positions,
        "aggregate_pct": round(aggregate, 4),
        "regime_code": regime_code,
        "effective_top_n": effective_top,
    }
