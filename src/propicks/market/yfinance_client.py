"""Adapter yfinance cache-aware — l'unico modulo che parla con la rete.

Tenere il client isolato qui permette di:
- testare domain/ e io/ con fixture statiche (no HTTP)
- sostituire in futuro il provider (Alpha Vantage, IBKR) toccando solo questo file

## Cache layer (Phase 2)

Pattern **read-through con TTL** su SQLite:

- ``download_history``, ``download_weekly_history``, ``download_benchmark``,
  ``download_benchmark_weekly`` → miss-then-fetch: se la cache ha righe fresche
  (``fetched_at >= now - TTL``) le ritorna, altrimenti fetcha da yfinance,
  UPSERT, ritorna.
- ``get_ticker_sector``, ``get_ticker_beta`` → cached in ``market_ticker_meta``
  con TTL 7gg (questi campi cambiano di rado).
- ``get_current_prices`` → read-through del close più recente dal daily cache.

**Correttezza della freshness**: daily TTL 8h copre l'intera sessione di trading
(scan alle 15:00 riusa cache di scan alle 9:00). Weekly TTL 7gg copre il
settimanale stabile post-Fri close. Meta TTL 7gg è conservativo per beta
(Yahoo lo ricalcola settimanale).

**Public API invariata**: firma identica alla versione pre-cache. Callers
(domain, CLI, dashboard) non cambiano.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from propicks.config import (
    EARNINGS_CACHE_TTL_HOURS,
    EMA_SLOW,
    MARKET_CACHE_TTL_DAILY_HOURS,
    MARKET_CACHE_TTL_META_HOURS,
    MARKET_CACHE_TTL_WEEKLY_HOURS,
    MARKET_MIN_DAILY_BARS,
    REGIME_MIN_WEEKLY_BARS,
)
from propicks.io.db import (
    market_earnings_read,
    market_earnings_upsert,
    market_meta_read,
    market_meta_upsert,
    market_ohlcv_is_fresh,
    market_ohlcv_read,
    market_ohlcv_upsert,
)
from propicks.obs.log import get_logger

_log = get_logger("market.yfinance")


@dataclass
class DataUnavailable(Exception):
    """yfinance non ha fornito dati utilizzabili per il ticker."""

    ticker: str
    message: str

    def __str__(self) -> str:
        return f"{self.ticker}: {self.message}"


# ---------------------------------------------------------------------------
# Helpers pd.DataFrame ↔ cache rows
# ---------------------------------------------------------------------------
_YF_TO_DB_COLS_DAILY = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


def _yf_df_to_cache_rows(df: pd.DataFrame, date_col: str) -> list[dict]:
    """Converte un DataFrame yfinance in list[dict] per market_ohlcv_upsert.

    ``date_col``: ``"date"`` per daily, ``"week_start"`` per weekly.
    Preserva la tz-naive ISO date come stringa YYYY-MM-DD.
    """
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for ts, row in df.iterrows():
        # Normalizza tz-aware → naive ISO date
        if hasattr(ts, "tz_localize") and ts.tzinfo is not None:
            ts = ts.tz_convert(None).tz_localize(None)
        date_str = ts.strftime("%Y-%m-%d")
        close = row.get("Close")
        if pd.isna(close):
            continue  # skip bar con close invalido
        bars.append({
            date_col: date_str,
            "open": float(row.get("Open")) if not pd.isna(row.get("Open")) else None,
            "high": float(row.get("High")) if not pd.isna(row.get("High")) else None,
            "low": float(row.get("Low")) if not pd.isna(row.get("Low")) else None,
            "close": float(close),
            "adj_close": (
                float(row.get("Adj Close"))
                if "Adj Close" in row and not pd.isna(row.get("Adj Close"))
                else None
            ),
            "volume": (
                int(row.get("Volume"))
                if "Volume" in row and not pd.isna(row.get("Volume"))
                else None
            ),
        })
    return bars


def _cache_rows_to_yf_df(rows: list[dict]) -> pd.DataFrame:
    """Ricostruisce un DataFrame yfinance-like da list[dict] della cache.

    Output columns: Open, High, Low, Close, Adj Close, Volume — identiche
    a ``yf.Ticker.history()`` con auto_adjust=False. Index: DatetimeIndex
    tz-naive (i consumer etf_scoring gestiscono già tz-naive).
    """
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["date"])
    df = df.set_index("Date").sort_index()
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adj_close": "Adj Close",
            "volume": "Volume",
        }
    )
    return df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]


# ---------------------------------------------------------------------------
# Daily history
# ---------------------------------------------------------------------------
def _yf_fetch_daily(ticker: str, period: str) -> pd.DataFrame:
    """Fetch diretto da yfinance (no cache). Raw call per fallback/refresh."""
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    except Exception as exc:
        raise DataUnavailable(ticker, f"errore yfinance: {exc}") from exc
    if hist is None or hist.empty:
        raise DataUnavailable(ticker, "nessun dato disponibile (ticker non trovato?)")
    return hist.dropna(subset=["Close"])


def download_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Scarica lo storico OHLCV daily, cache-aware.

    Se la cache ha righe fresh (fetched_at < TTL) per questo ticker,
    ritorna dalla cache. Altrimenti fetcha yfinance, UPSERT, ritorna.

    ``min_bars = EMA_SLOW*3+5 = 155`` garantisce stabilità dell'EMA50 e
    spazio per il 52w high. Invariato dal pre-cache.
    """
    ticker = ticker.upper()

    # Fast path: cache fresh
    if market_ohlcv_is_fresh(ticker, "daily", MARKET_CACHE_TTL_DAILY_HOURS):
        rows = market_ohlcv_read(ticker, "daily")
        df = _cache_rows_to_yf_df(rows)
        if len(df) >= MARKET_MIN_DAILY_BARS:
            _log.debug("cache_hit_daily", extra={"ctx": {"ticker": ticker, "bars": len(df)}})
            return df
        # Meno barre richieste → forza refresh (potrebbe essere ticker giovane)

    # Miss o stale: fetch + upsert
    hist = _yf_fetch_daily(ticker, period)
    rows = _yf_df_to_cache_rows(hist, "date")
    market_ohlcv_upsert(ticker, "daily", rows)

    if len(hist) < MARKET_MIN_DAILY_BARS:
        raise DataUnavailable(
            ticker,
            f"storia insufficiente: {len(hist)} barre "
            f"(min {MARKET_MIN_DAILY_BARS} per EMA{EMA_SLOW} stabile)",
        )
    _log.debug("cache_miss_daily", extra={"ctx": {"ticker": ticker, "fetched": len(rows)}})
    return hist


