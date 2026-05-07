"""Sector-Filtered Momentum (SFM) — strategia ibrida top-down/bottom-up.

Combina due segnali documentati in letteratura:

1. **Industry momentum** (Moskowitz-Grinblatt 1999): i settori in trend up
   tendono a continuare 3-12 mesi. Il segnale top-down arriva da
   ``etf_scoring.rank_universe``.
2. **Intra-industry winners** (Asness-Porter-Stevens 2000): dentro un
   settore vincente, gli stock con peer-RS forte battono l'ETF settoriale
   di 200-400 bps annui. Il segnale bottom-up arriva da
   ``scoring.analyze_ticker`` + ``stock_rs.score_rs_vs_sector``.

## Pipeline

Due modalità d'uso:

**Rotate-driven** (default, automatico):
    1. ``rank_universe(region="US")`` → settori ordinati per score
    2. Filter top-N settori con classification A (score ≥ SFM_MIN_SECTOR_SCORE)
    3. Per ogni settore: filter S&P 500 universe by sector_key
    4. ``discover_momentum_candidates`` su universo filtrato
    5. Ranking finale con peer-RS overlay (composite × 0.80 + rs_score × 0.20)

**Sector-explicit** (manuale):
    1. Caller passa peer ETF (es. "XLK") o sector_key (es. "technology")
    2. Salta lo step 1-2: filter S&P 500 e va al momentum scoring direttamente
    3. Utile per backtest, debug, override discrezionale

## Sector taxonomy normalization

Wikipedia ritorna stringhe GICS ("Information Technology", "Health Care").
Yahoo Finance usa varianti ("Technology", "Healthcare", "Consumer Cyclical").
Il SP500_FALLBACK in ``index_constituents`` usa Yahoo. Per uniformare, mappa
entrambe in ``sector_key`` interno (lowercase, GICS-normalized).

## Architettura: layer puro

Nessuna dipendenza da ``io/``, ``cli/``, ``reports/``. Riceve universe
filtrate in input, ritorna analysis dict in output. Il fetch della rotation
è iniettabile via ``ranked_etfs`` per evitare costo rete nei test e nei
backtest che girano già il rank.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

import pandas as pd

from propicks.config import (
    SFM_DEFAULT_TOP_SECTORS,
    SFM_MIN_SECTOR_SCORE,
    SFM_MIN_STOCK_SCORE,
    SFM_RS_OVERLAY_WEIGHT,
)
from propicks.domain.momentum_discovery import (
    DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    DISCOVERY_PREFILTER_RSI_MIN,
    discover_momentum_candidates,
)
from propicks.domain.scoring import analyze_ticker
from propicks.domain.stock_rs import SECTOR_KEY_TO_US_ETF
from propicks.domain.subetf_universe import sub_etfs_for_parent

# ---------------------------------------------------------------------------
# Instrument modes (stock | subetf)
# ---------------------------------------------------------------------------
INSTRUMENT_STOCK = "stock"
INSTRUMENT_SUBETF = "subetf"
VALID_INSTRUMENTS: frozenset[str] = frozenset({INSTRUMENT_STOCK, INSTRUMENT_SUBETF})

# ---------------------------------------------------------------------------
# Sector taxonomy normalization (GICS Wikipedia ↔ Yahoo ↔ sector_key interno)
# ---------------------------------------------------------------------------
# Ogni sector_key ha set di alias: stringhe canoniche GICS (Wikipedia) +
# varianti Yahoo Finance (fallback hardcoded). Lookup case-insensitive.
SECTOR_KEY_ALIASES: dict[str, frozenset[str]] = {
    "technology": frozenset({
        "information technology",   # GICS canonico (Wikipedia)
        "technology",                # Yahoo
    }),
    "financials": frozenset({
        "financials",                # GICS
        "financial services",        # Yahoo
    }),
    "energy": frozenset({
        "energy",                    # GICS + Yahoo
    }),
    "healthcare": frozenset({
        "health care",               # GICS canonico
        "healthcare",                # Yahoo
    }),
    "industrials": frozenset({
        "industrials",               # GICS + Yahoo
    }),
    "consumer_discretionary": frozenset({
        "consumer discretionary",    # GICS
        "consumer cyclical",         # Yahoo
    }),
    "consumer_staples": frozenset({
        "consumer staples",          # GICS
        "consumer defensive",        # Yahoo
    }),
    "utilities": frozenset({
        "utilities",                 # GICS + Yahoo
    }),
    "real_estate": frozenset({
        "real estate",               # GICS + Yahoo
    }),
    "materials": frozenset({
        "materials",                 # GICS
        "basic materials",           # Yahoo
    }),
    "communications": frozenset({
        "communication services",    # GICS + Yahoo (canonical)
        "communications",            # short variant
    }),
}


def normalize_sector_to_key(sector: str | None) -> str | None:
    """Normalizza una stringa sector (GICS o Yahoo) → sector_key interno.

    Args:
        sector: stringa raw da Wikipedia ("Information Technology"),
            Yahoo Finance ("Technology"), o fallback ("Consumer Cyclical").
            Confronto case-insensitive, whitespace-tollerante.

    Returns:
        sector_key in ``SECTOR_KEY_ALIASES`` (lowercase, snake_case),
        o None se non riconosciuto.

    Examples:
        >>> normalize_sector_to_key("Information Technology")
        'technology'
        >>> normalize_sector_to_key("Consumer Cyclical")
        'consumer_discretionary'
        >>> normalize_sector_to_key("Health Care")
        'healthcare'
        >>> normalize_sector_to_key("Unknown Sector")
        >>> normalize_sector_to_key(None)
    """
    if not sector or not isinstance(sector, str):
        return None
    needle = sector.strip().lower()
    if not needle:
        return None
    for key, aliases in SECTOR_KEY_ALIASES.items():
        if needle in aliases:
            return key
    return None


def peer_etf_for_sector_key(sector_key: str) -> str | None:
    """sector_key → US peer ETF (Select Sector SPDR XL*).

    Wrapper su ``stock_rs.SECTOR_KEY_TO_US_ETF`` per coesione del layer.
    """
    return SECTOR_KEY_TO_US_ETF.get(sector_key)


def sector_key_for_peer_etf(peer_etf: str) -> str | None:
    """Inverso: ETF (XLK) → sector_key (technology).

    Utile per CLI ``--sector XLK`` → sector_key per filtrare universe.
    """
    target = peer_etf.upper()
    for key, etf in SECTOR_KEY_TO_US_ETF.items():
        if etf == target:
            return key
    return None


# ---------------------------------------------------------------------------
# Universe filter by sector
# ---------------------------------------------------------------------------
def filter_universe_by_sector(
    detailed_universe: list[dict],
    sector_key: str,
) -> list[str]:
    """Filtra universo dettagliato (con metadata sector) → list ticker dello stesso sector_key.

    Args:
        detailed_universe: output di ``get_index_universe_detailed`` o
            ``get_sp500_universe_detailed`` — list di dict con almeno
            ``{ticker, sector}``. ``sector`` può essere GICS (Wikipedia)
            o Yahoo (fallback) — viene normalizzato qui.
        sector_key: chiave interna (es. "technology"). Vedi
            ``SECTOR_KEY_ALIASES``.

    Returns:
        Lista ticker (uppercase) dei membri del settore. Lista vuota se
        nessun match (es. sector_key invalido o universe senza sector
        column).

    Note:
        Ticker con ``sector`` None (es. IPO recente, fallback senza
        metadata) vengono esclusi: non possiamo affermare appartenenza.
    """
    if not detailed_universe or not sector_key:
        return []
    if sector_key not in SECTOR_KEY_ALIASES:
        return []
    aliases = SECTOR_KEY_ALIASES[sector_key]
    out: list[str] = []
    for row in detailed_universe:
        sec = row.get("sector")
        if not sec or not isinstance(sec, str):
            continue
        if sec.strip().lower() in aliases:
            ticker = row.get("ticker")
            if isinstance(ticker, str) and ticker:
                out.append(ticker.upper())
    return out


# ---------------------------------------------------------------------------
# Peer-RS overlay (combina momentum composite con stock_rs vs sector)
# ---------------------------------------------------------------------------
def apply_peer_rs_overlay(
    base_composite: float,
    rs_vs_sector: dict | None,
    *,
    weight: float = SFM_RS_OVERLAY_WEIGHT,
) -> float:
    """Combina composite momentum classico con score peer-RS vs settore.

    Pattern overlay non-breaking (analogo a ``combine_with_earnings_revision``):
    quando rs_vs_sector è None (ticker non US o senza peer mapping), ritorna
    base_composite invariato. Altrimenti weighted average:

        sfm_composite = base × (1 - w) + rs_score × w

    Args:
        base_composite: score 0-100 da ``analyze_ticker.score_composite``.
        rs_vs_sector: dict da ``analyze_ticker['rs_vs_sector']`` (può essere
            None per ticker non-US). Cerca chiave ``score`` 0-100.
        weight: peso peer-RS overlay [0, 1]. Default da config.

    Returns:
        Composite SFM 0-100. Clamp finale a [0, 100] per safety.

    Examples:
        >>> apply_peer_rs_overlay(70.0, {"score": 90.0}, weight=0.20)
        74.0
        >>> apply_peer_rs_overlay(70.0, None)
        70.0
        >>> apply_peer_rs_overlay(70.0, {"score": 50.0})  # default 0.20
        66.0
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight {weight} must be in [0, 1]")
    if rs_vs_sector is None:
        return base_composite
    rs_score = rs_vs_sector.get("score")
    if not isinstance(rs_score, (int, float)):
        return base_composite
    if not 0.0 <= rs_score <= 100.0:
        return base_composite
    combined = base_composite * (1.0 - weight) + float(rs_score) * weight
    return max(0.0, min(100.0, combined))


