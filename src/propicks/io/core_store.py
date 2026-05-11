"""Persistenza Core Portfolio — bucket long-term PIC/PAC isolato dal satellite.

Source of truth: tabelle ``core_holdings`` (stato corrente) + ``core_contributions``
(log append-only di tutte le movimentazioni) in SQLite.

## Modello

- ``core_holdings``: 1 riga per ticker. ``shares`` = somma cumulata da
  contributions. ``avg_cost`` = weighted average ricalcolato ad ogni mutation
  (denormalizzato per query rapide; true source resta ``core_contributions``).
- ``core_contributions``: append-only. Mai cancellato (audit). Kind:
  ``PIC`` (lump sum iniziale) / ``PAC`` (rata periodica) /
  ``DIVIDEND_REINVEST`` / ``SELL`` (shares negativo per parziali sell).

## Invariant

- Bucket **isolato** dal satellite: NON entra nei cap Stock 40% / ETF 60%.
- Nessun stop/target/AI/R/R. Risk model = buy & hold.
- ``avg_cost`` ricalcolato weighted: ``sum(amount + fees) / sum(shares)``
  su contributions con ``shares > 0`` (i SELL non alterano avg_cost).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from propicks.config import (
    CORE_CONTRIBUTION_KINDS,
    DATE_FMT,
)
from propicks.io.db import connect, transaction

# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------
def _row_to_holding(row) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "name": row["name"],
        "asset_class": row["asset_class"],
        "region": row["region"],
        "sector_key": row["sector_key"],
        "shares": float(row["shares"]),
        "avg_cost": float(row["avg_cost"]),
        "currency": row["currency"] or "EUR",
        "target_weight": (
            float(row["target_weight"]) if row["target_weight"] is not None else None
        ),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_contribution(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ticker": row["ticker"],
        "date": row["date"],
        "shares": float(row["shares"]),
        "price": float(row["price"]),
        "amount": float(row["amount"]),
        "fees": float(row["fees"]),
        "kind": row["kind"],
        "currency": row["currency"] or "EUR",
        "notes": row["notes"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------
def load_core() -> dict[str, dict[str, Any]]:
    """Carica tutte le core_holdings con shares > 0. Ritorna {ticker: holding}.

    Le holding con shares=0 (rimosse) restano in DB per audit ma vengono
    escluse dal load — non contano per exposure/drift.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM core_holdings WHERE shares > 0 ORDER BY ticker"
        ).fetchall()
    finally:
        conn.close()
    return {row["ticker"]: _row_to_holding(row) for row in rows}


def get_holding(ticker: str) -> dict[str, Any] | None:
    """Ritorna la holding (anche con shares=0) o None se mai vista."""
    ticker = ticker.upper()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_holding(row) if row else None


