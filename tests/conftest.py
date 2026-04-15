"""Fixture condivise. Isola i test I/O su tmp_path per non toccare data/ reale."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_portfolio() -> dict:
    return {
        "positions": {
            "AAPL": {
                "shares": 10,
                "entry_price": 200.0,
                "stop_loss": 190.0,
                "target": 220.0,
                "strategy": "TechTitans",
                "score_claude": 8,
                "score_tech": 75,
                "entry_date": "2026-01-15",
                "catalyst": None,
            }
        },
        "cash": 8_000.0,
        "last_updated": "2026-01-15",
    }


@pytest.fixture
def sample_closed_trades() -> list[dict]:
    return [
        {
            "id": 1, "ticker": "AAPL", "direction": "long", "status": "closed",
            "entry_price": 100.0, "exit_price": 110.0, "pnl_pct": 10.0,
            "entry_date": "2026-01-01", "exit_date": "2026-01-15",
            "duration_days": 14, "strategy": "TechTitans", "score_claude": 8,
        },
        {
            "id": 2, "ticker": "MSFT", "direction": "long", "status": "closed",
            "entry_price": 300.0, "exit_price": 285.0, "pnl_pct": -5.0,
            "entry_date": "2026-01-10", "exit_date": "2026-01-25",
            "duration_days": 15, "strategy": "TechTitans", "score_claude": 7,
        },
    ]
