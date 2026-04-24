"""Foundation SQLite per il trading engine.

Source of truth: ``config.DB_FILE`` (default ``data/propicks.db``).

## Design choices

- **sqlite3 stdlib raw**, no SQLAlchemy. Zero magic, zero deps extra, velocità
  massima per single-user. Migrazione a Postgres futura toccherebbe solo questo
  modulo e i DDL.
- **Connection per call** invece di pool: SQLite su file locale è nanosecond-
  fast per open/close. Evita problemi di thread safety (Streamlit è multi-
  thread, APScheduler pure).
- **WAL mode**: abilita lettori concorrenti anche durante una write (utile
  quando dashboard legge mentre CLI scrive).
- **Row factory = sqlite3.Row**: permette accesso per nome colonna (`row["ticker"]`)
  oltre che per indice. Dict-like ma più efficiente di un dict.
- **Foreign keys ON**: SQLite le disabilita di default per retrocompat. Noi le
  vogliamo accese per integrity.

## Schema init

``init_schema()`` è idempotente: le statement SQL usano ``CREATE TABLE IF NOT
EXISTS``. La prima volta crea tutto, le successive no-op. Lo schema vive in
``schema.sql`` a fianco, letto come risorsa del package.

## Migrazione da JSON

Al primo ``connect()`` se il DB non esiste viene creato e lo schema inizializzato.
La migrazione dei JSON legacy è delegata a ``scripts/migrate_json_to_sqlite.py``
(one-shot manuale o auto-trigger via ``auto_migrate_if_needed()``).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Set of DB paths where schema has been applied in this process. Applying
# CREATE TABLE IF NOT EXISTS is idempotent ma ha un costo I/O; cachiamo per
# evitarne l'esecuzione ad ogni connect. Per test isolation, l'autouse fixture
# ``_isolate_db`` usa un path diverso per ogni test → ogni test trigger
# un'init fresca, corretto.
_SCHEMA_APPLIED_PATHS: set[str] = set()


def _get_db_path() -> str:
    """Re-reads ``config.DB_FILE`` at each call — permette monkeypatch nei test."""
    from propicks.config import DB_FILE
    return DB_FILE


def _load_schema_sql() -> str:
    """Legge schema.sql dal package. Cached staticamente dopo il primo read."""
    global _SCHEMA_CACHE
    if "_SCHEMA_CACHE" not in globals():
        _SCHEMA_CACHE = _SCHEMA_PATH.read_text(encoding="utf-8")
    return _SCHEMA_CACHE


def connect(path: str | None = None) -> sqlite3.Connection:
    """Apre una connessione SQLite con settings standardizzati.

    - ``row_factory = sqlite3.Row`` → accesso per colonna via ``row["col"]``
    - ``PRAGMA foreign_keys = ON`` → integrity referenziale
    - ``PRAGMA journal_mode = WAL`` → reader concorrenti durante write
    - Parse esplicito di timestamp via ``detect_types``
    - Schema inizializzato se DB nuovo

    Args:
        path: override per test. Default: ``config.DB_FILE``.
    """
    db_path = path or _get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Niente ``detect_types``: le colonne DATE / TIMESTAMP sono gestite come
    # TEXT ISO-formatted lato applicativo. Evita fragilità di parsing quando
    # il formato del timestamp non è il default atteso da sqlite3 (es.
    # legacy ``YYYY-MM-DD HH:MM`` senza secondi).
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,  # autocommit off — noi gestiamo transazioni
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")  # trade-off perf/safety ragionevole

    # Applica lo schema al primo connect per path in questo processo.
    # Idempotente (CREATE TABLE IF NOT EXISTS) ma evitiamo l'overhead ripetuto.
    # Cruciale per DB esistenti quando si aggiungono nuove tabelle (Phase 2+).
    if db_path not in _SCHEMA_APPLIED_PATHS:
        _init_schema(conn)
        _SCHEMA_APPLIED_PATHS.add(db_path)

    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check se una colonna esiste già in ``table`` via PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Applica schema migrations incrementali per DB esistenti.

    Le nuove tabelle sono gestite da CREATE TABLE IF NOT EXISTS nello
    schema.sql. Le colonne aggiunte a tabelle esistenti (Phase 4+)
    richiedono ALTER TABLE — ecco dove vanno.

    Pattern: ogni migration è idempotent via check ``_column_exists``.
    """
    # Phase 4: delivery tracking su alerts
    for column, ddl in (
        ("delivered", "ALTER TABLE alerts ADD COLUMN delivered INTEGER DEFAULT 0"),
        ("delivered_at", "ALTER TABLE alerts ADD COLUMN delivered_at TIMESTAMP"),
        ("delivery_error", "ALTER TABLE alerts ADD COLUMN delivery_error TEXT"),
    ):
        if not _column_exists(conn, "alerts", column):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                # Tabella alerts non esiste ancora (es. DB nuovo, già gestito dallo
                # schema.sql CREATE TABLE IF NOT EXISTS). Safe ignore.
                pass


