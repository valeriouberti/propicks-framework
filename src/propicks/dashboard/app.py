"""Home / Overview — stato aggregato del portfolio.

Streamlit multi-page app: ogni file in ``pages/`` diventa una voce di menu.
Questa è la pagina di default (sidebar top entry).
"""

from __future__ import annotations

# IMPORTANTE: bridge secrets → env PRIMA di qualsiasi import propicks.*
# (config.py legge env vars all'import).
from propicks.dashboard import _bootstrap  # noqa: F401

from datetime import date

import streamlit as st

from propicks.config import MAX_LOSS_WEEKLY_PCT, MAX_POSITIONS, MIN_CASH_RESERVE_PCT
from propicks.dashboard._shared import (
    cached_current_prices,
    fmt_eur,
    fmt_pct,
    invariants_note,
    kpi_row,
    load_journal,
    load_portfolio,
    page_header,
    pnl_arrow,
    regime_badge,
    score_badge,
)
from propicks.dashboard.cadence import DAY_NAMES_IT, WEEKLY_CADENCE, today_block
from propicks.domain.sizing import portfolio_value
from propicks.domain.trade_mgmt import (
    DEFAULT_FLAT_THRESHOLD_PCT,
    DEFAULT_TIME_STOP_DAYS,
    check_time_stop,
)
from propicks.io.watchlist_store import load_watchlist

st.set_page_config(
    page_title="Propicks Dashboard",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)


def _current_regime() -> dict | None:
    """Lookup regime weekly via il classifier. Cache-backed dentro rank_universe
    non è riusabile qui (serve solo il regime, non scan settori). Chiamata
    diretta ma cached a parte."""

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch() -> dict | None:
        from propicks.config import ETF_BENCHMARK
        from propicks.domain.regime import classify_regime
        from propicks.market.yfinance_client import (
            DataUnavailable,
            download_weekly_history,
        )
        try:
            weekly = download_weekly_history(ETF_BENCHMARK)
            return classify_regime(weekly)
        except DataUnavailable:
            return None

    return _fetch()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
page_header(
    "Portfolio Overview",
    "Stato aggregato — posizioni aperte, P&L unrealized, regime macro e invariants.",
)
invariants_note()

portfolio = load_portfolio()
positions = portfolio.get("positions", {})
cash = float(portfolio.get("cash") or 0)
total = portfolio_value(portfolio)

regime = _current_regime()
st.markdown("**Regime macro weekly:** " + regime_badge(regime), unsafe_allow_html=True)
if regime is not None:
    st.caption(
        f"Classifier su ^GSPC — "
        f"close {regime.get('close', 0):.2f} · "
        f"EMA30w {regime.get('ema_slow', 0):.2f} · "
        f"RSI(w) {regime.get('rsi_weekly', 0):.1f} · "
        f"entry long {'allowed' if regime.get('entry_allowed') else 'NOT allowed'}"
    )

# ─── Data freshness banner ─────────────────────────────────────────────
# Mostra last-update timestamp per le 4 source critiche + warning se stale.
# Soglie: daily 8h, weekly 7d, regime 24h, ai_verdict 7d (storico).
@st.cache_data(ttl=60, show_spinner=False)
def _data_freshness() -> dict:
    """Pull last-update timestamps per cache key sources."""
    from datetime import datetime, timezone
    from propicks.io.db import connect

    out: dict = {}
    conn = connect()
    try:
        for table, col in [
            ("market_ohlcv_daily", "fetched_at"),
            ("market_ohlcv_weekly", "fetched_at"),
            ("regime_history", "recorded_at"),
            ("ai_verdicts", "run_timestamp"),
        ]:
            try:
                row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
                ts_str = row[0] if row else None
                if ts_str:
                    # SQLite timestamps sono "YYYY-MM-DD HH:MM:SS" (no tz)
                    dt = datetime.fromisoformat(ts_str.replace(" ", "T"))
                    age_hours = (datetime.now() - dt).total_seconds() / 3600
                    out[table] = {"ts": ts_str, "age_h": age_hours}
                else:
                    out[table] = {"ts": None, "age_h": None}
            except Exception:
                out[table] = {"ts": None, "age_h": None}
    finally:
        conn.close()
    return out


_freshness = _data_freshness()


def _fmt_age(age_h: float | None) -> str:
    if age_h is None:
        return "—"
    if age_h < 1:
        return f"{int(age_h * 60)}min ago"
    if age_h < 24:
        return f"{age_h:.1f}h ago"
    return f"{age_h / 24:.1f}d ago"


def _status(age_h: float | None, fresh_h: float, stale_h: float) -> str:
    """🟢 fresh, 🟡 stale, 🔴 very stale, ⚪ unknown."""
    if age_h is None:
        return "⚪"
    if age_h <= fresh_h:
        return "🟢"
    if age_h <= stale_h:
        return "🟡"
    return "🔴"


_fresh_cols = st.columns(4)
_fresh_cols[0].markdown(
    f"**📊 Daily OHLCV**  \n"
    f"{_status(_freshness['market_ohlcv_daily']['age_h'], 8, 24)} "
    f"{_fmt_age(_freshness['market_ohlcv_daily']['age_h'])}"
)
_fresh_cols[1].markdown(
    f"**📈 Weekly OHLCV**  \n"
    f"{_status(_freshness['market_ohlcv_weekly']['age_h'], 24*7, 24*14)} "
    f"{_fmt_age(_freshness['market_ohlcv_weekly']['age_h'])}"
)
_fresh_cols[2].markdown(
    f"**🌡 Regime classifier**  \n"
    f"{_status(_freshness['regime_history']['age_h'], 24, 24*3)} "
    f"{_fmt_age(_freshness['regime_history']['age_h'])}"
)
_fresh_cols[3].markdown(
    f"**🤖 AI verdict latest**  \n"
    f"{_status(_freshness['ai_verdicts']['age_h'], 24*7, 24*30)} "
    f"{_fmt_age(_freshness['ai_verdicts']['age_h'])}"
)

