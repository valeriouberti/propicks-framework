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

st.success(
    "✅ **Strategy-agnostic**: filtra closed trades dal journal per strategia. "
    "Funziona per momentum / contrarian / ETF rotation / thematic — l'unica "
    "richiesta è avere closed trades nel journal con `strategy_tag` settato. "
    "Per thematic: serve aspettare 15+ trade chiusi (gate journal-evidence "
    "del subpackage, vedi `THEMATIC_STRATEGY.md` §9).",
    icon="✅",
)

with st.expander("📖 Come funziona — in 5 righe", expanded=False):
    st.markdown(
        """
**Idea**: una strategia profittevole in backtest può degradarsi in produzione
(market change, alpha decay, regime shift). Tre detector indipendenti
misurano deviation tra Sharpe atteso e Sharpe realizzato:

1. **Rolling Sharpe** — Sharpe degli ultimi N trade. Se crolla sotto soglia
   = warning visivo immediato.
2. **CUSUM** (Page 1954, Cumulative Sum) — somma cumulativa delle deviation
   tra Sharpe atteso e realizzato. Cattura **drift lento** (degrade 6-12
   mesi) prima che il rolling Sharpe lo veda.
3. **SPRT** (Wald 1945, Sequential Probability Ratio Test) — test
   sequenziale H0: "edge ancora valido" vs H1: "edge dimezzato". Decide
   ALIVE / DEAD / continua a campionare con falsi positivi controllati (α).

Decision composite:
- **ALIVE** — tutti e 3 detector OK
- **MONITOR** — 1 detector borderline
- **WARNING** — 2 detector flagged
- **ALERT_DECAY** — 3 detector flagged → **stop strategia + review**

**Quando usarla**: ogni 4-6 settimane su trade chiusi della strategia in
produzione. Se `ALERT_DECAY` → confronta con regime change (page 13)
e con backtest re-run (page 6) per discriminare alpha decay vs regime shift.
"""
    )

with st.expander("🎛️ Parametri — cosa fanno e come sceglierli", expanded=False):
    st.markdown(
        """
| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Strategy filter** | Filtra closed trades per `strategy_tag` (momentum / contrarian / etf / thematic / all) | Imposta sulla strategia che vuoi monitorare. `all` solo per overview generale (mescola Sharpe diversi) |
| **Expected Sharpe per-trade** | Sharpe per-trade atteso da backtest baseline | Da page *Backtest*: `expectancy_pct / volatility_pct`. Tipico 0.10-0.30. Default 0.20 ≈ Sharpe annuo ~1.2 (50 trade/anno) |
| **Rolling Sharpe window (trades)** | N ultimi trade per rolling Sharpe | 30 default. Sotto 20 = troppo rumoroso. Sopra 60 = rileva degrade troppo tardi |
| **CUSUM threshold (σ units)** | Soglia di trigger del CUSUM detector | 5.0 default. Più basso = più sensibile (più early-warning ma più false positive). 3.0 = aggressivo, 7.0 = conservativo |
| **SPRT α (false positive rate)** | Probabilità di "ALERT_DECAY" quando edge è ancora valido | 0.05 default = 5% false positive. Sotto 0.02 = troppo conservativo, decision tarda |

**Sample size banner**:
- < 30 trade chiusi → **OUTPUT NON AFFIDABILE**, framework ready ma stat
  signal troppo debole. Usa solo come sanity check visivo.
- 30-50 trade → output indicativo, decisioni con cautela.
- ≥ 50 trade → output affidabile, ALERT_DECAY = action.

**Workflow tipico**:
1. Filter su `momentum` (o strategia che monitori).
2. Expected Sharpe = quello del tuo backtest 5y validato.
3. Default rolling/CUSUM/SPRT params.
4. Se ALERT_DECAY: NON chiudere subito le posizioni aperte, ma **stop
   nuove entry**, fai review qualitativa (cambiamenti macro? rotazione
   settoriale? feature dell'engine deprecate?).
"""
    )


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("decay_form", border=True):
    col1, col2, col3 = st.columns(3)
    strategy_filter = col1.selectbox(
        "Strategy filter",
        options=["all", "momentum", "contrarian", "etf", "thematic"],
        index=0,
        help=(
            "Filtra closed trades per strategy_tag. "
            "thematic: richiede 15+ trade chiusi (gate journal-evidence)"
        ),
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

# Sample-size tiered banner — la statistica decay è strutturalmente fragile
# sotto 30 trade. Banner esplicito per evitare che l'utente prenda decisioni
# da output rumoroso (specie su strategie giovani come thematic).
if n_trades < 5:
    st.error(
        f"🛑 **{n_trades} trade chiusi — output NON calcolabile.** "
        f"Decay framework richiede minimo 5 trade. "
        f"Strategia `{strategy_filter}`: continua a tradare e accumula "
        f"trade chiusi nel journal prima di rieseguire."
    )
    st.stop()
elif n_trades < 30:
    st.warning(
        f"⚠️ **{n_trades} trade chiusi — output NON AFFIDABILE.** "
        f"Stat signal troppo debole sotto 30 trade. "
        f"Framework esegue, ma usa solo come **sanity check visivo**, "
        f"NON per decisioni di stop strategia. "
        + (
            "Per **thematic** specifico: il subpackage richiede 15+ trade "
            "chiusi prima della promotion gate (vedi `THEMATIC_STRATEGY.md` §9)."
            if strategy_filter == "thematic"
            else ""
        )
    )
elif n_trades < 50:
    st.info(
        f"ℹ️ **{n_trades} trade chiusi — output indicativo.** "
        f"50+ trade per signal pienamente affidabile. "
        f"Decisioni ALERT_DECAY in questa fascia richiedono sanity check "
        f"con regime composite (page 13) e backtest re-run (page 6).",
        icon="ℹ️",
    )


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
