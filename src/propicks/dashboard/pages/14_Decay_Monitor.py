"""Strategy Decay Monitor (Fase D.4 SIGNAL_ROADMAP).

Esegue rolling Sharpe + CUSUM (Page 1954) + SPRT (Wald 1945) su closed
trades dal DB o file. Output composite decision: ALERT_DECAY / WARNING /
ALIVE / MONITOR. Vedi docs/DECAY_MONITOR.md.
"""
# ruff: noqa: E402

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import page_header

st.set_page_config(page_title="Decay Monitor · Propicks", layout="wide")
page_header(
    "Strategy Decay Monitor (Fase D.4)",
    "Early-warning su edge degradation. Rolling Sharpe + CUSUM + SPRT su "
    "closed trades. Read-only diagnostic.",
)

st.info(
    "💡 **Cosa**: dato lo storico trade chiusi e Sharpe atteso da backtest, "
    "calcola 3 detector (rolling, CUSUM, SPRT) e decision composite. "
    "**ALERT_DECAY** = pause + review consigliato. Sample < 50 trade = "
    "framework ready, output indicativo only.",
    icon="ℹ️",
)


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("decay_form", border=True):
    col1, col2, col3 = st.columns(3)
    strategy_filter = col1.selectbox(
        "Strategy filter", options=["all", "momentum", "contrarian", "etf"], index=0,
    )
    expected_sharpe = col2.number_input(
        "Expected Sharpe per-trade",
        min_value=0.0, max_value=2.0, value=0.20, step=0.05,
        help="Da backtest baseline_v2. Es. 0.20 ≈ Sharpe ann ~1.2 (50 trade/anno)",
    )
    rolling_window = col3.number_input(
        "Rolling Sharpe window (trades)",
        min_value=5, max_value=200, value=30, step=5,
    )

    col4, col5 = st.columns(2)
    cusum_threshold_h = col4.number_input(
        "CUSUM threshold (σ units)",
        min_value=1.0, max_value=10.0, value=5.0, step=0.5,
        help="Più basso = più sensibile (più false positive)",
    )
    sprt_alpha = col5.number_input(
        "SPRT α (false positive)",
        min_value=0.01, max_value=0.30, value=0.05, step=0.01,
    )

    submitted = st.form_submit_button("▶️ Run decay analysis", type="primary")


if not submitted:
    st.caption("_Premi 'Run decay analysis' per fetch closed trades + compute._")
    st.stop()


# ---------------------------------------------------------------------------
# Fetch trades + run
# ---------------------------------------------------------------------------
import pandas as pd

from propicks.domain.decay_monitor import (
    decay_alert_summary, cusum_decay_detector, sprt_test, rolling_sharpe,
)
from propicks.io.db import connect

with st.spinner("Fetching closed trades…"):
    conn = connect()
    try:
        where = "status='closed' AND pnl_pct IS NOT NULL"
        params: list = []
        if strategy_filter != "all":
            where += " AND strategy = ?"
            params = [strategy_filter]
        rows = conn.execute(
            f"""SELECT ticker, strategy, entry_date, exit_date, pnl_pct, exit_reason
                FROM trades WHERE {where}
                ORDER BY exit_date ASC""",
            params,
        ).fetchall()
    finally:
        conn.close()

n_trades = len(rows)
st.metric("Closed trades found", n_trades)

if n_trades < 5:
    st.warning(
        f"⚠ Solo {n_trades} trade chiusi. Decay framework richiede minimo 5 — "
        "preferibilmente 50+. Output potrebbe essere fuorviante."
    )
    if n_trades < 5:
        st.stop()


# Convert to returns frazionali
returns = [r["pnl_pct"] / 100.0 for r in rows]


# Composite alert summary
summary = decay_alert_summary(
    returns,
    expected_sharpe_per_trade=expected_sharpe,
    rolling_window=int(rolling_window),
    cusum_threshold_h=cusum_threshold_h,
)


# ---------------------------------------------------------------------------
# Display decision
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🚨 Composite decision")

