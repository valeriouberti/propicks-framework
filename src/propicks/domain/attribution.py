"""P&L attribution — decomposizione trade per componenti (Phase 9).

Per ogni trade CHIUSO, decomponi il P&L in 4 componenti additive:

    total_pnl_pct = market + sector + alpha + timing

- **market (beta component)**: `beta × spx_return(entry→exit)` — quanto del P&L
  è spiegato dal movimento del mercato. Se beta=1.0 e SPX +5% tra entry e exit,
  market component = +5%.
- **sector**: `sector_etf_return(entry→exit) - spx_return(entry→exit)` — extra
  rotazione settoriale oltre il mercato. Per ticker US con sector mappato.
  Per .MI/EU: 0.0 (skip — richiederebbe STOXX sector ETF).
- **alpha (residual)**: `total - market - sector - timing` — il vero valore
  della selezione del titolo. Se positivo, il tuo sistema trova alpha.
- **timing**: `total_actual - total_if_held_for_median_bars` — edge del tuo
  entry/exit timing vs un hold passivo per la durata mediana della strategia.

Gate Phase 7 (promuovere nuove strategie) — ogni strategia deve raggiungere:
- N trade chiusi >= 15
- Profit factor >= 1.3
- Sharpe (trade-level) >= 0.8
- Win rate: momentum >= 50%, contrarian >= 55%
- Max drawdown <= 15%
- Correlation con SPX <= 0.7

Se dopo 6 mesi una strategia non raggiunge queste soglie, si ritira invece
di aggiungere una strategia nuova per compensare.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Pure decomposition
# ---------------------------------------------------------------------------
def _price_at_date(series: pd.Series, target_date: str) -> float | None:
    """Ritorna il close del primo trading day >= target_date in series.

    Gestisce weekend/holidays: se target_date è sabato, prende il lunedì successivo.
    Ritorna None se target_date è dopo l'ultimo bar.
    """
    if series is None or series.empty:
        return None
    idx = pd.to_datetime(series.index)
    target = pd.to_datetime(target_date)
    # Primo index >= target
    mask = idx >= target
    if not mask.any():
        return None
    first_ix = idx[mask][0]
    return float(series.loc[first_ix])


def _period_return(series: pd.Series, start_date: str, end_date: str) -> float | None:
    """Returns `close(end) / close(start) - 1`, None se dati insufficienti."""
    start = _price_at_date(series, start_date)
    end = _price_at_date(series, end_date)
    if start is None or end is None or start <= 0:
        return None
    return (end / start) - 1


def decompose_trade(
    trade: dict,
    benchmark_series: pd.Series,
    sector_series: pd.Series | None = None,
    beta: float | None = None,
    median_holding_days: int | None = None,
) -> dict:
    """Decompone un trade chiuso in {market, sector, alpha, timing}.

    Args:
        trade: dict del trade journal (closed, con entry_date + exit_date + pnl_pct).
        benchmark_series: close SPX (o benchmark scelto) indicizzato per data.
        sector_series: close sector ETF per il ticker (opzionale, None per EU).
        beta: beta del ticker vs benchmark (None → assume 1.0).
        median_holding_days: durata mediana della strategia (per timing component).

    Returns:
        dict con:
        - ``total_pct``: P&L totale del trade (%)
        - ``market_pct``: beta × benchmark_return
        - ``sector_pct``: sector_return - benchmark_return (o 0 se sector_series None)
        - ``alpha_pct``: residuo (total - market - sector - timing)
        - ``timing_pct``: edge dell'exit timing vs hold passivo median_holding_days
        - ``benchmark_return``, ``sector_return``: for transparency
        - ``_decomposable``: False se dati mancanti (total restituito ma components None)
    """
    if trade.get("status") != "closed":
        return {"_decomposable": False, "reason": "trade not closed"}

    total_pct = trade.get("pnl_pct")
    entry_date = trade.get("entry_date")
    exit_date = trade.get("exit_date")
    if total_pct is None or not entry_date or not exit_date:
        return {"_decomposable": False, "reason": "missing pnl/dates"}

    # Trade pnl_pct in journal è memorizzato *100 (es. 10.0 = 10%). Normalizziamo.
    total = total_pct / 100.0

    bench_ret = _period_return(benchmark_series, entry_date, exit_date)
    if bench_ret is None:
        return {
            "_decomposable": False,
            "total_pct": total_pct,
            "reason": "benchmark series insufficient",
        }

    # Market component
    beta_used = beta if beta is not None else 1.0
    market_ret = beta_used * bench_ret

    # Sector component (se sector_series disponibile)
    sector_ret_raw = None
    sector_component = 0.0
    if sector_series is not None:
        sector_ret_raw = _period_return(sector_series, entry_date, exit_date)
        if sector_ret_raw is not None:
            sector_component = sector_ret_raw - bench_ret

    # Timing component: se avessi tenuto fino a entry + median_holding_days,
    # avresti catturato un pnl diverso. Il timing è la differenza.
    # NB: calcolo basato sul benchmark (per avere base comparabile), non sul
    # ticker — il timing è "hai beccato il momento giusto di USCIRE rispetto
    # al mercato?"
    timing_component = 0.0
    if median_holding_days and median_holding_days > 0:
        median_exit_date = (
            pd.to_datetime(entry_date) + pd.Timedelta(days=median_holding_days)
        ).strftime("%Y-%m-%d")
        median_bench_ret = _period_return(benchmark_series, entry_date, median_exit_date)
        if median_bench_ret is not None:
            # Se actual exit è migliore del median exit → timing positivo
            timing_component = (bench_ret - median_bench_ret) * beta_used

    # Alpha = residuo
    alpha = total - market_ret - sector_component - timing_component

    return {
        "_decomposable": True,
        "total_pct": round(total_pct, 4),
        "market_pct": round(market_ret * 100, 4),
        "sector_pct": round(sector_component * 100, 4),
        "alpha_pct": round(alpha * 100, 4),
        "timing_pct": round(timing_component * 100, 4),
        "benchmark_return": round(bench_ret * 100, 4),
        "sector_return": round(sector_ret_raw * 100, 4) if sector_ret_raw is not None else None,
        "beta_used": beta_used,
    }


# ---------------------------------------------------------------------------
# Aggregates per strategy
# ---------------------------------------------------------------------------
def _sharpe_trade_level(returns: list[float]) -> float | None:
    """Sharpe ratio trade-level (no annualization): mean / stdev.

    Per attribution retail ci interessa "consistency" del trade P&L,
    non l'annualizzazione che richiede assumption sulla frequenza.
    """
    if len(returns) < 2:
        return None
    mean = statistics.mean(returns)
    std = statistics.stdev(returns)
    if std <= 0:
        return None
    return round(mean / std, 3)


def _max_drawdown(returns: list[float]) -> float | None:
    """Max drawdown sull'equity curve trade-level (cumulative product)."""
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r / 100)
        peak = max(peak, equity)
        dd = (equity - peak) / peak
        max_dd = min(max_dd, dd)
    return round(max_dd * 100, 2)


