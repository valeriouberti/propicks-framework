"""Contrarian scanner — quality-filtered mean reversion.

Equivalent UI di ``propicks-contra [TICKER ...] [--validate]``. Parallelo
alla page Momentum (page 1): stesso ticker può essere analizzato da
entrambe le strategie, i verdict sono cached separatamente.
"""

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import (
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
)

st.set_page_config(page_title="Contrarian · Propicks", layout="wide")
page_header(
    "Contrarian",
    "Quality-filtered mean reversion. Cerca setup oversold su titoli di qualità con trend strutturale intatto. "
    "Parallelo alla page Momentum, NON la sostituisce.",
)
invariants_note(strategy_bucket="contrarian")

st.info(
    "**Strategia contrarian — invarianti diverse dal momentum:**  \n"
    "• Size max: **8%** per posizione (vs 15% momentum)  \n"
    "• Bucket cap aggregato: **20%** del capitale  \n"
    "• Max posizioni contrarian simultanee: **3** (condivide cap globale 10 con momentum)  \n"
    "• Stop = `recent_low − 1×ATR`, max loss 12% per trade  \n"
    "• Time stop: 15 giorni (vs 30gg momentum)  \n"
    "• Target: reversion a EMA50 daily (dinamico, drift-tracked, NON trailing)  \n"
    "• Regime gate INVERSO: skip STRONG_BULL/STRONG_BEAR, sweet spot NEUTRAL",
    icon="ℹ️",
)

# ---------------------------------------------------------------------------
# Cached contrarian analyze (TTL 5min come lo scanner momentum)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cached_analyze_contra(ticker: str, vix: float | None) -> dict | None:
    from propicks.domain.contrarian_scoring import analyze_contra_ticker
    return analyze_contra_ticker(ticker, strategy="Contrarian", vix=vix)


@st.cache_data(ttl=300, show_spinner=False)
def cached_vix() -> float | None:
    from propicks.market.yfinance_client import download_benchmark
    vix_series = download_benchmark("^VIX", days=10)
    if vix_series is None or vix_series.empty:
        return None
    return float(vix_series.iloc[-1])


# ---------------------------------------------------------------------------
# Mode selector: Manual (ticker espliciti) vs Discovery (universe-wide scan)
# ---------------------------------------------------------------------------
INDEX_OPTIONS = {
    "S&P 500 (~500 nomi US)": "sp500",
    "Nasdaq-100 (~100 nomi tech US)": "nasdaq100",
    "FTSE MIB (40 large-cap IT)": "ftsemib",
    "STOXX Europe 600 (~600 nomi)": "stoxx600",
}

tab_manual, tab_discovery = st.tabs(["📝 Manual scan", "🔭 Discovery (universe-wide)"])

# Init shared state
tickers: list[str] = []
validate_ai = False
force_ai = False
discover_universe: str | None = None
discover_top_n = 10
discover_min_score = 0.0
discover_rsi_max = 35.0
discover_atr_min = 1.0
discover_refresh = False
submitted = False

with tab_manual:
    with st.form("contra_form_manual", border=True):
        tickers_raw = st.text_input(
            "Ticker (separati da spazio o virgola)",
            placeholder="AAPL MSFT NVDA  (titoli di qualità Pro Picks oversold)",
        )
        col1, col2 = st.columns([1, 1])
        validate_ai_m = col1.checkbox(
            "Valida con Claude (flush vs break)", value=False, key="m_validate"
        )
        force_ai_m = col2.checkbox(
            "Force (bypassa gate + cache)", value=False, key="m_force"
        )
        submit_manual = st.form_submit_button(
            "Analizza setup contrarian", type="primary", width="stretch"
        )

    if submit_manual:
        tickers = [
            t.strip().upper()
            for t in tickers_raw.replace(",", " ").split()
            if t.strip()
        ]
        if not tickers:
            st.warning("Inserisci almeno un ticker.")
            st.stop()
        validate_ai = validate_ai_m
        force_ai = force_ai_m
        submitted = True

        # Compute results immediately and persist in session_state so that
        # subsequent reruns (caused by widgets post-submit, e.g. the radio
        # in the prompt selector or the watchlist add button) can reuse the
        # data without re-fetching. cached_analyze_contra is already
        # @st.cache_data so this fetch is cheap on cache hit.
        with st.spinner("Fetching VIX…"):
            _vix_compute = cached_vix()
        with st.spinner(f"Scanning {len(tickers)} ticker (contrarian)…"):
            _results_compute: list[dict] = []
            for _t in tickers:
                _r = cached_analyze_contra(_t, _vix_compute)
                if _r is not None:
                    _results_compute.append(_r)
        if not _results_compute:
            st.error("Nessun ticker analizzabile. Verifica i simboli o la connessione.")
            st.stop()
        st.session_state["contra_results"] = _results_compute
        st.session_state["contra_vix"] = _vix_compute
        st.session_state["contra_branch"] = "manual"
        st.session_state["contra_validate_ai"] = validate_ai_m
        st.session_state["contra_force_ai"] = force_ai_m
        st.session_state["contra_first_render"] = True


