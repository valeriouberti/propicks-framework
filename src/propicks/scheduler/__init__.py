"""Scheduler infrastructure — APScheduler daemon + cron-callable jobs.

Due modalità operative:

1. **Daemon** via ``propicks-scheduler run``: BlockingScheduler con cron triggers
   (tz Europe/Rome). Richiede processo always-on (tmux/nohup/launchd).

2. **Cron-callable** via ``propicks-scheduler job <name>``: un singolo job
   eseguito one-shot. OS cron (launchd macOS, crontab Linux) triggera i
   comandi esterni. Più robusto per desktop-only — nessun daemon da
   supervisionare, reboot-safe via launchd/systemd.

Ogni job è una **funzione pura** in ``jobs.py`` (testabile, iniettabile),
wrappata dal decoratore ``@run_job`` in ``history.py`` che logga inizio/fine
in ``scheduler_runs``. Gli alert generati dai job vengono scritti in
``alerts`` via helpers di ``alerts.py``.
"""

from propicks.scheduler.jobs import (
    check_earnings_calendar,
    cleanup_stale_watchlist,
    record_regime,
    scan_watchlist,
    snapshot_portfolio,
    trailing_stop_check,
    warm_cache,
    weekly_attribution_report_job,
)

__all__ = [
    "check_earnings_calendar",
    "cleanup_stale_watchlist",
    "record_regime",
    "scan_watchlist",
    "snapshot_portfolio",
    "trailing_stop_check",
    "warm_cache",
    "weekly_attribution_report_job",
]