def _profit_factor(returns: list[float]) -> float | None:
    """Sum(winners) / |Sum(losers)|. None se nessun loser."""
    wins = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return round(wins / losses, 3)


def _correlation(returns_a: list[float], returns_b: list[float]) -> float | None:
    """Pearson correlation. None se len<3 o stdev=0."""
    if len(returns_a) < 3 or len(returns_a) != len(returns_b):
        return None
    try:
        return round(statistics.correlation(returns_a, returns_b), 3)
    except statistics.StatisticsError:
        return None


def aggregate_by_strategy(
    trades: list[dict],
    benchmark_pnl_pct_map: dict[int, float] | None = None,
) -> dict[str, dict]:
    """Aggregati per strategia su trade CHIUSI con ``pnl_pct`` valido.

    Args:
        trades: lista completa dei trade (la funzione filtra ``status=closed``).
        benchmark_pnl_pct_map: optional dict ``{trade_id: benchmark_pnl_pct}``
            calcolato upstream. Se fornito, abilita calcolo correlation e
            alpha aggregato.

    Returns: ``{strategy_name: {n_trades, win_rate, avg_pnl_pct, profit_factor,
        sharpe_trade, max_drawdown_pct, correlation_spx}}``.
    """
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_pct") is not None]

    by_strat: dict[str, list[dict]] = {}
    for t in closed:
        strat = t.get("strategy") or "Unknown"
        by_strat.setdefault(strat, []).append(t)

    out: dict[str, dict] = {}
    for strat, group in by_strat.items():
        returns = [float(t["pnl_pct"]) for t in group]
        wins = sum(1 for r in returns if r > 0)

        stats = {
            "n_trades": len(returns),
            "wins": wins,
            "losses": len(returns) - wins,
            "win_rate": round(wins / len(returns), 4) if returns else None,
            "avg_pnl_pct": round(statistics.mean(returns), 4) if returns else None,
            "median_pnl_pct": round(statistics.median(returns), 4) if returns else None,
            "profit_factor": _profit_factor(returns),
            "sharpe_trade": _sharpe_trade_level(returns),
            "max_drawdown_pct": _max_drawdown(returns),
            "best_trade_pct": round(max(returns), 4) if returns else None,
            "worst_trade_pct": round(min(returns), 4) if returns else None,
        }

        # Correlation con benchmark se mappa fornita
        if benchmark_pnl_pct_map:
            bench_returns = [
                benchmark_pnl_pct_map.get(t["id"]) for t in group
                if t.get("id") in benchmark_pnl_pct_map
            ]
            bench_returns = [x for x in bench_returns if x is not None]
            strat_returns_paired = [
                float(t["pnl_pct"]) for t in group
                if t.get("id") in benchmark_pnl_pct_map
                and benchmark_pnl_pct_map.get(t["id"]) is not None
            ]
            if len(bench_returns) == len(strat_returns_paired):
                stats["correlation_spx"] = _correlation(
                    strat_returns_paired, bench_returns
                )

        out[strat] = stats
    return out


