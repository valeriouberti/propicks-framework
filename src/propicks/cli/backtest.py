"""CLI thin wrapper per il backtest walk-forward.

Esempi:
    propicks-backtest AAPL                          # default 5y, threshold 60
    propicks-backtest AAPL MSFT NVDA --period 3y
    propicks-backtest AAPL --threshold 70 --json
    propicks-backtest AAPL --stop-atr 2 --target-atr 3 --time-stop 20
"""

from __future__ import annotations

import argparse
import json
import sys

from tabulate import tabulate

from propicks.backtest import backtest_ticker, compute_metrics
from propicks.backtest.metrics import aggregate_metrics
from propicks.market.yfinance_client import DataUnavailable


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "-"


def _fmt_num(x: float | None, digits: int = 2) -> str:
    return f"{x:.{digits}f}" if x is not None else "-"


def _print_result_summary(m: dict) -> None:
    rows = [
        ["Ticker", m["ticker"]],
        ["Periodo", f"{m['period_start']} → {m['period_end']}"],
        ["Segnali generati / trade eseguiti", f"{m['signals_generated']} / {m['n_trades']}"],
        ["Win rate", _fmt_pct(m["win_rate"])],
        ["Profit factor", _fmt_num(m["profit_factor"])],
        ["Avg win / loss", f"{_fmt_pct(m['avg_win_pct'])} / {_fmt_pct(m['avg_loss_pct'])}"],
        ["Expectancy per trade", _fmt_pct(m["expectancy_pct"])],
        ["CAGR", _fmt_pct(m["cagr_pct"])],
        ["Max drawdown", _fmt_pct(m["max_drawdown_pct"])],
        ["Sharpe / Sortino", f"{_fmt_num(m['sharpe'])} / {_fmt_num(m['sortino'])}"],
        ["Avg bars held", _fmt_num(m["avg_bars_held"], 1)],
        ["Exit reasons", ", ".join(f"{k}={v}" for k, v in m["exit_reasons"].items()) or "-"],
        [
            "Equity: initial → final",
            f"{_fmt_num(m['initial_equity'])} → {_fmt_num(m['final_equity'])}",
        ],
    ]
    print(tabulate(rows, tablefmt="simple"))


def _print_trade_table(result) -> None:
    if not result.trades:
        print("Nessun trade eseguito.")
        return
    headers = ["Entry", "Entry $", "Stop", "Target", "Score", "Exit", "Exit $", "Why", "P&L", "Bars"]
    rows = []
    for t in result.trades:
        rows.append(
            [
                t.entry_date.isoformat(),
                f"{t.entry_price:.2f}",
                f"{t.stop_price:.2f}",
                f"{t.target_price:.2f}",
                f"{t.entry_score:.0f}",
                t.exit_date.isoformat() if t.exit_date else "-",
                f"{t.exit_price:.2f}" if t.exit_price else "-",
                t.exit_reason or "-",
                _fmt_pct(t.pnl_pct),
                t.bars_held or "-",
            ]
        )
    print(tabulate(rows, headers=headers, tablefmt="github"))


