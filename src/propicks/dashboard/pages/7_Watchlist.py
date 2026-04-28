"""Watchlist — incubatrice di idee tra scan e entry.

Equivalent UI di:
    propicks-watchlist add / remove / update / list / status
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from propicks.config import DATE_FMT
from propicks.dashboard._shared import (
    cached_analyze,
    invariants_note,
    page_header,
    score_badge,
)
from propicks.io.watchlist_store import (
    add_to_watchlist,
    is_stale,
    load_watchlist,
    remove_from_watchlist,
    update_watchlist_entry,
)

st.set_page_config(page_title="Watchlist · Propicks", layout="wide")
page_header(
    "Watchlist",
    "Incubatrice di idee: titoli classe B dallo scanner (auto-popolati) + "
    "aggiunte manuali in attesa di pullback, breakout o catalyst.",
)
invariants_note()

STALE_DAYS = 60
READY_SCORE_MIN = 60
READY_DISTANCE_PCT = 0.02


def _days_since(added_date: str | None) -> int | None:
    if not added_date:
        return None
    try:
        dt = datetime.strptime(added_date, DATE_FMT)
    except ValueError:
        return None
    return (datetime.now() - dt).days


wl = load_watchlist()
tickers = wl.get("tickers", {})

# ---------------------------------------------------------------------------
# Top KPIs
# ---------------------------------------------------------------------------
n_total = len(tickers)
n_auto = sum(1 for e in tickers.values() if e.get("source") == "auto_scan")
n_stale = sum(1 for e in tickers.values() if is_stale(e, days=STALE_DAYS))

col1, col2, col3, col4 = st.columns(4)
col1.metric("In watchlist", n_total)
col2.metric("Auto (scanner B)", n_auto)
col3.metric("Manuali", n_total - n_auto)
col4.metric(f"Stale (>{STALE_DAYS}gg)", n_stale)

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_attiva, tab_aggiungi, tab_stale = st.tabs(["Attiva (live score)", "Aggiungi", "Stale"])

# ---------------------------------------------------------------------------
# Attiva — score live + distanza target + READY flag
# ---------------------------------------------------------------------------
with tab_attiva:
    if not tickers:
        st.info("Watchlist vuota. Aggiungi un ticker dal tab *Aggiungi* o lancia Momentum (classe B viene auto-aggiunta).")
    else:
        refresh = st.button("🔄 Ricalcola score live", type="secondary")
        if refresh:
            cached_analyze.clear()  # type: ignore[attr-defined]

        rows = []
        ready = []
        with st.spinner(f"Scanning {len(tickers)} ticker…"):
            for t, e in sorted(tickers.items()):
                r = cached_analyze(t, None)
                if r is None:
                    rows.append({
                        "Ticker": t,
                        "Price": "—",
                        "Target": f"{e['target_entry']:.2f}" if e.get("target_entry") else "—",
                        "Dist%": "—",
                        "Score": "—",
                        "Class": "—",
                        "Regime": "—",
                        "Flag": "no data",
                        "Added": e.get("added_date") or "—",
                        "Source": e.get("source") or "manual",
                        "Note": e.get("note") or "",
                    })
                    continue
                price = r["price"]
                score = r["score_composite"]
                classification = r["classification"].split(" — ")[0]
                regime = (r.get("regime") or {}).get("regime", "N/D")
                target = e.get("target_entry")
                if target:
                    dist = (price - target) / target
                    dist_str = f"{dist * 100:+.2f}%"
                    is_ready = score >= READY_SCORE_MIN and abs(dist) <= READY_DISTANCE_PCT
                else:
                    dist_str = "—"
                    is_ready = False
                if is_ready:
                    ready.append(t)
                rows.append({
                    "Ticker": t,
                    "Price": f"{price:.2f}",
                    "Target": f"{target:.2f}" if target else "—",
                    "Dist%": dist_str,
                    "Score": f"{score:.1f}",
                    "Class": classification,
                    "Regime": regime,
                    "Flag": "READY ✓" if is_ready else "",
                    "Added": e.get("added_date") or "—",
                    "Source": e.get("source") or "manual",
                    "Note": (e.get("note") or "")[:60],
                })
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(
            f"**READY** = score ≥{READY_SCORE_MIN} + entro {READY_DISTANCE_PCT * 100:.0f}% dal target. "
            "Prossimo step: vai su Momentum per re-analisi completa, poi Portfolio → Size."
        )

        if ready:
            st.success(f"{len(ready)} entry READY: {', '.join(ready)}")

        # Quick actions: remove / update target
        st.divider()
        st.markdown("### Azioni rapide")
        col_rm, col_upd = st.columns(2)
        with col_rm:
            to_remove = st.selectbox(
                "Rimuovi ticker", options=["", *sorted(tickers.keys())], key="wl_remove"
            )
            if st.button("Rimuovi", type="secondary", disabled=not to_remove):
                try:
                    remove_from_watchlist(wl, to_remove)
                    st.success(f"Rimosso {to_remove}.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        with col_upd:
            to_update = st.selectbox(
                "Aggiorna target di", options=["", *sorted(tickers.keys())], key="wl_update"
            )
            new_target = st.number_input(
                "Nuovo target", min_value=0.0, value=0.0, step=0.01, key="wl_new_target"
            )
            if st.button("Aggiorna", type="secondary", disabled=(not to_update or new_target <= 0)):
                try:
                    update_watchlist_entry(wl, to_update, target_entry=new_target)
                    st.success(f"Target di {to_update} → {new_target:.2f}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

# ---------------------------------------------------------------------------
# Aggiungi (manuale)
# ---------------------------------------------------------------------------
with tab_aggiungi:
    with st.form("watchlist_add_form", border=True):
        st.markdown("Aggiunta **manuale**. I ticker classe B scansionati arrivano qui automaticamente.")
        c1, c2 = st.columns([1, 1])
        new_ticker = c1.text_input("Ticker", placeholder="AAPL").strip().upper()
        new_target = c2.number_input(
            "Target entry (opzionale)", min_value=0.0, value=0.0, step=0.01
        )
        new_note = st.text_input(
            "Nota / catalyst (opzionale)",
            placeholder="pullback EMA20, aspetto earnings 2026-05-02, ...",
        )
        submit_add = st.form_submit_button("Aggiungi a watchlist", type="primary")

    if submit_add:
        if not new_ticker:
            st.warning("Inserisci un ticker.")
        else:
            r = cached_analyze(new_ticker, None)
            regime_at = None
            score_at = None
            class_at = None
            if r is not None:
                regime_at = (r.get("regime") or {}).get("regime")
                score_at = r.get("score_composite")
                class_at = r.get("classification")
            entry, is_new = add_to_watchlist(
                wl,
                new_ticker,
                target_entry=(new_target if new_target > 0 else None),
                note=(new_note or None),
                score_at_add=score_at,
                regime_at_add=regime_at,
                classification_at_add=class_at,
                source="manual",
            )
            verb = "Aggiunto" if is_new else "Aggiornato"
            badge = score_badge(score_at) if score_at is not None else ""
            st.success(f"{verb} {new_ticker} in watchlist.")
            if badge:
                st.markdown(f"Score corrente: {badge}", unsafe_allow_html=True)
            st.rerun()

# ---------------------------------------------------------------------------
# Stale — candidati alla pulizia
# ---------------------------------------------------------------------------
with tab_stale:
    stale_entries = [
        (t, e) for t, e in tickers.items() if is_stale(e, days=STALE_DAYS)
    ]
    if not stale_entries:
        st.info(f"Nessuna entry > {STALE_DAYS} giorni. Watchlist pulita.")
    else:
        st.markdown(
            f"Entry in watchlist da più di **{STALE_DAYS} giorni**. "
            "Probabilmente il setup non si è materializzato — valuta rimozione."
        )
        rows = []
        for t, e in sorted(stale_entries, key=lambda x: x[1].get("added_date") or ""):
            rows.append({
                "Ticker": t,
                "Added": e.get("added_date") or "—",
                "Age (gg)": _days_since(e.get("added_date")) or "—",
                "Target": f"{e['target_entry']:.2f}" if e.get("target_entry") else "—",
                "Score@add": f"{e['score_at_add']:.1f}" if e.get("score_at_add") is not None else "—",
                "Regime@add": e.get("regime_at_add") or "—",
                "Source": e.get("source") or "manual",
                "Note": (e.get("note") or "")[:60],
            })
        st.dataframe(rows, width="stretch", hide_index=True)

        st.divider()
        to_purge = st.multiselect(
            "Rimuovi in blocco",
            options=[t for t, _ in stale_entries],
        )
        if st.button("Rimuovi selezionati", type="primary", disabled=not to_purge):
            removed = 0
            for t in to_purge:
                try:
                    remove_from_watchlist(wl, t)
                    removed += 1
                except ValueError:
                    pass
            st.success(f"Rimossi {removed} ticker stale.")
            st.rerun()
