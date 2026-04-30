#!/usr/bin/env python3
"""Historical scenario replay (Fase E.1 SIGNAL_ROADMAP).

Forced replay della strategia su 3 scenari storici critici per stress-test
drawdown + recovery behavior:

1. **2008 GFC**: Lehman crash + bear market 2008-09. Universe stress credit + equity.
2. **2020 COVID**: V-shape −34% Feb-Mar then rapid recovery.
3. **2022 rate shock**: bear market full year, rotation from growth to value.

## Confronto

Per ciascun scenario:
- baseline_v2 (post survivorship A.1)
- + B.1 cross-sectional rank (auto P88 universe-aware)
- + B.1 + C.6 multi-lookback (defensive momentum ensemble)

Misura: total return, Sharpe, max drawdown, recovery time, n_trades.

## Output

- `data/scenario_replay.json`
- `docs/SCENARIO_REPLAY.md`

## Note

yfinance fetch storico per 2008-2009 può essere parziale/lento. Universe
ridotto a top 20 mega-cap stable (AAPL, MSFT, JNJ, JPM, ecc.) per fattibilità.
Survivorship filter attivo per evitare bias TSLA/NVDA add-late.
"""

from __future__ import annotations

import argparse
import json
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


# Scenari hardcoded — periodo include 6m pre-event + scenario + 12m recovery
SCENARIOS = {
    "2008_GFC": {
        "label": "2008 Global Financial Crisis (Lehman → recovery)",
        "start": "2007-07-01",
        "end": "2010-06-30",
        "key_event": "2008-09-15 Lehman bankruptcy",
    },
    "2020_COVID": {
        "label": "2020 COVID crash + V-shape recovery",
        "start": "2019-07-01",
        "end": "2021-06-30",
        "key_event": "2020-03-23 SP500 bottom",
    },
    "2022_RATE_SHOCK": {
        "label": "2022 Fed rate shock + bear market",
        "start": "2021-07-01",
        "end": "2023-12-31",
        "key_event": "2022-10-13 CPI bottom",
    },
}

# Universe stable mega-cap (in S&P 500 da 10+ anni, solid liquidity)
STABLE_UNIVERSE = [
    "AAPL", "MSFT", "JNJ", "JPM", "PG", "KO", "WMT", "HD",
    "MCD", "V", "MA", "UNH", "PEP", "VZ", "T", "XOM", "CVX",
    "BA", "CAT", "GS",
]


def _fetch_universe(tickers: list[str], start: str, end: str) -> dict:
    import pandas as pd
    import yfinance as yf

    universe = {}
    print(f"  [fetch] {len(tickers)} ticker, {start} → {end}", file=sys.stderr)
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=start, end=end, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
        except Exception as exc:
            print(f"    ✗ {t}: {exc}", file=sys.stderr)
    print(f"    {len(universe)} fetched", file=sys.stderr)
    return universe


def _recovery_metrics(equity_curve: list[tuple]) -> dict:
    """Compute peak-to-trough drawdown + recovery time da equity curve."""
    if not equity_curve:
        return {"max_dd_pct": 0.0, "peak_date": None, "trough_date": None,
                "recovery_date": None, "recovery_days": None}
    dates = [d for d, _ in equity_curve]
    values = [v for _, v in equity_curve]
    peak = values[0]
    peak_idx = 0
    max_dd = 0.0
    trough_idx = 0
    for i, v in enumerate(values):
        if v > peak:
            peak = v
            peak_idx = i
        dd = (v - peak) / peak if peak > 0 else 0
        if dd < max_dd:
            max_dd = dd
            trough_idx = i
    # Recovery: primo bar dopo trough che torna >= peak
    recovery_idx = None
    if trough_idx > 0 and peak_idx < trough_idx:
        peak_value = values[peak_idx]
        for i in range(trough_idx, len(values)):
            if values[i] >= peak_value:
                recovery_idx = i
                break
    return {
        "max_dd_pct": round(max_dd * 100, 2),
        "peak_date": str(dates[peak_idx]) if peak_idx < len(dates) else None,
        "trough_date": str(dates[trough_idx]) if trough_idx < len(dates) else None,
        "recovery_date": str(dates[recovery_idx]) if recovery_idx is not None else None,
        "recovery_days": (
            (dates[recovery_idx] - dates[trough_idx]).days
            if recovery_idx is not None else None
        ),
    }


def _run_config(label, *, universe, scoring_fn, threshold, use_xs, provider):
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
        universe_provider=provider,
    )
    m = compute_portfolio_metrics(state)
    m["recovery"] = _recovery_metrics(state.equity_curve)
    return m


