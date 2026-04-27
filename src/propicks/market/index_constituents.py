"""Universe discovery: lista membri di un index (S&P 500 oggi, NASDAQ-100 future).

Sorgente primaria: **Wikipedia** parsata via ``pandas.read_html``. È il metodo
standard nell'ecosistema retail-quant: aggiornato dalla community in real-time,
no API key, no rate limit. Tradeoff: dipendenza dallo schema HTML — se cambia
struttura tabella la fetch fallisce, e attiviamo il fallback.

## Architettura cache (mirror Phase 2 OHLCV)

1. **Fast path**: cache SQLite fresh entro TTL (7gg) → ritorna dalla cache
2. **Miss/stale**: fetch Wikipedia → sanity check (≥ ``INDEX_MIN_CONSTITUENTS``
   nomi) → UPSERT atomico → ritorna
3. **Fallback**: se Wikipedia fails O sanity check non passa, ritorna lo
   snapshot hardcoded ``SP500_FALLBACK`` (subset di mega-cap stabili).
   Lo snapshot NON viene mai persistito su DB — è solo runtime safety net.

## Public API

- ``get_sp500_universe(force_refresh=False) -> list[str]`` — lista ticker
  pronti per yfinance (già normalizzati BRK.B → BRK-B).
- ``get_sp500_universe_detailed(...) -> list[dict]`` — variante con company
  name + sector quando servono per discovery filtering.
"""

from __future__ import annotations

import pandas as pd

from propicks.config import (
    INDEX_CONSTITUENTS_CACHE_TTL_HOURS,
    INDEX_MIN_CONSTITUENTS,
    SP500_WIKIPEDIA_URL,
)
from propicks.io.db import (
    index_constituents_is_fresh,
    index_constituents_read,
    index_constituents_replace,
)
from propicks.obs.log import get_logger

_log = get_logger("market.index_constituents")

INDEX_NAME_SP500 = "sp500"