with tab_discovery:
    st.markdown(
        "**Discovery automatico**: scansiona un intero index, applica un "
        "prefilter cheap (RSI + distanza ATR) e ritorna i top N candidati "
        "ranked per composite. Il primo run di un index può richiedere "
        "5-10 min (cache OHLCV cold)."
    )
    with st.form("contra_form_discovery", border=True):
        col_a, col_b = st.columns([2, 1])
        universe_label = col_a.selectbox(
            "Universe", options=list(INDEX_OPTIONS.keys()), index=0
        )
        top_n_in = col_b.number_input(
            "Top N", min_value=1, max_value=50, value=10, step=1
        )
        col_c, col_d, col_e = st.columns(3)
        min_score_in = col_c.number_input(
            "Min score (filtro post-scoring)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=5.0,
            help="Es. 60 per filtrare solo classe A+B.",
        )
        rsi_max_in = col_d.number_input(
            "Prefilter RSI max",
            min_value=10.0,
            max_value=50.0,
            value=35.0,
            step=1.0,
        )
        atr_min_in = col_e.number_input(
            "Prefilter distance ATR min",
            min_value=0.5,
            max_value=5.0,
            value=1.0,
            step=0.5,
        )
        col_f, col_g, col_h = st.columns(3)
        validate_ai_d = col_f.checkbox(
            "Valida top con Claude", value=False, key="d_validate"
        )
        force_ai_d = col_g.checkbox(
            "Force (bypassa gate + cache)", value=False, key="d_force"
        )
        refresh_in = col_h.checkbox(
            "Refresh universe (bypass cache 7gg)",
            value=False,
            help="Forza re-fetch della lista da Wikipedia.",
        )
        submit_disc = st.form_submit_button(
            "Esegui discovery", type="primary", width="stretch"
        )

    if submit_disc:
        discover_universe = INDEX_OPTIONS[universe_label]
        discover_top_n = int(top_n_in)
        discover_min_score = float(min_score_in)
        discover_rsi_max = float(rsi_max_in)
        discover_atr_min = float(atr_min_in)
        discover_refresh = bool(refresh_in)
        validate_ai = validate_ai_d
        force_ai = force_ai_d
        submitted = True

        # Compute discovery results immediately and persist in session_state.
        # Discovery pipeline can take 5-10 min on cold cache → without this
        # persistence, every post-submit rerun (radio click, watchlist add)
        # would retrigger the full pipeline.
        from propicks.domain.contrarian_discovery import discover_contra_candidates
        from propicks.market.index_constituents import get_index_universe, index_label

        with st.spinner("Fetching VIX…"):
            _vix_compute = cached_vix()
        _label = index_label(discover_universe)
        try:
            with st.spinner(f"Loading {_label} universe…"):
                _universe = get_index_universe(
                    discover_universe, force_refresh=discover_refresh
                )
        except Exception as exc:
            st.error(f"Impossibile caricare l'universo {_label}: {exc}")
            st.stop()

        st.caption(
            f"Universe **{_label}**: {len(_universe)} ticker. "
            f"Stage 1 prefilter (RSI ≤ {discover_rsi_max}, "
            f"distance ≥ {discover_atr_min}× ATR)…"
        )
        _progress = st.progress(0.0, text="Discovery in corso…")

        def _ui_progress_disc(stage: str, current: int, total: int, ticker: str) -> None:
            if total <= 0:
                return
            pct = (
                0.70 * (current / total)
                if stage == "prefilter"
                else 0.70 + 0.30 * (current / total)
            )
            _progress.progress(min(pct, 1.0), text=f"[{stage}] {current}/{total} · {ticker}")

        with st.spinner(f"Running discovery pipeline su {_label}…"):
            _out = discover_contra_candidates(
                _universe,
                top_n=discover_top_n,
                rsi_max=discover_rsi_max,
                atr_distance_min=discover_atr_min,
                min_score=discover_min_score,
                vix=_vix_compute,
                progress_callback=_ui_progress_disc,
            )
        _progress.empty()

        if not _out["candidates"]:
            st.warning(
                "**Nessun candidato qualificato dopo full scoring.** "
                "Possibili cause: regime macro contrarian non favorevole "
                "(STRONG_BULL/STRONG_BEAR azzerano il composite via hard gate), "
                "oppure soglie prefilter troppo strict per il momento di mercato. "
                "Prova a rilassare RSI max o distance ATR min."
            )
            st.stop()

        st.session_state["contra_results"] = _out["candidates"]
        st.session_state["contra_vix"] = _vix_compute
        st.session_state["contra_discover_summary"] = {
            "label": _label,
            "universe_size": _out["universe_size"],
            "prefilter_pass": _out["prefilter_pass"],
            "scored": _out["scored"],
        }
        st.session_state["contra_branch"] = "discovery"
        st.session_state["contra_validate_ai"] = validate_ai_d
        st.session_state["contra_force_ai"] = force_ai_d
        st.session_state["contra_first_render"] = True


