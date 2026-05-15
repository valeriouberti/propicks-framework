"""Portfolio management — size calculator, add/update/remove, risk, trade mgmt.

Equivalent UI di:
    propicks-portfolio status / risk
    propicks-portfolio size
    propicks-portfolio add / update / remove
    propicks-portfolio manage [--apply] / trail enable|disable
"""

from __future__ import annotations

import streamlit as st

# Bridge st.secrets → env vars (precede import propicks.config).
from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import MAX_LOSS_WEEKLY_PCT, MIN_CASH_RESERVE_PCT
from propicks.dashboard._shared import (
    INDICATOR_HELP_PORTFOLIO,
    cached_current_atr,
    cached_current_prices,
    cached_returns,
    cached_ticker_betas,
    cached_ticker_sectors,
    fmt_eur,
    fmt_pct,
    invariants_note,
    load_portfolio,
    page_header,
    render_indicator_legend,
)
from propicks.domain.etf_universe import get_asset_type, resolve_sector_key
from propicks.domain.exposure import (
    compute_beta_weighted_exposure,
    compute_concentration_warnings,
    compute_correlation_matrix,
    compute_sector_exposure,
    find_correlated_pairs,
)
from propicks.domain.sizing import (
    is_etf_rotation_position,
    is_thematic_position,
)
from propicks.domain.sizing import (
    calculate_position_size,
    portfolio_market_value,
    portfolio_value,
)
from propicks.domain.stock_rs import YF_SECTOR_TO_KEY
from propicks.domain.trade_mgmt import (
    DEFAULT_FLAT_THRESHOLD_PCT,
    DEFAULT_TIME_STOP_DAYS,
    DEFAULT_TRAILING_ATR_MULT,
    suggest_stop_update,
)
from propicks.io.portfolio_store import (
    add_position,
    get_initial_capital,
    increase_position,
    remove_position,
    set_initial_capital,
    update_position,
)

st.set_page_config(page_title="Portfolio · Propicks", layout="wide")
page_header(
    "Portfolio",
    "Size calculator + mutazioni posizioni + rischio aggregato + trade management. "
    "Tutte le validazioni hard (invariants) sono enforced dallo store.",
)
invariants_note()

portfolio = load_portfolio()
positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)
total = portfolio_value(portfolio)
ref_capital = get_initial_capital(portfolio)

# ---------------------------------------------------------------------------
# Header metrics
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Capital riferimento", fmt_eur(ref_capital))
col2.metric("Portfolio value", fmt_eur(total))
col3.metric("Cash", fmt_eur(cash), fmt_pct(cash / total) if total else "—")
col4.metric("Posizioni aperte", len(positions))

if cash < total * MIN_CASH_RESERVE_PCT:
    st.error(
        f"Cash sotto riserva minima {MIN_CASH_RESERVE_PCT * 100:.0f}% — blocca nuove entry."
    )

with st.expander("Modifica capitale di riferimento", expanded=False):
    st.caption(
        "Il **capitale di riferimento** è il seed iniziale usato solo per display "
        "(header + sidebar invariants). Non influisce sul sizing, che usa sempre "
        "`cash + sum(shares × entry)`. Con **Reset cash** (consentito solo a "
        "portfolio vuoto) puoi ri-allineare anche il cash disponibile — utile "
        "se parti con un capitale diverso da € 10.000."
    )
    with st.form("capital_form", border=True):
        new_cap = st.number_input(
            "Capitale di riferimento (€)",
            min_value=0.01,
            value=float(ref_capital),
            step=500.0,
            format="%.2f",
            key="cap_value",
        )
        reset_cash = st.checkbox(
            "Reset anche del cash disponibile (solo a portfolio vuoto)",
            value=False,
            disabled=bool(positions),
            key="cap_reset_cash",
            help=(
                "Azzera il cash al nuovo valore. Disabilitato se ci sono "
                "posizioni aperte — chiudile prima del reset per non rompere "
                "il cash accounting."
            ),
        )
        cap_submit = st.form_submit_button("Applica", type="primary")

    if cap_submit:
        try:
            set_initial_capital(portfolio, new_cap, reset_cash=reset_cash)
            msg = f"Capitale aggiornato a {fmt_eur(new_cap)}"
            if reset_cash:
                msg += " (cash resettato)"
            st.toast(msg, icon="✅")
            st.rerun()
        except ValueError as err:
            st.error(str(err))

st.divider()

# ---------------------------------------------------------------------------
# Tabs: Risk | Mgmt | Size | Add | Update | Remove
# ---------------------------------------------------------------------------
(
    tab_risk,
    tab_mgmt,
    tab_size,
    tab_add,
    tab_increase,
    tab_update,
    tab_remove,
    tab_broker,
) = st.tabs([
    "Rischio & esposizione",
    "Trade management",
    "Size calculator",
    "Apri posizione",
    "Incrementa",
    "Aggiorna stop/target",
    "Chiudi posizione",
    "📥 Broker import",
])

