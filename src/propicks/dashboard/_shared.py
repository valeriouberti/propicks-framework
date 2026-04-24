"""Helper condivisi tra pagine streamlit.

- Cache wrappers sulle funzioni puramente read-only (scan, rank) con TTL breve
  per evitare download yfinance ripetuti durante una singola sessione.
- Formatter riutilizzabili (pct, currency, regime badge).
- Lookup stato portfolio/journal.

Le funzioni *mutanti* (add_position, add_trade, close_trade) NON vengono
cachate — Streamlit le chiama dentro on_click handler e lo stato viene ricaricato
dallo store dopo la mutazione.
"""

from __future__ import annotations

import streamlit as st

from propicks.config import (
    CONTRA_MAX_AGGREGATE_EXPOSURE_PCT,
    CONTRA_MAX_LOSS_PER_TRADE_PCT,
    CONTRA_MAX_POSITION_SIZE_PCT,
    CONTRA_MAX_POSITIONS,
    MAX_LOSS_WEEKLY_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
)


# ---------------------------------------------------------------------------
# Cached readers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cached_analyze(ticker: str, strategy: str | None) -> dict | None:
    """Scan tecnico singolo ticker. TTL 5min: i prezzi intraday si muovono."""
    from propicks.domain.scoring import analyze_ticker
    return analyze_ticker(ticker, strategy=strategy)


@st.cache_data(ttl=300, show_spinner=False)
def cached_rank(region: str) -> list[dict]:
    """Ranking universo ETF. TTL 5min."""
    from propicks.domain.etf_scoring import rank_universe
    return rank_universe(region=region)  # type: ignore[arg-type]


