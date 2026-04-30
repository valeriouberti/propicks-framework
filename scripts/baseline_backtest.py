#!/usr/bin/env python3
"""Baseline backtest orchestrator (Fase A.3 SIGNAL_ROADMAP).

Runna un set di backtest canonici due volte:

- **v1 (biased)**: senza ``--historical-membership``, universo statico (ticker
  oggi). Numeri attuali pre-Fase A — soggetti a survivorship bias.
- **v2 (unbiased)**: con membership filter point-in-time + DSR computed.

Salva i risultati in:

- ``data/baseline_v1_biased.json``: snapshot numeri pre-Fase A (archivio)
- ``data/baseline_v2.json``: nuova baseline reference per ablation Fase B

Output anche markdown comparison ``docs/BASELINE_COMPARISON.md``.

## Specs canoniche (hardcoded)

1. **momentum_sp500_top30_5y**: top 30 ticker S&P 500 oggi, 5y, momentum
2. **momentum_ndx_top30_5y**: top 30 Nasdaq-100, 5y, momentum

Aggiungere altre specs (contrarian, ETF rotation) in iterazioni successive.
Per ora focus momentum perché è la strategia con `--discover-sp500/--discover-nasdaq`
più esposta a survivorship.

## Usage

    python scripts/baseline_backtest.py
    python scripts/baseline_backtest.py --period 3y --top 20  # versione veloce
    python scripts/baseline_backtest.py --skip-v1            # salva solo v2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Permette esecuzione "python scripts/x.py" da repo root senza pip install
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DATA_DIR = _REPO_ROOT / "data"
DOCS_DIR = _REPO_ROOT / "docs"


def _build_momentum_scoring_fn():
    """Replica scoring momentum core (vedi cli/calibrate.py)."""
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


def _resolve_universe(spec_name: str, top: int) -> list[str]:
    """Estrae lista ticker per spec (sp500 / nasdaq100)."""
    from propicks.market.index_constituents import (
        get_nasdaq100_universe,
        get_sp500_universe,
    )
    if "sp500" in spec_name:
        return get_sp500_universe()[:top]
    elif "nasdaq" in spec_name or "ndx" in spec_name:
        return get_nasdaq100_universe()[:top]
    else:
        raise ValueError(f"spec {spec_name} non gestito")


def _fetch_universe(tickers: list[str], period: str) -> dict:
    """Fetch yfinance diretto, bypass cache (cache copre solo 1y)."""
    import yfinance as yf
    import pandas as pd

    universe = {}
    print(f"  [fetch] {len(tickers)} ticker, period={period}", file=sys.stderr)
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=period, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
        except Exception as e:
            print(f"    ✗ {t}: {e}", file=sys.stderr)
    return universe


def _run_spec(
    spec_name: str,
    top: int,
    period: str,
    *,
    use_membership: bool,
    threshold: float = 60.0,
    use_cross_sectional: bool = False,
    cached_universe: dict | None = None,
) -> dict:
    """Esegue una spec, ritorna dict metriche.

    ``cached_universe`` permette di riusare lo stesso universe fetched tra
    runs multipli (v1 vs v2 vs B.1) senza re-fetch yfinance.
    """
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    from propicks.io.index_membership import build_universe_provider

    if cached_universe is not None:
        universe = cached_universe
        tickers = list(universe.keys())
    else:
        tickers = _resolve_universe(spec_name, top)
        universe = _fetch_universe(tickers, period)
    if not universe:
        return {"error": "empty universe"}

    scoring_fn = _build_momentum_scoring_fn()
    config = BacktestConfig(
        initial_capital=10_000.0,
        score_threshold=threshold,
        use_earnings_gate=False,
        strategy_tag="momentum",
        use_cross_sectional_rank=use_cross_sectional,
    )

    provider = None
    if use_membership:
        index_name = "sp500" if "sp500" in spec_name else "nasdaq100"
        provider = build_universe_provider(index_name)

    t0 = time.time()
    state = simulate_portfolio(
        universe=universe,
        scoring_fn=scoring_fn,
        config=config,
        universe_provider=provider,
    )
    elapsed = time.time() - t0

    metrics = compute_portfolio_metrics(state)
    metrics["spec"] = spec_name
    metrics["membership_filter"] = use_membership
    metrics["period_setting"] = period
    metrics["universe_requested"] = len(tickers)
    metrics["universe_resolved"] = len(universe)
    metrics["threshold"] = threshold
    metrics["backtest_elapsed_s"] = round(elapsed, 2)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Baseline backtest orchestrator (Fase A.3)"
    )
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--threshold", type=float, default=60.0)
    parser.add_argument("--skip-v1", action="store_true", help="Skip v1 biased run")
    parser.add_argument("--specs", default="sp500,ndx", help="comma list")
    args = parser.parse_args()

    spec_map = {
        "sp500": "momentum_sp500_top{top}_{period}",
        "ndx": "momentum_ndx_top{top}_{period}",
    }
    chosen = [s.strip() for s in args.specs.split(",") if s.strip()]
    specs = [
        spec_map[s].format(top=args.top, period=args.period)
        for s in chosen
        if s in spec_map
    ]

    timestamp_iso = datetime.now().isoformat(timespec="seconds")
    git_sha = "unknown"
    try:
        import subprocess
        git_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT
            )
            .decode()
            .strip()
        )
    except Exception:
        pass

    v1_results: dict = {}
    v2_results: dict = {}

    for spec in specs:
        print(f"\n=== SPEC: {spec} ===")

        if not args.skip_v1:
            print(f"  [v1 biased] universe statico (no membership filter)")
            v1_results[spec] = _run_spec(
                spec, args.top, args.period,
                use_membership=False, threshold=args.threshold,
            )
            print(
                f"  [v1] n_trades={v1_results[spec].get('n_trades')} "
                f"sharpe_ann={v1_results[spec].get('sharpe_annualized')} "
                f"total_ret={v1_results[spec].get('total_return_pct')}%"
            )

        print(f"  [v2 unbiased] membership filter + DSR")
        v2_results[spec] = _run_spec(
            spec, args.top, args.period,
            use_membership=True, threshold=args.threshold,
        )
        print(
            f"  [v2] n_trades={v2_results[spec].get('n_trades')} "
            f"sharpe_ann={v2_results[spec].get('sharpe_annualized')} "
            f"total_ret={v2_results[spec].get('total_return_pct')}% "
            f"PSR={v2_results[spec].get('psr')} DSR={v2_results[spec].get('dsr')}"
        )

    # Save JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_v1:
        v1_path = DATA_DIR / "baseline_v1_biased.json"
        v1_payload = {
            "generated_at": timestamp_iso,
            "git_sha": git_sha,
            "phase": "pre-A.1 — biased universe",
            "params": {
                "period": args.period, "top": args.top, "threshold": args.threshold,
            },
            "results": v1_results,
        }
        with open(v1_path, "w") as fh:
            json.dump(v1_payload, fh, indent=2, default=str)
        print(f"\n[saved] {v1_path}")

    v2_path = DATA_DIR / "baseline_v2.json"
    v2_payload = {
        "generated_at": timestamp_iso,
        "git_sha": git_sha,
        "phase": "post-A.1+A.2 — survivorship-corrected + DSR",
        "params": {
            "period": args.period, "top": args.top, "threshold": args.threshold,
        },
        "results": v2_results,
    }
    with open(v2_path, "w") as fh:
        json.dump(v2_payload, fh, indent=2, default=str)
    print(f"[saved] {v2_path}")

    # Markdown comparison
    if not args.skip_v1:
        _write_comparison_md(v1_results, v2_results, timestamp_iso, git_sha, args)

    return 0


def _fmt(v, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}{suffix}"
    return f"{v}{suffix}"


def _write_comparison_md(
    v1_results: dict, v2_results: dict, ts: str, git_sha: str, args
) -> None:
    """Genera docs/BASELINE_COMPARISON.md con tabella per ogni spec."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# Baseline Backtest Comparison — v1 (biased) vs v2 (unbiased)")
    lines.append("")
    lines.append(f"Generated: **{ts}** (git `{git_sha}`)")
    lines.append("")
    lines.append(
        f"Params: period={args.period}, top={args.top}, threshold={args.threshold}"
    )
    lines.append("")
    lines.append("Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase A.3.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for spec in v2_results:
        m1 = v1_results.get(spec, {})
        m2 = v2_results.get(spec, {})
        lines.append(f"## {spec}")
        lines.append("")
        lines.append(
            "| Metric | v1 (biased universe) | v2 (point-in-time) | Δ (v1 − v2) |"
        )
        lines.append("|--------|----------------------|---------------------|-------------|")
        for k, label in [
            ("n_trades", "N trades"),
            ("total_return_pct", "Total return %"),
            ("cagr_pct", "CAGR %"),
            ("sharpe_annualized", "Sharpe annualized"),
            ("sortino_annualized", "Sortino annualized"),
            ("sharpe_per_trade", "Sharpe per-trade"),
            ("psr", "PSR"),
            ("dsr", "DSR"),
            ("max_drawdown_pct", "Max DD %"),
            ("calmar_ratio", "Calmar"),
            ("win_rate", "Win rate"),
            ("profit_factor", "Profit factor"),
            ("avg_duration_days", "Avg duration days"),
        ]:
            v1 = m1.get(k)
            v2 = m2.get(k)
            delta = None
            if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                delta = v1 - v2
            lines.append(
                f"| {label} | {_fmt(v1)} | {_fmt(v2)} | "
                f"{_fmt(delta) if delta is not None else '—'} |"
            )
        lines.append("")
        # Trade breakdown se disponibile (per ticker delta non strutturato qui)
        lines.append(
            f"Universe resolved v1={m1.get('universe_resolved')} | "
            f"v2={m2.get('universe_resolved')}"
        )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Lettura")
    lines.append("")
    lines.append(
        "- **Δ positivo su return/Sharpe** = v1 sovrastima edge per "
        "survivorship bias. Più alto Δ = più bias."
    )
    lines.append(
        "- **DSR (v2 only)**: è il numero da usare per gate decisione. "
        "DSR > 0.95 = strategia robusta a multiple testing."
    )
    lines.append(
        "- **Max DD δ**: se v1 ha max DD migliore di v2, è un altro indicatore "
        "di bias (delisted ticker non visti = drawdown sottostimati)."
    )
    lines.append("")
    out = DOCS_DIR / "BASELINE_COMPARISON.md"
    with open(out, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out}")


if __name__ == "__main__":
    sys.exit(main())