# Refresh buttons — warm cache + record_regime via scheduler.jobs
_btn_cols = st.columns([1, 1, 1, 2])
_refresh_done = False

with _btn_cols[0]:
    if st.button("🔄 OHLCV", help="Warm cache daily+weekly per portfolio+watchlist", key="btn_refresh_ohlcv"):
        from propicks.scheduler.jobs import warm_cache as _wc
        with st.spinner("Warm cache OHLCV…"):
            try:
                res = _wc()
                st.toast(f"✓ OHLCV warmed: {res.get('notes', '')}", icon="✅")
                _refresh_done = True
            except Exception as exc:
                st.error(f"Warm cache fallito: {exc}")

with _btn_cols[1]:
    if st.button("🔄 Regime", help="Recompute regime weekly classifier su ^GSPC", key="btn_refresh_regime"):
        from propicks.scheduler.jobs import record_regime as _rr
        with st.spinner("Computing regime…"):
            try:
                res = _rr()
                st.toast(
                    f"✓ Regime: {res.get('regime_label', '?')} "
                    f"({res.get('regime_code', '?')}/5)",
                    icon="✅",
                )
                _refresh_done = True
            except Exception as exc:
                st.error(f"Regime fallito: {exc}")

with _btn_cols[2]:
    if st.button("🔄 All", type="primary", help="Refresh OHLCV + Regime", key="btn_refresh_all"):
        from propicks.scheduler.jobs import record_regime as _rr
        from propicks.scheduler.jobs import warm_cache as _wc
        with st.spinner("Refresh all…"):
            errs = []
            try:
                _wc()
            except Exception as exc:
                errs.append(f"OHLCV: {exc}")
            try:
                _rr()
            except Exception as exc:
                errs.append(f"Regime: {exc}")
            if errs:
                for e in errs:
                    st.error(e)
            else:
                st.toast("✓ All refreshed", icon="✅")
                _refresh_done = True

with _btn_cols[3]:
    st.caption(
        "Refresh manuale on-demand. Equivalente CLI: "
        "`propicks-scheduler job warm` + `propicks-scheduler job regime`."
    )

# Auto-clear freshness cache + rerun se refresh eseguito
if _refresh_done:
    _data_freshness.clear()  # type: ignore[attr-defined]
    st.rerun()

# Show warning banner if any source is stale
_warns = []
if (_freshness['market_ohlcv_daily']['age_h'] or 99) > 24:
    _warns.append(
        f"📊 Daily OHLCV cache stale ({_fmt_age(_freshness['market_ohlcv_daily']['age_h'])}). "
        "Click **🔄 OHLCV** sopra o lancia discovery per refetch."
    )
if (_freshness['regime_history']['age_h'] or 99) > 24*3:
    _warns.append(
        f"🌡 Regime classifier non aggiornato da {_fmt_age(_freshness['regime_history']['age_h'])}. "
        "Click **🔄 Regime** sopra per ricomputare."
    )
if _warns:
    for _w in _warns:
        st.warning(_w)

st.divider()

# ---------------------------------------------------------------------------
# Next actions — cosa richiede attenzione oggi
# ---------------------------------------------------------------------------
st.subheader("Prossime azioni")

_all_tickers: set[str] = set(positions.keys())
_watchlist = load_watchlist()
_wl_entries: dict = _watchlist.get("tickers", {}) if isinstance(_watchlist, dict) else {}
_wl_with_target = {t: e for t, e in _wl_entries.items() if e.get("target_entry")}
_all_tickers |= set(_wl_with_target.keys())

_prices_all: dict[str, float] = {}
if _all_tickers:
    with st.spinner("Fetching prezzi per next actions…"):
        _prices_all = cached_current_prices(tuple(sorted(_all_tickers)))

# 1) Time-stop triggered
_today = date.today()
time_stop_hits: list[tuple[str, int, float]] = []
for _t, _p in positions.items():
    cur = _prices_all.get(_t)
    if cur is None or not _p.get("entry_date"):
        continue
    if check_time_stop(
        _p["entry_date"], float(_p["entry_price"]), _today, float(cur),
        max_days_flat=DEFAULT_TIME_STOP_DAYS,
        flat_threshold_pct=DEFAULT_FLAT_THRESHOLD_PCT,
    ):
        pnl_pct = (cur - _p["entry_price"]) / _p["entry_price"]
        from datetime import datetime as _dt
        days = (_today - _dt.strptime(_p["entry_date"], "%Y-%m-%d").date()).days
        time_stop_hits.append((_t, days, pnl_pct))

# 2) Stop distance critica (< 2% dal current price)
stop_critical: list[tuple[str, float]] = []
for _t, _p in positions.items():
    cur = _prices_all.get(_t)
    stop = _p.get("stop_loss")
    if cur is None or stop is None or cur <= 0:
        continue
    dist = (cur - float(stop)) / cur
    if 0 <= dist <= 0.02:
        stop_critical.append((_t, dist))

# 3) Watchlist READY price-trigger (entro 2% dal target_entry)
READY_DIST_PCT = 0.02
ready_hits: list[tuple[str, float, float]] = []
for _t, _e in _wl_with_target.items():
    cur = _prices_all.get(_t)
    target = float(_e["target_entry"])
    if cur is None or target <= 0:
        continue
    dist = (cur - target) / target
    if abs(dist) <= READY_DIST_PCT:
        ready_hits.append((_t, cur, dist))

