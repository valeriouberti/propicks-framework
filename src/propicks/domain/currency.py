"""Multi-currency support — FX conversion to base currency (EUR).

Strategia v1 (semplificata):
- **Base currency**: EUR (config-fixed, IT-resident).
- **Currency detection**: ticker suffix → currency (vedi ``infer_currency``).
- **FX fetch**: yfinance ``EUR<XXX>=X`` → cache ``fx_rates_daily`` (TTL 24h).
- **Mark-to-market**: shares × current_price × FX_now.
- **Cost basis**: shares × entry_price (NO FX-at-entry tracking, semplificazione
  v1 — equity P&L mescolato con FX P&L). Per separare FX/equity P&L servirebbe
  ``entry_fx_rate`` colonna. Phase 2 future.

Convenzione: ``rate(EURUSD) = 1.08`` significa **1 EUR = 1.08 USD**. Quindi
``USD_amount / rate(EURUSD) = EUR_amount``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

BASE_CURRENCY = "EUR"
SUPPORTED_CURRENCIES = ("EUR", "USD", "GBP", "CHF", "JPY")
FX_CACHE_TTL_HOURS = 24


def infer_currency(ticker: str) -> str:
    """Inferisci currency da ticker suffix (best effort).

    - ``.MI`` (Borsa Italiana), ``.DE`` (Xetra), ``.PA`` (Paris), ``.AS`` (Amsterdam) → EUR
    - ``.L`` (LSE) → GBP (note: alcuni ETF su LSE quotati USD, override manuale)
    - ``.SW`` (SIX) → CHF
    - ``.T`` (Tokyo) → JPY
    - ``.HK`` (Hong Kong) → HKD (non in SUPPORTED — fallback EUR)
    - No suffix + length 3-5 alpha → US (USD) [es. AAPL/MSFT/NVDA/SPY/QQQ]
    - No suffix + 1-2 char OR contains digits → BASE EUR (test placeholder o
      symbol non-standard, conservativo)
    """
    t = ticker.upper()
    if "." not in t:
        # US listings tipicamente 3-5 chars alpha (BRK.B excluded by dot check)
        # Test placeholders "A"/"B"/"C"/"X" → fallback BASE (EUR).
        # Numeric/digit tickers (es. "0700.HK" già escluso da dot) → BASE.
        if 3 <= len(t) <= 5 and t.isalpha():
            return "USD"
        return BASE_CURRENCY
    suffix = t.rsplit(".", 1)[-1]
    return {
        "MI": "EUR",
        "DE": "EUR",
        "PA": "EUR",
        "AS": "EUR",
        "BR": "EUR",
        "MC": "EUR",
        "VI": "EUR",
        "HE": "EUR",
        "LS": "EUR",
        "L": "GBP",
        "LON": "GBP",
        "SW": "CHF",
        "T": "JPY",
        "F": "EUR",  # Frankfurt
        "FRA": "EUR",
    }.get(suffix, BASE_CURRENCY)


def _fx_pair(currency: str) -> str:
    """Returns ``EUR<XXX>`` pair string. Es. USD → EURUSD."""
    return f"EUR{currency.upper()}"


def get_fx_rate(currency: str, *, force_refresh: bool = False) -> float:
    """Ritorna FX rate EUR→currency da cache o yfinance.

    Es. ``get_fx_rate('USD')`` = 1.08 (1 EUR = 1.08 USD).
    EUR → 1.0 (identity).

    Cache TTL 24h. Fallback: se yfinance fallisce, ritorna ultimo cached
    se < 7gg, altrimenti raise.
    """
    cur = currency.upper()
    if cur == BASE_CURRENCY:
        return 1.0
    if cur not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Currency '{cur}' non supportata: {SUPPORTED_CURRENCIES}")

    pair = _fx_pair(cur)
    from propicks.io.db import connect

    if not force_refresh:
        # Try cache (most recent within TTL)
        conn = connect()
        try:
            row = conn.execute(
                """SELECT rate, fetched_at FROM fx_rates_daily
                   WHERE pair = ?
                   ORDER BY date DESC LIMIT 1""",
                (pair,),
            ).fetchone()
            if row:
                fetched = row["fetched_at"]
                try:
                    fdt = datetime.fromisoformat(fetched.replace(" ", "T"))
                    age_h = (datetime.now() - fdt).total_seconds() / 3600
                    if age_h < FX_CACHE_TTL_HOURS:
                        return float(row["rate"])
                except (ValueError, TypeError):
                    pass
        finally:
            conn.close()

    # Fetch yfinance — yf ticker "EURUSD=X" returns last close
    try:
        import yfinance as yf

        yt = yf.Ticker(f"{pair}=X")
        hist = yt.history(period="5d", auto_adjust=False)
        if hist.empty:
            raise RuntimeError(f"Nessun dato yfinance {pair}")
        rate = float(hist["Close"].iloc[-1])
        if rate <= 0:
            raise RuntimeError(f"Rate {pair} <= 0: {rate}")
    except Exception as exc:
        # Fallback cache stale (< 7gg)
        conn = connect()
        try:
            row = conn.execute(
                """SELECT rate, fetched_at FROM fx_rates_daily
                   WHERE pair = ?
                   ORDER BY date DESC LIMIT 1""",
                (pair,),
            ).fetchone()
            if row:
                fetched = row["fetched_at"]
                try:
                    fdt = datetime.fromisoformat(fetched.replace(" ", "T"))
                    age_d = (datetime.now() - fdt).total_seconds() / 86400
                    if age_d < 7:
                        return float(row["rate"])
                except (ValueError, TypeError):
                    pass
        finally:
            conn.close()
        raise RuntimeError(f"FX fetch {pair} failed: {exc}") from exc

    # Persist
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO fx_rates_daily (date, pair, rate)
               VALUES (?, ?, ?)""",
            (date.today().isoformat(), pair, rate),
        )
        conn.commit()
    finally:
        conn.close()

    return rate


