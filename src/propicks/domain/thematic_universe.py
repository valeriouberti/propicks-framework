"""Query helpers sull'universo Thematic ETF.

Layer puro: legge solo ``propicks.config.THEMATIC_ETFS`` e i sector ETF
universes. Niente rete, niente I/O. Fornisce lookup parent + classificazione
asset_type esteso ("THEMATIC_ETF") usato dallo scoring engine tematico e
dal router CLI.

Convenzione: ticker normalizzati uppercase. Suffissi exchange (.DE, .MI)
preservati — listing diversi sono ticker diversi.
"""

from __future__ import annotations

from typing import Literal

from propicks.config import (
    SECTOR_ETFS_EU,
    SECTOR_ETFS_US,
    SECTOR_ETFS_WORLD,
    THEMATIC_ETFS,
)

ThemeRegion = Literal["US", "EU", "WORLD", "ALL"]


def is_thematic(ticker: str) -> bool:
    """True se il ticker è registrato come thematic ETF."""
    return ticker.upper() in THEMATIC_ETFS


def get_thematic_info(ticker: str) -> dict | None:
    """Metadata completo del tematico (name, theme_label, parent, region) o None."""
    t = ticker.upper()
    info = THEMATIC_ETFS.get(t)
    if info is None:
        return None
    return {"ticker": t, **info}


def get_parent_ticker(ticker: str) -> str | None:
    """Parent sector ETF ticker (per RS theme/parent). None se non tematico."""
    info = THEMATIC_ETFS.get(ticker.upper())
    if info is None:
        return None
    return info.get("parent_ticker")


def get_parent_sector_key(ticker: str) -> str | None:
    """Sector_key GICS del parent (per regime_fit lookup). None se non tematico."""
    info = THEMATIC_ETFS.get(ticker.upper())
    if info is None:
        return None
    return info.get("parent_sector_key")


def get_theme_label(ticker: str) -> str | None:
    """Etichetta tematica (semis, biotech, cybersec, ...). None se non tematico."""
    info = THEMATIC_ETFS.get(ticker.upper())
    if info is None:
        return None
    return info.get("theme_label")


def list_universe(region: ThemeRegion = "ALL") -> list[dict]:
    """Elenca i tematici filtrati per region (region del LISTING tematico).

    Output ordinato per (theme_label, ticker) per stabilità test/CLI.
    """
    rows: list[dict] = []
    for ticker, meta in THEMATIC_ETFS.items():
        if region == "ALL" or meta.get("region") == region:
            rows.append({"ticker": ticker, **meta})
    rows.sort(key=lambda r: (r.get("theme_label", ""), r["ticker"]))
    return rows


def list_themes() -> list[str]:
    """Lista distinct dei theme_label per filtro CLI."""
    seen = set()
    out: list[str] = []
    for meta in THEMATIC_ETFS.values():
        label = meta.get("theme_label")
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return sorted(out)


def list_universe_by_theme(theme_label: str) -> list[dict]:
    """Tutti i tematici con dato theme_label (es. 'biotech' → [XBI, IBB])."""
    rows = [
        {"ticker": t, **meta}
        for t, meta in THEMATIC_ETFS.items()
        if meta.get("theme_label") == theme_label
    ]
    rows.sort(key=lambda r: r["ticker"])
    return rows


def parent_exists_in_universe(ticker: str) -> bool:
    """Sanity check: parent_ticker del tematico è registrato in SECTOR_ETFS_*.

    Usato dai test di consistenza (test_thematic_universe.py) per garantire
    che ogni tematico abbia un parent valido — senza, lo scoring RS-vs-parent
    fallirebbe runtime.
    """
    parent = get_parent_ticker(ticker)
    if parent is None:
        return False
    return (
        parent in SECTOR_ETFS_US
        or parent in SECTOR_ETFS_EU
        or parent in SECTOR_ETFS_WORLD
    )