# 4) Invariants violati
_cash_pct = cash / total if total else 1.0
_risk_week = sum(
    (float(p["entry_price"]) - float(p["stop_loss"])) * float(p.get("shares") or 0)
    for p in positions.values()
    if p.get("stop_loss") is not None
)
_risk_pct = _risk_week / total if total else 0.0
invariant_alerts: list[str] = []
if _cash_pct < MIN_CASH_RESERVE_PCT:
    invariant_alerts.append(
        f"Cash {_cash_pct * 100:.1f}% sotto riserva minima "
        f"{MIN_CASH_RESERVE_PCT * 100:.0f}% — niente nuove entry"
    )
if _risk_pct >= MAX_LOSS_WEEKLY_PCT:
    invariant_alerts.append(
        f"Rischio settimanale aggregato {_risk_pct * 100:.2f}% oltre il "
        f"limite {MAX_LOSS_WEEKLY_PCT * 100:.0f}% — valuta riduzione"
    )

_n_actions = (
    len(time_stop_hits) + len(stop_critical) + len(ready_hits) + len(invariant_alerts)
)
if _n_actions == 0:
    st.success("Nessuna azione pendente. Portfolio in linea con invariants e watchlist senza trigger.")
else:
    _c1, _c2 = st.columns(2)
    with _c1:
        if time_stop_hits:
            st.markdown("**⏱ Time-stop triggered**")
            for _t, _days, _pnl in time_stop_hits:
                st.markdown(f"- **{_t}** · flat da {_days} gg ({_pnl * 100:+.2f}%) → valuta chiusura")
        if stop_critical:
            st.markdown("**🔻 Stop a rischio (≤ 2%)**")
            for _t, _d in sorted(stop_critical, key=lambda x: x[1]):
                st.markdown(f"- **{_t}** · dist stop {_d * 100:+.2f}%")
    with _c2:
        if ready_hits:
            st.markdown("**🎯 Watchlist entry pronte**")
            for _t, _cur, _dist in sorted(ready_hits, key=lambda x: abs(x[2])):
                st.markdown(
                    f"- **{_t}** @ {_cur:.2f} · {_dist * 100:+.2f}% dal target → "
                    "vai su **Momentum** per validazione completa"
                )
        if invariant_alerts:
            st.markdown("**⚠️ Invariants**")
            for _a in invariant_alerts:
                st.markdown(f"- {_a}")
    st.caption(
        "**Time-stop** = posizione flat (|P&L| < 2%) da ≥ 30 gg · "
        "**Stop a rischio** = current − stop ≤ 2% del prezzo · "
        "**Watchlist pronte** = trigger di prezzo (±2% dal target). "
        "Il READY completo con score+regime vive sulla pagina Watchlist."
    )

st.divider()

# ---------------------------------------------------------------------------
# Cadenza della settimana — focus sul giorno corrente
# ---------------------------------------------------------------------------
_today_dt = date.today()
_day_name, _day_cad = today_block(_today_dt.weekday())
st.subheader(f"Cadenza · {_day_name} — {_day_cad['name']}")
_dur = _day_cad.get("duration", "")
if _dur:
    st.caption(f"Budget tempo: {_dur}")

for _block_title, _block_dur, _block_items in _day_cad["blocks"]:
    _header = f"**{_block_title}**"
    if _block_dur and _block_dur != "—":
        _header += f"  · _{_block_dur}_"
    st.markdown(_header)
    for _item in _block_items:
        st.markdown(f"- {_item}")

with st.expander("Cadenza completa della settimana", expanded=False):
    for _dow in range(7):
        _name = DAY_NAMES_IT[_dow]
        _cad = WEEKLY_CADENCE[_dow]
        _is_today = _dow == _today_dt.weekday()
        _prefix = "▶ " if _is_today else ""
        st.markdown(
            f"{_prefix}**{_name} — {_cad['name']}** · _{_cad.get('duration', '')}_"
        )
        for _bt, _bd, _bi in _cad["blocks"]:
            st.markdown(f"&nbsp;&nbsp;• _{_bt}_ ({_bd}): " + " · ".join(_bi[:2])
                        + (" …" if len(_bi) > 2 else ""), unsafe_allow_html=True)
    st.caption(
        "Dettaglio completo con budget tempo e tabelle trigger: "
        "`docs/Weekly_Operating_Framework.md`."
    )

st.divider()

# ---------------------------------------------------------------------------
# Mark-to-market
# ---------------------------------------------------------------------------
prices: dict[str, float] = {}
unrealized = 0.0
if positions:
    with st.spinner("Fetching prezzi correnti…"):
        prices = cached_current_prices(tuple(sorted(positions.keys())))
    for t, p in positions.items():
        cur = prices.get(t)
        if cur is not None:
            unrealized += (cur - p["entry_price"]) * p["shares"]

invested = total - cash
cash_pct = cash / total if total else 0.0
min_cash = total * MIN_CASH_RESERVE_PCT

kpi_row([
    ("Portfolio value", fmt_eur(total + unrealized), None),
    ("Cash", fmt_eur(cash), fmt_pct(cash_pct)),
    ("Invested", fmt_eur(invested), fmt_pct(invested / total) if total else "—"),
    (
        "P&L unrealized",
        fmt_eur(unrealized),
        fmt_pct(unrealized / invested) if invested else None,
    ),
    ("Positions", f"{len(positions)} / {MAX_POSITIONS}", None),
])

if cash < min_cash:
    st.error(
        f"Cash sotto la riserva minima ({fmt_eur(cash)} < {fmt_eur(min_cash)} "
        f"= {MIN_CASH_RESERVE_PCT * 100:.0f}% del portfolio). Nessuna nuova entry."
    )

