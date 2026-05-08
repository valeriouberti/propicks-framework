"""Orchestrazione validazione AI per strategia THEMATIC.

Flusso:
    analyze_theme(...) → validate_thematic_thesis(result) → dict ai_verdict

Cache key distinta: ``<TICKER>_thematic_v1_<YYYY-MM-DD>``. Stesso ticker non
collide con altre strategie (un thematic ETF non viene mai scansionato dal
flow stock momentum, ma il namespace separato è cheap).

Gate:
- Score floor: composite_final >= THEMATIC_AI_MIN_SCORE_FOR_VALIDATION (60)
- Regime: skip BEAR (2) e STRONG_BEAR (1) — engine già forza score basso ma
  il gate AI risparmia chiamate inutili
- Correlation kill: se corr_kill_applied=True (corr ≥ 0.85), skip — composite
  è già 0, niente da validare

Cache TTL 24h (parallelo momentum stock — narrative tematica si muove veloce
su catalyst sub-industry, non come macro-rotation che TTL 8h+).
"""

from __future__ import annotations

import sys
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import (
    AIValidationError,
    ThematicVerdict,
    call_thematic_validation,
)
from propicks.ai.thematic_prompts import render_thematic_user_prompt
from propicks.config import (
    THEMATIC_AI_CACHE_TTL_HOURS,
    THEMATIC_AI_MIN_SCORE_FOR_VALIDATION,
)
from propicks.io.db import ai_verdict_cache_get, ai_verdict_cache_put

_CACHE_VERSION = "thematic_v1"
_STRATEGY_TAG = "thematic"


def _cache_key(ticker: str, day: str) -> str:
    safe = ticker.upper().replace("/", "_")
    return f"{safe}_{_CACHE_VERSION}_{day}"


def _load_cached(ticker: str, day: str) -> dict | None:
    return ai_verdict_cache_get(_cache_key(ticker, day), ttl_hours=THEMATIC_AI_CACHE_TTL_HOURS)


def _save_cache(ticker: str, day: str, verdict: dict) -> None:
    ai_verdict_cache_put(
        _cache_key(ticker, day),
        strategy=_STRATEGY_TAG,
        ticker=ticker,
        payload=verdict,
    )


def _enforce_thematic_sanity(analysis: dict, payload: dict) -> None:
    """Sanity layer parallelo a thesis/contrarian validators.

    Regole:
    - Se alternative_ticker proposto NON è registrato in THEMATIC_ETFS → null
      (Claude può allucinare ticker; meglio null che ticker fantasma).
    - Se theme_stage = LATE e verdict = CONFIRM → downgrade CAUTION (late stage
      richiede staggered entry, non allocate now).
    """
    alt = payload.get("alternative_ticker")
    if isinstance(alt, str) and alt:
        from propicks.config import THEMATIC_ETFS
        if alt.upper() not in THEMATIC_ETFS:
            print(
                f"[thematic-ai] {analysis.get('ticker', '?')}: "
                f"alternative_ticker '{alt}' non in THEMATIC_ETFS — set null",
                file=sys.stderr,
            )
            payload["alternative_ticker"] = None
            payload["_sanity_override"] = "alternative_unknown"

    if payload.get("theme_stage") == "LATE" and payload.get("verdict") == "CONFIRM":
        print(
            f"[thematic-ai] {analysis.get('ticker', '?')}: "
            f"LATE stage + CONFIRM incoerente — downgrade CAUTION",
            file=sys.stderr,
        )
        payload["verdict"] = "CAUTION"
        payload["_sanity_override"] = "late_stage_downgrade"


def validate_thematic_thesis(
    analysis: dict,
    *,
    force: bool = False,
    gate: bool = True,
) -> dict | None:
    """Valida la tesi tematica con Claude.

    Args:
        analysis: dict da ``analyze_theme``.
        force: ignora cache + gate.
        gate: se True, applica score/regime/corr gates.

    Returns:
        dict serializzabile del verdict, o None se skippato/fallito.
    """
    if gate:
        # Gate corr_kill: composite è già 0, nessuna validazione utile
        if analysis.get("corr_kill_applied"):
            print(
                f"[thematic-ai] {analysis.get('ticker', '?')} skipped: "
                f"correlation kill-switch (corr ≥ 0.85, alfa illusorio)",
                file=sys.stderr,
            )
            return None

        if analysis.get("score_composite", 0) < THEMATIC_AI_MIN_SCORE_FOR_VALIDATION:
            return None

        regime = analysis.get("regime")
        if regime is not None:
            code = regime.get("regime_code")
            if code in (1, 2):
                label = regime.get("regime", "?")
                print(
                    f"[thematic-ai] {analysis.get('ticker', '?')} skipped: "
                    f"regime {label} ({code}/5) — tematici skip BEAR/STRONG_BEAR",
                    file=sys.stderr,
                )
                return None

    ticker = analysis["ticker"]
    day = date.today().isoformat()

    if not force:
        cached = _load_cached(ticker, day)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

    try:
        check_budget()
    except AIBudgetExceeded as err:
        print(f"[thematic-ai] {ticker} skipped: {err}", file=sys.stderr)
        return None

    user_prompt = render_thematic_user_prompt(analysis, as_of_date=day)

    try:
        verdict: ThematicVerdict = call_thematic_validation(user_prompt)
    except AIValidationError as err:
        print(f"[thematic-ai] validation failed for {ticker}: {err}", file=sys.stderr)
        return None

    record_call()
    payload = verdict.model_dump()
    _enforce_thematic_sanity(analysis, payload)
    _save_cache(ticker, day, payload)
    payload["_cache_hit"] = False
    return payload
