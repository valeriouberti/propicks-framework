"""Budget cap giornaliero sulle chiamate Anthropic — backend SQLite.

Persistenza: tabella ``daily_budget`` con una riga per giorno
``(date, calls, est_cost_usd, updated_at)``. Il bucket giornaliero si resetta
automaticamente al cambio di data (nuova riga viene creata lazy al primo
record_call del giorno).

Cache hit NON chiama ``record_call`` — il budget si spende solo quando
si tocca davvero l'API.
"""

from __future__ import annotations

from datetime import date

from propicks import config
from propicks.io.db import connect, transaction


class AIBudgetExceeded(RuntimeError):
    """Budget giornaliero superato: la chiamata non è stata fatta."""


def _today(day: str | None = None) -> str:
    return day or date.today().isoformat()


def _load_usage(day: str | None = None) -> dict:
    """Legge il counter del giorno dal DB. Zero se riga assente."""
    d = _today(day)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT calls, est_cost_usd FROM daily_budget WHERE date = ?",
            (d,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"calls": 0, "est_cost_usd": 0.0}
    return {
        "calls": int(row["calls"]),
        "est_cost_usd": float(row["est_cost_usd"]),
    }


def check_budget(day: str | None = None) -> dict:
    """Solleva ``AIBudgetExceeded`` se calls o cost sono sopra cap.

    Ritorna lo stato corrente dell'usage per logging/debug del chiamante.
    """
    usage = _load_usage(day)
    calls = int(usage.get("calls", 0))
    cost = float(usage.get("est_cost_usd", 0.0))

    if calls >= config.AI_MAX_CALLS_PER_DAY:
        raise AIBudgetExceeded(
            f"daily call limit reached: {calls}/{config.AI_MAX_CALLS_PER_DAY}. "
            f"Override con PROPICKS_AI_MAX_CALLS_PER_DAY=<N> o aspetta "
            f"mezzanotte UTC (reset giornaliero automatico)."
        )
    if cost >= config.AI_MAX_COST_USD_PER_DAY:
        raise AIBudgetExceeded(
            f"daily cost limit reached: ${cost:.2f}/${config.AI_MAX_COST_USD_PER_DAY:.2f}. "
            f"Override con PROPICKS_AI_MAX_COST_USD_PER_DAY=<USD>."
        )
    return usage


def record_call(
    est_cost_usd: float | None = None,
    *,
    day: str | None = None,
) -> dict:
    """Incrementa il counter giornaliero via UPSERT atomic.

    Se ``est_cost_usd`` è None, usa il default da config. Va chiamato
    SOLO per chiamate che hanno effettivamente toccato la rete — cache
    hit e skip di gate non spendono.
    """
    est_cost = (
        config.AI_EST_COST_PER_CALL_USD
        if est_cost_usd is None
        else float(est_cost_usd)
    )
    d = _today(day)

    with transaction() as conn:
        # UPSERT: crea la riga se non esiste, altrimenti incrementa.
        conn.execute(
            """INSERT INTO daily_budget (date, calls, est_cost_usd, updated_at)
               VALUES (?, 1, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(date) DO UPDATE SET
                 calls = calls + 1,
                 est_cost_usd = ROUND(est_cost_usd + ?, 4),
                 updated_at = CURRENT_TIMESTAMP""",
            (d, est_cost, est_cost),
        )
    # Ritorna lo stato aggiornato (secondo SELECT — più semplice del RETURNING)
    return _load_usage(day)


def current_usage(day: str | None = None) -> dict:
    """Legge lo stato corrente senza modifiche — utile per status/debug."""
    return _load_usage(day)
