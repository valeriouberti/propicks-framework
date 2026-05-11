"""Stress test portfolio — simulazione P&L su shock di mercato.

3 modalità:
1. **Market shock uniform**: applica -X% a tutte le posizioni, modulato da beta
   se beta-weighted toggle attivo. Default: SPX shock × beta.
2. **Sector shock granular**: user assegna shock % per sector (es. tech -30%,
   energy +10%). Applicato per-position via sector_resolver config-first.
3. **Preset scenarios**: COVID 2020 (-34% SPX 30gg), GFC 2008 (-50%), Aug 2024
   carry trade (-10% in 5gg), Aug 2011 debt ceiling (-17% 1mo).

Output:
- Total portfolio Δ€ + Δ%
- Per-position waterfall chart
- Bucket impact (Stock / ETF / Cash)
- Cash reserve check post-shock
- Risk score: distance to weekly loss limit
"""
# ruff: noqa: E402

from __future__ import annotations

import streamlit as st

from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import MAX_LOSS_WEEKLY_PCT, MIN_CASH_RESERVE_PCT
from propicks.dashboard._shared import (
    cached_current_prices,
    cached_ticker_betas,
    cached_ticker_sectors,
    fmt_eur,
    fmt_pct,
    invariants_note,
    load_portfolio,
    page_header,
)
from propicks.domain.etf_universe import resolve_sector_key
from propicks.domain.sizing import (
    is_etf_position,
    portfolio_market_value,
    portfolio_value,
)

st.set_page_config(page_title="Stress Test · Propicks", layout="wide")
page_header(
    "Stress Test portfolio",
    "Simula P&L portfolio sotto shock di mercato. Diversità da VaR (statistical "
    "bootstrap): qui shock deterministici per stress test scenario-based.",
)
invariants_note()

st.info(
    "💡 **Come funziona**: per ogni posizione calcola Δ€ = market_value × shock × β "
    "(se beta-weighted attivo). Sector mode applica shock per-sector via "
    "`resolve_sector_key`. Preset scenarios usano valori storici reali "
    "(SPX peak-to-trough). **Cash NON è impattato** (immune da equity shock).",
    icon="ℹ️",
)

portfolio = load_portfolio()
positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)
total = portfolio_value(portfolio)

if not positions:
    st.warning("Portfolio vuoto. Apri posizioni da page 8 o broker import per stress test.")
    st.stop()

tickers = sorted(positions.keys())

with st.spinner("Fetching prezzi spot, beta, sector…"):
    prices = cached_current_prices(tuple(tickers))
    betas = cached_ticker_betas(tuple(tickers))
    sector_yf = cached_ticker_sectors(tuple(tickers))

sector_map = {t: resolve_sector_key(t, yahoo_sector_raw=s) for t, s in sector_yf.items()}
total_market = portfolio_market_value(portfolio, prices)


# ---------------------------------------------------------------------------
# Mode selector
# ---------------------------------------------------------------------------
mode = st.radio(
    "Modalità stress test",
    options=("Market shock (uniform)", "Sector shock (granular)", "Preset scenarios"),
    horizontal=True,
    key="stress_mode",
)

st.divider()

# Build position table base
def _pos_market_value(ticker: str, pos: dict) -> float:
    cur = prices.get(ticker)
    if cur is None:
        cur = float(pos.get("entry_price") or 0)
    return float(pos.get("shares") or 0) * float(cur)


