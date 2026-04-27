"""CLI thin wrapper per lo scoring CONTRARIAN (quality-filtered mean reversion).

Parallelo a ``propicks-scan`` (momentum) ma con scoring diverso: cerca setup
oversold su titoli di qualità, non momentum che accelera.

Esempi:
    propicks-contra AAPL
    propicks-contra AAPL MSFT NVDA
    propicks-contra AAPL --validate
    propicks-contra AAPL --json
    propicks-contra AAPL MSFT --brief
    propicks-contra --discover-sp500 --top 10
    propicks-contra --discover-sp500 --top 5 --validate --min-score 60
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.domain.contrarian_discovery import (
    DISCOVERY_DEFAULT_TOP_N,
    DISCOVERY_PREFILTER_ATR_DISTANCE_MIN,
    DISCOVERY_PREFILTER_RSI_MAX,
    discover_contra_candidates,
)
from propicks.domain.contrarian_scoring import analyze_contra_ticker
from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
from propicks.market.index_constituents import get_sp500_universe
from propicks.market.yfinance_client import download_benchmark


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "-"


def _regime_row(regime: dict | None) -> str:
    if not regime:
        return "n/a (dati weekly insufficienti)"
    return (
        f"{regime['regime']} ({regime['regime_code']}/5)  "
        f"| trend {regime['trend']}/{regime['trend_strength']} "
        f"| RSI(w) {regime['rsi']}"
    )


def print_analysis(r: dict) -> None:
    """Output dettagliato per un singolo ticker contrarian."""
    sub = r.get("sub_scores_detail") or {}
    oversold = sub.get("oversold") or {}
    quality = sub.get("quality") or {}
    context = sub.get("market_context") or {}
    reversion = sub.get("reversion") or {}

    header = [
        ["Ticker", r["ticker"]],
        ["Strategia", r.get("strategy", "Contrarian")],
        ["Prezzo", f"{r['price']:.2f}"],
        ["Recent low (5-bar)", f"{r.get('recent_low', r['price']):.2f}"],
        ["Regime weekly", _regime_row(r.get("regime"))],
        ["VIX", f"{r['vix']:.2f}" if r.get("vix") is not None else "n/a"],
        ["", ""],
        [
            "RSI(14)",
            f"{r['rsi']:.2f}  "
            f"{'← OVERSOLD' if r['rsi'] <= 30 else '← warm' if r['rsi'] <= 35 else ''}",
        ],
        [
            "EMA50 daily / distanza",
            f"{r['ema_slow']:.2f}  "
            f"({oversold.get('atr_distance_from_ema', 'n/a')}x ATR sotto)",
        ],
        [
            "EMA200 weekly (quality gate)",
            f"{r['ema_200_weekly'] if r.get('ema_200_weekly') is not None else 'n/a'}  "
            f"{'✓ sopra' if quality.get('above_ema200w') else '✗ SOTTO — quality ROTTA'}",
        ],
        [
            "Sequenza barre rosse",
            f"{r.get('consecutive_down', 0)} consecutive",
        ],
        [
            "Distanza da 52w high",
            _fmt_pct(r.get("distance_from_high_pct")),
        ],
        [
            "ATR(14)",
            f"{r['atr']:.2f}  ({(r['atr_pct'] or 0) * 100:.2f}% del prezzo)",
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
        [
            "Oversold       (peso 40%)",
            f"{s['oversold']:.1f} / 100",
        ],
        [
            "Quality gate   (peso 25%)",
            f"{s['quality']:.1f} / 100  "
            f"{'(GATE BROKEN)' if s['quality'] == 0 else ''}",
        ],
        [
            "Market context (peso 20%)",
            f"{s['market_context']:.1f} / 100  "
            f"({context.get('vix_note', '')})",
        ],
        [
            "Reversion R/R  (peso 15%)",
            f"{s['reversion']:.1f} / 100",
        ],
        ["─" * 28, "─" * 14],
        ["SCORE COMPOSITO", f"{r['score_composite']:.1f} / 100"],
        ["Classificazione", r["classification"]],
    ]
    print(tabulate(scores_rows, tablefmt="simple"))

    print()
    target = r.get("target_suggested")
    price = r.get("price")
    target_valid = (
        isinstance(target, (int, float))
        and isinstance(price, (int, float))
        and target > price
    )
    target_display = (
        f"{target:.2f}"
        if target_valid
        else "—  (setup invalido: price ≥ EMA50, non è mean reversion)"
    )
    rr = reversion.get("rr_ratio")
    rr_display = (
        f"{rr:.2f}:1" if isinstance(rr, (int, float)) and target_valid else "n/a"
    )
    trade_rows = [
        ["Entry (market)", f"{r['price']:.2f}"],
        [
            "Stop (recent_low − 3×ATR)",
            f"{r['stop_suggested']:.2f}  ({(r['stop_pct'] or 0) * 100:+.2f}%)",
        ],
        ["Target (reversion EMA50)", target_display],
        ["R/R teorico", rr_display],
    ]
    print("PARAMETRI DI TRADE CONTRARIAN")
    print(tabulate(trade_rows, tablefmt="simple"))


def print_summary_table(results: list[dict]) -> None:
    """Tabella compatta per batch."""
    headers = [
        "Ticker",
        "Prezzo",
        "Score",
        "Class.",
        "Oversold",
        "Quality",
        "Ctx",
        "R/R",
        "RSI",
        "Dist ATR",
        "Stop",
        "Target",
    ]
    rows = []
    for r in sorted(results, key=lambda x: x["score_composite"], reverse=True):
        s = r["scores"]
        sub = r.get("sub_scores_detail") or {}
        oversold = sub.get("oversold") or {}
        atr_dist = oversold.get("atr_distance_from_ema")
        target = r.get("target_suggested")
        price = r.get("price")
        # Target valido solo se > price (altrimenti price è già sopra EMA50 → no setup)
        target_valid = (
            isinstance(target, (int, float))
            and isinstance(price, (int, float))
            and target > price
        )
        rows.append(
            [
                r["ticker"],
                f"{r['price']:.2f}",
                f"{r['score_composite']:.1f}",
                r["classification"].split(" — ")[0],
                f"{s['oversold']:.0f}",
                f"{s['quality']:.0f}",
                f"{s['market_context']:.0f}",
                f"{s['reversion']:.0f}",
                f"{r['rsi']:.1f}",
                f"{atr_dist:.1f}x" if isinstance(atr_dist, (int, float)) else "—",
                f"{r['stop_suggested']:.2f}"
                if isinstance(r.get("stop_suggested"), (int, float))
                else "—",
                f"{target:.2f}" if target_valid else "—",
            ]
        )
    print(tabulate(rows, headers=headers, tablefmt="github"))


def print_ai_verdict(r: dict) -> None:
    """Output del verdetto AI contrarian."""
    v = r.get("ai_verdict")
    if v is None:
        return
    print()
    print("=" * 70)
    tag = " (cache)" if v.get("_cache_hit") else ""
    print(f"CLAUDE CONTRARIAN VALIDATION — {r['ticker']}{tag}")
    print("=" * 70)

    header = [
        ["Verdict", v["verdict"]],
        ["Flush vs Break", v.get("flush_vs_break", "-")],
        ["Catalyst type", v.get("catalyst_type", "-")],
        ["Conviction", f"{v['conviction_score']}/10"],
        [
            "Reversion target",
            f"{v.get('reversion_target', '-')}"
            if v.get("reversion_target") is not None
            else "-",
        ],
        [
            "Invalidation price",
            f"{v.get('invalidation_price', '-')}"
            if v.get("invalidation_price") is not None
            else "-",
        ],
        ["Time horizon (days)", v.get("time_horizon_days", "-")],
        ["Entry tactic", v.get("entry_tactic", "-")],
    ]
    print(tabulate(header, tablefmt="simple"))
    print()
    print("Thesis:", v.get("thesis_summary", "-"))
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
    _bullets("Key risks", v.get("key_risks", []))
    _bullets("Invalidation triggers", v.get("invalidation_triggers", []))


def _auto_watchlist_actionable(results: list[dict]) -> None:
    """Aggiunge ticker classe A/B alla watchlist con tag source=auto_scan_contra.

    Policy identica a ``scanner._auto_watchlist_actionable`` ma con source
    dedicato per tracciare separatamente le idee generate dalla strategia
    contrarian nell'audit della watchlist.
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
        # Policy contrarian: classe A → target = current_price (setup "ready now").
        # classe B → target = None, il trader imposta livello limit-below se vuole.
        if is_class_a and not (existing and existing.get("target_entry")):
            target = round(r["price"], 2)
        else:
            target = None
        regime = r.get("regime") or {}
        _, is_new = add_to_watchlist(
            wl,
            r["ticker"],
            target_entry=target,
            score_at_add=r.get("score_composite"),
            regime_at_add=regime.get("regime"),
            classification_at_add=classification,
            source="auto_scan_contra",
        )
        (added if is_new else updated).append(r["ticker"])

    msg_parts = []
    if added:
        msg_parts.append(f"aggiunti {', '.join(added)}")
    if updated:
        msg_parts.append(f"aggiornati {', '.join(updated)}")
    print(
        f"[watchlist] auto-update contrarian A+B: {'; '.join(msg_parts)}",
        file=sys.stderr,
    )


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
            "Scoring CONTRARIAN 0-100 (quality-filtered mean reversion). "
            "Cerca setup oversold su titoli di qualità con trend strutturale intatto."
        ),
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help=(
            "Uno o più ticker (es. AAPL MSFT ENI.MI). "
            "Omettere se si usa --discover-sp500."
        ),
    )
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
        help="Come --validate, ma ignora cache e gate",
    )
    parser.add_argument(
        "--no-watchlist",
        action="store_true",
        help="Non aggiungere i ticker classe A/B alla watchlist",
    )
    parser.add_argument(
        "--discover-sp500",
        action="store_true",
        help=(
            "Discovery automatico su tutto S&P 500 (constituents da Wikipedia, "
            "cache 7gg). Pipeline 3-stage: prefilter cheap → full scoring → top N."
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
        default=0.0,
        help="Score composito minimo per inclusione (es. 60 per classe A+B).",
    )
    parser.add_argument(
        "--prefilter-rsi-max",
        type=float,
        default=DISCOVERY_PREFILTER_RSI_MAX,
        help=f"Soglia RSI prefilter (default {DISCOVERY_PREFILTER_RSI_MAX}).",
    )
    parser.add_argument(
        "--prefilter-atr-min",
        type=float,
        default=DISCOVERY_PREFILTER_ATR_DISTANCE_MIN,
        help=(
            f"Soglia distanza ATR prefilter (default {DISCOVERY_PREFILTER_ATR_DISTANCE_MIN})."
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
        help="Forza re-fetch della lista S&P 500 da Wikipedia (bypass cache 7gg).",
    )
    args = parser.parse_args()

    # Validation: o ticker espliciti o discovery, ma non entrambi vuoti
    if not args.tickers and not args.discover_sp500:
        parser.error("Specifica almeno un ticker oppure usa --discover-sp500.")
    if args.tickers and args.discover_sp500:
        parser.error(
            "--discover-sp500 e ticker espliciti sono mutually exclusive: "
            "scegli uno dei due flussi."
        )

    # Scarica VIX una volta sola per batch (contesto di mercato condiviso)
    vix: float | None = None
    vix_series = download_benchmark("^VIX", days=10)
    if vix_series is not None and not vix_series.empty:
        vix = float(vix_series.iloc[-1])

    results: list[dict] = []
    if args.discover_sp500:
        try:
            universe = get_sp500_universe(force_refresh=args.refresh_universe)
        except Exception as exc:
            print(f"[errore] impossibile ottenere universo S&P 500: {exc}", file=sys.stderr)
            return 1

        print(
            f"[discovery] universo S&P 500: {len(universe)} ticker. "
            f"Stage 1 (prefilter RSI<={args.prefilter_rsi_max}, "
            f"distance>={args.prefilter_atr_min}×ATR)...",
            file=sys.stderr,
        )
        out = discover_contra_candidates(
            universe,
            top_n=args.top,
            rsi_max=args.prefilter_rsi_max,
            atr_distance_min=args.prefilter_atr_min,
            min_score=args.min_score,
            vix=vix,
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
            r = analyze_contra_ticker(t, strategy="Contrarian", vix=vix)
            if r is not None:
                results.append(r)

    if not results:
        if args.discover_sp500:
            print(
                "[discovery] nessun candidato qualificato dopo full scoring.",
                file=sys.stderr,
            )
        return 1

    if args.validate or args.force_validate:
        from propicks.ai import validate_contrarian_thesis

        for r in results:
            verdict = validate_contrarian_thesis(
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

    # In discovery mode default a summary table (n risultati ≥ 5 tipicamente):
    # l'analysis dettagliata × 10 è troppo rumorosa. Brief flag esplicito
    # forza summary anche su single-ticker.
    if args.brief or args.discover_sp500:
        print_summary_table(results)
        return 0

    if len(results) == 1:
        print_analysis(results[0])
        print_ai_verdict(results[0])
    else:
        print_summary_table(results)
        for r in results:
            print()
            print_analysis(r)
            print_ai_verdict(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
