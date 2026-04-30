"""FRED (Federal Reserve Economic Data) client (Fase B.3 SIGNAL_ROADMAP).

Source: ``https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>``
endpoint pubblico CSV. **No API key required**, rate-limit gentile (no
documented but soft-throttled). Per uso intensivo conviene API key gratis
(https://fredaccount.stlouisfed.org/apikeys) — rinviato.

## Serie supportate (B.3 minimal viable)

- ``BAMLH0A0HYM2``: ICE BofA US High Yield Index Option-Adjusted Spread
  (credit-equity barometer, leading indicator regime turning point)
- ``VIXCLS``: CBOE Volatility Index (fear gauge, daily close)
- ``T10Y2Y``: 10-Year Treasury minus 2-Year Treasury yield (yield curve
  slope, recession indicator quando inverte)

## Cache

Cache table ``fred_series_daily`` (PK series_id+date). TTL 24h tipicamente
sufficient (FRED aggiorna daily, weekend/holiday no-update). Read-through
pattern: cache fresh → return; miss → fetch CSV → upsert.

## Public API

- ``fetch_fred_series(series_id, start, end, force_refresh=False) -> dict[date_iso, float]``
- ``get_fred_latest(series_id) -> tuple[date_iso, float] | None``
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from propicks.io.db import _connect_for_table, _transaction_for_table
from propicks.obs.log import get_logger

_log = get_logger("market.fred_client")

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_UA = (
    "PropicksAI/0.1 (+https://github.com/valeriouberti/propicks-ai-framework) "
    "fred-fetch"
)
_FRED_TIMEOUT_S = 30.0

# TTL per la cache: macro daily, refresh non urgente. 24h ragionevole.
FRED_CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _read_fred_cache(
    series_id: str,
    start: str,
    end: str,
    *,
    path: str | None = None,
) -> dict[str, float]:
    """Leggi range serie da cache (no TTL check qui — chi chiama decide)."""
    conn = _connect_for_table("fred_series_daily", path)
    try:
        rows = conn.execute(
            """SELECT date, value FROM fred_series_daily
               WHERE series_id = ? AND date BETWEEN ? AND ?
               ORDER BY date ASC""",
            (series_id, start, end),
        ).fetchall()
    finally:
        conn.close()
    return {r["date"]: r["value"] for r in rows if r["value"] is not None}


def _is_fred_cache_fresh(
    series_id: str,
    *,
    ttl_hours: float = FRED_CACHE_TTL_HOURS,
    path: str | None = None,
) -> bool:
    """True se la cache ha almeno una row fetched recently."""
    conn = _connect_for_table("fred_series_daily", path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM fred_series_daily
               WHERE series_id = ? AND fetched_at >= datetime('now', ?)""",
            (series_id, f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()
    return row["n"] > 0


def _upsert_fred_rows(
    series_id: str,
    rows: dict[str, float | None],
    *,
    path: str | None = None,
) -> int:
    """Bulk upsert serie. Idempotent su PK (series_id, date)."""
    if not rows:
        return 0
    n = 0
    with _transaction_for_table("fred_series_daily", path) as conn:
        for date_iso, value in rows.items():
            conn.execute(
                """INSERT INTO fred_series_daily (series_id, date, value, fetched_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(series_id, date) DO UPDATE SET
                     value = excluded.value,
                     fetched_at = CURRENT_TIMESTAMP""",
                (series_id, date_iso, value),
            )
            n += 1
    return n


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------
def _fetch_csv(
    series_id: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, float | None]:
    """Fetch raw CSV da FRED endpoint.

    Returns:
        Dict {date_iso: value | None}. ``None`` per missing values (FRED
        usa "." come marker, lo convertiamo a None).

    Raises:
        ValueError: HTTP error / parse failure.
    """
    params = [f"id={series_id}"]
    if start:
        params.append(f"cosd={start}")
    if end:
        params.append(f"coed={end}")
    url = f"{_FRED_CSV_URL}?{'&'.join(params)}"

    req = urllib.request.Request(url, headers={"User-Agent": _FRED_UA})
    try:
        with urllib.request.urlopen(req, timeout=_FRED_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"FRED HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"FRED URL error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ValueError(f"FRED timeout > {_FRED_TIMEOUT_S}s") from exc

    out: dict[str, float | None] = {}
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError(f"FRED CSV {series_id}: header invalid {header}")
    for row in reader:
        if len(row) < 2:
            continue
        date_str = row[0].strip()
        val_str = row[1].strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        # FRED usa "." per missing data
        if val_str in ("", "."):
            out[date_str] = None
            continue
        try:
            out[date_str] = float(val_str)
        except ValueError:
            out[date_str] = None
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_fred_series(
    series_id: str,
    start: date | str | None = None,
    end: date | str | None = None,
    *,
    force_refresh: bool = False,
    path: str | None = None,
) -> dict[str, float]:
    """Fetch + cache serie FRED. Cache-aware read-through.

    Args:
        series_id: identificatore FRED (es. 'BAMLH0A0HYM2').
        start: data inizio (date / 'YYYY-MM-DD'). Default: 5 anni fa.
        end: data fine. Default: oggi.
        force_refresh: bypass cache.
        path: DB override (test).

    Returns:
        Dict {date_iso: value} ordered ASC. Esclude None/missing.
    """
    series_id = series_id.upper().strip()
    if isinstance(start, date):
        start = start.isoformat()
    if isinstance(end, date):
        end = end.isoformat()
    if start is None:
        start = (date.today() - timedelta(days=365 * 5)).isoformat()
    if end is None:
        end = date.today().isoformat()

    # Fast path: cache fresh per quel series_id
    if not force_refresh and _is_fred_cache_fresh(series_id, path=path):
        cached = _read_fred_cache(series_id, start, end, path=path)
        if cached:
            _log.debug(
                "fred_cache_hit",
                extra={"ctx": {"series": series_id, "n": len(cached)}},
            )
            return cached
        # Cache fresh ma vuota nel range richiesto → fall through fetch

    # Fetch
    try:
        raw = _fetch_csv(series_id, start, end)
    except ValueError as exc:
        _log.warning(
            "fred_fetch_failed",
            extra={"ctx": {"series": series_id, "error": str(exc)}},
        )
        # Fallback: cache anche se stale
        return _read_fred_cache(series_id, start, end, path=path)

    n = _upsert_fred_rows(series_id, raw, path=path)
    _log.info(
        "fred_fetched",
        extra={"ctx": {"series": series_id, "n_upserted": n}},
    )
    # Re-leggi dalla cache per filtrare None automaticamente
    return _read_fred_cache(series_id, start, end, path=path)


def get_fred_latest(
    series_id: str,
    *,
    path: str | None = None,
) -> tuple[str, float] | None:
    """Ritorna (date_iso, value) ultimo valore non-null disponibile in cache.

    Se cache vuota o tutti i valori null, ritorna None. Non triggera fetch
    (read-only). Per assicurare freshness chiama prima ``fetch_fred_series``.
    """
    conn = _connect_for_table("fred_series_daily", path)
    try:
        row = conn.execute(
            """SELECT date, value FROM fred_series_daily
               WHERE series_id = ? AND value IS NOT NULL
               ORDER BY date DESC LIMIT 1""",
            (series_id.upper().strip(),),
        ).fetchone()
    finally:
        conn.close()
    return (row["date"], row["value"]) if row else None
