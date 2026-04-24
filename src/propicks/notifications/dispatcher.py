"""Dispatcher: polla alerts undelivered, invia Telegram, marca delivered.

Async (python-telegram-bot v20+ è async-native). Il bot daemon lancia una
task concorrente che esegue ``dispatch_pending`` ogni ``poll_interval`` sec.

Failure handling:
- Se l'invio fallisce (rate limit, rete, chat_id invalido), salviamo
  ``delivery_error`` ma lasciamo ``delivered=0`` → il prossimo ciclo ritenta.
- Dopo 3 tentativi falliti (tracciato tramite delivery_error counter
  semplice), il bot logga warning e passa oltre per evitare flood.
- Chat list vuota → no-op silenzioso (nessun errore, utile per dev senza bot).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Protocol

from propicks.io.db import connect, transaction
from propicks.notifications.formatter import alert_to_markdown
from propicks.obs.log import get_logger

_log = get_logger("notifications.dispatcher")


class _TelegramClient(Protocol):
    """Sottoinsieme dell'API python-telegram-bot necessario al dispatcher.

    Tutti i test mockano questa interfaccia — il dispatcher non importa
    direttamente ``telegram``, dipende solo da questo contratto.
    """

    async def send_message(
        self, chat_id: str | int, text: str, parse_mode: str | None = None
    ) -> Any: ...


MAX_RETRIES = 3


def _fetch_pending(limit: int = 50) -> list[dict]:
    """Seleziona alerts con delivered=0 (nuovi o retry < MAX_RETRIES).

    Ordering: più recenti prima (ORDER BY created_at DESC). Se abbiamo
    accumulato 100 alert, preferiamo mandare gli ultimi — sono più rilevanti
    del backlog vecchio.

    Filtriamo ``delivery_error`` per contare i tentativi falliti. Usiamo
    un encoding semplice: ``delivery_error = "try:N|last_err"``.
    """
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, type, severity, ticker, message, metadata,
                      dedup_key, created_at, delivery_error
               FROM alerts
               WHERE delivered = 0
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        # Parse metadata JSON
        if d["metadata"]:
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (ValueError, TypeError):
                d["metadata"] = {}
        # Skip alerts oltre max retries
        retry_count = _parse_retry_count(d.get("delivery_error"))
        if retry_count >= MAX_RETRIES:
            continue
        out.append(d)
    return out


def _parse_retry_count(delivery_error: str | None) -> int:
    """Estrae il counter da ``try:N|...``. 0 se None o parse fail."""
    if not delivery_error:
        return 0
    if delivery_error.startswith("try:"):
        try:
            return int(delivery_error.split("|", 1)[0].split(":", 1)[1])
        except (ValueError, IndexError):
            return 0
    return 0


def _mark_delivered(alert_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            """UPDATE alerts
               SET delivered = 1,
                   delivered_at = CURRENT_TIMESTAMP,
                   delivery_error = NULL
               WHERE id = ?""",
            (alert_id,),
        )


def _mark_failed(alert_id: int, error: str, previous_tries: int) -> None:
    """Incrementa counter + salva ultimo errore. Resta delivered=0."""
    encoded = f"try:{previous_tries + 1}|{error[:200]}"
    with transaction() as conn:
        conn.execute(
            """UPDATE alerts
               SET delivery_error = ?
               WHERE id = ?""",
            (encoded, alert_id),
        )


async def dispatch_pending(
    client: _TelegramClient,
    chat_ids: list[str | int],
    *,
    limit: int = 50,
) -> dict:
    """Invia tutti gli alert pending a tutti i chat_ids autorizzati.

    Ritorna dict con counters: ``{sent, failed, skipped}``.
    """
    if not chat_ids:
        _log.debug("dispatcher_skip_no_chats")
        return {"sent": 0, "failed": 0, "skipped": 0}

    pending = _fetch_pending(limit=limit)
    if not pending:
        return {"sent": 0, "failed": 0, "skipped": 0}

    sent = 0
    failed = 0
    for alert in pending:
        text = alert_to_markdown(alert)
        alert_id = alert["id"]
        retry_count = _parse_retry_count(alert.get("delivery_error"))

        # Send a tutti i chat_ids — uno fallimento non blocca gli altri
        any_success = False
        last_err: str | None = None
        for chat_id in chat_ids:
            try:
                await client.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="Markdown",
                )
                any_success = True
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "dispatcher_send_failed",
                    extra={"ctx": {
                        "alert_id": alert_id,
                        "chat_id": str(chat_id),
                        "error": last_err,
                    }},
                )

        if any_success:
            _mark_delivered(alert_id)
            sent += 1
            _log.info(
                "dispatcher_delivered",
                extra={"ctx": {"alert_id": alert_id, "type": alert.get("type")}},
            )
        else:
            _mark_failed(alert_id, last_err or "unknown", retry_count)
            failed += 1

    return {"sent": sent, "failed": failed, "skipped": 0}


def reset_delivery_failures(alert_id: int | None = None) -> int:
    """Reset del counter retry per recuperare alert falliti.

    Utile per recuperare alert che hanno superato MAX_RETRIES (es. dopo
    aver corretto il chat_id). Ritorna numero di righe resettate.

    ``alert_id=None`` → reset su tutti gli alert falliti.
    """
    with transaction() as conn:
        if alert_id is not None:
            cur = conn.execute(
                """UPDATE alerts
                   SET delivery_error = NULL
                   WHERE id = ? AND delivered = 0""",
                (alert_id,),
            )
        else:
            cur = conn.execute(
                """UPDATE alerts
                   SET delivery_error = NULL
                   WHERE delivered = 0 AND delivery_error IS NOT NULL"""
            )
        return cur.rowcount


def delivery_stats() -> dict:
    """Counters: pending, delivered_today, failed_today."""
    conn = connect()
    try:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE delivered = 0"
        ).fetchone()["n"]
        delivered_today = conn.execute(
            """SELECT COUNT(*) AS n FROM alerts
               WHERE delivered = 1 AND delivered_at >= date('now')"""
        ).fetchone()["n"]
        failed_pending = conn.execute(
            """SELECT COUNT(*) AS n FROM alerts
               WHERE delivered = 0 AND delivery_error IS NOT NULL"""
        ).fetchone()["n"]
    finally:
        conn.close()
    return {
        "pending": pending,
        "delivered_today": delivered_today,
        "failed_pending": failed_pending,
    }


def mark_all_delivered() -> int:
    """Flag tutti pending come delivered senza inviare.

    Utile al primo setup del bot: il DB ha già alerts storici dalla
    Phase 3 che non vogliamo spammare al momento dell'attivazione.
    Il trader riceverà solo alert *nuovi* dopo questo reset.
    """
    with transaction() as conn:
        cur = conn.execute(
            """UPDATE alerts
               SET delivered = 1, delivered_at = CURRENT_TIMESTAMP
               WHERE delivered = 0"""
        )
        print(
            f"[dispatcher] {cur.rowcount} alerts pending sono stati marcati come "
            "delivered (senza essere inviati). I nuovi alert partiranno dal bot.",
            file=sys.stderr,
        )
        return cur.rowcount
