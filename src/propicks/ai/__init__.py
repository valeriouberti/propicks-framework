"""AI validation layer — unico modulo che parla con l'API Anthropic."""

from propicks.ai.etf_validator import validate_rotation
from propicks.ai.thesis_validator import validate_thesis

__all__ = ["validate_thesis", "validate_rotation"]
