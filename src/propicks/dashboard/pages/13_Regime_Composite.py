"""Regime Daily Composite (Fase B.3 SIGNAL_ROADMAP).

Visualizza composite z-score (HY OAS + breadth + VIX) + classificazione
5-bucket. Read-only diagnostic. Fetch FRED + breadth interno + plot Plotly.
Vedi docs/REGIME_COMPOSITE.md.
"""
# ruff: noqa: E402

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import page_header

st.set_page_config(page_title="Regime Composite · Propicks", layout="wide")
page_header(
    "Regime Daily Composite (Fase B.3)",
    "Z-score giornaliero combinato — HY OAS + breadth interno + VIX. "
    "Lead time turning point 1-3 settimane (vedi findings).",
)

st.info(
    "💡 **Cosa**: HY OAS (FRED) + breadth (% top S&P > MA200) + VIX → z-score "
    "rolling 252d → 5-bucket regime. **Anticipa weekly classifier** su turning "
    "point critici (COVID 2020, top 2022, CPI bottom Oct-2022). Read-only.",
    icon="ℹ️",
)

st.success(
    "✅ **Compatibile con tutte le strategie.** Il regime composite è un "
    "indicator macro globale (S&P 500 + credit + vol), non strategy-specific. "
    "Va usato come complemento al regime weekly: se il composite daily flippa "
    "PRIMA del weekly classifier, è early-warning per tutte le strategie "
    "(momentum, contrarian, ETF rotation, thematic).",
    icon="✅",
)

with st.expander("📖 Come si legge — in 5 righe", expanded=False):
    st.markdown(
        """
**Cosa misura**:
1. **HY OAS** (high-yield credit spread, FRED `BAMLH0A0HYM2`) — quando il
   credit spread allarga = stress sistemico, non c'è cushion per ciclico.
2. **Breadth** (% top-N S&P sopra MA200) — quanti nomi partecipano al trend.
   Sotto 40% = mercato narrow, distribution risk.
3. **VIX** (FRED `VIXCLS`) — paura option-implied. >25 = capitulation possibile.

I 3 segnali vengono z-scorati su 252 giorni rolling, pesati e sommati →
composite z. Soglie:

- z ≤ -1.5 → **STRONG_BULL** (credit tight, breadth alta, vix basso)
- z ≤ -0.5 → **BULL**
- |z| < 0.5 → **NEUTRAL**
- z ≥ 0.5 → **BEAR**
- z ≥ 1.5 → **STRONG_BEAR** (credit blow-out, breadth crash, vix spike)

**Quando entra in gioco**:
- Composite daily passa a BEAR mentre weekly è ancora NEUTRAL → **alert
  early**. Considera de-risking (no nuove entry momentum, scale-in più
  cauto su contrarian).
- Composite NEUTRAL mentre weekly STRONG_BULL → distribution stage,
  rotation possibile.

**Read-only**: NON cambia automaticamente il gate `regime_code` usato dalle
strategie (resta su classifier weekly). È un'overlay diagnostica per il
trader.
"""
    )

with st.expander("🎛️ Parametri", expanded=False):
    st.markdown(
        """
| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Start / End date** | Finestra storica analizzata | Default 1+ anno. Per stress test storico → COVID 2020-03 |
| **Breadth universe (top N S&P)** | Quanti ticker per calcolo `% above MA200` | 30 default = veloce. 100 = più rappresentativo, fetch ~1min |
| **Weight HY OAS / breadth / VIX** | Pesi composite (somma libera, internamente normalizzati) | Default 0.40 / 0.40 / 0.20. Durante crisi credit (2008, 2020-03) HY è dominante → alza HY a 0.50. In risk-off azionario puro (2022) → breadth+vix dominanti |

Output: serie temporale composite z + 5-bucket regime + tabella turning
points + plot Plotly. Vedi `docs/REGIME_COMPOSITE.md` per derivazione
e validazione storica.
"""
    )


# ---------------------------------------------------------------------------
# Form params
# ---------------------------------------------------------------------------
with st.form("regime_form", border=True):
    col1, col2, col3 = st.columns(3)
    start_date = col1.text_input("Start date (YYYY-MM-DD)", value="2024-01-01")
    end_date = col2.text_input("End date (YYYY-MM-DD)", value="")
    top_n_breadth = col3.number_input(
        "Breadth universe (top N S&P)", min_value=10, max_value=200, value=30, step=10,
        help="Più ticker = più rappresentativo ma fetch più lento",
    )

    col4, col5, col6 = st.columns(3)
    w_hy = col4.slider("Weight HY OAS", 0.0, 1.0, 0.40, 0.05)
    w_br = col5.slider("Weight breadth", 0.0, 1.0, 0.40, 0.05)
    w_vix = col6.slider("Weight VIX", 0.0, 1.0, 0.20, 0.05)

    submitted = st.form_submit_button("▶️ Compute regime", type="primary")


if not submitted:
    st.caption("_Premi 'Compute regime' per fetch + calcolo (30-60s)._")
    st.stop()


# ---------------------------------------------------------------------------
# Fetch + compute
# ---------------------------------------------------------------------------
import pandas as pd
import yfinance as yf

