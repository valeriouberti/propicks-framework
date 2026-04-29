"""Cross-asset macro overlay per ETF rotation (Fase B.5 SIGNAL_ROADMAP).

Razionale: sector rotation guidato da macro più di price momentum interno.
Yield curve, credit spread, USD, commodity ratios sono leading indicators
di sector performance documentati in Faber 2007 + Antonacci 2014.

## Macro features

5 indicatori cross-asset, tutti convertibili in z-score rolling 252d:

| Feature | Source | Sign convention (z positive = ...) |
|---------|--------|-------------------------------------|
| yield_slope | FRED `T10Y2Y` | curve steepening (banks/cyclicals favor) |
| usd_inv | FRED `DTWEXBGS` (inverted) | dollar weak (commodity favor) |
| hy_oas_inv | FRED `BAMLH0A0HYM2` (inverted) | credit calm (bull) |
| copper_gold | yfinance HG=F / GC=F | global growth signal |
| oil_gold | yfinance CL=F / GC=F | inflation / energy strength |

## Sector sensitivity matrix

Pre-defined matrice 11 sector ETF × 5 macro features. Sensitivity ∈ [-1, +1]:
positive = favor, negative = headwind. Macro fit composite:

    macro_fit_z = sum(macro_z[f] * sensitivity[etf][f]) / sum(|sensitivity|)

Normalizzato a [0, 100]: macro_fit = 50 + 25 * macro_fit_z (clip 0-100).

## Sector mapping rationale

- **XLF (Financials)**: yield curve steepening = NIM expansion. HY OAS calm
  helpful (credit risk). USD neutral.
- **XLE (Energy)**: oil/gold strong = energy strength. USD weak = commodity
  bull. Yield curve neutral.
- **XLK (Tech)**: yield curve steepening = headwind (long duration assets,
  rate sensitive). HY OAS calm helpful (refinancing). USD neutral-negative.
- **XLU (Utilities)**: rate-sensitive defensive. Yield slope steepening =
  headwind. Oil weak helpful (input costs).
- **XLI (Industrials)**: copper/gold strong = global growth bull. Yield
  curve cyclical. HY OAS calm.
- **XLY (Consumer Disc)**: yield curve cyclical. USD weak = exports favor.
- **XLP (Consumer Staples)**: defensive, less sensitivity. Slight negative
  yield curve (risk-off favor).
- **XLV (Healthcare)**: defensive. USD weak = international revenue.
- **XLB (Materials)**: copper/gold pure exposure. USD weak.
- **XLRE (REITs)**: rate-sensitive. Yield slope = headwind quando rises.
- **XLC (Communication)**: tech-like. HY OAS sensitive.

## Public API

- ``compute_copper_gold_ratio(copper_close, gold_close) -> float``
- ``compute_oil_gold_ratio(oil_close, gold_close) -> float``
- ``compute_macro_zscores(features_series_dict, window=252) -> pd.DataFrame``
- ``macro_fit_score(etf, macro_z_dict) -> float [0, 100]``
- ``SECTOR_SENSITIVITY_MATRIX``: dict pubblica per inspection / customization

## Reference

- Faber (2007), "A Quantitative Approach to Tactical Asset Allocation"
- Antonacci (2014), *Dual Momentum Investing*
- Conover-Jensen-Johnson (2008), "Sector Rotation and Monetary Conditions"
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Sector sensitivity matrix (Faber-style ETF rotation)
# ---------------------------------------------------------------------------
# Sensitivity values [-1, +1]. Positive = sector favored quando feature z > 0.
# Negative = sector hurt. 0 = no sensitivity.
SECTOR_SENSITIVITY_MATRIX: dict[str, dict[str, float]] = {
    "XLF": {  # Financials
        "yield_slope": +1.0,   # NIM expansion
        "usd_inv": +0.2,       # weak USD slight favor
        "hy_oas_inv": +0.5,    # credit calm = bank balance sheets ok
        "copper_gold": +0.3,
        "oil_gold": 0.0,
    },
    "XLE": {  # Energy
        "yield_slope": 0.0,
        "usd_inv": +0.5,        # weak USD = commodity bull
        "hy_oas_inv": +0.3,
        "copper_gold": +0.5,
        "oil_gold": +1.0,       # primary driver
    },
    "XLK": {  # Technology
        "yield_slope": -0.3,   # long duration headwind
        "usd_inv": -0.2,
        "hy_oas_inv": +0.5,    # credit calm = refinancing easy
        "copper_gold": 0.0,
        "oil_gold": 0.0,
    },
    "XLU": {  # Utilities
        "yield_slope": -0.5,   # rate-sensitive defensive
        "usd_inv": 0.0,
        "hy_oas_inv": +0.3,
        "copper_gold": 0.0,
        "oil_gold": -0.3,      # high oil = input cost
    },
    "XLI": {  # Industrials
        "yield_slope": +0.3,
        "usd_inv": -0.2,        # strong USD penalize multinationals
        "hy_oas_inv": +0.4,
        "copper_gold": +0.7,    # global growth proxy
        "oil_gold": +0.3,
    },
    "XLY": {  # Consumer Discretionary
        "yield_slope": +0.3,    # cyclical
        "usd_inv": -0.3,        # weak USD = exports
        "hy_oas_inv": +0.4,
        "copper_gold": +0.3,
        "oil_gold": -0.3,       # high oil = consumer hurt
    },
    "XLP": {  # Consumer Staples
        "yield_slope": -0.2,    # defensive favor risk-off
        "usd_inv": 0.0,
        "hy_oas_inv": +0.2,
        "copper_gold": 0.0,
        "oil_gold": -0.2,
    },
    "XLV": {  # Healthcare
        "yield_slope": 0.0,
        "usd_inv": -0.2,        # weak USD = intl revenue
        "hy_oas_inv": +0.2,
        "copper_gold": 0.0,
        "oil_gold": 0.0,
    },
    "XLB": {  # Materials
        "yield_slope": +0.2,
        "usd_inv": -0.5,        # weak USD bullish commodity
        "hy_oas_inv": +0.3,
        "copper_gold": +1.0,    # primary driver
        "oil_gold": +0.3,
    },
    "XLRE": {  # Real Estate
        "yield_slope": -0.5,    # rate-sensitive
        "usd_inv": 0.0,
        "hy_oas_inv": +0.3,
        "copper_gold": 0.0,
        "oil_gold": -0.3,
    },
    "XLC": {  # Communication Services
        "yield_slope": 0.0,
        "usd_inv": -0.2,
        "hy_oas_inv": +0.4,
        "copper_gold": 0.0,
        "oil_gold": 0.0,
    },
}


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------
def compute_copper_gold_ratio(copper_close: float, gold_close: float) -> float:
    """Copper/Gold price ratio. Higher = global growth signal."""
    if copper_close <= 0 or gold_close <= 0:
        return float("nan")
    return copper_close / gold_close


def compute_oil_gold_ratio(oil_close: float, gold_close: float) -> float:
    """Oil/Gold price ratio. Higher = inflation / energy strength."""
    if oil_close <= 0 or gold_close <= 0:
        return float("nan")
    return oil_close / gold_close


def compute_macro_zscores(
    features: dict[str, pd.Series],
    *,
    window: int = 252,
) -> pd.DataFrame:
    """Z-score rolling per ogni feature. Sign convention applicata.

    Args:
        features: dict {feature_name: pd.Series}. Keys raccomandate:
            - 'yield_slope' (raw, no inversion)
            - 'usd' (raw, sarà inverted internamente)
            - 'hy_oas' (raw, sarà inverted)
            - 'copper_gold' (raw)
            - 'oil_gold' (raw)
        window: lookback z-score (default 252 = 1y).

    Returns:
        DataFrame con colonne sign-corrected (positive = bullish/favor).
        Index = union date di tutte le features.
    """
    if not features:
        return pd.DataFrame()

    # Allinea su union date
    df_raw = pd.concat(features, axis=1)

    out = pd.DataFrame(index=df_raw.index)
    for feature_name, series in features.items():
        s = series.reindex(df_raw.index)
        z = _rolling_zscore(s, window)
        # Sign convention: applica inversione per features dove "alto = bear"
        if feature_name in ("usd",):
            out["usd_inv"] = -z
        elif feature_name in ("hy_oas",):
            out["hy_oas_inv"] = -z
        else:
            out[feature_name] = z
    return out


def _rolling_zscore(s: pd.Series, window: int) -> pd.Series:
    mean = s.rolling(window=window, min_periods=max(20, window // 4)).mean()
    std = s.rolling(window=window, min_periods=max(20, window // 4)).std(ddof=1)
    return (s - mean) / std.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Sector macro fit scoring
# ---------------------------------------------------------------------------
def macro_fit_score(
    etf: str,
    macro_z: dict[str, float],
    sensitivity_matrix: dict[str, dict[str, float]] | None = None,
) -> float:
    """Score [0, 100] del fit macro per un ETF dato z-scores correnti.

    Formula:
        weighted_z = sum(z[f] * sens[etf][f]) for valid features
        norm_factor = sum(|sens[etf][f]|) for valid features
        normalized_z = weighted_z / norm_factor   # in ~[-1, +1] tipicamente
        score = 50 + 25 * normalized_z            # mappato in [0, 100]
        clipped to [0, 100]

    Args:
        etf: ticker ETF (es. 'XLF', 'XLE', ...). Case-insensitive.
        macro_z: dict {feature_name: z_score}. Esempio:
            {'yield_slope': 0.5, 'usd_inv': -0.2, 'hy_oas_inv': 0.8,
             'copper_gold': 0.1, 'oil_gold': -0.3}
        sensitivity_matrix: matrice custom; default = SECTOR_SENSITIVITY_MATRIX.

    Returns:
        Float [0, 100]. 50 se ETF non in matrix o tutti i feature None.
    """
    matrix = sensitivity_matrix or SECTOR_SENSITIVITY_MATRIX
    etf_upper = etf.upper()
    if etf_upper not in matrix:
        return 50.0
    sens = matrix[etf_upper]

    weighted = 0.0
    norm = 0.0
    for feature, sensitivity in sens.items():
        z = macro_z.get(feature)
        if z is None or not math.isfinite(z) or sensitivity == 0:
            continue
        weighted += z * sensitivity
        norm += abs(sensitivity)

    if norm <= 0:
        return 50.0

    normalized_z = weighted / norm
    score = 50.0 + 25.0 * normalized_z
    return max(0.0, min(100.0, score))


def macro_fit_series(
    etf: str,
    macro_z_df: pd.DataFrame,
    sensitivity_matrix: dict[str, dict[str, float]] | None = None,
) -> pd.Series:
    """Serie temporale macro_fit per un ETF dato DataFrame di z-scores.

    Args:
        etf: ticker ETF.
        macro_z_df: DataFrame da ``compute_macro_zscores``. Colonne =
            feature names sign-corrected.
        sensitivity_matrix: opzionale.

    Returns:
        pd.Series indicizzata by date, valori [0, 100].
    """
    if macro_z_df is None or macro_z_df.empty:
        return pd.Series(dtype=float)
    out = []
    for ts, row in macro_z_df.iterrows():
        z_dict = {
            col: float(row[col])
            for col in macro_z_df.columns
            if pd.notna(row[col])
        }
        out.append(macro_fit_score(etf, z_dict, sensitivity_matrix))
    return pd.Series(out, index=macro_z_df.index, name=f"macro_fit_{etf.upper()}")
