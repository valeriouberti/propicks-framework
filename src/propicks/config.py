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


# WORLD: Xtrackers MSCI World Sector UCITS (serie XDW* / XWTS / XZRE su Xetra).
# Esposizione ai settori GICS su scala MSCI World (developed markets) — tipicamente
# ~65-70% US, ~15% Europa, ~6% Giappone, resto sviluppati. Serie Xtrackers (DWS),
# NON iShares e NON SPDR. Accumulating, domicilio IE, TER 0.25% per tutta la serie.
#
# Differenze operative vs Select Sector SPDR:
# - Composizione: settore world include europee/giapponesi (es. energy con
#   Shell/TotalEnergies/BP, industrials con Siemens/ABB, financials con HSBC/UBS).
#   Non è un sostituto 1:1 dei SPDR — è una tesi diversa (rotation globale).
# - Benchmark: per RS va usato un benchmark WORLD (URTH iShares MSCI World o
#   equivalente), NON ^GSPC. Mischiare i due confonde outperformance con
#   differenze di perimetro geografico. Cfr. ``ETF_BENCHMARK_WORLD``.
# - Real Estate: serie separata (XZRE) lanciata 2021 post-GICS reshuffle,
#   ISIN diverso dalla serie XDW* core.
# - Communication Services: ticker XWTS (outlier nella serie). Il fondo
#   riflette il GICS 2018 reshuffle, include Meta/Alphabet/Netflix in linea
#   con XLC US.
#
# IMPORTANTE: verificare ticker e ISIN sul proprio broker. Alcuni broker
# retail EU non quotano XWTS o XZRE su Xetra — fallback su listing Milano
# (.MI) se disponibile.

SECTOR_ETFS_WORLD: dict[str, dict] = {
    "XDWT.DE": {
        "name": "Xtrackers MSCI World Information Technology UCITS",
        "sector_key": "technology",
        "isin": "IE00BM67HT60",
    },
    "XDWF.DE": {
        "name": "Xtrackers MSCI World Financials UCITS",
        "sector_key": "financials",
        "isin": "IE00BM67HL84",
    },
    "XDW0.DE": {
        "name": "Xtrackers MSCI World Energy UCITS",
        "sector_key": "energy",
        "isin": "IE00BM67HM91",
    },
    "XDWH.DE": {
        "name": "Xtrackers MSCI World Health Care UCITS",
        "sector_key": "healthcare",
        "isin": "IE00BM67HK77",
    },
    "XDWI.DE": {
        "name": "Xtrackers MSCI World Industrials UCITS",
        "sector_key": "industrials",
        "isin": "IE00BM67HV82",
    },
    "XDWC.DE": {
        "name": "Xtrackers MSCI World Consumer Discretionary UCITS",
        "sector_key": "consumer_discretionary",
        "isin": "IE00BM67HP23",
    },
    "XDWS.DE": {
        "name": "Xtrackers MSCI World Consumer Staples UCITS",
        "sector_key": "consumer_staples",
        "isin": "IE00BM67HN09",
    },
    "XDWU.DE": {
        "name": "Xtrackers MSCI World Utilities UCITS",
        "sector_key": "utilities",
        "isin": "IE00BM67HQ30",
    },
    "XDWM.DE": {
        "name": "Xtrackers MSCI World Materials UCITS",
        "sector_key": "materials",
        "isin": "IE00BM67HS53",
    },
    "XWTS.DE": {
        "name": "Xtrackers MSCI World Communication Services UCITS",
        "sector_key": "communications",
        "isin": "IE00BM67HR47",
    },
    "IQQ6.DE": {
        "name": "iShares Developed Markets Property Yield UCITS",
        "sector_key": "real_estate",
        "isin": "IE00B1FZS350",
    },
}


# ---------------------------------------------------------------------------
# ETF ROTATION — PARAMETRI STRATEGIA
# ---------------------------------------------------------------------------
# Benchmark contro cui misurare la Relative Strength dei settori. Il benchmark
# dev'essere coerente col perimetro dell'universo — mischiare confonde RS con
# differenze geografiche:
#
# - US / EU  → ^GSPC (S&P 500). Coerente con Select Sector SPDR (US) e con
#              ZPD*.DE UCITS (stesso Select Sector Index, solo wrapper diverso).
# - WORLD    → URTH (iShares MSCI World ETF). Stesso perimetro geografico dei
#              settori Xtrackers XDW*/XWTS/XZRE. URTH è USD-denominated, liquido
#              via yfinance. In alternativa XDWD.DE se preferisci il wrapper
#              UCITS, ma URTH ha storia più lunga e pulita su yfinance.
#
# ``ETF_BENCHMARK`` resta come default US-centric (back-compat). Per il branch
# WORLD, ``get_etf_benchmark(region)`` ritorna il benchmark corretto.
ETF_BENCHMARK: str = "^GSPC"
ETF_BENCHMARK_WORLD: str = "URTH"

