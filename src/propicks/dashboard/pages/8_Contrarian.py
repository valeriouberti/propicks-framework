"""Contrarian scanner — quality-filtered mean reversion.

Equivalent UI di ``propicks-contra [TICKER ...] [--validate]``. Parallelo
allo Scanner momentum (page 1): stesso ticker può essere analizzato da
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
    "Parallelo allo Scanner momentum, NON lo sostituisce.",
)
invariants_note()

st.info(
    "**Strategia contrarian — invarianti diverse dal momentum:**  \n"
    "• Size max: **8%** per posizione (vs 15% momentum)  \n"
    "• Bucket cap aggregato: **20%** del capitale  \n"
    "• Max posizioni contrarian simultanee: **3** (condivide cap globale 10 con momentum)  \n"
    "• Stop ATR-based più largo (3×ATR vs 2×), max loss 12% per trade  \n"
    "• Time stop: 15 giorni (vs 30gg momentum)  \n"
    "• Target: reversion a EMA50 daily (fisso, NON trailing)  \n"
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


with st.form("contra_form", border=True):
    tickers_raw = st.text_input(
        "Ticker (separati da spazio o virgola)",
        placeholder="AAPL MSFT NVDA  (titoli di qualità Pro Picks oversold)",
    )
    col1, col2 = st.columns([1, 1])
    validate_ai = col1.checkbox("Valida con Claude (flush vs break)", value=False)
    force_ai = col2.checkbox("Force (bypassa gate + cache)", value=False)
    submitted = st.form_submit_button(
        "Analizza setup contrarian", type="primary", width="stretch"
    )

if not submitted:
    st.stop()

tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]
if not tickers:
    st.warning("Inserisci almeno un ticker.")
    st.stop()

# ---------------------------------------------------------------------------
# Fetch VIX una volta, propaga a tutti i ticker (contesto di mercato condiviso)
# ---------------------------------------------------------------------------
with st.spinner("Fetching VIX…"):
    vix = cached_vix()

if vix is None:
    st.warning(
        "VIX non disponibile — market context sub-score userà fallback neutrale."
    )
else:
    vix_label = "euforia (edge collassa)" if vix <= 14 else "paura (edge)" if vix >= 25 else "neutrale"
    st.caption(f"VIX corrente: **{vix:.2f}** — {vix_label}")

# ---------------------------------------------------------------------------
# Batch scan
# ---------------------------------------------------------------------------
results: list[dict] = []
with st.spinner(f"Scanning {len(tickers)} ticker (contrarian)…"):
    for t in tickers:
        r = cached_analyze_contra(t, vix)
        if r is not None:
            results.append(r)

if not results:
    st.error("Nessun ticker analizzabile. Verifica i simboli o la connessione.")
    st.stop()

# ---------------------------------------------------------------------------
# Auto-add classe A+B alla watchlist con source=auto_scan_contra
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
    })
st.dataframe(rows, width="stretch", hide_index=True)
st.caption(
    "**Oversold** (40%): RSI + distanza EMA50 in ATR + barre rosse consecutive · "
    "**Quality** (25%): gate EMA200 weekly + depth correzione · "
    "**Context** (20%): regime fit inverso + VIX · "
    "**R/R score** (15%): reversion a EMA50 vs stop a -3×ATR."
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
