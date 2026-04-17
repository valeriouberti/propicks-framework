"""Relative Strength di un titolo vs il proprio sector ETF (peer-relative).

Layer informativo: il punteggio calcolato qui NON entra nel composite di
``analyze_ticker`` — viene esposto come sub-field separato. Scopo: distinguere
i leader del settore dai passeggeri del trend (es. NVDA vs XLK).

Limitato agli US tickers per costruzione: la mappa ``YF_SECTOR_TO_ETF`` punta
ai Select Sector SPDR (XL*). Per ticker EU/IT (.MI, .DE, .L, ...) la funzione
ritorna None — la rotazione geografica inquinerebbe il segnale di peer RS.
"""

from __future__ import annotations

import pandas as pd

from propicks.domain.etf_scoring import score_rs

# yfinance/Yahoo sector taxonomy → sector_key interno (GICS-normalizzato)
# Yahoo usa una variante di GICS: "Consumer Cyclical" invece di "Consumer
# Discretionary", "Financial Services" invece di "Financials", ecc.
YF_SECTOR_TO_KEY: dict[str, str] = {
    "Technology": "technology",
    "Financial Services": "financials",
    "Energy": "energy",
    "Healthcare": "healthcare",
    "Industrials": "industrials",
    "Consumer Cyclical": "consumer_discretionary",
    "Consumer Defensive": "consumer_staples",
    "Utilities": "utilities",
    "Real Estate": "real_estate",
    "Basic Materials": "materials",
    "Communication Services": "communications",
}

SECTOR_KEY_TO_US_ETF: dict[str, str] = {
    "technology": "XLK",
    "financials": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "industrials": "XLI",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "utilities": "XLU",
    "real_estate": "XLRE",
    "materials": "XLB",
    "communications": "XLC",
}


NON_US_EXCHANGE_SUFFIXES: frozenset[str] = frozenset({
    "MI",    # Borsa Italiana (Milano)
    "DE",    # Deutsche Börse Xetra
    "F",     # Frankfurt
    "L",     # London Stock Exchange
    "PA",    # Euronext Paris
    "AS",    # Euronext Amsterdam
    "BR",    # Euronext Brussels
    "LS",    # Euronext Lisbon
    "MC",    # Bolsa Madrid
    "SW",    # SIX Swiss
    "VI",    # Vienna
    "ST",    # Stockholm
    "HE",    # Helsinki
    "OL",    # Oslo
    "CO",    # Copenhagen
    "IR",    # Ireland
    "AT",    # Athens
    "WA",    # Warsaw
    "TO",    # Toronto
    "V",     # TSX Venture
    "HK",    # Hong Kong
    "AX",    # ASX Sydney
    "T",     # Tokyo
    "KS",    # Korea
    "SS",    # Shanghai
    "SZ",    # Shenzhen
    "SA",    # B3 São Paulo
    "MX",    # Mexico
    "JO",    # Johannesburg
    "TA",    # Tel Aviv
})


def is_us_ticker(ticker: str) -> bool:
    """True se il ticker è listato su un exchange US.

    yfinance usa suffissi ISO per i listing non-US (``.MI``, ``.DE``, ``.L``,
    ``.PA``, ...). Uso una whitelist esplicita di exchange non-US invece di
    un test sulla lunghezza: VOD.L (London) ha suffisso 1 char e andrebbe
    classificato non-US, mentre BRK.B (Berkshire share class B) non ha
    suffisso exchange. Ticker senza suffisso o con suffisso ignoto ⇒ US
    (conservativo: in peggio facciamo una RS vs XLK che non ha senso, non
    skippiamo erroneamente).
    """
    t = ticker.upper()
    if "." not in t:
        return True
    suffix = t.rsplit(".", 1)[1]
    return suffix not in NON_US_EXCHANGE_SUFFIXES


def peer_etf_for(yf_sector: str | None) -> str | None:
    """Ritorna il ticker del sector ETF US corrispondente, o None."""
    if yf_sector is None:
        return None
    key = YF_SECTOR_TO_KEY.get(yf_sector)
    if key is None:
        return None
    return SECTOR_KEY_TO_US_ETF.get(key)


def score_rs_vs_sector(
    close_stock_weekly: pd.Series,
    close_sector_weekly: pd.Series,
    peer_etf: str,
) -> dict:
    """RS del titolo vs il proprio sector ETF (stesso engine di etf_scoring).

    Ritorna lo stesso dict di ``etf_scoring.score_rs`` con in più il ticker
    del peer ETF usato, così il caller può loggarlo (es. "AAPL vs XLK: 85").
    """
    result = score_rs(close_stock_weekly, close_sector_weekly)
    result["peer_etf"] = peer_etf
    return result
