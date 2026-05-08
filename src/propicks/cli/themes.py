"""CLI thin wrapper per Thematic ETF (sub-industry, cross-sector tilts).

Esempi:
    propicks-themes LOCK.MI                 # singolo
    propicks-themes SMH XBI CIBR            # batch ranking
    propicks-themes --rank                  # universo intero ordinato
    propicks-themes --rank --region WORLD   # filtra per region
    propicks-themes --rank --theme cybersecurity
    propicks-themes LOCK.MI --validate      # AI validation
    propicks-themes LOCK.MI --json
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.config import (
    THEMATIC_CORR_KILL_THRESHOLD,
    THEMATIC_MAX_POSITIONS,
    THEMATIC_PARENT_AGGREGATE_CAP_PCT,
    THEMATIC_STOP_LOSS_PCT,
)
from propicks.domain.thematic_scoring import analyze_theme, rank_universe
from propicks.domain.thematic_universe import list_themes


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "-"


def _regime_row(regime: dict | None) -> str:
    if not regime:
        return "n/a (dati weekly insufficienti)"
    gate = "✓ ENTRY OK" if regime["entry_allowed"] else "✗ NO ENTRY"
    return (
        f"{regime['regime']} ({regime['regime_code']}/5)  {gate}  "
        f"| trend {regime['trend']}/{regime['trend_strength']} "
        f"| ADX {regime['adx']} | RSI(w) {regime['rsi']}"
    )


def print_ranking_table(ranked: list[dict]) -> None:
    headers = [
        "#",
        "Ticker",
        "Theme",
        "Parent",
        "Score",
        "RS-vs-P",
        "Mom",
        "Trd",
        "P-Reg",
        "Corr",
        "Perf 3M",
        "Price",
        "Class.",
    ]
    rows = []
    for r in ranked:
        s = r["scores"]
        rs = r.get("rs_vs_parent", {})
        rs_ratio = rs.get("rs_ratio")
        rs_ratio_str = f"{rs_ratio:.3f}" if isinstance(rs_ratio, (int, float)) else "-"  # noqa: F841
        corr = r.get("correlation_with_parent")
        corr_str = f"{corr:.2f}" if isinstance(corr, (int, float)) else "-"
        flag = ""
        if r.get("corr_kill_applied"):
            flag = " ⚠C"
        elif r.get("regime_gate_applied"):
            flag = " ⚠R"
        rows.append([
            r["rank"],
            r["ticker"],
            r["theme_label"],
            r["parent_ticker"],
            f"{r['score_composite']:.1f}{flag}",
            f"{s['rs_vs_parent']:.0f}",
            f"{s['abs_momentum']:.0f}",
            f"{s['trend']:.0f}",
            f"{s['parent_regime_fit']:.0f}",
            corr_str,
            _fmt_pct(r.get("perf_3m")),
            f"{r['price']:.2f}",
            r["classification"].split(" — ")[0],
        ])
    print(tabulate(rows, headers=headers, tablefmt="github"))
    if any(r.get("corr_kill_applied") for r in ranked):
        print(f"\n  ⚠C = corr-kill triggered (corr ≥ {THEMATIC_CORR_KILL_THRESHOLD})")
    if any(r.get("regime_gate_applied") for r in ranked):
        print("  ⚠R = regime gate (BEAR/STRONG_BEAR cap)")


def print_detail(r: dict) -> None:
    rs = r.get("rs_vs_parent", {})
    trend = r.get("trend", {})
    regime = r.get("regime")
    header = [
        ["Ticker", f"{r['ticker']}  ({r['name']})"],
        ["Theme / Parent", f"{r['theme_label']}  →  {r['parent_ticker']} ({r['parent_sector_key']})"],
        ["Region", r.get("region", "-")],
        ["Prezzo", f"{r['price']:.2f}"],
        ["Regime weekly", _regime_row(regime)],
        ["Perf 1w / 1m / 3m", f"{_fmt_pct(r.get('perf_1w'))}  /  {_fmt_pct(r.get('perf_1m'))}  /  {_fmt_pct(r.get('perf_3m'))}"],
        ["RS-vs-parent ratio / slope", f"{rs.get('rs_ratio')} / {rs.get('rs_slope')}"],
        ["Corr 60d con parent", r.get("correlation_with_parent")],
        ["EMA30w (trend)", f"{trend.get('ema_value')}  (slope {trend.get('ema_slope')})"],
        [f"Stop suggerito (-{int(THEMATIC_STOP_LOSS_PCT * 100)}%)", f"{r['stop_suggested']:.2f}"],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    s = r["scores"]
    score_rows = [
        ["RS vs parent       (50%)", f"{s['rs_vs_parent']:.1f} / 100"],
        ["Abs momentum 3M    (25%)", f"{s['abs_momentum']:.1f} / 100"],
        ["Trend EMA30w       (15%)", f"{s['trend']:.1f} / 100"],
        ["Parent regime fit  (10%)", f"{s['parent_regime_fit']:.1f} / 100"],
        ["─" * 28, "─" * 14],
        ["Composite RAW", f"{r['score_composite_raw']:.1f} / 100"],
        ["Composite post-corr", f"{r['score_composite_post_corr']:.1f} / 100"],
        ["Composite FINAL", f"{r['score_composite']:.1f} / 100"],
        ["Corr kill applied", r["corr_kill_applied"]],
        ["Regime gate applied", r["regime_gate_applied"]],
        ["Classificazione", r["classification"]],
    ]
    print(tabulate(score_rows, tablefmt="simple"))
    print()
    print(
        f"Bucket constraint: max {THEMATIC_MAX_POSITIONS} tematici aperti, "
        f"weight(theme) + weight(parent_ETF) ≤ {THEMATIC_PARENT_AGGREGATE_CAP_PCT * 100:.0f}%"
    )


def print_ai_verdict(v: dict) -> None:
    print()
    print("=" * 70)
    tag = " (cache)" if v.get("_cache_hit") else ""
    print(f"CLAUDE THEMATIC VALIDATION{tag}")
    print("=" * 70)
    header = [
        ["Verdict", v["verdict"]],
        ["Conviction", f"{v['conviction_score']}/10"],
        ["Theme stage", v.get("theme_stage", "-")],
        ["Alternative", v.get("alternative_ticker") or "-"],
        ["Entry tactic", v.get("entry_tactic", "-")],
        ["Horizon", f"{v.get('time_horizon_weeks', '-')} weeks"],
        ["Alignment w/ parent", v.get("alignment_with_parent", "-")],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    print("Summary:", v.get("thematic_summary", "-"))
    print()

    cbd = v.get("confidence_by_dimension") or {}
    if cbd:
        rows = [[k.replace("_", " ").title(), f"{val}/10"] for k, val in cbd.items()]
        print("Confidence by dimension:")
        print(tabulate(rows, tablefmt="simple"))
        print()

    def _bullets(title: str, items: list[str] | None) -> None:
        if not items:
            return
        print(f"{title}:")
        for x in items:
            print(f"  - {x}")

    _bullets("Catalysts", v.get("catalysts", []))
    print(f"\nCrowding: {v.get('crowding_read', '-')}")
    print(f"Concentration: {v.get('concentration_read', '-')}\n")
    _bullets("Bull case", v.get("bull_case", []))
    _bullets("Bear case", v.get("bear_case", []))
    _bullets("Invalidation triggers", v.get("invalidation_triggers", []))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Thematic ETF scoring (RS-vs-parent + momentum + trend + parent regime fit).",
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Uno o più ticker da analizzare (es. SMH XBI LOCK.MI). Vuoto = richiede --rank.",
    )
    parser.add_argument(
        "--rank",
        action="store_true",
        help="Scora l'intero universo tematico e ordina per composite.",
    )
    parser.add_argument(
        "--region",
        choices=("US", "EU", "WORLD", "ALL"),
        default="ALL",
        help="Filtra per region del listing tematico (default ALL).",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help=(
            "Filtra per theme_label (es. cybersecurity, biotech, semiconductors). "
            f"Disponibili: {', '.join(list_themes())}"
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Valida via Claude (richiede ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--force-validate",
        action="store_true",
        help="Come --validate ma ignora cache + gate.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Salta il dettaglio per-ticker (solo tabella).",
    )
    args = parser.parse_args()

    if not args.tickers and not args.rank:
        parser.error("Specificare almeno un ticker o --rank.")

    results: list[dict]
    if args.rank:
        results = rank_universe(region=args.region, theme_label=args.theme)
    else:
        results = []
        for tk in args.tickers:
            r = analyze_theme(tk)
            if r is not None:
                results.append(r)
        results.sort(key=lambda x: x["score_composite"], reverse=True)
        for i, r in enumerate(results, 1):
            r["rank"] = i

    if not results:
        print("[errore] nessun risultato (universo vuoto, parent indisponibile o filtri stretti).", file=sys.stderr)
        return 1

    verdicts: list[dict] = []
    if args.validate or args.force_validate:
        from propicks.ai import validate_thematic_thesis

        for r in results:
            v = validate_thematic_thesis(r, force=args.force_validate, gate=not args.force_validate)
            if v is not None:
                r["ai_verdict"] = v
                verdicts.append(v)

    if args.json:
        print(json.dumps({"results": results}, indent=2, default=str))
        return 0

    print_ranking_table(results)
    if not args.no_detail and len(results) <= 5:
        for r in results:
            print()
            print("=" * 70)
            print(f"DETAIL — {r['ticker']}")
            print("=" * 70)
            print_detail(r)
            v = r.get("ai_verdict")
            if v is not None:
                print_ai_verdict(v)

    return 0


if __name__ == "__main__":
    sys.exit(main())
