"""Test dei gate SFM in ``portfolio_store.add_position``.

Verifica le 3 invarianti SFM-specifiche:
1. Size cap 10% (vs 15% momentum, 8% contrarian)
2. Max 3 stock per settore (sector_key gate)
3. Bucket aggregato 25% (sum SFM positions)
4. Cross-bucket sector cap 35% (sum SFM + ETF + momentum stesso settore)
5. sector_key required per posizioni SFM (fail-fast)

E i nuovi sizing helpers: is_sfm_position, sfm_aggregate_exposure,
sfm_position_count, sfm_positions_in_sector, sector_aggregate_exposure.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def portfolio_tmp():
    return None


# ---------------------------------------------------------------------------
# is_sfm_position helper
# ---------------------------------------------------------------------------
def test_is_sfm_position_matches_strategy_prefix():
    from propicks.domain.sizing import is_sfm_position

    assert is_sfm_position({"strategy": "SFM"}) is True
    assert is_sfm_position({"strategy": "sfm"}) is True
    assert is_sfm_position({"strategy": "SFM — XLK leader"}) is True
    assert is_sfm_position({"strategy": "sfm-tech"}) is True
    assert is_sfm_position({"strategy": "Momentum"}) is False
    assert is_sfm_position({"strategy": "Contrarian"}) is False
    assert is_sfm_position({"strategy": None}) is False
    assert is_sfm_position({}) is False


def test_sfm_aggregate_exposure_pure_function():
    """Test puro su dict portfolio — niente DB richiesto."""
    from propicks.domain.sizing import sfm_aggregate_exposure

    pf = {
        "cash": 5000.0,
        "positions": {
            "AAPL": {
                "strategy": "SFM", "shares": 10, "entry_price": 200.0,
                "sector_key": "technology",
            },
            "JPM": {
                "strategy": "Momentum", "shares": 10, "entry_price": 100.0,
            },
        },
    }
    # Total = 5000 + 10*200 + 10*100 = 8000
    # SFM = 10*200 = 2000 → 25% del totale
    assert sfm_aggregate_exposure(pf) == pytest.approx(0.25)


def test_sfm_positions_in_sector_filters_by_sector_key():
    from propicks.domain.sizing import sfm_positions_in_sector

    pf = {
        "positions": {
            "AAPL": {"strategy": "SFM", "sector_key": "technology"},
            "MSFT": {"strategy": "SFM", "sector_key": "technology"},
            "JPM":  {"strategy": "SFM", "sector_key": "financials"},
            "GE":   {"strategy": "Momentum", "sector_key": "industrials"},  # non-SFM
        },
    }
    assert sfm_positions_in_sector(pf, "technology") == 2
    assert sfm_positions_in_sector(pf, "financials") == 1
    assert sfm_positions_in_sector(pf, "industrials") == 0  # è momentum, non SFM
    assert sfm_positions_in_sector(pf, "energy") == 0


def test_sector_aggregate_exposure_uses_explicit_sector_key():
    from propicks.domain.sizing import sector_aggregate_exposure

    pf = {
        "cash": 5000.0,
        "positions": {
            "AAPL": {"shares": 10, "entry_price": 200.0, "sector_key": "technology"},
            "MSFT": {"shares": 10, "entry_price": 100.0, "sector_key": "technology"},
            "JPM":  {"shares": 5,  "entry_price": 200.0, "sector_key": "financials"},
        },
    }
    # Total = 5000 + 2000 + 1000 + 1000 = 9000
    # Tech = 2000 + 1000 = 3000 → 33.3%
    assert sector_aggregate_exposure(pf, "technology") == pytest.approx(3000 / 9000)
    assert sector_aggregate_exposure(pf, "financials") == pytest.approx(1000 / 9000)
    assert sector_aggregate_exposure(pf, "energy") == 0.0


def test_sector_aggregate_exposure_with_resolver():
    """Posizioni legacy senza sector_key → resolver runtime."""
    from propicks.domain.sizing import sector_aggregate_exposure

    pf = {
        "cash": 5000.0,
        "positions": {
            "AAPL": {"shares": 10, "entry_price": 200.0},  # no sector_key
            "MSFT": {"shares": 10, "entry_price": 100.0, "sector_key": "technology"},
        },
    }
    # Resolver: AAPL → technology (manual override)
    def resolver(t, p):
        return "technology" if t == "AAPL" else None

    # Total = 5000 + 2000 + 1000 = 8000
    # Tech = 2000 (AAPL via resolver) + 1000 (MSFT esplicito) = 3000 → 37.5%
    assert sector_aggregate_exposure(
        pf, "technology", sector_resolver=resolver
    ) == pytest.approx(3000 / 8000)


def test_sector_aggregate_exposure_resolver_exception_excludes():
    """Resolver che solleva → posizione esclusa (fail-open)."""
    from propicks.domain.sizing import sector_aggregate_exposure

    pf = {
        "cash": 5000.0,
        "positions": {
            "BAD": {"shares": 10, "entry_price": 100.0},  # no sector_key
        },
    }
    def resolver(t, p):
        raise RuntimeError("network down")

    # Total = 5000 + 1000 = 6000. Tech = 0 (BAD escluso per exception)
    assert sector_aggregate_exposure(
        pf, "technology", sector_resolver=resolver
    ) == 0.0


# ---------------------------------------------------------------------------
# add_position SFM gates
# ---------------------------------------------------------------------------
def test_add_sfm_requires_sector_key(portfolio_tmp):
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    with pytest.raises(ValueError, match="sector_key"):
        add_position(
            pf,
            ticker="AAPL",
            entry_price=100.0,
            shares=5,
            stop_loss=95.0,
            target=110.0,
            strategy="SFM",
            score_claude=7,
            score_tech=80,
            catalyst="tech leader",
            # sector_key NON passato → fail
        )


def test_add_sfm_enforces_10pct_size_cap(portfolio_tmp):
    """Size SFM > 10% del capitale → ValueError (vs 15% momentum)."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # CAPITAL default = 10_000. 12% sarebbe 1200€, sopra il cap SFM 10% (1000€)
    with pytest.raises(ValueError, match=r"10%.*sfm"):
        add_position(
            pf,
            ticker="AAPL",
            entry_price=100.0,
            shares=12,  # 12 × 100 = 1200€ = 12% → blocca
            stop_loss=95.0,
            target=110.0,
            strategy="SFM",
            score_claude=7,
            score_tech=80,
            catalyst=None,
            sector_key="technology",
        )


