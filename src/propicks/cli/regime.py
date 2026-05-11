"""CLI thin wrapper per Regime Composite (Fase B.3 SIGNAL_ROADMAP).

Mirror di ``pages/15_Regime_Composite.py``: calcola z-score giornaliero
combinato (HY OAS + breadth + VIX) e classifica in 5-bucket regime.

Subcomandi:
    composite — full series fetch + display latest + bucket distribution
    check     — short-form: fetch ultimi 6 mesi e mostra solo latest reading

Esempi:
    propicks-regime composite
    propicks-regime composite --start 2023-01-01 --top-n 50
    propicks-regime composite --weights 0.5,0.3,0.2 --json
    propicks-regime check
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

import pandas as pd
from tabulate import tabulate


def _parse_weights(spec: str) -> tuple[float, float, float]:
    """Parse 'HY,BR,VIX' string → tuple. Validate sum > 0."""
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "weights deve essere 3 valori separati da virgola: HY,BR,VIX"
        )
    try:
        w = tuple(float(p) for p in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"weights non numerici: {e}") from e
    if sum(w) <= 0:
        raise argparse.ArgumentTypeError("weights sum must be > 0")
    return w  # type: ignore[return-value]


def _fetch_data(
    start: str, end: str, top_n: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Fetch HY OAS + VIX (FRED) + breadth (yfinance top-N S&P).

    Lazy import per evitare lentezza help command.
    """
    import yfinance as yf

    from propicks.domain.breadth import breadth_series
    from propicks.market.fred_client import fetch_fred_series
    from propicks.market.index_constituents import get_sp500_universe

    print(f"[fetch] FRED HY OAS (BAMLH0A0HYM2) {start} → {end}…", file=sys.stderr)
    hy_d = fetch_fred_series("BAMLH0A0HYM2", start=start, end=end)
    hy = pd.Series(hy_d, dtype=float)
    hy.index = pd.to_datetime(hy.index)

    print(f"[fetch] FRED VIX (VIXCLS) {start} → {end}…", file=sys.stderr)
    vix_d = fetch_fred_series("VIXCLS", start=start, end=end)
    vix = pd.Series(vix_d, dtype=float)
    vix.index = pd.to_datetime(vix.index)

    print(f"[fetch] yfinance {top_n} ticker S&P (breadth)…", file=sys.stderr)
    tickers = get_sp500_universe()[:top_n]
    universe: dict = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=start, end=end, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
        except Exception:
            pass
    print(f"[fetch] {len(universe)} ticker valid", file=sys.stderr)

    breadth = breadth_series(universe, window=200)
    return hy, breadth, vix


