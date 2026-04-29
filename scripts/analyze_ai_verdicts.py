#!/usr/bin/env python3
"""Retrospective AI verdict analysis (Fase D.1.2 SIGNAL_ROADMAP).

Query ``ai_verdicts`` + ``trades`` tables, calcola:
1. Distribution verdict (CONFIRM / CAUTION / REJECT) e conviction
2. Hit rate per verdict tier (richiede join trade chiuso)
3. Brier score se conviction interpretabile come probability
4. AI add-value Sharpe (passed vs rejected)
5. Decision rule: drop AI gate se add-value < 0.05 Sharpe

## Caveat sample size

Framework pronto per analisi statistica proper. Su sample < 50 verdict
con outcome chiari, conclusions sono indicative ma not robust. Re-run
periodicamente man mano che storia cresce.

## Usage

    python scripts/analyze_ai_verdicts.py
    python scripts/analyze_ai_verdicts.py --strategy momentum
    python scripts/analyze_ai_verdicts.py --json > out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrospective AI verdict analysis")
    parser.add_argument("--strategy", default=None, help="Filter strategy (momentum/contrarian/etf)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from propicks.io.db import connect
    from propicks.domain.calibration_stats import (
        brier_score, expected_calibration_error,
        reliability_diagram, ai_add_value_sharpe,
    )

    conn = connect()
    try:
        # 1. Verdict distribution
        where = "1=1"
        params: list = []
        if args.strategy:
            where = "strategy = ?"
            params = [args.strategy]

        rows = conn.execute(
            f"""SELECT verdict, COUNT(*) as n, AVG(conviction) as avg_conv,
                       MIN(run_timestamp) as first, MAX(run_timestamp) as last
                FROM ai_verdicts
                WHERE {where}
                GROUP BY verdict
                ORDER BY n DESC""",
            params,
        ).fetchall()
        distribution = [dict(r) for r in rows]

        # 2. Total verdict count
        total = sum(r["n"] for r in distribution)

        # 3. Join verdicts ↔ trades (best effort: same ticker, verdict before entry)
        # Schema trades non ha direct FK a ai_verdicts; matching euristico
        verdict_trade_pairs = conn.execute(
            f"""SELECT v.verdict, v.conviction, v.ticker,
                       v.run_timestamp as verdict_ts,
                       t.entry_date, t.exit_date, t.pnl_pct, t.exit_reason
                FROM ai_verdicts v
                LEFT JOIN trades t ON v.ticker = t.ticker
                  AND date(v.run_timestamp) <= t.entry_date
                  AND t.entry_date <= date(v.run_timestamp, '+7 days')
                WHERE {where} AND t.status = 'closed'""",
            params,
        ).fetchall()
        pairs = [dict(r) for r in verdict_trade_pairs]

        # 4. Brier score: conviction (0-10) → probability (0-1) via /10.
        # Outcome = pnl_pct > 0
        if pairs:
            preds = [r["conviction"] / 10.0 for r in pairs if r["conviction"] is not None]
            outs = [
                1 if (r["pnl_pct"] is not None and r["pnl_pct"] > 0) else 0
                for r in pairs if r["conviction"] is not None
            ]
            brier = brier_score(preds, outs) if preds else None
            ece = expected_calibration_error(preds, outs, n_bins=5) if preds else None
            reliability = reliability_diagram(preds, outs, n_bins=5) if preds else []
        else:
            brier = None
            ece = None
            reliability = []
            preds = []
            outs = []

        # 5. AI add-value: confirm/conviction>=7 vs caution/reject
        passed_returns = [
            r["pnl_pct"] / 100.0 for r in pairs
            if r["pnl_pct"] is not None
            and r["verdict"] == "CONFIRM"
        ]
        rejected_returns = [
            r["pnl_pct"] / 100.0 for r in pairs
            if r["pnl_pct"] is not None
            and r["verdict"] in ("CAUTION", "REJECT")
        ]
        add_value = ai_add_value_sharpe(passed_returns, rejected_returns)

    finally:
        conn.close()

    out = {
        "distribution": distribution,
        "total_verdicts": total,
        "matched_trades": len(pairs),
        "brier_score": round(brier, 4) if brier is not None else None,
        "ece": round(ece, 4) if ece is not None else None,
        "reliability_diagram": reliability,
        "ai_add_value": add_value,
        "sample_size_warning": (
            "INSUFFICIENT" if total < 50
            else "MARGINAL" if total < 200
            else "OK"
        ),
        "decision_rule_threshold": 0.05,
    }

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    # Pretty-print
    print(f"=== AI Verdict Analysis (strategy={args.strategy or 'all'}) ===\n")
    print(f"Total verdicts: {total}")
    print(f"Sample warning: {out['sample_size_warning']}")
    print()
    print("Distribution:")
    for d in distribution:
        print(f"  {d['verdict']:>10}: {d['n']:>4} (avg conv {d['avg_conv']:.2f}, "
              f"{d['first'][:10]} → {d['last'][:10]})")
    print()
    print(f"Matched trades (verdict ↔ trade entry): {len(pairs)}")
    if brier is not None:
        print(f"Brier score: {brier:.4f}  (lower=better, 0.25=random uniform)")
    if ece is not None:
        print(f"ECE (5 bins): {ece:.4f}  (lower=better)")
    if reliability:
        print()
        print("Reliability diagram (5 bins):")
        print(f"  {'bin':<10} {'n':>4} {'mean_pred':>10} {'mean_obs':>10} {'gap':>8}")
        for b in reliability:
            print(f"  [{b['bin_lo']:.1f}-{b['bin_hi']:.1f}]  "
                  f"{b['n']:>4} {b['mean_pred']:>10.4f} {b['mean_obs']:>10.4f} "
                  f"{b['gap']:>+8.4f}")
    print()
    print("AI Add-Value (Sharpe passed CONFIRM vs CAUTION+REJECT):")
    print(f"  n_passed (CONFIRM):  {add_value['n_passed']}")
    print(f"  n_rejected:          {add_value['n_rejected']}")
    print(f"  Sharpe passed:       {add_value['sharpe_passed']}")
    print(f"  Sharpe rejected:     {add_value['sharpe_rejected']}")
    print(f"  Add-value:           {add_value['add_value']}")
    print(f"  Decision (threshold +0.05): {add_value['decision']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
