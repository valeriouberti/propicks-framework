"""Walk-forward backtest single-stock — UI parallela a `propicks-backtest`.

Form per ticker(s) + parametri → metrics summary + trade table + equity curve
+ aggregate (se più ticker). Streamlit cache per evitare ri-run su modifiche
solo cosmetiche (toggle expander, ecc.).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Bridge st.secrets → env vars (precede ogni import propicks.*).
from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.backtest import backtest_ticker, compute_metrics
from propicks.backtest.metrics import aggregate_metrics
from propicks.dashboard._shared import (
    fmt_pct,
    invariants_note,
    page_header,
)
from propicks.market.yfinance_client import DataUnavailable

st.set_page_config(page_title="Backtest · Propicks", layout="wide")
page_header(
    "Backtest walk-forward (single-ticker)",
    "Mirror di `propicks-backtest`. Rigira la stessa formula composite "
    "su storia point-in-time. **No slippage / no commissioni / no survivorship**: "
    "scopo è validare il *segno* della strategia. Per portfolio-level backtest "
    "con TC/slippage/OOS/Monte Carlo usa la page dedicata *Backtest Portfolio*.",
)
invariants_note()

# ─── Compatibility banner ──────────────────────────────────────────────────
st.warning(
    "⚠️ **Strategia supportata: SOLO Momentum.** Il backtest engine usa "
    "`domain.scoring.analyze_ticker` (formula 6 sub-score momentum). "
    "Contrarian / ETF Rotation / Thematic NON sono supportate qui — il loro "
    "scoring engine ha logica diversa (oversold + quality, RS-vs-benchmark, "
    "RS-vs-parent) non integrata. Per validare contrarian usa CLI "
    "`propicks-contra --discover-*` su periodo lungo + analisi journal manuale.",
    icon="⚠️",
)

# ─── Cosa fa, in 3 righe ──────────────────────────────────────────────────
with st.expander("📖 Come funziona (in 3 righe)", expanded=False):
    st.markdown(
        """
**Come funziona**:
1. Scarica storia daily del ticker e calcola lo score momentum per ogni barra.
2. Quando lo score supera la **threshold**, simula un'entry long con stop e
   target ancorati all'ATR (volatilità) e un time-stop.
3. Chiude su stop / target / time-stop, registra ogni trade e calcola
   metriche aggregate (win rate, profit factor, Sharpe, drawdown).

**Quando usarla**:
- Vuoi vedere se un singolo ticker (o pochi) avrebbe dato P&L positivo
  applicando la formula momentum su 1-10 anni di storia.
- Vuoi calibrare una threshold (60 vs 70 vs 75) e vedere cosa cambia.
- **Non è un portfolio test**: niente cross-ticker, niente capital constraint,
  niente correlation. Per quello → page *Backtest Portfolio*.

**Workflow tipico**:
1. Inserisci 3-5 ticker liquidi diversi (no penny stock).
2. Periodo `5y` (default), threshold `60` (default).
3. Premi *Esegui backtest*, leggi win rate + profit factor + drawdown.
4. Se profit factor < 1.3 o drawdown > 30%: setup non utilizzabile sui
   ticker scelti. Cambia threshold o ticker, NON tunare ATR.
"""
    )

with st.expander("🎛️ Parametri — cosa fanno e come sceglierli", expanded=False):
    st.markdown(
        """
| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Tickers** | Ticker da testare (1 alla volta o batch). Ogni ticker è simulato isolato | Lista 3-5 nomi liquidi. Per universi ampi (S&P 500) usa la CLI |
| **Periodo** | Storia daily da fetchare (1y → max). Più lungo = stat più stabili | `5y` default. `10y` se ticker ha storia. `1y` solo per smoke test |
| **Threshold composite** | Score minimo (0-100) per aprire un'entry | Default 60. Sotto = più trade ma più noise. Sopra 75 = pochi trade ma high-conviction |
| **Stop in ATR** | Distanza stop dall'entry in multipli di ATR(14) | 2.0 default. Più alto = stop più largo (meno stop-out, più loss per trade). 1.5 = aggressivo |
| **Target in ATR** | Distanza target in multipli di ATR. R:R = target/stop | 4.0 default → R:R 2:1. Per momentum 2:1 è il floor. Sotto 1.5 = setup rotto |
| **Time stop** | Numero di bar (giorni) max in trade | 30 default. Trade momentum reggono 1-2 mesi. Sotto 15 = chiude troppo presto, sopra 60 = capital tied up |

