"""Persistenza e mutazioni della watchlist — backend SQLite.

La watchlist è pensata come incubatrice di idee: titoli classe B dallo
scanner (auto-popolati) + titoli aggiunti manualmente in attesa di
pullback / breakout / catalyst. Non sostituisce il portfolio: l'entry
passa comunque da ``propicks-portfolio add`` con sizing esplicito.

API pubblica invariata rispetto alla versione JSON. Il dict ritornato da
``load_watchlist()`` ha la stessa forma:
    {
        "tickers": {
            TICKER: {
                "added_date": "YYYY-MM-DD",
                "target_entry": float | None,
                "note": str | None,
                "score_at_add": float | None,
                "regime_at_add": str | None,
                "classification_at_add": str | None,
                "source": "manual" | "auto_scan" | "auto_scan_contra"
            }
        },
        "last_updated": str | None
    }

Dedup per ticker è garantito dalla PK sulla colonna ``ticker``.
"""

from __future__ import annotations

from datetime import datetime

from propicks.config import DATE_FMT
from propicks.io.db import connect, meta_get, transaction

_WATCHLIST_META_KEY = "watchlist_last_updated"


def _row_to_entry(row) -> dict:
    return {
        "added_date": row["added_date"],
        "target_entry": row["target_entry"],
        "note": row["note"],
        "score_at_add": row["score_at_add"],
        "regime_at_add": row["regime_at_add"],
        "classification_at_add": row["classification_at_add"],
        "source": row["source"],
    }


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------
def load_watchlist() -> dict:
    """Carica la watchlist. Ritorna dict legacy-compatibile.

    Schema: ``{"tickers": {TICKER: {...}}, "last_updated": str|None}``.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM watchlist ORDER BY ticker"
        ).fetchall()
    finally:
        conn.close()

    tickers = {row["ticker"]: _row_to_entry(row) for row in rows}
    last_updated = meta_get(_WATCHLIST_META_KEY, default=None)

    return {
        "tickers": tickers,
        "last_updated": last_updated,
    }


def save_watchlist(watchlist: dict) -> None:
    """Sincronizza il dict in-memory con il DB (upsert di tutte le entry).

    Come per ``portfolio_store.save_portfolio``, raramente necessario: le API
    mutanti (``add_to_watchlist``, ``remove_from_watchlist``, etc.) persistono
    già direttamente.
    """
    now = datetime.now().strftime(DATE_FMT)
    tickers = watchlist.get("tickers", {})

    with transaction() as conn:
        conn.execute("DELETE FROM watchlist")
        for ticker, entry in tickers.items():
            conn.execute(
                """INSERT INTO watchlist (
                    ticker, added_date, target_entry, note, score_at_add,
                    regime_at_add, classification_at_add, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker.upper(),
                    entry.get("added_date") or now,
                    entry.get("target_entry"),
                    entry.get("note"),
                    entry.get("score_at_add"),
                    entry.get("regime_at_add"),
                    entry.get("classification_at_add"),
                    entry.get("source", "manual"),
                ),
            )
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (_WATCHLIST_META_KEY, now),
        )
    watchlist["last_updated"] = now


