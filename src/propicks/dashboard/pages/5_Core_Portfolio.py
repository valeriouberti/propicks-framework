"""Core Portfolio — bucket long-term PIC/PAC.

UI equivalente di `propicks-core`:
- add holding (PIC iniziale + meta asset_class/region/sector_key/target_weight)
- contribute (PAC / DIVIDEND_REINVEST)
- sell (parziale o totale)
- update metadati (target_weight, region, ecc.)
- remove (soft/hard)
- list holdings con prezzi live + P&L + weight
- history contributions

Per la vista consolidata (drift + sector overlap core+satellite) → page 17.

Invariant: questo bucket è ISOLATO dai cap satellite (Stock 40% / ETF 60%).
Nessun stop/target/AI. Risk model = buy & hold.
"""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import (
    ASSET_CLASS_LABELS,
    CORE_CONTRIBUTION_KINDS,
    REGION_LABELS,
)
from propicks.dashboard._shared import (
    cached_core_prices,
    fmt_eur,
    fmt_pct,
    load_core,
    page_header,
)
from propicks.domain import core_allocation as ca
from propicks.io import core_store


st.set_page_config(page_title="Core Portfolio · Propicks", layout="wide")
page_header(
    "Core Portfolio (long-term PIC/PAC)",
    "Holdings buy & hold di lungo periodo. Bucket isolato dal satellite — "
    "nessun stop/target/AI, solo tracking allocazione + drift vs target.",
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Bucket Core**")
st.sidebar.caption(
    "• Isolato da cap satellite (Stock 40% / ETF 60%)  \n"
    "• No stop/target/AI — buy & hold  \n"
    "• Drift alert se |actual − target| > 5%  \n"
    "• Overlap warn (core+satellite) > 35%/settore  \n"
    "→ Vai a page **Allocation Consolidated** per drift + overlap"
)


# ---------------------------------------------------------------------------
# Top KPIs
# ---------------------------------------------------------------------------
holdings = load_core()

if not holdings:
    st.info(
        "Core portfolio vuoto. Usa il tab **Aggiungi** per inserire il primo "
        "PIC, oppure **Import CSV** per backfill da broker."
    )
else:
    tickers = tuple(sorted(holdings.keys()))
    prices = cached_core_prices(tickers)
    values = ca.compute_holding_values(holdings, prices)
    total_value = ca.total_core_value(values)
    contributed = core_store.total_contributed()
    pnl_total = total_value - contributed
    pnl_total_pct = (pnl_total / contributed) if contributed > 0 else 0.0
    n_with_target = sum(1 for h in holdings.values() if h.get("target_weight"))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Holdings", len(holdings))
    c2.metric("Valore EUR", fmt_eur(total_value, decimals=0))
    c3.metric("Contributed", fmt_eur(contributed, decimals=0))
    c4.metric("P&L tot", fmt_eur(pnl_total, decimals=0), fmt_pct(pnl_total_pct))
    c5.metric("Con target", f"{n_with_target}/{len(holdings)}")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_list, tab_add, tab_contrib, tab_sell, tab_update, tab_history, tab_remove = st.tabs([
    "Holdings",
    "Aggiungi (PIC)",
    "Contribuisci (PAC)",
    "Vendi",
    "Aggiorna meta",
    "Storia",
    "Rimuovi",
])


# ---------------------------------------------------------------------------
# Tab: Holdings list
# ---------------------------------------------------------------------------
with tab_list:
    if not holdings:
        st.info("Nessuna holding. Apri il tab *Aggiungi (PIC)*.")
    else:
        rows: list[dict] = []
        for t, h in sorted(holdings.items()):
            v = values.get(t)
            target = h.get("target_weight")
            actual_weight = (v["current_value"] / total_value) if v and total_value > 0 else None
            rows.append({
                "Ticker": t,
                "Nome": h.get("name") or "—",
                "Asset": ASSET_CLASS_LABELS.get(h.get("asset_class") or "", h.get("asset_class") or "—"),
                "Region": REGION_LABELS.get(h.get("region") or "", h.get("region") or "—"),
                "Sector": h.get("sector_key") or "—",
                "Shares": f"{h['shares']:g}",
                "Avg cost": f"{h['avg_cost']:.2f}",
                "Price": f"{v['current_price']:.2f}" if v else "—",
                "Value EUR": fmt_eur(v["current_value"], decimals=0) if v else "—",
                "P&L €": fmt_eur(v["pnl"], decimals=0) if v else "—",
                "P&L %": fmt_pct(v["pnl_pct"]) if v else "—",
                "Weight": fmt_pct(actual_weight) if actual_weight is not None else "—",
                "Target": fmt_pct(target) if target else "—",
                "Currency": h.get("currency") or "EUR",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)

        n_missing = sum(1 for t in holdings if t not in values)
        if n_missing:
            st.warning(
                f"⚠️ {n_missing} holding senza prezzo corrente (delisted / API down). "
                f"Mostrate con '—' nelle colonne live."
            )


# ---------------------------------------------------------------------------
# Tab: Add (PIC iniziale)
# ---------------------------------------------------------------------------
with tab_add:
    st.markdown(
        "**PIC iniziale o nuova holding.** Usa questo tab solo per la prima "
        "apertura. Per PAC su holding esistente → tab *Contribuisci*."
    )
    with st.form("core_add_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        ticker = c1.text_input("Ticker *", placeholder="es. VWCE.MI").strip().upper()
        shares = c2.number_input("Shares *", min_value=0.0, step=1.0, format="%.4f")
        price = c3.number_input("Price *", min_value=0.0, step=0.01, format="%.4f")

        c4, c5, c6 = st.columns(3)
        asset_class = c4.selectbox(
            "Asset class *",
            options=["", "EQUITY_ETF", "BOND_ETF", "COMMODITY_ETF", "STOCK"],
            format_func=lambda x: ASSET_CLASS_LABELS.get(x, x) if x else "— seleziona —",
        )
        region = c5.selectbox(
            "Region *",
            options=["", "WORLD", "US", "EU", "EM", "IT"],
            format_func=lambda x: REGION_LABELS.get(x, x) if x else "— seleziona —",
        )
        sector_key = c6.text_input(
            "Sector key (opzionale)",
            placeholder="es. technology · vuoto = broad",
            help="Per ETF broad (VWCE, MSCI World) lascia vuoto. Per ETF settoriale "
                 "specifica technology/financials/healthcare/ecc.",
        )

        c7, c8, c9 = st.columns(3)
        target_weight = c7.number_input(
            "Target weight % (opzionale)",
            min_value=0.0, max_value=100.0, step=1.0, value=0.0,
            help="Target % del valore core. Lascia 0 per non tracciare drift.",
        )
        kind = c8.selectbox("Kind", options=["PIC", "PAC", "DIVIDEND_REINVEST"])
        fees = c9.number_input("Fees", min_value=0.0, step=0.01, format="%.2f")

        c10, c11, c12 = st.columns(3)
        currency = c10.text_input("Currency (auto se vuoto)", placeholder="EUR")
        date = c11.date_input("Date").isoformat()
        name = c12.text_input("Nome (opzionale)", placeholder="Vanguard FTSE All-World")

        notes = st.text_input("Note (opzionale)")
        submitted = st.form_submit_button("Aggiungi holding", type="primary")

    if submitted:
        if not ticker or shares <= 0 or price <= 0 or not asset_class or not region:
            st.error("Compila Ticker, Shares, Price, Asset class, Region.")
        else:
            try:
                h = core_store.add_holding(
                    ticker,
                    shares=shares,
                    price=price,
                    name=name or None,
                    asset_class=asset_class,
                    region=region,
                    sector_key=sector_key or None,
                    currency=currency or None,
                    target_weight=(target_weight / 100) if target_weight > 0 else None,
                    notes=notes or None,
                    date=date,
                    kind=kind,
                    fees=fees,
                )
                st.success(
                    f"✓ Aggiunto {h['ticker']}: {h['shares']:g} shares "
                    f"@ avg {h['avg_cost']:.4f} {h['currency']} (kind={kind})."
                )
                time.sleep(0.5)
                st.rerun()
            except ValueError as exc:
                st.error(f"Errore: {exc}")


# ---------------------------------------------------------------------------
# Tab: Contribute (PAC successive)
# ---------------------------------------------------------------------------
with tab_contrib:
    st.markdown(
        "**PAC o reinvest dividendi** su holding esistente. "
        "Per nuove holding → tab *Aggiungi (PIC)*."
    )
    if not holdings:
        st.info("Nessuna holding presente.")
    else:
        with st.form("core_contrib_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ticker = c1.selectbox(
                "Ticker *", options=sorted(holdings.keys()),
            )
            shares = c2.number_input(
                "Shares (>0) *", min_value=0.0, step=1.0, format="%.4f",
            )
            price = c3.number_input(
                "Price *", min_value=0.0, step=0.01, format="%.4f",
            )

            c4, c5, c6 = st.columns(3)
            kind = c4.selectbox("Kind", options=["PAC", "PIC", "DIVIDEND_REINVEST"])
            fees = c5.number_input("Fees", min_value=0.0, step=0.01, format="%.2f")
            date = c6.date_input("Date").isoformat()

            notes = st.text_input("Note (opzionale)")
            submitted = st.form_submit_button("Aggiungi contribution", type="primary")

        if submitted:
            if shares <= 0 or price <= 0:
                st.error("Shares e price devono essere > 0.")
            else:
                try:
                    h = core_store.add_contribution(
                        ticker, shares=shares, price=price, kind=kind,
                        date=date, fees=fees, notes=notes or None,
                    )
                    st.success(
                        f"✓ {kind} su {h['ticker']}: nuovo totale {h['shares']:g} "
                        f"shares @ avg {h['avg_cost']:.4f}."
                    )
                    time.sleep(0.5)
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Errore: {exc}")


# ---------------------------------------------------------------------------
# Tab: Sell
# ---------------------------------------------------------------------------
with tab_sell:
    st.markdown(
        "Vendita parziale o totale (kind=SELL). Non altera l'`avg_cost` "
        "(solo i BUY contribuiscono al cost basis)."
    )
    if not holdings:
        st.info("Nessuna holding da vendere.")
    else:
        with st.form("core_sell_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ticker = c1.selectbox("Ticker *", options=sorted(holdings.keys()))
            shares = c2.number_input(
                "Shares da vendere (>0) *", min_value=0.0, step=1.0, format="%.4f",
            )
            price = c3.number_input("Price *", min_value=0.0, step=0.01, format="%.4f")

            c4, c5 = st.columns(2)
            fees = c4.number_input("Fees", min_value=0.0, step=0.01, format="%.2f")
            date = c5.date_input("Date").isoformat()

            notes = st.text_input("Note (opzionale)")
            submitted = st.form_submit_button("Vendi", type="primary")

        if submitted:
            if shares <= 0 or price <= 0:
                st.error("Shares e price devono essere > 0.")
            else:
                try:
                    h = core_store.add_contribution(
                        ticker, shares=-abs(shares), price=price, kind="SELL",
                        date=date, fees=fees, notes=notes or None,
                    )
                    st.success(
                        f"✓ SELL su {h['ticker']}: residuo {h['shares']:g} shares."
                    )
                    time.sleep(0.5)
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Errore: {exc}")


# ---------------------------------------------------------------------------
# Tab: Update meta
# ---------------------------------------------------------------------------
with tab_update:
    st.markdown(
        "Aggiorna i metadati di classificazione (target weight, region, sector). "
        "Non tocca shares/avg_cost — quelli derivano dalle contributions."
    )
    if not holdings:
        st.info("Nessuna holding da aggiornare.")
    else:
        sel_ticker = st.selectbox(
            "Ticker", options=sorted(holdings.keys()), key="upd_ticker",
        )
        current = holdings.get(sel_ticker, {})

        with st.form("core_update_form"):
            c1, c2, c3 = st.columns(3)
            new_name = c1.text_input("Nome", value=current.get("name") or "")
            new_target = c2.number_input(
                "Target weight %",
                min_value=0.0, max_value=100.0, step=1.0,
                value=(float(current.get("target_weight") or 0) * 100),
            )
            new_region = c3.selectbox(
                "Region",
                options=["", "WORLD", "US", "EU", "EM", "IT"],
                index=(
                    ["", "WORLD", "US", "EU", "EM", "IT"].index(current.get("region") or "")
                    if (current.get("region") or "") in ["", "WORLD", "US", "EU", "EM", "IT"]
                    else 0
                ),
                format_func=lambda x: REGION_LABELS.get(x, x) if x else "—",
            )

            c4, c5 = st.columns(2)
            new_asset = c4.selectbox(
                "Asset class",
                options=["", "EQUITY_ETF", "BOND_ETF", "COMMODITY_ETF", "STOCK"],
                index=(
                    ["", "EQUITY_ETF", "BOND_ETF", "COMMODITY_ETF", "STOCK"].index(
                        current.get("asset_class") or ""
                    ) if (current.get("asset_class") or "") in
                    ["", "EQUITY_ETF", "BOND_ETF", "COMMODITY_ETF", "STOCK"]
                    else 0
                ),
                format_func=lambda x: ASSET_CLASS_LABELS.get(x, x) if x else "—",
            )
            new_sector = c5.text_input(
                "Sector key", value=current.get("sector_key") or "",
            )

            new_notes = st.text_input("Notes", value=current.get("notes") or "")
            submitted = st.form_submit_button("Aggiorna", type="primary")

        if submitted:
            try:
                core_store.update_holding_meta(
                    sel_ticker,
                    name=new_name or None,
                    asset_class=new_asset or None,
                    region=new_region or None,
                    sector_key=new_sector or None,
                    target_weight=(new_target / 100) if new_target > 0 else None,
                    notes=new_notes or None,
                )
                st.success(f"✓ Aggiornato {sel_ticker}.")
                time.sleep(0.5)
                st.rerun()
            except ValueError as exc:
                st.error(f"Errore: {exc}")


# ---------------------------------------------------------------------------
# Tab: History
# ---------------------------------------------------------------------------
with tab_history:
    c1, c2, c3 = st.columns(3)
    f_ticker = c1.selectbox(
        "Ticker", options=["(tutti)"] + sorted(holdings.keys()),
    )
    f_kind = c2.selectbox(
        "Kind", options=["(tutti)"] + list(CORE_CONTRIBUTION_KINDS),
    )
    f_since = c3.date_input("Da data (opzionale)", value=None)

    contribs = core_store.list_contributions(
        ticker=None if f_ticker == "(tutti)" else f_ticker,
        kind=None if f_kind == "(tutti)" else f_kind,
        since=f_since.isoformat() if f_since else None,
    )
    if not contribs:
        st.info("Nessuna contribution coi filtri specificati.")
    else:
        rows = [{
            "Date": c["date"],
            "Ticker": c["ticker"],
            "Kind": c["kind"],
            "Shares": f"{c['shares']:g}",
            "Price": f"{c['price']:.2f}",
            "Amount": fmt_eur(c["amount"], decimals=2),
            "Fees": f"{c['fees']:.2f}" if c["fees"] else "—",
            "Currency": c["currency"],
            "Notes": (c.get("notes") or "")[:50],
        } for c in contribs]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(f"Totale: {len(contribs)} contribution.")


# ---------------------------------------------------------------------------
# Tab: Remove
# ---------------------------------------------------------------------------
with tab_remove:
    st.warning(
        "**Soft delete** (default): la holding viene segnata con shares=0 ma "
        "le contribution restano per audit. **Hard delete**: cascade CASCADE "
        "rimuove tutto — distruttivo, usalo solo per errori di data entry."
    )
    if not holdings:
        st.info("Nessuna holding da rimuovere.")
    else:
        rm_ticker = st.selectbox(
            "Ticker", options=sorted(holdings.keys()), key="rm_ticker",
        )
        c1, c2 = st.columns([1, 3])
        hard = c1.checkbox("Hard delete (distruttivo)")
        confirm = c2.checkbox(f"Confermo rimozione di **{rm_ticker}**")
        if st.button("Rimuovi", disabled=not confirm, type="primary"):
            try:
                core_store.remove_holding(rm_ticker, keep_history=not hard)
                mode = "hard delete (CASCADE)" if hard else "soft delete (storia preservata)"
                st.success(f"✓ Rimosso {rm_ticker} — {mode}.")
                time.sleep(0.5)
                st.rerun()
            except ValueError as exc:
                st.error(f"Errore: {exc}")
