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

# Throttle libsql ``conn.sync()`` per replica: il pull da Turso remote ha
# costo di rete (round-trip ~100-500ms vs nanosec dello SQLite stdlib).
# Il design del codebase apre una connessione per call → senza throttle ogni
# read della dashboard triggera un round-trip.
# Strategia: sync una volta per processo (cold start) + ogni N secondi
# (TTL configurabile via PROPICKS_LIBSQL_SYNC_INTERVAL_S, default 60s).
# Single-user: writes locali sono già visibili in replica senza sync remoto.
_LAST_SYNC_TS: dict[str, float] = {}

# Pool connessioni libsql per processo. ``libsql.connect()`` ha handshake
# ~1s + ``PRAGMA foreign_keys`` round-trip ~800ms. Riusare la stessa
# connessione elimina entrambi gli overhead. Threading: dashboard Streamlit
# è multi-thread ma single-user; lock per sicurezza, contention bassa.
import threading as _threading
_LIBSQL_POOL: dict[str, object] = {}
_LIBSQL_POOL_LOCK = _threading.Lock()

# Tabelle che NON vanno replicate su Turso: dati di cache regenerabili.
# In modalità Turso le redirigamo a un file SQLite locale separato per
# evitare il network round-trip ~300ms per write (bottleneck su discovery
# pipelines di 100+ ticker).
_LOCAL_CACHE_TABLES = frozenset({
    "market_ohlcv_daily",
    "market_ohlcv_weekly",
    "market_ticker_meta",
    "index_constituents",
})


def _local_cache_path() -> str:
    """Path SQLite locale per le tabelle di cache regenerabili. Sibling
    del DB principale con suffisso ``.cache``. In modalità non-Turso
    coincide con il DB principale (single file)."""
    return _get_db_path() + ".cache"


_LOCAL_CACHE_SCHEMA_APPLIED: set[str] = set()


def _connect_local_cache() -> sqlite3.Connection:
    """Apre connessione SQLite stdlib al file di cache locale (per tabelle
    in ``_LOCAL_CACHE_TABLES``). Bypassa libsql replica → write veloci.

    Schema condiviso con il DB principale (riusa schema.sql, le tabelle
    non-cache sono no-op CREATE IF NOT EXISTS extra ma idempotenti).
    """
    cache_path = _local_cache_path()
    cache_dir = os.path.dirname(cache_path)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    conn = sqlite3.connect(cache_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    if cache_path not in _LOCAL_CACHE_SCHEMA_APPLIED:
        conn.executescript(_load_schema_sql())
        conn.commit()
        _LOCAL_CACHE_SCHEMA_APPLIED.add(cache_path)
    return conn


def _connect_for_table(table: str, path: str | None = None) -> sqlite3.Connection:
    """Dispatcher: tabelle cache → SQLite locale, altre → connect() standard.

    In Turso il routing evita network round-trip per cache. In test
    (path override) bypassa il routing — usa il path esplicito per
    preservare isolation.
    """
    if path is None and _is_turso_enabled() and table in _LOCAL_CACHE_TABLES:
        return _connect_local_cache()
    return connect(path)


@contextmanager
def _transaction_for_table(table: str, path: str | None = None):
    """Transaction context routato per tabella. Mirror di ``transaction()``
    ma sceglie SQLite locale per le tabelle cache in modalità Turso.
    """
    if path is None and _is_turso_enabled() and table in _LOCAL_CACHE_TABLES:
        # Local SQLite — niente lock globale necessario (file separato dal pool)
        conn = _connect_local_cache()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    else:
        with transaction(path) as conn:
            yield conn


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


def _is_turso_enabled() -> bool:
    """True se le env vars Turso sono entrambe set.

    Modalità libsql embedded replica: file locale + sync remoto. Stessa API di
    sqlite3 (drop-in via ``libsql-experimental``). Senza env vars resta
    sqlite3 puro — comportamento identico a prima per dev locale.
    """
    return bool(os.environ.get("TURSO_DATABASE_URL")) and bool(
        os.environ.get("TURSO_AUTH_TOKEN")
    )


# ---------------------------------------------------------------------------
# Row/Cursor wrapper per libsql_experimental
# ---------------------------------------------------------------------------
# libsql_experimental.Cursor.fetchone() ritorna tuple — non ``sqlite3.Row``.
# Il codebase fa accesso ``row["col"]`` ovunque, quindi quando si usa libsql
# wrappiamo Connection/Cursor per emulare ``sqlite3.Row``. Sqlite3 path resta
# nativo (zero overhead).
class _LibsqlRow:
    """Tuple + description → dict-like row (compatibile con sqlite3.Row).

    Supporta: ``row["col"]``, ``row[0]``, ``dict(row)``, ``r is None``,
    ``"col" in row`` non supportato (non usato dal codebase).
    """
    __slots__ = ("_data", "_keys")

    def __init__(self, data: tuple, keys: list[str]) -> None:
        self._data = data
        self._keys = keys

    def __getitem__(self, key):  # int o str
        if isinstance(key, int):
            return self._data[key]
        try:
            return self._data[self._keys.index(key)]
        except ValueError as e:
            raise KeyError(key) from e

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self) -> list[str]:
        return list(self._keys)

    def __repr__(self) -> str:
        return f"_LibsqlRow({dict(zip(self._keys, self._data))})"


