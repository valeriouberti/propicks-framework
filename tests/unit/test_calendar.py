"""Test domain/calendar.py (Phase 8) — earnings + macro events."""

from __future__ import annotations

from datetime import date

from propicks.domain.calendar import (
    blocked_tickers_from_earnings,
    days_to_earnings,
    earnings_gate_check,
    is_pre_earnings,
    macro_warning_check,
    upcoming_macro_events,
)


# ---------------------------------------------------------------------------
# days_to_earnings / is_pre_earnings
# ---------------------------------------------------------------------------
def test_days_to_earnings_future():
    today = date(2026, 4, 24)
    assert days_to_earnings("2026-04-29", as_of=today) == 5


def test_days_to_earnings_past():
    today = date(2026, 4, 24)
    assert days_to_earnings("2026-04-20", as_of=today) == -4


def test_days_to_earnings_today():
    today = date(2026, 4, 24)
    assert days_to_earnings("2026-04-24", as_of=today) == 0


def test_days_to_earnings_none():
    assert days_to_earnings(None) is None
    assert days_to_earnings("invalid-date") is None


def test_is_pre_earnings_within_window():
    today = date(2026, 4, 24)
    assert is_pre_earnings("2026-04-27", days_threshold=5, as_of=today) is True
    assert is_pre_earnings("2026-04-29", days_threshold=5, as_of=today) is True  # exact boundary
    assert is_pre_earnings("2026-04-30", days_threshold=5, as_of=today) is False


def test_is_pre_earnings_past_ignored():
    """Earnings passati non triggerano il gate."""
    today = date(2026, 4, 24)
    assert is_pre_earnings("2026-04-20", days_threshold=5, as_of=today) is False


def test_is_pre_earnings_none_date():
    assert is_pre_earnings(None) is False


# ---------------------------------------------------------------------------
# earnings_gate_check
# ---------------------------------------------------------------------------
def test_gate_check_blocked_within_window():
    today = date(2026, 4, 24)
    check = earnings_gate_check("AAPL", "2026-04-27", days_threshold=5, as_of=today)
    assert check["blocked"] is True
    assert check["days_to_earnings"] == 3
    assert "hard block" in check["reason"]


def test_gate_check_not_blocked_beyond_window():
    today = date(2026, 4, 24)
    check = earnings_gate_check("AAPL", "2026-05-15", days_threshold=5, as_of=today)
    assert check["blocked"] is False


def test_gate_check_today_emphatic():
    """Earnings oggi → blocked + messaggio emphatic."""
    today = date(2026, 4, 24)
    check = earnings_gate_check("AAPL", "2026-04-24", days_threshold=5, as_of=today)
    assert check["blocked"] is True
    assert "OGGI" in check["reason"]


def test_gate_check_no_earnings_date_not_blocked():
    check = earnings_gate_check("AAPL", None)
    assert check["blocked"] is False
    assert "non disponibile" in check["reason"]


# ---------------------------------------------------------------------------
# upcoming_macro_events
# ---------------------------------------------------------------------------
def test_macro_events_filters_by_window():
    """Eventi oltre days_ahead vengono esclusi."""
    from_date = date(2026, 4, 20)
    # Finestra 14gg: dal 2026-04-20 al 2026-05-04
    events = upcoming_macro_events(from_date=from_date, days_ahead=14)
    assert len(events) > 0
    assert all(ev["days_from_now"] <= 14 for ev in events)
    # Dovremmo avere FOMC 2026-04-29 (9gg) + NFP 2026-05-01 (11gg) dentro
    types_seen = {ev["type"] for ev in events}
    assert "FOMC" in types_seen
    assert "NFP" in types_seen


def test_macro_events_filter_by_type():
    from_date = date(2026, 4, 20)
    events = upcoming_macro_events(
        from_date=from_date,
        days_ahead=30,
        event_types=("FOMC",),
    )
    assert all(ev["type"] == "FOMC" for ev in events)


def test_macro_events_empty_window():
    """Finestra 0gg → nessun evento."""
    from_date = date(2026, 4, 1)
    events = upcoming_macro_events(from_date=from_date, days_ahead=0)
    # Solo eventi ESATTAMENTE il 2026-04-01
    # (NFP 2026-04-03, FOMC 2026-04-29 — nessuno il 2026-04-01)
    assert events == []


# ---------------------------------------------------------------------------
# macro_warning_check
# ---------------------------------------------------------------------------
def test_macro_warning_fires_near_fomc():
    # 2026-04-29 è un FOMC day. Entry il 2026-04-27 (2gg prima) → warning.
    check = macro_warning_check(
        entry_date="2026-04-27",
        warning_days=2,
    )
    assert check["has_warning"] is True
    assert any(ev["type"] == "FOMC" for ev in check["events"])


def test_macro_warning_not_triggered_when_far():
    check = macro_warning_check(
        entry_date="2026-04-10",  # far from any event
        warning_days=1,
    )
    assert check["has_warning"] is False


# ---------------------------------------------------------------------------
# blocked_tickers_from_earnings
# ---------------------------------------------------------------------------
def test_blocked_tickers_filters_and_sorts():
    today = date(2026, 4, 24)
    ticker_map = {
        "AAPL": "2026-04-27",  # 3d → blocked
        "MSFT": "2026-05-15",  # 21d → not blocked
        "NVDA": "2026-04-29",  # 5d → blocked (boundary)
        "TSLA": None,           # no date → not blocked
        "AMZN": "2026-04-20",  # passed → not blocked
    }
    blocked = blocked_tickers_from_earnings(ticker_map, days_threshold=5, as_of=today)
    assert len(blocked) == 2
    # Sorted by days_to_earnings ascending: AAPL (3) prima di NVDA (5)
    assert blocked[0]["ticker"] == "AAPL"
    assert blocked[1]["ticker"] == "NVDA"


def test_blocked_tickers_empty_when_all_past_or_far():
    today = date(2026, 4, 24)
    ticker_map = {
        "A": "2026-04-15",  # passed
        "B": "2026-07-01",  # far future
    }
    blocked = blocked_tickers_from_earnings(ticker_map, days_threshold=5, as_of=today)
    assert blocked == []
