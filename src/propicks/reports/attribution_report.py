"""Weekly attribution report (Phase 9) — markdown generator.

Struttura output:

```
# Attribution Report — Week ending YYYY-MM-DD

## 📊 Portfolio KPIs
Total value, weekly/MTD/YTD returns, vs SPX

## 📈 Trades chiusi questa settimana
Tabella con decomposition per-trade

## 🎯 Per-strategy (ultimi 30gg / 90gg / YTD)
Tabella aggregate KPIs + Phase 7 gate status

## 🌊 Per-regime breakdown
Win rate + avg P&L per regime macro al momento dell'entry

## ⚠️ Attention
Trade con >10% loss, strategie under gate, Sharpe basso
```

Output file: ``reports/attribution_YYYY-WW.md``.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from propicks.config import ETF_BENCHMARK, REPORTS_DIR
from propicks.domain.attribution import (
    GATE_THRESHOLDS,
    aggregate_by_regime,
    aggregate_by_strategy,
    decompose_trade,
    filter_trades_by_period,
    portfolio_vs_benchmark,
    strategy_gate_status,
)
from propicks.io.db import connect
from propicks.io.journal_store import load_journal


def _fmt_pct(x: float | None, decimals: int = 2) -> str:
    if x is None:
        return "—"
    return f"{x:+.{decimals}f}%"


def _fmt_float(x: float | None, decimals: int = 2) -> str:
    if x is None:
        return "—"
    if x == float("inf"):
        return "∞"
    return f"{x:.{decimals}f}"


def _iso_week(d: date) -> str:
    """Returns ISO week string like '2026-W17'."""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _load_snapshots(since_date: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM portfolio_snapshots"
        params: tuple = ()
        if since_date:
            sql += " WHERE date >= ?"
            params = (since_date,)
        sql += " ORDER BY date ASC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _load_regime_history() -> dict[str, int]:
    """Map entry_date → regime_code, usato per aggregate_by_regime."""
    conn = connect()
    try:
        rows = conn.execute("SELECT date, regime_code FROM regime_history").fetchall()
    finally:
        conn.close()
    return {r["date"]: r["regime_code"] for r in rows}


def _load_ticker_meta() -> dict[str, dict]:
    """Map ticker → {sector, beta} dalla cache Phase 2."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ticker, sector, beta FROM market_ticker_meta"
        ).fetchall()
    finally:
        conn.close()
    return {r["ticker"]: dict(r) for r in rows}


def _benchmark_series():
    """Carica close series SPX dal cache OHLCV. None se cache vuota."""
    from propicks.io.db import market_ohlcv_read

    rows = market_ohlcv_read(ETF_BENCHMARK, "daily")
    if not rows:
        return None

    import pandas as pd
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df["close"]


def _sector_series(sector_etf: str):
    from propicks.io.db import market_ohlcv_read

    rows = market_ohlcv_read(sector_etf, "daily")
    if not rows:
        return None
    import pandas as pd
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df["close"]


# ---------------------------------------------------------------------------
# Section generators
# ---------------------------------------------------------------------------
def _section_portfolio_kpis(as_of_date: date) -> str:
    """Sezione con KPIs portfolio (equity, MTD, YTD, vs SPX, max DD)."""
    # Window: last 365 days for YTD context
    cutoff = (as_of_date - timedelta(days=365)).isoformat()
    snapshots = _load_snapshots(since_date=cutoff)

    if len(snapshots) < 2:
        return (
            "## 📊 Portfolio KPIs\n\n"
            "_Insufficiente storico (servono almeno 2 snapshot). "
            "Esegui ``propicks-scheduler job snapshot`` per iniziare._\n"
        )

    perf = portfolio_vs_benchmark(snapshots, benchmark_key="benchmark_spx")
    if not perf.get("_ok"):
        return f"## 📊 Portfolio KPIs\n\n_Dati insufficienti: {perf.get('reason', '?')}_\n"

    # Snapshot più recente
    last = snapshots[-1]

    lines = ["## 📊 Portfolio KPIs", ""]
    lines.append(f"**As of:** {last['date']}")
    lines.append(f"**Total value:** € {last['total_value']:,.2f}")
    lines.append(f"**Cash:** € {last['cash']:,.2f} ({(last['cash'] / last['total_value']) * 100:.1f}%)")
    lines.append(f"**Posizioni aperte:** {last['n_positions']}")
    lines.append("")
    lines.append("### Returns")
    lines.append("")
    lines.append("| Periodo | Portfolio | SPX | Alpha |")
    lines.append("|---------|-----------|-----|-------|")
    lines.append(
        f"| Window ({perf['period_start']} → {perf['period_end']}) | "
        f"{_fmt_pct(perf['portfolio_return_pct'])} | "
        f"{_fmt_pct(perf['benchmark_return_pct'])} | "
        f"{_fmt_pct(perf['alpha_pct'])} |"
    )
    lines.append(
        f"| MTD | {_fmt_pct(perf['mtd_return_pct'])} | — | — |"
    )
    lines.append(
        f"| YTD | {_fmt_pct(perf['ytd_return_pct'])} | — | — |"
    )
    lines.append("")
    lines.append(f"**Max drawdown (window):** {_fmt_pct(perf['max_drawdown_pct'])}")
    lines.append("")
    return "\n".join(lines)


