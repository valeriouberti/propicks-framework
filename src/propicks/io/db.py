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

    is_new = not os.path.exists(db_path)

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

    if is_new:
        _init_schema(conn)

    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Applica lo schema via executescript (supporta multiple statements)."""
    conn.executescript(_load_schema_sql())
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
