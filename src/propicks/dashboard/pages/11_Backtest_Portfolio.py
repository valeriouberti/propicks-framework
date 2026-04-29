"""Backtest Portfolio (Phase 6) — multi-ticker con TC + portfolio constraints.

Mirror di ``propicks-backtest --portfolio``. Differenze dalla page single-ticker:
- Cross-ticker simulation (MAX_POSITIONS cap, cash reserve, earnings gate)
- Transaction costs + slippage configurabili
- Walk-forward OOS split per detection overfitting
- Monte Carlo bootstrap per CI 95% su Sharpe/WinRate/MaxDD
"""
# ruff: noqa: E402
# Imports post-form sono intentional: evitano il fetch yfinance a ogni rerun
# sul form interaction (Streamlit re-runs completi).

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import invariants_note, page_header

st.set_page_config(page_title="Backtest Portfolio · Propicks", layout="wide")
page_header(
    "Backtest Portfolio v2",
    "Phase 6: portfolio-level simulation con TC + slippage + max positions cap "
    "+ cash reserve. Walk-forward OOS split + Monte Carlo CI. **Validation pre-Phase 7.**",
)
invariants_note()

st.info(
    "💡 **Perché portfolio-level**: il single-ticker backtest testa se la formula "
    "ha edge *isolato*. Il portfolio test verifica se la strategia funziona "
    "con **budget reale**: cross-ticker competition, cash limitato, costs. "
    "Numeri più bassi del single-ticker sono normali (e realistici).",
    icon="ℹ️",
)


# ---------------------------------------------------------------------------
# Form input
# ---------------------------------------------------------------------------
with st.form("bt_portfolio_form", border=True):
    col1, col2 = st.columns([3, 2])
    tickers_raw = col1.text_input(
        "Universe (tickers separati da spazio/virgola)",
        placeholder="AAPL MSFT NVDA GOOGL META AMZN",
        help="Più ampio = più diversificazione + più tempo di fetch.",
    )
    period = col2.selectbox(
        "Periodo",
        options=["1y", "2y", "3y", "5y", "10y", "max"],
        index=3,
    )

    col3, col4, col5 = st.columns(3)
    threshold = col3.number_input(
        "Score threshold",
        min_value=40.0, max_value=100.0, value=60.0, step=5.0,
        help="Score minimo per aprire un'entry",
    )
    stop_atr = col4.number_input(
        "Stop in ATR",
        min_value=0.5, max_value=10.0, value=2.0, step=0.5,
    )
    target_atr = col5.number_input(
        "Target in ATR",
        min_value=1.0, max_value=20.0, value=4.0, step=0.5,
    )

    col6, col7, col8 = st.columns(3)
    time_stop = col6.number_input(
        "Time stop (bars)", min_value=5, max_value=180, value=30, step=5,
    )
    initial_capital = col7.number_input(
        "Capitale iniziale", min_value=1000.0, value=10_000.0, step=1000.0,
    )
    tc_bps = col8.number_input(
        "TC (bps)",
        min_value=0.0, max_value=100.0, value=5.0, step=1.0,
        help="Transaction cost totale (spread+slip). Default 5bp.",
    )

    st.divider()
    st.markdown("**Advanced**")
    col9, col10 = st.columns(2)
    oos_split = col9.slider(
        "OOS split (0 = disabilitato)",
        min_value=0.0, max_value=0.9, value=0.0, step=0.05,
        help="0.70 = 70%% train / 30%% test per detection overfitting. 0 = no split.",
    )
    mc_samples = col10.number_input(
        "Monte Carlo samples (0 = disabilitato)",
        min_value=0, max_value=2000, value=0, step=100,
        help="Bootstrap su trade sequence → CI 95%% Sharpe/WinRate/MaxDD. 500-1000 raccomandato.",
    )

    submitted = st.form_submit_button("▶️ Esegui backtest", type="primary")


if not submitted:
    st.caption(
        "_Premi 'Esegui backtest' per avviare la simulazione. Per universe "
        "di 10+ ticker con periodo 5y, aspetta 30-90 secondi._"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Parse + fetch
# ---------------------------------------------------------------------------
tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]
if not tickers:
    st.error("Inserisci almeno un ticker.")
    st.stop()