def _decompose_trades_with_meta(trades: list[dict]) -> list[dict]:
    """Per ogni trade closed, computa decomposition + info sector/beta.

    Ritorna list di dict con il trade originale + key ``_attribution``.
    """
    from propicks.domain.stock_rs import SECTOR_KEY_TO_US_ETF, YF_SECTOR_TO_KEY

    benchmark_series = _benchmark_series()
    if benchmark_series is None:
        return []

    ticker_meta = _load_ticker_meta()

    # Pre-compute sector ETF series cache (per-ETF, evita refetch)
    sector_series_cache: dict[str, object] = {}

    # Durata mediana per strategia (per timing component)
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    by_strat: dict[str, list[int]] = {}
    for t in closed_trades:
        by_strat.setdefault(t.get("strategy") or "?", []).append(
            int(t.get("duration_days") or 0)
        )
    median_days: dict[str, int] = {}
    for strat, durations in by_strat.items():
        if durations:
            median_days[strat] = sorted(durations)[len(durations) // 2]

    out = []
    for t in closed_trades:
        ticker = t.get("ticker", "").upper()
        meta = ticker_meta.get(ticker, {})
        beta = meta.get("beta")

        # Sector series lookup
        yf_sector = meta.get("sector")
        sector_series = None
        if yf_sector:
            sector_key = YF_SECTOR_TO_KEY.get(yf_sector)
            sector_etf = SECTOR_KEY_TO_US_ETF.get(sector_key) if sector_key else None
            if sector_etf:
                if sector_etf not in sector_series_cache:
                    sector_series_cache[sector_etf] = _sector_series(sector_etf)
                sector_series = sector_series_cache[sector_etf]

        median_for_strat = median_days.get(t.get("strategy") or "?")

        decomp = decompose_trade(
            t,
            benchmark_series=benchmark_series,
            sector_series=sector_series,
            beta=beta,
            median_holding_days=median_for_strat,
        )
        out.append({**t, "_attribution": decomp})

    return out


def _section_recent_trades(decomposed: list[dict], days_back: int = 7) -> str:
    """Sezione trade chiusi nell'ultima settimana con decomposition."""
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    recent = [
        t for t in decomposed
        if t.get("exit_date") and t["exit_date"] >= cutoff
    ]

    if not recent:
        return (
            f"## 📈 Trade chiusi ultimi {days_back} giorni\n\n"
            f"_Nessun trade chiuso dal {cutoff}._\n"
        )

    lines = [
        f"## 📈 Trade chiusi ultimi {days_back} giorni",
        "",
        "| Ticker | Strat | Entry | Exit | Days | **Total** | Market (β) | Sector | Alpha | Timing |",
        "|--------|-------|-------|------|------|-----------|------------|--------|-------|--------|",
    ]
    for t in recent:
        attr = t.get("_attribution", {})
        ticker = t.get("ticker", "?")
        strat = (t.get("strategy") or "?")[:12]
        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        days = t.get("duration_days", 0)

        if attr.get("_decomposable"):
            lines.append(
                f"| `{ticker}` | {strat} | {entry:.2f} | {exit_p:.2f} | {days} | "
                f"**{_fmt_pct(attr['total_pct'])}** | "
                f"{_fmt_pct(attr['market_pct'])} (β={attr['beta_used']:.2f}) | "
                f"{_fmt_pct(attr['sector_pct'])} | "
                f"**{_fmt_pct(attr['alpha_pct'])}** | "
                f"{_fmt_pct(attr['timing_pct'])} |"
            )
        else:
            # Trade non decomposable (no benchmark / no beta)
            total = t.get("pnl_pct")
            lines.append(
                f"| `{ticker}` | {strat} | {entry:.2f} | {exit_p:.2f} | {days} | "
                f"**{_fmt_pct(total)}** | — | — | — | — |"
            )
    lines.append("")
    return "\n".join(lines)


def _section_strategy_aggregates(trades: list[dict], period_days: int, label: str) -> str:
    filtered = filter_trades_by_period(trades, period_days=period_days)
    if not filtered:
        return f"### {label} (ultimi {period_days}gg)\n\n_Nessun trade chiuso in questo periodo._\n"

    aggs = aggregate_by_strategy(filtered)
    gate = strategy_gate_status(aggs)

    lines = [
        f"### {label} (ultimi {period_days}gg)",
        "",
        "| Strategy | N | Win% | Avg P&L | PF | Sharpe | Max DD | Gate |",
        "|----------|---|------|---------|----|----|--------|------|",
    ]
    for strat, stats in sorted(aggs.items(), key=lambda x: -x[1].get("n_trades", 0)):
        g = gate.get(strat, {})
        win_pct = f"{stats['win_rate'] * 100:.0f}%" if stats.get("win_rate") is not None else "—"
        lines.append(
            f"| {strat} | {stats['n_trades']} | {win_pct} | "
            f"{_fmt_pct(stats['avg_pnl_pct'])} | "
            f"{_fmt_float(stats['profit_factor'])} | "
            f"{_fmt_float(stats['sharpe_trade'])} | "
            f"{_fmt_pct(stats['max_drawdown_pct'])} | "
            f"{g.get('summary', '—')} |"
        )
    lines.append("")
    return "\n".join(lines)


def _section_regime_breakdown(trades: list[dict], regime_map: dict[str, int]) -> str:
    """Sezione aggregate per regime al momento entry."""
    closed = filter_trades_by_period(trades, period_days=90)
    aggs = aggregate_by_regime(closed, regime_map)

    if not aggs:
        return ""

    lines = [
        "## 🌊 Per-regime breakdown (ultimi 90gg)",
        "",
        "Regime macro weekly al momento dell'entry del trade.",
        "",
        "| Regime | N | Win% | Avg P&L | Total P&L |",
        "|--------|---|------|---------|-----------|",
    ]
    # Ordina STRONG_BULL → STRONG_BEAR
    order = ["STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR", "UNKNOWN"]
    for label in order:
        if label not in aggs:
            continue
        stats = aggs[label]
        win_pct = f"{stats['win_rate'] * 100:.0f}%" if stats.get("win_rate") else "—"
        lines.append(
            f"| {label} | {stats['n_trades']} | {win_pct} | "
            f"{_fmt_pct(stats['avg_pnl_pct'])} | "
            f"{_fmt_pct(stats['total_pnl_pct'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _section_gate_detail(trades: list[dict]) -> str:
    """Sezione con failure reasons per strategie sotto gate Phase 7."""
    closed = filter_trades_by_period(trades, period_days=180)
    if not closed:
        return ""

    aggs = aggregate_by_strategy(closed)
    gate = strategy_gate_status(aggs)

    failing = {k: v for k, v in gate.items() if not v["passed"]}
    if not failing:
        return "## ✅ Gate Phase 7 (6 mesi)\n\n_Tutte le strategie passano le soglie._\n"

    lines = [
        "## 🚧 Gate Phase 7 (ultimi 180gg) — strategie under threshold",
        "",
        f"Soglie: trades ≥ {GATE_THRESHOLDS['min_trades']}, "
        f"profit_factor ≥ {GATE_THRESHOLDS['min_profit_factor']}, "
        f"sharpe ≥ {GATE_THRESHOLDS['min_sharpe_trade']}, "
        f"win_rate ≥ 50-55%, max_DD ≥ {GATE_THRESHOLDS['max_drawdown_pct']}%, "
        f"corr_SPX ≤ {GATE_THRESHOLDS['max_correlation_spx']}",
        "",
    ]
    for strat, info in failing.items():
        lines.append(f"### {strat} (n={info['n_trades']})")
        lines.append("")
        for f in info["failures"]:
            lines.append(f"- ❌ {f}")
        lines.append("")
    return "\n".join(lines)


def _section_attention(trades: list[dict]) -> str:
    """Sezione "attention": trade con loss pesante, pattern sospetti."""
    closed = filter_trades_by_period(trades, period_days=30)
    heavy_losses = [t for t in closed if (t.get("pnl_pct") or 0) <= -10.0]

    if not heavy_losses:
        return ""

    lines = [
        "## ⚠️ Attention — trade con loss > 10% (30gg)",
        "",
        "| Ticker | Strategy | P&L% | Days | Exit reason |",
        "|--------|----------|------|------|-------------|",
    ]
    for t in sorted(heavy_losses, key=lambda x: x.get("pnl_pct", 0)):
        lines.append(
            f"| `{t['ticker']}` | {t.get('strategy', '?')} | "
            f"{_fmt_pct(t['pnl_pct'])} | "
            f"{t.get('duration_days', '?')} | "
            f"{t.get('exit_reason') or '—'} |"
        )
    lines.append("")
    lines.append(
        "_Rivedi criteri entry su questi ticker. "
        "Se il pattern si ripete, rivedi parametri della strategia._"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def weekly_attribution_report(as_of_date: date | None = None) -> dict:
    """Genera il report attribution e lo salva in ``reports/attribution_YYYY-WW.md``.

    Returns dict: ``{path, n_trades, n_closed_week, regime, has_attention}``.
    """
    as_of_date = as_of_date or date.today()
    iso_week = _iso_week(as_of_date)

    trades = load_journal()
    regime_map = _load_regime_history()

    # Decomposition per trade recenti (per la sezione "trade della settimana")
    decomposed = _decompose_trades_with_meta(trades)

    # Build markdown
    parts: list[str] = []
    parts.append(f"# Attribution Report — Week {iso_week}")
    parts.append("")
    parts.append(f"_Generato: {datetime.now().strftime('%Y-%m-%d %H:%M')} — as_of {as_of_date}_")
    parts.append("")

    parts.append(_section_portfolio_kpis(as_of_date))
    parts.append(_section_recent_trades(decomposed, days_back=7))

    parts.append("## 🎯 Performance per strategia")
    parts.append("")
    parts.append(_section_strategy_aggregates(trades, 30, "Ultimi 30gg"))
    parts.append(_section_strategy_aggregates(trades, 90, "Ultimi 90gg"))
    parts.append(_section_strategy_aggregates(trades, 365, "Ultimi 365gg"))

    parts.append(_section_regime_breakdown(trades, regime_map))
    parts.append(_section_gate_detail(trades))
    parts.append(_section_attention(trades))

    parts.append("---")
    parts.append(
        "_Report generato da ``propicks-report attribution``. "
        "Decomposition: total = market(β × spx_return) + sector(etf - spx) + "
        "alpha(residual) + timing(vs median holding days)._"
    )

    markdown = "\n".join(p for p in parts if p)

    # Save to file
    filename = f"attribution_{iso_week}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)

    closed_this_week = len([
        t for t in trades
        if t.get("status") == "closed"
        and t.get("exit_date", "") >= (as_of_date - timedelta(days=7)).isoformat()
    ])

    return {
        "path": filepath,
        "iso_week": iso_week,
        "n_trades": len([t for t in trades if t.get("status") == "closed"]),
        "n_closed_this_week": closed_this_week,
        "markdown_len": len(markdown),
    }


def latest_report_path() -> str | None:
    """Ritorna il path dell'ultimo report generato (per bot /report command)."""
    if not os.path.isdir(REPORTS_DIR):
        return None
    reports = sorted(
        [f for f in os.listdir(REPORTS_DIR) if f.startswith("attribution_") and f.endswith(".md")],
        reverse=True,
    )
    if not reports:
        return None
    return os.path.join(REPORTS_DIR, reports[0])