# ---------------------------------------------------------------------------
# Weekly history
# ---------------------------------------------------------------------------
def _yf_fetch_weekly(ticker: str, period: str) -> pd.DataFrame:
    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1wk", auto_adjust=False)
    except Exception as exc:
        raise DataUnavailable(ticker, f"errore yfinance (weekly): {exc}") from exc
    if hist is None or hist.empty:
        raise DataUnavailable(ticker, "nessun dato weekly disponibile")
    return hist.dropna(subset=["Close"])


def download_weekly_history(ticker: str, period: str = "3y") -> pd.DataFrame:
    """Scarica storico weekly per il regime classifier, cache-aware.

    Default ``period="3y"`` garantisce >= 150 barre settimanali, ampio margine
    oltre il warm-up ``REGIME_MIN_WEEKLY_BARS`` per stabilità di EMA40 + ADX.
    """
    ticker = ticker.upper()

    if market_ohlcv_is_fresh(ticker, "weekly", MARKET_CACHE_TTL_WEEKLY_HOURS):
        rows = market_ohlcv_read(ticker, "weekly")
        df = _cache_rows_to_yf_df(rows)
        if len(df) >= REGIME_MIN_WEEKLY_BARS:
            return df

    hist = _yf_fetch_weekly(ticker, period)
    rows = _yf_df_to_cache_rows(hist, "week_start")
    market_ohlcv_upsert(ticker, "weekly", rows)

    if len(hist) < REGIME_MIN_WEEKLY_BARS:
        raise DataUnavailable(
            ticker,
            f"storia weekly insufficiente: {len(hist)} barre "
            f"(min {REGIME_MIN_WEEKLY_BARS})",
        )
    return hist


