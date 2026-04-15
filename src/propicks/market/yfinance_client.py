"""Adapter yfinance: l'unico modulo che parla con la rete.

Tenere il client isolato qui permette di:
- testare domain/ e io/ con fixture statiche (no HTTP)
- sostituire in futuro il provider (Alpha Vantage, IBKR) toccando solo questo file
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf

from propicks.config import EMA_SLOW, REGIME_MIN_WEEKLY_BARS


@dataclass
class DataUnavailable(Exception):
    """yfinance non ha fornito dati utilizzabili per il ticker."""

    ticker: str
    message: str

    def __str__(self) -> str:
        return f"{self.ticker}: {self.message}"


def download_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Scarica lo storico OHLCV.

    ``min_bars`` = EMA_SLOW*3+5 garantisce stabilità dell'EMA50 (warm-up
    ~3x period) e spazio per il 52w high sul default ``period="1y"``.
    """
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    except Exception as exc:
        raise DataUnavailable(ticker, f"errore yfinance: {exc}") from exc
    if hist is None or hist.empty:
        raise DataUnavailable(ticker, "nessun dato disponibile (ticker non trovato?)")
    min_bars = EMA_SLOW * 3 + 5
    if len(hist) < min_bars:
        raise DataUnavailable(
            ticker,
            f"storia insufficiente: {len(hist)} barre (min {min_bars} per EMA{EMA_SLOW} stabile)",
        )
    return hist


def download_weekly_history(ticker: str, period: str = "3y") -> pd.DataFrame:
    """Scarica storico weekly per il regime classifier.

    Default ``period="3y"`` garantisce >= 150 barre settimanali, ampio margine
    oltre il warm-up ``REGIME_MIN_WEEKLY_BARS`` per stabilità di EMA40 + ADX.
    """
    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1wk", auto_adjust=False)
    except Exception as exc:
        raise DataUnavailable(ticker, f"errore yfinance (weekly): {exc}") from exc
    if hist is None or hist.empty:
        raise DataUnavailable(ticker, "nessun dato weekly disponibile")
    if len(hist) < REGIME_MIN_WEEKLY_BARS:
        raise DataUnavailable(
            ticker,
            f"storia weekly insufficiente: {len(hist)} barre "
            f"(min {REGIME_MIN_WEEKLY_BARS})",
        )
    return hist


def download_benchmark(ticker: str, days: int) -> Optional[pd.Series]:
    """Close series ≥ days giorni di calendario; None se dati insufficienti."""
    try:
        buffer = max(days + 10, 30)
        hist = yf.Ticker(ticker).history(period=f"{buffer}d", auto_adjust=False)
    except Exception as exc:
        print(f"[warning] benchmark {ticker} non disponibile: {exc}", file=sys.stderr)
        return None
    if hist is None or hist.empty:
        return None
    return hist["Close"]


def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Ultimo close per ticker. Batch via yf.download, fallback per-ticker."""
    if not tickers:
        return {}
    prices: dict[str, float] = {}

    try:
        data = yf.download(
            tickers=" ".join(tickers),
            period="5d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        print(f"[warning] download batch fallito: {exc}", file=sys.stderr)
        data = None

    if data is not None and not data.empty:
        for t in tickers:
            try:
                closes = data[t]["Close"] if len(tickers) > 1 else data["Close"]
                closes = closes.dropna()
                if not closes.empty:
                    prices[t] = float(closes.iloc[-1])
            except (KeyError, IndexError):
                pass

    for t in tickers:
        if t in prices:
            continue
        try:
            hist = yf.Ticker(t).history(period="5d", auto_adjust=False)
            if hist is not None and not hist.empty:
                prices[t] = float(hist["Close"].iloc[-1])
        except Exception as exc:
            print(f"[warning] prezzo non disponibile per {t}: {exc}", file=sys.stderr)
    return prices