st.divider()

# ---------------------------------------------------------------------------
# Open positions table
# ---------------------------------------------------------------------------
st.subheader("Posizioni aperte")
if not positions:
    st.info("Nessuna posizione aperta. Vai su **Momentum** o **ETF Rotation** per analizzare setup.")
else:
    denom = total + unrealized
    rows = []
    for ticker, p in sorted(positions.items()):
        cur = prices.get(ticker)
        mv = (cur or p["entry_price"]) * p["shares"]
        pnl = (cur - p["entry_price"]) * p["shares"] if cur is not None else None
        pnl_pct = (cur - p["entry_price"]) / p["entry_price"] if cur is not None else None
        stop_dist = (
            (cur - p["stop_loss"]) / cur if cur is not None and cur > 0 else None
        )
        rows.append({
            "": pnl_arrow(pnl_pct),
            "Ticker": ticker,
            "Strategy": p.get("strategy") or "—",
            "Shares": p["shares"],
            "Entry": p["entry_price"],
            "Current": cur,
            "MV": mv,
            # ProgressColumn/NumberColumn usano printf: valori già moltiplicati *100
            "Size%": (mv / denom * 100) if denom else None,
            "P&L": pnl,
            "P&L%": pnl_pct * 100 if pnl_pct is not None else None,
            "Stop": p["stop_loss"],
            "Stop dist": stop_dist * 100 if stop_dist is not None else None,
            "Target": p.get("target"),
        })
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "Entry": st.column_config.NumberColumn(format="%.2f"),
            "Current": st.column_config.NumberColumn(format="%.2f"),
            "MV": st.column_config.NumberColumn(format="€ %.2f"),
            "Size%": st.column_config.ProgressColumn(
                format="%.1f%%", min_value=0.0, max_value=20.0,
                help="Quota del portfolio mark-to-market. Cap: 15% stock / 20% ETF.",
            ),
            "P&L": st.column_config.NumberColumn(format="€ %+.2f"),
            "P&L%": st.column_config.NumberColumn(format="%+.2f%%"),
            "Stop": st.column_config.NumberColumn(format="%.2f"),
            "Stop dist": st.column_config.ProgressColumn(
                format="%.2f%%", min_value=0.0, max_value=10.0,
                help="Distanza current → stop. Vicino a 0 = stop a rischio.",
            ),
            "Target": st.column_config.NumberColumn(format="%.2f"),
        },
    )

