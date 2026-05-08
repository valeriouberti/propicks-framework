"""Threshold Calibration (Fase A.2 SIGNAL_ROADMAP) — UI per propicks-calibrate.

Threshold sweep + Probabilistic Sharpe Ratio (PSR, Bailey-Lopez 2012) +
Deflated Sharpe Ratio (DSR, Bailey-Lopez 2014) + Combinatorial Purged CV
(Lopez de Prado 2018). Vedi docs/THRESHOLD_CALIBRATION.md.
"""
# ruff: noqa: E402

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import invariants_note, page_header

st.set_page_config(page_title="Threshold Calibration · Propicks", layout="wide")
page_header(
    "Threshold Calibration (Fase A.2)",
    "Sweep su range threshold + DSR multi-trial + recommendation rule-based. "
    "Mirror di `propicks-calibrate`.",
)
invariants_note()

st.info(
    "💡 **Cosa fa**: per ogni threshold testa il backtest, calcola **PSR** "
    "(P(true Sharpe > 0)) e **DSR** (deflated by N trials testati). Output "
    "tabella + raccomandazione threshold ottimo. **Non modifica config** — "
    "informativo only.",
    icon="ℹ️",
)

# ─── Compatibility banner ──────────────────────────────────────────────────
st.warning(
    "⚠️ **Strategia supportata: SOLO Momentum.** La calibration usa "
    "`_build_momentum_scoring_fn()` + `strategy_tag='momentum'` hardcoded. "
    "Contrarian / ETF Rotation / Thematic non sono calibrabili qui — il loro "
    "scoring engine ha logica diversa (oversold + quality, RS-vs-benchmark, "
    "RS-vs-parent) e richiede framework dedicato (TODO se gate "
    "journal-evidence passa).",
    icon="⚠️",
)

with st.expander("📖 Come funziona — in 5 righe", expanded=False):
    st.markdown(
        """
**Idea**: testi N threshold diversi (es. 60, 65, 70, 75, 80) sullo stesso
universe e periodo. Ogni threshold dà un Sharpe. Senza correzioni, scegli
il Sharpe più alto = **fallacia di multiple testing**: con 5 threshold
testati, c'è ~25% di probabilità che il "migliore" sia rumore.

1. Per ogni threshold: backtest portfolio → trade list → Sharpe per-trade.
2. **PSR** (Bailey-Lopez 2012, Probabilistic Sharpe Ratio) = P(Sharpe vero > 0)
   data la distribuzione realizzata. PSR > 0.95 = "95% confidence che la
   strategia ha edge reale, non solo lucky draw".
3. **DSR** (Bailey-Lopez 2014, Deflated Sharpe Ratio) = PSR **deflato per
   il numero di trial testati**. Penalizza esplicitamente il multiple
   testing. DSR > 0.95 = "edge robusto anche dopo aver provato N threshold".
4. **CPCV** (Lopez de Prado 2018, Combinatorial Purged Cross-Validation) =
   genera N!/(k!(N-k)!) test path (purged + embargoed) per misurare
   varianza true vs path-dependent. Più rigoroso ma 10x più lento.
5. **Recommendation rule**: tier 1 = primo threshold con DSR ≥ target
   (default 0.95) E n_trades ≥ min_trades. Tier 2 = relax DSR ≥ 0.90.

**Quando usarla**:
- Stai per deployare un cambio di threshold (es. da 60 a 70) e vuoi
  evidenza statistica che NON è overfitting.
- Vuoi giustificare a te stesso (o a un investor) la scelta del threshold
  con un numero, non con "feeling".

**Quando NON usarla**:
- Non hai ancora validato la formula sul page *Backtest* — calibrare prima
  di sapere se la formula funziona è cargo cult.
- Universe < 10 ticker o periodo < 3y → stat signal troppo debole.
"""
    )

