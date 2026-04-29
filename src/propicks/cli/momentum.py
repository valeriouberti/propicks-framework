"""CLI thin wrapper per lo scoring MOMENTUM (trend/quality stock screener).

Esempi:
    propicks-momentum AAPL                              # singolo
    propicks-momentum AAPL MSFT NVDA --strategy TechTitans
    propicks-momentum AAPL --validate                   # gate score≥60 + regime≥NEUTRAL
    propicks-momentum AAPL --force-validate             # bypassa gate + cache
    propicks-momentum AAPL --json --brief --no-watchlist
    propicks-momentum --discover-sp500 --top 10
    propicks-momentum --discover-sp500 --top 5 --validate --min-score 75
    propicks-momentum --discover-nasdaq --top 10        # ~100 nomi tech US
    propicks-momentum --discover-ftsemib                # 40 large-cap IT
    propicks-momentum --discover-stoxx600 --top 15
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.domain.momentum_discovery import (
    DISCOVERY_DEFAULT_TOP_N,
    DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    DISCOVERY_PREFILTER_RSI_MIN,
    discover_momentum_candidates,
)
from propicks.domain.scoring import analyze_ticker
from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
from propicks.market.index_constituents import (
    INDEX_NAME_FTSEMIB,
    INDEX_NAME_NASDAQ100,
    INDEX_NAME_SP500,
    INDEX_NAME_STOXX600,
    get_index_universe,
    index_label,
)


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


def _earnings_badge(r: dict) -> str:
    """Badge earnings per output CLI momentum.

    - Earnings entro 5gg → 🚨 hard gate (add_position bloccato senza --ignore-earnings)
    - Earnings 6-14gg   → ⚠️  warning
    - Earnings recenti  → 📰 info post-report
    """
    days = r.get("days_to_earnings")
    next_date = r.get("next_earnings_date") or "—"
    if isinstance(days, int):
        if days < 0:
            return f"📰 earnings {abs(days)}gg fa ({next_date})"
        if days <= 5:
            return f"🚨 EARNINGS {days}gg ({next_date}) — add bloccato senza --ignore-earnings"
        if days <= 14:
            return f"⚠️ earnings {days}gg ({next_date})"
        return f"earnings in {days}gg ({next_date})"
    return "—"


def _earnings_short(r: dict) -> str:
    """Badge earnings compatto per summary table."""
    days = r.get("days_to_earnings")
    if not isinstance(days, int):
        return "—"
    if days < 0:
        return f"📰{abs(days)}d"
    if days <= 5:
        return f"🚨{days}d"
    if days <= 14:
        return f"⚠️{days}d"
    return f"{days}d"


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
            f"{r['current_volume']:,} / {r['avg_volume']:,}  "
            + (
                "(ratio n/a — sub-score neutralizzato a 50)"
                if r.get("volume_neutralized")
                else f"({r['volume_ratio']}x)"
            ),
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
        ["Earnings", _earnings_badge(r)],
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
        "Earn.",
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
                _earnings_short(r),
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


def _discovery_progress(stage: str, current: int, total: int, ticker: str) -> None:
    """Progress callback per il discovery: stampa solo ogni 25 ticker per non spammare."""
    if current == total or current % 25 == 0:
        print(
            f"[discovery/{stage}] {current}/{total} ({ticker})",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scoring MOMENTUM 0-100 (trend/quality stock screener). "
            "Cerca setup di accelerazione su titoli in trend up con momentum vivo."
        ),
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help=(
            "Uno o più ticker (es. AAPL MSFT ENI.MI). "
            "Omettere se si usa --discover-sp500 / --discover-ftsemib / --discover-stoxx600."
        ),
    )
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
    discover_group = parser.add_mutually_exclusive_group()
    discover_group.add_argument(
        "--discover-sp500",
        action="store_true",
        help=(
            "Discovery automatico su tutto S&P 500 (~500 nomi US). Pipeline "
            "3-stage: prefilter cheap → full scoring → top N."
        ),
    )
    discover_group.add_argument(
        "--discover-ftsemib",
        action="store_true",
        help="Discovery su FTSE MIB (40 large-cap italiani).",
    )
    discover_group.add_argument(
        "--discover-stoxx600",
        action="store_true",
        help=(
            "Discovery su STOXX Europe 600 (~600 nomi multi-paese — universo "
            "ampio, costo full scoring più alto)."
        ),
    )
    discover_group.add_argument(
        "--discover-nasdaq",
        action="store_true",
        help=(
            "Discovery su Nasdaq-100 (~100 nomi US tech-heavy, overlap con "
            "S&P 500 ma concentrazione tech maggiore)."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DISCOVERY_DEFAULT_TOP_N,
        help=f"Numero massimo di candidati da ritornare (default {DISCOVERY_DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help=(
            "Score composito minimo per inclusione. Default: MIN_SCORE_TECH "
            "(60) per filtrare almeno classe B; usa 75 per solo classe A, 0 per "
            "nessun filtro."
        ),
    )
    parser.add_argument(
        "--prefilter-rsi-min",
        type=float,
        default=DISCOVERY_PREFILTER_RSI_MIN,
        help=f"Soglia RSI minimo prefilter (default {DISCOVERY_PREFILTER_RSI_MIN}).",
    )
    parser.add_argument(
        "--prefilter-max-dist",
        type=float,
        default=DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
        help=(
            f"Distanza massima da 52w-high (frazione, default "
            f"{DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH})."
        ),
    )
    parser.add_argument(
        "--prefilter-cap",
        type=int,
        default=None,
        help=(
            "Cap opzionale sul n. ticker che passano allo stage 2 "
            "(utile per limitare costo full scoring)."
        ),
    )
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Forza re-fetch della lista index da Wikipedia (bypass cache 7gg).",
    )
    args = parser.parse_args()

    # Determina se è in modalità discovery e quale index
    discover_index: str | None = None
    if args.discover_sp500:
        discover_index = INDEX_NAME_SP500
    elif args.discover_ftsemib:
        discover_index = INDEX_NAME_FTSEMIB
    elif args.discover_stoxx600:
        discover_index = INDEX_NAME_STOXX600
    elif args.discover_nasdaq:
        discover_index = INDEX_NAME_NASDAQ100

    # Validation: o ticker espliciti o discovery, ma non entrambi vuoti
    if not args.tickers and discover_index is None:
        parser.error(
            "Specifica almeno un ticker oppure usa "
            "--discover-sp500 / --discover-nasdaq / --discover-ftsemib / --discover-stoxx600."
        )
    if args.tickers and discover_index is not None:
        parser.error(
            "Discovery flags e ticker espliciti sono mutually exclusive: "
            "scegli uno dei due flussi."
        )

    results: list[dict] = []
    if discover_index is not None:
        label = index_label(discover_index)
        try:
            universe = get_index_universe(
                discover_index, force_refresh=args.refresh_universe
            )
        except Exception as exc:
            print(
                f"[errore] impossibile ottenere universo {label}: {exc}",
                file=sys.stderr,
            )
            return 1

        print(
            f"[discovery] universo {label}: {len(universe)} ticker. "
            f"Stage 1 (prefilter RSI>={args.prefilter_rsi_min}, "
            f"dist_from_high<={args.prefilter_max_dist})...",
            file=sys.stderr,
        )
        # Default min_score = MIN_SCORE_TECH (60) per filtrare classe C/D: il
        # discovery di default ritorna solo nomi tradeable (classe A+B).
        from propicks.config import MIN_SCORE_TECH
        effective_min_score = (
            args.min_score if args.min_score is not None else float(MIN_SCORE_TECH)
        )
        out = discover_momentum_candidates(
            universe,
            top_n=args.top,
            rsi_min=args.prefilter_rsi_min,
            max_dist_from_high=args.prefilter_max_dist,
            min_score=effective_min_score,
            strategy=args.strategy,
            prefilter_cap=args.prefilter_cap,
            progress_callback=_discovery_progress,
        )
        print(
            f"[discovery] universe={out['universe_size']} "
            f"prefilter_pass={out['prefilter_pass']} "
            f"scored={out['scored']} "
            f"returned={len(out['candidates'])}",
            file=sys.stderr,
        )
        results = out["candidates"]
    else:
        for t in args.tickers:
            r = analyze_ticker(t, strategy=args.strategy)
            if r is not None:
                results.append(r)

    if not results:
        if discover_index is not None:
            print(
                "[discovery] nessun candidato qualificato dopo full scoring.",
                file=sys.stderr,
            )
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

    # In discovery mode default a summary table: l'analysis dettagliata × 10
    # è troppo rumorosa. Brief flag esplicito forza summary anche su single.
    if args.brief or discover_index is not None:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
