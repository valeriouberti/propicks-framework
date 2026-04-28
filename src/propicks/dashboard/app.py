"""Home / Overview — stato aggregato del portfolio.

Streamlit multi-page app: ogni file in ``pages/`` diventa una voce di menu.
Questa è la pagina di default (sidebar top entry).
"""

from __future__ import annotations

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
    st.info("Nessuna posizione aperta. Vai su **Scanner** o **ETF Rotation** per analizzare setup.")
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
# Workflow tipico — percorso end-to-end dal pick al review
# ---------------------------------------------------------------------------
st.subheader("Workflow tipico")
st.caption(
    "Il ciclo end-to-end dal pick mensile al review. Ogni step ha una pagina "
    "dedicata e il comando CLI equivalente."
)

_steps = [
    (
        "1 · Scan tecnico",
        "🔍",
        "Analisi indicatori + classificazione A/B/C/D. Auto-add in watchlist "
        "per classe A (target = prezzo corrente) e B.",
        "Scanner",
        "propicks-momentum TICKER",
    ),
    (
        "2 · Watchlist",
        "👀",
        "Incubatrice per classe B (target manuale) e A in attesa. Flag READY "
        "quando score ≥ 60 e prezzo entro ±2% dal target.",
        "Watchlist",
        "propicks-watchlist list",
    ),
    (
        "3 · Validate AI",
        "🤖",
        "Claude verdict strutturato (CONFIRM / CAUTION / REJECT) con gate "
        "doppio: score ≥ 60 **e** regime ≥ NEUTRAL. Cache 24h.",
        "Scanner → Valida",
        "propicks-momentum TICKER --validate",
    ),
    (
        "4 · Size + Open",
        "📏",
        "Sizing rischio (cap 15%/20%, reserve 20%, max loss 8%) poi Journal "
        "add (sync automatico su portfolio).",
        "Portfolio · Journal",
        "propicks-journal add TICKER ...",
    ),
    (
        "5 · Manage",
        "🔧",
        "Trailing stop ATR-based (ratchet-up) + time-stop su trade flat da "
        "30 gg. Opt-in per posizione.",
        "Portfolio → Trade mgmt",
        "propicks-portfolio manage --apply",
    ),
    (
        "6 · Close + Review",
        "💰",
        "Chiusura trade (P&L nel journal) e report weekly/monthly per "
        "validare che la strategia funzioni.",
        "Journal · Reports",
        "propicks-journal close / propicks-report",
    ),
]
_rows = [_steps[:3], _steps[3:]]
for _row in _rows:
    _cols = st.columns(3)
    for _col, (_title, _emoji, _desc, _page, _cli) in zip(_cols, _row, strict=True):
        _col.markdown(
            f"**{_emoji} {_title}**  \n"
            f"{_desc}  \n"
            f"_Pagina_: **{_page}**  \n"
            f"`{_cli}`"
        )

with st.expander("Scala di classificazione score (A/B/C/D)", expanded=False):
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.markdown("**A — Azione immediata**  \n" + score_badge(80) + "  \nscore ≥ 75", unsafe_allow_html=True)
    _c2.markdown("**B — Watchlist**  \n" + score_badge(65) + "  \nscore 60–74", unsafe_allow_html=True)
    _c3.markdown("**C — Neutrale**  \n" + score_badge(50) + "  \nscore 45–59", unsafe_allow_html=True)
    _c4.markdown("**D — Skip**  \n" + score_badge(30) + "  \nscore < 45", unsafe_allow_html=True)
