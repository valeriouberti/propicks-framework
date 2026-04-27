"""Scoring engine per quality-filtered mean reversion (strategia contrarian).

Layer parallelo a ``domain.scoring`` (momentum) e ``domain.etf_scoring`` (rotazione):
stesso pattern (sub-score puri + orchestratore), ma formula diversa perché il
problema è diverso. Momentum cerca forza che accelera; contrarian compra
qualità *temporaneamente* oversold.

Formula composite (pesi in config):
    composite = oversold*40% + quality*25% + market_context*20% + reversion*15%

Regime fit INVERSO rispetto a momentum (cfr. ``CONTRA_REGIME_FIT``):
- STRONG_BULL / STRONG_BEAR → score azzerato o quasi (edge collassa)
- NEUTRAL → sweet spot (100)
- BULL/BEAR → intermedi

**NON è un'implementazione di "short": tutte le posizioni restano long**.
La contrarian cerca entry long asimmetrici su titoli temporaneamente venduti
con trend strutturale intatto. Lo short selling resta fuori scope.

Gate architetturali (applicati nel CLI/orchestratore, non nel domain puro
per mantenere testabilità senza rete/portfolio state):
- Quality: ticker nel basket Pro Picks o universo watchlist curato
- Fundamental: Claude "flush vs break" → REJECT su guidance cut / fraud
- Sizing: max 8% per posizione, 20% aggregato (cfr. config CONTRA_*)
"""

from __future__ import annotations

import sys

import pandas as pd

from propicks.config import (
    ATR_PERIOD,
    CONTRA_ATR_DISTANCE_MIN,
    CONTRA_CONSECUTIVE_DOWN_DAYS,
    CONTRA_MIN_EMA200_BUFFER,
    CONTRA_REGIME_FIT,
    CONTRA_RSI_OVERSOLD,
    CONTRA_RSI_WARM,
    CONTRA_SCORE_A,
    CONTRA_SCORE_B,
    CONTRA_SCORE_C,
    CONTRA_STOP_ATR_MULT,
    CONTRA_TARGET_EMA_PERIOD,
    CONTRA_VIX_COMPLACENT,
    CONTRA_VIX_SPIKE,
    CONTRA_WEIGHT_MARKET_CONTEXT,
    CONTRA_WEIGHT_OVERSOLD,
    CONTRA_WEIGHT_QUALITY,
    CONTRA_WEIGHT_REVERSION,
    EMA_SLOW,
    REGIME_WEEKLY_EMA_200D,
    RSI_PERIOD,
)
from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi, pct_change
from propicks.domain.regime import classify_regime
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_benchmark,
    download_history,
    download_weekly_history,
    get_next_earnings_date,
)


