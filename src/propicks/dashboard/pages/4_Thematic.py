"""Thematic ETF — RS-vs-parent + composite + corr kill-switch.

Equivalent UI di:
    propicks-themes [TICKER] [--rank] [--region {US|EU|WORLD|ALL}]
                    [--theme <label>] [--validate]
"""

from __future__ import annotations

import streamlit as st

# Bridge st.secrets → env vars (precede import propicks.config / .io / .ai).
from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import (
    THEMATIC_CORR_KILL_THRESHOLD,
    THEMATIC_MAX_POSITIONS,
    THEMATIC_PARENT_AGGREGATE_CAP_PCT,
    THEMATIC_STOP_LOSS_PCT,
)
from propicks.dashboard._shared import (
    INDICATOR_HELP_THEMATIC,
    cached_thematic_analyze,
    cached_thematic_rank,
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
    render_indicator_legend,
)
from propicks.domain.thematic_universe import list_themes

st.set_page_config(page_title="Thematic ETF · Propicks", layout="wide")
page_header(
    "Thematic ETF (sub-industry / cross-sector)",
    "Scoring tematici: RS-vs-parent 50% + abs momentum 25% + trend 15% + "
    "parent regime fit 10%. Kill-switch su correlation 60d ≥ 0.85 "
    "(alfa illusorio = leverage parent). Regime gate: STRONG_BEAR=0, BEAR cap 40.",
)
invariants_note()


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
with st.form("thematic_form", border=True):
    col1, col2 = st.columns([2, 2])
    mode = col1.radio(
        "Mode",
        options=("Ranking universo", "Singolo ticker"),
        horizontal=True,
    )
    region = col2.radio(
        "Region (filtro listing tematico)",
        options=("ALL", "US", "WORLD"),
        horizontal=True,
        help=(
            "US: SPDR-parent listing (SMH/XBI/CIBR/...) · "
            "WORLD: Borsa Italiana .MI con parent Xtrackers MSCI World "
            "(LOCK.MI/SMH.MI/XAIX.MI/...) · ALL: unione."
        ),
    )

    themes_available = list_themes()
    col3, col4, col5 = st.columns([2, 1, 1])
    theme_pick = col3.selectbox(
        "Filtro theme (opzionale)",
        options=["(tutti)"] + themes_available,
        index=0,
        help="Filtra per sub-industry: cybersecurity, biotech, semis, robotics_ai, ...",
    )
    validate_ai = col4.checkbox("Valida (Claude)", value=False)
    force_ai = col5.checkbox("Force", value=False, help="Bypass cache + gate")

    ticker_input = st.text_input(
        "Ticker (solo modalità singolo)",
        value="LOCK.MI",
        help="Ticker tematico registrato in THEMATIC_ETFS (case-insensitive).",
    ).strip().upper()

    submitted = st.form_submit_button("Esegui", type="primary", width="stretch")

if submitted:
    st.session_state["thematic_active"] = True
    st.session_state["thematic_mode"] = mode
    st.session_state["thematic_region"] = region
    st.session_state["thematic_theme"] = None if theme_pick == "(tutti)" else theme_pick
    st.session_state["thematic_ticker"] = ticker_input
    st.session_state["thematic_validate"] = validate_ai
    st.session_state["thematic_force"] = force_ai

if not st.session_state.get("thematic_active"):
    st.stop()

mode_active = st.session_state["thematic_mode"]
region_active = st.session_state["thematic_region"]
theme_active = st.session_state["thematic_theme"]
ticker_active = st.session_state["thematic_ticker"]
validate_active = st.session_state["thematic_validate"]
force_active = st.session_state["thematic_force"]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
results: list[dict]
if mode_active == "Ranking universo":
    with st.spinner(f"Ranking thematic universe (region={region_active}, theme={theme_active or 'all'})…"):
        results = cached_thematic_rank(region_active, theme_active)
else:
    if not ticker_active:
        st.error("Inserisci un ticker tematico.")
        st.stop()
    with st.spinner(f"Analyzing {ticker_active}…"):
        single = cached_thematic_analyze(ticker_active)
    if single is None:
        st.error(
            f"{ticker_active}: non è un thematic registrato o dati insufficienti. "
            f"Universo registrato: vedi `config.THEMATIC_ETFS`."
        )
        st.stop()
    single["rank"] = 1
    results = [single]