# ---------------------------------------------------------------------------
# Risk & exposure
# ---------------------------------------------------------------------------
with tab_risk:
    st.caption(
        "Mirror di `propicks-portfolio risk`: rischio per posizione a stop, "
        "concentrazione settoriale, beta-weighted gross long vs SPX, "
        "pair correlate ≥ 0.7."
    )
    if not positions:
        st.info("Nessuna posizione aperta — niente da analizzare.")
    else:
        tickers = sorted(positions.keys())
        prices_map = cached_current_prices(tuple(tickers))

        # Per-position risk table
        risk_rows = []
        risk_sum = 0.0
        for ticker in tickers:
            p = positions[ticker]
            entry = p["entry_price"]
            stop = p["stop_loss"]
            shares = p["shares"]
            last = prices_map.get(ticker)
            risk_eur = (entry - stop) * shares
            risk_pct = risk_eur / total if total else 0.0
            risk_sum += risk_eur
            # P&L% vs entry; buffer = (last - stop) / last → quanto resta prima
            # di toccare stop (negativo = già sotto stop, da gestire)
            pnl_pct = ((last - entry) / entry * 100) if (last and entry > 0) else None
            buffer_pct = ((last - stop) / last * 100) if (last and last > 0) else None
            risk_rows.append({
                "Ticker": ticker,
                "Shares": shares,
                "Entry": entry,
                "Last": last if last is not None else float("nan"),
                "P&L%": pnl_pct if pnl_pct is not None else float("nan"),
                "Stop": stop,
                "Buffer %": buffer_pct if buffer_pct is not None else float("nan"),
                "Rischio €": risk_eur,
                "% capitale": risk_pct * 100,
            })
        st.subheader("Rischio per posizione (a stop)")
        st.dataframe(
            risk_rows,
            width="stretch",
            hide_index=True,
            column_config={
                "Entry": st.column_config.NumberColumn(format="%.2f"),
                "Last": st.column_config.NumberColumn(
                    format="%.2f",
                    help="Prezzo corrente (cached). NaN se quote yfinance non disponibile.",
                ),
                "P&L%": st.column_config.NumberColumn(
                    format="%+.2f%%",
                    help="(last − entry) / entry. Drift mark-to-market vs ingresso.",
                ),
                "Stop": st.column_config.NumberColumn(format="%.2f"),
                "Buffer %": st.column_config.NumberColumn(
                    format="%+.2f%%",
                    help="(last − stop) / last. Quanto può scendere prima dello stop. Negativo = sotto stop.",
                ),
                "Rischio €": st.column_config.NumberColumn(format="€ %.2f"),
                "% capitale": st.column_config.ProgressColumn(
                    format="%.2f%%", min_value=0.0, max_value=2.0,
                    help="Loss potenziale se lo stop salta, in % del portfolio. Soglia informativa: 2% per trade.",
                ),
            },
        )
        st.caption(
            "**Last** = prezzo corrente · **P&L%** = drift vs entry · "
            "**Buffer %** = distanza al stop · **Rischio €** = `(entry − stop) × shares` · "
            "**% capitale** = rischio / portfolio_value."
        )

        weekly_limit = total * MAX_LOSS_WEEKLY_PCT
        risk_pct_total = risk_sum / total if total else 0.0
        a, b = st.columns(2)
        a.metric(
            "Rischio aggregato",
            fmt_eur(risk_sum),
            fmt_pct(risk_pct_total),
            help=INDICATOR_HELP_PORTFOLIO["risk_aggregato"],
        )
        b.metric(
            f"Limite settimanale ({MAX_LOSS_WEEKLY_PCT * 100:.0f}%)",
            fmt_eur(weekly_limit),
            help=INDICATOR_HELP_PORTFOLIO["weekly_limit"],
        )
        if risk_sum > weekly_limit:
            st.error(
                f"Rischio aggregato ({fmt_eur(risk_sum)}) oltre il limite "
                f"settimanale ({fmt_eur(weekly_limit)})."
            )

        st.divider()

        # Sector exposure (resolver + pie charts + tabella)
        with st.status("Analisi esposizione…", expanded=False) as _exp_status:
            st.write(f"Fetch settori GICS per {len(tickers)} ticker")
            sector_yf = cached_ticker_sectors(tuple(tickers))
            st.write("Fetch beta vs SPX")
            betas = cached_ticker_betas(tuple(tickers))
            _exp_status.update(label="Esposizione pronta", state="complete")
        # Risoluzione sector con priorità config-first (sector ETF + thematic
        # da SECTOR_ETFS_*/THEMATIC_ETFS), fallback Yahoo per stock single-name.
        sector_key_map = {
            t: resolve_sector_key(t, yahoo_sector_raw=s)
            for t, s in sector_yf.items()
        }
        # Esposizione: denominatore mark-to-market in EUR per match coi
        # numeratori. Multi-currency: passiamo currency_map a sector/beta
        # affinché numeratore venga FX-converted (denominatore già EUR).
        # Senza currency_map → bug visivo: posizione USD mostrava slice
        # sotto-stimata di ~FX_USD_EUR.
        total_market = portfolio_market_value(portfolio, prices_map)
        currency_map = {
            t: (p.get("currency") or "EUR")
            for t, p in positions.items()
        }

        # ─── PIE 1: Allocation bucket (Stock / ETF rotation / Thematic / Cash)
        # Vista cap-compliance: confronto immediato vs 40/60 policy.
        st.subheader("📊 Allocation buckets")
        cash_mtm = float(portfolio.get("cash") or 0)
        stock_val = 0.0
        etf_rot_val = 0.0
        thematic_val = 0.0
        from propicks.domain.sizing import _fx_to_eur as _fxe
        for tk, pos in positions.items():
            cur = prices_map.get(tk)
            if cur is None:
                cur = pos.get("entry_price", 0)
            mv_raw = float(pos.get("shares") or 0) * float(cur)
            mv = _fxe(mv_raw, pos.get("currency"))
            if is_thematic_position(pos, ticker=tk):
                thematic_val += mv
            elif is_etf_rotation_position(pos, ticker=tk):
                etf_rot_val += mv
            else:
                stock_val += mv

        bucket_data = [
            ("📊 Stock (mom+contra)", stock_val, "#3b82f6"),  # blue
            ("📈 ETF Rotation", etf_rot_val, "#10b981"),  # green
            ("🎯 Thematic", thematic_val, "#a855f7"),  # purple
            ("💰 Cash", cash_mtm, "#94a3b8"),  # slate
        ]
        bucket_data = [(l, v, c) for l, v, c in bucket_data if v > 0]

        import plotly.graph_objects as go
        col_pie1, col_pie2 = st.columns(2)
        with col_pie1:
            fig_b = go.Figure(data=[go.Pie(
                labels=[d[0] for d in bucket_data],
                values=[d[1] for d in bucket_data],
                marker=dict(colors=[d[2] for d in bucket_data]),
                hole=0.4,
                textinfo="label+percent",
                textposition="outside",
                hovertemplate="<b>%{label}</b><br>€ %{value:.2f}<br>%{percent}<extra></extra>",
            )])
            fig_b.update_layout(
                height=350, margin=dict(l=10, r=10, t=30, b=10),
                showlegend=False,
                title=dict(
                    text=f"Bucket allocation (cap Stock 40% / ETF 60%)",
                    x=0.5, xanchor="center", font=dict(size=13),
                ),
            )
            st.plotly_chart(fig_b, width="stretch")
            # Cap compliance row
            stock_pct = stock_val / total_market * 100 if total_market else 0
            etf_pct = (etf_rot_val + thematic_val) / total_market * 100 if total_market else 0
            stock_status = "🟢" if stock_pct < 40 else "🔴"
            etf_status = "🟢" if etf_pct < 60 else "🔴"
            st.caption(
                f"{stock_status} Stock {stock_pct:.1f}% / 40%  ·  "
                f"{etf_status} ETF {etf_pct:.1f}% / 60%  ·  "
                f"Cash {cash_mtm/total_market*100:.1f}% (min 20%)"
            )

        # ─── PIE 2: Sector concentration ───
        # Cash escluso (già visibile in PIE 1 buckets). Denominatore =
        # invested_only così pie + barre tabella matchano (entrambi % del
        # capitale investito, non del portfolio totale).
        invested_only = max(total_market - cash_mtm, 0.0)
        sector_exp_for_pie = compute_sector_exposure(
            positions, prices_map, sector_key_map, invested_only,
            currency_map=currency_map,
        )
        with col_pie2:
            if sector_exp_for_pie:
                items = sorted(sector_exp_for_pie.items(), key=lambda x: x[1], reverse=True)
                fig_s = go.Figure(data=[go.Pie(
                    labels=[k for k, _ in items],
                    values=[v * 100 for _, v in items],
                    hole=0.4,
                    textinfo="label+percent",
                    textposition="outside",
                    hovertemplate="<b>%{label}</b><br>%{value:.2f}% invested<extra></extra>",
                )])
                fig_s.update_layout(
                    height=350, margin=dict(l=10, r=10, t=30, b=10),
                    showlegend=False,
                    title=dict(
                        text="Concentrazione settoriale (% invested · cap 30%)",
                        x=0.5, xanchor="center", font=dict(size=13),
                    ),
                )
                st.plotly_chart(fig_s, width="stretch")
                top_sector, top_pct = items[0]
                st.caption(
                    f"Top sector: **{top_sector}** ({top_pct*100:.1f}%) · "
                    f"Settori coperti: {len(items)} / 11 GICS · "
                    "_Denominatore = capitale investito (cash escluso, già in pie buckets)._"
                )
            else:
                st.caption("_Sector data non disponibile per pie chart._")

        st.divider()
        st.subheader(
            "Concentrazione settoriale (tabella)",
            help=INDICATOR_HELP_PORTFOLIO["sector_exposure"],
        )
        sector_exp = sector_exp_for_pie
        if sector_exp:
            sector_rows = sorted(
                ([{"Settore": k, "Esposizione": v * 100}
                  for k, v in sector_exp.items()]),
                key=lambda r: r["Esposizione"],
                reverse=True,
            )
            st.dataframe(
                sector_rows,
                width="stretch",
                hide_index=True,
                column_config={
                    "Esposizione": st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0.0, max_value=30.0,
                        help="Quota del **capitale investito** per settore (cash escluso). Cap informativo: 30% → warning.",
                    ),
                },
            )
            st.caption(
                "Mapping da Yahoo a tassonomia interna (`Consumer Cyclical` → "
                "`consumer_discretionary`, ecc.). "
                + INDICATOR_HELP_PORTFOLIO["sector_cap"]
            )
            for w in compute_concentration_warnings(sector_exp):
                st.warning(f"Concentrazione: {w}")
        else:
            st.caption("Sector data non disponibile.")

        st.divider()

        # Beta-weighted gross long
        st.subheader("Beta-weighted gross long (vs SPX)")
        beta_info = compute_beta_weighted_exposure(
            positions, prices_map, betas, total_market,
            currency_map=currency_map,
        )
        b1, b2, b3 = st.columns(3)
        b1.metric(
            "Gross long",
            fmt_pct(beta_info["gross_long"]),
            help=INDICATOR_HELP_PORTFOLIO["gross_long"],
        )
        b2.metric(
            "Beta-weighted",
            fmt_pct(beta_info["beta_weighted"]),
            help=INDICATOR_HELP_PORTFOLIO["beta_weighted"],
        )
        b3.metric(
            "Beta noto",
            f"{beta_info['n_positions_with_beta']} / {len(tickers)}",
            help=INDICATOR_HELP_PORTFOLIO["beta_known"],
        )
        st.caption(
            "Esempio lettura: gross 0.65 + beta-weighted 0.78 → portfolio "
            "investito al 65% che si muove come il 78% di SPX (la parte "
            "investita ha beta medio > 1, titoli più volatili della media)."
        )
        if beta_info["default_used_for"]:
            st.caption(
                f"Beta=1.0 fallback per: **{', '.join(beta_info['default_used_for'])}** "
                "(ETF / IPO recenti / esteri illiquidi senza beta Yahoo)."
            )

        st.divider()

        # Correlation pairs
        st.subheader(
            "Correlazioni pairwise (|corr| ≥ 0.7)",
            help=INDICATOR_HELP_PORTFOLIO["corr_pair"],
        )
        if len(tickers) < 2:
            st.caption("Servono almeno 2 posizioni per calcolare correlazioni.")
        else:
            with st.spinner("Fetching daily returns…"):
                returns = cached_returns(tuple(tickers), "6mo")
            corr = compute_correlation_matrix(returns)
            if corr is None:
                st.caption("Dati insufficienti per il calcolo correlazioni "
                           "(servono ≥ 30 giorni di dati comuni).")
            else:
                pairs = find_correlated_pairs(corr, threshold=0.7)
                if not pairs:
                    st.success("Nessuna pair sopra soglia — diversificazione ok.")
                else:
                    pair_rows = [
                        {"A": a, "B": b, "Corr": f"{c:+.2f}"} for a, b, c in pairs[:10]
                    ]
                    st.dataframe(pair_rows, width="stretch", hide_index=True)
                    st.caption(
                        "Pair sopra 0.7 sono effettivamente la stessa scommessa: "
                        "AAPL+MSFT+GOOGL non è 3 posizioni indipendenti su tech, "
                        "è 1 posizione tech con sizing 3x. Rischio camuffato "
                        "da diversificazione."
                    )

        st.divider()

        # -------------------------------------------------------------------
        # Phase 5: Advanced risk metrics — VaR, vol annualized, Kelly per-strategy
        # -------------------------------------------------------------------
        st.subheader(
            "⚗️ Advanced risk metrics (Phase 5)",
            help="Kelly fractional + vol annualized + VaR 95% + expected shortfall. "
                 "Advisory — i hard cap restano attivi.",
        )

        from propicks.domain.risk import (
            portfolio_var_95,
            portfolio_vol_annualized,
            strategy_kelly_from_trades,
        )
        from propicks.io.journal_store import load_journal as _load_journal

        # Weights mark-to-market
        total_mtm = portfolio_market_value(portfolio, prices_map)
        weights_mtm: dict[str, float] = {}
        if total_mtm > 0:
            for tk, pos in positions.items():
                cur_p = prices_map.get(tk) or pos.get("entry_price")
                shares_p = float(pos.get("shares") or 0)
                weights_mtm[tk] = (shares_p * float(cur_p)) / total_mtm

        # returns già scaricati sopra per la correlation matrix
        if "returns" not in dir() or returns is None or returns.empty:
            st.caption("_Returns non disponibili — skip VaR/vol._")
        else:
            vol_info = portfolio_vol_annualized(returns, weights_mtm)
            var_info = portfolio_var_95(returns, weights_mtm, horizon_days=5)

            cols_v = st.columns(4)
            cols_v[0].metric(
                "Vol annualized",
                f"{vol_info['vol_annualized'] * 100:.2f}%",
                help="Volatility portfolio annualizzata (covariance-weighted).",
            )
            var_pct = var_info.get("var_95_pct")
            cols_v[1].metric(
                "VaR 95% (5gg)",
                f"{var_pct:.2f}%" if var_pct is not None else "—",
                help="5° percentile della distribuzione P&L 5gg (bootstrap 500). "
                     "C'è 5% probabilità di perdere almeno questo %.",
            )
            es_pct = var_info.get("expected_shortfall_pct")
            cols_v[2].metric(
                "Expected Shortfall",
                f"{es_pct:.2f}%" if es_pct is not None else "—",
                help="Loss media condizionale al worst 5%.",
            )
            worst = var_info.get("worst_case_pct")
            cols_v[3].metric(
                "Worst case (simulato)",
                f"{worst:.2f}%" if worst is not None else "—",
                help="Peggior scenario osservato nelle 500 simulazioni.",
            )

        # Kelly per-strategy
        st.markdown("**Kelly fractional per strategia (advisory)**")
        trades_journal = _load_journal()
        strategies_in_journal = sorted({
            (t.get("strategy") or "").strip()
            for t in trades_journal
            if t.get("status") == "closed" and t.get("strategy")
        })
        if not strategies_in_journal:
            st.caption("_Nessuna strategia con trade chiusi nel journal._")
        else:
            kelly_rows = []
            for strat in strategies_in_journal:
                k = strategy_kelly_from_trades(trades_journal, strat)
                kelly_rows.append({
                    "Strategia": strat,
                    "n_trades": k["n_trades"],
                    "Win rate": (
                        f"{k['win_rate'] * 100:.1f}%"
                        if k.get("win_rate") is not None else "—"
                    ),
                    "W/L ratio": (
                        f"{k['win_loss_ratio']:.2f}"
                        if k.get("win_loss_ratio") is not None else "—"
                    ),
                    "Kelly %": (
                        f"{k['kelly_pct'] * 100:.2f}%"
                        if k.get("usable") else "—"
                    ),
                    "Status": "✅" if k.get("usable") else f"⚠️ {k.get('reason', '?')[:40]}",
                })
            st.dataframe(kelly_rows, width="stretch", hide_index=True)
            st.caption(
                "**Kelly fractional 25%** da journal storico. Advisory — "
                "mai override dei hard cap. Sotto 15 trade chiusi il Kelly "
                "non è affidabile e viene skippato (status ⚠️)."
            )

        st.divider()
        render_indicator_legend("portfolio")

