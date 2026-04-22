"""Persistenza e mutazioni della watchlist.

Schema data/watchlist.json:
    {
        "tickers": {
            TICKER: {
                "added_date": "YYYY-MM-DD",
                "target_entry": float | None,
                "note": str | None,
                "score_at_add": float | None,
                "regime_at_add": str | None,
                "classification_at_add": str | None,
                "source": "manual" | "auto_scan"
            }
        },
        "last_updated": str | None
    }

La watchlist è pensata come incubatrice di idee: titoli classe B dallo
scanner (auto-popolati) + titoli aggiunti manualmente in attesa di
pullback / breakout / catalyst. Non sostituisce il portfolio: l'entry
passa comunque da ``propicks-portfolio add`` con sizing esplicito.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from propicks.config import DATE_FMT, WATCHLIST_FILE
from propicks.io.atomic import atomic_write_json
from propicks.io.migrations import migrate, stamp_version


def _default_watchlist() -> dict:
    return {"tickers": {}, "last_updated": None}


def load_watchlist() -> dict:
    """Carica la watchlist, migrando schema legacy (tickers come lista)."""
    if not os.path.exists(WATCHLIST_FILE):
        wl = _default_watchlist()
        save_watchlist(wl)
        return wl

    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"[fatal] watchlist.json corrotto: {exc}. "
            f"Ripristina da backup o correggi manualmente."
        ) from exc

    # Migrazione schema legacy: {"tickers": []} o {"tickers": [str, ...]}
    if isinstance(data.get("tickers"), list):
        legacy = data.get("tickers", [])
        migrated: dict = {}
        for item in legacy:
            if isinstance(item, str):
                migrated[item.upper()] = {
                    "added_date": None,
                    "target_entry": None,
                    "note": None,
                    "score_at_add": None,
                    "regime_at_add": None,
                    "classification_at_add": None,
                    "source": "manual",
                }
            elif isinstance(item, dict) and "ticker" in item:
                t = item["ticker"].upper()
                migrated[t] = {k: v for k, v in item.items() if k != "ticker"}
        data = {"tickers": migrated, "last_updated": data.get("last_updated")}

    data.setdefault("tickers", {})
    data.setdefault("last_updated", None)
    data = migrate(data, "watchlist")
    return data


def save_watchlist(watchlist: dict) -> None:
    watchlist["last_updated"] = datetime.now().strftime(DATE_FMT)
    stamp_version(watchlist, "watchlist")
    atomic_write_json(WATCHLIST_FILE, watchlist)


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

    Se il ticker esiste già, aggiorna SOLO i campi non None forniti e
    preserva gli altri (es. `added_date` originale resta invariato).
    Ritorna ``(entry, is_new)``.
    """
    ticker = ticker.upper()
    tickers = watchlist.setdefault("tickers", {})

    is_new = ticker not in tickers
    existing = tickers.get(ticker, {})
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
        "source": existing.get("source", source) if not is_new else source,
    }
    tickers[ticker] = entry
    save_watchlist(watchlist)
    return entry, is_new


def remove_from_watchlist(watchlist: dict, ticker: str) -> dict:
    ticker = ticker.upper()
    tickers = watchlist.get("tickers", {})
    if ticker not in tickers:
        raise ValueError(f"{ticker} non è in watchlist.")
    entry = tickers.pop(ticker)
    save_watchlist(watchlist)
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
    if ticker not in tickers:
        raise ValueError(f"{ticker} non è in watchlist.")
    if target_entry is None and note is None:
        raise ValueError("Specificare almeno un campo da aggiornare (target o note).")
    entry = tickers[ticker]
    if target_entry is not None:
        entry["target_entry"] = round(target_entry, 2)
    if note is not None:
        entry["note"] = note
    save_watchlist(watchlist)
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
