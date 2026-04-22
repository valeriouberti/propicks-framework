"""Fixture condivise. Isola i test I/O su tmp_path per non toccare data/ reale."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_ai_cache_dir(tmp_path_factory, monkeypatch):
    """Redirige ``config.AI_CACHE_DIR`` su una tmp per test.

    Impedisce che qualunque test scriva accidentalmente nel vero
    ``data/ai_cache/`` (cache verdict reali, usage counter budget).
    I test che già patchano ``thesis_validator.AI_CACHE_DIR`` continuano
    a funzionare: quella è la variabile locale del modulo, questa è la
    source of truth del config — coesistono.
    """
    cache_dir = tmp_path_factory.mktemp("ai_cache")
    monkeypatch.setattr("propicks.config.AI_CACHE_DIR", str(cache_dir))


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
