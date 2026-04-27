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

    STRONG_BEAR: non-favored → score forzato a 0   (flat o solo difensivi)
    BEAR:        non-favored → score capped a 50   (no overweight cicliche)
    NEUTRAL:     non-favored → score capped a 65   (soft cap: B HOLD ok, no class A)
    BULL+:       nessun cap                        (ranking libero)

Il soft cap NEUTRAL evita che un settore con RS forte ma "non favorito"
esca class A (≥70 = OVERWEIGHT) quando il framework regime lo considera
ugualmente non-favored: in NEUTRAL è ammesso restare in HOLD ma non
ottenere overweight allocation senza una conferma di regime.
"""

from __future__ import annotations

import sys
from typing import Literal

import pandas as pd

from propicks.config import (
    ETF_BENCHMARK,
    ETF_MAX_AGGREGATE_EXPOSURE_PCT,
    ETF_MAX_POSITION_SIZE_PCT,
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
    get_etf_benchmark,
)
from propicks.domain.etf_universe import (
    favored_sectors_for_regime,
    get_etf_info,
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

Region = Literal["US", "EU", "WORLD", "ALL"]


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
    - ``rs_slope`` = (rs_norm[-1] − rs_norm[-ema_span-1]) / ema_span →
      vera slope su ``ema_span`` settimane (variazione media settimanale
      della RS line). Positivo = leadership che accelera, negativo = che
      rallenta. Più reattivo del precedente "spread vs EMA" sui pattern
      di stabilizzazione post-correzione.
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

    # yfinance restituisce indici tz-aware nel fuso dell'exchange
    # (NYSE → America/New_York, Xetra → Europe/Berlin). Senza strip, l'inner
    # join tra un ETF EU e un benchmark US produce 0 righe anche a date uguali.
    etf = close_etf_weekly.copy()
    bench = close_benchmark_weekly.copy()
    if etf.index.tz is not None:
        etf.index = etf.index.tz_localize(None)
    if bench.index.tz is not None:
        bench.index = bench.index.tz_localize(None)

    # Allinea gli indici (intersezione date)
    joined = pd.concat(
        [etf.rename("etf"), bench.rename("bench")],
        axis=1,
        join="inner",
    ).dropna()

    # Warm-up: lookback (per il base index) + 3×ema_span (per stabilizzare lo
    # smoothing della RS line). Soglia precedente lookback+ema_span ammetteva
    # signal su RS rumorosa per i primi ~6-12 mesi di vita di un ETF.
    min_bars = lookback + 3 * ema_span
    if len(joined) < min_bars:
        return {
            "score": 50.0,
            "rs_ratio": None,
            "rs_slope": None,
            "note": f"storia insufficiente per RS stabile: {len(joined)} barre (richieste {min_bars})",
        }

    rs = joined["etf"] / joined["bench"]
    # Normalizza al valore di ``lookback`` settimane fa → 1.0 = pari benchmark
    base = rs.iloc[-lookback - 1]
    if base <= 0:
        return {"score": 50.0, "rs_ratio": None, "rs_slope": None, "note": "base invalida"}
    rs_norm = rs / base
    rs_ratio = float(rs_norm.iloc[-1])

    # Vera slope: variazione media settimanale della RS line negli ultimi
    # ``ema_span`` periodi. Un EMA-spread veniva nominato "slope" ma in zone
    # di stabilizzazione post-correzione l'EMA in ritardo dava negativo
    # nonostante la RS si fosse appiattita: misclassificazione del segnale.
    rs_past = float(rs_norm.iloc[-ema_span - 1])
    rs_slope = (rs_ratio - rs_past) / ema_span

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


def score_regime_fit(sector_key: str | None, regime_code: int | None) -> float:
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


def score_abs_momentum(perf: float | None) -> float:
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
def apply_regime_cap(composite: float, sector_key: str, regime_code: int | None) -> float:
    """Applica il cap superiore da regime hard-gate.

    Non-favored in STRONG_BEAR (1) → 0              (no long ciclicali in crisi)
    Non-favored in BEAR (2)        → min(composite, 50)   (no overweight)
    Non-favored in NEUTRAL (3)     → min(composite, 65)   (soft cap: HOLD ok, no class A)
    BULL+ (4-5)                    → composite invariato  (ranking libero)

    Se il regime non è disponibile, lascia lo score invariato — non si può
    penalizzare in cieco.
    """
    if regime_code is None:
        return composite
    if regime_code in (1, 2, 3):
        favored = sector_key in favored_sectors_for_regime(regime_code)
        if not favored:
            if regime_code == 1:
                return 0.0
            if regime_code == 2:
                return min(composite, 50.0)
            return min(composite, 65.0)
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
    benchmark_weekly: pd.Series | None = None,
    regime_code: int | None = None,
    regime: dict | None = None,
) -> dict | None:
    """Analizza un singolo ETF settoriale.

    ``benchmark_weekly`` e ``regime_code`` sono iniettabili per permettere
    batch scan efficiente (scarica il benchmark e classifica il regime
    UNA volta, poi passa il risultato ad ogni analyze). Se non passati,
    vengono scaricati da yfinance qui.

    Benchmark: deriva dal ``region`` dell'ETF (US/EU → ^GSPC, WORLD → URTH)
    solo se ``benchmark_weekly`` non è iniettato. In batch via ``rank_universe``
    il benchmark giusto viene già passato.

    Il regime classifier resta su ^GSPC (US-based) anche per WORLD — la
    correlazione S&P/MSCI World weekly è ≈0.95, la tabella REGIME_FAVORED_SECTORS
    è calibrata sul ciclo US. Approssimazione accettabile, da rivedere se il
    framework diventa multi-regime per region.

    Ritorna None con warning su stderr in caso di dati insufficienti.
    """
    ticker = ticker.upper()
    info = get_etf_info(ticker)
    if info is None:
        print(f"[errore] {ticker}: non è un ETF settoriale mappato", file=sys.stderr)
        return None

    sector_key = info["sector_key"]
    region = info.get("region", "US")

    try:
        daily = download_history(ticker)
        weekly = download_weekly_history(ticker)
    except DataUnavailable as err:
        print(f"[errore] {err}", file=sys.stderr)
        return None

    if benchmark_weekly is None:
        benchmark_weekly = download_benchmark_weekly(get_etf_benchmark(region))

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
    regime_code: int | None = None,
    benchmark_weekly: pd.Series | None = None,
) -> list[dict]:
    """Scarica e scora l'intero universo, ritorna lista ordinata per score.

    Il regime e il benchmark vengono fetchati UNA volta e propagati a tutti
    gli analyze — evita 11+ download del benchmark. Errori per singolo
    ticker non abortiscono il batch.

    Il benchmark viene scelto automaticamente in base a ``region``:
    US/EU → ``^GSPC``, WORLD → ``URTH``. Per ``region=ALL`` il benchmark è
    ^GSPC (best-effort, ranking misto US+WORLD è rumoroso per definizione —
    preferire run separate).
    """
    benchmark_ticker = get_etf_benchmark(region)
    if benchmark_weekly is None:
        benchmark_weekly = download_benchmark_weekly(benchmark_ticker)

    regime: dict | None = None
    if regime_code is None:
        # Regime sempre su ^GSPC: la tabella REGIME_FAVORED_SECTORS è US-calibrata
        # (correlazione S&P/MSCI World ≈ 0.95 giustifica l'approssimazione).
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
    max_per_etf_pct: float = ETF_MAX_POSITION_SIZE_PCT,
    max_aggregate_pct: float = ETF_MAX_AGGREGATE_EXPOSURE_PCT,
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