**Regola pratica**: parti dai default. Cambia UNA cosa alla volta. Se cambi
threshold + stop + target insieme stai overfittando. Se threshold da 60 a
70 raddoppia il profit factor con n_trades dimezzato → la formula funziona,
60 era troppo basso. Se invece n_trades crolla a 2-3 → non c'è più segnale
statistico.
"""
    )


# ---------------------------------------------------------------------------
# Cached runner: chiave = parametri esatti
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def _run_backtest(
    tickers: tuple[str, ...],
    period: str,
    threshold: float,
    stop_atr: float,
    target_atr: float,
    time_stop: int,
):
    """Esegue backtest_ticker per ogni ticker, ritorna dict + lista errori."""
    out: dict = {}
    errors: list[str] = []
    for t in tickers:
        try:
            out[t] = backtest_ticker(
                t,
                period=period,
                threshold=threshold,
                stop_atr_mult=stop_atr,
                target_atr_mult=target_atr,
                time_stop_bars=time_stop,
            )
        except DataUnavailable as err:
            errors.append(f"{t}: {err}")
    return out, errors


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("backtest_form", border=True):
    cols = st.columns([3, 1, 1])
    tickers_raw = cols[0].text_input(
        "Tickers (separati da spazio o virgola)",
        placeholder="AAPL MSFT NVDA",
        key="bt_tickers",
    )
    period = cols[1].selectbox(
        "Periodo",
        ("1y", "2y", "3y", "5y", "10y", "max"),
        index=3,
        key="bt_period",
    )
    threshold = cols[2].number_input(
        "Threshold composite",
        min_value=0.0,
        max_value=100.0,
        value=60.0,
        step=5.0,
        key="bt_threshold",
    )
    cols2 = st.columns(3)
    stop_atr = cols2[0].number_input(
        "Stop in multipli ATR",
        min_value=0.5,
        max_value=10.0,
        value=2.0,
        step=0.1,
        key="bt_stop_atr",
    )
    target_atr = cols2[1].number_input(
        "Target in multipli ATR",
        min_value=0.5,
        max_value=20.0,
        value=4.0,
        step=0.1,
        key="bt_target_atr",
    )
    time_stop = cols2[2].number_input(
        "Time stop (bar)",
        min_value=5,
        max_value=120,
        value=30,
        step=1,
        key="bt_time_stop",
    )
    submitted = st.form_submit_button("Esegui backtest", type="primary")

st.caption(
    "Defaults: stop -2x ATR, target +4x ATR (R:R 2:1 teorico), threshold 60. "
    "Periodo 5y consigliato (richiede ~150+ bar di warm-up per stabilità EMA50)."
)

if not submitted:
    st.info("Inserisci uno o più ticker e premi **Esegui backtest**.")
    st.stop()

# ---------------------------------------------------------------------------
# Parse + run
# ---------------------------------------------------------------------------
tickers_list = [
    t.strip().upper()
    for t in tickers_raw.replace(",", " ").split()
    if t.strip()
]
if not tickers_list:
    st.warning("Inserisci almeno un ticker.")
    st.stop()

with st.spinner(f"Backtest in corso su {len(tickers_list)} ticker…"):
    results, errors = _run_backtest(
        tuple(tickers_list),
        period,
        float(threshold),
        float(stop_atr),
        float(target_atr),
        int(time_stop),
    )

for e in errors:
    st.error(e)

if not results:
    st.error("Nessun ticker ha prodotto risultati.")
    st.stop()


# ---------------------------------------------------------------------------
# Per-ticker rendering
# ---------------------------------------------------------------------------
def _fmt_num(x, digits: int = 2) -> str:
    return f"{x:.{digits}f}" if x is not None else "—"


def _render_ticker(ticker: str, result) -> None:
    m = compute_metrics(result)
    st.subheader(f"📊 {ticker}")
    st.caption(
        f"Periodo: **{m['period_start']} → {m['period_end']}** · "
        f"Segnali generati: **{m['signals_generated']}** · "
        f"Trade eseguiti: **{m['n_trades']}**"
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Win rate", fmt_pct(m["win_rate"]) if m["win_rate"] is not None else "—")
    k2.metric("Profit factor", _fmt_num(m["profit_factor"]))
    k3.metric("Expectancy / trade", fmt_pct(m["expectancy_pct"]))
    k4.metric("CAGR", fmt_pct(m["cagr_pct"]))

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Max drawdown", fmt_pct(m["max_drawdown_pct"]))
    k6.metric("Sharpe", _fmt_num(m["sharpe"]))
    k7.metric("Sortino", _fmt_num(m["sortino"]))
    k8.metric("Avg bars held", _fmt_num(m["avg_bars_held"], 1))

    k9, k10, k11 = st.columns(3)
    k9.metric("Avg win", fmt_pct(m["avg_win_pct"]))
    k10.metric("Avg loss", fmt_pct(m["avg_loss_pct"]))
    k11.metric(
        "Equity initial → final",
        f"{_fmt_num(m['initial_equity'])} → {_fmt_num(m['final_equity'])}",
    )

    if m["exit_reasons"]:
        st.caption(
            "**Exit reasons:** "
            + " · ".join(f"`{k}` = {v}" for k, v in m["exit_reasons"].items())
        )

    # Equity curve
    if not result.equity_curve.empty:
        eq = result.equity_curve.copy()
        normalized = eq / eq.iloc[0]
        chart_df = pd.DataFrame({"Equity (normalized, init=1.0)": normalized})
        st.line_chart(chart_df, height=220)

    # Trade table
    if result.trades:
        with st.expander(f"Trade-by-trade ({len(result.trades)})", expanded=False):
            rows = []
            for t in result.trades:
                rows.append({
                    "Entry date": t.entry_date.isoformat(),
                    "Entry $": f"{t.entry_price:.2f}",
                    "Stop": f"{t.stop_price:.2f}",
                    "Target": f"{t.target_price:.2f}",
                    "Score": f"{t.entry_score:.0f}",
                    "Exit date": t.exit_date.isoformat() if t.exit_date else "—",
                    "Exit $": f"{t.exit_price:.2f}" if t.exit_price else "—",
                    "Why": t.exit_reason or "—",
                    "P&L%": f"{t.pnl_pct * 100:+.2f}%" if t.pnl_pct is not None else "—",
                    "Bars": str(t.bars_held) if t.bars_held is not None else "—",
                })
            st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.caption("Nessun trade eseguito (threshold troppo alto o storia troppo flat).")


for ticker, result in results.items():
    st.divider()
    _render_ticker(ticker, result)


# ---------------------------------------------------------------------------
# Aggregate (multi-ticker)
# ---------------------------------------------------------------------------
if len(results) > 1:
    st.divider()
    st.subheader("📈 Aggregate (pool di tutti i trade)")
    st.caption(
        "Pool dei trade su tutti i ticker — utile per validare che la formula "
        "regga in media. **Non è un portfolio simulato**: niente correlazioni, "
        "niente concentration budget."
    )
    agg = aggregate_metrics(results)
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("N tickers", agg["n_tickers"])
    a2.metric("N trades", agg["n_trades"])
    a3.metric("Win rate", fmt_pct(agg["win_rate"]) if agg["win_rate"] is not None else "—")
    a4.metric("Profit factor", _fmt_num(agg["profit_factor"]))

    a5, a6, a7, a8 = st.columns(4)
    a5.metric("Avg win", fmt_pct(agg["avg_win_pct"]))
    a6.metric("Avg loss", fmt_pct(agg["avg_loss_pct"]))
    a7.metric("Expectancy / trade", fmt_pct(agg["expectancy_pct"]))
    a8.metric("Avg bars held", _fmt_num(agg["avg_bars_held"], 1))

    a9, a10 = st.columns(2)
    a9.metric("Avg equity (final, normalized)", _fmt_num(agg["avg_equity_final"]))
    a10.metric("Avg equity max DD", fmt_pct(agg["avg_equity_max_dd"]))

    if agg["exit_reasons"]:
        st.caption(
            "**Exit reasons (pool):** "
            + " · ".join(f"`{k}` = {v}" for k, v in agg["exit_reasons"].items())
        )

with st.expander("Limiti noti del backtest", expanded=False):
    st.markdown(
        """
- **No slippage, no commissioni** → fill esatto sui livelli teorici (ottimista).
- **No survivorship bias correction**: ticker delisted/merged non sono nel set;
  i vivi sono visti come vivi anche durante drawdown storici.
- **Earnings gap non filtrati**: stop gappato post-earnings viene compilato a stop
  level invece che al gap-down reale → sottostima della loss.
- **Sizing**: full-cash ogni trade, 1 posizione per ticker. Niente correlation
  budget cross-ticker.
- **Regime gate non integrato** (TODO v1.6): il backtest entra anche durante
  regime weekly BEAR/STRONG_BEAR, mentre la strategia live skippa.

Scopo dichiarato: validare che il *segno* dei pesi e dei sub-score sia corretto
(la strategia genera expectancy positiva su un universo di ticker liquidi),
non produrre un'equity curve da prendere literally come previsione futura.
"""
    )
