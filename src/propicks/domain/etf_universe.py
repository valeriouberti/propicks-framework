"""Query helpers sull'universo ETF settoriali.

Layer puro: legge solo i dict in ``propicks.config`` e restituisce dati
derivati. Niente rete, niente I/O. Usato dallo scoring engine ETF (Fase 2)
e dalla CLI per decidere il branch stock vs ETF a partire dal ticker.

Convenzione: i ticker sono normalizzati in uppercase. I suffissi exchange
(``.DE``, ``.MI``) sono preservati — ``EXV3.DE`` e ``EXV3`` sono ticker
diversi perché identificano listing diversi.
"""

from __future__ import annotations

from typing import Literal

from propicks.config import (
    REGIME_FAVORED_SECTORS,
    SECTOR_ETFS_US,
    SECTOR_ETFS_WORLD,
    THEMATIC_ETFS,
    AssetType,
)

Region = Literal["US", "WORLD", "ALL"]


def get_asset_type(ticker: str) -> AssetType:
    """Classifica il ticker come STOCK / SECTOR_ETF / THEMATIC_ETF.

    Precedenza: THEMATIC_ETFS prima di SECTOR_ETFS_*. Mai un thematic dovrebbe
    essere registrato anche come sector parent (validato da test_thematic_universe),
    ma controllo prima per safety.

    Commodity ETF non sono ancora registrati — ritornano ``STOCK`` finché
    non viene aggiunto ``COMMODITY_ETFS`` in config (Fase commodity).
    """
    t = ticker.upper()
    if t in THEMATIC_ETFS:
        return "THEMATIC_ETF"
    if t in SECTOR_ETFS_US or t in SECTOR_ETFS_WORLD:
        return "SECTOR_ETF"
    return "STOCK"


def get_sector_key(ticker: str) -> str | None:
    """Ritorna il ``sector_key`` GICS-normalizzato del ticker, o None se non ETF."""
    t = ticker.upper()
    if t in SECTOR_ETFS_US:
        return SECTOR_ETFS_US[t]["sector_key"]
    if t in SECTOR_ETFS_WORLD:
        return SECTOR_ETFS_WORLD[t]["sector_key"]
    return None


def get_etf_info(ticker: str) -> dict | None:
    """Ritorna il dict metadata completo del ticker ETF (name, sector)."""
    t = ticker.upper()
    if t in SECTOR_ETFS_US:
        return {"ticker": t, "region": "US", **SECTOR_ETFS_US[t]}
    if t in SECTOR_ETFS_WORLD:
        return {"ticker": t, "region": "WORLD", **SECTOR_ETFS_WORLD[t]}
    return None


def resolve_sector_key(
    ticker: str,
    yahoo_sector_raw: str | None = None,
) -> str | None:
    """Risolve il ``sector_key`` GICS-normalizzato del ticker con priorità.

    Order of resolution (config-first, Yahoo fallback):

    1. **Thematic ETF** registrato in ``THEMATIC_ETFS``: eredita
       ``parent_sector_key`` dal parent. Coerente col regime fit lookup
       (tematico LOCK.MI → tech come parent XDWT.MI).
    2. **Sector ETF** registrato in ``SECTOR_ETFS_US/WORLD``: usa
       ``sector_key`` autoritativo dal config (es. XLK → technology).
    3. **Stock** o ticker non registrato: usa il mapping Yahoo
       (``yahoo_sector_raw`` passato dal chiamante via ``get_ticker_sector``)
       attraverso ``YF_SECTOR_TO_KEY``.

    Args:
        ticker: ticker (case-insensitive).
        yahoo_sector_raw: stringa sector raw da yfinance (es. "Technology",
            "Financial Services", "Consumer Cyclical"). Passare None se non
            disponibile — il resolver usa solo i lookup config.

    Returns:
        ``sector_key`` GICS-normalizzato, o None se non risolvibile.

    Razionale: Yahoo restituisce "Financial Services" per ETF UCITS
    (mismatch con tassonomia interna "financials") e null per molti
    thematic .MI. La risoluzione config-first garantisce coerenza
    con ``REGIME_FAVORED_SECTORS`` e gli scoring engine.
    """
    t = ticker.upper()

    # 1. Thematic — eredita dal parent
    if t in THEMATIC_ETFS:
        return THEMATIC_ETFS[t].get("parent_sector_key")

    # 2. Sector ETF — sector_key da config
    if t in SECTOR_ETFS_US:
        return SECTOR_ETFS_US[t]["sector_key"]
    if t in SECTOR_ETFS_WORLD:
        return SECTOR_ETFS_WORLD[t]["sector_key"]

    # 3. Stock — Yahoo mapping (chiamante deve aver fetchato yahoo_sector_raw)
    if yahoo_sector_raw is None:
        return None
    from propicks.domain.stock_rs import YF_SECTOR_TO_KEY
    return YF_SECTOR_TO_KEY.get(yahoo_sector_raw)


def favored_sectors_for_regime(regime_code: int) -> tuple[str, ...]:
    """Lista dei ``sector_key`` favoriti per il regime weekly dato.

    ``regime_code`` segue la scala di ``domain.regime`` (1=STRONG_BEAR,
    5=STRONG_BULL). Regime non riconosciuto → tupla vuota.
    """
    return REGIME_FAVORED_SECTORS.get(regime_code, ())


def is_favored(ticker: str, regime_code: int) -> bool:
    """True se il ticker ETF è nei settori favoriti per il regime.

    Ritorna False se il ticker non è un ETF mappato — lo stock scoring
    non passa da qui. Usato dallo scoring ETF come input al sotto-score
    *regime fit*.
    """
    sector = get_sector_key(ticker)
    if sector is None:
        return False
    return sector in favored_sectors_for_regime(regime_code)


def list_universe(region: Region = "WORLD") -> list[dict]:
    """Elenca gli ETF dell'universo con metadata completo.

    ``region`` filtra per listing:
        - ``US``     = Select Sector SPDR (XL*) — reference, lunga storia
        - ``WORLD``  = Xtrackers MSCI World sector (XDW*.DE / XWTS / XZRE +
                       .MI Borsa Italiana) — universe operativo retail EU
        - ``ALL``    = US + WORLD (mescolare benchmark è sconsigliato)

    Output ordinato per sector_key poi ticker per stabilità in test e CLI.
    Default ``WORLD`` allineato al broker retail (Borsa Italiana).

    NOTA: mescolare US con WORLD nello stesso ranking è sconsigliato —
    il benchmark RS cambia (``^GSPC`` US, ``URTH`` WORLD). ``rank_universe``
    gestisce la scelta automatica.
    """
    rows: list[dict] = []
    if region in ("US", "ALL"):
        for ticker, meta in SECTOR_ETFS_US.items():
            rows.append({"ticker": ticker, "region": "US", **meta})
    if region in ("WORLD", "ALL"):
        for ticker, meta in SECTOR_ETFS_WORLD.items():
            rows.append({"ticker": ticker, "region": "WORLD", **meta})
    rows.sort(key=lambda r: (r["sector_key"], r["ticker"]))
    return rows
