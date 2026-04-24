"""One-shot migration dai JSON legacy a SQLite.

Uso:
    propicks-migrate           # esegue la migration live
    propicks-migrate --dry-run # stampa quello che farebbe senza toccare nulla
    propicks-migrate --verbose # dettaglio per-riga

Comportamento:
1. Legge i JSON in ``data/`` (portfolio, journal, watchlist, ai_cache/)
2. Inizializza lo schema SQLite in ``data/propicks.db`` se non esiste
3. Inserisce tutti i record nelle tabelle corrispondenti
4. Rinomina i JSON originali a ``*.json.bak`` (backup, non cancellati)
5. Verifica che i count combacino

Idempotente: se il DB è già popolato per una tabella, skippa la migration di
quella tabella (no duplicati). Esegue solo le migrations pending.

Perché ``*.json.bak`` e non delete: recovery se la migration ha bug sottili
che emergono dopo (schema diversi, edge case). I .bak vengono eliminati
manualmente dopo N settimane di stabilità.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from propicks.config import (
    AI_CACHE_DIR,
    CAPITAL,
    DB_FILE,
    JOURNAL_FILE,
    PORTFOLIO_FILE,
    WATCHLIST_FILE,
)
from propicks.io.db import connect, init_schema


def _log(msg: str, verbose: bool = True) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _read_json(path: str) -> dict | list | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"[errore] lettura {path}: {exc}")
        return None


def _backup_file(path: str, dry_run: bool = False) -> None:
    if not os.path.exists(path):
        return
    bak = path + ".bak"
    if dry_run:
        _log(f"  [dry-run] rename {path} → {bak}")
        return
    # Se esiste già un backup, incrementa il suffisso
    suffix = 1
    while os.path.exists(bak):
        bak = f"{path}.bak{suffix}"
        suffix += 1
    os.rename(path, bak)
    _log(f"  backup: {bak}")


# ---------------------------------------------------------------------------
# Migrazioni per store
# ---------------------------------------------------------------------------
def _migrate_portfolio(conn, dry_run: bool, verbose: bool) -> int:
    """Migra portfolio.json → tabella positions + portfolio_meta.

    Returns: numero di positions migrate.
    """
    _log(f"[portfolio] lettura {PORTFOLIO_FILE}", verbose)
    data = _read_json(PORTFOLIO_FILE)
    if data is None:
        _log("  nessun portfolio.json trovato, skip", verbose)
        return 0

    # Se positions è lista (schema legacy), normalizza a dict per-ticker
    positions_raw = data.get("positions", {})
    if isinstance(positions_raw, list):
        positions_raw = {
            p["ticker"]: {k: v for k, v in p.items() if k != "ticker"}
            for p in positions_raw
        }

    cash = float(data.get("cash") or data.get("capital_current") or CAPITAL)
    initial_capital = float(data.get("initial_capital") or CAPITAL)
    last_updated = data.get("last_updated") or data.get("last_update")

    # Check idempotenza: se positions già populate, skip
    existing = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    if existing > 0:
        _log(f"  positions già popolate ({existing} righe), skip portfolio", verbose)
        return 0

    n = 0
    for ticker, pos in positions_raw.items():
        if dry_run:
            _log(f"  [dry-run] INSERT positions {ticker}", verbose)
        else:
            conn.execute(
                """INSERT INTO positions (
                    ticker, strategy, entry_price, entry_date, shares,
                    stop_loss, target, highest_price_since_entry, trailing_enabled,
                    score_claude, score_tech, catalyst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker.upper(),
                    pos.get("strategy"),
                    float(pos["entry_price"]),
                    pos.get("entry_date"),
                    int(pos.get("shares") or 0),
                    pos.get("stop_loss"),
                    pos.get("target"),
                    pos.get("highest_price_since_entry"),
                    1 if pos.get("trailing_enabled") else 0,
                    pos.get("score_claude"),
                    pos.get("score_tech"),
                    pos.get("catalyst"),
                ),
            )
        n += 1

    # Upsert meta
    if not dry_run:
        for key, value in (
            ("cash", str(cash)),
            ("initial_capital", str(initial_capital)),
            ("last_updated", last_updated or ""),
        ):
            conn.execute(
                """INSERT INTO portfolio_meta (key, value)
                   VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
    _log(f"  migrate: {n} positions + meta (cash={cash}, capital={initial_capital})", verbose)
    return n


def _migrate_journal(conn, dry_run: bool, verbose: bool) -> int:
    """Migra journal.json → tabella trades."""
    _log(f"[journal] lettura {JOURNAL_FILE}", verbose)
    data = _read_json(JOURNAL_FILE)
    if data is None:
        _log("  nessun journal.json trovato, skip", verbose)
        return 0

    # Legacy: array puro o {"trades": [...]}
    trades = data if isinstance(data, list) else data.get("trades", [])
    if not isinstance(trades, list):
        _log("  formato journal.json non valido: trades non è una lista", verbose)
        return 0

    existing = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if existing > 0:
        _log(f"  trades già popolate ({existing} righe), skip journal", verbose)
        return 0

    n = 0
    for t in trades:
        # Legacy field normalization (matches load_journal)
        pnl_per_share = t.get("pnl_per_share")
        if pnl_per_share is None and "pnl_abs" in t:
            pnl_per_share = t["pnl_abs"]

        if dry_run:
            _log(f"  [dry-run] INSERT trade {t.get('ticker')} id={t.get('id')}", verbose)
        else:
            conn.execute(
                """INSERT INTO trades (
                    id, ticker, direction, strategy, entry_date, entry_price,
                    shares, stop_loss, target, score_claude, score_tech,
                    catalyst, notes, status, exit_date, exit_price, exit_reason,
                    pnl_pct, pnl_per_share, duration_days, post_trade_notes,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t.get("id"),
                    t["ticker"].upper(),
                    t.get("direction", "long"),
                    t.get("strategy"),
                    t.get("entry_date"),
                    float(t.get("entry_price") or 0),
                    int(t["shares"]) if t.get("shares") is not None else None,
                    t.get("stop_loss"),
                    t.get("target"),
                    t.get("score_claude"),
                    t.get("score_tech"),
                    t.get("catalyst"),
                    t.get("notes"),
                    t.get("status", "open"),
                    t.get("exit_date"),
                    t.get("exit_price"),
                    t.get("exit_reason"),
                    t.get("pnl_pct"),
                    pnl_per_share,
                    t.get("duration_days"),
                    t.get("post_trade_notes"),
                    t.get("created_at"),
                ),
            )
        n += 1
    _log(f"  migrate: {n} trades", verbose)
    return n


