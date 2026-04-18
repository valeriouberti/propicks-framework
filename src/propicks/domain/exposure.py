"""Calcoli di esposizione su un portfolio aperto.

Layer puro: prende dict positions + dati esterni iniettati (prezzi correnti,
mappa sector, beta, returns DataFrame). I download yfinance vivono nella CLI
che chiama queste funzioni.

Tre dimensioni:

1. **Concentrazione settoriale (GICS)** — somma % capitale per `sector_key`.
   Le regole hardcoded cappano la singola posizione al 15% e l'aggregato
   sector ETF al 60%, ma niente impedisce due tech stock a 15% ciascuno
   (= 30% effettivi su technology). Questo modulo lo rende visibile.

2. **Beta-weighted gross exposure** — sum(weight_i * beta_i). Misura la
   sensibilità del portfolio al mercato (SPX). Beta=1 → si muove come SPX,
   beta=1.5 → 50% più volatile. Utile per dimensionare hedging in regime
   di stress.

3. **Matrice correlazioni pairwise** — su daily returns recenti. Pair con
   |corr| > 0.7 sono effettivamente la stessa scommessa (rischio
   concentrato camuffato da diversificazione).
"""

from __future__ import annotations

import pandas as pd


def compute_sector_exposure(
    positions: dict[str, dict],
    current_prices: dict[str, float],
    sector_map: dict[str, str | None],
    total_capital: float,
) -> dict[str, float]:
    """Mappa sector_key -> % del capitale totale.

    Posizioni con sector ignoto finiscono in chiave ``"unknown"``. Cash
    NON è incluso (è esposizione zero per definizione).

    Esempio: {"technology": 0.30, "financials": 0.12, "unknown": 0.05}
    """
    exposure: dict[str, float] = {}
    if total_capital <= 0:
        return exposure

    for ticker, pos in positions.items():
        cur = current_prices.get(ticker)
        if cur is None:
            continue
        market_value = pos["shares"] * cur
        sector = sector_map.get(ticker) or "unknown"
        exposure[sector] = exposure.get(sector, 0.0) + market_value / total_capital

    return {k: round(v, 4) for k, v in exposure.items()}


def compute_concentration_warnings(
    sector_exposure: dict[str, float],
    single_sector_cap: float = 0.30,
) -> list[str]:
    """Genera warning testuali per sector con esposizione > cap.

    Default 30%: max single-name 15% x 2 = doppia posizione concentrata
    nello stesso settore. Sopra significa rischio settoriale rilevante non
    presidiato dalle regole single-name.
    """
    warnings: list[str] = []
    for sector, pct in sector_exposure.items():
        if pct > single_sector_cap:
            warnings.append(
                f"{sector}: {pct * 100:.1f}% del capitale (cap consigliato {single_sector_cap * 100:.0f}%)"
            )
    return warnings


def compute_beta_weighted_exposure(
    positions: dict[str, dict],
    current_prices: dict[str, float],
    betas: dict[str, float | None],
    total_capital: float,
    default_beta: float = 1.0,
) -> dict:
    """Beta-weighted gross long exposure vs SPX.

    Ritorna dict con:
        - beta_weighted: sum(weight_i * beta_i), espresso come frazione del capitale
        - gross_long: sum(weight_i) — esposizione lorda nominale
        - n_positions_with_beta: quante posizioni hanno beta noto
        - default_used_for: lista ticker dove è stato usato default_beta

    Esempio: gross 0.65 / beta-w 0.78 → portfolio 65% investito che si muove
    come 78% di SPX (ha titoli più volatili della media).
    """
    if total_capital <= 0:
        return {
            "beta_weighted": 0.0,
            "gross_long": 0.0,
            "n_positions_with_beta": 0,
            "default_used_for": [],
        }

    beta_weighted = 0.0
    gross_long = 0.0
    n_with_beta = 0
    default_used: list[str] = []

    for ticker, pos in positions.items():
        cur = current_prices.get(ticker)
        if cur is None:
            continue
        weight = (pos["shares"] * cur) / total_capital
        gross_long += weight
        beta = betas.get(ticker)
        if beta is None:
            default_used.append(ticker)
            beta = default_beta
        else:
            n_with_beta += 1
        beta_weighted += weight * beta

    return {
        "beta_weighted": round(beta_weighted, 4),
        "gross_long": round(gross_long, 4),
        "n_positions_with_beta": n_with_beta,
        "default_used_for": default_used,
    }


def compute_correlation_matrix(
    returns: pd.DataFrame,
    min_observations: int = 30,
) -> pd.DataFrame | None:
    """Matrice di correlazione su daily returns. None se osservazioni
    insufficienti.

    ``returns`` è un DataFrame con una colonna per ticker, ogni riga un
    giorno. Tipicamente costruito con ``pct_change()`` su prezzi.
    """
    if returns.empty or len(returns.columns) < 2:
        return None
    valid = returns.dropna(how="all")
    if len(valid) < min_observations:
        return None
    return valid.corr()


def find_correlated_pairs(
    corr_matrix: pd.DataFrame,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Estrae pair con |correlazione| >= threshold dalla matrice.

    Ritorna lista di tuple (ticker_a, ticker_b, corr) ordinata per |corr|
    discendente. Solo upper triangle (no duplicati, no diagonale).
    """
    pairs: list[tuple[str, str, float]] = []
    tickers = list(corr_matrix.columns)
    for i, a in enumerate(tickers):
        for b in tickers[i + 1 :]:
            value = corr_matrix.loc[a, b]
            if pd.isna(value):
                continue
            if abs(value) >= threshold:
                pairs.append((a, b, round(float(value), 3)))
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    return pairs
