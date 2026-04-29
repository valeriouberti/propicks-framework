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
    # `np.where` con NaN propaga False sulle comparazioni → la prima barra
    # (high.diff() = NaN) collassa silenziosamente a 0.0, biasando lo
    # smoothing iniziale verso il basso. Manteniamo NaN finché c'è NaN
    # nelle componenti, allineato col comportamento di compute_atr.
    nan_mask = up_move.isna() | down_move.isna()
    plus_raw = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_raw = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm_s = pd.Series(plus_raw, index=high.index).where(~nan_mask)
    minus_dm_s = pd.Series(minus_raw, index=high.index).where(~nan_mask)

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


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (Granville 1963). Cumulative volume signed by close direction.

    Logica:
    - Se close[t] > close[t-1] → OBV[t] = OBV[t-1] + volume[t]
    - Se close[t] < close[t-1] → OBV[t] = OBV[t-1] - volume[t]
    - Se close[t] == close[t-1] → OBV[t] = OBV[t-1]

    Edge: divergenze OBV vs price (price up but OBV flat = weak rally).

    Args:
        close: pd.Series close prices.
        volume: pd.Series volume (stessa lunghezza/index).

    Returns:
        pd.Series OBV cumulative. Primo valore = 0.
    """
    if len(close) != len(volume):
        raise ValueError(f"length mismatch: close={len(close)} volume={len(volume)}")
    direction = close.diff().fillna(0)
    signed_volume = volume.where(direction > 0, -volume.where(direction < 0, 0)).fillna(0)
    return signed_volume.cumsum()


def compute_multi_lookback_momentum(
    close: pd.Series,
    lookbacks: tuple[int, ...] = (21, 63, 126, 252),
    *,
    skip_recent: int = 0,
) -> float | None:
    """Multi-lookback momentum ensemble (Fase C.6 SIGNAL_ROADMAP).

    Razionale (AQR / Asness): single-window momentum (es. 12-1) vulnerabile
    a single-window noise. Ensemble di più lookback → più robusto a regime
    change. Standard institutional: 1m + 3m + 6m + 12m.

    Formula:
        avg_log_return = mean(log(close[-1] / close[-lookback])) for each lookback

    Output in unità log-return (≈ percent change per piccoli valori).
    Caller decide come mapparlo in score [0, 100].

    Args:
        close: pd.Series close prices, indicizzata cronologica.
        lookbacks: tuple bar lookback (default 21/63/126/252 ≈ 1m/3m/6m/12m).
        skip_recent: skip ultimi N bar (Jegadeesh-Titman skip-1 month
            convention per evitare short-term reversal). Default 0 (no skip).

    Returns:
        Float = average log-return across lookbacks. None se dati insufficienti.

    Edge cases:
        - close ha < max(lookbacks) bar → None
        - close[-1] o close[-lookback] <= 0 → quel lookback skipped
    """
    if not lookbacks:
        return None
    n = len(close)
    max_lb = max(lookbacks)
    if n < max_lb + skip_recent + 1:
        return None
    # close at "now" (post skip)
    end_idx = n - 1 - skip_recent
    end_price = float(close.iloc[end_idx])
    if end_price <= 0:
        return None

    log_returns = []
    for lb in lookbacks:
        start_idx = end_idx - lb
        if start_idx < 0:
            continue
        start_price = float(close.iloc[start_idx])
        if start_price <= 0:
            continue
        log_returns.append(np.log(end_price / start_price))

    if not log_returns:
        return None
    return float(np.mean(log_returns))


def compute_accumulation_distribution(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """Accumulation/Distribution Line (Chaikin). Cumulative volume × CLV.

    Close Location Value (CLV):
        CLV = ((close - low) - (high - close)) / (high - low)
    Range [-1, +1]: +1 = close at high (accumulation), -1 = close at low
    (distribution).

    A/D Line = cumsum(CLV × volume).

    Edge: misura intra-bar buying/selling pressure pesata dal volume.
    Più informativa di OBV su singoli bar (OBV è binary up/down).

    Args:
        high, low, close: pd.Series OHLC.
        volume: pd.Series volume.

    Returns:
        pd.Series A/D line cumulative.
    """
    rng = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / rng
    clv = clv.fillna(0)
    return (clv * volume).cumsum()
