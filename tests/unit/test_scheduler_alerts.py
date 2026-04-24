"""Test del layer alerts (CRUD + dedup)."""

from __future__ import annotations

from propicks.scheduler.alerts import (
    acknowledge_alert,
    acknowledge_all,
    create_alert,
    list_pending_alerts,
    stats,
)


def test_create_alert_basic():
    assert create_alert(
        alert_type="watchlist_ready",
        message="AAPL ready",
        severity="info",
        ticker="AAPL",
    ) is True
    pending = list_pending_alerts()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "AAPL"
    assert pending[0]["type"] == "watchlist_ready"


def test_create_alert_with_metadata():
    create_alert(
        alert_type="trailing_stop_update",
        message="update suggested",
        ticker="MSFT",
        metadata={"current_stop": 380, "suggested_stop": 395},
    )
    pending = list_pending_alerts()
    assert pending[0]["metadata"]["suggested_stop"] == 395


def test_dedup_same_key_no_duplicate():
    """Dedup key presente + alert non-acked → secondo create no-op."""
    assert create_alert("watchlist_ready", "AAPL ready", dedup_key="AAPL_ready_2026-04-24") is True
    assert create_alert("watchlist_ready", "AAPL ready (dup)", dedup_key="AAPL_ready_2026-04-24") is False
    assert len(list_pending_alerts()) == 1


def test_dedup_different_keys_both_created():
    assert create_alert("watchlist_ready", "AAPL", dedup_key="AAPL_ready_2026-04-24") is True
    assert create_alert("watchlist_ready", "MSFT", dedup_key="MSFT_ready_2026-04-24") is True
    assert len(list_pending_alerts()) == 2


def test_dedup_after_ack_allows_new():
    """Alert con stesso dedup_key ma quello precedente acked → nuovo creato.
    (Permette di ri-triggerare alert "ready" settimana dopo una volta ackata.)
    """
    create_alert("watchlist_ready", "AAPL ready", dedup_key="AAPL_ready_2026-04-24")
    pending = list_pending_alerts()
    alert_id = pending[0]["id"]
    acknowledge_alert(alert_id)

    # Ora la dedup_key è "libera" (tutti gli alert con quella key sono acked)
    assert create_alert("watchlist_ready", "AAPL ready again", dedup_key="AAPL_ready_2026-04-24") is True


def test_no_dedup_key_always_creates():
    """Senza dedup_key, ogni create genera un nuovo alert."""
    create_alert("regime_change", "first")
    create_alert("regime_change", "second")
    assert len(list_pending_alerts()) == 2


def test_list_pending_excludes_acked():
    create_alert("info", "msg1")
    create_alert("info", "msg2")
    pending = list_pending_alerts()
    assert len(pending) == 2
    acknowledge_alert(pending[0]["id"])
    assert len(list_pending_alerts()) == 1


def test_acknowledge_all():
    for i in range(5):
        create_alert("info", f"msg{i}")
    assert len(list_pending_alerts()) == 5
    n = acknowledge_all()
    assert n == 5
    assert len(list_pending_alerts()) == 0


def test_ack_missing_returns_false():
    """Ack di ID inesistente o già acked → False."""
    create_alert("info", "msg1")
    pending = list_pending_alerts()
    acknowledge_alert(pending[0]["id"])
    # Secondo ack dello stesso: False
    assert acknowledge_alert(pending[0]["id"]) is False
    # Ack di ID mai esistito: False
    assert acknowledge_alert(99999) is False


def test_stats_groups_by_type_severity():
    create_alert("watchlist_ready", "r1", severity="info")
    create_alert("watchlist_ready", "r2", severity="info")
    create_alert("regime_change", "rc", severity="critical")
    s = stats()
    assert s["pending_total"] == 3
    # Due tipi distinti
    assert len(s["by_type"]) == 2