if not results:
    st.warning("Nessun risultato — universo vuoto, parent indisponibile o filtri stretti.")
    st.stop()

regime = results[0].get("regime")
regime_code = results[0].get("regime_code")

col_a, col_b = st.columns([2, 1])
col_a.markdown("**Regime macro:** " + regime_badge(regime), unsafe_allow_html=True)
col_b.caption(
    f"Tematici scorati: **{len(results)}** · Region: **{region_active}**"
    + (f" · Theme: **{theme_active}**" if theme_active else "")
)

st.divider()


# ---------------------------------------------------------------------------
# Ranking table
# ---------------------------------------------------------------------------
st.subheader("Ranking")
rows = []
for r in results:
    rs = r.get("rs_vs_parent", {})
    s = r.get("scores", {})
    corr = r.get("correlation_with_parent")
    flag = ""
    if r.get("corr_kill_applied"):
        flag = "⚠ CORR-KILL"
    elif r.get("regime_gate_applied"):
        flag = "⚠ REGIME-GATE"
    rows.append({
        "#": r["rank"],
        "Ticker": r["ticker"],
        "Theme": r["theme_label"],
        "Parent": r["parent_ticker"],
        "Region": r.get("region", "—"),
        "Score": r["score_composite"],
        "Class": r["classification"].split(" ")[0],
        "RS-vs-P": f"{s.get('rs_vs_parent', 0):.0f}",
        "RS ratio": f"{rs['rs_ratio']:.3f}" if rs.get("rs_ratio") is not None else "—",
        "Abs mom": f"{s.get('abs_momentum', 0):.0f}",
        "Trend": f"{s.get('trend', 0):.0f}",
        "P-Reg": f"{s.get('parent_regime_fit', 0):.0f}",
        "Corr 60d": f"{corr:.2f}" if isinstance(corr, (int, float)) else "—",
        "Perf 3m": fmt_pct(r.get("perf_3m")),
        "Price": f"{r['price']:.2f}",
        "Flag": flag,
    })
st.dataframe(rows, width="stretch", hide_index=True)
st.caption(
    f"**RS-vs-P**=50% peso (RS theme/parent) · **Abs mom**=25% · **Trend**=15% · "
    f"**P-Reg**=10% · **Corr 60d**=correlation kill threshold {THEMATIC_CORR_KILL_THRESHOLD}. "
    f"⚠ CORR-KILL = composite forzato a 0 (alfa illusorio). "
    f"⚠ REGIME-GATE = STRONG_BEAR=0 / BEAR cap 40."
)


# ---------------------------------------------------------------------------
# Watchlist quick-add
# ---------------------------------------------------------------------------
wl_col1, wl_col2, wl_col3 = st.columns([2, 1, 2])
wl_pick = wl_col1.selectbox(
    "Aggiungi tematico a watchlist",
    options=[""] + [r["ticker"] for r in results],
    key="thematic_wl_pick",
)
if wl_col2.button("📋 Watchlist", type="secondary", disabled=not wl_pick):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    picked = next((r for r in results if r["ticker"] == wl_pick), None)
    if picked:
        wl = load_watchlist()
        _, is_new = add_to_watchlist(
            wl,
            picked["ticker"],
            score_at_add=picked.get("score_composite"),
            regime_at_add=picked.get("regime"),
            classification_at_add=picked.get("classification"),
            source="thematic",
        )
        verb = "Aggiunto" if is_new else "Aggiornato"
        wl_col3.success(f"{verb} {picked['ticker']} in watchlist.")


# ---------------------------------------------------------------------------
# Top pick detail
# ---------------------------------------------------------------------------
top = results[0]
st.subheader(f"Top pick: {top['ticker']} — {top['name']}")

cols = st.columns(4)
cols[0].metric(
    "Score composite",
    f"{top['score_composite']:.1f}",
    help=INDICATOR_HELP_THEMATIC["score_composite"],
)
cols[1].metric(
    "Classification",
    top["classification"].split(" ")[0],
    help=INDICATOR_HELP_THEMATIC["classification"],
)
cols[2].metric(
    "Theme",
    top["theme_label"],
    help=INDICATOR_HELP_THEMATIC["theme_label"],
)
cols[3].metric(
    "Parent",
    top["parent_ticker"],
    help=INDICATOR_HELP_THEMATIC["parent_ticker"],
)

