"""Point-in-time index membership lookup (Fase A.1 SIGNAL_ROADMAP).

Risolve il survivorship bias nei backtest: ``get_sp500_universe()`` ritorna la
lista *odierna*, mentre il backtest ha bisogno di sapere chi era nel S&P 500
in una data passata. Senza questo modulo, un backtest 2010-2020 vede solo i
ticker che oggi sono ancora nell'index — esclude i delisted/merged (Lehman,
Sears, Bear Stearns, etc.) e include ticker entrati dopo (Tesla 2020+).

## Schema

Tabella ``index_membership_history`` (vedi ``schema.sql``):

- ``(index_name, snapshot_date, ticker)`` PRIMARY KEY
- ``snapshot_date`` granularità mensile tipica
- ``source`` tracciato per debugging / re-fetch

## Query pattern

``get_constituents_at(date, "sp500")`` ritorna lo snapshot più recente
``<= date``. Se nessuno snapshot disponibile per quell'index, ``[]``.

## Public API

- ``get_constituents_at(date, index_name) -> list[str]``
- ``get_constituents_at_detailed(date, index_name) -> list[dict]``
- ``bulk_insert_snapshots(index_name, snapshots, source) -> int``
- ``get_snapshot_dates(index_name) -> list[date]``
- ``count_membership_rows(index_name) -> int``
- ``get_membership_date_range(index_name) -> tuple[date, date] | None``
- ``is_ticker_in_index_at(ticker, date, index_name) -> bool``
- ``build_universe_provider(index_name) -> Callable[[date], list[str]]``
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime

from propicks.io.db import _connect_for_table, _transaction_for_table


def _to_iso(d: date | str) -> str:
    """Normalizza date in stringa ISO YYYY-MM-DD."""
    if isinstance(d, str):
        # Sanity check formato; SQLite accetta qualsiasi text ma noi vogliamo
        # ISO per ordering lessicografico = ordering temporale.
        datetime.strptime(d, "%Y-%m-%d")
        return d
    return d.isoformat()


def get_constituents_at(
    at_date: date | str,
    index_name: str,
    *,
    path: str | None = None,
) -> list[str]:
    """Ritorna la lista di ticker membri di ``index_name`` alla data richiesta.

    Strategia: trova lo snapshot più recente con ``snapshot_date <= at_date`` e
    ritorna tutti i ticker di quello snapshot. Se nessuno snapshot disponibile
    (es. data prima del primo snapshot importato), ritorna lista vuota.

    Args:
        at_date: data point-in-time (date object o stringa ISO YYYY-MM-DD).
        index_name: 'sp500' | 'nasdaq100' | 'ftsemib' | 'stoxx600'.

    Returns:
        Lista di ticker (uppercase, normalizzati per yfinance), ordine
        alfabetico. Vuota se nessuno snapshot ≤ at_date.
    """
    iso = _to_iso(at_date)
    conn = _connect_for_table("index_membership_history", path)
    try:
        # Find most recent snapshot ≤ at_date in single round-trip
        row = conn.execute(
            """SELECT MAX(snapshot_date) AS d
               FROM index_membership_history
               WHERE index_name = ? AND snapshot_date <= ?""",
            (index_name, iso),
        ).fetchone()
        if row is None or row["d"] is None:
            return []
        snapshot_date = row["d"]
        rows = conn.execute(
            """SELECT ticker FROM index_membership_history
               WHERE index_name = ? AND snapshot_date = ?
               ORDER BY ticker ASC""",
            (index_name, snapshot_date),
        ).fetchall()
    finally:
        conn.close()
    return [r["ticker"] for r in rows]


def get_constituents_at_detailed(
    at_date: date | str,
    index_name: str,
    *,
    path: str | None = None,
) -> list[dict]:
    """Variante di ``get_constituents_at`` con metadata (company_name, sector).

    Ritorna list di dict con keys: ticker, company_name, sector, snapshot_date.
    """
    iso = _to_iso(at_date)
    conn = _connect_for_table("index_membership_history", path)
    try:
        row = conn.execute(
            """SELECT MAX(snapshot_date) AS d
               FROM index_membership_history
               WHERE index_name = ? AND snapshot_date <= ?""",
            (index_name, iso),
        ).fetchone()
        if row is None or row["d"] is None:
            return []
        snapshot_date = row["d"]
        rows = conn.execute(
            """SELECT ticker, company_name, sector, snapshot_date
               FROM index_membership_history
               WHERE index_name = ? AND snapshot_date = ?
               ORDER BY ticker ASC""",
            (index_name, snapshot_date),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def bulk_insert_snapshots(
    index_name: str,
    snapshots: dict[date | str, Iterable[dict | str]],
    *,
    source: str,
    path: str | None = None,
) -> int:
    """Bulk insert di multipli snapshot in singola transazione.

    Idempotente: usa ``INSERT OR REPLACE`` su PK (index_name, snapshot_date,
    ticker). Re-import dello stesso CSV è no-op semantico.

    Args:
        index_name: identificatore index ('sp500', etc.)
        snapshots: dict {date: [ticker_str | dict_with_metadata]}.
            Se valore è stringa = solo ticker.
            Se valore è dict = {'ticker': 'AAPL', 'company_name': ..., 'sector': ...}.
        source: tag origine ('fja05680' | 'wikipedia' | 'ishares' | 'manual').

    Returns:
        Numero righe inserite/aggiornate.
    """
    if not snapshots:
        return 0
    n = 0
    with _transaction_for_table("index_membership_history", path) as conn:
        for snap_date, members in snapshots.items():
            iso = _to_iso(snap_date)
            for m in members:
                if isinstance(m, str):
                    ticker = m.strip().upper()
                    company_name = None
                    sector = None
                else:
                    ticker = m["ticker"].strip().upper()
                    company_name = m.get("company_name")
                    sector = m.get("sector")
                if not ticker:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO index_membership_history (
                        index_name, snapshot_date, ticker,
                        company_name, sector, source, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (index_name, iso, ticker, company_name, sector, source),
                )
                n += 1
    return n


def get_snapshot_dates(
    index_name: str,
    *,
    path: str | None = None,
) -> list[str]:
    """Ritorna lista snapshot_date disponibili per un index, ordinati ASC.

    Stringhe ISO per evitare conversioni date/datetime accidentali. Diagnostic
    utility per CLI e dashboard.
    """
    conn = _connect_for_table("index_membership_history", path)
    try:
        rows = conn.execute(
            """SELECT DISTINCT snapshot_date FROM index_membership_history
               WHERE index_name = ?
               ORDER BY snapshot_date ASC""",
            (index_name,),
        ).fetchall()
    finally:
        conn.close()
    return [r["snapshot_date"] for r in rows]


def count_membership_rows(
    index_name: str,
    *,
    path: str | None = None,
) -> int:
    """Conta righe totali per un index (n_ticker × n_snapshot)."""
    conn = _connect_for_table("index_membership_history", path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM index_membership_history WHERE index_name = ?",
            (index_name,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def get_membership_date_range(
    index_name: str,
    *,
    path: str | None = None,
) -> tuple[str, str] | None:
    """Ritorna (min_snapshot, max_snapshot) ISO strings, o None se vuoto."""
    conn = _connect_for_table("index_membership_history", path)
    try:
        row = conn.execute(
            """SELECT MIN(snapshot_date) AS lo, MAX(snapshot_date) AS hi
               FROM index_membership_history
               WHERE index_name = ?""",
            (index_name,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["lo"] is None:
        return None
    return (row["lo"], row["hi"])


def is_ticker_in_index_at(
    ticker: str,
    at_date: date | str,
    index_name: str,
    *,
    path: str | None = None,
) -> bool:
    """True se ``ticker`` era membro di ``index_name`` alla data richiesta.

    Convenience wrapper su ``get_constituents_at``. Per check ripetuti su
    molti ticker, usa direttamente ``get_constituents_at`` una sola volta e
    confronta in memoria — evita N round-trip.
    """
    return ticker.strip().upper() in get_constituents_at(
        at_date, index_name, path=path
    )


def build_universe_provider(
    index_name: str,
    *,
    path: str | None = None,
) -> Callable[[date], list[str]]:
    """Costruisce un universe_provider per backtest engine point-in-time.

    Returns:
        Callable ``(d: date) -> list[str]`` che il backtest può chiamare a
        ogni rebalance day per ottenere universo membership-corretto.

    Pattern d'uso:
        provider = build_universe_provider("sp500")
        result = simulate_portfolio(
            universe=ohlcv_dict,
            scoring_fn=...,
            universe_provider=provider,  # filtra ticker eligible per data
            ...
        )

    Performance: ogni call a provider fa 2 query SQLite (~ms). Per backtest
    daily 10y = 2520 trading days × 2 query = 5040 query → ~1-2s overhead.
    Accettabile. Se necessario, aggiungere caching in-memory dei snapshot.
    """
    def _provider(d: date) -> list[str]:
        return get_constituents_at(d, index_name, path=path)
    return _provider
