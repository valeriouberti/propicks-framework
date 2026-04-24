"""Test dispatcher con mock Telegram client.

Verifica:
- Nessun chat → no-op silenzioso
- Alert pending → send + mark delivered
- Send fallito → mark_failed con counter (retry < MAX_RETRIES)
- Dopo 3 tentativi → skip
- mark_all_delivered marca pending senza inviare
"""

from __future__ import annotations

import pytest

from propicks.io.db import connect
from propicks.notifications.dispatcher import (
    MAX_RETRIES,
    delivery_stats,
    dispatch_pending,
    mark_all_delivered,
    reset_delivery_failures,
)
from propicks.scheduler.alerts import create_alert


class _MockTelegramClient:
    """Mock di _TelegramClient protocol. Registra ogni send_message."""

    def __init__(self, fail: bool = False, fail_on_chat: str | None = None):
        self.sent: list[dict] = []
        self.fail = fail
        self.fail_on_chat = fail_on_chat

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append({"chat_id": str(chat_id), "text": text, "parse_mode": parse_mode})
        if self.fail:
            raise RuntimeError("mock send failure")
        if self.fail_on_chat and str(chat_id) == self.fail_on_chat:
            raise RuntimeError("mock fail for specific chat")
        return {"message_id": 1}


@pytest.mark.asyncio
async def test_no_chats_is_noop():
    create_alert("watchlist_ready", "AAPL", ticker="AAPL")
    client = _MockTelegramClient()
    result = await dispatch_pending(client, chat_ids=[])
    assert result == {"sent": 0, "failed": 0, "skipped": 0}
    assert len(client.sent) == 0


@pytest.mark.asyncio
async def test_sends_pending_and_marks_delivered():
    create_alert("watchlist_ready", "AAPL ready", ticker="AAPL",
                 metadata={"price": 100, "target": 100.5, "score": 75})
    client = _MockTelegramClient()
    result = await dispatch_pending(client, chat_ids=["12345"])
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert len(client.sent) == 1
    # Alert marcato come delivered
    conn = connect()
    try:
        row = conn.execute(
            "SELECT delivered, delivered_at FROM alerts LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["delivered"] == 1
    assert row["delivered_at"] is not None


@pytest.mark.asyncio
async def test_send_to_multiple_chats():
    create_alert("watchlist_ready", "AAPL", ticker="AAPL", metadata={"price": 100, "target": 100.5})
    client = _MockTelegramClient()
    await dispatch_pending(client, chat_ids=["111", "222", "333"])
    assert len(client.sent) == 3
    # Solo un "delivered=1" però, non uno per chat
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM alerts WHERE delivered=1").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_send_failure_marks_failed_not_delivered():
    create_alert("watchlist_ready", "AAPL", ticker="AAPL", metadata={"price": 100, "target": 100.5})
    client = _MockTelegramClient(fail=True)
    result = await dispatch_pending(client, chat_ids=["111"])
    assert result["sent"] == 0
    assert result["failed"] == 1

    conn = connect()
    try:
        row = conn.execute(
            "SELECT delivered, delivery_error FROM alerts LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["delivered"] == 0
    assert row["delivery_error"] is not None
    assert "try:1" in row["delivery_error"]


@pytest.mark.asyncio
async def test_retry_counter_increments_and_skips_after_max():
    """Dopo MAX_RETRIES fallimenti consecutivi, l'alert viene skippato."""
    create_alert("watchlist_ready", "AAPL", ticker="AAPL", metadata={"price": 100, "target": 100.5})
    client = _MockTelegramClient(fail=True)

    # 3 tentativi
    for _ in range(MAX_RETRIES):
        await dispatch_pending(client, chat_ids=["111"])

    # 4° tentativo → alert skippato (non compare nella select pending)
    client_fresh = _MockTelegramClient()
    result = await dispatch_pending(client_fresh, chat_ids=["111"])
    assert result["sent"] == 0
    assert len(client_fresh.sent) == 0  # nemmeno tentato


@pytest.mark.asyncio
async def test_partial_success_on_mixed_chats():
    """Se 1 chat OK e 1 FAIL → alert comunque marcato delivered (any_success)."""
    create_alert("watchlist_ready", "AAPL", ticker="AAPL", metadata={"price": 100, "target": 100.5})
    client = _MockTelegramClient(fail_on_chat="BADCHAT")
    result = await dispatch_pending(client, chat_ids=["GOODCHAT", "BADCHAT"])
    assert result["sent"] == 1
    assert len(client.sent) == 2  # entrambi tentati

    conn = connect()
    try:
        row = conn.execute("SELECT delivered FROM alerts LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["delivered"] == 1


def test_reset_delivery_failures_single():
    create_alert("watchlist_ready", "AAPL", ticker="AAPL")
    # Simula failure via direct SQL
    from propicks.io.db import transaction
    with transaction() as conn:
        conn.execute("UPDATE alerts SET delivery_error = 'try:3|oops' WHERE id = 1")

    n = reset_delivery_failures(alert_id=1)
    assert n == 1

    conn = connect()
    try:
        row = conn.execute("SELECT delivery_error FROM alerts WHERE id=1").fetchone()
    finally:
        conn.close()
    assert row["delivery_error"] is None


def test_mark_all_delivered_mutes_backlog():
    for i in range(5):
        create_alert("watchlist_ready", f"t{i}", ticker=f"T{i}")
    n = mark_all_delivered()
    assert n == 5

    stats = delivery_stats()
    assert stats["pending"] == 0
    assert stats["delivered_today"] == 5


def test_delivery_stats_empty_db():
    stats = delivery_stats()
    assert stats == {"pending": 0, "delivered_today": 0, "failed_pending": 0}