@st.cache_data(ttl=60, show_spinner=False)
def cached_current_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Prezzi spot per mark-to-market. TTL 1min. Tuple in input perché list non è hashable."""
    from propicks.market.yfinance_client import get_current_prices
    return get_current_prices(list(tickers))


@st.cache_data(ttl=3600, show_spinner=False)
def cached_ticker_sectors(tickers: tuple[str, ...]) -> dict[str, str | None]:
    """Sector GICS-like per ticker via yf.Ticker(t).info. TTL 1h: il sector cambia di rado."""
    from propicks.market.yfinance_client import get_ticker_sector
    return {t: get_ticker_sector(t) for t in tickers}


@st.cache_data(ttl=3600, show_spinner=False)
def cached_ticker_betas(tickers: tuple[str, ...]) -> dict[str, float | None]:
    """Beta vs SPX via yf.Ticker(t).info['beta']. TTL 1h."""
    from propicks.market.yfinance_client import get_ticker_beta
    return {t: get_ticker_beta(t) for t in tickers}


@st.cache_data(ttl=600, show_spinner=False)
def cached_returns(tickers: tuple[str, ...], period: str = "6mo"):
    """Daily returns DataFrame per il calcolo correlazioni. TTL 10min."""
    from propicks.market.yfinance_client import download_returns
    return download_returns(list(tickers), period=period)


@st.cache_data(ttl=300, show_spinner=False)
def cached_current_atr(ticker: str) -> float | None:
    """ATR(14) corrente del ticker. TTL 5min. None se dati non disponibili."""
    from propicks.config import ATR_PERIOD
    from propicks.domain.indicators import compute_atr
    from propicks.market.yfinance_client import DataUnavailable, download_history
    try:
        hist = download_history(ticker)
    except DataUnavailable:
        return None
    atr = compute_atr(hist["High"], hist["Low"], hist["Close"], ATR_PERIOD)
    val = float(atr.iloc[-1])
    return val if val > 0 else None


def load_portfolio() -> dict:
    from propicks.io.portfolio_store import load_portfolio as _load
    return _load()


def load_journal() -> list[dict]:
    from propicks.io.journal_store import load_journal as _load
    return _load()


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
def fmt_pct(val: float | None, *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"{val * 100:.{decimals}f}%"


def fmt_eur(val: float | None, *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"€ {val:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_usd(val: float | None, *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"$ {val:,.{decimals}f}"


def pnl_arrow(val: float | None, *, flat_threshold: float = 0.002) -> str:
    """Pallino colorato per P&L (positivo/negativo/flat).

    ``val`` è la frazione (es. 0.023 per +2.3%). Sotto il threshold in valore
    assoluto il trade è considerato flat (pallino bianco).
    """
    if val is None:
        return "—"
    if val > flat_threshold:
        return "🟢"
    if val < -flat_threshold:
        return "🔴"
    return "⚪"


# ---------------------------------------------------------------------------
# UI primitives
# ---------------------------------------------------------------------------
REGIME_COLORS = {
    5: "#16a34a",  # STRONG_BULL — green
    4: "#65a30d",  # BULL — lime
    3: "#ca8a04",  # NEUTRAL — amber
    2: "#ea580c",  # BEAR — orange
    1: "#dc2626",  # STRONG_BEAR — red
}


def regime_badge(regime: dict | None) -> str:
    """Ritorna HTML per un badge colorato del regime corrente."""
    if regime is None:
        return (
            '<span style="background:#64748b;color:white;padding:4px 10px;'
            'border-radius:6px;font-weight:600;">REGIME N/D</span>'
        )
    code = regime.get("regime_code", 3)
    name = regime.get("regime", "NEUTRAL")
    color = REGIME_COLORS.get(code, "#64748b")
    return (
        f'<span style="background:{color};color:white;padding:4px 10px;'
        f'border-radius:6px;font-weight:600;">{code} — {name}</span>'
    )


def score_badge(score: float) -> str:
    """Badge colorato per score composite."""
    if score >= 75:
        color = "#16a34a"
        label = "A"
    elif score >= 60:
        color = "#65a30d"
        label = "B"
    elif score >= 45:
        color = "#ca8a04"
        label = "C"
    else:
        color = "#dc2626"
        label = "D"
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-weight:600;">{label} · {score:.1f}</span>'
    )


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.divider()


def kpi_row(items: list[tuple[str, str, str | None]]) -> None:
    """Render a row of metrics. Items: (label, value, delta_or_None)."""
    cols = st.columns(len(items))
    for col, (label, value, delta) in zip(cols, items, strict=True):
        col.metric(label, value, delta)


def _dot(level: str) -> str:
    """Pallino colorato per status inline. ``ok``→verde, ``warn``→giallo, ``bad``→rosso."""
    return {"ok": "🟢", "warn": "🟡", "bad": "🔴"}.get(level, "⚪")


def _status_positions(n: int) -> str:
    if n >= MAX_POSITIONS:
        return "bad"
    if n >= int(MAX_POSITIONS * 0.8):
        return "warn"
    return "ok"


def _status_cash(cash_pct: float) -> str:
    if cash_pct < MIN_CASH_RESERVE_PCT:
        return "bad"
    if cash_pct < MIN_CASH_RESERVE_PCT + 0.10:
        return "warn"
    return "ok"


def _status_weekly_risk(risk_pct: float) -> str:
    if risk_pct >= MAX_LOSS_WEEKLY_PCT:
        return "bad"
    if risk_pct >= MAX_LOSS_WEEKLY_PCT * 0.6:
        return "warn"
    return "ok"


def invariants_note(strategy_bucket: str = "momentum") -> None:
    """Sidebar con stato live + regole. I semafori leggono il portfolio corrente.

    Args:
        strategy_bucket: ``"momentum"`` (default, regole stock/ETF classiche) o
            ``"contrarian"`` (regole 8%/3pos/20% del bucket mean reversion).
            Ogni page della dashboard passa il bucket appropriato per evitare
            di mostrare regole fuorvianti a chi opera sulla strategia sbagliata.
    """
    from propicks.domain.sizing import (
        contrarian_aggregate_exposure,
        contrarian_position_count,
        portfolio_value,
    )
    from propicks.io.portfolio_store import get_initial_capital

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)
    ref_capital = get_initial_capital(portfolio)

    n_positions = len(positions)
    cash_pct = cash / total if total else 1.0
    # Rischio settimanale aggregato = Σ (entry-stop) × shares a cost-basis.
    # Stima conservativa: se tutti gli stop saltano insieme.
    risk_total = sum(
        (float(p["entry_price"]) - float(p["stop_loss"])) * float(p.get("shares") or 0)
        for p in positions.values()
        if p.get("stop_loss") is not None
    )
    risk_pct = risk_total / total if total else 0.0

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Stato corrente**")
    st.sidebar.markdown(
        f"{_dot(_status_positions(n_positions))} Posizioni · "
        f"**{n_positions}** / {MAX_POSITIONS}"
    )
    st.sidebar.markdown(
        f"{_dot(_status_cash(cash_pct))} Cash · "
        f"**{cash_pct * 100:.1f}%** (min {MIN_CASH_RESERVE_PCT * 100:.0f}%)"
    )
    st.sidebar.markdown(
        f"{_dot(_status_weekly_risk(risk_pct))} Rischio settimanale · "
        f"**{risk_pct * 100:.2f}%** (max {MAX_LOSS_WEEKLY_PCT * 100:.0f}%)"
    )

    # Stato bucket contrarian — visibile solo sulla page contrarian per
    # non rumorare le altre page con info non rilevanti.
    if strategy_bucket == "contrarian":
        contra_n = contrarian_position_count(portfolio)
        contra_pct = contrarian_aggregate_exposure(portfolio)
        contra_n_status = (
            "bad" if contra_n >= CONTRA_MAX_POSITIONS
            else "warn" if contra_n >= CONTRA_MAX_POSITIONS - 1
            else "ok"
        )
        contra_pct_status = (
            "bad" if contra_pct >= CONTRA_MAX_AGGREGATE_EXPOSURE_PCT
            else "warn" if contra_pct >= CONTRA_MAX_AGGREGATE_EXPOSURE_PCT * 0.75
            else "ok"
        )
        st.sidebar.markdown(
            f"{_dot(contra_n_status)} Contrarian pos · "
            f"**{contra_n}** / {CONTRA_MAX_POSITIONS}"
        )
        st.sidebar.markdown(
            f"{_dot(contra_pct_status)} Contrarian expo · "
            f"**{contra_pct * 100:.1f}%** / {CONTRA_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%"
        )

    st.sidebar.caption(f"Capitale rif.: € {ref_capital:,.0f}")

    st.sidebar.markdown("---")
    if strategy_bucket == "contrarian":
        st.sidebar.caption(
            "**Regole contrarian (bucket)**  \n"
            f"• Max posizioni bucket: {CONTRA_MAX_POSITIONS}  \n"
            f"• Max size/trade: {CONTRA_MAX_POSITION_SIZE_PCT * 100:.0f}%  \n"
            f"• Max expo aggregata: {CONTRA_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%  \n"
            f"• Max loss/trade: {CONTRA_MAX_LOSS_PER_TRADE_PCT * 100:.0f}% (stop −3×ATR)  \n"
            f"• Cap globale condiviso: {MAX_POSITIONS} pos totali  \n"
            "• Regime gate: skip STRONG_BULL/BEAR"
        )
    else:
        st.sidebar.caption(
            "**Regole**  \n"
            f"• Max posizioni: {MAX_POSITIONS}  \n"
            "• Max size: 15% stock / 20% ETF / 8% contrarian  \n"
            f"• Min cash: {MIN_CASH_RESERVE_PCT * 100:.0f}%  \n"
            f"• Max loss week: {MAX_LOSS_WEEKLY_PCT * 100:.0f}% → stop"
        )


# ---------------------------------------------------------------------------
# Indicator glossary — tooltip brevi + expander esteso
# ---------------------------------------------------------------------------
# Tooltip per `st.metric(..., help=...)`: una riga, linguaggio operativo.
INDICATOR_HELP_ETF: dict[str, str] = {
    "score_composite": (
        "Score 0-100 pesato: RS 40% + regime fit 30% + abs momentum 20% + "
        "trend 10%. Capped dal regime hard-gate (BEAR=50, STRONG_BEAR=0) "
        "per settori non favoriti."
    ),
    "classification": (
        "A OVERWEIGHT (score ≥75) · B HOLD (55-74) · "
        "C NEUTRAL (40-54) · D AVOID (<40)."
    ),
    "sector": "Settore GICS di appartenenza dell'ETF (mapping in config.py).",
    "perf_3m": "Performance assoluta 3 mesi (≈63 trading days).",
    "rs": (
        "Relative Strength vs benchmark — 40% del composite. "
        "close(ETF)/close(benchmark) normalizzato 26w + slope EMA(10w). "
        "Leader in accelerazione=100, leader stanco=55, lagger=10."
    ),
    "regime_fit": (
        "Fit del settore col regime weekly — 30% del composite. "
        "Favored nel regime corrente=100, in regime adiacente=60, "
        "non favored=20, regime ignoto=50."
    ),
    "abs_momentum": (
        "Momentum assoluto 3M — 20% del composite. "
        "+15%+=100, +10%=85, +5%=70, 0%=50, -2%=30, -5%+=10."
    ),
    "trend": (
        "Trend price vs EMA30 weekly + slope EMA 4w — 10% del composite. "
        "Price sopra EMA in salita=100."
    ),
    "rs_ratio": (
        "close(ETF)/close(benchmark) normalizzato al valore di 26 weeks fa. "
        "1.0=pari benchmark, >1=outperform, <1=underperform."
    ),
    "regime_cap": (
        "✓ = composite è stato ridotto dal regime hard-gate "
        "(STRONG_BEAR non-favored → 0, BEAR non-favored → cap 50)."
    ),
}

INDICATOR_HELP_STOCK: dict[str, str] = {
    "score": (
        "Score tecnico 0-100: trend 25% + momentum 20% + volume 15% + "
        "distance_high 15% + ma_cross 15% + volatility 10%."
    ),
    "class": (
        "A ≥75 · B 60-74 · C 45-59 · D <45. "
        "Minimo per entry: score ≥60 + regime ≥ NEUTRAL."
    ),
    "rsi": (
        "RSI 14d daily — momentum oscillator. "
        ">70 ipercomprato, <30 ipervenduto, 40-60 zona neutra."
    ),
    "atr_pct": (
        "ATR 14d in % del prezzo — volatilità. "
        "Usato per dimensionare stop loss (tipicamente 1.5-2× ATR)."
    ),
    "dist52wh": (
        "Distanza % dal massimo 52 settimane. "
        "0%=al max, setup pullback ottimali: -5% / -15%."
    ),
    "perf_1w": "Performance 5 trading days.",
    "perf_1m": "Performance 21 trading days.",
    "perf_3m": "Performance 63 trading days.",
    "regime": (
        "Classifier macro weekly (5-bucket) su ^GSPC: "
        "STRONG_BEAR · BEAR · NEUTRAL · BULL · STRONG_BULL. "
        "Gate per entry long: NEUTRAL+."
    ),
    # sub-score keys (come ritornati da analyze_ticker in `scores`)
    "trend": "Sub-score 25%: price vs EMA fast/slow — direzione del trend.",
    "momentum": "Sub-score 20%: RSI 14d mappato a 0-100 (50=neutro).",
    "volume": "Sub-score 15%: volume corrente vs media 20d — conferma istituzionale.",
    "distance_high": "Sub-score 15%: vicinanza al 52w high — uptrend maturo.",
    "volatility": "Sub-score 10%: ATR% normalizzato — premia volatilità tradable.",
    "ma_cross": "Sub-score 15%: EMA fast × EMA slow — golden / death cross recente.",
}

# Tooltip per il tab Risk & Esposizione + Trade management del Portfolio.
INDICATOR_HELP_PORTFOLIO: dict[str, str] = {
    # ---- Risk per posizione ----
    "risk_eur": (
        "Rischio in euro se lo stop viene colpito: (entry - stop) x shares. "
        "Assume fill esatto allo stop level (no slippage)."
    ),
    "risk_pct": (
        "Rischio della posizione come % del capitale totale (cash + invested). "
        "Regola interna: max 1-2% per trade su singola posizione."
    ),
    "risk_aggregato": (
        "Somma del rischio a stop di tutte le posizioni aperte. Misura la "
        "perdita massima teorica del portfolio se TUTTI gli stop venissero "
        "colpiti contemporaneamente."
    ),
    "weekly_limit": (
        "Limite settimanale hardcoded: 5% del capitale (MAX_LOSS_WEEKLY_PCT). "
        "Sopra → blocco trading e revisione setup."
    ),
    # ---- Concentrazione settoriale ----
    "sector_exposure": (
        "% del capitale investita per settore GICS. Mapping da Yahoo "
        "(Consumer Cyclical → consumer_discretionary, ecc.). Cash NON incluso."
    ),
    "sector_cap": (
        "Cap consigliato per settore: 30% del capitale. Le regole single-name "
        "cappano al 15% per posizione, ma 2 stock dello stesso settore al 15% "
        "ciascuno = 30% effettivo → questo lo rende visibile."
    ),
    # ---- Beta-weighted ----
    "gross_long": (
        "Esposizione lorda nominale = sum(weight_i). 0.65 = 65% del capitale "
        "investito long, 35% in cash. Non considera il beta."
    ),
    "beta_weighted": (
        "Esposizione pesata per beta = sum(weight_i x beta_i). Misura quanto "
        "il portfolio si muove in % per ogni 1% di SPX. 0.78 = portfolio si "
        "muove come il 78% di SPX (beta medio della parte investita > 1.0 "
        "se beta_weighted > gross_long)."
    ),
    "beta_known": (
        "Quante posizioni hanno un beta reale da Yahoo (5y monthly vs SPX). "
        "Per ETF / IPO recenti / esteri illiquidi viene usato beta=1.0 come "
        "fallback (vedi caption sotto)."
    ),
    # ---- Correlazioni ----
    "corr_pair": (
        "Pair con |correlazione daily returns| ≥ 0.7 su 6 mesi. Sopra soglia "
        "i due ticker sono effettivamente la stessa scommessa: rischio "
        "concentrato camuffato da diversificazione."
    ),
    # ---- Trade management ----
    "trailing_stop": (
        "Trailing ATR-based, ratchet-up only. Si attiva quando highest_price "
        "supera entry + 1R (1R = entry - initial_stop). Nuovo stop = "
        "highest - atr_mult x current_ATR. MAI scende, solo sale."
    ),
    "time_stop": (
        "Trade flat (|P&L| < flat_threshold) da N giorni → suggerisci chiusura. "
        "Rationale: il costo-opportunità di tenere capitale fermo è reale "
        "anche se il P&L mark-to-market è nullo."
    ),
    "atr_mult": (
        "Moltiplicatore ATR per il trailing stop. Default 2.0 = stop a "
        "highest - 2x ATR. Più alto = trailing più largo (meno stop-out "
        "rumorosi ma più profit lasciato sul tavolo)."
    ),
    "flat_threshold": (
        "Soglia |P&L%| sotto la quale il trade è considerato flat. "
        "Default 0.02 = 2%. Un trade a -1% da 40 giorni è flat; "
        "uno a +5% non lo è anche se vecchio."
    ),
    "highest_price": (
        "Massimo prezzo raggiunto post-entry. Aggiornato a ogni run di "
        "manage. Base di calcolo del trailing stop."
    ),
    "trail_toggle": (
        "Trailing è opt-in per posizione (default OFF). Il trader decide "
        "caso per caso quali setup meritano trailing (momentum) vs hard "
        "stop (mean-reversion)."
    ),
}


def render_indicator_legend(scope: str = "etf") -> None:
    """Expander collassato con spiegazione completa degli indicatori.

    ``scope``: ``"etf"`` per ETF Rotation, ``"stock"`` per Scanner,
    ``"portfolio"`` per Portfolio risk + trade management.
    """
    if scope == "etf":
        with st.expander("ℹ️ Legenda indicatori ETF", expanded=False):
            st.markdown(
                """
