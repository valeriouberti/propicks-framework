"""Earnings revision / surprise scoring (Fase B.2 SIGNAL_ROADMAP).

Razionale: Chan-Jegadeesh-Lakonishok (1996) "Momentum Strategies", *Journal of
Finance*. Trend EPS estimates 90d > price momentum su Sharpe netto. Edge
non-arbitraggiato perché deriva da analyst herding lento.

## Feature disponibili da yfinance

yfinance 1.2.2 espone:

1. **earnings_history** (DataFrame, ~4 quarter): `epsActual`, `epsEstimate`,
   `surprisePercent`. Track-record surprise — **usable per backtest**
   (point-in-time se filtrate by date < bar_date).
2. **earnings_estimate** (snapshot): `avg`, `growth`, `numberOfAnalysts`.
   Current consensus per next quarter / year. **NOT historical** —
   inutilizzabile per backtest, ok per live signal.
3. **eps_revisions** (snapshot): `upLast30days`, `downLast30days`.
   Net revisions count. **NOT historical** — same caveat.
4. **earnings_dates** (DataFrame, mix past+future): `Reported EPS`,
   `Surprise(%)`. Storico — usable per backtest.

## Score components

Lo scoring B.2 combina:

1. **avg_surprise_4q**: media `surprisePercent` ultimi 4 quarter
   (track record beat/miss consensus). **HISTORICAL-SAFE**.
2. **surprise_trend**: surprise[-1] - mean(surprise[-4:-1]) (improving track).
   **HISTORICAL-SAFE**.
3. **net_revisions_30d**: `upLast30days - downLast30days` per next quarter.
   **CURRENT-ONLY** — solo per live signal, NOT backtest.
4. **growth_consensus**: `growth` field current. **CURRENT-ONLY**.

Per backtest historical, attive solo features 1+2. Per live signal, tutte.

## Output

``score_earnings_revision(...)`` ritorna float [0, 100] con:

- 50 = neutral / no information
- > 50 = positive revision/surprise momentum (entry favor)
- < 50 = negative (entry penalty / contrarian gate trigger)

## API

- ``score_earnings_revision(avg_surprise_pct, surprise_trend_pct,
  net_revisions_30d, growth_consensus_pct, n_analysts)``
- ``score_earnings_history_only(avg_surprise_pct, surprise_trend_pct)``
  — variante backtest-safe (solo features storiche)

Tutte pure functions. NaN/None inputs → 50 (neutral, no penalty).

## Reference

Chan, Jegadeesh, Lakonishok (1996), "Momentum Strategies", *Journal of
Finance* 51(5), 1681-1713.
"""

from __future__ import annotations

import math


def _is_valid(x) -> bool:
    """Check x è float finito (non None, non NaN)."""
    if x is None:
        return False
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def score_earnings_history_only(
    avg_surprise_pct: float | None,
    surprise_trend_pct: float | None,
) -> float:
    """Score [0, 100] dalle sole features storiche (backtest-safe).

    Usa avg surprise track record + trend ultimo quarter vs media. Combinato
    50% avg + 50% trend.

    Args:
        avg_surprise_pct: media surprise % ultimi 4 quarter (es. 5.0 = +5%
            avg beat). None se historical insufficient.
        surprise_trend_pct: surprise % ultimo quarter MENO media precedente
            (es. +3.0 = miglioramento).

    Returns:
        Score [0, 100]. 50 se entrambi None. Mappatura:
        - avg_surprise: ±10% → ±25 punti (50→75 a +10%, 50→25 a -10%)
        - trend: ±5% → ±25 punti
    """
    if not _is_valid(avg_surprise_pct) and not _is_valid(surprise_trend_pct):
        return 50.0

    score = 50.0
    if _is_valid(avg_surprise_pct):
        # Saturate a ±10% per evitare estremi
        avg_clipped = max(-10.0, min(10.0, float(avg_surprise_pct)))
        score += avg_clipped * 2.5  # ±25 punti

    if _is_valid(surprise_trend_pct):
        trend_clipped = max(-5.0, min(5.0, float(surprise_trend_pct)))
        score += trend_clipped * 5.0  # ±25 punti

    return _clip(score)


