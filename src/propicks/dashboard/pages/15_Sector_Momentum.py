"""Sector-Filtered Momentum (SFM) — strategia ibrida top-down + bottom-up.

Equivalent UI di ``propicks-sector-momentum``. Combina ETF rotation (settori
OVERWEIGHT) con momentum scoring intra-settore (top stock dentro il settore
vincente). Edge atteso: industry momentum + intra-industry winners battono
ETF puro di 200-400 bps annui in trend regimes (Moskowitz-Grinblatt 1999 +
Asness-Porter-Stevens 2000).

Limitazione fase 1: solo S&P 500 universe. NASDAQ100/STOXX600 in roadmap.
"""

from __future__ import annotations

import streamlit as st

from propicks.config import (
    SFM_AI_CACHE_TTL_HOURS,
    SFM_DEFAULT_TOP_SECTORS,
    SFM_MAX_AGGREGATE_EXPOSURE_PCT,
    SFM_MAX_LOSS_PER_TRADE_PCT,
    SFM_MAX_POSITION_SIZE_PCT,
    SFM_MAX_STOCKS_PER_SECTOR,
    SFM_MIN_SECTOR_SCORE,
    SFM_MIN_STOCK_SCORE,
    SFM_RS_OVERLAY_WEIGHT,
)
from propicks.dashboard._shared import (
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
)

st.set_page_config(page_title="Sector Momentum · Propicks", layout="wide")
page_header(
    "Sector-Filtered Momentum (SFM)",
    "Top-down + bottom-up: ETF rotation seleziona i settori OVERWEIGHT, "
    "poi momentum scoring sceglie i top stock dentro ognuno. "
    "Parallelo a Momentum / ETF Rotation, NON le sostituisce.",
)
invariants_note(strategy_bucket="sfm")

st.info(
    f"**Strategia SFM — invarianti dedicate:**  \n"
    f"• Bucket aggregato: max **{SFM_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%** "
    f"del capitale (sum SFM positions)  \n"
    f"• Per stock: max **{SFM_MAX_POSITION_SIZE_PCT * 100:.0f}%** "
    f"(vs 15% momentum standalone — beta inflation premium)  \n"
    f"• Max stock per settore: **{SFM_MAX_STOCKS_PER_SECTOR}** "
    f"(evita over-concentration intra-bucket)  \n"
    f"• Stop max loss: **{SFM_MAX_LOSS_PER_TRADE_PCT * 100:.0f}%** "
    f"(vs 8% momentum — high-beta drawdown atteso)  \n"
    f"• Peer-RS overlay: composite × **{(1 - SFM_RS_OVERLAY_WEIGHT) * 100:.0f}%** + "
    f"rs_sector × **{SFM_RS_OVERLAY_WEIGHT * 100:.0f}%**  \n"
    f"• Gate ETF rotation: solo settori con score ≥ **{SFM_MIN_SECTOR_SCORE:.0f}** "
    f"(classe A OVERWEIGHT)  \n"
    f"• Gate stock momentum: solo score ≥ **{SFM_MIN_STOCK_SCORE:.0f}** "
    f"(classe A AZIONE IMMEDIATA)",
    icon="ℹ️",
)


# ---------------------------------------------------------------------------
# Cached helpers (TTL 5min, mirror altre page)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _cached_rank_us() -> list[dict]:
    from propicks.domain.etf_scoring import rank_universe
    return rank_universe(region="US")


@st.cache_data(ttl=300, show_spinner=False)
def _cached_sp500_detailed(force: bool) -> list[dict]:
    from propicks.market.index_constituents import (
        INDEX_NAME_SP500,
        get_index_universe_detailed,
    )
    return get_index_universe_detailed(INDEX_NAME_SP500, force_refresh=force)


