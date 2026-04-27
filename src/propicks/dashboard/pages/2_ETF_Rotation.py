"""Rotazione settoriale ETF — ranking universo + allocazione proposta.

Equivalent UI di ``propicks-rotate --region <R> [--allocate] [--validate]``.
"""

from __future__ import annotations

import streamlit as st

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
        options=("US", "EU", "WORLD", "ALL"),
        horizontal=True,
        help=(
            "US: SPDR Select Sector (XL*) · "
            "EU: SPDR UCITS (ZPD*.DE) · "
            "WORLD: Xtrackers MSCI World (XDW*/XWTS/XZRE) · "
            "ALL: unione — ranking misto, rumoroso"
        ),
    )
    top_n = col2.slider("Top N", min_value=1, max_value=11, value=3)
    allocate = col3.checkbox("Allocazione", value=True)
    validate_ai = col4.checkbox("Valida (Claude)", value=False)
    force_ai = st.checkbox(
        "Force validate (bypassa skip STRONG_BEAR + cache 48h)", value=False
    )
    submitted = st.form_submit_button("Esegui ranking", type="primary", width="stretch")

if not submitted:
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
# Fallback validate completo — due varianti distinte. System prompt
# Anthropic byte-equivalent in entrambi → compat piena con SDK / claude.ai.
# ---------------------------------------------------------------------------
with st.expander(
    "Prompt --validate completo per Perplexity (Sonar / Reasoning / Pro)",
    expanded=False,
):
    from datetime import date as _date

    from propicks.ai.user_prompts import perplexity_etf_validate_full

    st.caption(
        "Ottimizzato per Perplexity multi-modello (web search built-in). "
        "Header dedicato Sonar / Sonar Pro / Reasoning + Claude/GPT/Gemini via "
        "Perplexity Pro. Schema JSON con fallback `---JSON---` separator per "
        "modelli senza JSON mode strict."
    )
    _perp = perplexity_etf_validate_full(
        ranked=ranked,
        allocation=allocation,
        as_of_date=_date.today().isoformat(),
        region=region,
        benchmark=bench,
    )
    st.caption(
        f"~{len(_perp):,} caratteri · ~{len(_perp) // 4:,} token stimati."
    )
    st.code(_perp, language="markdown")

with st.expander(
    "Prompt --validate completo per LLM generico (Claude.ai / ChatGPT / Gemini)",
    expanded=False,
):
    from datetime import date as _date

    from propicks.ai.user_prompts import llm_generic_etf_validate_full

    st.caption(
        "Ottimizzato per Claude.ai / console Anthropic / ChatGPT / Gemini "
        "direct. System prompt Anthropic byte-per-byte → compat piena con "
        "SDK Claude e claude.ai senza modifiche. Schema JSON strict. Usa "
        "questo per un secondo parere SDK-grade sulla rotation macro view."
    )
    _llm = llm_generic_etf_validate_full(
        ranked=ranked,
        allocation=allocation,
        as_of_date=_date.today().isoformat(),
        region=region,
        benchmark=bench,
    )
    st.caption(
        f"~{len(_llm):,} caratteri · ~{len(_llm) // 4:,} token stimati. "
        "Verifica la context window del modello target prima di incollare."
    )
    st.code(_llm, language="markdown")

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
