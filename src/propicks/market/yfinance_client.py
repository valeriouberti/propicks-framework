"""Adapter yfinance: l'unico modulo che parla con la rete.

Tenere il client isolato qui permette di:
- testare domain/ e io/ con fixture statiche (no HTTP)
- sostituire in futuro il provider (Alpha Vantage, IBKR) toccando solo questo file
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

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
    hist = hist.dropna(subset=["Close"])
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
    # Droppa la barra parziale del lunedì (Close=NaN su ticker thin pre-market):
    # le comparazioni con NaN sono silenziosamente False → fallback errato a NEUTRAL.
    hist = hist.dropna(subset=["Close"])
    if len(hist) < REGIME_MIN_WEEKLY_BARS:
        raise DataUnavailable(
            ticker,
            f"storia weekly insufficiente: {len(hist)} barre "
            f"(min {REGIME_MIN_WEEKLY_BARS})",
        )
    return hist


def download_benchmark(ticker: str, days: int) -> pd.Series | None:
    """Close series ≥ days giorni di calendario; None se dati insufficienti."""
    try:
        buffer = max(days + 10, 30)
        hist = yf.Ticker(ticker).history(period=f"{buffer}d", auto_adjust=False)
    except Exception as exc:
        print(f"[warning] benchmark {ticker} non disponibile: {exc}", file=sys.stderr)
        return None
    if hist is None or hist.empty:
        return None
    return hist["Close"].dropna()


def download_benchmark_weekly(ticker: str, period: str = "3y") -> pd.Series | None:
    """Close weekly del benchmark per calcoli RS su scala settimanale.

    Ritorna None (non solleva) se i dati non sono disponibili: l'ETF scoring
    deve poter fallback a RS = neutrale se il benchmark manca, invece di
    abortire tutta la scan.
    """
    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1wk", auto_adjust=False)
    except Exception as exc:
        print(f"[warning] benchmark weekly {ticker} non disponibile: {exc}", file=sys.stderr)
        return None
    if hist is None or hist.empty:
        return None
    return hist["Close"].dropna()


def get_ticker_sector(ticker: str) -> str | None:
    """Sector GICS-like del ticker via ``yf.Ticker(t).info``, o None.

    Yahoo Finance restituisce una taxonomy leggermente diversa da GICS puro
    ("Consumer Cyclical" invece di "Consumer Discretionary", ecc.). Il mapping
    verso i sector_key interni avviene in ``domain.stock_rs``.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        print(f"[warning] sector non disponibile per {ticker}: {exc}", file=sys.stderr)
        return None
    if not isinstance(info, dict):
        return None
    sector = info.get("sector")
    if not isinstance(sector, str) or not sector:
        return None
    return sector


def get_ticker_beta(ticker: str) -> float | None:
    """Beta vs mercato (di norma S&P500) via ``yf.Ticker(t).info['beta']``.

    Yahoo calcola il beta su 5 anni di dati mensili. Ritorna None se il dato
    non è disponibile (ETF, ticker esteri illiquidi, IPO recenti).
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        print(f"[warning] beta non disponibile per {ticker}: {exc}", file=sys.stderr)
        return None
    if not isinstance(info, dict):
        return None
    beta = info.get("beta")
    if beta is None:
        return None
    try:
        return float(beta)
    except (TypeError, ValueError):
        return None


def download_returns(tickers: list[str], period: str = "6mo") -> pd.DataFrame:
    """Daily returns DataFrame (colonne = ticker) per il periodo dato.

    Usa pct_change() su Close. Righe con tutti NaN (giorni di non-trading per
    tutti i ticker) vengono rimosse. Ritorna DataFrame vuoto se nessun dato.
    """
    if not tickers:
        return pd.DataFrame()
    try:
        data = yf.download(
            tickers=" ".join(tickers),
            period=period,
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        print(f"[warning] download returns fallito: {exc}", file=sys.stderr)
        return pd.DataFrame()

    if data is None or data.empty:
        return pd.DataFrame()

    closes = pd.DataFrame()
    for t in tickers:
        try:
            series = data[t]["Close"] if len(tickers) > 1 else data["Close"]
            closes[t] = series
        except (KeyError, IndexError):
            continue

    if closes.empty:
        return pd.DataFrame()
    return closes.pct_change().dropna(how="all")


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