# ---------------------------------------------------------------------------
# Mode selector
# ---------------------------------------------------------------------------
SECTOR_KEYS_DISPLAY = {
    "technology": "Technology (XLK)",
    "financials": "Financials (XLF)",
    "energy": "Energy (XLE)",
    "healthcare": "Healthcare (XLV)",
    "industrials": "Industrials (XLI)",
    "consumer_discretionary": "Consumer Discretionary (XLY)",
    "consumer_staples": "Consumer Staples (XLP)",
    "utilities": "Utilities (XLU)",
    "real_estate": "Real Estate (XLRE)",
    "materials": "Materials (XLB)",
    "communications": "Communication Services (XLC)",
}

tab_rotate, tab_explicit = st.tabs([
    "🔄 Rotate-driven (default)",
    "🎯 Sector esplicito",
])

submitted = False

with tab_rotate:
    st.markdown(
        "**Mode A — automatico**: il rank ETF Rotation (US) seleziona i top "
        f"{SFM_DEFAULT_TOP_SECTORS} settori OVERWEIGHT, poi per ogni settore "
        f"vengono scelti i top {SFM_MAX_STOCKS_PER_SECTOR} stock momentum "
        "dentro l'universo S&P 500."
    )
    with st.form("sfm_form_rotate", border=True):
        col_a, col_b, col_c = st.columns(3)
        top_sectors_in = col_a.number_input(
            "Top settori",
            min_value=1,
            max_value=5,
            value=SFM_DEFAULT_TOP_SECTORS,
            step=1,
            help="Quanti settori OVERWEIGHT scansionare.",
        )
        top_stocks_in = col_b.number_input(
            "Top stock per settore",
            min_value=1,
            max_value=10,
            value=SFM_MAX_STOCKS_PER_SECTOR,
            step=1,
        )
        rs_weight_in = col_c.slider(
            "Peer-RS overlay weight",
            min_value=0.0,
            max_value=1.0,
            value=SFM_RS_OVERLAY_WEIGHT,
            step=0.05,
            help="Peso del peer-RS in score_sfm. Default 0.20 = composite × 0.80 + rs × 0.20.",
        )
        col_d, col_e = st.columns(2)
        min_sector_in = col_d.number_input(
            "Min sector score",
            min_value=0.0,
            max_value=100.0,
            value=SFM_MIN_SECTOR_SCORE,
            step=5.0,
            help=f"Default {SFM_MIN_SECTOR_SCORE:.0f} = classe A OVERWEIGHT.",
        )
        min_stock_in = col_e.number_input(
            "Min stock momentum score",
            min_value=0.0,
            max_value=100.0,
            value=SFM_MIN_STOCK_SCORE,
            step=5.0,
            help=f"Default {SFM_MIN_STOCK_SCORE:.0f} = classe A AZIONE IMMEDIATA.",
        )
        col_f, col_g = st.columns(2)
        validate_ai_r = col_f.checkbox(
            "Valida con Claude (SFM-specific prompt)",
            value=False,
            key="r_validate",
            help=(
                f"Usa validate_sfm_thesis (cache TTL {SFM_AI_CACHE_TTL_HOURS}h). "
                f"Prompt SFM-specifico: focus su intra-sector winner vs passenger."
            ),
        )
        force_ai_r = col_g.checkbox(
            "Force (bypassa gate + cache)",
            value=False,
            key="r_force",
        )
        refresh_in_r = st.checkbox(
            "Refresh universe S&P 500 (bypass cache 7gg)",
            value=False,
            key="r_refresh",
        )
        submit_rotate = st.form_submit_button(
            "Esegui SFM rotate-driven",
            type="primary",
            width="stretch",
        )

    if submit_rotate:
        st.session_state["sfm_mode"] = "rotate"
        st.session_state["sfm_top_sectors"] = int(top_sectors_in)
        st.session_state["sfm_top_stocks"] = int(top_stocks_in)
        st.session_state["sfm_rs_weight"] = float(rs_weight_in)
        st.session_state["sfm_min_sector"] = float(min_sector_in)
        st.session_state["sfm_min_stock"] = float(min_stock_in)
        st.session_state["sfm_validate_ai"] = validate_ai_r
        st.session_state["sfm_force_ai"] = force_ai_r
        st.session_state["sfm_refresh"] = bool(refresh_in_r)
        st.session_state["sfm_explicit_sector"] = None
        st.session_state["sfm_first_render"] = True
        submitted = True