from propicks.backtest.costs import CostModel
from propicks.backtest.metrics_v2 import compute_portfolio_metrics
from propicks.backtest.portfolio_engine import BacktestConfig, simulate_portfolio
from propicks.backtest.walkforward import monte_carlo_bootstrap, walk_forward_split
from propicks.market.yfinance_client import DataUnavailable, download_history

with st.status(f"Fetching {len(tickers)} ticker ({period})…", expanded=True) as status:
    universe: dict = {}
    for t in tickers:
        try:
            universe[t] = download_history(t, period=period)
            st.write(f"✓ {t}: {len(universe[t])} bars")
        except DataUnavailable as exc:
            st.write(f"✗ {t}: {exc}")

    if not universe:
        status.update(label="Errore: nessun ticker disponibile", state="error")
        st.stop()
    status.update(label=f"Fetch completato: {len(universe)}/{len(tickers)}", state="complete")


# Scoring function (replicata dal CLI)
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


def _scoring_fn(ticker, hist_slice):
    if len(hist_slice) < 200:
        return None
    close = hist_slice["Close"]
    high = hist_slice["High"]
    low = hist_slice["Low"]
    volume = hist_slice["Volume"]
    ema_fast = compute_ema(close, EMA_FAST).iloc[-1]
    ema_slow = compute_ema(close, EMA_SLOW).iloc[-1]
    rsi = compute_rsi(close, RSI_PERIOD).iloc[-1]
    atr = compute_atr(high, low, close, ATR_PERIOD).iloc[-1]
    price = float(close.iloc[-1])
    cur_vol = float(volume.iloc[-1])
    prev_vol = volume.iloc[-VOLUME_AVG_PERIOD - 1 : -1]
    avg_vol = float(prev_vol.mean()) if not prev_vol.empty else cur_vol
    high_52w = float(high.tail(min(252, len(high))).max())
    ema_fast_s = compute_ema(close, EMA_FAST)
    ema_slow_s = compute_ema(close, EMA_SLOW)
    prev_ema_fast = float(ema_fast_s.iloc[-6]) if len(ema_fast_s) >= 6 else float("nan")
    prev_ema_slow = float(ema_slow_s.iloc[-6]) if len(ema_slow_s) >= 6 else float("nan")
    composite = (
        score_trend(price, float(ema_fast), float(ema_slow)) * WEIGHT_TREND
        + score_momentum(float(rsi)) * WEIGHT_MOMENTUM
        + score_volume(cur_vol, avg_vol) * WEIGHT_VOLUME
        + score_distance_from_high(price, high_52w) * WEIGHT_DISTANCE_HIGH
        + score_volatility(float(atr), price) * WEIGHT_VOLATILITY
        + score_ma_cross(float(ema_fast), float(ema_slow), prev_ema_fast, prev_ema_slow) * WEIGHT_MA_CROSS
    )
    return max(0.0, min(100.0, composite))


cost_model = CostModel.from_bps(tc_bps) if tc_bps > 0 else CostModel.zero()
config = BacktestConfig(
    initial_capital=initial_capital,
    score_threshold=threshold,
    stop_atr_mult=stop_atr,
    target_atr_mult=target_atr,
    time_stop_bars=int(time_stop),
    cost_model=cost_model,
    strategy_tag="momentum",
    use_earnings_gate=False,
)