# Persistenza submit-flag in session_state: senza, ogni widget post-submit
# (es. il radio "Target LLM" nei prompt expander) causa un Streamlit rerun
# in cui ``submitted`` torna False → ``st.stop()`` collassa results.
# La key è scoped per pagina.
if submitted:
    st.session_state["contra_active"] = True

if not st.session_state.get("contra_active"):
    st.stop()

# ---------------------------------------------------------------------------
# Restore from session_state (results were computed inside the submit blocks
# above and persisted there). Subsequent reruns triggered by post-submit
# widgets (radio prompt selector, watchlist button) read from session_state
# instead of re-running the discovery pipeline (5-10 min cold) or the
# yfinance fetch loop.
# ---------------------------------------------------------------------------
results: list[dict] = list(st.session_state.get("contra_results") or [])
vix = st.session_state.get("contra_vix")
validate_ai = st.session_state.get("contra_validate_ai", False)
force_ai = st.session_state.get("contra_force_ai", False)
branch = st.session_state.get("contra_branch", "manual")

if not results:
    # Defensive: contra_active is set but results missing. Reset and retry.
    st.session_state.pop("contra_active", None)
    st.warning("Stato sessione invalido — esegui di nuovo.")
    st.stop()

if vix is None:
    st.warning(
        "VIX non disponibile — market context sub-score userà fallback neutrale."
    )
else:
    vix_label = (
        "euforia (edge collassa)"
        if vix <= 14
        else "paura (edge)"
        if vix >= 25
        else "neutrale"
    )
    st.caption(f"VIX corrente: **{vix:.2f}** — {vix_label}")

# Discovery summary metrics (only for discovery branch)
if branch == "discovery":
    _summary = st.session_state.get("contra_discover_summary") or {}
    if _summary:
        _summary_cols = st.columns(4)
        _summary_cols[0].metric("Universe", _summary["universe_size"])
        _summary_cols[1].metric("Prefilter pass", _summary["prefilter_pass"])
        _summary_cols[2].metric("Scored", _summary["scored"])
        _summary_cols[3].metric("Returned (top N)", len(results))