_ETF_BENCHMARK_BY_REGION: dict[str, str] = {
    "US": ETF_BENCHMARK,
    "EU": ETF_BENCHMARK,  # UCITS SPDR tracciano stesso Select Sector Index
    "WORLD": ETF_BENCHMARK_WORLD,
    "ALL": ETF_BENCHMARK,  # mixing → default US-centric (edge case, evitare)
}


def get_etf_benchmark(region: str) -> str:
    """Ritorna il ticker benchmark yfinance per la region ETF data.

    Sollevamento KeyError silenzioso → fallback US. Il chiamante può verificare
    se region è valida consultando ``_ETF_BENCHMARK_BY_REGION``.
    """
    return _ETF_BENCHMARK_BY_REGION.get(region.upper(), ETF_BENCHMARK)


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
ETF_SCORE_OVERWEIGHT: float = 70.0  # A — allocare
ETF_SCORE_HOLD: float = 55.0  # B — mantenere se già in portfolio
ETF_SCORE_NEUTRAL: float = 40.0  # C — no action
# sotto 40 → D AVOID

# ETF stop: 5% hard stop (non ATR-based — ETF sono a bassa vol e ATR stop
# salta sul rumore). Exit primario è trigger regime (gestito a livello di
# portfolio construction), stop % è il safety net.
ETF_STOP_LOSS_PCT: float = 0.05


# ---------------------------------------------------------------------------
# CONTRARIAN STRATEGY — QUALITY-FILTERED MEAN REVERSION
# ---------------------------------------------------------------------------
# Strategia parallela, additiva: momentum/quality cerca forza che accelera,
# contrarian compra qualità temporaneamente oversold. NON modifica lo scoring
# momentum attuale — è un motore indipendente con propri sub-score, regime
# fit inverso, e invarianti di sizing più strette (hit rate più basso).
#
# Setup valido solo se TUTTI i filtri passano:
#   1. Oversold tecnico (RSI + distanza ATR da EMA50)
#   2. Trend di lungo non rotto (price sopra EMA200 weekly)
#   3. Market context favorevole (VIX spike / breadth washout, regime NON bullish)
#   4. Qualità (via Pro Picks filter — enforced in CLI, non nel domain puro)
#   5. Non-broken fundamental (via Claude validation "flush vs break")

# Sizing: più stretto del momentum (6-8% vs 15%) per il profilo short-gamma.
CONTRA_MAX_POSITION_SIZE_PCT: float = 0.08
CONTRA_MAX_AGGREGATE_EXPOSURE_PCT: float = 0.20  # bucket cap su sum(contra)
CONTRA_MAX_POSITIONS: int = 3  # max simultanei contrarian (share cap globale MAX_POSITIONS)

# Soglie oversold — il core del segnale.
CONTRA_RSI_OVERSOLD: float = 30.0  # RSI(14) strict oversold
CONTRA_RSI_WARM: float = 35.0  # tolleranza soft per zona "near oversold"
CONTRA_ATR_DISTANCE_MIN: float = 2.0  # distanza min da EMA50 in multipli di ATR
CONTRA_CONSECUTIVE_DOWN_DAYS: int = 3  # minimo n barre rosse consecutive
CONTRA_MIN_EMA200_BUFFER: float = 0.0  # price must be >= EMA200w * (1 + buffer)

# Market context — breadth / fear indicators (via yfinance, injectable).
CONTRA_VIX_TICKER: str = "^VIX"
CONTRA_VIX_SPIKE: float = 25.0  # sopra = paura/capitulazione, ottimo contesto
CONTRA_VIX_COMPLACENT: float = 14.0  # sotto = euforia, edge contrarian collassa

# Stop e target specifici: più larghi del momentum (il "noise" è la normalità
# su oversold), target = reversion a EMA50 (non trailing — è mean reversion).
CONTRA_STOP_ATR_MULT: float = 3.0  # stop = low_recent - 3×ATR (wider di momentum 2×)
CONTRA_MAX_LOSS_PER_TRADE_PCT: float = 0.12  # 12% max (vs 8% momentum)
CONTRA_TIME_STOP_DAYS: int = 15  # esito atteso in 5-15 giorni, taglia a 15gg
CONTRA_TARGET_EMA_PERIOD: int = 50  # target = reversion a EMA50 daily