# ---------------------------------------------------------------------------
# Sub-score: ognuno ritorna un float 0-100 (o dict con 'score' + dettaglio)
# ---------------------------------------------------------------------------
def score_oversold(
    rsi: float,
    close: float,
    ema_slow: float,
    atr: float,
    consecutive_down: int,
    drawdown_5d_atr: float | None = None,
) -> dict:
    """Quanto è tirato l'elastico verso il basso.

    Tre dimensioni combinate:
    - **RSI** strict oversold (<30 ideal, <35 warm, >45 nulla) — 0-40 pts
    - **Distanza da EMA50** in multipli di ATR (≥2 ATR sotto = stretch) — 0-40 pts
    - **Capitulation** 0-20 pts: drawdown primario + consecutive_down fallback.
      Il drawdown 5-bar in multipli di ATR cattura sia *flush verticali*
      (1 red candle -3×ATR) sia *slow bleed* (5+ candele piccole). La sequenza
      consecutive_down è usata come fallback quando il drawdown è marginale —
      cattura il "death by thousand cuts" che una sola big red day non mostra.

    Il massimo 100 richiede tutti e tre i fattori attivi. Un RSI a 28 ma
    price ancora sopra EMA50 non qualifica: potrebbe essere un dip già
    riassorbito.

    Args:
        drawdown_5d_atr: (peak_5d − current_price) / atr. Se None (backward
            compat), il capitulation score degrada a solo consecutive_down.
    """
    if any(pd.isna(x) for x in (rsi, close, ema_slow, atr)) or atr <= 0 or ema_slow <= 0:
        return {
            "score": 0.0,
            "rsi": None,
            "atr_distance_from_ema": None,
            "drawdown_5d_atr": drawdown_5d_atr,
            "consecutive_down": consecutive_down,
            "note": "dati insufficienti",
        }

    # Distanza dal EMA50 espressa in ATR: positiva se price sotto EMA50.
    atr_distance = (ema_slow - close) / atr if atr > 0 else 0.0

    # RSI component (0-40 punti)
    if rsi <= CONTRA_RSI_OVERSOLD:
        rsi_pts = 40.0
    elif rsi <= CONTRA_RSI_WARM:
        rsi_pts = 25.0
    elif rsi <= 40.0:
        rsi_pts = 10.0
    else:
        rsi_pts = 0.0

    # ATR distance component (0-40 punti) — elastico stirato
    if atr_distance >= 3.0:
        atr_pts = 40.0
    elif atr_distance >= CONTRA_ATR_DISTANCE_MIN:
        atr_pts = 30.0
    elif atr_distance >= 1.0:
        atr_pts = 15.0
    elif atr_distance >= 0.0:
        atr_pts = 5.0
    else:
        # price sopra EMA50 → non è oversold, è retest
        atr_pts = 0.0

    # Capitulation component (0-20 punti) — primario: drawdown 5d in ATR;
    # fallback: consecutive_down. Max dei due → cattura sia flush che slow bleed.
    dd_pts = 0.0
    if drawdown_5d_atr is not None and not pd.isna(drawdown_5d_atr):
        if drawdown_5d_atr >= 2.5:
            dd_pts = 20.0  # capitulation sharp (single big red candle o similar)
        elif drawdown_5d_atr >= 1.5:
            dd_pts = 15.0
        elif drawdown_5d_atr >= 0.7:
            dd_pts = 8.0
        elif drawdown_5d_atr >= 0.3:
            dd_pts = 3.0

    consec_pts = 0.0
    if consecutive_down >= 5:
        consec_pts = 20.0
    elif consecutive_down >= CONTRA_CONSECUTIVE_DOWN_DAYS:
        consec_pts = 15.0
    elif consecutive_down >= 2:
        consec_pts = 8.0

    # max(drawdown_pts, consecutive_pts) — il segnale più forte vince.
    # Evita double-counting: un flush con 1 big red day avrà alto dd e basso
    # consec (≤2), mentre uno slow bleed avrà basso dd e alto consec. Non
    # vogliamo sommarli altrimenti un setup "medio" ottiene il massimo.
    capitulation_pts = max(dd_pts, consec_pts)

    total = rsi_pts + atr_pts + capitulation_pts
    return {
        "score": min(100.0, total),
        "rsi": round(rsi, 2),
        "atr_distance_from_ema": round(atr_distance, 2),
        "drawdown_5d_atr": (
            round(drawdown_5d_atr, 2) if drawdown_5d_atr is not None else None
        ),
        "consecutive_down": consecutive_down,
        "rsi_pts": rsi_pts,
        "atr_pts": atr_pts,
        "capitulation_pts": capitulation_pts,
        "capitulation_source": "drawdown" if dd_pts >= consec_pts else "consecutive",
    }