# ---------------------------------------------------------------------------
# Phase 7 gate — promote new strategies only when existing show edge
# ---------------------------------------------------------------------------
GATE_THRESHOLDS = {
    "min_trades": 15,
    "min_profit_factor": 1.3,
    "min_sharpe_trade": 0.8,
    "min_win_rate_momentum": 0.50,
    "min_win_rate_contrarian": 0.55,
    "max_drawdown_pct": -15.0,  # NB: max DD è negativo
    "max_correlation_spx": 0.70,
}


def strategy_gate_status(aggregates: dict[str, dict]) -> dict:
    """Valuta ogni strategia contro le soglie Phase 7.

    Ritorna dict {strategy: {passed: bool, failures: list[str], summary: str}}.
    Una strategia è "pass" se soddisfa tutte le soglie (gate conservativo).
    """
    result: dict[str, dict] = {}
    for strat, stats in aggregates.items():
        failures: list[str] = []

        n = stats.get("n_trades", 0)
        if n < GATE_THRESHOLDS["min_trades"]:
            failures.append(f"n_trades {n} < {GATE_THRESHOLDS['min_trades']} (sample insufficiente)")

        pf = stats.get("profit_factor")
        if pf is None or (pf != float("inf") and pf < GATE_THRESHOLDS["min_profit_factor"]):
            failures.append(
                f"profit_factor {pf if pf is not None else 'N/A'} < "
                f"{GATE_THRESHOLDS['min_profit_factor']}"
            )

        sharpe = stats.get("sharpe_trade")
        if sharpe is None or sharpe < GATE_THRESHOLDS["min_sharpe_trade"]:
            failures.append(
                f"sharpe_trade {sharpe if sharpe is not None else 'N/A'} < "
                f"{GATE_THRESHOLDS['min_sharpe_trade']}"
            )

        win_rate = stats.get("win_rate")
        # Threshold differenziato per Contrarian vs altri
        if "contra" in strat.lower():
            min_wr = GATE_THRESHOLDS["min_win_rate_contrarian"]
        else:
            min_wr = GATE_THRESHOLDS["min_win_rate_momentum"]
        if win_rate is None or win_rate < min_wr:
            failures.append(
                f"win_rate {win_rate if win_rate is not None else 'N/A'} < {min_wr}"
            )

        max_dd = stats.get("max_drawdown_pct")
        if max_dd is not None and max_dd < GATE_THRESHOLDS["max_drawdown_pct"]:
            failures.append(
                f"max_drawdown {max_dd}% > soglia {GATE_THRESHOLDS['max_drawdown_pct']}%"
            )

        corr = stats.get("correlation_spx")
        if corr is not None and abs(corr) > GATE_THRESHOLDS["max_correlation_spx"]:
            failures.append(
                f"corr_spx {corr} > {GATE_THRESHOLDS['max_correlation_spx']} "
                "(stai prendendo solo beta)"
            )

        passed = len(failures) == 0
        summary = "✅ PASS" if passed else f"❌ FAIL ({len(failures)} criteri)"
        result[strat] = {
            "passed": passed,
            "failures": failures,
            "summary": summary,
            "n_trades": n,
        }

    return result


