"""CLI ``propicks-calendar`` — earnings + macro events (Phase 8).

Subcommands:
    propicks-calendar earnings [--upcoming 7d] [--refresh]
    propicks-calendar macro [--upcoming 14d] [--types FOMC,CPI]
    propicks-calendar check TICKER
"""

from __future__ import annotations

import argparse
import re
import sys

from tabulate import tabulate

from propicks.config import EARNINGS_HARD_GATE_DAYS
from propicks.domain.calendar import (
    earnings_gate_check,
    upcoming_macro_events,
)
from propicks.io.db import market_earnings_all_from_cache
from propicks.io.portfolio_store import load_portfolio
from propicks.io.watchlist_store import load_watchlist


def _parse_period(s: str) -> int:
    """Converte '7d', '30d', '2w' in giorni. Default 7 su parse fail."""
    m = re.match(r"^(\d+)([dw]?)$", s.strip().lower())
    if not m:
        return 7
    n = int(m.group(1))
    unit = m.group(2) or "d"
    return n * (7 if unit == "w" else 1)


# ---------------------------------------------------------------------------
# earnings
# ---------------------------------------------------------------------------
def cmd_earnings(args: argparse.Namespace) -> int:
    """Lista earnings date per portfolio + watchlist tickers.

    Se ``--refresh``: forza il fetch da yfinance (ignora cache TTL).
    """
    from propicks.market.yfinance_client import get_next_earnings_date

    days_ahead = _parse_period(args.upcoming or "14d")

    portfolio = load_portfolio()
    watchlist = load_watchlist()
    tickers = sorted(set(
        list(portfolio.get("positions", {}).keys())
        + list(watchlist.get("tickers", {}).keys())
    ))

    if not tickers:
        print("Nessun ticker in portfolio o watchlist.")
        return 0

    # Refresh opzionale
    if args.refresh:
        print(f"[refresh] fetching earnings per {len(tickers)} ticker…", file=sys.stderr)
        for t in tickers:
            try:
                get_next_earnings_date(t, force_refresh=True)
            except Exception as exc:
                print(f"  [errore] {t}: {exc}", file=sys.stderr)

    # Carica dalla cache (post-refresh se applicato)
    meta_earnings = market_earnings_all_from_cache()

    # Build rows: ticker, earnings_date, days_to, status, in portfolio, in watchlist
    # NB: days_ahead è la finestra di display; days_threshold è la soglia
    # del hard gate (5gg default). I ticker fuori dal gate ma dentro la
    # finestra display sono "info" non "blocked".
    rows = []
    for t in tickers:
        ed = meta_earnings.get(t)
        if ed is None:
            continue  # skip se no earnings in cache
        check = earnings_gate_check(t, ed, days_threshold=EARNINGS_HARD_GATE_DAYS)
        if check["days_to_earnings"] is None or check["days_to_earnings"] < 0:
            continue
        if check["days_to_earnings"] > days_ahead:
            continue

        in_port = "✓" if t in portfolio.get("positions", {}) else ""
        in_wl = "✓" if t in watchlist.get("tickers", {}) else ""
        blocked = "🚨 BLOCKED" if check["blocked"] else "ℹ️  info"

        rows.append([
            t,
            ed,
            check["days_to_earnings"],
            blocked,
            in_port,
            in_wl,
        ])

    rows.sort(key=lambda r: r[2])  # sort by days_to_earnings asc

    if not rows:
        print(f"Nessun earnings upcoming nei prossimi {days_ahead}gg tra portfolio/watchlist.")
        print()
        print("_Use --refresh per forzare il re-fetch da yfinance (cache TTL 7gg)._")
        return 0

    print(
        tabulate(
            rows,
            headers=["Ticker", "Earnings Date", "Days", "Status", "Pf", "Wl"],
            tablefmt="github",
        )
    )
    n_blocked = sum(1 for r in rows if "BLOCKED" in r[3])
    print(f"\n{len(rows)} ticker upcoming, {n_blocked} bloccati da hard gate ({EARNINGS_HARD_GATE_DAYS}gg).")
    return 0


# ---------------------------------------------------------------------------
# macro
# ---------------------------------------------------------------------------
def cmd_macro(args: argparse.Namespace) -> int:
    days_ahead = _parse_period(args.upcoming or "14d")
    types = None
    if args.types:
        types = tuple(t.strip().upper() for t in args.types.split(","))

    events = upcoming_macro_events(days_ahead=days_ahead, event_types=types)
    if not events:
        print(f"Nessun macro event nei prossimi {days_ahead}gg.")
        return 0

    rows = []
    for ev in events:
        rows.append([
            ev["date"],
            ev["days_from_now"],
            ev["type"],
            ev["description"],
        ])
    print(
        tabulate(
            rows,
            headers=["Date", "Days", "Type", "Description"],
            tablefmt="github",
        )
    )
    print(f"\n{len(events)} eventi nei prossimi {days_ahead}gg.")
    return 0


# ---------------------------------------------------------------------------
# check TICKER
# ---------------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    from propicks.market.yfinance_client import get_next_earnings_date

    ticker = args.ticker.upper()
    print(f"[{ticker}] fetching earnings date…", file=sys.stderr)
    ed = get_next_earnings_date(ticker, force_refresh=args.refresh)
    check = earnings_gate_check(ticker, ed, days_threshold=EARNINGS_HARD_GATE_DAYS)

    rows = [
        ["Ticker", ticker],
        ["Next earnings", ed or "—"],
        ["Days to earnings", check["days_to_earnings"] if check["days_to_earnings"] is not None else "—"],
        ["Hard gate threshold", f"{EARNINGS_HARD_GATE_DAYS} days"],
        ["Blocked?", "🚨 YES" if check["blocked"] else "✅ NO"],
        ["Reason", check["reason"]],
    ]
    print(tabulate(rows, tablefmt="simple"))

    # Macro events proximity (solo info)
    from propicks.domain.calendar import macro_warning_check
    macro = macro_warning_check()
    if macro["has_warning"]:
        print()
        print("⚠️  Macro event proximity:")
        for ev in macro["events"]:
            print(f"  • {ev['date']} ({ev['days_from_now']}gg) — {ev['type']}: {ev['description']}")

    return 0 if not check["blocked"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Earnings + macro calendar (Phase 8).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_earn = sub.add_parser("earnings", help="Earnings upcoming portfolio/watchlist")
    p_earn.add_argument(
        "--upcoming",
        default="14d",
        help="Finestra forward (es. 7d, 14d, 2w). Default 14d.",
    )
    p_earn.add_argument(
        "--refresh",
        action="store_true",
        help="Forza fetch yfinance (bypass cache TTL 7gg)",
    )
    p_earn.set_defaults(func=cmd_earnings)

    p_macro = sub.add_parser("macro", help="Macro events (FOMC/CPI/NFP/ECB) upcoming")
    p_macro.add_argument("--upcoming", default="14d", help="Finestra forward (es. 14d, 1w)")
    p_macro.add_argument(
        "--types",
        help="Filtra per tipo CSV (es. 'FOMC,CPI'). Default: tutti.",
    )
    p_macro.set_defaults(func=cmd_macro)

    p_check = sub.add_parser("check", help="Check earnings gate per singolo ticker")
    p_check.add_argument("ticker")
    p_check.add_argument("--refresh", action="store_true", help="Forza fetch")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