def _print_ascii_equity(result, width: int = 60, height: int = 10) -> None:
    """Equity curve ASCII — utile per un colpo d'occhio in terminale.

    Normalizza a initial=1.0, campiona ``width`` punti equamente distribuiti,
    e disegna con '*' su griglia height x width.
    """
    eq = result.equity_curve
    if eq.empty:
        return
    normalized = eq / eq.iloc[0]
    step = max(1, len(normalized) // width)
    sampled = normalized.iloc[::step].values
    if len(sampled) < 2:
        return

    lo, hi = float(min(sampled)), float(max(sampled))
    span = hi - lo if hi > lo else 1.0

    grid = [[" "] * len(sampled) for _ in range(height)]
    for x, v in enumerate(sampled):
        y = int((1 - (v - lo) / span) * (height - 1))
        y = max(0, min(height - 1, y))
        grid[y][x] = "*"

    print()
    print(f"Equity curve (normalized, initial=1.0; range {lo:.2f} → {hi:.2f}):")
    for row in grid:
        print("  " + "".join(row))


def _run_portfolio_backtest(args: argparse.Namespace) -> int:
    """Phase 6: portfolio-level backtest + optional walk-forward + MC."""
    from propicks.backtest.costs import CostModel
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    from propicks.backtest.walkforward import (
        monte_carlo_bootstrap,
        walk_forward_split,
    )
    from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi
    from propicks.domain.scoring import (
        score_distance_from_high,
        score_ma_cross,
        score_momentum,
        score_trend,
        score_volatility,
        score_volume,
    )
    from propicks.market.yfinance_client import download_history

    print(f"[portfolio] fetching {len(args.tickers)} ticker…", file=sys.stderr)
    universe: dict = {}
    for t in args.tickers:
        try:
            universe[t.upper()] = download_history(t, period=args.period)
        except DataUnavailable as err:
            print(f"[skip] {err}", file=sys.stderr)

    if not universe:
        print("[errore] nessun ticker disponibile", file=sys.stderr)
        return 1

    # Scoring function point-in-time: replica domain.scoring sui dati fino a bar t
    from propicks.config import (
        ATR_PERIOD,
        EMA_FAST,
        EMA_SLOW,
        RSI_PERIOD,
        VOLUME_AVG_PERIOD,
        WEIGHT_DISTANCE_HIGH,
        WEIGHT_MA_CROSS,
        WEIGHT_MOMENTUM,
        WEIGHT_TREND,
        WEIGHT_VOLATILITY,
        WEIGHT_VOLUME,
    )

    def _scoring_fn(ticker: str, hist_slice):
        if len(hist_slice) < 200:
            return None
        close = hist_slice["Close"]
        high = hist_slice["High"]
        low = hist_slice["Low"]
        volume = hist_slice["Volume"]

        ema_fast = compute_ema(close, EMA_FAST).iloc[-1]
        ema_slow = compute_ema(close, EMA_SLOW).iloc[-1]
        rsi = compute_rsi(close, RSI_PERIOD).iloc[-1]
        atr = compute_atr(high, low, close, ATR_PERIOD).iloc[-1]

        price = float(close.iloc[-1])
        cur_vol = float(volume.iloc[-1])
        prev_vol = volume.iloc[-VOLUME_AVG_PERIOD - 1 : -1]
        avg_vol = float(prev_vol.mean()) if not prev_vol.empty else cur_vol
        high_52w = float(high.tail(min(252, len(high))).max())

        ema_fast_s = compute_ema(close, EMA_FAST)
        ema_slow_s = compute_ema(close, EMA_SLOW)
        prev_ema_fast = float(ema_fast_s.iloc[-6]) if len(ema_fast_s) >= 6 else float("nan")
        prev_ema_slow = float(ema_slow_s.iloc[-6]) if len(ema_slow_s) >= 6 else float("nan")

        composite = (
            score_trend(price, float(ema_fast), float(ema_slow)) * WEIGHT_TREND
            + score_momentum(float(rsi)) * WEIGHT_MOMENTUM
            + score_volume(cur_vol, avg_vol) * WEIGHT_VOLUME
            + score_distance_from_high(price, high_52w) * WEIGHT_DISTANCE_HIGH
            + score_volatility(float(atr), price) * WEIGHT_VOLATILITY
            + score_ma_cross(
                float(ema_fast), float(ema_slow), prev_ema_fast, prev_ema_slow
            ) * WEIGHT_MA_CROSS
        )
        return max(0.0, min(100.0, composite))

    cost_model = (
        CostModel.from_bps(args.tc_bps)
        if args.tc_bps is not None
        else CostModel()
    )
    config = BacktestConfig(
        initial_capital=args.initial_capital,
        score_threshold=args.threshold,
        stop_atr_mult=args.stop_atr,
        target_atr_mult=args.target_atr,
        time_stop_bars=args.time_stop,
        cost_model=cost_model,
        strategy_tag="momentum",
        use_earnings_gate=False,  # backtest storico: earnings non disponibili pre-Phase 8
        use_cross_sectional_rank=args.cross_sectional,
    )

    # Survivorship-bias fix (Fase A.1 SIGNAL_ROADMAP): filtra ticker eligible
    # at-time-T tramite membership history se ``--historical-membership`` attivo.
    universe_provider = None
    if args.historical_membership:
        from propicks.io.index_membership import (
            build_universe_provider,
            count_membership_rows,
            get_membership_date_range,
        )
        idx_name = args.historical_membership.lower()
        rng = get_membership_date_range(idx_name)
        n_rows = count_membership_rows(idx_name)
        if rng is None or n_rows == 0:
            print(
                f"[errore] nessuna membership history per '{idx_name}'. "
                f"Esegui prima: python scripts/import_sp500_history.py",
                file=sys.stderr,
            )
            return 1
        print(
            f"[membership] {idx_name} point-in-time (snapshot range {rng[0]} → "
            f"{rng[1]}, {n_rows:,} rows)",
            file=sys.stderr,
        )
        universe_provider = build_universe_provider(idx_name)

    # Walk-forward mode?
    if args.oos_split:
        print(f"[walkforward] split {args.oos_split * 100:.0f}% train / {(1 - args.oos_split) * 100:.0f}% test")
        wf = walk_forward_split(
            universe=universe,
            scoring_fn=_scoring_fn,
            split_ratio=args.oos_split,
            config=config,
            universe_provider=universe_provider,
        )

        header = [
            ["", "Train", "Test"],
            ["Window", f"{wf.train_window[0]} → {wf.train_window[1]}",
             f"{wf.test_window[0]} → {wf.test_window[1]}"],
            ["Total return", _fmt_pct(wf.train_metrics.get("total_return_pct") / 100 if wf.train_metrics.get("total_return_pct") else None),
             _fmt_pct(wf.test_metrics.get("total_return_pct") / 100 if wf.test_metrics.get("total_return_pct") else None)],
            ["CAGR", _fmt_pct(wf.train_metrics.get("cagr_pct") / 100 if wf.train_metrics.get("cagr_pct") else None),
             _fmt_pct(wf.test_metrics.get("cagr_pct") / 100 if wf.test_metrics.get("cagr_pct") else None)],
            ["Sharpe ann.", _fmt_num(wf.train_metrics.get("sharpe_annualized")),
             _fmt_num(wf.test_metrics.get("sharpe_annualized"))],
            ["Max DD", _fmt_pct(wf.train_metrics.get("max_drawdown_pct") / 100 if wf.train_metrics.get("max_drawdown_pct") else None),
             _fmt_pct(wf.test_metrics.get("max_drawdown_pct") / 100 if wf.test_metrics.get("max_drawdown_pct") else None)],
            ["N trades", wf.train_metrics.get("n_trades"), wf.test_metrics.get("n_trades")],
        ]
        print(tabulate(header, headers="firstrow", tablefmt="github"))
        print()
        sign = "✅ test > train" if wf.degradation_score >= 0 else "⚠️  overfitting suspect"
        print(f"Degradation score: {wf.degradation_score:+.3f}  ({sign})")
        return 0

    # Single-shot portfolio backtest
    state = simulate_portfolio(
        universe=universe,
        scoring_fn=_scoring_fn,
        config=config,
        universe_provider=universe_provider,
    )

    metrics = compute_portfolio_metrics(state)
    _print_portfolio_summary(metrics, state)

    # Monte Carlo opzionale
    if args.monte_carlo > 0:
        print()
        mc = monte_carlo_bootstrap(state.closed_trades, n_samples=args.monte_carlo)
        _print_monte_carlo(mc)

    return 0


def _print_portfolio_summary(metrics: dict, state) -> None:
    if "error" in metrics:
        print(f"[errore] {metrics['error']}", file=sys.stderr)
        return
    print()
    print("=" * 72)
    print("PORTFOLIO BACKTEST — Phase 6")
    print("=" * 72)
    rows = [
        ["Period", f"{metrics['period_start']} → {metrics['period_end']} ({metrics['days']}gg)"],
        ["Capital", f"{metrics['initial_capital']:.2f} → {metrics['final_value']:.2f}"],
        ["Total return", _fmt_pct(metrics['total_return_pct'] / 100)],
        ["CAGR", _fmt_pct((metrics.get('cagr_pct') or 0) / 100)],
        ["Sharpe annualized", _fmt_num(metrics.get('sharpe_annualized'))],
        ["Sortino annualized", _fmt_num(metrics.get('sortino_annualized'))],
        ["Max drawdown", _fmt_pct(metrics['max_drawdown_pct'] / 100)],
        ["Calmar ratio", _fmt_num(metrics.get('calmar_ratio'))],
        ["N trades", metrics['n_trades']],
        ["Win rate", _fmt_pct(metrics['win_rate'])],
        ["Profit factor", _fmt_num(metrics.get('profit_factor'))],
        ["Avg duration (days)", metrics['avg_duration_days']],
        ["Total TC (cost)", f"{metrics['total_commission']:.2f}"],
    ]
    print(tabulate(rows, tablefmt="simple"))

    if metrics.get("by_strategy") and len(metrics["by_strategy"]) > 1:
        print()
        print("Per strategy:")
        strat_rows = []
        for strat, s in metrics["by_strategy"].items():
            strat_rows.append([strat, s["n_trades"], _fmt_pct(s["win_rate"]),
                              f"{s['avg_pnl_pct']:+.2f}%"])
        print(tabulate(strat_rows, headers=["Strategy", "N", "Win rate", "Avg P&L"], tablefmt="github"))

    if metrics.get("exit_reasons"):
        print()
        print("Exit reasons:")
        for reason, n in metrics["exit_reasons"].items():
            print(f"  {reason}: {n}")


def _print_monte_carlo(mc) -> None:
    print("=" * 72)
    print(f"MONTE CARLO BOOTSTRAP — {mc.n_samples} samples")
    print("=" * 72)
    rows = [
        ["Metric", "Mean", "CI 95% lower", "CI 95% upper"],
        ["Sharpe", _fmt_num(mc.sharpe_mean), _fmt_num(mc.sharpe_ci[0]), _fmt_num(mc.sharpe_ci[1])],
        ["Win rate", _fmt_pct(mc.win_rate_mean), _fmt_pct(mc.win_rate_ci[0]), _fmt_pct(mc.win_rate_ci[1])],
        ["Total return", _fmt_pct(mc.total_return_mean), _fmt_pct(mc.total_return_ci[0]), _fmt_pct(mc.total_return_ci[1])],
        ["Max DD", _fmt_pct(mc.max_dd_mean), _fmt_pct(mc.max_dd_ci[0]), _fmt_pct(mc.max_dd_ci[1])],
    ]
    print(tabulate(rows, headers="firstrow", tablefmt="github"))
    print()
    sign = "🟢 robusto" if mc.robustness_score >= 0.7 else "🟡 moderato" if mc.robustness_score >= 0.4 else "🔴 fragile"
    print(f"Robustness score: {mc.robustness_score:.3f}  {sign}")
    print("_(> 0.7 = Sharpe CI_95 vicino al mean → risultato robusto al random)_")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest della strategia single-stock (EOD, no slippage, no commissioni).",
    )
    parser.add_argument("tickers", nargs="+", help="Uno o più ticker")
    parser.add_argument("--period", default="5y", help="Periodo yfinance (default 5y)")
    parser.add_argument("--threshold", type=float, default=60.0, help="Composite minimo (default 60)")
    parser.add_argument("--stop-atr", type=float, default=2.0, help="Stop loss in multipli di ATR (default 2.0)")
    parser.add_argument(
        "--target-atr",
        type=float,
        default=4.0,
        help="Target profit in multipli di ATR (default 4.0 → R:R 2:1 teorico)",
    )
    parser.add_argument(
        "--time-stop",
        type=int,
        default=30,
        help="Time stop: bar max senza progresso (default 30)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON strutturato")
    parser.add_argument("--no-trades", action="store_true", help="Nasconde la tabella trade-by-trade")
    parser.add_argument("--no-equity", action="store_true", help="Nasconde l'ASCII equity curve")
    # Phase 6 — portfolio backtest
    parser.add_argument(
        "--portfolio",
        action="store_true",
        help=(
            "Phase 6: portfolio-level simulation (cross-ticker, TC + slippage, "
            "max positions cap, cash reserve, earnings gate). Invece del loop "
            "single-ticker legacy."
        ),
    )
    parser.add_argument(
        "--tc-bps",
        type=float,
        default=None,
        help="Phase 6: total transaction cost in bps (applied per leg). Default: CostModel standard",
    )
    parser.add_argument(
        "--oos-split",
        type=float,
        default=None,
        help="Phase 6: walk-forward split ratio (es. 0.70 = 70%% train, 30%% test)",
    )
    parser.add_argument(
        "--monte-carlo",
        type=int,
        default=0,
        help="Phase 6: N samples Monte Carlo bootstrap su trade sequence (0 = skip)",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=10_000.0,
        help="Phase 6: capitale iniziale portfolio (default 10.000)",
    )
    parser.add_argument(
        "--historical-membership",
        type=str,
        default=None,
        metavar="INDEX",
        help=(
            "Fase A.1 SIGNAL_ROADMAP: filtra i ticker eligible at-time-T tramite "
            "snapshot membership index (es. 'sp500'). Risolve survivorship bias. "
            "Richiede dati importati con scripts/import_sp500_history.py. "
            "Solo modalità --portfolio."
        ),
    )
    parser.add_argument(
        "--cross-sectional",
        action="store_true",
        help=(
            "Fase B.1 SIGNAL_ROADMAP: interpreta --threshold come PERCENTILE "
            "rank (0-100) cross-sectional invece di score assoluto. "
            "Es. --threshold 80 + --cross-sectional = entry top quintile (P80+). "
            "Edge documentato Jegadeesh-Titman 1993. Solo modalità --portfolio."
        ),
    )
    args = parser.parse_args()

    if args.portfolio:
        return _run_portfolio_backtest(args)

    results = {}
    for t in args.tickers:
        try:
            results[t] = backtest_ticker(
                t,
                period=args.period,
                threshold=args.threshold,
                stop_atr_mult=args.stop_atr,
                target_atr_mult=args.target_atr,
                time_stop_bars=args.time_stop,
            )
        except DataUnavailable as err:
            print(f"[errore] {err}", file=sys.stderr)

    if not results:
        return 1

    if args.json:
        out = {
            "per_ticker": {t: compute_metrics(r) for t, r in results.items()},
            "aggregate": aggregate_metrics(results) if len(results) > 1 else None,
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    for ticker, result in results.items():
        print()
        print("=" * 72)
        print(f"BACKTEST — {ticker}")
        print("=" * 72)
        _print_result_summary(compute_metrics(result))
        if not args.no_trades:
            print()
            _print_trade_table(result)
        if not args.no_equity:
            _print_ascii_equity(result)

    if len(results) > 1:
        print()
        print("=" * 72)
        print("AGGREGATE (pool di tutti i trade)")
        print("=" * 72)
        agg = aggregate_metrics(results)
        rows = [[k, v] for k, v in agg.items() if not isinstance(v, dict)]
        rows.append(
            [
                "exit_reasons",
                ", ".join(f"{k}={v}" for k, v in agg["exit_reasons"].items()) or "-",
            ]
        )
        print(tabulate(rows, tablefmt="simple"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