def main() -> int:
    parser = argparse.ArgumentParser(description="Scenario replay Fase E.1")
    parser.add_argument(
        "--scenario", default="all",
        choices=["all", "2008_GFC", "2020_COVID", "2022_RATE_SHOCK"],
    )
    args = parser.parse_args()

    from scripts.ablation_c_cumulative import _build_scoring_fn
    from propicks.io.index_membership import build_universe_provider
    from propicks.domain.scoring import auto_percentile_for_universe

    scenarios_to_run = (
        [args.scenario] if args.scenario != "all" else list(SCENARIOS.keys())
    )

    provider = build_universe_provider("sp500")
    results = {}

    t0 = time.time()
    for scen_name in scenarios_to_run:
        scen = SCENARIOS[scen_name]
        print(f"\n=== {scen_name}: {scen['label']} ===")
        print(f"  Period: {scen['start']} → {scen['end']}")
        print(f"  Key event: {scen['key_event']}")

        universe = _fetch_universe(STABLE_UNIVERSE, scen["start"], scen["end"])
        if not universe:
            print(f"  [skip] {scen_name}: empty universe", file=sys.stderr)
            continue

        auto_pct = auto_percentile_for_universe(len(universe))

        configs = [
            ("baseline_v2", _build_scoring_fn(use_obv=False, use_multi_lookback=False),
             60.0, False),
            ("B1_xs_auto_pct", _build_scoring_fn(use_obv=False, use_multi_lookback=False),
             auto_pct, True),
            ("B1_C6_full", _build_scoring_fn(use_obv=False, use_multi_lookback=True),
             auto_pct, True),
        ]

        scen_results = {}
        for label, sfn, thr, use_xs in configs:
            print(f"  [{label}] thr={thr} xs={use_xs}", file=sys.stderr)
            m = _run_config(
                label, universe=universe, scoring_fn=sfn,
                threshold=thr, use_xs=use_xs, provider=provider,
            )
            scen_results[label] = m
            rec = m["recovery"]
            print(
                f"    n={m.get('n_trades')} sharpe={m.get('sharpe_annualized')} "
                f"ret={m.get('total_return_pct')}% "
                f"DD={rec['max_dd_pct']}% recovery={rec.get('recovery_days')}d",
                file=sys.stderr,
            )

        results[scen_name] = {
            "scenario": scen,
            "auto_percentile": auto_pct,
            "universe_size": len(universe),
            "results": scen_results,
        }

    print(f"\n  total elapsed: {time.time()-t0:.1f}s")

    # Save JSON
    DATA_DIR = _REPO_ROOT / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DATA_DIR / "scenario_replay.json"
    with open(out_json, "w") as fh:
        json.dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": "E.1 — historical scenario replay",
            "stable_universe": STABLE_UNIVERSE,
            "scenarios": results,
        }, fh, indent=2, default=str)
    print(f"[saved] {out_json}")

    # Markdown
    DOCS_DIR = _REPO_ROOT / "docs"
    out_md = DOCS_DIR / "SCENARIO_REPLAY.md"
    lines = []
    lines.append("# Historical Scenario Replay — Fase E.1 SIGNAL_ROADMAP")
    lines.append("")
    lines.append(f"Generated: **{datetime.now().isoformat(timespec='seconds')}**")
    lines.append(f"Universe stable: {len(STABLE_UNIVERSE)} mega-cap")
    lines.append("")
    lines.append("Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §8 Fase E.1.")
    lines.append("")
    lines.append("## Scenari testati")
    lines.append("")

    def _f(v, fmt="{:.3f}"):
        if v is None:
            return "—"
        try:
            return fmt.format(v)
        except (TypeError, ValueError):
            return str(v)

    for scen_name, scen_data in results.items():
        scen = scen_data["scenario"]
        lines.append(f"### {scen_name}")
        lines.append("")
        lines.append(f"**{scen['label']}**")
        lines.append(f"Period: `{scen['start']}` → `{scen['end']}`. "
                     f"Key event: {scen['key_event']}.")
        lines.append(f"Universe resolved: {scen_data['universe_size']} ticker. "
                     f"Auto percentile: P{scen_data['auto_percentile']:.0f}.")
        lines.append("")
        lines.append(
            "| Config | N | Sharpe ann | Tot ret % | Max DD % | "
            "Recovery days | PSR |"
        )
        lines.append(
            "|--------|---|------------|-----------|----------|---------------|-----|"
        )
        for label in scen_data["results"]:
            m = scen_data["results"][label]
            rec = m["recovery"]
            lines.append(
                f"| {label} | {m.get('n_trades', '—')} | "
                f"{_f(m.get('sharpe_annualized'))} | "
                f"{_f(m.get('total_return_pct'), '{:.2f}')} | "
                f"{_f(rec['max_dd_pct'], '{:.2f}')} | "
                f"{_f(rec.get('recovery_days'), '{:.0f}')} | "
                f"{_f(m.get('psr'))} |"
            )
        lines.append("")

    lines.append("## Lettura")
    lines.append("")
    lines.append(
        "- **Recovery days** = giorni dal trough al ritorno a peak pre-crash. "
        "Più rapido = strategia robusta a recovery"
    )
    lines.append(
        "- **Max DD scenario** vs Max DD baseline 5y: discrepanza grande "
        "= drawdown protection sub-optimal"
    )
    lines.append(
        "- **B.1 + C.6 vs baseline**: cross-sectional + multi-lookback "
        "dovrebbero ridurre DD su crash event (mom flip earlier)"
    )
    lines.append("")
    lines.append("## Caveat")
    lines.append("")
    lines.append(
        "- Universe stable (top 20 mega-cap) sotto-rappresentativo: durante "
        "GFC 2008 banks/financials hanno fatto −60%, mega-cap tech +30% "
        "(Apple+google). Bias positivo per stable selection"
    )
    lines.append(
        "- yfinance pre-2010 può avere data quality issue (split adjustment, "
        "delisted not present)"
    )
    lines.append(
        "- Earnings gate disabilitato (storical earnings not available) "
        "→ trades opened during earnings"
    )

    with open(out_md, "w") as fh:
        fh.write("\n".join(lines))
    print(f"[saved] {out_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
