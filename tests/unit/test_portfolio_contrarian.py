"""Test dei gate contrarian in ``portfolio_store.add_position``.

Verifica che un trader che chiama ``add_position`` direttamente (bypassando
``calculate_position_size``) non possa superare le regole del bucket:
size 8%, max 3 posizioni, cap aggregato 20%, max loss/trade 12%.

Bug fix #2: prima di questo test, `add_position` usava solo il cap
``MAX_POSITION_SIZE_PCT`` (15%) anche per trade taggati ``Contrarian``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def portfolio_tmp():
    """Compat alias: l'isolation DB è già autouse via conftest._isolate_db.
    Mantenuto per i test che lo richiedono come parametro (no-op)."""
    return None


def test_add_position_contrarian_enforces_8pct_size_cap(portfolio_tmp):
    """Size contrarian > 8% del capitale → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # CAPITAL default = 10_000. 10% sarebbe 1000€, sopra il cap contrarian 8% (800€)
    with pytest.raises(ValueError, match=r"8%.*contrarian"):
        add_position(
            pf,
            ticker="AAPL",
            entry_price=100.0,
            shares=10,  # 10 × 100 = 1000€ = 10% → blocca
            stop_loss=92.0,
            target=110.0,
            strategy="Contrarian",
            score_claude=7,
            score_tech=65,
            catalyst="oversold flush",
        )


def test_add_position_momentum_still_allows_10pct(portfolio_tmp):
    """Stesso size 10% ma strategy="TechTitans" → OK (cap 15%)."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    pos = add_position(
        pf,
        ticker="AAPL",
        entry_price=100.0,
        shares=10,  # 10% → OK per momentum
        stop_loss=95.0,
        target=110.0,
        strategy="TechTitans",
        score_claude=7,
        score_tech=70,
        catalyst=None,
    )
    assert pos["shares"] == 10


def test_add_position_contrarian_blocked_at_3_positions(portfolio_tmp):
    """Tentativo di aggiungere il 4° contrarian → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    for tkr, entry in [("A", 50.0), ("B", 60.0), ("C", 70.0)]:
        add_position(
            pf, ticker=tkr, entry_price=entry, shares=5,
            stop_loss=entry * 0.92, target=entry * 1.1,
            strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
        )

    with pytest.raises(ValueError, match="Bucket contrarian pieno"):
        add_position(
            pf, ticker="D", entry_price=40.0, shares=5,
            stop_loss=36.0, target=44.0,
            strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
        )


def test_add_position_contrarian_aggregate_cap(portfolio_tmp):
    """Aggiungere un trade che porta expo contrarian > 20% → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # Apri 2 contrarian da 7.5% ciascuna (vicini al 20% aggregato dopo la terza)
    add_position(
        pf, ticker="A", entry_price=100.0, shares=7,  # 7% dei 10k
        stop_loss=92.0, target=110.0,
        strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
    )
    add_position(
        pf, ticker="B", entry_price=100.0, shares=7,  # +7% → 14%
        stop_loss=92.0, target=110.0,
        strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
    )
    # Ora expo contrarian = 1400/10000 = 14%. Aggiungere +8% supera il 20%.
    with pytest.raises(ValueError, match=r"cap 20"):
        add_position(
            pf, ticker="C", entry_price=100.0, shares=8,  # +8% → 22% totale
            stop_loss=92.0, target=110.0,
            strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
        )


def test_add_position_contrarian_wider_loss_tolerance(portfolio_tmp):
    """Stop contrarian fino al 12% passa (vs 8% momentum)."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # Stop a 10% sotto → permesso per contrarian (cap 12%), vietato per momentum (cap 8%)
    pos = add_position(
        pf, ticker="AAPL", entry_price=100.0, shares=7,  # 7% del cap
        stop_loss=90.0,  # loss 10% — ok per contrarian
        target=115.0,
        strategy="Contrarian", score_claude=7, score_tech=65, catalyst="flush",
    )
    assert pos["stop_loss"] == 90.0


def test_add_position_contrarian_loss_above_12pct_blocks(portfolio_tmp):
    """Stop contrarian oltre 12% → ValueError."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    with pytest.raises(ValueError, match=r"(13%|12%).*contrarian"):
        add_position(
            pf, ticker="AAPL", entry_price=100.0, shares=7,
            stop_loss=87.0,  # loss 13% — oltre cap contrarian 12%
            target=110.0,
            strategy="Contrarian", score_claude=7, score_tech=65, catalyst=None,
        )


def test_add_position_case_insensitive_contrarian_match(portfolio_tmp):
    """Tag strategy case-insensitive: 'contrarian-pullback', 'CONTRA — macro' tutti matchano."""
    from propicks.io.portfolio_store import add_position, load_portfolio

    pf = load_portfolio()
    # 10% size: passerebbe momentum (15% cap) ma NON contrarian (8% cap).
    # Uso "contra — macro_flush" per testare che il prefix match funziona.
    with pytest.raises(ValueError, match="contrarian"):
        add_position(
            pf, ticker="AAPL", entry_price=100.0, shares=10,
            stop_loss=92.0, target=110.0,
            strategy="contra — macro_flush",  # prefix "contra" case-insensitive
            score_claude=7, score_tech=65, catalyst=None,
        )