# ---------------------------------------------------------------------------
# Auto-add classe A+B alla watchlist con source=auto_scan_contra.
# Guard ``contra_first_render`` — il flag è settato a True nei branch submit
# e consumato qui (pop) così l'auto-add fa side-effect SOLO al primo render
# post-submit, non a ogni rerun da widget interattivi (radio prompt, button).
# ---------------------------------------------------------------------------
actionable = [
    r for r in results if r.get("classification", "").startswith(("A", "B"))
]
if actionable and st.session_state.pop("contra_first_render", False):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    wl = load_watchlist()
    added, updated = [], []
    for r in actionable:
        classification = r.get("classification", "")
        is_class_a = classification.startswith("A")
        existing = wl.get("tickers", {}).get(r["ticker"].upper())
        if is_class_a and not (existing and existing.get("target_entry")):
            target = round(r["price"], 2)
        else:
            target = None
        regime = r.get("regime") or {}
        _, is_new = add_to_watchlist(
            wl,
            r["ticker"],
            target_entry=target,
            score_at_add=r.get("score_composite"),
            regime_at_add=regime.get("regime"),
            classification_at_add=classification,
            source="auto_scan_contra",
        )
        (added if is_new else updated).append(r["ticker"])
    parts = []
    if added:
        parts.append(f"nuovi: {', '.join(added)}")
    if updated:
        parts.append(f"aggiornati: {', '.join(updated)}")
    st.toast(f"Watchlist contrarian (A+B) — {' · '.join(parts)}", icon="📋")

# ---------------------------------------------------------------------------
# Summary table — tutte le colonne serializzate come stringhe omogenee
# (evita PyArrow errors quando i valori possono essere None o sentinel "—")
# ---------------------------------------------------------------------------
def _earnings_short(r: dict) -> str:
    """Badge earnings compatto per summary table (mirror CLI contrarian)."""
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


st.subheader("Risultati contrarian")
rows = []
for r in sorted(results, key=lambda x: x["score_composite"], reverse=True):
    s = r["scores"]
    sub = r.get("sub_scores_detail") or {}
    oversold = sub.get("oversold") or {}
    reversion = sub.get("reversion") or {}
    atr_dist = oversold.get("atr_distance_from_ema")
    rr = reversion.get("rr_ratio")
    target = r.get("target_suggested")
    price = r.get("price")
    # Target valido solo se > price (altrimenti non è mean reversion long)
    target_valid = (
        isinstance(target, (int, float))
        and isinstance(price, (int, float))
        and target > price
    )
    rows.append({
        "Ticker": r["ticker"],
        "Price": f"{r['price']:.2f}",
        "Score": f"{r['score_composite']:.1f}",
        "Class": r["classification"].split(" — ")[0],
        "Oversold": f"{s['oversold']:.0f}",
        "Quality": f"{s['quality']:.0f}",
        "Context": f"{s['market_context']:.0f}",
        "R/R score": f"{s['reversion']:.0f}",
        "RSI": f"{r['rsi']:.1f}",
        "Dist EMA50": f"{atr_dist:.1f}x ATR" if isinstance(atr_dist, (int, float)) else "—",
        "Stop": (
            f"{r['stop_suggested']:.2f}"
            if isinstance(r.get("stop_suggested"), (int, float))
            else "—"
        ),
        "Target": f"{target:.2f}" if target_valid else "—",
        "R/R": f"{rr:.2f}" if isinstance(rr, (int, float)) and target_valid else "—",
        "Perf 1m": fmt_pct(r.get("perf_1m")),
        "Regime": (r.get("regime") or {}).get("regime", "N/D"),
        "Earn.": _earnings_short(r),
    })
st.dataframe(rows, width="stretch", hide_index=True)
st.caption(
    "**Oversold** (40%): RSI + distanza EMA50 in ATR + barre rosse consecutive · "
    "**Quality** (25%): gate EMA200 weekly + depth correzione · "
    "**Context** (20%): regime fit inverso + VIX · "
    "**R/R score** (15%): reversion a EMA50 vs stop a -3×ATR · "
    "**Earn.**: giorni al prossimo earnings (🚨 ≤5gg = hard gate, ⚠️ ≤14gg = "
    "warning, 📰 = report passato → possibile post-flush)."
)

