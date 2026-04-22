"""Test verdict logic e max drawdown."""

from __future__ import annotations

import math

from propicks.domain.verdict import max_drawdown, profit_factor, verdict


def test_profit_factor_empty():
    assert profit_factor([]) == 0.0


def test_profit_factor_only_wins():
    assert profit_factor([5, 10, 3]) == float("inf")


def test_profit_factor_only_losses():
    pf = profit_factor([-5, -3])
    assert pf == 0.0


def test_profit_factor_balanced():
    # 2 wins +10, 2 losses -5 → PF = 20 / 10 = 2.0
    assert profit_factor([10, 10, -5, -5]) == 2.0


def test_profit_factor_unbalanced_many_wins():
    # 8W a +10% e 2L a -15% → PF = 80/30 ≈ 2.67
    # NB: il vecchio bug avg_win/avg_loss = 10/15 = 0.67 → sottostima 75%.
    pnls = [10.0] * 8 + [-15.0] * 2
    pf = profit_factor(pnls)
    assert math.isclose(pf, 80 / 30, rel_tol=1e-9)


def test_profit_factor_unbalanced_many_losses():
    # 2W a +20%, 8L a -5% → PF = 40/40 = 1.0 (break-even)
    # Vecchio bug: 20/5 = 4.0 → sovrastima 300%.
    pnls = [20.0] * 2 + [-5.0] * 8
    assert math.isclose(profit_factor(pnls), 1.0, rel_tol=1e-9)


def test_max_drawdown_empty():
    assert max_drawdown([]) == 0.0


def test_max_drawdown_all_wins():
    assert max_drawdown([5, 10, 3, 2]) == 0.0


def test_max_drawdown_simple():
    # equity: 1.0 → 1.1 → 0.99 → drawdown peak-to-trough = (1.1-0.99)/1.1 ≈ 10%
    dd = max_drawdown([10, -10])
    assert 9 < dd < 11


def test_verdict_insufficient_data():
    assert "INSUFFICIENTI" in verdict(0.6, 2.0, n=5)


def test_verdict_profittevole():
    assert verdict(0.55, 1.8, n=30) == "PROFITTEVOLE"


def test_verdict_marginale():
    assert verdict(0.42, 1.3, n=30) == "MARGINALE"


def test_verdict_perdente():
    assert verdict(0.30, 0.8, n=30) == "PERDENTE"
