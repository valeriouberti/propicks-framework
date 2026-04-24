"""Catalyst calendar (Phase 8) — earnings + macro events.

**Earnings** (ticker-specific): da yfinance, cached in ``market_ticker_meta.
next_earnings_date`` con TTL 7gg. Hard gate pre-entry se earnings entro
``EARNINGS_HARD_GATE_DAYS`` (default 5).

**Macro events** (whole-market): hardcoded in ``config.MACRO_EVENTS_2026``
per FOMC, CPI, NFP, ECB. Soft warning se entry entro ``MACRO_WARNING_DAYS``
(default 2) — non blocca perché coinvolge tutto il mercato.

Modulo puro: accetta datestring / dict / list, ritorna dict. Zero I/O
interno — chiama il market layer per il fetch, chiama il config per i
macro events. I test iniettano fixture dirette.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from propicks.config import (
    DATE_FMT,
    EARNINGS_HARD_GATE_DAYS,
    MACRO_EVENTS_2026,
    MACRO_WARNING_DAYS,
)


# ---------------------------------------------------------------------------
# Earnings gate
# ---------------------------------------------------------------------------
def days_to_earnings(earnings_date: str | None, as_of: date | None = None) -> int | None:
    """Giorni di calendario da ``as_of`` a ``earnings_date``.

    Ritorna:
    - None se earnings_date è None o non parsabile
    - Negativo se earnings è passato
    - 0 se è oggi
    - Positivo se è nel futuro
    """
    if not earnings_date:
        return None
    try:
        ed = datetime.strptime(earnings_date, DATE_FMT).date()
    except (ValueError, TypeError):
        return None
    as_of = as_of or date.today()
    return (ed - as_of).days


def is_pre_earnings(
    earnings_date: str | None,
    days_threshold: int = EARNINGS_HARD_GATE_DAYS,
    as_of: date | None = None,
) -> bool:
    """True se ``earnings_date`` è entro ``days_threshold`` giorni (incluso oggi).

    Earnings già passate (giorni negativi) → False.
    """
    dte = days_to_earnings(earnings_date, as_of=as_of)
    if dte is None:
        return False
    return 0 <= dte <= days_threshold


def earnings_gate_check(
    ticker: str,
    earnings_date: str | None,
    days_threshold: int = EARNINGS_HARD_GATE_DAYS,
    as_of: date | None = None,
) -> dict:
    """Ritorna dict con l'esito del check earnings gate.

    {
      'blocked': bool,         # True se trade va bloccato
      'days_to_earnings': int | None,
      'earnings_date': str | None,
      'reason': str            # spiegazione umana
    }
    """
    dte = days_to_earnings(earnings_date, as_of=as_of)
    blocked = is_pre_earnings(earnings_date, days_threshold, as_of)
    if dte is None:
        reason = "earnings date non disponibile"
    elif dte < 0:
        reason = f"earnings passate ({abs(dte)}gg fa)"
    elif dte == 0:
        reason = "EARNINGS OGGI (hard block)"
    elif blocked:
        reason = f"earnings in {dte}gg (hard block: soglia {days_threshold})"
    else:
        reason = f"earnings in {dte}gg (oltre soglia {days_threshold} — ok)"
    return {
        "ticker": ticker.upper(),
        "blocked": blocked,
        "days_to_earnings": dte,
        "earnings_date": earnings_date,
        "threshold_days": days_threshold,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Macro events
# ---------------------------------------------------------------------------
def upcoming_macro_events(
    from_date: date | None = None,
    days_ahead: int = 14,
    event_types: tuple[str, ...] | None = None,
) -> list[dict]:
    """Ritorna eventi macro nei prossimi ``days_ahead`` giorni.

    Args:
        from_date: start date (default oggi)
        days_ahead: finestra forward
        event_types: filtra per tipo (es. ('FOMC', 'CPI')). None = tutti.

    Returns: list di dict ordinata per data asc, con:
        {'date': 'YYYY-MM-DD', 'days_from_now': int, 'type': str, 'description': str}
    """
    from_date = from_date or date.today()
    end_date = from_date + timedelta(days=days_ahead)

    out: list[dict] = []
    for ev_date_str, events in MACRO_EVENTS_2026.items():
        try:
            ev_date = datetime.strptime(ev_date_str, DATE_FMT).date()
        except ValueError:
            continue
        if ev_date < from_date or ev_date > end_date:
            continue
        for ev_type, desc in events:
            if event_types and ev_type not in event_types:
                continue
            out.append({
                "date": ev_date_str,
                "days_from_now": (ev_date - from_date).days,
                "type": ev_type,
                "description": desc,
            })

    out.sort(key=lambda x: (x["date"], x["type"]))
    return out


def macro_warning_check(
    entry_date: str | None = None,
    warning_days: int = MACRO_WARNING_DAYS,
    event_types: tuple[str, ...] = ("FOMC", "CPI", "NFP", "ECB"),
) -> dict:
    """Check se una data di entry è vicina a un macro event.

    Ritorna dict:
    {
      'has_warning': bool,
      'events': list[dict],  # eventi entro warning_days
      'reason': str
    }
    """
    if entry_date:
        try:
            from_date = datetime.strptime(entry_date, DATE_FMT).date()
        except ValueError:
            from_date = date.today()
    else:
        from_date = date.today()

    events = upcoming_macro_events(
        from_date=from_date,
        days_ahead=warning_days,
        event_types=event_types,
    )

    if not events:
        return {
            "has_warning": False,
            "events": [],
            "reason": f"nessun macro event nei prossimi {warning_days}gg",
        }

    labels = [f"{e['type']} in {e['days_from_now']}gg" for e in events[:3]]
    return {
        "has_warning": True,
        "events": events,
        "reason": f"Macro event imminente: {', '.join(labels)}",
    }


# ---------------------------------------------------------------------------
# Helpers aggregati
# ---------------------------------------------------------------------------
def blocked_tickers_from_earnings(
    ticker_earnings_map: dict[str, str | None],
    days_threshold: int = EARNINGS_HARD_GATE_DAYS,
    as_of: date | None = None,
) -> list[dict]:
    """Ritorna list di ticker bloccati + dettaglio per report/alerting.

    Args:
        ticker_earnings_map: dict ``{ticker: earnings_date_iso | None}``
            tipicamente caricato dal ``market_ticker_meta``.
    """
    out = []
    for ticker, earnings_date in ticker_earnings_map.items():
        check = earnings_gate_check(ticker, earnings_date, days_threshold, as_of)
        if check["blocked"]:
            out.append(check)
    out.sort(key=lambda x: x.get("days_to_earnings") or 999)
    return out
