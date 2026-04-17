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
