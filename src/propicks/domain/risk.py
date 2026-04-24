"""Risk framework v2 — funzioni pure per Kelly, VaR, vol, correlation penalty.

**Design principle: Kelly è ADVISORY, hard caps sempre vincono.**

La stima di edge (P(win), win/loss ratio) da journal storico è notoriamente
rumorosa:
- Sample size basso (<30 trade per strategia)
- Regime-dependent (P(win) in BULL ≠ BEAR)
- Overfitting risk se ri-stimato dopo ogni nuovo trade

Full Kelly è matematicamente "optimal" ma operativamente pericoloso quando
gli input sono stimati. Usiamo **Kelly fractional 25%** (industry standard
retail) come UPPER BOUND suggestion. Mai size > hard cap (15% / 8% / 20%).

## Funzioni

- ``kelly_fractional(win_rate, win_loss_ratio, fraction=0.25)`` — Kelly formula
- ``strategy_kelly_from_trades(trades, strategy)`` — Kelly per-strategy
  da journal trade chiusi. None se <N trade (insufficient sample).
- ``portfolio_vol_annualized(returns_df, weights)`` — vol daily × sqrt(252)
- ``portfolio_var_95(returns_df, weights, method='bootstrap')`` — VaR 95%
- ``correlation_adjusted_size(new_ticker, base_size, existing_weights, corr_matrix)``
  — scala down se il nuovo ticker è correlato ≥0.7 con esistenti (ridondanza).

Tutte zero I/O. Testabili passando DataFrame/Series sintetici.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Kelly criterion
# ---------------------------------------------------------------------------
MIN_TRADES_FOR_KELLY = 15  # sotto questa soglia, Kelly non è affidabile
KELLY_FRACTION_DEFAULT = 0.25  # quarter Kelly, industry retail standard
KELLY_MAX = 0.20  # cap assoluto anche con fractional (safety floor)


def kelly_fractional(
    win_rate: float,
    win_loss_ratio: float,
    fraction: float = KELLY_FRACTION_DEFAULT,
) -> float:
    """Kelly fractional size per trade.

    Formula classica: ``f* = (p × b - q) / b`` dove:
    - ``p`` = probability win (win_rate)
    - ``q = 1 - p`` = probability loss
    - ``b`` = ratio avg_win / avg_loss (win_loss_ratio)

    Kelly fractional = ``f* × fraction``, cap a ``KELLY_MAX`` per safety.

    Returns: size suggerita come frazione del capitale. 0.0 se edge ≤ 0
    (strategia losing), None se input invalidi.

    Example:
        win_rate=0.6, win_loss_ratio=1.5, fraction=0.25 →
        full = (0.6 × 1.5 - 0.4) / 1.5 = 0.333
        fractional = 0.333 × 0.25 = 0.083 (8.3%)
    """
    if win_rate is None or win_loss_ratio is None:
        return 0.0
    if not (0 < win_rate < 1):
        return 0.0
    if win_loss_ratio <= 0:
        return 0.0

    loss_rate = 1.0 - win_rate
    full_kelly = (win_rate * win_loss_ratio - loss_rate) / win_loss_ratio

    if full_kelly <= 0:
        # Edge negativo → non allocare (tornerebbe short-bet se full negativo)
        return 0.0

    fractional = full_kelly * fraction
    return min(fractional, KELLY_MAX)


def strategy_kelly_from_trades(
    trades: list[dict],
    strategy: str,
    fraction: float = KELLY_FRACTION_DEFAULT,
) -> dict:
    """Estrae Kelly suggestion da journal trade chiusi per una strategia.

    Args:
        trades: lista journal (funziona filtra status=closed + matching strategy)
        strategy: tag strategia (case-insensitive match su ``trade['strategy']``)
        fraction: 0.25 = quarter Kelly (default conservativo)

    Returns: dict con:
        - ``kelly_pct``: float 0-0.20, frazione suggerita del capitale
        - ``n_trades``: count trade usati per la stima
        - ``win_rate``: stimato da journal
        - ``win_loss_ratio``: avg(wins) / |avg(losses)|
        - ``usable``: bool, True se n_trades ≥ MIN_TRADES_FOR_KELLY
        - ``reason``: str, spiegazione se not usable
    """
    matching = [
        t for t in trades
        if t.get("status") == "closed"
        and t.get("pnl_pct") is not None
        and (t.get("strategy") or "").lower() == strategy.lower()
    ]

    if len(matching) < MIN_TRADES_FOR_KELLY:
        return {
            "kelly_pct": 0.0,
            "n_trades": len(matching),
            "win_rate": None,
            "win_loss_ratio": None,
            "usable": False,
            "reason": f"Only {len(matching)} trade (min {MIN_TRADES_FOR_KELLY})",
        }

    returns = [float(t["pnl_pct"]) for t in matching]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    if not wins or not losses:
        return {
            "kelly_pct": 0.0,
            "n_trades": len(matching),
            "win_rate": None,
            "win_loss_ratio": None,
            "usable": False,
            "reason": "Degenerate sample (all wins or all losses)",
        }

    win_rate = len(wins) / len(matching)
    avg_win = statistics.mean(wins)
    avg_loss = abs(statistics.mean(losses))
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    kelly_pct = kelly_fractional(win_rate, win_loss_ratio, fraction=fraction)

    return {
        "kelly_pct": round(kelly_pct, 4),
        "n_trades": len(matching),
        "win_rate": round(win_rate, 4),
        "win_loss_ratio": round(win_loss_ratio, 3),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "fraction": fraction,
        "usable": True,
    }


# ---------------------------------------------------------------------------
# Portfolio volatility
# ---------------------------------------------------------------------------
TRADING_DAYS_PER_YEAR = 252


def portfolio_vol_annualized(
    returns_df: pd.DataFrame,
    weights: dict[str, float],
) -> dict:
    """Volatilità portfolio annualizzata.

    Usa la formula classica: ``σ_portfolio = sqrt(w' Σ w) × sqrt(252)``
    dove Σ è la matrice di covarianza dei daily returns.

    Args:
        returns_df: DataFrame con daily returns, colonne = ticker
        weights: dict {ticker: weight_fraction} — weights non serve sommino a 1
            (il portfolio può avere parte in cash)

    Returns: dict con ``vol_daily``, ``vol_annualized``, ``n_tickers_used``,
        ``warnings`` (list di ticker skippati per dati mancanti).
    """
    if returns_df.empty or not weights:
        return {
            "vol_daily": 0.0,
            "vol_annualized": 0.0,
            "n_tickers_used": 0,
            "warnings": ["no data"],
        }

    # Seleziona solo i ticker con returns disponibili
    available = [t for t in weights if t in returns_df.columns]
    missing = [t for t in weights if t not in returns_df.columns]

    if not available:
        return {
            "vol_daily": 0.0,
            "vol_annualized": 0.0,
            "n_tickers_used": 0,
            "warnings": [f"No returns for any of: {list(weights.keys())}"],
        }

    sub = returns_df[available].dropna(how="all")
    if sub.empty:
        return {
            "vol_daily": 0.0,
            "vol_annualized": 0.0,
            "n_tickers_used": 0,
            "warnings": ["returns df empty after dropna"],
        }

    # Covariance matrix
    cov = sub.cov()
    w = np.array([weights[t] for t in available])

    var_daily = float(w.T @ cov.values @ w)
    # Numerical stability: floating point può produrre var leggermente neg
    var_daily = max(0.0, var_daily)
    vol_daily = math.sqrt(var_daily)
    vol_annual = vol_daily * math.sqrt(TRADING_DAYS_PER_YEAR)

    warnings_list = []
    if missing:
        warnings_list.append(f"Missing returns: {missing}")

    return {
        "vol_daily": round(vol_daily, 6),
        "vol_annualized": round(vol_annual, 4),
        "n_tickers_used": len(available),
        "total_weight_used": round(sum(weights[t] for t in available), 4),
        "warnings": warnings_list,
    }


# ---------------------------------------------------------------------------
# Portfolio VaR 95% (bootstrap)
# ---------------------------------------------------------------------------
def portfolio_var_95(
    returns_df: pd.DataFrame,
    weights: dict[str, float],
    n_bootstrap: int = 500,
    horizon_days: int = 1,
    seed: int = 42,
) -> dict:
    """VaR 95% portfolio via bootstrap.

    Logic:
    1. Prende N daily returns per ticker, w pesati → serie daily P&L portfolio
    2. Bootstrap: ricampiona ``n_bootstrap`` volte una finestra ``horizon_days``
    3. VaR = 5° percentile della distribuzione di P&L

    Args:
        returns_df: DataFrame returns daily, colonne = ticker
        weights: dict {ticker: weight_fraction}
        n_bootstrap: numero di simulazioni (default 500 — balance speed/accuracy)
        horizon_days: orizzonte in giorni (1 = VaR giornaliero, 5 = settimanale)

    Returns: dict con ``var_95_pct`` (positivo = loss potenziale), ``expected_shortfall_pct``
        (ES = avg loss condizionale al peggior 5%), ``worst_case_pct``, ``n_bootstrap``.
    """
    if returns_df.empty or not weights:
        return {
            "var_95_pct": None,
            "expected_shortfall_pct": None,
            "worst_case_pct": None,
            "n_bootstrap": 0,
            "note": "no data",
        }

    available = [t for t in weights if t in returns_df.columns]
    if not available:
        return {
            "var_95_pct": None,
            "expected_shortfall_pct": None,
            "worst_case_pct": None,
            "n_bootstrap": 0,
            "note": "no overlap",
        }

    sub = returns_df[available].dropna(how="any")  # need all tickers for a day
    if len(sub) < 30:
        return {
            "var_95_pct": None,
            "expected_shortfall_pct": None,
            "worst_case_pct": None,
            "n_bootstrap": 0,
            "note": f"insufficient history: {len(sub)} bars (need 30+)",
        }

    w = np.array([weights[t] for t in available])
    # Daily portfolio return series
    daily_pnl = sub.values @ w  # numpy 1D array

    # Bootstrap: sample horizon_days-block returns with replacement
    rng = np.random.default_rng(seed)
    n_days_avail = len(daily_pnl)

    simulated = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n_days_avail, size=horizon_days)
        # Compound returns over horizon
        cumulative = float(np.prod(1.0 + daily_pnl[idx]) - 1.0)
        simulated[i] = cumulative

    # VaR 95 = 5° percentile (bottom 5% di worst cases)
    var_95 = float(np.percentile(simulated, 5))
    # Expected shortfall: avg dei peggiori 5%
    es = float(np.mean(simulated[simulated <= var_95]))
    worst = float(np.min(simulated))

    # Convertiamo a "loss positivo": VaR 95% = X% significa "c'è 5% probabilità
    # di perdere almeno X% in `horizon_days`"
    return {
        "var_95_pct": round(-var_95 * 100, 4),  # sign flip per leggibilità
        "expected_shortfall_pct": round(-es * 100, 4),
        "worst_case_pct": round(-worst * 100, 4),
        "n_bootstrap": n_bootstrap,
        "horizon_days": horizon_days,
        "n_days_history": n_days_avail,
        "n_tickers_used": len(available),
    }


# ---------------------------------------------------------------------------
# Correlation-adjusted sizing
# ---------------------------------------------------------------------------
def correlation_adjusted_size(
    base_size_pct: float,
    new_ticker: str,
    existing_weights: dict[str, float],
    corr_matrix: pd.DataFrame | None,
    corr_threshold: float = 0.7,
    penalty_factor: float = 0.5,
) -> dict:
    """Scala down il size proposto se il nuovo ticker è correlato ≥0.7 con
    posizioni esistenti.

    Logic: se corr(new_ticker, existing_ticker_i) ≥ corr_threshold,
    considera il nuovo ticker "duplicato" all'X% con existing_ticker_i
    (X = corr). Riduce la size proposta per evitare doppia esposizione
    mascherata da "diversificazione".

    Formula:
        effective_exposure = sum(weight_i × corr(new, i) per i in existing
                                 se corr >= threshold)
        scale_factor = max(0, 1 - effective_exposure × penalty_factor)
        adjusted_size = base_size × scale_factor

    Args:
        base_size_pct: proposed size (fraction, es. 0.08 = 8%)
        new_ticker: ticker che si vuole aggiungere
        existing_weights: dict {ticker: weight} delle posizioni esistenti
        corr_matrix: pd.DataFrame con correlazioni pairwise (colonne = ticker)
        corr_threshold: soglia sopra la quale scatta il penalty (default 0.7)
        penalty_factor: quanto penalizzare (1.0 = piena riduzione, 0.5 = metà)

    Returns: dict con ``adjusted_size_pct``, ``scale_factor``,
        ``correlated_pairs`` (list di (ticker, corr) above threshold),
        ``effective_exposure``.
    """
    if corr_matrix is None or corr_matrix.empty:
        return {
            "adjusted_size_pct": base_size_pct,
            "scale_factor": 1.0,
            "correlated_pairs": [],
            "effective_exposure": 0.0,
            "note": "no correlation matrix",
        }

    if new_ticker not in corr_matrix.columns:
        return {
            "adjusted_size_pct": base_size_pct,
            "scale_factor": 1.0,
            "correlated_pairs": [],
            "effective_exposure": 0.0,
            "note": f"{new_ticker} not in corr matrix",
        }

    correlated = []
    effective = 0.0
    for ticker, weight in existing_weights.items():
        if ticker == new_ticker:
            continue
        if ticker not in corr_matrix.columns:
            continue
        corr = corr_matrix.at[new_ticker, ticker]
        if pd.isna(corr):
            continue
        corr = float(corr)
        if abs(corr) >= corr_threshold:
            correlated.append((ticker, round(corr, 3)))
            effective += weight * abs(corr)

    scale_factor = max(0.0, 1.0 - effective * penalty_factor)
    adjusted = base_size_pct * scale_factor

    return {
        "adjusted_size_pct": round(adjusted, 4),
        "scale_factor": round(scale_factor, 4),
        "correlated_pairs": correlated,
        "effective_exposure": round(effective, 4),
    }


# ---------------------------------------------------------------------------
# Volatility targeting
# ---------------------------------------------------------------------------
def vol_target_scale(
    current_portfolio_vol: float,
    target_vol: float,
    floor_scale: float = 0.5,
    ceiling_scale: float = 1.5,
) -> dict:
    """Calcola scale factor per raggiungere target vol portfolio.

    Se ``current_vol > target_vol``, scala le posizioni down (portfolio
    troppo volatile per il target). Se ``current_vol < target_vol``, scala
    up — MA non super cap esistenti a livello single-name.

    Clamping:
    - ``floor_scale``: minimo (non ridurre sotto il 50% del nominal)
    - ``ceiling_scale``: massimo (non incrementare oltre il 150%, per safety)

    Args:
        current_portfolio_vol: vol annualized corrente (es. 0.12 = 12%)
        target_vol: vol target (es. 0.10 = 10%)

    Returns: dict con ``scale_factor``, ``recommendation`` ('scale_up' /
        'scale_down' / 'hold'), ``current_vol``, ``target_vol``.
    """
    if current_portfolio_vol <= 0 or target_vol <= 0:
        return {
            "scale_factor": 1.0,
            "recommendation": "hold",
            "note": "invalid inputs",
        }

    raw_scale = target_vol / current_portfolio_vol
    clamped = max(floor_scale, min(ceiling_scale, raw_scale))

    if clamped < 0.95:
        rec = "scale_down"
    elif clamped > 1.05:
        rec = "scale_up"
    else:
        rec = "hold"

    return {
        "scale_factor": round(clamped, 4),
        "raw_scale_factor": round(raw_scale, 4),
        "recommendation": rec,
        "current_vol": round(current_portfolio_vol, 4),
        "target_vol": round(target_vol, 4),
        "clamped": clamped != raw_scale,
    }


# ---------------------------------------------------------------------------
# Combined risk snapshot
# ---------------------------------------------------------------------------
def risk_snapshot(
    portfolio: dict,
    returns_df: pd.DataFrame | None = None,
    trades: list[dict] | None = None,
    target_vol: float = 0.15,
) -> dict:
    """Snapshot completo del rischio portfolio — combina VaR, vol, Kelly per-strategy.

    Args:
        portfolio: dict da load_portfolio (con positions + cash)
        returns_df: daily returns DataFrame (da download_returns o da cache).
            None → skippa VaR/vol.
        trades: journal trades per Kelly per-strategy. None → skippa Kelly.
        target_vol: target vol annualized (default 15%)

    Returns: dict aggregato pronto per display/dashboard.
    """
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)

    # Compute weights mark-to-cost
    total_invested = sum(
        float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
        for p in positions.values()
    )
    total_capital = cash + total_invested
    weights = {}
    if total_capital > 0:
        for ticker, p in positions.items():
            invested = float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
            weights[ticker] = invested / total_capital

    snapshot: dict[str, Any] = {
        "n_positions": len(positions),
        "total_capital": round(total_capital, 2),
        "invested_weight": round(sum(weights.values()), 4),
        "cash_weight": round(cash / total_capital, 4) if total_capital > 0 else 1.0,
    }

    if returns_df is not None and weights:
        snapshot["vol"] = portfolio_vol_annualized(returns_df, weights)
        snapshot["var"] = portfolio_var_95(returns_df, weights)
        if snapshot["vol"]["vol_annualized"] > 0:
            snapshot["vol_target"] = vol_target_scale(
                snapshot["vol"]["vol_annualized"],
                target_vol,
            )

    if trades is not None:
        # Kelly per ogni strategia distinta nel journal
        strategies = set(
            (t.get("strategy") or "").strip()
            for t in trades
            if t.get("status") == "closed" and t.get("strategy")
        )
        snapshot["kelly"] = {
            strat: strategy_kelly_from_trades(trades, strat)
            for strat in sorted(strategies)
        }

    return snapshot