def score_quality_gate(
    close: float,
    ema_200_weekly: float | None,
    distance_from_high: float | None,
) -> dict:
    """Il trend strutturale è ancora intatto? (hard gate contrarian).

    Due condizioni:
    - price sopra EMA200 weekly (il "long-term trend" non è rotto).
      Se sotto → quality collassa a 0 (falling knife, non mean reversion).
    - distance from 52w high tra -15% e -40% = sweet spot (correzione ma non
      crash). -5% = troppo poco stretched. -50%+ = non è più pullback, è
      downtrend.
    """
    if close is None or pd.isna(close):
        return {"score": 0.0, "above_ema200w": None, "note": "no price"}

    if ema_200_weekly is None or pd.isna(ema_200_weekly):
        # Fail-closed: senza EMA200 weekly non possiamo giudicare se il trend
        # strutturale regge. Azzera invece di passare come neutro — il quality
        # gate è per design hard filter, non soft proxy. Tipicamente succede
        # su IPO recenti (<60 settimane di storia). Il trader può valutare
        # a mano via propicks-scan + Perplexity, ma il contrarian engine skippa.
        return {
            "score": 0.0,
            "above_ema200w": None,
            "note": (
                "EMA200 weekly non disponibile (IPO recente <60w?) — "
                "gate fail-closed per sicurezza strutturale"
            ),
        }

    min_level = ema_200_weekly * (1.0 + CONTRA_MIN_EMA200_BUFFER)
    above_ema200w = close >= min_level
    if not above_ema200w:
        return {
            "score": 0.0,
            "above_ema200w": False,
            "ema_200_weekly": round(float(ema_200_weekly), 2),
            "note": "sotto EMA200w → trend rotto, skip mean reversion",
        }

    # Sopra EMA200w: modula il punteggio sulla profondità della correzione.
    if distance_from_high is None:
        depth_pts = 60.0
    else:
        dist = abs(distance_from_high)
        if 0.10 <= dist <= 0.25:
            depth_pts = 100.0  # sweet spot: -10% / -25%
        elif 0.05 <= dist < 0.10:
            depth_pts = 70.0   # dip superficiale
        elif 0.25 < dist <= 0.40:
            depth_pts = 60.0   # correzione profonda ma valida
        elif dist < 0.05:
            depth_pts = 30.0   # quasi all'high — non è oversold significativo
        else:
            depth_pts = 20.0   # > -40% — rischio downtrend

    return {
        "score": depth_pts,
        "above_ema200w": True,
        "ema_200_weekly": round(float(ema_200_weekly), 2),
        "distance_from_high_pct": distance_from_high,
    }


def score_market_context(
    regime_code: int | None,
    vix: float | None,
) -> dict:
    """Il macro contesto supporta mean reversion?

    Combina:
    - Regime fit inverso (lookup CONTRA_REGIME_FIT): NEUTRAL=100, BULL=70,
      BEAR=85, STRONG_BULL/STRONG_BEAR=0-25.
    - VIX level: >25 = paura/capitulazione (bonus +20), <14 = euforia
      (penalty -30). Tra 14 e 25 neutro.

    Output capped 0-100.
    """
    regime_fit = CONTRA_REGIME_FIT.get(regime_code, 50.0) if regime_code else 50.0

    vix_adjustment = 0.0
    vix_note = "VIX n/a"
    if vix is not None and not pd.isna(vix):
        if vix >= CONTRA_VIX_SPIKE:
            vix_adjustment = 20.0
            vix_note = f"VIX {vix:.1f} ≥ {CONTRA_VIX_SPIKE} (paura → edge)"
        elif vix <= CONTRA_VIX_COMPLACENT:
            vix_adjustment = -30.0
            vix_note = f"VIX {vix:.1f} ≤ {CONTRA_VIX_COMPLACENT} (euforia → edge collassa)"
        else:
            vix_note = f"VIX {vix:.1f} neutrale ({CONTRA_VIX_COMPLACENT}-{CONTRA_VIX_SPIKE})"

    total = regime_fit + vix_adjustment
    return {
        "score": max(0.0, min(100.0, total)),
        "regime_code": regime_code,
        "regime_fit": regime_fit,
        "vix": round(float(vix), 2) if vix is not None and not pd.isna(vix) else None,
        "vix_adjustment": vix_adjustment,
        "vix_note": vix_note,
    }


