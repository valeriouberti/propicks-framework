"""CLI thin wrapper per Strategy Decay Monitor (Fase D.4 SIGNAL_ROADMAP).

Mirror di ``pages/14_Decay_Monitor.py``: rolling Sharpe + CUSUM (Page 1954)
+ SPRT (Wald 1945) su closed trades dal journal SQLite.

Stateless: ricomputa ogni volta dai trade chiusi. Niente baseline persistito.

Esempi:
    propicks-decay monitor                        # all strategies
    propicks-decay monitor --strategy momentum    # filter
    propicks-decay monitor --expected-sharpe 0.20 --rolling 30
    propicks-decay monitor --strategy thematic --json
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

VALID_STRATEGIES = ("all", "momentum", "contrarian", "etf", "thematic")


def _fetch_returns(strategy_filter: str) -> tuple[list[float], list[dict]]:
    """Pull closed trades dal DB, filter per strategy, return (returns, rows).

    Lazy import per velocità help.
    """
    from propicks.io.db import connect

    conn = connect()
    try:
        where = "status='closed' AND pnl_pct IS NOT NULL"
        params: list = []
        if strategy_filter != "all":
            where += " AND strategy = ?"
            params = [strategy_filter]
        rows = conn.execute(
            f"""SELECT ticker, strategy, entry_date, exit_date, pnl_pct, exit_reason
                FROM trades WHERE {where}
                ORDER BY exit_date ASC""",
            params,
        ).fetchall()
    finally:
        conn.close()

    rows_dict = [dict(r) for r in rows]
    returns = [float(r["pnl_pct"]) / 100.0 for r in rows_dict]
    return returns, rows_dict


def cmd_monitor(args: argparse.Namespace) -> int:
    from propicks.domain.decay_monitor import (
        cusum_decay_detector,
        decay_alert_summary,
        rolling_sharpe,
        sprt_test,
    )

    strategy = args.strategy.lower()
    if strategy not in VALID_STRATEGIES:
        print(
            f"[errore] strategy '{strategy}' non valida. "
            f"Valid: {', '.join(VALID_STRATEGIES)}",
            file=sys.stderr,
        )
        return 1

    returns, rows = _fetch_returns(strategy)
    n = len(returns)

    if args.json:
        if n < 5:
            print(json.dumps({
                "strategy": strategy,
                "n_obs": n,
                "decision": "INSUFFICIENT_DATA",
                "message": f"Need at least 5 closed trades, got {n}",
            }, indent=2))
            return 0
        summary = decay_alert_summary(
            returns,
            expected_sharpe_per_trade=float(args.expected_sharpe),
            rolling_window=int(args.rolling),
            cusum_threshold_h=float(args.cusum_h),
        )
        # Add sample-size tier annotation
        if n < 30:
            summary["sample_size_tier"] = "UNRELIABLE"
        elif n < 50:
            summary["sample_size_tier"] = "INDICATIVE"
        else:
            summary["sample_size_tier"] = "RELIABLE"
        summary["strategy"] = strategy
        print(json.dumps(summary, indent=2, default=str))
        return 0

    # ─── Plain output ───
    print()
    print("=" * 70)
    print(f"STRATEGY DECAY MONITOR — strategy={strategy}")
    print("=" * 70)
    print(f"Closed trades found: {n}")
    print()

    # Sample-size tiered banner
    if n < 5:
        print(f"🛑 INSUFFICIENT DATA — need ≥5 closed trades, got {n}.")
        print(f"   Continua a tradare e accumula trade chiusi nel journal.")
        return 0
    if n < 30:
        print(f"⚠️  UNRELIABLE — {n} trade < 30. Output sanity check only.")
        if strategy == "thematic":
            print(
                "    Thematic: gate journal-evidence richiede 15+ trade "
                "(vedi THEMATIC_STRATEGY.md §9)."
            )
    elif n < 50:
        print(f"ℹ️  INDICATIVE — {n} trade. 50+ trade per signal pienamente affidabile.")
    else:
        print(f"✅ RELIABLE — {n} trade.")
    print()

    summary = decay_alert_summary(
        returns,
        expected_sharpe_per_trade=float(args.expected_sharpe),
        rolling_window=int(args.rolling),
        cusum_threshold_h=float(args.cusum_h),
    )

    # ─── Composite decision ───
    decision = summary["decision"]
    decision_emoji = {
        "ALERT_DECAY": "🔴",
        "WARNING": "🟡",
        "MONITOR": "⚪",
        "ALIVE": "🟢",
        "NO_DATA": "⚪",
    }.get(decision, "⚪")

    print(f"{decision_emoji}  COMPOSITE DECISION: {decision}")
    print()

    # ─── Detector breakdown ───
    rs_latest = summary["rolling_sharpe_latest"]
    rs_thr = summary["rolling_sharpe_threshold_warn"]
    rows_metrics = [
        ["Expected Sharpe (per trade)", f"{args.expected_sharpe:.3f}"],
        ["Rolling SR latest", f"{rs_latest:.3f}" if rs_latest is not None else "—"],
        ["Rolling SR warn threshold", f"{rs_thr:.3f}"],
        ["CUSUM decay detected", "YES" if summary["cusum_decay_detected"] else "no"],
        ["CUSUM alarm @ index", str(summary["cusum_alarm_index"]) if summary["cusum_alarm_index"] is not None else "—"],
        ["SPRT decision", summary["sprt_decision"]],
        ["SPRT decision @ index", str(summary["sprt_decision_index"]) if summary.get("sprt_decision_index") is not None else "—"],
        ["N obs", str(summary["n_obs"])],
    ]
    print(tabulate(rows_metrics, tablefmt="simple"))

    # ─── Action recommendation ───
    print()
    print("=" * 70)
    print("ACTION")
    print("=" * 70)
    actions = {
        "ALERT_DECAY": (
            "🔴 STOP nuove entry. Review qualitativa: regime change? rotation? "
            "feature deprecate? Confronta con propicks-regime composite + "
            "propicks-backtest re-run prima di chiudere posizioni esistenti."
        ),
        "WARNING": (
            "🟡 Rolling SR sotto soglia. Riduci size nuove entry, tieni stretto "
            "lo stop. Re-check tra 5-10 trade chiusi."
        ),
        "MONITOR": "⚪ Edge ambiguo. Continua a tradare con sizing standard.",
        "ALIVE": "🟢 Edge confermato. Continua trading normale.",
        "NO_DATA": "⚪ Dati insufficienti.",
    }
    print(actions.get(decision, "—"))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Strategy decay monitor (Fase D.4): rolling Sharpe + CUSUM + SPRT "
            "su closed trades. Output composite decision (ALIVE/MONITOR/WARNING/ALERT_DECAY)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mon = sub.add_parser(
        "monitor",
        help="Run decay analysis su trade chiusi del journal",
    )
    p_mon.add_argument(
        "--strategy", default="all",
        choices=VALID_STRATEGIES,
        help="Filter trades per strategy (default 'all')",
    )
    p_mon.add_argument(
        "--expected-sharpe", type=float, default=0.20,
        help=(
            "Sharpe per-trade atteso da backtest baseline (default 0.20). "
            "Es. 0.20 ≈ Sharpe annuo ~1.2 su 50 trade/anno"
        ),
    )
    p_mon.add_argument(
        "--rolling", type=int, default=30,
        help="Rolling Sharpe window in trade (default 30, min 20)",
    )
    p_mon.add_argument(
        "--cusum-h", type=float, default=5.0,
        help="CUSUM threshold in σ units (default 5.0; più basso = più sensibile)",
    )
    p_mon.add_argument(
        "--json", action="store_true",
        help="Output JSON strutturato",
    )
    p_mon.set_defaults(func=cmd_monitor)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
