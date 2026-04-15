"""CLI per lo stato e la mutazione del portafoglio.

Esempi:
    propicks-portfolio status
    propicks-portfolio risk
    propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \\
        --score-claude 8 --score-tech 75
    propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \\
        --target 210 --strategy TechTitans
    propicks-portfolio update AAPL --stop 180 --target 215
    propicks-portfolio remove AAPL
"""

from __future__ import annotations

import argparse
import sys

from tabulate import tabulate

from propicks.config import (
    MAX_LOSS_WEEKLY_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
)
from propicks.domain.sizing import calculate_position_size, portfolio_value
from propicks.io.portfolio_store import (
    add_position,
    load_portfolio,
    remove_position,
    update_position,
)
from propicks.market.yfinance_client import get_current_prices


def show_status(portfolio: dict) -> None:
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)
    cash_pct = cash / total if total else 0.0

    header = [
        ["Capitale totale (cash + invested)", f"{total:.2f}"],
        ["Cash", f"{cash:.2f}  ({cash_pct*100:.1f}%)"],
        ["Invested", f"{total - cash:.2f}"],
        ["Posizioni aperte", f"{len(positions)} / {MAX_POSITIONS}"],
        ["Ultimo aggiornamento", portfolio.get("last_updated") or "-"],
    ]
    print(tabulate(header, tablefmt="simple"))

    if cash_pct < MIN_CASH_RESERVE_PCT:
        print(f"\n[warning] cash sotto riserva minima ({MIN_CASH_RESERVE_PCT*100:.0f}%).")

    if not positions:
        print("\nNessuna posizione aperta.")
        return

    prices = get_current_prices(list(positions.keys()))
    rows = []
    total_pl = 0.0
    for ticker, p in positions.items():
        entry = p["entry_price"]
        shares = p["shares"]
        cur = prices.get(ticker)
        pl_abs = (cur - entry) * shares if cur else None
        pl_pct = (cur - entry) / entry if cur else None
        if pl_abs is not None:
            total_pl += pl_abs
        rows.append([
            ticker, shares, f"{entry:.2f}",
            f"{cur:.2f}" if cur else "-",
            f"{pl_abs:+.2f}" if pl_abs is not None else "-",
            f"{pl_pct*100:+.2f}%" if pl_pct is not None else "-",
            f"{p['stop_loss']:.2f}",
            f"{p['target']:.2f}" if p.get("target") else "-",
            p.get("strategy") or "-",
        ])
    print()
    print(tabulate(
        rows,
        headers=["Ticker", "Shares", "Entry", "Current", "P&L €", "P&L %", "Stop", "Target", "Strategia"],
        tablefmt="github",
    ))
    print(f"\nP&L totale unrealized: {total_pl:+.2f}")

    print()
    print("=" * 70)
    print("COPIA/INCOLLA per prompt Claude 3B (review portafoglio)")
    print("=" * 70)
    print()
    print("| Ticker | Shares | Entry | Current | P&L % | Stop | Target | Strategia |")
    print("|--------|--------|-------|---------|-------|------|--------|-----------|")
    for ticker, p in positions.items():
        cur = prices.get(ticker)
        pl_pct = (cur - p["entry_price"]) / p["entry_price"] if cur else None
        cur_str = f"{cur:.2f}" if cur else "-"
        pl_str = f"{pl_pct*100:+.2f}%" if pl_pct is not None else "-"
        target_str = f"{p['target']:.2f}" if p.get("target") else "-"
        print(
            f"| {ticker} | {p['shares']} | {p['entry_price']:.2f} | {cur_str} | "
            f"{pl_str} | {p['stop_loss']:.2f} | {target_str} | "
            f"{p.get('strategy') or '-'} |"
        )


def show_risk(portfolio: dict) -> None:
    positions = portfolio.get("positions", {})
    total = portfolio_value(portfolio)
    if not positions:
        print("Nessuna posizione aperta.")
        return

    rows = []
    risk_sum = 0.0
    for ticker, p in positions.items():
        entry = p["entry_price"]
        stop = p["stop_loss"]
        shares = p["shares"]
        risk = (entry - stop) * shares
        risk_pct = risk / total if total else 0.0
        risk_sum += risk
        rows.append([
            ticker, shares,
            f"{entry:.2f}", f"{stop:.2f}",
            f"{risk:.2f}",
            f"{risk_pct*100:.2f}%",
        ])

    print(tabulate(
        rows,
        headers=["Ticker", "Shares", "Entry", "Stop", "Rischio €", "% capitale"],
        tablefmt="github",
    ))

    weekly_limit = total * MAX_LOSS_WEEKLY_PCT
    risk_pct = risk_sum / total if total else 0.0
    print()
    print(f"Rischio aggregato a stop: {risk_sum:.2f}  ({risk_pct*100:.2f}% del capitale)")
    print(f"Limite settimanale:       {weekly_limit:.2f}  ({MAX_LOSS_WEEKLY_PCT*100:.0f}%)")
    if risk_sum > weekly_limit:
        print("[warning] rischio aggregato oltre il limite settimanale.")