def _init_schema(conn: sqlite3.Connection) -> None:
    """Applica lo schema + migrations incrementali.

    Ordine: CREATE TABLE IF NOT EXISTS (nuove tabelle) → ALTER TABLE per
    colonne aggiunte (migrations). Entrambi idempotenti.
    """
    conn.executescript(_load_schema_sql())
    _apply_migrations(conn)
    conn.commit()


def init_schema(path: str | None = None) -> None:
    """Entry point pubblico per (re-)inizializzare lo schema.

    Idempotente: CREATE TABLE IF NOT EXISTS. Utile in test setup e migration.
    """
    conn = connect(path)
    try:
        _init_schema(conn)
    finally:
        conn.close()


@contextmanager
def transaction(path: str | None = None):
    """Context manager per transazioni atomiche con commit/rollback automatici.

    Example:
        with transaction() as conn:
            conn.execute("INSERT INTO positions ...")
            conn.execute("UPDATE portfolio_meta SET value=? WHERE key='cash'", (100,))
        # commit automatico; su exception → rollback
    """
    conn = connect(path)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Meta KV helpers (portfolio_meta)
# ---------------------------------------------------------------------------
def meta_get(key: str, default: str | None = None, *, path: str | None = None) -> str | None:
    """Lettura singola dalla tabella ``portfolio_meta``."""
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM portfolio_meta WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    return row["value"] if row else default


def meta_set(key: str, value: str, *, path: str | None = None) -> None:
    """Upsert singolo nella tabella ``portfolio_meta``."""
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (key, value),
        )


