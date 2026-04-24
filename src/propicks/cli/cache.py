"""CLI per la cache OHLCV (Phase 2).

Tre sub-comandi:

    propicks-cache stats
        Statistiche aggregate della cache (righe, ticker unici, date range).

    propicks-cache warm AAPL MSFT NVDA
        Prefetch storia completa (daily + weekly) per i ticker dati.
        Utile prima di un batch scan per garantire cache hit.

    propicks-cache clear [--ticker TICKER] [--all] [--stale] [--interval daily|weekly]
        Invalida cache:
        - senza flag: richiede --ticker o --all
        - --stale: rimuove solo righe fuori TTL
        - --interval: solo daily o solo weekly (default entrambe)
"""

from __future__ import annotations

import argparse
import sys

from tabulate import tabulate

from propicks.config import (
    MARKET_CACHE_TTL_DAILY_HOURS,
    MARKET_CACHE_TTL_WEEKLY_HOURS,
)


def cmd_stats(_args: argparse.Namespace) -> int:
    from propicks.io.db import market_ohlcv_stats

    stats = market_ohlcv_stats()
    rows = [
        [
            interval,
            s.get("total_rows") or 0,
            s.get("n_tickers") or 0,
            s.get("date_min") or "-",
            s.get("date_max") or "-",
            s.get("last_fetch") or "-",
        ]
        for interval, s in stats.items()
    ]
    print(
        tabulate(
            rows,
            headers=["Interval", "Rows", "Tickers", "Date min", "Date max", "Last fetch"],
            tablefmt="github",
        )
    )
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    """Prefetch daily + weekly per tutti i ticker dati.

    Forza il refresh anche se la cache è fresh — utile per garantire
    consistenza prima di un backtest o attribution report.
    """
    from propicks.io.db import market_ohlcv_clear
    from propicks.market.yfinance_client import (
        DataUnavailable,
        download_history,
        download_weekly_history,
    )

    tickers = [t.upper() for t in args.tickers]
    if args.force:
        # Invalida prima di fetchare → forza il refresh
        for t in tickers:
            market_ohlcv_clear(ticker=t)

    results: list[list] = []
    for t in tickers:
        daily_ok = False
        weekly_ok = False
        err = ""
        try:
            hist = download_history(t)
            daily_ok = not hist.empty
        except DataUnavailable as exc:
            err = str(exc)
        try:
            hist_w = download_weekly_history(t)
            weekly_ok = not hist_w.empty
        except DataUnavailable as exc:
            err = err or str(exc)
        results.append([
            t,
            "✓" if daily_ok else "✗",
            "✓" if weekly_ok else "✗",
            err[:60] if err else "-",
        ])

    print(
        tabulate(
            results,
            headers=["Ticker", "Daily", "Weekly", "Error"],
            tablefmt="github",
        )
    )
    failed = sum(1 for r in results if r[1] == "✗" or r[2] == "✗")
    return 1 if failed > 0 else 0


def cmd_clear(args: argparse.Namespace) -> int:
    from propicks.io.db import market_ohlcv_clear

    if not args.all and not args.ticker and not args.stale:
        print(
            "[errore] specificare --all, --ticker TICKER, o --stale",
            file=sys.stderr,
        )
        return 2

    if args.stale:
        # TTL differente per daily e weekly. Passiamo per tabella.
        n_daily = market_ohlcv_clear(
            ticker=args.ticker,
            interval="daily",
            stale_ttl_hours=MARKET_CACHE_TTL_DAILY_HOURS,
        )
        n_weekly = market_ohlcv_clear(
            ticker=args.ticker,
            interval="weekly",
            stale_ttl_hours=MARKET_CACHE_TTL_WEEKLY_HOURS,
        )
        scope = f"ticker={args.ticker}" if args.ticker else "all tickers"
        print(
            f"Cancellate {n_daily} righe stale daily + {n_weekly} weekly ({scope})"
        )
        return 0

    interval = args.interval if args.interval else None
    n = market_ohlcv_clear(ticker=args.ticker, interval=interval)
    scope = []
    if args.all:
        scope.append("all")
    if args.ticker:
        scope.append(f"ticker={args.ticker}")
    if interval:
        scope.append(f"interval={interval}")
    print(f"Cancellate {n} righe ({', '.join(scope) if scope else 'all'})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gestione cache OHLCV yfinance (Phase 2).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stats = sub.add_parser("stats", help="Statistiche cache")
    p_stats.set_defaults(func=cmd_stats)

    p_warm = sub.add_parser(
        "warm",
        help="Prefetch daily+weekly per una lista di ticker",
    )
    p_warm.add_argument("tickers", nargs="+", help="Uno o più ticker")
    p_warm.add_argument(
        "--force",
        action="store_true",
        help="Invalida cache prima di fetchare (refresh garantito)",
    )
    p_warm.set_defaults(func=cmd_warm)

    p_clear = sub.add_parser("clear", help="Invalida cache (ticker o totale)")
    p_clear.add_argument("--ticker", help="Solo questo ticker (altrimenti usa --all)")
    p_clear.add_argument("--all", action="store_true", help="Wipe totale")
    p_clear.add_argument(
        "--stale",
        action="store_true",
        help="Solo righe fuori TTL",
    )
    p_clear.add_argument(
        "--interval",
        choices=["daily", "weekly"],
        help="Solo una granularità (default entrambe)",
    )
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