with st.expander("🎛️ Parametri — cosa fanno e come sceglierli", expanded=False):
    st.markdown(
        """
**Universe & periodo**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Universe** | Lista ticker testati. Se vuoto + Discover SP500 attivo → top N | 20-50 nomi liquidi. Sotto 10 = signal debole, sopra 100 = lentezza |
| **Discover SP500** | Carica top N S&P 500 da Wikipedia | ON per universe automatica. Combinare con membership filter |
| **Top N** | Quanti ticker se Discover | 30 default. 100 per stat signal robusto |
| **Periodo** | Storia daily | 5y default. 10y per CPCV (più paths) |
| **Capitale iniziale** | Budget simulazione | 10k default. Non cambia metriche % |

**Threshold sweep**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Threshold spec** | Range `start:end:step` (es. `60:80:5`) o lista (`60,65,70`) | Default `60:80:5` = 5 trial. Range stretto = recommendation più affidabile (meno multiple-testing penalty su DSR). Range largo (`50:90:5` = 9 trial) = DSR scende meccanicamente |

**CPCV (opzionale)**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Combinatorial Purged CV** | Genera multipli test path purged + embargoed | ON per validazione rigorosa pre-production. OFF per smoke test rapido |
| **CPCV groups** | N gruppi temporali di split | 6 default. 4-8 range tipico |
| **CPCV test groups** | Quanti gruppi in test set per path | 2 default. Più alto = più paths ma stat per path più deboli |
| **CPCV embargo days** | Giorni di gap fra train/test (anti-leakage) | 5 default. Aumenta se ATR/vol features hanno lookback 30+ giorni |

**Recommendation tuning**:

| Parametro | Cosa fa | Quando cambiarlo |
|-----------|---------|------------------|
| **Target DSR** | Soglia tier-1 raccomandazione | 0.95 default = high confidence. 0.90 = relax per universi piccoli. Sotto 0.85 = non production-ready |
| **Min trades per recommendation** | Floor n_trades per validità | 30 default. Sotto 20 = too noisy. Sopra 50 per universi grandi |

**Survivorship**:

`Membership filter sp500` ON usa point-in-time membership (vedi
`SURVIVORSHIP_BIAS_ANALYSIS.md`): include ticker delistati storicamente.
Senza, calibri solo sui sopravvissuti = bias positivo strutturale ~1-2%
Sharpe inflation. Richiede import history (`scripts/import_sp500_history.py`).

**Workflow tipico**:
1. Universe `Discover SP500 + top 30`, periodo `5y`, threshold `60:80:5`.
2. CPCV OFF per smoke test, target DSR 0.95.
3. Se top threshold ha DSR ≥ 0.95 → **deploy**. Documenta nel journal.
4. Se nessun threshold passa → range threshold sbagliato O strategia non
   robusta. Re-run con range stretto (es. `65:75:2`) prima di abbandonare.
5. Pre-production: CPCV ON con groups=6 + test_groups=2. Lento (~10min)
   ma necessario per giustificare deploy.
"""
    )


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("calibrate_form", border=True):
    col1, col2 = st.columns([3, 2])
    tickers_raw = col1.text_input(
        "Universe (tickers separati da spazio/virgola)",
        placeholder="AAPL MSFT NVDA GOOGL META AMZN",
        help="Lascia vuoto + spunta 'Discover SP500' per universe automatico",
    )
    period = col2.selectbox(
        "Periodo", options=["1y", "2y", "3y", "5y", "10y", "max"], index=3,
    )

    col3, col4, col5 = st.columns(3)
    discover_sp500 = col3.checkbox("Discover SP500 (top N)", value=False)
    top_n = col4.number_input("Top N (se discover)", min_value=10, max_value=500, value=30, step=5)
    initial_capital = col5.number_input(
        "Capitale iniziale", min_value=1000.0, value=10_000.0, step=1000.0,
    )

    thresholds_spec = st.text_input(
        "Threshold spec (range `start:end:step` o lista `60,65,70`)",
        value="60:80:5",
        help="Default 60-80 step 5. Range stretto = recommendation più affidabile",
    )

    col6, col7 = st.columns(2)
    use_cpcv = col6.checkbox(
        "🧪 Combinatorial Purged CV (Lopez de Prado)",
        value=False,
        help="Più rigoroso ma ~10x più lento. Genera comb(N,k) test path.",
    )
    use_membership = col7.checkbox(
        "🛡️ Membership filter sp500 (Fase A.1)",
        value=True,
        help="Survivorship-correct universe via index_membership_history",
    )

    if use_cpcv:
        col8, col9, col10 = st.columns(3)
        cpcv_groups = col8.number_input("CPCV groups", min_value=3, max_value=10, value=6)
        cpcv_test_groups = col9.number_input("CPCV test groups", min_value=1, max_value=5, value=2)
        cpcv_embargo = col10.number_input("CPCV embargo days", min_value=0, max_value=30, value=5)
    else:
        cpcv_groups, cpcv_test_groups, cpcv_embargo = 6, 2, 5

    col11, col12 = st.columns(2)
    target_dsr = col11.number_input(
        "Target DSR (recommendation tier 1)",
        min_value=0.5, max_value=0.99, value=0.95, step=0.01,
    )
    min_trades = col12.number_input(
        "Min trades per recommendation", min_value=10, max_value=500, value=30, step=10,
    )

    submitted = st.form_submit_button("▶️ Esegui calibration", type="primary")


if not submitted:
    st.caption(
        "_Premi 'Esegui calibration' per avviare. Sweep singolo (no CPCV) "
        "≈ 30-60s su 5-10 ticker. Con CPCV ≈ 5-10 min._"
    )
    st.stop()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
import pandas as pd
import yfinance as yf