def enrich_with_sfm_score(
    analysis: dict,
    *,
    weight: float = SFM_RS_OVERLAY_WEIGHT,
) -> dict:
    """Aggiunge ``score_sfm`` al dict di ``analyze_ticker``.

    Mantiene ``score_composite`` originale (Pine sync, momentum standalone)
    e aggiunge un campo derivato. Il caller può usare ``score_sfm`` per
    ranking/threshold senza perdere il composite originale.
    """
    base = float(analysis.get("score_composite", 0.0))
    rs = analysis.get("rs_vs_sector")
    sfm = apply_peer_rs_overlay(base, rs, weight=weight)
    enriched = dict(analysis)
    enriched["score_sfm"] = round(sfm, 1)
    enriched["sfm_overlay_weight"] = weight
    return enriched


# ---------------------------------------------------------------------------
# Sector selection from rotation ranking
# ---------------------------------------------------------------------------
def select_top_sectors(
    ranked_etfs: list[dict],
    *,
    top_n: int = SFM_DEFAULT_TOP_SECTORS,
    min_score: float = SFM_MIN_SECTOR_SCORE,
) -> list[dict]:
    """Filtra il ranking ETF rotation → top-N settori OVERWEIGHT.

    Args:
        ranked_etfs: output di ``etf_scoring.rank_universe`` (lista dict
            ordinata desc by ``score_composite``, con ``sector_key``).
        top_n: max settori da ritornare.
        min_score: threshold composite minimo (default 70 = classe A).

    Returns:
        list[dict] sotto-set di ``ranked_etfs`` filtrato + truncato.
        Vuota se nessun settore qualifica.
    """
    if not ranked_etfs or top_n <= 0:
        return []
    eligible = [
        r for r in ranked_etfs
        if float(r.get("score_composite", 0.0)) >= min_score
    ]
    return eligible[:top_n]


