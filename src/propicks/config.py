"""Configurazione centrale del trading engine.

Tutti i parametri operativi vivono in questo file: è l'unico punto
da modificare per cambiare dimensionamento, soglie di rischio,
pesi di scoring o path del progetto.

I path runtime (data/, reports/) sono ancorati alla root del progetto,
identificata risalendo l'albero fino a trovare ``pyproject.toml``.
Questo consente di eseguire i comandi da qualsiasi cwd senza rompere
la persistenza.
"""

from __future__ import annotations

import os
from typing import Literal

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None


# ---------------------------------------------------------------------------
# ASSET TYPES
# ---------------------------------------------------------------------------
# Il framework supporta strategie parallele su stock e ETF settoriali. Il tipo
# viene derivato dal ticker via ``domain.etf_universe.get_asset_type`` e pilota
# lo scoring engine (stock = tesi aziendale, ETF settoriale = regime + RS).
AssetType = Literal["STOCK", "SECTOR_ETF", "COMMODITY_ETF"]

SectorKey = Literal[
    "technology",
    "financials",
    "energy",
    "healthcare",
    "industrials",
    "consumer_discretionary",
    "consumer_staples",
    "utilities",
    "real_estate",
    "materials",
    "communications",
]


# ---------------------------------------------------------------------------
# CAPITALE E SIZING
# ---------------------------------------------------------------------------
CAPITAL: float = 10_000.0
MAX_POSITIONS: int = 10
MAX_POSITION_SIZE_PCT: float = 0.15
MIN_CASH_RESERVE_PCT: float = 0.20

HIGH_CONVICTION_SIZE_PCT: float = 0.12
MEDIUM_CONVICTION_SIZE_PCT: float = 0.08

# ETF settoriali sono già diversificati: il cap è più largo dei titoli single-name.
# I miners (GDX/COPX/URA) e gli inversi/leveraged restano fuori: tracciati nei
# commodity ETF con regole sizing dedicate (Fase commodity, TODO).
ETF_MAX_POSITION_SIZE_PCT: float = 0.20

MIN_SCORE_CLAUDE: int = 6
MIN_SCORE_TECH: int = 60


# ---------------------------------------------------------------------------
# RISK MANAGEMENT
# ---------------------------------------------------------------------------
MAX_LOSS_PER_TRADE_PCT: float = 0.08
MAX_LOSS_WEEKLY_PCT: float = 0.05
MAX_LOSS_MONTHLY_PCT: float = 0.15
EARNINGS_WARNING_DAYS: int = 5


# ---------------------------------------------------------------------------
# INDICATORI TECNICI
# ---------------------------------------------------------------------------
EMA_FAST: int = 20
EMA_SLOW: int = 50

RSI_PERIOD: int = 14
RSI_OVERSOLD: int = 30
RSI_OVERBOUGHT: int = 70

ATR_PERIOD: int = 14

VOLUME_AVG_PERIOD: int = 20
VOLUME_SPIKE_MULTIPLIER: float = 1.5

LOOKBACK_DAYS: int = 120


# ---------------------------------------------------------------------------
# WEEKLY REGIME (filtro macro — replica Pine weekly_regime_engine.pine)
# ---------------------------------------------------------------------------
REGIME_WEEKLY_EMA_FAST: int = 10  # ≈ EMA 50 daily
REGIME_WEEKLY_EMA_SLOW: int = 30  # ≈ EMA 150 daily
REGIME_WEEKLY_EMA_200D: int = 40  # ≈ EMA 200 daily
REGIME_ADX_PERIOD: int = 14
REGIME_ADX_STRONG: float = 25.0
REGIME_ADX_WEAK: float = 20.0
REGIME_MIN_WEEKLY_BARS: int = 60  # warm-up per EMA40 stabile


# ---------------------------------------------------------------------------
# SCORING WEIGHTS
# ---------------------------------------------------------------------------
WEIGHT_TREND: float = 0.25
WEIGHT_MOMENTUM: float = 0.20
WEIGHT_VOLUME: float = 0.15
WEIGHT_DISTANCE_HIGH: float = 0.15
WEIGHT_VOLATILITY: float = 0.10
WEIGHT_MA_CROSS: float = 0.15

