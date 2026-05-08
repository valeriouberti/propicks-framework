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
    "Usa come **first-order approx**, non come backtest accurato. Per bootstrap "
    "statistical (CI 95% drawdown), usa Page 8 → Risk → VaR/Expected Shortfall."
)