sub_cols = st.columns(4)
sub_cols[0].metric(
    "RS-vs-parent (50%)",
    f"{top['scores']['rs_vs_parent']:.0f}",
    help=INDICATOR_HELP_THEMATIC["rs_vs_parent"],
)
sub_cols[1].metric(
    "Abs momentum (25%)",
    f"{top['scores']['abs_momentum']:.0f}",
    help=INDICATOR_HELP_THEMATIC["abs_momentum"],
)
sub_cols[2].metric(
    "Trend (15%)",
    f"{top['scores']['trend']:.0f}",
    help=INDICATOR_HELP_THEMATIC["trend"],
)
sub_cols[3].metric(
    "Parent regime fit (10%)",
    f"{top['scores']['parent_regime_fit']:.0f}",
    help=INDICATOR_HELP_THEMATIC["parent_regime_fit"],
)

# Corr + gates row
gate_cols = st.columns(4)
corr_val = top.get("correlation_with_parent")
corr_str = f"{corr_val:.3f}" if isinstance(corr_val, (int, float)) else "n/a"
gate_cols[0].metric(
    "Corr 60d",
    corr_str,
    help=INDICATOR_HELP_THEMATIC["corr_60d"],
)
gate_cols[1].metric(
    "Corr kill",
    "✓ KILL" if top.get("corr_kill_applied") else "—",
    help=INDICATOR_HELP_THEMATIC["corr_kill"],
)
gate_cols[2].metric(
    "Regime gate",
    "✓ GATE" if top.get("regime_gate_applied") else "—",
    help=INDICATOR_HELP_THEMATIC["regime_gate"],
)
gate_cols[3].metric(
    f"Stop sugg. (-{int(THEMATIC_STOP_LOSS_PCT * 100)}%)",
    f"{top['stop_suggested']:.2f}",
)

st.caption(
    f"Bucket constraint: max **{THEMATIC_MAX_POSITIONS}** tematici aperti, "
    f"`weight(theme) + weight(parent_ETF) ≤ {THEMATIC_PARENT_AGGREGATE_CAP_PCT * 100:.0f}%`. "
    f"Stop hard {int(THEMATIC_STOP_LOSS_PCT * 100)}%."
)


# ---------------------------------------------------------------------------
# Prompt --validate completo (selettore target LLM) — copia-incolla per Sonar
# nativo o LLM generico (Claude.ai / ChatGPT / Gemini). Indipendente dal
# `--validate` Anthropic SDK, utile quando vuoi cross-check senza spendere
# budget Anthropic o serve web search live (AUM flows, holdings %).
# ---------------------------------------------------------------------------
with st.expander(
    "Prompt --validate completo (selettore target LLM)",
    expanded=False,
):
    from datetime import date as _date

    from propicks.ai.user_prompts import (
        llm_generic_thematic_validate_full,
        sonar_thematic_validate_full,
    )
    from propicks.config import THEMATIC_ETFS

    _target_label = st.radio(
        "Target LLM",
        options=[
            "Sonar (Perplexity nativo) — Recommended",
            "Claude.ai / ChatGPT / Gemini diretto",
        ],
        index=0,
        horizontal=False,
        key="thematic_prompt_target",
        help=(
            "Sonar nativo: schema in cima, persona thematic distillata, "
            "regole computabili (REJECT su corr-kill / STRONG_BEAR), "
            "constraint esplicito su alternative_ticker (solo same-cohort). "
            "LLM diretto: system prompt Anthropic byte-per-byte."
        ),
    )

    _today = _date.today().isoformat()

    # Candidates per alternative_ticker: stesso theme_label, escluso top.
    _theme_now = top.get("theme_label")
    _candidates = [
        {
            "ticker": tk,
            "theme_label": meta.get("theme_label"),
            "parent_ticker": meta.get("parent_ticker"),
        }
        for tk, meta in THEMATIC_ETFS.items()
        if meta.get("theme_label") == _theme_now and tk.upper() != top["ticker"].upper()
    ]

    if _target_label.startswith("Sonar"):
        st.caption(
            f"Ottimizzato per Sonar / Sonar Pro / Sonar Reasoning. Schema "
            f"JSON in cima, hard rules computabili (REJECT su "
            f"`corr_kill_applied` o STRONG_BEAR, CAUTION se LATE+crowding<5). "
            f"**Constraint esplicito** su `alternative_ticker`: "
            f"{len(_candidates)} same-cohort candidates "
            f"(theme=**{_theme_now}**). **Default consigliato**."
        )
        _prompt = sonar_thematic_validate_full(
            top,
            candidates=_candidates,
            as_of_date=_today,
        )
    else:
        st.caption(
            "Per Claude.ai / console Anthropic / ChatGPT / Gemini direct. "
            "System prompt Anthropic byte-per-byte → compat piena con SDK "
            "Claude e claude.ai. Schema JSON strict. NO constraint esplicito "
            "su alternative_ticker — il modello vede solo il system prompt "
            "originale, può confabulare ticker (sanity layer Anthropic SDK "
            "filtra ma copia-incolla no)."
        )
        _prompt = llm_generic_thematic_validate_full(top, as_of_date=_today)

    st.caption(
        f"~{len(_prompt):,} caratteri · ~{len(_prompt) // 4:,} token stimati."
    )
    st.code(_prompt, language="markdown")


