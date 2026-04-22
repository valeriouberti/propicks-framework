"""Journal append-only — add/close trade, list, stats aggregate.

Equivalent UI di:
    propicks-journal add / close / list / stats
"""

from __future__ import annotations

import statistics
from datetime import date

import streamlit as st

from propicks.dashboard._shared import (
    fmt_pct,
    invariants_note,
    load_journal,
    page_header,
)
from propicks.domain.verdict import max_drawdown, profit_factor, verdict
from propicks.io.journal_store import find_open
from propicks.io.trade_sync import close_trade as sync_close_trade
from propicks.io.trade_sync import open_trade as sync_open_trade

st.set_page_config(page_title="Journal · Propicks", layout="wide")
page_header(
    "Journal",
    "Append-only trade log. Source of truth per valutare la strategia. "
    "I trade chiusi non vengono mai cancellati — viene aggiunto il campo exit_*.",
)
invariants_note()

trades = load_journal()
open_trades = [t for t in trades if t.get("status") == "open"]
closed_trades = [t for t in trades if t.get("status") == "closed"]

# ---------------------------------------------------------------------------
# Top KPIs
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total trades", len(trades))
col2.metric("Open", len(open_trades))
col3.metric("Closed", len(closed_trades))
if closed_trades:
    wins = sum(1 for t in closed_trades if (t.get("pnl_pct") or 0) > 0)
    col4.metric("Win rate", f"{wins / len(closed_trades) * 100:.1f}%")
else:
    col4.metric("Win rate", "—")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_list, tab_stats, tab_add, tab_close = st.tabs([
    "Trades", "Stats", "Add trade", "Close trade",
])

STRATEGIES_FILTER = ["(tutti)", "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane", "ETF_Rotation", "Altro"]

