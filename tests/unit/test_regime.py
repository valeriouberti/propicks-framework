"""Test del regime classifier settimanale."""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.config import REGIME_MIN_WEEKLY_BARS
from propicks.domain.regime import classify_regime


def _ohlc(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
        }
    )


def test_regime_none_when_too_few_bars():
    close = pd.Series(np.linspace(100, 110, 10))
    assert classify_regime(_ohlc(close)) is None


def test_regime_strong_bull_on_steady_uptrend():
    # Trend forte monotono → close sopra tutte le EMA + ADX alto + momentum bull
    n = 120
    close = pd.Series(np.linspace(80, 180, n))
    result = classify_regime(_ohlc(close))
    assert result is not None
    assert result["entry_allowed"] is True
    assert result["regime_code"] == 5
    assert result["regime"] == "STRONG_BULL"


def test_regime_strong_bear_on_steady_downtrend():
    n = 120
    close = pd.Series(np.linspace(180, 80, n))
    result = classify_regime(_ohlc(close))
    assert result is not None
    assert result["entry_allowed"] is False
    assert result["regime_code"] == 1
    assert result["regime"] == "STRONG_BEAR"


def test_regime_neutral_on_mixed_tape():
    # Trend rialzista che stalla: primi 2/3 in salita, ultimo terzo flat.
    # EMA fast si appiattisce, ADX cala, momentum diventa mixed → NEUTRAL.
    rising = np.linspace(80, 130, 80)
    flat = np.linspace(130, 128, 40)
    close = pd.Series(np.concatenate([rising, flat]))
    result = classify_regime(_ohlc(close))
    assert result is not None
    assert result["regime_code"] == 3
    assert result["regime"] == "NEUTRAL"
    # NEUTRAL permette ancora l'entry (è solo BEAR/STRONG_BEAR a bloccarla)
    assert result["entry_allowed"] is True


def test_regime_drops_trailing_nan_bar():
    """Bug LTMC.MI (2026-04-20): yfinance ritorna la barra in corso con
    Close=NaN su ticker thin pre-market. Le comparazioni con NaN sono
    silenziosamente False, trend_bull e trend_bear entrambi falsi →
    fallback errato a NEUTRAL invece del vero STRONG_BULL.
    """
    n = 120
    close = pd.Series(np.linspace(80, 180, n))
    ohlc = _ohlc(close)
    # Appendi una barra parziale con Close=NaN come farebbe yfinance lunedì mattina
    nan_row = pd.DataFrame(
        {"High": [np.nan], "Low": [np.nan], "Close": [np.nan]},
        index=[ohlc.index[-1] + 1],
    )
    with_nan = pd.concat([ohlc, nan_row])
    result = classify_regime(with_nan)
    assert result is not None
    assert result["regime_code"] == 5
    assert result["regime"] == "STRONG_BULL"


def test_regime_reports_expected_fields():
    n = max(REGIME_MIN_WEEKLY_BARS, 100)
    close = pd.Series(np.linspace(100, 130, n))
    result = classify_regime(_ohlc(close))
    assert result is not None
    expected_keys = {
        "regime",
        "regime_code",
        "entry_allowed",
        "price",
        "ema_fast",
        "ema_slow",
        "ema_200d",
        "adx",
        "trend",
        "trend_strength",
        "momentum",
        "rsi",
        "macd_hist",
    }
    assert expected_keys.issubset(result.keys())