# ---------------------------------------------------------------------------
# Allocation glance (mini-pie buckets) — quick view senza click su page 8
# ---------------------------------------------------------------------------
if positions:
    from propicks.domain.sizing import (
        is_etf_rotation_position,
        is_thematic_position,
    )

    cash_mtm = float(portfolio.get("cash") or 0)
    stock_val = 0.0
    etf_rot_val = 0.0
    thematic_val = 0.0
    for tk, pos in positions.items():
        cur = prices.get(tk)
        if cur is None:
            cur = pos.get("entry_price", 0)
        mv_pos = float(pos.get("shares") or 0) * float(cur)
        if is_thematic_position(pos, ticker=tk):
            thematic_val += mv_pos
        elif is_etf_rotation_position(pos, ticker=tk):
            etf_rot_val += mv_pos
        else:
            stock_val += mv_pos

    bucket_data = [
        ("📊 Stock", stock_val, "#3b82f6"),
        ("📈 ETF Rotation", etf_rot_val, "#10b981"),
        ("🎯 Thematic", thematic_val, "#a855f7"),
        ("💰 Cash", cash_mtm, "#94a3b8"),
    ]
    bucket_data = [(l, v, c) for l, v, c in bucket_data if v > 0]

    total_mtm = stock_val + etf_rot_val + thematic_val + cash_mtm
    if total_mtm > 0:
        import plotly.graph_objects as go

        col_pie, col_caps = st.columns([1, 1])
        with col_pie:
            fig = go.Figure(data=[go.Pie(
                labels=[d[0] for d in bucket_data],
                values=[d[1] for d in bucket_data],
                marker=dict(colors=[d[2] for d in bucket_data]),
                hole=0.5,
                textinfo="label+percent",
                textposition="outside",
                hovertemplate="<b>%{label}</b><br>€ %{value:.2f}<br>%{percent}<extra></extra>",
            )])
            fig.update_layout(
                height=280,
                margin=dict(l=10, r=10, t=20, b=10),
                showlegend=False,
            )
            st.plotly_chart(fig, width="stretch")

        with col_caps:
            stock_pct = stock_val / total_mtm * 100
            etf_pct = (etf_rot_val + thematic_val) / total_mtm * 100
            cash_pct = cash_mtm / total_mtm * 100
            stock_status = "🟢" if stock_pct < 40 else "🔴"
            etf_status = "🟢" if etf_pct < 60 else "🔴"
            cash_status = "🟢" if cash_pct >= 20 else "🔴"

            st.markdown("##### Bucket allocation vs cap")
            st.markdown(
                f"{stock_status} **Stock** {stock_pct:.1f}% / 40%  "
                f"<span style='color:#94a3b8'>headroom {max(0, 40 - stock_pct):.1f}%</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"{etf_status} **ETF** {etf_pct:.1f}% / 60%  "
                f"<span style='color:#94a3b8'>headroom {max(0, 60 - etf_pct):.1f}%</span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"{cash_status} **Cash** {cash_pct:.1f}% (min 20%)  "
                f"<span style='color:#94a3b8'>investible {max(0, cash_pct - 20):.1f}%</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                "Stock = momentum + contrarian merged. ETF = rotation + thematic merged. "
                "Per breakdown sector + correlation vai a **Portfolio → Rischio & esposizione**."
            )

    # ─── Risk dashboard widget ─────────────────────────────────────────
    # Per-position rischio a stop + bucket headroom + weekly risk used.
    from propicks.config import MAX_LOSS_WEEKLY_PCT

    st.subheader("⚠️ Risk dashboard")

    # Calcola rischio per posizione
    risk_rows = []
    risk_total = 0.0
    for tk, p in positions.items():
        entry = float(p.get("entry_price", 0))
        stop = float(p.get("stop_loss", 0))
        shares = float(p.get("shares") or 0)
        if entry > 0 and stop > 0 and entry > stop:
            r_eur = (entry - stop) * shares
            r_pct = r_eur / total * 100 if total else 0
            risk_total += r_eur
            risk_rows.append({"ticker": tk, "risk_eur": r_eur, "risk_pct": r_pct})

    weekly_limit_eur = total * MAX_LOSS_WEEKLY_PCT
    weekly_used_pct = risk_total / weekly_limit_eur * 100 if weekly_limit_eur else 0

    risk_col1, risk_col2 = st.columns([1, 1])

    # Risk per-position bar chart
    with risk_col1:
        if risk_rows:
            import plotly.graph_objects as go

            risk_rows.sort(key=lambda r: r["risk_pct"], reverse=True)
            tk_r = [r["ticker"] for r in risk_rows]
            pct_r = [r["risk_pct"] for r in risk_rows]
            # Color per soglia: <1% verde, 1-2% giallo, >2% rosso
            colors_r = [
                "#16a34a" if p < 1.0
                else "#ca8a04" if p < 2.0
                else "#dc2626"
                for p in pct_r
            ]
            fig_r = go.Figure()
            fig_r.add_trace(go.Bar(
                x=pct_r, y=tk_r, orientation="h",
                marker=dict(color=colors_r),
                text=[f"{p:.2f}%" for p in pct_r],
                textposition="outside",
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Rischio €%{customdata:.2f}<br>"
                    "%{x:.2f}% capitale<extra></extra>"
                ),
                customdata=[r["risk_eur"] for r in risk_rows],
            ))
            # Vline 2% reference
            fig_r.add_vline(
                x=2.0, line_dash="dot", line_color="#dc2626", opacity=0.5,
                annotation_text="2% per-trade", annotation_position="top",
            )
            fig_r.update_layout(
                title=dict(
                    text="Rischio per posizione (a stop)",
                    x=0.5, xanchor="center", font=dict(size=13),
                ),
                xaxis_title="% capitale", yaxis_title="",
                yaxis=dict(autorange="reversed"),
                height=max(220, len(risk_rows) * 32 + 80),
                margin=dict(l=20, r=20, t=50, b=20),
                showlegend=False,
            )
            st.plotly_chart(fig_r, width="stretch")
        else:
            st.caption("_Nessuna posizione con stop valido per calcolo rischio._")

    # Weekly risk + bucket caps progress
    with risk_col2:
        st.markdown("##### Weekly risk used")
        weekly_color = (
            "#16a34a" if weekly_used_pct < 50
            else "#ca8a04" if weekly_used_pct < 80
            else "#dc2626"
        )
        st.markdown(
            f"<div style='font-size:28px;font-weight:700;color:{weekly_color};'>"
            f"{weekly_used_pct:.0f}%</div>"
            f"<div style='color:#64748b;font-size:13px;'>"
            f"€{risk_total:.2f} su €{weekly_limit_eur:.2f} cap "
            f"({MAX_LOSS_WEEKLY_PCT*100:.0f}% portfolio)</div>",
            unsafe_allow_html=True,
        )
        st.progress(min(1.0, weekly_used_pct / 100), text=None)
        st.caption(
            f"Worst-case se TUTTI gli stop saltano insieme. Soglia warning 80%, "
            f"hard stop trading sopra 100%."
        )

        # Bucket caps progress
        st.markdown("##### Bucket caps")
        st.progress(min(1.0, stock_pct / 40), text=f"📊 Stock {stock_pct:.1f}% / 40%")
        st.progress(min(1.0, etf_pct / 60), text=f"📈 ETF {etf_pct:.1f}% / 60%")
        st.progress(
            min(1.0, max(0, (20 - cash_pct)) / 20) if cash_pct < 20 else 0.0,
            text=f"💰 Cash {cash_pct:.1f}% (min 20% — {'⚠ sotto' if cash_pct < 20 else 'OK'})",
        )

    st.divider()

# ---------------------------------------------------------------------------
# Recent journal entries
# ---------------------------------------------------------------------------
st.subheader("Ultime chiusure")
journal = load_journal()
closed = [t for t in journal if t.get("status") == "closed"]
closed.sort(key=lambda t: t.get("exit_date") or "", reverse=True)
if not closed:
    st.caption("Nessun trade chiuso nel journal.")
else:
    recent = closed[:5]
    rows = []
    for t in recent:
        pnl_pct = t.get("pnl_pct")
        rows.append({
            "": pnl_arrow((pnl_pct / 100) if pnl_pct is not None else None),
            "Ticker": t["ticker"],
            "Strategy": t.get("strategy") or "—",
            "Entry date": t["entry_date"],
            "Exit date": t.get("exit_date") or "—",
            "Days": t.get("duration_days"),
            "P&L %": pnl_pct,
            "Reason": t.get("exit_reason") or "—",
        })
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "P&L %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Days": st.column_config.NumberColumn(format="%d"),
        },
    )