# ---------------------------------------------------------------------------
# Benchmark daily (used by rare callers that want a Close series)
# ---------------------------------------------------------------------------
def download_benchmark(ticker: str, days: int) -> pd.Series | None:
    """Close series ≥ days giorni di calendario; None se dati insufficienti.

    Cache-aware: usa il daily cache se fresh. Altrimenti fetch best-effort,
    NON solleva su errore — callers come exposure analysis devono poter
    continuare anche senza benchmark.
    """
    ticker = ticker.upper()
    try:
        if market_ohlcv_is_fresh(ticker, "daily", MARKET_CACHE_TTL_DAILY_HOURS):
            rows = market_ohlcv_read(ticker, "daily")
            df = _cache_rows_to_yf_df(rows)
            if not df.empty:
                return df["Close"]
    except Exception as exc:
        _log.warning("benchmark_cache_read_fail", extra={"ctx": {"ticker": ticker, "error": str(exc)}})

    # Fallback / cache miss: fetch diretto con buffer
    try:
        buffer = max(days + 10, 30)
        hist = yf.Ticker(ticker).history(period=f"{buffer}d", auto_adjust=False)
    except Exception as exc:
        _log.warning(
            "yf_benchmark_unavailable",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )
        return None
    if hist is None or hist.empty:
        return None
    # Popola cache anche sul fetch on-demand (benefici ai prossimi call)
    hist = hist.dropna(subset=["Close"])
    try:
        market_ohlcv_upsert(ticker, "daily", _yf_df_to_cache_rows(hist, "date"))
    except Exception:
        pass  # non bloccare il caller per un problema di persistenza
    return hist["Close"]


def download_benchmark_weekly(ticker: str, period: str = "3y") -> pd.Series | None:
    """Close weekly del benchmark — cache-aware, None-safe.

    Non solleva: ETF scoring deve poter fallback a RS = neutrale se il
    benchmark manca, invece di abortire tutta la scan.
    """
    ticker = ticker.upper()
    try:
        if market_ohlcv_is_fresh(ticker, "weekly", MARKET_CACHE_TTL_WEEKLY_HOURS):
            rows = market_ohlcv_read(ticker, "weekly")
            df = _cache_rows_to_yf_df(rows)
            if not df.empty:
                return df["Close"]
    except Exception as exc:
        _log.warning("benchmark_weekly_cache_fail", extra={"ctx": {"ticker": ticker, "error": str(exc)}})

    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1wk", auto_adjust=False)
    except Exception as exc:
        _log.warning(
            "yf_benchmark_weekly_unavailable",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )
        return None
    if hist is None or hist.empty:
        return None
    hist = hist.dropna(subset=["Close"])
    try:
        market_ohlcv_upsert(ticker, "weekly", _yf_df_to_cache_rows(hist, "week_start"))
    except Exception:
        pass
    return hist["Close"]


# ---------------------------------------------------------------------------
# Ticker meta (sector, beta, name)
# ---------------------------------------------------------------------------
def _yf_fetch_info(ticker: str) -> dict | None:
    """Raw yfinance info call. Lento (~500ms) → aggressivamente cachato."""
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        _log.warning(
            "yf_info_unavailable",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )
        return None
    return info if isinstance(info, dict) else None


def get_ticker_sector(ticker: str) -> str | None:
    """Sector GICS-like del ticker — cache-aware (TTL 7gg).

    Yahoo Finance restituisce una taxonomy leggermente diversa da GICS puro
    ("Consumer Cyclical" invece di "Consumer Discretionary", ecc.). Il mapping
    verso i sector_key interni avviene in ``domain.stock_rs``.
    """
    ticker = ticker.upper()
    cached = market_meta_read(ticker, MARKET_CACHE_TTL_META_HOURS)
    if cached is not None and cached.get("sector") is not None:
        return cached["sector"]

    info = _yf_fetch_info(ticker)
    if info is None:
        return None
    sector = info.get("sector")
    sector = sector if isinstance(sector, str) and sector else None

    # Popola cache anche se sector è None, con beta se disponibile nello stesso call
    beta_raw = info.get("beta")
    beta = None
    if beta_raw is not None:
        try:
            beta = float(beta_raw)
        except (TypeError, ValueError):
            beta = None
    name = info.get("shortName") or info.get("longName")
    market_meta_upsert(ticker, sector=sector, beta=beta, name=name)
    return sector


