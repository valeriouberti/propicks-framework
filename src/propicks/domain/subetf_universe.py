"""Sub-ETF universe per Sector-Filtered Momentum (mode ``instrument=subetf``).

Mappa curata parent Select Sector SPDR (XL*) → list di thematic / sub-industry
ETF US-listed. Permette al motore SFM di sostituire lo Stage 3 (bottom-up
stock pick) con uno scoring momentum su sub-ETF dentro il settore vincente.

## Razionale

Asness-Porter-Stevens 2000 stima ~200-400 bps annui di intra-sector dispersion
tra leader e laggard. Sub-ETF cattura una frazione di questo edge (sub-industry
dispersion < single-stock dispersion) ma elimina l'idiosyncratic risk:
- No earnings gate (sub-ETF basket diversifica earnings calls)
- No quality filter (no per-name fundamentals)
- Tracking error sub-ETF vs parent può essere parzialmente correlato (es. SOXX
  vs XLK condividono NVDA/AVGO weight) — quindi l'edge residuo è più piccolo
  del puro intra-stock alpha. Trade-off accettato in fase 1.

## Curation criteria

Sub-ETF inclusi solo se:
- US-listed (CUSIP / ticker tradabile su broker US)
- AUM > $200M (gate liquidity hard) — evita ETF nicchia thin-traded
- ADV > $5M (volume medio giornaliero) — evita slippage / wide spreads
- Tracking sub-industry distinta dal parent (no overlap >70% holdings)

Lista validata 2026-Q1. Drift atteso semestrale: provider lanciano/chiudono
ETF, AUM oscilla. Review manuale ogni 6 mesi.

Fonti AUM/ADV: SSGA / iShares / Invesco / VanEck / Global X factsheets.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Parent (Select Sector SPDR) → curated sub-ETF list
# ---------------------------------------------------------------------------
# Ogni parent ha 3-6 thematic/sub-industry ETF rappresentativi. Liste corte
# ridurre il rischio di selezione rumorosa: con 10+ sub-ETF per parent il top-N
# scoring estrae varianza casuale, non segnale.
PARENT_TO_SUB_ETFS: dict[str, list[str]] = {
    # Technology — semis, software, cyber, cloud, AI
    "XLK": ["SOXX", "SMH", "IGV", "CIBR", "SKYY", "AIQ"],
    # Financials — regional banks, broker-dealer, insurance, capital markets
    "XLF": ["KRE", "KBE", "IAI", "KIE", "KCE"],
    # Healthcare — biotech, devices, services, pharma
    "XLV": ["IBB", "XBI", "IHF", "IHI"],
    # Consumer Discretionary — retail, leisure, homebuilders, autos
    "XLY": ["XRT", "PEJ", "XHB", "CARZ"],
    # Consumer Staples — food/beverage, agribusiness, global staples
    "XLP": ["PBJ", "MOO", "KXI"],
    # Energy — E&P, services, solar, uranium
    "XLE": ["XOP", "OIH", "TAN", "URA"],
    # Industrials — aerospace/defense, airlines, transports
    "XLI": ["ITA", "JETS", "IYT", "XAR"],
    # Materials — copper, gold miners, steel, lithium, rare earth
    "XLB": ["COPX", "GDX", "SLX", "LIT", "REMX"],
    # Utilities — clean energy, smart grid, wind
    "XLU": ["ICLN", "GRID", "FAN"],
    # Real Estate — mortgage, residential, industrial REIT
    "XLRE": ["REM", "REZ", "INDS"],
    # Communications — social, esports, gaming
    "XLC": ["SOCL", "ESPO", "HERO"],
}


# Reverse index: sub-ETF → parent peer ETF. Util per enrichment context senza
# scan lineare. Costruito alla load-time (immutable dopo).
SUB_ETF_TO_PARENT: dict[str, str] = {
    sub: parent
    for parent, subs in PARENT_TO_SUB_ETFS.items()
    for sub in subs
}


def sub_etfs_for_parent(parent_etf: str) -> list[str]:
    """Ritorna la lista curata di sub-ETF per un parent (es. ``"XLK"`` → ``["SOXX", ...]``).

    Args:
        parent_etf: ticker parent Select Sector SPDR (case-insensitive). Validi:
            XLK, XLF, XLV, XLY, XLP, XLE, XLI, XLB, XLU, XLRE, XLC.

    Returns:
        Lista ticker uppercase. Lista vuota se parent non riconosciuto.

    Examples:
        >>> sub_etfs_for_parent("XLK")
        ['SOXX', 'SMH', 'IGV', 'CIBR', 'SKYY', 'AIQ']
        >>> sub_etfs_for_parent("xlf")
        ['KRE', 'KBE', 'IAI', 'KIE', 'KCE']
        >>> sub_etfs_for_parent("UNKNOWN")
        []
    """
    if not parent_etf or not isinstance(parent_etf, str):
        return []
    return list(PARENT_TO_SUB_ETFS.get(parent_etf.strip().upper(), []))


def parent_for_sub_etf(sub_etf: str) -> str | None:
    """Ritorna il parent peer ETF per un sub-ETF (es. ``"SOXX"`` → ``"XLK"``).

    Returns None se ``sub_etf`` non è in ``SUB_ETF_TO_PARENT``.
    """
    if not sub_etf or not isinstance(sub_etf, str):
        return None
    return SUB_ETF_TO_PARENT.get(sub_etf.strip().upper())


def all_sub_etfs() -> list[str]:
    """Tutti i sub-ETF curati, ordinati. Utile per smoke-test universe coverage."""
    return sorted(SUB_ETF_TO_PARENT.keys())