**Composite score (0-100)** — somma pesata di 4 sub-score:

| Pilastro | Peso | Cosa misura |
|----------|------|-------------|
| **RS** | 40% | Leadership settoriale vs benchmark (US/EU→^GSPC, WORLD→URTH) |
| **Regime fit** | 30% | Allineamento col regime macro weekly corrente |
| **Abs momentum** | 20% | Performance assoluta 3M (non relativa) |
| **Trend** | 10% | Price vs EMA30 weekly + slope |

**RS (Relative Strength)** — `close(ETF)/close(benchmark)` normalizzato sul
valore di 26 weeks fa (1.0 = performance identica). Combinato con la slope
della EMA(10w) della RS line per distinguere leader in accelerazione da
leader stanchi.

| Condizione | Score RS |
|-----------|----------|
| RS ≥1.05 & slope positiva (leader in accelerazione) | 100 |
| RS ≥1.02 & slope positiva | 85 |
| RS ≥1.0 & slope positiva | 70 |
| RS ≥1.0 & slope negativa (leader stanco) | 55 |
| RS <1.0 & slope positiva (lagger in recupero) | 45 |
| RS <1.0 & slope negativa (lagger distribuzione) | 10-20 |

**Regime fit** — lookup su `REGIME_FAVORED_SECTORS`:
favorito nel regime corrente=100, favorito in regime adiacente
(zona di transizione 5↔4, 2↔1)=60, non favorito=20.

