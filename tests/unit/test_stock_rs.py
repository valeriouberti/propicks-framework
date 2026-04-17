"""Test puri sul layer RS stock vs sector ETF.

``score_rs_vs_sector`` è un wrapper su ``etf_scoring.score_rs`` (già coperto da
test dedicati) — qui si testa solo il mapping e il gate US-only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from propicks.domain.stock_rs import (
    SECTOR_KEY_TO_US_ETF,
    YF_SECTOR_TO_KEY,
    is_us_ticker,
    peer_etf_for,
    score_rs_vs_sector,
)


def _geom_series(n: int, step: float, start: float = 100.0) -> pd.Series:
    idx = pd.date_range("2022-01-01", periods=n, freq="W")
    values = start * np.power(1 + step, np.arange(n))
    return pd.Series(values, index=idx)


# ---------------------------------------------------------------------------
# is_us_ticker
# ---------------------------------------------------------------------------
def test_us_ticker_no_suffix():
    assert is_us_ticker("AAPL") is True
    assert is_us_ticker("MSFT") is True
    assert is_us_ticker("NVDA") is True


def test_non_us_ticker_with_exchange_suffix():
    assert is_us_ticker("ENI.MI") is False
    assert is_us_ticker("ISP.MI") is False
    assert is_us_ticker("SAP.DE") is False
    assert is_us_ticker("VOD.L") is False
    assert is_us_ticker("TOTF.PA") is False


def test_us_share_class_dot_is_treated_as_us():
    # BRK.B ha un punto ma la parte dopo è 1 char → non è un exchange code
    assert is_us_ticker("BRK.B") is True
    assert is_us_ticker("BF.B") is True


# ---------------------------------------------------------------------------
# peer_etf_for
# ---------------------------------------------------------------------------
def test_peer_etf_tech_maps_to_xlk():
    assert peer_etf_for("Technology") == "XLK"


def test_peer_etf_financial_services_maps_to_xlf():
    assert peer_etf_for("Financial Services") == "XLF"


def test_peer_etf_consumer_cyclical_maps_to_xly():
    # Yahoo usa "Consumer Cyclical" per quello che GICS chiama "Consumer
    # Discretionary" → deve risolvere su XLY (Consumer Discretionary SPDR).
    assert peer_etf_for("Consumer Cyclical") == "XLY"


def test_peer_etf_none_or_unknown_returns_none():
    assert peer_etf_for(None) is None
    assert peer_etf_for("Not A Real Sector") is None
    assert peer_etf_for("") is None


def test_every_yf_sector_has_a_peer_etf():
    # Tutte le chiavi della taxonomy Yahoo devono risolvere su un ETF esistente:
    # se aggiungiamo una voce in YF_SECTOR_TO_KEY ma dimentichiamo il match in
    # SECTOR_KEY_TO_US_ETF, il mapping silenziosamente ritornerebbe None.
    for yf_sector, key in YF_SECTOR_TO_KEY.items():
        assert key in SECTOR_KEY_TO_US_ETF, f"{yf_sector} → {key} senza ETF"


# ---------------------------------------------------------------------------
# score_rs_vs_sector (wrapper: delega a etf_scoring.score_rs)
# ---------------------------------------------------------------------------
def test_score_rs_vs_sector_includes_peer_etf():
    stock = _geom_series(60, 0.005)
    sector = _geom_series(60, 0.002)
    out = score_rs_vs_sector(stock, sector, peer_etf="XLK")
    assert out["peer_etf"] == "XLK"
    assert out["score"] > 50
    assert out["rs_ratio"] > 1.0


def test_score_rs_vs_sector_laggard_scores_low():
    stock = _geom_series(60, -0.002)
    sector = _geom_series(60, 0.003)
    out = score_rs_vs_sector(stock, sector, peer_etf="XLK")
    assert out["peer_etf"] == "XLK"
    assert out["score"] <= 25
