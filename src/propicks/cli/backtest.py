"""CLI thin wrapper per il backtest walk-forward.

Esempi:
    propicks-backtest AAPL                          # default 5y, threshold 60
    propicks-backtest AAPL MSFT NVDA --period 3y
    propicks-backtest AAPL --threshold 70 --json
    propicks-backtest AAPL --stop-atr 2 --target-atr 3 --time-stop 20
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.backtest import backtest_ticker, compute_metrics
from propicks.backtest.metrics import aggregate_metrics
from propicks.market.yfinance_client import DataUnavailable


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "-"


def _fmt_num(x: float | None, digits: int = 2) -> str:
    return f"{x:.{digits}f}" if x is not None else "-"


def _print_result_summary(m: dict) -> None:
    rows = [
        ["Ticker", m["ticker"]],
        ["Periodo", f"{m['period_start']} → {m['period_end']}"],
        ["Segnali generati / trade eseguiti", f"{m['signals_generated']} / {m['n_trades']}"],
        ["Win rate", _fmt_pct(m["win_rate"])],
        ["Profit factor", _fmt_num(m["profit_factor"])],
        ["Avg win / loss", f"{_fmt_pct(m['avg_win_pct'])} / {_fmt_pct(m['avg_loss_pct'])}"],
        ["Expectancy per trade", _fmt_pct(m["expectancy_pct"])],
        ["CAGR", _fmt_pct(m["cagr_pct"])],
        ["Max drawdown", _fmt_pct(m["max_drawdown_pct"])],
        ["Sharpe / Sortino", f"{_fmt_num(m['sharpe'])} / {_fmt_num(m['sortino'])}"],
        ["Avg bars held", _fmt_num(m["avg_bars_held"], 1)],
        ["Exit reasons", ", ".join(f"{k}={v}" for k, v in m["exit_reasons"].items()) or "-"],
        [
            "Equity: initial → final",
            f"{_fmt_num(m['initial_equity'])} → {_fmt_num(m['final_equity'])}",
        ],
    ]
    print(tabulate(rows, tablefmt="simple"))


def _print_trade_table(result) -> None:
    if not result.trades:
        print("Nessun trade eseguito.")
        return
    headers = ["Entry", "Entry $", "Stop", "Target", "Score", "Exit", "Exit $", "Why", "P&L", "Bars"]
    rows = []
    for t in result.trades:
        rows.append(
            [
                t.entry_date.isoformat(),
                f"{t.entry_price:.2f}",
                f"{t.stop_price:.2f}",
                f"{t.target_price:.2f}",
                f"{t.entry_score:.0f}",
                t.exit_date.isoformat() if t.exit_date else "-",
                f"{t.exit_price:.2f}" if t.exit_price else "-",
                t.exit_reason or "-",
                _fmt_pct(t.pnl_pct),
                t.bars_held or "-",
            ]
        )
    print(tabulate(rows, headers=headers, tablefmt="github"))


def _print_ascii_equity(result, width: int = 60, height: int = 10) -> None:
    """Equity curve ASCII — utile per un colpo d'occhio in terminale.

    Normalizza a initial=1.0, campiona ``width`` punti equamente distribuiti,
    e disegna con '*' su griglia height x width.
    """
    eq = result.equity_curve
    if eq.empty:
        return
    normalized = eq / eq.iloc[0]
    step = max(1, len(normalized) // width)
    sampled = normalized.iloc[::step].values
    if len(sampled) < 2:
        return

    lo, hi = float(min(sampled)), float(max(sampled))
    span = hi - lo if hi > lo else 1.0

    grid = [[" "] * len(sampled) for _ in range(height)]
    for x, v in enumerate(sampled):
        y = int((1 - (v - lo) / span) * (height - 1))
        y = max(0, min(height - 1, y))
        grid[y][x] = "*"

    print()
    print(f"Equity curve (normalized, initial=1.0; range {lo:.2f} → {hi:.2f}):")
    for row in grid:
        print("  " + "".join(row))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest della strategia single-stock (EOD, no slippage, no commissioni).",
    )
    parser.add_argument("tickers", nargs="+", help="Uno o più ticker")
    parser.add_argument("--period", default="5y", help="Periodo yfinance (default 5y)")
    parser.add_argument("--threshold", type=float, default=60.0, help="Composite minimo (default 60)")
    parser.add_argument("--stop-atr", type=float, default=2.0, help="Stop loss in multipli di ATR (default 2.0)")
    parser.add_argument(
        "--target-atr",
        type=float,
        default=4.0,
        help="Target profit in multipli di ATR (default 4.0 → R:R 2:1 teorico)",
    )
    parser.add_argument(
        "--time-stop",
        type=int,
        default=30,
        help="Time stop: bar max senza progresso (default 30)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON strutturato")
    parser.add_argument("--no-trades", action="store_true", help="Nasconde la tabella trade-by-trade")
    parser.add_argument("--no-equity", action="store_true", help="Nasconde l'ASCII equity curve")
    args = parser.parse_args()

    results = {}
    for t in args.tickers:
        try:
            results[t] = backtest_ticker(
                t,
                period=args.period,
                threshold=args.threshold,
                stop_atr_mult=args.stop_atr,
                target_atr_mult=args.target_atr,
                time_stop_bars=args.time_stop,
            )
        except DataUnavailable as err:
            print(f"[errore] {err}", file=sys.stderr)

    if not results:
        return 1

    if args.json:
        out = {
            "per_ticker": {t: compute_metrics(r) for t, r in results.items()},
            "aggregate": aggregate_metrics(results) if len(results) > 1 else None,
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    for ticker, result in results.items():
        print()
        print("=" * 72)
        print(f"BACKTEST — {ticker}")
        print("=" * 72)
        _print_result_summary(compute_metrics(result))
        if not args.no_trades:
            print()
            _print_trade_table(result)
        if not args.no_equity:
            _print_ascii_equity(result)

    if len(results) > 1:
        print()
        print("=" * 72)
        print("AGGREGATE (pool di tutti i trade)")
        print("=" * 72)
        agg = aggregate_metrics(results)
        rows = [[k, v] for k, v in agg.items() if not isinstance(v, dict)]
        rows.append(
            [
                "exit_reasons",
                ", ".join(f"{k}={v}" for k, v in agg["exit_reasons"].items()) or "-",
            ]
        )
        print(tabulate(rows, tablefmt="simple"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
