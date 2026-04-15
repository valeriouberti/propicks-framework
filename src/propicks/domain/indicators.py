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


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """ADX di Wilder — misura la forza del trend, non la direzione.

    Implementazione speculare al blocco ADX del Pine weekly_regime_engine:
    smoothing RMA (equivalente a EMA con alpha=1/period) su TR, +DM, -DM.
    """
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm_s = pd.Series(plus_dm, index=high.index)
    minus_dm_s = pd.Series(minus_dm, index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm_s.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm_s.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD classico. Ritorna (macd_line, signal_line, histogram)."""
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist
