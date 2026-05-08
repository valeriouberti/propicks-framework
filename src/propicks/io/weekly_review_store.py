"""Weekly portfolio review snapshot — build current + load prior history.

Pattern: ogni weekly review salva snapshot in ``manual_ai_verdicts`` con
``ticker='PORTFOLIO'``, ``strategy='weekly_review'``, ``parsed_payload``
contiene il JSON verdict + snapshot data come ``raw_paste`` (markdown).

History: per Phase 2 time-series → pull last K reviews per build trend.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any


def build_portfolio_snapshot() -> dict[str, Any]:
    """Costruisce snapshot live del portfolio per weekly review.

    Pull da live state DB (positions / journal / ai_verdicts) + market data
    fresca (current_prices). Output dict consumabile da
    ``render_weekly_review_user_prompt``.
    """
    import statistics

    from propicks.config import (
        DATE_FMT, ETF_BENCHMARK, ETF_MAX_AGGREGATE_EXPOSURE_PCT, MAX_POSITIONS,
        MIN_CASH_RESERVE_PCT, STOCK_MAX_AGGREGATE_EXPOSURE_PCT,
    )
    from propicks.domain.etf_universe import resolve_sector_key
    from propicks.domain.exposure import compute_sector_exposure
    from propicks.domain.regime import classify_regime
    from propicks.domain.sizing import (
        etf_aggregate_exposure, portfolio_market_value, portfolio_value,
        stock_aggregate_exposure,
    )
    from propicks.io.journal_store import load_journal
    from propicks.io.manual_verdicts_store import compute_accuracy
    from propicks.io.portfolio_store import load_portfolio
    from propicks.market.yfinance_client import (
        DataUnavailable, download_weekly_history,
        get_current_prices, get_ticker_sector,
    )

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)

    tickers = sorted(positions.keys())
    prices: dict[str, float] = {}
    if tickers:
        try:
            prices = get_current_prices(tickers)
        except Exception:
            prices = {}

    total_market = portfolio_market_value(portfolio, prices) if prices else total
    stock_pct = stock_aggregate_exposure(portfolio) * 100 if total > 0 else 0
    etf_pct = etf_aggregate_exposure(portfolio) * 100 if total > 0 else 0
    cash_pct = cash / total * 100 if total > 0 else 0

    # Sector resolver
    sector_yf = {t: get_ticker_sector(t) for t in tickers} if tickers else {}
    sector_map = {
        t: resolve_sector_key(t, yahoo_sector_raw=s) for t, s in sector_yf.items()
    }
    currency_map = {t: (p.get("currency") or "EUR") for t, p in positions.items()}
    sector_exp = (
        compute_sector_exposure(
            positions, prices, sector_map, total_market,
            currency_map=currency_map,
        )
        if positions else {}
    )

    # Position details
    today_d = date.today()
    pos_rows = []
    unrealized_total = 0.0
    for tk, p in sorted(positions.items()):
        cur = prices.get(tk)
        entry = float(p.get("entry_price", 0))
        shares = float(p.get("shares") or 0)
        mv = (cur or entry) * shares
        cost_basis = entry * shares
        pnl_pct = (cur - entry) / entry * 100 if cur and entry > 0 else 0
        pnl_eur = (cur - entry) * shares if cur else 0
        unrealized_total += pnl_eur
        # Days held
        ed_str = p.get("entry_date") or ""
        days_held = None
        try:
            ed = datetime.strptime(ed_str, DATE_FMT).date()
            days_held = (today_d - ed).days
        except (ValueError, TypeError):
            pass
        pos_rows.append({
            "ticker": tk,
            "shares": shares,
            "cost_basis": cost_basis,
            "mv": mv,
            "pnl_pct": pnl_pct,
            "size_pct": (mv / total_market * 100) if total_market else 0,
            "stop": float(p.get("stop_loss") or 0),
            "target": float(p.get("target") or 0) if p.get("target") else None,
            "strategy": p.get("strategy") or "—",
            "sector": sector_map.get(tk) or "unknown",
            "days_held": days_held,
            "currency": p.get("currency") or "EUR",
        })

    # Week closings
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "closed"]
    closed.sort(key=lambda t: t.get("exit_date") or "", reverse=True)

    week_closings = []
    for t in closed:
        ed = t.get("exit_date")
        if not ed:
            continue
        try:
            ed_dt = datetime.strptime(ed, DATE_FMT).date()
        except (ValueError, TypeError):
            continue
        if (today_d - ed_dt).days <= 7:
            week_closings.append(t)
    week_pnls = [t["pnl_pct"] for t in week_closings if t.get("pnl_pct") is not None]
    week_wins = [p for p in week_pnls if p > 0]

    # Regime
    regime: dict | None = None
    try:
        bench = download_weekly_history(ETF_BENCHMARK)
        regime = classify_regime(bench)
    except (DataUnavailable, Exception):
        regime = None

    accuracy = compute_accuracy()

    return {
        "as_of": today_d.isoformat(),
        "portfolio_value": total + unrealized_total,
        "cost_basis": total,
        "cash": cash,
        "cash_pct": cash_pct,
        "stock_pct": stock_pct,
        "etf_pct": etf_pct,
        "n_positions": len(positions),
        "max_positions": MAX_POSITIONS,
        "unrealized_eur": unrealized_total,
        "unrealized_pct": (
            unrealized_total / (total - cash) * 100
            if (total - cash) > 0 else 0
        ),
        "positions": pos_rows,
        "sector_exp": sector_exp,
        "week_closings": week_closings,
        "week_pnls": week_pnls,
        "week_avg_pnl": statistics.mean(week_pnls) if week_pnls else 0,
        "week_win_rate": (len(week_wins) / len(week_pnls)) if week_pnls else 0,
        "recent_closings": closed[:10],
        "accuracy": accuracy,
        "regime": regime,
        "bucket_cap_status": {
            "stock_cap": STOCK_MAX_AGGREGATE_EXPOSURE_PCT,
            "etf_cap": ETF_MAX_AGGREGATE_EXPOSURE_PCT,
            "cash_min": MIN_CASH_RESERVE_PCT,
        },
    }


def save_weekly_review(snapshot: dict, paste_raw: str, source: str = "perplexity_pro") -> int:
    """Persiste weekly review verdict in manual_ai_verdicts.

    Storage convention:
    - ticker='PORTFOLIO'
    - strategy='weekly_review'
    - raw_paste = LLM response full text
    - parsed_payload = JSON estratto (verdict + history snapshot serializzato)
    - notes = as_of_date per query history

    Returns:
        verdict_id inserito.
    """
    from propicks.io.manual_verdicts_store import save_manual_verdict, parse_paste

    # Parse verdict from paste
    parsed = parse_paste(paste_raw)

    # Augment payload with snapshot summary (per history reconstruction)
    payload_dict: dict = {}
    if parsed.get("parsed_payload"):
        try:
            payload_dict = json.loads(parsed["parsed_payload"])
        except (json.JSONDecodeError, ValueError):
            payload_dict = {}
    payload_dict["_snapshot"] = {
        "as_of": snapshot.get("as_of"),
        "portfolio_value": snapshot.get("portfolio_value"),
        "cost_basis": snapshot.get("cost_basis"),
        "cash_pct": snapshot.get("cash_pct"),
        "stock_pct": snapshot.get("stock_pct"),
        "etf_pct": snapshot.get("etf_pct"),
        "n_positions": snapshot.get("n_positions"),
        "week_avg_pnl": snapshot.get("week_avg_pnl"),
        "week_win_rate": snapshot.get("week_win_rate"),
        "n_week_closings": len(snapshot.get("week_pnls") or []),
        "regime_code": (snapshot.get("regime") or {}).get("regime_code"),
    }
    parsed_payload_str = json.dumps(payload_dict)

    return save_manual_verdict(
        ticker="PORTFOLIO",
        source=source,
        raw_paste=paste_raw,
        verdict=parsed.get("verdict"),  # CONFIRM/CAUTION/REJECT — not used for review,
                                          # ma compat schema. None ok.
        conviction=parsed.get("conviction"),
        strategy="weekly_review",
        parsed_payload=parsed_payload_str,
        notes=f"weekly_review {snapshot.get('as_of')}",
    )


def load_review_history(n_weeks: int = 4) -> list[dict]:
    """Pull ultime N weekly review snapshots (più recente in coda) per Phase 2.

    Returns:
        List di portfolio_data dict ricostruiti da `_snapshot` field nel
        parsed_payload. Più recente in coda. Empty se prima review.
    """
    from propicks.io.db import connect

    conn = connect()
    try:
        rows = conn.execute(
            """SELECT parsed_payload, pasted_at, notes
               FROM manual_ai_verdicts
               WHERE ticker = 'PORTFOLIO' AND strategy = 'weekly_review'
                 AND parsed_payload IS NOT NULL
               ORDER BY pasted_at DESC
               LIMIT ?""",
            (n_weeks,),
        ).fetchall()
    finally:
        conn.close()

    history: list[dict] = []
    for r in rows:
        try:
            payload = json.loads(r["parsed_payload"])
            snap = payload.get("_snapshot")
            if snap:
                history.append(snap)
        except (json.JSONDecodeError, ValueError, KeyError):
            continue

    # Reverse → most recent in tail
    return list(reversed(history))


def load_weekly_review_verdicts(limit: int = 12) -> list[dict]:
    """Pull weekly review verdict completi (con health/action_items) per timeline UI."""
    from propicks.io.manual_verdicts_store import list_all_verdicts

    all_v = list_all_verdicts(strategy="weekly_review")
    out = []
    for v in all_v[:limit]:
        try:
            payload = json.loads(v.get("parsed_payload") or "{}")
        except (json.JSONDecodeError, ValueError):
            payload = {}
        out.append({
            "id": v["id"],
            "pasted_at": v.get("pasted_at"),
            "source": v.get("source"),
            "health_verdict": payload.get("health_verdict"),
            "conviction": payload.get("conviction_score"),
            "executive_summary": payload.get("executive_summary"),
            "snapshot": payload.get("_snapshot"),
            "action_items": payload.get("action_items") or [],
        })
    return out
