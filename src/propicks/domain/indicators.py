"""Indicatori tecnici puri su pandas.Series.

Nessuna dipendenza da I/O o yfinance: input/output sono solo Series,
così questi helper sono testabili senza rete.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.config import ATR_PERIOD, RSI_PERIOD


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average standard (adjust=False)."""
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI di Wilder con smoothing esponenziale (alpha = 1/period).

    Gestisce il caso degenere ``avg_loss == 0``: quando non ci sono loss,
    RSI è per definizione 100 (non NaN).
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100.0)


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = ATR_PERIOD,
) -> pd.Series:
    """Average True Range di Wilder."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def pct_change(close: pd.Series, bars: int) -> float | None:
    """Variazione % tra ``close.iloc[-bars-1]`` e l'ultimo close."""
    if len(close) <= bars:
        return None
    past = float(close.iloc[-bars - 1])
    now = float(close.iloc[-1])
    if past <= 0:
        return None
    return (now - past) / past