# ---------------------------------------------------------------------------
# Fallback snapshot (safety net runtime — NON persistito)
# ---------------------------------------------------------------------------
# Subset di ~50 mega-cap S&P 500 stabili (in index da 5+ anni, marketcap top).
# Usato solo se Wikipedia fails E la cache è vuota/stale. Coverage ridotta ma
# garantisce che il discovery non crashi mai per problemi di network/parsing.
# Aggiornare ~annualmente quando entrano nuovi mega-cap (es. ARM, IPO recenti).
SP500_FALLBACK: list[dict] = [
    # Technology
    {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology"},
    {"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Technology"},
    {"ticker": "NVDA", "company_name": "NVIDIA Corp.", "sector": "Technology"},
    {"ticker": "AVGO", "company_name": "Broadcom Inc.", "sector": "Technology"},
    {"ticker": "ORCL", "company_name": "Oracle Corp.", "sector": "Technology"},
    {"ticker": "CRM", "company_name": "Salesforce Inc.", "sector": "Technology"},
    {"ticker": "ADBE", "company_name": "Adobe Inc.", "sector": "Technology"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "sector": "Technology"},
    {"ticker": "CSCO", "company_name": "Cisco Systems", "sector": "Technology"},
    {"ticker": "INTC", "company_name": "Intel Corp.", "sector": "Technology"},
    # Communication Services
    {"ticker": "GOOGL", "company_name": "Alphabet Inc. Class A", "sector": "Communication Services"},
    {"ticker": "META", "company_name": "Meta Platforms", "sector": "Communication Services"},
    {"ticker": "NFLX", "company_name": "Netflix Inc.", "sector": "Communication Services"},
    {"ticker": "DIS", "company_name": "Walt Disney Co.", "sector": "Communication Services"},
    {"ticker": "VZ", "company_name": "Verizon Communications", "sector": "Communication Services"},
    {"ticker": "T", "company_name": "AT&T Inc.", "sector": "Communication Services"},
    # Consumer Discretionary
    {"ticker": "AMZN", "company_name": "Amazon.com Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "TSLA", "company_name": "Tesla Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "HD", "company_name": "Home Depot Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "MCD", "company_name": "McDonald's Corp.", "sector": "Consumer Cyclical"},
    {"ticker": "NKE", "company_name": "Nike Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "SBUX", "company_name": "Starbucks Corp.", "sector": "Consumer Cyclical"},
    # Consumer Staples
    {"ticker": "WMT", "company_name": "Walmart Inc.", "sector": "Consumer Defensive"},
    {"ticker": "PG", "company_name": "Procter & Gamble", "sector": "Consumer Defensive"},
    {"ticker": "KO", "company_name": "Coca-Cola Co.", "sector": "Consumer Defensive"},
    {"ticker": "PEP", "company_name": "PepsiCo Inc.", "sector": "Consumer Defensive"},
    {"ticker": "COST", "company_name": "Costco Wholesale", "sector": "Consumer Defensive"},
    # Healthcare
    {"ticker": "JNJ", "company_name": "Johnson & Johnson", "sector": "Healthcare"},
    {"ticker": "UNH", "company_name": "UnitedHealth Group", "sector": "Healthcare"},
    {"ticker": "LLY", "company_name": "Eli Lilly and Co.", "sector": "Healthcare"},
    {"ticker": "MRK", "company_name": "Merck & Co.", "sector": "Healthcare"},
    {"ticker": "PFE", "company_name": "Pfizer Inc.", "sector": "Healthcare"},
    {"ticker": "ABBV", "company_name": "AbbVie Inc.", "sector": "Healthcare"},
    {"ticker": "TMO", "company_name": "Thermo Fisher Scientific", "sector": "Healthcare"},
    # Financials
    {"ticker": "JPM", "company_name": "JPMorgan Chase", "sector": "Financial Services"},
    {"ticker": "BAC", "company_name": "Bank of America", "sector": "Financial Services"},
    {"ticker": "WFC", "company_name": "Wells Fargo", "sector": "Financial Services"},
    {"ticker": "GS", "company_name": "Goldman Sachs Group", "sector": "Financial Services"},
    {"ticker": "MS", "company_name": "Morgan Stanley", "sector": "Financial Services"},
    {"ticker": "V", "company_name": "Visa Inc.", "sector": "Financial Services"},
    {"ticker": "MA", "company_name": "Mastercard Inc.", "sector": "Financial Services"},
    {"ticker": "BRK-B", "company_name": "Berkshire Hathaway B", "sector": "Financial Services"},
    # Industrials
    {"ticker": "CAT", "company_name": "Caterpillar Inc.", "sector": "Industrials"},
    {"ticker": "BA", "company_name": "Boeing Co.", "sector": "Industrials"},
    {"ticker": "HON", "company_name": "Honeywell International", "sector": "Industrials"},
    {"ticker": "UPS", "company_name": "United Parcel Service", "sector": "Industrials"},
    {"ticker": "GE", "company_name": "General Electric", "sector": "Industrials"},
    # Energy
    {"ticker": "XOM", "company_name": "Exxon Mobil Corp.", "sector": "Energy"},
    {"ticker": "CVX", "company_name": "Chevron Corp.", "sector": "Energy"},
    {"ticker": "COP", "company_name": "ConocoPhillips", "sector": "Energy"},
    # Materials
    {"ticker": "LIN", "company_name": "Linde plc", "sector": "Basic Materials"},
    # Utilities
    {"ticker": "NEE", "company_name": "NextEra Energy", "sector": "Utilities"},
    # Real Estate
    {"ticker": "PLD", "company_name": "Prologis Inc.", "sector": "Real Estate"},
]


# ---------------------------------------------------------------------------
# Wikipedia fetcher
# ---------------------------------------------------------------------------
def _normalize_yf_ticker(symbol: str) -> str:
    """Normalizza ticker Wikipedia → yfinance.

    Wikipedia usa la notazione "BRK.B" (dot per share class), yfinance
    richiede "BRK-B" (dash). Stessa cosa per BF.B → BF-B, etc.
    Strip whitespace per safety.
    """
    return symbol.strip().replace(".", "-")


def _fetch_sp500_from_wikipedia() -> list[dict]:
    """Fetch + parse della tabella S&P 500 da Wikipedia.

    Ritorna list[dict] con keys: ticker, company_name, sector, added_date.
    Solleva ValueError se la pagina non ha la struttura attesa o ritorna
    troppo pochi nomi (sanity check su INDEX_MIN_CONSTITUENTS).
    """
    try:
        tables = pd.read_html(SP500_WIKIPEDIA_URL)
    except Exception as exc:
        raise ValueError(f"Wikipedia fetch failed: {exc}") from exc

    if not tables:
        raise ValueError("Wikipedia returned no tables")

    df = tables[0]

    # Schema Wikipedia (stabile da anni): "Symbol", "Security", "GICS Sector",
    # "Date added". Difensivi su rename in caso di future changes.
    required_cols = {"Symbol", "Security"}
    if not required_cols.issubset(df.columns):
        raise ValueError(
            f"Wikipedia table missing expected columns. "
            f"Got: {list(df.columns)[:10]}"
        )

    rows: list[dict] = []
    for _, row in df.iterrows():
        symbol = row.get("Symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        rows.append(
            {
                "ticker": _normalize_yf_ticker(symbol),
                "company_name": row.get("Security") if isinstance(row.get("Security"), str) else None,
                "sector": row.get("GICS Sector") if isinstance(row.get("GICS Sector"), str) else None,
                "added_date": row.get("Date added") if isinstance(row.get("Date added"), str) else None,
            }
        )

    if len(rows) < INDEX_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia returned only {len(rows)} constituents "
            f"(min expected: {INDEX_MIN_CONSTITUENTS}) — table format may have changed"
        )

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_sp500_universe_detailed(
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Ritorna lista membri S&P 500 con metadata (ticker + name + sector).

    Pattern read-through con TTL 7gg. Sanity check: se Wikipedia ritorna meno
    di ``INDEX_MIN_CONSTITUENTS`` nomi, fallback sul cache esistente, e se
    anche quello è vuoto, sull'hardcoded ``SP500_FALLBACK``.

    ``force_refresh=True`` bypass cache e forza un fetch fresh.
    """
    # Fast path: cache fresh
    if not force_refresh and index_constituents_is_fresh(
        INDEX_NAME_SP500, INDEX_CONSTITUENTS_CACHE_TTL_HOURS
    ):
        cached = index_constituents_read(INDEX_NAME_SP500)
        if cached and len(cached) >= INDEX_MIN_CONSTITUENTS:
            _log.debug(
                "sp500_cache_hit",
                extra={"ctx": {"n_tickers": len(cached)}},
            )
            return cached

    # Miss / stale / force: fetch Wikipedia
    try:
        fresh_rows = _fetch_sp500_from_wikipedia()
        index_constituents_replace(INDEX_NAME_SP500, fresh_rows)
        _log.info(
            "sp500_wikipedia_fetched",
            extra={"ctx": {"n_tickers": len(fresh_rows)}},
        )
        return fresh_rows
    except ValueError as exc:
        _log.warning(
            "sp500_wikipedia_fetch_failed",
            extra={"ctx": {"error": str(exc)}},
        )

    # Fallback layer 1: cache esistente (anche se stale)
    cached = index_constituents_read(INDEX_NAME_SP500)
    if cached:
        _log.warning(
            "sp500_using_stale_cache",
            extra={"ctx": {"n_tickers": len(cached)}},
        )
        return cached

    # Fallback layer 2: hardcoded snapshot (mai persistito)
    _log.warning(
        "sp500_using_hardcoded_snapshot",
        extra={"ctx": {"n_tickers": len(SP500_FALLBACK)}},
    )
    return list(SP500_FALLBACK)


def get_sp500_universe(*, force_refresh: bool = False) -> list[str]:
    """Convenience: ritorna solo la lista di ticker.

    Per la maggior parte dei caller (discovery pipeline, batch warm cache)
    serve solo la lista di ticker normalizzati per yfinance.
    """
    return [r["ticker"] for r in get_sp500_universe_detailed(force_refresh=force_refresh)]