# ---------------------------------------------------------------------------
# Compute Δ given shock_per_ticker dict
# ---------------------------------------------------------------------------
def _compute_impact(shock_per_ticker: dict[str, float]) -> dict:
    """shock_per_ticker: ticker → shock fraction (es. -0.20 = -20%).

    Returns:
        Dict con per_position list + totals.
    """
    rows = []
    total_delta = 0.0
    stock_delta = 0.0
    etf_delta = 0.0

    for tk, pos in positions.items():
        mv = _pos_market_value(tk, pos)
        shock = shock_per_ticker.get(tk, 0.0)
        delta = mv * shock
        new_mv = mv + delta
        rows.append({
            "ticker": tk,
            "sector": sector_map.get(tk) or "unknown",
            "mv_pre": mv,
            "shock_pct": shock * 100,
            "delta_eur": delta,
            "mv_post": new_mv,
            "bucket": "ETF" if is_etf_position(pos, ticker=tk) else "Stock",
        })
        total_delta += delta
        if is_etf_position(pos, ticker=tk):
            etf_delta += delta
        else:
            stock_delta += delta

    rows.sort(key=lambda r: r["delta_eur"])  # worst first
    pre_total = total_market
    post_total = pre_total + total_delta + 0  # cash invariant
    return {
        "rows": rows,
        "total_delta_eur": total_delta,
        "total_delta_pct": (total_delta / pre_total * 100) if pre_total else 0,
        "stock_delta_eur": stock_delta,
        "etf_delta_eur": etf_delta,
        "pre_total": pre_total,
        "post_total_invested": pre_total + total_delta,
        "post_total_with_cash": cash + pre_total + total_delta,
    }


# ---------------------------------------------------------------------------
# Mode 1: Market shock uniform
# ---------------------------------------------------------------------------
if mode == "Market shock (uniform)":
    c1, c2 = st.columns([2, 1])
    shock_pct = c1.slider(
        "Market shock %",
        min_value=-50.0, max_value=20.0, value=-20.0, step=1.0,
        key="market_shock_pct",
        help="Negative = crash. Applicato uniformemente a tutte le posizioni.",
    )
    use_beta = c2.checkbox(
        "Beta-weighted",
        value=True,
        key="market_shock_use_beta",
        help="Se ON: shock × β per-ticker (high-beta soffre di più). "
             "Se OFF: shock uniforme su tutto invested.",
    )

    # Build shock dict
    shock_dict = {}
    fallback_beta_count = 0
    for tk in positions:
        beta = betas.get(tk)
        if use_beta:
            if beta is None:
                fallback_beta_count += 1
                beta = 1.0
            shock_dict[tk] = (shock_pct / 100) * beta
        else:
            shock_dict[tk] = shock_pct / 100

    impact = _compute_impact(shock_dict)

    if use_beta and fallback_beta_count > 0:
        st.caption(
            f"_β fallback 1.0 per {fallback_beta_count} ticker_ "
            "(ETF / IPO recenti / esteri illiquidi senza beta Yahoo)."
        )


# ---------------------------------------------------------------------------
# Mode 2: Sector shock
# ---------------------------------------------------------------------------
elif mode == "Sector shock (granular)":
    sectors_in_portfolio = sorted({
        s for s in sector_map.values() if s
    } | {"unknown"} if any(v is None for v in sector_map.values()) else {
        s for s in sector_map.values() if s
    })

    st.caption("Assegna shock % a ogni settore presente nel portfolio.")
    cols_per_row = 4
    sector_shocks: dict[str, float] = {}
    sector_chunks = [
        sectors_in_portfolio[i:i + cols_per_row]
        for i in range(0, len(sectors_in_portfolio), cols_per_row)
    ]
    for chunk in sector_chunks:
        cols = st.columns(cols_per_row)
        for col, sec in zip(cols, chunk, strict=False):
            sector_shocks[sec] = col.number_input(
                f"{sec}",
                min_value=-50.0, max_value=20.0, value=0.0, step=1.0,
                key=f"sec_shock_{sec}",
            ) / 100

    shock_dict = {
        tk: sector_shocks.get(sector_map.get(tk) or "unknown", 0.0)
        for tk in positions
    }
    impact = _compute_impact(shock_dict)


