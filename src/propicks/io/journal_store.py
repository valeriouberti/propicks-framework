"""Persistenza append-only del journal dei trade — backend SQLite.

I trade non vengono mai cancellati: ``close_trade`` aggiorna i campi ``exit_*``
sulla stessa riga senza rimuoverla. La tabella ``trades`` ha PK auto-increment,
conservando l'ordine di inserimento storico.

API pubblica invariata rispetto alla versione JSON:
- ``load_journal() -> list[dict]``
- ``find_open(trades, ticker) -> dict | None``
- ``add_trade(...)`` / ``close_trade(...)``

Forma dei dict ritornati è compatibile byte-per-byte con la vecchia versione
JSON per non rompere report, dashboard, journal stats, backtest engine.
"""

from __future__ import annotations

from datetime import datetime

from propicks.config import DATE_FMT
from propicks.domain.validation import validate_date, validate_scores
from propicks.io.db import connect, transaction


# ---------------------------------------------------------------------------
# Row ↔ dict converter
# ---------------------------------------------------------------------------
def _row_to_trade_dict(row) -> dict:
    """Converte una riga di ``trades`` nel dict legacy-compatibile."""
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "direction": row["direction"],
        "strategy": row["strategy"],
        "entry_date": row["entry_date"],
        "entry_price": row["entry_price"],
        "shares": row["shares"],
        "stop_loss": row["stop_loss"],
        "target": row["target"],
        "score_claude": row["score_claude"],
        "score_tech": row["score_tech"],
        "catalyst": row["catalyst"],
        "notes": row["notes"],
        "status": row["status"],
        "exit_price": row["exit_price"],
        "exit_date": row["exit_date"],
        "exit_reason": row["exit_reason"],
        "pnl_pct": row["pnl_pct"],
        "pnl_per_share": row["pnl_per_share"],
        "duration_days": row["duration_days"],
        "post_trade_notes": row["post_trade_notes"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------
def load_journal() -> list[dict]:
    """Carica tutti i trade in ordine di ``id`` crescente (= ordine cronologico).

    Ritorna sempre ``list[dict]`` compatibile con la vecchia API JSON.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_trade_dict(r) for r in rows]


def find_open(trades: list[dict], ticker: str) -> dict | None:
    """Trova il trade aperto (status='open') per un ticker, o None.

    Prende la lista ``trades`` come parametro per preservare la firma storica
    — i caller la hanno già in memoria, evitiamo SELECT aggiuntivo.
    """
    ticker = ticker.upper()
    for t in trades:
        if t.get("ticker") == ticker and t.get("status") == "open":
            return t
    return None


# ---------------------------------------------------------------------------
# Mutating API
# ---------------------------------------------------------------------------
def add_trade(
    ticker: str,
    direction: str,
    entry_price: float,
    entry_date: str,
    stop_loss: float,
    target: float | None,
    score_claude: int | None,
    score_tech: int | None,
    strategy: str | None,
    catalyst: str | None,
    notes: str | None = None,
    shares: int | None = None,
) -> dict:
    """Inserisce un nuovo trade nel journal con tutte le validazioni.

    Validazioni identiche alla versione JSON:
    - ticker senza trade aperto esistente
    - stop_loss vs entry_price coerente con direction
    - shares > 0 se fornito
    - score_claude/score_tech via validate_scores

    Returns il dict del trade inserito (con id assegnato dal DB).
    """
    ticker = ticker.upper()

    # Controllo trade aperto su questo ticker — unica SELECT prima del write.
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM trades WHERE ticker = ? AND status = 'open'",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if existing:
        raise ValueError(f"Esiste già un trade aperto per {ticker}.")

    if stop_loss >= entry_price and direction == "long":
        raise ValueError("Per un long, stop_loss deve essere < entry_price.")
    if direction == "short" and stop_loss <= entry_price:
        raise ValueError("Per uno short, stop_loss deve essere > entry_price.")
    if shares is not None and shares <= 0:
        raise ValueError(f"shares deve essere > 0 (ricevuto {shares}).")
    validate_scores(score_claude, score_tech)

    entry_date_val = validate_date(entry_date)
    # ISO completo con secondi: serve a sqlite3.PARSE_DECLTYPES per colonne
    # TIMESTAMP (altrimenti "not enough values to unpack" su secondi mancanti).
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO trades (
                ticker, direction, strategy, entry_date, entry_price,
                shares, stop_loss, target, score_claude, score_tech,
                catalyst, notes, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
            (
                ticker,
                direction,
                strategy,
                entry_date_val,
                round(entry_price, 2),
                int(shares) if shares is not None else None,
                round(stop_loss, 2),
                round(target, 2) if target is not None else None,
                score_claude,
                score_tech,
                catalyst,
                notes,
                created_at,
            ),
        )
        new_id = cur.lastrowid

    return {
        "id": new_id,
        "ticker": ticker,
        "direction": direction,
        "strategy": strategy,
        "entry_price": round(entry_price, 2),
        "entry_date": entry_date_val,
        "shares": int(shares) if shares is not None else None,
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2) if target is not None else None,
        "score_claude": score_claude,
        "score_tech": score_tech,
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
        "created_at": created_at,
    }


def close_trade(
    ticker: str,
    exit_price: float,
    exit_date: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
) -> dict:
    """Chiude un trade aperto calcolando P&L + duration in giorni."""
    ticker = ticker.upper()

    # Carica il trade aperto su ticker (necessario per P&L calc)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE ticker = ? AND status = 'open'",
            (ticker,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"Nessun trade aperto per {ticker}.")

    trade = _row_to_trade_dict(row)
    exit_date_val = (
        validate_date(exit_date) if exit_date else datetime.now().strftime(DATE_FMT)
    )

    entry = trade["entry_price"]
    direction = trade.get("direction", "long")
    if direction == "long":
        pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0
        pnl_per_share = exit_price - entry
    else:
        pnl_pct = (entry - exit_price) / entry * 100 if entry else 0.0
        pnl_per_share = entry - exit_price

    d_entry = datetime.strptime(trade["entry_date"], DATE_FMT)
    d_exit = datetime.strptime(exit_date_val, DATE_FMT)
    if d_exit < d_entry:
        raise ValueError(
            f"exit_date {exit_date_val} precede entry_date {trade['entry_date']}."
        )
    duration = (d_exit - d_entry).days

    updates = {
        "status": "closed",
        "exit_price": round(exit_price, 2),
        "exit_date": exit_date_val,
        "exit_reason": reason,
        "pnl_pct": round(pnl_pct, 4),
        "pnl_per_share": round(pnl_per_share, 2),
        "duration_days": duration,
        "post_trade_notes": notes,
    }

    with transaction() as conn:
        conn.execute(
            """UPDATE trades SET
                status = ?, exit_price = ?, exit_date = ?, exit_reason = ?,
                pnl_pct = ?, pnl_per_share = ?, duration_days = ?,
                post_trade_notes = ?
               WHERE id = ?""",
            (
                updates["status"],
                updates["exit_price"],
                updates["exit_date"],
                updates["exit_reason"],
                updates["pnl_pct"],
                updates["pnl_per_share"],
                updates["duration_days"],
                updates["post_trade_notes"],
                trade["id"],
            ),
        )

    trade.update(updates)
    return trade
