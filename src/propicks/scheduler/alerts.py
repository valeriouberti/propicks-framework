"""CRUD + dedup sulla queue alerts.

Design: ogni alert ha un ``dedup_key`` (es. ``AAPL_watchlist_ready_2026-04-24``).
La funzione ``create_alert`` è **idempotente per dedup_key nello stesso giorno**:
se chiamata 2 volte con stesso key nella stessa giornata, la seconda no-op.
Questo evita spam di alert duplicati quando i job girano più volte al giorno
(e.g., warm_cache alle 17:45 + scan_watchlist alle 18:30 entrambi producono
READY alerts).
"""

from __future__ import annotations

import json
from typing import Any

from propicks.io.db import connect, transaction

AlertSeverity = str  # 'info' | 'warning' | 'critical'
AlertType = str      # 'watchlist_ready' | 'regime_change' | ...


def create_alert(
    alert_type: AlertType,
    message: str,
    *,
    severity: AlertSeverity = "info",
    ticker: str | None = None,
    metadata: dict[str, Any] | None = None,
    dedup_key: str | None = None,
) -> bool:
    """Crea un nuovo alert se non esiste già (dedup_key).

    Returns True se alert creato, False se skippato per dedup.

    Dedup logic: cerchiamo un alert con ``dedup_key`` matching e
    ``acknowledged = 0`` — se esiste, non creiamo duplicato. Gli alert
    già acknowledged possono essere ri-triggerati (es. READY alert la
    settimana dopo per lo stesso ticker).
    """
    if dedup_key:
        conn = connect()
        try:
            existing = conn.execute(
                """SELECT id FROM alerts
                   WHERE dedup_key = ? AND acknowledged = 0
                   LIMIT 1""",
                (dedup_key,),
            ).fetchone()
        finally:
            conn.close()
        if existing:
            return False

    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    with transaction() as conn:
        conn.execute(
            """INSERT INTO alerts (
                type, severity, ticker, message, metadata, dedup_key
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (alert_type, severity, ticker, message, meta_json, dedup_key),
        )
    return True


def list_pending_alerts(limit: int = 100) -> list[dict]:
    """Ritorna alert non-acknowledged, più recenti prima."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, created_at, type, severity, ticker, message,
                      metadata, dedup_key
               FROM alerts
               WHERE acknowledged = 0
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if d["metadata"]:
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def acknowledge_alert(alert_id: int) -> bool:
    """Segna come acknowledged. Returns True se modifica avvenuta."""
    with transaction() as conn:
        cur = conn.execute(
            """UPDATE alerts
               SET acknowledged = 1, acknowledged_at = CURRENT_TIMESTAMP
               WHERE id = ? AND acknowledged = 0""",
            (alert_id,),
        )
        return cur.rowcount > 0


def acknowledge_all() -> int:
    """Acknowledge di tutti gli alert pending. Ritorna numero acked.

    Utile per il trader dopo aver letto la lista — "mark all as read".
    """
    with transaction() as conn:
        cur = conn.execute(
            """UPDATE alerts
               SET acknowledged = 1, acknowledged_at = CURRENT_TIMESTAMP
               WHERE acknowledged = 0"""
        )
        return cur.rowcount


def stats() -> dict:
    """Aggregate stats — count per tipo, per severity."""
    conn = connect()
    try:
        by_type = conn.execute(
            """SELECT type, severity, COUNT(*) AS n
               FROM alerts WHERE acknowledged = 0
               GROUP BY type, severity
               ORDER BY n DESC"""
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE acknowledged = 0"
        ).fetchone()["n"]
    finally:
        conn.close()
    return {
        "pending_total": total,
        "by_type": [dict(r) for r in by_type],
    }
