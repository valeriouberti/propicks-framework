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
