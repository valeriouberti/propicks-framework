"""Manual AI verdicts store — paste-based accuracy tracking.

Quando il trader usa LLM esterno (Perplexity Pro, GPT/Gemini diretto,
Claude.ai web) invece del Claude SDK call, può incollare la response qui
per linkare il verdict a un trade e tracciare l'accuracy ex-post.

Schema: ``manual_ai_verdicts`` (vedi ``io/schema.sql``).

Workflow:
1. Trader incolla risposta LLM in dashboard page Momentum/Contrarian/Thematic
2. ``parse_paste()`` estrae verdict/conviction da JSON inline (best effort)
3. ``save_manual_verdict()`` persiste con trade_id=None (pre-trade)
4. Quando trade aperto: ``link_to_trade()`` aggancia trade_id
5. Quando trade chiuso: page 9 Stats accuracy section calcola match outcome
"""

from __future__ import annotations

import json
import re
from typing import Any

from propicks.io.db import connect

VALID_SOURCES = ("perplexity_pro", "sonar", "gemini", "gpt", "claude_web", "other")
VALID_VERDICTS = ("CONFIRM", "CAUTION", "REJECT")


def parse_paste(raw: str) -> dict[str, Any]:
    """Best-effort parser per estrarre verdict + conviction dal testo.

    Cerca pattern:
    1. JSON block tra ``---JSON---`` separator (Sonar format)
    2. JSON block in code fence ```json ... ```
    3. Plain JSON object (regex {...} top-level)
    4. Pattern testuale "verdict: CONFIRM" / "conviction: 8"

    Returns:
        dict con chiavi disponibili: verdict, conviction, parsed_payload (raw JSON str).
        Tutte ottimistiche — il chiamante può fare override manuale.
    """
    out: dict[str, Any] = {
        "verdict": None,
        "conviction": None,
        "parsed_payload": None,
    }
    if not raw or not raw.strip():
        return out

    # Pattern 1: ---JSON--- separator (Sonar nativo)
    if "---JSON---" in raw:
        json_part = raw.split("---JSON---", 1)[1].strip()
        # Strip trailing markdown / commenti
        # Cerca primo { e ultimo }
        m = re.search(r"\{.*\}", json_part, re.DOTALL)
        if m:
            try:
                payload = json.loads(m.group(0))
                _extract_from_payload(payload, out)
                out["parsed_payload"] = json.dumps(payload)
                return out
            except (json.JSONDecodeError, ValueError):
                pass

    # Pattern 2: code fence ```json
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence_match:
        try:
            payload = json.loads(fence_match.group(1))
            _extract_from_payload(payload, out)
            out["parsed_payload"] = json.dumps(payload)
            return out
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 3: plain top-level JSON object
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    if m:
        try:
            payload = json.loads(m.group(0))
            _extract_from_payload(payload, out)
            out["parsed_payload"] = json.dumps(payload)
            return out
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 4: regex testuale fallback
    v_match = re.search(
        r"verdict[\s:]*[\"\'`]?(CONFIRM|CAUTION|REJECT)",
        raw, re.IGNORECASE,
    )
    if v_match:
        out["verdict"] = v_match.group(1).upper()

    c_match = re.search(
        r"conviction(?:_score)?[\s:]*[\"\'`]?(\d{1,2})",
        raw, re.IGNORECASE,
    )
    if c_match:
        try:
            c = int(c_match.group(1))
            if 0 <= c <= 10:
                out["conviction"] = c
            elif 10 < c <= 100:  # 0-100 scale fallback
                out["conviction"] = round(c / 10)
        except ValueError:
            pass

    return out


def _extract_from_payload(payload: dict, out: dict) -> None:
    """Mutate out dict con verdict/conviction dal payload JSON parsato."""
    v = payload.get("verdict")
    if isinstance(v, str) and v.upper() in VALID_VERDICTS:
        out["verdict"] = v.upper()

    for key in ("conviction_score", "conviction"):
        c = payload.get(key)
        if isinstance(c, (int, float)):
            if 0 <= c <= 10:
                out["conviction"] = int(c)
                break
            if 10 < c <= 100:
                out["conviction"] = round(c / 10)
                break


