#!/usr/bin/env python3
"""Ablation Fase C cumulativa — C.0 + C.4 + C.6.

Test cumulative su SP500 top 50 5y:

| Config | C.0 (auto P) | C.4 (OBV) | C.6 (multi-lookback) |
|--------|--------------|-----------|----------------------|
| baseline_v2 | — | — | — |
| C0_only | ✓ | — | — |
| C4_only | — | ✓ | — |
| C6_only | — | — | ✓ |
| C0+C4 | ✓ | ✓ | — |
| C0+C6 | ✓ | — | ✓ |
| C4+C6 | — | ✓ | ✓ |
| C0+C4+C6 | ✓ | ✓ | ✓ |

Skip:
- C.1/C.2/C.3: contrarian-specific, scope diverso
- C.5: breadth confirmation richiede sector mapping per ticker
- C.7: ETF rotation, scope diverso (testato standalone)
- C.8: sector breadth interno, ETF rotation specific
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DATA_DIR = _REPO_ROOT / "data"
DOCS_DIR = _REPO_ROOT / "docs"


def _build_scoring_fn(*, use_obv: bool, use_multi_lookback: bool):
    """Build scoring fn componibile con C.4 (OBV) + C.6 (multi-lookback).

    Riusa score_trend, score_momentum (RSI), score_distance_from_high,
    score_volatility, score_ma_cross dalla scoring.py legacy.

    Sostituzioni:
    - se use_obv: volume sub-score = z-score OBV trend invece di asym ratio
    - se use_multi_lookback: trend/momentum sub-score = ensemble multi-lb
    """
    from propicks.config import (
        ATR_PERIOD, EMA_FAST, EMA_SLOW, RSI_PERIOD, VOLUME_AVG_PERIOD,
        WEIGHT_DISTANCE_HIGH, WEIGHT_MA_CROSS, WEIGHT_MOMENTUM,
        WEIGHT_TREND, WEIGHT_VOLATILITY, WEIGHT_VOLUME,
    )
    from propicks.domain.indicators import (
        compute_atr, compute_ema, compute_rsi,
        compute_obv, compute_multi_lookback_momentum,
    )
    from propicks.domain.scoring import (
        score_distance_from_high, score_ma_cross, score_momentum,
        score_trend, score_volatility, score_volume,
        score_multi_lookback_momentum,
    )

    def _fn(ticker, hist_slice):
        if len(hist_slice) < 252 + 5:
            return None
        close = hist_slice["Close"]
        high = hist_slice["High"]
        low = hist_slice["Low"]
        volume = hist_slice["Volume"]

        ema_fast_s = compute_ema(close, EMA_FAST)
        ema_slow_s = compute_ema(close, EMA_SLOW)
        ema_fast = float(ema_fast_s.iloc[-1])
        ema_slow = float(ema_slow_s.iloc[-1])
        rsi = float(compute_rsi(close, RSI_PERIOD).iloc[-1])
        atr = float(compute_atr(high, low, close, ATR_PERIOD).iloc[-1])

        price = float(close.iloc[-1])
        cur_vol = float(volume.iloc[-1])
        prev_vol = volume.iloc[-VOLUME_AVG_PERIOD - 1 : -1]
        avg_vol = float(prev_vol.mean()) if not prev_vol.empty else cur_vol
        high_52w = float(high.tail(min(252, len(high))).max())
        prev_ema_fast = float(ema_fast_s.iloc[-6]) if len(ema_fast_s) >= 6 else float("nan")
        prev_ema_slow = float(ema_slow_s.iloc[-6]) if len(ema_slow_s) >= 6 else float("nan")

        # Sub-score base (legacy)
        s_trend = score_trend(price, ema_fast, ema_slow)
        s_momentum_rsi = score_momentum(rsi)
        s_distance = score_distance_from_high(price, high_52w)
        s_volatility = score_volatility(atr, price)
        s_ma_cross = score_ma_cross(ema_fast, ema_slow, prev_ema_fast, prev_ema_slow)
        s_volume = score_volume(cur_vol, avg_vol)

        # C.4: OBV trend sostituisce volume asym (se attivo)
        if use_obv:
            obv = compute_obv(close, volume)
            # OBV trend: pct change ultimi 30 bar
            if len(obv) >= 30:
                obv_now = float(obv.iloc[-1])
                obv_30 = float(obv.iloc[-30])
                # Normalize by avg volume × 30 bars per scaling
                denom = abs(obv_30) + (avg_vol * 30)
                if denom > 0:
                    obv_change_norm = (obv_now - obv_30) / denom
                    # Map ±1 → ±50 score (saturate)
                    s_volume = max(0.0, min(100.0, 50.0 + obv_change_norm * 50.0))
                else:
                    s_volume = 50.0
            else:
                s_volume = 50.0

        # C.6: multi-lookback ensemble sostituisce single-window trend
        if use_multi_lookback:
            mom = compute_multi_lookback_momentum(
                close, lookbacks=(21, 63, 126, 252), skip_recent=21
            )
            s_multi = score_multi_lookback_momentum(mom)
            # Sostituisce s_trend (peso resta WEIGHT_TREND)
            s_trend = s_multi

        composite = (
            s_trend * WEIGHT_TREND
            + s_momentum_rsi * WEIGHT_MOMENTUM
            + s_volume * WEIGHT_VOLUME
            + s_distance * WEIGHT_DISTANCE_HIGH
            + s_volatility * WEIGHT_VOLATILITY
            + s_ma_cross * WEIGHT_MA_CROSS
        )
        return max(0.0, min(100.0, composite))

    return _fn


def _run_config(label, *, universe, scoring_fn, threshold, use_xs,
                universe_provider):
    from propicks.backtest.metrics_v2 import compute_portfolio_metrics
    from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
    config = BacktestConfig(
        initial_capital=10_000.0,
        score_threshold=threshold,
        use_earnings_gate=False,
        strategy_tag="momentum",
        use_cross_sectional_rank=use_xs,
    )
    state = simulate_portfolio(
        universe=universe, scoring_fn=scoring_fn, config=config,
        universe_provider=universe_provider,
    )
    return compute_portfolio_metrics(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablation Fase C cumulativa")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=50)
    args = parser.parse_args()

    from scripts.baseline_backtest import _fetch_universe, _resolve_universe
    from propicks.io.index_membership import build_universe_provider
    from propicks.domain.scoring import auto_percentile_for_universe

    spec = f"momentum_sp500_top{args.top}_{args.period}"
    print(f"=== Ablation C cumulativa — {spec} ===")

    tickers = _resolve_universe(spec, args.top)
    universe = _fetch_universe(tickers, args.period)
    if not universe:
        print("[errore] universe vuoto", file=sys.stderr)
        return 1
    n_universe = len(universe)
    print(f"  universe: {n_universe} ticker", file=sys.stderr)

    # C.0: auto-tuned percentile threshold
    auto_pct = auto_percentile_for_universe(n_universe)
    print(f"  C.0 auto_percentile({n_universe}) = {auto_pct:.1f}", file=sys.stderr)

    provider = build_universe_provider("sp500")

    configs = [
        # (label, threshold, use_xs, use_obv, use_multi_lookback)
        ("baseline_v2", 60.0, False, False, False),
        ("C0_auto_percentile", auto_pct, True, False, False),
        ("C4_obv_only", 60.0, False, True, False),
        ("C6_multi_lookback_only", 60.0, False, False, True),
        ("C0_C4", auto_pct, True, True, False),
        ("C0_C6", auto_pct, True, False, True),
        ("C4_C6", 60.0, False, True, True),
        ("C0_C4_C6", auto_pct, True, True, True),
    ]

    results = {}
    sharpes = []
    t0 = time.time()
    for label, thr, use_xs, use_obv, use_ml in configs:
        print(f"  [{label}] thr={thr} xs={use_xs} obv={use_obv} ml={use_ml}",
              file=sys.stderr)
        scoring_fn = _build_scoring_fn(use_obv=use_obv, use_multi_lookback=use_ml)
        m = _run_config(
            label, universe=universe, scoring_fn=scoring_fn,
            threshold=thr, use_xs=use_xs, universe_provider=provider,
        )
        m["config"] = {
            "threshold": thr, "use_cross_sectional": use_xs,
            "use_obv": use_obv, "use_multi_lookback": use_ml,
        }
        results[label] = m
        if m.get("sharpe_per_trade") is not None:
            sharpes.append(m["sharpe_per_trade"])
        print(
            f"    n={m.get('n_trades')} sharpe_ann={m.get('sharpe_annualized')} "
            f"ret={m.get('total_return_pct')}% PSR={m.get('psr')}",
            file=sys.stderr,
        )
    print(f"  total: {time.time()-t0:.1f}s")

    # DSR multi-trial
    n_trials = len(configs)
    var_sr = statistics.variance(sharpes) if len(sharpes) > 1 else 0.01
    print(f"  var(SR) cross={var_sr:.4f}, n_trials={n_trials}")

    from propicks.domain.risk_stats import expected_max_sharpe, _z_critical
    from math import erf, sqrt

    sr_expected = expected_max_sharpe(n_trials, var_sr)
    for label in results:
        m = results[label]
        sr = m.get("sharpe_per_trade")
        psr = m.get("psr")
        if sr is not None and psr is not None and 0 < psr < 1 and sr > 0:
            z_psr = _z_critical(psr)
            if z_psr > 0:
                se = sr / z_psr
                z_dsr = (sr - sr_expected) / se
                m["dsr_multi_trial"] = round(0.5 * (1 + erf(z_dsr / sqrt(2))), 4)
            else:
                m["dsr_multi_trial"] = None
        else:
            m["dsr_multi_trial"] = None

    # Decision rule
    base = results["baseline_v2"]
    base_sharpe = base.get("sharpe_annualized") or 0
    decisions = {}
    for label in results:
        if label == "baseline_v2":
            continue
        r = results[label]
        d_sharpe = (r.get("sharpe_annualized") or 0) - base_sharpe
        dsr_mt = r.get("dsr_multi_trial")
        dsr_p = (1 - dsr_mt) if dsr_mt is not None else None
        keep = d_sharpe >= 0.10 and dsr_p is not None and dsr_p < 0.10
        decisions[label] = {
            "delta_sharpe_ann": round(d_sharpe, 4),
            "dsr_multi_trial": dsr_mt,
            "dsr_p_value": round(dsr_p, 4) if dsr_p is not None else None,
            "keep_decision": keep,
        }

    # Save JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "ablation_c_cumulative.json"
    with open(out_json, "w") as fh:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": "C cumulative — C.0 + C.4 + C.6",
            "spec": spec,
            "params": {
                "period": args.period, "top": args.top,
                "auto_percentile": auto_pct,
            },
            "n_trials": n_trials,
            "var_sr_trials": var_sr,
            "results": results,
            "decisions": decisions,
        }, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_md = DOCS_DIR / "ABLATION_C_CUMULATIVE.md"
    lines = []
    lines.append("# Ablation Fase C — C.0 + C.4 + C.6")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Spec: `{spec}` ({n_universe} ticker)")
    lines.append(f"Auto percentile (C.0): **P{auto_pct:.0f}** for {n_universe} universe")
    lines.append("")
    lines.append("## Risultati")
    lines.append("")
    lines.append(
        "| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR | DSR mt | DSR p |"
    )
    lines.append("|--------|---|------------|-----------|----------|-----|--------|-------|")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for label, *_ in configs:
        m = results[label]
        dsr_mt = m.get("dsr_multi_trial")
        dsr_p = (1 - dsr_mt) if dsr_mt is not None else None
        lines.append(
            f"| {label} | {m.get('n_trades', '—')} | "
            f"{_f(m.get('sharpe_annualized'))} | "
            f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
            f"{_f(m.get('max_drawdown_pct'), '{:.2f}')} | "
            f"{_f(m.get('psr'))} | "
            f"{_f(dsr_mt)} | {_f(dsr_p)} |"
        )

    lines.append("")
    lines.append("## Decision per config")
    lines.append("")
    lines.append("| Config | Δ Sharpe ann | DSR p | Keep? |")
    lines.append("|--------|--------------|-------|-------|")
    for label, dec in decisions.items():
        keep = "✓ KEEP" if dec["keep_decision"] else "✗ DROP"
        lines.append(
            f"| {label} | {dec['delta_sharpe_ann']:+.3f} | "
            f"{_f(dec['dsr_p_value'])} | {keep} |"
        )
    lines.append("")
    lines.append("## Note")
    lines.append("")
    lines.append(
        "- **C.0**: auto-tuned percentile per universe size. Risolve scaling "
        "issue B.1 su universe broader (B.6 finding)"
    )
    lines.append(
        "- **C.4 OBV**: sostituisce volume sub-score asymmetric. NON look-ahead."
    )
    lines.append(
        "- **C.6 multi-lookback**: ensemble 1m/3m/6m/12m skip-recent 21. "
        "Pure mathematical, NON look-ahead. Standard institutional"
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