# ---------------------------------------------------------------------------
# Walk-forward mode
# ---------------------------------------------------------------------------
if oos_split > 0:
    with st.spinner(f"Walk-forward split {oos_split:.0%} train / {1 - oos_split:.0%} test…"):
        wf = walk_forward_split(
            universe=universe,
            scoring_fn=_scoring_fn,
            split_ratio=oos_split,
            config=config,
        )

    st.subheader("📊 Walk-forward OOS results")
    c1, c2 = st.columns(2)
    c1.markdown(f"**Train**: `{wf.train_window[0]}` → `{wf.train_window[1]}`")
    c2.markdown(f"**Test**: `{wf.test_window[0]}` → `{wf.test_window[1]}`")

    compare_rows = [
        {
            "Metric": "Total return",
            "Train": f"{wf.train_metrics.get('total_return_pct', 0):+.2f}%",
            "Test": f"{wf.test_metrics.get('total_return_pct', 0):+.2f}%",
        },
        {
            "Metric": "CAGR",
            "Train": f"{wf.train_metrics.get('cagr_pct', 0):+.2f}%",
            "Test": f"{wf.test_metrics.get('cagr_pct', 0):+.2f}%",
        },
        {
            "Metric": "Sharpe annualized",
            "Train": f"{wf.train_metrics.get('sharpe_annualized') or 0:.2f}",
            "Test": f"{wf.test_metrics.get('sharpe_annualized') or 0:.2f}",
        },
        {
            "Metric": "Max drawdown",
            "Train": f"{wf.train_metrics.get('max_drawdown_pct', 0):+.2f}%",
            "Test": f"{wf.test_metrics.get('max_drawdown_pct', 0):+.2f}%",
        },
        {
            "Metric": "N trades",
            "Train": f"{wf.train_metrics.get('n_trades', 0)}",
            "Test": f"{wf.test_metrics.get('n_trades', 0)}",
        },
    ]
    st.dataframe(compare_rows, width="stretch", hide_index=True)

    if wf.degradation_score >= 0:
        st.success(f"✅ **Degradation score {wf.degradation_score:+.3f}** — test performance ≥ train. No overfitting evidence.")
    else:
        st.warning(
            f"⚠️ **Degradation score {wf.degradation_score:+.3f}** — test < train. "
            "Possibile overfitting — i pesi scoring potrebbero essere specifici al training window."
        )

    st.stop()

# ---------------------------------------------------------------------------
# Single-shot mode
# ---------------------------------------------------------------------------
with st.spinner(f"Simulating {len(universe)} ticker × {period}…"):
    state = simulate_portfolio(
        universe=universe,
        scoring_fn=_scoring_fn,
        config=config,
    )
    metrics = compute_portfolio_metrics(state)

if "error" in metrics:
    st.error(f"Backtest fallito: {metrics['error']}")
    st.stop()

# ---------------------------------------------------------------------------
# Display results
# ---------------------------------------------------------------------------
st.subheader("📊 Portfolio KPIs")
cols = st.columns(4)
cols[0].metric(
    "Total return",
    f"{metrics['total_return_pct']:+.2f}%",
    delta=f"€ {metrics['final_value'] - metrics['initial_capital']:+,.0f}",
)
cols[1].metric("CAGR", f"{metrics.get('cagr_pct', 0):+.2f}%")
cols[2].metric(
    "Sharpe ann.",
    f"{metrics.get('sharpe_annualized') or 0:.2f}",
)
cols[3].metric("Max DD", f"{metrics['max_drawdown_pct']:+.2f}%")

cols2 = st.columns(4)
cols2[0].metric("N trades", metrics["n_trades"])
cols2[1].metric("Win rate", f"{metrics['win_rate'] * 100:.1f}%")
cols2[2].metric(
    "Profit factor",
    f"{metrics.get('profit_factor') or 0:.2f}" if metrics.get("profit_factor") else "—",
)
cols2[3].metric(
    "Calmar ratio",
    f"{metrics.get('calmar_ratio') or 0:.2f}" if metrics.get("calmar_ratio") else "—",
)

# Equity curve chart
if state.equity_curve:
    import pandas as pd
    eq_df = pd.DataFrame(state.equity_curve, columns=["date", "equity"])
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date")
    eq_df["drawdown"] = (eq_df["equity"] - eq_df["equity"].cummax()) / eq_df["equity"].cummax() * 100

    st.subheader("📈 Equity curve")
    st.line_chart(eq_df["equity"])
    st.subheader("📉 Drawdown")
    st.area_chart(eq_df["drawdown"])

