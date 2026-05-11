"""Allocazione consolidata — vista unificata core + satellite.

Tre dimensioni:

1. **Core breakdown** — asset class / region / sector (donut + tabella).
2. **Drift core vs target_weight** — alert quando |actual − target| > 5%,
   suggerimento rebalance EUR signed (+ compra, − vendi).
3. **Sector exposure consolidato (core + satellite)** — somma core con
   sector_key esplicito + satellite per settore vs capitale totale.
   Overlap warnings se settore > 35% (config.CORE_OVERLAP_SECTOR_WARN_PCT).

Nota: i broad ETF core (sector_key=None, es. VWCE) NON contribuiscono al
sector consolidato — richiedono look-through holdings (TODO v2).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import (
    ASSET_CLASS_LABELS,
    CORE_DRIFT_REBALANCE_THRESHOLD_PCT,
    CORE_OVERLAP_SECTOR_WARN_PCT,
    REGION_LABELS,
)
from propicks.dashboard._shared import (
    cached_core_prices,
    cached_current_prices,
    cached_ticker_sectors,
    fmt_eur,
    fmt_pct,
    load_core,
    load_portfolio,
    page_header,
)
from propicks.domain import core_allocation as ca
from propicks.io import core_store


st.set_page_config(page_title="Allocation Consolidata · Propicks", layout="wide")
page_header(
    "Allocation Consolidata (core + satellite)",
    "Vista unificata: asset class · region · sector breakdown del core, drift "
    "vs target, overlap sector consolidato con satellite.",
)

# ---------------------------------------------------------------------------
# Load state
# ---------------------------------------------------------------------------
holdings = load_core()
portfolio = load_portfolio()
sat_positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)

if not holdings and not sat_positions:
    st.info(
        "Nessuna posizione (core o satellite). Aggiungi una holding dal "
        "page **Core Portfolio** o un trade dal page **Portfolio**."
    )
    st.stop()

# Prezzi: prefer cached_current_prices per condividere TTL con altre page
all_tickers = tuple(sorted(set(list(holdings.keys()) + list(sat_positions.keys()))))
prices = cached_current_prices(all_tickers) if all_tickers else {}

# Core values
core_values = ca.compute_holding_values(holdings, prices) if holdings else {}
core_total = ca.total_core_value(core_values)

# Satellite market value (EUR)
sat_currency_map: dict[str, str] = {
    t: pos.get("currency", "EUR") for t, pos in sat_positions.items()
}
sat_market_value = 0.0
for t, pos in sat_positions.items():
    px = prices.get(t)
    if px is None:
        continue
    mv = pos["shares"] * px
    sat_market_value += ca._mv_to_eur(mv, sat_currency_map.get(t))

total_capital = core_total + sat_market_value + cash


# ---------------------------------------------------------------------------
# Top KPIs
# ---------------------------------------------------------------------------
contributed = core_store.total_contributed() if holdings else 0.0
core_pnl = core_total - contributed
core_pnl_pct = (core_pnl / contributed) if contributed > 0 else 0.0
core_share = (core_total / total_capital) if total_capital > 0 else 0.0
sat_share = (sat_market_value / total_capital) if total_capital > 0 else 0.0
cash_share = (cash / total_capital) if total_capital > 0 else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Capitale totale", fmt_eur(total_capital, decimals=0))
c2.metric("Core", fmt_eur(core_total, decimals=0), fmt_pct(core_share))
c3.metric("Satellite", fmt_eur(sat_market_value, decimals=0), fmt_pct(sat_share))
c4.metric("Cash", fmt_eur(cash, decimals=0), fmt_pct(cash_share))
c5.metric("Core P&L", fmt_eur(core_pnl, decimals=0), fmt_pct(core_pnl_pct))

st.divider()


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_breakdown, tab_drift, tab_consolidated = st.tabs([
    "Core breakdown",
    "Drift vs target",
    "Sector consolidato (overlap)",
])


def _donut(data: dict[str, float], title: str, label_map: dict | None = None) -> go.Figure:
    """Piccolo helper per donut chart Plotly."""
    if not data:
        return go.Figure().update_layout(
            title=title, annotations=[dict(text="(vuoto)", showarrow=False)],
        )
    labels = [label_map.get(k, k) if label_map else k for k in data.keys()]
    values = [v * 100 for v in data.values()]
    fig = go.Figure(data=[go.Pie(
        labels=labels, values=values, hole=0.55,
        textinfo="label+percent", textposition="outside",
    )])
    fig.update_layout(
        title=title,
        showlegend=True,
        height=380,
        margin=dict(t=50, b=10, l=10, r=10),
    )
    return fig


# ---------------------------------------------------------------------------
# Tab: Core breakdown
# ---------------------------------------------------------------------------
with tab_breakdown:
    if not holdings:
        st.info("Core portfolio vuoto.")
    else:
        asset_class_bd = ca.compute_asset_class_breakdown(
            holdings, core_values, core_total,
        )
        region_bd = ca.compute_region_breakdown(holdings, core_values, core_total)
        sector_bd = ca.compute_core_sector_breakdown(
            holdings, core_values, core_total,
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.plotly_chart(
                _donut(asset_class_bd, "Asset class", ASSET_CLASS_LABELS),
                width="stretch",
            )
        with c2:
            st.plotly_chart(
                _donut(region_bd, "Region", REGION_LABELS),
                width="stretch",
            )
        with c3:
            st.plotly_chart(
                _donut(sector_bd, "Sector (broad = ETF diversificato)"),
                width="stretch",
            )

        st.divider()
        # Tabella unificata
        rows_ac = [
            {"Bucket": ASSET_CLASS_LABELS.get(k, k), "Type": "Asset class", "Weight": v}
            for k, v in sorted(asset_class_bd.items(), key=lambda x: x[1], reverse=True)
        ]
        rows_rg = [
            {"Bucket": REGION_LABELS.get(k, k), "Type": "Region", "Weight": v}
            for k, v in sorted(region_bd.items(), key=lambda x: x[1], reverse=True)
        ]
        rows_se = [
            {"Bucket": k, "Type": "Sector", "Weight": v}
            for k, v in sorted(sector_bd.items(), key=lambda x: x[1], reverse=True)
        ]
        df_bd = pd.DataFrame(rows_ac + rows_rg + rows_se)
        if not df_bd.empty:
            df_bd["Weight"] = df_bd["Weight"].apply(lambda v: fmt_pct(v))
        st.dataframe(df_bd, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Tab: Drift
# ---------------------------------------------------------------------------
with tab_drift:
    if not holdings:
        st.info("Core portfolio vuoto.")
    else:
        threshold = st.slider(
            "Soglia drift % per flag rebalance",
            min_value=1.0, max_value=20.0,
            value=CORE_DRIFT_REBALANCE_THRESHOLD_PCT * 100,
            step=0.5,
        ) / 100.0

        drift = ca.compute_drift(
            holdings, core_values, core_total,
            rebalance_threshold=threshold,
        )
        if not drift:
            st.info(
                "Nessuna holding con `target_weight` definito. "
                "Vai a page **Core Portfolio** → tab *Aggiorna meta* per "
                "impostare i target."
            )
        else:
            rows = []
            flagged = 0
            for t, d in sorted(drift.items()):
                if d["needs_rebalance"]:
                    flagged += 1
                rows.append({
                    "Ticker": t,
                    "Actual": fmt_pct(d["actual_weight"]),
                    "Target": fmt_pct(d["target_weight"]),
                    "Drift": fmt_pct(d["drift"], decimals=2),
                    "Rebal EUR": fmt_eur(d["rebalance_eur"], decimals=0),
                    "Azione": (
                        "🟢 OK" if not d["needs_rebalance"]
                        else ("🔴 VENDI" if d["drift"] > 0 else "🟢 COMPRA")
                    ),
                })
            df_drift = pd.DataFrame(rows)
            st.dataframe(df_drift, width="stretch", hide_index=True)

            st.caption(
                f"Core total EUR {fmt_eur(core_total, decimals=0)}. "
                f"{flagged}/{len(drift)} holding sopra soglia drift "
                f"{threshold * 100:.1f}%. "
                f"Rebal EUR positivo = compra al prossimo PAC, negativo = vendi."
            )

            # Bar chart drift signed
            fig = go.Figure()
            tickers_sorted = sorted(drift.keys())
            drift_vals = [drift[t]["drift"] * 100 for t in tickers_sorted]
            colors = [
                "#dc2626" if abs(drift[t]["drift"]) > threshold
                else "#16a34a" for t in tickers_sorted
            ]
            fig.add_trace(go.Bar(
                x=tickers_sorted, y=drift_vals, marker_color=colors,
                text=[f"{v:+.1f}%" for v in drift_vals],
                textposition="outside",
            ))
            fig.add_hline(y=threshold * 100, line_dash="dash", line_color="#dc2626")
            fig.add_hline(y=-threshold * 100, line_dash="dash", line_color="#dc2626")
            fig.update_layout(
                title="Drift % (actual − target) — rosso oltre soglia",
                yaxis_title="Drift %",
                height=380,
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch")


# ---------------------------------------------------------------------------
# Tab: Sector consolidato + overlap
# ---------------------------------------------------------------------------
with tab_consolidated:
    st.markdown(
        "Sector exposure consolidato = core (con `sector_key` esplicito) + "
        "satellite, denominatore = capitale totale (core + satellite + cash). "
        "I broad ETF core (VWCE/IWDA/...) NON sono inclusi — richiedono "
        "look-through holdings (TODO v2)."
    )

    threshold = st.slider(
        "Soglia warn overlap %",
        min_value=10.0, max_value=60.0,
        value=CORE_OVERLAP_SECTOR_WARN_PCT * 100,
        step=1.0,
        key="overlap_threshold",
    ) / 100.0

    # Satellite sector map (via yfinance cache)
    sat_tickers = tuple(sorted(sat_positions.keys()))
    sat_sectors = cached_ticker_sectors(sat_tickers) if sat_tickers else {}

    consolidated = ca.compute_consolidated_sector_exposure(
        core_holdings=holdings,
        core_values=core_values,
        satellite_positions=sat_positions,
        satellite_prices={t: prices[t] for t in sat_positions if t in prices},
        satellite_sector_map=sat_sectors,
        total_capital_eur=total_capital,
        satellite_currency_map=sat_currency_map,
    )
    warns = ca.detect_overlap_warnings(consolidated, warn_threshold=threshold)

    if not consolidated:
        st.info(
            "Nessuna esposizione settoriale tracciabile. "
            "Possibili cause: core è 100% broad ETF (sector_key=None) e "
            "satellite è vuoto, oppure i prezzi correnti non sono disponibili."
        )
    else:
        # Tabella
        rows = [
            {
                "Sector": k,
                "% Capitale": fmt_pct(v),
                "EUR": fmt_eur(v * total_capital, decimals=0),
                "Over threshold": "🔴 sì" if v > threshold else "✓",
            }
            for k, v in sorted(consolidated.items(), key=lambda x: x[1], reverse=True)
        ]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # Bar chart
        sectors_sorted = sorted(consolidated.keys(), key=lambda k: consolidated[k], reverse=True)
        bar_colors = [
            "#dc2626" if consolidated[s] > threshold else "#65a30d"
            for s in sectors_sorted
        ]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=sectors_sorted,
            y=[consolidated[s] * 100 for s in sectors_sorted],
            marker_color=bar_colors,
            text=[f"{consolidated[s] * 100:.1f}%" for s in sectors_sorted],
            textposition="outside",
        ))
        fig.add_hline(y=threshold * 100, line_dash="dash", line_color="#dc2626")
        fig.update_layout(
            title=f"Sector exposure consolidato — soglia {threshold * 100:.0f}%",
            yaxis_title="% capitale",
            height=400,
            showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")

        if warns:
            st.error(
                f"⚠️ **{len(warns)} settori sopra soglia {threshold * 100:.0f}%**:"
            )
            for w in warns:
                st.markdown(
                    f"- **{w['sector']}**: {w['pct'] * 100:.1f}% capitale "
                    f"(over by {w['over_by'] * 100:+.1f}%)"
                )
            st.caption(
                "Azione: riduci esposizione satellite sul settore overlappato, "
                "oppure passa parte del core su un broad ETF (sector_key=None)."
            )
        else:
            st.success(f"✓ Nessun overlap sopra soglia {threshold * 100:.0f}%.")
