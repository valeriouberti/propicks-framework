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

STRATEGIES_FILTER = [
    "(tutti)",
    "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane",
    "ETF_Rotation",
    "Contrarian",
    "Thematic",
    "Altro",
]

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

        # ─── CHARTS (lazy import plotly) ──────────────────────────────────
        import plotly.graph_objects as go

        st.divider()

        # ── Chart 1: Equity curve cumulativa (P&L compounded per exit_date)
        # Aggrega trade chiusi per exit_date, applica compounding (1+r) e
        # mostra growth-of-1 dell'equity strategy. Realized only.
        from datetime import datetime as _dt

        scope_sorted = sorted(
            [t for t in scope if t.get("exit_date")],
            key=lambda t: t["exit_date"],
        )
        if scope_sorted:
            dates = []
            equity = [1.0]
            for t in scope_sorted:
                try:
                    d = _dt.strptime(t["exit_date"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                ret = float(t["pnl_pct"]) / 100.0
                equity.append(equity[-1] * (1 + ret))
                dates.append(d)
            # First point = pre-trades baseline (equity 1.0)
            if dates:
                pre = [dates[0]]  # placeholder, we'll align series
                eq_series = equity[1:]  # drop initial 1.0, align with dates
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(
                    x=dates, y=eq_series, mode="lines+markers",
                    name="Equity (growth-of-1)",
                    line=dict(color="#3b82f6", width=2),
                    marker=dict(size=6),
                    hovertemplate="<b>%{x}</b><br>Equity %{y:.4f}<br>Cumulative %{customdata:+.2f}%<extra></extra>",
                    customdata=[(e - 1) * 100 for e in eq_series],
                ))
                fig_eq.add_hline(y=1.0, line_dash="dot", line_color="#94a3b8", annotation_text="break-even")
                final_eq = eq_series[-1]
                fig_eq.update_layout(
                    title=dict(
                        text=f"Equity curve (realized) — final {final_eq:.4f} ({(final_eq - 1) * 100:+.2f}%)",
                        x=0.5, xanchor="center", font=dict(size=13),
                    ),
                    xaxis_title="Exit date",
                    yaxis_title="Growth of 1",
                    height=340,
                    hovermode="x unified",
                    margin=dict(l=20, r=20, t=50, b=20),
                )
                st.plotly_chart(fig_eq, width="stretch")
                st.caption(
                    "Equity compounded sui closed trades, ordinato per exit_date. "
                    "**Realized only** — non include unrealized P&L delle posizioni aperte."
                )

        # ── Chart 2 + 3 side-by-side: P&L histogram + Win rate per strategy
        col_h, col_w = st.columns(2)

        with col_h:
            # P&L distribution histogram con vline su 0 + media
            fig_h = go.Figure()
            fig_h.add_trace(go.Histogram(
                x=pnls_pct, nbinsx=15,
                marker=dict(
                    color=["#16a34a" if p > 0 else "#dc2626" for p in pnls_pct],
                    line=dict(color="white", width=1),
                ),
                hovertemplate="P&L %{x:.1f}%<br>Count %{y}<extra></extra>",
            ))
            mean_pnl = statistics.mean(pnls_pct)
            fig_h.add_vline(x=0, line_dash="dash", line_color="#64748b", annotation_text="break-even")
            fig_h.add_vline(
                x=mean_pnl, line_dash="dot", line_color="#3b82f6",
                annotation_text=f"avg {mean_pnl:+.2f}%", annotation_position="top",
            )
            fig_h.update_layout(
                title=dict(
                    text=f"P&L distribution (n={len(pnls_pct)})",
                    x=0.5, xanchor="center", font=dict(size=13),
                ),
                xaxis_title="P&L %", yaxis_title="N trades",
                height=320, showlegend=False,
                margin=dict(l=20, r=20, t=50, b=20), bargap=0.05,
            )
            st.plotly_chart(fig_h, width="stretch")
            st.caption(
                "Verde = win, rosso = loss. Skew destra = upside catturato. "
                "Tail sinistra lunga = stop-loss che saltano oltre soglia."
            )

        with col_w:
            # Win rate per strategy bar chart (solo se non filtrato)
            if strat_filter == "(tutti)":
                by_strat_pnl: dict[str, list[float]] = {}
                for t in scope:
                    by_strat_pnl.setdefault(t.get("strategy") or "—", []).append(t["pnl_pct"])
                strats = sorted(by_strat_pnl.keys())
                wr_per = [
                    sum(1 for p in by_strat_pnl[s] if p > 0) / len(by_strat_pnl[s]) * 100
                    for s in strats
                ]
                avg_per = [statistics.mean(by_strat_pnl[s]) for s in strats]
                n_per = [len(by_strat_pnl[s]) for s in strats]

                fig_w = go.Figure()
                fig_w.add_trace(go.Bar(
                    x=strats, y=wr_per,
                    marker=dict(
                        color=[
                            "#16a34a" if wr >= 50 else "#ca8a04" if wr >= 40 else "#dc2626"
                            for wr in wr_per
                        ],
                    ),
                    text=[f"{wr:.0f}%<br>n={n}" for wr, n in zip(wr_per, n_per, strict=True)],
                    textposition="auto",
                    hovertemplate="<b>%{x}</b><br>Win rate %{y:.1f}%<br>Avg P&L %{customdata:+.2f}%<extra></extra>",
                    customdata=avg_per,
                ))
                fig_w.add_hline(y=50, line_dash="dot", line_color="#64748b", annotation_text="50%")
                fig_w.update_layout(
                    title=dict(
                        text="Win rate per strategy",
                        x=0.5, xanchor="center", font=dict(size=13),
                    ),
                    xaxis_title="", yaxis_title="Win rate %",
                    height=320, yaxis=dict(range=[0, 100]),
                    margin=dict(l=20, r=20, t=50, b=20),
                )
                st.plotly_chart(fig_w, width="stretch")
                st.caption(
                    "Verde ≥ 50%, giallo 40-50%, rosso < 40%. Confronto edge per "
                    "strategia. Sotto 15 trade per strategy = stat signal debole."
                )
            else:
                st.info(
                    f"Win-rate per strategy disponibile solo con filtro **'(tutti)'**. "
                    f"Filtro corrente: {strat_filter}."
                )

        st.divider()

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

        # ─── Manual AI verdicts accuracy ──────────────────────────────────
        st.divider()
        st.subheader("🤖 AI verdict accuracy (manual paste)")
        st.caption(
            "Calibration dei verdict LLM esterni (Perplexity Pro / Sonar / "
            "Claude.ai / GPT / Gemini) incollati nelle page Momentum/Contrarian/"
            "Thematic. Linkati a trade chiusi → match outcome ex-post. "
            "**Per linkare** un verdict a un trade, vai a Stats → expander "
            "'🔗 Link manuali' qui sotto."
        )

        from propicks.io.manual_verdicts_store import (
            compute_accuracy as _acc, list_all_verdicts,
        )

        # Filter source per stats
        _src_pick = st.selectbox(
            "Filter source",
            options=["all", "perplexity_pro", "sonar", "claude_web", "gpt", "gemini", "other"],
            key="acc_src_filter",
        )
        _src_arg = None if _src_pick == "all" else _src_pick
        _strat_arg = None if strat_filter == "(tutti)" else strat_filter

        acc = _acc(strategy=_strat_arg, source=_src_arg)

        if acc["n_total"] == 0:
            st.info(
                "Nessun verdict manuale linkato a trade chiusi. "
                "Apri page 1/3/4, incolla risposta LLM, poi linka via expander "
                "'🔗 Link manuali' qui sotto quando il trade è aperto + chiuso."
            )
        else:
            ac1, ac2, ac3, ac4 = st.columns(4)
            ac1.metric("Verdict directional", acc["n_directional"])
            ac2.metric(
                "Accuracy",
                f"{acc['accuracy'] * 100:.1f}%" if acc["accuracy"] is not None else "—",
                help="N correct / (N CONFIRM+REJECT). >50% = better than random.",
            )
            ac3.metric(
                "Brier score",
                f"{acc['brier_score']:.3f}" if acc["brier_score"] is not None else "—",
                help="< 0.25 = better than random (lower is better)",
            )
            ac4.metric("Caution (skipped)", acc["n_caution"])

            # Confusion matrix-style table
            conf_rows = [
                {
                    "Verdict": "🟢 CONFIRM",
                    "Outcome WIN": acc["n_confirm_win"],
                    "Outcome LOSS": acc["n_confirm_loss"],
                    "Match": "✅" if acc["n_confirm_win"] >= acc["n_confirm_loss"] else "❌",
                },
                {
                    "Verdict": "🔴 REJECT",
                    "Outcome WIN": acc["n_reject_win"],
                    "Outcome LOSS": acc["n_reject_loss"],
                    "Match": "✅" if acc["n_reject_loss"] >= acc["n_reject_win"] else "❌",
                },
            ]
            st.dataframe(conf_rows, width="stretch", hide_index=True)
            st.caption(
                "**Match logic**: CONFIRM+WIN ✅ correct (true positive) · "
                "CONFIRM+LOSS ❌ false positive · "
                "REJECT+LOSS ✅ correct (true negative) · "
                "REJECT+WIN ❌ false negative. "
                "**Brier score** = mean((P(verdict) - actual)²) con "
                "P(CONFIRM)=0.8, P(CAUTION)=0.5, P(REJECT)=0.2."
            )

        # ─── Link verdict ⇄ trade (manual + auto bulk) ──────────────────────
        with st.expander("🔗 Link verdict ⇄ trade (auto + manuale)", expanded=False):
            from propicks.io.manual_verdicts_store import (
                auto_link_all_orphans, delete_verdict, link_to_trade,
            )

            # ─ Auto-link bulk button ─
            auto_col1, auto_col2 = st.columns([1, 2])
            _max_days = auto_col1.number_input(
                "Match window (gg)",
                min_value=1, max_value=30, value=7, step=1,
                key="autolink_max_days",
                help="Cerca trade con entry_date entro ±N gg dal pasted_at del verdict.",
            )
            if auto_col1.button(
                "🔗 Auto-link orphans",
                type="primary",
                key="btn_autolink_all",
                help="Tenta link automatico per TUTTI i verdict senza trade_id",
            ):
                res = auto_link_all_orphans(max_days=int(_max_days))
                if res["total_orphan"] == 0:
                    auto_col2.info("Nessun verdict orphan da linkare.")
                else:
                    auto_col2.success(
                        f"Auto-link completato: **{res['linked']}** linkati · "
                        f"**{res['skipped']}** skipped (su {res['total_orphan']} orphan)"
                    )
                    # Show details
                    if res["details"]:
                        det_rows = [
                            {
                                "Verdict ID": d["verdict_id"],
                                "Ticker": d["ticker"],
                                "Result": d["result"],
                                "Trade ID": d.get("trade_id") or "—",
                                "Detail": d["msg"][:60],
                            }
                            for d in res["details"]
                        ]
                        st.dataframe(det_rows, width="stretch", hide_index=True)
                st.rerun()

            st.divider()
            st.markdown("##### Link manuale (override)")

            all_v = list_all_verdicts(strategy=_strat_arg, source=_src_arg)
            if not all_v:
                st.caption("_Nessun verdict salvato. Vai a page 1/3/4 → '📥 Incolla risposta LLM'._")
            else:
                # Lista verdict salvati (linkati e non)
                v_rows = [
                    {
                        "ID": v["id"],
                        "Ticker": v["ticker"],
                        "Strategy": v.get("strategy") or "—",
                        "Source": v.get("source"),
                        "Verdict": v.get("verdict") or "—",
                        "Conviction": v.get("conviction") or "—",
                        "Trade ID": v.get("trade_id") or "—",
                        "Pasted": v.get("pasted_at"),
                    }
                    for v in all_v
                ]
                st.dataframe(v_rows, width="stretch", hide_index=True)

                lc1, lc2, lc3 = st.columns([1, 1, 1])
                with lc1:
                    _vid_link = st.number_input(
                        "Verdict ID",
                        min_value=0, value=0, step=1,
                        key="link_verdict_id",
                    )
                    _tid_link = st.number_input(
                        "Trade ID",
                        min_value=0, value=0, step=1,
                        key="link_trade_id",
                    )
                    if st.button("Link", type="primary", key="btn_link_v"):
                        if _vid_link > 0 and _tid_link > 0:
                            try:
                                link_to_trade(int(_vid_link), int(_tid_link))
                                st.success(f"Linked verdict #{_vid_link} → trade #{_tid_link}")
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))
                        else:
                            st.warning("Verdict ID e Trade ID > 0 obbligatori.")
                with lc2:
                    _vid_del = st.number_input(
                        "Verdict ID delete",
                        min_value=0, value=0, step=1,
                        key="del_verdict_id",
                    )
                    if st.button("Delete", type="secondary", key="btn_del_v"):
                        if _vid_del > 0:
                            delete_verdict(int(_vid_del))
                            st.success(f"Deleted verdict #{_vid_del}")
                            st.rerun()
                with lc3:
                    st.caption(
                        "Workflow: 1) Incolla LLM response in page 1/3/4 → "
                        "verdict salvato no-trade-link. 2) Apri trade in journal "
                        "(otteni trade ID dalla tabella Trades). 3) Qui Link "
                        "verdict_id + trade_id. 4) Quando trade close, accuracy "
                        "aggiornata automaticamente."
                    )

# ---------------------------------------------------------------------------
# Add trade
# ---------------------------------------------------------------------------
STRATEGIES = (
    "",
    "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane",
    "ETF_Rotation",
    "Contrarian",
    "Thematic",
    "Altro",
)

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