def score_reversion_potential(
    close: float,
    ema_slow: float,
    atr: float,
    stop_price: float,
) -> dict:
    """R/R teorico: reversion a EMA50 vs stop a -3×ATR.

    Il target naturale della strategia è la reversion al EMA50. Se lo stop
    è a -3 ATR e il target è EMA50, il R/R = (ema_slow - close) / (close - stop).

    - R/R ≥ 3.0 → 100 (asimmetria forte)
    - R/R ≥ 2.0 → 80 (soglia minima operativa)
    - R/R ≥ 1.5 → 55
    - R/R ≥ 1.0 → 30
    - R/R < 1.0 → 10 (setup rotto: il target è più vicino dello stop)

    Ritorna stop_price e target_price da usare downstream per sizing.
    """
    if any(pd.isna(x) for x in (close, ema_slow, atr, stop_price)) or close <= 0:
        return {"score": 0.0, "rr_ratio": None, "target": None, "stop": None}

    target = ema_slow  # reversion al EMA50 daily
    reward = target - close
    risk = close - stop_price

    if risk <= 0 or reward <= 0:
        return {
            "score": 0.0,
            "rr_ratio": None,
            "target": round(float(target), 2),
            "stop": round(float(stop_price), 2),
            "note": "reward o risk non positivo",
        }

    rr = reward / risk

    if rr >= 3.0:
        score = 100.0
    elif rr >= 2.0:
        score = 80.0
    elif rr >= 1.5:
        score = 55.0
    elif rr >= 1.0:
        score = 30.0
    else:
        score = 10.0

    return {
        "score": score,
        "rr_ratio": round(rr, 2),
        "target": round(float(target), 2),
        "stop": round(float(stop_price), 2),
        "reward": round(float(reward), 2),
        "risk": round(float(risk), 2),
    }


# ---------------------------------------------------------------------------
# Helpers puri
# ---------------------------------------------------------------------------
def _consecutive_down_bars(close: pd.Series) -> int:
    """Numero di barre consecutive con close < close precedente, dall'ultima indietro."""
    if len(close) < 2:
        return 0
    count = 0
    for i in range(len(close) - 1, 0, -1):
        if close.iloc[i] < close.iloc[i - 1]:
            count += 1
        else:
            break
    return count


def _drawdown_5d_atr(high: pd.Series, close: pd.Series, atr: float, window: int = 5) -> float | None:
    """Drawdown da peak-5d al current_close espresso in multipli di ATR.

    Misura di capitulation che cattura sia flush verticali (1 big red candle)
    sia slow bleed (5 small red candles). Positivo = drawdown, 0 = al massimo,
    negativo = current_close sopra il peak (non dovrebbe capitare visto che
    il peak include la barra corrente).

    Args:
        high: series High daily
        close: series Close daily
        atr: ATR corrente (scalar); se ≤ 0 ritorna None
        window: finestra di lookback in barre (default 5 = ~1 settimana)

    Returns:
        drawdown in ATR, o None se dati insufficienti.
    """
    if atr is None or atr <= 0 or pd.isna(atr):
        return None
    if high is None or close is None or len(high) < window or len(close) < 1:
        return None
    peak = float(high.tail(window).max())
    current = float(close.iloc[-1])
    if pd.isna(peak) or pd.isna(current):
        return None
    return (peak - current) / atr


# ---------------------------------------------------------------------------
# Regime hard-gate cap (mirror di etf_scoring.apply_regime_cap)
# ---------------------------------------------------------------------------
def apply_regime_cap(composite: float, regime_code: int | None) -> float:
    """Applica il cap superiore da regime hard-gate per la contrarian.

    La contrarian ha un edge che collassa agli estremi del ciclo:
    - STRONG_BULL (5): "oversold" sono solo dip superficiali, da gestire col
      momentum. Composite forzato a 0 per evitare Class A fuorvianti.
    - STRONG_BEAR (1): falling knives, non mean reversion. Composite forzato
      a 0 (coerente col gate della AI validation).
    - BULL/NEUTRAL/BEAR: nessun cap, composite libero.

    Se regime non disponibile → composite invariato (non si penalizza alla
    cieca, stesso principio dell'ETF cap).
    """
    if regime_code is None:
        return composite
    if regime_code in (1, 5):  # STRONG_BEAR / STRONG_BULL
        return 0.0
    return composite


