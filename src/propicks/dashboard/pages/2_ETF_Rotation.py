"""Rotazione settoriale ETF — ranking universo + allocazione proposta.

Equivalent UI di ``propicks-rotate --region <R> [--allocate] [--validate]``.
"""

from __future__ import annotations

import streamlit as st

# Bridge st.secrets → env vars (deve precedere ogni import propicks.config / .io / .ai).
from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import get_etf_benchmark
from propicks.dashboard._shared import (
    INDICATOR_HELP_ETF,
    cached_rank,
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
    render_indicator_legend,
)
from propicks.domain.etf_scoring import suggest_allocation

st.set_page_config(page_title="ETF Rotation · Propicks", layout="wide")
page_header(
    "ETF Sector Rotation",
    "Ranking universo sector ETF (RS 40% + regime 30% + momentum 20% + trend 10%). "
    "Regime hard-gate: STRONG_BEAR non-favored → 0, BEAR non-favored → cap 50.",
)
invariants_note()


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
with st.form("rotate_form", border=True):
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    region = col1.radio(
        "Region",
        options=("WORLD", "US", "ALL"),
        horizontal=True,
        index=0,
        help=(
            "WORLD (default): Xtrackers MSCI World (XDW*/XWTS .DE + .MI Borsa "
            "Italiana + IQQ6 Real Estate proxy) — operativo retail EU. "
            "US: SPDR Select Sector (XL*) — reference, lunga storia. "
            "ALL: unione (sconsigliato, benchmark non uniforme)."
        ),
    )
    top_n = col2.slider("Top N", min_value=1, max_value=11, value=3)
    allocate = col3.checkbox("Allocazione", value=True)
    validate_ai = col4.checkbox("Valida (Claude)", value=False)
    force_ai = st.checkbox(
        "Force validate (bypassa skip STRONG_BEAR + cache 48h)", value=False
    )
    submitted = st.form_submit_button("Esegui ranking", type="primary", width="stretch")

# Persistenza submit-flag in session_state: senza, ogni widget post-submit
# (es. il radio "Target LLM" nei prompt expander) causa un Streamlit rerun
# in cui ``submitted`` torna False → ``st.stop()`` collassa il ranking e
# il prompt selector sparisce. La key è scoped per pagina.
if submitted:
    st.session_state["etf_rotate_active"] = True

if not st.session_state.get("etf_rotate_active"):
    st.stop()

# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
with st.spinner(f"Ranking universo {region} (benchmark {get_etf_benchmark(region)})…"):
    ranked = cached_rank(region)

if not ranked:
    st.error("Ranking vuoto — universo non disponibile o errore rete.")
    st.stop()

regime = ranked[0].get("regime")
regime_code = ranked[0].get("regime_code")
bench = get_etf_benchmark(region)

col_a, col_b = st.columns([2, 1])
col_a.markdown("**Regime macro:** " + regime_badge(regime), unsafe_allow_html=True)
col_b.caption(f"Universo: **{region}** · Benchmark RS: **{bench}** · ETF scorati: {len(ranked)}")

st.divider()

# ---------------------------------------------------------------------------
# Ranking table
# ---------------------------------------------------------------------------
st.subheader("Ranking")
rows = []
for r in ranked:
    rs = r.get("rs", {})
    trend = r.get("trend", {})
    rows.append({
        "#": r["rank"],
        "Ticker": r["ticker"],
        "Sector": r["sector_key"],
        "Region": r["region"],
        "Score": r["score_composite"],
        "Class": r["classification"].split(" ")[0],
        "RS": f"{rs.get('score', 0):.0f}",
        "RS ratio": f"{rs['rs_ratio']:.3f}" if rs.get("rs_ratio") is not None else "—",
        "Regime fit": f"{r.get('regime_fit_score', 0):.0f}",
        "Abs mom": f"{r.get('abs_momentum_score', 0):.0f}",
        "Trend": f"{trend.get('score', 0):.0f}",
        "Perf 3m": fmt_pct(r.get("perf_3m")),
        "Price": f"{r['price']:.2f}",
        "Cap?": "✓" if r.get("regime_cap_applied") else "",
    })
st.dataframe(rows, width="stretch", hide_index=True)
st.caption(
    "Colonne: **Score**=composite 0-100 · **Class**=A/B/C/D · "
    "**RS**=40% peso · **Regime fit**=30% · **Abs mom**=20% · **Trend**=10% · "
    "**Cap?**=✓ se composite ridotto dal regime hard-gate. "
    "Apri la legenda in fondo per il dettaglio delle formule."
)

