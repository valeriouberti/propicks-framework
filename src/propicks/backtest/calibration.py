"""Threshold calibration framework (Fase A.2 SIGNAL_ROADMAP).

Sweep su un range di score threshold, esegue backtest per ognuno, calcola
Sharpe + Probabilistic Sharpe Ratio + Deflated Sharpe Ratio, e raccomanda
il threshold ottimo.

## Logica

1. Per ciascun ``threshold`` in ``thresholds``:
   - Run ``simulate_portfolio`` (single shot o CPCV se abilitato)
   - Estrae metriche: Sharpe per-trade, annualized, win rate, n_trades, total return
   - Calcola PSR vs benchmark 0
2. Calcola variance di Sharpe attraverso threshold (= ``var_sr_trials``)
3. Per ciascun threshold: DSR = PSR(SR_threshold | E[max SR | n_thresholds])
4. Recommend threshold con criterio:
   - DSR > 0.95 (95% confidence post multi-test correction) **se** raggiungibile
   - Else: max DSR con n_trades > min_trades (default 30) e Sharpe > 0
   - Else: explicit "no edge"

## API

- ``calibrate_threshold(universe, scoring_fn, thresholds, ...)`` →
  ``CalibrationResult``
- ``ThresholdResult`` — riga per ogni threshold testato
- ``CalibrationResult`` — wrapper con recommendation

## Note

Il modulo usa ``simulate_portfolio`` di ``portfolio_engine``. Il scoring_fn è
fornito dal caller (CLI), invariante rispetto al threshold (lo soglia il
config). Se ``--use-cpcv``, ogni threshold valutato su ``comb(n_groups,
n_test_groups)`` test path indipendenti.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from propicks.backtest.cpcv import cpcv_dates_split, n_cpcv_paths
from propicks.backtest.metrics_v2 import compute_portfolio_metrics
from propicks.backtest.portfolio_engine import (
    BacktestConfig,
    PortfolioState,
    simulate_portfolio,
)
from propicks.domain.risk_stats import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sharpe_with_confidence,
)


@dataclass
class ThresholdResult:
    """Risultato per un singolo threshold testato."""
    threshold: float
    n_trades: int
    sharpe_per_trade: float
    sharpe_annualized: float | None
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    psr: float                       # P(true Sharpe > 0 | data)
    dsr: float                       # PSR deflated by n_thresholds tested
    sharpe_ci: tuple[float, float]   # CI 95% del Sharpe per-trade
    # CPCV-specific (None se single shot)
    cpcv_n_paths: int | None = None
    cpcv_sharpe_mean: float | None = None
    cpcv_sharpe_std: float | None = None


@dataclass
class CalibrationResult:
    """Risultato aggregato di calibrazione con recommendation."""
    results: list[ThresholdResult] = field(default_factory=list)
    recommended_threshold: float | None = None
    recommendation_reason: str = ""
    n_thresholds_tested: int = 0
    var_sr_across_thresholds: float = 0.0
    universe_size: int = 0
    period_start: date | None = None
    period_end: date | None = None
    cpcv_enabled: bool = False


def _extract_trade_returns(state: PortfolioState) -> list[float]:
    """Estrae sequence di pnl_pct (per-trade returns frazionari) da uno stato.

    ``ClosedTrade.pnl_pct`` è in unità percentuali (es. 5.0 = +5%). Convertiamo
    a frazione (0.05) per coerenza con risk_stats che assume return frazionari.
    """
    return [t.pnl_pct / 100.0 for t in state.closed_trades]


def _summarize_state(
    state: PortfolioState,
    threshold: float,
    n_thresholds: int,
    var_sr_trials: float,
    n_trades_per_year: int = 50,
) -> ThresholdResult:
    """Calcola metriche + PSR + DSR per uno stato."""
    metrics = compute_portfolio_metrics(state)
    returns = _extract_trade_returns(state)

    if len(returns) < 3:
        # Trade insufficienti per stat robuste
        return ThresholdResult(
            threshold=threshold,
            n_trades=len(returns),
            sharpe_per_trade=0.0,
            sharpe_annualized=None,
            win_rate=0.0,
            total_return_pct=metrics.get("total_return_pct", 0.0),
            max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
            psr=0.0,
            dsr=0.0,
            sharpe_ci=(0.0, 0.0),
        )

    sr = sharpe_ratio(returns)
    psr, _ = probabilistic_sharpe_ratio(returns, sr_benchmark=0.0)
    dsr, _ = (
        deflated_sharpe_ratio(returns, n_trials=n_thresholds, var_sr_trials=var_sr_trials)
        if n_thresholds > 1
        else (psr, 0.0)
    )
    sr_a, ci_lo, ci_hi = sharpe_with_confidence(returns, alpha=0.05)
    # Annualize: n_trades_per_year media stimata via observed n_trades / years
    sr_annualized = (
        metrics.get("sharpe_annualized") if metrics.get("sharpe_annualized") is not None
        else None
    )

    return ThresholdResult(
        threshold=threshold,
        n_trades=len(returns),
        sharpe_per_trade=sr,
        sharpe_annualized=sr_annualized,
        win_rate=metrics.get("win_rate", 0.0),
        total_return_pct=metrics.get("total_return_pct", 0.0),
        max_drawdown_pct=metrics.get("max_drawdown_pct", 0.0),
        psr=psr,
        dsr=dsr,
        sharpe_ci=(ci_lo, ci_hi),
    )


def _recommend_threshold(
    results: list[ThresholdResult],
    *,
    min_trades: int = 30,
    target_dsr: float = 0.95,
) -> tuple[float | None, str]:
    """Rule-based recommendation.

    Priorità:
    1. Max DSR tra threshold con DSR ≥ target_dsr e n_trades ≥ min_trades
    2. Max DSR tra threshold con n_trades ≥ min_trades (anche se DSR < target)
    3. Max Sharpe se nessuno ha trade sufficienti
    4. None + "no edge" se nessun threshold positivo
    """
    if not results:
        return None, "nessun threshold testato"

    # Tier 1: DSR ≥ target
    tier1 = [r for r in results if r.dsr >= target_dsr and r.n_trades >= min_trades]
    if tier1:
        best = max(tier1, key=lambda r: r.dsr)
        return (
            best.threshold,
            f"DSR={best.dsr:.3f} ≥ {target_dsr:.2f} con {best.n_trades} trade — "
            f"strategia robusta a multiple testing",
        )

    # Tier 2: best DSR sopra min_trades
    tier2 = [r for r in results if r.n_trades >= min_trades]
    if tier2:
        best = max(tier2, key=lambda r: r.dsr)
        return (
            best.threshold,
            f"DSR={best.dsr:.3f} (sotto target {target_dsr:.2f}) ma highest "
            f"con {best.n_trades} trade — edge marginale, considerare se accettare",
        )

    # Tier 3: best Sharpe se nessuno ha trade sufficienti
    if results:
        best = max(results, key=lambda r: r.sharpe_per_trade)
        return (
            best.threshold,
            f"trade insufficienti su tutti i threshold (max {best.n_trades} < {min_trades}) — "
            f"recommendation by Sharpe ma poco affidabile",
        )

    return None, "no edge significativo"


def calibrate_threshold(
    *,
    universe: dict[str, pd.DataFrame],
    scoring_fn: Callable[[str, pd.DataFrame], float | None],
    thresholds: list[float],
    base_config: BacktestConfig | None = None,
    regime_series: pd.Series | None = None,
    earnings_dates: dict[str, str] | None = None,
    universe_provider: Callable[[date], list[str]] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    use_cpcv: bool = False,
    cpcv_n_groups: int = 6,
    cpcv_n_test_groups: int = 2,
    cpcv_embargo_days: int = 5,
    min_trades: int = 30,
    target_dsr: float = 0.95,
    progress_cb: Callable[[int, int, float], None] | None = None,
) -> CalibrationResult:
    """Esegue threshold sweep + DSR analysis + recommendation.

    Args:
        universe: OHLCV per ticker.
        scoring_fn: stessa che useresti in ``simulate_portfolio``.
        thresholds: lista di score threshold da testare (es. ``[40, 50, 60, 70, 80]``).
        base_config: ``BacktestConfig`` template. ``score_threshold`` viene
            sovrascritto per ogni run del sweep.
        regime_series, earnings_dates, universe_provider: passati through a
            ``simulate_portfolio``.
        start_date, end_date: range backtest.
        use_cpcv: se True, ogni threshold è valutato su CPCV split.
        cpcv_n_groups, cpcv_n_test_groups, cpcv_embargo_days: parametri CPCV.
        min_trades: minimo trade per recommendation tier 1/2.
        target_dsr: DSR threshold per recommendation tier 1 (default 0.95).
        progress_cb: callback ``(current, total, threshold)`` per UI.

    Returns:
        ``CalibrationResult`` con ``results`` per ogni threshold + recommendation.
    """
    base_config = base_config or BacktestConfig()
    n = len(thresholds)
    if n == 0:
        raise ValueError("thresholds list is empty")

    # First pass: run all thresholds, collect raw Sharpe per-trade per ognuno.
    # Serve per stimare ``var_sr_trials`` necessario al DSR.
    raw_sharpes: list[float] = []
    raw_states: list[PortfolioState] = []
    raw_cpcv_summaries: list[dict[str, float] | None] = []

    for i, thr in enumerate(thresholds):
        if progress_cb:
            progress_cb(i + 1, n, thr)

        # Override threshold nel config
        config_i = BacktestConfig(
            initial_capital=base_config.initial_capital,
            max_positions=base_config.max_positions,
            size_cap_pct=base_config.size_cap_pct,
            min_cash_reserve_pct=base_config.min_cash_reserve_pct,
            score_threshold=thr,
            stop_atr_mult=base_config.stop_atr_mult,
            target_atr_mult=base_config.target_atr_mult,
            time_stop_bars=base_config.time_stop_bars,
            time_stop_flat_pct=base_config.time_stop_flat_pct,
            use_earnings_gate=base_config.use_earnings_gate,
            earnings_gate_days=base_config.earnings_gate_days,
            cost_model=base_config.cost_model,
            strategy_tag=base_config.strategy_tag,
        )

        if use_cpcv:
            # Run su tutti i CPCV path, raccogli Sharpe per-path
            all_dates = sorted({d for df in universe.values() for d in df.index})
            cpcv_path_sharpes: list[float] = []
            best_state: PortfolioState | None = None
            for train_dates, test_dates in cpcv_dates_split(
                all_dates,
                n_groups=cpcv_n_groups,
                n_test_groups=cpcv_n_test_groups,
                embargo_days=cpcv_embargo_days,
            ):
                if not test_dates:
                    continue
                state_path = simulate_portfolio(
                    universe=universe,
                    scoring_fn=scoring_fn,
                    regime_series=regime_series,
                    earnings_dates=earnings_dates,
                    config=config_i,
                    start_date=test_dates[0],
                    end_date=test_dates[-1],
                    universe_provider=universe_provider,
                )
                rets = _extract_trade_returns(state_path)
                if len(rets) >= 3:
                    cpcv_path_sharpes.append(sharpe_ratio(rets))
                # Tieni l'ultimo state per metric base (fallback)
                best_state = state_path
            if best_state is None:
                best_state = simulate_portfolio(
                    universe=universe,
                    scoring_fn=scoring_fn,
                    config=config_i,
                    start_date=start_date,
                    end_date=end_date,
                    universe_provider=universe_provider,
                )
            raw_states.append(best_state)
            # Sharpe summary cross-path
            if cpcv_path_sharpes:
                import numpy as np
                arr = np.asarray(cpcv_path_sharpes)
                summary = {
                    "n_paths": len(arr),
                    "mean": float(arr.mean()),
                    "std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
                }
                raw_cpcv_summaries.append(summary)
                # Use mean cross-path come stima di Sharpe per ranking
                raw_sharpes.append(float(arr.mean()))
            else:
                raw_cpcv_summaries.append(None)
                raw_sharpes.append(0.0)
        else:
            # Single shot
            state = simulate_portfolio(
                universe=universe,
                scoring_fn=scoring_fn,
                regime_series=regime_series,
                earnings_dates=earnings_dates,
                config=config_i,
                start_date=start_date,
                end_date=end_date,
                universe_provider=universe_provider,
            )
            raw_states.append(state)
            raw_cpcv_summaries.append(None)
            rets = _extract_trade_returns(state)
            raw_sharpes.append(sharpe_ratio(rets) if len(rets) >= 3 else 0.0)

    # Variance di Sharpe attraverso threshold (per DSR correction)
    if len(raw_sharpes) > 1:
        import numpy as np
        var_sr = float(np.var(raw_sharpes, ddof=1))
    else:
        var_sr = 0.0
    # Floor a 0.01 — se var=0 il DSR collassa, evitiamo edge case numerico
    var_sr_for_dsr = max(var_sr, 0.01)

    # Second pass: ThresholdResult con DSR usando var_sr_for_dsr
    results: list[ThresholdResult] = []
    for thr, state, cpcv_sum in zip(thresholds, raw_states, raw_cpcv_summaries):
        result = _summarize_state(
            state=state,
            threshold=thr,
            n_thresholds=n,
            var_sr_trials=var_sr_for_dsr,
        )
        if cpcv_sum is not None:
            result.cpcv_n_paths = cpcv_sum.get("n_paths")
            result.cpcv_sharpe_mean = cpcv_sum.get("mean")
            result.cpcv_sharpe_std = cpcv_sum.get("std")
        results.append(result)

    # Recommendation
    rec_thr, rec_reason = _recommend_threshold(
        results, min_trades=min_trades, target_dsr=target_dsr
    )

    # Period range
    period_start = period_end = None
    if raw_states and raw_states[0].equity_curve:
        period_start = raw_states[0].equity_curve[0][0]
        period_end = raw_states[0].equity_curve[-1][0]

    return CalibrationResult(
        results=results,
        recommended_threshold=rec_thr,
        recommendation_reason=rec_reason,
        n_thresholds_tested=n,
        var_sr_across_thresholds=var_sr,
        universe_size=len(universe),
        period_start=period_start,
        period_end=period_end,
        cpcv_enabled=use_cpcv,
    )


def format_calibration_report(result: CalibrationResult) -> str:
    """Formatta CalibrationResult come tabella ASCII per CLI."""
    lines = []
    lines.append("=" * 80)
    lines.append("THRESHOLD CALIBRATION — Fase A.2 SIGNAL_ROADMAP")
    lines.append("=" * 80)
    lines.append(f"Universe:              {result.universe_size} ticker")
    if result.period_start and result.period_end:
        lines.append(f"Period:                {result.period_start} → {result.period_end}")
    lines.append(f"Thresholds tested:     {result.n_thresholds_tested}")
    lines.append(f"Var(SR) across:        {result.var_sr_across_thresholds:.4f}")
    lines.append(f"CPCV enabled:          {result.cpcv_enabled}")
    lines.append("")

    # Header
    if result.cpcv_enabled:
        lines.append(
            f"{'Threshold':>9} {'N trades':>9} {'SR/trade':>9} {'CPCV mean':>10} "
            f"{'CPCV std':>9} {'Win%':>6} {'Tot ret%':>9} {'PSR':>6} {'DSR':>6}"
        )
    else:
        lines.append(
            f"{'Threshold':>9} {'N trades':>9} {'SR/trade':>9} {'SR ann':>8} "
            f"{'Win%':>6} {'Tot ret%':>9} {'Max DD%':>9} {'PSR':>6} {'DSR':>6}"
        )
    lines.append("-" * 80)

    for r in result.results:
        is_recommended = r.threshold == result.recommended_threshold
        marker = " ★" if is_recommended else "  "
        if result.cpcv_enabled:
            cpcv_mean = f"{r.cpcv_sharpe_mean:.3f}" if r.cpcv_sharpe_mean is not None else "—"
            cpcv_std = f"{r.cpcv_sharpe_std:.3f}" if r.cpcv_sharpe_std is not None else "—"
            sr_ann_str = ""
            line = (
                f"{r.threshold:>9.1f} {r.n_trades:>9d} {r.sharpe_per_trade:>9.3f} "
                f"{cpcv_mean:>10} {cpcv_std:>9} {r.win_rate * 100:>5.1f}% "
                f"{r.total_return_pct:>9.2f} {r.psr:>6.3f} {r.dsr:>6.3f}{marker}"
            )
        else:
            sr_ann_str = (
                f"{r.sharpe_annualized:>8.3f}" if r.sharpe_annualized is not None
                else f"{'—':>8}"
            )
            line = (
                f"{r.threshold:>9.1f} {r.n_trades:>9d} {r.sharpe_per_trade:>9.3f} "
                f"{sr_ann_str} {r.win_rate * 100:>5.1f}% {r.total_return_pct:>9.2f} "
                f"{r.max_drawdown_pct:>9.2f} {r.psr:>6.3f} {r.dsr:>6.3f}{marker}"
            )
        lines.append(line)

    lines.append("")
    if result.recommended_threshold is not None:
        lines.append(f"★ Recommended threshold: {result.recommended_threshold:.1f}")
        lines.append(f"   Reason: {result.recommendation_reason}")
    else:
        lines.append("⚠ Nessuna recommendation: " + result.recommendation_reason)
    lines.append("")
    lines.append("Legenda:")
    lines.append("  PSR > 0.95 = 95% confidence Sharpe vero > 0")
    lines.append("  DSR > 0.95 = 95% confidence post correzione multiple testing")
    lines.append("  DSR sempre <= PSR (penalizza N test)")
    return "\n".join(lines)
