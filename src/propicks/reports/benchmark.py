"""Performance di un benchmark negli ultimi N giorni di calendario."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from propicks.market.yfinance_client import download_benchmark


def get_benchmark_performance(ticker: str, days: int) -> Optional[float]:
    """Performance % negli ultimi ``days`` giorni; None se dati insufficienti."""
    close = download_benchmark(ticker, days)
    if close is None or close.empty:
        return None

    end = datetime.now()
    start = end - timedelta(days=days)
    # Maschera tz-safe: confronto su pd.Timestamp allineato alla tz dell'indice
    start_ts = pd.Timestamp(start)
    if getattr(close.index, "tz", None) is not None:
        start_ts = start_ts.tz_localize(close.index.tz)
    mask = close.index >= start_ts
    if not mask.any():
        return None
    window = close[mask]
    price_start = float(window.iloc[0])
    price_end = float(window.iloc[-1])
    if price_start <= 0:
        return None
    return (price_end - price_start) / price_start * 100
