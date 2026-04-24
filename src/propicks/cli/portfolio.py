"""CLI per lo stato e la mutazione del portafoglio.

Esempi:
    propicks-portfolio status
    propicks-portfolio risk
    propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \\
        --score-claude 8 --score-tech 75
    propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \\
        --target 210 --strategy TechTitans
    propicks-portfolio update AAPL --stop 180 --target 215
    propicks-portfolio remove AAPL
"""

from __future__ import annotations

import argparse
import sys

from tabulate import tabulate

from propicks.config import (
    ATR_PERIOD,
    MAX_LOSS_WEEKLY_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
)
from propicks.domain.exposure import (
    compute_beta_weighted_exposure,
    compute_concentration_warnings,
    compute_correlation_matrix,
    compute_sector_exposure,
    find_correlated_pairs,
)
from propicks.domain.indicators import compute_atr
from propicks.domain.sizing import (
    calculate_position_size,
    portfolio_market_value,
    portfolio_value,
)
from propicks.domain.stock_rs import YF_SECTOR_TO_KEY
from propicks.domain.trade_mgmt import (
    DEFAULT_FLAT_THRESHOLD_PCT,
    DEFAULT_TIME_STOP_DAYS,
    DEFAULT_TRAILING_ATR_MULT,
    suggest_stop_update,
)
from propicks.io.portfolio_store import (
    add_position,
    load_portfolio,
    remove_position,
    update_position,
)
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_history,
    download_returns,
    get_current_prices,
    get_ticker_beta,
    get_ticker_sector,
)


def show_status(portfolio: dict) -> None:
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)
    cash_pct = cash / total if total else 0.0

    header = [
        ["Capitale totale (cash + invested)", f"{total:.2f}"],
        ["Cash", f"{cash:.2f}  ({cash_pct*100:.1f}%)"],
        ["Invested", f"{total - cash:.2f}"],
        ["Posizioni aperte", f"{len(positions)} / {MAX_POSITIONS}"],
        ["Ultimo aggiornamento", portfolio.get("last_updated") or "-"],
    ]
    print(tabulate(header, tablefmt="simple"))

    if cash_pct < MIN_CASH_RESERVE_PCT:
        print(f"\n[warning] cash sotto riserva minima ({MIN_CASH_RESERVE_PCT*100:.0f}%).")

    if not positions:
        print("\nNessuna posizione aperta.")
        return

    prices = get_current_prices(list(positions.keys()))
    rows = []
    total_pl = 0.0
    for ticker, p in positions.items():
        entry = p["entry_price"]
        shares = p["shares"]
        cur = prices.get(ticker)
        pl_abs = (cur - entry) * shares if cur else None
        pl_pct = (cur - entry) / entry if cur else None
        if pl_abs is not None:
            total_pl += pl_abs
        rows.append([
            ticker, shares, f"{entry:.2f}",
            f"{cur:.2f}" if cur else "-",
            f"{pl_abs:+.2f}" if pl_abs is not None else "-",
            f"{pl_pct*100:+.2f}%" if pl_pct is not None else "-",
            f"{p['stop_loss']:.2f}",
            f"{p['target']:.2f}" if p.get("target") else "-",
            p.get("strategy") or "-",
        ])
    print()
    print(tabulate(
        rows,
        headers=["Ticker", "Shares", "Entry", "Current", "P&L €", "P&L %", "Stop", "Target", "Strategia"],
        tablefmt="github",
    ))
    print(f"\nP&L totale unrealized: {total_pl:+.2f}")

    print()
    print("=" * 70)
    print("COPIA/INCOLLA per prompt Claude 3B (review portafoglio)")
    print("=" * 70)
    print()
    print("| Ticker | Shares | Entry | Current | P&L % | Stop | Target | Strategia |")
    print("|--------|--------|-------|---------|-------|------|--------|-----------|")
    for ticker, p in positions.items():
        cur = prices.get(ticker)
        pl_pct = (cur - p["entry_price"]) / p["entry_price"] if cur else None
        cur_str = f"{cur:.2f}" if cur else "-"
        pl_str = f"{pl_pct*100:+.2f}%" if pl_pct is not None else "-"
        target_str = f"{p['target']:.2f}" if p.get("target") else "-"
        print(
            f"| {ticker} | {p['shares']} | {p['entry_price']:.2f} | {cur_str} | "
            f"{pl_str} | {p['stop_loss']:.2f} | {target_str} | "
            f"{p.get('strategy') or '-'} |"
        )