# ---------------------------------------------------------------------------
# List trades
# ---------------------------------------------------------------------------
with tab_list:
    col_f1, col_f2 = st.columns([1, 1])
    status_f = col_f1.radio(
        "Status", options=("all", "open", "closed"), horizontal=True, key="jlist_status"
    )
    strat_f = col_f2.selectbox("Strategy", STRATEGIES_FILTER, key="jlist_strat")

    filtered = trades
    if status_f != "all":
        filtered = [t for t in filtered if t.get("status") == status_f]
    if strat_f != "(tutti)":
        filtered = [t for t in filtered if t.get("strategy") == strat_f]

    if not filtered:
        st.info("Nessun trade con questi filtri.")
    else:
        filtered_sorted = sorted(
            filtered,
            key=lambda t: t.get("entry_date") or "",
            reverse=True,
        )
        rows = []
        for t in filtered_sorted:
            pnl_pct = t.get("pnl_pct")
            rows.append({
                "ID": t.get("id"),
                "Ticker": t.get("ticker"),
                "Dir": t.get("direction"),
                "Status": t.get("status"),
                "Strategy": t.get("strategy") or "—",
                "Entry date": t.get("entry_date"),
                "Entry": f"{t.get('entry_price', 0):.2f}",
                "Stop": f"{t.get('stop_loss', 0):.2f}",
                "Target": f"{t.get('target'):.2f}" if t.get("target") else "—",
                "Exit date": t.get("exit_date") or "—",
                "Exit": f"{t.get('exit_price'):.2f}" if t.get("exit_price") is not None else "—",
                "P&L %": f"{pnl_pct:+.2f}%" if pnl_pct is not None else "—",
                "Days": str(t.get("duration_days")) if t.get("duration_days") is not None else "—",
                "Score C": str(t.get("score_claude")) if t.get("score_claude") is not None else "—",
                "Score T": str(t.get("score_tech")) if t.get("score_tech") is not None else "—",
                "Catalyst": (t.get("catalyst") or "")[:40],
            })
        st.dataframe(rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
with tab_stats:
    strat_filter = st.selectbox(
        "Filtra per strategy (opzionale)", STRATEGIES_FILTER, key="jstats_strat"
    )
    scope = closed_trades
    if strat_filter != "(tutti)":
        scope = [t for t in scope if t.get("strategy") == strat_filter]

    if not scope:
        st.info("Nessun trade chiuso per questo filtro.")
    else:
        pnls_pct = [t["pnl_pct"] for t in scope]
        wins = [p for p in pnls_pct if p > 0]
        losses = [p for p in pnls_pct if p <= 0]
        wr = len(wins) / len(scope)
        avg_win = statistics.mean(wins) if wins else 0.0
        avg_loss = statistics.mean(losses) if losses else 0.0
        pf = profit_factor(pnls_pct)
        max_dd = max_drawdown(pnls_pct)

        a, b, c, d = st.columns(4)
        a.metric("Trade chiusi", len(scope))
        b.metric("Win rate", fmt_pct(wr))
        c.metric("Avg win", f"{avg_win:+.2f}%")
        d.metric("Avg loss", f"{avg_loss:+.2f}%")

        a, b, c, d = st.columns(4)
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        a.metric("Profit factor", pf_str)
        b.metric("Best", f"{max(pnls_pct):+.2f}%")
        c.metric("Worst", f"{min(pnls_pct):+.2f}%")
        d.metric("Max DD cumulativo", f"{max_dd:.2f}%")

        # Breakdown per strategy (solo se non filtrato)
        if strat_filter == "(tutti)":
            st.subheader("Breakdown per strategy")
            by_strat: dict[str, list[float]] = {}
            for t in scope:
                by_strat.setdefault(t.get("strategy") or "—", []).append(t["pnl_pct"])
            rows = []
            for strat, pls in by_strat.items():
                wr_s = sum(1 for p in pls if p > 0) / len(pls)
                rows.append({
                    "Strategy": strat,
                    "# trade": len(pls),
                    "Avg P&L": f"{statistics.mean(pls):+.2f}%",
                    "Win rate": fmt_pct(wr_s),
                    "Best": f"{max(pls):+.2f}%",
                    "Worst": f"{min(pls):+.2f}%",
                })
            st.dataframe(rows, width="stretch", hide_index=True)

        # Breakdown per score band
        st.subheader("Breakdown per score Claude")
        bands: dict[str, list[float]] = {"alta (>= 8)": [], "media (6-7)": [], "altro/N/A": []}
        for t in scope:
            sc = t.get("score_claude")
            if sc is None:
                bands["altro/N/A"].append(t["pnl_pct"])
            elif sc >= 8:
                bands["alta (>= 8)"].append(t["pnl_pct"])
            elif sc >= 6:
                bands["media (6-7)"].append(t["pnl_pct"])
            else:
                bands["altro/N/A"].append(t["pnl_pct"])
        rows = []
        for band, pls in bands.items():
            if not pls:
                rows.append({"Band": band, "# trade": 0, "Avg P&L": "—", "Win rate": "—"})
                continue
            wr_b = sum(1 for p in pls if p > 0) / len(pls)
            rows.append({
                "Band": band,
                "# trade": len(pls),
                "Avg P&L": f"{statistics.mean(pls):+.2f}%",
                "Win rate": fmt_pct(wr_b),
            })
        st.dataframe(rows, width="stretch", hide_index=True)

        st.info(f"**Verdetto sintetico:** {verdict(wr, pf, len(scope))}")

# ---------------------------------------------------------------------------
# Add trade
# ---------------------------------------------------------------------------
STRATEGIES = ("", "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane", "ETF_Rotation", "Altro")

with tab_add:
    with st.form("add_trade_form", border=True):
        cols = st.columns([2, 1, 1, 1, 1])
        t_ticker = cols[0].text_input("Ticker", key="at_ticker")
        t_dir = cols[1].selectbox("Direction", ("long", "short"), key="at_dir")
        t_entry = cols[2].number_input("Entry price", min_value=0.01, step=0.01, format="%.2f", key="at_entry")
        t_shares = cols[3].number_input("Shares", min_value=1, step=1, value=1, key="at_shares")
        t_stop = cols[4].number_input("Stop", min_value=0.01, step=0.01, format="%.2f", key="at_stop")

        cols2 = st.columns([1, 1, 1, 1])
        t_date = cols2[0].date_input("Entry date", value=date.today(), key="at_date")
        t_target = cols2[1].number_input("Target (0 = skip)", min_value=0.0, step=0.01, format="%.2f", key="at_target")
        t_sc = cols2[2].slider("Score Claude", 0, 10, 7, key="at_sc")
        t_st = cols2[3].slider("Score tech", 0, 100, 70, key="at_st")

        t_strat = st.selectbox("Strategy", STRATEGIES, key="at_strat")
        t_cat = st.text_input("Catalyst", placeholder="Beat earnings Q4, guidance raise, …", key="at_cat")
        t_notes = st.text_area("Notes", placeholder="Contesto aggiuntivo (opzionale)", key="at_notes")

        submitted = st.form_submit_button("Add trade", type="primary")

    if submitted:
        if not t_ticker.strip():
            st.warning("Ticker obbligatorio.")
        else:
            try:
                tr, pos, warnings = sync_open_trade(
                    ticker=t_ticker.strip(),
                    direction=t_dir,
                    entry_price=t_entry,
                    entry_date=t_date.isoformat(),
                    shares=int(t_shares),
                    stop_loss=t_stop,
                    target=t_target or None,
                    score_claude=t_sc,
                    score_tech=t_st,
                    strategy=t_strat or None,
                    catalyst=t_cat or None,
                    notes=t_notes or None,
                )
                st.toast(
                    f"Trade #{tr['id']} {tr['ticker']} aperto · "
                    f"{int(t_shares)} @ {tr['entry_price']:.2f}",
                    icon="✅",
                )
                if warnings:
                    # warnings richiedono lettura, niente rerun finché l'utente li vede
                    for w in warnings:
                        st.warning(w)
                    if pos is not None:
                        cost = pos["shares"] * pos["entry_price"]
                        st.info(
                            f"Portfolio aggiornato: -{cost:.2f} cash, "
                            f"+{pos['shares']} {tr['ticker']}"
                        )
                else:
                    st.rerun()
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Close trade
# ---------------------------------------------------------------------------
with tab_close:
    if not open_trades:
        st.info("Nessun trade aperto da chiudere.")
    else:
        # Selectbox fuori dal form: il pre-fill exit_price con lo spot reagisce
        # alla scelta del ticker.
        c_ticker = st.selectbox(
            "Ticker",
            sorted(t["ticker"] for t in open_trades),
            key="ct_ticker",
        )
        cur_trade = find_open(trades, c_ticker)
        if cur_trade is not None:
            target_str = (
                f"{cur_trade['target']:.2f}" if cur_trade.get("target") else "—"
            )
            st.caption(
                f"Aperto: {cur_trade['entry_date']} @ "
                f"{cur_trade['entry_price']:.2f} · stop "
                f"{cur_trade['stop_loss']:.2f} · target {target_str}"
            )

        from propicks.dashboard._shared import cached_current_prices as _ccp
        _spot = _ccp(tuple([c_ticker])).get(c_ticker)

        with st.form("close_trade_form", border=True):
            cols = st.columns([1, 1])
            # Key per-ticker: evita che session_state trattenga lo spot del
            # ticker precedente quando l'utente cambia selectbox.
            c_price = cols[0].number_input(
                "Exit price",
                min_value=0.01,
                value=float(_spot) if _spot else 0.01,
                step=0.01,
                format="%.2f",
                key=f"ct_price_{c_ticker}",
                help=(
                    f"Pre-fill con lo spot corrente ({_spot:.2f})." if _spot
                    else "Spot non disponibile — inserisci manualmente."
                ),
            )
            c_date = cols[1].date_input("Exit date", value=date.today(), key="ct_date")
            c_reason = st.selectbox(
                "Reason",
                ("Target raggiunto", "Stop colpito", "Trailing stop", "Exit manuale",
                 "Degrado tesi", "Earnings", "Altro"),
                key="ct_reason",
            )
            c_notes = st.text_area("Post-trade notes", key="ct_notes")
            submitted = st.form_submit_button("Close trade", type="primary")

        if submitted:
            try:
                tr, removed, warnings = sync_close_trade(
                    ticker=c_ticker,
                    exit_price=c_price,
                    exit_date=c_date.isoformat(),
                    reason=c_reason,
                    notes=c_notes or None,
                )
                # Il risultato di una chiusura è informazione *da leggere*
                # (P&L finale, warnings) — non faccio rerun automatico qui.
                pnl_color = "green" if tr["pnl_pct"] > 0 else "red"
                st.markdown(
                    f"Trade #{tr['id']} {tr['ticker']} chiuso: "
                    f"{tr['entry_price']:.2f} → {tr['exit_price']:.2f} · "
                    f"<span style='color:{pnl_color};font-weight:600;'>"
                    f"{tr['pnl_pct']:+.2f}%</span> in {tr['duration_days']} gg",
                    unsafe_allow_html=True,
                )
                if removed is not None:
                    proceeds = removed["shares"] * tr["exit_price"]
                    st.info(
                        f"Portfolio aggiornato: +{proceeds:.2f} cash, "
                        f"-{removed['shares']} {tr['ticker']}"
                    )
                for w in warnings:
                    st.warning(w)

                # -----------------------------------------------------
                # Claude 3D — post-trade analysis pronto da incollare
                # (Playbook §3D) — il momento giusto per generarlo è ora.
                # -----------------------------------------------------
                from propicks.ai.user_prompts import claude_3d_post_trade

                with st.expander(
                    "Prompt Claude 3D — analisi post-trade (copia-incolla)",
                    expanded=False,
                ):
                    st.caption(
                        "Da incollare nella web app Claude per estrarre "
                        "lesson-learn. Il campo catalyst del trade viene "
                        "incluso come motivo entry."
                    )
                    st.code(claude_3d_post_trade(tr), language=None)
            except ValueError as err:
                st.error(str(err))