def get_next_earnings_date(ticker: str, *, force_refresh: bool = False) -> str | None:
    """Ritorna next earnings date ISO (``YYYY-MM-DD``) o None. Cache TTL 7gg.

    Yahoo espone la earnings date via ``yf.Ticker(t).calendar`` (dict) o
    ``get_earnings_dates(limit=N)`` (DataFrame con date future + past).
    Per robustezza priviligiamo ``calendar`` quando disponibile, con fallback
    a ``get_earnings_dates``.

    ``force_refresh=True`` bypass cache.

    Returns None se:
    - earnings non pubblicati / ticker non ha earnings (ETF, index)
    - yfinance fallisce o ritorna date passate
    - ticker non valido

    Note: questo call può essere lento (~300-500ms per info). Il TTL 7gg
    riduce fortemente il carico.
    """
    ticker = ticker.upper()

    if not force_refresh:
        cached = market_earnings_read(ticker, EARNINGS_CACHE_TTL_HOURS)
        if cached is not None:
            return cached

    # Fetch from yfinance
    earnings_iso: str | None = None
    try:
        tk = yf.Ticker(ticker)
        # Pattern 1: .calendar (new API)
        cal = getattr(tk, "calendar", None)
        if isinstance(cal, dict):
            date_val = cal.get("Earnings Date")
            if isinstance(date_val, list) and date_val:
                # Prende la prima data futura nella lista
                import pandas as _pd
                for d in date_val:
                    if hasattr(d, "strftime"):
                        earnings_iso = d.strftime("%Y-%m-%d")
                        break
                    if isinstance(d, str):
                        earnings_iso = d
                        break
                    try:
                        earnings_iso = _pd.to_datetime(d).strftime("%Y-%m-%d")
                        break
                    except Exception:
                        continue

        # Pattern 2: get_earnings_dates (fallback o supplementare)
        if earnings_iso is None:
            try:
                import pandas as _pd
                df = tk.get_earnings_dates(limit=12)
                if df is not None and not df.empty:
                    today = _pd.Timestamp.now(tz=df.index.tz) if df.index.tz is not None else _pd.Timestamp.now()
                    future = df.index[df.index >= today]
                    if len(future) > 0:
                        earnings_iso = future[0].strftime("%Y-%m-%d")
            except Exception:
                pass
    except Exception as exc:
        _log.warning(
            "yf_earnings_unavailable",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )

    # Sanity: se la data è nel passato, scartala
    if earnings_iso:
        from datetime import date as _date
        from datetime import datetime as _dt
        try:
            parsed = _dt.strptime(earnings_iso, "%Y-%m-%d").date()
            if parsed < _date.today():
                earnings_iso = None
        except ValueError:
            earnings_iso = None

    # Cache sia il valore (anche None, per evitare re-fetch spam)
    market_earnings_upsert(ticker, earnings_iso)
    return earnings_iso