def show_risk(portfolio: dict) -> None:
    positions = portfolio.get("positions", {})
    total = portfolio_value(portfolio)
    if not positions:
        print("Nessuna posizione aperta.")
        return

    rows = []
    risk_sum = 0.0
    for ticker, p in positions.items():
        entry = p["entry_price"]
        stop = p["stop_loss"]
        shares = p["shares"]
        risk = (entry - stop) * shares
        risk_pct = risk / total if total else 0.0
        risk_sum += risk
        rows.append([
            ticker, shares,
            f"{entry:.2f}", f"{stop:.2f}",
            f"{risk:.2f}",
            f"{risk_pct*100:.2f}%",
        ])

    print(tabulate(
        rows,
        headers=["Ticker", "Shares", "Entry", "Stop", "Rischio €", "% capitale"],
        tablefmt="github",
    ))

    weekly_limit = total * MAX_LOSS_WEEKLY_PCT
    risk_pct = risk_sum / total if total else 0.0
    print()
    print(f"Rischio aggregato a stop: {risk_sum:.2f}  ({risk_pct*100:.2f}% del capitale)")
    print(f"Limite settimanale:       {weekly_limit:.2f}  ({MAX_LOSS_WEEKLY_PCT*100:.0f}%)")
    if risk_sum > weekly_limit:
        print("[warning] rischio aggregato oltre il limite settimanale.")

    _show_exposure(portfolio, positions)


def _show_exposure(portfolio: dict, positions: dict) -> None:
    """Sezione esposizione: settori + beta-weighted + correlazioni pairwise.

    Tutti i fetch (prezzi, sector, beta, returns) avvengono qui nella CLI.
    Le funzioni di ``domain.exposure`` ricevono dati già materializzati.

    Il totale qui è **mark-to-market** (``portfolio_market_value``), non
    cost-basis (``portfolio_value``): i numeratori sector/beta sono
    mark-to-market, denominatore deve matchare. Vedi CLAUDE.md.
    """
    tickers = list(positions.keys())
    print()
    print("=" * 70)
    print("ESPOSIZIONE")
    print("=" * 70)

    prices = get_current_prices(tickers)
    total_capital = portfolio_market_value(portfolio, prices)
    sector_map = {t: get_ticker_sector(t) for t in tickers}
    sector_key_map = {
        t: (YF_SECTOR_TO_KEY.get(s) if s else None) for t, s in sector_map.items()
    }
    sector_exp = compute_sector_exposure(positions, prices, sector_key_map, total_capital)

    if sector_exp:
        rows = sorted(
            ([k, f"{v * 100:.1f}%"] for k, v in sector_exp.items()),
            key=lambda r: float(r[1].rstrip("%")),
            reverse=True,
        )
        print()
        print("Concentrazione settoriale (% capitale):")
        print(tabulate(rows, headers=["Settore", "Esposizione"], tablefmt="github"))
        for w in compute_concentration_warnings(sector_exp):
            print(f"[warning] concentrazione: {w}")

    betas = {t: get_ticker_beta(t) for t in tickers}
    beta_info = compute_beta_weighted_exposure(positions, prices, betas, total_capital)
    print()
    print("Beta-weighted gross long exposure (vs SPX):")
    print(f"  Gross long:           {beta_info['gross_long'] * 100:.1f}% del capitale")
    print(f"  Beta-weighted:        {beta_info['beta_weighted'] * 100:.1f}% del capitale")
    print(f"  Posizioni con beta:   {beta_info['n_positions_with_beta']}/{len(tickers)}")
    if beta_info["default_used_for"]:
        print(
            f"  [info] beta=1.0 usato come fallback per: "
            f"{', '.join(beta_info['default_used_for'])}"
        )

    if len(tickers) >= 2:
        returns = download_returns(tickers, period="6mo")
        corr = compute_correlation_matrix(returns)
        if corr is None:
            print("\n[info] correlazioni: dati insufficienti.")
        else:
            pairs = find_correlated_pairs(corr, threshold=0.7)
            print()
            if pairs:
                print("Pair con |corr| >= 0.7 (rischio concentrato camuffato):")
                rows = [[a, b, f"{c:+.2f}"] for a, b, c in pairs[:10]]
                print(tabulate(rows, headers=["A", "B", "Corr"], tablefmt="github"))
            else:
                print("Nessuna pair sopra soglia |corr| >= 0.7 (diversificazione ok).")


