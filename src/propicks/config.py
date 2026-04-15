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

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None


# ---------------------------------------------------------------------------
# CAPITALE E SIZING
# ---------------------------------------------------------------------------
CAPITAL: float = 10_000.0
MAX_POSITIONS: int = 10
MAX_POSITION_SIZE_PCT: float = 0.15
MIN_CASH_RESERVE_PCT: float = 0.20

HIGH_CONVICTION_SIZE_PCT: float = 0.12
MEDIUM_CONVICTION_SIZE_PCT: float = 0.08

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

AI_WEB_SEARCH_ENABLED: bool = os.environ.get(
    "PROPICKS_AI_WEB_SEARCH", "1"
).lower() not in ("0", "false", "no", "off", "")
AI_WEB_SEARCH_MAX_USES: int = int(os.environ.get("PROPICKS_AI_WEB_SEARCH_MAX_USES", "5"))

os.makedirs(AI_CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# FORMATI
# ---------------------------------------------------------------------------
DATE_FMT: str = "%Y-%m-%d"