def meta_set_many(pairs: dict[str, str], *, path: str | None = None) -> None:
    """Upsert multiplo in singola transazione. Per coerenza quando aggiorni
    cash + last_updated insieme, per esempio."""
    with transaction(path) as conn:
        for key, value in pairs.items():
            conn.execute(
                """INSERT INTO portfolio_meta (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )


# ---------------------------------------------------------------------------
# AI verdict cache (sostituisce data/ai_cache/ file-based)
# ---------------------------------------------------------------------------
def ai_verdict_cache_get(
    cache_key: str,
    *,
    ttl_hours: float,
    path: str | None = None,
) -> dict | None:
    """Ritorna il JSON payload del verdict più recente per ``cache_key``,
    se entro la finestra TTL. Altrimenti None.

    TTL viene applicato comparando ``run_timestamp`` con CURRENT_TIMESTAMP
    direttamente in SQL, evitando un deserialize inutile.
    """
    import json as _json

    conn = connect(path)
    try:
        row = conn.execute(
            """SELECT payload, run_timestamp FROM ai_verdicts
               WHERE cache_key = ?
                 AND run_timestamp >= datetime('now', ?)
               ORDER BY run_timestamp DESC
               LIMIT 1""",
            (cache_key, f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    try:
        return _json.loads(row["payload"])
    except (ValueError, TypeError):
        return None


def ai_verdict_cache_put(
    cache_key: str,
    strategy: str,
    ticker: str,
    payload: dict,
    *,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    path: str | None = None,
) -> None:
    """Persiste un nuovo verdict nella tabella ``ai_verdicts``.

    Non sostituisce i verdict esistenti con stesso ``cache_key``: li accoda.
    Questo preserva la storia — utile per audit trail delle decisioni AI.
    Il cache lookup prende sempre il più recente via ORDER BY DESC LIMIT 1.
    """
    import json as _json

    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO ai_verdicts (
                strategy, ticker, cache_key, verdict, conviction, payload,
                tokens_in, tokens_out, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy,
                ticker.upper(),
                cache_key,
                payload.get("verdict"),
                payload.get("conviction_score"),
                _json.dumps(payload, ensure_ascii=False),
                tokens_in,
                tokens_out,
                cost_usd,
            ),
        )


# ---------------------------------------------------------------------------
# Market data OHLCV cache (Phase 2)
# ---------------------------------------------------------------------------
def market_ohlcv_is_fresh(
    ticker: str,
    interval: str,
    ttl_hours: float,
    *,
    path: str | None = None,
) -> bool:
    """True se la cache ha ALMENO UNA riga non stale per (ticker, interval).

    Pattern: l'operazione "fetch yfinance" ritorna un blocco di barre con
    fetched_at tutte identiche. "Fresh" = almeno una fetch entro TTL. Non
    verifichiamo il range di date coperto (delegato al caller via row count).
    """
    assert interval in ("daily", "weekly"), f"interval invalido: {interval}"
    table = f"market_ohlcv_{interval}"

    conn = connect(path)
    try:
        row = conn.execute(
            f"""SELECT COUNT(*) AS n FROM {table}
                WHERE ticker = ?
                  AND fetched_at >= datetime('now', ?)""",
            (ticker.upper(), f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()
    return row["n"] > 0


def market_ohlcv_read(
    ticker: str,
    interval: str,
    *,
    path: str | None = None,
) -> list[dict]:
    """Ritorna tutte le righe OHLCV per ticker+interval, ordinate per data ASC.

    Per semplicità ritorna list[dict] invece di pd.DataFrame: il chiamante
    (``yfinance_client``) può costruire il DataFrame con i casting pandas
    che preferisce. Così ``io/`` resta indipendente da pandas.
    """
    assert interval in ("daily", "weekly"), f"interval invalido: {interval}"
    table = f"market_ohlcv_{interval}"
    date_col = "date" if interval == "daily" else "week_start"

    conn = connect(path)
    try:
        rows = conn.execute(
            f"""SELECT ticker, {date_col} AS date,
                       open, high, low, close, adj_close, volume, fetched_at
                FROM {table}
                WHERE ticker = ?
                ORDER BY {date_col} ASC""",
            (ticker.upper(),),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def market_ohlcv_upsert(
    ticker: str,
    interval: str,
    bars: list[dict],
    *,
    path: str | None = None,
) -> int:
    """UPSERT di bar OHLCV. ``bars`` = list di dict con chiavi:
    ``date``/``week_start``, ``open``, ``high``, ``low``, ``close``,
    ``adj_close``, ``volume``.

    Ogni insert aggiorna ``fetched_at = CURRENT_TIMESTAMP``, permettendo
    TTL-based freshness check.

    Returns: numero di righe inserite/aggiornate.
    """
    assert interval in ("daily", "weekly"), f"interval invalido: {interval}"
    if not bars:
        return 0
    table = f"market_ohlcv_{interval}"
    date_col = "date" if interval == "daily" else "week_start"

    n = 0
    with transaction(path) as conn:
        for bar in bars:
            conn.execute(
                f"""INSERT INTO {table} (
                    ticker, {date_col}, open, high, low, close, adj_close, volume, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ticker, {date_col}) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    adj_close = excluded.adj_close,
                    volume = excluded.volume,
                    fetched_at = CURRENT_TIMESTAMP""",
                (
                    ticker.upper(),
                    bar[date_col],
                    bar.get("open"),
                    bar.get("high"),
                    bar.get("low"),
                    bar["close"],
                    bar.get("adj_close"),
                    bar.get("volume"),
                ),
            )
            n += 1
    return n


def market_ohlcv_clear(
    ticker: str | None = None,
    interval: str | None = None,
    *,
    stale_ttl_hours: float | None = None,
    path: str | None = None,
) -> int:
    """Invalida cache OHLCV. Ritorna numero di righe cancellate.

    - ``ticker=None, interval=None``: wipe totale (usato da --all).
    - ``ticker="AAPL"``: solo quel ticker (daily + weekly).
    - ``interval="daily"``: solo una tabella.
    - ``stale_ttl_hours=24``: solo righe con fetched_at vecchio > 24h.
    """
    tables = (
        (f"market_ohlcv_{interval}",) if interval
        else ("market_ohlcv_daily", "market_ohlcv_weekly")
    )
    total = 0
    with transaction(path) as conn:
        for tbl in tables:
            clauses: list[str] = []
            params: list = []
            if ticker:
                clauses.append("ticker = ?")
                params.append(ticker.upper())
            if stale_ttl_hours is not None:
                clauses.append("fetched_at < datetime('now', ?)")
                params.append(f"-{stale_ttl_hours} hours")
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur = conn.execute(f"DELETE FROM {tbl} {where}", params)
            total += cur.rowcount
    return total


def market_ohlcv_stats(*, path: str | None = None) -> dict:
    """Ritorna statistiche aggregate della cache OHLCV."""
    conn = connect(path)
    try:
        stats: dict = {}
        for interval in ("daily", "weekly"):
            table = f"market_ohlcv_{interval}"
            date_col = "date" if interval == "daily" else "week_start"
            row = conn.execute(
                f"""SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT ticker) AS n_tickers,
                    MIN({date_col}) AS date_min,
                    MAX({date_col}) AS date_max,
                    MAX(fetched_at) AS last_fetch
                FROM {table}"""
            ).fetchone()
            stats[interval] = dict(row)
    finally:
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# Ticker meta cache (sector, beta, name)
# ---------------------------------------------------------------------------
def market_meta_read(
    ticker: str,
    ttl_hours: float,
    *,
    path: str | None = None,
) -> dict | None:
    """Ritorna dict con sector/beta/name se cache fresh, altrimenti None."""
    conn = connect(path)
    try:
        row = conn.execute(
            """SELECT sector, beta, name, fetched_at FROM market_ticker_meta
               WHERE ticker = ?
                 AND fetched_at >= datetime('now', ?)""",
            (ticker.upper(), f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def market_meta_upsert(
    ticker: str,
    *,
    sector: str | None = None,
    beta: float | None = None,
    name: str | None = None,
    path: str | None = None,
) -> None:
    """UPSERT ticker meta. Campi None sono preservati se la riga esiste."""
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO market_ticker_meta (ticker, sector, beta, name, fetched_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(ticker) DO UPDATE SET
                   sector = COALESCE(excluded.sector, sector),
                   beta = COALESCE(excluded.beta, beta),
                   name = COALESCE(excluded.name, name),
                   fetched_at = CURRENT_TIMESTAMP""",
            (ticker.upper(), sector, beta, name),
        )


# ---------------------------------------------------------------------------
# Auto-migration hook (optional)
# ---------------------------------------------------------------------------
def auto_migrate_if_needed() -> bool:
    """Invoca la migration JSON → SQLite se il DB è vuoto e i JSON esistono.

    Returns:
        True se la migration è stata eseguita, False se no-op (DB già popolato
        o JSON assenti).
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return False  # DB nuovo, schema verrà creato al prossimo connect

    conn = connect()
    try:
        n_positions = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        n_watchlist = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    finally:
        conn.close()

    if n_positions > 0 or n_trades > 0 or n_watchlist > 0:
        return False  # DB già popolato — non fare nulla

    from propicks.config import JOURNAL_FILE, PORTFOLIO_FILE, WATCHLIST_FILE

    any_json_exists = any(
        os.path.exists(p) for p in (PORTFOLIO_FILE, JOURNAL_FILE, WATCHLIST_FILE)
    )
    if not any_json_exists:
        return False

    # Import lazy per evitare dep circolare durante i test
    from propicks.scripts.migrate_json_to_sqlite import run_migration
    run_migration(verbose=False)
    return True