def _print_size_result(ticker: str, r: dict) -> None:
    if not r.get("ok"):
        print(f"[errore] {r.get('error')}", file=sys.stderr)
        return
    rows = [
        ["Ticker", ticker.upper()],
        ["Entry / Stop", f"{r['entry_price']:.2f} / {r['stop_price']:.2f}"],
        ["Rischio per azione", f"{r['risk_per_share']:.2f}"],
        ["Convinzione", f"{r['conviction']} (avg score {r['avg_score']:.1f})"],
        ["Target value (conv.)", f"{r['target_value']:.2f}"],
        ["Max value (15% cap)", f"{r['max_value']:.2f}"],
        ["Cash disponibile", f"{r['cash_available']:.2f}"],
        ["Shares", r["shares"]],
        ["Position value effettivo", f"{r['position_value']:.2f}  ({r['position_pct']*100:.2f}% cap)"],
        ["Rischio totale a stop", f"{r['risk_total']:.2f}  ({r['risk_pct_capital']*100:.2f}% cap)"],
        ["Risk % per trade", f"{r['risk_pct_trade']*100:.2f}%"],
    ]
    print(tabulate(rows, tablefmt="simple"))
    for w in r.get("warnings", []):
        print(f"[warning] {w}")


def cmd_status(_: argparse.Namespace) -> int:
    show_status(load_portfolio())
    return 0


def cmd_risk(_: argparse.Namespace) -> int:
    show_risk(load_portfolio())
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    r = calculate_position_size(
        entry_price=args.entry,
        stop_price=args.stop,
        score_claude=args.score_claude,
        score_tech=args.score_tech,
        portfolio=portfolio,
    )
    _print_size_result(args.ticker, r)
    return 0 if r.get("ok") else 2


def cmd_add(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = add_position(
            portfolio,
            ticker=args.ticker,
            entry_price=args.entry,
            shares=args.shares,
            stop_loss=args.stop,
            target=args.target,
            strategy=args.strategy,
            score_claude=args.score_claude,
            score_tech=args.score_tech,
            catalyst=args.catalyst,
            entry_date=args.entry_date,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(f"Aggiunto {args.ticker.upper()}: {pos['shares']} azioni @ {pos['entry_price']:.2f}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = remove_position(portfolio, args.ticker)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"Rimosso {args.ticker.upper()}: cash rimborsato al prezzo di entry "
        f"({pos['shares']} × {pos['entry_price']:.2f})."
    )
    print(
        "Promemoria: registra la chiusura nel journal con:\n"
        f"  propicks-journal close {args.ticker.upper()} --exit-price <X> "
        "--exit-date YYYY-MM-DD --reason '<motivo>'"
    )
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = update_position(portfolio, args.ticker, args.stop, args.target)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    target_str = f"{pos['target']:.2f}" if pos.get("target") else "-"
    print(
        f"Aggiornato {args.ticker.upper()}: stop {pos['stop_loss']:.2f}, target {target_str}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestione portafoglio.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Stato portafoglio e P&L").set_defaults(func=cmd_status)
    sub.add_parser("risk", help="Rischio aggregato a stop").set_defaults(func=cmd_risk)

    p_size = sub.add_parser("size", help="Calcola position size")
    p_size.add_argument("ticker")
    p_size.add_argument("--entry", type=float, required=True)
    p_size.add_argument("--stop", type=float, required=True)
    p_size.add_argument("--score-claude", type=int, default=7)
    p_size.add_argument("--score-tech", type=int, default=70)
    p_size.set_defaults(func=cmd_size)

    p_add = sub.add_parser("add", help="Apre una posizione")
    p_add.add_argument("ticker")
    p_add.add_argument("--entry", type=float, required=True)
    p_add.add_argument("--shares", type=int, required=True)
    p_add.add_argument("--stop", type=float, required=True)
    p_add.add_argument("--target", type=float, default=None)
    p_add.add_argument("--strategy", default=None)
    p_add.add_argument("--score-claude", type=int, default=None)
    p_add.add_argument("--score-tech", type=int, default=None)
    p_add.add_argument("--catalyst", default=None)
    p_add.add_argument("--entry-date", default=None, help="YYYY-MM-DD (default: oggi)")
    p_add.set_defaults(func=cmd_add)

    p_upd = sub.add_parser("update", help="Aggiorna stop o target")
    p_upd.add_argument("ticker")
    p_upd.add_argument("--stop", type=float, default=None)
    p_upd.add_argument("--target", type=float, default=None)
    p_upd.set_defaults(func=cmd_update)

    p_rm = sub.add_parser("remove", help="Rimuove una posizione (non chiude il trade)")
    p_rm.add_argument("ticker")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