# ---------------------------------------------------------------------------
# Mode 3: Preset scenarios
# ---------------------------------------------------------------------------
else:
    presets = {
        "COVID crash 2020 (Feb-Mar)": -0.34,           # SPX -34% in 33gg
        "GFC 2008 (full crisis)": -0.50,                # SPX -50% peak Sep07 → Mar09
        "Carry trade unwind Aug 2024": -0.10,           # SPX -10% in 5gg
        "Debt ceiling Aug 2011": -0.17,                 # SPX -17% in 30gg
        "Dotcom bust 2000-2002": -0.49,                 # SPX -49% peak Mar00 → Oct02
        "Black Monday 1987": -0.22,                     # SPX -22% in 1gg
        "Volmageddon Feb 2018": -0.10,                  # SPX -10% in 9gg
        "China devaluation Aug 2015": -0.11,            # SPX -11% in 6gg
    }
    scen = st.selectbox(
        "Scenario storico",
        options=list(presets.keys()),
        key="preset_scen",
    )
    shock_pct = presets[scen] * 100
    use_beta = st.checkbox(
        "Beta-weighted (consigliato per scenario reali)",
        value=True,
        key="preset_use_beta",
    )

    st.markdown(
        f"**Scenario**: SPX shock atteso **{shock_pct:+.1f}%**. "
        f"Source: drawdown peak-to-trough storico."
    )

    shock_dict = {}
    for tk in positions:
        beta = betas.get(tk)
        if use_beta:
            beta = 1.0 if beta is None else beta
            shock_dict[tk] = (shock_pct / 100) * beta
        else:
            shock_dict[tk] = shock_pct / 100

    impact = _compute_impact(shock_dict)


# ---------------------------------------------------------------------------
# Output: KPI + bucket breakdown + waterfall + table
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📉 Impact summary")

kc1, kc2, kc3, kc4 = st.columns(4)
kc1.metric(
    "Δ€ portfolio",
    fmt_eur(impact["total_delta_eur"]),
    delta=f"{impact['total_delta_pct']:+.2f}%",
    delta_color="inverse",
)
kc2.metric(
    "Δ€ Stock bucket",
    fmt_eur(impact["stock_delta_eur"]),
)
kc3.metric(
    "Δ€ ETF bucket",
    fmt_eur(impact["etf_delta_eur"]),
)
kc4.metric(
    "Cash post (invariato)",
    fmt_eur(cash),
    help="Cash è immune da equity shock — solo invested impattato.",
)

# Post-shock totals + reserve check
ptc1, ptc2, ptc3 = st.columns(3)
ptc1.metric(
    "Portfolio value post",
    fmt_eur(impact["post_total_with_cash"]),
    delta=f"{(impact['post_total_with_cash'] - total) / total * 100:+.2f}%" if total else "—",
    delta_color="inverse",
)
new_cash_pct = cash / impact["post_total_with_cash"] * 100 if impact["post_total_with_cash"] else 0
ptc2.metric(
    "Cash % post",
    f"{new_cash_pct:.1f}%",
    delta=f"{new_cash_pct - cash/total*100:+.1f}pp" if total else None,
    help=(
        f"Cash reserve target {MIN_CASH_RESERVE_PCT*100:.0f}%. "
        f"Sopra = OK, sotto = warning."
    ),
)
weekly_limit = total * MAX_LOSS_WEEKLY_PCT
weekly_breach = abs(impact["total_delta_eur"]) > weekly_limit
ptc3.metric(
    "Weekly loss cap",
    fmt_eur(weekly_limit),
    delta=(
        f"⚠ BREACH ({abs(impact['total_delta_eur']) / weekly_limit * 100:.0f}%)"
        if weekly_breach else "✅ entro cap"
    ),
    help=f"Cap settimanale {MAX_LOSS_WEEKLY_PCT*100:.0f}%. Se Δ < -cap → trade halt.",
)

if weekly_breach:
    st.error(
        f"⚠ **Stress > weekly loss cap**: shock simulato Δ {fmt_eur(impact['total_delta_eur'])} "
        f"supera cap settimanale {fmt_eur(weekly_limit)} ({MAX_LOSS_WEEKLY_PCT*100:.0f}% del portfolio). "
        "In scenario reale, regole portfolio bloccherebbero nuove entry."
    )

