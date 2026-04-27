"""Orchestrazione della validazione AI per la rotazione settoriale.

Parallelo a ``thesis_validator`` ma con assunzioni diverse:

- **Nessun gate hard su score**: il valore di una review macro sulla rotazione
  non dipende da un singolo score ETF. Il chiamante decide se validare.
- **Gate opzionale su regime**: in STRONG_BEAR la rotazione proposta è
  "flat": chiamare Claude è solo per confermare la decisione di non allocare.
  Utile ma spendere va deciso — default skip, override con ``force=True``.
- **Cache breve (8h)**: la view macro si muove più lenta di una tesi
  single-name, ma il top sector può flippare intraday su catalyst (Fed
  leak, CPI surprise, geo-shock). Una TTL 48h serviva verdict stale fino a
  2 giorni dopo il flip; 8h copre la sessione corrente senza arrivare al
  giorno successivo.

Chiave cache: (region, regime_code, hash dei top-3 ranked, YYYY-MM-DD).
L'hash dei top-3 invalida il cache appena la leadership cambia anche
all'interno della stessa giornata: stesso regime ma top diverso = view
macro potenzialmente diversa, va richiamata.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import (
    AIValidationError,
    ETFRotationVerdict,
    call_etf_validation,
)
from propicks.ai.etf_prompts import render_etf_user_prompt
from propicks.config import get_etf_benchmark
from propicks.io.db import ai_verdict_cache_get, ai_verdict_cache_put

_CACHE_VERSION = "etf-v2"
_CACHE_TTL_HOURS = 8
_STRATEGY_TAG = "etf_rotation"
_RANKED_HASH_TOP_N = 3


def _ranked_hash(ranked: list[dict] | None, top_n: int = _RANKED_HASH_TOP_N) -> str:
    """Hash stabile dei top-N ticker per invalidare la cache su flip ranking.

    Usa solo il ticker (non lo score) per evitare cache miss su micro-drift
    di score che lasciano l'ordinamento invariato. SHA1 troncato a 8 char è
    largo abbastanza da evitare collisioni nel piccolo universo settoriale
    (~11 ticker per region).
    """
    if not ranked:
        return "empty"
    top = [r.get("ticker", "?") for r in ranked[:top_n]]
    digest = hashlib.sha1("|".join(top).encode("utf-8")).hexdigest()[:8]
    return digest


def _cache_key(
    region: str, regime_code: int | None, day: str, ranked_hash: str
) -> str:
    """Chiave: ``rotation_<REGION>_<REGIME>_<HASH>_<VERSION>_<DAY>``.

    L'hash dei top-3 ranked è incluso per invalidare il cache appena la
    leadership cambia: stesso regime ma top diverso = view macro
    potenzialmente diversa, va richiamata.
    """
    rc = regime_code if regime_code is not None else "NA"
    return f"rotation_{region}_{rc}_{ranked_hash}_{_CACHE_VERSION}_{day}"


def _load_cached(
    region: str, regime_code: int | None, day: str, ranked_hash: str
) -> dict | None:
    return ai_verdict_cache_get(
        _cache_key(region, regime_code, day, ranked_hash),
        ttl_hours=_CACHE_TTL_HOURS,
    )


def _ticker_handle(payload: dict, region: str) -> str:
    """Handle stabile per audit trail nella tabella ai_verdicts.

    Quando ``top_sector_verdict`` è "FLAT" (forced in STRONG_BEAR) o assente,
    usiamo "rotation:<REGION>" così che query analitiche non mescolino
    record di region diverse sotto lo stesso ticker fittizio.
    """
    raw = payload.get("top_sector_verdict")
    if isinstance(raw, str) and raw and raw.upper() != "FLAT":
        return raw
    return f"rotation:{region.upper()}"


def _save_cache(
    region: str,
    regime_code: int | None,
    day: str,
    ranked_hash: str,
    payload: dict,
) -> None:
    ai_verdict_cache_put(
        _cache_key(region, regime_code, day, ranked_hash),
        strategy=_STRATEGY_TAG,
        ticker=_ticker_handle(payload, region),
        payload=payload,
    )


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
    ranked_hash = _ranked_hash(ranked)

    if not force:
        cached = _load_cached(region, regime_code, day, ranked_hash)
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
    _save_cache(region, regime_code, day, ranked_hash, payload)
    payload["_cache_hit"] = False
    return payload