# ---------------------------------------------------------------------------
# AI validation
# ---------------------------------------------------------------------------
if validate_active:
    from propicks.ai.thematic_validator import validate_thematic_thesis

    st.divider()
    st.subheader("Thematic view — Claude")
    with st.spinner(f"Validation {top['ticker']} in corso…"):
        verdict = validate_thematic_thesis(
            top,
            force=force_active,
            gate=not force_active,
        )

    if verdict is None:
        if top.get("corr_kill_applied"):
            st.warning(
                "Skipped: correlation kill triggered (≥0.85). Composite=0, "
                "alfa tematico illusorio. Force per chiamare comunque."
            )
        elif regime_code in (1, 2):
            st.info(
                f"Skipped: regime {regime['regime'] if regime else '?'} ({regime_code}/5) — "
                "tematici skip BEAR/STRONG_BEAR. Force per override."
            )
        elif top.get("score_composite", 0) < 60:
            st.info(
                f"Skipped: score {top['score_composite']:.1f} < 60 (gate). "
                "Force per override."
            )
        else:
            st.warning("Validation non disponibile (errore API o cache invalida).")
    else:
        v_color = {
            "CONFIRM": "#16a34a",
            "CAUTION": "#ca8a04",
            "REJECT": "#dc2626",
        }.get(verdict.get("verdict", ""), "#64748b")
        st.markdown(
            f'<div style="background:{v_color};color:white;padding:8px 12px;'
            f'border-radius:6px;display:inline-block;font-weight:600;">'
            f'Claude: {verdict.get("verdict", "?")} · '
            f'conviction {verdict.get("conviction_score", "?")}/10 · '
            f'stage {verdict.get("theme_stage", "?")} · '
            f'horizon {verdict.get("time_horizon_weeks", "?")}w'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Alignment w/ parent: {verdict.get('alignment_with_parent', '?')} · "
            f"Cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
        )
        if verdict.get("thematic_summary"):
            st.markdown("**Sintesi:** " + verdict["thematic_summary"])
        if verdict.get("alternative_ticker"):
            st.markdown("**Alternative wrapper:** " + str(verdict["alternative_ticker"]))
        if verdict.get("entry_tactic"):
            st.markdown("**Tactic:** " + str(verdict["entry_tactic"]))
        if verdict.get("catalysts"):
            st.markdown("**Catalysts:**")
            for c in verdict["catalysts"]:
                st.markdown(f"- {c}")
        if verdict.get("crowding_read"):
            st.markdown("**Crowding:** " + verdict["crowding_read"])
        if verdict.get("concentration_read"):
            st.markdown("**Concentration:** " + verdict["concentration_read"])
        if verdict.get("bull_case"):
            st.markdown("**Bull case:**")
            for b in verdict["bull_case"]:
                st.markdown(f"- {b}")
        if verdict.get("bear_case"):
            st.markdown("**Bear case:**")
            for b in verdict["bear_case"]:
                st.markdown(f"- {b}")
        if verdict.get("invalidation_triggers"):
            st.markdown("**Invalidation triggers:**")
            for t in verdict["invalidation_triggers"]:
                st.markdown(f"- {t}")


st.divider()
render_indicator_legend("thematic")
