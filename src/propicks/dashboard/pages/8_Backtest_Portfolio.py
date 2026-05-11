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

# ─── Compatibility banner ──────────────────────────────────────────────────
st.warning(
    "⚠️ **Strategia supportata: SOLO Momentum.** Anche con flag avanzati "
    "(cross-sectional rank, quality filter, earnings revision overlay) il "
    "core scoring resta `domain.scoring.rank_universe` momentum. "
    "Contrarian / ETF Rotation / Thematic non sono supportate. Per ETF "
    "rotation serve un portfolio engine separato (TODO Phase 9). "
    "Per thematic: **gate journal-evidence** (15 trade chiusi via "
    "`propicks-themes` + manual journal) prima di promuovere a backtest "
    "dedicato (vedi `THEMATIC_STRATEGY.md` §9).",
    icon="⚠️",
)

with st.expander("📖 Come funziona (in 4 righe)", expanded=False):
    st.markdown(
        """
**Come funziona**:
1. Per ogni barra giornaliera scora tutti i ticker dell'universo, ranking
   cross-sectional o threshold-based.
2. Apre top-N posizioni rispettando `MAX_POSITIONS` (10) + cash reserve
   minimo + earnings hard gate (skip entry se earnings < 5gg).
3. Ogni trade paga **TC** (transaction cost) + slippage configurabile.
4. Walk-forward OOS split: prime N% delle barre = IS (in-sample, calibrazione),
   resto = OOS (out-of-sample, verifica). Monte Carlo bootstrap = CI 95%
   su Sharpe/Win-rate/Drawdown per misurare incertezza statistica.

**Quando usarla**:
- Hai già validato la formula su single-ticker (page *Backtest*).
- Vuoi sapere se la strategia regge con **costi reali** (TC 5-10bps),
  **vincoli di capitale** (max 10 posizioni), e **decisioni cross-ticker**
  (chi entra se 5 ticker hanno score 70+ ma ho solo 3 slot).
- Vuoi un Sharpe/CAGR difensibile per produzione, non solo "il backtest funziona".

**Workflow tipico**:
1. Universe: lista 20-50 ticker liquidi (S&P 500 subset).
2. Periodo `5y`, threshold `60`, OOS split `0.70` (70% IS / 30% OOS).
3. TC `5bps`, Monte Carlo `1000` runs.
4. Leggi: Sharpe IS vs OOS (gap > 50% = overfitting). DSR (Deflated Sharpe
   Ratio, vedi page *Calibration* per multi-trial DSR rigoroso).
"""
    )