decision = summary["decision"]
color_map = {
    "ALERT_DECAY": ("🔴", "error"),
    "WARNING": ("🟡", "warning"),
    "MONITOR": ("⚪", "info"),
    "ALIVE": ("🟢", "success"),
    "NO_DATA": ("⚪", "info"),
}
emoji, level = color_map.get(decision, ("⚪", "info"))
getattr(st, level)(f"{emoji} **{decision}** — n={summary['n_obs']} trades analyzed")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Rolling SR latest", f"{summary['rolling_sharpe_latest']:.3f}" if summary['rolling_sharpe_latest'] else "—")
c2.metric("Rolling threshold (warn)", f"{summary['rolling_sharpe_threshold_warn']:.3f}")
c3.metric("CUSUM alarm @", str(summary["cusum_alarm_index"]) if summary["cusum_alarm_index"] is not None else "—")
c4.metric("SPRT decision", summary["sprt_decision"])


# ---------------------------------------------------------------------------
# Detail plots
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📈 Detail series")

import plotly.graph_objects as go

# Rolling Sharpe
rs = rolling_sharpe(returns, int(rolling_window))
fig_rs = go.Figure()
fig_rs.add_trace(go.Scatter(
    x=list(range(len(rs))), y=rs, mode="lines", name="rolling SR",
    line=dict(color="#1f77b4"),
))
fig_rs.add_hline(y=expected_sharpe, line_dash="dot", line_color="green",
                 annotation_text="expected", annotation_position="right")
fig_rs.add_hline(y=expected_sharpe * 0.5, line_dash="dot", line_color="orange",
                 annotation_text="warn (50%)", annotation_position="right")
fig_rs.update_layout(
    title="Rolling Sharpe per-trade",
    height=300, margin=dict(l=20, r=20, t=40, b=20),
    xaxis_title="trade index", yaxis_title="rolling SR",
)
st.plotly_chart(fig_rs, use_container_width=True)


# CUSUM
import numpy as np
cusum_full = cusum_decay_detector(
    returns,
    expected_mean=expected_sharpe * float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0,
    threshold_h=cusum_threshold_h,
)
fig_cu = go.Figure()
fig_cu.add_trace(go.Scatter(
    x=list(range(len(cusum_full["cusum_series"]))),
    y=cusum_full["cusum_series"],
    mode="lines", name="CUSUM", line=dict(color="#d62728"),
))
fig_cu.add_hline(y=cusum_full.get("threshold", 0), line_dash="dot",
                 line_color="red", annotation_text="alarm threshold")
if cusum_full["alarm_index"] is not None:
    fig_cu.add_vline(x=cusum_full["alarm_index"], line_dash="dash", line_color="red",
                     annotation_text=f"ALARM @ {cusum_full['alarm_index']}",
                     annotation_position="top")
fig_cu.update_layout(
    title="CUSUM lower (downward drift detector)",
    height=300, margin=dict(l=20, r=20, t=40, b=20),
    xaxis_title="trade index", yaxis_title="CUSUM",
)
st.plotly_chart(fig_cu, use_container_width=True)


# SPRT
sprt_full = sprt_test(returns, h0_mean=0.0, alpha=sprt_alpha)
if "log_lr_series" in sprt_full:
    fig_sp = go.Figure()
    fig_sp.add_trace(go.Scatter(
        x=list(range(len(sprt_full["log_lr_series"]))),
        y=sprt_full["log_lr_series"],
        mode="lines", name="log-LR", line=dict(color="#2ca02c"),
    ))
    fig_sp.add_hline(y=sprt_full["boundary_a"], line_dash="dot", line_color="green",
                     annotation_text="A: edge alive")
    fig_sp.add_hline(y=sprt_full["boundary_b"], line_dash="dot", line_color="red",
                     annotation_text="B: edge dead")
    if sprt_full.get("decision_index") is not None:
        fig_sp.add_vline(x=sprt_full["decision_index"], line_dash="dash",
                         line_color="purple",
                         annotation_text=f"{sprt_full['decision']} @ {sprt_full['decision_index']}")
    fig_sp.update_layout(
        title="SPRT log-likelihood ratio",
        height=300, margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title="trade index", yaxis_title="log-LR",
    )
    st.plotly_chart(fig_sp, use_container_width=True)


# Trade detail table
st.divider()
st.subheader("📋 Trade detail")
df_trades = pd.DataFrame([dict(r) for r in rows])
st.dataframe(df_trades, use_container_width=True, hide_index=True)


# Caveat
st.divider()
st.caption(
    "**Caveat**: CUSUM ottimizzato per cambio abrupt > 1σ — gradual decay sub-optimal "
    "con default sensitivity. SPRT decision sticky (non si aggiorna su regime change). "
    "Sigma-stationarity assumption — vol regime change può triggerare false alarm. "
    "Vedi docs/DECAY_MONITOR.md."
)
