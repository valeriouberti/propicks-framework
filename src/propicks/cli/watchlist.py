"""CLI per la watchlist — incubatrice di idee prima dell'entry.

Esempi:
    propicks-watchlist add AAPL --target 185.50 --note "pullback EMA20"
    propicks-watchlist remove AAPL
    propicks-watchlist update AAPL --target 190
    propicks-watchlist list
    propicks-watchlist list --stale           # solo >60 giorni
    propicks-watchlist status                 # score live + distanza target
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from tabulate import tabulate

from propicks.config import DATE_FMT
from propicks.domain.scoring import analyze_ticker
from propicks.io.watchlist_store import (
    add_to_watchlist,
    is_stale,
    load_watchlist,
    remove_from_watchlist,
    update_watchlist_entry,
)

STALE_DAYS = 60
READY_SCORE_MIN = 60
READY_DISTANCE_PCT = 0.02  # entro 2% dal target


def _days_since(added_date: str | None) -> int | None:
    if not added_date:
        return None
    try:
        dt = datetime.strptime(added_date, DATE_FMT)
    except ValueError:
        return None
    return (datetime.now() - dt).days


def cmd_add(args: argparse.Namespace) -> int:
    wl = load_watchlist()
    entry, is_new = add_to_watchlist(
        wl,
        args.ticker,
        target_entry=args.target,
        note=args.note,
        source="manual",
    )
    action = "Aggiunto" if is_new else "Aggiornato"
    target_str = f"target {entry['target_entry']:.2f}" if entry.get("target_entry") else "no target"
    print(f"{action} {args.ticker.upper()} in watchlist ({target_str}).")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    wl = load_watchlist()
    try:
        remove_from_watchlist(wl, args.ticker)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(f"Rimosso {args.ticker.upper()} dalla watchlist.")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    wl = load_watchlist()
    try:
        entry = update_watchlist_entry(
            wl, args.ticker, target_entry=args.target, note=args.note
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    target_str = f"{entry['target_entry']:.2f}" if entry.get("target_entry") else "-"
    print(f"Aggiornato {args.ticker.upper()}: target {target_str}, note '{entry.get('note') or '-'}'.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    wl = load_watchlist()
    tickers = wl.get("tickers", {})
    if not tickers:
        print("Watchlist vuota.")
        return 0

    rows = []
    for t, e in tickers.items():
        if args.stale and not is_stale(e, days=STALE_DAYS):
            continue
        age = _days_since(e.get("added_date"))
        rows.append([
            t,
            e.get("added_date") or "-",
            f"{age}gg" if age is not None else "-",
            f"{e['target_entry']:.2f}" if e.get("target_entry") else "-",
            e.get("classification_at_add") or "-",
            f"{e['score_at_add']:.1f}" if e.get("score_at_add") is not None else "-",
            e.get("regime_at_add") or "-",
            e.get("source") or "manual",
            (e.get("note") or "")[:50],
        ])

    if not rows:
        label = "stale" if args.stale else ""
        print(f"Nessuna entry {label}in watchlist.")
        return 0

    print(tabulate(
        rows,
        headers=["Ticker", "Added", "Age", "Target", "Class@add", "Score@add", "Regime@add", "Source", "Note"],
        tablefmt="github",
    ))
    print(f"\n{len(rows)} entry{'  (stale)' if args.stale else ''}.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Score live + distanza dal target + flag READY."""
    wl = load_watchlist()
    tickers = wl.get("tickers", {})
    if not tickers:
        print("Watchlist vuota.")
        return 0

    rows = []
    ready_tickers = []
    for t, e in tickers.items():
        r = analyze_ticker(t, strategy=None)
        if r is None:
            rows.append([t, "-", "-", "-", "-", "-", "-", "skip (no data)"])
            continue
        price = r["price"]
        score = r["score_composite"]
        classification = r["classification"].split(" — ")[0]
        target = e.get("target_entry")
        dist_str = "-"
        ready = False
        if target:
            dist = (price - target) / target
            dist_str = f"{dist * 100:+.2f}%"
            if score >= READY_SCORE_MIN and abs(dist) <= READY_DISTANCE_PCT:
                ready = True
        flag = "READY" if ready else ""
        if ready:
            ready_tickers.append(t)
        regime = r.get("regime") or {}
        rows.append([
            t,
            f"{price:.2f}",
            f"{target:.2f}" if target else "-",
            dist_str,
            f"{score:.1f}",
            classification,
            regime.get("regime", "N/D"),
            flag,
        ])

    print(tabulate(
        rows,
        headers=["Ticker", "Price", "Target", "Dist%", "Score", "Class", "Regime", "Flag"],
        tablefmt="github",
    ))
    if ready_tickers:
        print()
        print(
            f"{len(ready_tickers)} entry READY (score ≥{READY_SCORE_MIN} + "
            f"entro {READY_DISTANCE_PCT * 100:.0f}% dal target): "
            f"{', '.join(ready_tickers)}"
        )
        print("Prossimi passi: propicks-scan + propicks-portfolio size su ogni READY.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestione watchlist.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Aggiungi o aggiorna un ticker")
    p_add.add_argument("ticker")
    p_add.add_argument("--target", type=float, default=None, help="Target entry price")
    p_add.add_argument("--note", default=None, help="Nota libera (es. catalyst, livello tecnico)")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="Rimuovi un ticker dalla watchlist")
    p_rm.add_argument("ticker")
    p_rm.set_defaults(func=cmd_remove)

    p_upd = sub.add_parser("update", help="Aggiorna target o note di una entry")
    p_upd.add_argument("ticker")
    p_upd.add_argument("--target", type=float, default=None)
    p_upd.add_argument("--note", default=None)
    p_upd.set_defaults(func=cmd_update)

    p_list = sub.add_parser("list", help="Elenca la watchlist")
    p_list.add_argument("--stale", action="store_true", help=f"Solo entry > {STALE_DAYS} giorni")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Score live + distanza target + flag READY")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
