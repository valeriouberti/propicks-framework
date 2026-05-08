"""Broker CSV/TSV import + reconciliation.

Supporta formato "Portafoglio di sintesi" (tab-separated o multi-space) tipico
di broker IT (Fineco, Directa, Degiro EU). Header row contiene almeno:
``Titolo``, ``Simbolo``, ``Quantità``, ``P.zo medio di carico``.

Workflow:
1. ``parse_broker_paste(raw)`` → list[BrokerPosition] (ticker/shares/entry/isin)
2. ``reconcile_with_portfolio(broker_positions, portfolio)`` → diff dict con:
   - in_sync: posizioni allineate
   - drift: shares o entry mismatch
   - only_broker: presenti in broker ma non in portfolio (nuove entry da
     creare o sync mancante)
   - only_portfolio: presenti in portfolio ma non in broker (chiuse o
     rinominate)
3. UI dashboard: mostra diff + apply buttons per ogni discrepanza.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class BrokerPosition:
    """Una riga del broker statement."""
    ticker: str
    shares: float
    entry_price: float
    isin: str | None = None
    strumento: str | None = None  # Azione / ETF
    valuta: str | None = None     # EUR / USD
    titolo: str | None = None     # nome esteso
    valore_carico: float | None = None
    valore_mercato: float | None = None


# Header column names (case-insensitive, stripped). Mapping to BrokerPosition fields.
_HEADER_ALIASES = {
    "titolo": "titolo",
    "isin": "isin",
    "simbolo": "ticker",
    "ticker": "ticker",
    "strumento": "strumento",
    "valuta": "valuta",
    "quantità": "shares",
    "quantita": "shares",
    "qty": "shares",
    "p.zo medio di carico": "entry_price",
    "prezzo medio di carico": "entry_price",
    "prezzo medio": "entry_price",
    "avg price": "entry_price",
    "valore di carico": "valore_carico",
    "valore di mercato €": "valore_mercato",
    "valore di mercato": "valore_mercato",
}


def _split_row(line: str) -> list[str]:
    """Split row by tab OR multi-space. Tollera entrambi i formati paste Excel.

    Excel paste produce tab-separated. Alcuni broker output multi-space.
    """
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    # Fallback: split on 2+ spaces (broker text export style)
    return [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]


def _parse_number(s: str) -> float | None:
    """Parse numero IT-style: '1.234,56' o '1,234.56' o '1234.56'.

    Strategia: rimuove spazi, decide separatore decimale by ultima occorrenza.
    """
    if not s or s in ("—", "-", "n/a", ""):
        return None
    s = s.replace(" ", "").replace("€", "").replace("$", "").strip()
    if not s:
        return None
    # Detect decimal separator
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        # Es. "1.234,56" (IT) o "1,234.56" (EN). Decimal = ultimo separatore.
        if s.rfind(",") > s.rfind("."):
            # IT: rimuovi punti (thousand sep), comma → dot
            s = s.replace(".", "").replace(",", ".")
        else:
            # EN: rimuovi virgole (thousand sep)
            s = s.replace(",", "")
    elif has_comma:
        # Solo virgola: assume decimal IT (es. "10,27")
        s = s.replace(",", ".")
    # else: solo punto, già ok
    try:
        return float(s)
    except ValueError:
        return None


def parse_broker_paste(raw: str) -> tuple[list[BrokerPosition], list[str]]:
    """Parse paste tab-separated o multi-space.

    Returns:
        (positions, warnings): warnings = righe skippate o malformed.
    """
    positions: list[BrokerPosition] = []
    warnings: list[str] = []

    if not raw or not raw.strip():
        return positions, ["Input vuoto"]

    lines = [ln for ln in raw.splitlines() if ln.strip()]

    # Find header row (first che contiene 'Simbolo' o 'Ticker' + 'Quantità')
    header_idx = None
    header_cols: list[str] = []
    for i, ln in enumerate(lines):
        cols = _split_row(ln)
        cols_lower = [c.lower() for c in cols]
        if any(c in cols_lower for c in ("simbolo", "ticker")) and any(
            c.startswith("quant") for c in cols_lower
        ):
            header_idx = i
            header_cols = cols_lower
            break

    if header_idx is None:
        return positions, [
            "Header row non trovata (richiesto 'Simbolo' + 'Quantità'). "
            "Verifica che il paste includa la riga di intestazione."
        ]

    # Map column index → field name
    col_map: dict[int, str] = {}
    for idx, col in enumerate(header_cols):
        if col in _HEADER_ALIASES:
            col_map[idx] = _HEADER_ALIASES[col]

    if "ticker" not in col_map.values() or "shares" not in col_map.values():
        return positions, [
            f"Colonne obbligatorie mancanti. Trovate: {col_map.values()}"
        ]

    # Parse data rows (skip header + footer "Totale")
    for ln in lines[header_idx + 1:]:
        cols = _split_row(ln)
        if not cols:
            continue
        # Skip footer rows: "Totale" / "EUR ..." con poche colonne
        if cols[0].lower().startswith(("totale", "total")):
            continue
        if cols[0].upper() in ("EUR", "USD", "GBP") and len(cols) < 5:
            continue

        # Extract by mapped indices
        data: dict = {}
        for idx, field in col_map.items():
            if idx < len(cols):
                data[field] = cols[idx]

        ticker_raw = data.get("ticker", "").upper().strip()
        if not ticker_raw or len(ticker_raw) > 20:
            warnings.append(f"Skip row (ticker invalido): {cols[:3]}")
            continue

        shares = _parse_number(data.get("shares", ""))
        entry = _parse_number(data.get("entry_price", ""))
        if shares is None or shares <= 0:
            warnings.append(f"Skip {ticker_raw}: shares invalide ({data.get('shares')})")
            continue
        if entry is None or entry <= 0:
            warnings.append(f"Skip {ticker_raw}: entry_price invalido ({data.get('entry_price')})")
            continue

        positions.append(BrokerPosition(
            ticker=ticker_raw,
            shares=shares,
            entry_price=entry,
            isin=data.get("isin") or None,
            strumento=data.get("strumento") or None,
            valuta=data.get("valuta") or None,
            titolo=data.get("titolo") or None,
            valore_carico=_parse_number(data.get("valore_carico", "")),
            valore_mercato=_parse_number(data.get("valore_mercato", "")),
        ))

    if not positions:
        warnings.append("Nessuna posizione parsata (controlla formato).")

    return positions, warnings


def reconcile_with_portfolio(
    broker_positions: list[BrokerPosition],
    portfolio: dict,
    *,
    shares_tolerance: float = 0.01,
    price_tolerance_pct: float = 0.01,
) -> dict:
    """Diff broker vs portfolio. Ritorna 4 categorie.

    Args:
        broker_positions: output di ``parse_broker_paste``.
        portfolio: dict portfolio (load_portfolio).
        shares_tolerance: assoluta, default 0.01 share.
        price_tolerance_pct: relativa, default 1% sul entry_price.

    Returns:
        Dict con liste:
        - in_sync: dict (ticker → broker_pos, portfolio_pos)
        - drift: dict (ticker → broker_pos, portfolio_pos, drift_msg)
        - only_broker: list[BrokerPosition]
        - only_portfolio: list[(ticker, portfolio_pos)]
    """
    pf_positions = portfolio.get("positions", {})

    broker_map = {b.ticker.upper(): b for b in broker_positions}
    pf_tickers = {t.upper(): p for t, p in pf_positions.items()}

    in_sync: dict = {}
    drift: dict = {}

    for tk, b in broker_map.items():
        if tk in pf_tickers:
            p = pf_tickers[tk]
            pf_shares = float(p.get("shares") or 0)
            pf_entry = float(p.get("entry_price") or 0)
            shares_diff = abs(b.shares - pf_shares)
            price_diff_pct = abs(b.entry_price - pf_entry) / pf_entry if pf_entry > 0 else 1.0

            if shares_diff <= shares_tolerance and price_diff_pct <= price_tolerance_pct:
                in_sync[tk] = {"broker": b, "portfolio": p}
            else:
                drift_parts = []
                if shares_diff > shares_tolerance:
                    drift_parts.append(
                        f"shares {pf_shares:.4f} → {b.shares:.4f} "
                        f"(Δ {b.shares - pf_shares:+.4f})"
                    )
                if price_diff_pct > price_tolerance_pct:
                    drift_parts.append(
                        f"entry {pf_entry:.2f} → {b.entry_price:.2f} "
                        f"(Δ {(b.entry_price - pf_entry) / pf_entry * 100:+.2f}%)"
                    )
                drift[tk] = {
                    "broker": b,
                    "portfolio": p,
                    "drift_msg": " · ".join(drift_parts),
                }

    only_broker = [b for tk, b in broker_map.items() if tk not in pf_tickers]
    only_portfolio = [
        (tk, p) for tk, p in pf_tickers.items() if tk not in broker_map
    ]

    return {
        "in_sync": in_sync,
        "drift": drift,
        "only_broker": only_broker,
        "only_portfolio": only_portfolio,
    }


def apply_broker_position(
    broker_pos: BrokerPosition,
    *,
    entry_date: str | None = None,
    strategy: str | None = None,
    score_claude: int = 7,
    score_tech: int = 70,
    stop_loss_pct: float = 0.10,
    catalyst: str | None = None,
) -> dict:
    """Aggiungi una broker_pos al portfolio + journal sync.

    Usato per ``only_broker``: posizioni nel broker ma non in portfolio.
    Bypass earnings hard gate (la trade è già fatta nel broker, non
    bloccare l'import). Default stop = entry × (1 - stop_loss_pct).

    Args:
        entry_date: ISO date (default oggi).
        strategy: tag manuale (default 'BrokerImport').
        stop_loss_pct: % default per stop loss se non noto. 10% conservative.

    Returns:
        Dict con result da add_position.
    """
    from datetime import date as _date

    from propicks.io.trade_sync import open_trade

    ticker = broker_pos.ticker.upper()
    entry = round(broker_pos.entry_price, 4)
    shares = int(broker_pos.shares) if broker_pos.shares == int(broker_pos.shares) else broker_pos.shares
    if not isinstance(shares, int):
        # add_position vuole int — round se decimal residual è < 0.01 (quote frazionario raro IT)
        shares = int(round(broker_pos.shares))
    stop = round(entry * (1 - stop_loss_pct), 2)
    if stop >= entry:
        stop = round(entry * 0.95, 2)  # fallback 5%

    eff_date = entry_date or _date.today().isoformat()
    eff_strategy = strategy or _infer_strategy(broker_pos)
    eff_catalyst = catalyst or f"Broker import {eff_date}"

    # Currency: priority broker.valuta > infer_currency(ticker)
    from propicks.domain.currency import infer_currency
    eff_currency = (
        (broker_pos.valuta or "").upper()
        if broker_pos.valuta
        else infer_currency(ticker)
    )

    trade, position, warnings = open_trade(
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_date=eff_date,
        shares=shares,
        stop_loss=stop,
        target=None,
        score_claude=score_claude,
        score_tech=score_tech,
        strategy=eff_strategy,
        catalyst=eff_catalyst,
        notes=(
            f"ISIN={broker_pos.isin or 'n/a'} · "
            f"strumento={broker_pos.strumento or 'n/a'} · "
            f"currency={eff_currency}"
        ),
        currency=eff_currency,
    )
    return {"trade": trade, "position": position, "warnings": warnings}


def _infer_strategy(broker_pos: BrokerPosition) -> str:
    """Inferisci strategy da ticker/strumento — best effort.

    - Ticker in THEMATIC_ETFS → 'Thematic'
    - Ticker in SECTOR_ETFS_*  → 'ETF_Rotation'
    - strumento == 'ETF' (broad index, es. SWDA/VWCE) → 'ETF_Rotation' come bucket
    - default → 'Altro'
    """
    from propicks.config import (
        SECTOR_ETFS_US, SECTOR_ETFS_WORLD, THEMATIC_ETFS,
    )

    tk = broker_pos.ticker.upper()
    if tk in THEMATIC_ETFS:
        return "Thematic"
    if tk in SECTOR_ETFS_US or tk in SECTOR_ETFS_WORLD:
        return "ETF_Rotation"
    if broker_pos.strumento and broker_pos.strumento.upper() == "ETF":
        # ETF broad-index non registrato (es. SWDA/VWCE) → ETF bucket comunque
        return "ETF_Rotation"
    return "Altro"


def apply_drift_update(
    ticker: str,
    new_shares: float,
    new_entry: float,
) -> None:
    """Aggiorna shares + entry_price di una posizione esistente.

    Direct SQL UPDATE — NON ricalcola cash (broker è source of truth, il
    cash dovrebbe essere allineato separatamente). Non scrive su journal:
    la modifica è una correzione data-entry, non una trade open/close.
    """
    from propicks.io.db import connect

    ticker_up = ticker.upper()
    shares_int = int(round(new_shares))
    entry_round = round(float(new_entry), 4)

    if shares_int <= 0 or entry_round <= 0:
        raise ValueError(f"shares/entry invalidi: {new_shares}, {new_entry}")

    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE positions SET shares = ?, entry_price = ? WHERE ticker = ?",
            (shares_int, entry_round, ticker_up),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Nessuna posizione su {ticker_up}.")
        conn.commit()
    finally:
        conn.close()


def remove_orphan_position(ticker: str) -> dict:
    """Rimuove posizione present in portfolio ma non in broker.

    Wrapper su ``portfolio_store.remove_position`` con load+save pattern.
    NON chiude il trade journal — solo data-entry correction.
    Per chiudere trade journal, usa ``trade_sync.close_trade``.
    """
    from propicks.io.portfolio_store import load_portfolio, remove_position

    portfolio = load_portfolio()
    return remove_position(portfolio=portfolio, ticker=ticker)
