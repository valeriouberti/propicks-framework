#!/usr/bin/env python3
"""Quantifica il survivorship bias confrontando backtest with/without membership filter (Fase A.1.6).

Procedura:
1. Fetcha OHLCV per un universo di ticker (default: top mega-cap S&P 500 odierno
   + ticker entrati late come TSLA/META per amplificare il bias)
2. Esegue 2 backtest:
   - **A. Senza filter** (universo statico = ticker oggi-vivi, tradabile da day 1)
   - **B. Con filter point-in-time** (ticker eligible solo when in-index)
3. Confronta metriche: Sharpe, CAGR, total return, n_trades, win rate, max DD
4. Logga il delta come bias quantification

Edge prevista: senza filter, backtest può aprire posizioni su TSLA nel 2015-2019
(non era nel S&P fino al 2020-12-21). Se TSLA performa bene nel periodo (lo
ha fatto), il backtest gonfia returns.

## Usage

    # Default: 10 ticker mix mega-cap + late-add, 2015-2020
    python scripts/quantify_survivorship_bias.py

    # Custom
    python scripts/quantify_survivorship_bias.py \\
        --tickers AAPL MSFT TSLA META NVDA \\
        --start 2015-01-01 --end 2020-12-31 \\
        --db /tmp/propicks_test_a1.db

## Requirements

DB con membership history popolata:
    python scripts/import_sp500_history.py --db /tmp/propicks_test_a1.db
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

# Permette esecuzione "python scripts/x.py" da repo root senza pip install
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# Universo default: mix mega-cap stabili (in-index decennio) + late-add che
# amplificano il bias se non filtrati.
DEFAULT_UNIVERSE = [
    # Stabili in-index (no bias)
    "AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "JNJ",
    # Late additions (bias amplifier)
    "TSLA",   # added 2020-12-21 — bias gigante se backtest 2015-2020
    "META",   # added 2013-12-23 — bias se backtest pre-2014
    "NVDA",   # in-index ma growth esplosivo recente
    "ABBV",   # spinoff 2013 — bias se backtest pre-2013
]


def _fetch_universe(tickers: list[str], start: str, end: str) -> dict:
    """Fetch OHLCV per ogni ticker direttamente via yfinance (bypass cache).

    La cache stdlib del framework copre tipicamente 1y; per smoke test su
    periodi multi-anno (2015-2020) serve fetch fresco. Usa
    ``yfinance.Ticker.history`` direttamente invece di
    ``market.yfinance_client.download_history`` che è cache-aware.
    """
    import yfinance as yf
    import pandas as pd

    universe = {}
    print(f"[fetch] {len(tickers)} ticker, {start} → {end}")
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=start, end=end, auto_adjust=False)
            # Strip tz per coerenza con simulate_portfolio
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
                print(
                    f"  ✓ {t}: {len(df)} bars "
                    f"({df.index[0].date()} → {df.index[-1].date()})"
                )
            else:
                print(f"  ✗ {t}: {len(df)} bars insufficienti", file=sys.stderr)
        except Exception as e:
            print(f"  ✗ {t}: {e}", file=sys.stderr)
    return universe


def _make_scoring_fn():
    """Replica scoring momentum di domain.scoring per backtest point-in-time."""
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
    from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi
    from propicks.domain.scoring import (
        score_distance_from_high,
        score_ma_cross,
        score_momentum,
        score_trend,
        score_volatility,
        score_volume,
    )

    def _scoring_fn(ticker, hist_slice):
        if len(hist_slice) < 200:
            return None
        close = hist_slice["Close"]
        high = hist_slice["High"]
        low = hist_slice["Low"]
        volume = hist_slice["Volume"]

        ema_fast_s = compute_ema(close, EMA_FAST)
        ema_slow_s = compute_ema(close, EMA_SLOW)
        ema_fast = ema_fast_s.iloc[-1]
        ema_slow = ema_slow_s.iloc[-1]
        rsi = compute_rsi(close, RSI_PERIOD).iloc[-1]
        atr = compute_atr(high, low, close, ATR_PERIOD).iloc[-1]

        price = float(close.iloc[-1])
        cur_vol = float(volume.iloc[-1])
        prev_vol = volume.iloc[-VOLUME_AVG_PERIOD - 1 : -1]
        avg_vol = float(prev_vol.mean()) if not prev_vol.empty else cur_vol
        high_52w = float(high.tail(min(252, len(high))).max())

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

    return _scoring_fn


def _format_metrics(label: str, metrics: dict, n_trade_breakdown: dict) -> str:
    """Formatta tabella metriche per print."""
    lines = [
        f"--- {label} ---",
        f"  Period:        {metrics['period_start']} → {metrics['period_end']}",
        f"  Initial → final: {metrics['initial_capital']:.0f} → {metrics['final_value']:.0f}",
        f"  Total return:  {metrics['total_return_pct']:+.2f}%",
        f"  CAGR:          {metrics.get('cagr_pct', 0):+.2f}%",
        f"  Sharpe ann:    {metrics.get('sharpe_annualized', 0):.3f}",
        f"  Sortino ann:   {metrics.get('sortino_annualized', 0):.3f}",
        f"  Max drawdown:  {metrics['max_drawdown_pct']:.2f}%",
        f"  N trades:      {metrics['n_trades']}",
        f"  Win rate:      {metrics['win_rate'] * 100:.1f}%",
        f"  Trade per ticker:",
    ]
    for tk, n in sorted(n_trade_breakdown.items(), key=lambda x: -x[1]):
        lines.append(f"    {tk:8} {n}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Quantifica survivorship bias con/senza membership filter"
    )
    parser.add_argument("--tickers", nargs="+", default=DEFAULT_UNIVERSE)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2020-12-31")
    parser.add_argument(
        "--db",
        default=None,
        help="Path SQLite con membership data (default: config.DB_FILE)",
    )
    parser.add_argument("--index", default="sp500")
    parser.add_argument(
        "--threshold", type=float, default=60.0,
        help="Composite score min per entry (default 60)",
    )
    parser.add_argument(
        "--initial-capital", type=float, default=10_000.0,
    )
    args = parser.parse_args()

    # Validate membership data exists
    from propicks.io.index_membership import (
        build_universe_provider,
        get_membership_date_range,
    )
    rng = get_membership_date_range(args.index, path=args.db)
    if rng is None:
        print(
            f"[ERROR] Nessuna membership data per '{args.index}' nel DB. "
            f"Esegui prima: python scripts/import_sp500_history.py --db {args.db or '<default>'}",
            file=sys.stderr,
        )
        return 1
    print(f"[membership] {args.index} range: {rng[0]} → {rng[1]}")

    # Validate range backtest dentro membership range
    if args.start < rng[0]:
        print(
            f"[WARN] backtest start {args.start} prima del primo snapshot {rng[0]} "
            f"— il provider ritornerà universo vuoto pre-{rng[0]}",
            file=sys.stderr,
        )

    # Fetch universe
    universe = _fetch_universe(args.tickers, args.start, args.end)
    if not universe:
        print("[ERROR] universo vuoto dopo fetch", file=sys.stderr)
        return 1

    # Setup backtest
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio

    config = BacktestConfig(
        initial_capital=args.initial_capital,
        score_threshold=args.threshold,
        use_earnings_gate=False,
        strategy_tag="momentum_bias_test",
    )
    scoring_fn = _make_scoring_fn()
    start_d = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_d = datetime.strptime(args.end, "%Y-%m-%d").date()

    # Run A — without membership filter
    print()
    print("=" * 72)
    print("RUN A — Without membership filter (BIASED — universo statico oggi)")
    print("=" * 72)
    state_a = simulate_portfolio(
        universe=universe,
        scoring_fn=scoring_fn,
        config=config,
        start_date=start_d,
        end_date=end_d,
    )
    metrics_a = compute_portfolio_metrics(state_a)
    breakdown_a: dict[str, int] = {}
    for t in state_a.closed_trades:
        breakdown_a[t.ticker] = breakdown_a.get(t.ticker, 0) + 1

    # Run B — with membership filter
    print()
    print("=" * 72)
    print("RUN B — With membership filter (UNBIASED — point-in-time)")
    print("=" * 72)
    provider = build_universe_provider(args.index, path=args.db)
    state_b = simulate_portfolio(
        universe=universe,
        scoring_fn=scoring_fn,
        config=config,
        start_date=start_d,
        end_date=end_d,
        universe_provider=provider,
    )
    metrics_b = compute_portfolio_metrics(state_b)
    breakdown_b: dict[str, int] = {}
    for t in state_b.closed_trades:
        breakdown_b[t.ticker] = breakdown_b.get(t.ticker, 0) + 1

    # Print summary
    print()
    print(_format_metrics("RUN A (BIASED)", metrics_a, breakdown_a))
    print()
    print(_format_metrics("RUN B (UNBIASED)", metrics_b, breakdown_b))

    # Delta
    print()
    print("=" * 72)
    print("BIAS DELTA (A − B)")
    print("=" * 72)
    delta_ret = metrics_a["total_return_pct"] - metrics_b["total_return_pct"]
    delta_sharpe = (
        (metrics_a.get("sharpe_annualized") or 0)
        - (metrics_b.get("sharpe_annualized") or 0)
    )
    delta_cagr = (
        (metrics_a.get("cagr_pct") or 0) - (metrics_b.get("cagr_pct") or 0)
    )
    delta_trades = metrics_a["n_trades"] - metrics_b["n_trades"]
    print(f"  Δ Total return:  {delta_ret:+.2f}%")
    print(f"  Δ CAGR:          {delta_cagr:+.2f}%")
    print(f"  Δ Sharpe:        {delta_sharpe:+.3f}")
    print(f"  Δ N trades:      {delta_trades:+d}")
    print()
    if abs(delta_sharpe) > 0.1 or abs(delta_ret) > 5:
        print("  → BIAS SIGNIFICATIVO. Backtest senza filter sovrastima edge.")
    else:
        print("  → bias marginale su questo universo / periodo.")

    # Trade breakdown delta
    print()
    print("Trade breakdown delta (A trades − B trades per ticker):")
    all_tickers = set(breakdown_a) | set(breakdown_b)
    for tk in sorted(all_tickers):
        a_n = breakdown_a.get(tk, 0)
        b_n = breakdown_b.get(tk, 0)
        d = a_n - b_n
        flag = "  ← survivorship trades" if d > 0 else ""
        print(f"  {tk:8} A={a_n:3} B={b_n:3} Δ={d:+d}{flag}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
