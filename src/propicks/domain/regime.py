"""Classificatore di regime macro settimanale.

Porta in Python la logica del Pine ``weekly_regime_engine.pine``. NON
duplica i trigger di entry del Pine daily: qui si risponde solo a una
domanda — *"il contesto macro supporta long su questo titolo?"*.

Bucket ritornati (mirror esatto del Pine, linee 108-112):

    5 = STRONG_BULL   close sopra tutte le EMA, trend_bull, ADX strong, momentum bull
    4 = BULL          EMA fast > slow e close > EMA 200d, momentum bull
    3 = NEUTRAL       tutto il resto
    2 = BEAR          EMA fast < slow e close < EMA 200d, momentum bear
    1 = STRONG_BEAR   close sotto tutte le EMA, trend_bear, ADX strong, momentum bear

STRONG_BULL/STRONG_BEAR richiedono esplicitamente ``trend_bull``/``trend_bear``:
``above_all`` da solo include configurazioni di pullback (price sopra EMA200
ma EMA fast in catch-up sotto EMA slow) che non meritano l'etichetta più alta.

``entry_allowed = regime >= 3`` è lo stesso filtro del Pine.
"""

from __future__ import annotations

import pandas as pd

from propicks.config import (
    REGIME_ADX_PERIOD,
    REGIME_ADX_STRONG,
    REGIME_ADX_WEAK,
    REGIME_MIN_WEEKLY_BARS,
    REGIME_WEEKLY_EMA_200D,
    REGIME_WEEKLY_EMA_FAST,
    REGIME_WEEKLY_EMA_SLOW,
    RSI_PERIOD,
)
from propicks.domain.indicators import (
    compute_adx,
    compute_ema,
    compute_macd,
    compute_rsi,
)

_BUCKETS = {
    5: "STRONG_BULL",
    4: "BULL",
    3: "NEUTRAL",
    2: "BEAR",
    1: "STRONG_BEAR",
}


def classify_regime(weekly: pd.DataFrame) -> dict | None:
    """Classifica il regime macro da un DataFrame OHLC settimanale.

    Args:
        weekly: DataFrame con colonne ``High``, ``Low``, ``Close`` indicizzato
            per settimana (output tipico di ``yf.Ticker.history(interval='1wk')``).

    Returns:
        dict con ``regime``, ``regime_code`` (1-5), ``entry_allowed``,
        ``adx``, ``trend``, ``momentum``, ``rsi``, ``macd_hist``, o
        ``None`` se le barre disponibili sono sotto la soglia di warm-up.
    """
    if weekly is None or len(weekly) < REGIME_MIN_WEEKLY_BARS:
        return None

    # Difesa in profondità: se il caller non ha già droppato la barra parziale
    # (Close=NaN su ticker thin pre-market), comparazioni con NaN falliscono
    # silenziosamente e il bucket match collassa a NEUTRAL.
    weekly = weekly.dropna(subset=["Close"])
    if len(weekly) < REGIME_MIN_WEEKLY_BARS:
        return None

    close = weekly["Close"]
    high = weekly["High"]
    low = weekly["Low"]

    ema_fast = compute_ema(close, REGIME_WEEKLY_EMA_FAST).iloc[-1]
    ema_slow = compute_ema(close, REGIME_WEEKLY_EMA_SLOW).iloc[-1]
    ema_200d = compute_ema(close, REGIME_WEEKLY_EMA_200D).iloc[-1]
    adx = float(compute_adx(high, low, close, REGIME_ADX_PERIOD).iloc[-1])
    rsi = float(compute_rsi(close, RSI_PERIOD).iloc[-1])

    macd_line, signal_line, hist = compute_macd(close)
    macd_bull = macd_line.iloc[-1] > signal_line.iloc[-1]
    macd_hist = float(hist.iloc[-1])

    price = float(close.iloc[-1])
    ema_fast = float(ema_fast)
    ema_slow = float(ema_slow)
    ema_200d = float(ema_200d)

    trend_bull = ema_fast > ema_slow and price > ema_200d
    trend_bear = ema_fast < ema_slow and price < ema_200d
    trend_strong = adx > REGIME_ADX_STRONG
    trend_weak = adx < REGIME_ADX_WEAK

    momentum_bull = rsi > 50 and macd_bull
    momentum_bear = rsi < 50 and not macd_bull

    above_all = price > ema_fast and price > ema_slow and price > ema_200d
    below_all = price < ema_fast and price < ema_slow and price < ema_200d

    if above_all and trend_bull and trend_strong and momentum_bull:
        code = 5
    elif trend_bull and momentum_bull:
        code = 4
    elif below_all and trend_bear and trend_strong and momentum_bear:
        code = 1
    elif trend_bear and momentum_bear:
        code = 2
    else:
        code = 3

    trend_label = "BULL" if trend_bull else "BEAR" if trend_bear else "MIXED"
    momentum_label = (
        "BULL" if momentum_bull else "BEAR" if momentum_bear else "MIXED"
    )
    strength_label = "STRONG" if trend_strong else "WEAK" if trend_weak else "MODERATE"

    return {
        "regime": _BUCKETS[code],
        "regime_code": code,
        "entry_allowed": code >= 3,
        "price": round(price, 2),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "ema_200d": round(ema_200d, 2),
        "adx": round(adx, 1),
        "trend": trend_label,
        "trend_strength": strength_label,
        "momentum": momentum_label,
        "rsi": round(rsi, 1),
        "macd_hist": round(macd_hist, 3),
    }