# ---------------------------------------------------------------------------
# Workflow per strategia — percorso end-to-end dal pick al review
# ---------------------------------------------------------------------------
st.subheader("Workflow per strategia")
st.caption(
    "Step-by-step end-to-end per ogni strategia: scan → check engine Python → "
    "AI verdict → conferma TradingView → entry sizing → trade management. "
    "Ogni riga ha pagina dashboard + comando CLI."
)

_wf_tabs = st.tabs([
    "📊 Stock Momentum",
    "↩️ Contrarian",
    "🔁 ETF Rotation",
    "🎯 Thematic ETF",
])


def _render_workflow(steps: list[tuple[str, str, str, str, str]]) -> None:
    """Render N-step workflow as a vertical timeline with cards."""
    for i, (title, emoji, desc, page, cli) in enumerate(steps, 1):
        col_n, col_body = st.columns([1, 12])
        with col_n:
            st.markdown(
                f"<div style='font-size:22px;line-height:1;text-align:center;"
                f"padding-top:6px;color:#3b82f6;font-weight:700;'>{i}</div>",
                unsafe_allow_html=True,
            )
        with col_body:
            st.markdown(
                f"**{emoji} {title}**  \n"
                f"{desc}  \n"
                f"_Pagina_: **{page}**  ·  `{cli}`"
            )
        if i < len(steps):
            st.markdown(
                "<div style='border-left:2px dashed #cbd5e1;height:14px;"
                "margin-left:18px;'></div>",
                unsafe_allow_html=True,
            )


# ─── 1. Stock Momentum ────────────────────────────────────────────────────
with _wf_tabs[0]:
    st.markdown(
        "**Quando**: regime weekly ≥ NEUTRAL, universo S&P 500 / Nasdaq / "
        "FTSE MIB / STOXX 600. Setup pullback su uptrend o breakout.  \n"
        "**Cap**: 15% per posizione · 8% loss · bucket Stock 40% aggregate."
    )
    _render_workflow([
        (
            "Scan tecnico Python", "🔍",
            "Lancia discovery su universo (top 30-50 ticker per market cap). "
            "Engine calcola composite 6 sub-score (trend/momentum/volume/dist-high/vol/MA-cross). "
            "Auto-add classe A (target=current) e B (no target) in watchlist.",
            "Page 1 Momentum → tab Discovery",
            "propicks-momentum --discover-sp500 --top 50",
        ),
        (
            "Validate AI (Claude)", "🤖",
            "Gate doppio: score ≥60 + regime ≥NEUTRAL. Verdict CONFIRM/CAUTION/REJECT "
            "con conviction 0-10, bull/bear case, invalidation triggers. Cache 24h.",
            "Page 1 → bottone Valida",
            "propicks-momentum AAPL --validate",
        ),
        (
            "Cross-check Perplexity / Sonar", "🔎",
            "Copy prompt --validate (selettore Sonar/Claude diretto) per second opinion. "
            "Sonar ha web search live (catalyst, earnings imminent, short interest).",
            "Page 1 → expander Prompt --validate",
            "propicks-momentum AAPL --validate --json (fallback)",
        ),
        (
            "Conferma su TradingView", "📈",
            "Apri chart ticker daily + Pine `daily_signal_engine.pine`. "
            "Verifica: composite Pine ≈ engine, regime weekly NEUTRAL+, "
            "volume divergence, no gap earnings entro 5gg.",
            "tradingview/daily_signal_engine.pine",
            "(visual check, no CLI)",
        ),
        (
            "Sizing + Entry", "📏",
            "Tab Size calculator: entry/stop/target → propone shares con cap "
            "15% + check earnings hard gate 5gg + Stock bucket 40% headroom.",
            "Page 8 Portfolio → tab Size calculator",
            "propicks-portfolio size AAPL --entry 100 --stop 95 --score-claude 8 --score-tech 75",
        ),
        (
            "Open trade (sync portfolio + journal)", "📝",
            "Tab Add position O Page 9 Journal → Add trade. Journal scrive prima "
            "(append-only), poi sync portfolio (cash decrement, position add).",
            "Page 9 Journal → tab Add trade",
            "propicks-journal add AAPL long --entry-price 100 --shares 10 --stop 95 --target 120 --strategy TechTitans",
        ),
        (
            "Trade management", "🔧",
            "Trailing stop ATR-based (ratchet-up only, opt-in per posizione, default OFF). "
            "Time-stop 30gg se P&L flat (|<2%|). Page 8 tab Trade mgmt → 'Calcola suggerimenti' + Apply.",
            "Page 8 → tab Trade management",
            "propicks-portfolio manage --apply",
        ),
        (
            "Close + Review", "💰",
            "Stop hit / target raggiunto / time-stop / tesi invalidata → Journal close. "
            "Page 9 Stats: equity curve realized, P&L distribution, win rate per strategy.",
            "Page 9 Journal → tab Close trade",
            "propicks-journal close AAPL --exit-price 118 --reason 'Target raggiunto'",
        ),
    ])