# ---------------------------------------------------------------------------
# Classificazione
# ---------------------------------------------------------------------------
def classify_contra(score: float) -> str:
    """Tier contrarian — stessi cutoff del momentum ma semantica diversa.

    A ≥75 → setup oversold "pronto", entry con size piena contrarian (8%)
    B 60-74 → setup oversold "incubante", entry ridotta o wait
    C 45-59 → non abbastanza tirato o quality marginale
    D <45 → skip (trend rotto, market bullish, o R/R inadeguato)
    """
    if score >= CONTRA_SCORE_A:
        return "A — OVERSOLD READY"
    if score >= CONTRA_SCORE_B:
        return "B — OVERSOLD INCUBATING"
    if score >= CONTRA_SCORE_C:
        return "C — MARGINAL"
    return "D — SKIP"


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------
def analyze_contra_ticker(
    ticker: str,
    strategy: str | None = None,
    vix: float | None = None,
) -> dict | None:
    """Analizza un ticker per setup contrarian e ritorna dict completo.

    Args:
        ticker: ticker da analizzare
        strategy: tag strategia (default "Contrarian" se None nel CLI)
        vix: valore VIX corrente (iniettabile per batch; se None viene
            scaricato da yfinance — unica eccezione alla purezza, necessaria
            perché VIX è contesto di mercato, non specifico del ticker)

    Returns:
        dict con sub-score, composite, classification, parametri di trade
        (stop, target, R/R), regime, o None se dati insufficienti.
    """
    ticker = ticker.upper()
    try:
        hist = download_history(ticker)
    except DataUnavailable as err:
        print(f"[errore] {err}", file=sys.stderr)
        return None

    # Weekly per EMA200 weekly (quality gate) e regime
    regime: dict | None = None
    ema_200_weekly: float | None = None
    weekly: pd.DataFrame | None = None
    try:
        weekly = download_weekly_history(ticker)
        regime = classify_regime(weekly)
        ema_200_weekly = float(
            compute_ema(weekly["Close"], REGIME_WEEKLY_EMA_200D).iloc[-1]
        )
    except DataUnavailable as err:
        print(f"[warning] weekly non disponibile per {ticker}: {err}", file=sys.stderr)

    # VIX: scarica solo se non iniettato (batch più efficiente)
    if vix is None:
        vix_series = download_benchmark("^VIX", days=10)
        if vix_series is not None and not vix_series.empty:
            vix = float(vix_series.iloc[-1])

    # Earnings calendar: surface upcoming earnings per warning + flag post-flush.
    # Fail-open su yfinance error (non blocca lo scoring se data source giù).
    next_earnings_date: str | None = None
    days_to_earnings: int | None = None
    try:
        next_earnings_date = get_next_earnings_date(ticker)
    except Exception:
        next_earnings_date = None
    if next_earnings_date:
        try:
            from datetime import date as _date, datetime as _dt
            ed = _dt.strptime(next_earnings_date, "%Y-%m-%d").date()
            days_to_earnings = (ed - _date.today()).days
        except (ValueError, TypeError):
            days_to_earnings = None

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]

    ema_slow_s = compute_ema(close, EMA_SLOW)
    rsi_s = compute_rsi(close, RSI_PERIOD)
    atr_s = compute_atr(high, low, close, ATR_PERIOD)
    ema_target_s = compute_ema(close, CONTRA_TARGET_EMA_PERIOD)

    price = float(close.iloc[-1])
    ema_slow = float(ema_slow_s.iloc[-1])
    ema_target = float(ema_target_s.iloc[-1])
    rsi = float(rsi_s.iloc[-1])
    atr = float(atr_s.iloc[-1])
    high_52w = float(high.tail(min(252, len(high))).max())
    distance_from_high = (high_52w - price) / high_52w if high_52w > 0 else None
    consecutive_down = _consecutive_down_bars(close)
    drawdown_5d_atr = _drawdown_5d_atr(high, close, atr, window=5)

    # Stop contrarian: usa il minimo recente (ultime 5 barre) come anchor,
    # poi sottrai stop_atr_mult * ATR. Più robusto di price - atr_mult*ATR
    # perché tiene conto della capitulation recente.
    recent_low = float(low.tail(5).min())
    stop_price = recent_low - (atr * CONTRA_STOP_ATR_MULT)

    oversold = score_oversold(
        rsi, price, ema_slow, atr, consecutive_down,
        drawdown_5d_atr=drawdown_5d_atr,
    )
    quality = score_quality_gate(price, ema_200_weekly, distance_from_high)
    context = score_market_context(
        regime_code=regime["regime_code"] if regime else None,
        vix=vix,
    )
    reversion = score_reversion_potential(price, ema_target, atr, stop_price)

    composite_raw = (
        oversold["score"] * CONTRA_WEIGHT_OVERSOLD
        + quality["score"] * CONTRA_WEIGHT_QUALITY
        + context["score"] * CONTRA_WEIGHT_MARKET_CONTEXT
        + reversion["score"] * CONTRA_WEIGHT_REVERSION
    )
    composite_raw = max(0.0, min(100.0, composite_raw))

    # Hard gate #1: se quality = 0 (sotto EMA200w o EMA200w non disponibile)
    # il composite è azzerato. Mean reversion su trend rotto = falling knife.
    composite = composite_raw
    if quality["score"] == 0.0:
        composite = 0.0

    # Hard gate #2: regime cap per STRONG_BULL/STRONG_BEAR (edge collassato).
    # Coerente con la AI validation che skippa gli stessi regimi.
    regime_code = regime["regime_code"] if regime else None
    composite = apply_regime_cap(composite, regime_code)
    cap_triggered = composite < composite_raw

    return {
        "ticker": ticker,
        "strategy": strategy or "Contrarian",
        "asset_type": "STOCK",
        "price": round(price, 2),
        "ema_slow": round(ema_slow, 2),
        "ema_target": round(ema_target, 2),
        "ema_200_weekly": round(ema_200_weekly, 2) if ema_200_weekly is not None else None,
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "atr_pct": round(atr / price, 4) if price else None,
        "high_52w": round(high_52w, 2),
        "distance_from_high_pct": (
            round(distance_from_high, 4) if distance_from_high is not None else None
        ),
        "consecutive_down": consecutive_down,
        "drawdown_5d_atr": (
            round(drawdown_5d_atr, 2) if drawdown_5d_atr is not None else None
        ),
        "recent_low": round(recent_low, 2),
        "vix": round(vix, 2) if vix is not None else None,
        "scores": {
            "oversold": round(oversold["score"], 1),
            "quality": round(quality["score"], 1),
            "market_context": round(context["score"], 1),
            "reversion": round(reversion["score"], 1),
        },
        "sub_scores_detail": {
            "oversold": oversold,
            "quality": quality,
            "market_context": context,
            "reversion": reversion,
        },
        "score_composite": round(composite, 1),
        "score_composite_raw": round(composite_raw, 1),
        "regime_cap_applied": cap_triggered,
        "classification": classify_contra(composite),
        "stop_suggested": round(stop_price, 2),
        "stop_pct": round((stop_price - price) / price, 4) if price else None,
        "target_suggested": reversion.get("target"),
        "rr_ratio": reversion.get("rr_ratio"),
        "perf_1w": pct_change(close, 5),
        "perf_1m": pct_change(close, 21),
        "perf_3m": pct_change(close, 63),
        "regime": regime,
        "next_earnings_date": next_earnings_date,
        "days_to_earnings": days_to_earnings,
    }
