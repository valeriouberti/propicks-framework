"""Coordinator che tiene allineati ``journal.json`` e ``portfolio.json``.

I due store restano indipendenti (separation of concerns: journal è l'append-log
immutabile, portfolio è lo stato corrente), ma operazioni di apertura/chiusura
trade devono scrivere in entrambi. Questo modulo è l'unico punto da cui passano
quelle operazioni coordinate.

Policy di robustezza (deliberate, documentate):

- **Apertura**: journal viene scritto per primo. Se ``add_position`` fallisce
  (cash insufficiente, violazione risk rules, ticker già in portfolio),
  il journal resta scritto e viene ritornato un warning. Motivazione: il
  trade reale *è* aperto sul broker — il record deve esistere a prescindere
  da cosa dice il portfolio tracker.
- **Chiusura**: journal exit viene scritto per primo. Se la posizione non
  è presente nel portfolio (mai sincronizzata, o già rimossa manualmente),
  il journal viene comunque chiuso e ritornato un warning. La chiusura
  journal è la fonte di verità per il P&L.
- **Idempotenza**: se la posizione è già in portfolio quando apri (es.
  creata via ``propicks-portfolio add`` prima), il journal viene scritto
  ma il portfolio non duplicato. Warning informativo.
"""

from __future__ import annotations

from propicks.io import journal_store
from propicks.io.portfolio_store import (
    add_position,
    close_position,
    load_portfolio,
)


def open_trade(
    *,
    ticker: str,
    direction: str,
    entry_price: float,
    entry_date: str,
    shares: int,
    stop_loss: float,
    target: float | None = None,
    score_claude: int | None = None,
    score_tech: int | None = None,
    strategy: str | None = None,
    catalyst: str | None = None,
    notes: str | None = None,
) -> tuple[dict, dict | None, list[str]]:
    """Apre un trade: scrive journal e sincronizza portfolio.

    Returns:
        (trade, position, warnings) — ``trade`` è il record journal sempre
        scritto; ``position`` è il dict portfolio se la sync è riuscita,
        None altrimenti; ``warnings`` contiene messaggi informativi da
        mostrare al trader (posizione già esistente, risk check fallito,
        ecc.). Solleva ``ValueError`` solo se la validazione journal
        fallisce (stop >= entry, shares <= 0, date invalide).
    """
    ticker_up = ticker.upper()
    warnings: list[str] = []

    # 1. Journal first — append-only, source of truth per il P&L
    trade = journal_store.add_trade(
        ticker=ticker_up,
        direction=direction,
        entry_price=entry_price,
        entry_date=entry_date,
        stop_loss=stop_loss,
        target=target,
        score_claude=score_claude,
        score_tech=score_tech,
        strategy=strategy,
        catalyst=catalyst,
        notes=notes,
        shares=shares,
    )

    # 2. Portfolio sync — può fallire, journal resta scritto
    position: dict | None = None
    portfolio = load_portfolio()
    if ticker_up in portfolio.get("positions", {}):
        warnings.append(
            f"{ticker_up} già in portfolio: journal scritto, portfolio invariato."
        )
    else:
        try:
            position = add_position(
                portfolio,
                ticker=ticker_up,
                entry_price=entry_price,
                shares=shares,
                stop_loss=stop_loss,
                target=target,
                strategy=strategy,
                score_claude=score_claude,
                score_tech=score_tech,
                catalyst=catalyst,
                entry_date=entry_date,
            )
        except ValueError as exc:
            warnings.append(
                f"Portfolio non aggiornato ({exc}). Il trade è nel journal; "
                f"correggi manualmente con propicks-portfolio add."
            )
    return trade, position, warnings


def close_trade(
    *,
    ticker: str,
    exit_price: float,
    exit_date: str | None = None,
    reason: str | None = None,
    notes: str | None = None,
) -> tuple[dict, dict | None, list[str]]:
    """Chiude un trade: scrive exit su journal e rimuove dal portfolio.

    Returns:
        (trade, removed_position, warnings) — ``trade`` è sempre il record
        journal aggiornato con ``exit_*``; ``removed_position`` è la
        posizione rimossa dal portfolio (con cash rimborsato a
        ``shares*exit_price``), None se la posizione non era presente.
    """
    ticker_up = ticker.upper()
    warnings: list[str] = []

    # 1. Journal close (source of truth per P&L)
    trade = journal_store.close_trade(
        ticker=ticker_up,
        exit_price=exit_price,
        exit_date=exit_date,
        reason=reason,
        notes=notes,
    )

    # 2. Portfolio close con cash accounting corretto (proceeds, non cost)
    removed: dict | None = None
    portfolio = load_portfolio()
    if ticker_up not in portfolio.get("positions", {}):
        warnings.append(
            f"{ticker_up} non in portfolio: journal chiuso, cash invariato. "
            f"(La posizione era stata rimossa manualmente o mai sincronizzata.)"
        )
    else:
        try:
            removed = close_position(portfolio, ticker_up, exit_price)
        except ValueError as exc:
            warnings.append(f"Portfolio close fallito ({exc}).")
    return trade, removed, warnings