with st.expander("🎛️ Parametri — cosa fanno e come sceglierli", expanded=False):
    st.markdown(
        """
**Universe & periodo**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Universe** | Lista ticker testati. Ogni barra ranking cross-sectional fra tutti | 20-50 nomi liquidi. Sotto 10 = poca diversificazione, sopra 100 = lentezza fetch. CLI per S&P 500 full |
| **Periodo** | Storia daily fetchata | 5y default. 10y se vuoi 2 cicli completi. Sotto 2y = troppo pochi trade |

**Entry / Exit**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Score threshold** | Min score per entry | 60 default. Combina con cross-sectional rank per universi grandi |
| **Stop in ATR** | Distanza stop in multipli ATR | 2.0 default |
| **Target in ATR** | Distanza target. R:R = target/stop | 4.0 default → R:R 2:1 |
| **Time stop (bars)** | Max giorni in trade | 30 default. Momentum chiude swing in 4-8 settimane |

**Costi & capital**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Capitale iniziale** | Budget simulazione | 10k default. Numero non cambia metriche %, solo importi nominali |
| **TC (bps)** | Transaction cost totale (spread+slip+fees) | 5 default per US large-cap retail. 10 per EU mid-cap, 20 per emerging |

**Validation overfitting**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **OOS split** | % storia per IS (in-sample, calibrazione). Il resto = OOS (verifica) | 0.70 default. 0.50 più conservativo. Sotto 0.30 IS = troppo pochi trade per calibrare |
| **Monte Carlo runs** | N bootstrap su trade list per CI 95% Sharpe/WinRate/DD | 1000 default. 500 = veloce, 5000 = CI più strette. Linear scaling tempo |

**Survivorship bias**:

Per tickers cancellati storicamente (es. ENRN, LEH) usa `--historical-membership sp500`
da CLI: il backtest popola l'universo con membership point-in-time S&P 500.
Senza, stai testando solo i sopravvissuti = bias positivo strutturale.
Vedi `SURVIVORSHIP_BIAS_ANALYSIS.md`.

**Regole pratiche**:
- IS Sharpe 1.5 / OOS Sharpe 0.4 = **overfitting severo**, threshold non usabile.
- IS 1.2 / OOS 1.0 = setup robusto.
- Monte Carlo 5° percentile Sharpe < 0 = strategia non statisticamente
  significativa, declina deployment.
- DSR < 0.95 (vedi page *Calibration*) = troppe trial → rischio false discovery.
"""
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

    st.divider()
    st.markdown("**Fase A-B SIGNAL_ROADMAP** _(opt-in)_")
    col11, col12 = st.columns(2)
    use_membership = col11.checkbox(
        "🛡️ Survivorship-correct (Fase A.1)",
        value=False,
        help=(
            "Filtra ticker eligible at-time-T via index_membership_history. "
            "Risolve survivorship bias. Richiede import preventivo: "
            "`python scripts/import_sp500_history.py`"
        ),
    )
    use_xs_rank = col12.checkbox(
        "🎯 Cross-sectional rank (Fase B.1)",
        value=False,
        help=(
            "Threshold = percentile rank (0-100) invece di score assoluto. "
            "Es. threshold 80 = entry top quintile (P80+) ogni giorno. "
            "Edge documentato Jegadeesh-Titman 1993."
        ),
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
    use_cross_sectional_rank=use_xs_rank,
)

# Fase A.1 SIGNAL_ROADMAP — survivorship correction
universe_provider = None
if use_membership:
    from propicks.io.index_membership import (
        build_universe_provider,
        count_membership_rows,
        get_membership_date_range,
    )
    n_rows = count_membership_rows("sp500")
    rng = get_membership_date_range("sp500")
    if n_rows == 0 or rng is None:
        st.error(
            "❌ Membership history non importata. "
            "Esegui prima: `python scripts/import_sp500_history.py`"
        )
        st.stop()
    st.caption(
        f"🛡️ Membership filter sp500 attivo — {n_rows:,} row, "
        f"range {rng[0]} → {rng[1]}"
    )
    universe_provider = build_universe_provider("sp500")

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
            universe_provider=universe_provider,
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
        universe_provider=universe_provider,
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

# Equity curve + Drawdown underwater + Monthly heatmap (Plotly)
if state.equity_curve:
    import pandas as pd
    import plotly.graph_objects as go

    eq_df = pd.DataFrame(state.equity_curve, columns=["date", "equity"])
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.set_index("date").sort_index()
    cummax = eq_df["equity"].cummax()
    eq_df["drawdown"] = (eq_df["equity"] - cummax) / cummax * 100
    eq_df["peak"] = cummax

    # Max DD info
    dd_min = float(eq_df["drawdown"].min())
    dd_min_date = eq_df["drawdown"].idxmin()
    # Recovery date: primo timestamp post-trough con equity ≥ peak al trough
    peak_at_trough = float(eq_df.loc[dd_min_date, "peak"])
    recovery = eq_df.loc[dd_min_date:].query("equity >= @peak_at_trough")
    recovery_date = recovery.index[0] if not recovery.empty else None

    # ─── Equity curve ───
    st.subheader("📈 Equity curve")
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=eq_df.index, y=eq_df["equity"],
        mode="lines", name="Equity",
        line=dict(color="#3b82f6", width=2),
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Equity € %{y:,.2f}<extra></extra>",
    ))
    fig_eq.add_trace(go.Scatter(
        x=eq_df.index, y=eq_df["peak"],
        mode="lines", name="Peak",
        line=dict(color="#94a3b8", width=1, dash="dot"),
        hovertemplate="Peak € %{y:,.2f}<extra></extra>",
    ))
    fig_eq.update_layout(
        height=320, hovermode="x unified",
        xaxis_title="", yaxis_title="Equity €",
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1.0),
    )
    st.plotly_chart(fig_eq, width="stretch")

    # ─── Drawdown underwater ───
    st.subheader("📉 Drawdown (underwater)")
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=eq_df.index, y=eq_df["drawdown"],
        mode="lines", name="Drawdown",
        line=dict(color="#dc2626", width=1.5),
        fill="tozeroy", fillcolor="rgba(220,38,38,0.25)",
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>DD %{y:+.2f}%<extra></extra>",
    ))
    # Mark peak DD
    fig_dd.add_trace(go.Scatter(
        x=[dd_min_date], y=[dd_min],
        mode="markers+text",
        marker=dict(color="#7f1d1d", size=10, symbol="x"),
        text=[f"max {dd_min:+.2f}%"],
        textposition="bottom center",
        showlegend=False,
        hovertemplate=f"Max DD {dd_min:+.2f}%<br>%{{x|%Y-%m-%d}}<extra></extra>",
    ))
    fig_dd.add_hline(y=0, line_color="#94a3b8", line_width=1)
    fig_dd.update_layout(
        height=260, hovermode="x unified",
        xaxis_title="", yaxis_title="Drawdown %",
        margin=dict(l=20, r=20, t=20, b=20),
        showlegend=False,
    )
    st.plotly_chart(fig_dd, width="stretch")

    # DD info caption
    if recovery_date is not None:
        recovery_days = (recovery_date - dd_min_date).days
        st.caption(
            f"Max DD **{dd_min:+.2f}%** @ {dd_min_date.date()} · "
            f"Recovery to peak in **{recovery_days} giorni** ({recovery_date.date()})."
        )
    else:
        from_today = (eq_df.index[-1] - dd_min_date).days
        st.caption(
            f"Max DD **{dd_min:+.2f}%** @ {dd_min_date.date()} · "
            f"⚠️ NOT recovered yet — {from_today} giorni from trough."
        )

    # ─── Monthly returns heatmap ───
    if len(eq_df) >= 30:
        st.subheader("🗓️ Monthly returns heatmap")
        monthly = eq_df["equity"].resample("ME").last().pct_change().dropna() * 100
        if len(monthly) >= 2:
            mdf = monthly.to_frame("ret")
            mdf["year"] = mdf.index.year
            mdf["month"] = mdf.index.month
            pivot = mdf.pivot_table(index="year", columns="month", values="ret", aggfunc="first")
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            cols_present = [c for c in range(1, 13) if c in pivot.columns]

            fig_hm = go.Figure(data=go.Heatmap(
                z=pivot[cols_present].values,
                x=[month_names[c - 1] for c in cols_present],
                y=[str(y) for y in pivot.index],
                colorscale=[
                    [0.0, "#7f1d1d"], [0.4, "#dc2626"], [0.5, "#f3f4f6"],
                    [0.6, "#16a34a"], [1.0, "#14532d"],
                ],
                zmid=0,
                text=[[f"{v:+.1f}%" if pd.notna(v) else "" for v in row] for row in pivot[cols_present].values],
                texttemplate="%{text}",
                textfont=dict(size=11),
                hovertemplate="<b>%{y} %{x}</b><br>%{z:+.2f}%<extra></extra>",
                colorbar=dict(title="Return %"),
            ))
            fig_hm.update_layout(
                height=max(180, len(pivot) * 50 + 60),
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis=dict(side="top"),
                yaxis=dict(autorange="reversed"),  # year più recente in alto
            )
            st.plotly_chart(fig_hm, width="stretch")
            # Quick stats
            best_m = monthly.idxmax()
            worst_m = monthly.idxmin()
            n_pos = (monthly > 0).sum()
            st.caption(
                f"Best month **{monthly.max():+.2f}%** ({best_m.strftime('%Y-%m')}) · "
                f"Worst **{monthly.min():+.2f}%** ({worst_m.strftime('%Y-%m')}) · "
                f"Positive months **{n_pos}/{len(monthly)}** ({n_pos / len(monthly) * 100:.0f}%)"
            )

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
