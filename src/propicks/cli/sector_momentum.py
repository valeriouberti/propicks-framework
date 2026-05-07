"""CLI thin wrapper per Sector-Filtered Momentum (SFM).

Ibrido top-down/bottom-up: ETF rotation seleziona settori OVERWEIGHT, poi
momentum scoring sceglie i top stock dentro ogni settore. Edge atteso da
Moskowitz-Grinblatt 1999 (industry momentum) + Asness-Porter-Stevens 2000
(intra-industry winners).

Esempi:
    # Mode A — rotate-driven (default): scopri top 2 settori → top 3 stock ognuno
    propicks-sector-momentum

    # Mode A custom: top 3 settori, top 5 stock per settore
    propicks-sector-momentum --top-sectors 3 --top-stocks 5

    # Mode B — sector esplicito: skip rotation, vai diretto su tech
    propicks-sector-momentum --sector XLK
    propicks-sector-momentum --sector technology --top-stocks 5

    # Validation AI + JSON
    propicks-sector-momentum --validate --json

Limitazione fase 1: solo S&P 500 universe. NASDAQ100/STOXX600 in roadmap.
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.config import (
    SFM_DEFAULT_TOP_SECTORS,
    SFM_MAX_LOSS_PER_TRADE_PCT,
    SFM_MAX_POSITION_SIZE_PCT,
    SFM_MAX_STOCKS_PER_SECTOR,
    SFM_MIN_SECTOR_SCORE,
    SFM_MIN_STOCK_SCORE,
    SFM_RS_OVERLAY_WEIGHT,
    SFM_SUBETF_MAX_LOSS_PER_TRADE_PCT,
    SFM_SUBETF_MAX_POSITION_SIZE_PCT,
)
from propicks.domain.etf_scoring import rank_universe
from propicks.domain.sector_momentum import (
    INSTRUMENT_STOCK,
    INSTRUMENT_SUBETF,
    VALID_INSTRUMENTS,
    discover_sector_momentum_candidates,
    normalize_sector_to_key,
    sector_key_for_peer_etf,
)
from propicks.io.watchlist_store import add_to_watchlist, load_watchlist
from propicks.market.index_constituents import (
    INDEX_NAME_SP500,
    get_index_universe_detailed,
    index_label,
)


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "-"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def print_sectors_summary(sectors_evaluated: list[dict]) -> None:
    """Tabella settori valutati: peer ETF, score rotation, n. universe, n. stock."""
    if not sectors_evaluated:
        print("[sfm] nessun settore valutato.", file=sys.stderr)
        return
    rows = []
    for s in sectors_evaluated:
        sec_score = s.get("sector_score")
        sec_score_str = f"{sec_score:.1f}" if isinstance(sec_score, (int, float)) else "—"
        rows.append([
            s["sector_key"],
            s["peer_etf"],
            sec_score_str,
            s["n_universe"],
            s["n_candidates"],
        ])
    headers = ["Settore", "Peer ETF", "Score Rot.", "N. Universe", "N. Stock"]
    print()
    print("=" * 70)
    print("SETTORI VALUTATI")
    print("=" * 70)
    print(tabulate(rows, headers=headers, tablefmt="github"))


def print_candidates_table(candidates: list[dict]) -> None:
    """Tabella stock SFM ordinati cross-sector by score_sfm desc."""
    if not candidates:
        print(
            "[sfm] nessun candidato sopra le soglie momentum + sector.",
            file=sys.stderr,
        )
        return

    headers = [
        "#",
        "Ticker",
        "Settore",
        "Peer",
        "Px",
        "SFM",
        "Mom",
        "RS sec",
        "Class.",
        "Stop",
        "1m",
    ]
    rows = []
    for i, r in enumerate(candidates, 1):
        rs = r.get("rs_vs_sector") or {}
        rs_score = rs.get("score")
        rs_score_str = f"{rs_score:.0f}" if isinstance(rs_score, (int, float)) else "—"
        rows.append([
            i,
            r["ticker"],
            r.get("sector_key", "-"),
            r.get("peer_etf", "-"),
            f"{r['price']:.2f}",
            f"{r.get('score_sfm', 0):.1f}",
            f"{r.get('score_composite', 0):.1f}",
            rs_score_str,
            r.get("classification", "-").split(" — ")[0],
            f"{r['stop_suggested']:.2f}",
            _fmt_pct(r.get("perf_1m")),
        ])
    print()
    print("=" * 70)
    print("CANDIDATI SFM (ranked desc by score_sfm)")
    print("=" * 70)
    print(tabulate(rows, headers=headers, tablefmt="github"))


def print_invariants_footer(instrument: str = INSTRUMENT_STOCK) -> None:
    """Reminder degli invarianti SFM da rispettare in add_position."""
    print()
    if instrument == INSTRUMENT_SUBETF:
        print("Invarianti SFM (sub-ETF mode):")
        print(
            f"  - Max per sub-ETF: {SFM_SUBETF_MAX_POSITION_SIZE_PCT * 100:.0f}% "
            f"(vs 10% stock SFM — basket diversificato, idio risk minore)"
        )
        print(
            f"  - Max sub-ETF per settore: {SFM_MAX_STOCKS_PER_SECTOR} "
            f"(shared cap con stock SFM)"
        )
        print(
            f"  - Stop max loss: {SFM_SUBETF_MAX_LOSS_PER_TRADE_PCT * 100:.0f}% "
            f"(vs 6% stock SFM — lower vol intrinseca)"
        )
        print(
            "  - Peer-RS overlay: degrade graceful a base composite "
            "(rs_vs_sector ≈ None per ETF basket)"
        )
        print("  - No earnings gate (basket diversificato)")
        print("  - Bucket cap shared con SFM stock (25% aggregate)")
        return
    print("Invarianti SFM:")
    print(
        f"  - Max per stock: {SFM_MAX_POSITION_SIZE_PCT * 100:.0f}% "
        f"(vs 15% momentum standalone — beta inflation premium)"
    )
    print(
        f"  - Max stock per settore: {SFM_MAX_STOCKS_PER_SECTOR} "
        f"(evita over-concentration intra-bucket)"
    )
    print(
        f"  - Stop max loss: {SFM_MAX_LOSS_PER_TRADE_PCT * 100:.0f}% "
        f"(vs 8% momentum — high-beta drawdown atteso)"
    )
    print(
        f"  - Peer-RS overlay weight: {SFM_RS_OVERLAY_WEIGHT * 100:.0f}% "
        f"(score_sfm = composite × 0.80 + rs_sector × 0.20)"
    )


def _auto_watchlist_actionable(candidates: list[dict]) -> None:
    """Auto-add classe A/B candidates alla watchlist (parallelo a momentum CLI)."""
    actionable = [
        c for c in candidates
        if c.get("classification", "").startswith(("A", "B"))
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
        target = round(r["price"], 2) if (is_class_a and not (existing and existing.get("target_entry"))) else None
        regime = r.get("regime") or {}
        _, is_new = add_to_watchlist(
            wl,
            r["ticker"],
            target_entry=target,
            score_at_add=r.get("score_sfm"),
            regime_at_add=regime.get("regime"),
            classification_at_add=classification,
            source="auto_sfm_scan",
        )
        (added if is_new else updated).append(r["ticker"])
    msg_parts = []
    if added:
        msg_parts.append(f"aggiunti {', '.join(added)}")
    if updated:
        msg_parts.append(f"aggiornati {', '.join(updated)}")
    if msg_parts:
        print(f"[watchlist] auto-update SFM: {'; '.join(msg_parts)}", file=sys.stderr)


def _discovery_progress(stage: str, current: int, total: int, ticker: str) -> None:
    if stage == "sector":
        print(
            f"[sfm/sector] {current}/{total} → {ticker}",
            file=sys.stderr,
        )
        return
    if current == total or current % 25 == 0:
        print(
            f"[sfm/{stage}] {current}/{total} ({ticker})",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sector-Filtered Momentum: combina ETF rotation (top-down) con "
            "momentum scoring intra-settore (bottom-up). Solo S&P 500 fase 1."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--sector",
        type=str,
        default=None,
        help=(
            "Sector esplicito: ticker peer ETF (XLK, XLF, ...) o sector_key "
            "(technology, financials, ...). Skippa rotation gating."
        ),
    )
    parser.add_argument(
        "--top-sectors",
        type=int,
        default=SFM_DEFAULT_TOP_SECTORS,
        help=(
            f"Mode rotate-driven: top-N settori OVERWEIGHT da scansionare "
            f"(default {SFM_DEFAULT_TOP_SECTORS})."
        ),
    )
    parser.add_argument(
        "--top-stocks",
        type=int,
        default=SFM_MAX_STOCKS_PER_SECTOR,
        help=(
            f"Top-N stock per settore (default {SFM_MAX_STOCKS_PER_SECTOR} = "
            f"SFM_MAX_STOCKS_PER_SECTOR)."
        ),
    )
    parser.add_argument(
        "--min-sector-score",
        type=float,
        default=SFM_MIN_SECTOR_SCORE,
        help=(
            f"Score composite ETF rotation minimo per qualificare (default "
            f"{SFM_MIN_SECTOR_SCORE} = classe A OVERWEIGHT)."
        ),
    )
    parser.add_argument(
        "--min-stock-score",
        type=float,
        default=SFM_MIN_STOCK_SCORE,
        help=(
            f"Score momentum minimo per inclusione candidato (default "
            f"{SFM_MIN_STOCK_SCORE} = classe A AZIONE IMMEDIATA)."
        ),
    )
    parser.add_argument(
        "--rs-weight",
        type=float,
        default=SFM_RS_OVERLAY_WEIGHT,
        help=(
            f"Peso overlay peer-RS in composite SFM (default "
            f"{SFM_RS_OVERLAY_WEIGHT}). Range [0, 1]."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Valida ogni candidato via Claude (richiede ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--force-validate",
        action="store_true",
        help="Come --validate, ma ignora cache e gate di score.",
    )
    parser.add_argument(
        "--no-watchlist",
        action="store_true",
        help="Non aggiungere automaticamente i candidati classe A/B in watchlist.",
    )
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Forza re-fetch S&P 500 da Wikipedia (bypass cache 7gg).",
    )
    parser.add_argument(
        "--instrument",
        choices=sorted(VALID_INSTRUMENTS),
        default=INSTRUMENT_STOCK,
        help=(
            f"Strumento da scegliere dentro il settore vincente: "
            f"'{INSTRUMENT_STOCK}' = top stock S&P 500 (default), "
            f"'{INSTRUMENT_SUBETF}' = top sub-ETF curato (no earnings gate, "
            f"sizing 13%%, stop 5%%)."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--brief", action="store_true", help="Solo tabella candidati")

    args = parser.parse_args()

    # Validazione args
    if not 0.0 <= args.rs_weight <= 1.0:
        parser.error("--rs-weight deve essere in [0, 1]")

    # ------------------------------------------------------------------
    # Step 1: ottieni universe SP500 detailed (con sector field).
    # In instrument=subetf mode l'universe S&P 500 non serve (deriva tutto da
    # PARENT_TO_SUB_ETFS curato in domain/subetf_universe.py).
    # ------------------------------------------------------------------
    detailed: list[dict] | None = None
    if args.instrument == INSTRUMENT_STOCK:
        label = index_label(INDEX_NAME_SP500)
        try:
            detailed = get_index_universe_detailed(
                INDEX_NAME_SP500, force_refresh=args.refresh_universe
            )
        except Exception as exc:
            print(
                f"[errore] impossibile ottenere universo {label}: {exc}",
                file=sys.stderr,
            )
            return 1
        print(
            f"[sfm] universo {label}: {len(detailed)} ticker.",
            file=sys.stderr,
        )
    else:
        print(
            "[sfm] mode instrument=subetf: skip S&P 500 fetch "
            "(universe da PARENT_TO_SUB_ETFS).",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Step 2: determina mode (rotate-driven vs sector-explicit)
    # ------------------------------------------------------------------
    ranked: list[dict] | None = None
    sector_keys: list[str] | None = None

    if args.sector is not None:
        # Mode B: sector esplicito. Risolvi sia ETF (XLK) che sector_key (technology).
        token = args.sector.strip()
        # Try peer ETF first (uppercase), poi sector_key (case-insensitive normalize)
        sector_key = sector_key_for_peer_etf(token) or normalize_sector_to_key(token)
        if sector_key is None:
            # Permetti anche match diretto su sector_key lowercase
            from propicks.domain.sector_momentum import SECTOR_KEY_ALIASES
            if token.lower() in SECTOR_KEY_ALIASES:
                sector_key = token.lower()
        if sector_key is None:
            print(
                f"[errore] --sector '{args.sector}' non riconosciuto. "
                f"Usa peer ETF (XLK, XLF, ...) o sector_key "
                f"(technology, financials, healthcare, ...).",
                file=sys.stderr,
            )
            return 1
        sector_keys = [sector_key]
        print(
            f"[sfm] mode sector-explicit: sector_key='{sector_key}'",
            file=sys.stderr,
        )
    else:
        # Mode A: rotate-driven
        print(
            "[sfm] mode rotate-driven: ranking ETF universe US...",
            file=sys.stderr,
        )
        try:
            ranked = rank_universe(region="US")
        except Exception as exc:
            print(
                f"[errore] rank_universe ETF fallito: {exc}",
                file=sys.stderr,
            )
            return 1
        if not ranked:
            print("[errore] ranking ETF vuoto.", file=sys.stderr)
            return 1
        # Stampa breve riassunto rotation top
        eligible = [r for r in ranked if r["score_composite"] >= args.min_sector_score]
        print(
            f"[sfm] ETF rotation: {len(eligible)} settori ≥{args.min_sector_score:.0f} "
            f"(top: {', '.join(r['ticker'] for r in eligible[:args.top_sectors])})",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Step 3: pipeline SFM
    # ------------------------------------------------------------------
    out = discover_sector_momentum_candidates(
        detailed,
        instrument=args.instrument,
        ranked_etfs=ranked,
        sector_keys=sector_keys,
        top_sectors=args.top_sectors,
        top_stocks_per_sector=args.top_stocks,
        min_sector_score=args.min_sector_score,
        min_stock_score=args.min_stock_score,
        rs_overlay_weight=args.rs_weight,
        progress_callback=_discovery_progress,
    )

    candidates = out["candidates"]
    if not candidates:
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            print_sectors_summary(out["sectors_evaluated"])
            print(
                "\n[sfm] nessun candidato qualificato. Possibili cause:\n"
                f"  - nessun settore ≥ {args.min_sector_score:.0f} (regime sfavorevole)\n"
                f"  - nessuno stock ≥ {args.min_stock_score:.0f} dentro i settori vincenti\n"
                "  - prefilter momentum scarta tutti (RSI/dist_high)",
                file=sys.stderr,
            )
        return 1

    # ------------------------------------------------------------------
    # Step 4: AI validation (opt-in) — solo instrument=stock fase 1.
    # Sub-ETF mode salta AI: il prompt SFM è frame-su-stock (business_quality,
    # narrative_catalysts) — non ha senso per ETF basket. Out of scope fase 1.
    # ------------------------------------------------------------------
    if args.validate or args.force_validate:
        if args.instrument == INSTRUMENT_SUBETF:
            print(
                "[sfm] AI validation skipped in instrument=subetf mode (fase 1).",
                file=sys.stderr,
            )
        else:
            from propicks.ai import validate_sfm_thesis

            for r in candidates:
                verdict = validate_sfm_thesis(
                    r,
                    force=args.force_validate,
                    gate=not args.force_validate,
                )
                if verdict is not None:
                    r["ai_verdict"] = verdict

    # ------------------------------------------------------------------
    # Step 5: watchlist auto-update
    # ------------------------------------------------------------------
    if not args.no_watchlist:
        _auto_watchlist_actionable(candidates)

    # ------------------------------------------------------------------
    # Step 6: output
    # ------------------------------------------------------------------
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print_sectors_summary(out["sectors_evaluated"])
    print_candidates_table(candidates)
    if not args.brief:
        print_invariants_footer(args.instrument)
    return 0


if __name__ == "__main__":
    sys.exit(main())
