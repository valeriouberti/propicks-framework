"""Allocazione e drift del Core Portfolio (long-term PIC/PAC).

Layer **puro**: nessun I/O, nessuna rete. Riceve dict holdings (già caricati
da ``io/core_store.py``) + prezzi correnti iniettati, ritorna dict di
allocazione/drift/breakdown.

## Concetti

- **Current value**: per holding = ``shares × current_price`` (in valuta della
  holding, opzionalmente convertito in EUR via ``currency_map``).
- **Actual weight**: ``current_value / total_core_value``. Frazione [0, 1].
- **Target weight**: campo opzionale su ``core_holdings``. Se mancante il
  drift non viene calcolato per quella holding.
- **Drift**: ``actual − target``. Segno + significa sovrappeso (vendere o
  saltare PAC), − significa sottopeso (PAC successivo va lì).
- **Rebalance hint**: importo EUR da movimentare per riallineare.
  ``rebalance_eur = (target − actual) × total_value``. + = compra, − = vendi.

## Overlap detector (core × satellite)

Il vero valore aggiunto: il bucket core (es. VWCE 60%) ha esposizione
**implicita** ai settori. Se satellite compra NVDA (technology 15%) sopra
il 12% di tech già implicito in VWCE → tech consolidato = 27%, sotto
``CORE_OVERLAP_SECTOR_WARN_PCT`` (35%) ma vicino. Se aggiungo MSFT (+15%) →
42% consolidato → flag.

Il modulo NON conosce la decomposizione settoriale interna di un ETF (richiede
holdings download da provider). Strategia v1: l'utente assegna un
``sector_key`` esplicito a ogni holding core (ETF tematico/settoriale = key
preciso, ETF broad = NULL e contribuisce solo all'asset class breakdown).
"""

from __future__ import annotations

from typing import Any


def _mv_to_eur(amount: float, currency: str | None) -> float:
    """Identity se EUR/None/error. Mirror di ``exposure._mv_to_eur``."""
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return float(amount)
    try:
        from propicks.domain.currency import convert_to_eur
        return convert_to_eur(amount, cur)
    except Exception:
        return float(amount)