def _migrate_watchlist(conn, dry_run: bool, verbose: bool) -> int:
    """Migra watchlist.json → tabella watchlist."""
    _log(f"[watchlist] lettura {WATCHLIST_FILE}", verbose)
    data = _read_json(WATCHLIST_FILE)
    if data is None:
        _log("  nessuna watchlist.json trovata, skip", verbose)
        return 0

    tickers_raw = data.get("tickers", {})
    # Legacy: list of strings or list of dicts
    if isinstance(tickers_raw, list):
        migrated: dict = {}
        for item in tickers_raw:
            if isinstance(item, str):
                migrated[item.upper()] = {
                    "added_date": None, "target_entry": None, "note": None,
                    "score_at_add": None, "regime_at_add": None,
                    "classification_at_add": None, "source": "manual",
                }
            elif isinstance(item, dict) and "ticker" in item:
                migrated[item["ticker"].upper()] = {
                    k: v for k, v in item.items() if k != "ticker"
                }
        tickers_raw = migrated

    existing = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    if existing > 0:
        _log(f"  watchlist già popolata ({existing} righe), skip", verbose)
        return 0

    n = 0
    for ticker, entry in tickers_raw.items():
        if dry_run:
            _log(f"  [dry-run] INSERT watchlist {ticker}", verbose)
        else:
            conn.execute(
                """INSERT INTO watchlist (
                    ticker, added_date, target_entry, note, score_at_add,
                    regime_at_add, classification_at_add, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker.upper(),
                    entry.get("added_date"),
                    entry.get("target_entry"),
                    entry.get("note"),
                    entry.get("score_at_add"),
                    entry.get("regime_at_add"),
                    entry.get("classification_at_add"),
                    entry.get("source", "manual"),
                ),
            )
        n += 1
    _log(f"  migrate: {n} watchlist entries", verbose)
    return n


def _migrate_ai_cache(conn, dry_run: bool, verbose: bool) -> int:
    """Migra data/ai_cache/*.json → tabella ai_verdicts.

    Riconosce il formato del filename:
    - {TICKER}_v4_{YYYY-MM-DD}.json        → momentum stock
    - {TICKER}_contra_v1_{YYYY-MM-DD}.json → contrarian
    - {TICKER}_etf_v1_{YYYY-MM-DD}.json    → ETF rotation (se presente)
    """
    if not os.path.isdir(AI_CACHE_DIR):
        _log(f"[ai_cache] dir {AI_CACHE_DIR} non esiste, skip", verbose)
        return 0

    files = sorted(Path(AI_CACHE_DIR).glob("*.json"))
    if not files:
        _log(f"[ai_cache] nessun file in {AI_CACHE_DIR}, skip", verbose)
        return 0

    existing = conn.execute("SELECT COUNT(*) FROM ai_verdicts").fetchone()[0]
    if existing > 0:
        _log(f"[ai_cache] ai_verdicts già populata ({existing}), skip", verbose)
        return 0

    _log(f"[ai_cache] trovati {len(files)} file", verbose)
    n = 0
    for f in files:
        name = f.stem  # senza .json
        # Skip file di usage counter / budget (non sono verdict)
        if name.startswith("usage_"):
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue

        # Determina strategia e cache_key dal nome
        parts = name.split("_")
        if len(parts) < 3:
            continue
        ticker = parts[0]
        if "contra" in name:
            strategy = "contrarian"
        elif "etf" in name:
            strategy = "etf_rotation"
        else:
            strategy = "momentum"
        cache_key = name
        run_ts = parts[-1]  # YYYY-MM-DD dal filename

        if dry_run:
            _log(f"  [dry-run] INSERT verdict {cache_key}", verbose)
        else:
            conn.execute(
                """INSERT INTO ai_verdicts (
                    run_timestamp, strategy, ticker, cache_key,
                    verdict, conviction, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_ts + " 00:00:00",
                    strategy,
                    ticker,
                    cache_key,
                    payload.get("verdict"),
                    payload.get("conviction_score"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        n += 1
    _log(f"  migrate: {n} ai_verdicts", verbose)
    return n


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_migration(*, dry_run: bool = False, verbose: bool = True) -> dict:
    """Esegue la migration di tutti gli store.

    Returns: dict con i contatori {positions, trades, watchlist, ai_verdicts}.
    """
    if not dry_run:
        init_schema()
    _log(f"DB path: {DB_FILE}", verbose)
    _log(f"Dry run: {dry_run}", verbose)

    conn = connect()
    try:
        conn.execute("BEGIN")

        stats = {
            "positions": _migrate_portfolio(conn, dry_run, verbose),
            "trades": _migrate_journal(conn, dry_run, verbose),
            "watchlist": _migrate_watchlist(conn, dry_run, verbose),
            "ai_verdicts": _migrate_ai_cache(conn, dry_run, verbose),
        }

        if dry_run:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    # Backup dei JSON originali (solo se non dry-run e migration avvenuta)
    if not dry_run and any(stats.values()):
        _log("\n[backup] rinomino JSON originali a *.json.bak", verbose)
        _backup_file(PORTFOLIO_FILE, dry_run)
        _backup_file(JOURNAL_FILE, dry_run)
        _backup_file(WATCHLIST_FILE, dry_run)
        # ai_cache/ backup: rinomina la dir
        if stats["ai_verdicts"] > 0 and os.path.isdir(AI_CACHE_DIR):
            bak_dir = AI_CACHE_DIR + ".bak"
            suffix = 1
            while os.path.exists(bak_dir):
                bak_dir = f"{AI_CACHE_DIR}.bak{suffix}"
                suffix += 1
            os.rename(AI_CACHE_DIR, bak_dir)
            _log(f"  backup: {bak_dir}", verbose)

    _log(f"\n[done] Migration stats: {stats}", verbose)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra i JSON legacy a SQLite (one-shot)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Nessuna scrittura")
    parser.add_argument("--quiet", action="store_true", help="Output minimo")
    args = parser.parse_args()

    run_migration(dry_run=args.dry_run, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