from propicks.domain.breadth import breadth_series
from propicks.domain.regime_composite import compute_regime_series
from propicks.market.fred_client import fetch_fred_series
from propicks.market.index_constituents import get_sp500_universe

end_eff = end_date.strip() or pd.Timestamp.today().strftime("%Y-%m-%d")

with st.status(f"Fetching {start_date} → {end_eff}…", expanded=True) as status:
    st.write("📥 FRED HY OAS (BAMLH0A0HYM2)…")
    hy_d = fetch_fred_series("BAMLH0A0HYM2", start=start_date, end=end_eff)
    hy = pd.Series(hy_d, dtype=float)
    hy.index = pd.to_datetime(hy.index)
    st.write(f"  {len(hy)} obs")

    st.write("📥 FRED VIX (VIXCLS)…")
    vix_d = fetch_fred_series("VIXCLS", start=start_date, end=end_eff)
    vix = pd.Series(vix_d, dtype=float)
    vix.index = pd.to_datetime(vix.index)
    st.write(f"  {len(vix)} obs")

    st.write(f"📥 yfinance {int(top_n_breadth)} ticker S&P (breadth)…")
    tickers = get_sp500_universe()[: int(top_n_breadth)]
    universe = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=start_date, end=end_eff, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
        except Exception:
            pass
    st.write(f"  {len(universe)} ticker fetched")

    st.write("⚙️ Compute breadth…")
    breadth = breadth_series(universe, window=200)
    st.write(f"  {len(breadth)} obs, range [{breadth.min():.1f}, {breadth.max():.1f}]")

    st.write("⚙️ Compute regime composite z-score…")
    result = compute_regime_series(
        hy_oas=hy, breadth=breadth, vix=vix,
        zscore_window=252, weights=(w_hy, w_br, w_vix),
    )
    status.update(label="✓ Done", state="complete")


if result.empty:
    st.error("No data — check date range")
    st.stop()


# ---------------------------------------------------------------------------
# Latest reading
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📍 Latest reading")

latest = result.dropna(subset=["composite_z"]).iloc[-1] if not result["composite_z"].dropna().empty else None
if latest is not None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Date", str(latest.name.date()))
    c2.metric(
        "Composite z",
        f"{latest['composite_z']:.3f}",
        delta=f"Code {int(latest['regime_code'])}",
    )
    c3.metric("Regime", latest["regime_label"])
    c4.metric("z HY OAS (inv)", f"{latest['z_hy_oas']:.3f}" if pd.notna(latest['z_hy_oas']) else "—")
    c5.metric("z breadth", f"{latest['z_breadth']:.3f}" if pd.notna(latest['z_breadth']) else "—")


# ---------------------------------------------------------------------------
# Plot composite z-score + bucket
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📈 Composite z-score history")

import plotly.graph_objects as go

valid = result.dropna(subset=["composite_z"])
fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=valid.index, y=valid["composite_z"],
        mode="lines", name="composite_z",
        line=dict(color="#1f77b4", width=1.5),
    )
)
# Boundary bands
for thr, color, label in [
    (1.0, "#2ca02c", "STRONG_BULL"),
    (0.3, "#98df8a", "BULL"),
    (-0.3, "#ffbb78", "BEAR"),
    (-1.0, "#d62728", "STRONG_BEAR"),
]:
    fig.add_hline(y=thr, line_dash="dot", line_color=color, opacity=0.4,
                  annotation_text=label, annotation_position="right")

fig.update_layout(
    height=420, hovermode="x unified",
    xaxis_title="Date", yaxis_title="Composite z-score",
    margin=dict(l=20, r=20, t=20, b=20),
)
st.plotly_chart(fig, width="stretch")


# Distribution
col_a, col_b = st.columns(2)
with col_a:
    st.subheader("📊 Regime distribution")
    code_counts = valid["regime_code"].value_counts().sort_index()
    label_map = {1: "STRONG_BEAR", 2: "BEAR", 3: "NEUTRAL", 4: "BULL", 5: "STRONG_BULL"}
    df_dist = pd.DataFrame({
        "regime": [label_map.get(int(c), str(c)) for c in code_counts.index],
        "n_days": code_counts.values,
        "pct": (code_counts.values / code_counts.sum() * 100).round(1),
    })
    st.dataframe(df_dist, width="stretch", hide_index=True)

with col_b:
    st.subheader("🔍 Sub-features z-scores")
    z_view = valid[["z_hy_oas", "z_breadth", "z_vix"]].tail(60)
    fig2 = go.Figure()
    for col, color in [
        ("z_hy_oas", "#9467bd"), ("z_breadth", "#1f77b4"), ("z_vix", "#ff7f0e"),
    ]:
        fig2.add_trace(go.Scatter(x=z_view.index, y=z_view[col], mode="lines", name=col, line=dict(color=color, width=1.2)))
    fig2.update_layout(height=300, hovermode="x unified", margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig2, width="stretch")


# Caveat
st.divider()
st.caption(
    "**Caveat**: FRED default ~2y range — pre-2024 composite usa solo breadth. "
    "Pesi 40/40/20 default arbitrari (Faber-style) — tuning rigoroso pendente "
    "B.6 ablation. Universe top N ≠ full S&P 500. "
    "Vedi docs/REGIME_COMPOSITE.md."
)
