"""Fixture condivise. Isola i test I/O su tmp_path per non toccare data/ reale.

Post Phase 1: lo storage è SQLite. La fixture ``_isolate_db`` è autouse:
ogni test ottiene un DB fresco su tmp_path e schema inizializzato al primo
connect(). Nessun test tocca mai ``data/propicks.db`` reale.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Redirige ``config.DB_FILE`` su un SQLite ephemeral per test.

    Schema inizializzato lazy al primo ``db.connect()`` tramite il controllo
    ``is_new`` in ``io/db.py::connect()``. Ogni test parte con DB vuoto —
    zero cross-test pollution, zero setup boilerplate.

    ``autouse=True`` perché il DB è la source of truth di positions, trades,
    watchlist e AI verdicts. Un test che bypassa la fixture rischia di
    scrivere sul DB reale.
    """
    db_path = tmp_path / "test_propicks.db"
    monkeypatch.setattr("propicks.config.DB_FILE", str(db_path))


@pytest.fixture
def sample_portfolio() -> dict:
    """Portfolio dict per test che NON persistono (test puri su domain).

    NB: non viene scritto sul DB. I test che vogliono la persistenza devono
    usare ``add_position(...)`` che scrive su DB.
    """
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
        "initial_capital": 10_000.0,
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