# ---------------------------------------------------------------------------
# Waterfall chart per-position
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🌊 Waterfall per-position (worst → best)")

import plotly.graph_objects as go

rows_chart = impact["rows"]
fig = go.Figure()

# Add bars: signed delta, color rosso/verde
fig.add_trace(go.Bar(
    x=[r["ticker"] for r in rows_chart],
    y=[r["delta_eur"] for r in rows_chart],
    marker=dict(
        color=["#dc2626" if r["delta_eur"] < 0 else "#16a34a" for r in rows_chart],
    ),
    text=[
        f"€{r['delta_eur']:+,.0f}<br>{r['shock_pct']:+.1f}%"
        for r in rows_chart
    ],
    textposition="outside",
    hovertemplate=(
        "<b>%{x}</b><br>"
        "Sector %{customdata[0]}<br>"
        "Bucket %{customdata[1]}<br>"
        "MV pre €%{customdata[2]:,.2f}<br>"
        "Shock %{customdata[3]:+.2f}%<br>"
        "Δ€ %{y:+,.2f}<br>"
        "MV post €%{customdata[4]:,.2f}<extra></extra>"
    ),
    customdata=[
        (r["sector"], r["bucket"], r["mv_pre"], r["shock_pct"], r["mv_post"])
        for r in rows_chart
    ],
))

fig.add_hline(y=0, line_color="#94a3b8", line_width=1)
fig.update_layout(
    title=dict(
        text=f"Δ€ per posizione · totale {fmt_eur(impact['total_delta_eur'])}",
        x=0.5, xanchor="center", font=dict(size=14),
    ),
    xaxis_title="", yaxis_title="Δ€",
    height=380, showlegend=False,
    margin=dict(l=20, r=20, t=50, b=20),
)
st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Detailed table
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Per-position detail")

table_rows = [
    {
        "Ticker": r["ticker"],
        "Bucket": r["bucket"],
        "Sector": r["sector"],
        "MV pre €": round(r["mv_pre"], 2),
        "Shock %": round(r["shock_pct"], 2),
        "Δ€": round(r["delta_eur"], 2),
        "MV post €": round(r["mv_post"], 2),
        "Δ%": round(r["delta_eur"] / r["mv_pre"] * 100, 2) if r["mv_pre"] else 0,
    }
    for r in rows_chart
]
st.dataframe(
    table_rows,
    width="stretch",
    hide_index=True,
    column_config={
        "MV pre €": st.column_config.NumberColumn(format="€ %.2f"),
        "Δ€": st.column_config.NumberColumn(format="€ %+.2f"),
        "MV post €": st.column_config.NumberColumn(format="€ %.2f"),
        "Shock %": st.column_config.NumberColumn(format="%+.2f%%"),
        "Δ%": st.column_config.NumberColumn(format="%+.2f%%"),
    },
)

st.caption(
    "**Limiti modello**: shock applicato istantaneo, no propagazione "
    "sector-correlation, no liquidity gap fill, no margin call simulation. "
    "Usa come **first-order approx**, non come backtest accurato."
)