# ─── 2. Contrarian ──────────────────────────────────────────────────────
with _wf_tabs[1]:
    st.markdown(
        "**Quando**: regime weekly NEUTRAL/BEAR (skip STRONG_BULL/STRONG_BEAR). "
        "VIX > 20 = sweet spot (fear without crash). Setup oversold con quality intact.  \n"
        "**Cap**: 8% per posizione · 12% loss · max 3 pos · bucket Contrarian 20% (sub di Stock 40%)."
    )
    _render_workflow([
        (
            "Discovery oversold", "🔍",
            "3-stage pipeline: prefilter cheap (RSI<35 + dist ATR≥1×) → full scoring "
            "(oversold 40% + quality 25% + context 20% + reversion 15%) → top N.",
            "Page 3 Contrarian → tab Discovery",
            "propicks-contra --discover-sp500 --top 20",
        ),
        (
            "Quality gate hard", "🛡️",
            "Filtro non bypass: price > EMA200 weekly. Garantisce trend lungo intact "
            "(no falling knife). VIX context check: spike 25+ = paura tradabile, <14 = euforia (skip).",
            "Page 3 → KPI VIX live",
            "(automatico in scoring)",
        ),
        (
            "Validate AI flush vs break", "🤖",
            "Claude classifica selloff: FLUSH (macro/sector rotation/tech) → tradable, "
            "BREAK (earnings miss strutturale, fraud, guidance cut) → REJECT. "
            "Catalyst type assessment + reversion target (EMA50 daily).",
            "Page 3 → bottone Valida",
            "propicks-contra GILD --validate",
        ),
        (
            "Conferma su TradingView", "📈",
            "Apri chart + Pine `contrarian_signal_engine.pine`. "
            "Verifica: oversold confermato (RSI<30, multi-ATR sotto EMA50), "
            "quality EMA200w intact, regime weekly compatibile.",
            "tradingview/contrarian_signal_engine.pine",
            "(visual check)",
        ),
        (
            "Sizing + Entry", "📏",
            "Bucket=contrarian → cap 8%, loss 12%, max 3 pos enforced. Stop = "
            "recent_low − 1×ATR (NO trailing — target fisso EMA50). "
            "ignore_earnings=True ammesso per trade post-flush intentional.",
            "Page 8 → tab Size (bucket=contrarian)",
            "propicks-portfolio size GILD --entry 130 --stop 124 --contrarian",
        ),
        (
            "Open trade", "📝",
            "Strategy='Contrarian'. Target = EMA50 daily (drifta ricalcolato a ogni manage). "
            "Tab Open posizione + sync journal.",
            "Page 9 Journal → Add trade (strategy=Contrarian)",
            "propicks-journal add GILD long --entry-price 130 --target 137 --stop 124 --strategy Contrarian",
        ),
        (
            "Trade management", "🔧",
            "Time-stop 15gg (mean reversion 5-15gg, oltre = tesi invalida). "
            "Target dinamico EMA50 ricalcolato — se prezzo raggiunge target → close. "
            "Trailing OFF di default per contrarian.",
            "Page 8 → tab Trade management",
            "propicks-portfolio manage --time-stop 15 --apply",
        ),
        (
            "Close + Review", "💰",
            "Reason: target / stop / time-stop / tesi rotta. Decay monitor "
            "controlla edge nel tempo (richiede 50+ trade per affidabilità).",
            "Page 14 Decay Monitor (filter contrarian)",
            "propicks-decay monitor --strategy contrarian",
        ),
    ])


# ─── 3. ETF Rotation ────────────────────────────────────────────────────
with _wf_tabs[2]:
    st.markdown(
        "**Quando**: rebalance weekly/biweekly. Universo WORLD (Xtrackers MSCI World "
        "10 settori, .MI Borsa Italiana) o US SPDR reference.  \n"
        "**Cap**: 20% per posizione · 5% stop fisso · bucket ETF 60% (con thematic merged)."
    )
    _render_workflow([
        (
            "Ranking universo", "🔍",
            "Scoring 4 sub-score: RS vs benchmark (40%) + regime fit (30%) + "
            "abs momentum 3M (20%) + trend EMA30w (10%). Regime hard-gate: "
            "STRONG_BEAR non-favored=0, BEAR non-favored cap 50.",
            "Page 2 ETF Rotation",
            "propicks-rotate (default WORLD)",
        ),
        (
            "Allocation proposta", "📊",
            "Top-N (default 3) equal-weight 20% ciascuno, capped 60% aggregate. "
            "STRONG_BEAR → flat (no allocation). BEAR → top-1 difensivo only.",
            "Page 2 → checkbox Allocazione",
            "propicks-rotate --allocate",
        ),
        (
            "Validate macro Claude", "🤖",
            "Macro strategist persona: stress-test su breadth, positioning, flows, "
            "rotation stage (EARLY/MID/LATE). Cache 8h, skip in STRONG_BEAR.",
            "Page 2 → checkbox Valida",
            "propicks-rotate --validate",
        ),
        (
            "Cross-check Sonar", "🔎",
            "Copy prompt Sonar nativo per macro view live: HY OAS, DXY, breadth %, "
            "AUM flows ETF. Constraint alternative_sector = lista universo - top-3.",
            "Page 2 → expander Prompt --validate",
            "(copy-incolla Sonar)",
        ),
        (
            "Conferma su TradingView", "📈",
            "Apri chart ETF (es. XDWT.MI) weekly + Pine `etf_rotation_engine.pine`. "
            "Verifica RS line slope, regime classifier weekly, sector_key match config.",
            "tradingview/etf_rotation_engine.pine",
            "(visual check)",
        ),
        (
            "Sizing + Entry", "📏",
            "asset_type=SECTOR_ETF → cap 20%. Stop -5% hard (ETF bassa vol, ATR salta). "
            "Strategy='ETF_Rotation'. Bucket ETF aggregate gate 60%.",
            "Page 8 → tab Size (asset_type=SECTOR_ETF)",
            "propicks-portfolio size XDWT.MI --entry 110 --stop 104.5 --score-tech 75",
        ),
        (
            "Open trade (a tranche)", "📝",
            "Regola operativa: cambio regime BULL→BEAR = uscita 2-3 tranche su 5 sessioni "
            "per evitare whipsaw. Add position con strategy=ETF_Rotation.",
            "Page 9 Journal → Add trade",
            "propicks-journal add XDWT.MI long --entry-price 110 --shares 3 --stop 104.5 --strategy ETF_Rotation",
        ),
        (
            "Manage + Rebalance", "🔧",
            "Trigger rotate quando score sotto threshold delta 10pts (hysteresis). "
            "Stop -5% safety net, exit primario è regime change. "
            "Page 13 Regime Composite per early-warning lead 1-3 settimane.",
            "Page 2 (re-run) · Page 13 Regime Composite",
            "propicks-rotate · propicks-regime composite",
        ),
        (
            "Close + Review", "💰",
            "Close su rotation suggested o regime degrade. Report attribution "
            "decompone alpha/beta/sector/timing.",
            "Page 9 Journal → Close · Page 10 Reports",
            "propicks-report attribution",
        ),
    ])


