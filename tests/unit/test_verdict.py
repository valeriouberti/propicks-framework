"""Test verdict logic e max drawdown."""

from __future__ import annotations

from propicks.domain.verdict import max_drawdown, verdict


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