class _LibsqlCursorWrap:
    """Wrap libsql.Cursor: fetchone/fetchall/fetchmany ritornano _LibsqlRow."""
    def __init__(self, cur) -> None:
        self._cur = cur

    def __getattr__(self, name):
        # Forward attributi non override (rowcount, lastrowid, description, close, ecc.)
        return getattr(self._cur, name)

    def _keys(self) -> list[str]:
        desc = self._cur.description
        return [d[0] for d in desc] if desc else []

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _LibsqlRow(row, self._keys())

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        keys = self._keys()
        return [_LibsqlRow(r, keys) for r in rows]

    def fetchmany(self, size: int | None = None):
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        if not rows:
            return []
        keys = self._keys()
        return [_LibsqlRow(r, keys) for r in rows]

    def __iter__(self):
        keys = self._keys()
        for row in self._cur:
            yield _LibsqlRow(row, keys)


class _LibsqlConnectionWrap:
    """Wrap libsql.Connection: execute() → _LibsqlCursorWrap.

    Espone ``row_factory`` come attributo no-op (settable) per compat con
    codice che fa ``conn.row_factory = sqlite3.Row``. Sempre wrappiamo righe
    indipendentemente dal valore.
    """
    def __init__(self, conn) -> None:
        self._conn = conn
        self.row_factory = None  # no-op, sempre wrap

    def __getattr__(self, name):
        # Forward: commit, rollback, close, sync, in_transaction, autocommit, ecc.
        return getattr(self._conn, name)

    def execute(self, *args, **kwargs):
        return _LibsqlCursorWrap(self._conn.execute(*args, **kwargs))

    def executemany(self, *args, **kwargs):
        return _LibsqlCursorWrap(self._conn.executemany(*args, **kwargs))

    def executescript(self, *args, **kwargs):
        return self._conn.executescript(*args, **kwargs)

    def cursor(self):
        return _LibsqlCursorWrap(self._conn.cursor())

    def close(self) -> None:
        # No-op: la connessione è poolata a livello di processo (vedi
        # ``_LIBSQL_POOL``). Chiusura reale solo a process exit. Le call site
        # del codebase fanno ``conn.close()`` dopo ogni op (pattern sqlite3
        # stdlib); per libsql il close è troppo costoso (ricreare = 1.8s).
        return None