_SCORING_WEIGHTS = (
    WEIGHT_TREND,
    WEIGHT_MOMENTUM,
    WEIGHT_VOLUME,
    WEIGHT_DISTANCE_HIGH,
    WEIGHT_VOLATILITY,
    WEIGHT_MA_CROSS,
)
assert abs(sum(_SCORING_WEIGHTS) - 1.0) < 1e-9, (
    f"Scoring weights non sommano a 1.0: {sum(_SCORING_WEIGHTS)}"
)


# ---------------------------------------------------------------------------
# STRATEGIE PRO PICKS
# ---------------------------------------------------------------------------
STRATEGIES = (
    "TechTitans",
    "DominaDow",
    "BattiSP500",
    "MiglioriItaliane",
)


# ---------------------------------------------------------------------------
# UNIVERSO ETF SETTORIALI (US SPDR + UCITS wrapper su stesso indice)
# ---------------------------------------------------------------------------
# Strategia parallela a quella single-stock: quando il ticker è qui dentro,
# lo scoring usa RS vs benchmark + regime fit invece della tesi aziendale.
#
# US: Select Sector SPDR (11 settori GICS) — replicano i Select Sector
# indices di S&P.
#
# EU: SPDR S&P U.S. Select Sector UCITS (tickers ZPD*.DE su Xetra). Tracciano
# lo STESSO Select Sector Index dei SPDR US — esposizione identica, solo
# wrapper UCITS domiciliato in Irlanda, accumulating (ISIN IE00B*). La tesi
# di rotazione è unica: se XLK è favorito, anche ZPDT.DE lo è. Il trader
# sceglie il listing in base a fiscalità (UCITS = no W8-BEN, tax drag minore
# su dividendi tramite accumulo) e liquidità del proprio broker.
#
# XLRE non ha un SPDR US Real Estate Select Sector UCITS equivalente. Campo
# ``eu_equivalent`` lasciato None — se serve esposizione REIT US in formato
# UCITS, valutare alternative esterne all'universo (es. IUSP.L iShares US
# Property Yield, che però traccia un indice diverso).
#
# IMPORTANTE: verificare ticker e ISIN sul proprio broker prima dell'uso.
# Distribuzione: varianti distributing/UCITS su LSE (SXR*, SXLK etc) esistono
# ma hanno ticker e tax treatment diversi — qui non registrate.

SECTOR_ETFS_US: dict[str, dict] = {
    "XLK": {
        "name": "Technology Select Sector SPDR",
        "sector_key": "technology",
        "eu_equivalent": "ZPDT.DE",
    },
    "XLF": {
        "name": "Financial Select Sector SPDR",
        "sector_key": "financials",
        "eu_equivalent": "ZPDF.DE",
    },
    "XLE": {
        "name": "Energy Select Sector SPDR",
        "sector_key": "energy",
        "eu_equivalent": "ZPDE.DE",
    },
    "XLV": {
        "name": "Health Care Select Sector SPDR",
        "sector_key": "healthcare",
        "eu_equivalent": "ZPDH.DE",
    },
    "XLI": {
        "name": "Industrial Select Sector SPDR",
        "sector_key": "industrials",
        "eu_equivalent": "ZPDI.DE",
    },
    "XLY": {
        "name": "Consumer Discretionary Select Sector SPDR",
        "sector_key": "consumer_discretionary",
        "eu_equivalent": "ZPDD.DE",
    },
    "XLP": {
        "name": "Consumer Staples Select Sector SPDR",
        "sector_key": "consumer_staples",
        "eu_equivalent": "ZPDS.DE",
    },
    "XLU": {
        "name": "Utilities Select Sector SPDR",
        "sector_key": "utilities",
        "eu_equivalent": "ZPDU.DE",
    },
    "XLRE": {
        "name": "Real Estate Select Sector SPDR",
        "sector_key": "real_estate",
        "eu_equivalent": None,
        "eu_equivalent_note": "Nessun SPDR US Real Estate Select Sector UCITS — alternativa esterna: IUSP.L (iShares US Property Yield, indice diverso)",
    },
    "XLB": {
        "name": "Materials Select Sector SPDR",
        "sector_key": "materials",
        "eu_equivalent": "ZPDM.DE",
    },
    "XLC": {
        "name": "Communication Services Select Sector SPDR",
        "sector_key": "communications",
        "eu_equivalent": "ZPDX.DE",
    },
}

