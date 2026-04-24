"""Test formatter alert → markdown Telegram.

Formatter è puro — nessun I/O, facile da testare esaustivamente.
"""

from __future__ import annotations

from propicks.notifications.formatter import alert_to_markdown


def test_watchlist_ready_full_metadata():
    alert = {
        "id": 42,
        "type": "watchlist_ready",
        "severity": "info",
        "ticker": "AAPL",
        "message": "AAPL READY",
        "metadata": {
            "price": 185.10,
            "target": 185.50,
            "distance_pct": 0.0022,
            "score": 78.3,
            "classification": "A — AZIONE IMMEDIATA",
        },
    }
    out = alert_to_markdown(alert)
    assert "🟢" in out
    assert "AAPL" in out
    assert "185.10" in out
    assert "185.50" in out
    assert "/ack 42" in out  # footer con ID


def test_watchlist_ready_missing_metadata():
    """Alert senza metadata → no crash, genera comunque output."""
    alert = {
        "id": 1,
        "type": "watchlist_ready",
        "severity": "info",
        "ticker": "AAPL",
        "message": "AAPL ready",
        "metadata": None,
    }
    out = alert_to_markdown(alert)
    assert "AAPL" in out


def test_regime_change_with_direction():
    alert = {
        "id": 10,
        "type": "regime_change",
        "severity": "critical",
        "message": "change",
        "metadata": {
            "from": "NEUTRAL", "from_code": 3,
            "to": "BULL", "to_code": 4,
            "date": "2026-04-24",
        },
    }
    out = alert_to_markdown(alert)
    assert "REGIME CHANGE" in out
    assert "NEUTRAL" in out
    assert "BULL" in out
    assert "↗️" in out  # direzione up


def test_regime_change_downward():
    alert = {
        "type": "regime_change",
        "severity": "critical",
        "message": "down",
        "metadata": {"from": "BULL", "from_code": 4, "to": "BEAR", "to_code": 2},
    }
    out = alert_to_markdown(alert)
    assert "↘️" in out


def test_trailing_stop_update():
    alert = {
        "id": 5,
        "type": "trailing_stop_update",
        "severity": "info",
        "ticker": "MSFT",
        "message": "trail",
        "metadata": {
            "current_stop": 380.0,
            "suggested_stop": 400.0,
            "highest_price": 415.0,
            "rationale": ["Trailing: stop 380.00 -> 400.00"],
        },
    }
    out = alert_to_markdown(alert)
    assert "TRAIL" in out
    assert "MSFT" in out
    assert "380.00" in out
    assert "400.00" in out


def test_stale_position():
    alert = {
        "type": "stale_position",
        "severity": "warning",
        "ticker": "LMT",
        "message": "flat",
        "metadata": {"price": 450.0, "entry_price": 448.0, "entry_date": "2026-01-15"},
    }
    out = alert_to_markdown(alert)
    assert "TIME-STOP" in out
    assert "LMT" in out


def test_stale_watchlist_many_tickers():
    alert = {
        "type": "stale_watchlist",
        "severity": "info",
        "message": "stale",
        "metadata": {
            "tickers": [f"TICK{i}" for i in range(15)],
            "days_threshold": 60,
        },
    }
    out = alert_to_markdown(alert)
    assert "STALE WATCHLIST" in out
    assert "15 entries" in out
    assert "altri 5" in out  # preview troncato a 10


def test_contra_near_cap():
    alert = {
        "type": "contra_near_cap",
        "severity": "warning",
        "message": "near cap",
        "metadata": {"exposure": 0.162, "cap": 0.20},
    }
    out = alert_to_markdown(alert)
    assert "CONTRARIAN" in out
    assert "16.2%" in out


def test_unknown_type_falls_back_to_generic():
    """Tipo sconosciuto → non crasha, usa _fmt_generic."""
    alert = {
        "type": "my_custom_type",
        "severity": "info",
        "ticker": "AAPL",
        "message": "custom message body",
    }
    out = alert_to_markdown(alert)
    assert "AAPL" in out
    assert "custom message body" in out


def test_no_id_no_ack_footer():
    """Alert senza ID non ha il footer /ack."""
    alert = {
        "type": "stale_watchlist",
        "severity": "info",
        "message": "stale",
        "metadata": {"tickers": ["AAPL"], "days_threshold": 60},
    }
    out = alert_to_markdown(alert)
    assert "/ack" not in out
