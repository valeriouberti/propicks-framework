"""Persistenza append-only del journal dei trade.

I trade non vengono mai cancellati: ``close_trade`` aggiunge i campi
``exit_*`` al record esistente senza rimuoverlo.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from propicks.config import DATE_FMT, JOURNAL_FILE
from propicks.domain.validation import validate_date, validate_scores
from propicks.io.atomic import atomic_write_json


def load_journal() -> list[dict]:
    """Carica il journal. Supporta array puro e schema legacy {"trades": [...]}.

    Migra la chiave legacy ``pnl_abs`` (valore per-share) → ``pnl_per_share``.
    """
    if not os.path.exists(JOURNAL_FILE):
        _save_journal([])
        return []

    try:
        with open(JOURNAL_FILE) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"[fatal] journal.json corrotto: {exc}. "
            f"Ripristina da backup o correggi manualmente."
        )

    if isinstance(data, dict) and "trades" in data:
        data = data["trades"]
    if not isinstance(data, list):
        raise ValueError("Formato journal.json non valido.")

    for t in data:
        if "pnl_abs" in t and "pnl_per_share" not in t:
            t["pnl_per_share"] = t.pop("pnl_abs")
    return data


def _save_journal(trades: list[dict]) -> None:
    atomic_write_json(JOURNAL_FILE, trades)


def _next_id(trades: list[dict]) -> int:
    return max((t.get("id", 0) for t in trades), default=0) + 1


def find_open(trades: list[dict], ticker: str) -> Optional[dict]:
    ticker = ticker.upper()
    for t in trades:
        if t.get("ticker") == ticker and t.get("status") == "open":
            return t
    return None


def add_trade(
    ticker: str,
    direction: str,
    entry_price: float,
    entry_date: str,
    stop_loss: float,
    target: Optional[float],
    score_claude: Optional[int],
    score_tech: Optional[int],
    strategy: Optional[str],
    catalyst: Optional[str],
    notes: Optional[str] = None,
    shares: Optional[int] = None,
) -> dict:
    trades = load_journal()
    ticker = ticker.upper()

    if find_open(trades, ticker):
        raise ValueError(f"Esiste già un trade aperto per {ticker}.")
    if stop_loss >= entry_price and direction == "long":
        raise ValueError("Per un long, stop_loss deve essere < entry_price.")
    if direction == "short" and stop_loss <= entry_price:
        raise ValueError("Per uno short, stop_loss deve essere > entry_price.")
    if shares is not None and shares <= 0:
        raise ValueError(f"shares deve essere > 0 (ricevuto {shares}).")
    validate_scores(score_claude, score_tech)

    trade = {
        "id": _next_id(trades),
        "ticker": ticker,
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "entry_date": validate_date(entry_date),
        "shares": int(shares) if shares is not None else None,
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2) if target is not None else None,
        "score_claude": score_claude,
        "score_tech": score_tech,
        "strategy": strategy,
        "catalyst": catalyst,
        "notes": notes,
        "status": "open",
        "exit_price": None,
        "exit_date": None,
        "exit_reason": None,
        "pnl_pct": None,
        "pnl_per_share": None,
        "duration_days": None,
        "post_trade_notes": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    trades.append(trade)
    _save_journal(trades)
    return trade


def close_trade(
    ticker: str,
    exit_price: float,
    exit_date: Optional[str] = None,
    reason: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    trades = load_journal()
    trade = find_open(trades, ticker)
    if not trade:
        raise ValueError(f"Nessun trade aperto per {ticker.upper()}.")

    exit_date = validate_date(exit_date) if exit_date else datetime.now().strftime(DATE_FMT)

    entry = trade["entry_price"]
    direction = trade.get("direction", "long")
    if direction == "long":
        pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0
        pnl_per_share = exit_price - entry
    else:
        pnl_pct = (entry - exit_price) / entry * 100 if entry else 0.0
        pnl_per_share = entry - exit_price

    d_entry = datetime.strptime(trade["entry_date"], DATE_FMT)
    d_exit = datetime.strptime(exit_date, DATE_FMT)
    if d_exit < d_entry:
        raise ValueError(
            f"exit_date {exit_date} precede entry_date {trade['entry_date']}."
        )
    duration = (d_exit - d_entry).days

    trade.update({
        "status": "closed",
        "exit_price": round(exit_price, 2),
        "exit_date": exit_date,
        "exit_reason": reason,
        "pnl_pct": round(pnl_pct, 4),
        "pnl_per_share": round(pnl_per_share, 2),
        "duration_days": duration,
        "post_trade_notes": notes,
    })
    _save_journal(trades)
    return trade
