"""Audit trail delle esecuzioni di job → tabella ``scheduler_runs``.

Il decoratore ``@run_job`` wrappa ogni job function:
1. Inserisce una riga con status='running'
2. Esegue il job
3. Aggiorna la riga con finished_at + status + duration + n_items + error

Se il job solleva eccezione, il wrapper la cattura, logga, e la re-raise.
Il caller (CLI, scheduler) può decidere cosa fare (scheduler continua col
prossimo job; CLI fa exit code diverso).
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from functools import wraps
from typing import Any

from propicks.io.db import connect, transaction
from propicks.obs.log import get_logger

_log = get_logger("scheduler.history")


def _start_run(job_name: str) -> int:
    """Inserisce la riga iniziale con status='running'. Returns run_id."""
    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO scheduler_runs (job_name, status)
               VALUES (?, 'running')""",
            (job_name,),
        )
        return cur.lastrowid


def _finish_run(
    run_id: int,
    status: str,
    duration_ms: int,
    n_items: int | None = None,
    error: str | None = None,
    notes: str | None = None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """UPDATE scheduler_runs
               SET finished_at = CURRENT_TIMESTAMP,
                   status = ?,
                   duration_ms = ?,
                   n_items = ?,
                   error = ?,
                   notes = ?
               WHERE id = ?""",
            (status, duration_ms, n_items, error, notes, run_id),
        )


def run_job(name: str) -> Callable:
    """Decorator che wrappa un job function con audit logging.

    Il job deve ritornare un dict. Le chiavi speciali interpretate:
    - ``n_items`` (int): count di item processati → in scheduler_runs
    - ``notes`` (str): nota libera → in scheduler_runs

    Usage:
        @run_job("snapshot_portfolio")
        def snapshot_portfolio() -> dict:
            ...
            return {"n_items": 5, "notes": "daily_return=+0.8%"}
    """
    def decorator(fn: Callable[..., dict]) -> Callable[..., dict]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> dict:
            run_id = _start_run(name)
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs) or {}
                duration_ms = int((time.monotonic() - t0) * 1000)
                _finish_run(
                    run_id,
                    status="success",
                    duration_ms=duration_ms,
                    n_items=result.get("n_items"),
                    notes=result.get("notes"),
                )
                _log.info(
                    "job_success",
                    extra={"ctx": {
                        "job": name,
                        "duration_ms": duration_ms,
                        "n_items": result.get("n_items"),
                    }},
                )
                return result
            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                err_str = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()
                _finish_run(
                    run_id,
                    status="error",
                    duration_ms=duration_ms,
                    error=err_str,
                    notes=tb[-500:],  # ultimi 500 char del traceback
                )
                _log.error(
                    "job_error",
                    extra={"ctx": {
                        "job": name,
                        "duration_ms": duration_ms,
                        "error": err_str,
                    }},
                )
                print(f"[scheduler] {name} failed: {err_str}", file=sys.stderr)
                raise
        return wrapper
    return decorator


def list_recent_runs(limit: int = 20) -> list[dict]:
    """Ritorna gli ultimi N run per dashboard/CLI."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, job_name, started_at, finished_at, status,
                      duration_ms, n_items, error
               FROM scheduler_runs
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def stats_by_job(days: int = 30) -> list[dict]:
    """Aggregate per job_name: count, success_rate, avg_duration."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT
                 job_name,
                 COUNT(*) AS total,
                 SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                 SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                 ROUND(AVG(duration_ms), 0) AS avg_duration_ms
               FROM scheduler_runs
               WHERE started_at >= datetime('now', ?)
               GROUP BY job_name
               ORDER BY job_name""",
            (f"-{days} days",),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
