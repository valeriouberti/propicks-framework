"""APScheduler daemon — cron triggers per i 6 job.

Modalità: ``BlockingScheduler`` con tz ``Europe/Rome``. Richiede processo
always-on (tmux/nohup/launchd). Per setup desktop-only senza daemon, usare
``propicks-scheduler job <name>`` triggerato da OS cron (vedere CLAUDE.md
sezione Phase 3 per esempio crontab/launchd).

**Timing**: scelto per EU trading desk (Italia). Sessione EU chiude alle
17:30 CET/CEST, US alle 22:00 CET. I job EOD girano dopo EU close quando
yfinance ha dati freschi per EU, US ancora aggiornerà alle 22:00 (second
pass opzionale).

| Job | Trigger | Rationale |
|-----|---------|-----------|
| warm_cache | Mon-Fri 17:45 CET | Prefetch pre-scan (5 min pre-EOD EU) |
| record_regime | Mon-Fri 18:00 CET | Regime ^GSPC weekly change detection |
| snapshot_portfolio | Mon-Fri 18:30 CET | Equity curve + exposure breakdown |
| scan_watchlist | Mon-Fri 18:30 CET | READY detection + strategy_runs log |
| trailing_stop_check | Mon-Fri 18:30 CET | Suggest update trailing stops |
| cleanup_stale_watchlist | Sun 20:00 CET | Weekly housekeeping |

Graceful shutdown: SIGINT / SIGTERM → scheduler.shutdown(wait=True).
"""

from __future__ import annotations

import signal
import sys

from propicks.obs.log import get_logger
from propicks.scheduler.jobs import (
    cleanup_stale_watchlist,
    record_regime,
    scan_watchlist,
    snapshot_portfolio,
    trailing_stop_check,
    warm_cache,
    weekly_attribution_report_job,
)

_log = get_logger("scheduler.runner")

_TZ = "Europe/Rome"


def _add_jobs(scheduler) -> None:
    """Registra tutti i job con cron triggers."""
    from apscheduler.triggers.cron import CronTrigger

    # Weekday triggers (mon-fri) per i job EOD
    weekday_cron = lambda h, m: CronTrigger(  # noqa: E731
        day_of_week="mon-fri", hour=h, minute=m, timezone=_TZ
    )

    scheduler.add_job(
        warm_cache,
        trigger=weekday_cron(17, 45),
        id="warm_cache",
        name="warm_cache",
        replace_existing=True,
        misfire_grace_time=1800,  # fino a 30 min di ritardo tollerato
    )
    scheduler.add_job(
        record_regime,
        trigger=weekday_cron(18, 0),
        id="record_regime",
        name="record_regime",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        snapshot_portfolio,
        trigger=weekday_cron(18, 30),
        id="snapshot_portfolio",
        name="snapshot_portfolio",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        scan_watchlist,
        trigger=weekday_cron(18, 30),
        id="scan_watchlist",
        name="scan_watchlist",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        trailing_stop_check,
        trigger=weekday_cron(18, 30),
        id="trailing_stop_check",
        name="trailing_stop_check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Weekly: domenica 20:00 CET
    scheduler.add_job(
        cleanup_stale_watchlist,
        trigger=CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=_TZ),
        id="cleanup_stale_watchlist",
        name="cleanup_stale_watchlist",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Weekly attribution report: sabato 21:00 CET (mercati chiusi, dati stabili)
    scheduler.add_job(
        weekly_attribution_report_job,
        trigger=CronTrigger(day_of_week="sat", hour=21, minute=0, timezone=_TZ),
        id="weekly_attribution_report",
        name="weekly_attribution_report",
        replace_existing=True,
        misfire_grace_time=7200,
    )


def run_daemon() -> int:
    """Avvia il BlockingScheduler daemon. Bloccante fino a SIGINT/SIGTERM."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError as exc:
        print(
            "[errore] apscheduler non installato. Installa con: pip install -e .",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    scheduler = BlockingScheduler(timezone=_TZ)
    _add_jobs(scheduler)

    def _shutdown(signum, _frame) -> None:
        _log.info("scheduler_shutdown", extra={"ctx": {"signal": signum}})
        print(f"\n[scheduler] shutdown ({signum})...", file=sys.stderr)
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(
        f"[scheduler] avviato (tz={_TZ})\n"
        "Jobs registrati:\n"
        "  • warm_cache                 Mon-Fri 17:45\n"
        "  • record_regime              Mon-Fri 18:00\n"
        "  • snapshot_portfolio         Mon-Fri 18:30\n"
        "  • scan_watchlist             Mon-Fri 18:30\n"
        "  • trailing_stop_check        Mon-Fri 18:30\n"
        "  • weekly_attribution_report  Sat 21:00\n"
        "  • cleanup_stale_watchlist    Sun 20:00\n"
        "\nCtrl+C per fermare.",
        file=sys.stderr,
    )
    _log.info("scheduler_started", extra={"ctx": {"tz": _TZ, "n_jobs": 7}})

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0
