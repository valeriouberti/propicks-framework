"""Test command handlers puri (no Telegram mock needed).

Ogni handler prende ``list[str] args`` e ritorna ``{text, parse_mode}``.
"""

from __future__ import annotations

from propicks.notifications.bot_commands import (
    handle_ack,
    handle_ackall,
    handle_alerts,
    handle_cache,
    handle_help,
    handle_history,
    handle_portfolio,
    handle_regime,
    handle_start,
    handle_status,
    is_authorized,
)
from propicks.scheduler.alerts import create_alert


def test_handle_start_and_help():
    s = handle_start([])
    assert "Propicks" in s["text"]
    h = handle_help([])
    assert "/status" in h["text"]
    assert "/alerts" in h["text"]


def test_handle_status_empty_portfolio(monkeypatch):
    # Evita chiamate rete — mock get_current_prices per unrealized_pl
    monkeypatch.setattr(
        "propicks.market.yfinance_client.get_current_prices",
        lambda tickers: {},
    )
    resp = handle_status([])
    assert "PORTFOLIO" in resp["text"]
    assert "0" in resp["text"]  # 0 posizioni


def test_handle_portfolio_empty():
    resp = handle_portfolio([])
    assert "Nessuna posizione" in resp["text"]


def test_handle_alerts_empty():
    resp = handle_alerts([])
    assert "Nessun alert" in resp["text"]


def test_handle_alerts_with_pending():
    create_alert(
        "watchlist_ready", "AAPL ready",
        ticker="AAPL", severity="info",
        metadata={"price": 100.0, "target": 100.5, "score": 75, "distance_pct": 0.005},
    )
    resp = handle_alerts([])
    assert "1 ALERT" in resp["text"]
    assert "AAPL" in resp["text"]


def test_handle_ack_missing_arg():
    resp = handle_ack([])
    assert "ID" in resp["text"]


def test_handle_ack_invalid_id():
    resp = handle_ack(["notanumber"])
    assert "non valido" in resp["text"]


def test_handle_ack_nonexistent():
    resp = handle_ack(["99999"])
    assert "non trovato" in resp["text"]


def test_handle_ack_success():
    create_alert("watchlist_ready", "msg", ticker="AAPL")
    # Get the ID
    from propicks.scheduler.alerts import list_pending_alerts
    alert_id = list_pending_alerts()[0]["id"]
    resp = handle_ack([str(alert_id)])
    assert "acknowledged" in resp["text"]


def test_handle_ackall():
    for i in range(3):
        create_alert("info", f"msg{i}")
    resp = handle_ackall([])
    assert "3" in resp["text"]


def test_handle_history_empty():
    resp = handle_history([])
    assert "Nessun run" in resp["text"]


def test_handle_cache_empty():
    resp = handle_cache([])
    assert "CACHE" in resp["text"]


def test_handle_regime_empty():
    resp = handle_regime([])
    assert "non ancora registrato" in resp["text"]


def test_handle_regime_populated():
    """Se regime_history ha una riga, handler la restituisce."""
    from propicks.io.db import transaction
    with transaction() as conn:
        conn.execute(
            """INSERT INTO regime_history (date, regime_code, regime_label, adx, rsi)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-04-24", 4, "BULL", 20.5, 55.0),
        )

    resp = handle_regime([])
    assert "BULL" in resp["text"]
    assert "4/5" in resp["text"]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
def test_is_authorized_whitelist(monkeypatch):
    monkeypatch.setenv("PROPICKS_TELEGRAM_CHAT_ID", "123,456")
    assert is_authorized(123) is True
    assert is_authorized("123") is True
    assert is_authorized(456) is True
    assert is_authorized(999) is False


def test_is_authorized_empty_env_blocks(monkeypatch):
    """Env vuoto → nessun chat autorizzato (sicuro-by-default)."""
    monkeypatch.setenv("PROPICKS_TELEGRAM_CHAT_ID", "")
    assert is_authorized(123) is False
