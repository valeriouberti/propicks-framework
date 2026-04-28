"""Momentum scanner — trend/quality stock screener.

Equivalent UI di ``propicks-momentum [TICKER ...] [--validate]
[--discover-sp500|--discover-ftsemib|--discover-stoxx600]``.
"""

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import (
    INDICATOR_HELP_STOCK,
    cached_analyze,
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
    render_indicator_legend,
)

st.set_page_config(page_title="Momentum · Propicks", layout="wide")
page_header(
    "Momentum",
    "Trend/quality stock screener single-ticker, batch o discovery universe-wide. "
    "Validazione Claude opzionale (gate regime + score).",
)
invariants_note()

STRATEGIES = ("", "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane", "Altro")

# ---------------------------------------------------------------------------
# Mode selector: Manual (ticker espliciti) vs Discovery (universe-wide scan)
# ---------------------------------------------------------------------------
INDEX_OPTIONS = {
    "S&P 500 (~500 nomi US)": "sp500",
    "FTSE MIB (40 large-cap IT)": "ftsemib",
    "STOXX Europe 600 (~600 nomi)": "stoxx600",
}

tab_manual, tab_discovery = st.tabs(["📝 Manual scan", "🔭 Discovery (universe-wide)"])

# Init shared state
tickers: list[str] = []
strategy_val: str | None = None
validate_ai = False
force_ai = False
discover_universe: str | None = None
discover_top_n = 10
discover_min_score = 60.0
discover_rsi_min = 45.0
discover_max_dist = 0.35
discover_prefilter_cap: int | None = None
discover_refresh = False
submitted = False

