"""Threshold Calibration (Fase A.2 SIGNAL_ROADMAP) — UI per propicks-calibrate.

Threshold sweep + Probabilistic Sharpe Ratio (PSR, Bailey-Lopez 2012) +
Deflated Sharpe Ratio (DSR, Bailey-Lopez 2014) + Combinatorial Purged CV
(Lopez de Prado 2018). Vedi docs/THRESHOLD_CALIBRATION.md.
"""
# ruff: noqa: E402

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import invariants_note, page_header

st.set_page_config(page_title="Threshold Calibration · Propicks", layout="wide")
page_header(
    "Threshold Calibration (Fase A.2)",
    "Sweep su range threshold + DSR multi-trial + recommendation rule-based. "
    "Mirror di `propicks-calibrate`.",
)
invariants_note()

st.info(
    "💡 **Cosa fa**: per ogni threshold testa il backtest, calcola **PSR** "
    "(P(true Sharpe > 0)) e **DSR** (deflated by N trials testati). Output "
    "tabella + raccomandazione threshold ottimo. **Non modifica config** — "
    "informativo only.",
    icon="ℹ️",
)


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("calibrate_form", border=True):
    col1, col2 = st.columns([3, 2])
    tickers_raw = col1.text_input(
        "Universe (tickers separati da spazio/virgola)",
        placeholder="AAPL MSFT NVDA GOOGL META AMZN",
        help="Lascia vuoto + spunta 'Discover SP500' per universe automatico",
    )
    period = col2.selectbox(
        "Periodo", options=["1y", "2y", "3y", "5y", "10y", "max"], index=3,
    )

    col3, col4, col5 = st.columns(3)
    discover_sp500 = col3.checkbox("Discover SP500 (top N)", value=False)
    top_n = col4.number_input("Top N (se discover)", min_value=10, max_value=500, value=30, step=5)
    initial_capital = col5.number_input(
        "Capitale iniziale", min_value=1000.0, value=10_000.0, step=1000.0,
    )

    thresholds_spec = st.text_input(
        "Threshold spec (range `start:end:step` o lista `60,65,70`)",
        value="60:80:5",
        help="Default 60-80 step 5. Range stretto = recommendation più affidabile",
    )

    col6, col7 = st.columns(2)
    use_cpcv = col6.checkbox(
        "🧪 Combinatorial Purged CV (Lopez de Prado)",
        value=False,
        help="Più rigoroso ma ~10x più lento. Genera comb(N,k) test path.",
    )
    use_membership = col7.checkbox(
        "🛡️ Membership filter sp500 (Fase A.1)",
        value=True,
        help="Survivorship-correct universe via index_membership_history",
    )

    if use_cpcv:
        col8, col9, col10 = st.columns(3)
        cpcv_groups = col8.number_input("CPCV groups", min_value=3, max_value=10, value=6)
        cpcv_test_groups = col9.number_input("CPCV test groups", min_value=1, max_value=5, value=2)
        cpcv_embargo = col10.number_input("CPCV embargo days", min_value=0, max_value=30, value=5)
    else:
        cpcv_groups, cpcv_test_groups, cpcv_embargo = 6, 2, 5

    col11, col12 = st.columns(2)
    target_dsr = col11.number_input(
        "Target DSR (recommendation tier 1)",
        min_value=0.5, max_value=0.99, value=0.95, step=0.01,
    )
    min_trades = col12.number_input(
        "Min trades per recommendation", min_value=10, max_value=500, value=30, step=10,
    )

    submitted = st.form_submit_button("▶️ Esegui calibration", type="primary")


if not submitted:
    st.caption(
        "_Premi 'Esegui calibration' per avviare. Sweep singolo (no CPCV) "
        "≈ 30-60s su 5-10 ticker. Con CPCV ≈ 5-10 min._"
    )
    st.stop()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
import pandas as pd
import yfinance as yf

from propicks.backtest.calibration import calibrate_threshold, format_calibration_report
from propicks.backtest.portfolio_engine import BacktestConfig
from propicks.cli.calibrate import _build_momentum_scoring_fn, _parse_thresholds


# Resolve tickers
tickers: list[str] = []
if discover_sp500:
    from propicks.market.index_constituents import get_sp500_universe
    tickers = get_sp500_universe()[: int(top_n)]
    st.caption(f"📥 Discover SP500: top {top_n} ticker")
