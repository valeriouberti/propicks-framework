"""Gestione di trade in vita: trailing stop e time stop.

Layer puro: funzioni che prendono numeri/stringhe e ritornano suggerimenti.
L'applicazione (update del file portfolio) è responsabilità della CLI.

**Trailing stop**: ATR-based, ratchet-up only. Non scende mai — se il prezzo
recede dal massimo raggiunto, lo stop resta fermo. Questo è il comportamento
standard: il trailing protegge il profitto accumulato, non accompagna il
ripiegamento.

**Time stop**: se il trade è flat (|P&L| sotto una soglia) da N giorni,
chiudi. Rationale: il costo-opportunità di tenere capitale fermo su un trade
che non va da nessuna parte è reale, anche se il P&L mark-to-market è nullo.
"""

from __future__ import annotations

from datetime import date, datetime

from propicks.config import DATE_FMT

DEFAULT_TRAILING_ATR_MULT: float = 2.0
DEFAULT_TIME_STOP_DAYS: int = 30
DEFAULT_FLAT_THRESHOLD_PCT: float = 0.02  # 2% in valore assoluto


def compute_trailing_stop(
    entry_price: float,
    highest_price_since_entry: float,
    current_atr: float,
    current_stop: float,
    atr_mult: float = DEFAULT_TRAILING_ATR_MULT,
    activation_r_multiple: float = 1.0,
) -> float:
    """Suggerisce il nuovo livello di stop trailing, ratchet-up only.

    Logica:
    - Fino a quando il prezzo non raggiunge entry + 1R (1x la distanza iniziale
      stop→entry), lo stop iniziale resta invariato. **Rationale**: muovere lo
      stop troppo presto trasforma uno swing legittimo in uno stop-out rumoroso.
      Aspettiamo che il trade sia in guadagno significativo.
    - Oltre soglia: ``proposed = highest_price - atr_mult * current_atr``.
      Il nuovo stop è ``max(current_stop, proposed)`` — MAI scende.

    Parametri:
        activation_r_multiple: soglia in multipli di R (initial risk)
            prima di attivare il trailing. 1.0 = stop iniziale coincide con
            1R, attiva quando prezzo >= entry + 1R.
    """
    initial_risk = entry_price - current_stop
    if initial_risk <= 0:
        # Stop già sopra entry o uguale: non tocchiamo (scenario degenere)
        return current_stop

    activation_price = entry_price + activation_r_multiple * initial_risk
    if highest_price_since_entry < activation_price:
        return current_stop

    proposed = highest_price_since_entry - atr_mult * current_atr
    return max(current_stop, round(proposed, 2))


def check_time_stop(
    entry_date_str: str,
    entry_price: float,
    current_date: date,
    current_price: float,
    max_days_flat: int = DEFAULT_TIME_STOP_DAYS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> bool:
    """True se il trade è flat da troppo tempo → suggerisci chiusura.

    "Flat" = |P&L%| < flat_threshold_pct. Un trade in guadagno di +5% non è
    flat anche se vecchio; un trade a -1% da 40 giorni sì.
    """
    try:
        entry_date = datetime.strptime(entry_date_str, DATE_FMT).date()
    except (ValueError, TypeError):
        return False

    days_held = (current_date - entry_date).days
    if days_held < max_days_flat:
        return False

    pnl_pct = (current_price - entry_price) / entry_price
    return abs(pnl_pct) < flat_threshold_pct


def suggest_stop_update(
    position: dict,
    current_price: float,
    current_atr: float,
    current_date: date | None = None,
    atr_mult: float = DEFAULT_TRAILING_ATR_MULT,
    max_days_flat: int = DEFAULT_TIME_STOP_DAYS,
    flat_threshold_pct: float = DEFAULT_FLAT_THRESHOLD_PCT,
) -> dict:
    """Valutazione unificata: trailing stop + time stop su una posizione.

    ``position`` è il dict del portfolio (schema portfolio_store), con
    opzionalmente ``highest_price_since_entry`` e ``trailing_enabled``.
    Se ``highest_price_since_entry`` manca, viene inizializzato al massimo
    tra entry_price e current_price.

    Ritorna dict con:
        - new_stop: float | None (None se nessun aggiornamento)
        - stop_changed: bool
        - time_stop_triggered: bool
        - highest_price: float (aggiornato)
        - rationale: list[str]
    """
    if current_date is None:
        current_date = date.today()

    entry_price = float(position["entry_price"])
    current_stop = float(position["stop_loss"])
    highest_prev = float(
        position.get("highest_price_since_entry") or max(entry_price, current_price)
    )
    highest = max(highest_prev, current_price)

    rationale: list[str] = []
    new_stop: float | None = None

    trailing_enabled = bool(position.get("trailing_enabled", False))
    if trailing_enabled and current_atr > 0:
        proposed = compute_trailing_stop(
            entry_price=entry_price,
            highest_price_since_entry=highest,
            current_atr=current_atr,
            current_stop=current_stop,
            atr_mult=atr_mult,
        )
        if proposed > current_stop:
            new_stop = proposed
            rationale.append(
                f"Trailing: stop {current_stop:.2f} -> {proposed:.2f} "
                f"(highest {highest:.2f} - {atr_mult}xATR {current_atr:.2f})"
            )

    time_stop = check_time_stop(
        entry_date_str=position["entry_date"],
        entry_price=entry_price,
        current_date=current_date,
        current_price=current_price,
        max_days_flat=max_days_flat,
        flat_threshold_pct=flat_threshold_pct,
    )
    if time_stop:
        rationale.append(
            f"Time stop: trade flat da >= {max_days_flat}gg, "
            f"P&L attuale {(current_price - entry_price) / entry_price * 100:+.2f}%"
        )

    return {
        "new_stop": new_stop,
        "stop_changed": new_stop is not None,
        "time_stop_triggered": time_stop,
        "highest_price": round(highest, 2),
        "rationale": rationale,
    }