SECTOR_ETFS_EU: dict[str, dict] = {
    "ZPDT.DE": {
        "name": "SPDR S&P U.S. Technology Select Sector UCITS",
        "sector_key": "technology",
        "us_equivalent": "XLK",
    },
    "ZPDF.DE": {
        "name": "SPDR S&P U.S. Financials Select Sector UCITS",
        "sector_key": "financials",
        "us_equivalent": "XLF",
    },
    "ZPDE.DE": {
        "name": "SPDR S&P U.S. Energy Select Sector UCITS",
        "sector_key": "energy",
        "us_equivalent": "XLE",
    },
    "ZPDH.DE": {
        "name": "SPDR S&P U.S. Health Care Select Sector UCITS",
        "sector_key": "healthcare",
        "us_equivalent": "XLV",
    },
    "ZPDI.DE": {
        "name": "SPDR S&P U.S. Industrials Select Sector UCITS",
        "sector_key": "industrials",
        "us_equivalent": "XLI",
    },
    "ZPDD.DE": {
        "name": "SPDR S&P U.S. Consumer Discretionary Select Sector UCITS",
        "sector_key": "consumer_discretionary",
        "us_equivalent": "XLY",
    },
    "ZPDS.DE": {
        "name": "SPDR S&P U.S. Consumer Staples Select Sector UCITS",
        "sector_key": "consumer_staples",
        "us_equivalent": "XLP",
    },
    "ZPDU.DE": {
        "name": "SPDR S&P U.S. Utilities Select Sector UCITS",
        "sector_key": "utilities",
        "us_equivalent": "XLU",
    },
    "ZPDM.DE": {
        "name": "SPDR S&P U.S. Materials Select Sector UCITS",
        "sector_key": "materials",
        "us_equivalent": "XLB",
    },
    "ZPDX.DE": {
        "name": "SPDR S&P U.S. Communication Services Select Sector UCITS",
        "sector_key": "communications",
        "us_equivalent": "XLC",
    },
}


# ---------------------------------------------------------------------------
# ETF ROTATION — PARAMETRI STRATEGIA
# ---------------------------------------------------------------------------
# Benchmark contro cui misurare la Relative Strength dei settori. ^GSPC
# (S&P 500 spot) è coerente con l'universo Select Sector SPDR US (stesso
# perimetro GICS). Per l'universo UCITS EU il benchmark resta ^GSPC: i
# ZPD*.DE tracciano gli stessi Select Sector Index, cambia solo il wrapper
# — usare STOXX 600 confonderebbe currency effect e constituents diversi.
ETF_BENCHMARK: str = "^GSPC"

# Finestra weekly per il calcolo RS vs benchmark. 26 settimane ≈ 6 mesi —
# abbastanza lunga da smussare rumore, corta abbastanza da captare rotazioni.
ETF_RS_LOOKBACK_WEEKS: int = 26
ETF_RS_EMA_WEEKS: int = 10  # slope della RS line

# Finestra assoluta di momentum (daily). 63 = ~3 mesi di trading.
ETF_MOMENTUM_LOOKBACK_DAYS: int = 63

# Pesi del composite ETF (somma = 1.0). Cfr. CLAUDE.md Fase 2.
ETF_WEIGHT_RS: float = 0.40
ETF_WEIGHT_REGIME_FIT: float = 0.30
ETF_WEIGHT_ABS_MOMENTUM: float = 0.20
ETF_WEIGHT_TREND: float = 0.10

_ETF_WEIGHTS = (
    ETF_WEIGHT_RS,
    ETF_WEIGHT_REGIME_FIT,
    ETF_WEIGHT_ABS_MOMENTUM,
    ETF_WEIGHT_TREND,
)
assert abs(sum(_ETF_WEIGHTS) - 1.0) < 1e-9, (
    f"ETF scoring weights non sommano a 1.0: {sum(_ETF_WEIGHTS)}"
)

# Portafoglio ETF: quanti settori tenere simultaneamente e cap aggregato.
ETF_TOP_N_DEFAULT: int = 3
ETF_MAX_AGGREGATE_EXPOSURE_PCT: float = 0.60  # lascia headroom per single-stock + cash

