"""Portfolio management — size calculator, add/update/remove position.

Equivalent UI di:
    propicks-portfolio status
    propicks-portfolio risk
    propicks-portfolio size
    propicks-portfolio add / update / remove
"""

from __future__ import annotations

import streamlit as st

from propicks.config import CAPITAL, MIN_CASH_RESERVE_PCT
from propicks.dashboard._shared import (
    cached_current_prices,
    fmt_eur,
    fmt_pct,
    invariants_note,
    load_portfolio,
    page_header,
)
from propicks.domain.etf_universe import get_asset_type
from propicks.domain.sizing import calculate_position_size, portfolio_value
from propicks.io.portfolio_store import (
    add_position,
    remove_position,
    update_position,
)

st.set_page_config(page_title="Portfolio · Propicks", layout="wide")
page_header(
    "Portfolio",
    "Size calculator + mutazioni posizioni. Tutte le validazioni hard (invariants) sono enforced dallo store.",
)
invariants_note()

portfolio = load_portfolio()
positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)
total = portfolio_value(portfolio)

# ---------------------------------------------------------------------------
# Header metrics
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Capital riferimento", fmt_eur(CAPITAL))
col2.metric("Portfolio value", fmt_eur(total))
col3.metric("Cash", fmt_eur(cash), fmt_pct(cash / total) if total else "—")
col4.metric("Posizioni aperte", len(positions))

if cash < total * MIN_CASH_RESERVE_PCT:
    st.error(
        f"Cash sotto riserva minima {MIN_CASH_RESERVE_PCT * 100:.0f}% — blocca nuove entry."
    )

st.divider()

# ---------------------------------------------------------------------------
# Tabs: Size | Add | Update | Remove
# ---------------------------------------------------------------------------
tab_size, tab_add, tab_update, tab_remove = st.tabs([
    "Size calculator", "Apri posizione", "Aggiorna stop/target", "Chiudi posizione",
])

# ---------------------------------------------------------------------------
# Size calculator
# ---------------------------------------------------------------------------
with tab_size:
    st.caption(
        "Calcola shares da comprare dato entry, stop e convinzione. "
        "Cap per asset type: 15% stock / 20% ETF. Non modifica il portfolio."
    )
    with st.form("size_form", border=True):
        cols = st.columns([2, 1, 1, 1, 1, 1])
        ticker = cols[0].text_input("Ticker", placeholder="AAPL / XLK / XDWT.DE")
        entry = cols[1].number_input("Entry price", min_value=0.01, step=0.01, format="%.2f")
        stop = cols[2].number_input("Stop price", min_value=0.01, step=0.01, format="%.2f")
        score_claude = cols[3].slider("Score Claude", 0, 10, 7)
        score_tech = cols[4].slider("Score tech", 0, 100, 70)
        asset_override = cols[5].selectbox(
            "Asset type", options=("auto", "STOCK", "SECTOR_ETF"), index=0
        )
        submitted = st.form_submit_button("Calcola", type="primary")

    if submitted:
        if not ticker.strip():
            st.warning("Inserisci un ticker.")
            st.stop()
        asset_type = (
            get_asset_type(ticker.strip()) if asset_override == "auto" else asset_override
        )
        result = calculate_position_size(
            entry_price=entry,
            stop_price=stop,
            score_claude=score_claude,
            score_tech=score_tech,
            portfolio=portfolio,
            asset_type=asset_type,  # type: ignore[arg-type]
        )
        if not result.get("ok"):
            st.error(result.get("error", "Errore sconosciuto."))
        else:
            st.success(
                f"**{result['shares']} shares** · {fmt_eur(result['position_value'])} "
                f"({fmt_pct(result['position_pct'])} portfolio) · "
                f"Conviction {result['conviction']} · asset {result['asset_type']}"
            )
            a, b, c, d = st.columns(4)
            a.metric("Risk/share", f"{result['risk_per_share']:.2f}")
            b.metric("Risk totale", fmt_eur(result["risk_total"]))
            c.metric("Risk % trade", fmt_pct(result["risk_pct_trade"]))
            d.metric("Risk % capitale", fmt_pct(result["risk_pct_capital"]))
            for w in result.get("warnings", []):
                st.warning(w)

# ---------------------------------------------------------------------------
# Add position
# ---------------------------------------------------------------------------
STRATEGIES = ("", "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane", "ETF_Rotation", "Altro")