**Regime hard-gate** (oltre al peso 30%):
- **STRONG_BEAR + non-favored** → composite forzato a **0** (no long ciclicali in crisi)
- **BEAR + non-favored** → composite capped a **50** (no overweight cicliche)
- **NEUTRAL+** → nessun cap, ranking libero

La colonna **Cap?** in tabella indica ✓ quando il cap è stato applicato.

**Classification** (da composite):

| Class | Score | Azione |
|-------|-------|--------|
| **A** OVERWEIGHT | ≥75 | Top pick per il regime — overweight settore |
| **B** HOLD | 55-74 | Mantieni se già in portfolio, no nuove entry aggressive |
| **C** NEUTRAL | 40-54 | No overweight, skip per nuove allocazioni |
| **D** AVOID | <40 | Evita long — sottoperformance attesa |

**RS ratio** = `close(ETF) / close(benchmark)` normalizzato. Serve a leggere
direttamente il numero: 1.012 = outperform del 1.2% sul benchmark negli ultimi
26 weeks.

**Perf 3m** = performance assoluta a 63 trading days. Entra nel sub-score
**Abs momentum** (20%) ma è mostrata come colonna separata perché è la metrica
più leggibile per il trader.

**Benchmark per region**:
- US, EU → `^GSPC` (S&P 500) · coerente con Select Sector Index
- WORLD → `URTH` (iShares MSCI World) · stesso perimetro dei Xtrackers XDW*
"""
            )
    elif scope == "stock":
        with st.expander("ℹ️ Legenda indicatori stock", expanded=False):
            st.markdown(
                """