def _print_size_result(ticker: str, r: dict) -> None:
    if not r.get("ok"):
        print(f"[errore] {r.get('error')}", file=sys.stderr)
        return
    rows = [
        ["Ticker", ticker.upper()],
        ["Entry / Stop", f"{r['entry_price']:.2f} / {r['stop_price']:.2f}"],
        ["Rischio per azione", f"{r['risk_per_share']:.2f}"],
        ["Convinzione", f"{r['conviction']} (avg score {r['avg_score']:.1f})"],
        ["Target value (conv.)", f"{r['target_value']:.2f}"],
        ["Max value (15% cap)", f"{r['max_value']:.2f}"],
        ["Cash disponibile", f"{r['cash_available']:.2f}"],
        ["Shares", r["shares"]],
        ["Position value effettivo", f"{r['position_value']:.2f}  ({r['position_pct']*100:.2f}% cap)"],
        ["Rischio totale a stop", f"{r['risk_total']:.2f}  ({r['risk_pct_capital']*100:.2f}% cap)"],
        ["Risk % per trade", f"{r['risk_pct_trade']*100:.2f}%"],
    ]
    print(tabulate(rows, tablefmt="simple"))
    for w in r.get("warnings", []):
        print(f"[warning] {w}")


def cmd_status(_: argparse.Namespace) -> int:
    show_status(load_portfolio())
    return 0


def cmd_risk(_: argparse.Namespace) -> int:
    show_risk(load_portfolio())
    return 0


