"""Regime composite z-score (Fase B.3 SIGNAL_ROADMAP).

Combina più indicatori in un single z-score adattivo + classifica in 5 bucket
mirror del weekly regime classifier esistente. Razionale: regime weekly
mirror Pine ha lag su turning point. Daily composite con leading indicators
(credit spread, breadth, vol) anticipa regime change di 1-3 settimane
tipicamente.

## Indicatori usati (B.3 minimal viable)

1. **HY OAS** (`BAMLH0A0HYM2` da FRED) — credit spread investment grade vs
   HY. Spike = stress credit precede stress equity. Edge documentato (vedi
   Adrian-Boyarchenko-Crump 2018, "Vulnerable Growth").
2. **Breadth** (% S&P 500 > MA200) — internal market participation. Top
   formato spesso da divergenza: indici tengono, breadth gira prima.
3. **VIX** (`VIXCLS` da FRED) — implied vol. High VIX = fear. Mean-reverting
   ma utile per filtrare BEAR vs NEUTRAL.

Espandibile con AAII bull-bear, put/call ratio, NAAIM, ecc. (Fase B.3.5+).

## Score composition

Z-score rolling 252 bar (1y) per stabilità adattiva. Ogni feature
trasformata in z-score. Sign convention: **positive z = bullish**:

- z_hy_oas_inv = -z(hy_oas)        # spread basso = bull
- z_breadth = z(breadth)            # breadth alto = bull
- z_vix_inv = -z(vix)                # vol bassa = bull

Composite = weighted average. Default 40% HY OAS + 40% breadth + 20% VIX.

## 5-bucket classification

Mirror del weekly classifier (regime_code 1-5):

| Composite z | Code | Label |
|-------------|------|-------|
| > +1.0 | 5 | STRONG_BULL |
| (+0.3, +1.0] | 4 | BULL |
| [-0.3, +0.3] | 3 | NEUTRAL |
| [-1.0, -0.3) | 2 | BEAR |
| < -1.0 | 1 | STRONG_BEAR |

## API

- ``compute_regime_z(hy_oas, breadth, vix, weights)`` — single point
- ``classify_regime_z(z) -> (code, label)`` — bucket
- ``compute_regime_series(...)`` — serie temporale

Pure functions. No I/O.

## Reference

- Adrian, Boyarchenko, Crump (2018), "Vulnerable Growth", *AER* 109(4)
- Whaley (2009), "Understanding VIX", *JPM*
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# Bucket boundaries per classification 5-level (z-score thresholds)
_BUCKET_THRESHOLDS = [
    (-math.inf, -1.0, 1, "STRONG_BEAR"),
    (-1.0, -0.3, 2, "BEAR"),
    (-0.3, 0.3, 3, "NEUTRAL"),
    (0.3, 1.0, 4, "BULL"),
    (1.0, math.inf, 5, "STRONG_BULL"),
]


def classify_regime_z(z: float) -> tuple[int, str]:
    """Bucket 1-5 dato z composite.

    Args:
        z: composite z-score (sign convention: positive = bullish).

    Returns:
        ``(regime_code, regime_label)`` matching weekly classifier convention.
    """
    if not np.isfinite(z):
        return 3, "NEUTRAL"
    for lo, hi, code, label in _BUCKET_THRESHOLDS:
        if lo < z <= hi:
            return code, label
    return 3, "NEUTRAL"


def compute_regime_z(
    z_hy_oas: float | None,
    z_breadth: float | None,
    z_vix: float | None = None,
    *,
    weight_hy_oas: float = 0.40,
    weight_breadth: float = 0.40,
    weight_vix: float = 0.20,
) -> float:
    """Composite z-score singolo da feature z-scores già calcolate.

    Sign convention input: z PRE-INVERSION (raw z di hy_oas, vix). La
    funzione applica inversione internamente per allineare a "positive =
    bullish".

    Args:
        z_hy_oas: raw z-score HY OAS (alto = stress credit). Se None,
            esclusa dalla weighted average.
        z_breadth: raw z-score breadth (alto = bull). Se None, esclusa.
        z_vix: raw z-score VIX (alto = fear). Se None, esclusa.
        weight_*: pesi delle feature. Re-normalizzati su feature non-None.

    Returns:
        Composite z-score. 0 se tutte None.
    """
    contribs: list[tuple[float, float]] = []  # (weighted_z, weight)

    if z_hy_oas is not None and np.isfinite(z_hy_oas):
        contribs.append((-z_hy_oas, weight_hy_oas))  # invert: low spread = bull
    if z_breadth is not None and np.isfinite(z_breadth):
        contribs.append((z_breadth, weight_breadth))
    if z_vix is not None and np.isfinite(z_vix):
        contribs.append((-z_vix, weight_vix))  # invert: low vol = bull

    if not contribs:
        return 0.0

    weight_sum = sum(w for _, w in contribs)
    if weight_sum <= 0:
        return 0.0

    return sum(z * w for z, w in contribs) / weight_sum


def _rolling_zscore(s: pd.Series, window: int = 252) -> pd.Series:
    """Rolling z-score: (x - mean_252) / std_252."""
    mean = s.rolling(window=window, min_periods=max(20, window // 4)).mean()
    std = s.rolling(window=window, min_periods=max(20, window // 4)).std(ddof=1)
    z = (s - mean) / std.replace(0, np.nan)
    return z


def build_daily_regime_series_from_fred(
    *,
    start: str = "2010-01-01",
    end: str | None = None,
    breadth_universe: dict[str, pd.DataFrame] | None = None,
    breadth_window: int = 200,
    zscore_window: int = 252,
    weights: tuple[float, float, float] = (0.40, 0.40, 0.20),
) -> pd.Series:
    """Helper di alto livello: fetcha FRED + computa breadth → ritorna ``regime_code`` series.

    Pronto per ``simulate_portfolio(regime_series=...)``. Source:
    - HY OAS via ``market.fred_client`` (BAMLH0A0HYM2)
    - VIX via FRED (VIXCLS)
    - Breadth: optional, calcolato da ``breadth_universe`` se passato

    Args:
        start, end: range fetch FRED.
        breadth_universe: optional dict {ticker: OHLCV df} per breadth interno.
            Se None, breadth feature esclusa (composite usa solo HY+VIX).
        breadth_window: MA period per breadth (default 200).
        zscore_window: lookback z-score (default 252).
        weights: (w_hy, w_breadth, w_vix). Default (0.40, 0.40, 0.20).

    Returns:
        ``pd.Series`` regime_code (1-5) indicizzata by date. Pronto come input
        a ``simulate_portfolio.regime_series``.
    """
    from propicks.market.fred_client import fetch_fred_series
    from propicks.domain.breadth import breadth_series as _breadth_series

    if end is None:
        end = pd.Timestamp.today().strftime("%Y-%m-%d")

    hy_d = fetch_fred_series("BAMLH0A0HYM2", start=start, end=end)
    hy = pd.Series(hy_d, dtype=float)
    if not hy.empty:
        hy.index = pd.to_datetime(hy.index)

    vix_d = fetch_fred_series("VIXCLS", start=start, end=end)
    vix = pd.Series(vix_d, dtype=float)
    if not vix.empty:
        vix.index = pd.to_datetime(vix.index)

    breadth = None
    if breadth_universe:
        breadth = _breadth_series(breadth_universe, window=breadth_window)

    df = compute_regime_series(
        hy_oas=hy if not hy.empty else None,
        breadth=breadth,
        vix=vix if not vix.empty else None,
        zscore_window=zscore_window,
        weights=weights,
    )
    if df.empty:
        return pd.Series(dtype=float, name="regime_code")
    return df["regime_code"].dropna()


def compute_regime_series(
    hy_oas: pd.Series | None = None,
    breadth: pd.Series | None = None,
    vix: pd.Series | None = None,
    *,
    zscore_window: int = 252,
    weights: tuple[float, float, float] = (0.40, 0.40, 0.20),
) -> pd.DataFrame:
    """Serie temporale completa: z per feature + composite + bucket code/label.

    Allinea le serie sull'intersezione delle date disponibili. Rolling z-score
    su ``zscore_window`` bar (default 1y). Composite + classificazione applicata
    per ogni data.

    Args:
        hy_oas: pd.Series HY OAS (raw values, NOT z), index by date.
        breadth: pd.Series breadth % above MA200 (0-100).
        vix: pd.Series VIX close.
        zscore_window: lookback per z-score rolling (default 252 = 1y).
        weights: (w_hy_oas, w_breadth, w_vix). Default (0.40, 0.40, 0.20).

    Returns:
        DataFrame con colonne:
        - ``z_hy_oas``, ``z_breadth``, ``z_vix``: z-scores grezzi
        - ``composite_z``: composite weighted score (sign-corrected)
        - ``regime_code``: 1-5
        - ``regime_label``: STRONG_BEAR..STRONG_BULL

        Index = union date (intersect feature provided). Rows con composite
        non-computabile (warmup zscore_window) hanno NaN.
    """
    series_dict: dict[str, pd.Series] = {}
    if hy_oas is not None and len(hy_oas) > 0:
        series_dict["hy_oas"] = hy_oas.astype(float)
    if breadth is not None and len(breadth) > 0:
        series_dict["breadth"] = breadth.astype(float)
    if vix is not None and len(vix) > 0:
        series_dict["vix"] = vix.astype(float)

    if not series_dict:
        return pd.DataFrame(
            columns=[
                "z_hy_oas", "z_breadth", "z_vix",
                "composite_z", "regime_code", "regime_label",
            ]
        )

    # Outer join: tutte le date disponibili. NaN sui mancanti per ogni feature.
    df = pd.concat(series_dict, axis=1)

    # Z-score rolling per ogni feature presente
    z_hy = (
        _rolling_zscore(df["hy_oas"], zscore_window)
        if "hy_oas" in df.columns else pd.Series(np.nan, index=df.index)
    )
    z_br = (
        _rolling_zscore(df["breadth"], zscore_window)
        if "breadth" in df.columns else pd.Series(np.nan, index=df.index)
    )
    z_vix_s = (
        _rolling_zscore(df["vix"], zscore_window)
        if "vix" in df.columns else pd.Series(np.nan, index=df.index)
    )

    w_hy, w_br, w_vix = weights

    # Composite: vectorized via apply (clean e leggibile, performance OK
    # per timeseries < 100k bars)
    composite = []
    codes = []
    labels = []
    for i in range(len(df)):
        z = compute_regime_z(
            float(z_hy.iloc[i]) if pd.notna(z_hy.iloc[i]) else None,
            float(z_br.iloc[i]) if pd.notna(z_br.iloc[i]) else None,
            float(z_vix_s.iloc[i]) if pd.notna(z_vix_s.iloc[i]) else None,
            weight_hy_oas=w_hy, weight_breadth=w_br, weight_vix=w_vix,
        )
        composite.append(z)
        if pd.isna(z) or not (
            pd.notna(z_hy.iloc[i]) or pd.notna(z_br.iloc[i]) or pd.notna(z_vix_s.iloc[i])
        ):
            codes.append(np.nan)
            labels.append(None)
        else:
            code, lab = classify_regime_z(z)
            codes.append(float(code))
            labels.append(lab)

    out = pd.DataFrame(
        {
            "z_hy_oas": z_hy,
            "z_breadth": z_br,
            "z_vix": z_vix_s,
            "composite_z": composite,
            "regime_code": codes,
            "regime_label": labels,
        },
        index=df.index,
    )
    return out