**Score tecnico (0-100)** — somma pesata di 6 sub-score:

| Pilastro | Peso | Cosa misura |
|----------|------|-------------|
| **Trend** | 25% | Price vs EMA fast/slow — direzione |
| **Momentum** | 20% | RSI 14d mappato a score |
| **Volume** | 15% | Volume corrente vs media — interesse istituzionale |
| **Distance high** | 15% | Vicinanza al 52w high — uptrend maturo |
| **MA cross** | 15% | Golden / death cross recente su EMA fast × slow |
| **Volatility** | 10% | ATR% normalizzato — penalizza estremi |

**Classification** (da score composite):

| Class | Score | Azione |
|-------|-------|--------|
| **A** | ≥75 | Setup ottimale — entry con size piena |
| **B** | 60-74 | Setup valido — entry con size ridotta |
| **C** | 45-59 | Setup marginale — watchlist, no entry |
| **D** | <45 | Skip |

**Gate per entry long**: score ≥60 **E** regime weekly ≥ NEUTRAL (code ≥3).
Sotto il gate `--validate` non spende token Claude.

**RSI** (Relative Strength Index 14d) — oscillatore 0-100:
- >70 = ipercomprato (rischio pullback)
- 50-70 = uptrend momentum sano
- 30-50 = consolidamento / lateralità
- <30 = ipervenduto (rischio continuation short)