# ─── 4. Thematic ETF ────────────────────────────────────────────────────
with _wf_tabs[3]:
    st.markdown(
        "**Quando**: tematici sub-industry / cross-sector con alfa distinto dal parent. "
        "Bucket satellite, max 2 posizioni simultanee, skip BEAR/STRONG_BEAR.  \n"
        "**Cap**: 15% per posizione · 10% loss · max 2 pos · cap parent-aggregate 25% "
        "(weight(theme)+weight(parent_ETF)≤25%)."
    )
    _render_workflow([
        (
            "Ranking universo", "🔍",
            "Scoring 4 sub-score: RS-vs-parent (50%) + abs mom 25% + trend 15% + "
            "parent regime fit 10%. RS-vs-parent peso alto = discrimina alfa "
            "tematico vs leveraged sector bet.",
            "Page 4 Thematic → mode 'Ranking universo'",
            "propicks-themes --rank",
        ),
        (
            "Correlation kill-switch", "⚠️",
            "Corr 60d theme/parent ≥ 0.85 → composite forzato a 0 (alfa illusorio = "
            "leverage parent). Visibile in tabella con flag ⚠ CORR-KILL.",
            "Page 4 → tabella ranking + chart sub-score",
            "(automatico in scoring)",
        ),
        (
            "Validate AI thematic specialist", "🤖",
            "Persona thematic investor: theme stage (EARLY 3-6M / MID 6-18M / LATE 18M+), "
            "crowding/AUM flows, concentration top 3-5 holdings, catalyst 2-3M.",
            "Page 4 → bottone Valida",
            "propicks-themes LOCK.MI --validate",
        ),
        (
            "Cross-check Sonar (alternative_ticker)", "🔎",
            "Sonar prompt con constraint alternative_ticker = same-cohort candidates "
            "(es. cybersecurity → CIBR/BUG/LOCK). Anti-confabulazione + anti-cohort-drift.",
            "Page 4 → expander Prompt --validate",
            "(copy-incolla Sonar)",
        ),
        (
            "Conferma su TradingView", "📈",
            "Apri chart tematico (es. LOCK.MI) weekly + Pine `thematic_signal_engine.pine` "
            "configurando parent symbol (es. XDWT.MI). RS line theme/parent on-chart, "
            "alert su corr-kill.",
            "tradingview/thematic_signal_engine.pine",
            "(visual check, set parent_symbol)",
        ),
        (
            "Sizing + Entry (parent-aggregate check)", "📏",
            "Bucket=thematic → cap 15%, loss 10%, max 2 pos, parent-aggregate 25% enforce. "
            "Esempio: se XDWT.MI già 12% in portfolio, LOCK.MI max 13% (= 25% - 12%).",
            "Page 8 → tab Size (bucket=thematic, asset=THEMATIC_ETF)",
            "propicks-portfolio size LOCK.MI --entry 9 --stop 8.1 --score-tech 75",
        ),
        (
            "Open trade", "📝",
            "Strategy='Thematic'. Add position con parent_aggregate gate enforced. "
            "Detection automatica via ticker registrato in THEMATIC_ETFS.",
            "Page 9 Journal → Add trade (strategy=Thematic)",
            "propicks-journal add LOCK.MI long --entry-price 9 --shares 50 --stop 8.1 --strategy Thematic",
        ),
        (
            "Trade management", "🔧",
            "Stop hard 10% (ATR% tematici alto). Time-stop 30gg default. "
            "Re-check corr trimestrale: se corr_60d cresce verso 0.85 → considera close.",
            "Page 8 → tab Trade management",
            "propicks-portfolio manage --apply",
        ),
        (
            "Decay + Promotion gate", "📊",
            "Gate journal-evidence: dopo 15 trade chiusi tematici verifica win rate ≥ "
            "baseline single-stock + corr_avg < 0.85. Se fail → kill subpackage. "
            "Decay monitor + filter thematic.",
            "Page 14 Decay Monitor",
            "propicks-decay monitor --strategy thematic",
        ),
    ])


st.caption(
    "💡 **Pattern comune ai 4 workflow**: scan engine Python → AI verdict (Claude) → "
    "cross-check Sonar/Perplexity → conferma TradingView Pine (visual) → "
    "sizing rispetta bucket cap → open journal+portfolio sync → manage trailing/time-stop → "
    "close + review stats. Ogni step ha CLI e dashboard equivalente."
)

with st.expander("Scala di classificazione score (A/B/C/D)", expanded=False):
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.markdown("**A — Azione immediata**  \n" + score_badge(80) + "  \nscore ≥ 75", unsafe_allow_html=True)
    _c2.markdown("**B — Watchlist**  \n" + score_badge(65) + "  \nscore 60–74", unsafe_allow_html=True)
    _c3.markdown("**C — Neutrale**  \n" + score_badge(50) + "  \nscore 45–59", unsafe_allow_html=True)
    _c4.markdown("**D — Skip**  \n" + score_badge(30) + "  \nscore < 45", unsafe_allow_html=True)
