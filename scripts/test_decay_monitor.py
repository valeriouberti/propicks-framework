#!/usr/bin/env python3
"""Smoke decay monitor (Fase D.4.2 SIGNAL_ROADMAP).

Test decay detection su sequenze sintetiche di test + opzionalmente su
trade reali dal DB (closed trades). Documenta finding pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    import numpy as np
    from propicks.domain.decay_monitor import (
        decay_alert_summary, cusum_decay_detector, sprt_test,
        rolling_sharpe,
    )
    from propicks.io.db import connect

    print("=== Decay monitor smoke ===\n")

    # 1. Synthetic scenarios
    np.random.seed(42)
    scenarios = [
        ("ALIVE: stable +0.5%/trade", np.random.normal(0.005, 0.02, 200)),
        ("DEAD: zero mean throughout", np.random.normal(0.0, 0.02, 200)),
        ("GRADUAL_DECAY: 0.5% → 0.1% over 200 trades",
         np.concatenate([
             np.random.normal(0.005, 0.02, 100),
             np.random.normal(0.001, 0.02, 100),
         ])),
        ("ABRUPT_DECAY: 0.5% then -0.3%",
         np.concatenate([
             np.random.normal(0.005, 0.02, 100),
             np.random.normal(-0.003, 0.02, 100),
         ])),
        ("REGIME_SHIFT: 0.5% → 0% mid",
         np.concatenate([
             np.random.normal(0.005, 0.02, 100),
             np.random.normal(0.0, 0.02, 100),
         ])),
    ]

    print(f"{'Scenario':<55} {'Decision':<15} {'CUSUM@':<10} {'SPRT':<15}")
    print("-" * 100)
    for label, rets in scenarios:
        out = decay_alert_summary(
            rets.tolist(), expected_sharpe_per_trade=0.20,
        )
        sprt_decision = out["sprt_decision"]
        sprt_idx = out.get("sprt_decision_index")
        sprt_str = f"{sprt_decision}@{sprt_idx}" if sprt_idx else sprt_decision
        cusum_str = str(out["cusum_alarm_index"]) if out["cusum_alarm_index"] else "—"
        print(f"{label:<55} {out['decision']:<15} {cusum_str:<10} {sprt_str:<15}")

    # 2. Real trades from DB (closed)
    print()
    print("=== Real closed trades from DB ===")
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT pnl_pct, entry_date, exit_date, ticker, strategy
               FROM trades WHERE status='closed' AND pnl_pct IS NOT NULL
               ORDER BY exit_date ASC"""
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("  no closed trades in DB")
        return 0

    print(f"  n closed trades: {len(rows)}")
    rets = [r["pnl_pct"] / 100.0 for r in rows]

    if len(rets) >= 5:
        out = decay_alert_summary(rets, expected_sharpe_per_trade=0.20)
        print(f"  decision: {out['decision']}")
        print(f"  rolling Sharpe latest: {out['rolling_sharpe_latest']}")
        print(f"  CUSUM alarm index: {out['cusum_alarm_index']}")
        print(f"  SPRT decision: {out['sprt_decision']}")
    else:
        print(f"  insufficient: {len(rets)} < 5")

    return 0


if __name__ == "__main__":
    sys.exit(main())