# Pesi del composite contrarian (somma = 1.0).
#   oversold: quanto è tirato l'elastico (40%)
#   quality: il trend strutturale non è rotto (25%)
#   market_context: VIX + regime inverso (20%)
#   reversion_potential: gap da EMA50 espresso come R/R teorico (15%)
CONTRA_WEIGHT_OVERSOLD: float = 0.40
CONTRA_WEIGHT_QUALITY: float = 0.25
CONTRA_WEIGHT_MARKET_CONTEXT: float = 0.20
CONTRA_WEIGHT_REVERSION: float = 0.15

_CONTRA_WEIGHTS = (
    CONTRA_WEIGHT_OVERSOLD,
    CONTRA_WEIGHT_QUALITY,
    CONTRA_WEIGHT_MARKET_CONTEXT,
    CONTRA_WEIGHT_REVERSION,
)
assert abs(sum(_CONTRA_WEIGHTS) - 1.0) < 1e-9, (
    f"Contrarian scoring weights non sommano a 1.0: {sum(_CONTRA_WEIGHTS)}"
)

# Classification — stessi tier del momentum ma interpretazione diversa
# (A = setup oversold pronto, D = non abbastanza tirato o trend rotto).
CONTRA_SCORE_A: float = 75.0
CONTRA_SCORE_B: float = 60.0
CONTRA_SCORE_C: float = 45.0

# Gate regime INVERSO al momentum:
#   STRONG_BULL (5) → skip: non ci sono veri oversold, sono solo dip da comprare col momentum
#   BULL (4)        → ok ma conservativo (regime_fit 70)
#   NEUTRAL (3)     → sweet spot (100)
#   BEAR (2)        → ok se quality gate regge (85)
#   STRONG_BEAR (1) → skip: falling knives, non mean reversion
CONTRA_REGIME_FIT: dict[int, float] = {
    5: 25.0,   # STRONG_BULL: oversold rari e poco durevoli
    4: 70.0,   # BULL: mean reversion su pullback sani
    3: 100.0,  # NEUTRAL: sweet spot
    2: 85.0,   # BEAR: ottimo se quality gate regge
    1: 0.0,    # STRONG_BEAR: niente mean reversion su crash
}

# AI validation contrarian — cache separata (chiave con strategy tag per non
# collidere con cache momentum dello stesso ticker).
CONTRA_AI_CACHE_TTL_HOURS: int = 24
CONTRA_AI_MIN_SCORE_FOR_VALIDATION: float = 60.0


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

# SQLite database: source of truth di tutto lo stato transazionale
# (positions, trades, watchlist, AI verdicts, strategy runs, regime history).
# I file JSON sono stati ritirati al completamento di Phase 1 — la migrazione
# è one-shot via ``scripts/migrate_json_to_sqlite.py``. I path JSON sono mantenuti
# sotto per la migration script e il backup (non vengono più letti/scritti
# dal runtime).
DB_FILE: str = os.path.join(DATA_DIR, "propicks.db")

# Path legacy JSON — solo per migration script (one-shot). Non usare dai store.
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

# Budget cap giornaliero — impedisce che un loop accidentale o un run batch
# troppo ampio bruci token senza limite. Il contatore vive in
# ``data/ai_cache/usage_YYYY-MM-DD.json`` e si resetta automaticamente al
# cambio di giorno (nuovo file). Cache hit NON contano verso il budget.
# Override via env per sessioni ad-hoc: ``PROPICKS_AI_MAX_CALLS_PER_DAY=200``.
AI_MAX_CALLS_PER_DAY: int = int(os.environ.get("PROPICKS_AI_MAX_CALLS_PER_DAY", "50"))
AI_MAX_COST_USD_PER_DAY: float = float(
    os.environ.get("PROPICKS_AI_MAX_COST_USD_PER_DAY", "5.0")
)
# Costo stimato per chiamata (input + output + web_search medio). Conservative:
# prompt caching abbatte l'input, ma web_search è $0.01/ricerca e Opus ha output
# caro. $0.10 = upper bound realistico per validate stock con ~5 web searches.
AI_EST_COST_PER_CALL_USD: float = float(
    os.environ.get("PROPICKS_AI_EST_COST_PER_CALL_USD", "0.10")
)

os.makedirs(AI_CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# FORMATI
# ---------------------------------------------------------------------------
DATE_FMT: str = "%Y-%m-%d"
