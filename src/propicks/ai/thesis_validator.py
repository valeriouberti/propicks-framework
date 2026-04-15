"""Orchestrazione della validazione AI con cache su disco.

Flusso:
    analyze_ticker(...)  →  validate_thesis(result)  →  dict con ai_verdict

La cache è indicizzata da (ticker, YYYY-MM-DD) per evitare chiamate duplicate
nello stesso giorno. TTL configurabile via ``AI_CACHE_TTL_HOURS``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from typing import Optional

from propicks.ai.claude_client import AIValidationError, ThesisVerdict, call_validation
from propicks.ai.prompts import render_user_prompt
from propicks.config import (
    AI_CACHE_DIR,
    AI_CACHE_TTL_HOURS,
    AI_MIN_SCORE_FOR_VALIDATION,
)


_CACHE_VERSION = "v3"

_RR_TOLERANCE = 0.05
_RR_CONFIRM_FLOOR = 2.0


def _cache_path(ticker: str, day: str) -> str:
    safe = ticker.upper().replace("/", "_")
    return os.path.join(AI_CACHE_DIR, f"{safe}_{_CACHE_VERSION}_{day}.json")


def _enforce_reward_risk(analysis: dict, payload: dict) -> None:
    """Recompute R/R from (price, stop, target) and enforce the CONFIRM floor.

    The model occasionally reports a reward_risk_ratio inconsistent with its
    own suggested stop/target. We overwrite with the arithmetic truth and,
    if the true R/R is below the 2.0 floor, downgrade CONFIRM → CAUTION.
    """
    price = analysis.get("price")
    adj = payload.get("suggested_adjustments") or {}

    raw_stop = adj.get("stop")
    stop = raw_stop if isinstance(raw_stop, (int, float)) else analysis.get("stop_suggested")

    raw_target = adj.get("target")
    target = raw_target if isinstance(raw_target, (int, float)) else None

    if price is None or stop is None or target is None:
        return
    risk = price - stop
    if risk <= 0:
        return
    reward = target - price
    computed = round(reward / risk, 2) if reward > 0 else 0.0

    reported = payload.get("reward_risk_ratio")
    if not isinstance(reported, (int, float)) or abs(computed - reported) > _RR_TOLERANCE:
        print(
            f"[ai] R/R corrected for {analysis.get('ticker', '?')}: "
            f"reported={reported}, computed={computed:.2f} "
            f"(entry={price:.2f}, stop={stop:.2f}, target={target:.2f})",
            file=sys.stderr,
        )
        payload["reward_risk_ratio"] = computed

    if computed < _RR_CONFIRM_FLOOR and payload.get("verdict") == "CONFIRM":
        print(
            f"[ai] Verdict downgraded CONFIRM→CAUTION for "
            f"{analysis.get('ticker', '?')}: computed R/R {computed:.2f} "
            f"< floor {_RR_CONFIRM_FLOOR}",
            file=sys.stderr,
        )
        payload["verdict"] = "CAUTION"


def _load_cached(ticker: str, day: str) -> Optional[dict]:
    path = _cache_path(ticker, day)
    if not os.path.exists(path):
        return None
    age_h = (time.time() - os.path.getmtime(path)) / 3600.0
    if age_h > AI_CACHE_TTL_HOURS:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(ticker: str, day: str, verdict: dict) -> None:
    path = _cache_path(ticker, day)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def validate_thesis(
    analysis: dict,
    *,
    force: bool = False,
    gate: bool = True,
) -> Optional[dict]:
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

    ticker = analysis["ticker"]
    day = date.today().isoformat()

    if not force:
        cached = _load_cached(ticker, day)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

    user_prompt = render_user_prompt(analysis, as_of_date=day)

    try:
        verdict: ThesisVerdict = call_validation(user_prompt)
    except AIValidationError as err:
        print(f"[ai] validation failed for {ticker}: {err}", file=sys.stderr)
        return None

    payload = verdict.model_dump()
    _enforce_reward_risk(analysis, payload)
    _save_cache(ticker, day, payload)
    payload["_cache_hit"] = False
    return payload
