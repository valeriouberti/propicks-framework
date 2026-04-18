"""Portfolio management — size calculator, add/update/remove, risk, trade mgmt.

Equivalent UI di:
    propicks-portfolio status / risk
    propicks-portfolio size
    propicks-portfolio add / update / remove
    propicks-portfolio manage [--apply] / trail enable|disable
"""

from __future__ import annotations

import streamlit as st

from propicks.config import CAPITAL, MAX_LOSS_WEEKLY_PCT, MIN_CASH_RESERVE_PCT
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
from propicks.domain.etf_universe import get_asset_type
from propicks.domain.exposure import (
    compute_beta_weighted_exposure,
    compute_concentration_warnings,
    compute_correlation_matrix,
    compute_sector_exposure,
    find_correlated_pairs,
)
from propicks.domain.sizing import calculate_position_size, portfolio_value
from propicks.domain.stock_rs import YF_SECTOR_TO_KEY
from propicks.domain.trade_mgmt import (
    DEFAULT_FLAT_THRESHOLD_PCT,
    DEFAULT_TIME_STOP_DAYS,
    DEFAULT_TRAILING_ATR_MULT,
    suggest_stop_update,
)
from propicks.io.portfolio_store import (
    add_position,
    remove_position,
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
# Tabs: Risk | Mgmt | Size | Add | Update | Remove
# ---------------------------------------------------------------------------
tab_risk, tab_mgmt, tab_size, tab_add, tab_update, tab_remove = st.tabs([
    "Rischio & esposizione",
    "Trade management",
    "Size calculator",
    "Apri posizione",
    "Aggiorna stop/target",
    "Chiudi posizione",
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
            risk_eur = (entry - stop) * shares
            risk_pct = risk_eur / total if total else 0.0
            risk_sum += risk_eur
            risk_rows.append({
                "Ticker": ticker,
                "Shares": shares,
                "Entry": f"{entry:.2f}",
                "Stop": f"{stop:.2f}",
                "Rischio €": fmt_eur(risk_eur),
                "% capitale": fmt_pct(risk_pct),
            })
        st.subheader("Rischio per posizione (a stop)")
        st.dataframe(risk_rows, width="stretch", hide_index=True)
        st.caption(
            "**Rischio €** = `(entry - stop) x shares` · "
            "**% capitale** = rischio / portfolio_value. "
            "Apri la legenda in fondo per il dettaglio."
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

        # Sector exposure
        st.subheader(
            "Concentrazione settoriale",
            help=INDICATOR_HELP_PORTFOLIO["sector_exposure"],
        )
        with st.spinner("Fetching sector / beta…"):
            sector_yf = cached_ticker_sectors(tuple(tickers))
            betas = cached_ticker_betas(tuple(tickers))
        sector_key_map = {
            t: (YF_SECTOR_TO_KEY.get(s) if s else None) for t, s in sector_yf.items()
        }
        sector_exp = compute_sector_exposure(positions, prices_map, sector_key_map, total)
        if sector_exp:
            sector_rows = sorted(
                ([{"Settore": k, "Esposizione": fmt_pct(v), "_pct": v}
                  for k, v in sector_exp.items()]),
                key=lambda r: r["_pct"],
                reverse=True,
            )
            st.dataframe(
                [{k: v for k, v in r.items() if k != "_pct"} for r in sector_rows],
                width="stretch",
                hide_index=True,
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
        beta_info = compute_beta_weighted_exposure(positions, prices_map, betas, total)
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
                st.success(
                    f"Trailing {'abilitato' if t_action == 'enable' else 'disabilitato'} "
                    f"su {t_ticker} (stop attuale {pos['stop_loss']:.2f})."
                )
                st.caption("Ricarica la pagina per refresh.")
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
            with st.spinner("Fetching prezzi e ATR…"):
                prices_map = cached_current_prices(tuple(tickers))
                atrs = {t: cached_current_atr(t) for t in tickers}

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
                st.success(f"Aggiornate {applied}/{len(suggestions)} posizioni.")
                for e in errors:
                    st.error(e)
                # Invalidate cached suggestions after apply
                st.session_state.pop("mgmt_suggestions", None)

            time_stops = [t for t, _, s, _ in suggestions if s["time_stop_triggered"]]
            if time_stops:
                st.warning(
                    f"TIME-STOP triggered su: **{', '.join(time_stops)}**. "
                    "Valuta chiusura manuale (tab **Chiudi posizione** + Journal close)."
                )

    st.divider()
    render_indicator_legend("portfolio")

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
                    f"{pos['shares']} x {pos['entry_price']:.2f} = "
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
