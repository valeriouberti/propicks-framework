"""Home / Overview — stato aggregato del portfolio.

Streamlit multi-page app: ogni file in ``pages/`` diventa una voce di menu.
Questa è la pagina di default (sidebar top entry).
"""

from __future__ import annotations

import streamlit as st

from datetime import date

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
    regime_badge,
    score_badge,
)
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
                    "vai su **Scanner** per validazione completa"
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
    st.info("Nessuna posizione aperta. Vai su **Scanner** o **ETF Rotation** per analizzare setup.")
else:
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
            "Ticker": ticker,
            "Strategy": p.get("strategy") or "—",
            "Shares": p["shares"],
            "Entry": f"{p['entry_price']:.2f}",
            "Current": f"{cur:.2f}" if cur is not None else "—",
            "MV": fmt_eur(mv),
            "Size%": fmt_pct(mv / (total + unrealized)) if total else "—",
            "P&L": fmt_eur(pnl) if pnl is not None else "—",
            "P&L%": fmt_pct(pnl_pct) if pnl_pct is not None else "—",
            "Stop": f"{p['stop_loss']:.2f}",
            "Stop dist": fmt_pct(stop_dist) if stop_dist is not None else "—",
            "Target": f"{p['target']:.2f}" if p.get("target") else "—",
        })
    st.dataframe(rows, width="stretch", hide_index=True)

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
        rows.append({
            "Ticker": t["ticker"],
            "Strategy": t.get("strategy") or "—",
            "Entry date": t["entry_date"],
            "Exit date": t.get("exit_date") or "—",
            "Days": str(t.get("duration_days")) if t.get("duration_days") is not None else "—",
            "P&L %": fmt_pct((t.get("pnl_pct") or 0) / 100) if t.get("pnl_pct") is not None else "—",
            "Reason": t.get("exit_reason") or "—",
        })
    st.dataframe(rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Watchlist scorer (top-N dal journal recent closed per strategy)
# ---------------------------------------------------------------------------
st.subheader("Score classification — riferimento")
col1, col2, col3, col4 = st.columns(4)
col1.markdown("A — Azione immediata  \n" + score_badge(80), unsafe_allow_html=True)
col2.markdown("B — Watchlist  \n" + score_badge(65), unsafe_allow_html=True)
col3.markdown("C — Neutrale  \n" + score_badge(50), unsafe_allow_html=True)
col4.markdown("D — Skip  \n" + score_badge(30), unsafe_allow_html=True)
