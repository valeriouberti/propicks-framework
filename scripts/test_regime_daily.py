#!/usr/bin/env python3
"""Smoke test regime daily composite (Fase B.3.4 SIGNAL_ROADMAP).

Confronta regime daily composite (HY OAS + breadth interno + VIX) con turning
point storici noti:

- **2020-03-23**: COVID bottom (S&P -34% peak-to-trough, recovery V-shape)
- **2022-10-13**: CPI bottom (rate fear → bear market end)
- **2020-09-02**: top tech 2020 pre-correction
- **2022-01-04**: top S&P 2022 pre-bear

Misura: quando il composite z-score / regime_code ha "girato" rispetto al
turning point reale. Daily anticipa weekly idealmente di 1-3 settimane.

## Dati

- HY OAS: FRED `BAMLH0A0HYM2` (gratis, daily)
- VIX: FRED `VIXCLS` (daily)
- Breadth: calcolato internamente su universe S&P 500 corrente (TOP 50 mega-cap
  per fattibilità yfinance fetch — full 500 sarebbe lento)

Caveat: breadth su top 50 ≠ breadth full S&P 500. Mega-cap meno volatili,
breadth reading più conservativo. Per spec proper serve full universe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test regime daily composite")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2026-04-29")
    parser.add_argument("--top", type=int, default=50,
                        help="Top N S&P per breadth interno (default 50)")
    args = parser.parse_args()

    import pandas as pd
    import yfinance as yf

    from propicks.domain.breadth import breadth_series
    from propicks.domain.regime_composite import compute_regime_series
    from propicks.market.fred_client import fetch_fred_series
    from propicks.market.index_constituents import get_sp500_universe

    print(f"=== Regime daily composite — {args.start} → {args.end} ===")

    # 1. Fetch HY OAS + VIX
    print("[fred] HY OAS...")
    hy_dict = fetch_fred_series("BAMLH0A0HYM2", start=args.start, end=args.end)
    hy = pd.Series(hy_dict, dtype=float)
    hy.index = pd.to_datetime(hy.index)
    print(f"  hy_oas: {len(hy)} obs ({hy.index.min().date()} → {hy.index.max().date()})")

    print("[fred] VIX...")
    vix_dict = fetch_fred_series("VIXCLS", start=args.start, end=args.end)
    vix = pd.Series(vix_dict, dtype=float)
    vix.index = pd.to_datetime(vix.index)
    print(f"  vix: {len(vix)} obs ({vix.index.min().date()} → {vix.index.max().date()})")

    # 2. Breadth interno (top N S&P)
    print(f"[breadth] yfinance fetch {args.top} ticker...")
    tickers = get_sp500_universe()[: args.top]
    universe = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=args.start, end=args.end, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
        except Exception as e:
            print(f"  ✗ {t}: {e}", file=sys.stderr)
    print(f"  universe: {len(universe)} ticker fetched")

    breadth = breadth_series(universe, window=200)
    print(f"  breadth: {len(breadth)} obs, range [{breadth.min():.1f}, {breadth.max():.1f}]")

    # 3. Compute regime composite
    print("[regime] composite z-score...")
    result = compute_regime_series(
        hy_oas=hy, breadth=breadth, vix=vix, zscore_window=252,
    )
    print(f"  rows: {len(result)}, valid: {result['regime_code'].notna().sum()}")
    print(f"  regime_code distribution:")
    print(result["regime_code"].value_counts().sort_index())

    # 4. Turning point analysis
    print()
    print("=== TURNING POINT ANALYSIS ===")
    turning_points = [
        ("2020-03-23", "COVID bottom (V-shape recovery)"),
        ("2020-09-02", "Tech top 2020 pre-correction"),
        ("2022-01-04", "S&P top 2022 pre-bear"),
        ("2022-10-13", "CPI bottom (bear market end)"),
        ("2024-08-05", "Yen carry unwind / vol spike"),
    ]
    for tp_str, label in turning_points:
        tp = pd.Timestamp(tp_str)
        if tp not in result.index:
            # Find nearest
            idx = result.index.get_indexer([tp], method="nearest")[0]
            if idx < 0 or idx >= len(result):
                print(f"  {tp_str} ({label}): out of range")
                continue
            tp_actual = result.index[idx]
        else:
            tp_actual = tp
        # Window 30d before, 30d after
        window_lo = tp_actual - pd.Timedelta(days=30)
        window_hi = tp_actual + pd.Timedelta(days=30)
        sub = result.loc[window_lo:window_hi]
        if sub.empty:
            print(f"  {tp_str} ({label}): no data in window")
            continue
        # Find composite_z value AT tp + min/max in window
        z_at = result.loc[tp_actual, "composite_z"] if tp_actual in result.index else None
        z_min_idx = sub["composite_z"].idxmin() if sub["composite_z"].notna().any() else None
        z_max_idx = sub["composite_z"].idxmax() if sub["composite_z"].notna().any() else None
        regime_at = result.loc[tp_actual, "regime_label"] if tp_actual in result.index else None
        print(
            f"  {tp_str} ({label}): "
            f"composite_z={z_at:.3f} regime={regime_at}"
        )
        if z_min_idx is not None:
            lag_min = (z_min_idx - tp_actual).days
            print(f"    z min @ {z_min_idx.date()} (lag {lag_min:+d}d): "
                  f"{sub.loc[z_min_idx, 'composite_z']:.3f} {sub.loc[z_min_idx, 'regime_label']}")
        if z_max_idx is not None:
            lag_max = (z_max_idx - tp_actual).days
            print(f"    z max @ {z_max_idx.date()} (lag {lag_max:+d}d): "
                  f"{sub.loc[z_max_idx, 'composite_z']:.3f} {sub.loc[z_max_idx, 'regime_label']}")

    # 5. Salva CSV per inspection visiva
    out_csv = _REPO_ROOT / "data" / "regime_composite_history.csv"
    result.to_csv(out_csv)
    print(f"\n[saved] {out_csv}")

    # 6. Sample finale
    print()
    print("=== Latest 10 days ===")
    print(result.tail(10).to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