def get_earnings_revision_metrics(
    ticker: str,
    *,
    force_refresh: bool = False,
) -> dict:
    """Fetch + cache earnings revision/surprise metrics (Fase B.2 SIGNAL_ROADMAP).

    Combina 3 source yfinance:
    - ``earnings_history`` → avg_surprise_4q + surprise_trend (storiche)
    - ``earnings_estimate`` → growth_consensus + n_analysts (current)
    - ``eps_revisions`` → net_revisions_30d (current)

    Cache TTL 7gg (revisioni cambiano lente, surprise solo dopo earnings call).

    Args:
        ticker: simbolo yfinance (case-insensitive).
        force_refresh: bypass cache.

    Returns:
        Dict con keys ``avg_surprise_4q``, ``surprise_trend``,
        ``growth_consensus``, ``net_revisions_30d``, ``n_analysts``,
        ``fetched_at``. Ogni valore può essere ``None`` se yfinance non lo
        espone per il ticker (ETF, IPO recente, micro-cap).
    """
    from propicks.io.db import (
        market_earnings_revision_read,
        market_earnings_revision_upsert,
    )
    from propicks.domain.earnings_revision import compute_features_from_history

    ticker = ticker.upper()
    # TTL 7gg = 168h. Riusa MARKET_CACHE_TTL_META_HOURS (config) se diverso.
    ttl_hours = 24 * 7

    if not force_refresh:
        cached = market_earnings_revision_read(ticker, ttl_hours)
        if cached is not None:
            return cached

    # Cache miss / forced — fetch yfinance
    import yfinance as yf
    yt = yf.Ticker(ticker)

    avg_surprise_4q: float | None = None
    surprise_trend: float | None = None
    growth_consensus: float | None = None
    net_revisions_30d: int | None = None
    n_analysts: int | None = None

    # 1. earnings_history → surprise track record
    try:
        eh = yt.earnings_history
        if eh is not None and len(eh) > 0 and "surprisePercent" in eh.columns:
            # Index ordinato cronologicamente (oldest first per yfinance default)
            surprises_pct = [
                float(s) * 100 if isinstance(s, (int, float)) and abs(s) < 1.0
                else float(s) if isinstance(s, (int, float)) else None
                for s in eh["surprisePercent"].tolist()
            ]
            avg_surprise_4q, surprise_trend = compute_features_from_history(surprises_pct)
    except Exception as exc:
        _log.debug(
            "earnings_history fetch failed",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )

    # 2. earnings_estimate → growth + n_analysts (next quarter ('+1q'))
    # NB: yfinance ritorna numpy types (int64/float64) che non passano
    # isinstance(int, float) — usiamo conversione esplicita try/except.
    try:
        ee = yt.earnings_estimate
        if ee is not None and len(ee) > 0:
            row = None
            if "+1q" in ee.index:
                row = ee.loc["+1q"]
            elif "0q" in ee.index:
                row = ee.loc["0q"]
            else:
                row = ee.iloc[0]
            if row is not None:
                try:
                    g = float(row.get("growth"))
                    if g == g:  # not NaN
                        growth_consensus = g
                except (TypeError, ValueError):
                    pass
                try:
                    na = int(row.get("numberOfAnalysts"))
                    if na > 0:
                        n_analysts = na
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        _log.debug(
            "earnings_estimate fetch failed",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )

    # 3. eps_revisions → net revisions 30d (next quarter)
    try:
        er = yt.eps_revisions
        if er is not None and len(er) > 0:
            row = None
            if "+1q" in er.index:
                row = er.loc["+1q"]
            elif "0q" in er.index:
                row = er.loc["0q"]
            else:
                row = er.iloc[0]
            if row is not None:
                try:
                    up = int(row.get("upLast30days") or 0)
                    dn = int(row.get("downLast30days") or 0)
                    net_revisions_30d = up - dn
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        _log.debug(
            "eps_revisions fetch failed",
            extra={"ctx": {"ticker": ticker, "error": str(exc)}},
        )

    # Persist
    market_earnings_revision_upsert(
        ticker,
        avg_surprise_4q=avg_surprise_4q,
        surprise_trend=surprise_trend,
        growth_consensus=growth_consensus,
        net_revisions_30d=net_revisions_30d,
        n_analysts=n_analysts,
    )

    return {
        "avg_surprise_4q": avg_surprise_4q,
        "surprise_trend": surprise_trend,
        "growth_consensus": growth_consensus,
        "net_revisions_30d": net_revisions_30d,
        "n_analysts": n_analysts,
    }


def get_quality_metrics(
    ticker: str,
    *,
    force_refresh: bool = False,
) -> dict:
    """Fetch + cache quality metrics (Fase B.4 SIGNAL_ROADMAP).

    Source: yfinance ``info`` (current snapshot, NO point-in-time historical).

    Cache TTL 90gg (fundamentals slow-moving). Quality_score computed and
    cached per evitare re-compute a ogni read.

    Args:
        ticker: simbolo yfinance.
        force_refresh: bypass cache.

    Returns:
        Dict {roa, gross_margin, debt_equity, score, fetched_at}.
        Score ricalcolato fresh se feature presenti, None se mancanti.
    """
    from propicks.io.db import (
        market_quality_read,
        market_quality_upsert,
    )
    from propicks.domain.quality import score_quality

    ticker = ticker.upper()
    ttl_hours = 24 * 90  # 90 giorni

    if not force_refresh:
        cached = market_quality_read(ticker, ttl_hours)
        if cached is not None:
            return cached

    # Fetch yfinance info
    info = _yf_fetch_info(ticker)
    if info is None:
        return {
            "roa": None, "gross_margin": None, "debt_equity": None,
            "score": None, "fetched_at": None,
        }

    def _safe_float(v):
        try:
            f = float(v)
            return f if f == f else None  # NaN check
        except (TypeError, ValueError):
            return None

    roa = _safe_float(info.get("returnOnAssets"))
    gm = _safe_float(info.get("grossMargins"))
    de = _safe_float(info.get("debtToEquity"))
    score = score_quality(roa, gm, de)

    market_quality_upsert(
        ticker,
        roa=roa,
        gross_margin=gm,
        debt_equity=de,
        score=score,
    )

    return {
        "roa": roa,
        "gross_margin": gm,
        "debt_equity": de,
        "score": round(score, 2) if score is not None else None,
    }


