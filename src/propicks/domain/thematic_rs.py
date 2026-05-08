"""Relative Strength theme vs parent sector (puro).

Discrimina alfa tematico genuino da leveraged sector bet. Se un tematico
batte ^GSPC quasi tautologicamente in risk-on, ma NON batte il parent
sector, non c'è alfa: è solo concentration più alta del parent.

Layer puro: input pandas.Series weekly close del tematico e del parent.
Output dict con score 0-100 + componenti diagnostiche.

Pattern parallelo a ``etf_scoring.score_rs`` (RS vs benchmark) con due
differenze:
- Reference è il parent sector ETF, non il broad benchmark.
- Aggiunge correlation 60d daily come kill-switch (corr ≥ 0.85 → alfa
  dubbio, score forzato a 0 dal chiamante).
"""

from __future__ import annotations

import pandas as pd

from propicks.config import (
    THEMATIC_CORR_LOOKBACK_DAYS,
    THEMATIC_RS_EMA_WEEKS,
    THEMATIC_RS_LOOKBACK_WEEKS,
)


def _strip_tz(s: pd.Series) -> pd.Series:
    """yfinance restituisce indici tz-aware diversi per exchange (NYSE vs Xetra).

    Senza strip, l'inner join tra theme listato Milano e parent US Xetra/NYSE
    produce 0 righe anche su date uguali.
    """
    s = s.copy()
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s


def compute_rs_vs_parent(
    close_theme_weekly: pd.Series,
    close_parent_weekly: pd.Series,
    lookback: int = THEMATIC_RS_LOOKBACK_WEEKS,
    ema_span: int = THEMATIC_RS_EMA_WEEKS,
) -> dict:
    """RS theme/parent — level × slope, score 0-100.

    Logica:
    - rs_ratio_now = close(theme) / close(parent), normalizzato dal valore
      ``lookback`` weeks fa → 1.0 = pari parent, >1.0 = outperform parent.
    - rs_slope = variazione media settimanale RS line negli ultimi ``ema_span``
      periodi. Positivo = leadership tematica che accelera.
    - Score:
        level >= 1.05 + slope > 0  → 100 (alfa tematico in accelerazione)
        level >= 1.02 + slope > 0  → 85
        level >= 1.0  + slope > 0  → 70
        level >= 1.0  + slope <=0  → 55 (alfa stanco — watch)
        level <  1.0  + slope > 0  → 45 (recupero su parent)
        level <  1.0  + slope <=0  → 20 (lagger — solo leverage del parent)
        level < 0.95  + slope <=0  → 10
    """
    if close_theme_weekly is None or close_parent_weekly is None:
        return {"score": 50.0, "rs_ratio": None, "rs_slope": None, "note": "no parent"}

    theme = _strip_tz(close_theme_weekly)
    parent = _strip_tz(close_parent_weekly)

    joined = pd.concat(
        [theme.rename("theme"), parent.rename("parent")],
        axis=1,
        join="inner",
    ).dropna()

    min_bars = lookback + 3 * ema_span
    if len(joined) < min_bars:
        return {
            "score": 50.0,
            "rs_ratio": None,
            "rs_slope": None,
            "note": f"storia insufficiente RS theme/parent: {len(joined)} barre (richieste {min_bars})",
        }

    rs = joined["theme"] / joined["parent"]
    base = rs.iloc[-lookback - 1]
    if base <= 0:
        return {"score": 50.0, "rs_ratio": None, "rs_slope": None, "note": "base invalida"}
    rs_norm = rs / base
    rs_ratio = float(rs_norm.iloc[-1])
    rs_past = float(rs_norm.iloc[-ema_span - 1])
    rs_slope = (rs_ratio - rs_past) / ema_span

    if rs_ratio >= 1.05 and rs_slope > 0:
        score = 100.0
    elif rs_ratio >= 1.02 and rs_slope > 0:
        score = 85.0
    elif rs_ratio >= 1.0 and rs_slope > 0:
        score = 70.0
    elif rs_ratio >= 1.0:
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


def compute_correlation(
    close_theme_daily: pd.Series,
    close_parent_daily: pd.Series,
    lookback_days: int = THEMATIC_CORR_LOOKBACK_DAYS,
) -> float | None:
    """Correlazione daily-returns theme/parent su ``lookback_days`` (default 60).

    Ritorna None se storia insufficiente. Il chiamante (scoring) usa il
    valore vs ``THEMATIC_CORR_KILL_THRESHOLD`` per kill-switch:
    corr ≥ 0.85 → alfa tematico dubbio (è solo leverage del parent), score
    composite forzato a 0.

    60d è il default Antonacci/AFP standard per stabilità senza eccesso lag.
    """
    if close_theme_daily is None or close_parent_daily is None:
        return None

    theme = _strip_tz(close_theme_daily)
    parent = _strip_tz(close_parent_daily)

    joined = pd.concat(
        [theme.rename("theme"), parent.rename("parent")],
        axis=1,
        join="inner",
    ).dropna()

    if len(joined) < lookback_days + 1:
        return None

    rets = joined.tail(lookback_days + 1).pct_change().dropna()
    if len(rets) < lookback_days // 2:
        return None

    corr = rets["theme"].corr(rets["parent"])
    if pd.isna(corr):
        return None
    return float(corr)