with tab_add:
    st.caption(
        "Apri una posizione. Validazioni hard applicate (max size, cash floor, stop, score min). "
        "Non scrive il journal — vai alla tab **Journal** per il record append-only."
    )
    with st.form("add_form", border=True):
        cols = st.columns([2, 1, 1, 1])
        a_ticker = cols[0].text_input("Ticker", key="add_ticker")
        a_entry = cols[1].number_input(
            "Entry", min_value=0.01, step=0.01, format="%.2f", key="add_entry"
        )
        a_shares = cols[2].number_input("Shares", min_value=1, step=1, key="add_shares")
        a_stop = cols[3].number_input(
            "Stop", min_value=0.01, step=0.01, format="%.2f", key="add_stop"
        )

        cols2 = st.columns([1, 2, 1, 1])
        a_target = cols2[0].number_input(
            "Target (0 = skip)", min_value=0.0, step=0.01, format="%.2f", key="add_target"
        )
        a_strategy = cols2[1].selectbox("Strategy", STRATEGIES, key="add_strategy")
        a_claude = cols2[2].slider("Score Claude", 0, 10, 7, key="add_sc")
        a_tech = cols2[3].slider("Score tech", 0, 100, 70, key="add_st")
        a_catalyst = st.text_input(
            "Catalyst (breve)", placeholder="Earnings beat, guidance raise, …", key="add_cat"
        )
        submitted = st.form_submit_button("Apri posizione", type="primary")

    if submitted:
        if not a_ticker.strip():
            st.warning("Ticker obbligatorio.")
        else:
            try:
                pos = add_position(
                    portfolio=portfolio,
                    ticker=a_ticker.strip(),
                    entry_price=a_entry,
                    shares=int(a_shares),
                    stop_loss=a_stop,
                    target=a_target or None,
                    strategy=(a_strategy or None),
                    score_claude=a_claude,
                    score_tech=a_tech,
                    catalyst=a_catalyst or None,
                )
                st.success(
                    f"Apertura {a_ticker.upper()} salvata: "
                    f"{pos['shares']} × {pos['entry_price']:.2f} = "
                    f"{fmt_eur(pos['shares'] * pos['entry_price'])}"
                )
                st.caption("Ricarica la pagina per vedere il portfolio aggiornato.")
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Update position
# ---------------------------------------------------------------------------
with tab_update:
    if not positions:
        st.info("Nessuna posizione aperta.")
    else:
        with st.form("update_form", border=True):
            cols = st.columns([2, 1, 1])
            u_ticker = cols[0].selectbox("Ticker", sorted(positions.keys()), key="upd_ticker")
            cur_pos = positions[u_ticker]
            u_stop = cols[1].number_input(
                "Nuovo stop (0 = no change)",
                min_value=0.0,
                value=float(cur_pos.get("stop_loss") or 0),
                step=0.01,
                format="%.2f",
                key="upd_stop",
            )
            u_target = cols[2].number_input(
                "Nuovo target (0 = no change)",
                min_value=0.0,
                value=float(cur_pos.get("target") or 0),
                step=0.01,
                format="%.2f",
                key="upd_target",
            )
            submitted = st.form_submit_button("Aggiorna", type="primary")

        if submitted:
            try:
                new = update_position(
                    portfolio=portfolio,
                    ticker=u_ticker,
                    stop_loss=u_stop if u_stop > 0 else None,
                    target=u_target if u_target > 0 else None,
                )
                target_str = f"{new['target']:.2f}" if new.get("target") else "—"
                st.success(
                    f"{u_ticker}: stop {new['stop_loss']:.2f} · target {target_str}"
                )
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Remove position
# ---------------------------------------------------------------------------
with tab_remove:
    if not positions:
        st.info("Nessuna posizione da chiudere.")
    else:
        st.warning(
            "**Attenzione**: *Remove* rimette il valore al costo di entry nel cash. "
            "Non registra P&L — per tracciare la chiusura usa **Journal → Close trade**. "
            "Questa azione serve solo per rimuovere un errore di data-entry."
        )
        with st.form("remove_form", border=True):
            r_ticker = st.selectbox("Ticker", sorted(positions.keys()), key="rm_ticker")
            prices_now = cached_current_prices(tuple(sorted(positions.keys())))
            pos = positions[r_ticker]
            cur = prices_now.get(r_ticker)
            if cur is not None:
                st.caption(
                    f"Current price: **{cur:.2f}** · "
                    f"Entry: {pos['entry_price']:.2f} · "
                    f"Unrealized P&L: {fmt_eur((cur - pos['entry_price']) * pos['shares'])}"
                )
            submitted = st.form_submit_button("Rimuovi (solo correzione data-entry)", type="secondary")

        if submitted:
            try:
                removed = remove_position(portfolio=portfolio, ticker=r_ticker)
                st.success(
                    f"{r_ticker} rimosso · refund cash {fmt_eur(removed['shares'] * removed['entry_price'])}"
                )
            except ValueError as err:
                st.error(str(err))
