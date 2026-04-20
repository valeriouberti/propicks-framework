"""CLI thin wrapper per lo scoring tecnico.

Esempi:
    propicks-scan AAPL
    propicks-scan AAPL MSFT NVDA --strategy TechTitans
    propicks-scan AAPL --json
    propicks-scan AAPL MSFT --brief
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.domain.scoring import analyze_ticker
from propicks.io.watchlist_store import add_to_watchlist, load_watchlist


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


def _rs_sector_row(rs: dict | None) -> str:
    if not rs or rs.get("rs_ratio") is None:
        return "n/a (solo US + settore mappato)"
    ratio = rs["rs_ratio"]
    slope = rs.get("rs_slope")
    slope_str = f"{slope:+.3f}" if slope is not None else "-"
    return (
        f"vs {rs.get('peer_etf', '?')}  | ratio {ratio:.3f}  "
        f"| slope {slope_str}  | score {rs.get('score', 0):.0f}/100  (informativo)"
    )


def print_analysis(r: dict) -> None:
    """Output dettagliato per un singolo ticker."""
    header = [
        ["Ticker", r["ticker"]],
        ["Strategia", r["strategy"] or "-"],
        ["Regime weekly", _regime_row(r.get("regime"))],
        ["RS vs settore", _rs_sector_row(r.get("rs_vs_sector"))],
        ["Prezzo", f"{r['price']:.2f}"],
        ["EMA fast / slow", f"{r['ema_fast']:.2f} / {r['ema_slow']:.2f}"],
        ["RSI(14)", f"{r['rsi']:.2f}"],
        ["ATR(14)", f"{r['atr']:.2f}  ({(r['atr_pct'] or 0) * 100:.2f}% del prezzo)"],
        [
            "Volume corrente / medio",
            f"{r['current_volume']:,} / {r['avg_volume']:,}  ({r['volume_ratio']}x)",
        ],
        [
            "Massimo 52w",
            f"{r['high_52w']:.2f}  ({(r['distance_from_high_pct'] or 0) * 100:.2f}% di distanza)",
        ],
        [
            "Stop suggerito (-2 ATR)",
            f"{r['stop_suggested']:.2f}  ({(r['stop_pct'] or 0) * 100:+.2f}%)",
        ],
        [
            "Performance 1w / 1m / 3m",
            f"{_fmt_pct(r['perf_1w'])}  /  {_fmt_pct(r['perf_1m'])}  /  {_fmt_pct(r['perf_3m'])}",
        ],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()

    s = r["scores"]
    scores_rows = [
        ["Trend        (peso 25%)", f"{s['trend']:.1f} / 100"],
        ["Momentum     (peso 20%)", f"{s['momentum']:.1f} / 100"],
        ["Volume       (peso 15%)", f"{s['volume']:.1f} / 100"],
        ["Dist. high   (peso 15%)", f"{s['distance_high']:.1f} / 100"],
        ["Volatilità   (peso 10%)", f"{s['volatility']:.1f} / 100"],
        ["MA cross     (peso 15%)", f"{s['ma_cross']:.1f} / 100"],
        ["─" * 24, "─" * 14],
        ["SCORE COMPOSITO", f"{r['score_composite']:.1f} / 100"],
        ["Classificazione", r["classification"]],
    ]
    print(tabulate(scores_rows, tablefmt="simple"))


def print_summary_table(results: list[dict]) -> None:
    """Tabella compatta per batch di ticker."""
    headers = [
        "Ticker",
        "Prezzo",
        "Score",
        "Class.",
        "Trend",
        "Mom.",
        "Vol.",
        "Dist.H",
        "Volat.",
        "MA×",
        "Stop",
        "1m",
    ]
    rows = []
    for r in sorted(results, key=lambda x: x["score_composite"], reverse=True):
        s = r["scores"]
        rows.append(
            [
                r["ticker"],
                f"{r['price']:.2f}",
                f"{r['score_composite']:.1f}",
                r["classification"].split(" — ")[0],
                f"{s['trend']:.0f}",
                f"{s['momentum']:.0f}",
                f"{s['volume']:.0f}",
                f"{s['distance_high']:.0f}",
                f"{s['volatility']:.0f}",
                f"{s['ma_cross']:.0f}",
                f"{r['stop_suggested']:.2f}",
                _fmt_pct(r["perf_1m"]),
            ]
        )
    print(tabulate(rows, headers=headers, tablefmt="github"))


def print_ai_verdict(r: dict) -> None:
    """Output del verdetto AI per un singolo ticker."""
    v = r.get("ai_verdict")
    if v is None:
        return
    print()
    print("=" * 70)
    tag = " (cache)" if v.get("_cache_hit") else ""
    print(f"CLAUDE THESIS VALIDATION — {r['ticker']}{tag}")
    print("=" * 70)

    rr = v.get("reward_risk_ratio")
    rr_str = f"{rr:.2f}:1" if isinstance(rr, (int, float)) else "-"
    header = [
        ["Verdict", v["verdict"]],
        ["Conviction", f"{v['conviction_score']}/10"],
        ["Reward / Risk", rr_str],
        ["Entry tactic", v.get("entry_tactic", "-")],
        ["Time horizon", v.get("time_horizon", "-")],
        ["Invalidation deadline", v.get("invalidation_deadline", "-")],
        ["Alignment w/ technicals", v.get("alignment_with_technicals", "-")],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    print("Thesis:", v["thesis_summary"])
    print()

    cbd = v.get("confidence_by_dimension") or {}
    if cbd:
        rows = [[k.replace("_", " ").title(), f"{val}/10"] for k, val in cbd.items()]
        print("Confidence by dimension:")
        print(tabulate(rows, tablefmt="simple"))
        print()

    def _bullets(title: str, items: list[str]) -> None:
        if not items:
            return
        print(f"{title}:")
        for x in items:
            print(f"  - {x}")

    _bullets("Bull case", v.get("bull_case", []))
    _bullets("Bear case", v.get("bear_case", []))
    _bullets("Key catalysts", v.get("key_catalysts", []))
    _bullets("Key risks", v.get("key_risks", []))
    _bullets("Invalidation triggers", v.get("invalidation_triggers", []))

    if v.get("stop_rationale") or v.get("target_rationale"):
        print()
        if v.get("stop_rationale"):
            print(f"Stop rationale:   {v['stop_rationale']}")
        if v.get("target_rationale"):
            print(f"Target rationale: {v['target_rationale']}")

    adj = v.get("suggested_adjustments") or {}
    if any(adj.get(k) is not None for k in ("stop", "target", "size_multiplier")):
        print()
        print("Suggested adjustments:")
        for k in ("stop", "target", "size_multiplier"):
            if adj.get(k) is not None:
                print(f"  - {k}: {adj[k]}")


def print_tradingview_block(r: dict) -> None:
    """Blocco con i numeri da incollare negli input del Pine daily.

    Usa target da Claude se disponibile, altrimenti lo omette
    (il Pine accetta 0 come "disabled" ma mostrarlo vuoto è più chiaro).
    """
    price = r["price"]
    v = r.get("ai_verdict") or {}
    adj = v.get("suggested_adjustments") or {}

    stop = adj.get("stop") if isinstance(adj.get("stop"), (int, float)) else r["stop_suggested"]
    target = adj.get("target") if isinstance(adj.get("target"), (int, float)) else None

    print()
    print("=" * 70)
    print(f"TRADINGVIEW PINE INPUTS — {r['ticker']}")
    print("=" * 70)
    print('Apri il Pine "AI Trading System — Daily" → Settings → Position:')
    print(f"  Entry Price:   {price:.2f}")
    print(f"  Stop Loss:     {stop:.2f}")
    if target is not None:
        print(f"  Target:        {target:.2f}")
    else:
        print("  Target:        -  (Claude non ha suggerito un target)")
    print()


def print_copy_paste(results: list[dict]) -> None:
    """Blocco pronto da incollare nel prompt Claude 3A."""
    print()
    print("=" * 70)
    print("COPIA/INCOLLA per prompt Claude 3A")
    print("=" * 70)
    for r in results:
        s = r["scores"]
        strategy = r["strategy"] or "N/A"
        print(
            f"TICKER: {r['ticker']}  |  STRATEGIA: {strategy}\n"
            f"PREZZO: {r['price']:.2f}  |  STOP SUGG: {r['stop_suggested']:.2f} "
            f"({(r['stop_pct'] or 0) * 100:+.2f}%)\n"
            f"SCORE TECNICO: {r['score_composite']:.1f}/100 ({r['classification']})\n"
            f"  Trend {s['trend']:.0f} | Mom {s['momentum']:.0f} | "
            f"Vol {s['volume']:.0f} | DistH {s['distance_high']:.0f} | "
            f"Volat {s['volatility']:.0f} | MA× {s['ma_cross']:.0f}\n"
            f"RSI {r['rsi']:.1f} | ATR {(r['atr_pct'] or 0) * 100:.2f}% | "
            f"Vol×{r['volume_ratio']} | DistHigh {(r['distance_from_high_pct'] or 0) * 100:.2f}%\n"
            f"Perf: 1w {_fmt_pct(r['perf_1w'])}  1m {_fmt_pct(r['perf_1m'])}  "
            f"3m {_fmt_pct(r['perf_3m'])}"
        )
        print("-" * 70)


def _auto_watchlist_actionable(results: list[dict]) -> None:
    """Aggiunge i ticker classe A e B alla watchlist e stampa le modifiche su stderr.

    Policy:
    - Classe A (≥75, AZIONE IMMEDIATA): target_entry = current_price per le
      nuove entry → distanza 0% → immediatamente READY quando ricontrolli status.
      Per entry già esistenti con target, il target viene preservato (non
      sovrascriviamo né input manuali né target settati da scan precedenti).
    - Classe B (60-74, WATCHLIST): aggiunta senza target, il trader lo imposta
      manualmente quando ha un livello preciso (pullback/breakout).
    - Classe C/D: skip per design — rumore se entrassero.

    Disabilitabile con --no-watchlist.
    """
    actionable = [
        r for r in results
        if r.get("classification", "").startswith(("A", "B"))
    ]
    if not actionable:
        return

    wl = load_watchlist()
    added: list[str] = []
    updated: list[str] = []
    for r in actionable:
        classification = r.get("classification", "")
        is_class_a = classification.startswith("A")
        existing = wl.get("tickers", {}).get(r["ticker"].upper())
        # Solo per classe A nuove (o classe A senza target già settato):
        # target = current price. Per entry esistenti con target → preserva.
        if is_class_a and not (existing and existing.get("target_entry")):
            target = round(r["price"], 2)
        else:
            target = None  # add_to_watchlist preserva l'esistente se None
        regime = r.get("regime") or {}
        _, is_new = add_to_watchlist(
            wl,
            r["ticker"],
            target_entry=target,
            score_at_add=r.get("score_composite"),
            regime_at_add=regime.get("regime"),
            classification_at_add=classification,
            source="auto_scan",
        )
        (added if is_new else updated).append(r["ticker"])

    msg_parts = []
    if added:
        msg_parts.append(f"aggiunti {', '.join(added)}")
    if updated:
        msg_parts.append(f"aggiornati {', '.join(updated)}")
    print(f"[watchlist] auto-update classe A+B: {'; '.join(msg_parts)}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scoring tecnico 0-100 per uno o più ticker (yfinance).",
    )
    parser.add_argument("tickers", nargs="+", help="Uno o più ticker (es. AAPL MSFT ENI.MI)")
    parser.add_argument("--strategy", default=None, help="Nome della strategia Pro Picks")
    parser.add_argument("--json", action="store_true", help="Output in formato JSON")
    parser.add_argument("--brief", action="store_true", help="Solo tabella riassuntiva")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Valida la tesi via Claude (richiede ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--force-validate",
        action="store_true",
        help="Come --validate, ma ignora cache e gate di score",
    )
    parser.add_argument(
        "--no-watchlist",
        action="store_true",
        help="Non aggiungere automaticamente i ticker classe A/B alla watchlist",
    )
    args = parser.parse_args()

    results: list[dict] = []
    for t in args.tickers:
        r = analyze_ticker(t, strategy=args.strategy)
        if r is not None:
            results.append(r)

    if not results:
        return 1

    if args.validate or args.force_validate:
        from propicks.ai import validate_thesis

        for r in results:
            verdict = validate_thesis(
                r,
                force=args.force_validate,
                gate=not args.force_validate,
            )
            if verdict is not None:
                r["ai_verdict"] = verdict

    if not args.no_watchlist:
        _auto_watchlist_actionable(results)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    if args.brief:
        print_summary_table(results)
        return 0

    if len(results) == 1:
        print_analysis(results[0])
        print_ai_verdict(results[0])
        print_tradingview_block(results[0])
    else:
        print_summary_table(results)
        for r in results:
            print_ai_verdict(r)
            print_tradingview_block(r)
    # print_copy_paste(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
