#!/usr/bin/env python3
"""Multi-period stability test (Next Step P0.2 SIGNAL_ROADMAP).

Backtest stesso config su periodi diversi per verificare che edge sia
stabile cross-regime, non artifact di un singolo period bull.

Periodi (2y ciascuno):
- 2018-2020: pre-COVID (mid bull + correction Q4 2018)
- 2020-2022: COVID + reflation rally
- 2022-2024: rate shock + bear/recovery
- 2024-2026: AI rally + recent

Per ogni periodo: baseline_v2 + cumulative C0+C4+C6 best (con
historical-membership filter attivo).

## Caveat

yfinance fetch storico può essere parziale per ticker delisted/cambiati.
Universe corrente top 50 è proxy ragionevole anche per period storici (post-A.1
filter membership-aware).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


PERIODS = [
    ("2018_2020", "2018-01-01", "2019-12-31", "Pre-COVID + Q4 2018 correction"),
    ("2020_2022", "2020-01-01", "2021-12-31", "COVID + reflation rally"),
    ("2022_2024", "2022-01-01", "2023-12-31", "Rate shock + bear + recovery"),
    ("2024_2026", "2024-01-01", "2026-04-30", "AI rally + recent"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-period stability")
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()

    import pandas as pd
    import yfinance as yf

    from scripts.ablation_c_cumulative import _build_scoring_fn
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    from propicks.io.index_membership import build_universe_provider
    from propicks.market.index_constituents import get_sp500_universe
    from propicks.domain.scoring import auto_percentile_for_universe

    tickers = get_sp500_universe()[: args.top]
    provider = build_universe_provider("sp500")

    # Fetch fascia totale 2018-2026 una volta sola, poi filter per period
    print(f"=== Multi-period stability test (top {args.top}) ===")
    print("[fetch] 2018-2026 full range...")
    universe_full: dict = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start="2018-01-01", end="2026-04-30",
                                       auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe_full[t.upper()] = df
        except Exception:
            pass
    print(f"  fetched: {len(universe_full)} ticker")

    auto_pct = auto_percentile_for_universe(len(universe_full))
    print(f"  auto_percentile = {auto_pct:.0f}")

    configs = [
        ("baseline_v2", _build_scoring_fn(use_obv=False, use_multi_lookback=False),
         60.0, False),
        ("C0_C4_C6", _build_scoring_fn(use_obv=True, use_multi_lookback=True),
         auto_pct, True),
    ]

    results = {}
    t0 = time.time()
    for period_id, start, end, label in PERIODS:
        print(f"\n=== {period_id}: {label} ({start} → {end}) ===")
        # Filter universe per period
        universe_period = {}
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        for tk, df in universe_full.items():
            sub = df[(df.index >= start_ts) & (df.index <= end_ts)]
            if len(sub) >= 252:  # almeno 1 anno
                universe_period[tk] = sub
        print(f"  universe size in period: {len(universe_period)}")
        if len(universe_period) < 10:
            print(f"  skip — insufficient ticker")
            continue

        period_results = {}
        for config_name, sfn, thr, use_xs in configs:
            config = BacktestConfig(
                initial_capital=10_000.0, score_threshold=thr,
                use_earnings_gate=False, strategy_tag="momentum",
                use_cross_sectional_rank=use_xs,
            )
            state = simulate_portfolio(
                universe=universe_period, scoring_fn=sfn,
                config=config, universe_provider=provider,
            )
            m = compute_portfolio_metrics(state)
            period_results[config_name] = m
            print(
                f"  [{config_name}] n={m.get('n_trades')} "
                f"sharpe={m.get('sharpe_annualized')} "
                f"ret={m.get('total_return_pct')}% "
                f"DD={m.get('max_drawdown_pct')}% "
                f"PSR={m.get('psr')}"
            )
        results[period_id] = {
            "label": label, "start": start, "end": end,
            "universe_size": len(universe_period),
            "results": period_results,
        }

    print(f"\n  total elapsed: {time.time()-t0:.1f}s")

    # Save JSON
    DATA_DIR = _REPO_ROOT / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "multiperiod_stability.json"
    with open(out_json, "w") as fh:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": "P0.2 — multi-period stability",
            "top": args.top,
            "auto_percentile": auto_pct,
            "periods": results,
        }, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown
    DOCS_DIR = _REPO_ROOT / "docs"
    out_md = DOCS_DIR / "MULTIPERIOD_STABILITY.md"
    lines = []
    lines.append("# Multi-Period Stability — P0.2 SIGNAL_ROADMAP")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Universe: top {args.top} SP500 (auto_percentile P{auto_pct:.0f})")
    lines.append("")
    lines.append("## Risultati per periodo")
    lines.append("")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for period_id, period_data in results.items():
        lines.append(f"### {period_id}: {period_data['label']}")
        lines.append("")
        lines.append(f"Period: `{period_data['start']}` → `{period_data['end']}`. "
                     f"Universe: {period_data['universe_size']} ticker.")
        lines.append("")
        lines.append("| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR |")
        lines.append("|--------|---|------------|-----------|----------|-----|")
        for cfg_name, m in period_data["results"].items():
            lines.append(
                f"| {cfg_name} | {m.get('n_trades', '—')} | "
                f"{_f(m.get('sharpe_annualized'))} | "
                f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
                f"{_f(m.get('max_drawdown_pct'), '{:.2f}')} | "
                f"{_f(m.get('psr'))} |"
            )
        lines.append("")

    # Stability metrics: variance Sharpe across period
    sharpe_values = {"baseline_v2": [], "C0_C4_C6": []}
    for pdata in results.values():
        for cfg, m in pdata["results"].items():
            sr = m.get("sharpe_annualized")
            if sr is not None and cfg in sharpe_values:
                sharpe_values[cfg].append(sr)

    import statistics
    lines.append("## Stability metrics cross-period")
    lines.append("")
    lines.append("| Config | N periods | Sharpe mean | Sharpe std | Sharpe min | Sharpe max |")
    lines.append("|--------|-----------|-------------|------------|------------|------------|")
    for cfg, vals in sharpe_values.items():
        if not vals:
            continue
        lines.append(
            f"| {cfg} | {len(vals)} | "
            f"{statistics.mean(vals):.3f} | "
            f"{statistics.stdev(vals) if len(vals) > 1 else 0:.3f} | "
            f"{min(vals):.3f} | {max(vals):.3f} |"
        )

    lines.append("")
    lines.append("## Lettura")
    lines.append("")
    lines.append(
        "- **Sharpe std cross-period basso** = strategia stabile across regime"
    )
    lines.append(
        "- **Sharpe min**: worst-case observed. Se < 0 in qualche periodo, "
        "edge non robust"
    )
    lines.append(
        "- **C0_C4_C6 vs baseline**: differenza Sharpe stable across period "
        "= edge incrementale robusto. Se differenza varia molto, edge "
        "regime-dependent (concerning)"
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