# ---------------------------------------------------------------------------
# Current value + total
# ---------------------------------------------------------------------------
def compute_holding_values(
    holdings: dict[str, dict],
    current_prices: dict[str, float],
    *,
    convert_to_eur: bool = True,
) -> dict[str, dict[str, float]]:
    """Per ogni holding ritorna {current_price, current_value, cost_basis, pnl, pnl_pct}.

    Salta holding con ``shares <= 0`` (rimosse). Salta holding senza
    ``current_price`` disponibile (e.g. ticker delisted).

    ``current_value`` è già in EUR se ``convert_to_eur=True`` (default), per
    matching col denominatore tipicamente in EUR. Per multi-currency report
    raw, passa ``convert_to_eur=False``.
    """
    out: dict[str, dict[str, float]] = {}
    for ticker, h in holdings.items():
        shares = float(h.get("shares") or 0)
        if shares <= 0:
            continue
        px = current_prices.get(ticker)
        if px is None:
            continue
        avg = float(h.get("avg_cost") or 0)
        currency = h.get("currency") or "EUR"
        raw_value = shares * px
        raw_cost = shares * avg
        if convert_to_eur:
            value = _mv_to_eur(raw_value, currency)
            cost = _mv_to_eur(raw_cost, currency)
        else:
            value = raw_value
            cost = raw_cost
        pnl = value - cost
        pnl_pct = (pnl / cost) if cost > 0 else 0.0
        out[ticker] = {
            "current_price": round(float(px), 4),
            "current_value": round(value, 2),
            "cost_basis": round(cost, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "shares": shares,
            "avg_cost": round(avg, 4),
        }
    return out


def total_core_value(
    holding_values: dict[str, dict[str, float]],
) -> float:
    """Somma di ``current_value`` su tutte le holding. EUR se i valori sono
    stati convertiti a EUR upstream."""
    return round(sum(v["current_value"] for v in holding_values.values()), 2)


# ---------------------------------------------------------------------------
# Drift vs target_weight
# ---------------------------------------------------------------------------
def compute_drift(
    holdings: dict[str, dict],
    holding_values: dict[str, dict[str, float]],
    total_value: float,
    *,
    rebalance_threshold: float = 0.05,
) -> dict[str, dict[str, Any]]:
    """Drift per ogni holding con ``target_weight`` definito.

    Ritorna per ogni ticker:
        - actual_weight (frazione)
        - target_weight (frazione)
        - drift (actual − target, signed)
        - rebalance_eur (target − actual) × total_value, signed
        - needs_rebalance (|drift| > threshold)

    Holding senza target_weight vengono skippate. Se total_value <= 0
    ritorna dict vuoto.
    """
    out: dict[str, dict[str, Any]] = {}
    if total_value <= 0:
        return out
    for ticker, h in holdings.items():
        target = h.get("target_weight")
        if target is None:
            continue
        target = float(target)
        hv = holding_values.get(ticker)
        actual_value = float(hv["current_value"]) if hv else 0.0
        actual_weight = actual_value / total_value
        drift = actual_weight - target
        rebalance_eur = (target - actual_weight) * total_value
        out[ticker] = {
            "actual_weight": round(actual_weight, 4),
            "target_weight": round(target, 4),
            "drift": round(drift, 4),
            "rebalance_eur": round(rebalance_eur, 2),
            "needs_rebalance": abs(drift) > rebalance_threshold,
        }
    return out


# ---------------------------------------------------------------------------
# Breakdown asset class / region / sector
# ---------------------------------------------------------------------------
def _breakdown_by_field(
    holdings: dict[str, dict],
    holding_values: dict[str, dict[str, float]],
    total_value: float,
    field: str,
    *,
    unknown_label: str = "unknown",
) -> dict[str, float]:
    """Generic breakdown weighted-by-value su un campo della holding."""
    out: dict[str, float] = {}
    if total_value <= 0:
        return out
    for ticker, h in holdings.items():
        hv = holding_values.get(ticker)
        if hv is None:
            continue
        key = h.get(field) or unknown_label
        out[key] = out.get(key, 0.0) + hv["current_value"] / total_value
    return {k: round(v, 4) for k, v in out.items()}


def compute_asset_class_breakdown(
    holdings: dict[str, dict],
    holding_values: dict[str, dict[str, float]],
    total_value: float,
) -> dict[str, float]:
    """{asset_class: pct_of_core}. Es. {EQUITY_ETF: 0.70, BOND_ETF: 0.30}."""
    return _breakdown_by_field(
        holdings, holding_values, total_value, "asset_class"
    )


def compute_region_breakdown(
    holdings: dict[str, dict],
    holding_values: dict[str, dict[str, float]],
    total_value: float,
) -> dict[str, float]:
    """{region: pct_of_core}. Es. {WORLD: 0.60, EU: 0.20, EM: 0.20}."""
    return _breakdown_by_field(
        holdings, holding_values, total_value, "region"
    )


def compute_core_sector_breakdown(
    holdings: dict[str, dict],
    holding_values: dict[str, dict[str, float]],
    total_value: float,
) -> dict[str, float]:
    """{sector_key: pct_of_core}.

    Holding con ``sector_key=None`` (es. broad ETF VWCE) finiscono in
    ``"broad"`` invece di ``"unknown"`` per chiarezza semantica:
    non è ignoto, è intenzionalmente diversificato.
    """
    out: dict[str, float] = {}
    if total_value <= 0:
        return out
    for ticker, h in holdings.items():
        hv = holding_values.get(ticker)
        if hv is None:
            continue
        key = h.get("sector_key") or "broad"
        out[key] = out.get(key, 0.0) + hv["current_value"] / total_value
    return {k: round(v, 4) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Overlap detector core + satellite
# ---------------------------------------------------------------------------
def compute_consolidated_sector_exposure(
    core_holdings: dict[str, dict],
    core_values: dict[str, dict[str, float]],
    satellite_positions: dict[str, dict],
    satellite_prices: dict[str, float],
    satellite_sector_map: dict[str, str | None],
    total_capital_eur: float,
    *,
    satellite_currency_map: dict[str, str] | None = None,
) -> dict[str, float]:
    """Sector exposure consolidato (core + satellite) come % capitale totale.

    Denominatore = ``total_capital_eur`` (NON il solo bucket: è il capitale
    complessivo del trader = core + satellite + cash). Questo permette di
    confrontare le percentuali con un cap unico (es. 35% tech consolidato).

    Holding core ``sector_key=None`` (broad ETF) NON contribuiscono al
    breakdown settoriale — la loro esposizione è intenzionalmente diluita.
    Per modellarla servirebbe il look-through del provider (TODO v2).
    """
    out: dict[str, float] = {}
    if total_capital_eur <= 0:
        return out

    # Core: usa current_value già convertito in EUR (da compute_holding_values)
    for ticker, h in core_holdings.items():
        sector = h.get("sector_key")
        if not sector:
            continue  # broad ETF skippato (no look-through)
        hv = core_values.get(ticker)
        if hv is None:
            continue
        out[sector] = out.get(sector, 0.0) + hv["current_value"] / total_capital_eur

    # Satellite: shares × price → EUR via currency_map (come exposure.py)
    for ticker, pos in satellite_positions.items():
        px = satellite_prices.get(ticker)
        if px is None:
            continue
        mv = pos["shares"] * px
        if satellite_currency_map is not None:
            mv = _mv_to_eur(mv, satellite_currency_map.get(ticker))
        sector = satellite_sector_map.get(ticker) or "unknown"
        out[sector] = out.get(sector, 0.0) + mv / total_capital_eur

    return {k: round(v, 4) for k, v in out.items()}


def detect_overlap_warnings(
    consolidated_sector_exposure: dict[str, float],
    *,
    warn_threshold: float = 0.35,
) -> list[dict[str, Any]]:
    """Genera warning per settori sopra il cap consolidato.

    Ritorna lista ordinata per pct desc:
        [{"sector": "technology", "pct": 0.42, "over_by": 0.07}, ...]
    """
    warns: list[dict[str, Any]] = []
    for sector, pct in consolidated_sector_exposure.items():
        if sector == "unknown":
            continue
        if pct > warn_threshold:
            warns.append({
                "sector": sector,
                "pct": round(pct, 4),
                "over_by": round(pct - warn_threshold, 4),
            })
    warns.sort(key=lambda x: x["pct"], reverse=True)
    return warns


# ---------------------------------------------------------------------------
# Summary helper (tutto in un colpo)
# ---------------------------------------------------------------------------
def summarize_core(
    holdings: dict[str, dict],
    current_prices: dict[str, float],
    *,
    rebalance_threshold: float = 0.05,
) -> dict[str, Any]:
    """One-shot summary del core: values + drift + breakdown.

    Pensata per dashboard e CLI ``propicks-core exposure`` — un solo helper
    chiamato dal layer superiore, niente da ricomporre client-side.
    """
    values = compute_holding_values(holdings, current_prices, convert_to_eur=True)
    total = total_core_value(values)
    return {
        "total_value_eur": total,
        "holdings": values,
        "drift": compute_drift(
            holdings, values, total, rebalance_threshold=rebalance_threshold
        ),
        "asset_class": compute_asset_class_breakdown(holdings, values, total),
        "region": compute_region_breakdown(holdings, values, total),
        "sector": compute_core_sector_breakdown(holdings, values, total),
        "n_holdings": len(values),
        "n_missing_price": sum(
            1 for t, h in holdings.items()
            if float(h.get("shares") or 0) > 0 and t not in values
        ),
    }
