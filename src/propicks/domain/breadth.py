"""Market breadth indicators (Fase B.3 SIGNAL_ROADMAP).

Breadth = % di ticker dell'universe che soddisfano un criterio (es. price >
MA200). Leading indicator di regime turning point: top di mercato precedono
spesso un calo di breadth (mega-cap salgono mentre la maggioranza ha già
girato), bottom precedono spike di breadth (mass washout completa).

## API

- ``pct_above_ma(prices_at_t, ma_at_t)`` — single point-in-time
- ``breadth_series(ohlcv_universe, window=200, start=None, end=None)`` —
  series temporale calcolata per ogni trading day comune

Pure functions, no I/O. Caller decide come fornire i dati (yfinance cache,
synthetic, file, etc.).

## Performance

``breadth_series`` su universe 500 × 5y daily (~1.3M bar) gira in ~1-2s
con numpy vectorized. Cache opzionale via ``regime_history`` table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def pct_above_ma(
    current_prices: dict[str, float],
    ma_values: dict[str, float],
) -> float:
    """% ticker con ``current_price > ma_value`` (single point-in-time).

    Args:
        current_prices: {ticker: close_price}.
        ma_values: {ticker: moving average value}. Stessi ticker di
            ``current_prices``. Ticker mancanti in ``ma_values`` skippati
            (non count né numerator né denominator).

    Returns:
        Float in [0, 100]. 100 = tutti above MA. 0 = nessuno. ``50.0`` se
        no ticker valido.
    """
    above = 0
    total = 0
    for ticker, price in current_prices.items():
        ma = ma_values.get(ticker)
        if ma is None or not np.isfinite(ma) or not np.isfinite(price):
            continue
        total += 1
        if price > ma:
            above += 1
    if total == 0:
        return 50.0
    return round(100.0 * above / total, 4)


def breadth_series(
    ohlcv_universe: dict[str, pd.DataFrame],
    *,
    window: int = 200,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.Series:
    """Serie temporale breadth (% above MA{window}) sull'universe.

    Calcola per ogni trading day comune all'universe la % di ticker con
    ``Close > rolling_mean(Close, window)``.

    Args:
        ohlcv_universe: {ticker: DataFrame con 'Close' indicizzato by date}.
            Index tz-naive raccomandato (chi usa simulate_portfolio già
            normalizza).
        window: lookback MA in bar (default 200).
        start, end: range filter optional.

    Returns:
        ``pd.Series`` indicizzata by date, valori [0, 100]. Index = unione
        date dell'universe (dove abbastanza dati per MA).
    """
    if not ohlcv_universe:
        return pd.Series(dtype=float)

    # Computa close > MA window flag per ogni ticker, allineato per data
    flags: dict[str, pd.Series] = {}
    for ticker, df in ohlcv_universe.items():
        if df is None or "Close" not in df.columns or len(df) < window + 1:
            continue
        close = df["Close"].astype(float)
        ma = close.rolling(window=window, min_periods=window).mean()
        flag = (close > ma).astype(float)
        # Set NaN sui primi `window-1` bar (MA non ancora valido)
        flag = flag.where(ma.notna())
        flags[ticker] = flag

    if not flags:
        return pd.Series(dtype=float)

    # Allinea su union date
    panel = pd.DataFrame(flags)
    # Per ogni date, % ticker above MA (escludi NaN dal denominatore)
    pct = panel.mean(axis=1, skipna=True) * 100.0

    if start is not None:
        pct = pct[pct.index >= start]
    if end is not None:
        pct = pct[pct.index <= end]

    return pct.dropna().rename("breadth_pct_above_ma200")