def score_earnings_revision(
    avg_surprise_pct: float | None = None,
    surprise_trend_pct: float | None = None,
    net_revisions_30d: int | float | None = None,
    growth_consensus_pct: float | None = None,
    n_analysts: int | None = None,
) -> float:
    """Score [0, 100] full feature set (live signal mode).

    Combina:
    - 30% avg_surprise (track record)
    - 20% surprise_trend (improving/deteriorating)
    - 25% net_revisions_30d (forward sentiment)
    - 15% growth_consensus (forward EPS growth expected)
    - 10% n_analysts coverage (proxy quality, ≥10 = full)

    Args:
        avg_surprise_pct: media surprise % ultimi 4q (es. 5.0).
        surprise_trend_pct: trend surprise (es. +3.0 = improving).
        net_revisions_30d: up_30d - down_30d (es. +3 = 3 net up).
        growth_consensus_pct: growth y/y forward (es. 0.15 = 15%).
        n_analysts: # analyst covering (es. 30).

    Returns:
        Score [0, 100]. 50 se tutti i feature None.

    Edge cases:
        - tutti None → 50 (neutral, no information)
        - sottoinsieme None → contributo 0 da quelle features
        - growth_consensus può essere frazionale (0.15) o percentuale (15.0):
          accept entrambi, scaling auto via magnitude
    """
    score = 50.0
    n_features_used = 0

    if _is_valid(avg_surprise_pct):
        avg_clipped = max(-10.0, min(10.0, float(avg_surprise_pct)))
        score += avg_clipped * 1.5  # ±15 punti (peso 30% × 50 max)
        n_features_used += 1

    if _is_valid(surprise_trend_pct):
        trend_clipped = max(-5.0, min(5.0, float(surprise_trend_pct)))
        score += trend_clipped * 2.0  # ±10 punti (peso 20% × 50)
        n_features_used += 1

    if _is_valid(net_revisions_30d):
        # Net revisions: ±5 → ±12.5 punti (peso 25% × 50)
        net_clipped = max(-5.0, min(5.0, float(net_revisions_30d)))
        score += net_clipped * 2.5
        n_features_used += 1

    if _is_valid(growth_consensus_pct):
        gc = float(growth_consensus_pct)
        # Auto-detect frazionale vs percentuale
        if abs(gc) < 1.0:
            gc *= 100.0
        gc_clipped = max(-30.0, min(30.0, gc))
        score += gc_clipped * 0.25  # ±7.5 punti (peso 15% × 50)
        n_features_used += 1

    if _is_valid(n_analysts) and n_analysts is not None:
        # Coverage: 0-10 → 0-5 punti, ≥10 → 5 punti (peso 10% × 50)
        n = min(10, int(n_analysts))
        score += (n - 5) * 0.5  # baseline 5 = 0 punti, sopra premio
        n_features_used += 1

    if n_features_used == 0:
        return 50.0

    return _clip(score)


def has_falling_knife_signal(
    avg_surprise_pct: float | None,
    surprise_trend_pct: float | None,
    *,
    threshold_avg: float = -10.0,
    threshold_trend: float = -5.0,
) -> bool:
    """True se earnings collapse pattern (filter contrarian "no falling knife").

    Razionale: stock oversold con earnings revisione drasticamente negative =
    falling knife. Reject entry contrarian per evitare catch-the-knife.

    Args:
        avg_surprise_pct: media surprise % ultimi 4q.
        surprise_trend_pct: trend recente.
        threshold_avg: avg sotto cui flagga (default -10% = miss consensus
            di 10% in media).
        threshold_trend: trend sotto cui flagga (default -5% = miss
            peggiora 5% vs trimestri precedenti).

    Returns:
        True se HA segnale falling-knife (suggerisce reject entry).
    """
    if not _is_valid(avg_surprise_pct) and not _is_valid(surprise_trend_pct):
        return False  # No data → no signal, no reject (don't penalize unknown)
    if _is_valid(avg_surprise_pct) and float(avg_surprise_pct) <= threshold_avg:
        return True
    if _is_valid(surprise_trend_pct) and float(surprise_trend_pct) <= threshold_trend:
        return True
    return False


def compute_features_from_history(
    surprise_pcts: list[float | None],
) -> tuple[float | None, float | None]:
    """Calcola (avg_surprise, surprise_trend) da lista historical surprise.

    Args:
        surprise_pcts: lista cronologica (oldest first → newest last) di
            surprise %. None per quarter senza data. Tipicamente 4-8 quarters.

    Returns:
        (avg, trend):
        - avg: media degli ultimi 4 surprise validi. None se < 2 valid.
        - trend: surprise[-1] − media(surprise[-4:-1]). None se < 4 valid.
    """
    valid = [
        float(x) for x in surprise_pcts
        if _is_valid(x)
    ]
    if len(valid) < 2:
        return None, None

    last_4 = valid[-4:] if len(valid) >= 4 else valid
    avg = sum(last_4) / len(last_4)

    if len(valid) >= 4:
        last_one = valid[-1]
        prev_3_avg = sum(valid[-4:-1]) / 3
        trend = last_one - prev_3_avg
    else:
        trend = None

    return round(avg, 4), round(trend, 4) if trend is not None else None
