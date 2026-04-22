"""Helper condivisi tra report settimanali e mensili."""

from __future__ import annotations

from datetime import datetime

from propicks.config import DATE_FMT


def fmt_pct(x: float | None) -> str:
    return f"{x:+.2f}%" if x is not None else "N/A"


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, DATE_FMT)
    except ValueError:
        return None


def trades_closed_between(
    trades: list[dict], start: datetime, end: datetime
) -> list[dict]:
    out = []
    for t in trades:
        if t.get("status") != "closed":
            continue
        d = parse_date(t.get("exit_date"))
        if d and start <= d <= end:
            out.append(t)
    return out


def trades_opened_between(
    trades: list[dict], start: datetime, end: datetime
) -> list[dict]:
    out = []
    for t in trades:
        d = parse_date(t.get("entry_date"))
        if d and start <= d <= end:
            out.append(t)
    return out