def test_add_sfm_loss_above_6pct_blocks(portfolio_tmp):
    """Stop SFM > 6% → ValueError (più stretto del momentum 8%)."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    with pytest.raises(ValueError, match=r"6%.*sfm"):
        add_position(
            pf, ticker="AAPL", entry_price=100.0, shares=5,
            stop_loss=92.0,  # 8% loss → sopra cap SFM 6%
            target=110.0,
            strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
            sector_key="technology",
        )


def test_add_sfm_blocks_4th_stock_in_same_sector(portfolio_tmp):
    """Tentativo 4° stock SFM in stesso settore → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # Apri 3 SFM in technology (size piccola per non triggerare cap aggregato)
    for tkr, entry in [("A1", 50.0), ("A2", 50.0), ("A3", 50.0)]:
        add_position(
            pf, ticker=tkr, entry_price=entry, shares=10,  # 5% ciascuna
            stop_loss=entry * 0.95, target=entry * 1.05,
            strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
            sector_key="technology",
        )
    with pytest.raises(ValueError, match="technology.*pieno"):
        add_position(
            pf, ticker="A4", entry_price=50.0, shares=10,
            stop_loss=47.5, target=52.5,
            strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
            sector_key="technology",
        )


def test_add_sfm_blocks_aggregate_above_25pct(portfolio_tmp):
    """SFM bucket aggregate > 25% → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # Apri 3 SFM in 3 settori diversi (2 da 9%, 1 da 8% = 26%)
    add_position(
        pf, ticker="TECH1", entry_price=100.0, shares=9,  # 9% del 10k
        stop_loss=95.0, target=105.0,
        strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
        sector_key="technology",
    )
    add_position(
        pf, ticker="FIN1", entry_price=100.0, shares=9,  # +9% → 18%
        stop_loss=95.0, target=105.0,
        strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
        sector_key="financials",
    )
    with pytest.raises(ValueError, match=r"cap 25"):
        add_position(
            pf, ticker="HC1", entry_price=100.0, shares=9,  # +9% → 27% > cap 25
            stop_loss=95.0, target=105.0,
            strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
            sector_key="healthcare",
        )


def test_add_sfm_cross_bucket_sector_cap(portfolio_tmp):
    """Cross-bucket: SFM tech + ETF tech + momentum tech > 35% → block.

    Setup: 1 momentum tech 15% (max momentum cap) + 2 SFM tech 10% ciascuno (20%)
    = 35%. Aggiungere un altro nome tech qualunque → blocca.
    """
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # Step 1: momentum stock tech a 15% (con sector_key salvato)
    add_position(
        pf, ticker="MSFT", entry_price=100.0, shares=15,  # 15% del 10k
        stop_loss=95.0, target=110.0,
        strategy="Momentum", score_claude=7, score_tech=70, catalyst=None,
        sector_key="technology",
    )
    # Step 2: SFM tech a 10%
    add_position(
        pf, ticker="AAPL", entry_price=100.0, shares=10,
        stop_loss=95.0, target=105.0,
        strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
        sector_key="technology",
    )
    # Step 3: SFM tech +10% → bucket SFM 20% OK, sector tech 25% OK (sotto 35)
    add_position(
        pf, ticker="NVDA", entry_price=100.0, shares=10,
        stop_loss=95.0, target=105.0,
        strategy="SFM", score_claude=7, score_tech=80, catalyst=None,
        sector_key="technology",
    )
    # Tech total = 15 + 10 + 10 = 35% → cap raggiunto. +1 piccolo → boom.
    # Wait: bucket SFM in tech sarebbe 30% > 25% → block bucket prima.
    # Fix: aggiungiamo un ETF tech invece (non triggera SFM bucket).
    with pytest.raises(ValueError, match=r"sector cap.*35"):
        add_position(
            pf, ticker="XLK", entry_price=100.0, shares=2,  # +2% tech
            stop_loss=95.0, target=105.0,
            strategy="ETF rotation", score_claude=None, score_tech=None,
            catalyst=None, sector_key="technology",
        )


def test_add_sfm_size_below_cap_passes(portfolio_tmp):
    """Sanity: SFM al 5% con tutti gli altri gate OK → passa."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="AAPL",
        entry_price=100.0,
        shares=5,  # 5% → OK
        stop_loss=96.0,  # 4% stop, sotto 6%
        target=110.0,
        strategy="SFM",
        score_claude=8,
        score_tech=82,
        catalyst="leader tech",
        sector_key="technology",
    )
    assert pos["shares"] == 5
    assert pos["sector_key"] == "technology"


def test_add_sfm_persists_sector_key_to_db(portfolio_tmp):
    """Dopo add_position SFM, il reload mostra sector_key salvato."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    add_position(
        pf, ticker="AAPL", entry_price=100.0, shares=5,
        stop_loss=96.0, target=110.0,
        strategy="SFM", score_claude=8, score_tech=82, catalyst=None,
        sector_key="technology",
    )

    reloaded = load_portfolio()
    assert reloaded["positions"]["AAPL"]["sector_key"] == "technology"
    assert reloaded["positions"]["AAPL"]["strategy"] == "SFM"