**ATR%** = Average True Range 14d / price. Volatilità normalizzata in %.
Usato per dimensionare lo stop: stop tipico = `price − 1.5 × ATR` per swing,
`price − 2 × ATR` per trend trade.

**Dist52wH** = distanza % dal massimo 52 settimane.
- 0% = al massimo (breakout in corso)
- -2% / -5% = pullback su uptrend (entry sweet spot)
- -15% / -25% = correzione profonda (attendi reset)

**Regime weekly** — classifier 5-bucket su ^GSPC:
STRONG_BEAR (1) · BEAR (2) · NEUTRAL (3) · BULL (4) · STRONG_BULL (5).
Entry long abilitato da NEUTRAL in su.

**Stop suggerito** = livello calcolato dal motore (struttura + ATR),
da copiare nei settings del Pine script daily come `stop_suggest`.
"""
            )
    elif scope == "portfolio":
        with st.expander("Legenda rischio & trade management", expanded=False):
            st.markdown(
                """
### Rischio per posizione (a stop)

| Metrica | Formula | Cosa dice |
|---------|---------|-----------|
| **Rischio €** | `(entry - stop) x shares` | Perdita se lo stop viene colpito (no slippage) |
| **% capitale** | `Rischio € / portfolio_value` | Quanto pesa quella perdita sul totale |
| **Rischio aggregato** | somma di tutti i Rischio € | Worst case se TUTTI gli stop scattano insieme |
| **Limite settimanale** | `5% x portfolio_value` | Hardcoded `MAX_LOSS_WEEKLY_PCT`. Sopra → blocco trading |