# ---------------------------------------------------------------------------
# Trade management (trailing + time stop)
# ---------------------------------------------------------------------------
with tab_mgmt:
    st.caption(
        "Mirror di `propicks-portfolio manage [--apply]` + `trail enable|disable`. "
        "Trailing è opt-in per posizione; il time-stop scatta se trade flat "
        "(|P&L| < soglia) da N giorni."
    )
    if not positions:
        st.info("Nessuna posizione aperta.")
    else:
        # Trailing toggle
        st.subheader(
            "Toggle trailing per posizione",
            help=INDICATOR_HELP_PORTFOLIO["trail_toggle"],
        )
        with st.form("trail_form", border=True):
            tcols = st.columns([2, 1, 1])
            t_ticker = tcols[0].selectbox(
                "Ticker", sorted(positions.keys()), key="trail_ticker"
            )
            cur_state = bool(positions[t_ticker].get("trailing_enabled", False))
            t_action = tcols[1].radio(
                "Azione",
                ["enable", "disable"],
                horizontal=True,
                key="trail_action",
                index=0 if not cur_state else 1,
            )
            tcols[2].markdown(
                f"<br/>Stato attuale: **{'ON' if cur_state else 'OFF'}**",
                unsafe_allow_html=True,
            )
            t_submit = st.form_submit_button("Applica toggle", type="primary")
        if t_submit:
            try:
                pos = update_position(
                    portfolio, t_ticker, trailing_enabled=(t_action == "enable")
                )
                verb = "abilitato" if t_action == "enable" else "disabilitato"
                st.toast(
                    f"Trailing {verb} su {t_ticker} (stop {pos['stop_loss']:.2f})",
                    icon="✅",
                )
                st.rerun()
            except ValueError as err:
                st.error(str(err))

        st.divider()

        # Manage parameters + dry-run
        st.subheader("Suggerimenti trailing + time-stop")
        pcols = st.columns(3)
        atr_mult = pcols[0].number_input(
            "ATR multiplier (trailing)",
            min_value=0.5,
            max_value=5.0,
            value=float(DEFAULT_TRAILING_ATR_MULT),
            step=0.1,
            key="mgmt_atr_mult",
            help=INDICATOR_HELP_PORTFOLIO["atr_mult"],
        )
        time_stop = pcols[1].number_input(
            "Time stop (giorni)",
            min_value=5,
            max_value=120,
            value=int(DEFAULT_TIME_STOP_DAYS),
            step=1,
            key="mgmt_time_stop",
            help=INDICATOR_HELP_PORTFOLIO["time_stop"],
        )
        flat_threshold = pcols[2].number_input(
            "Flat threshold (|P&L| <)",
            min_value=0.005,
            max_value=0.10,
            value=float(DEFAULT_FLAT_THRESHOLD_PCT),
            step=0.005,
            format="%.3f",
            key="mgmt_flat_threshold",
            help=INDICATOR_HELP_PORTFOLIO["flat_threshold"],
        )

        run_btn = st.button("Calcola suggerimenti", type="primary", key="mgmt_run")
        if run_btn:
            tickers = sorted(positions.keys())
            with st.status("Calcolo suggerimenti trailing…", expanded=False) as _mgmt_status:
                st.write(f"Fetch prezzi spot per {len(tickers)} ticker")
                prices_map = cached_current_prices(tuple(tickers))
                st.write("Calcolo ATR per ogni ticker")
                atrs = {t: cached_current_atr(t) for t in tickers}
                _mgmt_status.update(label="Dati pronti", state="complete")

            suggestions: list[tuple[str, dict, dict, float]] = []
            for ticker in tickers:
                pos = positions[ticker]
                cur_price = prices_map.get(ticker)
                cur_atr = atrs.get(ticker)
                if cur_price is None:
                    st.warning(f"{ticker}: prezzo non disponibile, skip")
                    continue
                if cur_atr is None:
                    st.warning(f"{ticker}: ATR non disponibile, skip")
                    continue
                sug = suggest_stop_update(
                    position=pos,
                    current_price=cur_price,
                    current_atr=cur_atr,
                    atr_mult=atr_mult,
                    max_days_flat=int(time_stop),
                    flat_threshold_pct=flat_threshold,
                )
                suggestions.append((ticker, pos, sug, cur_price))

            st.session_state["mgmt_suggestions"] = suggestions

        suggestions = st.session_state.get("mgmt_suggestions", [])
        if suggestions:
            rows = []
            for ticker, pos, sug, cur in suggestions:
                flags = []
                if sug["stop_changed"]:
                    flags.append(f"trail→{sug['new_stop']:.2f}")
                if sug["time_stop_triggered"]:
                    flags.append("TIME-STOP")
                if not flags:
                    flags.append("hold")
                rows.append({
                    "Ticker": ticker,
                    "Entry": f"{pos['entry_price']:.2f}",
                    "Current": f"{cur:.2f}",
                    "P&L%": f"{(cur - pos['entry_price']) / pos['entry_price'] * 100:+.2f}%",
                    "Stop": f"{pos['stop_loss']:.2f}",
                    "Highest": f"{sug['highest_price']:.2f}",
                    "Trail?": "Y" if pos.get("trailing_enabled") else "N",
                    "Action": ", ".join(flags),
                })
            st.dataframe(rows, width="stretch", hide_index=True)
            st.caption(
                "Colonne: **Highest** = max post-entry (base trailing) · "
                "**Trail?** = Y/N opt-in per posizione · "
                "**Action** = `trail→<new_stop>` se ratchet-up, `TIME-STOP` "
                "se flat da N giorni, `hold` altrimenti."
            )

            with st.expander("Rationale per ticker", expanded=False):
                for ticker, _pos, sug, _cur in suggestions:
                    if sug["rationale"]:
                        st.markdown(f"**{ticker}**")
                        for r in sug["rationale"]:
                            st.markdown(f"- {r}")

            n_changes = sum(
                1 for _, _, s, _ in suggestions
                if s["stop_changed"] or s["highest_price"]
            )
            apply_col, info_col = st.columns([1, 3])
            apply_btn = apply_col.button(
                "Applica modifiche a portfolio.json",
                type="primary",
                key="mgmt_apply",
                disabled=(n_changes == 0),
            )
            info_col.caption(
                "Scrive `stop_loss` (se trailing si è mosso) e `highest_price_since_entry` "
                "su tutte le posizioni con prezzo disponibile. Le posizioni TIME-STOP "
                "vanno chiuse manualmente dal tab **Chiudi posizione** + Journal."
            )
            if apply_btn:
                applied = 0
                errors = []
                for ticker, _pos, sug, _cur in suggestions:
                    kwargs: dict = {"highest_price": sug["highest_price"]}
                    if sug["stop_changed"]:
                        kwargs["stop_loss"] = sug["new_stop"]
                    try:
                        update_position(portfolio, ticker, **kwargs)
                        applied += 1
                    except ValueError as err:
                        errors.append(f"{ticker}: {err}")
                st.session_state.pop("mgmt_suggestions", None)
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    st.toast(
                        f"Aggiornate {applied}/{len(suggestions)} posizioni",
                        icon="✅",
                    )
                    st.rerun()

            time_stops = [t for t, _, s, _ in suggestions if s["time_stop_triggered"]]
            if time_stops:
                st.warning(
                    f"TIME-STOP triggered su: **{', '.join(time_stops)}**. "
                    "Valuta chiusura manuale (tab **Chiudi posizione** + Journal close)."
                )

        # ─── Trailing alert preview — checking required ─────────────────
        # Lista posizioni dove trailing-recon è vicino al prezzo corrente
        # (entro 1xATR). Priority "tomorrow check": se prezzo continua il
        # trend, trailing scatterà entro 1-3gg trading.
        st.divider()
        st.subheader("🚨 Trailing watch — checking required")
        st.caption(
            "Posizioni dove trailing-stop ricostruito è entro 1×ATR dal prezzo "
            "corrente. Sono i candidati più probabili a stop-out nei prossimi "
            "1-3gg trading se il trend si inverte. Sort by distance asc."
        )

        from propicks.config import ATR_PERIOD
        from propicks.domain.indicators import compute_atr
        from propicks.domain.trade_mgmt import compute_trailing_stop
        from propicks.market.yfinance_client import (
            DataUnavailable as _Du, download_history as _dh,
        )

        atr_mult_watch = float(st.session_state.get("mgmt_atr_mult") or DEFAULT_TRAILING_ATR_MULT)

        watch_rows: list[dict] = []
        with st.spinner(f"Compute trailing stops per {len(positions)} posizioni…"):
            for tk_w, pos_w in positions.items():
                cur_p = prices_map.get(tk_w) if 'prices_map' in dir() else None
                if cur_p is None:
                    cur_p = cached_current_prices((tk_w,)).get(tk_w)
                if cur_p is None:
                    continue
                try:
                    hist_w = _dh(tk_w)
                except _Du:
                    continue
                if hist_w is None or len(hist_w) < ATR_PERIOD + 5:
                    continue
                atr_series = compute_atr(
                    hist_w["High"], hist_w["Low"], hist_w["Close"], ATR_PERIOD,
                )
                cur_atr_w = float(atr_series.iloc[-1]) if len(atr_series) else 0
                if cur_atr_w <= 0:
                    continue

                entry_w = float(pos_w.get("entry_price", 0))
                stop_w = float(pos_w.get("stop_loss", 0))
                highest_w = float(
                    pos_w.get("highest_price_since_entry") or max(entry_w, cur_p)
                )
                # Compute proposed trailing
                proposed_trail = compute_trailing_stop(
                    entry_price=entry_w,
                    highest_price_since_entry=max(highest_w, cur_p),
                    current_atr=cur_atr_w,
                    current_stop=stop_w,
                    atr_mult=atr_mult_watch,
                )
                # Distance current → trailing recon, in ATR multiples
                dist_eur = cur_p - proposed_trail
                dist_atr = dist_eur / cur_atr_w if cur_atr_w > 0 else 99
                dist_pct = dist_eur / cur_p * 100 if cur_p > 0 else 0
                trailing_active = bool(pos_w.get("trailing_enabled"))

                watch_rows.append({
                    "ticker": tk_w,
                    "current": cur_p,
                    "trailing_recon": proposed_trail,
                    "current_stop": stop_w,
                    "dist_eur": dist_eur,
                    "dist_atr": dist_atr,
                    "dist_pct": dist_pct,
                    "trailing_active": trailing_active,
                    "entry": entry_w,
                    "atr": cur_atr_w,
                })

        if not watch_rows:
            st.caption("_Nessuna posizione analizzabile (storia / ATR insufficienti)._")
        else:
            # Sort distance asc — closest first
            watch_rows.sort(key=lambda r: r["dist_atr"])

            # Build table
            display_rows = [
                {
                    "Ticker": r["ticker"],
                    "Trail?": "✅" if r["trailing_active"] else "—",
                    "Current": f"{r['current']:.2f}",
                    "Trail recon": f"{r['trailing_recon']:.2f}",
                    "Stop attuale": f"{r['current_stop']:.2f}",
                    "Δ ATR": round(r["dist_atr"], 2),
                    "Δ %": round(r["dist_pct"], 2),
                    "Priority": (
                        "🔴 IMMEDIATE" if r["dist_atr"] < 0.5
                        else "🟠 1-2gg" if r["dist_atr"] < 1.0
                        else "🟡 3-5gg" if r["dist_atr"] < 2.0
                        else "🟢 safe"
                    ),
                }
                for r in watch_rows
            ]
            st.dataframe(
                display_rows,
                width="stretch",
                hide_index=True,
                column_config={
                    "Δ ATR": st.column_config.ProgressColumn(
                        format="%.2f", min_value=0.0, max_value=3.0,
                        help="Multipli di ATR tra current e trailing-recon. "
                             "<0.5 = stop-out immediate possibile.",
                    ),
                    "Δ %": st.column_config.NumberColumn(format="%+.2f%%"),
                },
            )
            st.caption(
                "**Δ ATR < 0.5** = trailing-stop a portata di un giorno volatile · "
                "**0.5-1.0** = 1-2gg trading se trend inverte · "
                "**1.0-2.0** = 3-5gg cushion · "
                "**> 2.0** = safe distance. "
                "Trail? ✅ = trailing attivo, — = trailing OFF (default per contrarian/thematic)."
            )

            # Alert specifico immediate
            immediate = [r for r in watch_rows if r["dist_atr"] < 0.5]
            if immediate:
                st.error(
                    "🔴 **CHECK NOW**: " + ", ".join(r["ticker"] for r in immediate) +
                    " — trailing-recon entro 0.5×ATR dal prezzo corrente. "
                    "Decidi: enable trailing per protect, OR tighten stop manuale, "
                    "OR close partial."
                )

        # ─── Per-position evolution chart ─────────────────────────────────
        # Price history dall'entry_date + entry/stop/target hline + reconstructed
        # trailing stop (rolling highest - atr_mult × ATR) per visualizzare se
        # il trailing avrebbe protetto profit storicamente.
        st.divider()
        st.subheader("📈 Per-position evolution")
        st.caption(
            "Visualizza price history dall'entry_date + livelli (entry/stop/target) + "
            "trailing stop ricostruito. Utile per capire ex-post se il trailing "
            "avrebbe colpito o lasciato corsa."
        )

        sel_ticker = st.selectbox(
            "Posizione", options=sorted(positions.keys()),
            key="mgmt_evo_ticker",
        )
        if sel_ticker:
            pos = positions[sel_ticker]
            entry_date_str = pos.get("entry_date")
            entry_price = float(pos.get("entry_price", 0))
            cur_stop = float(pos.get("stop_loss", 0))
            cur_target = pos.get("target")

            if not entry_date_str:
                st.info(f"{sel_ticker}: entry_date mancante, skip evolution chart.")
            else:
                from datetime import datetime as _dt

                from propicks.config import ATR_PERIOD
                from propicks.domain.indicators import compute_atr
                from propicks.market.yfinance_client import (
                    DataUnavailable,
                    download_history,
                )

                try:
                    hist = download_history(sel_ticker)
                except DataUnavailable as err:
                    st.error(f"{sel_ticker}: {err}")
                    hist = None

                if hist is not None and not hist.empty:
                    try:
                        entry_dt = _dt.strptime(entry_date_str, "%Y-%m-%d")
                    except ValueError:
                        entry_dt = None

                    if entry_dt is not None:
                        # Slice history dall'entry_date
                        hist_index = hist.index
                        if hasattr(hist_index, "tz_localize") and hist_index.tz is not None:
                            hist = hist.tz_localize(None)
                        hist_post = hist[hist.index >= entry_dt]
                    else:
                        hist_post = hist.tail(120)

                    if len(hist_post) < 3:
                        st.info(
                            f"{sel_ticker}: storia post-entry insufficiente ({len(hist_post)} bar). "
                            "Riprova tra qualche giorno di trading."
                        )
                    else:
                        # Trailing stop ricostruito: max(entry - initial_risk,
                        # rolling_highest - atr_mult × ATR), ratchet-up
                        atr_mult = float(st.session_state.get("mgmt_atr_mult") or DEFAULT_TRAILING_ATR_MULT)
                        atr = compute_atr(
                            hist_post["High"], hist_post["Low"], hist_post["Close"],
                            ATR_PERIOD,
                        )
                        rolling_high = hist_post["High"].cummax()
                        initial_stop = cur_stop  # fallback se non si conosce stop iniziale
                        # Activation: highest > entry + (entry - initial_stop)
                        initial_risk = entry_price - initial_stop
                        activation_threshold = entry_price + initial_risk

                        trailing_recon = []
                        running_stop = initial_stop
                        for i, (idx, row) in enumerate(hist_post.iterrows()):
                            highest_so_far = rolling_high.iloc[i]
                            cur_atr = atr.iloc[i] if not (
                                atr.iloc[i] != atr.iloc[i]  # NaN check
                            ) else 0
                            if highest_so_far >= activation_threshold and cur_atr > 0:
                                proposed = highest_so_far - atr_mult * cur_atr
                                running_stop = max(running_stop, proposed)
                            trailing_recon.append(running_stop)

                        import plotly.graph_objects as go
                        fig_evo = go.Figure()

                        # Price line
                        fig_evo.add_trace(go.Scatter(
                            x=hist_post.index, y=hist_post["Close"],
                            mode="lines", name="Close",
                            line=dict(color="#3b82f6", width=2),
                            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Close %{y:.2f}<extra></extra>",
                        ))

                        # Rolling highest
                        fig_evo.add_trace(go.Scatter(
                            x=hist_post.index, y=rolling_high,
                            mode="lines", name="Rolling highest",
                            line=dict(color="#10b981", width=1, dash="dot"),
                            hovertemplate="Highest %{y:.2f}<extra></extra>",
                        ))

                        # Trailing stop ricostruito
                        fig_evo.add_trace(go.Scatter(
                            x=hist_post.index, y=trailing_recon,
                            mode="lines", name=f"Trailing stop ({atr_mult}×ATR)",
                            line=dict(color="#f97316", width=1.5, dash="dash"),
                            hovertemplate="Trailing %{y:.2f}<extra></extra>",
                        ))

                        # Hlines: entry, current stop, target
                        fig_evo.add_hline(
                            y=entry_price, line_color="#94a3b8", line_dash="solid",
                            line_width=1, annotation_text=f"Entry {entry_price:.2f}",
                            annotation_position="right",
                        )
                        fig_evo.add_hline(
                            y=cur_stop, line_color="#dc2626", line_dash="solid",
                            line_width=1, annotation_text=f"Stop {cur_stop:.2f}",
                            annotation_position="right",
                        )
                        if cur_target:
                            fig_evo.add_hline(
                                y=float(cur_target), line_color="#16a34a", line_dash="solid",
                                line_width=1, annotation_text=f"Target {cur_target:.2f}",
                                annotation_position="right",
                            )

                        # Current price marker
                        last_price = float(hist_post["Close"].iloc[-1])
                        fig_evo.add_trace(go.Scatter(
                            x=[hist_post.index[-1]], y=[last_price],
                            mode="markers",
                            marker=dict(color="#3b82f6", size=12, symbol="circle",
                                        line=dict(color="white", width=2)),
                            name=f"Current {last_price:.2f}",
                            hovertemplate=f"Current {last_price:.2f}<extra></extra>",
                        ))

                        fig_evo.update_layout(
                            title=dict(
                                text=f"{sel_ticker} — entry {entry_date_str}",
                                x=0.5, xanchor="center", font=dict(size=13),
                            ),
                            xaxis_title="", yaxis_title="Price",
                            height=400, hovermode="x unified",
                            margin=dict(l=20, r=20, t=50, b=20),
                            legend=dict(orientation="h", yanchor="bottom",
                                        y=1.0, xanchor="right", x=1.0),
                        )
                        st.plotly_chart(fig_evo, width="stretch")

                        # Quick stats sotto il chart
                        max_high = float(rolling_high.iloc[-1])
                        pnl_pct_now = (last_price - entry_price) / entry_price * 100
                        max_pnl_pct = (max_high - entry_price) / entry_price * 100
                        days_held = (hist_post.index[-1] - hist_post.index[0]).days
                        recon_stop_now = trailing_recon[-1]

                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Days held", days_held)
                        c2.metric("P&L now", f"{pnl_pct_now:+.2f}%")
                        c3.metric("Max P&L (rolling high)", f"{max_pnl_pct:+.2f}%")
                        c4.metric(
                            "Recon trailing now",
                            f"{recon_stop_now:.2f}",
                            delta=f"vs current {cur_stop:.2f}",
                            delta_color=("normal" if abs(recon_stop_now - cur_stop) < 0.01 else "inverse"),
                        )
                        if abs(recon_stop_now - cur_stop) > 0.01:
                            if recon_stop_now > cur_stop:
                                st.caption(
                                    f"💡 Trailing ricostruito (€{recon_stop_now:.2f}) > stop corrente "
                                    f"(€{cur_stop:.2f}): considera applicare trailing per proteggere profit."
                                )
                            else:
                                st.caption(
                                    f"ℹ️ Stop corrente (€{cur_stop:.2f}) più stretto del trailing "
                                    f"ricostruito (€{recon_stop_now:.2f}) — stop manuale aggressivo OK."
                                )

    st.divider()
    render_indicator_legend("portfolio")