with tab_explicit:
    st.markdown(
        "**Mode B — manual override**: salta la rotation, vai diretto sui top "
        "stock momentum di un settore specifico. Utile per backtest, debug, "
        "o quando hai una view discrezionale che differisce dal ranking."
    )
    with st.form("sfm_form_explicit", border=True):
        sector_label = st.selectbox(
            "Settore target",
            options=list(SECTOR_KEYS_DISPLAY.values()),
            index=0,
        )
        sector_key_picked = next(
            k for k, v in SECTOR_KEYS_DISPLAY.items() if v == sector_label
        )
        col_x, col_y = st.columns(2)
        top_stocks_e = col_x.number_input(
            "Top stock",
            min_value=1,
            max_value=10,
            value=5,
            step=1,
            key="e_top",
        )
        min_stock_e = col_y.number_input(
            "Min stock momentum score",
            min_value=0.0,
            max_value=100.0,
            value=60.0,  # più permissivo in mode B (manual override)
            step=5.0,
            key="e_min_stock",
            help="Default 60 in mode B = include classe B watchlist (più permissivo).",
        )
        col_z, col_w = st.columns(2)
        validate_ai_e = col_z.checkbox(
            "Valida con Claude",
            value=False,
            key="e_validate",
        )
        force_ai_e = col_w.checkbox(
            "Force",
            value=False,
            key="e_force",
        )
        refresh_in_e = st.checkbox(
            "Refresh universe",
            value=False,
            key="e_refresh",
        )
        submit_explicit = st.form_submit_button(
            "Esegui SFM sector-explicit",
            type="primary",
            width="stretch",
        )

    if submit_explicit:
        st.session_state["sfm_mode"] = "explicit"
        st.session_state["sfm_explicit_sector"] = sector_key_picked
        st.session_state["sfm_top_stocks"] = int(top_stocks_e)
        st.session_state["sfm_rs_weight"] = SFM_RS_OVERLAY_WEIGHT
        st.session_state["sfm_min_stock"] = float(min_stock_e)
        st.session_state["sfm_validate_ai"] = validate_ai_e
        st.session_state["sfm_force_ai"] = force_ai_e
        st.session_state["sfm_refresh"] = bool(refresh_in_e)
        st.session_state["sfm_first_render"] = True
        submitted = True


# ---------------------------------------------------------------------------
# Compute pipeline (post-submit)
# ---------------------------------------------------------------------------
if submitted:
    from propicks.domain.sector_momentum import discover_sector_momentum_candidates

    refresh = bool(st.session_state.get("sfm_refresh", False))
    try:
        with st.spinner("Loading S&P 500 universe…"):
            detailed = _cached_sp500_detailed(refresh)
    except Exception as exc:
        st.error(f"Universe S&P 500 non caricabile: {exc}")
        st.stop()

    mode = st.session_state.get("sfm_mode")
    ranked = None
    sector_keys = None
    if mode == "rotate":
        with st.spinner("Ranking ETF Rotation US…"):
            try:
                ranked = _cached_rank_us()
            except Exception as exc:
                st.error(f"ETF Rotation rank fallito: {exc}")
                st.stop()
        if not ranked:
            st.error("Ranking ETF vuoto.")
            st.stop()
    else:
        sector_keys = [st.session_state["sfm_explicit_sector"]]

    progress = st.progress(0.0, text="SFM pipeline in corso…")

    def _ui_progress(stage: str, current: int, total: int, ticker: str) -> None:
        if total <= 0:
            return
        if stage == "sector":
            pct = current / total * 0.10
        elif stage == "prefilter":
            pct = 0.10 + (current / total) * 0.40
        else:
            pct = 0.50 + (current / total) * 0.50
        progress.progress(min(pct, 1.0), text=f"[{stage}] {current}/{total} · {ticker}")

    with st.spinner("Discovery candidati SFM…"):
        out = discover_sector_momentum_candidates(
            detailed,
            ranked_etfs=ranked,
            sector_keys=sector_keys,
            top_sectors=st.session_state.get("sfm_top_sectors", SFM_DEFAULT_TOP_SECTORS),
            top_stocks_per_sector=st.session_state["sfm_top_stocks"],
            min_sector_score=st.session_state.get("sfm_min_sector", SFM_MIN_SECTOR_SCORE),
            min_stock_score=st.session_state["sfm_min_stock"],
            rs_overlay_weight=st.session_state["sfm_rs_weight"],
            progress_callback=_ui_progress,
        )
    progress.empty()

    st.session_state["sfm_results"] = out
    st.session_state["sfm_active"] = True