# Exit reasons breakdown
if metrics.get("exit_reasons"):
    st.subheader("🚪 Exit reasons")
    exit_rows = [{"Reason": k, "Count": v} for k, v in metrics["exit_reasons"].items()]
    st.dataframe(exit_rows, width="stretch", hide_index=True)

# Per-strategy breakdown se > 1 strategia
if metrics.get("by_strategy") and len(metrics["by_strategy"]) > 1:
    st.subheader("🎯 Per-strategy")
    strat_rows = []
    for strat, s in metrics["by_strategy"].items():
        strat_rows.append({
            "Strategy": strat,
            "N trades": s["n_trades"],
            "Win rate": f"{s['win_rate'] * 100:.1f}%",
            "Avg P&L %": f"{s['avg_pnl_pct']:+.2f}%",
            "Total P&L %": f"{s['total_pnl_pct']:+.2f}%",
        })
    st.dataframe(strat_rows, width="stretch", hide_index=True)

# Trades table
if state.closed_trades:
    st.subheader(f"📋 Trade chiusi ({len(state.closed_trades)})")
    trade_rows = [
        {
            "Ticker": t.ticker,
            "Entry": t.entry_date.isoformat(),
            "Exit": t.exit_date.isoformat(),
            "Days": t.duration_days,
            "Entry $": f"{t.effective_entry:.2f}",
            "Exit $": f"{t.effective_exit:.2f}",
            "Shares": t.shares,
            "Reason": t.exit_reason,
            "P&L net %": f"{t.pnl_pct:+.2f}%",
            "P&L €": f"{t.pnl_net:+.2f}",
        }
        for t in sorted(state.closed_trades, key=lambda x: x.exit_date, reverse=True)
    ]
    st.dataframe(trade_rows, width="stretch", hide_index=True, height=400)

# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
if mc_samples > 0 and state.closed_trades:
    st.subheader(f"🎲 Monte Carlo bootstrap ({mc_samples} samples)")
    with st.spinner("Running bootstrap…"):
        mc = monte_carlo_bootstrap(state.closed_trades, n_samples=int(mc_samples))

    mc_rows = [
        {
            "Metric": "Sharpe",
            "Mean": f"{mc.sharpe_mean:.3f}",
            "CI 95% lower": f"{mc.sharpe_ci[0]:.3f}",
            "CI 95% upper": f"{mc.sharpe_ci[1]:.3f}",
        },
        {
            "Metric": "Win rate",
            "Mean": f"{mc.win_rate_mean * 100:.1f}%",
            "CI 95% lower": f"{mc.win_rate_ci[0] * 100:.1f}%",
            "CI 95% upper": f"{mc.win_rate_ci[1] * 100:.1f}%",
        },
        {
            "Metric": "Total return",
            "Mean": f"{mc.total_return_mean * 100:+.2f}%",
            "CI 95% lower": f"{mc.total_return_ci[0] * 100:+.2f}%",
            "CI 95% upper": f"{mc.total_return_ci[1] * 100:+.2f}%",
        },
        {
            "Metric": "Max DD",
            "Mean": f"{mc.max_dd_mean * 100:+.2f}%",
            "CI 95% lower": f"{mc.max_dd_ci[0] * 100:+.2f}%",
            "CI 95% upper": f"{mc.max_dd_ci[1] * 100:+.2f}%",
        },
    ]
    st.dataframe(mc_rows, width="stretch", hide_index=True)

    rob_emoji = "🟢" if mc.robustness_score >= 0.7 else "🟡" if mc.robustness_score >= 0.4 else "🔴"
    rob_label = (
        "**robusto** — il risultato non dipende dal random"
        if mc.robustness_score >= 0.7
        else "**moderato** — margine di incertezza presente"
        if mc.robustness_score >= 0.4
        else "**fragile** — il risultato è dominato dal luck, non edge"
    )
    st.markdown(f"### Robustness score: {rob_emoji} `{mc.robustness_score:.3f}` — {rob_label}")
    st.caption(
        "_`sharpe_lower_95 / sharpe_mean`. >0.7 = CI stretto vicino al mean → "
        "robusto. <0.4 = CI larga, risultato dominato dal luck._"
    )
