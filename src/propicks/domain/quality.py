"""Quality scoring (Fase B.4 SIGNAL_ROADMAP).

Razionale: Asness-Frazzini-Pedersen (2013), "Quality Minus Junk" (AQR working
paper). Quality + momentum > momentum puro su Sharpe netto (~+0.3).

## Features

Da yfinance ``info`` snapshot (current state, NO point-in-time):

1. **ROA** (`returnOnAssets`) — proxy ROIC. Higher = capital efficient.
2. **Gross Margin** (`grossMargins`) — Gross Profit / Revenue. Higher = pricing
   power / moat (Novy-Marx 2013 "Other side of value")
3. **Debt/Equity** (`debtToEquity`) — leverage. Lower = financial soundness
   (penalty quando alto)

## Scoring

Combine in single [0, 100]:

- score_roa: ±20% → ±25 punti (50→75 a +20%)
- score_gross_margin: ±50% → ±25 punti
- score_debt_equity (inverted): ±150 ratio → ∓25 punti (alto D/E penalty)

Composite default (1/3 each); pesi tunabili.

## Filter mode

In ``simulate_portfolio`` integration: usato come **gate filter top tercile**
(T67+ percentile cross-sectional) prima entry, NON come overlay (Fase B.2
caveat). Filter mantiene un universe quality-controlled: low-quality ticker
non entrano mai in trade, indipendentemente dal momentum score.

## Caveat critico — look-ahead bias

yfinance ``info`` ritorna **current snapshot** (TTM). Backtest historical
con quality filter applica filter "current quality" a entry passate →
look-ahead. Coerente con caveat B.2 (earnings revision). Per OOS proper
serve historical fundamentals (paid: Compustat, Sharadar; free limited:
SimFin).

## API

- ``compute_roa_score(roa) -> [0, 100]``
- ``compute_gross_margin_score(gm) -> [0, 100]``
- ``compute_debt_equity_score(de) -> [0, 100]``
- ``score_quality(roa, gm, de, weights) -> [0, 100]``

Pure functions. None inputs → component score 50 (neutral, no contribution).

## Reference

- Asness, Frazzini, Pedersen (2013), "Quality Minus Junk", AQR
- Novy-Marx (2013), "The other side of value: The gross profitability premium"
"""

from __future__ import annotations

import math


def _is_valid(x) -> bool:
    if x is None:
        return False
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def compute_roa_score(roa: float | None) -> float:
    """Score [0, 100] da ROA (returnOnAssets).

    Mappatura: ROA 0% → 50, ROA +20% → 100, ROA -20% → 0. Linear con
    saturazione ±20%.

    yfinance returnOnAssets è frazione (es. 0.244 = 24.4%). Auto-detect se
    valore < 1 (frazionale) vs > 1 (percentuale).
    """
    if not _is_valid(roa):
        return 50.0
    val = float(roa)
    # Auto-scale: yfinance ROA frazionale tipicamente |val| < 1.
    # Se |val| > 1, probabilmente già in unità %.
    if abs(val) <= 1.0:
        val *= 100.0
    saturated = max(-20.0, min(20.0, val))
    return _clip(50.0 + saturated * 2.5)  # ±50 da saturated×2.5


def compute_gross_margin_score(gm: float | None) -> float:
    """Score [0, 100] da gross margin.

    Gross margin: 0% → 25, 25% → 50, 50%+ → 75-100. Mid-cap industrial
    tipico ~25-30%, software ~70-90%, retail ~20-40%.

    Mappatura: gm 25% baseline (50), saturazione ±50% (max 100, min 0).
    """
    if not _is_valid(gm):
        return 50.0
    val = float(gm)
    if abs(val) <= 1.0:
        val *= 100.0
    # Center 25%, saturate ±50% → ±50 punti
    centered = val - 25.0
    saturated = max(-50.0, min(50.0, centered))
    return _clip(50.0 + saturated)  # 1pp = 1 score point


def compute_debt_equity_score(de: float | None) -> float:
    """Score [0, 100] da Debt/Equity ratio (inverted: low D/E = good).

    yfinance debtToEquity tipicamente in % (es. 102.63 = 1.026 ratio).
    Auto-scale: se abs(val) < 5, treated as ratio; altrimenti %.

    Mappatura: D/E 0% → 100 (no debt), 100% → 50 (1:1 ratio), 200%+ → 0
    (highly leveraged). Saturazione ±200%.
    """
    if not _is_valid(de):
        return 50.0
    val = float(de)
    # Auto-detect ratio vs %: yfinance tipicamente in % (es. 102.63 = 1:1)
    if abs(val) <= 5.0:
        val *= 100.0
    # 0% = 100 (best), 100% = 50, 200% = 0 (worst)
    # Linear: score = 100 - val/2, clip
    return _clip(100.0 - val / 2.0)


def score_quality(
    roa: float | None = None,
    gross_margin: float | None = None,
    debt_equity: float | None = None,
    *,
    weight_roa: float = 1 / 3,
    weight_gross_margin: float = 1 / 3,
    weight_debt_equity: float = 1 / 3,
) -> float:
    """Composite quality score [0, 100].

    Weighted average dei 3 sub-score. Pesi default 1/3 each. Re-normalize
    su feature non-None — feature mancanti non penalizzano (neutral
    contribution invece di NaN propagation).

    Args:
        roa: returnOnAssets (yfinance frazione o %).
        gross_margin: grossMargins (yfinance frazione o %).
        debt_equity: debtToEquity (yfinance ratio o %).
        weight_*: pesi delle 3 feature.

    Returns:
        Composite [0, 100]. 50 se tutti None.
    """
    contribs: list[tuple[float, float]] = []
    if _is_valid(roa):
        contribs.append((compute_roa_score(roa), weight_roa))
    if _is_valid(gross_margin):
        contribs.append((compute_gross_margin_score(gross_margin), weight_gross_margin))
    if _is_valid(debt_equity):
        contribs.append((compute_debt_equity_score(debt_equity), weight_debt_equity))

    if not contribs:
        return 50.0
    weight_sum = sum(w for _, w in contribs)
    if weight_sum <= 0:
        return 50.0
    return sum(s * w for s, w in contribs) / weight_sum


def is_above_quality_tercile(
    quality_scores: dict[str, float],
    *,
    percentile: float = 67.0,
) -> dict[str, bool]:
    """Cross-sectional: per ciascun ticker True se score >= P-th percentile.

    Args:
        quality_scores: {ticker: score}. None values esclusi dal cross-section.
        percentile: threshold (default 67 = top tercile T67+).

    Returns:
        Dict {ticker: bool}. Ticker con score None mappati a False (non
        passing — quality unknown = excluded).
    """
    valid = {t: s for t, s in quality_scores.items() if _is_valid(s)}
    if len(valid) < 2:
        # Insufficient data per percentile: tutti pass (no filter)
        return {t: True for t in quality_scores}

    import numpy as np
    cutoff = float(np.percentile(list(valid.values()), percentile))

    out: dict[str, bool] = {}
    for ticker, score in quality_scores.items():
        if not _is_valid(score):
            out[ticker] = False
        else:
            out[ticker] = float(score) >= cutoff
    return out