with tab_manual:
    with st.form("momentum_form_manual", border=True):
        tickers_raw = st.text_input(
            "Ticker (separati da spazio o virgola)",
            placeholder="AAPL MSFT NVDA  oppure  ENI.MI ISP.MI",
        )
        col1, col2, col3 = st.columns([2, 1, 1])
        strategy = col1.selectbox(
            "Strategy (opzionale)", STRATEGIES, index=0, key="m_strategy"
        )
        validate_ai_m = col2.checkbox("Valida con Claude", value=False, key="m_validate")
        force_ai_m = col3.checkbox(
            "Force (bypassa gate + cache)", value=False, key="m_force"
        )
        submit_manual = st.form_submit_button(
            "Analizza", type="primary", width="stretch"
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
        strategy_val = strategy or None
        validate_ai = validate_ai_m
        force_ai = force_ai_m
        submitted = True


with tab_discovery:
    st.markdown(
        "**Discovery automatico**: scansiona un intero index, applica un "
        "prefilter cheap (trend EMA50 + RSI vivo + dentro range 52w-high) e "
        "ritorna i top N candidati ranked per composite. Il primo run di un "
        "index può richiedere 5-10 min (cache OHLCV cold)."
    )
    with st.form("momentum_form_discovery", border=True):
        col_a, col_b = st.columns([2, 1])
        universe_label = col_a.selectbox(
            "Universe", options=list(INDEX_OPTIONS.keys()), index=0, key="d_universe"
        )
        top_n_in = col_b.number_input(
            "Top N", min_value=1, max_value=50, value=10, step=1, key="d_top_n"
        )
        col_c, col_d, col_e = st.columns(3)
        strategy_d = col_c.selectbox(
            "Strategy (tag)", STRATEGIES, index=0, key="d_strategy"
        )
        min_score_in = col_d.number_input(
            "Min score (filtro post-scoring)",
            min_value=0.0,
            max_value=100.0,
            value=60.0,
            step=5.0,
            help="Default 60 → solo classe A+B. 75 → solo A. 0 → nessun filtro.",
            key="d_min_score",
        )
        prefilter_cap_in = col_e.number_input(
            "Prefilter cap (0 = no cap)",
            min_value=0,
            max_value=500,
            value=0,
            step=10,
            help="Limita il n. di ticker che passano allo stage 2 (full scoring).",
            key="d_prefilter_cap",
        )
        col_f, col_g = st.columns(2)
        rsi_min_in = col_f.number_input(
            "Prefilter RSI min",
            min_value=20.0,
            max_value=70.0,
            value=45.0,
            step=1.0,
            help="Default 45 (più permissivo del sweet-spot 50-65 dello score finale).",
            key="d_rsi_min",
        )
        max_dist_in = col_g.number_input(
            "Prefilter max dist 52w-high (frazione)",
            min_value=0.05,
            max_value=0.60,
            value=0.35,
            step=0.05,
            format="%.2f",
            help="Default 0.35 (dentro 35% dal massimo annuale).",
            key="d_max_dist",
        )
        col_h, col_i, col_j = st.columns(3)
        validate_ai_d = col_h.checkbox(
            "Valida top con Claude", value=False, key="d_validate"
        )
        force_ai_d = col_i.checkbox("Force (bypassa gate)", value=False, key="d_force")
        refresh_in = col_j.checkbox(
            "Refresh universe (bypass cache 7gg)",
            value=False,
            help="Forza re-fetch della lista da Wikipedia.",
            key="d_refresh",
        )
        submit_disc = st.form_submit_button(
            "Esegui discovery", type="primary", width="stretch"
        )

    if submit_disc:
        discover_universe = INDEX_OPTIONS[universe_label]
        discover_top_n = int(top_n_in)
        discover_min_score = float(min_score_in)
        discover_rsi_min = float(rsi_min_in)
        discover_max_dist = float(max_dist_in)
        discover_prefilter_cap = (
            int(prefilter_cap_in) if int(prefilter_cap_in) > 0 else None
        )
        discover_refresh = bool(refresh_in)
        strategy_val = strategy_d or None
        validate_ai = validate_ai_d
        force_ai = force_ai_d
        submitted = True


if not submitted:
    st.stop()

# ---------------------------------------------------------------------------
# Batch scan — manual vs discovery branch
# ---------------------------------------------------------------------------
results: list[dict] = []

if discover_universe is not None:
    from propicks.domain.momentum_discovery import discover_momentum_candidates
    from propicks.market.index_constituents import get_index_universe, index_label

    label = index_label(discover_universe)
    try:
        with st.spinner(f"Loading {label} universe…"):
            universe = get_index_universe(
                discover_universe, force_refresh=discover_refresh
            )
    except Exception as exc:
        st.error(f"Impossibile caricare l'universo {label}: {exc}")
        st.stop()

    st.caption(
        f"Universe **{label}**: {len(universe)} ticker. "
        f"Stage 1 prefilter (RSI ≥ {discover_rsi_min}, "
        f"dist_from_high ≤ {discover_max_dist})…"
    )

    progress = st.progress(0.0, text="Discovery in corso…")

    def _ui_progress(stage: str, current: int, total: int, ticker: str) -> None:
        if total <= 0:
            return
        # Stage 1 occupa 0-70%, stage 2 70-100% (full scoring più lento)
        if stage == "prefilter":
            pct = 0.70 * (current / total)
        else:
            pct = 0.70 + 0.30 * (current / total)
        progress.progress(min(pct, 1.0), text=f"[{stage}] {current}/{total} · {ticker}")

    with st.spinner(f"Running discovery pipeline su {label}…"):
        out = discover_momentum_candidates(
            universe,
            top_n=discover_top_n,
            rsi_min=discover_rsi_min,
            max_dist_from_high=discover_max_dist,
            min_score=discover_min_score,
            strategy=strategy_val,
            prefilter_cap=discover_prefilter_cap,
            progress_callback=_ui_progress,
        )
    progress.empty()

    summary_cols = st.columns(4)
    summary_cols[0].metric("Universe", out["universe_size"])
    summary_cols[1].metric("Prefilter pass", out["prefilter_pass"])
    summary_cols[2].metric("Scored", out["scored"])
    summary_cols[3].metric("Returned (top N)", len(out["candidates"]))

    results = out["candidates"]

    if not results:
        st.warning(
            "**Nessun candidato qualificato dopo full scoring.** "
            "Possibili cause: regime macro BEAR/STRONG_BEAR (gate weekly skippa "
            "i long), oppure soglie prefilter troppo strict per il momento di "
            "mercato. Prova a rilassare RSI min, allargare max dist 52w-high, "
            "o abbassare min score a 45 per vedere classe C."
        )
        st.stop()
else:
    with st.spinner(f"Scanning {len(tickers)} ticker…"):
        for t in tickers:
            r = cached_analyze(t, strategy_val)
            if r is not None:
                results.append(r)

    if not results:
        st.error(
            "Nessun ticker analizzabile. Verifica i simboli o la connessione di rete."
        )
        st.stop()

# ---------------------------------------------------------------------------
# Auto-add classe A+B alla watchlist (coerente col CLI propicks-momentum)
#   - Classe A → target = current_price per nuove entry (preserva target esistente)
#   - Classe B → senza target (il trader imposta livello pullback/breakout manuale)
# ---------------------------------------------------------------------------
actionable = [
    r for r in results if r.get("classification", "").startswith(("A", "B"))
]
if actionable:
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
            source="auto_scan",
        )
        (added if is_new else updated).append(r["ticker"])
    parts = []
    if added:
        parts.append(f"nuovi: {', '.join(added)}")
    if updated:
        parts.append(f"aggiornati: {', '.join(updated)}")
    st.toast(f"Watchlist (classe A+B) — {' · '.join(parts)}", icon="📋")

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
st.subheader("Risultati")
rows = []
for r in sorted(results, key=lambda x: x["score_composite"], reverse=True):
    regime = r.get("regime") or {}
    rows.append({
        "Ticker": r["ticker"],
        "Price": f"{r['price']:.2f}",
        "Score": r["score_composite"],
        "Class": r["classification"].split(" ")[0],
        "RSI": f"{r['rsi']:.1f}",
        "ATR%": fmt_pct(r.get("atr_pct")),
        "Dist52wH": fmt_pct(r.get("distance_from_high_pct")),
        "Perf 1w": fmt_pct(r.get("perf_1w")),
        "Perf 1m": fmt_pct(r.get("perf_1m")),
        "Perf 3m": fmt_pct(r.get("perf_3m")),
        "Regime": regime.get("regime", "N/D"),
        "Stop sugg.": f"{r['stop_suggested']:.2f}",
    })
