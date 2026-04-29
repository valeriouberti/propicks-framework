"""Universe discovery: lista membri di un index (S&P 500, FTSE MIB, STOXX 600).

Sorgente primaria: **Wikipedia** parsata via ``pandas.read_html``. È il metodo
standard nell'ecosistema retail-quant: aggiornato dalla community in real-time,
no API key, no rate limit. Tradeoff: dipendenza dallo schema HTML — se cambia
struttura tabella la fetch fallisce, e attiviamo il fallback.

## Indici supportati

- **sp500** — S&P 500 (US large cap). ~500 nomi, sanity ≥ 480.
- **ftsemib** — FTSE MIB (Italia, Borsa Italiana). 40 nomi fissi, sanity ≥ 35.
- **stoxx600** — STOXX Europe 600 (17 paesi, large/mid/small cap). ~600 nomi.

## Architettura cache (mirror Phase 2 OHLCV)

1. **Fast path**: cache SQLite fresh entro TTL (7gg) → ritorna dalla cache
2. **Miss/stale**: fetch Wikipedia → sanity check (≥ min constituents per
   index) → UPSERT atomico → ritorna
3. **Fallback**: se Wikipedia fails O sanity check non passa, ritorna lo
   snapshot hardcoded per quell'index. Lo snapshot NON viene mai persistito
   su DB — è solo runtime safety net.

## Public API

- ``get_sp500_universe(force_refresh=False) -> list[str]``
- ``get_ftsemib_universe(force_refresh=False) -> list[str]``
- ``get_stoxx600_universe(force_refresh=False) -> list[str]``
- ``get_index_universe(name, ...)`` — dispatcher generico per CLI/dashboard
- ``*_detailed`` varianti con company_name + sector
"""

from __future__ import annotations

import urllib.error
import urllib.request
from io import StringIO

import pandas as pd

from propicks.config import (
    FTSEMIB_MIN_CONSTITUENTS,
    FTSEMIB_WIKIPEDIA_URL,
    INDEX_CONSTITUENTS_CACHE_TTL_HOURS,
    INDEX_MIN_CONSTITUENTS,
    NASDAQ100_MIN_CONSTITUENTS,
    NASDAQ100_WIKIPEDIA_URL,
    SP500_WIKIPEDIA_URL,
    STOXX600_MIN_CONSTITUENTS,
    STOXX600_WIKIPEDIA_URL,
)
from propicks.io.db import (
    index_constituents_is_fresh,
    index_constituents_read,
    index_constituents_replace,
)
from propicks.obs.log import get_logger

_log = get_logger("market.index_constituents")

INDEX_NAME_SP500 = "sp500"
INDEX_NAME_FTSEMIB = "ftsemib"
INDEX_NAME_STOXX600 = "stoxx600"
INDEX_NAME_NASDAQ100 = "nasdaq100"

SUPPORTED_INDEXES = (
    INDEX_NAME_SP500,
    INDEX_NAME_FTSEMIB,
    INDEX_NAME_STOXX600,
    INDEX_NAME_NASDAQ100,
)