def connect(path: str | None = None) -> sqlite3.Connection:
    """Apre una connessione SQLite con settings standardizzati.

    - ``row_factory = sqlite3.Row`` → accesso per colonna via ``row["col"]``
    - ``PRAGMA foreign_keys = ON`` → integrity referenziale
    - ``PRAGMA journal_mode = WAL`` → reader concorrenti durante write (solo locale)
    - Parse esplicito di timestamp via ``detect_types``
    - Schema inizializzato se DB nuovo

    Driver selection (env-driven):
    - ``TURSO_DATABASE_URL`` + ``TURSO_AUTH_TOKEN`` set → ``libsql-experimental``
      embedded replica (file locale sincronizzato con Turso remote). Usato in
      deploy Streamlit Cloud / dashboard online.
    - Altrimenti → ``sqlite3`` stdlib (default per dev locale, CLI, tests).

    Args:
        path: override per test. Default: ``config.DB_FILE``.
    """
    db_path = path or _get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    if _is_turso_enabled():
        try:
            import libsql_experimental as libsql  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "TURSO_DATABASE_URL set but libsql-experimental non installato. "
                "Install: pip install -e '.[turso]'"
            ) from e
        # Replica path separato dal file SQLite stdlib: libsql usa formato
        # WAL proprietario, in conflitto con eventuale file SQLite preesistente
        # (errore ``wal_insert_begin failed``). Path canonico:
        # ``<original>.libsql``. Permette anche di tenere lo SQLite locale
        # come backup leggibile.
        replica_path = db_path + ".libsql"

        # Pool: una sola connessione per replica_path nel processo. Riusata
        # da tutti i call siti (CLAUDE.md "connection per call" rilassato in
        # modalità Turso per evitare ~1.8s overhead per call).
        with _LIBSQL_POOL_LOCK:
            cached = _LIBSQL_POOL.get(replica_path)
            if cached is None:
                raw_conn = libsql.connect(
                    replica_path,
                    sync_url=os.environ["TURSO_DATABASE_URL"],
                    auth_token=os.environ["TURSO_AUTH_TOKEN"],
                )
                conn = _LibsqlConnectionWrap(raw_conn)
                conn.execute("PRAGMA foreign_keys = ON")
                _LIBSQL_POOL[replica_path] = conn
            else:
                conn = cached  # type: ignore[assignment]
                raw_conn = conn._conn  # type: ignore[attr-defined]

        # Sync throttle: cold start sync + refresh ogni N secondi.
        # Default 60s — sufficiente per dashboard single-user. Force con
        # ``PROPICKS_LIBSQL_SYNC_INTERVAL_S=0`` (sync ad ogni connect, lento).
        import time as _time
        sync_interval = float(os.environ.get("PROPICKS_LIBSQL_SYNC_INTERVAL_S", "60"))
        now = _time.monotonic()
        last = _LAST_SYNC_TS.get(replica_path, 0.0)
        if now - last >= sync_interval:
            try:
                raw_conn.sync()
            except Exception:
                # Cold start con DB remoto vuoto: sync no-op accettabile.
                pass
            _LAST_SYNC_TS[replica_path] = now
        # journal_mode WAL non applicabile in modalità replica (gestito server-side).
    else:
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
    # Phase 4: delivery tracking su alerts (ALTER per DB esistenti pre-Phase 4)
    for column, ddl in (
        ("delivered", "ALTER TABLE alerts ADD COLUMN delivered INTEGER DEFAULT 0"),
        ("delivered_at", "ALTER TABLE alerts ADD COLUMN delivered_at TIMESTAMP"),
        ("delivery_error", "ALTER TABLE alerts ADD COLUMN delivery_error TEXT"),
    ):
        if not _column_exists(conn, "alerts", column):
            try:
                conn.execute(ddl)
            except (sqlite3.OperationalError, ValueError):
                # Tabella alerts non esiste ancora (edge case, race con CREATE).
                # Safe ignore: CREATE TABLE IF NOT EXISTS in schema.sql la creerà.
                pass

    # Index su ``delivered`` creato qui dopo che la colonna esiste (o già è stata
    # creata dallo schema.sql per DB nuovi). ``IF NOT EXISTS`` idempotente.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_undelivered "
            "ON alerts(delivered, created_at)"
        )
    except (sqlite3.OperationalError, ValueError):
        pass

    # Phase 8: earnings date columns su market_ticker_meta
    for column, ddl in (
        (
            "next_earnings_date",
            "ALTER TABLE market_ticker_meta ADD COLUMN next_earnings_date DATE",
        ),
        (
            "earnings_fetched_at",
            "ALTER TABLE market_ticker_meta ADD COLUMN earnings_fetched_at TIMESTAMP",
        ),
    ):
        if not _column_exists(conn, "market_ticker_meta", column):
            try:
                conn.execute(ddl)
            except (sqlite3.OperationalError, ValueError):
                pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_next_earnings "
            "ON market_ticker_meta(next_earnings_date)"
        )
    except (sqlite3.OperationalError, ValueError):
        pass


def _init_schema(conn: sqlite3.Connection) -> None:
    """Applica lo schema + migrations incrementali.

    Ordine: CREATE TABLE IF NOT EXISTS (nuove tabelle) → ALTER TABLE per
    colonne aggiunte (migrations). Entrambi idempotenti.

    In modalità Turso, lo schema remoto è source of truth: `executescript`
    è no-op (CREATE IF NOT EXISTS) e `_apply_migrations` viene skippato per
    evitare ALTER TABLE su colonne già presenti — la sync pulla lo schema
    completo dal remote. Le migration sono pensate per upgrade in-place di
    DB SQLite locali pre-esistenti.
    """
    conn.executescript(_load_schema_sql())
    conn.commit()
    if not _is_turso_enabled():
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


_TRANSACTION_LOCK = _threading.Lock()