def list_contributions(
    ticker: str | None = None,
    since: str | None = None,
    *,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Storia contributions filtrata. Ordine ASC per date."""
    sql = "SELECT * FROM core_contributions WHERE 1=1"
    params: list[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    if since:
        sql += " AND date >= ?"
        params.append(since)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY date ASC, id ASC"
    conn = connect()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_contribution(r) for r in rows]


def total_contributed(ticker: str | None = None) -> float:
    """Somma ``amount + fees`` di tutte le contributions BUY (shares > 0) in
    valuta della contribution (no FX conversion qui — il caller decide se
    convertire). I SELL non sono inclusi.
    """
    sql = (
        "SELECT COALESCE(SUM(amount + fees), 0) AS total "
        "FROM core_contributions WHERE shares > 0"
    )
    params: list[Any] = []
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    conn = connect()
    try:
        row = conn.execute(sql, params).fetchone()
    finally:
        conn.close()
    return float(row["total"] or 0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _recompute_aggregates(conn, ticker: str) -> tuple[float, float]:
    """Ricalcola (shares_totali, avg_cost) da core_contributions.

    avg_cost = sum(amount + fees) / sum(shares) sui BUY (shares > 0).
    I SELL riducono shares_totali ma NON alterano avg_cost (LIFO/FIFO non
    modellato — semplificazione: cost basis weighted sui buy).
    """
    rows = conn.execute(
        "SELECT shares, amount, fees FROM core_contributions WHERE ticker = ?",
        (ticker,),
    ).fetchall()
    total_shares = 0.0
    buy_shares = 0.0
    buy_cost = 0.0
    for r in rows:
        sh = float(r["shares"])
        total_shares += sh
        if sh > 0:
            buy_shares += sh
            buy_cost += float(r["amount"]) + float(r["fees"])
    avg_cost = (buy_cost / buy_shares) if buy_shares > 0 else 0.0
    # Floor a 0 per evitare shares negative da errori di data entry
    return max(total_shares, 0.0), avg_cost


def _upsert_holding_aggregates(conn, ticker: str) -> None:
    """Aggiorna shares + avg_cost + updated_at su core_holdings dopo INSERT
    in core_contributions. Richiede che la riga holding esista già."""
    shares, avg_cost = _recompute_aggregates(conn, ticker)
    conn.execute(
        """UPDATE core_holdings
           SET shares = ?, avg_cost = ?, updated_at = CURRENT_TIMESTAMP
           WHERE ticker = ?""",
        (shares, round(avg_cost, 4), ticker),
    )


def _validate_kind(kind: str) -> str:
    kind_u = kind.upper()
    if kind_u not in CORE_CONTRIBUTION_KINDS:
        raise ValueError(
            f"kind '{kind}' non valido. Ammessi: {', '.join(CORE_CONTRIBUTION_KINDS)}"
        )
    return kind_u


# ---------------------------------------------------------------------------
# Mutating API
# ---------------------------------------------------------------------------
def add_holding(
    ticker: str,
    *,
    shares: float,
    price: float,
    name: str | None = None,
    asset_class: str | None = None,
    region: str | None = None,
    sector_key: str | None = None,
    currency: str | None = None,
    target_weight: float | None = None,
    notes: str | None = None,
    date: str | None = None,
    kind: str = "PIC",
    fees: float = 0.0,
) -> dict[str, Any]:
    """Crea una nuova holding con la prima contribution.

    Usato per il PIC iniziale (lump sum) o quando si comincia a tracciare
    un ticker su cui non si era ancora investito. Per aggiungere a una
    holding esistente usa ``add_contribution``.

    Idempotenza: se ``ticker`` esiste già, raise. Per top-up vedi
    ``add_contribution``.
    """
    ticker = ticker.upper()
    if shares <= 0:
        raise ValueError(f"shares deve essere > 0 (ricevuto {shares}).")
    if price <= 0:
        raise ValueError(f"price deve essere > 0 (ricevuto {price}).")
    if fees < 0:
        raise ValueError(f"fees deve essere >= 0 (ricevuto {fees}).")
    kind_u = _validate_kind(kind)
    if kind_u == "SELL":
        raise ValueError("add_holding non accetta kind=SELL. Apri con PIC/PAC.")

    if currency is None:
        from propicks.domain.currency import infer_currency
        currency = infer_currency(ticker)
    currency = currency.upper()

    date = date or datetime.now().strftime(DATE_FMT)
    amount = shares * price

    with transaction() as conn:
        # Refuse se già esiste con shares > 0
        existing = conn.execute(
            "SELECT shares FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
        if existing and float(existing["shares"] or 0) > 0:
            raise ValueError(
                f"Holding {ticker} già presente con {existing['shares']} shares. "
                f"Usa add_contribution() per top-up."
            )
        # Insert/replace holding row (shares verrà aggiornato da _upsert_aggregates)
        conn.execute(
            """INSERT INTO core_holdings (
                ticker, name, asset_class, region, sector_key,
                shares, avg_cost, currency, target_weight, notes
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name = COALESCE(excluded.name, core_holdings.name),
                asset_class = COALESCE(excluded.asset_class, core_holdings.asset_class),
                region = COALESCE(excluded.region, core_holdings.region),
                sector_key = COALESCE(excluded.sector_key, core_holdings.sector_key),
                currency = excluded.currency,
                target_weight = COALESCE(excluded.target_weight, core_holdings.target_weight),
                notes = COALESCE(excluded.notes, core_holdings.notes),
                updated_at = CURRENT_TIMESTAMP""",
            (
                ticker, name, asset_class, region, sector_key,
                currency, target_weight, notes,
            ),
        )
        conn.execute(
            """INSERT INTO core_contributions (
                ticker, date, shares, price, amount, fees, kind, currency, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, date, shares, price, amount, fees, kind_u, currency, notes),
        )
        _upsert_holding_aggregates(conn, ticker)
        row = conn.execute(
            "SELECT * FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
    return _row_to_holding(row)


def add_contribution(
    ticker: str,
    *,
    shares: float,
    price: float,
    kind: str = "PAC",
    date: str | None = None,
    fees: float = 0.0,
    notes: str | None = None,
) -> dict[str, Any]:
    """Append una contribution a una holding esistente.

    ``shares`` può essere negativo solo se ``kind=SELL`` (parziale sell).
    Per kind buy (PIC/PAC/DIVIDEND_REINVEST) deve essere > 0.
    """
    ticker = ticker.upper()
    kind_u = _validate_kind(kind)
    if price <= 0:
        raise ValueError(f"price deve essere > 0 (ricevuto {price}).")
    if fees < 0:
        raise ValueError(f"fees deve essere >= 0 (ricevuto {fees}).")
    if kind_u == "SELL":
        if shares >= 0:
            raise ValueError("kind=SELL richiede shares negativo (es. -5).")
    else:
        if shares <= 0:
            raise ValueError(
                f"kind={kind_u} richiede shares > 0 (ricevuto {shares}). "
                f"Per parziali sell usa kind=SELL con shares negativo."
            )

    date = date or datetime.now().strftime(DATE_FMT)
    amount = shares * price

    with transaction() as conn:
        existing = conn.execute(
            "SELECT shares, currency FROM core_holdings WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if not existing:
            raise ValueError(
                f"Holding {ticker} non esiste. Usa add_holding() per crearla."
            )
        # SELL non può portare shares < 0
        if kind_u == "SELL":
            new_total = float(existing["shares"]) + shares
            if new_total < 0:
                raise ValueError(
                    f"SELL di {abs(shares)} shares supera il posseduto "
                    f"({existing['shares']})."
                )
        currency = existing["currency"] or "EUR"
        conn.execute(
            """INSERT INTO core_contributions (
                ticker, date, shares, price, amount, fees, kind, currency, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, date, shares, price, amount, fees, kind_u, currency, notes),
        )
        _upsert_holding_aggregates(conn, ticker)
        row = conn.execute(
            "SELECT * FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
    return _row_to_holding(row)


def update_holding_meta(
    ticker: str,
    *,
    name: str | None = None,
    asset_class: str | None = None,
    region: str | None = None,
    sector_key: str | None = None,
    target_weight: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Aggiorna i metadati di classificazione (NO shares/avg_cost — quelli
    derivano da contributions). Passare None lascia il valore attuale."""
    ticker = ticker.upper()
    fields: list[tuple[str, Any]] = [
        ("name", name),
        ("asset_class", asset_class),
        ("region", region),
        ("sector_key", sector_key),
        ("target_weight", target_weight),
        ("notes", notes),
    ]
    setters = [f"{col} = ?" for col, val in fields if val is not None]
    if not setters:
        raise ValueError("Specifica almeno un campo da aggiornare.")
    params = [val for _, val in fields if val is not None]
    setters.append("updated_at = CURRENT_TIMESTAMP")
    params.append(ticker)

    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE core_holdings SET {', '.join(setters)} WHERE ticker = ?",
            params,
        )
        if cur.rowcount == 0:
            raise ValueError(f"Holding {ticker} non esiste.")
        row = conn.execute(
            "SELECT * FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
    return _row_to_holding(row)


def remove_holding(ticker: str, *, keep_history: bool = True) -> dict[str, Any]:
    """Rimuove una holding.

    ``keep_history=True`` (default): mantiene la riga in core_holdings con
    shares=0 e tutte le contributions per audit/storia. Equivalente a un
    full sell ma senza tracking del prezzo di uscita.

    ``keep_history=False``: HARD delete con CASCADE su contributions. Usalo
    solo per correggere errori di data entry. Distruttivo.
    """
    ticker = ticker.upper()
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM core_holdings WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row:
            raise ValueError(f"Holding {ticker} non esiste.")
        if keep_history:
            conn.execute(
                """UPDATE core_holdings SET shares = 0, updated_at = CURRENT_TIMESTAMP
                   WHERE ticker = ?""",
                (ticker,),
            )
        else:
            conn.execute("DELETE FROM core_holdings WHERE ticker = ?", (ticker,))
            # CASCADE rimuove core_contributions automaticamente (FK ON DELETE CASCADE)
    return _row_to_holding(row)


def total_core_value_eur(prices_eur: dict[str, float]) -> float:
    """Valore di mercato totale del core in EUR.

    ``prices_eur`` deve già contenere prezzi convertiti in EUR (caller
    responsabile della FX conversion via ``domain.currency``). Skippa
    ticker senza prezzo.
    """
    holdings = load_core()
    total = 0.0
    for ticker, h in holdings.items():
        px = prices_eur.get(ticker)
        if px is None:
            continue
        total += float(h["shares"]) * float(px)
    return total
