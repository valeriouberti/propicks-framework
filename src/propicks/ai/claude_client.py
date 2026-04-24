"""Wrapper sottile sull'SDK Anthropic per la validazione tesi.

Unico punto del codice che importa ``anthropic``. Se in futuro si cambia
provider o si aggiunge un secondo model (es. fallback a Sonnet), si tocca
solo qui.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from pydantic import BaseModel, Field

from propicks.ai.contrarian_prompts import CONTRA_SYSTEM_PROMPT
from propicks.ai.etf_prompts import ETF_SYSTEM_PROMPT
from propicks.ai.prompts import SYSTEM_PROMPT
from propicks.config import (
    AI_MAX_TOKENS,
    AI_MODEL,
    AI_TIMEOUT_SECONDS,
    AI_WEB_SEARCH_ENABLED,
    AI_WEB_SEARCH_MAX_USES,
)
from propicks.obs.log import get_logger

WEB_SEARCH_UNIT_PRICE_USD: float = 0.01

_log = get_logger("ai.claude")


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


class ContraConfidenceByDimension(BaseModel):
    """Confidence per-dimensione (0-10) sulle 5 dimensioni del framework contrarian."""

    quality_persistence: int = Field(ge=0, le=10)
    catalyst_type_assessment: int = Field(ge=0, le=10)
    market_context: int = Field(ge=0, le=10)
    reversion_path: int = Field(ge=0, le=10)
    fundamental_risk: int = Field(ge=0, le=10)


class ContrarianVerdict(BaseModel):
    """Schema strutturato della risposta di Claude per setup mean reversion.

    La chiave discriminante è ``flush_vs_break``: il selloff è un flush tecnico
    di qualità (buy) o una frattura strutturale (reject)?
    """

    verdict: str = Field(description="CONFIRM | CAUTION | REJECT")
    flush_vs_break: str = Field(description="FLUSH | BREAK | MIXED")
    catalyst_type: str = Field(
        description=(
            "macro_flush | sector_rotation | earnings_miss_fundamental | "
            "fraud_or_accounting | guidance_cut | technical_only | other"
        )
    )
    conviction_score: int = Field(ge=0, le=10)
    thesis_summary: str
    reversion_target: float = Field(
        description="Price at which to take profit (typical: EMA50 daily)"
    )
    invalidation_price: float = Field(
        description="Price at which thesis is invalidated (hard stop)"
    )
    time_horizon_days: int = Field(
        ge=3, le=30, description="Expected days to thesis resolution (5-15 typical)"
    )
    bull_case: list[str]
    bear_case: list[str]
    key_risks: list[str]
    invalidation_triggers: list[str]
    entry_tactic: str = Field(
        description="MARKET_NOW | LIMIT_BELOW | SCALE_IN_TRANCHES | WAIT_STABILIZATION"
    )
    confidence_by_dimension: ContraConfidenceByDimension


class ETFConfidenceByDimension(BaseModel):
    """Confidence per-dimensione (0-10) sulle 6 dimensioni del framework ETF."""

    macro_fit: int = Field(ge=0, le=10)
    breadth: int = Field(ge=0, le=10)
    positioning_flows: int = Field(ge=0, le=10)
    rotation_stage: int = Field(ge=0, le=10)
    alternatives: int = Field(ge=0, le=10)
    regime_consistency: int = Field(ge=0, le=10)


class ETFRotationVerdict(BaseModel):
    """Schema strutturato della risposta di Claude per la rotazione ETF."""

    verdict: str = Field(description="CONFIRM | CAUTION | REJECT")
    conviction_score: int = Field(ge=0, le=10)
    rotation_summary: str
    top_sector_verdict: str = Field(description="Ticker or 'FLAT'")
    alternative_sector: str | None = None
    stage: str = Field(description="EARLY | MID | LATE | UNKNOWN")
    macro_drivers: list[str]
    breadth_read: str
    positioning_read: str
    bull_case: list[str]
    bear_case: list[str]
    invalidation_triggers: list[str]
    entry_tactic: str = Field(
        description="ALLOCATE_NOW | STAGGER_3_TRANCHES | WAIT_PULLBACK | WAIT_CONFIRMATION | HOLD_CASH"
    )
    rebalance_horizon_weeks: int = Field(ge=2, le=12)
    alignment_with_ranking: str = Field(description="STRONG | MIXED | CONTRADICTORY")
    confidence_by_dimension: ETFConfidenceByDimension


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


_CONTRA_CONFIDENCE_KEYS = (
    "quality_persistence",
    "catalyst_type_assessment",
    "market_context",
    "reversion_path",
    "fundamental_risk",
)

_CONTRA_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["CONFIRM", "CAUTION", "REJECT"]},
        "flush_vs_break": {"type": "string", "enum": ["FLUSH", "BREAK", "MIXED"]},
        "catalyst_type": {
            "type": "string",
            "enum": [
                "macro_flush",
                "sector_rotation",
                "earnings_miss_fundamental",
                "fraud_or_accounting",
                "guidance_cut",
                "technical_only",
                "other",
            ],
        },
        "conviction_score": {"type": "integer"},
        "thesis_summary": {"type": "string"},
        "reversion_target": {"type": "number"},
        "invalidation_price": {"type": "number"},
        "time_horizon_days": {"type": "integer"},
        "bull_case": {"type": "array", "items": {"type": "string"}},
        "bear_case": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "invalidation_triggers": {"type": "array", "items": {"type": "string"}},
        "entry_tactic": {
            "type": "string",
            "enum": [
                "MARKET_NOW",
                "LIMIT_BELOW",
                "SCALE_IN_TRANCHES",
                "WAIT_STABILIZATION",
            ],
        },
        "confidence_by_dimension": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in _CONTRA_CONFIDENCE_KEYS},
            "required": list(_CONTRA_CONFIDENCE_KEYS),
            "additionalProperties": False,
        },
    },
    "required": [
        "verdict",
        "flush_vs_break",
        "catalyst_type",
        "conviction_score",
        "thesis_summary",
        "reversion_target",
        "invalidation_price",
        "time_horizon_days",
        "bull_case",
        "bear_case",
        "key_risks",
        "invalidation_triggers",
        "entry_tactic",
        "confidence_by_dimension",
    ],
    "additionalProperties": False,
}


_ETF_CONFIDENCE_KEYS = (
    "macro_fit",
    "breadth",
    "positioning_flows",
    "rotation_stage",
    "alternatives",
    "regime_consistency",
)

_ETF_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["CONFIRM", "CAUTION", "REJECT"]},
        "conviction_score": {"type": "integer"},
        "rotation_summary": {"type": "string"},
        "top_sector_verdict": {"type": "string"},
        "alternative_sector": {"type": ["string", "null"]},
        "stage": {"type": "string", "enum": ["EARLY", "MID", "LATE", "UNKNOWN"]},
        "macro_drivers": {"type": "array", "items": {"type": "string"}},
        "breadth_read": {"type": "string"},
        "positioning_read": {"type": "string"},
        "bull_case": {"type": "array", "items": {"type": "string"}},
        "bear_case": {"type": "array", "items": {"type": "string"}},
        "invalidation_triggers": {"type": "array", "items": {"type": "string"}},
        "entry_tactic": {
            "type": "string",
            "enum": [
                "ALLOCATE_NOW",
                "STAGGER_3_TRANCHES",
                "WAIT_PULLBACK",
                "WAIT_CONFIRMATION",
                "HOLD_CASH",
            ],
        },
        "rebalance_horizon_weeks": {"type": "integer"},
        "alignment_with_ranking": {
            "type": "string",
            "enum": ["STRONG", "MIXED", "CONTRADICTORY"],
        },
        "confidence_by_dimension": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in _ETF_CONFIDENCE_KEYS},
            "required": list(_ETF_CONFIDENCE_KEYS),
            "additionalProperties": False,
        },
    },
    "required": [
        "verdict",
        "conviction_score",
        "rotation_summary",
        "top_sector_verdict",
        "stage",
        "macro_drivers",
        "breadth_read",
        "positioning_read",
        "bull_case",
        "bear_case",
        "invalidation_triggers",
        "entry_tactic",
        "rebalance_horizon_weeks",
        "alignment_with_ranking",
        "confidence_by_dimension",
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


def _extract_usage_context(response: Any) -> dict[str, Any]:
    """Estrae token counts + web_search dalla usage di Anthropic, safe-access."""
    ctx: dict[str, Any] = {}
    usage = getattr(response, "usage", None)
    if usage is None:
        return ctx

    for attr in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        val = getattr(usage, attr, None)
        if isinstance(val, int) and val:
            ctx[attr] = val

    try:
        ws = usage.server_tool_use.web_search_requests
    except AttributeError:
        ws = None
    if isinstance(ws, int) and ws:
        ctx["web_search_count"] = ws
        ctx["web_search_cost_usd"] = round(ws * WEB_SEARCH_UNIT_PRICE_USD, 4)
    return ctx


def _call_claude_with_schema(
    user_prompt: str,
    system_prompt: str,
    json_schema: dict,
) -> dict:
    """Chiama Claude con prompt caching + json_schema e ritorna il payload grezzo.

    Helper interno: gestisce build client, tools, error mapping, parse JSON,
    normalizzazione 0-10 degli score (Claude a volte ritorna 0-100). Il
    chiamante è responsabile di validare il payload nello schema pydantic
    corretto (ThesisVerdict per stock, ETFRotationVerdict per ETF).
    """
    import anthropic

    client = _build_client()

    kwargs: dict[str, Any] = dict(
        model=AI_MODEL,
        max_tokens=AI_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": {"type": "json_schema", "schema": json_schema}},
    )
    tools = _build_tools()
    if tools:
        kwargs["tools"] = tools

    _log.info(
        "ai_call_start",
        extra={"ctx": {"model": AI_MODEL, "web_search": bool(tools)}},
    )
    t0 = time.monotonic()
    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as err:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _log.error(
            "ai_call_error",
            extra={
                "ctx": {
                    "model": AI_MODEL,
                    "duration_ms": duration_ms,
                    "kind": "api_status",
                    "status": getattr(err, "status_code", None),
                }
            },
        )
        raise AIValidationError(f"Anthropic API error: {err.message}") from err
    except anthropic.APIConnectionError as err:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _log.error(
            "ai_call_error",
            extra={
                "ctx": {
                    "model": AI_MODEL,
                    "duration_ms": duration_ms,
                    "kind": "connection",
                }
            },
        )
        raise AIValidationError(f"Network error talking to Anthropic: {err}") from err

    duration_ms = int((time.monotonic() - t0) * 1000)
    usage_ctx = _extract_usage_context(response)
    usage_ctx["model"] = AI_MODEL
    usage_ctx["duration_ms"] = duration_ms
    usage_ctx["stop_reason"] = getattr(response, "stop_reason", None)
    _log.info("ai_call_success", extra={"ctx": usage_ctx})

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

    return payload


def call_validation(user_prompt: str) -> ThesisVerdict:
    """Chiama Claude per la validazione tesi stock e ritorna ``ThesisVerdict``."""
    payload = _call_claude_with_schema(user_prompt, SYSTEM_PROMPT, _JSON_SCHEMA)
    try:
        return ThesisVerdict.model_validate(payload)
    except Exception as err:
        raise AIValidationError(f"Schema mismatch (stock): {err}") from err


def call_etf_validation(user_prompt: str) -> ETFRotationVerdict:
    """Chiama Claude per la validazione rotazione ETF, ritorna ``ETFRotationVerdict``."""
    payload = _call_claude_with_schema(user_prompt, ETF_SYSTEM_PROMPT, _ETF_JSON_SCHEMA)
    try:
        return ETFRotationVerdict.model_validate(payload)
    except Exception as err:
        raise AIValidationError(f"Schema mismatch (etf): {err}") from err


def call_contrarian_validation(user_prompt: str) -> ContrarianVerdict:
    """Chiama Claude per la validazione tesi contrarian (mean reversion)."""
    payload = _call_claude_with_schema(
        user_prompt, CONTRA_SYSTEM_PROMPT, _CONTRA_JSON_SCHEMA
    )
    try:
        return ContrarianVerdict.model_validate(payload)
    except Exception as err:
        raise AIValidationError(f"Schema mismatch (contrarian): {err}") from err