# ---------------------------------------------------------------------------
# Size calculator
# ---------------------------------------------------------------------------
with tab_size:
    st.caption(
        "Calcola shares da comprare dato entry, stop e convinzione. "
        "Cap per asset type: 15% stock / 20% ETF. Bucket contrarian: 8% size, "
        "12% loss, max 3 pos, 20% aggregate. Non modifica il portfolio."
    )
    with st.form("size_form", border=True):
        cols = st.columns([2, 1, 1, 1, 1, 1, 1])
        ticker = cols[0].text_input("Ticker", placeholder="AAPL / XLK / XDWT.MI")
        entry = cols[1].number_input("Entry price", min_value=0.01, step=0.01, format="%.2f")
        stop = cols[2].number_input("Stop price", min_value=0.01, step=0.01, format="%.2f")
        score_claude = cols[3].slider("Score Claude", 0, 10, 7)
        score_tech = cols[4].slider("Score tech", 0, 100, 70)
        asset_override = cols[5].selectbox(
            "Asset type",
            options=("auto", "STOCK", "SECTOR_ETF", "THEMATIC_ETF"),
            index=0,
        )
        bucket_choice = cols[6].selectbox(
            "Bucket",
            options=("momentum", "contrarian", "etf_rotation", "thematic"),
            index=0,
            help=(
                "momentum → 15% / 8% loss · "
                "contrarian → 8% / 12% loss / max 3 pos / 20% aggregate · "
                "etf_rotation → 20% / 8% loss · "
                "thematic → 15% / 10% loss / max 2 pos / 25% parent-aggregate cap."
            ),
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
            strategy_bucket=bucket_choice,  # type: ignore[arg-type]
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
STRATEGIES = (
    "",
    "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane",  # momentum
    "ETF_Rotation",
    "Contrarian",
    "Thematic",
    "Altro",
)

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

        cols3 = st.columns([1, 3])
        a_currency = cols3[0].selectbox(
            "Currency",
            options=("auto", "EUR", "USD", "GBP", "CHF", "JPY"),
            index=0,
            key="add_currency",
            help=(
                "auto = inferita da ticker suffix (.MI→EUR, .L→GBP, no suffix→USD). "
                "Override manuale se necessario (es. ETF UCITS .L quotato USD)."
            ),
        )
        a_catalyst = cols3[1].text_input(
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
                    currency=(None if a_currency == "auto" else a_currency),
                )
                st.toast(
                    f"{a_ticker.upper()}: {pos['shares']} @ {pos['entry_price']:.2f} "
                    f"{pos.get('currency', 'EUR')}",
                    icon="✅",
                )
                st.rerun()
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Increase position (pyramiding)
# ---------------------------------------------------------------------------
with tab_increase:
    st.caption(
        "Incrementa una posizione aperta con **entry medio pesato**. "
        "Addebita solo la tranche aggiunta (no rimbalzo cash), non resetta "
        "`entry_date` (continuità time-stop) e **non tocca il journal**. "
        "Tutti i gate hard riapplicati sulla nuova size."
    )
    if not positions:
        st.info("Nessuna posizione aperta da incrementare.")
    else:
        i_ticker = st.selectbox(
            "Ticker", sorted(positions.keys()), key="inc_ticker"
        )
        ip = positions[i_ticker]
        ip_sh = int(ip.get("shares") or 0)
        ip_entry = float(ip.get("entry_price") or 0)
        ip_stop = float(ip.get("stop_loss") or 0)
        ip_target = float(ip.get("target") or 0)
        ip_target_str = f"{ip_target:.2f}" if ip_target else "—"
        st.caption(
            f"Posizione corrente — **{ip_sh}** azioni @ entry "
            f"**{ip_entry:.2f}** · stop {ip_stop:.2f} · "
            f"target {ip_target_str}. "
            "Mediando al rialzo il rischio% a stop fisso cresce: se il vecchio "
            "stop sfora il loss cap, passa un nuovo stop (idem target)."
        )

        with st.form("increase_form", border=True):
            cols = st.columns([1, 1, 1, 1])
            i_shares = cols[0].number_input(
                "Shares da aggiungere",
                min_value=1,
                step=1,
                key=f"inc_shares_{i_ticker}",
            )
            i_price = cols[1].number_input(
                "Prezzo tranche",
                min_value=0.01,
                step=0.01,
                format="%.2f",
                key=f"inc_price_{i_ticker}",
            )
            i_stop = cols[2].number_input(
                "Nuovo stop (0 = invariato)",
                min_value=0.0,
                value=0.0,
                step=0.01,
                format="%.2f",
                key=f"inc_stop_{i_ticker}",
            )
            i_target = cols[3].number_input(
                "Nuovo target (0 = invariato)",
                min_value=0.0,
                value=0.0,
                step=0.01,
                format="%.2f",
                key=f"inc_target_{i_ticker}",
            )
            i_ignore_earn = st.checkbox(
                "Bypassa earnings hard gate (add intentional)",
                value=False,
                key=f"inc_ign_{i_ticker}",
            )

            # Preview entry medio
            if i_shares and i_price:
                avg = (ip_sh * ip_entry + int(i_shares) * i_price) / (
                    ip_sh + int(i_shares)
                )
                st.caption(
                    f"→ Post-incremento: **{ip_sh + int(i_shares)}** azioni @ "
                    f"entry medio **{avg:.2f}** "
                    f"(da {ip_entry:.2f}), tranche {int(i_shares) * i_price:.2f}."
                )
            submitted = st.form_submit_button("Incrementa", type="primary")

        if submitted:
            try:
                pos = increase_position(
                    portfolio,
                    i_ticker,
                    add_shares=int(i_shares),
                    add_price=i_price,
                    new_stop=(i_stop or None),
                    new_target=(i_target or None),
                    ignore_earnings=i_ignore_earn,
                )
                tgt = f"{pos['target']:.2f}" if pos.get("target") else "—"
                st.toast(
                    f"{i_ticker}: {pos['shares']} @ entry medio "
                    f"{pos['entry_price']:.2f} · stop {pos['stop_loss']:.2f} · "
                    f"target {tgt}",
                    icon="✅",
                )
                st.info(
                    "Journal non toccato — se tracci il trade, annota l'add a "
                    "mano (trade journal resta sull'apertura originale)."
                )
                st.rerun()
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Update position
# ---------------------------------------------------------------------------
with tab_update:
    if not positions:
        st.info("Nessuna posizione aperta.")
    else:
        # Selectbox fuori dal form: il pre-fill stop/target reagisce al ticker
        u_ticker = st.selectbox("Ticker", sorted(positions.keys()), key="upd_ticker")
        cur_pos = positions[u_ticker]
        cur_stop = float(cur_pos.get("stop_loss") or 0)
        cur_target = float(cur_pos.get("target") or 0)
        st.caption(
            f"Valori correnti — stop **{cur_stop:.2f}** · target "
            f"**{cur_target:.2f}** · entry {cur_pos['entry_price']:.2f}. "
            "Modifica un campo e premi **Aggiorna**; i campi invariati "
            "vengono ignorati."
        )

        with st.form("update_form", border=True):
            cols = st.columns([1, 1])
            # Key per-ticker: cambiando selectbox si crea un widget fresco con
            # il pre-fill corretto, evitando la session_state stickiness di
            # una key fissa tipo `upd_stop`.
            u_stop = cols[0].number_input(
                "Nuovo stop",
                min_value=0.01,
                value=cur_stop if cur_stop > 0 else 0.01,
                step=0.01,
                format="%.2f",
                key=f"upd_stop_{u_ticker}",
            )
            u_target = cols[1].number_input(
                "Nuovo target",
                min_value=0.01,
                value=cur_target if cur_target > 0 else 0.01,
                step=0.01,
                format="%.2f",
                key=f"upd_target_{u_ticker}",
            )
            submitted = st.form_submit_button("Aggiorna", type="primary")

        if submitted:
            # Rileva cambi via confronto con tolleranza (centesimo). Un campo
            # uguale al corrente non viene toccato.
            EPS = 0.005
            stop_changed = abs(u_stop - cur_stop) > EPS
            target_changed = abs(u_target - cur_target) > EPS
            if not (stop_changed or target_changed):
                st.info("Nessuna modifica rilevata.")
            else:
                try:
                    new = update_position(
                        portfolio=portfolio,
                        ticker=u_ticker,
                        stop_loss=u_stop if stop_changed else None,
                        target=u_target if target_changed else None,
                    )
                    target_str = f"{new['target']:.2f}" if new.get("target") else "—"
                    st.toast(
                        f"{u_ticker}: stop {new['stop_loss']:.2f} · target {target_str}",
                        icon="✅",
                    )
                    st.rerun()
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
                refund = fmt_eur(removed["shares"] * removed["entry_price"])
                st.toast(f"{r_ticker} rimosso · refund {refund}", icon="✅")
                st.rerun()
            except ValueError as err:
                st.error(str(err))

# ---------------------------------------------------------------------------
# Tab: Broker import (riconciliazione CSV/paste vs portfolio)
# ---------------------------------------------------------------------------
with tab_broker:
    from propicks.io.broker_import import (
        apply_broker_position,
        apply_drift_update,
        parse_broker_paste,
        reconcile_with_portfolio,
        remove_orphan_position,
    )

    st.caption(
        "Incolla qui l'export del broker (Fineco / Directa / Degiro EU "
        "'Portafoglio di sintesi'). Il sistema parsa, confronta col portfolio "
        "e propone azioni per ogni discrepanza."
    )

    bi_raw = st.text_area(
        "Paste broker statement (TSV / Excel paste)",
        height=200,
        placeholder=(
            "Titolo\tISIN\tSimbolo\tMercato\tStrumento\tValuta\tQuantità\t"
            "P.zo medio di carico\t..."
        ),
        key="broker_raw",
    )

    if bi_raw and bi_raw.strip():
        broker_positions, warns = parse_broker_paste(bi_raw)

        if warns:
            for w in warns:
                st.caption(f"⚠ {w}")

        if not broker_positions:
            st.error("Nessuna posizione parsata. Verifica il formato.")
        else:
            st.success(
                f"✓ Parsate **{len(broker_positions)}** posizioni dal broker."
            )

            # Reconcile
            diff = reconcile_with_portfolio(broker_positions, portfolio)

            # KPI counts
            kc1, kc2, kc3, kc4 = st.columns(4)
            kc1.metric("✅ In sync", len(diff["in_sync"]))
            kc2.metric("⚠ Drift", len(diff["drift"]))
            kc3.metric("📥 Solo broker", len(diff["only_broker"]))
            kc4.metric("📤 Solo portfolio", len(diff["only_portfolio"]))

            st.divider()

            # ── In sync (read-only) ─────────────────────────────────────
            if diff["in_sync"]:
                with st.expander(
                    f"✅ In sync ({len(diff['in_sync'])}) — niente da fare",
                    expanded=False,
                ):
                    rows_sync = [
                        {
                            "Ticker": tk,
                            "Shares": d["broker"].shares,
                            "Entry": d["broker"].entry_price,
                            "ISIN": d["broker"].isin or "—",
                        }
                        for tk, d in diff["in_sync"].items()
                    ]
                    st.dataframe(rows_sync, width="stretch", hide_index=True)

            # ── Drift (action required) ─────────────────────────────────
            if diff["drift"]:
                st.subheader(f"⚠ Drift ({len(diff['drift'])}) — shares/entry mismatch")
                for tk, d in diff["drift"].items():
                    b = d["broker"]
                    p = d["portfolio"]
                    cc1, cc2, cc3 = st.columns([2, 4, 1])
                    cc1.markdown(f"**{tk}**")
                    cc2.caption(d["drift_msg"])
                    if cc3.button(
                        "Apply drift",
                        key=f"bi_drift_{tk}",
                        type="primary",
                    ):
                        try:
                            apply_drift_update(tk, b.shares, b.entry_price)
                            st.toast(
                                f"✓ {tk}: {p['shares']} → {b.shares} shares · "
                                f"{p['entry_price']} → {b.entry_price} entry",
                                icon="✅",
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))

            # ── Only broker (import as new) ─────────────────────────────
            if diff["only_broker"]:
                st.subheader(
                    f"📥 Solo broker ({len(diff['only_broker'])}) — "
                    "presenti nel broker ma NON in portfolio"
                )
                bi_strategy_default = st.text_input(
                    "Strategy tag default (override per ogni import)",
                    value="BrokerImport",
                    key="bi_strat_default",
                    help="Lascia 'BrokerImport' per inferire automaticamente "
                         "(Thematic se in THEMATIC_ETFS, ETF_Rotation se ETF, "
                         "Altro per stock).",
                )
                bi_stop_pct = st.slider(
                    "Default stop loss % (entry × (1 - X))",
                    min_value=0.05, max_value=0.20, value=0.10, step=0.01,
                    key="bi_stop_pct",
                    help="10% = conservativo per ETF/long-term holding. "
                         "Per momentum stock 8% standard.",
                )

                for b in diff["only_broker"]:
                    bcol1, bcol2, bcol3, bcol4 = st.columns([1, 1, 1, 1])
                    bcol1.markdown(f"**{b.ticker}**")
                    bcol2.caption(
                        f"qty {b.shares:.0f} @ {b.entry_price:.4f} · "
                        f"{b.strumento or 'n/a'}"
                    )
                    bcol3.caption(b.titolo[:40] if b.titolo else "—")
                    if bcol4.button(
                        "Import",
                        key=f"bi_import_{b.ticker}",
                        type="primary",
                    ):
                        try:
                            override_strat = (
                                None if bi_strategy_default == "BrokerImport"
                                else bi_strategy_default
                            )
                            res = apply_broker_position(
                                b,
                                strategy=override_strat,
                                stop_loss_pct=bi_stop_pct,
                            )
                            st.toast(
                                f"✓ {b.ticker} importato — trade #{res['trade'].get('id')}",
                                icon="✅",
                            )
                            for w in res.get("warnings", []):
                                st.warning(w)
                            st.rerun()
                        except ValueError as exc:
                            st.error(f"{b.ticker}: {exc}")

            # ── Only portfolio (orphan, propose remove) ─────────────────
            if diff["only_portfolio"]:
                st.subheader(
                    f"📤 Solo portfolio ({len(diff['only_portfolio'])}) — "
                    "in portfolio ma NON nel broker (probabile chiusa)"
                )
                st.caption(
                    "⚠ **Remove non chiude il trade journal**: solo data-entry "
                    "correction. Se la trade è realmente chiusa (broker l'ha "
                    "venduta), apri Page 9 Journal → tab Close trade per "
                    "registrare exit_price + exit_date corretti, poi torna "
                    "qui per cleanup."
                )
                for tk, p in diff["only_portfolio"]:
                    rcol1, rcol2, rcol3 = st.columns([2, 3, 1])
                    rcol1.markdown(f"**{tk}**")
                    rcol2.caption(
                        f"qty {p['shares']} @ {p['entry_price']:.2f} · "
                        f"strategy {p.get('strategy') or '—'}"
                    )
                    if rcol3.button(
                        "🗑 Remove",
                        key=f"bi_orphan_{tk}",
                        type="secondary",
                    ):
                        try:
                            remove_orphan_position(tk)
                            st.toast(
                                f"✓ {tk} rimosso da portfolio "
                                "(journal NON chiuso — chiudi manual via Page 9)",
                                icon="🗑",
                            )
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
            elif not diff["drift"] and not diff["only_broker"]:
                st.success("🎯 Portfolio perfettamente allineato col broker.")
    else:
        st.info(
            "Incolla l'export broker per iniziare. **Formato atteso**: tab-separated "
            "con header `Titolo / ISIN / Simbolo / Mercato / Strumento / Valuta / "
            "Quantità / P.zo medio di carico / ...`"
        )