Una buona regola operativa: ogni singola posizione 1-2% del capitale, rischio aggregato sotto il limite settimanale.

### Concentrazione settoriale (GICS)

Somma il `% capitale` per settore aggregando tutte le posizioni dello stesso GICS sector.
Mapping da Yahoo a tassonomia interna in `domain/stock_rs.py::YF_SECTOR_TO_KEY`
(`Consumer Cyclical` → `consumer_discretionary`, `Communication Services` → `communications`, ecc.).

- Cap consigliato per settore: **30%** del capitale.
- Le regole single-name cappano al 15% per posizione, ma 2 stock dello stesso settore al 15% ciascuno = 30% effettivo: questa tabella lo rende visibile.
- Cash NON è incluso (è esposizione zero per definizione).
- Posizioni con sector ignoto finiscono in `unknown` (ETF esteri, IPO recenti).

### Beta-weighted gross long (vs SPX)

Tre numeri da leggere insieme:

| Metrica | Cosa misura |
|---------|-------------|
| **Gross long** | Esposizione lorda nominale `sum(weight_i)`. 0.65 = 65% investito, 35% cash. |
| **Beta-weighted** | `sum(weight_i x beta_i)`. Sensibilità al mercato. |
| **Beta noto** | Quante posizioni hanno beta reale da Yahoo (vs fallback 1.0). |

Esempio: gross 0.65 / beta-weighted 0.78 → portfolio investito al 65% che si muove come il 78% di SPX. Significa che la parte investita ha beta medio > 1 (titoli più volatili della media).

Beta proviene da `yf.Ticker(t).info['beta']` (calcolato da Yahoo su 5y monthly returns). Per ETF / IPO recenti / esteri illiquidi viene usato `beta=1.0` come fallback — la lista è mostrata sotto la tabella.

### Correlazioni pairwise

Matrice di correlazione su daily returns degli ultimi 6 mesi. Estraiamo solo l'**upper triangle** (no diagonale, no duplicati) e teniamo le pair con `|corr| ≥ 0.7`.

- **Significato**: due ticker con corr ≥ 0.7 si muovono insieme la grande maggioranza del tempo. Sono effettivamente la stessa scommessa.
- **Implicazione**: avere AAPL + MSFT + GOOGL non è 3 posizioni indipendenti su tech, è 1 posizione tech con sizing 3x. Il rischio è camuffato.
- Servono ≥ 30 osservazioni per pair (giorni con dati su entrambi). Sotto soglia ritorna None invece di una matrice rumorosa.

### Trade management — trailing stop

ATR-based, **ratchet-up only**. Logica:

1. Lo stop iniziale resta invariato finché `highest_price < entry + 1R` (1R = `entry - initial_stop`). Rationale: muovere lo stop troppo presto trasforma uno swing legittimo in stop-out rumoroso.
2. Sopra soglia: `proposed = highest_price - atr_mult x current_ATR`. Il nuovo stop è `max(current_stop, proposed)` — **mai scende**.
3. **Trailing è opt-in per posizione** (default OFF). Il trader decide caso per caso quali setup meritano trailing (momentum / breakout) vs hard stop fisso (mean-reversion).

Parametri:

| Parametro | Default | Effetto |
|-----------|---------|---------|
| **ATR multiplier** | 2.0 | Stop a `highest - 2x ATR`. Più alto = trailing più largo (meno stop-out rumorosi, più profit lasciato sul tavolo). |
| **Highest price** | tracked | Massimo raggiunto post-entry; aggiornato a ogni `manage` run. |

### Trade management — time stop

Se trade flat (`|P&L%| < flat_threshold`) da almeno N giorni → suggerisci chiusura.

| Parametro | Default | Effetto |
|-----------|---------|---------|
| **Time stop (giorni)** | 30 | Soglia di pazienza. Sotto = trade troppo recente per giudicare. |
| **Flat threshold** | 0.02 (2%) | Sotto questa soglia il trade è "flat". Un -1% da 40gg è flat; un +5% non lo è. |

Rationale: il costo-opportunità di tenere capitale fermo su un trade che non va da nessuna parte è reale, anche se il P&L mark-to-market è nullo. Quel cash potrebbe essere su un setup migliore.

**Importante:** la dashboard NON chiude automaticamente le posizioni con TIME-STOP. Mostra il flag e lascia decisione + esecuzione al trader (tab **Chiudi posizione** + Journal close).
"""
            )
