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

from typing import Optional

import streamlit as st

from propicks.config import CAPITAL, MAX_POSITIONS


# ---------------------------------------------------------------------------
# Cached readers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def cached_analyze(ticker: str, strategy: Optional[str]) -> Optional[dict]:
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


def load_portfolio() -> dict:
    from propicks.io.portfolio_store import load_portfolio as _load
    return _load()


def load_journal() -> list[dict]:
    from propicks.io.journal_store import load_journal as _load
    return _load()


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
def fmt_pct(val: Optional[float], *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"{val * 100:.{decimals}f}%"


def fmt_eur(val: Optional[float], *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"€ {val:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_usd(val: Optional[float], *, decimals: int = 2, none: str = "—") -> str:
    if val is None:
        return none
    return f"$ {val:,.{decimals}f}"


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


def regime_badge(regime: Optional[dict]) -> str:
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


def kpi_row(items: list[tuple[str, str, Optional[str]]]) -> None:
    """Render a row of metrics. Items: (label, value, delta_or_None)."""
    cols = st.columns(len(items))
    for col, (label, value, delta) in zip(cols, items):
        col.metric(label, value, delta)


def invariants_note() -> None:
    """Sidebar footer con invariants rapidi — promemoria visibile."""
    st.sidebar.markdown("---")
    st.sidebar.caption(
        f"**Invariants**  \n"
        f"• Capitale riferimento: € {CAPITAL:,.0f}  \n"
        f"• Max posizioni: {MAX_POSITIONS}  \n"
        f"• Max size: 15% stock / 20% ETF  \n"
        f"• Min cash: 20%  \n"
        f"• Max loss week: 5% → stop"
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


def render_indicator_legend(scope: str = "etf") -> None:
    """Expander collassato con spiegazione completa degli indicatori.

    ``scope``: ``"etf"`` per ETF Rotation, ``"stock"`` per Scanner.
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