def save_manual_verdict(
    *,
    ticker: str,
    source: str,
    raw_paste: str,
    verdict: str | None = None,
    conviction: int | None = None,
    strategy: str | None = None,
    trade_id: int | None = None,
    parsed_payload: str | None = None,
    notes: str | None = None,
) -> int:
    """Persiste un manual verdict. Ritorna l'id inserito.

    Se ``verdict``/``conviction`` non passati, viene tentato auto-parse via
    ``parse_paste(raw_paste)``. Override esplicito (kwarg) ha sempre la
    precedenza sul parsing.
    """
    if not ticker or not raw_paste:
        raise ValueError("ticker e raw_paste obbligatori")
    if source not in VALID_SOURCES:
        raise ValueError(f"source '{source}' non valida — usa {VALID_SOURCES}")

    # Auto-parse se mancano fields
    if verdict is None or conviction is None or parsed_payload is None:
        parsed = parse_paste(raw_paste)
        if verdict is None:
            verdict = parsed.get("verdict")
        if conviction is None:
            conviction = parsed.get("conviction")
        if parsed_payload is None:
            parsed_payload = parsed.get("parsed_payload")

    if verdict is not None and verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict '{verdict}' non valido — usa {VALID_VERDICTS}")
    if conviction is not None and not (0 <= conviction <= 10):
        raise ValueError(f"conviction '{conviction}' fuori range 0-10")

    conn = connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO manual_ai_verdicts
                (trade_id, ticker, strategy, source, verdict, conviction,
                 raw_paste, parsed_payload, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id, ticker.upper(), strategy, source,
                verdict, conviction, raw_paste, parsed_payload, notes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def list_verdicts_for_ticker(ticker: str) -> list[dict]:
    """Tutti i verdict per ticker, ordinati pasted_at desc."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT id, trade_id, ticker, strategy, source, verdict, conviction,
                      raw_paste, parsed_payload, notes, pasted_at
               FROM manual_ai_verdicts
               WHERE ticker = ?
               ORDER BY pasted_at DESC""",
            (ticker.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_all_verdicts(
    *,
    strategy: str | None = None,
    source: str | None = None,
    linked_only: bool = False,
) -> list[dict]:
    """Lista tutti i verdict. Filtri opzionali per strategy/source.

    ``linked_only=True`` → solo verdict con trade_id non-null (per accuracy).
    """
    conn = connect()
    try:
        where = []
        params: list = []
        if strategy:
            where.append("strategy = ?")
            params.append(strategy)
        if source:
            where.append("source = ?")
            params.append(source)
        if linked_only:
            where.append("trade_id IS NOT NULL")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"""SELECT id, trade_id, ticker, strategy, source, verdict, conviction,
                       raw_paste, parsed_payload, notes, pasted_at
                FROM manual_ai_verdicts
                {where_sql}
                ORDER BY pasted_at DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def link_to_trade(verdict_id: int, trade_id: int) -> None:
    """Linka un verdict esistente a un trade_id."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE manual_ai_verdicts SET trade_id = ? WHERE id = ?",
            (trade_id, verdict_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_verdict(verdict_id: int) -> None:
    """Rimuove un verdict (per cleanup paste errati)."""
    conn = connect()
    try:
        conn.execute("DELETE FROM manual_ai_verdicts WHERE id = ?", (verdict_id,))
        conn.commit()
    finally:
        conn.close()


def compute_accuracy(
    *,
    strategy: str | None = None,
    source: str | None = None,
) -> dict:
    """Calcola accuracy dei verdict linkati a trade chiusi.

    Match logic:
        verdict CONFIRM + outcome WIN  → correct (true positive)
        verdict CONFIRM + outcome LOSS → false_positive (AI sbagliata)
        verdict REJECT  + outcome WIN  → false_negative (trader giusto a ignorare)
        verdict REJECT  + outcome LOSS → correct (true negative — AI giusta)
        verdict CAUTION + any          → neutral (skipped)

    WIN = pnl_pct > 0, LOSS = pnl_pct ≤ 0.

    Returns:
        Dict con counts + rates per categoria + Brier score equivalente
        (mappa verdict→prob: CONFIRM=0.8, CAUTION=0.5, REJECT=0.2; outcome 1/0).
    """
    conn = connect()
    try:
        where = ["v.trade_id IS NOT NULL", "t.status = 'closed'", "t.pnl_pct IS NOT NULL"]
        params: list = []
        if strategy:
            where.append("v.strategy = ?")
            params.append(strategy)
        if source:
            where.append("v.source = ?")
            params.append(source)

        rows = conn.execute(
            f"""SELECT v.verdict, v.conviction, v.source, v.strategy,
                       t.pnl_pct, t.ticker
                FROM manual_ai_verdicts v
                JOIN trades t ON v.trade_id = t.id
                WHERE {' AND '.join(where)}""",
            params,
        ).fetchall()
    finally:
        conn.close()

    n_confirm_win = n_confirm_loss = 0
    n_reject_win = n_reject_loss = 0
    n_caution = 0
    brier_sum = 0.0
    n_brier = 0

    prob_map = {"CONFIRM": 0.8, "CAUTION": 0.5, "REJECT": 0.2}

    for r in rows:
        verdict = r["verdict"]
        pnl = r["pnl_pct"]
        if verdict not in VALID_VERDICTS or pnl is None:
            continue
        won = pnl > 0
        if verdict == "CONFIRM":
            if won:
                n_confirm_win += 1
            else:
                n_confirm_loss += 1
        elif verdict == "REJECT":
            if won:
                n_reject_win += 1
            else:
                n_reject_loss += 1
        else:
            n_caution += 1

        # Brier: (prob - actual)^2
        prob = prob_map.get(verdict, 0.5)
        actual = 1.0 if won else 0.0
        brier_sum += (prob - actual) ** 2
        n_brier += 1

    n_total = n_confirm_win + n_confirm_loss + n_reject_win + n_reject_loss + n_caution
    n_directional = n_confirm_win + n_confirm_loss + n_reject_win + n_reject_loss
    n_correct = n_confirm_win + n_reject_loss
    n_wrong = n_confirm_loss + n_reject_win

    accuracy = (n_correct / n_directional) if n_directional > 0 else None
    brier = (brier_sum / n_brier) if n_brier > 0 else None

    return {
        "n_total": n_total,
        "n_directional": n_directional,  # esclude CAUTION
        "n_caution": n_caution,
        "n_correct": n_correct,
        "n_wrong": n_wrong,
        "accuracy": accuracy,
        "n_confirm_win": n_confirm_win,
        "n_confirm_loss": n_confirm_loss,
        "n_reject_win": n_reject_win,
        "n_reject_loss": n_reject_loss,
        "brier_score": brier,  # < 0.25 = better than random
    }
