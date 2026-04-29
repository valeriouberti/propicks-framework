"""CLI thin wrapper per threshold calibration (Fase A.2 SIGNAL_ROADMAP).

Esegue threshold sweep + DSR analysis su un universo + strategia. Stampa
tabella risultati + recommendation.

## Esempi

    # Default: thresholds 40-80 step 5, single shot, momentum
    propicks-calibrate AAPL MSFT NVDA GOOGL --period 5y

    # Range custom
    propicks-calibrate AAPL --thresholds "50,55,60,65,70,75"

    # CPCV per nested validation (più rigoroso, ~10x più lento)
    propicks-calibrate AAPL MSFT NVDA --use-cpcv --cpcv-groups 6

    # Discover-mode + survivorship fix
    propicks-calibrate --discover-sp500 --top 50 --historical-membership sp500

## Output

Tabella per threshold con: n_trades, Sharpe per-trade, Sharpe annualized,
win rate, total return, PSR, DSR. Recommendation evidenziata con ★.

PSR e DSR sono dataclass-driven da ``domain.risk_stats`` (Bailey-Lopez).
Il CLI è informativo — NON modifica ``config.MIN_SCORE_TECH`` automaticamente.
La decisione di promuovere il threshold raccomandato in produzione resta
manuale (richiede backtesting con OOS validation prima).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime


def _parse_thresholds(spec: str) -> list[float]:
    """Parse spec ``"40,50,60"`` o ``"40:80:5"`` → list[float].

    Forma con due punti = range start:end:step (end incluso se cade su step).
    Forma con virgole = lista esplicita.
    """
    spec = spec.strip()
    if ":" in spec:
        parts = spec.split(":")
        if len(parts) != 3:
            raise ValueError(f"range spec '{spec}' deve essere start:end:step")
        start, end, step = float(parts[0]), float(parts[1]), float(parts[2])
        if step <= 0:
            raise ValueError(f"step ({step}) deve essere > 0")
        out: list[float] = []
        x = start
        while x <= end + 1e-9:
            out.append(round(x, 4))
            x += step
        return out
    return [float(p.strip()) for p in spec.split(",") if p.strip()]


def _build_momentum_scoring_fn():
    """Crea scoring_fn per strategia momentum (replica domain.scoring)."""
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

    def _fn(ticker, hist_slice):
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

    return _fn


def _fetch_universe(tickers: list[str], period: str, *, bypass_cache: bool) -> dict:
    """Fetch OHLCV per ogni ticker. Se ``bypass_cache``, usa yfinance diretto.

    Cache framework copre tipicamente 1y; per backtest multi-anno servono
    fetch fresh con ``period`` esplicito.
    """
    if bypass_cache:
        import yfinance as yf
        import pandas as pd
        universe = {}
        print(f"[fetch] yfinance direct: {len(tickers)} ticker, period={period}", file=sys.stderr)
        for t in tickers:
            try:
                df = yf.Ticker(t).history(period=period, auto_adjust=False)
                if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                if len(df) >= 200:
                    universe[t.upper()] = df
                else:
                    print(f"  ✗ {t}: {len(df)} bars insufficienti", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ {t}: {e}", file=sys.stderr)
        return universe
    else:
        from propicks.market.yfinance_client import DataUnavailable, download_history
        universe = {}
        print(f"[fetch] cache-aware: {len(tickers)} ticker, period={period}", file=sys.stderr)
        for t in tickers:
            try:
                universe[t.upper()] = download_history(t, period=period)
            except DataUnavailable as e:
                print(f"  ✗ {t}: {e}", file=sys.stderr)
        return universe


def _resolve_universe_provider(index_name: str | None):
    """Crea universe_provider point-in-time se index richiesto."""
    if not index_name:
        return None
    from propicks.io.index_membership import (
        build_universe_provider,
        count_membership_rows,
        get_membership_date_range,
    )
    rng = get_membership_date_range(index_name.lower())
    n = count_membership_rows(index_name.lower())
    if rng is None or n == 0:
        print(
            f"[errore] nessuna membership history per '{index_name}'. "
            f"Esegui prima: python scripts/import_sp500_history.py",
            file=sys.stderr,
        )
        return "MISSING"
    print(
        f"[membership] {index_name} range {rng[0]} → {rng[1]} ({n:,} rows)",
        file=sys.stderr,
    )
    return build_universe_provider(index_name.lower())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Threshold calibration con DSR (Fase A.2 SIGNAL_ROADMAP)."
    )
    parser.add_argument("tickers", nargs="*", help="Ticker espliciti")
    parser.add_argument(
        "--discover-sp500",
        action="store_true",
        help="Universo S&P 500 corrente (richiede --top per limit)",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Limita universe a top N ticker per market cap (default 20)",
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        default="40:80:5",
        help="Range threshold (es. '40:80:5' = 40,45,...,80) o lista (es. '50,60,70')",
    )
    parser.add_argument(
        "--period", default="5y",
        help="Periodo yfinance fetch (default 5y)",
    )
    parser.add_argument(
        "--strategy", default="momentum",
        choices=["momentum"],
        help="Strategia (per ora solo momentum; contrarian/etf in roadmap)",
    )
    parser.add_argument(
        "--use-cpcv", action="store_true",
        help="Combinatorial Purged CV per nested validation (~10x più lento)",
    )
    parser.add_argument(
        "--cpcv-groups", type=int, default=6,
        help="CPCV n_groups (default 6)",
    )
    parser.add_argument(
        "--cpcv-test-groups", type=int, default=2,
        help="CPCV n_test_groups (default 2 → C(6,2)=15 path)",
    )
    parser.add_argument(
        "--cpcv-embargo", type=int, default=5,
        help="CPCV embargo days (default 5)",
    )
    parser.add_argument(
        "--historical-membership",
        type=str, default=None, metavar="INDEX",
        help="Membership index point-in-time (es. 'sp500')",
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Date start (YYYY-MM-DD). Default: tutto il period.",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="Date end (YYYY-MM-DD). Default: tutto il period.",
    )
    parser.add_argument(
        "--bypass-cache", action="store_true", default=True,
        help="Skip framework cache, fetch yfinance diretto (default ON per range multi-anno)",
    )
    parser.add_argument(
        "--use-cache", action="store_true",
        help="Forza usage cache framework (override --bypass-cache)",
    )
    parser.add_argument(
        "--min-trades", type=int, default=30,
        help="Minimo trade per recommendation tier 1/2 (default 30)",
    )
    parser.add_argument(
        "--target-dsr", type=float, default=0.95,
        help="DSR threshold per recommendation tier 1 (default 0.95)",
    )
    parser.add_argument(
        "--initial-capital", type=float, default=10_000.0,
    )
    args = parser.parse_args()

    # Resolve tickers
    tickers = list(args.tickers)
    if args.discover_sp500:
        from propicks.market.index_constituents import get_sp500_universe
        sp500 = get_sp500_universe()
        tickers = sp500[: args.top] if args.top > 0 else sp500
        print(f"[discover] S&P 500 top {args.top}: {tickers[:5]}...", file=sys.stderr)
    if not tickers:
        print("[errore] nessun ticker specificato", file=sys.stderr)
        return 1

    # Parse thresholds
    try:
        thresholds = _parse_thresholds(args.thresholds)
    except ValueError as e:
        print(f"[errore] {e}", file=sys.stderr)
        return 1
    print(f"[thresholds] {len(thresholds)}: {thresholds}", file=sys.stderr)

    # Resolve universe_provider
    provider = _resolve_universe_provider(args.historical_membership)
    if provider == "MISSING":
        return 1

    # Fetch universe
    bypass = not args.use_cache and args.bypass_cache
    universe = _fetch_universe(tickers, args.period, bypass_cache=bypass)
    if not universe:
        print("[errore] universo vuoto dopo fetch", file=sys.stderr)
        return 1
    print(f"[universe] {len(universe)} ticker", file=sys.stderr)

    # Resolve dates
    start_d = (
        datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    )
    end_d = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None

    # Build scoring + config
    if args.strategy == "momentum":
        scoring_fn = _build_momentum_scoring_fn()
    else:
        print(f"[errore] strategia {args.strategy} non supportata", file=sys.stderr)
        return 1

    from propicks.backtest.calibration import (
        calibrate_threshold,
        format_calibration_report,
    )
    from propicks.backtest.portfolio_engine import BacktestConfig

    base_config = BacktestConfig(
        initial_capital=args.initial_capital,
        score_threshold=thresholds[0],  # placeholder; sweep override
        use_earnings_gate=False,
        strategy_tag=args.strategy,
    )

    # Progress callback
    def _cb(curr, total, thr):
        print(f"  [{curr}/{total}] threshold={thr:.1f}", file=sys.stderr, end="\r")

    print(
        f"[calibrate] CPCV={'on' if args.use_cpcv else 'off'} "
        f"strategy={args.strategy} period={args.period}",
        file=sys.stderr,
    )

    result = calibrate_threshold(
        universe=universe,
        scoring_fn=scoring_fn,
        thresholds=thresholds,
        base_config=base_config,
        universe_provider=provider if provider != "MISSING" else None,
        start_date=start_d,
        end_date=end_d,
        use_cpcv=args.use_cpcv,
        cpcv_n_groups=args.cpcv_groups,
        cpcv_n_test_groups=args.cpcv_test_groups,
        cpcv_embargo_days=args.cpcv_embargo,
        min_trades=args.min_trades,
        target_dsr=args.target_dsr,
        progress_cb=_cb,
    )

    print()  # newline post progress
    print(format_calibration_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