@contextmanager
def transaction(path: str | None = None):
    """Context manager per transazioni atomiche con commit/rollback automatici.

    Example:
        with transaction() as conn:
            conn.execute("INSERT INTO positions ...")
            conn.execute("UPDATE portfolio_meta SET value=? WHERE key='cash'", (100,))
        # commit automatico; su exception → rollback

    In modalità Turso la connessione è poolata (singola per processo); il
    lock ``_TRANSACTION_LOCK`` serializza i ``BEGIN/COMMIT`` per evitare
    nesting illegale (BEGIN-on-BEGIN). Single-user, contention bassa.
    """
    pooled = _is_turso_enabled()
    if pooled:
        _TRANSACTION_LOCK.acquire()
    try:
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
    finally:
        if pooled:
            _TRANSACTION_LOCK.release()


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

    conn = _connect_for_table(table, path)
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

    conn = _connect_for_table(table, path)
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
    with _transaction_for_table(table, path) as conn:
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
    # Tutti i tables sono cache → routing identico (uso il primo come repr).
    with _transaction_for_table(tables[0], path) as conn:
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
    conn = _connect_for_table("market_ohlcv_daily", path)
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
    conn = _connect_for_table("market_ticker_meta", path)
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
    with _transaction_for_table("market_ticker_meta", path) as conn:
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


def market_earnings_read(
    ticker: str,
    ttl_hours: float,
    *,
    path: str | None = None,
) -> str | None:
    """Ritorna next_earnings_date ISO se in cache e non scaduto. Else None."""
    conn = _connect_for_table("market_ticker_meta", path)
    try:
        row = conn.execute(
            """SELECT next_earnings_date FROM market_ticker_meta
               WHERE ticker = ?
                 AND earnings_fetched_at IS NOT NULL
                 AND earnings_fetched_at >= datetime('now', ?)""",
            (ticker.upper(), f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return row["next_earnings_date"]


def market_earnings_upsert(
    ticker: str,
    next_earnings_date: str | None,
    *,
    path: str | None = None,
) -> None:
    """UPSERT earnings date. None valido = ticker senza earnings annunciato."""
    with _transaction_for_table("market_ticker_meta", path) as conn:
        conn.execute(
            """INSERT INTO market_ticker_meta (
                    ticker, next_earnings_date, earnings_fetched_at, fetched_at
               ) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(ticker) DO UPDATE SET
                   next_earnings_date = excluded.next_earnings_date,
                   earnings_fetched_at = CURRENT_TIMESTAMP""",
            (ticker.upper(), next_earnings_date),
        )


def index_constituents_is_fresh(
    index_name: str,
    ttl_hours: float,
    *,
    path: str | None = None,
) -> bool:
    """True se la cache ha ALMENO UNA riga non stale per ``index_name``."""
    conn = _connect_for_table("index_constituents", path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM index_constituents
               WHERE index_name = ?
                 AND fetched_at >= datetime('now', ?)""",
            (index_name, f"-{ttl_hours} hours"),
        ).fetchone()
    finally:
        conn.close()
    return row["n"] > 0


def index_constituents_read(
    index_name: str,
    *,
    path: str | None = None,
) -> list[dict]:
    """Ritorna list di dict {ticker, company_name, sector, added_date, fetched_at}."""
    conn = _connect_for_table("index_constituents", path)
    try:
        rows = conn.execute(
            """SELECT ticker, company_name, sector, added_date, fetched_at
               FROM index_constituents
               WHERE index_name = ?
               ORDER BY ticker ASC""",
            (index_name,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def index_constituents_replace(
    index_name: str,
    rows: list[dict],
    *,
    path: str | None = None,
) -> int:
    """Replace atomico della lista membri per un index.

    Pattern: DELETE + INSERT in una sola transazione. Evita lo stato
    intermedio "metà vecchi + metà nuovi" se la fetch ritorna una lista
    parziale. Se ``rows`` è vuoto, no-op (preserva il dato esistente —
    comportamento safe per snapshot fallback).
    """
    if not rows:
        return 0
    n = 0
    with _transaction_for_table("index_constituents", path) as conn:
        conn.execute(
            "DELETE FROM index_constituents WHERE index_name = ?", (index_name,)
        )
        for r in rows:
            conn.execute(
                """INSERT INTO index_constituents (
                    index_name, ticker, company_name, sector, added_date, fetched_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    index_name,
                    r["ticker"].upper(),
                    r.get("company_name"),
                    r.get("sector"),
                    r.get("added_date"),
                ),
            )
            n += 1
    return n


def market_earnings_all_from_cache(*, path: str | None = None) -> dict[str, str | None]:
    """Ritorna mappa {ticker: next_earnings_date} di TUTTE le righe in cache
    (anche stale). Usato per report / bulk check.
    """
    conn = _connect_for_table("market_ticker_meta", path)
    try:
        rows = conn.execute(
            """SELECT ticker, next_earnings_date FROM market_ticker_meta
               WHERE next_earnings_date IS NOT NULL"""
        ).fetchall()
    finally:
        conn.close()
    return {r["ticker"]: r["next_earnings_date"] for r in rows}


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
