#!/usr/bin/env python3
"""Ablation B.2 — Earnings revision overlay vs baseline momentum.

Confronta:

- **Run 0 (baseline)**: classic momentum composite (6 sub-score)
- **Run 1 (B.2 overlay 0.15)**: classic × 0.85 + earnings_revision × 0.15
- **Run 2 (B.2 overlay 0.20)**: classic × 0.80 + earnings_revision × 0.20
- **Run 3 (B.2 overlay 0.30)**: classic × 0.70 + earnings_revision × 0.30

## Caveat critico

`yfinance` espone earnings revision metrics solo come **current snapshot**.
Non ha trend storico delle revisioni quarter-by-quarter. Questo significa
che in backtest historical il `earnings_score` è:

- **STATICO** per tutti i bar dello stesso ticker (stesso snapshot oggi)
- Effectively un **ticker-level prior** (ticker con good revision history
  oggi → boost in tutto il backtest)

Per signal validation propria di "earnings revision momentum" alpha
(Chan-Jegadeesh-Lakonishok 1996) servirebbe IBES historical (paid). Questo
ablation testa il prior current — utile come **filter quality** ma NON come
alpha-generator dinamico.

## Note signal-side

In live signal mode (CLI propicks-momentum), il earnings overlay aggiorna
ogni 7gg (TTL cache) e quindi cambia nel tempo. Quindi in produzione
l'effetto è dinamico. Questo backtest NON cattura quel comportamento.
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


def _build_scoring_with_overlay(earnings_scores: dict[str, float | None], weight: float):
    """Crea scoring_fn che combina classic momentum + earnings overlay.

    ``earnings_scores`` = dict {ticker: score [0,100] | None}, pre-computed
    una volta (current snapshot per ticker).
    """
    from scripts.baseline_backtest import _build_momentum_scoring_fn
    from propicks.domain.scoring import combine_with_earnings_revision

    base_fn = _build_momentum_scoring_fn()

    def _fn(ticker, hist_slice):
        base = base_fn(ticker, hist_slice)
        if base is None:
            return None
        if weight == 0.0:
            return base
        earn = earnings_scores.get(ticker.upper())
        return combine_with_earnings_revision(base, earn, weight=weight)

    return _fn


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablation B.2 earnings revision")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=60.0)
    parser.add_argument("--cross-sectional", action="store_true",
                        help="Use cross-sectional rank (combina con B.1)")
    args = parser.parse_args()

    # Lazy import
    from scripts.baseline_backtest import _fetch_universe, _resolve_universe
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    from propicks.io.index_membership import build_universe_provider
    from propicks.market.yfinance_client import get_earnings_revision_metrics
    from propicks.domain.earnings_revision import score_earnings_revision

    spec_name = f"momentum_sp500_top{args.top}_{args.period}"
    print(f"=== Ablation B.2 earnings_revision — {spec_name} ===")
    if args.cross_sectional:
        print("  cross-sectional mode ON (combinata con B.1)")

    # 1. Universe + fetch OHLCV (una volta)
    tickers = _resolve_universe(spec_name, args.top)
    universe = _fetch_universe(tickers, args.period)
    if not universe:
        print("[errore] universe vuoto", file=sys.stderr)
        return 1
    print(f"  universe: {len(universe)} ticker fetched", file=sys.stderr)

    # 2. Earnings revision per ticker (current snapshot, cached)
    print("  [earnings] fetching revision metrics per ticker...", file=sys.stderr)
    earnings_scores: dict[str, float | None] = {}
    for ticker in universe.keys():
        try:
            m = get_earnings_revision_metrics(ticker)
            score = score_earnings_revision(
                m.get("avg_surprise_4q"),
                m.get("surprise_trend"),
                m.get("net_revisions_30d"),
                m.get("growth_consensus"),
                m.get("n_analysts"),
            )
            earnings_scores[ticker] = score
        except Exception as exc:
            print(f"    ✗ {ticker}: {exc}", file=sys.stderr)
            earnings_scores[ticker] = None

    # Print earnings score distribution
    valid = [s for s in earnings_scores.values() if s is not None]
    if valid:
        import statistics
        print(
            f"  earnings_scores: n={len(valid)}, "
            f"mean={statistics.mean(valid):.1f}, "
            f"min={min(valid):.1f}, max={max(valid):.1f}",
            file=sys.stderr,
        )

    # 3. Run backtest per ogni weight
    runs = [
        ("baseline_no_overlay", 0.0),
        ("b2_overlay_0.15", 0.15),
        ("b2_overlay_0.20", 0.20),
        ("b2_overlay_0.30", 0.30),
    ]

    provider = build_universe_provider("sp500")
    results: dict = {}
    t0 = time.time()
    for label, weight in runs:
        print(f"  [{label}] weight={weight}", file=sys.stderr)
        config = BacktestConfig(
            initial_capital=10_000.0,
            score_threshold=args.threshold,
            use_earnings_gate=False,
            strategy_tag="momentum",
            use_cross_sectional_rank=args.cross_sectional,
        )
        scoring_fn = _build_scoring_with_overlay(earnings_scores, weight)
        state = simulate_portfolio(
            universe=universe,
            scoring_fn=scoring_fn,
            config=config,
            universe_provider=provider,
        )
        m = compute_portfolio_metrics(state)
        m["weight"] = weight
        results[label] = m
        print(
            f"    n_trades={m.get('n_trades')} sharpe_ann={m.get('sharpe_annualized')} "
            f"ret={m.get('total_return_pct')}% PSR={m.get('psr')}",
            file=sys.stderr,
        )
    elapsed = time.time() - t0
    print(f"  total elapsed: {elapsed:.1f}s")

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "ablation_b2_earnings_revision.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "B.2 — earnings revision overlay",
        "spec": spec_name,
        "params": {
            "period": args.period, "top": args.top,
            "threshold": args.threshold,
            "cross_sectional": args.cross_sectional,
        },
        "earnings_scores": earnings_scores,
        "results": results,
    }
    with open(out_json, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = DOCS_DIR / "ABLATION_B2_EARNINGS_REVISION.md"
    lines = []
    lines.append("# Ablation B.2 — Earnings Revision Overlay")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Spec: `{spec_name}` ({len(universe)} ticker)")
    lines.append(f"Cross-sectional: {args.cross_sectional}")
    lines.append("")
    lines.append("Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.2.")
    lines.append("")
    lines.append("## Caveat dataset")
    lines.append("")
    lines.append(
        "yfinance espone earnings revision metrics solo come **current snapshot**. "
        "Backtest historical applica lo *stesso* earnings_score per tutti i bar "
        "dello stesso ticker. Effetto = ticker-level prior. Per alpha "
        "Chan-Jegadeesh-Lakonishok dinamico serve IBES historical (paid)."
    )
    lines.append("")
    lines.append("## Risultati")
    lines.append("")
    lines.append(
        "| Configurazione | Weight | N trades | Sharpe ann | Total ret % | "
        "Max DD % | Win rate | PSR |"
    )
    lines.append("|----------------|--------|----------|------------|-------------|----------|----------|-----|")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for label, weight in runs:
        m = results[label]
        lines.append(
            f"| {label} | {weight:.2f} | {m.get('n_trades', '—')} | "
            f"{_f(m.get('sharpe_annualized'))} | "
            f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
            f"{_f(m.get('max_drawdown_pct'), '{:.2f}')} | "
            f"{_f(m.get('win_rate'), '{:.3f}')} | "
            f"{_f(m.get('psr'))} |"
        )
    lines.append("")

    baseline = results.get("baseline_no_overlay", {})
    lines.append("## Δ vs baseline (no overlay)")
    lines.append("")
    lines.append(
        "| Run | Δ Sharpe ann | Δ Total ret % | Δ N trades |"
    )
    lines.append("|-----|--------------|---------------|------------|")
    for label, weight in runs:
        if label == "baseline_no_overlay":
            continue
        r = results[label]
        d_sharpe = (
            (r.get("sharpe_annualized") or 0) - (baseline.get("sharpe_annualized") or 0)
        )
        d_ret = (
            (r.get("total_return_pct") or 0) - (baseline.get("total_return_pct") or 0)
        )
        d_n = (r.get("n_trades") or 0) - (baseline.get("n_trades") or 0)
        lines.append(f"| {label} | {d_sharpe:+.3f} | {d_ret:+.2f} | {d_n:+d} |")
    lines.append("")

    # Earnings score distribution
    lines.append("## Earnings score distribution")
    lines.append("")
    lines.append("| Ticker | Score |")
    lines.append("|--------|-------|")
    for tk in sorted(earnings_scores.keys()):
        s = earnings_scores[tk]
        lines.append(f"| {tk} | {_f(s, '{:.1f}')} |")
    lines.append("")
    lines.append("## Decision rule SIGNAL_ROADMAP B.6")
    lines.append("")
    lines.append(
        "Mantieni overlay solo se Δ Sharpe > +0.10 + DSR p < 0.10. "
        "Considerare caveat ticker-level-prior — alpha vero dipende da dataset "
        "historical revisions, non disponibile su yfinance."
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
