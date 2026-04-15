"""Validazioni condivise tra domain, io e cli."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from propicks.config import DATE_FMT


def validate_scores(score_claude: Optional[int], score_tech: Optional[int]) -> None:
    """Controlla i range: Claude 1-10, Tech 0-100. None è valido."""
    if score_claude is not None and not (1 <= score_claude <= 10):
        raise ValueError(f"score_claude fuori range 1-10: {score_claude}")
    if score_tech is not None and not (0 <= score_tech <= 100):
        raise ValueError(f"score_tech fuori range 0-100: {score_tech}")


def validate_date(s: str) -> str:
    """Parsing + re-stringify in DATE_FMT. Raise ValueError se invalida."""
    datetime.strptime(s, DATE_FMT)
    return s
