"""Orchestrazione della validazione AI con cache su disco.

Flusso:
    analyze_ticker(...)  →  validate_thesis(result)  →  dict con ai_verdict

La cache è indicizzata da (ticker, YYYY-MM-DD) per evitare chiamate duplicate
nello stesso giorno. TTL configurabile via ``AI_CACHE_TTL_HOURS``.
"""

from __future__ import annotations

import sys
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import AIValidationError, ThesisVerdict, call_validation
from propicks.ai.prompts import render_user_prompt
from propicks.config import AI_CACHE_TTL_HOURS, AI_MIN_SCORE_FOR_VALIDATION
from propicks.io.db import ai_verdict_cache_get, ai_verdict_cache_put

_CACHE_VERSION = "v4"
_STRATEGY_TAG = "momentum"

_RR_TOLERANCE = 0.05
_RR_CONFIRM_FLOOR = 2.0


def _cache_key(ticker: str, day: str) -> str:
    """Chiave stabile identica al naming file-based legacy (per continuità
    della migration: ``AAPL_v4_2026-04-24``)."""
    safe = ticker.upper().replace("/", "_")
    return f"{safe}_{_CACHE_VERSION}_{day}"


def _enforce_reward_risk(analysis: dict, payload: dict) -> None:
    """Recompute R/R from (price, stop, target) and enforce the CONFIRM floor.

    The model occasionally reports a reward_risk_ratio inconsistent with its
    own suggested stop/target. We overwrite with the arithmetic truth and,
    if the true R/R is below the 2.0 floor, downgrade CONFIRM → CAUTION.

    Quando il modello non fornisce un target esplicito in
    ``suggested_adjustments``, il sanity layer non può ricalcolare R/R
    aritmeticamente — ma deve comunque applicare il floor sul valore
    *reportato* da Claude (parità di trattamento con contrarian, dove
    reversion_target è required nello schema).
    """
    ticker = analysis.get("ticker", "?")
    price = analysis.get("price")
    adj = payload.get("suggested_adjustments") or {}

    raw_stop = adj.get("stop")
    stop = raw_stop if isinstance(raw_stop, (int, float)) else analysis.get("stop_suggested")

    raw_target = adj.get("target")
    target = raw_target if isinstance(raw_target, (int, float)) else None

    reported = payload.get("reward_risk_ratio")
    can_compute = (
        isinstance(price, (int, float))
        and isinstance(stop, (int, float))
        and isinstance(target, (int, float))
        and price > stop
        and target > price
    )

    if can_compute:
        risk = price - stop
        reward = target - price
        computed = round(reward / risk, 2)
        if not isinstance(reported, (int, float)) or abs(computed - reported) > _RR_TOLERANCE:
            print(
                f"[ai] R/R corrected for {ticker}: "
                f"reported={reported}, computed={computed:.2f} "
                f"(entry={price:.2f}, stop={stop:.2f}, target={target:.2f})",
                file=sys.stderr,
            )
            payload["reward_risk_ratio"] = computed
        effective_rr: float | None = computed
    else:
        # Fallback: usa il R/R reportato da Claude per applicare comunque il floor.
        # Senza questo, una CONFIRM senza target esplicito sfuggirebbe al gate.
        effective_rr = reported if isinstance(reported, (int, float)) else None

    if (
        effective_rr is not None
        and effective_rr < _RR_CONFIRM_FLOOR
        and payload.get("verdict") == "CONFIRM"
    ):
        print(
            f"[ai] Verdict downgraded CONFIRM→CAUTION for {ticker}: "
            f"R/R {effective_rr:.2f} < floor {_RR_CONFIRM_FLOOR}",
            file=sys.stderr,
        )
        payload["verdict"] = "CAUTION"


def _load_cached(ticker: str, day: str) -> dict | None:
    """Lookup verdict nella tabella ``ai_verdicts`` con TTL applicato in SQL."""
    return ai_verdict_cache_get(
        _cache_key(ticker, day), ttl_hours=AI_CACHE_TTL_HOURS
    )


def _save_cache(ticker: str, day: str, verdict: dict) -> None:
    """Inserisce il verdict nella tabella ``ai_verdicts``."""
    ai_verdict_cache_put(
        _cache_key(ticker, day),
        strategy=_STRATEGY_TAG,
        ticker=ticker,
        payload=verdict,
    )


def validate_thesis(
    analysis: dict,
    *,
    force: bool = False,
    gate: bool = True,
) -> dict | None:
    """Valida qualitativamente la tesi con Claude.

    Args:
        analysis: dict ritornato da ``analyze_ticker``.
        force: ignora la cache locale.
        gate: se True, salta la chiamata per score sotto
            ``AI_MIN_SCORE_FOR_VALIDATION``.

    Returns:
        dict serializzabile con il verdetto, o None se skippato/fallito.
        Il chiamante dovrebbe mergiarlo come ``result["ai_verdict"] = ...``.
    """
    if gate and analysis.get("score_composite", 0) < AI_MIN_SCORE_FOR_VALIDATION:
        return None

    if gate:
        regime = analysis.get("regime")
        if regime is None:
            # Fail-closed: senza classificazione di regime (storia weekly
            # insufficiente, IPO recenti, ticker thin) non possiamo applicare
            # il floor macro → meglio skip che spendere AI su setup che il
            # framework non può inquadrare. Coerente col quality gate
            # contrarian (fail-closed se EMA200w manca). Override con force=True.
            print(
                f"[ai] {analysis.get('ticker', '?')} skipped: weekly regime "
                f"non disponibile (storia insufficiente) — fail-closed",
                file=sys.stderr,
            )
            return None
        if not regime.get("entry_allowed", True):
            print(
                f"[ai] {analysis.get('ticker', '?')} skipped: weekly regime "
                f"{regime.get('regime', '?')} — no long entries allowed",
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
        print(f"[ai] {ticker} skipped: {err}", file=sys.stderr)
        return None

    user_prompt = render_user_prompt(analysis, as_of_date=day)

    try:
        verdict: ThesisVerdict = call_validation(user_prompt)
    except AIValidationError as err:
        print(f"[ai] validation failed for {ticker}: {err}", file=sys.stderr)
        return None

    record_call()
    payload = verdict.model_dump()
    _enforce_reward_risk(analysis, payload)
    _save_cache(ticker, day, payload)
    payload["_cache_hit"] = False
    return payload