# ---------------------------------------------------------------------------
# Detail cards (expander per ticker)
# ---------------------------------------------------------------------------
st.subheader("Dettaglio per ticker")
for r in results:
    sub = r.get("sub_scores_detail") or {}
    oversold_d = sub.get("oversold") or {}
    quality_d = sub.get("quality") or {}
    context_d = sub.get("market_context") or {}
    reversion_d = sub.get("reversion") or {}

    with st.expander(
        f"{r['ticker']}  —  score {r['score_composite']:.1f}  ({r['classification']})",
        expanded=len(results) == 1,
    ):
        cols = st.columns([1, 1, 1, 2])
        cols[0].metric("Prezzo", f"{r['price']:.2f}")
        cols[1].metric(
            "Score",
            f"{r['score_composite']:.1f}",
            help="Composite contrarian 0-100 (oversold 40% + quality 25% + context 20% + R/R 15%).",
        )
        cols[2].metric(
            "RSI",
            f"{r['rsi']:.1f}",
            help="RSI 14d. Oversold ideale <30, warm <35.",
        )
        cols[3].markdown(
            "**Regime:** " + regime_badge(r.get("regime")), unsafe_allow_html=True
        )

        # Sub-score visual
        st.markdown("**Sub-score contrarian**")
        sub_cols = st.columns(4)
        sub_cols[0].metric(
            "Oversold (40%)",
            f"{r['scores']['oversold']:.0f}",
            help="RSI + distanza EMA50 in ATR + barre rosse consecutive.",
        )
        quality_note = (
            "GATE ROTTO"
            if quality_d.get("above_ema200w") is False
            else "sopra EMA200w ✓"
        )
        sub_cols[1].metric(
            "Quality (25%)",
            f"{r['scores']['quality']:.0f}",
            help=f"Gate EMA200 weekly: {quality_note}",
        )
        sub_cols[2].metric(
            "Context (20%)",
            f"{r['scores']['market_context']:.0f}",
            help=context_d.get("vix_note", "VIX + regime fit inverso"),
        )
        rr_val = reversion_d.get("rr_ratio")
        sub_cols[3].metric(
            "R/R score (15%)",
            f"{r['scores']['reversion']:.0f}",
            help=f"R/R teorico: {rr_val:.2f}:1" if rr_val is not None else "R/R n/a",
        )

        # Oversold details
        st.markdown("**Segnali oversold**")
        o_cols = st.columns(4)
        o_cols[0].write(
            f"Dist EMA50: **{oversold_d.get('atr_distance_from_ema', 0):.1f}×** ATR"
        )
        o_cols[1].write(f"Barre rosse: **{r.get('consecutive_down', 0)}**")
        o_cols[2].write(f"Recent low: **{r.get('recent_low', 0):.2f}**")
        o_cols[3].write(f"ATR: **{r['atr']:.2f}** ({fmt_pct(r.get('atr_pct'))})")

        # Quality gate details
        st.markdown("**Quality gate (trend strutturale)**")
        q_cols = st.columns(3)
        ema200w = r.get("ema_200_weekly")
        q_cols[0].write(
            f"EMA200 weekly: **{ema200w:.2f}**"
            if ema200w is not None
            else "EMA200 weekly: **n/a**"
        )
        q_cols[1].write(
            f"Sopra EMA200w: **{'✓ sì' if quality_d.get('above_ema200w') else '✗ NO'}**"
        )
        q_cols[2].write(
            f"Distanza 52w high: **{fmt_pct(r.get('distance_from_high_pct'))}**"
        )

        if quality_d.get("above_ema200w") is False:
            st.error(
                "**Quality gate rotto** — il titolo è sotto EMA200 weekly. "
                "Questo NON è mean reversion ma downtrend: composite forzato a 0. "
                "Skip per sicurezza strutturale."
            )

        # Proposed trade
        st.markdown("**Parametri di trade proposti**")
        target_v = r.get("target_suggested")
        price_v = r.get("price")
        target_valid = (
            isinstance(target_v, (int, float))
            and isinstance(price_v, (int, float))
            and target_v > price_v
        )
        t_cols = st.columns(4)
        t_cols[0].metric("Entry", f"{r['price']:.2f}")
        t_cols[1].metric(
            "Stop", f"{r['stop_suggested']:.2f}",
            delta=fmt_pct(r.get("stop_pct")),
            delta_color="inverse",
        )
        t_cols[2].metric(
            "Target (EMA50)",
            f"{target_v:.2f}" if target_valid else "—",
            help=None if target_valid else "Setup invalido: price ≥ EMA50, non è mean reversion long.",
        )
        t_cols[3].metric(
            "R/R teorico",
            f"{rr_val:.2f}:1"
            if isinstance(rr_val, (int, float)) and target_valid
            else "—",
        )

        # Earnings hard-gate awareness — su contrarian un earnings imminente
        # è cruciale: il selloff può essere pre-report (BREAK probabile, REJECT)
        # vs post-report (FLUSH tradable). Il dato è già nel dict da analyze_contra_ticker.
        days_e = r.get("days_to_earnings")
        next_e = r.get("next_earnings_date")
        if isinstance(days_e, int) and next_e:
            if 0 <= days_e <= 5:
                st.error(
                    f"🚨 **Earnings in {days_e}gg ({next_e})** — `add_position` "
                    f"bloccato dal hard gate. Su contrarian questo è quasi sempre "
                    f"BREAK (selloff pre-report). Override solo per intentional "
                    f"post-earnings flush: `propicks-portfolio add ... "
                    f"--ignore-earnings --contrarian`."
                )
            elif 6 <= days_e <= 14:
                st.warning(
                    f"⚠️ Earnings in {days_e}gg ({next_e}) — entry permessa ma "
                    f"R/R reale è compresso dal report imminente. Considera di "
                    f"aspettare il post-report se vuoi un flush vero."
                )
            elif -7 <= days_e < 0:
                st.info(
                    f"📰 Ultimo earnings {abs(days_e)}gg fa ({next_e}) — "
                    f"setup post-flush plausibile, verifica cause selloff in "
                    f"--validate (FLUSH vs BREAK)."
                )

        # Manual watchlist add
        wl_col1, wl_col2 = st.columns([1, 3])
        if wl_col1.button(
            "📋 Aggiungi a watchlist",
            key=f"wl_contra_btn_{r['ticker']}",
            type="secondary",
        ):
            from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

            wl = load_watchlist()
            regime = r.get("regime") or {}
            _, is_new = add_to_watchlist(
                wl,
                r["ticker"],
                score_at_add=r.get("score_composite"),
                regime_at_add=regime.get("regime"),
                classification_at_add=r.get("classification"),
                source="manual_contra",
            )
            verb = "Aggiunto" if is_new else "Aggiornato"
            wl_col2.success(f"{verb} {r['ticker']} in watchlist (tag contra).")

        # -----------------------------------------------------------------
        # Prompt esterni (Perplexity contrarian) — cross-check indipendente.
        # Mirror del system prompt di contrarian_validator: discrimina FLUSH
        # vs BREAK cercando la causa del selloff.
        # -----------------------------------------------------------------
        with st.expander("Prompt Perplexity contrarian (copia-incolla)", expanded=False):
            from propicks.ai.user_prompts import perplexity_contrarian, perplexity_2c

            st.caption(
                "Cross-check indipendente a `--validate` Claude. Focus sulla "
                "**causa del selloff** (FLUSH tradable vs BREAK fondamentale). "
                "Perplexity è più affidabile sui catalyst recenti rispetto al "
                "web search di Claude — usalo come secondo paio di occhi su "
                "earnings miss, guidance cut, analyst revisions, peer action."
            )
            st.markdown("**Contrarian — analisi causa selloff** (FLUSH vs BREAK)")
            st.code(
                perplexity_contrarian(r["ticker"], r.get("name") or ""),
                language=None,
            )

            st.markdown("**2C — Check pre-entry** (red flag ultime 24h)")
            st.code(perplexity_2c(r["ticker"]), language=None)

        # -----------------------------------------------------------------
        # Fallback validate completo — selettore target LLM con 3 varianti.
        # Vedi pages/1_Momentum.py per il razionale completo dei trade-off.
        # -----------------------------------------------------------------
        with st.expander(
            "Prompt --validate contrarian (selettore target LLM)",
            expanded=False,
        ):
            from datetime import date as _date

            from propicks.ai.user_prompts import (
                llm_generic_contrarian_validate_full,
                perplexity_contrarian_validate_full,
                sonar_contrarian_validate_full,
            )

            _target_label = st.radio(
                "Target LLM",
                options=[
                    "Sonar (Perplexity nativo)",
                    "Perplexity Pro (Claude/GPT/Gemini via Pro)",
                    "Claude.ai / ChatGPT / Gemini diretto",
                ],
                index=0,
                horizontal=False,
                key=f"contra_prompt_target_{r['ticker']}",
                help=(
                    "Sonar nativo: prompt distillato + schema in cima + "
                    "regole computabili FLUSH/BREAK. Perplexity Pro: system "
                    "prompt Claude completo (~70 righe). LLM diretto: system "
                    "prompt Anthropic byte-per-byte."
                ),
            )

            _today = _date.today().isoformat()
            if _target_label.startswith("Sonar"):
                st.caption(
                    "Ottimizzato per Sonar / Sonar Pro / Sonar Reasoning. "
                    "Schema JSON in cima, persona event-driven / mean-reversion "
                    "PM distillata, FLUSH/BREAK come regola computabile, "
                    "confidence_by_dimension a 3 chiavi (quality_persistence / "
                    "catalyst_credibility / risk_asymmetry). **Default consigliato**."
                )
                _prompt = sonar_contrarian_validate_full(r, _today)
            elif _target_label.startswith("Perplexity Pro"):
                st.caption(
                    "Per Claude / GPT / Gemini eseguiti via Perplexity Pro. "
                    "Persona system prompt: senior event-driven / mean-reversion "
                    "PM (versione Anthropic completa, sezione `# Web search "
                    "usage` rimossa). Schema JSON con fallback `---JSON---`."
                )
                _prompt = perplexity_contrarian_validate_full(r, _today)
            else:
                st.caption(
                    "Per Claude.ai / console Anthropic / ChatGPT / Gemini "
                    "direct. System prompt Anthropic byte-per-byte → compat "
                    "piena con SDK Claude e claude.ai. Schema JSON strict. "
                    "Verifica la context window del modello target prima di incollare."
                )
                _prompt = llm_generic_contrarian_validate_full(r, _today)

            st.caption(
                f"~{len(_prompt):,} caratteri · ~{len(_prompt) // 4:,} token stimati."
            )
            st.code(_prompt, language="markdown")

        # AI validation on-demand
        if validate_ai:
            from propicks.ai.contrarian_validator import validate_contrarian_thesis

            with st.spinner(f"Validating {r['ticker']} con Claude (flush vs break)…"):
                verdict = validate_contrarian_thesis(
                    r, force=force_ai, gate=not force_ai
                )

            if verdict is None:
                score = r.get("score_composite", 0)
                regime_obj = r.get("regime") or {}
                regime_code = regime_obj.get("regime_code")
                reason = []
                if score < 60:
                    reason.append(f"score {score:.1f} < 60")
                if regime_code in (1, 5):
                    reason.append(
                        f"regime {regime_obj.get('regime', '?')} — edge contrarian collassa"
                    )
                st.warning(
                    "Validation skipped: "
                    + (", ".join(reason) if reason else "gate o errore API")
                    + ". Usa *Force* per bypassare."
                )
            else:
                v_verdict = verdict.get("verdict", "?")
                v_color = {
                    "CONFIRM": "#16a34a",
                    "CAUTION": "#ca8a04",
                    "REJECT": "#dc2626",
                }.get(v_verdict, "#64748b")

                flush = verdict.get("flush_vs_break", "?")
                flush_color = {
                    "FLUSH": "#16a34a",
                    "MIXED": "#ca8a04",
                    "BREAK": "#dc2626",
                }.get(flush, "#64748b")

                st.markdown(
                    f'<div style="display:flex;gap:8px;">'
                    f'<div style="background:{v_color};color:white;padding:8px 12px;'
                    f'border-radius:6px;font-weight:600;">'
                    f'Claude: {v_verdict} · conv {verdict.get("conviction_score", "?")}/10'
                    f'</div>'
                    f'<div style="background:{flush_color};color:white;padding:8px 12px;'
                    f'border-radius:6px;font-weight:600;">'
                    f'{flush}'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Catalyst: **{verdict.get('catalyst_type', '?')}** · "
                    f"target {verdict.get('reversion_target', '?')} · "
                    f"invalidation {verdict.get('invalidation_price', '?')} · "
                    f"horizon {verdict.get('time_horizon_days', '?')}gg · "
                    f"tactic {verdict.get('entry_tactic', '?')} · "
                    f"cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
                )

                if verdict.get("thesis_summary"):
                    st.markdown("**Tesi:** " + verdict["thesis_summary"])
                if verdict.get("key_risks"):
                    st.markdown("**Rischi chiave:**")
                    for risk in verdict["key_risks"]:
                        st.markdown(f"- {risk}")
                if verdict.get("invalidation_triggers"):
                    st.markdown("**Invalidation triggers:**")
                    for trig in verdict["invalidation_triggers"]:
                        st.markdown(f"- {trig}")