def get_ticker_beta(ticker: str) -> float | None:
    """Beta vs mercato (SPX) — cache-aware (TTL 7gg).

    Yahoo calcola il beta su 5 anni di dati mensili. Ritorna None se il dato
    non è disponibile (ETF, ticker esteri illiquidi, IPO recenti).
    """
    ticker = ticker.upper()
    cached = market_meta_read(ticker, MARKET_CACHE_TTL_META_HOURS)
    if cached is not None and cached.get("beta") is not None:
        return float(cached["beta"])

    info = _yf_fetch_info(ticker)
    if info is None:
        return None
    beta_raw = info.get("beta")
    beta: float | None
    if beta_raw is None:
        beta = None
    else:
        try:
            beta = float(beta_raw)
        except (TypeError, ValueError):
            beta = None

    sector = info.get("sector") if isinstance(info.get("sector"), str) else None
    name = info.get("shortName") or info.get("longName")
    market_meta_upsert(ticker, sector=sector, beta=beta, name=name)
    return beta


# ---------------------------------------------------------------------------
# Returns + current prices (batch helpers)
# ---------------------------------------------------------------------------
def download_returns(tickers: list[str], period: str = "6mo") -> pd.DataFrame:
    """Daily returns DataFrame (colonne = ticker) per il periodo dato.

    Non beneficia del cache OHLCV in modo ovvio (yf.download batch è più
    efficiente di N query singole quando tutti i ticker sono miss), ma se
    tutti i ticker sono fresh in cache preferiamo il cache — zero rete.

    Logica: se TUTTI i ticker sono cache-hit, compone i returns dalla cache.
    Altrimenti fallback al batch yf.download (invariato dal pre-cache).
    """
    if not tickers:
        return pd.DataFrame()

    upper = [t.upper() for t in tickers]
    all_fresh = all(
        market_ohlcv_is_fresh(t, "daily", MARKET_CACHE_TTL_DAILY_HOURS) for t in upper
    )
    if all_fresh:
        closes = pd.DataFrame()
        for t in upper:
            rows = market_ohlcv_read(t, "daily")
            if not rows:
                continue
            df = _cache_rows_to_yf_df(rows)
            closes[t] = df["Close"]
        if not closes.empty:
            return closes.pct_change().dropna(how="all")

    # Fallback: yf.download batch (stessa logica pre-cache)
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
        _log.warning(
            "yf_returns_download_failed",
            extra={"ctx": {"n_tickers": len(tickers), "error": str(exc)}},
        )
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
    """Ultimo close per ticker. Cache-aware + fallback per-ticker.

    Strategy: se il ticker ha il daily cache fresh, usa l'ultimo close della
    cache. Altrimenti batch via yf.download, fallback per-ticker via
    ``yf.Ticker.history``.
    """
    if not tickers:
        return {}
    prices: dict[str, float] = {}
    missing: list[str] = []

    for t in tickers:
        tu = t.upper()
        if market_ohlcv_is_fresh(tu, "daily", MARKET_CACHE_TTL_DAILY_HOURS):
            rows = market_ohlcv_read(tu, "daily")
            if rows:
                prices[tu] = float(rows[-1]["close"])
                continue
        missing.append(t)

    if not missing:
        return prices

    # Batch fetch per i cache miss
    try:
        data = yf.download(
            tickers=" ".join(missing),
            period="5d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=False,
        )
    except Exception as exc:
        _log.warning(
            "yf_prices_batch_failed",
            extra={"ctx": {"n_tickers": len(missing), "error": str(exc)}},
        )
        data = None

    if data is not None and not data.empty:
        for t in missing:
            try:
                closes = data[t]["Close"] if len(missing) > 1 else data["Close"]
                closes = closes.dropna()
                if not closes.empty:
                    prices[t.upper()] = float(closes.iloc[-1])
            except (KeyError, IndexError):
                pass

    # Per-ticker fallback finale
    for t in missing:
        if t.upper() in prices:
            continue
        try:
            hist = yf.Ticker(t).history(period="5d", auto_adjust=False)
            if hist is not None and not hist.empty:
                prices[t.upper()] = float(hist["Close"].iloc[-1])
        except Exception as exc:
            _log.warning(
                "yf_price_unavailable",
                extra={"ctx": {"ticker": t, "error": str(exc)}},
            )
    return prices