# ---------------------------------------------------------------------------
# Per-regime breakdown
# ---------------------------------------------------------------------------
def aggregate_by_regime(
    trades: list[dict],
    regime_map: dict[str, int],
) -> dict[str, dict]:
    """Group closed trades by regime at entry date.

    Args:
        trades: lista trade
        regime_map: dict ``{entry_date_str: regime_code}`` precalcolato upstream.

    Returns: ``{regime_label: {n_trades, win_rate, avg_pnl_pct}}``.
    """
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl_pct") is not None]

    regime_labels = {5: "STRONG_BULL", 4: "BULL", 3: "NEUTRAL", 2: "BEAR", 1: "STRONG_BEAR"}
    by_regime: dict[str, list[float]] = {}

    for t in closed:
        entry_date = t.get("entry_date")
        code = regime_map.get(entry_date)
        if code is None:
            label = "UNKNOWN"
        else:
            label = regime_labels.get(code, f"CODE_{code}")
        by_regime.setdefault(label, []).append(float(t["pnl_pct"]))

    out: dict[str, dict] = {}
    for label, returns in by_regime.items():
        if not returns:
            continue
        wins = sum(1 for r in returns if r > 0)
        out[label] = {
            "n_trades": len(returns),
            "win_rate": round(wins / len(returns), 4),
            "avg_pnl_pct": round(statistics.mean(returns), 4),
            "total_pnl_pct": round(sum(returns), 4),
        }
    return out


# ---------------------------------------------------------------------------
# Portfolio-level stats (vs SPX) from portfolio_snapshots
# ---------------------------------------------------------------------------
def portfolio_vs_benchmark(
    snapshots: list[dict],
    benchmark_key: str = "benchmark_spx",
) -> dict:
    """Compara equity curve portfolio vs benchmark usando portfolio_snapshots.

    Args:
        snapshots: list di dict da portfolio_snapshots (con total_value, date,
            benchmark_spx, benchmark_ftsemib).
        benchmark_key: quale colonna usare come benchmark.

    Returns: dict con portfolio_return, benchmark_return, alpha (differenza),
        ytd_return, mtd_return, max_drawdown_pct.
    """
    if len(snapshots) < 2:
        return {"_ok": False, "reason": "insufficient snapshots"}

    # Ordina by date asc
    sorted_snaps = sorted(snapshots, key=lambda s: s["date"])
    first = sorted_snaps[0]
    last = sorted_snaps[-1]

    if not (first.get("total_value") and last.get("total_value")):
        return {"_ok": False, "reason": "missing total_value"}

    portfolio_return = (last["total_value"] / first["total_value"]) - 1

    benchmark_return = None
    if first.get(benchmark_key) and last.get(benchmark_key):
        benchmark_return = (last[benchmark_key] / first[benchmark_key]) - 1

    alpha = portfolio_return - benchmark_return if benchmark_return is not None else None

    # Equity curve drawdown
    values = [float(s["total_value"]) for s in sorted_snaps if s.get("total_value")]
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (v - peak) / peak
        max_dd = min(max_dd, dd)

    return {
        "_ok": True,
        "portfolio_return_pct": round(portfolio_return * 100, 4),
        "benchmark_return_pct": (
            round(benchmark_return * 100, 4) if benchmark_return is not None else None
        ),
        "alpha_pct": round(alpha * 100, 4) if alpha is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 4),
        "n_snapshots": len(sorted_snaps),
        "period_start": first["date"],
        "period_end": last["date"],
        "mtd_return_pct": (
            round(last.get("mtd_return") * 100, 4) if last.get("mtd_return") else None
        ),
        "ytd_return_pct": (
            round(last.get("ytd_return") * 100, 4) if last.get("ytd_return") else None
        ),
    }


# ---------------------------------------------------------------------------
# Utility: filter trades by period
# ---------------------------------------------------------------------------
def filter_trades_by_period(
    trades: list[dict],
    period_days: int | None = None,
    exit_after: str | None = None,
    exit_before: str | None = None,
) -> list[dict]:
    """Filter closed trades by exit_date range."""
    from datetime import date as dt_date
    from datetime import timedelta

    closed = [t for t in trades if t.get("status") == "closed" and t.get("exit_date")]

    if period_days is not None:
        cutoff = (dt_date.today() - timedelta(days=period_days)).isoformat()
        closed = [t for t in closed if t["exit_date"] >= cutoff]

    if exit_after:
        closed = [t for t in closed if t["exit_date"] >= exit_after]

    if exit_before:
        closed = [t for t in closed if t["exit_date"] <= exit_before]

    return closed


# ---------------------------------------------------------------------------
# _math helper for tests
# ---------------------------------------------------------------------------
def _is_finite(x: Any) -> bool:
    """True se x è float/int finito. NaN/None/inf → False."""
    if x is None:
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
