"""Persistenza e mutazioni del portafoglio.

Schema data/portfolio.json:
    {"positions": {TICKER: {...}}, "cash": float, "last_updated": str|None}
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from propicks.config import (
    CAPITAL,
    DATE_FMT,
    MAX_LOSS_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
    MIN_SCORE_CLAUDE,
    MIN_SCORE_TECH,
    PORTFOLIO_FILE,
)
from propicks.domain.sizing import portfolio_value
from propicks.domain.validation import validate_scores
from propicks.io.atomic import atomic_write_json


def _default_portfolio() -> dict:
    return {"positions": {}, "cash": CAPITAL, "last_updated": None}


def load_portfolio() -> dict:
    """Carica il portafoglio, migrando schema legacy (positions come lista)."""
    if not os.path.exists(PORTFOLIO_FILE):
        pf = _default_portfolio()
        save_portfolio(pf)
        return pf

    try:
        with open(PORTFOLIO_FILE) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"[fatal] portfolio.json corrotto: {exc}. "
            f"Ripristina da backup o correggi manualmente."
        )

    if isinstance(data.get("positions"), list):
        positions = {p["ticker"]: {k: v for k, v in p.items() if k != "ticker"}
                     for p in data.get("positions", [])}
        cash = float(data.get("cash") or data.get("capital_current") or CAPITAL)
        last = data.get("last_updated") or data.get("last_update")
        data = {"positions": positions, "cash": cash, "last_updated": last}

    data.setdefault("positions", {})
    data.setdefault("cash", CAPITAL)
    data.setdefault("last_updated", None)
    return data


def save_portfolio(portfolio: dict) -> None:
    portfolio["last_updated"] = datetime.now().strftime(DATE_FMT)
    atomic_write_json(PORTFOLIO_FILE, portfolio)


def unrealized_pl(portfolio: dict) -> tuple[float, dict[str, float]]:
    """Ritorna (P&L unrealized totale, mappa ticker→prezzo corrente)."""
    from propicks.market.yfinance_client import get_current_prices

    positions = portfolio.get("positions", {})
    if not positions:
        return 0.0, {}
    prices = get_current_prices(list(positions.keys()))
    total = 0.0
    for ticker, p in positions.items():
        cur = prices.get(ticker)
        if cur is not None:
            total += (cur - p["entry_price"]) * p["shares"]
    return total, prices


def add_position(
    portfolio: dict,
    ticker: str,
    entry_price: float,
    shares: int,
    stop_loss: float,
    target: Optional[float],
    strategy: Optional[str],
    score_claude: Optional[int],
    score_tech: Optional[int],
    catalyst: Optional[str],
    entry_date: Optional[str] = None,
) -> dict:
    ticker = ticker.upper()
    positions = portfolio.setdefault("positions", {})

    if ticker in positions:
        raise ValueError(f"Posizione già aperta su {ticker}.")
    if len(positions) >= MAX_POSITIONS:
        raise ValueError(f"Portafoglio pieno: {MAX_POSITIONS} posizioni.")
    if shares <= 0:
        raise ValueError(f"shares deve essere > 0 (ricevuto {shares}).")
    if stop_loss >= entry_price:
        raise ValueError(
            f"stop_loss {stop_loss:.2f} >= entry {entry_price:.2f}: invalido per long."
        )
    validate_scores(score_claude, score_tech)

    cost = shares * entry_price
    cash = float(portfolio.get("cash") or 0)
    if cost > cash:
        raise ValueError(
            f"Cash insufficiente: servono {cost:.2f}, disponibili {cash:.2f}."
        )

    total = portfolio_value(portfolio)
    if cost > total * MAX_POSITION_SIZE_PCT:
        raise ValueError(
            f"Size {cost/total*100:.1f}% supera il limite "
            f"{MAX_POSITION_SIZE_PCT*100:.0f}% per posizione."
        )
    new_cash = cash - cost
    if new_cash < total * MIN_CASH_RESERVE_PCT:
        raise ValueError(
            f"Apertura violerebbe la riserva cash minima "
            f"({MIN_CASH_RESERVE_PCT*100:.0f}%): cash residuo {new_cash:.2f} "
            f"< {total * MIN_CASH_RESERVE_PCT:.2f}."
        )
    risk_pct_trade = (entry_price - stop_loss) / entry_price
    if risk_pct_trade > MAX_LOSS_PER_TRADE_PCT:
        raise ValueError(
            f"Stop distante {risk_pct_trade*100:.2f}% > limite "
            f"{MAX_LOSS_PER_TRADE_PCT*100:.0f}% per trade."
        )
    if score_claude is not None and score_claude < MIN_SCORE_CLAUDE:
        raise ValueError(
            f"score_claude {score_claude} < soglia minima {MIN_SCORE_CLAUDE}."
        )
    if score_tech is not None and score_tech < MIN_SCORE_TECH:
        raise ValueError(
            f"score_tech {score_tech} < soglia minima {MIN_SCORE_TECH}."
        )

    entry_date = entry_date or datetime.now().strftime(DATE_FMT)
    positions[ticker] = {
        "entry_price": round(entry_price, 2),
        "entry_date": entry_date,
        "shares": int(shares),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2) if target is not None else None,
        "strategy": strategy,
        "score_claude": score_claude,
        "score_tech": score_tech,
        "catalyst": catalyst,
    }
    portfolio["cash"] = round(cash - cost, 2)
    save_portfolio(portfolio)
    return positions[ticker]


def remove_position(portfolio: dict, ticker: str) -> dict:
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(f"Nessuna posizione aperta su {ticker}.")
    pos = positions.pop(ticker)
    refund = pos["shares"] * pos["entry_price"]
    portfolio["cash"] = round(float(portfolio.get("cash") or 0) + refund, 2)
    save_portfolio(portfolio)
    return pos


def update_position(
    portfolio: dict,
    ticker: str,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    highest_price: Optional[float] = None,
    trailing_enabled: Optional[bool] = None,
) -> dict:
    ticker = ticker.upper()
    positions = portfolio.get("positions", {})
    if ticker not in positions:
        raise ValueError(f"Nessuna posizione aperta su {ticker}.")
    fields = (stop_loss, target, highest_price, trailing_enabled)
    if all(f is None for f in fields):
        raise ValueError("Specificare almeno un campo da aggiornare.")
    pos = positions[ticker]
    if stop_loss is not None:
        pos["stop_loss"] = round(stop_loss, 2)
    if target is not None:
        pos["target"] = round(target, 2)
    if highest_price is not None:
        pos["highest_price_since_entry"] = round(highest_price, 2)
    if trailing_enabled is not None:
        pos["trailing_enabled"] = bool(trailing_enabled)
    save_portfolio(portfolio)
    return pos