# ---------------------------------------------------------------------------
# Mutating API
# ---------------------------------------------------------------------------
def add_to_watchlist(
    watchlist: dict,
    ticker: str,
    *,
    target_entry: float | None = None,
    note: str | None = None,
    score_at_add: float | None = None,
    regime_at_add: str | None = None,
    classification_at_add: str | None = None,
    source: str = "manual",
    added_date: str | None = None,
) -> tuple[dict, bool]:
    """Aggiunge o aggiorna un ticker in watchlist.

    Se il ticker esiste già, aggiorna SOLO i campi non-None forniti e
    preserva gli altri (es. ``added_date`` originale resta invariato,
    ``source`` originale resta invariato per auditability).

    Ritorna ``(entry, is_new)`` — lo stesso tuple della versione JSON.
    """
    ticker = ticker.upper()
    tickers = watchlist.setdefault("tickers", {})

    # Leggi lo stato corrente dal DB per avere la fonte di verità
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    is_new = row is None
    existing = _row_to_entry(row) if row else {}

    entry = {
        "added_date": (
            existing.get("added_date")
            or added_date
            or datetime.now().strftime(DATE_FMT)
        ),
        "target_entry": (
            round(target_entry, 2) if target_entry is not None
            else existing.get("target_entry")
        ),
        "note": note if note is not None else existing.get("note"),
        "score_at_add": (
            round(score_at_add, 1) if score_at_add is not None
            else existing.get("score_at_add")
        ),
        "regime_at_add": (
            regime_at_add if regime_at_add is not None
            else existing.get("regime_at_add")
        ),
        "classification_at_add": (
            classification_at_add if classification_at_add is not None
            else existing.get("classification_at_add")
        ),
        # Source: preserva quello originale se la entry esiste (auditability)
        "source": existing.get("source", source) if not is_new else source,
    }

    now = datetime.now().strftime(DATE_FMT)
    with transaction() as conn:
        conn.execute(
            """INSERT INTO watchlist (
                ticker, added_date, target_entry, note, score_at_add,
                regime_at_add, classification_at_add, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                target_entry = excluded.target_entry,
                note = excluded.note,
                score_at_add = excluded.score_at_add,
                regime_at_add = excluded.regime_at_add,
                classification_at_add = excluded.classification_at_add,
                last_updated = CURRENT_TIMESTAMP""",
            (
                ticker,
                entry["added_date"],
                entry["target_entry"],
                entry["note"],
                entry["score_at_add"],
                entry["regime_at_add"],
                entry["classification_at_add"],
                entry["source"],
            ),
        )
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (_WATCHLIST_META_KEY, now),
        )

    # Sync in-process dict per compatibilità con caller che lo rileggono
    tickers[ticker] = entry
    watchlist["last_updated"] = now
    return entry, is_new


def remove_from_watchlist(watchlist: dict, ticker: str) -> dict:
    ticker = ticker.upper()
    tickers = watchlist.get("tickers", {})

    # Fetch from DB as source of truth
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"{ticker} non è in watchlist.")
    entry = _row_to_entry(row)

    now = datetime.now().strftime(DATE_FMT)
    with transaction() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (_WATCHLIST_META_KEY, now),
        )

    tickers.pop(ticker, None)
    watchlist["last_updated"] = now
    return entry


def update_watchlist_entry(
    watchlist: dict,
    ticker: str,
    *,
    target_entry: float | None = None,
    note: str | None = None,
) -> dict:
    ticker = ticker.upper()
    tickers = watchlist.get("tickers", {})

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"{ticker} non è in watchlist.")
    if target_entry is None and note is None:
        raise ValueError("Specificare almeno un campo da aggiornare (target o note).")

    entry = _row_to_entry(row)
    setters: list[str] = []
    params: list = []
    if target_entry is not None:
        setters.append("target_entry = ?")
        params.append(round(target_entry, 2))
        entry["target_entry"] = round(target_entry, 2)
    if note is not None:
        setters.append("note = ?")
        params.append(note)
        entry["note"] = note
    setters.append("last_updated = CURRENT_TIMESTAMP")
    params.append(ticker)

    now = datetime.now().strftime(DATE_FMT)
    with transaction() as conn:
        conn.execute(
            f"UPDATE watchlist SET {', '.join(setters)} WHERE ticker = ?",
            params,
        )
        conn.execute(
            """INSERT INTO portfolio_meta (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (_WATCHLIST_META_KEY, now),
        )

    tickers[ticker] = entry
    watchlist["last_updated"] = now
    return entry


def is_stale(entry: dict, days: int = 60) -> bool:
    """True se ``added_date`` è più vecchia di ``days`` giorni."""
    added = entry.get("added_date")
    if not added:
        return False
    try:
        dt = datetime.strptime(added, DATE_FMT)
    except ValueError:
        return False
    return (datetime.now() - dt).days >= days
