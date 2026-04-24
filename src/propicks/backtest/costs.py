"""Transaction cost + slippage model per Phase 6 backtest.

Pure functions: prendono entry/exit price + shares + ticker, ritornano
cost/slippage in €/USD. Il caller (engine portfolio) li applica al fill price.

## Modello di default (retail IBKR-like)

- **Commissioni**:
  - Stock US (no suffisso): **$0 / trade** (IBKR Lite / Robinhood free)
  - Stock .MI (Borsa Italiana): **€2 / trade** (IBKR ESMA EU)
  - ETF US (XL*, SP*): **$0** (come stock)
  - ETF EU (.DE, .MI suffisso): **€2**

- **Bid-ask spread (one-way)**:
  - Stock US liquid (SPX500): **5 bps** (0.05%)
  - Stock .MI (large cap): **10 bps** (0.10%)
  - ETF US (liquidi): **2 bps** (0.02%)
  - ETF EU (.DE/.MI): **5 bps** (0.05%)

- **Slippage aggiuntivo**:
  - Market orders a close: **2 bps** extra (price jitter pre-close)

Questi default sono **configurabili** tramite parametri. Utente può stringere
su portfolio HFT-like o allargare su mercati illiquidi.

## Riferimenti

- IBKR commission schedule: interactivebrokers.com/en/pricing/commissions-home
- Spread stimato da bid-ask avg su top 500 US + top 40 Borsa Italiana
  (pullati da polygon.io sample, 2024)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Default cost params
# ---------------------------------------------------------------------------
DEFAULT_COMMISSION_US: float = 0.0      # IBKR Lite / Robinhood US stock
DEFAULT_COMMISSION_EU: float = 2.0       # €2 flat per .MI, .DE trade
DEFAULT_SPREAD_BPS_US: float = 5.0       # 0.05%
DEFAULT_SPREAD_BPS_EU: float = 10.0      # 0.10%
DEFAULT_SPREAD_BPS_ETF_US: float = 2.0   # 0.02% (liquid ETF)
DEFAULT_SPREAD_BPS_ETF_EU: float = 5.0
DEFAULT_SLIPPAGE_BPS: float = 2.0        # mkt-on-close jitter


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CostModel:
    """Parametri del cost model. Frozen per immutabilità in backtest loop."""

    commission_us: float = DEFAULT_COMMISSION_US
    commission_eu: float = DEFAULT_COMMISSION_EU
    spread_bps_us: float = DEFAULT_SPREAD_BPS_US
    spread_bps_eu: float = DEFAULT_SPREAD_BPS_EU
    spread_bps_etf_us: float = DEFAULT_SPREAD_BPS_ETF_US
    spread_bps_etf_eu: float = DEFAULT_SPREAD_BPS_ETF_EU
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS

    @classmethod
    def zero(cls) -> CostModel:
        """Helper: no cost (per confronto con legacy backtest)."""
        return cls(
            commission_us=0.0, commission_eu=0.0,
            spread_bps_us=0.0, spread_bps_eu=0.0,
            spread_bps_etf_us=0.0, spread_bps_etf_eu=0.0,
            slippage_bps=0.0,
        )

    @classmethod
    def from_bps(cls, total_bps: float, commission_us: float = 0.0, commission_eu: float = 2.0) -> CostModel:
        """Shortcut: un singolo bps value per TUTTI i costi di spread+slip,
        applicato a ogni asset. Utile per sensitivity analysis (``--tc-bps 20``)."""
        return cls(
            commission_us=commission_us,
            commission_eu=commission_eu,
            spread_bps_us=total_bps,
            spread_bps_eu=total_bps,
            spread_bps_etf_us=total_bps,
            spread_bps_etf_eu=total_bps,
            slippage_bps=0.0,  # inclusa nel total_bps
        )


# ---------------------------------------------------------------------------
# Ticker classification
# ---------------------------------------------------------------------------
AssetClass = Literal["stock_us", "stock_eu", "etf_us", "etf_eu"]


_EU_SUFFIXES = frozenset({
    "MI",  # Milan
    "DE", "F", "BE", "DU",  # Germany
    "L",   # London
    "PA",  # Paris
    "AS",  # Amsterdam
    "BR",  # Brussels
    "MC",  # Madrid
    "SW",  # Swiss
    "ST",  # Stockholm
    "HE",  # Helsinki
    "OL",  # Oslo
    "CO",  # Copenhagen
})


def _is_etf(ticker: str) -> bool:
    """Heuristic: ticker è un ETF se matcha pattern noti.

    Non perfetto ma sufficiente per cost estimation (la differenza
    stock-vs-ETF è minor: 2bp vs 5bp). False negative = lieve
    over-estimation costi (safe).
    """
    ticker_u = ticker.upper()
    # US Select Sector SPDR
    if ticker_u in frozenset({"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLRE", "XLB", "XLC"}):
        return True
    # SPY, QQQ, DIA, IWM, VTI, VOO — major US ETF
    if ticker_u in frozenset({"SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "URTH"}):
        return True
    # UCITS prefixes typical for ETF EU
    if ticker_u.startswith(("ZPD", "XDW", "IUSA", "EUNL", "VUAA", "CSPX")):
        return True
    return False


def classify_asset(ticker: str) -> AssetClass:
    """Classifica il ticker per cost lookup.

    - Suffisso ``.MI`` / `.DE` / `.L` / etc → EU
    - Altrimenti → US
    - Pattern ETF noti → etf_*
    """
    ticker_u = ticker.upper()
    parts = ticker_u.split(".")
    suffix = parts[-1] if len(parts) > 1 else ""

    is_eu = suffix in _EU_SUFFIXES
    is_etf = _is_etf(ticker_u)

    if is_etf:
        return "etf_eu" if is_eu else "etf_us"
    return "stock_eu" if is_eu else "stock_us"


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------
def spread_bps_for(ticker: str, model: CostModel) -> float:
    """Ritorna lo spread in bps per il ticker dato il model."""
    cls = classify_asset(ticker)
    return {
        "stock_us": model.spread_bps_us,
        "stock_eu": model.spread_bps_eu,
        "etf_us": model.spread_bps_etf_us,
        "etf_eu": model.spread_bps_etf_eu,
    }[cls]


def commission_for(ticker: str, model: CostModel) -> float:
    """Ritorna la commission flat per il trade (non dipende da shares/price)."""
    cls = classify_asset(ticker)
    return model.commission_eu if cls.endswith("_eu") else model.commission_us


def apply_entry_costs(
    entry_price: float,
    shares: int,
    ticker: str,
    model: CostModel,
) -> dict:
    """Applica costi all'entry (buy).

    Conservative: il fill entry è **peggiore** del mid (paghi l'ask →
    entry_price × (1 + spread/2) — approx half-spread one-way).
    Aggiungi slippage fisso. Commissione flat.

    Returns dict con:
        - ``effective_entry``: prezzo fill realistico (>= entry_price)
        - ``cost_total``: total cost in currency (commission + slippage in $)
        - ``cost_bps``: total cost in bps vs gross trade value
    """
    if shares <= 0 or entry_price <= 0:
        return {
            "effective_entry": entry_price,
            "cost_total": 0.0,
            "cost_bps": 0.0,
        }
    spread = spread_bps_for(ticker, model)
    slip = model.slippage_bps
    half_spread_bps = spread / 2  # one-way = half bid-ask
    # Entry price peggiorato (paghi l'ask + slippage)
    markup = (half_spread_bps + slip) / 10000.0  # bps → fraction
    effective_entry = entry_price * (1 + markup)
    commission = commission_for(ticker, model)
    implicit_cost = (effective_entry - entry_price) * shares  # differenza vs mid
    total_cost = commission + implicit_cost
    gross = entry_price * shares
    return {
        "effective_entry": round(effective_entry, 4),
        "cost_total": round(total_cost, 2),
        "cost_bps": round(total_cost / gross * 10000, 2) if gross > 0 else 0.0,
        "commission": commission,
        "implicit_spread_slip": round(implicit_cost, 2),
    }


def apply_exit_costs(
    exit_price: float,
    shares: int,
    ticker: str,
    model: CostModel,
) -> dict:
    """Applica costi all'exit (sell).

    Symmetric all'entry: il fill exit è **peggiore** del mid (paghi il bid →
    exit_price × (1 - spread/2)). Slippage + commission.
    """
    if shares <= 0 or exit_price <= 0:
        return {
            "effective_exit": exit_price,
            "cost_total": 0.0,
            "cost_bps": 0.0,
        }
    spread = spread_bps_for(ticker, model)
    slip = model.slippage_bps
    half_spread_bps = spread / 2
    markdown = (half_spread_bps + slip) / 10000.0
    effective_exit = exit_price * (1 - markdown)
    commission = commission_for(ticker, model)
    implicit_cost = (exit_price - effective_exit) * shares
    total_cost = commission + implicit_cost
    gross = exit_price * shares
    return {
        "effective_exit": round(effective_exit, 4),
        "cost_total": round(total_cost, 2),
        "cost_bps": round(total_cost / gross * 10000, 2) if gross > 0 else 0.0,
        "commission": commission,
        "implicit_spread_slip": round(implicit_cost, 2),
    }


def round_trip_cost_bps(ticker: str, model: CostModel) -> float:
    """Costo roundtrip approssimato (entry + exit) in bps — per sensitivity.

    Assume shares/price normali (commission diventa trascurabile su trade
    di 1000+ €). Per retail piccoli la commission domina → usa apply_*_costs
    per numeri esatti.
    """
    spread = spread_bps_for(ticker, model)
    slip = model.slippage_bps
    # Ogni leg: half-spread + slip. Roundtrip: 2 × (half_spread + slip) = spread + 2*slip
    return spread + 2 * slip
