"""CLI per il journal: add/close/list/stats.

Esempi:
    propicks-journal add AAPL long --entry-price 185.50 \\
        --entry-date 2026-01-15 --stop 171.50 --target 210 \\
        --score-claude 8 --score-tech 75 --strategy TechTitans \\
        --catalyst "Beat earnings Q4"

    propicks-journal close AAPL --exit-price 208.30 \\
        --exit-date 2026-02-10 --reason "Target raggiunto"

    propicks-journal list
    propicks-journal list --open
    propicks-journal list --closed --strategy TechTitans
    propicks-journal stats
    propicks-journal stats --strategy TechTitans
"""

from __future__ import annotations

import argparse
import statistics
import sys
from typing import Optional

from tabulate import tabulate

from propicks.domain.verdict import max_drawdown, verdict
from propicks.io.journal_store import load_journal
from propicks.io.trade_sync import close_trade as sync_close_trade
from propicks.io.trade_sync import open_trade as sync_open_trade


def list_trades(
    filter_status: Optional[str] = None,
    filter_strategy: Optional[str] = None,
) -> None:
    trades = load_journal()
    if filter_status:
        trades = [t for t in trades if t.get("status") == filter_status]
    if filter_strategy:
        trades = [t for t in trades if t.get("strategy") == filter_strategy]

    if not trades:
        print("Nessun trade da mostrare.")
        return

    rows = []
    for t in trades:
        pnl = t.get("pnl_pct")
        rows.append([
            t.get("id"),
            t.get("ticker"),
            t.get("direction"),
            t.get("entry_date"),
            f"{t.get('entry_price', 0):.2f}",
            t.get("exit_date") or "-",
            f"{t.get('exit_price'):.2f}" if t.get("exit_price") is not None else "-",
            f"{pnl:+.2f}%" if pnl is not None else "-",
            t.get("status"),
            t.get("strategy") or "-",
        ])
    print(tabulate(
        rows,
        headers=["ID", "Ticker", "Dir", "Entry date", "Entry", "Exit date",
                 "Exit", "P&L %", "Status", "Strategia"],
        tablefmt="github",
    ))


def compute_stats(filter_strategy: Optional[str] = None) -> None:
    trades = load_journal()
    closed = [t for t in trades if t.get("status") == "closed"]
    if filter_strategy:
        closed = [t for t in closed if t.get("strategy") == filter_strategy]

    if not closed:
        msg = f" per strategia {filter_strategy}" if filter_strategy else ""
        print(f"Nessun trade chiuso{msg}.")
        return

    pnls_pct = [t["pnl_pct"] for t in closed]
    wins = [p for p in pnls_pct if p > 0]
    losses = [p for p in pnls_pct if p <= 0]
    wr = len(wins) / len(closed)
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    pf = abs(avg_win / avg_loss) if avg_loss else float("inf") if wins else 0.0
    best = max(pnls_pct)
    worst = min(pnls_pct)
    max_dd = max_drawdown(pnls_pct)

    durations_w = [t.get("duration_days") or 0 for t in closed if t.get("pnl_pct", 0) > 0]
    durations_l = [t.get("duration_days") or 0 for t in closed if t.get("pnl_pct", 0) <= 0]
    avg_dur_w = statistics.mean(durations_w) if durations_w else 0.0
    avg_dur_l = statistics.mean(durations_l) if durations_l else 0.0

    rows = [
        ["Trade chiusi", len(closed)],
        ["Win rate", f"{wr*100:.1f}% ({len(wins)}W / {len(losses)}L)"],
        ["Avg win", f"{avg_win:+.2f}%"],
        ["Avg loss", f"{avg_loss:+.2f}%"],
        ["Profit factor", f"{pf:.2f}"],
        ["Miglior trade", f"{best:+.2f}%"],
        ["Peggior trade", f"{worst:+.2f}%"],
        ["Max drawdown (cumul.)", f"{max_dd:.2f}%"],
        ["Durata media vincenti", f"{avg_dur_w:.1f} gg"],
        ["Durata media perdenti", f"{avg_dur_l:.1f} gg"],
    ]
    print(tabulate(rows, tablefmt="simple"))

    print("\n--- Breakdown per score Claude ---")
    bands = {"alta (>= 8)": [], "media (6-7)": [], "altro/N/A": []}
    for t in closed:
        sc = t.get("score_claude")
        if sc is None:
            bands["altro/N/A"].append(t["pnl_pct"])
        elif sc >= 8:
            bands["alta (>= 8)"].append(t["pnl_pct"])
        elif sc >= 6:
            bands["media (6-7)"].append(t["pnl_pct"])
        else:
            bands["altro/N/A"].append(t["pnl_pct"])
    brows = []
    for band, pls in bands.items():
        if not pls:
            brows.append([band, "-", "-", "-"])
            continue
        wr_b = sum(1 for p in pls if p > 0) / len(pls) * 100
        brows.append([
            band, len(pls), f"{statistics.mean(pls):+.2f}%", f"{wr_b:.1f}%",
        ])
    print(tabulate(brows, headers=["Band", "# trade", "Avg P&L", "Win rate"], tablefmt="github"))

    if not filter_strategy:
        print("\n--- Breakdown per strategia ---")
        by_strat: dict[str, list[float]] = {}
        for t in closed:
            by_strat.setdefault(t.get("strategy") or "-", []).append(t["pnl_pct"])
        srows = []
        for strat, pls in by_strat.items():
            wr_s = sum(1 for p in pls if p > 0) / len(pls) * 100
            srows.append([
                strat, len(pls),
                f"{statistics.mean(pls):+.2f}%",
                f"{wr_s:.1f}%",
                f"{max(pls):+.2f}%",
                f"{min(pls):+.2f}%",
            ])
        print(tabulate(
            srows,
            headers=["Strategia", "# trade", "Avg P&L", "WR", "Best", "Worst"],
            tablefmt="github",
        ))

    print(f"\nVerdetto: {verdict(wr, pf, len(closed))}")