# ---------------------------------------------------------------------------
# Charts: composite score + sub-score breakdown
# ---------------------------------------------------------------------------
if len(ranked) >= 2:
    import plotly.graph_objects as go

    st.divider()
    col_c, col_s = st.columns(2)

    with col_c:
        # Composite score bar chart (all ranked, colored by class)
        sorted_by_score = sorted(ranked, key=lambda x: x["score_composite"], reverse=True)
        tk_c = [r["ticker"] for r in sorted_by_score]
        scores_c = [r["score_composite"] for r in sorted_by_score]
        cls_c = [r["classification"].split(" ")[0] for r in sorted_by_score]
        cap_c = [r.get("regime_cap_applied", False) for r in sorted_by_score]

        # Color: A green / B lime / C amber / D red
        cmap_c = {"A": "#16a34a", "B": "#10b981", "C": "#ca8a04", "D": "#dc2626"}
        bar_colors = [cmap_c.get(c, "#94a3b8") for c in cls_c]
        # Highlight cap with white border
        line_widths = [2 if cap else 0 for cap in cap_c]
        line_colors = ["#fef3c7" if cap else "white" for cap in cap_c]

        fig_c = go.Figure()
        fig_c.add_trace(go.Bar(
            x=tk_c, y=scores_c,
            marker=dict(
                color=bar_colors,
                line=dict(color=line_colors, width=line_widths),
            ),
            text=[f"{s:.0f}{' ⚠' if cap else ''}" for s, cap in zip(scores_c, cap_c, strict=True)],
            textposition="outside",
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Score %{y:.1f}<br>"
                "Class %{customdata[0]}<br>"
                "Regime cap %{customdata[1]}<extra></extra>"
            ),
            customdata=list(zip(cls_c, cap_c, strict=True)),
        ))
        # Class boundary hlines
        for thr, lbl, c in [(70, "A", "#16a34a"), (55, "B", "#10b981"), (40, "C", "#ca8a04")]:
            fig_c.add_hline(
                y=thr, line_dash="dot", line_color=c, opacity=0.5,
                annotation_text=lbl, annotation_position="right",
            )
        fig_c.update_layout(
            title=dict(
                text=f"Composite score (n={len(ranked)})",
                x=0.5, xanchor="center", font=dict(size=13),
            ),
            xaxis_title="", yaxis_title="Composite 0-100",
            yaxis=dict(range=[0, 105]),
            height=360, showlegend=False,
            margin=dict(l=20, r=20, t=50, b=20),
        )
        st.plotly_chart(fig_c, width="stretch")
        st.caption(
            "Bar color: 🟢 A (≥70) · 🟢 B (55-70) · 🟡 C (40-55) · 🔴 D (<40). "
            "Border giallo + ⚠ = composite capped dal regime hard-gate."
        )

    with col_s:
        # Sub-score grouped bar chart (top 5)
        top_n_chart = min(5, len(ranked))
        top_subset = sorted(ranked, key=lambda x: x["score_composite"], reverse=True)[:top_n_chart]
        tk_s = [r["ticker"] for r in top_subset]
        rs_s = [r.get("rs", {}).get("score", 0) for r in top_subset]
        regime_s = [r.get("regime_fit_score", 0) for r in top_subset]
        mom_s = [r.get("abs_momentum_score", 0) for r in top_subset]
        trend_s = [r.get("trend", {}).get("score", 0) for r in top_subset]

        fig_s = go.Figure()
        fig_s.add_trace(go.Bar(
            name="RS (40%)", x=tk_s, y=rs_s,
            marker=dict(color="#3b82f6"),
            hovertemplate="<b>%{x}</b><br>RS %{y:.0f}<extra></extra>",
        ))
        fig_s.add_trace(go.Bar(
            name="Regime fit (30%)", x=tk_s, y=regime_s,
            marker=dict(color="#10b981"),
            hovertemplate="<b>%{x}</b><br>Regime fit %{y:.0f}<extra></extra>",
        ))
        fig_s.add_trace(go.Bar(
            name="Abs mom (20%)", x=tk_s, y=mom_s,
            marker=dict(color="#f59e0b"),
            hovertemplate="<b>%{x}</b><br>Abs mom %{y:.0f}<extra></extra>",
        ))
        fig_s.add_trace(go.Bar(
            name="Trend (10%)", x=tk_s, y=trend_s,
            marker=dict(color="#a855f7"),
            hovertemplate="<b>%{x}</b><br>Trend %{y:.0f}<extra></extra>",
        ))
        fig_s.update_layout(
            title=dict(
                text=f"Sub-score breakdown (top {top_n_chart})",
                x=0.5, xanchor="center", font=dict(size=13),
            ),
            barmode="group",
            xaxis_title="", yaxis_title="Sub-score 0-100",
            yaxis=dict(range=[0, 105]),
            height=360,
            margin=dict(l=20, r=20, t=50, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
        )
        st.plotly_chart(fig_s, width="stretch")
        st.caption(
            "Grouped bar per pillar. ETF con RS=100 + Regime fit=100 = "
            "leadership ottimale (top pick natural). RS basso ma Regime fit/Trend "
            "alti = settore difensivo in regime adverse."
        )

# ---------------------------------------------------------------------------
# Watchlist quick-add (manuale, nessun auto-add: la rotation è rank→alloc,
# la watchlist serve solo per monitorare ETF che vuoi tenere d'occhio)
# ---------------------------------------------------------------------------
wl_col1, wl_col2, wl_col3 = st.columns([2, 1, 2])
wl_pick = wl_col1.selectbox(
    "Aggiungi ETF a watchlist",
    options=[""] + [r["ticker"] for r in ranked],
    key="rotate_wl_pick",
    help="Selezione manuale: la rotation non fa auto-add, gli ETF top vanno ad allocazione diretta.",
)
if wl_col2.button("📋 Watchlist", type="secondary", disabled=not wl_pick):
    from propicks.io.watchlist_store import add_to_watchlist, load_watchlist

    picked = next((r for r in ranked if r["ticker"] == wl_pick), None)
    if picked:
        wl = load_watchlist()
        _, is_new = add_to_watchlist(
            wl,
            picked["ticker"],
            score_at_add=picked.get("score_composite"),
            regime_at_add=picked.get("regime"),
            classification_at_add=picked.get("classification"),
            source="manual",
        )
        verb = "Aggiunto" if is_new else "Aggiornato"
        wl_col3.success(f"{verb} {picked['ticker']} in watchlist.")

# ---------------------------------------------------------------------------
# Top pick detail
# ---------------------------------------------------------------------------
top = ranked[0]
st.subheader(f"Top pick: {top['ticker']} — {top['name']}")
cols = st.columns(4)
cols[0].metric(
    "Score composite",
    f"{top['score_composite']:.1f}",
    help=INDICATOR_HELP_ETF["score_composite"],
)
cols[1].metric(
    "Classification",
    top["classification"].split(" ")[0],
    help=INDICATOR_HELP_ETF["classification"],
)
cols[2].metric("Sector", top["sector_key"], help=INDICATOR_HELP_ETF["sector"])
cols[3].metric(
    "Perf 3m", fmt_pct(top.get("perf_3m")), help=INDICATOR_HELP_ETF["perf_3m"]
)

sub_cols = st.columns(4)
for col, (k, v) in zip(sub_cols, top.get("scores", {}).items(), strict=True):
    col.metric(k, f"{v:.0f}", help=INDICATOR_HELP_ETF.get(k))

# ---------------------------------------------------------------------------
# Allocation proposal
# ---------------------------------------------------------------------------
allocation = None
if allocate:
    st.divider()
    st.subheader("Allocazione proposta")
    allocation = suggest_allocation(ranked, top_n=top_n)
    note = allocation.get("note")
    if note:
        st.info(note)

    positions = allocation.get("positions", [])
    if positions:
        alloc_rows = [
            {
                "Ticker": p["ticker"],
                "Sector": p["sector_key"],
                "Score": p["score"],
                "Class": p["classification"].split(" ")[0],
                "Alloc %": fmt_pct(p["allocation_pct"]),
                "Price": f"{p['price']:.2f}",
                "Stop sugg.": f"{p['stop_suggested']:.2f}",
            }
            for p in positions
        ]
        st.dataframe(alloc_rows, width="stretch", hide_index=True)
        agg = allocation.get("aggregate_pct", 0)
        st.caption(
            f"Aggregato: {fmt_pct(agg)} · Cash residuo ETF bucket: {fmt_pct(0.60 - agg)}"
        )

# ---------------------------------------------------------------------------
# Prompt esterni (Perplexity ETF rotation) — cross-check sintetico stile
# perplexity_2a per stock. Catalyst-focused, prosa free-form, niente JSON.
# ---------------------------------------------------------------------------
with st.expander("Prompt Perplexity rotation (copia-incolla)", expanded=False):
    from propicks.ai.user_prompts import perplexity_etf_rotation

    st.caption(
        "Cross-check macro/catalyst indipendente a `--validate` Claude. "
        "Focus su rotation flows, sector breadth, FOMC/CPI imminent, "
        "narrative shift. Output prosa free-form (per il payload completo "
        "con schema JSON vedi il fallback più sotto)."
    )
    st.markdown("**ETF rotation — analisi macro/catalyst** (top-3 personalizzato)")
    st.code(perplexity_etf_rotation(ranked, region), language=None)

# ---------------------------------------------------------------------------
# Fallback validate completo — selettore target LLM con 3 varianti.
# Vedi pages/1_Momentum.py per il razionale completo dei trade-off.
# La variante Sonar nativo include constraint esplicito su alternative_sector
# (lista universo - top-3) per evitare ticker confabulati.
# ---------------------------------------------------------------------------
with st.expander(
    "Prompt --validate completo (selettore target LLM)",
    expanded=False,
):
    from datetime import date as _date

    from propicks.ai.user_prompts import (
        llm_generic_etf_validate_full,
        perplexity_etf_validate_full,
        sonar_etf_validate_full,
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
        key="etf_prompt_target",
        help=(
            "Sonar nativo: prompt distillato + constraint esplicito su "
            "alternative_sector (no ticker inventati). Perplexity Pro: "
            "system prompt Claude completo. LLM diretto: Anthropic byte-per-byte."
        ),
    )

    _today = _date.today().isoformat()
    if _target_label.startswith("Sonar"):
        st.caption(
            "Ottimizzato per Sonar / Sonar Pro / Sonar Reasoning. Schema "
            "JSON in cima, persona macro strategist distillata, regole "
            "computabili (REJECT in STRONG_BEAR, CAUTION se LATE+breadth<5). "
            "**Constraint esplicito** su `alternative_sector` (solo universo "
            "rimanente, no ticker inventati). **Default consigliato**."
        )
        _prompt = sonar_etf_validate_full(
            ranked=ranked,
            allocation=allocation,
            as_of_date=_today,
            region=region,
            benchmark=bench,
        )
    elif _target_label.startswith("Perplexity Pro"):
        st.caption(
            "Per Claude / GPT / Gemini eseguiti via Perplexity Pro. System "
            "prompt Anthropic completo con sezione `# Web search usage` "
            "rimossa (Perplexity ha search nativa). Schema JSON con fallback "
            "`---JSON---`. NO constraint esplicito su alternative_sector — "
            "il modello vede solo il system prompt originale."
        )
        _prompt = perplexity_etf_validate_full(
            ranked=ranked,
            allocation=allocation,
            as_of_date=_today,
            region=region,
            benchmark=bench,
        )
    else:
        st.caption(
            "Per Claude.ai / console Anthropic / ChatGPT / Gemini direct. "
            "System prompt Anthropic byte-per-byte → compat piena con SDK "
            "Claude e claude.ai. Schema JSON strict. Verifica la context "
            "window del modello target prima di incollare."
        )
        _prompt = llm_generic_etf_validate_full(
            ranked=ranked,
            allocation=allocation,
            as_of_date=_today,
            region=region,
            benchmark=bench,
        )

    st.caption(
        f"~{len(_prompt):,} caratteri · ~{len(_prompt) // 4:,} token stimati."
    )
    st.code(_prompt, language="markdown")

# ---------------------------------------------------------------------------
# AI validation (macro view)
# ---------------------------------------------------------------------------
if validate_ai:
    from propicks.ai.etf_validator import validate_rotation

    st.divider()
    st.subheader("Macro view — Claude")
    with st.spinner("Validazione macro in corso…"):
        verdict = validate_rotation(
            ranked,
            allocation=allocation,
            region=region,
            force=force_ai,
            skip_in_strong_bear=not force_ai,
        )

    if verdict is None:
        if regime_code == 1 and not force_ai:
            st.info(
                "Skipped: regime STRONG_BEAR → allocazione flat è la risposta ovvia. "
                "Spunta *Force* per forzare la chiamata."
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
            f'stage {verdict.get("stage", "?")} · '
            f'horizon {verdict.get("rebalance_horizon_weeks", "?")}w'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Alignment: {verdict.get('alignment_with_ranking', '?')} · "
            f"Cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
        )
        if verdict.get("rotation_summary"):
            st.markdown("**Sintesi:** " + verdict["rotation_summary"])

        if verdict.get("top_sector_verdict"):
            st.markdown("**Top sector:** " + verdict["top_sector_verdict"])
        if verdict.get("alternative_sector"):
            st.markdown("**Alternative:** " + verdict["alternative_sector"])
        if verdict.get("entry_tactic"):
            st.markdown("**Tactic:** " + str(verdict["entry_tactic"]))
        if verdict.get("macro_drivers"):
            st.markdown("**Macro drivers:**")
            drivers = verdict["macro_drivers"]
            if isinstance(drivers, list):
                for d in drivers:
                    st.markdown(f"- {d}")
            else:
                st.write(drivers)
        if verdict.get("breadth_read"):
            st.markdown("**Breadth:** " + verdict["breadth_read"])
        if verdict.get("positioning_read"):
            st.markdown("**Positioning:** " + verdict["positioning_read"])
        if verdict.get("bear_case"):
            st.markdown("**Bear case:**")
            for r in verdict["bear_case"]:
                st.markdown(f"- {r}")
        if verdict.get("invalidation_triggers"):
            st.markdown("**Invalidation triggers:**")
            for r in verdict["invalidation_triggers"]:
                st.markdown(f"- {r}")

st.divider()
render_indicator_legend("etf")