# ---------------------------------------------------------------------------
# Drawdown forecast Monte Carlo (statistical complement to deterministic)
# ---------------------------------------------------------------------------
st.divider()
with st.expander("📊 Drawdown forecast — Monte Carlo bootstrap", expanded=False):
    st.caption(
        "Differenza dallo stress test sopra: questo è **statistical bootstrap** "
        "su returns history reali. Non scenario deterministico, ma distribuzione "
        "di possibili drawdown nei prossimi N giorni."
    )

    mc1, mc2, mc3 = st.columns(3)
    horizon_days = mc1.slider(
        "Horizon forecast (giorni)",
        min_value=5, max_value=90, value=30, step=5,
        key="mc_horizon",
    )
    n_paths = mc2.select_slider(
        "N paths Monte Carlo",
        options=[200, 500, 1000, 2000, 5000],
        value=1000,
        key="mc_paths",
    )
    history_period = mc3.selectbox(
        "Returns history",
        options=("3mo", "6mo", "1y", "2y"),
        index=2,
        key="mc_period",
    )

    if st.button("▶️ Run Monte Carlo", type="primary", key="mc_run"):
        from propicks.dashboard._shared import cached_returns

        with st.spinner(f"Bootstrap {n_paths} paths × {horizon_days} days…"):
            returns_df = cached_returns(tuple(tickers), history_period)

        if returns_df is None or returns_df.empty:
            st.error("Returns history non disponibile — impossibile bootstrap.")
        else:
            import numpy as np
            import pandas as pd

            # Compute portfolio weights mark-to-market
            weights = {}
            for tk, pos in positions.items():
                mv = _pos_market_value(tk, pos)
                weights[tk] = mv / total_market if total_market else 0

            # Portfolio daily returns history (weighted)
            common_tickers = [tk for tk in tickers if tk in returns_df.columns]
            if not common_tickers:
                st.error(f"Nessun ticker con returns disponibile (cercati: {tickers}).")
            else:
                rets = returns_df[common_tickers].dropna()
                if len(rets) < 30:
                    st.warning(
                        f"Returns history corta ({len(rets)} giorni). "
                        "Forecast indicativo only."
                    )
                w_arr = np.array([weights.get(tk, 0) for tk in common_tickers])
                portfolio_rets = (rets.values * w_arr).sum(axis=1)

                # Bootstrap N paths
                rng = np.random.default_rng(42)
                n_obs = len(portfolio_rets)
                if n_obs < 5:
                    st.error("Returns insufficienti per bootstrap.")
                else:
                    # Sample with replacement
                    paths = np.zeros((n_paths, horizon_days))
                    for i in range(n_paths):
                        sampled = rng.choice(portfolio_rets, size=horizon_days, replace=True)
                        paths[i] = sampled

                    # Compound to equity curve per path
                    equity_paths = np.cumprod(1 + paths, axis=1)  # shape (n_paths, horizon)

                    # Max drawdown per path
                    max_dd_per_path = np.zeros(n_paths)
                    for i in range(n_paths):
                        eq = np.concatenate([[1.0], equity_paths[i]])
                        peak = np.maximum.accumulate(eq)
                        dd = (eq - peak) / peak
                        max_dd_per_path[i] = dd.min()

                    # Final equity per path
                    final_eq = equity_paths[:, -1]
                    final_ret_pct = (final_eq - 1) * 100

                    # Stats
                    p5_dd = np.percentile(max_dd_per_path, 5) * 100
                    p50_dd = np.percentile(max_dd_per_path, 50) * 100
                    p95_dd = np.percentile(max_dd_per_path, 95) * 100

                    p5_ret = np.percentile(final_ret_pct, 5)
                    p50_ret = np.percentile(final_ret_pct, 50)
                    p95_ret = np.percentile(final_ret_pct, 95)

                    # Probabilità DD < soglia
                    p_dd_5 = (max_dd_per_path < -0.05).mean() * 100
                    p_dd_10 = (max_dd_per_path < -0.10).mean() * 100
                    p_dd_15 = (max_dd_per_path < -0.15).mean() * 100
                    p_dd_20 = (max_dd_per_path < -0.20).mean() * 100

                    st.markdown(f"##### 📉 Max drawdown forecast ({horizon_days}gg)")
                    dc1, dc2, dc3 = st.columns(3)
                    dc1.metric("5° percentile (worst)", f"{p5_dd:+.2f}%")
                    dc2.metric("Mediana", f"{p50_dd:+.2f}%")
                    dc3.metric("95° percentile (best)", f"{p95_dd:+.2f}%")

                    st.markdown(f"##### 📈 Final return forecast ({horizon_days}gg)")
                    rc1, rc2, rc3 = st.columns(3)
                    rc1.metric("5° percentile", f"{p5_ret:+.2f}%")
                    rc2.metric("Mediana", f"{p50_ret:+.2f}%")
                    rc3.metric("95° percentile", f"{p95_ret:+.2f}%")

                    st.markdown("##### 🚨 Probability of DD breach")
                    pdc = st.columns(4)
                    pdc[0].metric("P(DD < -5%)", f"{p_dd_5:.1f}%")
                    pdc[1].metric("P(DD < -10%)", f"{p_dd_10:.1f}%")
                    pdc[2].metric("P(DD < -15%)", f"{p_dd_15:.1f}%")
                    pdc[3].metric("P(DD < -20%)", f"{p_dd_20:.1f}%")

                    # Histogram drawdown distribution
                    fig_dd_hist = go.Figure()
                    fig_dd_hist.add_trace(go.Histogram(
                        x=max_dd_per_path * 100,
                        nbinsx=40,
                        marker=dict(color="#dc2626", line=dict(color="white", width=1)),
                    ))
                    fig_dd_hist.add_vline(
                        x=p5_dd, line_dash="dash", line_color="#7f1d1d",
                        annotation_text=f"P5 {p5_dd:.1f}%", annotation_position="top",
                    )
                    fig_dd_hist.add_vline(
                        x=p50_dd, line_dash="dot", line_color="#3b82f6",
                        annotation_text=f"P50 {p50_dd:.1f}%", annotation_position="top",
                    )
                    fig_dd_hist.update_layout(
                        title=dict(
                            text=f"Distribuzione max drawdown · {n_paths} paths · "
                                 f"{horizon_days}gg horizon",
                            x=0.5, xanchor="center", font=dict(size=13),
                        ),
                        xaxis_title="Max DD %", yaxis_title="N paths",
                        height=320, showlegend=False,
                        margin=dict(l=20, r=20, t=50, b=20),
                    )
                    st.plotly_chart(fig_dd_hist, width="stretch")

                    # Sample equity paths chart
                    sample_paths = paths[:30]  # show 30 sample
                    sample_eq = np.cumprod(1 + sample_paths, axis=1)
                    fig_paths = go.Figure()
                    for i in range(min(30, len(sample_eq))):
                        fig_paths.add_trace(go.Scatter(
                            x=list(range(1, horizon_days + 1)),
                            y=sample_eq[i],
                            mode="lines",
                            line=dict(color="rgba(59,130,246,0.3)", width=1),
                            showlegend=False,
                            hovertemplate=None,
                        ))
                    # Add P5/P50/P95 envelope per day
                    p5_path = np.percentile(sample_eq, 5, axis=0) if len(sample_eq) > 0 else None
                    p95_path = np.percentile(sample_eq, 95, axis=0) if len(sample_eq) > 0 else None
                    median_path = np.percentile(equity_paths, 50, axis=0)
                    fig_paths.add_trace(go.Scatter(
                        x=list(range(1, horizon_days + 1)),
                        y=median_path, mode="lines",
                        line=dict(color="#3b82f6", width=2),
                        name="Median path",
                    ))
                    fig_paths.add_hline(
                        y=1.0, line_dash="dot", line_color="#94a3b8",
                        annotation_text="break-even",
                    )
                    fig_paths.update_layout(
                        title=dict(
                            text="Sample paths (30) + median",
                            x=0.5, xanchor="center", font=dict(size=13),
                        ),
                        xaxis_title="Days forward", yaxis_title="Growth of 1",
                        height=320,
                        margin=dict(l=20, r=20, t=50, b=20),
                    )
                    st.plotly_chart(fig_paths, width="stretch")

                    st.caption(
                        f"**Lettura**: con probabilità ~{p_dd_10:.0f}% il portfolio "
                        f"farà drawdown peggiore del -10% nei prossimi {horizon_days}gg "
                        f"(secondo bootstrap su returns storici {history_period}). "
                        f"**Limiti**: assume returns IID stationari (no regime change), "
                        f"no fat tails extra rispetto a quanto già osservato."
                    )