def cmd_composite(args: argparse.Namespace) -> int:
    """Esegue pipeline completa e ritorna serie + tabella regime distribution."""
    from propicks.domain.regime_composite import compute_regime_series

    end_eff = args.end or date.today().strftime("%Y-%m-%d")

    try:
        hy, breadth, vix = _fetch_data(args.start, end_eff, int(args.top_n))
    except Exception as e:
        print(f"[errore] fetch fallito: {e}", file=sys.stderr)
        return 1

    result = compute_regime_series(
        hy_oas=hy,
        breadth=breadth,
        vix=vix,
        zscore_window=int(args.zscore_window),
        weights=args.weights,
    )

    if result.empty or result["composite_z"].dropna().empty:
        print("[errore] no data — verify date range / fetch failure", file=sys.stderr)
        return 1

    valid = result.dropna(subset=["composite_z"])
    latest = valid.iloc[-1]

    if args.json:
        # Distribution
        code_counts = valid["regime_code"].value_counts().sort_index()
        label_map = {1: "STRONG_BEAR", 2: "BEAR", 3: "NEUTRAL", 4: "BULL", 5: "STRONG_BULL"}
        out = {
            "latest": {
                "date": str(latest.name.date()),
                "composite_z": round(float(latest["composite_z"]), 4),
                "regime_code": int(latest["regime_code"]),
                "regime_label": str(latest["regime_label"]),
                "z_hy_oas": (
                    round(float(latest["z_hy_oas"]), 4)
                    if pd.notna(latest["z_hy_oas"]) else None
                ),
                "z_breadth": (
                    round(float(latest["z_breadth"]), 4)
                    if pd.notna(latest["z_breadth"]) else None
                ),
                "z_vix": (
                    round(float(latest["z_vix"]), 4)
                    if pd.notna(latest["z_vix"]) else None
                ),
            },
            "distribution": [
                {
                    "regime": label_map.get(int(c), str(c)),
                    "regime_code": int(c),
                    "n_days": int(n),
                    "pct": round(n / code_counts.sum() * 100, 1),
                }
                for c, n in code_counts.items()
            ],
            "n_obs": int(len(valid)),
            "period": {"start": args.start, "end": end_eff},
            "weights": list(args.weights),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    # ─── Latest reading ───
    print()
    print("=" * 70)
    print("REGIME COMPOSITE — LATEST READING")
    print("=" * 70)
    rows = [
        ["Date", str(latest.name.date())],
        ["Regime", f"{latest['regime_label']} (code {int(latest['regime_code'])}/5)"],
        ["Composite z", f"{latest['composite_z']:+.3f}"],
        ["z HY OAS (inv)", f"{latest['z_hy_oas']:+.3f}" if pd.notna(latest['z_hy_oas']) else "—"],
        ["z breadth", f"{latest['z_breadth']:+.3f}" if pd.notna(latest['z_breadth']) else "—"],
        ["z VIX (inv)", f"{latest['z_vix']:+.3f}" if pd.notna(latest['z_vix']) else "—"],
    ]
    print(tabulate(rows, tablefmt="simple"))

    # ─── Distribution ───
    print()
    print("=" * 70)
    print(f"REGIME DISTRIBUTION — {len(valid)} valid days")
    print("=" * 70)
    code_counts = valid["regime_code"].value_counts().sort_index()
    label_map = {1: "STRONG_BEAR", 2: "BEAR", 3: "NEUTRAL", 4: "BULL", 5: "STRONG_BULL"}
    dist_rows = [
        [
            f"{int(c)}/5",
            label_map.get(int(c), str(c)),
            int(n),
            f"{n / code_counts.sum() * 100:.1f}%",
        ]
        for c, n in code_counts.items()
    ]
    print(tabulate(
        dist_rows,
        headers=["Code", "Regime", "N days", "%"],
        tablefmt="github",
    ))

    # ─── Last 30 days summary ───
    print()
    print("=" * 70)
    print("LAST 30 DAYS — composite z trail")
    print("=" * 70)
    tail = valid.tail(30)
    trail_rows = []
    for d, r in tail.iterrows():
        trail_rows.append([
            str(d.date()),
            f"{r['composite_z']:+.3f}",
            int(r['regime_code']),
            r['regime_label'],
        ])
    print(tabulate(
        trail_rows,
        headers=["Date", "Composite z", "Code", "Regime"],
        tablefmt="github",
    ))

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Short-form: latest reading only, range default = 6 mesi."""
    end_eff = date.today()
    start_eff = (end_eff - timedelta(days=200)).strftime("%Y-%m-%d")

    args.start = start_eff
    args.end = end_eff.strftime("%Y-%m-%d")
    # Force JSON-shape latest only se --json passato; altrimenti tabella latest only
    if args.json:
        return cmd_composite(args)

    from propicks.domain.regime_composite import compute_regime_series

    try:
        hy, breadth, vix = _fetch_data(args.start, args.end, int(args.top_n))
    except Exception as e:
        print(f"[errore] fetch fallito: {e}", file=sys.stderr)
        return 1

    result = compute_regime_series(
        hy_oas=hy, breadth=breadth, vix=vix,
        zscore_window=int(args.zscore_window), weights=args.weights,
    )
    valid = result.dropna(subset=["composite_z"])
    if valid.empty:
        print("[errore] no valid composite readings", file=sys.stderr)
        return 1
    latest = valid.iloc[-1]
    print(
        f"{latest.name.date()}  "
        f"{latest['regime_label']} (code {int(latest['regime_code'])}/5)  "
        f"composite_z={latest['composite_z']:+.3f}  "
        f"hy_inv={latest['z_hy_oas']:+.2f}  "
        f"breadth={latest['z_breadth']:+.2f}  "
        f"vix_inv={latest['z_vix']:+.2f}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regime composite (Fase B.3): HY OAS + breadth + VIX z-score → 5-bucket regime.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ─── composite ───
    p_comp = sub.add_parser(
        "composite",
        help="Full series + latest + distribution. Default range: 2024-01-01 → today",
    )
    p_comp.add_argument(
        "--start", default="2024-01-01",
        help="Start date YYYY-MM-DD (default 2024-01-01)",
    )
    p_comp.add_argument(
        "--end", default=None,
        help="End date YYYY-MM-DD (default today)",
    )
    p_comp.add_argument(
        "--top-n", type=int, default=30,
        help="Breadth universe top-N S&P (default 30; più alto = più lento)",
    )
    p_comp.add_argument(
        "--zscore-window", type=int, default=252,
        help="Rolling z-score window in days (default 252 = 1y)",
    )
    p_comp.add_argument(
        "--weights", type=_parse_weights, default=(0.40, 0.40, 0.20),
        metavar="HY,BR,VIX",
        help="Weights composite (default 0.40,0.40,0.20)",
    )
    p_comp.add_argument("--json", action="store_true", help="Output JSON strutturato")
    p_comp.set_defaults(func=cmd_composite)

    # ─── check ───
    p_check = sub.add_parser(
        "check",
        help="Short-form: solo latest reading (range fissato a ~6 mesi)",
    )
    p_check.add_argument(
        "--top-n", type=int, default=30,
        help="Breadth universe top-N S&P (default 30)",
    )
    p_check.add_argument(
        "--zscore-window", type=int, default=252,
        help="Rolling z-score window (default 252)",
    )
    p_check.add_argument(
        "--weights", type=_parse_weights, default=(0.40, 0.40, 0.20),
        metavar="HY,BR,VIX",
        help="Weights composite (default 0.40,0.40,0.20)",
    )
    p_check.add_argument("--json", action="store_true", help="Output JSON")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