# ---------------------------------------------------------------------------
# Fallback snapshot (safety net runtime — NON persistito)
# ---------------------------------------------------------------------------
# Subset di ~50 mega-cap S&P 500 stabili (in index da 5+ anni, marketcap top).
# Usato solo se Wikipedia fails E la cache è vuota/stale. Coverage ridotta ma
# garantisce che il discovery non crashi mai per problemi di network/parsing.
# Aggiornare ~annualmente quando entrano nuovi mega-cap (es. ARM, IPO recenti).
SP500_FALLBACK: list[dict] = [
    # Technology
    {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology"},
    {"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Technology"},
    {"ticker": "NVDA", "company_name": "NVIDIA Corp.", "sector": "Technology"},
    {"ticker": "AVGO", "company_name": "Broadcom Inc.", "sector": "Technology"},
    {"ticker": "ORCL", "company_name": "Oracle Corp.", "sector": "Technology"},
    {"ticker": "CRM", "company_name": "Salesforce Inc.", "sector": "Technology"},
    {"ticker": "ADBE", "company_name": "Adobe Inc.", "sector": "Technology"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "sector": "Technology"},
    {"ticker": "CSCO", "company_name": "Cisco Systems", "sector": "Technology"},
    {"ticker": "INTC", "company_name": "Intel Corp.", "sector": "Technology"},
    # Communication Services
    {"ticker": "GOOGL", "company_name": "Alphabet Inc. Class A", "sector": "Communication Services"},
    {"ticker": "META", "company_name": "Meta Platforms", "sector": "Communication Services"},
    {"ticker": "NFLX", "company_name": "Netflix Inc.", "sector": "Communication Services"},
    {"ticker": "DIS", "company_name": "Walt Disney Co.", "sector": "Communication Services"},
    {"ticker": "VZ", "company_name": "Verizon Communications", "sector": "Communication Services"},
    {"ticker": "T", "company_name": "AT&T Inc.", "sector": "Communication Services"},
    # Consumer Discretionary
    {"ticker": "AMZN", "company_name": "Amazon.com Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "TSLA", "company_name": "Tesla Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "HD", "company_name": "Home Depot Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "MCD", "company_name": "McDonald's Corp.", "sector": "Consumer Cyclical"},
    {"ticker": "NKE", "company_name": "Nike Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "SBUX", "company_name": "Starbucks Corp.", "sector": "Consumer Cyclical"},
    # Consumer Staples
    {"ticker": "WMT", "company_name": "Walmart Inc.", "sector": "Consumer Defensive"},
    {"ticker": "PG", "company_name": "Procter & Gamble", "sector": "Consumer Defensive"},
    {"ticker": "KO", "company_name": "Coca-Cola Co.", "sector": "Consumer Defensive"},
    {"ticker": "PEP", "company_name": "PepsiCo Inc.", "sector": "Consumer Defensive"},
    {"ticker": "COST", "company_name": "Costco Wholesale", "sector": "Consumer Defensive"},
    # Healthcare
    {"ticker": "JNJ", "company_name": "Johnson & Johnson", "sector": "Healthcare"},
    {"ticker": "UNH", "company_name": "UnitedHealth Group", "sector": "Healthcare"},
    {"ticker": "LLY", "company_name": "Eli Lilly and Co.", "sector": "Healthcare"},
    {"ticker": "MRK", "company_name": "Merck & Co.", "sector": "Healthcare"},
    {"ticker": "PFE", "company_name": "Pfizer Inc.", "sector": "Healthcare"},
    {"ticker": "ABBV", "company_name": "AbbVie Inc.", "sector": "Healthcare"},
    {"ticker": "TMO", "company_name": "Thermo Fisher Scientific", "sector": "Healthcare"},
    # Financials
    {"ticker": "JPM", "company_name": "JPMorgan Chase", "sector": "Financial Services"},
    {"ticker": "BAC", "company_name": "Bank of America", "sector": "Financial Services"},
    {"ticker": "WFC", "company_name": "Wells Fargo", "sector": "Financial Services"},
    {"ticker": "GS", "company_name": "Goldman Sachs Group", "sector": "Financial Services"},
    {"ticker": "MS", "company_name": "Morgan Stanley", "sector": "Financial Services"},
    {"ticker": "V", "company_name": "Visa Inc.", "sector": "Financial Services"},
    {"ticker": "MA", "company_name": "Mastercard Inc.", "sector": "Financial Services"},
    {"ticker": "BRK-B", "company_name": "Berkshire Hathaway B", "sector": "Financial Services"},
    # Industrials
    {"ticker": "CAT", "company_name": "Caterpillar Inc.", "sector": "Industrials"},
    {"ticker": "BA", "company_name": "Boeing Co.", "sector": "Industrials"},
    {"ticker": "HON", "company_name": "Honeywell International", "sector": "Industrials"},
    {"ticker": "UPS", "company_name": "United Parcel Service", "sector": "Industrials"},
    {"ticker": "GE", "company_name": "General Electric", "sector": "Industrials"},
    # Energy
    {"ticker": "XOM", "company_name": "Exxon Mobil Corp.", "sector": "Energy"},
    {"ticker": "CVX", "company_name": "Chevron Corp.", "sector": "Energy"},
    {"ticker": "COP", "company_name": "ConocoPhillips", "sector": "Energy"},
    # Materials
    {"ticker": "LIN", "company_name": "Linde plc", "sector": "Basic Materials"},
    # Utilities
    {"ticker": "NEE", "company_name": "NextEra Energy", "sector": "Utilities"},
    # Real Estate
    {"ticker": "PLD", "company_name": "Prologis Inc.", "sector": "Real Estate"},
]


# ---------------------------------------------------------------------------
# FTSE MIB fallback (40 nomi top — universo stabile, snapshot 2026)
# ---------------------------------------------------------------------------
# Lista mega-cap MIB (consensus 2024-2026) — usata solo se Wikipedia fails E
# cache vuota. I ticker hanno già il suffisso .MI per yfinance.
FTSEMIB_FALLBACK: list[dict] = [
    {"ticker": "ENI.MI", "company_name": "Eni S.p.A.", "sector": "Energy"},
    {"ticker": "ENEL.MI", "company_name": "Enel S.p.A.", "sector": "Utilities"},
    {"ticker": "ISP.MI", "company_name": "Intesa Sanpaolo", "sector": "Financial Services"},
    {"ticker": "UCG.MI", "company_name": "UniCredit", "sector": "Financial Services"},
    {"ticker": "STLAM.MI", "company_name": "Stellantis", "sector": "Consumer Cyclical"},
    {"ticker": "RACE.MI", "company_name": "Ferrari N.V.", "sector": "Consumer Cyclical"},
    {"ticker": "G.MI", "company_name": "Assicurazioni Generali", "sector": "Financial Services"},
    {"ticker": "STM.MI", "company_name": "STMicroelectronics", "sector": "Technology"},
    {"ticker": "MB.MI", "company_name": "Mediobanca", "sector": "Financial Services"},
    {"ticker": "CNHI.MI", "company_name": "CNH Industrial", "sector": "Industrials"},
    {"ticker": "BMED.MI", "company_name": "Banca Mediolanum", "sector": "Financial Services"},
    {"ticker": "BAMI.MI", "company_name": "Banco BPM", "sector": "Financial Services"},
    {"ticker": "PIRC.MI", "company_name": "Pirelli & C.", "sector": "Consumer Cyclical"},
    {"ticker": "MONC.MI", "company_name": "Moncler", "sector": "Consumer Cyclical"},
    {"ticker": "FBK.MI", "company_name": "FinecoBank", "sector": "Financial Services"},
    {"ticker": "TRN.MI", "company_name": "Terna", "sector": "Utilities"},
    {"ticker": "SRG.MI", "company_name": "Snam", "sector": "Energy"},
    {"ticker": "TIT.MI", "company_name": "Telecom Italia", "sector": "Communication Services"},
    {"ticker": "PRY.MI", "company_name": "Prysmian", "sector": "Industrials"},
    {"ticker": "LDO.MI", "company_name": "Leonardo S.p.A.", "sector": "Industrials"},
    {"ticker": "REC.MI", "company_name": "Recordati", "sector": "Healthcare"},
    {"ticker": "DIA.MI", "company_name": "DiaSorin", "sector": "Healthcare"},
    {"ticker": "AMP.MI", "company_name": "Amplifon", "sector": "Healthcare"},
    {"ticker": "INW.MI", "company_name": "Inwit", "sector": "Communication Services"},
    {"ticker": "BPE.MI", "company_name": "BPER Banca", "sector": "Financial Services"},
    {"ticker": "TEN.MI", "company_name": "Tenaris", "sector": "Energy"},
    {"ticker": "AZM.MI", "company_name": "Azimut Holding", "sector": "Financial Services"},
    {"ticker": "CPR.MI", "company_name": "Davide Campari-Milano", "sector": "Consumer Defensive"},
    {"ticker": "BGN.MI", "company_name": "Banca Generali", "sector": "Financial Services"},
    {"ticker": "HER.MI", "company_name": "Hera", "sector": "Utilities"},
    {"ticker": "A2A.MI", "company_name": "A2A", "sector": "Utilities"},
    {"ticker": "IG.MI", "company_name": "Italgas", "sector": "Utilities"},
    {"ticker": "ENV.MI", "company_name": "Enav", "sector": "Industrials"},
    {"ticker": "INTM.MI", "company_name": "Intermonte Partners SIM", "sector": "Financial Services"},
    {"ticker": "IVG.MI", "company_name": "Iveco Group", "sector": "Industrials"},
    {"ticker": "SPM.MI", "company_name": "Saipem", "sector": "Energy"},
    {"ticker": "DLG.MI", "company_name": "De'Longhi", "sector": "Consumer Cyclical"},
    {"ticker": "BC.MI", "company_name": "Brunello Cucinelli", "sector": "Consumer Cyclical"},
    {"ticker": "ERG.MI", "company_name": "Erg S.p.A.", "sector": "Utilities"},
    {"ticker": "INW.MI", "company_name": "Inwit", "sector": "Communication Services"},
]


# ---------------------------------------------------------------------------
# STOXX 600 fallback — subset 60 mega-cap europei (snapshot 2026)
# ---------------------------------------------------------------------------
# Coverage parziale: STOXX 600 ha 600 nomi, troppo per hardcodare. Il fallback
# copre solo i top mega-cap multi-paese. Sufficient come safety net runtime —
# l'utente vede comunque candidati validi se Wikipedia è giù temporaneamente.
STOXX600_FALLBACK: list[dict] = [
    # Tech
    {"ticker": "ASML.AS", "company_name": "ASML Holding", "sector": "Technology"},
    {"ticker": "SAP.DE", "company_name": "SAP SE", "sector": "Technology"},
    {"ticker": "STM.MI", "company_name": "STMicroelectronics", "sector": "Technology"},
    # Healthcare
    {"ticker": "NOVO-B.CO", "company_name": "Novo Nordisk", "sector": "Healthcare"},
    {"ticker": "AZN.L", "company_name": "AstraZeneca", "sector": "Healthcare"},
    {"ticker": "ROG.SW", "company_name": "Roche Holding", "sector": "Healthcare"},
    {"ticker": "NOVN.SW", "company_name": "Novartis", "sector": "Healthcare"},
    {"ticker": "SAN.PA", "company_name": "Sanofi", "sector": "Healthcare"},
    # Consumer
    {"ticker": "MC.PA", "company_name": "LVMH", "sector": "Consumer Cyclical"},
    {"ticker": "OR.PA", "company_name": "L'Oreal", "sector": "Consumer Defensive"},
    {"ticker": "NESN.SW", "company_name": "Nestle", "sector": "Consumer Defensive"},
    {"ticker": "RMS.PA", "company_name": "Hermes International", "sector": "Consumer Cyclical"},
    {"ticker": "KER.PA", "company_name": "Kering", "sector": "Consumer Cyclical"},
    {"ticker": "ULVR.L", "company_name": "Unilever", "sector": "Consumer Defensive"},
    {"ticker": "DGE.L", "company_name": "Diageo", "sector": "Consumer Defensive"},
    {"ticker": "ABI.BR", "company_name": "Anheuser-Busch InBev", "sector": "Consumer Defensive"},
    # Financials
    {"ticker": "HSBA.L", "company_name": "HSBC Holdings", "sector": "Financial Services"},
    {"ticker": "BNP.PA", "company_name": "BNP Paribas", "sector": "Financial Services"},
    {"ticker": "SAN.MC", "company_name": "Banco Santander", "sector": "Financial Services"},
    {"ticker": "DBK.DE", "company_name": "Deutsche Bank", "sector": "Financial Services"},
    {"ticker": "INGA.AS", "company_name": "ING Groep", "sector": "Financial Services"},
    {"ticker": "ALV.DE", "company_name": "Allianz", "sector": "Financial Services"},
    {"ticker": "AXAF.PA", "company_name": "AXA", "sector": "Financial Services"},
    {"ticker": "ZURN.SW", "company_name": "Zurich Insurance", "sector": "Financial Services"},
    {"ticker": "ISP.MI", "company_name": "Intesa Sanpaolo", "sector": "Financial Services"},
    {"ticker": "UCG.MI", "company_name": "UniCredit", "sector": "Financial Services"},
    # Energy
    {"ticker": "SHEL.L", "company_name": "Shell plc", "sector": "Energy"},
    {"ticker": "BP.L", "company_name": "BP plc", "sector": "Energy"},
    {"ticker": "TTE.PA", "company_name": "TotalEnergies", "sector": "Energy"},
    {"ticker": "ENI.MI", "company_name": "Eni S.p.A.", "sector": "Energy"},
    {"ticker": "EQNR.OL", "company_name": "Equinor", "sector": "Energy"},
    # Industrials
    {"ticker": "AIR.PA", "company_name": "Airbus", "sector": "Industrials"},
    {"ticker": "SIE.DE", "company_name": "Siemens", "sector": "Industrials"},
    {"ticker": "SU.PA", "company_name": "Schneider Electric", "sector": "Industrials"},
    {"ticker": "ABBN.SW", "company_name": "ABB Ltd", "sector": "Industrials"},
    {"ticker": "DSV.CO", "company_name": "DSV", "sector": "Industrials"},
    # Materials
    {"ticker": "RIO.L", "company_name": "Rio Tinto", "sector": "Basic Materials"},
    {"ticker": "GLEN.L", "company_name": "Glencore", "sector": "Basic Materials"},
    {"ticker": "BHP.L", "company_name": "BHP Group", "sector": "Basic Materials"},
    {"ticker": "BAS.DE", "company_name": "BASF", "sector": "Basic Materials"},
    # Utilities
    {"ticker": "IBE.MC", "company_name": "Iberdrola", "sector": "Utilities"},
    {"ticker": "ENEL.MI", "company_name": "Enel S.p.A.", "sector": "Utilities"},
    {"ticker": "EOAN.DE", "company_name": "E.ON", "sector": "Utilities"},
    # Communications
    {"ticker": "DTE.DE", "company_name": "Deutsche Telekom", "sector": "Communication Services"},
    {"ticker": "VOD.L", "company_name": "Vodafone Group", "sector": "Communication Services"},
    # Auto / Luxury
    {"ticker": "VOW3.DE", "company_name": "Volkswagen AG", "sector": "Consumer Cyclical"},
    {"ticker": "BMW.DE", "company_name": "BMW", "sector": "Consumer Cyclical"},
    {"ticker": "MBG.DE", "company_name": "Mercedes-Benz Group", "sector": "Consumer Cyclical"},
    {"ticker": "STLAM.MI", "company_name": "Stellantis", "sector": "Consumer Cyclical"},
    {"ticker": "RACE.MI", "company_name": "Ferrari N.V.", "sector": "Consumer Cyclical"},
]


# ---------------------------------------------------------------------------
# Nasdaq-100 fallback — top 30 mega-cap tech (snapshot 2026)
# ---------------------------------------------------------------------------
# Coverage parziale ma sufficient come safety net. Nasdaq-100 ha ~100 nomi
# tech-heavy, overlap forte con SP500_FALLBACK; qui evitiamo duplicazione
# limitando ai mega-cap incontestati. Aggiornare ~annualmente.
NASDAQ100_FALLBACK: list[dict] = [
    {"ticker": "AAPL", "company_name": "Apple Inc.", "sector": "Technology"},
    {"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Technology"},
    {"ticker": "NVDA", "company_name": "NVIDIA Corp.", "sector": "Technology"},
    {"ticker": "AMZN", "company_name": "Amazon.com Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "META", "company_name": "Meta Platforms", "sector": "Communication Services"},
    {"ticker": "GOOGL", "company_name": "Alphabet Inc. Class A", "sector": "Communication Services"},
    {"ticker": "GOOG", "company_name": "Alphabet Inc. Class C", "sector": "Communication Services"},
    {"ticker": "TSLA", "company_name": "Tesla Inc.", "sector": "Consumer Cyclical"},
    {"ticker": "AVGO", "company_name": "Broadcom Inc.", "sector": "Technology"},
    {"ticker": "COST", "company_name": "Costco Wholesale", "sector": "Consumer Defensive"},
    {"ticker": "NFLX", "company_name": "Netflix Inc.", "sector": "Communication Services"},
    {"ticker": "ADBE", "company_name": "Adobe Inc.", "sector": "Technology"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices", "sector": "Technology"},
    {"ticker": "PEP", "company_name": "PepsiCo Inc.", "sector": "Consumer Defensive"},
    {"ticker": "CSCO", "company_name": "Cisco Systems", "sector": "Technology"},
    {"ticker": "TMUS", "company_name": "T-Mobile US", "sector": "Communication Services"},
    {"ticker": "INTC", "company_name": "Intel Corp.", "sector": "Technology"},
    {"ticker": "QCOM", "company_name": "Qualcomm Inc.", "sector": "Technology"},
    {"ticker": "LIN", "company_name": "Linde plc", "sector": "Basic Materials"},
    {"ticker": "AMAT", "company_name": "Applied Materials", "sector": "Technology"},
    {"ticker": "TXN", "company_name": "Texas Instruments", "sector": "Technology"},
    {"ticker": "AMGN", "company_name": "Amgen Inc.", "sector": "Healthcare"},
    {"ticker": "INTU", "company_name": "Intuit Inc.", "sector": "Technology"},
    {"ticker": "ISRG", "company_name": "Intuitive Surgical", "sector": "Healthcare"},
    {"ticker": "BKNG", "company_name": "Booking Holdings", "sector": "Consumer Cyclical"},
    {"ticker": "MU", "company_name": "Micron Technology", "sector": "Technology"},
    {"ticker": "ADI", "company_name": "Analog Devices", "sector": "Technology"},
    {"ticker": "LRCX", "company_name": "Lam Research", "sector": "Technology"},
    {"ticker": "SBUX", "company_name": "Starbucks Corp.", "sector": "Consumer Cyclical"},
    {"ticker": "MDLZ", "company_name": "Mondelez International", "sector": "Consumer Defensive"},
    {"ticker": "GILD", "company_name": "Gilead Sciences", "sector": "Healthcare"},
    {"ticker": "ADP", "company_name": "Automatic Data Processing", "sector": "Industrials"},
    {"ticker": "PANW", "company_name": "Palo Alto Networks", "sector": "Technology"},
    {"ticker": "REGN", "company_name": "Regeneron Pharmaceuticals", "sector": "Healthcare"},
    {"ticker": "VRTX", "company_name": "Vertex Pharmaceuticals", "sector": "Healthcare"},
]


# ---------------------------------------------------------------------------
# Wikipedia fetcher
# ---------------------------------------------------------------------------
# Wikipedia blocca le request senza User-Agent custom (HTTP 403 dal 2024).
# Identifichiamoci con un UA descrittivo per rispettare la policy
# https://meta.wikimedia.org/wiki/User-Agent_policy
_WIKIPEDIA_UA: str = (
    "PropicksAI/0.1 (https://github.com/valeriouberti/propicks-ai-framework; "
    "valerio.uberti23@gmail.com) python-urllib"
)
_WIKIPEDIA_TIMEOUT_SECONDS: float = 20.0


def _read_wikipedia_tables(url: str) -> list[pd.DataFrame]:
    """Fetch HTML con UA custom e parse via ``pd.read_html``.

    ``pandas.read_html`` di default fa una request senza User-Agent custom,
    che Wikipedia da qualche tempo blocca con 403. Pre-fetchiamo via
    ``urllib`` con UA descrittivo, poi passiamo l'HTML come stringa al parser.
    Richiede ``lxml`` installato (dichiarato nelle dependencies).
    """
    req = urllib.request.Request(url, headers={"User-Agent": _WIKIPEDIA_UA})
    try:
        with urllib.request.urlopen(req, timeout=_WIKIPEDIA_TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Wikipedia HTTP error {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Wikipedia URL error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ValueError(f"Wikipedia fetch timeout (>{_WIKIPEDIA_TIMEOUT_SECONDS}s)") from exc

    try:
        return pd.read_html(StringIO(html))
    except ImportError as exc:
        raise ValueError(
            "pd.read_html parser missing — install 'lxml' "
            "(declared in pyproject dependencies)"
        ) from exc
    except ValueError as exc:
        raise ValueError(f"Wikipedia HTML parse failed: {exc}") from exc


def _normalize_yf_ticker(symbol: str) -> str:
    """Normalizza ticker Wikipedia → yfinance.

    Wikipedia usa la notazione "BRK.B" (dot per share class), yfinance
    richiede "BRK-B" (dash). Stessa cosa per BF.B → BF-B, etc.
    Strip whitespace per safety.
    """
    return symbol.strip().replace(".", "-")


def _fetch_sp500_from_wikipedia() -> list[dict]:
    """Fetch + parse della tabella S&P 500 da Wikipedia.

    Ritorna list[dict] con keys: ticker, company_name, sector, added_date.
    Solleva ValueError se la pagina non ha la struttura attesa o ritorna
    troppo pochi nomi (sanity check su INDEX_MIN_CONSTITUENTS).
    """
    tables = _read_wikipedia_tables(SP500_WIKIPEDIA_URL)
    if not tables:
        raise ValueError("Wikipedia returned no tables")

    df = tables[0]

    # Schema Wikipedia (stabile da anni): "Symbol", "Security", "GICS Sector",
    # "Date added". Difensivi su rename in caso di future changes.
    required_cols = {"Symbol", "Security"}
    if not required_cols.issubset(df.columns):
        raise ValueError(
            f"Wikipedia table missing expected columns. "
            f"Got: {list(df.columns)[:10]}"
        )

    rows: list[dict] = []
    for _, row in df.iterrows():
        symbol = row.get("Symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        rows.append(
            {
                "ticker": _normalize_yf_ticker(symbol),
                "company_name": row.get("Security") if isinstance(row.get("Security"), str) else None,
                "sector": row.get("GICS Sector") if isinstance(row.get("GICS Sector"), str) else None,
                "added_date": row.get("Date added") if isinstance(row.get("Date added"), str) else None,
            }
        )

    if len(rows) < INDEX_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia returned only {len(rows)} constituents "
            f"(min expected: {INDEX_MIN_CONSTITUENTS}) — table format may have changed"
        )

    return rows


def _normalize_ftsemib_ticker(symbol: str) -> str:
    """FTSE MIB Wikipedia → yfinance: aggiunge ``.MI`` se mancante.

    Su Wikipedia i ticker MIB sono spesso scritti senza suffisso (es. "ENI",
    "UCG"). yfinance richiede ".MI" per la Borsa Italiana. Se il ticker già
    contiene un suffisso (es. ".MI" già presente da scraping diverso), lo
    preserviamo per idempotenza.
    """
    s = symbol.strip().upper().replace(".", "-")
    if "-" in s or "." in symbol:
        # Già con suffisso (raro ma possibile su tabelle non-canoniche)
        return symbol.strip().upper()
    return f"{s}.MI"


def _fetch_ftsemib_from_wikipedia() -> list[dict]:
    """Fetch + parse della tabella FTSE MIB da Wikipedia.

    La pagina ha una sezione "Components" con una tabella (tipicamente
    indice 1 o 2 perché la prima è l'infobox). Strategia robusta: cerchiamo
    la tabella che ha ``Ticker`` o ``ISIN`` come colonna e contiene almeno
    35 righe.
    """
    tables = _read_wikipedia_tables(FTSEMIB_WIKIPEDIA_URL)
    if not tables:
        raise ValueError("Wikipedia FTSE MIB returned no tables")

    # Trova la tabella Components (prima con colonna Ticker o ISIN)
    components_df: pd.DataFrame | None = None
    for df in tables:
        cols = {str(c) for c in df.columns}
        has_ticker = any(c in cols for c in ("Ticker", "Symbol", "ISIN"))
        if has_ticker and len(df) >= FTSEMIB_MIN_CONSTITUENTS:
            components_df = df
            break

    if components_df is None:
        raise ValueError(
            "Wikipedia FTSE MIB: no table found with Ticker/Symbol column "
            f"and ≥ {FTSEMIB_MIN_CONSTITUENTS} rows"
        )

    # Determina la colonna ticker
    ticker_col = next(
        (c for c in ("Ticker", "Symbol") if c in components_df.columns),
        None,
    )
    if ticker_col is None:
        raise ValueError("Wikipedia FTSE MIB: ticker column not identified")

    name_col = next(
        (c for c in ("Company", "Name", "Security") if c in components_df.columns),
        None,
    )
    sector_col = next(
        (c for c in ("ICB Sector", "Sector", "Industry") if c in components_df.columns),
        None,
    )

    rows: list[dict] = []
    for _, row in components_df.iterrows():
        sym = row.get(ticker_col)
        if not isinstance(sym, str) or not sym.strip():
            continue
        rows.append(
            {
                "ticker": _normalize_ftsemib_ticker(sym),
                "company_name": (
                    row.get(name_col) if name_col and isinstance(row.get(name_col), str) else None
                ),
                "sector": (
                    row.get(sector_col)
                    if sector_col and isinstance(row.get(sector_col), str)
                    else None
                ),
                "added_date": None,
            }
        )

    if len(rows) < FTSEMIB_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia FTSE MIB returned only {len(rows)} constituents "
            f"(min expected: {FTSEMIB_MIN_CONSTITUENTS})"
        )

    return rows


def _fetch_stoxx600_from_wikipedia() -> list[dict]:
    """Fetch + parse della tabella STOXX 600 da Wikipedia.

    La pagina ha una tabella "Components" molto larga (~600 righe). I
    ticker su Wikipedia sono già con il suffisso giusto per yfinance
    (ENI.MI, ASML.AS, etc.), quindi nessuna normalizzazione magica —
    solo strip + upper.

    Schema atteso: colonne ``Ticker`` (o ``Symbol``) e ``Company`` (o
    ``Name``). Sector può essere ``ICB Industry`` o ``Sector``.
    """
    tables = _read_wikipedia_tables(STOXX600_WIKIPEDIA_URL)
    if not tables:
        raise ValueError("Wikipedia STOXX 600 returned no tables")

    # La tabella components è la più grande (~600 righe)
    components_df = max(tables, key=lambda df: len(df))
    if len(components_df) < STOXX600_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia STOXX 600 largest table has only "
            f"{len(components_df)} rows (min expected: {STOXX600_MIN_CONSTITUENTS})"
        )

    # Identifica colonne dinamicamente (lo schema può cambiare leggermente)
    cols = {str(c) for c in components_df.columns}
    ticker_col = next(
        (c for c in ("Ticker", "Symbol", "RIC") if c in cols),
        None,
    )
    if ticker_col is None:
        raise ValueError(
            f"Wikipedia STOXX 600: no ticker column found. Got: {list(cols)[:10]}"
        )

    name_col = next(
        (c for c in ("Company", "Name", "Security") if c in cols),
        None,
    )
    sector_col = next(
        (c for c in ("ICB Industry", "Sector", "Industry", "ICB Sector") if c in cols),
        None,
    )

    rows: list[dict] = []
    for _, row in components_df.iterrows():
        sym = row.get(ticker_col)
        if not isinstance(sym, str) or not sym.strip():
            continue
        # Strip + upper. Su Wikipedia STOXX 600 i ticker sono già con suffisso
        # corretto (ENI.MI, ASML.AS, ...). Non normalizziamo dot→dash perché
        # quello rovinerebbe i suffissi exchange.
        ticker = sym.strip().upper()
        rows.append(
            {
                "ticker": ticker,
                "company_name": (
                    row.get(name_col)
                    if name_col and isinstance(row.get(name_col), str)
                    else None
                ),
                "sector": (
                    row.get(sector_col)
                    if sector_col and isinstance(row.get(sector_col), str)
                    else None
                ),
                "added_date": None,
            }
        )

    if len(rows) < STOXX600_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia STOXX 600 returned only {len(rows)} constituents "
            f"(min expected: {STOXX600_MIN_CONSTITUENTS})"
        )

    return rows


def _fetch_nasdaq100_from_wikipedia() -> list[dict]:
    """Fetch + parse della tabella Nasdaq-100 da Wikipedia.

    La pagina ha una sezione "Components" con tabella ~100 righe. Le colonne
    canoniche sono ``Ticker`` (o ``Symbol``) e ``Company`` + ``GICS Sector``.
    Strategia: prima tabella con colonna Ticker/Symbol e ≥ min_constituents.
    Ticker sono nativi yfinance (no suffix exchange), nessuna normalizzazione
    speciale richiesta oltre strip/upper.
    """
    tables = _read_wikipedia_tables(NASDAQ100_WIKIPEDIA_URL)
    if not tables:
        raise ValueError("Wikipedia Nasdaq-100 returned no tables")

    components_df: pd.DataFrame | None = None
    for df in tables:
        cols = {str(c) for c in df.columns}
        has_ticker = any(c in cols for c in ("Ticker", "Symbol"))
        if has_ticker and len(df) >= NASDAQ100_MIN_CONSTITUENTS:
            components_df = df
            break

    if components_df is None:
        raise ValueError(
            "Wikipedia Nasdaq-100: no table found with Ticker/Symbol column "
            f"and ≥ {NASDAQ100_MIN_CONSTITUENTS} rows"
        )

    ticker_col = next(
        (c for c in ("Ticker", "Symbol") if c in components_df.columns), None
    )
    if ticker_col is None:
        raise ValueError("Wikipedia Nasdaq-100: ticker column not identified")

    name_col = next(
        (c for c in ("Company", "Security", "Name") if c in components_df.columns), None
    )
    sector_col = next(
        (c for c in ("GICS Sector", "Sector", "Industry") if c in components_df.columns),
        None,
    )

    rows: list[dict] = []
    for _, row in components_df.iterrows():
        sym = row.get(ticker_col)
        if not isinstance(sym, str) or not sym.strip():
            continue
        rows.append(
            {
                "ticker": _normalize_yf_ticker(sym),
                "company_name": (
                    row.get(name_col)
                    if name_col and isinstance(row.get(name_col), str)
                    else None
                ),
                "sector": (
                    row.get(sector_col)
                    if sector_col and isinstance(row.get(sector_col), str)
                    else None
                ),
                "added_date": None,
            }
        )

    if len(rows) < NASDAQ100_MIN_CONSTITUENTS:
        raise ValueError(
            f"Wikipedia Nasdaq-100 returned only {len(rows)} constituents "
            f"(min expected: {NASDAQ100_MIN_CONSTITUENTS})"
        )

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# Registry interno: mappa index_name → (fetch_fn, fallback_snapshot, min_constituents)
# Aggiungere nuovi indici qui registrandoli — il dispatcher generico li serve
# automaticamente senza ripetere la fallback chain.
_INDEX_REGISTRY: dict[str, dict] = {
    INDEX_NAME_SP500: {
        "fetch_fn": lambda: _fetch_sp500_from_wikipedia(),
        "fallback": SP500_FALLBACK,
        "min_constituents": INDEX_MIN_CONSTITUENTS,
        "label": "S&P 500",
    },
    INDEX_NAME_FTSEMIB: {
        "fetch_fn": lambda: _fetch_ftsemib_from_wikipedia(),
        "fallback": FTSEMIB_FALLBACK,
        "min_constituents": FTSEMIB_MIN_CONSTITUENTS,
        "label": "FTSE MIB",
    },
    INDEX_NAME_STOXX600: {
        "fetch_fn": lambda: _fetch_stoxx600_from_wikipedia(),
        "fallback": STOXX600_FALLBACK,
        "min_constituents": STOXX600_MIN_CONSTITUENTS,
        "label": "STOXX Europe 600",
    },
    INDEX_NAME_NASDAQ100: {
        "fetch_fn": lambda: _fetch_nasdaq100_from_wikipedia(),
        "fallback": NASDAQ100_FALLBACK,
        "min_constituents": NASDAQ100_MIN_CONSTITUENTS,
        "label": "Nasdaq-100",
    },
}


def _get_universe_detailed(
    index_name: str,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Read-through con fallback chain — implementazione condivisa.

    Layers:
    1. Cache SQLite fresh (TTL 7gg) entro min_constituents
    2. Wikipedia fetch + UPSERT
    3. Cache SQLite stale (anche se TTL scaduto)
    4. Snapshot hardcoded (mai persistito)
    """
    if index_name not in _INDEX_REGISTRY:
        raise ValueError(
            f"Index '{index_name}' non supportato. "
            f"Disponibili: {sorted(_INDEX_REGISTRY.keys())}"
        )
    spec = _INDEX_REGISTRY[index_name]
    min_n = spec["min_constituents"]

    # Fast path: cache fresh
    if not force_refresh and index_constituents_is_fresh(
        index_name, INDEX_CONSTITUENTS_CACHE_TTL_HOURS
    ):
        cached = index_constituents_read(index_name)
        if cached and len(cached) >= min_n:
            _log.debug(
                "index_cache_hit",
                extra={"ctx": {"index": index_name, "n_tickers": len(cached)}},
            )
            return cached

    # Miss / stale / force: fetch Wikipedia
    try:
        fresh_rows = spec["fetch_fn"]()
        index_constituents_replace(index_name, fresh_rows)
        _log.info(
            "index_wikipedia_fetched",
            extra={"ctx": {"index": index_name, "n_tickers": len(fresh_rows)}},
        )
        return fresh_rows
    except ValueError as exc:
        _log.warning(
            "index_wikipedia_fetch_failed",
            extra={"ctx": {"index": index_name, "error": str(exc)}},
        )

    # Fallback layer 1: cache esistente (anche se stale)
    cached = index_constituents_read(index_name)
    if cached:
        _log.warning(
            "index_using_stale_cache",
            extra={"ctx": {"index": index_name, "n_tickers": len(cached)}},
        )
        return cached

    # Fallback layer 2: hardcoded snapshot (mai persistito)
    fallback = spec["fallback"]
    _log.warning(
        "index_using_hardcoded_snapshot",
        extra={"ctx": {"index": index_name, "n_tickers": len(fallback)}},
    )
    return list(fallback)


def get_index_universe(
    name: str,
    *,
    force_refresh: bool = False,
) -> list[str]:
    """Dispatcher generico: ritorna la lista di ticker per un index.

    Args:
        name: uno dei valori in ``SUPPORTED_INDEXES`` (sp500/ftsemib/stoxx600).
        force_refresh: bypass cache e forza re-fetch da Wikipedia.

    Raises:
        ValueError: se ``name`` non è un index supportato.
    """
    return [r["ticker"] for r in _get_universe_detailed(name, force_refresh=force_refresh)]


def get_index_universe_detailed(
    name: str,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Variante di ``get_index_universe`` con metadata completa per dashboard."""
    return _get_universe_detailed(name, force_refresh=force_refresh)


# ---------------------------------------------------------------------------
# Backward-compatible per-index wrappers (preservati dall'API originale)
# ---------------------------------------------------------------------------
def get_sp500_universe_detailed(*, force_refresh: bool = False) -> list[dict]:
    """S&P 500 con metadata. Wrapper sul dispatcher generico (backward-compat)."""
    return _get_universe_detailed(INDEX_NAME_SP500, force_refresh=force_refresh)


def get_sp500_universe(*, force_refresh: bool = False) -> list[str]:
    """S&P 500 ticker list. Wrapper sul dispatcher generico (backward-compat)."""
    return get_index_universe(INDEX_NAME_SP500, force_refresh=force_refresh)


def get_ftsemib_universe(*, force_refresh: bool = False) -> list[str]:
    """FTSE MIB ticker list (.MI suffix per yfinance)."""
    return get_index_universe(INDEX_NAME_FTSEMIB, force_refresh=force_refresh)


def get_ftsemib_universe_detailed(*, force_refresh: bool = False) -> list[dict]:
    """FTSE MIB con metadata."""
    return _get_universe_detailed(INDEX_NAME_FTSEMIB, force_refresh=force_refresh)


def get_stoxx600_universe(*, force_refresh: bool = False) -> list[str]:
    """STOXX Europe 600 ticker list (multi-suffix: .L .DE .PA .AS .SW .MI ...)."""
    return get_index_universe(INDEX_NAME_STOXX600, force_refresh=force_refresh)


def get_stoxx600_universe_detailed(*, force_refresh: bool = False) -> list[dict]:
    """STOXX 600 con metadata."""
    return _get_universe_detailed(INDEX_NAME_STOXX600, force_refresh=force_refresh)


def get_nasdaq100_universe(*, force_refresh: bool = False) -> list[str]:
    """Nasdaq-100 ticker list (US tech-heavy, no exchange suffix)."""
    return get_index_universe(INDEX_NAME_NASDAQ100, force_refresh=force_refresh)


def get_nasdaq100_universe_detailed(*, force_refresh: bool = False) -> list[dict]:
    """Nasdaq-100 con metadata."""
    return _get_universe_detailed(INDEX_NAME_NASDAQ100, force_refresh=force_refresh)


def index_label(name: str) -> str:
    """Etichetta human-readable per un index name (UI display)."""
    return _INDEX_REGISTRY.get(name, {}).get("label", name)
