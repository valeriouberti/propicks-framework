"""Scanner tecnico + validazione AI opzionale.

Equivalent UI di ``propicks-scan [TICKER ...] [--validate]``.
"""

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import (
    cached_analyze,
    fmt_pct,
    invariants_note,
    page_header,
    regime_badge,
    score_badge,
)

st.set_page_config(page_title="Scanner · Propicks", layout="wide")
page_header(
    "Scanner",
    "Analisi tecnica single-ticker o batch. Validazione Claude opzionale (gate regime + score).",
)
invariants_note()

STRATEGIES = ("", "TechTitans", "DominaDow", "BattiSP500", "MiglioriItaliane", "Altro")

with st.form("scanner_form", border=True):
    tickers_raw = st.text_input(
        "Ticker (separati da spazio o virgola)",
        placeholder="AAPL MSFT NVDA  oppure  ENI.MI ISP.MI",
    )
    col1, col2, col3 = st.columns([2, 1, 1])
    strategy = col1.selectbox("Strategy (opzionale)", STRATEGIES, index=0)
    validate_ai = col2.checkbox("Valida con Claude", value=False)
    force_ai = col3.checkbox("Force (bypassa gate + cache)", value=False)
    submitted = st.form_submit_button("Analizza", type="primary", use_container_width=True)

if not submitted:
    st.stop()

tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]
if not tickers:
    st.warning("Inserisci almeno un ticker.")
    st.stop()

strategy_val = strategy or None

# ---------------------------------------------------------------------------
# Batch scan
# ---------------------------------------------------------------------------
results: list[dict] = []
with st.spinner(f"Scanning {len(tickers)} ticker…"):
    for t in tickers:
        r = cached_analyze(t, strategy_val)
        if r is not None:
            results.append(r)

if not results:
    st.error("Nessun ticker analizzabile. Verifica i simboli o la connessione di rete.")
    st.stop()

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
st.subheader("Risultati")
rows = []
for r in results:
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
st.dataframe(rows, use_container_width=True, hide_index=True)

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
        cols[1].metric("Score", f"{r['score_composite']:.1f}")
        cols[2].metric("RSI", f"{r['rsi']:.1f}")
        cols[3].markdown(
            "**Regime:** " + regime_badge(r.get("regime")), unsafe_allow_html=True
        )

        scores = r.get("scores", {})
        st.markdown("**Sub-score**")
        sub_cols = st.columns(len(scores))
        for col, (k, v) in zip(sub_cols, scores.items()):
            col.metric(k, f"{v:.0f}")

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
                    f'Claude: {v_verdict} · conviction {verdict.get("conviction", "?")}/10'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"R/R {verdict.get('reward_risk_ratio', '?')} · "
                    f"horizon {verdict.get('time_horizon_days', '?')}gg · "
                    f"cache: {'hit' if verdict.get('_cache_hit') else 'fresh'}"
                )

                if verdict.get("thesis"):
                    st.markdown("**Tesi:** " + verdict["thesis"])
                if verdict.get("key_risks"):
                    st.markdown("**Rischi chiave:**")
                    for risk in verdict["key_risks"]:
                        st.markdown(f"- {risk}")
                if verdict.get("suggested_adjustments"):
                    st.markdown("**Aggiustamenti suggeriti:**")
                    st.json(verdict["suggested_adjustments"])
