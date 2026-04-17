"""Home / Overview — stato aggregato del portfolio.

Streamlit multi-page app: ogni file in ``pages/`` diventa una voce di menu.
Questa è la pagina di default (sidebar top entry).
"""

from __future__ import annotations

import streamlit as st

from propicks.config import MAX_POSITIONS, MIN_CASH_RESERVE_PCT
from propicks.dashboard._shared import (
    cached_current_prices,
    fmt_eur,
    fmt_pct,
    invariants_note,
    kpi_row,
    load_journal,
    load_portfolio,
    page_header,
    regime_badge,
    score_badge,
)
from propicks.domain.sizing import portfolio_value

st.set_page_config(
    page_title="Propicks Dashboard",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)


def _current_regime() -> dict | None:
    """Lookup regime weekly via il classifier. Cache-backed dentro rank_universe
    non è riusabile qui (serve solo il regime, non scan settori). Chiamata
    diretta ma cached a parte."""

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch() -> dict | None:
        from propicks.config import ETF_BENCHMARK
        from propicks.domain.regime import classify_regime
        from propicks.market.yfinance_client import (
            DataUnavailable,
            download_weekly_history,
        )
        try:
            weekly = download_weekly_history(ETF_BENCHMARK)
            return classify_regime(weekly)
        except DataUnavailable:
            return None

    return _fetch()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
page_header(
    "Portfolio Overview",
    "Stato aggregato — posizioni aperte, P&L unrealized, regime macro e invariants.",
)
invariants_note()

portfolio = load_portfolio()
positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)
total = portfolio_value(portfolio)

regime = _current_regime()
st.markdown("**Regime macro weekly:** " + regime_badge(regime), unsafe_allow_html=True)
if regime is not None:
    st.caption(
        f"Classifier su ^GSPC — "
        f"close {regime.get('close', 0):.2f} · "
        f"EMA30w {regime.get('ema_slow', 0):.2f} · "
        f"RSI(w) {regime.get('rsi_weekly', 0):.1f} · "
        f"entry long {'allowed' if regime.get('entry_allowed') else 'NOT allowed'}"
    )
st.divider()

# ---------------------------------------------------------------------------
# Mark-to-market
# ---------------------------------------------------------------------------
prices: dict[str, float] = {}
unrealized = 0.0
if positions:
    with st.spinner("Fetching prezzi correnti…"):
        prices = cached_current_prices(tuple(sorted(positions.keys())))
    for t, p in positions.items():
        cur = prices.get(t)
        if cur is not None:
            unrealized += (cur - p["entry_price"]) * p["shares"]

invested = total - cash
cash_pct = cash / total if total else 0.0
min_cash = total * MIN_CASH_RESERVE_PCT

kpi_row([
    ("Portfolio value", fmt_eur(total + unrealized), None),
    ("Cash", fmt_eur(cash), fmt_pct(cash_pct)),
    ("Invested", fmt_eur(invested), fmt_pct(invested / total) if total else "—"),
    (
        "P&L unrealized",
        fmt_eur(unrealized),
        fmt_pct(unrealized / invested) if invested else None,
    ),
    ("Positions", f"{len(positions)} / {MAX_POSITIONS}", None),
])

if cash < min_cash:
    st.error(
        f"Cash sotto la riserva minima ({fmt_eur(cash)} < {fmt_eur(min_cash)} "
        f"= {MIN_CASH_RESERVE_PCT * 100:.0f}% del portfolio). Nessuna nuova entry."
    )

st.divider()

# ---------------------------------------------------------------------------
# Open positions table
# ---------------------------------------------------------------------------
st.subheader("Posizioni aperte")
if not positions:
    st.info("Nessuna posizione aperta. Vai su **Scanner** o **ETF Rotation** per analizzare setup.")
else:
    rows = []
    for ticker, p in sorted(positions.items()):
        cur = prices.get(ticker)
        mv = (cur or p["entry_price"]) * p["shares"]
        pnl = (cur - p["entry_price"]) * p["shares"] if cur is not None else None
        pnl_pct = (cur - p["entry_price"]) / p["entry_price"] if cur is not None else None
        stop_dist = (
            (cur - p["stop_loss"]) / cur if cur is not None and cur > 0 else None
        )
        rows.append({
            "Ticker": ticker,
            "Strategy": p.get("strategy") or "—",
            "Shares": p["shares"],
            "Entry": f"{p['entry_price']:.2f}",
            "Current": f"{cur:.2f}" if cur is not None else "—",
            "MV": fmt_eur(mv),
            "Size%": fmt_pct(mv / (total + unrealized)) if total else "—",
            "P&L": fmt_eur(pnl) if pnl is not None else "—",
            "P&L%": fmt_pct(pnl_pct) if pnl_pct is not None else "—",
            "Stop": f"{p['stop_loss']:.2f}",
            "Stop dist": fmt_pct(stop_dist) if stop_dist is not None else "—",
            "Target": f"{p['target']:.2f}" if p.get("target") else "—",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Recent journal entries
# ---------------------------------------------------------------------------
st.subheader("Ultime chiusure")
journal = load_journal()
closed = [t for t in journal if t.get("status") == "closed"]
closed.sort(key=lambda t: t.get("exit_date") or "", reverse=True)
if not closed:
    st.caption("Nessun trade chiuso nel journal.")
else:
    recent = closed[:5]
    rows = []
    for t in recent:
        rows.append({
            "Ticker": t["ticker"],
            "Strategy": t.get("strategy") or "—",
            "Entry date": t["entry_date"],
            "Exit date": t.get("exit_date") or "—",
            "Days": t.get("duration_days") or "—",
            "P&L %": fmt_pct((t.get("pnl_pct") or 0) / 100) if t.get("pnl_pct") is not None else "—",
            "Reason": t.get("exit_reason") or "—",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Watchlist scorer (top-N dal journal recent closed per strategy)
# ---------------------------------------------------------------------------
st.subheader("Score classification — riferimento")
col1, col2, col3, col4 = st.columns(4)
col1.markdown("A — Azione immediata  \n" + score_badge(80), unsafe_allow_html=True)
col2.markdown("B — Watchlist  \n" + score_badge(65), unsafe_allow_html=True)
col3.markdown("C — Neutrale  \n" + score_badge(50), unsafe_allow_html=True)
col4.markdown("D — Skip  \n" + score_badge(30), unsafe_allow_html=True)