# ---------------------------------------------------------------------------
# Sub-ETF Stage 3 (instrument="subetf"): scoring senza prefilter.
# Sub-ETF universe è già corto (3-6 per parent) e curato per liquidity, quindi
# il prefilter cheap di ``momentum_discovery`` (RSI/dist-high) è skippabile.
# ---------------------------------------------------------------------------
def score_sub_etfs(
    sub_etfs: list[str],
    *,
    top_n: int = 3,
    min_score: float = 0.0,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
    analyze_fn: Callable[[str], dict | None] | None = None,
) -> list[dict]:
    """Scora una lista di sub-ETF con ``analyze_ticker`` momentum classico.

    Riusa l'engine momentum stock (6 sub-score) sui sub-ETF: trend + momentum
    RSI + distance from 52w high si applicano identicamente. ``volume`` e
    ``ma_cross`` sono comparabili. Skippa prefilter (universe corto).

    No earnings gate: gli ETF non hanno earnings calls (basket diversificato).
    Peer-RS vs parent: ``analyze_ticker.rs_vs_sector`` ritorna None per gli ETF
    perché ``get_ticker_sector`` non resolve un settore valido per i basket —
    quindi il composite SFM degrade gracefully a base composite via overlay.

    Args:
        sub_etfs: lista ticker (es. ``["SOXX", "SMH", "IGV"]``).
        top_n: max candidati da ritornare dopo filtro min_score.
        min_score: composite minimo per inclusione.
        progress_callback: ``cb(stage, current, total, ticker)`` con
            ``stage`` in ``{"scoring"}``.
        analyze_fn: iniettabile per test offline (default ``analyze_ticker``).

    Returns:
        list[dict] ordinata desc by ``score_composite``, troncata a top_n.
    """
    fn = analyze_fn or analyze_ticker
    out: list[dict] = []
    for idx, ticker in enumerate(sub_etfs):
        if progress_callback:
            progress_callback("scoring", idx + 1, len(sub_etfs), ticker)
        try:
            result = fn(ticker)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[sfm-subetf] errore analyze {ticker}: {exc}",
                file=sys.stderr,
            )
            continue
        if result is None:
            continue
        if result.get("score_composite", 0) < min_score:
            continue
        out.append(result)
    out.sort(key=lambda x: x.get("score_composite", 0), reverse=True)
    return out[:top_n]


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------
def discover_sector_momentum_candidates(
    detailed_universe: list[dict] | None,
    *,
    instrument: str = INSTRUMENT_STOCK,
    ranked_etfs: list[dict] | None = None,
    sector_keys: Iterable[str] | None = None,
    top_sectors: int = SFM_DEFAULT_TOP_SECTORS,
    top_stocks_per_sector: int = 3,
    min_sector_score: float = SFM_MIN_SECTOR_SCORE,
    min_stock_score: float = SFM_MIN_STOCK_SCORE,
    rs_overlay_weight: float = SFM_RS_OVERLAY_WEIGHT,
    rsi_min: float = DISCOVERY_PREFILTER_RSI_MIN,
    max_dist_from_high: float = DISCOVERY_PREFILTER_MAX_DIST_FROM_HIGH,
    prefilter_cap: int | None = None,
    fetch_fn: Callable[[str], pd.DataFrame | None] | None = None,
    analyze_fn: Callable[[str], dict | None] | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict:
    """Pipeline SFM completa: settori ranking → filter universe → momentum.

    **Mode A (rotate-driven)**: passa ``ranked_etfs`` e lascia ``sector_keys=None``.
    Seleziona internamente i top-N settori con score ≥ ``min_sector_score``.

    **Mode B (sector-explicit)**: passa ``sector_keys`` (es. ["technology"])
    e lascia ``ranked_etfs=None``. Skippa la rotation, va diretto al momentum
    su universe filtrato.

    Esattamente uno tra i due deve essere fornito.

    **Instrument**: ``stock`` (default) seleziona top stock dentro settori
    OVERWEIGHT via S&P 500 universe. ``subetf`` seleziona top sub-ETF
    thematic/sub-industry dentro il parent settoriale (universe curato in
    ``subetf_universe.PARENT_TO_SUB_ETFS``). In subetf-mode ``detailed_universe``
    è ignorato (può essere ``None``).

    Args:
        detailed_universe: output di ``get_index_universe_detailed("sp500")``
            o equivalente — list di dict con ``{ticker, sector, ...}``.
            Richiesto solo in ``instrument=stock``. Ignorato in ``subetf``.
        instrument: ``"stock"`` (default) | ``"subetf"``. Vedi
            ``VALID_INSTRUMENTS``.
        ranked_etfs: output di ``etf_scoring.rank_universe(region="US")``.
            Se None, richiede ``sector_keys``.
        sector_keys: iterable di sector_key (es. ``["technology", "financials"]``).
            Se None, deriva da ``ranked_etfs`` via ``select_top_sectors``.
        top_sectors: max settori (rotate-driven mode).
        top_stocks_per_sector: max stock/sub-ETF per settore dopo full scoring.
        min_sector_score: threshold ETF rotation (default 70).
        min_stock_score: threshold momentum composite (default 75 = classe A).
        rs_overlay_weight: peso peer-RS in composite SFM. Per sub-ETF, il
            ``rs_vs_sector`` è tipicamente None (ETF basket non ha sector
            resolver) → overlay degrade a base composite (no penalty/boost).
        rsi_min, max_dist_from_high, prefilter_cap: passati a
            ``discover_momentum_candidates``. Ignorati in ``subetf`` mode
            (universe curato e corto, prefilter skippato).
        fetch_fn: iniettabile per test (passato al prefilter stock-mode).
        analyze_fn: iniettabile per test (sostituisce ``analyze_ticker`` in
            subetf-mode). Stock-mode usa il default di
            ``discover_momentum_candidates``.
        progress_callback: ``cb(stage, current, total, ticker)`` con
            ``stage`` in ``{"prefilter", "scoring", "sector"}``.

    Returns:
        dict con chiavi:
        - ``instrument``: echo del parametro (``"stock"`` | ``"subetf"``)
        - ``sectors_evaluated``: list[dict] ``{sector_key, peer_etf, sector_score, n_universe, n_candidates}``
        - ``candidates``: list[dict] (analysis arricchiti con ``score_sfm``,
          ordinati desc by score_sfm cross-sector, troncati a
          ``top_stocks_per_sector × n_sectors``).

    Raises:
        ValueError: se né ``ranked_etfs`` né ``sector_keys`` fornito, o
            entrambi; se ``instrument`` non in ``VALID_INSTRUMENTS``; se
            ``instrument=stock`` e ``detailed_universe`` è None.
    """
    if (ranked_etfs is None) == (sector_keys is None):
        raise ValueError(
            "Esattamente uno tra `ranked_etfs` e `sector_keys` deve essere fornito"
        )
    if instrument not in VALID_INSTRUMENTS:
        raise ValueError(
            f"instrument='{instrument}' non valido. Usa: {sorted(VALID_INSTRUMENTS)}"
        )
    if instrument == INSTRUMENT_STOCK and detailed_universe is None:
        raise ValueError("`detailed_universe` richiesto in instrument='stock'")

    # Step 1: determina sector_keys target
    if sector_keys is None:
        # Mode A: rotate-driven
        top_secs = select_top_sectors(
            ranked_etfs, top_n=top_sectors, min_score=min_sector_score
        )
        if not top_secs:
            return {"sectors_evaluated": [], "candidates": []}
        sector_specs = [
            {
                "sector_key": s["sector_key"],
                "peer_etf": s["ticker"],
                "sector_score": s["score_composite"],
            }
            for s in top_secs
        ]
    else:
        # Mode B: sector-explicit. Sector score sconosciuto (manual override).
        sector_specs = []
        for key in sector_keys:
            peer = peer_etf_for_sector_key(key)
            if peer is None:
                print(
                    f"[sfm] sector_key '{key}' non mappato — skip",
                    file=sys.stderr,
                )
                continue
            sector_specs.append({
                "sector_key": key,
                "peer_etf": peer,
                "sector_score": None,
            })
        if not sector_specs:
            return {"sectors_evaluated": [], "candidates": []}

    sectors_evaluated: list[dict] = []
    all_candidates: list[dict] = []

    # Step 2: per ogni settore → filter universe → momentum discovery
    for idx, spec in enumerate(sector_specs):
        sector_key = spec["sector_key"]
        peer_etf = spec["peer_etf"]

        if progress_callback:
            progress_callback(
                "sector", idx + 1, len(sector_specs), peer_etf
            )

        # Determine universe: stock filter S&P 500 vs sub-ETF curated map
        if instrument == INSTRUMENT_SUBETF:
            sub_universe = sub_etfs_for_parent(peer_etf)
            sector_record = {
                "sector_key": sector_key,
                "peer_etf": peer_etf,
                "sector_score": spec["sector_score"],
                "n_universe": len(sub_universe),
                "n_candidates": 0,
            }
            if not sub_universe:
                sectors_evaluated.append(sector_record)
                continue
            scored = score_sub_etfs(
                sub_universe,
                top_n=top_stocks_per_sector,
                min_score=min_stock_score,
                progress_callback=progress_callback,
                analyze_fn=analyze_fn,
            )
            for cand in scored:
                enriched = enrich_with_sfm_score(cand, weight=rs_overlay_weight)
                enriched["sector_key"] = sector_key
                enriched["peer_etf"] = peer_etf
                enriched["sector_score"] = spec["sector_score"]
                enriched["instrument"] = INSTRUMENT_SUBETF
                all_candidates.append(enriched)
            sector_record["n_candidates"] = len(scored)
            sectors_evaluated.append(sector_record)
            continue

        # instrument == stock: filter S&P 500 + full momentum pipeline
        sector_universe = filter_universe_by_sector(
            detailed_universe, sector_key
        )

        sector_record = {
            "sector_key": sector_key,
            "peer_etf": peer_etf,
            "sector_score": spec["sector_score"],
            "n_universe": len(sector_universe),
            "n_candidates": 0,
        }

        if not sector_universe:
            sectors_evaluated.append(sector_record)
            continue

        # Stage 1+2 momentum (riusa pipeline esistente)
        out = discover_momentum_candidates(
            sector_universe,
            top_n=top_stocks_per_sector,
            rsi_min=rsi_min,
            max_dist_from_high=max_dist_from_high,
            min_score=min_stock_score,
            prefilter_cap=prefilter_cap,
            fetch_fn=fetch_fn,
            progress_callback=progress_callback,
        )

        # Step 3: arricchisci ogni candidato con score_sfm + sector context
        for cand in out["candidates"]:
            enriched = enrich_with_sfm_score(cand, weight=rs_overlay_weight)
            enriched["sector_key"] = sector_key
            enriched["peer_etf"] = peer_etf
            enriched["sector_score"] = spec["sector_score"]
            enriched["instrument"] = INSTRUMENT_STOCK
            all_candidates.append(enriched)

        sector_record["n_candidates"] = len(out["candidates"])
        sectors_evaluated.append(sector_record)

    # Step 4: ranking finale cross-sector by score_sfm desc
    all_candidates.sort(key=lambda x: x.get("score_sfm", 0.0), reverse=True)

    return {
        "instrument": instrument,
        "sectors_evaluated": sectors_evaluated,
        "candidates": all_candidates,
    }
