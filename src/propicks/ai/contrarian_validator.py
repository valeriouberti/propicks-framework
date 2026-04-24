"""Orchestrazione della validazione AI per la strategia CONTRARIAN.

Flusso:
    analyze_contra_ticker(...)  →  validate_contrarian_thesis(result)  →
    dict con ai_verdict

Chiave di cache distinta dalla cache momentum: ``<TICKER>_contra_<VERSION>_<YYYY-MM-DD>.json``.
Stesso ticker può essere scansionato da entrambe le strategie nello stesso
giorno senza collisioni (momentum cache = ``<TICKER>_v4_<YYYY-MM-DD>.json``).

Regime gate inverso rispetto a ``thesis_validator``:
- Skip in STRONG_BULL (5) e STRONG_BEAR (1) — edge collassato in entrambi gli estremi
- Non skippa in BEAR (2) — è anzi un regime favorevole per contrarian se quality regge
"""

from __future__ import annotations

import sys
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import (
    AIValidationError,
    ContrarianVerdict,
    call_contrarian_validation,
)
from propicks.ai.contrarian_prompts import render_contrarian_user_prompt
from propicks.config import (
    CONTRA_AI_CACHE_TTL_HOURS,
    CONTRA_AI_MIN_SCORE_FOR_VALIDATION,
)
from propicks.io.db import ai_verdict_cache_get, ai_verdict_cache_put

_CACHE_VERSION = "contra_v1"
_STRATEGY_TAG = "contrarian"

# Stessa filosofia di thesis_validator._RR_CONFIRM_FLOOR: un CONFIRM deve
# avere R/R matematicamente >= 2.0. Sotto → downgrade automatico.
_RR_CONFIRM_FLOOR = 2.0
_RR_REJECT_FLOOR = 1.0  # R/R < 1 significa "stop più distante del target" → setup rotto
_RR_TOLERANCE = 0.05


def _cache_key(ticker: str, day: str) -> str:
    """Chiave stabile identica al naming file-based legacy: ``AAPL_contra_v1_2026-04-24``."""
    safe = ticker.upper().replace("/", "_")
    return f"{safe}_{_CACHE_VERSION}_{day}"


def _enforce_contrarian_sanity(analysis: dict, payload: dict) -> None:
    """Recompute R/R e valida la posizionalità di target/invalidation.

    Claude può:
    - ritornare R/R inconsistente con target/invalidation
    - allucinare `reversion_target` SOTTO current_price (nonsenso per long)
    - allucinare `invalidation_price` SOPRA current_price (stop sopra entry)

    Questa funzione è la safety net architetturale. Coerente con
    `thesis_validator._enforce_reward_risk`:
    - downgrade CONFIRM → CAUTION se R/R < 2.0
    - downgrade qualsiasi verdict → REJECT se R/R < 1.0 o se target/invalidation
      sono posizionalmente invalidi (setup strutturalmente rotto)
    """
    price = analysis.get("price")
    target = payload.get("reversion_target")
    invalidation = payload.get("invalidation_price")
    verdict = payload.get("verdict")
    ticker = analysis.get("ticker", "?")

    if not isinstance(price, (int, float)) or price <= 0:
        return

    # 1. Positional checks: target > price, invalidation < price
    if isinstance(target, (int, float)) and target <= price:
        print(
            f"[contrarian-ai] {ticker}: reversion_target {target:.2f} <= price "
            f"{price:.2f} (non è mean reversion long). Verdict → REJECT.",
            file=sys.stderr,
        )
        payload["verdict"] = "REJECT"
        payload["_sanity_override"] = "target_below_price"
        return
    if isinstance(invalidation, (int, float)) and invalidation >= price:
        print(
            f"[contrarian-ai] {ticker}: invalidation_price {invalidation:.2f} "
            f">= price {price:.2f} (stop sopra entry). Verdict → REJECT.",
            file=sys.stderr,
        )
        payload["verdict"] = "REJECT"
        payload["_sanity_override"] = "invalidation_above_price"
        return

    # 2. Compute arithmetic R/R and enforce floors
    if not isinstance(target, (int, float)) or not isinstance(invalidation, (int, float)):
        return
    reward = target - price
    risk = price - invalidation
    if risk <= 0 or reward <= 0:
        return
    computed_rr = reward / risk

    # Store il R/R computato nel payload per audit trail
    payload["_rr_computed"] = round(computed_rr, 2)

    if computed_rr < _RR_REJECT_FLOOR:
        if verdict != "REJECT":
            print(
                f"[contrarian-ai] {ticker}: R/R computed {computed_rr:.2f} "
                f"< {_RR_REJECT_FLOOR} floor → downgrade {verdict} → REJECT",
                file=sys.stderr,
            )
        payload["verdict"] = "REJECT"
        payload["_sanity_override"] = "rr_below_reject_floor"
    elif computed_rr < _RR_CONFIRM_FLOOR and verdict == "CONFIRM":
        print(
            f"[contrarian-ai] {ticker}: R/R computed {computed_rr:.2f} "
            f"< {_RR_CONFIRM_FLOOR} CONFIRM floor → downgrade CONFIRM → CAUTION",
            file=sys.stderr,
        )
        payload["verdict"] = "CAUTION"
        payload["_sanity_override"] = "rr_below_confirm_floor"


def _load_cached(ticker: str, day: str) -> dict | None:
    """Lookup verdict nella tabella ``ai_verdicts`` con TTL applicato in SQL."""
    return ai_verdict_cache_get(
        _cache_key(ticker, day), ttl_hours=CONTRA_AI_CACHE_TTL_HOURS
    )


def _save_cache(ticker: str, day: str, verdict: dict) -> None:
    """Inserisce il verdict nella tabella ``ai_verdicts``."""
    ai_verdict_cache_put(
        _cache_key(ticker, day),
        strategy=_STRATEGY_TAG,
        ticker=ticker,
        payload=verdict,
    )


def validate_contrarian_thesis(
    analysis: dict,
    *,
    force: bool = False,
    gate: bool = True,
) -> dict | None:
    """Valida qualitativamente la tesi contrarian con Claude.

    Args:
        analysis: dict ritornato da ``analyze_contra_ticker``.
        force: ignora cache locale + gate.
        gate: se True, skip score < soglia e regime agli estremi.

    Returns:
        dict serializzabile del verdetto, o None se skippato/fallito.
    """
    if gate and analysis.get("score_composite", 0) < CONTRA_AI_MIN_SCORE_FOR_VALIDATION:
        return None

    if gate:
        regime = analysis.get("regime")
        if regime is not None:
            code = regime.get("regime_code")
            # Skip estremi: STRONG_BULL (no vere oversold) e STRONG_BEAR (falling knife)
            if code in (1, 5):
                label = regime.get("regime", "?")
                print(
                    f"[contrarian-ai] {analysis.get('ticker', '?')} skipped: "
                    f"regime {label} ({code}/5) — edge contrarian collassa agli estremi",
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
        print(f"[contrarian-ai] {ticker} skipped: {err}", file=sys.stderr)
        return None

    user_prompt = render_contrarian_user_prompt(analysis, as_of_date=day)

    try:
        verdict: ContrarianVerdict = call_contrarian_validation(user_prompt)
    except AIValidationError as err:
        print(
            f"[contrarian-ai] validation failed for {ticker}: {err}",
            file=sys.stderr,
        )
        return None

    record_call()
    payload = verdict.model_dump()
    _enforce_contrarian_sanity(analysis, payload)
    _save_cache(ticker, day, payload)
    payload["_cache_hit"] = False
    return payload
