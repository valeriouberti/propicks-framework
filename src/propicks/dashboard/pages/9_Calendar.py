"""Calendar page — earnings hard gate + macro events (Phase 8).

Mirror CLI di ``propicks-calendar earnings/macro/check``. Mostra:
- Tabella earnings upcoming per portfolio + watchlist (hard gate badge)
- Tabella macro events FOMC/CPI/NFP/ECB (14gg configurabile)
- Inspector per singolo ticker (gate status + detail)
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from propicks.config import EARNINGS_HARD_GATE_DAYS
from propicks.dashboard._shared import invariants_note, page_header

st.set_page_config(page_title="Calendar · Propicks", layout="wide")
page_header(
    "Calendar",
    "Earnings hard gate (5gg ticker-specific) + macro events (FOMC/CPI/NFP/ECB). "
    "Mirror di `propicks-calendar earnings/macro/check`.",
)
invariants_note()

st.info(
    f"**Earnings hard gate**: {EARNINGS_HARD_GATE_DAYS}gg. `propicks-portfolio add` "
    "rifiuta entry se earnings entro soglia. Override con `--ignore-earnings`.  \n"
    "**Macro warning**: 2gg soft (solo info, non blocca — coinvolge tutto il mercato).",
    icon="ℹ️",
)

tab_earn, tab_macro, tab_check = st.tabs([
    "📊 Earnings upcoming",
    "🏛️ Macro events",
    "🔍 Check ticker",
])

# ---------------------------------------------------------------------------
# Earnings upcoming
# ---------------------------------------------------------------------------
with tab_earn:
    from propicks.domain.calendar import earnings_gate_check
    from propicks.io.db import market_earnings_all_from_cache
    from propicks.io.portfolio_store import load_portfolio
    from propicks.io.watchlist_store import load_watchlist

    col1, col2 = st.columns([1, 1])
    days_ahead = col1.slider("Finestra forward (giorni)", 5, 60, 14)
    refresh = col2.button("🔄 Refresh earnings dates (yfinance)", type="secondary")

    if refresh:
        from propicks.market.yfinance_client import get_next_earnings_date

        pf = load_portfolio()
        wl = load_watchlist()
        tickers = sorted(set(
            list(pf.get("positions", {}).keys())
            + list(wl.get("tickers", {}).keys())
        ))
        with st.status(f"Fetching earnings per {len(tickers)} ticker…") as status:
            ok_count = 0
            for t in tickers:
                try:
                    get_next_earnings_date(t, force_refresh=True)
                    ok_count += 1
                    st.write(f"✓ {t}")
                except Exception as exc:  # noqa: BLE001
                    st.write(f"✗ {t}: {exc}")
            status.update(
                label=f"Refresh completato: {ok_count}/{len(tickers)} ok",
                state="complete",
            )

    portfolio = load_portfolio()
    watchlist = load_watchlist()
    tickers = sorted(set(
        list(portfolio.get("positions", {}).keys())
        + list(watchlist.get("tickers", {}).keys())
    ))
    meta = market_earnings_all_from_cache()

    rows = []
    for t in tickers:
        ed = meta.get(t)
        if ed is None:
            continue
        check = earnings_gate_check(t, ed, days_threshold=EARNINGS_HARD_GATE_DAYS)
        dte = check["days_to_earnings"]
        if dte is None or dte < 0 or dte > days_ahead:
            continue
        status_badge = "🚨 BLOCKED" if check["blocked"] else "ℹ️ info"
        rows.append({
            "Ticker": t,
            "Earnings Date": ed,
            "Days": dte,
            "Status": status_badge,
            "In portfolio": "✓" if t in portfolio.get("positions", {}) else "—",
            "In watchlist": "✓" if t in watchlist.get("tickers", {}) else "—",
        })

    rows.sort(key=lambda r: r["Days"])

    if not rows:
        st.success(
            f"Nessun earnings upcoming nei prossimi {days_ahead}gg tra "
            "portfolio + watchlist. Usa *Refresh* per forzare fetch yfinance."
        )
    else:
        n_blocked = sum(1 for r in rows if "BLOCKED" in r["Status"])
        a, b = st.columns(2)
        a.metric("Ticker upcoming", len(rows))
        b.metric("Hard gate blocked", n_blocked, help=f"Entro {EARNINGS_HARD_GATE_DAYS}gg")
        st.dataframe(rows, width="stretch", hide_index=True)
        if n_blocked:
            st.warning(
                f"**{n_blocked} ticker sono bloccati** dal hard gate. "
                "Nuovi entry rifiutati da `add_position`. "
                "Override solo per trade intentional post-earnings: "
                "`propicks-portfolio add ... --ignore-earnings`."
            )

# ---------------------------------------------------------------------------
# Macro events
# ---------------------------------------------------------------------------
with tab_macro:
    from propicks.domain.calendar import upcoming_macro_events

    col1, col2 = st.columns([1, 1])
    days_ahead_m = col1.slider(
        "Finestra forward (giorni)",
        5, 90, 14,
        key="macro_days_slider",
    )
    types_filter = col2.multiselect(
        "Filtra per tipo",
        options=["FOMC", "CPI", "NFP", "ECB"],
        default=["FOMC", "CPI", "NFP", "ECB"],
    )

    events = upcoming_macro_events(
        days_ahead=days_ahead_m,
        event_types=tuple(types_filter) if types_filter else None,
    )

    if not events:
        st.info(f"Nessun evento nei prossimi {days_ahead_m}gg.")
    else:
        type_emoji = {
            "FOMC": "🏦",
            "CPI": "📈",
            "NFP": "💼",
            "ECB": "🇪🇺",
        }
        rows = []
        for ev in events:
            rows.append({
                "Date": ev["date"],
                "Days": ev["days_from_now"],
                "Type": f"{type_emoji.get(ev['type'], '📅')} {ev['type']}",
                "Description": ev["description"],
            })
        st.dataframe(rows, width="stretch", hide_index=True)
        st.caption(
            "_Soft warning: macro events coinvolgono tutto il mercato. "
            "Un entry 1-2gg prima di FOMC/CPI è notoriamente volatile — "
            "valuta se aspettare la reazione post-evento._"
        )

# ---------------------------------------------------------------------------
# Check ticker
# ---------------------------------------------------------------------------
with tab_check:
    from propicks.domain.calendar import earnings_gate_check, macro_warning_check
    from propicks.market.yfinance_client import get_next_earnings_date

    with st.form("check_ticker_form", border=True):
        col1, col2 = st.columns([2, 1])
        ticker_input = col1.text_input("Ticker", placeholder="es. AAPL")
        force_refresh = col2.checkbox("Force refresh cache", value=False)
        check_submit = st.form_submit_button("Check gate", type="primary")

    if check_submit and ticker_input:
        ticker = ticker_input.strip().upper()
        with st.spinner(f"Fetching earnings date per {ticker}…"):
            try:
                ed = get_next_earnings_date(ticker, force_refresh=force_refresh)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Fetch fallito: {exc}")
                ed = None

        check = earnings_gate_check(ticker, ed, days_threshold=EARNINGS_HARD_GATE_DAYS)

        cols = st.columns(3)
        cols[0].metric("Next earnings", ed or "—")
        cols[1].metric(
            "Days to earnings",
            check["days_to_earnings"] if check["days_to_earnings"] is not None else "—",
        )
        if check["blocked"]:
            cols[2].metric("Hard gate", "🚨 BLOCKED")
        else:
            cols[2].metric("Hard gate", "✅ PASS")

        st.caption(f"**Reason:** {check['reason']}")

        # Macro proximity
        macro = macro_warning_check()
        if macro["has_warning"]:
            st.warning("⚠️ Macro event imminente:")
            for ev in macro["events"]:
                st.markdown(
                    f"- **{ev['type']}** `{ev['date']}` "
                    f"({ev['days_from_now']}gg) — {ev['description']}"
                )
