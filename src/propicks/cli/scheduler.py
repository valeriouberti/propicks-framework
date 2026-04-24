"""CLI ``propicks-scheduler`` — daemon + job triggers + alerts + history.

Subcommands:

    propicks-scheduler run              # daemon APScheduler (bloccante)
    propicks-scheduler job NAME         # esegui un singolo job one-shot
    propicks-scheduler job NAME --date YYYY-MM-DD  # (solo snapshot/regime)
    propicks-scheduler alerts           # lista alert pending
    propicks-scheduler alerts --ack ID  # acknowledge singolo alert
    propicks-scheduler alerts --ack-all # acknowledge tutti pending
    propicks-scheduler history          # ultimi N run con status/duration
    propicks-scheduler history --days 7 # stats aggregate per job
"""

from __future__ import annotations

import argparse
import sys

from tabulate import tabulate

_JOB_REGISTRY: dict[str, str] = {
    "snapshot_portfolio": "snapshot_portfolio",
    "snapshot": "snapshot_portfolio",  # alias
    "record_regime": "record_regime",
    "regime": "record_regime",
    "warm_cache": "warm_cache",
    "warm": "warm_cache",
    "scan_watchlist": "scan_watchlist",
    "scan": "scan_watchlist",
    "trailing_stop_check": "trailing_stop_check",
    "trailing": "trailing_stop_check",
    "cleanup_stale_watchlist": "cleanup_stale_watchlist",
    "cleanup": "cleanup_stale_watchlist",
    "weekly_attribution_report": "weekly_attribution_report_job",
    "attribution": "weekly_attribution_report_job",  # alias
    "report": "weekly_attribution_report_job",
}


def cmd_run(_args: argparse.Namespace) -> int:
    """Avvia il daemon APScheduler. Bloccante."""
    from propicks.scheduler.runner import run_daemon
    return run_daemon()


def cmd_job(args: argparse.Namespace) -> int:
    """Esegue un singolo job one-shot. Utile per cron OS-level."""
    from propicks.scheduler import jobs

    canonical = _JOB_REGISTRY.get(args.name.lower())
    if canonical is None:
        print(
            f"[errore] job '{args.name}' non valido. Validi: "
            f"{', '.join(sorted(set(_JOB_REGISTRY.values())))}",
            file=sys.stderr,
        )
        return 2

    fn = getattr(jobs, canonical)

    # Alcuni job accettano un optional date override per backfill
    kwargs: dict = {}
    if canonical == "snapshot_portfolio" and args.date:
        kwargs["snapshot_date"] = args.date
    if canonical == "record_regime" and args.date:
        kwargs["record_date"] = args.date

    try:
        result = fn(**kwargs)
    except Exception as exc:
        print(f"[errore] {canonical} fallito: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"[{canonical}] ok — {result.get('notes', '')}")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    from propicks.scheduler.alerts import (
        acknowledge_alert,
        acknowledge_all,
        list_pending_alerts,
        stats,
    )

    if args.ack:
        if acknowledge_alert(args.ack):
            print(f"Alert {args.ack} acknowledged.")
            return 0
        print(f"[errore] alert {args.ack} non trovato o già acked", file=sys.stderr)
        return 2

    if args.ack_all:
        n = acknowledge_all()
        print(f"Acknowledged {n} alerts.")
        return 0

    if args.stats:
        s = stats()
        print(f"Pending total: {s['pending_total']}")
        if s["by_type"]:
            print(tabulate(s["by_type"], headers="keys", tablefmt="github"))
        return 0

    alerts = list_pending_alerts(limit=args.limit)
    if not alerts:
        print("Nessun alert pending.")
        return 0

    _SEV_BADGE = {"info": "ℹ", "warning": "⚠", "critical": "🚨"}
    rows = []
    for a in alerts:
        rows.append([
            a["id"],
            f"{_SEV_BADGE.get(a['severity'], '?')} {a['severity']}",
            a["type"],
            a.get("ticker") or "-",
            a["created_at"],
            a["message"][:80],
        ])
    print(
        tabulate(
            rows,
            headers=["ID", "Sev", "Type", "Ticker", "Created", "Message"],
            tablefmt="github",
        )
    )
    print(f"\n{len(alerts)} alert pending. Ack con: propicks-scheduler alerts --ack ID")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from propicks.scheduler.history import list_recent_runs, stats_by_job

    if args.days:
        # Modalità stats aggregate
        runs = stats_by_job(days=args.days)
        if not runs:
            print(f"Nessun run negli ultimi {args.days} giorni.")
            return 0
        rows = []
        for r in runs:
            success_rate = (
                f"{(r['success'] / r['total']) * 100:.0f}%"
                if r["total"] > 0 else "-"
            )
            rows.append([
                r["job_name"],
                r["total"],
                r["success"],
                r["errors"],
                success_rate,
                f"{r['avg_duration_ms'] or 0:.0f}ms",
            ])
        print(f"Stats ultimi {args.days} giorni:")
        print(
            tabulate(
                rows,
                headers=["Job", "Total", "Success", "Errors", "Rate", "Avg dur"],
                tablefmt="github",
            )
        )
        return 0

    runs = list_recent_runs(limit=args.limit)
    if not runs:
        print("Nessun run registrato.")
        return 0

    _STATUS_BADGE = {"success": "✓", "error": "✗", "running": "…", "partial": "◐"}
    rows = []
    for r in runs:
        rows.append([
            r["id"],
            r["job_name"],
            r["started_at"],
            f"{_STATUS_BADGE.get(r['status'], '?')} {r['status']}",
            f"{r['duration_ms'] or 0}ms" if r["duration_ms"] else "-",
            r["n_items"] if r["n_items"] is not None else "-",
            (r["error"] or "")[:40] if r["error"] else "-",
        ])
    print(
        tabulate(
            rows,
            headers=["ID", "Job", "Started", "Status", "Dur", "Items", "Error"],
            tablefmt="github",
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scheduler + alerts + history (Phase 3).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Avvia daemon APScheduler (bloccante)")
    p_run.set_defaults(func=cmd_run)

    p_job = sub.add_parser("job", help="Esegui un singolo job one-shot")
    p_job.add_argument(
        "name",
        help=(
            "Nome job: "
            + ", ".join(sorted(set(_JOB_REGISTRY.values())))
            + " (accetta anche alias: snapshot, regime, warm, scan, trailing, cleanup)"
        ),
    )
    p_job.add_argument(
        "--date",
        help="YYYY-MM-DD — override solo per snapshot_portfolio e record_regime (backfill)",
    )
    p_job.set_defaults(func=cmd_job)

    p_alerts = sub.add_parser("alerts", help="Gestione alert queue")
    p_alerts.add_argument("--ack", type=int, help="Acknowledge alert ID")
    p_alerts.add_argument("--ack-all", action="store_true", help="Acknowledge tutti pending")
    p_alerts.add_argument("--stats", action="store_true", help="Aggregate stats")
    p_alerts.add_argument("--limit", type=int, default=50, help="Righe max (default 50)")
    p_alerts.set_defaults(func=cmd_alerts)

    p_hist = sub.add_parser("history", help="Storia job run")
    p_hist.add_argument("--limit", type=int, default=20, help="Ultime N run (default 20)")
    p_hist.add_argument("--days", type=int, help="Se settato, stats aggregate ultimi N giorni")
    p_hist.set_defaults(func=cmd_history)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
