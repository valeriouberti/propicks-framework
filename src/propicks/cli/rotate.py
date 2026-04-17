"""CLI thin wrapper per la rotazione settoriale ETF.

Esempi:
    propicks-rotate                        # US universe (SPDR Select Sector), top 3
    propicks-rotate --top 5                # US, top 5
    propicks-rotate --region EU            # SPDR UCITS su Xetra (ZPD*.DE)
    propicks-rotate --region WORLD         # Xtrackers MSCI World sector (XDW*/XWTS/XZRE)
    propicks-rotate --allocate             # include proposta allocazione
    propicks-rotate --validate             # validazione macro via Claude
    propicks-rotate --json                 # output JSON
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.config import (
    ETF_MAX_AGGREGATE_EXPOSURE_PCT,
    ETF_MAX_POSITION_SIZE_PCT,
    ETF_TOP_N_DEFAULT,
)
from propicks.domain.etf_scoring import rank_universe, suggest_allocation


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


def print_rotation_table(ranked: list[dict]) -> None:
    """Tabella compatta con ranking completo e indicatori chiave."""
    headers = [
        "#",
        "Ticker",
        "Sector",
        "Score",
        "RS",
        "Reg",
        "Mom",
        "Trd",
        "RS-ratio",
        "Perf 3M",
        "Price",
        "Class.",
    ]
    rows = []
    for r in ranked:
        s = r["scores"]
        rs_ratio = r.get("rs", {}).get("rs_ratio")
        rs_ratio_str = f"{rs_ratio:.3f}" if isinstance(rs_ratio, (int, float)) else "-"
        cap = " *" if r.get("regime_cap_applied") else ""
        rows.append([
            r["rank"],
            r["ticker"],
            r["sector_key"],
            f"{r['score_composite']:.1f}{cap}",
            f"{s['rs']:.0f}",
            f"{s['regime_fit']:.0f}",
            f"{s['abs_momentum']:.0f}",
            f"{s['trend']:.0f}",
            rs_ratio_str,
            _fmt_pct(r.get("perf_3m")),
            f"{r['price']:.2f}",
            r["classification"].split(" — ")[0],
        ])
    print(tabulate(rows, headers=headers, tablefmt="github"))
    has_cap = any(r.get("regime_cap_applied") for r in ranked)
    if has_cap:
        print("\n  * = score capped dal regime (non-favored in BEAR/STRONG_BEAR)")


def print_top_detail(r: dict) -> None:
    """Dettaglio del top-pick con sub-score e livelli."""
    rs = r.get("rs", {})
    trend = r.get("trend", {})
    regime = r.get("regime")
    header = [
        ["Ticker", f"{r['ticker']}  ({r['name']})"],
        ["Region / Sector", f"{r['region']}  /  {r['sector_key']}"],
        ["Prezzo", f"{r['price']:.2f}"],
        ["Regime weekly", _regime_row(regime)],
        ["Favored nel regime", "SI" if r.get("favored_in_regime") else "no"],
        ["Perf 1w / 1m / 3m", f"{_fmt_pct(r.get('perf_1w'))}  /  {_fmt_pct(r.get('perf_1m'))}  /  {_fmt_pct(r.get('perf_3m'))}"],
        ["RS ratio / slope", f"{rs.get('rs_ratio')} / {rs.get('rs_slope')}"],
        ["EMA30w (trend)", f"{trend.get('ema_value')}  (slope {trend.get('ema_slope')})"],
        ["Stop suggerito (-5%)", f"{r['stop_suggested']:.2f}"],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    s = r["scores"]
    score_rows = [
        ["RS            (peso 40%)", f"{s['rs']:.1f} / 100"],
        ["Regime fit    (peso 30%)", f"{s['regime_fit']:.1f} / 100"],
        ["Abs momentum  (peso 20%)", f"{s['abs_momentum']:.1f} / 100"],
        ["Trend         (peso 10%)", f"{s['trend']:.1f} / 100"],
        ["─" * 24, "─" * 14],
        ["Composite RAW", f"{r['score_composite_raw']:.1f} / 100"],
        ["Composite (post-cap)", f"{r['score_composite']:.1f} / 100"],
        ["Classificazione", r["classification"]],
    ]
    print(tabulate(score_rows, tablefmt="simple"))


def print_allocation(alloc: dict) -> None:
    print()
    print("=" * 70)
    print("PROPOSTA ALLOCAZIONE")
    print("=" * 70)
    positions = alloc.get("positions", [])
    if not positions:
        print(alloc.get("note", "Nessuna allocazione proposta."))
        return
    rows = [
        [
            p["ticker"],
            p["sector_key"],
            p["classification"].split(" — ")[0],
            f"{p['allocation_pct'] * 100:.1f}%",
            f"{p['price']:.2f}",
            f"{p['stop_suggested']:.2f}",
            f"{p['score']:.1f}",
        ]
        for p in positions
    ]
    headers = ["Ticker", "Sector", "Class.", "Alloc", "Price", "Stop", "Score"]
    print(tabulate(rows, headers=headers, tablefmt="github"))
    print()
    print(
        f"Esposizione aggregata sector ETF: {alloc['aggregate_pct'] * 100:.1f}% "
        f"(cap {ETF_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%, "
        f"per-ETF {ETF_MAX_POSITION_SIZE_PCT * 100:.0f}%)"
    )
    if alloc.get("note"):
        print(f"Nota: {alloc['note']}")


def print_ai_verdict(v: dict) -> None:
    print()
    print("=" * 70)
    tag = " (cache)" if v.get("_cache_hit") else ""
    print(f"CLAUDE ROTATION VALIDATION{tag}")
    print("=" * 70)
    header = [
        ["Verdict", v["verdict"]],
        ["Conviction", f"{v['conviction_score']}/10"],
        ["Top sector", v.get("top_sector_verdict", "-")],
        ["Alternative", v.get("alternative_sector") or "-"],
        ["Stage", v.get("stage", "-")],
        ["Entry tactic", v.get("entry_tactic", "-")],
        ["Rebalance horizon", f"{v.get('rebalance_horizon_weeks', '-')} weeks"],
        ["Alignment w/ ranking", v.get("alignment_with_ranking", "-")],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    print("Summary:", v.get("rotation_summary", "-"))
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

    _bullets("Macro drivers", v.get("macro_drivers", []))
    print(f"\nBreadth: {v.get('breadth_read', '-')}")
    print(f"Positioning: {v.get('positioning_read', '-')}\n")
    _bullets("Bull case", v.get("bull_case", []))
    _bullets("Bear case", v.get("bear_case", []))
    _bullets("Invalidation triggers", v.get("invalidation_triggers", []))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ranking rotazione ETF settoriali (RS + regime + momentum + trend).",
    )
    parser.add_argument(
        "--region",
        choices=("US", "EU", "WORLD", "ALL"),
        default="US",
        help=(
            "Universo: SPDR US (XL*), SPDR UCITS (ZPD*.DE), "
            "Xtrackers MSCI World (XDW*/XWTS/XZRE), o ALL."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=ETF_TOP_N_DEFAULT,
        help=f"Numero di settori in proposta allocazione (default {ETF_TOP_N_DEFAULT}).",
    )
    parser.add_argument(
        "--allocate",
        action="store_true",
        help="Stampa anche la proposta di allocazione sui top-N.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Valida la rotazione via Claude (richiede ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--force-validate",
        action="store_true",
        help="Come --validate ma ignora cache e skip automatico in STRONG_BEAR.",
    )
    parser.add_argument("--json", action="store_true", help="Output in formato JSON.")
    parser.add_argument(
        "--no-top-detail",
        action="store_true",
        help="Salta il dettaglio del top-pick (solo tabella).",
    )
    args = parser.parse_args()

    ranked = rank_universe(region=args.region)
    if not ranked:
        print("[errore] universo vuoto o benchmark non disponibile.", file=sys.stderr)
        return 1

    allocation = None
    if args.allocate or args.validate or args.force_validate:
        allocation = suggest_allocation(
            ranked,
            top_n=args.top,
            max_per_etf_pct=ETF_MAX_POSITION_SIZE_PCT,
            max_aggregate_pct=ETF_MAX_AGGREGATE_EXPOSURE_PCT,
        )

    verdict = None
    if args.validate or args.force_validate:
        from propicks.ai import validate_rotation

        verdict = validate_rotation(
            ranked,
            allocation=allocation,
            region=args.region,
            force=args.force_validate,
        )

    if args.json:
        out = {"ranked": ranked}
        if allocation is not None:
            out["allocation"] = allocation
        if verdict is not None:
            out["ai_verdict"] = verdict
        print(json.dumps(out, indent=2, default=str))
        return 0

    print_rotation_table(ranked)
    if not args.no_top_detail:
        print()
        print("=" * 70)
        print(f"TOP PICK — {ranked[0]['ticker']}")
        print("=" * 70)
        print_top_detail(ranked[0])

    if allocation is not None:
        print_allocation(allocation)

    if verdict is not None:
        print_ai_verdict(verdict)

    return 0


if __name__ == "__main__":
    sys.exit(main())