from propicks.backtest.calibration import calibrate_threshold, format_calibration_report
from propicks.backtest.portfolio_engine import BacktestConfig
from propicks.cli.calibrate import _build_momentum_scoring_fn, _parse_thresholds


# Resolve tickers
tickers: list[str] = []
if discover_sp500:
    from propicks.market.index_constituents import get_sp500_universe
    tickers = get_sp500_universe()[: int(top_n)]
    st.caption(f"📥 Discover SP500: top {top_n} ticker")
else:
    tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]

if not tickers:
    st.error("Specifica tickers o spunta 'Discover SP500'.")
    st.stop()

# Parse thresholds
try:
    thresholds = _parse_thresholds(thresholds_spec)
except ValueError as exc:
    st.error(f"Threshold spec invalida: {exc}")
    st.stop()

st.write(f"**Thresholds**: {thresholds} ({len(thresholds)} valori)")


# Membership provider
provider = None
if use_membership:
    from propicks.io.index_membership import (
        build_universe_provider, count_membership_rows,
    )
    n_rows = count_membership_rows("sp500")
    if n_rows == 0:
        st.error(
            "Membership history non importata. "
            "Esegui: `python scripts/import_sp500_history.py`"
        )
        st.stop()
    provider = build_universe_provider("sp500")
    st.caption(f"🛡️ Membership filter sp500 attivo ({n_rows:,} row)")


# Fetch yfinance diretto (cache framework copre solo 1y)
with st.status(f"Fetching {len(tickers)} ticker ({period})…", expanded=True) as status:
    universe: dict = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=period, auto_adjust=False)
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            if len(df) >= 200:
                universe[t.upper()] = df
                st.write(f"✓ {t}: {len(df)} bars")
            else:
                st.write(f"✗ {t}: insufficient ({len(df)} bars)")
        except Exception as exc:
            st.write(f"✗ {t}: {exc}")
    if not universe:
        status.update(label="❌ Universe vuoto", state="error")
        st.stop()
    status.update(label=f"Fetch completato: {len(universe)} ticker", state="complete")


scoring_fn = _build_momentum_scoring_fn()
base_config = BacktestConfig(
    initial_capital=initial_capital,
    score_threshold=thresholds[0],  # placeholder; sweep override
    use_earnings_gate=False,
    strategy_tag="momentum",
)

progress_bar = st.progress(0, text="Starting calibration…")


def _cb(curr, total, thr):
    progress_bar.progress(curr / total, text=f"[{curr}/{total}] threshold={thr:.1f}")


with st.spinner("Running threshold sweep…"):
    result = calibrate_threshold(
        universe=universe,
        scoring_fn=scoring_fn,
        thresholds=thresholds,
        base_config=base_config,
        universe_provider=provider,
        use_cpcv=use_cpcv,
        cpcv_n_groups=int(cpcv_groups),
        cpcv_n_test_groups=int(cpcv_test_groups),
        cpcv_embargo_days=int(cpcv_embargo),
        min_trades=int(min_trades),
        target_dsr=target_dsr,
        progress_cb=_cb,
    )

progress_bar.progress(1.0, text="Done")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📊 Risultati")

# Metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Universe", result.universe_size)
c2.metric("Thresholds tested", result.n_thresholds_tested)
c3.metric("CPCV", "ON" if result.cpcv_enabled else "OFF")
c4.metric("Var(SR) cross", f"{result.var_sr_across_thresholds:.4f}")

# Tabella
import pandas as pd
rows = []
for r in result.results:
    is_recommended = r.threshold == result.recommended_threshold
    rows.append({
        "★": "★" if is_recommended else "",
        "Threshold": r.threshold,
        "N trades": r.n_trades,
        "Sharpe ann": r.sharpe_annualized,
        "Sharpe/trade": r.sharpe_per_trade,
        "Win %": f"{r.win_rate * 100:.1f}",
        "Tot ret %": r.total_return_pct,
        "Max DD %": r.max_drawdown_pct,
        "PSR": r.psr,
        "DSR": r.dsr,
    })
df = pd.DataFrame(rows)
st.dataframe(df, width="stretch", hide_index=True)


# Recommendation
if result.recommended_threshold is not None:
    st.success(
        f"⭐ **Recommended threshold: {result.recommended_threshold:.1f}** — "
        f"{result.recommendation_reason}"
    )
else:
    st.warning(f"⚠ Nessuna recommendation: {result.recommendation_reason}")

# Note
st.divider()
st.caption(
    "**Legenda**: PSR > 0.95 = 95% confidence Sharpe vero > 0. "
    "DSR > 0.95 = robust a multiple testing post-correzione. "
    "DSR sempre ≤ PSR. Vedi docs/THRESHOLD_CALIBRATION.md per metodologia."
)