def convert_to_eur(amount: float, currency: str) -> float:
    """Converte ``amount`` da ``currency`` a EUR usando FX corrente.

    Logic: amount_eur = amount_curr / FX_rate(EUR→curr).
    Es. 100 USD / 1.08 = 92.59 EUR.
    """
    if currency.upper() == BASE_CURRENCY:
        return float(amount)
    rate = get_fx_rate(currency)
    return float(amount) / rate


def convert_from_eur(amount_eur: float, currency: str) -> float:
    """Converte EUR → currency. Es. 100 EUR × 1.08 = 108 USD."""
    if currency.upper() == BASE_CURRENCY:
        return float(amount_eur)
    rate = get_fx_rate(currency)
    return float(amount_eur) * rate


def fmt_currency(amount: float, currency: str, *, decimals: int = 2) -> str:
    """Format amount con simbolo currency (€/$/£/¥/CHF)."""
    sym = {
        "EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF",
    }.get(currency.upper(), currency.upper())
    if currency.upper() == "JPY":
        decimals = 0  # JPY no decimals
    return f"{sym} {amount:,.{decimals}f}"


def get_fx_freshness() -> dict[str, dict]:
    """Ritorna info freshness per ogni FX pair tracked. Per dashboard banner."""
    from propicks.io.db import connect

    out: dict[str, dict] = {}
    conn = connect()
    try:
        for cur in SUPPORTED_CURRENCIES:
            if cur == BASE_CURRENCY:
                continue
            pair = _fx_pair(cur)
            row = conn.execute(
                """SELECT rate, fetched_at FROM fx_rates_daily
                   WHERE pair = ? ORDER BY date DESC LIMIT 1""",
                (pair,),
            ).fetchone()
            if row:
                try:
                    fdt = datetime.fromisoformat(row["fetched_at"].replace(" ", "T"))
                    age_h = (datetime.now() - fdt).total_seconds() / 3600
                    out[pair] = {"rate": float(row["rate"]), "age_h": age_h}
                except (ValueError, TypeError):
                    out[pair] = {"rate": float(row["rate"]), "age_h": None}
            else:
                out[pair] = {"rate": None, "age_h": None}
    finally:
        conn.close()
    return out
