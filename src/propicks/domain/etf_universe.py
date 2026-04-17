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
    SECTOR_ETFS_EU,
    SECTOR_ETFS_US,
    SECTOR_ETFS_WORLD,
    AssetType,
)

Region = Literal["US", "EU", "WORLD", "ALL"]


def get_asset_type(ticker: str) -> AssetType:
    """Classifica il ticker come STOCK o SECTOR_ETF.

    Commodity ETF non sono ancora registrati — ritornano ``STOCK`` finché
    non viene aggiunto ``COMMODITY_ETFS`` in config (Fase commodity).
    """
    t = ticker.upper()
    if t in SECTOR_ETFS_US or t in SECTOR_ETFS_EU or t in SECTOR_ETFS_WORLD:
        return "SECTOR_ETF"
    return "STOCK"


def get_sector_key(ticker: str) -> str | None:
    """Ritorna il ``sector_key`` GICS-normalizzato del ticker, o None se non ETF."""
    t = ticker.upper()
    if t in SECTOR_ETFS_US:
        return SECTOR_ETFS_US[t]["sector_key"]
    if t in SECTOR_ETFS_EU:
        return SECTOR_ETFS_EU[t]["sector_key"]
    if t in SECTOR_ETFS_WORLD:
        return SECTOR_ETFS_WORLD[t]["sector_key"]
    return None


def get_etf_info(ticker: str) -> dict | None:
    """Ritorna il dict metadata completo del ticker ETF (name, sector, equivalente)."""
    t = ticker.upper()
    if t in SECTOR_ETFS_US:
        return {"ticker": t, "region": "US", **SECTOR_ETFS_US[t]}
    if t in SECTOR_ETFS_EU:
        return {"ticker": t, "region": "EU", **SECTOR_ETFS_EU[t]}
    if t in SECTOR_ETFS_WORLD:
        return {"ticker": t, "region": "WORLD", **SECTOR_ETFS_WORLD[t]}
    return None


def get_eu_equivalent(us_ticker: str) -> str | None:
    """Ritorna il ticker EU equivalente di un SPDR US, o None se non mappato."""
    info = SECTOR_ETFS_US.get(us_ticker.upper())
    if info is None:
        return None
    return info.get("eu_equivalent")


def get_us_equivalent(eu_ticker: str) -> str | None:
    """Ritorna il ticker US equivalente di un UCITS EU, o None se non mappato."""
    info = SECTOR_ETFS_EU.get(eu_ticker.upper())
    if info is None:
        return None
    return info.get("us_equivalent")


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


def list_universe(region: Region = "ALL") -> list[dict]:
    """Elenca gli ETF dell'universo con metadata completo.

    ``region`` filtra per listing:
        - ``US``     = Select Sector SPDR (XL*)
        - ``EU``     = SPDR UCITS wrapper (ZPD*.DE), stesso indice US
        - ``WORLD``  = Xtrackers MSCI World sector (XDW*.DE / XWTS / XZRE)
        - ``ALL``    = tutti (attenzione: benchmark RS non uniforme)

    Output ordinato per sector_key poi ticker per stabilità in test e CLI.

    NOTA: mescolare US/EU con WORLD nello stesso ranking è sconsigliato —
    il benchmark RS cambia per region (``^GSPC`` per US/EU, ``URTH`` per
    WORLD). ``rank_universe`` gestisce la scelta automatica.
    """
    rows: list[dict] = []
    if region in ("US", "ALL"):
        for ticker, meta in SECTOR_ETFS_US.items():
            rows.append({"ticker": ticker, "region": "US", **meta})
    if region in ("EU", "ALL"):
        for ticker, meta in SECTOR_ETFS_EU.items():
            rows.append({"ticker": ticker, "region": "EU", **meta})
    if region in ("WORLD", "ALL"):
        for ticker, meta in SECTOR_ETFS_WORLD.items():
            rows.append({"ticker": ticker, "region": "WORLD", **meta})
    rows.sort(key=lambda r: (r["sector_key"], r["ticker"]))
    return rows
