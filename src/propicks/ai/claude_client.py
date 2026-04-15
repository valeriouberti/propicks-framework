"""Wrapper sottile sull'SDK Anthropic per la validazione tesi.

Unico punto del codice che importa ``anthropic``. Se in futuro si cambia
provider o si aggiunge un secondo model (es. fallback a Sonnet), si tocca
solo qui.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from pydantic import BaseModel, Field

from propicks.ai.prompts import SYSTEM_PROMPT
from propicks.config import (
    AI_MAX_TOKENS,
    AI_MODEL,
    AI_TIMEOUT_SECONDS,
    AI_WEB_SEARCH_ENABLED,
    AI_WEB_SEARCH_MAX_USES,
)

WEB_SEARCH_UNIT_PRICE_USD: float = 0.01


class ConfidenceByDimension(BaseModel):
    """Confidence per-dimensione (0-10) sulle 6 dimensioni del framework."""

    business_quality: int = Field(ge=0, le=10)
    narrative_catalysts: int = Field(ge=0, le=10)
    sector_macro_fit: int = Field(ge=0, le=10)
    crowding_sentiment: int = Field(ge=0, le=10)
    risk_asymmetry: int = Field(ge=0, le=10)
    technicals_alignment: int = Field(ge=0, le=10)


class ThesisVerdict(BaseModel):
    """Schema strutturato della risposta di Claude."""

    verdict: str = Field(description="CONFIRM | CAUTION | REJECT")
    conviction_score: int = Field(ge=0, le=10)
    thesis_summary: str
    bull_case: list[str]
    bear_case: list[str]
    key_catalysts: list[str]
    key_risks: list[str]
    invalidation_triggers: list[str]
    invalidation_deadline: str = Field(description="YYYY-MM-DD")
    time_horizon: str = Field(description="1-3M | 3-6M | 6-12M")
    alignment_with_technicals: str = Field(description="STRONG | MIXED | CONTRADICTORY")
    entry_tactic: str = Field(
        description="MARKET_NOW | LIMIT_PULLBACK | WAIT_VOLUME_CONFIRMATION | SCALE_IN"
    )
    reward_risk_ratio: float = Field(ge=0)
    stop_rationale: str
    target_rationale: str
    confidence_by_dimension: ConfidenceByDimension
    suggested_adjustments: dict[str, Any] = Field(default_factory=dict)


class AIValidationError(RuntimeError):
    """Errore applicativo di alto livello per la validazione AI."""


def _build_client():
    """Costruisce il client Anthropic. Import lazy per non forzare il load
    durante i test offline sul dominio."""
    try:
        import anthropic
    except ImportError as err:
        raise AIValidationError(
            "anthropic SDK non installato. Esegui: pip install -e '.[dev]'"
        ) from err

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIValidationError(
            "ANTHROPIC_API_KEY non impostata. Esporta la variabile d'ambiente."
        )
    return anthropic.Anthropic(api_key=api_key, timeout=AI_TIMEOUT_SECONDS)


_CONFIDENCE_KEYS = (
    "business_quality",
    "narrative_catalysts",
    "sector_macro_fit",
    "crowding_sentiment",
    "risk_asymmetry",
    "technicals_alignment",
)

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["CONFIRM", "CAUTION", "REJECT"]},
        "conviction_score": {"type": "integer"},
        "thesis_summary": {"type": "string"},
        "bull_case": {"type": "array", "items": {"type": "string"}},
        "bear_case": {"type": "array", "items": {"type": "string"}},
        "key_catalysts": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "invalidation_triggers": {"type": "array", "items": {"type": "string"}},
        "invalidation_deadline": {"type": "string"},
        "time_horizon": {"type": "string", "enum": ["1-3M", "3-6M", "6-12M"]},
        "alignment_with_technicals": {
            "type": "string",
            "enum": ["STRONG", "MIXED", "CONTRADICTORY"],
        },
        "entry_tactic": {
            "type": "string",
            "enum": [
                "MARKET_NOW",
                "LIMIT_PULLBACK",
                "WAIT_VOLUME_CONFIRMATION",
                "SCALE_IN",
            ],
        },
        "reward_risk_ratio": {"type": "number"},
        "stop_rationale": {"type": "string"},
        "target_rationale": {"type": "string"},
        "confidence_by_dimension": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in _CONFIDENCE_KEYS},
            "required": list(_CONFIDENCE_KEYS),
            "additionalProperties": False,
        },
        "suggested_adjustments": {
            "type": "object",
            "properties": {
                "stop": {"type": ["number", "null"]},
                "target": {"type": ["number", "null"]},
                "size_multiplier": {"type": ["number", "null"]},
            },
            "additionalProperties": False,
        },
    },
    "required": [
        "verdict",
        "conviction_score",
        "thesis_summary",
        "bull_case",
        "bear_case",
        "key_catalysts",
        "key_risks",
        "invalidation_triggers",
        "invalidation_deadline",
        "time_horizon",
        "alignment_with_technicals",
        "entry_tactic",
        "reward_risk_ratio",
        "stop_rationale",
        "target_rationale",
        "confidence_by_dimension",
        "suggested_adjustments",
    ],
    "additionalProperties": False,
}


def _build_tools() -> list[dict] | None:
    """Tools passati a Claude. Attualmente: web_search (server-side Anthropic)."""
    if not AI_WEB_SEARCH_ENABLED:
        return None
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": AI_WEB_SEARCH_MAX_USES,
        }
    ]


def _log_web_search_usage(response: Any) -> None:
    """Stampa su stderr il conteggio delle ricerche web e il costo stimato."""
    try:
        count = response.usage.server_tool_use.web_search_requests
    except AttributeError:
        return
    if not count:
        return
    cost = count * WEB_SEARCH_UNIT_PRICE_USD
    print(f"[ai] {count} web search(es) ≈ ${cost:.2f}", file=sys.stderr)


def call_validation(user_prompt: str) -> ThesisVerdict:
    """Chiama Claude e ritorna un ``ThesisVerdict`` validato.

    Raises:
        AIValidationError: per problemi di config, rete, rate limit o parsing.
    """
    import anthropic

    client = _build_client()

    kwargs: dict[str, Any] = dict(
        model=AI_MODEL,
        max_tokens=AI_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": {"type": "json_schema", "schema": _JSON_SCHEMA}},
    )
    tools = _build_tools()
    if tools:
        kwargs["tools"] = tools

    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as err:
        raise AIValidationError(f"Anthropic API error: {err.message}") from err
    except anthropic.APIConnectionError as err:
        raise AIValidationError(f"Network error talking to Anthropic: {err}") from err

    _log_web_search_usage(response)

    if getattr(response, "stop_reason", None) == "pause_turn":
        raise AIValidationError(
            "Anthropic ha sospeso la risposta (pause_turn). Riduci "
            "PROPICKS_AI_WEB_SEARCH_MAX_USES o riprova."
        )

    text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
    if not text_blocks:
        raise AIValidationError("Risposta priva di blocchi testuali")
    text = text_blocks[-1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        raise AIValidationError(f"JSON non valido dalla risposta: {err}") from err

    v = payload.get("conviction_score")
    if isinstance(v, (int, float)) and 10 < v <= 100:
        payload["conviction_score"] = round(v / 10)

    cbd = payload.get("confidence_by_dimension")
    if isinstance(cbd, dict):
        for k, val in list(cbd.items()):
            if isinstance(val, (int, float)) and 10 < val <= 100:
                cbd[k] = round(val / 10)

    try:
        return ThesisVerdict.model_validate(payload)
    except Exception as err:
        raise AIValidationError(f"Schema mismatch: {err}") from err