# ---------------------------------------------------------------------------
# Render (read from session_state — survives post-submit reruns)
# ---------------------------------------------------------------------------
if not st.session_state.get("sfm_active"):
    st.stop()

out = st.session_state.get("sfm_results") or {}
sectors_evaluated = out.get("sectors_evaluated", [])
candidates = out.get("candidates", [])
mode = st.session_state.get("sfm_mode", "rotate")
validate_ai = st.session_state.get("sfm_validate_ai", False)
force_ai = st.session_state.get("sfm_force_ai", False)

# ---------------------------------------------------------------------------
# Sectors evaluated summary
# ---------------------------------------------------------------------------
st.subheader("Settori valutati")
if not sectors_evaluated:
    st.warning(
        "**Nessun settore qualificato.** "
        "In mode rotate: il regime macro non offre settori OVERWEIGHT "
        f"(score ≥ {SFM_MIN_SECTOR_SCORE:.0f}). "
        "Considera di abbassare *Min sector score*, o aspettare un setup migliore. "
        "In mode explicit: sector_key non valido."
    )
    st.stop()

sector_rows = []
for s in sectors_evaluated:
    sec_score = s.get("sector_score")
    sector_rows.append({
        "Sector": s["sector_key"],
        "Peer ETF": s["peer_etf"],
        "Score Rot.": f"{sec_score:.1f}" if isinstance(sec_score, (int, float)) else "—",
        "N. Universe": s["n_universe"],
        "N. Candidati": s["n_candidates"],
    })
