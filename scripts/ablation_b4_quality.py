#!/usr/bin/env python3
"""Ablation B.4 — Quality filter (Asness QMJ) vs baseline momentum.

Confronta:

- **Run 0 (baseline)**: momentum, no quality filter
- **Run 1 (B.4 top half)**: quality filter T50+
- **Run 2 (B.4 top tercile)**: quality filter T67+ (default raccomandato)
- **Run 3 (B.4 top quintile)**: quality filter T80+

Tutti con `--historical-membership sp500` + cross-sectional rank (B.1).

## Caveat critico

yfinance ``info`` espone fundamentals come **snapshot oggi** (TTM). Backtest
historical applica filter "current quality" a entry passate → look-ahead bias.
Stesso pattern del caveat B.2.

Per OOS proper serve historical fundamentals (paid: Compustat, Sharadar;
free limited: SimFin, EDGAR direct parsing).
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

DATA_DIR = _REPO_ROOT / "data"
DOCS_DIR = _REPO_ROOT / "docs"


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablation B.4 quality filter")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=60.0)
    parser.add_argument("--no-cross-sectional", action="store_true",
                        help="Disable cross-sectional ranking (default ON)")
    args = parser.parse_args()

    from scripts.baseline_backtest import (
        _build_momentum_scoring_fn, _fetch_universe, _resolve_universe,
    )
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    from propicks.io.index_membership import build_universe_provider
    from propicks.market.yfinance_client import get_quality_metrics

    spec = f"momentum_sp500_top{args.top}_{args.period}"
    print(f"=== Ablation B.4 quality — {spec} ===")
    use_xs = not args.no_cross_sectional
    print(f"  cross-sectional: {use_xs}")

    # Fetch OHLCV (una volta)
    tickers = _resolve_universe(spec, args.top)
    universe = _fetch_universe(tickers, args.period)
    if not universe:
        print("[errore] universe vuoto", file=sys.stderr)
        return 1
    print(f"  universe: {len(universe)} ticker fetched", file=sys.stderr)

    # Quality scores per ogni ticker (snapshot oggi, una volta)
    print("  [quality] fetching metrics...", file=sys.stderr)
    quality_scores: dict[str, float | None] = {}
    for ticker in universe:
        try:
            m = get_quality_metrics(ticker)
            quality_scores[ticker] = m.get("score")
        except Exception as exc:
            print(f"    ✗ {ticker}: {exc}", file=sys.stderr)
            quality_scores[ticker] = None

    valid = [s for s in quality_scores.values() if s is not None]
    if valid:
        import statistics
        print(
            f"  quality_scores: n_valid={len(valid)}, "
            f"mean={statistics.mean(valid):.1f}, "
            f"min={min(valid):.1f}, max={max(valid):.1f}",
            file=sys.stderr,
        )

    runs = [
        ("baseline_no_quality_filter", None),
        ("b4_top_half_T50", 50.0),
        ("b4_top_tercile_T67", 67.0),
        ("b4_top_quintile_T80", 80.0),
    ]

    provider = build_universe_provider("sp500")
    scoring_fn = _build_momentum_scoring_fn()
    results: dict = {}
    t0 = time.time()
    for label, q_pct in runs:
        print(f"  [{label}] q_filter={q_pct}", file=sys.stderr)
        config = BacktestConfig(
            initial_capital=10_000.0,
            score_threshold=args.threshold,
            use_earnings_gate=False,
            strategy_tag="momentum",
            use_cross_sectional_rank=use_xs,
            quality_scores=quality_scores if q_pct is not None else None,
            quality_filter_pct=q_pct,
        )
        state = simulate_portfolio(
            universe=universe, scoring_fn=scoring_fn, config=config,
            universe_provider=provider,
        )
        m = compute_portfolio_metrics(state)
        m["quality_filter_pct"] = q_pct
        results[label] = m
        print(
            f"    n_trades={m.get('n_trades')} sharpe_ann={m.get('sharpe_annualized')} "
            f"ret={m.get('total_return_pct')}% PSR={m.get('psr')}",
            file=sys.stderr,
        )
    print(f"  total elapsed: {time.time()-t0:.1f}s")

    # Save JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "ablation_b4_quality.json"
    with open(out_json, "w") as fh:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": "B.4 — quality filter (Asness QMJ)",
            "spec": spec,
            "params": {
                "period": args.period, "top": args.top,
                "threshold": args.threshold, "cross_sectional": use_xs,
            },
            "quality_scores": quality_scores,
            "results": results,
        }, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = DOCS_DIR / "ABLATION_B4_QUALITY.md"
    lines = []
    lines.append("# Ablation B.4 — Quality Filter (Asness QMJ)")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Spec: `{spec}` ({len(universe)} ticker)")
    lines.append(f"Cross-sectional: {use_xs}")
    lines.append("")
    lines.append("Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.4.")
    lines.append("")
    lines.append("## ⚠ Caveat look-ahead bias")
    lines.append("")
    lines.append(
        "yfinance ``info`` ritorna **snapshot oggi** dei fundamentals (TTM). "
        "Backtest historical applica filter 'current quality' a entry passate "
        "→ look-ahead bias. Numeri sotto NON interpretabili come edge OOS reale."
    )
    lines.append("")
    lines.append("## Risultati")
    lines.append("")
    lines.append(
        "| Configurazione | Q% | N trades | Sharpe ann | Tot ret % | "
        "Max DD % | Win % | PSR |"
    )
    lines.append("|----------------|----|----------|------------|-----------|----------|-------|-----|")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for label, q in runs:
        m = results[label]
        lines.append(
            f"| {label} | {q!s} | {m.get('n_trades', '—')} | "
            f"{_f(m.get('sharpe_annualized'))} | "
            f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
            f"{_f(m.get('max_drawdown_pct'), '{:.2f}')} | "
            f"{_f(m.get('win_rate'), '{:.3f}')} | "
            f"{_f(m.get('psr'))} |"
        )
    lines.append("")
    baseline = results.get("baseline_no_quality_filter", {})
    lines.append("## Δ vs baseline")
    lines.append("")
    lines.append(
        "| Run | Δ Sharpe | Δ Tot ret % | Δ N trades |"
    )
    lines.append("|-----|----------|-------------|------------|")
    for label, q in runs:
        if label == "baseline_no_quality_filter":
            continue
        r = results[label]
        d_sharpe = (r.get("sharpe_annualized") or 0) - (baseline.get("sharpe_annualized") or 0)
        d_ret = (r.get("total_return_pct") or 0) - (baseline.get("total_return_pct") or 0)
        d_n = (r.get("n_trades") or 0) - (baseline.get("n_trades") or 0)
        lines.append(f"| {label} | {d_sharpe:+.3f} | {d_ret:+.2f} | {d_n:+d} |")
    lines.append("")
    lines.append("## Quality scores distribution")
    lines.append("")
    lines.append("| Ticker | Quality score |")
    lines.append("|--------|---------------|")
    for tk in sorted(quality_scores.keys()):
        s = quality_scores[tk]
        lines.append(f"| {tk} | {_f(s, '{:.1f}')} |")
    lines.append("")
    lines.append("## Decision rule")
    lines.append("")
    lines.append(
        "Mantieni quality filter solo se Δ Sharpe > +0.10 senza inflate "
        "look-ahead. Per validation OOS proper serve dataset historical "
        "fundamentals (paid). Feature utile in **live signal mode** "
        "(snapshot truly current); **NON adottare default basandosi su backtest**."
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