else:
    tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]

if not tickers:
    st.error("Specifica tickers o spunta 'Discover SP500'.")
    st.stop()

# Parse thresholds
try:
    thresholds = _parse_thresholds(thresholds_spec)
except ValueError as exc:
    st.error(f"Threshold spec invalida: {exc}")
    st.stop()

st.write(f"**Thresholds**: {thresholds} ({len(thresholds)} valori)")


# Membership provider
provider = None
if use_membership:
    from propicks.io.index_membership import (
        build_universe_provider, count_membership_rows,
    )
    n_rows = count_membership_rows("sp500")
    if n_rows == 0:
        st.error(
            "Membership history non importata. "
            "Esegui: `python scripts/import_sp500_history.py`"
        )
        st.stop()
    provider = build_universe_provider("sp500")
    st.caption(f"🛡️ Membership filter sp500 attivo ({n_rows:,} row)")


# Fetch yfinance diretto (cache framework copre solo 1y)
with st.status(f"Fetching {len(tickers)} ticker ({period})…", expanded=True) as status:
    universe: dict = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=period, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
                st.write(f"✓ {t}: {len(df)} bars")
            else:
                st.write(f"✗ {t}: insufficient ({len(df)} bars)")
        except Exception as exc:
            st.write(f"✗ {t}: {exc}")
    if not universe:
        status.update(label="❌ Universe vuoto", state="error")
        st.stop()
    status.update(label=f"Fetch completato: {len(universe)} ticker", state="complete")


scoring_fn = _build_momentum_scoring_fn()
base_config = BacktestConfig(
    initial_capital=initial_capital,
    score_threshold=thresholds[0],  # placeholder; sweep override
    use_earnings_gate=False,
    strategy_tag="momentum",
)

progress_bar = st.progress(0, text="Starting calibration…")


def _cb(curr, total, thr):
    progress_bar.progress(curr / total, text=f"[{curr}/{total}] threshold={thr:.1f}")


with st.spinner("Running threshold sweep…"):
    result = calibrate_threshold(
        universe=universe,
        scoring_fn=scoring_fn,
        thresholds=thresholds,
        base_config=base_config,
        universe_provider=provider,
        use_cpcv=use_cpcv,
        cpcv_n_groups=int(cpcv_groups),
        cpcv_n_test_groups=int(cpcv_test_groups),
        cpcv_embargo_days=int(cpcv_embargo),
        min_trades=int(min_trades),
        target_dsr=target_dsr,
        progress_cb=_cb,
    )

progress_bar.progress(1.0, text="Done")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📊 Risultati")

# Metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Universe", result.universe_size)
c2.metric("Thresholds tested", result.n_thresholds_tested)
c3.metric("CPCV", "ON" if result.cpcv_enabled else "OFF")
c4.metric("Var(SR) cross", f"{result.var_sr_across_thresholds:.4f}")

# Tabella
import pandas as pd
rows = []
for r in result.results:
    is_recommended = r.threshold == result.recommended_threshold
    rows.append({
        "★": "★" if is_recommended else "",
        "Threshold": r.threshold,
        "N trades": r.n_trades,
        "Sharpe ann": r.sharpe_annualized,
        "Sharpe/trade": r.sharpe_per_trade,
        "Win %": f"{r.win_rate * 100:.1f}",
        "Tot ret %": r.total_return_pct,
        "Max DD %": r.max_drawdown_pct,
        "PSR": r.psr,
        "DSR": r.dsr,
    })
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)


# Recommendation
if result.recommended_threshold is not None:
    st.success(
        f"⭐ **Recommended threshold: {result.recommended_threshold:.1f}** — "
        f"{result.recommendation_reason}"
    )
else:
    st.warning(f"⚠ Nessuna recommendation: {result.recommendation_reason}")

# Note
st.divider()
st.caption(
    "**Legenda**: PSR > 0.95 = 95% confidence Sharpe vero > 0. "
    "DSR > 0.95 = robust a multiple testing post-correzione. "
    "DSR sempre ≤ PSR. Vedi docs/THRESHOLD_CALIBRATION.md per metodologia."
)
