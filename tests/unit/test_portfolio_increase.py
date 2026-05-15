"""Test di ``portfolio_store.increase_position`` (pyramiding).

Invarianti verificati:
- entry medio pesato corretto
- cash addebitato SOLO della tranche aggiunta (no rimbalzo entry vecchio)
- size cap riapplicato sulla NUOVA size totale
- stop vecchio rivalidato contro il nuovo entry medio (loss cap)
- journal/entry_date non toccati (increase non scrive su trades)

Ticker EUR (``.MI``) → FX = 1.0, math deterministico contro CAPITAL = 10_000.
"""

from __future__ import annotations

import pytest

from propicks.io.portfolio_store import (
    add_position,
    increase_position,
    load_portfolio,
)


def _open(pf, *, shares=50, entry=10.0, stop=9.5, target=13.0):
    return add_position(
        pf,
        ticker="ENI.MI",
        entry_price=entry,
        shares=shares,
        stop_loss=stop,
        target=target,
        strategy="TechTitans",
        score_claude=7,
        score_tech=70,
        catalyst=None,
    )


def test_increase_weighted_avg_and_cash():
    pf = load_portfolio()
    _open(pf)  # 50 @ 10 = 500€ (5% di 10_000)
    cash_before = pf["cash"]

    # Mediando al rialzo (10→12) lo stop fisso vecchio sforerebbe il loss cap
    # 8%: bisogna alzare lo stop col nuovo entry. (12-11.1)/12 = 7.5% < 8%.
    pos = increase_position(
        pf, "ENI.MI", add_shares=50, add_price=14.0, new_stop=11.1
    )

    # entry medio = (50*10 + 50*14) / 100 = 12.0
    assert pos["shares"] == 100
    assert pos["entry_price"] == 12.0
    assert pos["stop_loss"] == 11.1
    # cash addebitato SOLO della tranche (50*14=700), non rimbalzo entry vecchio
    assert pf["cash"] == round(cash_before - 700.0, 2)
    # 100 @ 12 = 1200€ = 12% < cap 15% standard → ok


def test_increase_rejects_unknown_ticker():
    pf = load_portfolio()
    with pytest.raises(ValueError, match=r"Nessuna posizione aperta"):
        increase_position(pf, "MSFT", add_shares=5, add_price=300.0)


def test_increase_size_cap_on_new_total():
    """Add che porta la posizione sopra il 15% standard → blocco."""
    pf = load_portfolio()
    _open(pf)  # 500€ = 5%
    # +120 @ 10 → 170 sh, avg 10 → 1700€ = 17% > cap 15%
    with pytest.raises(ValueError, match=r"15%.*standard"):
        increase_position(pf, "ENI.MI", add_shares=120, add_price=10.0)


def test_increase_old_stop_breaches_loss_cap_requires_new_stop():
    """Mediando al rialzo lo stop fisso vecchio sfora il loss cap 8%."""
    pf = load_portfolio()
    _open(pf, entry=10.0, stop=9.5)  # risk 5%
    # add 50 @ 20 → entry medio = (500+1000)/100 = 15; stop 9.5 → risk 36.7% > 8%
    with pytest.raises(ValueError, match=r"limite 8%.*new_stop"):
        increase_position(pf, "ENI.MI", add_shares=50, add_price=20.0)

    # Con new_stop dentro il cap passa: (15-14)/15 = 6.7% < 8%. Il target
    # vecchio (13) ora è sotto l'entry medio (15) → serve anche new_target.
    pos = increase_position(
        pf,
        "ENI.MI",
        add_shares=50,
        add_price=20.0,
        new_stop=14.0,
        new_target=22.0,
    )
    assert pos["shares"] == 100
    assert pos["entry_price"] == 15.0
    assert pos["stop_loss"] == 14.0
    assert pos["target"] == 22.0


def test_increase_does_not_touch_journal():
    pf = load_portfolio()
    _open(pf)
    increase_position(pf, "ENI.MI", add_shares=20, add_price=11.0)

    from propicks.io.journal_store import load_journal

    assert all(t.get("ticker") != "ENI.MI" for t in load_journal())