# Hysteresis regime: il regime change non causa immediata rotazione.
# Il settore esce dalla top-N solo se lo score scende sotto questa soglia.
ETF_REBALANCE_THRESHOLD: float = 10.0  # delta score necessario per triggerare rotate

# Classificazione ETF (parallela a ``classify`` degli stock).
ETF_SCORE_OVERWEIGHT: float = 70.0   # A — allocare
ETF_SCORE_HOLD: float = 55.0          # B — mantenere se già in portfolio
ETF_SCORE_NEUTRAL: float = 40.0       # C — no action
# sotto 40 → D AVOID

# ETF stop: 5% hard stop (non ATR-based — ETF sono a bassa vol e ATR stop
# salta sul rumore). Exit primario è trigger regime (gestito a livello di
# portfolio construction), stop % è il safety net.
ETF_STOP_LOSS_PCT: float = 0.05


# ---------------------------------------------------------------------------
# REGIME → SETTORI FAVORITI
# ---------------------------------------------------------------------------
# Lookup che traduce il regime_code weekly (1-5 da domain.regime) nella lista
# di sector_key da privilegiare nella rotazione. È la tabella più opinabile
# del framework: rappresenta una view ciclica classica (early→late cycle →
# defensives → capital preservation). Va rivista a ogni regime change per
# verificare che i leader reali la confermino.
#
# Cross-reference:
#   5 STRONG_BULL  → risk-on puro (growth + cyclicals + financials)
#   4 BULL         → mid-cycle (cyclicals + materials)
#   3 NEUTRAL      → quality tilt (healthcare + industrials)
#   2 BEAR         → difensivi (staples + utilities + healthcare)
#   1 STRONG_BEAR  → capital preservation (staples + utilities)

REGIME_FAVORED_SECTORS: dict[int, tuple[str, ...]] = {
    5: ("technology", "consumer_discretionary", "communications", "financials", "industrials"),
    4: ("technology", "consumer_discretionary", "industrials", "materials", "financials"),
    3: ("healthcare", "industrials", "financials", "technology"),
    2: ("consumer_staples", "utilities", "healthcare"),
    1: ("consumer_staples", "utilities"),
}


# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
def _find_project_root() -> str:
    """Risale l'albero da questo file fino a trovare pyproject.toml.

    In editable install il package vive in src/propicks/, due livelli
    sotto la root. Fallback alla cwd se non trovato (improbabile).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.exists(os.path.join(here, "pyproject.toml")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.getcwd()
        here = parent


BASE_DIR: str = _find_project_root()

if _load_dotenv is not None:
    _load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

DATA_DIR: str = os.path.join(BASE_DIR, "data")
REPORTS_DIR: str = os.path.join(BASE_DIR, "reports")
BASKETS_DIR: str = os.path.join(DATA_DIR, "baskets")

PORTFOLIO_FILE: str = os.path.join(DATA_DIR, "portfolio.json")
JOURNAL_FILE: str = os.path.join(DATA_DIR, "journal.json")
WATCHLIST_FILE: str = os.path.join(DATA_DIR, "watchlist.json")

for _d in (DATA_DIR, REPORTS_DIR, BASKETS_DIR):
    os.makedirs(_d, exist_ok=True)


# ---------------------------------------------------------------------------
# AI VALIDATION (Claude)
# ---------------------------------------------------------------------------
AI_MODEL: str = os.environ.get("PROPICKS_AI_MODEL", "claude-opus-4-6")
AI_MAX_TOKENS: int = 4096
AI_CACHE_DIR: str = os.path.join(DATA_DIR, "ai_cache")
AI_CACHE_TTL_HOURS: int = 24
AI_MIN_SCORE_FOR_VALIDATION: int = MIN_SCORE_TECH
AI_TIMEOUT_SECONDS: float = 120.0

AI_WEB_SEARCH_ENABLED: bool = os.environ.get("PROPICKS_AI_WEB_SEARCH", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
    "",
)
AI_WEB_SEARCH_MAX_USES: int = int(os.environ.get("PROPICKS_AI_WEB_SEARCH_MAX_USES", "5"))

os.makedirs(AI_CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# FORMATI
# ---------------------------------------------------------------------------
DATE_FMT: str = "%Y-%m-%d"