st.dataframe(sector_rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Watchlist auto-add (only on first render after submit)
# ---------------------------------------------------------------------------
if candidates and st.session_state.pop("sfm_first_render", False):
    actionable = [
        c for c in candidates
        if c.get("classification", "").startswith(("A", "B"))
    ]
    if actionable:
        from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

        wl = load_watchlist()
        added, updated = [], []
        for r in actionable:
            classification = r.get("classification", "")
            is_class_a = classification.startswith("A")
            existing = wl.get("tickers", {}).get(r["ticker"].upper())
            target = (
                round(r["price"], 2)
                if is_class_a and not (existing and existing.get("target_entry"))
                else None
            )
            regime = r.get("regime") or {}
            _, is_new = add_to_watchlist(
                wl,
                r["ticker"],
                target_entry=target,
                score_at_add=r.get("score_sfm"),
                regime_at_add=regime.get("regime"),
                classification_at_add=classification,
                source="auto_sfm_scan",
            )
            (added if is_new else updated).append(r["ticker"])
        parts = []
        if added:
            parts.append(f"nuovi: {', '.join(added)}")
        if updated:
            parts.append(f"aggiornati: {', '.join(updated)}")
        if parts:
            st.toast(f"Watchlist SFM (A+B) — {' · '.join(parts)}", icon="📋")


# ---------------------------------------------------------------------------
# Candidates summary table (cross-sector ranked by score_sfm desc)
# ---------------------------------------------------------------------------
st.subheader("Candidati SFM (ranked desc by score_sfm)")
if not candidates:
    st.warning(
        f"**Nessun candidato sopra {st.session_state['sfm_min_stock']:.0f} momentum.** "
        "Possibili cause: sector OVERWEIGHT ma stock dentro non in trend up; "
        "prefilter momentum scarta tutti (RSI < 45 o dist > 35% high). "
        "Prova a abbassare *Min stock score* o cambiare settore."
    )
    st.stop()


def _earnings_short(r: dict) -> str:
    days = r.get("days_to_earnings")
    if not isinstance(days, int):
        return "—"
    if days < 0:
        return f"📰{abs(days)}d"
    if days <= 5:
        return f"🚨{days}d"
    if days <= 14:
        return f"⚠️{days}d"
    return f"{days}d"


cand_rows = []
for r in candidates:
    s = r.get("scores", {})
    rs = r.get("rs_vs_sector") or {}
    rs_score = rs.get("score")
    rs_score_str = f"{rs_score:.0f}" if isinstance(rs_score, (int, float)) else "—"
    rs_slope = rs.get("rs_slope")
    rs_slope_str = (
        f"{rs_slope:+.4f}" if isinstance(rs_slope, (int, float)) else "—"
    )
    cand_rows.append({
        "Ticker": r["ticker"],
        "Sector": r.get("sector_key", "-"),
        "Peer": r.get("peer_etf", "-"),
        "Price": f"{r['price']:.2f}",
        "SFM": f"{r.get('score_sfm', 0):.1f}",
        "Mom.": f"{r.get('score_composite', 0):.1f}",
        "RS sec": rs_score_str,
        "RS slope": rs_slope_str,
        "Class.": r.get("classification", "-").split(" — ")[0],
        "Stop": f"{r['stop_suggested']:.2f}",
        "1m": fmt_pct(r.get("perf_1m")),
        "3m": fmt_pct(r.get("perf_3m")),
        "Earn.": _earnings_short(r),
    })
st.dataframe(cand_rows, width="stretch", hide_index=True)
st.caption(
    f"**SFM** = score_composite × {(1 - st.session_state['sfm_rs_weight']) * 100:.0f}% + "
    f"rs_sector × {st.session_state['sfm_rs_weight'] * 100:.0f}%. "
    "**RS sec** ≥ 60 + slope > 0 = leader confermato. "
    "**RS slope** ≤ 0 = passenger risk (sector tailwind ma stock non lead). "
    "**Earn.**: 🚨 ≤ 5gg hard gate, ⚠️ ≤ 14gg warning."
)


# ---------------------------------------------------------------------------
# Per-ticker detail (peer-RS deep dive + AI validation)
# ---------------------------------------------------------------------------
st.subheader("Dettaglio per ticker")
for r in candidates:
    rs = r.get("rs_vs_sector") or {}
    rs_score = rs.get("score")
    rs_slope = rs.get("rs_slope")
    rs_passenger = (
        isinstance(rs_score, (int, float))
        and isinstance(rs_slope, (int, float))
        and rs_score < 60
        and rs_slope <= 0
    )

    badge = "🟢" if not rs_passenger else "🟡"
    title = (
        f"{badge} {r['ticker']}  —  SFM {r.get('score_sfm', 0):.1f}  "
        f"(sector {r.get('sector_key', '-')} via {r.get('peer_etf', '-')})"
    )
    with st.expander(title, expanded=len(candidates) == 1):
        cols = st.columns([1, 1, 1, 2])
        cols[0].metric("Prezzo", f"{r['price']:.2f}")
        cols[1].metric(
            "Score SFM",
            f"{r.get('score_sfm', 0):.1f}",
            help="composite × 0.80 + rs_sector × 0.20",
        )
        cols[2].metric(
            "Score momentum",
            f"{r.get('score_composite', 0):.1f}",
            help="6 sub-score classico, identico a Momentum standalone.",
        )
        cols[3].markdown(
            "**Regime:** " + regime_badge(r.get("regime")), unsafe_allow_html=True
        )

        # Peer-RS deep dive
        st.markdown("**Peer-RS vs sector ETF (l'edge SFM)**")
        pr_cols = st.columns(4)
        pr_cols[0].metric(
            "RS score",
            f"{rs_score:.0f}" if isinstance(rs_score, (int, float)) else "—",
            help="0-100. ≥ 60 + slope > 0 = leader confermato.",
        )
        pr_cols[1].metric(
            "RS ratio",
            f"{rs.get('rs_ratio', 0):.4f}"
            if isinstance(rs.get("rs_ratio"), (int, float))
            else "—",
            help=">1.0 = stock outperform peer ETF in 26w lookback.",
        )
        pr_cols[2].metric(
            "RS slope",
            f"{rs_slope:+.4f}" if isinstance(rs_slope, (int, float)) else "—",
            help="Variazione settimanale media RS line. >0 = leadership in accelerazione.",
        )
        pr_cols[3].metric(
            "Peer ETF",
            r.get("peer_etf", "—"),
        )

        if rs_passenger:
            st.warning(
                "🟡 **Passenger risk**: peer-RS score < 60 AND slope ≤ 0. "
                "Il titolo non sta effettivamente battendo il sector ETF — "
                "stai pagando per beta settoriale + costi extra vs comprare "
                "direttamente il peer ETF. AI validation skippata se gate attivo. "
                "Considera di sostituire con altro nome del settore."
            )
        elif rs:
            st.success(
                "🟢 **Leader intra-settore confermato**: peer-RS supporta la tesi "
                "SFM. Stai catturando un alpha aggiuntivo sopra il sector beta."
            )
        else:
            st.info(
                "ℹ️ Peer-RS non disponibile (ticker non-US o senza peer mapping). "
                "Il composite SFM coincide col composite momentum standalone."
            )

        # Sub-score momentum
        st.markdown("**Sub-score momentum (6 dimensioni, classic engine)**")
        s = r.get("scores", {})
        sub_cols = st.columns(6)
        sub_cols[0].metric("Trend (25%)", f"{s.get('trend', 0):.0f}")
        sub_cols[1].metric("Momentum (20%)", f"{s.get('momentum', 0):.0f}")
        sub_cols[2].metric("Volume (15%)", f"{s.get('volume', 0):.0f}")
        sub_cols[3].metric("Dist.High (15%)", f"{s.get('distance_high', 0):.0f}")
        sub_cols[4].metric("Volat. (10%)", f"{s.get('volatility', 0):.0f}")
        sub_cols[5].metric("MA× (15%)", f"{s.get('ma_cross', 0):.0f}")

        # Trade params
        st.markdown("**Parametri trade (regole SFM)**")
        t_cols = st.columns(4)
        t_cols[0].metric("Entry", f"{r['price']:.2f}")
        t_cols[1].metric(
            "Stop (-2 ATR)",
            f"{r['stop_suggested']:.2f}",
            delta=fmt_pct(r.get("stop_pct")),
            delta_color="inverse",
        )
        t_cols[2].metric(
            "Max loss",
            f"{SFM_MAX_LOSS_PER_TRADE_PCT * 100:.0f}%",
            help="Stop tighter del momentum standalone (8%) per beta inflation.",
        )
        t_cols[3].metric(
            "Max size",
            f"{SFM_MAX_POSITION_SIZE_PCT * 100:.0f}%",
            help="Cap per stock SFM (vs 15% momentum) per factor concentration.",
        )

        # Earnings awareness
        days_e = r.get("days_to_earnings")
        next_e = r.get("next_earnings_date")
        if isinstance(days_e, int) and next_e:
            if 0 <= days_e <= 5:
                st.error(
                    f"🚨 **Earnings in {days_e}gg ({next_e})** — "
                    f"`add_position` bloccato dal hard gate. "
                    f"In SFM evita comunque entry pre-earnings: il segnale "
                    f"sector momentum può essere confuso con earnings drift."
                )
            elif 6 <= days_e <= 14:
                st.warning(
                    f"⚠️ Earnings in {days_e}gg ({next_e}) — entry permessa "
                    f"ma R/R compresso. Considera SCALE_IN tranches."
                )

        # Manual watchlist add
        wl_col1, wl_col2 = st.columns([1, 3])
        if wl_col1.button(
            "📋 Aggiungi a watchlist (SFM)",
            key=f"wl_sfm_btn_{r['ticker']}",
            type="secondary",
        ):
            from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

            wl = load_watchlist()
            regime = r.get("regime") or {}
            _, is_new = add_to_watchlist(
                wl,
                r["ticker"],
                score_at_add=r.get("score_sfm"),
                regime_at_add=regime.get("regime"),
                classification_at_add=r.get("classification"),
                source="manual_sfm",
            )
            verb = "Aggiunto" if is_new else "Aggiornato"
            wl_col2.success(f"{verb} {r['ticker']} in watchlist (tag sfm).")

        # AI validation (validate_sfm_thesis — SFM-specific prompt)
        if validate_ai:
            from propicks.ai.sfm_validator import validate_sfm_thesis

            with st.spinner(f"Validating {r['ticker']} con Claude (SFM frame)…"):
                verdict = validate_sfm_thesis(
                    r, force=force_ai, gate=not force_ai
                )

            if verdict is None:
                reasons = []
                score_sfm = r.get("score_sfm", 0)
                if score_sfm < SFM_MIN_STOCK_SCORE:
                    reasons.append(f"score_sfm {score_sfm:.1f} < {SFM_MIN_STOCK_SCORE:.0f}")
                if rs_passenger:
                    reasons.append("peer-RS dead (score < 60 AND slope ≤ 0)")
                regime = r.get("regime") or {}
                if not regime.get("entry_allowed", True):
                    reasons.append(f"regime {regime.get('regime', '?')} no-entry")
                st.warning(
                    "AI validation skipped: "
                    + (", ".join(reasons) if reasons else "errore API o budget")
                    + ". Usa *Force* per bypassare."
                )
            else:
                v_verdict = verdict.get("verdict", "?")
                v_color = {
                    "CONFIRM": "#16a34a",
                    "CAUTION": "#ca8a04",
                    "REJECT": "#dc2626",
                }.get(v_verdict, "#64748b")

                st.markdown(
                    f'<div style="background:{v_color};color:white;padding:8px 12px;'
                    f'border-radius:6px;font-weight:600;display:inline-block;">'
                    f'Claude SFM: {v_verdict} · '
                    f'conv {verdict.get("conviction_score", "?")}/10 · '
                    f'R/R {verdict.get("reward_risk_ratio", "?")}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Tactic **{verdict.get('entry_tactic', '?')}** · "
                    f"horizon **{verdict.get('time_horizon', '?')}** · "
                    f"alignment {verdict.get('alignment_with_technicals', '?')} · "
                    f"deadline {verdict.get('invalidation_deadline', '?')} · "
                    f"cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
                )

                if verdict.get("thesis_summary"):
                    st.markdown("**Tesi SFM:** " + verdict["thesis_summary"])

                cbd = verdict.get("confidence_by_dimension") or {}
                if cbd:
                    st.markdown("**Confidence by dimension (0-10)**")
                    cbd_cols = st.columns(6)
                    keys = [
                        "business_quality",
                        "narrative_catalysts",
                        "sector_macro_fit",
                        "crowding_sentiment",
                        "risk_asymmetry",
                        "technicals_alignment",
                    ]
                    labels = {
                        "business_quality": "Business",
                        "narrative_catalysts": "Catalyst",
                        "sector_macro_fit": "Sector fit",
                        "crowding_sentiment": "Crowding",
                        "risk_asymmetry": "R/R asym",
                        "technicals_alignment": "Tech align",
                    }
                    for i, k in enumerate(keys):
                        cbd_cols[i].metric(labels[k], f"{cbd.get(k, '?')}/10")

                if verdict.get("bull_case"):
                    st.markdown("**Bull case:**")
                    for x in verdict["bull_case"]:
                        st.markdown(f"- {x}")
                if verdict.get("bear_case"):
                    st.markdown("**Bear case:**")
                    for x in verdict["bear_case"]:
                        st.markdown(f"- {x}")
                if verdict.get("invalidation_triggers"):
                    st.markdown("**Invalidation triggers:**")
                    for x in verdict["invalidation_triggers"]:
                        st.markdown(f"- {x}")
                if verdict.get("stop_rationale"):
                    st.caption(f"**Stop rationale:** {verdict['stop_rationale']}")
                if verdict.get("target_rationale"):
                    st.caption(f"**Target rationale:** {verdict['target_rationale']}")