st.dataframe(rows, width="stretch", hide_index=True)
st.caption(
    "Colonne: **Score**=tecnico 0-100 · **Class**=A/B/C/D · **RSI**=14d · "
    "**ATR%**=volatilità · **Dist52wH**=% dal max 52w · "
    "**Perf**=performance a 5/21/63gg. Apri la legenda in fondo per il dettaglio."
)

# ---------------------------------------------------------------------------
# Detail cards (expander per ticker)
# ---------------------------------------------------------------------------
st.subheader("Dettaglio per ticker")
for r in results:
    with st.expander(
        f"{r['ticker']}  —  score {r['score_composite']:.1f}  ({r['classification']})",
        expanded=len(results) == 1,
    ):
        cols = st.columns([1, 1, 1, 2])
        cols[0].metric("Prezzo", f"{r['price']:.2f}")
        cols[1].metric("Score", f"{r['score_composite']:.1f}", help=INDICATOR_HELP_STOCK["score"])
        cols[2].metric("RSI", f"{r['rsi']:.1f}", help=INDICATOR_HELP_STOCK["rsi"])
        cols[3].markdown(
            "**Regime:** " + regime_badge(r.get("regime")), unsafe_allow_html=True
        )

        scores = r.get("scores", {})
        st.markdown("**Sub-score**")
        sub_cols = st.columns(len(scores))
        for col, (k, v) in zip(sub_cols, scores.items(), strict=True):
            col.metric(k, f"{v:.0f}", help=INDICATOR_HELP_STOCK.get(k))

        rs = r.get("rs_vs_sector")
        if rs and rs.get("rs_ratio") is not None:
            st.caption(
                f"**RS vs settore** (informativo, non entra nel composite): "
                f"{r['ticker']} vs {rs.get('peer_etf', '?')} — "
                f"ratio {rs['rs_ratio']:.3f} · "
                f"slope {rs.get('rs_slope', 0):+.3f} · "
                f"score {rs.get('score', 0):.0f}/100"
            )

        st.markdown("**Indicatori tecnici**")
        tech_cols = st.columns(4)
        tech_cols[0].write(f"EMA fast: {r['ema_fast']:.2f}")
        tech_cols[1].write(f"EMA slow: {r['ema_slow']:.2f}")
        tech_cols[2].write(f"ATR: {r['atr']:.2f} ({fmt_pct(r.get('atr_pct'))})")
        tech_cols[3].write(f"52w high: {r['high_52w']:.2f}")

        # Pine inputs block — aiuta copia-incolla nei settings del Pine daily
        st.markdown("**TradingView Pine Inputs** (copia negli input del Pine daily):")
        st.code(
            f"entry_price  = {r['price']:.2f}\n"
            f"ema_fast     = {r['ema_fast']:.2f}\n"
            f"ema_slow     = {r['ema_slow']:.2f}\n"
            f"atr          = {r['atr']:.2f}\n"
            f"stop_suggest = {r['stop_suggested']:.2f}\n",
            language="text",
        )

        # -----------------------------------------------------------------
        # Prompt esterni (Perplexity) — cross-check indipendente a Claude
        # I prompt vivono in `ai/user_prompts.py` parametrizzati sul ticker.
        # -----------------------------------------------------------------
        with st.expander("Prompt Perplexity (copia-incolla)", expanded=False):
            from propicks.ai.user_prompts import (
                is_italian_ticker,
                perplexity_2a,
                perplexity_2b,
                perplexity_2c,
            )

            st.caption(
                "Cross-check indipendente a `--validate` Claude. "
                "Perplexity 2C (red flag 24h) va eseguito **sempre** prima "
                "di un'entry, anche con verdict CONFIRM."
            )
            _prompt_label_full = (
                "2B — Titoli italiani"
                if is_italian_ticker(r["ticker"])
                else "2A — Nuovi ingressi"
            )
            st.markdown(f"**{_prompt_label_full}** (analisi completa catalyst + rischi)")
            _prompt_full = (
                perplexity_2b(r["ticker"], r.get("name") or "")
                if is_italian_ticker(r["ticker"])
                else perplexity_2a(
                    r["ticker"],
                    r.get("name") or "",
                    (strategy_val or "").strip(),
                )
            )
            st.code(_prompt_full, language=None)

            st.markdown("**2C — Check pre-entry** (red flag ultime 24h)")
            st.code(perplexity_2c(r["ticker"]), language=None)

        # -----------------------------------------------------------------
        # Fallback validate completo — due varianti distinte, da scegliere
        # in base al destinatario (Perplexity multi-modello vs LLM generico
        # tipo Claude.ai / ChatGPT / Gemini direct). System prompt Anthropic
        # byte-equivalent in entrambi → compat piena con SDK / claude.ai.
        # -----------------------------------------------------------------
        with st.expander(
            "Prompt --validate completo per Perplexity (Sonar / Reasoning / Pro)",
            expanded=False,
        ):
            from datetime import date as _date

            from propicks.ai.user_prompts import perplexity_stock_validate_full

            st.caption(
                "Ottimizzato per Perplexity multi-modello (web search built-in). "
                "Header dedicato Sonar / Sonar Pro / Sonar Reasoning + Claude/GPT/Gemini "
                "via Perplexity Pro. Schema JSON con fallback `---JSON---` separator "
                "per modelli senza JSON mode strict."
            )
            _perp = perplexity_stock_validate_full(r, _date.today().isoformat())
            st.caption(
                f"~{len(_perp):,} caratteri · ~{len(_perp) // 4:,} token stimati."
            )
            st.code(_perp, language="markdown")

        with st.expander(
            "Prompt --validate completo per LLM generico (Claude.ai / ChatGPT / Gemini)",
            expanded=False,
        ):
            from datetime import date as _date

            from propicks.ai.user_prompts import llm_generic_stock_validate_full

            st.caption(
                "Ottimizzato per Claude.ai / console Anthropic / ChatGPT / Gemini "
                "direct. System prompt Anthropic byte-per-byte → compat piena con "
                "SDK Claude e claude.ai senza modifiche. Schema JSON strict (no "
                "fallback prosa). Usa questo quando vuoi un secondo parere su un "
                "modello SDK-grade invece che su Perplexity."
            )
            _llm = llm_generic_stock_validate_full(r, _date.today().isoformat())
            st.caption(
                f"~{len(_llm):,} caratteri · ~{len(_llm) // 4:,} token stimati. "
                "Verifica la context window del modello target prima di incollare."
            )
            st.code(_llm, language="markdown")

        # -----------------------------------------------------------------
        # Manual "→ Watchlist" — funziona per qualunque classe (anche C/D)
        # Utile quando il setup non è pronto ma vuoi tenerlo d'occhio
        # -----------------------------------------------------------------
        wl_col1, wl_col2 = st.columns([1, 3])
        if wl_col1.button(
            "📋 Aggiungi a watchlist",
            key=f"wl_btn_{r['ticker']}",
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
                source="manual",
            )
            verb = "Aggiunto" if is_new else "Aggiornato"
            wl_col2.success(f"{verb} {r['ticker']} in watchlist.")

        # -----------------------------------------------------------------
        # AI validation on-demand
        # -----------------------------------------------------------------
        if validate_ai:
            from propicks.ai.thesis_validator import validate_thesis

            with st.spinner(f"Validating {r['ticker']} con Claude…"):
                verdict = validate_thesis(r, force=force_ai, gate=not force_ai)

            if verdict is None:
                score = r.get("score_composite", 0)
                regime_obj = r.get("regime") or {}
                reason = []
                if score < 60:
                    reason.append(f"score {score:.1f} < 60")
                if regime_obj.get("entry_allowed") is False:
                    reason.append(f"regime {regime_obj.get('regime', '?')} blocca long")
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
                st.markdown(
                    f'<div style="background:{v_color};color:white;padding:8px 12px;'
                    f'border-radius:6px;display:inline-block;font-weight:600;">'
                    f'Claude: {v_verdict} · conviction {verdict.get("conviction_score", "?")}/10'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"R/R {verdict.get('reward_risk_ratio', '?')} · "
                    f"horizon {verdict.get('time_horizon', '?')} · "
                    f"alignment {verdict.get('alignment_with_technicals', '?')} · "
                    f"tactic {verdict.get('entry_tactic', '?')} · "
                    f"cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
                )

                if verdict.get("thesis_summary"):
                    st.markdown("**Tesi:** " + verdict["thesis_summary"])
                if verdict.get("key_risks"):
                    st.markdown("**Rischi chiave:**")
                    for risk in verdict["key_risks"]:
                        st.markdown(f"- {risk}")
                if verdict.get("suggested_adjustments"):
                    st.markdown("**Aggiustamenti suggeriti:**")
                    st.json(verdict["suggested_adjustments"])

st.divider()
render_indicator_legend("stock")