def cmd_add(args: argparse.Namespace) -> int:
    try:
        trade, position, warnings = sync_open_trade(
            ticker=args.ticker,
            direction=args.direction,
            entry_price=args.entry_price,
            entry_date=args.entry_date,
            shares=args.shares,
            stop_loss=args.stop,
            target=args.target,
            score_claude=args.score_claude,
            score_tech=args.score_tech,
            strategy=args.strategy,
            catalyst=args.catalyst,
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"Trade #{trade['id']} aperto: {trade['ticker']} {trade['direction']} "
        f"{args.shares} @ {trade['entry_price']:.2f} (stop {trade['stop_loss']:.2f})"
    )
    if position is not None:
        cost = position["shares"] * position["entry_price"]
        print(f"Portfolio aggiornato: -{cost:.2f} cash, +{position['shares']} {trade['ticker']}")
    for w in warnings:
        print(f"[warning] {w}", file=sys.stderr)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    try:
        trade, removed, warnings = sync_close_trade(
            ticker=args.ticker,
            exit_price=args.exit_price,
            exit_date=args.exit_date,
            reason=args.reason,
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2

    icon = "🟢" if trade["pnl_pct"] > 0 else "🔴"
    print(
        f"{icon} Trade #{trade['id']} {trade['ticker']} chiuso: "
        f"{trade['entry_price']:.2f} → {trade['exit_price']:.2f} "
        f"({trade['pnl_pct']:+.2f}%, {trade['duration_days']} gg)"
    )
    if removed is not None:
        proceeds = removed["shares"] * trade["exit_price"]
        print(f"Portfolio aggiornato: +{proceeds:.2f} cash, -{removed['shares']} {trade['ticker']}")
    for w in warnings:
        print(f"[warning] {w}", file=sys.stderr)
    print(
        "\nPer la post-trade analysis, incolla questo trade nel prompt Claude 3D "
        "(post-trade review)."
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    status = "open" if args.open else "closed" if args.closed else None
    list_trades(filter_status=status, filter_strategy=args.strategy)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    compute_stats(filter_strategy=args.strategy)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Journal append-only dei trade.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Apre un trade")
    p_add.add_argument("ticker")
    p_add.add_argument("direction", choices=["long", "short"])
    p_add.add_argument("--entry-price", type=float, required=True)
    p_add.add_argument("--entry-date", required=True, help="YYYY-MM-DD")
    p_add.add_argument("--shares", type=int, required=True, help="Numero di azioni")
    p_add.add_argument("--stop", type=float, required=True, help="Stop loss")
    p_add.add_argument("--target", type=float, default=None)
    p_add.add_argument("--score-claude", type=int, default=None)
    p_add.add_argument("--score-tech", type=int, default=None)
    p_add.add_argument("--strategy", default=None)
    p_add.add_argument("--catalyst", default=None)
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=cmd_add)

    p_close = sub.add_parser("close", help="Chiude un trade aperto")
    p_close.add_argument("ticker")
    p_close.add_argument("--exit-price", type=float, required=True)
    p_close.add_argument("--exit-date", default=None, help="YYYY-MM-DD (default: oggi)")
    p_close.add_argument("--reason", default=None)
    p_close.add_argument("--notes", default=None)
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list", help="Elenca i trade")
    g = p_list.add_mutually_exclusive_group()
    g.add_argument("--open", action="store_true")
    g.add_argument("--closed", action="store_true")
    p_list.add_argument("--strategy", default=None)
    p_list.set_defaults(func=cmd_list)

    p_stats = sub.add_parser("stats", help="Metriche aggregate sui trade chiusi")
    p_stats.add_argument("--strategy", default=None)
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