def cmd_size(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    bucket = "contrarian" if getattr(args, "contrarian", False) else "momentum"

    if getattr(args, "advanced", False):
        return _cmd_size_advanced(args, portfolio, bucket)

    r = calculate_position_size(
        entry_price=args.entry,
        stop_price=args.stop,
        score_claude=args.score_claude,
        score_tech=args.score_tech,
        portfolio=portfolio,
        strategy_bucket=bucket,
    )
    _print_size_result(args.ticker, r)
    return 0 if r.get("ok") else 2


def _cmd_size_advanced(args, portfolio: dict, bucket: str) -> int:
    """Advanced sizing: base + Kelly + vol target + corr penalty (Phase 5)."""
    from propicks.domain.sizing_v2 import (
        apply_correlation_penalty,
        calculate_position_size_advanced,
    )
    from propicks.io.journal_store import load_journal

    trades = load_journal()

    # Returns DataFrame + corr matrix: solo se ci sono posizioni esistenti.
    # Skip i download di rete se portfolio vuoto (niente da correlare).
    positions = portfolio.get("positions", {})
    returns_df = None
    corr_matrix = None
    if positions:
        from propicks.market.yfinance_client import download_returns
        all_tickers = list({*positions.keys(), args.ticker.upper()})
        print(f"[advanced] fetching returns per {len(all_tickers)} ticker...", file=sys.stderr)
        returns_df = download_returns(all_tickers, period="6mo")
        if not returns_df.empty:
            corr_matrix = returns_df.corr()

    # Strategy name: l'user può passarlo via --strategy. Altrimenti uso il bucket.
    strategy_name = getattr(args, "strategy_name", None) or (
        "Contrarian" if bucket == "contrarian" else None
    )

    r = calculate_position_size_advanced(
        entry_price=args.entry,
        stop_price=args.stop,
        score_claude=args.score_claude,
        score_tech=args.score_tech,
        portfolio=portfolio,
        strategy_bucket=bucket,
        strategy_name=strategy_name,
        trades=trades,
        returns_df=returns_df,
        target_vol=getattr(args, "vol_target", 0.15),
    )

    # Applica correlation penalty se disponibile
    if corr_matrix is not None and positions and r.get("ok"):
        total_cap = float(portfolio.get("cash") or 0) + sum(
            float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
            for p in positions.values()
        )
        existing_weights = {}
        if total_cap > 0:
            for ticker, p in positions.items():
                invested = float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
                existing_weights[ticker] = invested / total_cap
        r = apply_correlation_penalty(
            r,
            new_ticker=args.ticker,
            existing_weights=existing_weights,
            corr_matrix=corr_matrix,
        )

    _print_advanced_size_result(args.ticker, r)
    return 0 if r.get("ok") else 2


def _print_advanced_size_result(ticker: str, r: dict) -> None:
    """Print breakdown del calculate_position_size_advanced."""
    from tabulate import tabulate

    if not r.get("ok"):
        print(f"[{ticker}] NOT OK: {r.get('error', 'unknown')}", file=sys.stderr)
        return

    # Riepilogo finale
    base_shares = r.get("base_shares", r["shares"])
    reduction = r.get("shares_reduction", 0)
    binding = r.get("binding_constraint", "—")

    summary = [
        ["Ticker", ticker.upper()],
        ["Strategy bucket", r.get("strategy_bucket")],
        ["Entry × shares", f"{r['entry_price']:.2f} × {r['shares']}"],
        ["Position value", f"€ {r['final_value']:,.2f}"],
        ["Final size %", f"{r['final_size_pct'] * 100:.2f}%"],
        ["Base (naive) size %", f"{r['base_size_pct'] * 100:.2f}%"],
        ["Binding constraint", binding],
    ]
    if reduction > 0:
        summary.append(
            ["Shares reduction", f"{reduction} shares (from {base_shares} → {r['shares']})"]
        )
    print(tabulate(summary, tablefmt="simple"))
    print()

    breakdown = r.get("breakdown", {})

    # Kelly detail
    kelly = breakdown.get("kelly", {})
    if kelly:
        print("--- Kelly per strategia ---")
        if kelly.get("usable"):
            rows = [
                ["n_trades", kelly["n_trades"]],
                ["win_rate", f"{kelly['win_rate'] * 100:.1f}%"],
                ["win/loss ratio", f"{kelly['win_loss_ratio']:.2f}"],
                ["avg_win / avg_loss", f"+{kelly['avg_win_pct']:.2f}% / {kelly['avg_loss_pct']:.2f}%"],
                ["Kelly pct (fractional 25%)", f"{kelly['kelly_pct'] * 100:.2f}%"],
            ]
        else:
            rows = [
                ["Status", "NON USABILE"],
                ["Reason", kelly.get("reason", "—")],
                ["n_trades", kelly.get("n_trades", 0)],
            ]
        print(tabulate(rows, tablefmt="simple"))
        print()

    # Vol info
    vol = breakdown.get("current_vol")
    if vol:
        print("--- Portfolio vol corrente ---")
        rows = [
            ["Vol annualized", f"{vol['vol_annualized'] * 100:.2f}%"],
            ["n_tickers_used", vol["n_tickers_used"]],
            ["Weight coverage", f"{vol.get('total_weight_used', 0) * 100:.2f}%"],
        ]
        print(tabulate(rows, tablefmt="simple"))
        vt = breakdown.get("vol_target")
        if vt:
            print()
            rows = [
                ["Target vol", f"{vt['target_vol'] * 100:.0f}%"],
                ["Current vol", f"{vt['current_vol'] * 100:.2f}%"],
                ["Scale factor", f"{vt['scale_factor']:.3f}"],
                ["Recommendation", vt["recommendation"]],
            ]
            print(tabulate(rows, tablefmt="simple"))
        print()

    # Corr penalty
    corr = breakdown.get("corr_penalty")
    if corr:
        print("--- Correlation penalty ---")
        rows = [
            ["Scale factor", f"{corr.get('scale_factor', 1.0):.3f}"],
            ["Effective duplicate exposure", f"{corr.get('effective_exposure', 0) * 100:.2f}%"],
        ]
        pairs = corr.get("correlated_pairs", [])
        if pairs:
            rows.append(["Correlated with", ", ".join(f"{t}({c:+.2f})" for t, c in pairs[:5])])
        print(tabulate(rows, tablefmt="simple"))
        print()

    if r.get("warnings"):
        print("Warnings:")
        for w in r["warnings"]:
            print(f"  ⚠️  {w}")


def cmd_add(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = add_position(
            portfolio,
            ticker=args.ticker,
            entry_price=args.entry,
            shares=args.shares,
            stop_loss=args.stop,
            target=args.target,
            strategy=args.strategy,
            score_claude=args.score_claude,
            score_tech=args.score_tech,
            catalyst=args.catalyst,
            entry_date=args.entry_date,
        )
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(f"Aggiunto {args.ticker.upper()}: {pos['shares']} azioni @ {pos['entry_price']:.2f}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = remove_position(portfolio, args.ticker)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    print(
        f"Rimosso {args.ticker.upper()}: cash rimborsato al prezzo di entry "
        f"({pos['shares']} x {pos['entry_price']:.2f})."
    )
    print(
        "Promemoria: registra la chiusura nel journal con:\n"
        f"  propicks-journal close {args.ticker.upper()} --exit-price <X> "
        "--exit-date YYYY-MM-DD --reason '<motivo>'"
    )
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    portfolio = load_portfolio()
    try:
        pos = update_position(portfolio, args.ticker, args.stop, args.target)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    target_str = f"{pos['target']:.2f}" if pos.get("target") else "-"
    print(
        f"Aggiornato {args.ticker.upper()}: stop {pos['stop_loss']:.2f}, target {target_str}"
    )
    return 0


def _fetch_current_atr(ticker: str) -> float | None:
    """Scarica history e ritorna l'ATR(14) corrente. None su errore."""
    try:
        hist = download_history(ticker)
    except DataUnavailable:
        return None
    atr = compute_atr(hist["High"], hist["Low"], hist["Close"], ATR_PERIOD)
    val = float(atr.iloc[-1])
    return val if val > 0 else None


def cmd_manage(args: argparse.Namespace) -> int:
    """Suggerisci trailing stop update e flagga time stop su posizioni aperte."""
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    if not positions:
        print("Nessuna posizione aperta.")
        return 0

    prices = get_current_prices(list(positions.keys()))
    suggestions: list[tuple[str, dict, dict, float]] = []

    for ticker, pos in positions.items():
        cur_price = prices.get(ticker)
        if cur_price is None:
            print(f"[warning] {ticker}: prezzo non disponibile, skip", file=sys.stderr)
            continue
        cur_atr = _fetch_current_atr(ticker)
        if cur_atr is None:
            print(f"[warning] {ticker}: ATR non disponibile, skip", file=sys.stderr)
            continue
        suggestion = suggest_stop_update(
            position=pos,
            current_price=cur_price,
            current_atr=cur_atr,
            atr_mult=args.atr_mult,
            max_days_flat=args.time_stop,
            flat_threshold_pct=args.flat_threshold,
        )
        suggestions.append((ticker, pos, suggestion, cur_price))

    if not suggestions:
        return 1

    rows = []
    for ticker, pos, sug, cur in suggestions:
        flags = []
        if sug["stop_changed"]:
            flags.append(f"trail→{sug['new_stop']:.2f}")
        if sug["time_stop_triggered"]:
            flags.append("TIME-STOP")
        if not flags:
            flags.append("hold")
        rows.append([
            ticker,
            f"{pos['entry_price']:.2f}",
            f"{cur:.2f}",
            f"{(cur - pos['entry_price']) / pos['entry_price'] * 100:+.2f}%",
            f"{pos['stop_loss']:.2f}",
            f"{sug['highest_price']:.2f}",
            "Y" if pos.get("trailing_enabled") else "N",
            ", ".join(flags),
        ])
    print(tabulate(
        rows,
        headers=["Ticker", "Entry", "Current", "P&L%", "Stop", "Highest", "Trail?", "Action"],
        tablefmt="github",
    ))

    for ticker, _, sug, _ in suggestions:
        if sug["rationale"]:
            print(f"\n{ticker}:")
            for r in sug["rationale"]:
                print(f"  - {r}")

    if not args.apply:
        print("\n(Run senza modifiche. Usa --apply per scrivere stop/highest_price su portfolio.json.)")
        return 0

    applied = 0
    for ticker, _pos, sug, _cur in suggestions:
        kwargs: dict = {"highest_price": sug["highest_price"]}
        if sug["stop_changed"]:
            kwargs["stop_loss"] = sug["new_stop"]
        try:
            update_position(portfolio, ticker, **kwargs)
            applied += 1
        except ValueError as exc:
            print(f"[errore] {ticker}: {exc}", file=sys.stderr)
    print(f"\nAggiornate {applied}/{len(suggestions)} posizioni.")
    print("Nota: le posizioni con TIME-STOP devono essere chiuse manualmente:")
    print("  propicks-portfolio remove <TICKER>")
    print("  propicks-journal close <TICKER> --exit-price <X> --exit-date YYYY-MM-DD --reason 'time stop'")
    return 0


def cmd_trail(args: argparse.Namespace) -> int:
    """Abilita/disabilita trailing su una posizione specifica."""
    portfolio = load_portfolio()
    enabled = args.action == "enable"
    try:
        pos = update_position(portfolio, args.ticker, trailing_enabled=enabled)
    except ValueError as exc:
        print(f"[errore] {exc}", file=sys.stderr)
        return 2
    state = "abilitato" if enabled else "disabilitato"
    print(f"Trailing {state} su {args.ticker.upper()} (stop attuale {pos['stop_loss']:.2f}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gestione portafoglio.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Stato portafoglio e P&L").set_defaults(func=cmd_status)
    sub.add_parser("risk", help="Rischio aggregato a stop").set_defaults(func=cmd_risk)

    p_size = sub.add_parser("size", help="Calcola position size")
    p_size.add_argument("ticker")
    p_size.add_argument("--entry", type=float, required=True)
    p_size.add_argument("--stop", type=float, required=True)
    p_size.add_argument("--score-claude", type=int, default=7)
    p_size.add_argument("--score-tech", type=int, default=70)
    p_size.add_argument(
        "--contrarian",
        action="store_true",
        help="Applica regole bucket contrarian: size cap 8%%, max 3 pos, 20%% aggregate",
    )
    p_size.add_argument(
        "--advanced",
        action="store_true",
        help=(
            "Phase 5 advanced sizing: applica Kelly fractional da journal + "
            "vol target + corr penalty. Hard caps restano attivi (solo downscale)."
        ),
    )
    p_size.add_argument(
        "--strategy-name",
        help=(
            "Tag strategia per matching Kelly (es. TechTitans, Contrarian). "
            "Se omesso, usa default del bucket."
        ),
    )
    p_size.add_argument(
        "--vol-target",
        type=float,
        default=0.15,
        help="Portfolio vol target annualizzato (default 0.15 = 15%%)",
    )
    p_size.set_defaults(func=cmd_size)

    p_add = sub.add_parser("add", help="Apre una posizione")
    p_add.add_argument("ticker")
    p_add.add_argument("--entry", type=float, required=True)
    p_add.add_argument("--shares", type=int, required=True)
    p_add.add_argument("--stop", type=float, required=True)
    p_add.add_argument("--target", type=float, default=None)
    p_add.add_argument("--strategy", default=None)
    p_add.add_argument("--score-claude", type=int, default=None)
    p_add.add_argument("--score-tech", type=int, default=None)
    p_add.add_argument("--catalyst", default=None)
    p_add.add_argument("--entry-date", default=None, help="YYYY-MM-DD (default: oggi)")
    p_add.set_defaults(func=cmd_add)

    p_upd = sub.add_parser("update", help="Aggiorna stop o target")
    p_upd.add_argument("ticker")
    p_upd.add_argument("--stop", type=float, default=None)
    p_upd.add_argument("--target", type=float, default=None)
    p_upd.set_defaults(func=cmd_update)

    p_rm = sub.add_parser("remove", help="Rimuove una posizione (non chiude il trade)")
    p_rm.add_argument("ticker")
    p_rm.set_defaults(func=cmd_remove)

    p_mgmt = sub.add_parser(
        "manage",
        help="Suggerisci trailing stop update e flagga time stop su posizioni aperte",
    )
    p_mgmt.add_argument(
        "--atr-mult",
        type=float,
        default=DEFAULT_TRAILING_ATR_MULT,
        help=f"Trailing stop in multipli di ATR (default {DEFAULT_TRAILING_ATR_MULT})",
    )
    p_mgmt.add_argument(
        "--time-stop",
        type=int,
        default=DEFAULT_TIME_STOP_DAYS,
        help=f"Time stop in giorni (default {DEFAULT_TIME_STOP_DAYS})",
    )
    p_mgmt.add_argument(
        "--flat-threshold",
        type=float,
        default=DEFAULT_FLAT_THRESHOLD_PCT,
        help=f"Soglia |P&L%%| per considerare flat (default {DEFAULT_FLAT_THRESHOLD_PCT})",
    )
    p_mgmt.add_argument(
        "--apply",
        action="store_true",
        help="Applica le modifiche a portfolio.json (default: dry-run)",
    )
    p_mgmt.set_defaults(func=cmd_manage)

    p_trail = sub.add_parser("trail", help="Abilita/disabilita trailing su una posizione")
    p_trail.add_argument("action", choices=["enable", "disable"])
    p_trail.add_argument("ticker")
    p_trail.set_defaults(func=cmd_trail)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
