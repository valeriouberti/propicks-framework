#!/usr/bin/env python3
"""Smoke test macro overlay (Fase B.5.4 SIGNAL_ROADMAP).

Calcola z-scores macro features + macro_fit per ogni sector ETF.
Confronta ranking attuale via macro_fit con ranking storico.

Verifica:
1. Pipeline end-to-end (FRED + yfinance commodities → z → macro_fit per ETF)
2. Sensibilità sector matrix coerente con expectation:
   - XLF score alto quando yield_slope alto + HY OAS calm
   - XLE score alto quando oil/gold + USD weak
   - XLK score alto quando HY OAS calm + yield slope basso
3. Latest reading sui sector US.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke macro overlay")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-04-29")
    args = parser.parse_args()

    import pandas as pd
    import yfinance as yf

    from propicks.domain.macro_overlay import (
        SECTOR_SENSITIVITY_MATRIX,
        compute_macro_zscores,
        macro_fit_score,
        macro_fit_series,
    )
    from propicks.market.fred_client import fetch_fred_series

    print(f"=== Macro overlay smoke — {args.start} → {args.end} ===")

    # 1. FRED features
    print("[fred] yield slope, USD, HY OAS...")
    yslope_d = fetch_fred_series("T10Y2Y", start=args.start, end=args.end)
    yslope = pd.Series(yslope_d, dtype=float)
    yslope.index = pd.to_datetime(yslope.index)

    usd_d = fetch_fred_series("DTWEXBGS", start=args.start, end=args.end)
    usd = pd.Series(usd_d, dtype=float)
    usd.index = pd.to_datetime(usd.index)

    hy_d = fetch_fred_series("BAMLH0A0HYM2", start=args.start, end=args.end)
    hy = pd.Series(hy_d, dtype=float)
    hy.index = pd.to_datetime(hy.index)

    # 2. Commodities ratios
    print("[yfinance] copper, gold, oil futures...")
    copper = yf.Ticker("HG=F").history(start=args.start, end=args.end, auto_adjust=False)
    gold = yf.Ticker("GC=F").history(start=args.start, end=args.end, auto_adjust=False)
    oil = yf.Ticker("CL=F").history(start=args.start, end=args.end, auto_adjust=False)
    for df in (copper, gold, oil):
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

    cu_au = (copper["Close"] / gold["Close"]).rename("copper_gold")
    oil_au = (oil["Close"] / gold["Close"]).rename("oil_gold")

    print(
        f"  yslope: {len(yslope)} obs, latest={yslope.iloc[-1]:.2f}\n"
        f"  usd: {len(usd)} obs, latest={usd.iloc[-1]:.2f}\n"
        f"  hy_oas: {len(hy)} obs, latest={hy.iloc[-1]:.2f}\n"
        f"  copper/gold: {len(cu_au)} obs, latest={cu_au.iloc[-1]:.5f}\n"
        f"  oil/gold: {len(oil_au)} obs, latest={oil_au.iloc[-1]:.5f}"
    )

    # 3. Compute z-scores
    print("[zscore] rolling 252d...")
    macro_z_df = compute_macro_zscores(
        features={
            "yield_slope": yslope,
            "usd": usd,
            "hy_oas": hy,
            "copper_gold": cu_au,
            "oil_gold": oil_au,
        },
        window=252,
    )
    print(f"  z-score df: {macro_z_df.shape[0]} rows, cols={list(macro_z_df.columns)}")
    print()
    print("Latest z-scores (last row):")
    if not macro_z_df.empty:
        last = macro_z_df.iloc[-1]
        for col in macro_z_df.columns:
            print(f"  {col:>15}: {last[col]:+.3f}")

    # 4. Per-ETF macro_fit
    print()
    print("=== Macro fit score per ETF (sorted desc) ===")
    if not macro_z_df.empty:
        last_z = {
            col: float(macro_z_df.iloc[-1][col])
            for col in macro_z_df.columns
            if pd.notna(macro_z_df.iloc[-1][col])
        }
        rankings = []
        for etf in SECTOR_SENSITIVITY_MATRIX:
            score = macro_fit_score(etf, last_z)
            rankings.append((etf, score))
        rankings.sort(key=lambda x: -x[1])
        for etf, score in rankings:
            sens = SECTOR_SENSITIVITY_MATRIX[etf]
            top_drivers = sorted(sens.items(), key=lambda x: -abs(x[1]))[:2]
            drivers_str = ", ".join(f"{f}({s:+.1f})" for f, s in top_drivers)
            print(f"  {etf:>5}: {score:.1f}   drivers: {drivers_str}")

    # 5. Save series for inspection
    out_csv = _REPO_ROOT / "data" / "macro_zscore_history.csv"
    macro_z_df.to_csv(out_csv)
    print(f"\n[saved] {out_csv}")

    # 6. Per-ETF macro_fit series + save
    fits = {}
    for etf in SECTOR_SENSITIVITY_MATRIX:
        fits[etf] = macro_fit_series(etf, macro_z_df)
    fits_df = pd.DataFrame(fits)
    out_fits_csv = _REPO_ROOT / "data" / "macro_fit_history.csv"
    fits_df.to_csv(out_fits_csv)
    print(f"[saved] {out_fits_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
