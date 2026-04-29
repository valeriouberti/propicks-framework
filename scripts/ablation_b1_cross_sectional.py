#!/usr/bin/env python3
"""Ablation B.1 — Cross-sectional rank vs absolute threshold.

Confronta tre configurazioni su stesso universe:
- **Run 0 (legacy)**: absolute threshold 60 (config attuale)
- **Run 1 (B.1 top quintile)**: cross-sectional rank ≥ 80
- **Run 2 (B.1 top tercile)**: cross-sectional rank ≥ 67

Tutti con `--historical-membership sp500` per separare effect cross-sectional
da effect survivorship.

## Razionale Fase B.1

Edge momentum classico (Jegadeesh-Titman 1993): top quintile vs bottom
quintile. Score absolute 60 cattura "decent momentum" universalmente — non
distingue regime. In BULL universe medio è 70 → score 60 è sotto-mediana.
In BEAR medio è 35 → score 60 è top decile.

Cross-sectional rank rende threshold relativo allo stato del mercato,
selezionando sempre top X% dell'universe disponibile.

## Hypothesis

Cross-sectional > absolute su Sharpe netto, specialmente in regimi misti
(bull + bear nello stesso periodo). Su universe ristretto top-30 mega-cap
in bull market 2021-2026, vantaggio atteso modesto (universe già filtered
to high quality).

## Output

`docs/ABLATION_B1_CROSS_SECTIONAL.md` con tabella + interpretation.
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
# Permetti import "from scripts.xxx" anche da subdir (per riusare baseline_backtest)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DATA_DIR = _REPO_ROOT / "data"
DOCS_DIR = _REPO_ROOT / "docs"


def _load_helpers():
    """Lazy import per evitare side effect su scripts/__init__ etc."""
    from scripts.baseline_backtest import (
        _build_momentum_scoring_fn,
        _fetch_universe,
        _resolve_universe,
        _run_spec,
    )
    return _build_momentum_scoring_fn, _fetch_universe, _resolve_universe, _run_spec


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablation Fase B.1 cross-sectional")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--spec", default="sp500", choices=["sp500", "ndx"])
    args = parser.parse_args()

    _build_score_fn, _fetch, _resolve, _run = _load_helpers()

    spec_name = (
        f"momentum_{args.spec}_top{args.top}_{args.period}"
    )
    print(f"=== Ablation B.1 cross-sectional — {spec_name} ===")

    # Fetch UNA volta, riusa per tutti i run
    tickers = _resolve(spec_name, args.top)
    universe = _fetch(tickers, args.period)
    print(f"  fetched: {len(universe)} ticker", file=sys.stderr)
    if not universe:
        print("[errore] universe vuoto", file=sys.stderr)
        return 1

    runs = [
        # (label, threshold, use_cross_sectional)
        ("baseline_v2_absolute_60", 60.0, False),
        ("b1_cross_sectional_top_quintile_p80", 80.0, True),
        ("b1_cross_sectional_top_tercile_p67", 67.0, True),
        ("b1_cross_sectional_top_decile_p90", 90.0, True),
    ]

    results: dict = {}
    t0 = time.time()
    for label, thr, xs in runs:
        print(f"  [{label}] thr={thr} xs={xs}", file=sys.stderr)
        m = _run(
            spec_name, args.top, args.period,
            use_membership=True,
            threshold=thr,
            use_cross_sectional=xs,
            cached_universe=universe,
        )
        results[label] = m
        print(
            f"    n_trades={m.get('n_trades')} sharpe_ann={m.get('sharpe_annualized')} "
            f"ret={m.get('total_return_pct')}% PSR={m.get('psr')}",
            file=sys.stderr,
        )
    elapsed = time.time() - t0
    print(f"  total elapsed: {elapsed:.1f}s")

    # Save JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "ablation_b1_cross_sectional.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "B.1 — cross-sectional rank vs absolute",
        "spec": spec_name,
        "params": {"period": args.period, "top": args.top},
        "results": results,
    }
    with open(out_json, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown comparison
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = DOCS_DIR / "ABLATION_B1_CROSS_SECTIONAL.md"
    lines = []
    lines.append("# Ablation B.1 — Cross-Sectional Rank vs Absolute Threshold")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Spec: `{spec_name}` ({len(universe)} ticker resolved)")
    lines.append("")
    lines.append("Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.1.")
    lines.append("")
    lines.append("## Risultati")
    lines.append("")
    lines.append(
        "| Configurazione | N trades | Sharpe ann | Total ret % | "
        "Max DD % | Win rate | PSR | Sharpe per-trade |"
    )
    lines.append("|----------------|----------|------------|-------------|----------|----------|-----|------------------|")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for label, _, _ in runs:
        m = results[label]
        lines.append(
            f"| {label} | {m.get('n_trades', '—')} | "
            f"{_f(m.get('sharpe_annualized'))} | "
            f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
            f"{_f(m.get('max_drawdown_pct'), '{:.2f}')} | "
            f"{_f(m.get('win_rate'), '{:.3f}')} | "
            f"{_f(m.get('psr'))} | "
            f"{_f(m.get('sharpe_per_trade'))} |"
        )
    lines.append("")

    # Compute deltas vs baseline
    baseline = results.get("baseline_v2_absolute_60", {})
    lines.append("## Δ vs baseline absolute_60")
    lines.append("")
    lines.append(
        "| Run | Δ Sharpe ann | Δ Total ret % | Δ N trades |"
    )
    lines.append("|-----|--------------|---------------|------------|")
    for label, _, _ in runs:
        if label == "baseline_v2_absolute_60":
            continue
        r = results[label]
        d_sharpe = (
            (r.get("sharpe_annualized") or 0) - (baseline.get("sharpe_annualized") or 0)
        )
        d_ret = (
            (r.get("total_return_pct") or 0) - (baseline.get("total_return_pct") or 0)
        )
        d_n = (r.get("n_trades") or 0) - (baseline.get("n_trades") or 0)
        lines.append(
            f"| {label} | {d_sharpe:+.3f} | {d_ret:+.2f} | {d_n:+d} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "- **Δ Sharpe positivo** = cross-sectional aggiunge edge"
    )
    lines.append(
        "- **N trade ridotto** atteso (filter più restrittivo) — verifica che "
        "Sharpe migliora abbastanza per compensare meno diversificazione temporale"
    )
    lines.append(
        "- **Top quintile (P80) vs top tercile (P67)**: tradeoff edge vs n_trade. "
        "Top decile (P90) può fallire per insufficient samples"
    )
    lines.append("")
    lines.append("## Decision rule SIGNAL_ROADMAP B.6")
    lines.append("")
    lines.append(
        "Mantieni cross-sectional in default solo se delta Sharpe > +0.10 "
        "AND DSR p < 0.10 vs baseline. Altrimenti opzionale via flag."
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
