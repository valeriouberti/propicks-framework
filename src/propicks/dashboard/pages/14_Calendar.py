"""Calendar page — earnings hard gate + macro events (Phase 8).

Mirror CLI di ``propicks-calendar earnings/macro/check``. Mostra:
- Tabella earnings upcoming per portfolio + watchlist (hard gate badge)
- Tabella macro events FOMC/CPI/NFP/ECB (14gg configurabile)
- Inspector per singolo ticker (gate status + detail)
"""

from __future__ import annotations

import streamlit as st

# Bridge st.secrets → env vars (precede import propicks.config).
from propicks.dashboard import _bootstrap  # noqa: F401
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

tab_timeline, tab_earn, tab_macro, tab_check = st.tabs([
    "📅 Timeline",
    "📊 Earnings upcoming",
    "🏛️ Macro events",
    "🔍 Check ticker",
])

# ---------------------------------------------------------------------------
# Timeline — combined earnings + macro scatter chart
# ---------------------------------------------------------------------------
with tab_timeline:
    from datetime import date as _date, timedelta as _td

    from propicks.domain.calendar import (
        earnings_gate_check as _eg_check,
        upcoming_macro_events as _um_events,
    )
    from propicks.io.db import market_earnings_all_from_cache as _earn_all

    col_w, col_t = st.columns([1, 1])
    win_days = col_w.slider(
        "Finestra forward (giorni)",
        7, 90, 30,
        key="tl_days_slider",
        help="Quanti giorni in avanti includere nella timeline.",
    )
    types_tl = col_t.multiselect(
        "Tipi macro",
        options=["FOMC", "CPI", "NFP", "ECB"],
        default=["FOMC", "CPI", "NFP", "ECB"],
        key="tl_types",
    )

    # Carica events
    from propicks.io.portfolio_store import load_portfolio as _lp
    from propicks.io.watchlist_store import load_watchlist as _lw

    _portfolio = _lp()
    _watchlist = _lw()
    _tickers_pf = set(_portfolio.get("positions", {}).keys())
    _tickers_wl = set(_watchlist.get("tickers", {}).keys())
    _tickers_all = sorted(_tickers_pf | _tickers_wl)

    today_d = _date.today()
    cutoff = today_d + _td(days=win_days)

    events_tl: list[dict] = []

    # Earnings events (portfolio + watchlist)
    earn_meta = _earn_all()
    for tk in _tickers_all:
        ed = earn_meta.get(tk)
        if ed is None:
            continue
        try:
            ed_dt = _date.fromisoformat(ed)
        except (ValueError, TypeError):
            continue
        if ed_dt < today_d or ed_dt > cutoff:
            continue
        check = _eg_check(tk, ed, days_threshold=EARNINGS_HARD_GATE_DAYS)
        in_pf = tk in _tickers_pf
        events_tl.append({
            "date": ed_dt,
            "type": "EARNINGS",
            "label": tk,
            "description": (
                f"{tk} earnings"
                + (" · IN PORTFOLIO" if in_pf else " · watchlist")
                + (" · 🚨 HARD GATE" if check["blocked"] else "")
            ),
            "blocked": check["blocked"],
            "in_portfolio": in_pf,
            "row": "Earnings",
        })

    # Macro events
    macro_evs = _um_events(
        days_ahead=win_days,
        event_types=tuple(types_tl) if types_tl else None,
    )
    for ev in macro_evs:
        try:
            ev_dt = _date.fromisoformat(ev["date"])
        except (ValueError, TypeError):
            continue
        events_tl.append({
            "date": ev_dt,
            "type": ev["type"],
            "label": ev["type"],
            "description": ev["description"],
            "blocked": False,
            "in_portfolio": False,
            "row": ev["type"],
        })

    if not events_tl:
        st.info(
            f"Nessun evento (earnings o macro) nei prossimi {win_days}gg "
            "tra portfolio + watchlist + macro filtrati."
        )
    else:
        import plotly.graph_objects as go

        # Sort + group by row category
        row_order = ["Earnings", "FOMC", "CPI", "NFP", "ECB"]
        events_tl.sort(key=lambda e: (row_order.index(e["row"]) if e["row"] in row_order else 99, e["date"]))

        # Color per type
        color_map = {
            "EARNINGS": "#dc2626",  # red default
            "FOMC": "#7c3aed",       # purple
            "CPI": "#0891b2",        # cyan
            "NFP": "#ca8a04",        # amber
            "ECB": "#2563eb",        # blue
        }
        symbol_map = {
            "EARNINGS": "diamond",
            "FOMC": "square",
            "CPI": "circle",
            "NFP": "triangle-up",
            "ECB": "star",
        }

        fig = go.Figure()
        for typ in ["EARNINGS", "FOMC", "CPI", "NFP", "ECB"]:
            subset = [e for e in events_tl if e["type"] == typ]
            if not subset:
                continue
            xs = [e["date"] for e in subset]
            ys = [e["row"] for e in subset]
            colors = [
                "#7f1d1d" if e["blocked"]
                else "#dc2626" if e["type"] == "EARNINGS" and e["in_portfolio"]
                else color_map.get(e["type"], "#94a3b8")
                for e in subset
            ]
            sizes = [
                18 if e.get("blocked") else 14 if e.get("in_portfolio") else 11
                for e in subset
            ]
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="markers+text",
                marker=dict(
                    color=colors,
                    size=sizes,
                    symbol=symbol_map.get(typ, "circle"),
                    line=dict(color="white", width=1.5),
                ),
                text=[e["label"] for e in subset],
                textposition="top center",
                textfont=dict(size=10),
                name=typ,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "%{x|%Y-%m-%d}<br>"
                    "%{customdata[1]}<extra></extra>"
                ),
                customdata=[
                    (e["label"], e["description"]) for e in subset
                ],
            ))

        # Vertical line "today" + Vrect hard gate. Plotly internamente fa
        # _mean(x) per posizionare l'annotation: con string date fallisce
        # (sum di str). Workaround: usa add_shape (no annotation auto) +
        # add_annotation manuale. Coordinate come pd.Timestamp.
        import pandas as _pd
        today_ts = _pd.Timestamp(today_d)
        gate_end_ts = _pd.Timestamp(today_d + _td(days=EARNINGS_HARD_GATE_DAYS))

        fig.add_shape(
            type="line",
            x0=today_ts, x1=today_ts, xref="x",
            y0=0, y1=1, yref="paper",
            line=dict(color="#16a34a", width=2),
        )
        fig.add_annotation(
            x=today_ts, y=1.0, xref="x", yref="paper",
            text="TODAY", showarrow=False,
            font=dict(color="#16a34a", size=11),
            yanchor="bottom",
        )
        fig.add_shape(
            type="rect",
            x0=today_ts, x1=gate_end_ts, xref="x",
            y0=0, y1=1, yref="paper",
            fillcolor="#dc2626", opacity=0.10,
            line=dict(width=0),
            layer="below",
        )
        fig.add_annotation(
            x=today_ts, y=1.0, xref="x", yref="paper",
            text=f"⚠ Earnings hard gate zone ({EARNINGS_HARD_GATE_DAYS}gg)",
            showarrow=False,
            font=dict(color="#dc2626", size=10),
            xanchor="left", yanchor="top",
        )

        # Height adattiva
        n_rows = len({e["row"] for e in events_tl})
        fig.update_layout(
            title=dict(
                text=f"Timeline {today_d} → {cutoff} ({win_days}gg) — "
                     f"{len(events_tl)} eventi",
                x=0.5, xanchor="center", font=dict(size=13),
            ),
            xaxis_title="", yaxis_title="",
            xaxis=dict(type="date"),
            yaxis=dict(
                categoryorder="array",
                categoryarray=row_order,
                autorange="reversed",
            ),
            height=max(280, n_rows * 70 + 100),
            margin=dict(l=20, r=20, t=60, b=20),
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig, width="stretch")

        # Quick stats
        n_earn = sum(1 for e in events_tl if e["type"] == "EARNINGS")
        n_blocked = sum(1 for e in events_tl if e.get("blocked"))
        n_macro = len(events_tl) - n_earn
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Earnings", n_earn)
        c2.metric("Hard gate blocked", n_blocked, help=f"Earnings entro {EARNINGS_HARD_GATE_DAYS}gg")
        c3.metric("Macro events", n_macro)
        c4.metric("Window", f"{win_days}gg")
        st.caption(
            "🔺 EARNINGS · ⬛ FOMC · ⬤ CPI · 🔼 NFP · ★ ECB. "
            "Marker grande rosso scuro = ticker in portfolio + dentro hard gate "
            "(blocco entry). Marker grande rosso = ticker in portfolio. "
            "Vrect rosso = zona hard gate (5gg)."
        )


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
                except Exception as exc:
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
            except Exception as exc:
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
