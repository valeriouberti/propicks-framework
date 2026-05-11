"""CLI Core Portfolio — bucket long-term PIC/PAC isolato dal satellite.

Esempi:
    propicks-core add VWCE.MI --shares 10 --price 110.50 --kind PIC \\
        --asset-class EQUITY_ETF --region WORLD --target-weight 0.60 \\
        --date 2025-01-15
    propicks-core contribute VWCE.MI --shares 2 --price 115.20 --kind PAC
    propicks-core sell VWCE.MI --shares 3 --price 130.0
    propicks-core list
    propicks-core history --ticker VWCE.MI --since 2025-01-01
    propicks-core update VWCE.MI --target-weight 0.65 --notes "..."
    propicks-core remove VWCE.MI                  # soft delete
    propicks-core remove VWCE.MI --hard           # cascade delete
    propicks-core exposure                        # asset class + region + sector
    propicks-core drift                           # vs target_weight
    propicks-core consolidated                    # overlap core + satellite
    propicks-core import-csv path/file.csv        # bulk PAC backfill

Invariant:
- Bucket isolato dai cap satellite (Stock 40% / ETF 60%).
- Nessun stop/target/AI. Risk model = buy & hold.
- Drift alert se |actual − target| > 5% (CORE_DRIFT_REBALANCE_THRESHOLD_PCT).
- Overlap warn se settore consolidato (core+satellite) > 35%.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from tabulate import tabulate

from propicks.config import (
    ASSET_CLASS_LABELS,
    CORE_CONTRIBUTION_KINDS,
    CORE_DRIFT_REBALANCE_THRESHOLD_PCT,
    CORE_OVERLAP_SECTOR_WARN_PCT,
    REGION_LABELS,
)
from propicks.domain import core_allocation as ca
from propicks.io import core_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_pct(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.{decimals}f}%" if abs(value) < 10 else f"{value:+.{decimals}f}"


def _fmt_money(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{decimals}f}"


def _load_prices(tickers: list[str]) -> dict[str, float]:
    """Wrapper su yfinance_client. Estratto per testabilità."""
    if not tickers:
        return {}
    from propicks.market.yfinance_client import get_current_prices
    return get_current_prices(tickers)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_add(args: argparse.Namespace) -> int:
    try:
        h = core_store.add_holding(
            args.ticker,
            shares=args.shares,
            price=args.price,
            name=args.name,
            asset_class=args.asset_class,
            region=args.region,
            sector_key=args.sector_key,
            currency=args.currency,
            target_weight=args.target_weight,
            notes=args.notes,
            date=args.date,
            kind=args.kind,
            fees=args.fees,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"Aggiunto {h['ticker']}: {h['shares']:g} shares @ avg {h['avg_cost']:.4f} "
        f"{h['currency']} (kind={args.kind})."
    )
    return 0


def cmd_contribute(args: argparse.Namespace) -> int:
    try:
        h = core_store.add_contribution(
            args.ticker,
            shares=args.shares,
            price=args.price,
            kind=args.kind,
            date=args.date,
            fees=args.fees,
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"{args.kind} su {h['ticker']}: nuovo totale {h['shares']:g} shares "
        f"@ avg {h['avg_cost']:.4f} {h['currency']}."
    )
    return 0


def cmd_sell(args: argparse.Namespace) -> int:
    # Wrapper su add_contribution con kind=SELL, shares negativo.
    shares_neg = -abs(args.shares)
    try:
        h = core_store.add_contribution(
            args.ticker,
            shares=shares_neg,
            price=args.price,
            kind="SELL",
            date=args.date,
            fees=args.fees,
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"SELL su {h['ticker']}: residuo {h['shares']:g} shares "
        f"@ avg {h['avg_cost']:.4f} {h['currency']}."
    )
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    try:
        h = core_store.update_holding_meta(
            args.ticker,
            name=args.name,
            asset_class=args.asset_class,
            region=args.region,
            sector_key=args.sector_key,
            target_weight=args.target_weight,
            notes=args.notes,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    tw = f"{h['target_weight'] * 100:.1f}%" if h.get("target_weight") else "-"
    print(
        f"Aggiornato {h['ticker']}: asset_class={h.get('asset_class') or '-'}, "
        f"region={h.get('region') or '-'}, sector={h.get('sector_key') or '-'}, "
        f"target_weight={tw}."
    )
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        h = core_store.remove_holding(args.ticker, keep_history=not args.hard)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    mode = "hard delete (CASCADE contributions)" if args.hard else "soft delete (storia preservata)"
    print(f"Rimosso {h['ticker']} — {mode}.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    holdings = core_store.load_core()
    if not holdings:
        print("Core portfolio vuoto. Usa `propicks-core add` per iniziare.")
        return 0

    tickers = list(holdings.keys())
    prices = _load_prices(tickers) if not args.no_prices else {}
    values = ca.compute_holding_values(holdings, prices)
    total = ca.total_core_value(values)

    rows = []
    for t, h in sorted(holdings.items()):
        v = values.get(t)
        if v:
            cur_price = f"{v['current_price']:.2f}"
            cur_value = _fmt_money(v["current_value"])
            pnl = _fmt_money(v["pnl"])
            pnl_pct = _fmt_pct(v["pnl_pct"])
            weight = f"{v['current_value'] / total * 100:.1f}%" if total > 0 else "-"
        else:
            cur_price = cur_value = pnl = pnl_pct = weight = "—"
        target = h.get("target_weight")
        target_str = f"{target * 100:.1f}%" if target else "-"
        rows.append([
            t,
            h.get("asset_class") or "-",
            h.get("region") or "-",
            f"{h['shares']:g}",
            f"{h['avg_cost']:.2f}",
            cur_price,
            cur_value,
            pnl,
            pnl_pct,
            weight,
            target_str,
        ])

    headers = [
        "Ticker", "Asset", "Region", "Shares", "AvgCost",
        "Price", "Value", "P&L", "P&L%", "Weight", "Target",
    ]
    print(tabulate(rows, headers=headers, tablefmt="github"))
    contributed = core_store.total_contributed()
    pnl_total = total - contributed if total > 0 else 0.0
    pnl_total_pct = (pnl_total / contributed) if contributed > 0 else 0.0
    print()
    print(
        f"Totale: {len(rows)} holding · Valore EUR {_fmt_money(total)} · "
        f"Contributed {_fmt_money(contributed)} · "
        f"P&L tot {_fmt_money(pnl_total)} ({_fmt_pct(pnl_total_pct)})"
    )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    contribs = core_store.list_contributions(
        ticker=args.ticker, since=args.since, kind=args.kind
    )
    if not contribs:
        print("Nessuna contribution trovata coi filtri specificati.")
        return 0
    rows = []
    for c in contribs:
        rows.append([
            c["date"],
            c["ticker"],
            c["kind"],
            f"{c['shares']:g}",
            f"{c['price']:.2f}",
            _fmt_money(c["amount"]),
            f"{c['fees']:.2f}" if c["fees"] else "-",
            c["currency"],
            (c.get("notes") or "")[:40],
        ])
    print(tabulate(
        rows,
        headers=["Date", "Ticker", "Kind", "Shares", "Price", "Amount", "Fees", "Cur", "Notes"],
        tablefmt="github",
    ))
    print()
    print(f"Totale: {len(contribs)} contribution.")
    return 0


def cmd_exposure(args: argparse.Namespace) -> int:
    holdings = core_store.load_core()
    if not holdings:
        print("Core portfolio vuoto.")
        return 0
    prices = _load_prices(list(holdings.keys()))
    summary = ca.summarize_core(holdings, prices)

    print(f"Core total value EUR: {_fmt_money(summary['total_value_eur'])}")
    if summary["n_missing_price"]:
        print(f"⚠️  {summary['n_missing_price']} holding senza prezzo corrente (skipped).")
    print()

    def _print_breakdown(title: str, data: dict[str, float], labels: dict[str, str] | None):
        if not data:
            return
        print(f"## {title}")
        rows = sorted(data.items(), key=lambda x: x[1], reverse=True)
        table = [
            [labels.get(k, k) if labels else k, f"{v * 100:.1f}%"]
            for k, v in rows
        ]
        print(tabulate(table, headers=["Bucket", "Weight"], tablefmt="github"))
        print()

    _print_breakdown("Asset class", summary["asset_class"], ASSET_CLASS_LABELS)
    _print_breakdown("Region", summary["region"], REGION_LABELS)
    _print_breakdown("Sector (broad = ETF diversificato)", summary["sector"], None)
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    holdings = core_store.load_core()
    if not holdings:
        print("Core portfolio vuoto.")
        return 0
    prices = _load_prices(list(holdings.keys()))
    values = ca.compute_holding_values(holdings, prices)
    total = ca.total_core_value(values)
    drift = ca.compute_drift(
        holdings, values, total,
        rebalance_threshold=args.threshold or CORE_DRIFT_REBALANCE_THRESHOLD_PCT,
    )
    if not drift:
        print("Nessuna holding con target_weight definito. Usa `update --target-weight`.")
        return 0

    rows = []
    flagged = 0
    for t, d in sorted(drift.items()):
        flag = "⚠️ REBAL" if d["needs_rebalance"] else ""
        if d["needs_rebalance"]:
            flagged += 1
        rows.append([
            t,
            f"{d['actual_weight'] * 100:.1f}%",
            f"{d['target_weight'] * 100:.1f}%",
            f"{d['drift'] * 100:+.1f}%",
            _fmt_money(d["rebalance_eur"]),
            flag,
        ])
    print(tabulate(
        rows,
        headers=["Ticker", "Actual", "Target", "Drift", "Rebal EUR", "Flag"],
        tablefmt="github",
    ))
    threshold = args.threshold or CORE_DRIFT_REBALANCE_THRESHOLD_PCT
    print()
    print(
        f"Total core EUR {_fmt_money(total)}. "
        f"{flagged}/{len(drift)} holding sopra soglia drift {threshold * 100:.0f}%. "
        f"Rebal EUR > 0 = compra, < 0 = vendi."
    )
    return 0


def cmd_consolidated(args: argparse.Namespace) -> int:
    """Sector exposure consolidato core + satellite vs capitale totale."""
    from propicks.io.portfolio_store import load_portfolio
    from propicks.market.yfinance_client import get_sector

    holdings = core_store.load_core()
    portfolio = load_portfolio()
    sat_positions = portfolio.get("positions", {})

    if not holdings and not sat_positions:
        print("Nessuna posizione (core o satellite).")
        return 0

    # Core values (EUR)
    core_tickers = list(holdings.keys())
    sat_tickers = list(sat_positions.keys())
    all_tickers = list(set(core_tickers + sat_tickers))
    prices = _load_prices(all_tickers)
    core_values = ca.compute_holding_values(holdings, prices)

    # Satellite sector map + currency map
    sat_sectors: dict[str, str | None] = {}
    sat_currency: dict[str, str] = {}
    for t, pos in sat_positions.items():
        try:
            sat_sectors[t] = get_sector(t)
        except Exception:
            sat_sectors[t] = None
        sat_currency[t] = pos.get("currency", "EUR")

    # Capitale totale = core EUR + satellite EUR + cash EUR
    core_total = ca.total_core_value(core_values)
    sat_prices = {t: prices[t] for t in sat_tickers if t in prices}
    sat_market_value = 0.0
    for t, pos in sat_positions.items():
        px = sat_prices.get(t)
        if px is None:
            continue
        mv = pos["shares"] * px
        sat_market_value += ca._mv_to_eur(mv, sat_currency.get(t))
    cash = float(portfolio.get("cash") or 0)
    total_capital = core_total + sat_market_value + cash

    consolidated = ca.compute_consolidated_sector_exposure(
        core_holdings=holdings,
        core_values=core_values,
        satellite_positions=sat_positions,
        satellite_prices=sat_prices,
        satellite_sector_map=sat_sectors,
        total_capital_eur=total_capital,
        satellite_currency_map=sat_currency,
    )
    warns = ca.detect_overlap_warnings(
        consolidated, warn_threshold=args.threshold or CORE_OVERLAP_SECTOR_WARN_PCT,
    )

    print(f"Capitale totale EUR: {_fmt_money(total_capital)}")
    print(
        f"  Core: {_fmt_money(core_total)} · "
        f"Satellite: {_fmt_money(sat_market_value)} · "
        f"Cash: {_fmt_money(cash)}"
    )
    print()

    if not consolidated:
        print("Nessuna esposizione settoriale tracciabile (core broad ETF + satellite vuoto).")
        return 0

    rows = sorted(consolidated.items(), key=lambda x: x[1], reverse=True)
    table = [[k, f"{v * 100:.1f}%"] for k, v in rows]
    print("## Sector exposure consolidato (core con sector_key + satellite)")
    print(tabulate(table, headers=["Sector", "% Capitale"], tablefmt="github"))
    print()

    if warns:
        print(f"⚠️  Overlap warnings (soglia {(args.threshold or CORE_OVERLAP_SECTOR_WARN_PCT) * 100:.0f}%):")
        for w in warns:
            print(
                f"   • {w['sector']}: {w['pct'] * 100:.1f}% "
                f"(over by {w['over_by'] * 100:+.1f}%)"
            )
    else:
        print("✓ Nessun overlap sopra soglia.")
    return 0


def cmd_import_csv(args: argparse.Namespace) -> int:
    """Bulk import contributions da CSV broker.

    CSV columns required: date, ticker, kind, shares, price
    Optional: fees, currency, notes, asset_class, region, sector_key, target_weight

    Strategia: per ogni riga, se holding non esiste → add_holding (richiede
    asset_class+region nella riga o default), altrimenti add_contribution.
    """
    path = Path(args.path)
    if not path.exists():
        print(f"[errore] File non trovato: {path}", file=sys.stderr)
        return 2

    n_holdings = 0
    n_contribs = 0
    errors: list[str] = []

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):  # row 1 = header
            try:
                ticker = row["ticker"].strip().upper()
                kind = (row.get("kind") or "PAC").strip().upper()
                shares = float(row["shares"])
                price = float(row["price"])
                date = (row.get("date") or "").strip() or None
                fees = float(row.get("fees") or 0)
                notes = (row.get("notes") or "").strip() or None

                if args.dry_run:
                    print(f"  [dry] row {i}: {date} {ticker} {kind} {shares}@{price}")
                    continue

                existing = core_store.get_holding(ticker)
                if existing is None or float(existing["shares"] or 0) == 0:
                    # Crea holding (richiede meta sulla prima riga)
                    core_store.add_holding(
                        ticker,
                        shares=shares,
                        price=price,
                        name=(row.get("name") or "").strip() or None,
                        asset_class=(row.get("asset_class") or "").strip() or None,
                        region=(row.get("region") or "").strip() or None,
                        sector_key=(row.get("sector_key") or "").strip() or None,
                        currency=(row.get("currency") or "").strip() or None,
                        target_weight=(
                            float(row["target_weight"])
                            if row.get("target_weight") else None
                        ),
                        notes=notes,
                        date=date,
                        kind=kind if kind != "SELL" else "PIC",
                        fees=fees,
                    )
                    n_holdings += 1
                else:
                    sh = -abs(shares) if kind == "SELL" else shares
                    core_store.add_contribution(
                        ticker,
                        shares=sh,
                        price=price,
                        kind=kind,
                        date=date,
                        fees=fees,
                        notes=notes,
                    )
                    n_contribs += 1
            except (KeyError, ValueError) as exc:
                errors.append(f"row {i}: {exc}")
                continue

    mode = "DRY RUN" if args.dry_run else "Imported"
    print(f"{mode}: {n_holdings} new holding, {n_contribs} contribution.")
    if errors:
        print(f"⚠️  {len(errors)} errori:")
        for e in errors[:10]:
            print(f"   {e}")
        if len(errors) > 10:
            print(f"   ... + {len(errors) - 10} altri")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------
def _add_common_meta_args(p: argparse.ArgumentParser, *, required_meta: bool = False):
    p.add_argument("--name", default=None, help="Nome esteso (es. 'Vanguard FTSE All-World')")
    p.add_argument(
        "--asset-class",
        dest="asset_class",
        default=None,
        choices=("EQUITY_ETF", "BOND_ETF", "COMMODITY_ETF", "STOCK"),
        help="Classe asset (richiesto per nuove holding)",
    )
    p.add_argument(
        "--region", default=None,
        choices=("WORLD", "US", "EU", "EM", "IT"),
        help="Region (richiesto per nuove holding)",
    )
    p.add_argument(
        "--sector-key", dest="sector_key", default=None,
        help="GICS sector key (es. technology). NULL per broad ETF.",
    )
    p.add_argument(
        "--target-weight", dest="target_weight", type=float, default=None,
        help="Target % capitale core (frazione, es. 0.60 = 60%%)",
    )
    p.add_argument("--notes", default=None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Core Portfolio — bucket long-term PIC/PAC isolato dal satellite.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # add
    p_add = sub.add_parser("add", help="Apri una nuova holding (PIC iniziale)")
    p_add.add_argument("ticker")
    p_add.add_argument("--shares", type=float, required=True)
    p_add.add_argument("--price", type=float, required=True)
    p_add.add_argument("--currency", default=None, help="Auto-infer da suffix se omesso")
    p_add.add_argument("--date", default=None, help="YYYY-MM-DD (default: oggi)")
    p_add.add_argument(
        "--kind", default="PIC",
        choices=tuple(k for k in CORE_CONTRIBUTION_KINDS if k != "SELL"),
    )
    p_add.add_argument("--fees", type=float, default=0.0)
    _add_common_meta_args(p_add, required_meta=True)
    p_add.set_defaults(func=cmd_add)

    # contribute
    p_c = sub.add_parser("contribute", help="Aggiungi una contribution a holding esistente")
    p_c.add_argument("ticker")
    p_c.add_argument("--shares", type=float, required=True)
    p_c.add_argument("--price", type=float, required=True)
    p_c.add_argument(
        "--kind", default="PAC",
        choices=("PIC", "PAC", "DIVIDEND_REINVEST"),
    )
    p_c.add_argument("--date", default=None)
    p_c.add_argument("--fees", type=float, default=0.0)
    p_c.add_argument("--notes", default=None)
    p_c.set_defaults(func=cmd_contribute)

    # sell
    p_s = sub.add_parser("sell", help="Vendi (parziale o totale) — kind=SELL")
    p_s.add_argument("ticker")
    p_s.add_argument("--shares", type=float, required=True, help="Quantità da vendere (positivo)")
    p_s.add_argument("--price", type=float, required=True)
    p_s.add_argument("--date", default=None)
    p_s.add_argument("--fees", type=float, default=0.0)
    p_s.add_argument("--notes", default=None)
    p_s.set_defaults(func=cmd_sell)

    # update
    p_u = sub.add_parser("update", help="Aggiorna metadati (target_weight, region, ...)")
    p_u.add_argument("ticker")
    _add_common_meta_args(p_u)
    p_u.set_defaults(func=cmd_update)

    # remove
    p_r = sub.add_parser("remove", help="Rimuovi holding (soft default, --hard per cascade)")
    p_r.add_argument("ticker")
    p_r.add_argument("--hard", action="store_true", help="Cascade delete contributions (distruttivo)")
    p_r.set_defaults(func=cmd_remove)

    # list
    p_l = sub.add_parser("list", help="Elenca holding con prezzi correnti, P&L, weight")
    p_l.add_argument("--no-prices", action="store_true", help="Skip fetch yfinance (offline)")
    p_l.set_defaults(func=cmd_list)

    # history
    p_h = sub.add_parser("history", help="Storia contributions (PIC/PAC/SELL/DIV)")
    p_h.add_argument("--ticker", default=None)
    p_h.add_argument("--since", default=None, help="YYYY-MM-DD")
    p_h.add_argument(
        "--kind", default=None,
        choices=("PIC", "PAC", "DIVIDEND_REINVEST", "SELL"),
    )
    p_h.set_defaults(func=cmd_history)

    # exposure
    p_e = sub.add_parser("exposure", help="Breakdown asset class + region + sector")
    p_e.set_defaults(func=cmd_exposure)

    # drift
    p_d = sub.add_parser("drift", help="Drift vs target_weight + suggerimento rebalance EUR")
    p_d.add_argument(
        "--threshold", type=float, default=None,
        help=f"Soglia drift (default {CORE_DRIFT_REBALANCE_THRESHOLD_PCT})",
    )
    p_d.set_defaults(func=cmd_drift)

    # consolidated
    p_co = sub.add_parser(
        "consolidated", help="Sector exposure core + satellite + overlap warnings",
    )
    p_co.add_argument(
        "--threshold", type=float, default=None,
        help=f"Soglia warn (default {CORE_OVERLAP_SECTOR_WARN_PCT})",
    )
    p_co.set_defaults(func=cmd_consolidated)

    # import-csv
    p_imp = sub.add_parser("import-csv", help="Bulk import contributions da CSV")
    p_imp.add_argument("path", help="Path al file CSV")
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.set_defaults(func=cmd_import_csv)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
