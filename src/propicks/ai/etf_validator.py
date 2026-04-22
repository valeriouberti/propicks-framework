"""Orchestrazione della validazione AI per la rotazione settoriale.

Parallelo a ``thesis_validator`` ma con assunzioni diverse:

- **Nessun gate hard su score**: il valore di una review macro sulla rotazione
  non dipende da un singolo score ETF. Il chiamante decide se validare.
- **Gate opzionale su regime**: in STRONG_BEAR la rotazione proposta è
  "flat": chiamare Claude è solo per confermare la decisione di non allocare.
  Utile ma spendere va deciso — default skip, override con ``force=True``.
- **Cache più lunga (48h)**: la view macro si muove più lenta di una tesi
  single-name. 48h bilancia freschezza e costo — dopo 48h il regime o la
  leadership settoriale possono essere cambiati abbastanza da valere un
  re-check.

Chiave cache: (region, regime_code, YYYY-MM-DD) — la stessa region nello
stesso regime nello stesso giorno ha la stessa view macro, non serve
richiamare.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import (
    AIValidationError,
    ETFRotationVerdict,
    call_etf_validation,
)
from propicks.ai.etf_prompts import render_etf_user_prompt
from propicks.config import AI_CACHE_DIR, get_etf_benchmark

_CACHE_VERSION = "etf-v1"
_CACHE_TTL_HOURS = 48


def _cache_path(region: str, regime_code: int | None, day: str) -> str:
    rc = regime_code if regime_code is not None else "NA"
    return os.path.join(AI_CACHE_DIR, f"rotation_{region}_{rc}_{_CACHE_VERSION}_{day}.json")


def _load_cached(region: str, regime_code: int | None, day: str) -> dict | None:
    path = _cache_path(region, regime_code, day)
    if not os.path.exists(path):
        return None
    age_h = (time.time() - os.path.getmtime(path)) / 3600.0
    if age_h > _CACHE_TTL_HOURS:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(region: str, regime_code: int | None, day: str, payload: dict) -> None:
    path = _cache_path(region, regime_code, day)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def validate_rotation(
    ranked: list[dict],
    allocation: dict | None = None,
    *,
    region: str = "US",
    force: bool = False,
    skip_in_strong_bear: bool = True,
) -> dict | None:
    """Valida qualitativamente la rotazione proposta con Claude.

    Args:
        ranked: output di ``domain.etf_scoring.rank_universe``, ordinato.
        allocation: output di ``suggest_allocation`` (opzionale ma consigliato).
        region: "US" | "EU" | "WORLD" | "ALL" — va in prompt per context.
        force: ignora cache + skip in STRONG_BEAR.
        skip_in_strong_bear: in regime 1 la risposta è ovvia (flat). Default
            skippa per non spendere. ``force=True`` o questo flag = False
            per chiamare comunque.

    Returns:
        dict serializzabile del verdict, o None se skippato/fallito.
    """
    if not ranked:
        return None

    regime_code = ranked[0].get("regime_code")

    if not force and skip_in_strong_bear and regime_code == 1:
        print(
            "[ai] ETF rotation validation skipped: STRONG_BEAR regime → "
            "no sector allocation proposed. Use force=True per override.",
            file=sys.stderr,
        )
        return None

    day = date.today().isoformat()

    if not force:
        cached = _load_cached(region, regime_code, day)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

    try:
        check_budget()
    except AIBudgetExceeded as err:
        print(f"[ai] ETF rotation skipped: {err}", file=sys.stderr)
        return None

    user_prompt = render_etf_user_prompt(
        ranked=ranked,
        allocation=allocation,
        as_of_date=day,
        region=region,
        benchmark=get_etf_benchmark(region),
    )

    try:
        verdict: ETFRotationVerdict = call_etf_validation(user_prompt)
    except AIValidationError as err:
        print(f"[ai] ETF rotation validation failed: {err}", file=sys.stderr)
        return None

    record_call()
    payload = verdict.model_dump()
    _save_cache(region, regime_code, day, payload)
    payload["_cache_hit"] = False
    return payload
