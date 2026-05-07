"""Orchestrazione validazione AI per Sector-Filtered Momentum (SFM).

Parallelo a ``thesis_validator`` ma con cache key dedicata: lo stesso ticker
analizzato in contesto SFM (sector_key noto, peer-RS primary edge driver) ha
prior diverso da momentum standalone — non condividono cache.

## Differenze chiave da ``validate_thesis``

- **Cache key**: include sector_key per invalidare quando il ticker cambia
  contesto sector (raro ma possibile su GICS reshuffle).
- **Strategy tag**: ``sfm`` (vs ``momentum``) per audit trail in tabella
  ``ai_verdicts``.
- **Cache version**: ``sfm-v1``.
- **Score gate**: usa ``SFM_MIN_STOCK_SCORE`` (75 default) sul ``score_sfm``,
  non sul ``score_composite`` momentum classico.
- **Regime gate**: stesso failsafe momentum (skip se regime non disponibile o
  entry_allowed=False), ma più stretto in STRONG_BEAR (skip senza force).
- **Peer-RS gate** (HARD): se ``rs_vs_sector.score < 60 AND rs_vs_sector.slope ≤ 0``,
  skip silenziosamente — la tesi SFM senza peer-RS è solo sector beta. Il
  prompt SFM stesso applica downgrade a CAUTION/REJECT, ma evitiamo di
  spendere AI budget se il segnale è già morto.

## Sanity check post-AI

Identico a ``thesis_validator._enforce_reward_risk``: ricomputa R/R da
(price, stop, target) suggested_adjustments e downgrade CONFIRM→CAUTION se
sotto floor 2.0. Riusato (DRY) importando dal modulo momentum.
"""

from __future__ import annotations

import sys
from datetime import date

from propicks.ai.budget import AIBudgetExceeded, check_budget, record_call
from propicks.ai.claude_client import (
    AIValidationError,
    ThesisVerdict,
    call_sfm_validation,
)
from propicks.ai.sfm_prompts import render_sfm_user_prompt
from propicks.ai.thesis_validator import _enforce_reward_risk
from propicks.config import (
    SFM_AI_CACHE_TTL_HOURS,
    SFM_MIN_STOCK_SCORE,
    SFM_RS_OVERLAY_WEIGHT,
)
from propicks.io.db import ai_verdict_cache_get, ai_verdict_cache_put

_CACHE_VERSION = "sfm-v1"
_STRATEGY_TAG = "sfm"


def _cache_key(ticker: str, sector_key: str | None, day: str) -> str:
    """Chiave: ``<TICKER>_<SECTOR>_sfm-v1_<YYYY-MM-DD>``.

    Sector_key incluso per invalidare cache su sector reshuffle (raro ma
    possibile, vedi GICS 2018 reshuffle che spostò META/GOOGL da Tech a
    Communications).
    """
    safe_ticker = ticker.upper().replace("/", "_")
    safe_sector = (sector_key or "unknown").lower().replace("/", "_")
    return f"{safe_ticker}_{safe_sector}_{_CACHE_VERSION}_{day}"


def _load_cached(ticker: str, sector_key: str | None, day: str) -> dict | None:
    return ai_verdict_cache_get(
        _cache_key(ticker, sector_key, day),
        ttl_hours=SFM_AI_CACHE_TTL_HOURS,
    )


def _save_cache(
    ticker: str, sector_key: str | None, day: str, verdict: dict
) -> None:
    ai_verdict_cache_put(
        _cache_key(ticker, sector_key, day),
        strategy=_STRATEGY_TAG,
        ticker=ticker,
        payload=verdict,
    )


def _peer_rs_gate_fails(analysis: dict) -> bool:
    """HARD gate: peer-RS score < 60 AND slope ≤ 0 → skip senza spendere AI.

    Senza peer-RS leadership la tesi SFM degrade a sector beta — il prompt
    farebbe REJECT/CAUTION ma il valore aggiunto è zero, meglio non spendere.
    Per ticker non-US (rs_vs_sector=None) NON è un fail — il prompt sa che è
    informazione mancante e modera la conviction. Solo gate hard quando il
    segnale è esplicitamente DEAD.
    """
    rs = analysis.get("rs_vs_sector")
    if not rs:
        return False  # missing != dead
    score = rs.get("score")
    slope = rs.get("rs_slope")
    if not isinstance(score, (int, float)) or not isinstance(slope, (int, float)):
        return False
    return score < 60.0 and slope <= 0.0


def validate_sfm_thesis(
    analysis: dict,
    *,
    force: bool = False,
    gate: bool = True,
) -> dict | None:
    """Valida qualitativamente la tesi SFM con Claude.

    Args:
        analysis: dict ritornato da ``enrich_with_sfm_score`` (deve contenere
            ``sector_key``, ``peer_etf``, ``score_sfm``, oltre ai campi
            standard di ``analyze_ticker``).
        force: ignora cache + gate.
        gate: se True, applica i tre gate: score_sfm ≥ SFM_MIN_STOCK_SCORE,
            regime entry_allowed, peer-RS non DEAD.

    Returns:
        dict serializzabile del verdict, o None se skippato/fallito.
    """
    ticker = analysis.get("ticker", "?")
    sector_key = analysis.get("sector_key")

    if gate and not force:
        # Gate 1: score SFM threshold
        score_sfm = analysis.get("score_sfm", analysis.get("score_composite", 0))
        if score_sfm < SFM_MIN_STOCK_SCORE:
            return None

        # Gate 2: regime
        regime = analysis.get("regime")
        if regime is None:
            print(
                f"[ai/sfm] {ticker} skipped: weekly regime non disponibile "
                f"— fail-closed",
                file=sys.stderr,
            )
            return None
        if not regime.get("entry_allowed", True):
            print(
                f"[ai/sfm] {ticker} skipped: weekly regime "
                f"{regime.get('regime', '?')} — no long entries allowed",
                file=sys.stderr,
            )
            return None

        # Gate 3: peer-RS dead
        if _peer_rs_gate_fails(analysis):
            print(
                f"[ai/sfm] {ticker} skipped: peer-RS dead "
                f"(score < 60 AND slope ≤ 0) — passenger trade, "
                f"SFM thesis collapses to sector beta",
                file=sys.stderr,
            )
            return None

    day = date.today().isoformat()

    if not force:
        cached = _load_cached(ticker, sector_key, day)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

    try:
        check_budget()
    except AIBudgetExceeded as err:
        print(f"[ai/sfm] {ticker} skipped: {err}", file=sys.stderr)
        return None

    user_prompt = render_sfm_user_prompt(
        analysis,
        as_of_date=day,
        overlay_weight=SFM_RS_OVERLAY_WEIGHT,
    )

    try:
        verdict: ThesisVerdict = call_sfm_validation(user_prompt)
    except AIValidationError as err:
        print(f"[ai/sfm] validation failed for {ticker}: {err}", file=sys.stderr)
        return None

    record_call()
    payload = verdict.model_dump()
    # Riusa il sanity layer R/R del momentum standalone — stessa schema/floor.
    _enforce_reward_risk(analysis, payload)
    _save_cache(ticker, sector_key, day, payload)
    payload["_cache_hit"] = False
    return payload
