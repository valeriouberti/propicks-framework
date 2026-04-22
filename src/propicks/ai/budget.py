"""Budget cap giornaliero sulle chiamate Anthropic.

Persistenza leggera: un file JSON al giorno in ``data/ai_cache/`` con
``{"calls": int, "est_cost_usd": float}``. Niente lock tra processi —
il trader è single-user, il worst-case è che due scan paralleli
superino di 1-2 chiamate il cap. Accettabile.

Cache hit NON chiama ``record_call`` — il budget si spende solo quando
si tocca davvero l'API.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

from propicks import config
from propicks.io.atomic import atomic_write_json


class AIBudgetExceeded(RuntimeError):
    """Budget giornaliero superato: la chiamata non è stata fatta."""


def _usage_path(day: str | None = None) -> str:
    # Letto lazy da config per permettere ai test di monkeypatchare
    # ``propicks.config.AI_CACHE_DIR`` senza duplicarsi il patching.
    day = day or date.today().isoformat()
    return os.path.join(config.AI_CACHE_DIR, f"usage_{day}.json")


def _load_usage(day: str | None = None) -> dict:
    path = _usage_path(day)
    if not os.path.exists(path):
        return {"calls": 0, "est_cost_usd": 0.0}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        # File corrotto: meglio ripartire da zero che bloccare la CLI.
        # Un utente che ha già buttato il file probabilmente voleva resettare.
        print(
            f"[ai.budget] usage file {path} illeggibile, reset a zero",
            file=sys.stderr,
        )
        return {"calls": 0, "est_cost_usd": 0.0}
    data.setdefault("calls", 0)
    data.setdefault("est_cost_usd", 0.0)
    return data


def check_budget(day: str | None = None) -> dict:
    """Solleva ``AIBudgetExceeded`` se calls o cost sono sopra cap.

    Ritorna lo stato corrente dell'usage per logging/debug del chiamante.
    """
    usage = _load_usage(day)
    calls = int(usage.get("calls", 0))
    cost = float(usage.get("est_cost_usd", 0.0))

    if calls >= config.AI_MAX_CALLS_PER_DAY:
        raise AIBudgetExceeded(
            f"daily call limit reached: {calls}/{config.AI_MAX_CALLS_PER_DAY}. "
            f"Override con PROPICKS_AI_MAX_CALLS_PER_DAY=<N> o aspetta "
            f"mezzanotte UTC (reset file giornaliero)."
        )
    if cost >= config.AI_MAX_COST_USD_PER_DAY:
        raise AIBudgetExceeded(
            f"daily cost limit reached: ${cost:.2f}/${config.AI_MAX_COST_USD_PER_DAY:.2f}. "
            f"Override con PROPICKS_AI_MAX_COST_USD_PER_DAY=<USD>."
        )
    return usage


def record_call(
    est_cost_usd: float | None = None,
    *,
    day: str | None = None,
) -> dict:
    """Incrementa il contatore giornaliero dopo una chiamata reale all'API.

    Se ``est_cost_usd`` è None, usa il default da config. Va chiamato
    SOLO per chiamate che hanno effettivamente toccato la rete — cache
    hit e skip di gate non spendono.
    """
    est_cost_usd = (
        config.AI_EST_COST_PER_CALL_USD
        if est_cost_usd is None
        else float(est_cost_usd)
    )
    usage = _load_usage(day)
    usage["calls"] = int(usage.get("calls", 0)) + 1
    usage["est_cost_usd"] = round(
        float(usage.get("est_cost_usd", 0.0)) + est_cost_usd, 4
    )
    atomic_write_json(_usage_path(day), usage)
    return usage


def current_usage(day: str | None = None) -> dict:
    """Legge lo stato corrente senza modifiche — utile per status/debug."""
    return _load_usage(day)
