"""CLI sync Turso ↔ SQLite locale.

Modes:
    propicks-sync                # default: bidirectional via libsql sync
    propicks-sync --check        # dry-run: count diff per tabella, no scrittura
    propicks-sync --pull         # forced pull remote → local (overwrite local)
    propicks-sync --push         # push local → remote
    propicks-sync --backup       # crea backup locale + sync bidirectional

Richiede env vars:
    TURSO_DATABASE_URL=libsql://...
    TURSO_AUTH_TOKEN=eyJ...

Oppure passali in `.streamlit/secrets.toml` o esporta in shell.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

from propicks.config import DATA_DIR, DB_FILE


def _check_secrets() -> tuple[str, str]:
    """Carica TURSO_DATABASE_URL + TURSO_AUTH_TOKEN da env o secrets.toml."""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")

    if not url or not token:
        # Try .streamlit/secrets.toml fallback (anche se commented)
        from propicks.config import BASE_DIR
        secrets_path = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            try:
                import tomllib  # py3.11+
            except ImportError:
                tomllib = None  # type: ignore
            if tomllib is not None:
                try:
                    with open(secrets_path, "rb") as f:
                        secrets = tomllib.load(f)
                    if not url:
                        url = secrets.get("TURSO_DATABASE_URL")
                    if not token:
                        token = secrets.get("TURSO_AUTH_TOKEN")
                except Exception:
                    pass

    if not url or not token:
        print(
            "[errore] TURSO_DATABASE_URL + TURSO_AUTH_TOKEN non impostate.\n"
            "Esporta in shell oppure decommenta in `.streamlit/secrets.toml`.",
            file=sys.stderr,
        )
        sys.exit(1)

    return url, token


def _import_libsql():
    """Lazy import libsql_experimental con error message friendly."""
    try:
        import libsql_experimental as libsql  # noqa: F401
        return libsql
    except ImportError:
        print(
            "[errore] libsql_experimental non installato. Run:\n"
            "    pip install -e '.[turso]'",
            file=sys.stderr,
        )
        sys.exit(1)


def _local_counts(db_path: str) -> dict[str, int]:
    """Count rows per tabella sul DB locale SQLite."""
    if not os.path.exists(db_path):
        return {}
    counts: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'libsql_%' "
            "ORDER BY name"
        ).fetchall()
        for (name,) in rows:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                counts[name] = n
            except sqlite3.OperationalError:
                counts[name] = -1
    finally:
        conn.close()
    return counts


def _remote_counts(libsql, url: str, token: str) -> dict[str, int]:
    """Pull-only embedded replica → count rows per tabella remote."""
    tmp = tempfile.mkdtemp()
    try:
        replica = os.path.join(tmp, "remote_check.db")
        conn = libsql.connect(replica, sync_url=url, auth_token=token)
        conn.sync()
        counts: dict[str, int] = {}
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'libsql_%' "
            "ORDER BY name"
        ).fetchall()
        for r in rows:
            name = r[0] if not hasattr(r, "__getitem__") else r[0]
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                counts[name] = n
            except Exception:
                counts[name] = -1
        return counts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _backup_local(db_path: str) -> str:
    """Crea backup file `data/backups/propicks-YYYYMMDD-HHMMSS.db`."""
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bpath = os.path.join(backup_dir, f"propicks-{ts}.db")
    shutil.copy2(db_path, bpath)
    return bpath


def _print_diff(local: dict, remote: dict) -> int:
    """Print tabella diff. Returns count rows in disagreement."""
    all_tables = sorted(set(local.keys()) | set(remote.keys()))
    print()
    print(f"  {'Table':<32} {'Local':>10} {'Remote':>10} {'Δ':>8}")
    print(f"  {'-' * 32} {'-' * 10} {'-' * 10} {'-' * 8}")
    diff_count = 0
    for t in all_tables:
        loc_n = local.get(t, 0)
        rem_n = remote.get(t, 0)
        delta = rem_n - loc_n
        marker = "" if delta == 0 else (" ←" if delta > 0 else " →")
        print(f"  {t:<32} {loc_n:>10} {rem_n:>10} {delta:>+8}{marker}")
        if delta != 0:
            diff_count += abs(delta)
    return diff_count


def cmd_check(args: argparse.Namespace) -> int:
    """Dry-run: confronta counts locale vs remote senza scrivere."""
    url, token = _check_secrets()
    libsql = _import_libsql()

    print(f"📊 Local DB: {DB_FILE}")
    local = _local_counts(DB_FILE)
    print(f"   {sum(local.values())} righe totali in {len(local)} tabelle")

    print(f"\n📡 Remote: {url}")
    print("   Pulling…")
    remote = _remote_counts(libsql, url, token)
    print(f"   {sum(remote.values())} righe totali in {len(remote)} tabelle")

    diff = _print_diff(local, remote)
    print()
    if diff == 0:
        print("✅ Local + Remote allineati.")
        return 0
    else:
        print(f"⚠️  {diff} righe in disagreement. "
              "Run senza --check per applicare sync bidirectional.")
        return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Sync bidirectional via libsql embedded replica."""
    url, token = _check_secrets()
    libsql = _import_libsql()

    if args.backup:
        if os.path.exists(DB_FILE):
            bpath = _backup_local(DB_FILE)
            print(f"💾 Backup locale: {bpath}")
        else:
            print(f"[warn] DB locale non esiste ({DB_FILE}), skip backup")

    print(f"📡 Sync embedded replica {DB_FILE} ↔ {url}")
    print("   1. Connect + pull remote")
    conn = libsql.connect(DB_FILE, sync_url=url, auth_token=token)
    conn.sync()
    print("   2. Pulled.")

    # Recompute counts post-sync
    local = _local_counts(DB_FILE)
    print(f"   {sum(local.values())} righe totali post-sync")

    # Show summary
    print()
    print(f"  {'Table':<32} {'Rows':>10}")
    print(f"  {'-' * 32} {'-' * 10}")
    for t, n in sorted(local.items()):
        print(f"  {t:<32} {n:>10}")
    print()
    print("✅ Sync completato.")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Forced pull remote → local. Overwrite local DB.

    Usa sqlite3 .backup per copia clean (no libsql metadata) → file SQLite
    nativo in `data/propicks.db`.
    """
    url, token = _check_secrets()
    libsql = _import_libsql()

    if args.backup and os.path.exists(DB_FILE):
        bpath = _backup_local(DB_FILE)
        print(f"💾 Backup locale: {bpath}")

    print(f"📡 Forced pull {url} → {DB_FILE}")
    tmp = tempfile.mkdtemp()
    try:
        replica = os.path.join(tmp, "pull.db")
        conn = libsql.connect(replica, sync_url=url, auth_token=token)
        conn.sync()
        # Clean copy via sqlite .backup
        src = sqlite3.connect(replica)
        dst = sqlite3.connect(DB_FILE)
        src.backup(dst)
        src.close()
        dst.close()
        print("✅ Pull completato. Local DB sostituito da remote state.")

        local = _local_counts(DB_FILE)
        print(f"   {sum(local.values())} righe totali in {len(local)} tabelle")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_push(args: argparse.Namespace) -> int:
    """Push local → remote via libsql embedded replica.

    Apre local DB come embedded replica con sync_url, fa una scrittura no-op
    per triggerare push (libsql_experimental sync() è bidirectional).
    """
    url, token = _check_secrets()
    libsql = _import_libsql()

    if not os.path.exists(DB_FILE):
        print(f"[errore] DB locale {DB_FILE} non esiste.", file=sys.stderr)
        return 1

    print(f"📡 Push {DB_FILE} → {url}")
    conn = libsql.connect(DB_FILE, sync_url=url, auth_token=token)
    print("   1. sync() bidirectional (push pending writes + pull remote)")
    conn.sync()
    print("✅ Push completato.")

    # Verify remote
    print("\n📡 Verify remote post-push:")
    remote = _remote_counts(libsql, url, token)
    print(f"   {sum(remote.values())} righe totali in {len(remote)} tabelle")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Turso remote ↔ SQLite locale.",
        epilog=(
            "Esempi:\n"
            "  propicks-sync                # bidirectional sync\n"
            "  propicks-sync --check        # dry-run diff\n"
            "  propicks-sync pull           # forced pull remote → local\n"
            "  propicks-sync push           # push local → remote\n"
            "  propicks-sync --backup       # bidirectional + backup pre-sync"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    # Default: bidirectional sync (no subcmd needed)
    parser.add_argument(
        "--check", action="store_true",
        help="Dry-run: confronta counts senza modificare",
    )
    parser.add_argument(
        "--backup", action="store_true",
        help="Crea backup data/backups/propicks-TIMESTAMP.db prima del sync",
    )

    # Subcommands espliciti
    p_pull = sub.add_parser("pull", help="Forced pull remote → local")
    p_pull.add_argument("--backup", action="store_true", default=True,
                        help="Backup locale prima (default ON)")
    p_pull.set_defaults(func=cmd_pull)

    p_push = sub.add_parser("push", help="Push local → remote")
    p_push.set_defaults(func=cmd_push)

    p_check = sub.add_parser("check", help="Dry-run: confronta counts")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()

    # Subcommand routing
    if hasattr(args, "func"):
        return args.func(args)

    # No subcmd: default flow
    if args.check:
        return cmd_check(args)
    return cmd_sync(args)


if __name__ == "__main__":
    sys.exit(main())
