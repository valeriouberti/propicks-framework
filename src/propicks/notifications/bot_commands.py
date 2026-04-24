"""Command handlers per il Telegram bot.

Ogni handler è una funzione **pura** che accetta argomenti di comando
(``str``) e ritorna un dict ``{text, parse_mode}`` da inviare. Il wiring
con ``python-telegram-bot`` vive in ``bot.py`` (handler lì wrappano queste
funzioni in async coroutines che adattano a ``telegram.Update``).

Testabilità: puoi testare ``handle_status()`` passando una chat_id + args
senza toccare Telegram.

Auth: ogni comando viene chiamato SOLO se ``is_authorized(chat_id)``
(check in bot.py prima di dispatch). Questi handler non rifanno l'auth.
"""

from __future__ import annotations

import os
from typing import Any

_MARKDOWN = "Markdown"


def _env_chat_ids() -> list[str]:
    """Parse ``PROPICKS_TELEGRAM_CHAT_ID`` env — supporta CSV multi-chat."""
    raw = os.environ.get("PROPICKS_TELEGRAM_CHAT_ID", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def is_authorized(chat_id: str | int) -> bool:
    """True se il chat_id è nella whitelist env."""
    allowed = _env_chat_ids()
    if not allowed:
        return False
    return str(chat_id) in allowed


# ---------------------------------------------------------------------------
# /start, /help
# ---------------------------------------------------------------------------
def handle_start(_args: list[str]) -> dict:
    return {
        "text": (
            "👋 *Propicks Bot* — Phase 4 attiva.\n\n"
            "Invia /help per la lista comandi.\n"
            "I nuovi alert dallo scheduler (READY, regime change, trailing stop) "
            "arriveranno in questa chat automaticamente."
        ),
        "parse_mode": _MARKDOWN,
    }


def handle_help(_args: list[str]) -> dict:
    return {
        "text": (
            "*Comandi disponibili*\n\n"
            "/status — P\\&L live + posizioni aperte\n"
            "/portfolio — dettaglio per ticker\n"
            "/alerts — alert pending non-ack\n"
            "/ack `N` — acknowledge alert N\n"
            "/ackall — ack tutti pending\n"
            "/history — ultimi 10 job scheduler\n"
            "/cache — stats cache OHLCV\n"
            "/regime — regime macro corrente\n"
            "/report — attribution summary ultimi 30gg\n"
            "/help — questo messaggio"
        ),
        "parse_mode": _MARKDOWN,
    }


# ---------------------------------------------------------------------------
# /status — portfolio summary
# ---------------------------------------------------------------------------
def handle_status(_args: list[str]) -> dict:
    from propicks.domain.sizing import portfolio_value
    from propicks.io.portfolio_store import load_portfolio, unrealized_pl

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)
    cash_pct = (cash / total * 100) if total > 0 else 0

    # Unrealized P&L (mark-to-market). Può essere lento (N chiamate prezzi).
    # Se ci sono molte posizioni, il bot potrebbe timeoutare — proteggiamo.
    try:
        pnl, _prices = unrealized_pl(portfolio)
    except Exception:
        pnl = None

    lines = [
        "📊 *PORTFOLIO STATUS*",
        f"Totale: `€ {total:,.2f}`",
        f"Cash: `€ {cash:,.2f}` (_{cash_pct:.1f}%_)",
        f"Posizioni aperte: *{len(positions)}* / 10",
    ]
    if pnl is not None:
        emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        lines.append(f"P\\&L unrealized: {emoji} `€ {pnl:+,.2f}`")

    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


def handle_portfolio(_args: list[str]) -> dict:
    from propicks.io.portfolio_store import load_portfolio, unrealized_pl

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})

    if not positions:
        return {"text": "📊 Nessuna posizione aperta.", "parse_mode": None}

    try:
        _pnl, prices = unrealized_pl(portfolio)
    except Exception:
        prices = {}

    lines = ["📊 *POSIZIONI APERTE*", ""]
    for ticker, pos in positions.items():
        entry = float(pos["entry_price"])
        shares = int(pos.get("shares") or 0)
        current = prices.get(ticker)
        pnl_pct = ((current / entry) - 1) * 100 if current else None
        strategy = pos.get("strategy", "-")

        line = f"`{ticker}` ({strategy}) — {shares} × {entry:.2f}"
        if pnl_pct is not None:
            emoji = "🟢" if pnl_pct > 0 else "🔴" if pnl_pct < 0 else "⚪"
            line += f" {emoji} *{pnl_pct:+.2f}%*"
        lines.append(line)

    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


# ---------------------------------------------------------------------------
# /alerts, /ack, /ackall
# ---------------------------------------------------------------------------
def handle_alerts(_args: list[str]) -> dict:
    from propicks.scheduler.alerts import list_pending_alerts

    alerts = list_pending_alerts(limit=20)
    if not alerts:
        return {"text": "✅ Nessun alert pending.", "parse_mode": None}

    _SEV = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
    lines = [f"🔔 *{len(alerts)} ALERT PENDING*", ""]
    for a in alerts:
        sev = _SEV.get(a.get("severity", "info"), "ℹ️")
        ticker = f" `{a['ticker']}`" if a.get("ticker") else ""
        msg = (a.get("message") or "").split("\n")[0][:80]
        lines.append(f"`[{a['id']}]` {sev}{ticker} — {msg}")
    lines.append("")
    lines.append("_Ack con_ `/ack N` _o_ `/ackall`")

    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


def handle_ack(args: list[str]) -> dict:
    from propicks.scheduler.alerts import acknowledge_alert

    if not args:
        return {"text": "⚠️ Usa `/ack N` dove N è l'ID dell'alert.", "parse_mode": _MARKDOWN}
    try:
        alert_id = int(args[0])
    except ValueError:
        return {"text": f"⚠️ ID non valido: `{args[0]}`", "parse_mode": _MARKDOWN}

    if acknowledge_alert(alert_id):
        return {"text": f"✅ Alert `{alert_id}` acknowledged.", "parse_mode": _MARKDOWN}
    return {
        "text": f"⚠️ Alert `{alert_id}` non trovato o già acknowledged.",
        "parse_mode": _MARKDOWN,
    }


def handle_ackall(_args: list[str]) -> dict:
    from propicks.scheduler.alerts import acknowledge_all

    n = acknowledge_all()
    return {"text": f"✅ Acknowledged {n} alerts.", "parse_mode": None}


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------
def handle_history(_args: list[str]) -> dict:
    from propicks.scheduler.history import list_recent_runs

    runs = list_recent_runs(limit=10)
    if not runs:
        return {"text": "📜 Nessun run registrato.", "parse_mode": None}

    _STATUS = {"success": "✅", "error": "❌", "running": "…", "partial": "◐"}
    lines = ["📜 *ULTIMI JOB RUN*", ""]
    for r in runs:
        icon = _STATUS.get(r.get("status"), "?")
        dur = f"{r['duration_ms'] or 0}ms"
        items = r.get("n_items")
        items_str = f" ({items}i)" if items is not None else ""
        lines.append(f"{icon} `{r['job_name']}` — {dur}{items_str}")

    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


# ---------------------------------------------------------------------------
# /cache, /regime — quick diagnostics
# ---------------------------------------------------------------------------
def handle_cache(_args: list[str]) -> dict:
    from propicks.io.db import market_ohlcv_stats

    stats = market_ohlcv_stats()
    lines = ["💾 *CACHE OHLCV*", ""]
    for interval, s in stats.items():
        lines.append(
            f"`{interval}`: {s.get('total_rows') or 0} rows, "
            f"{s.get('n_tickers') or 0} ticker, "
            f"max date `{s.get('date_max') or '-'}`"
        )
    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


def handle_report(_args: list[str]) -> dict:
    """Invia il summary dell'ultimo attribution report (Phase 9).

    Il report completo è un markdown file. Per Telegram usiamo un *summary
    inline*: KPI portfolio + per-strategy stats + attention. Se l'utente
    vuole il full markdown, lo legge da filesystem locale.
    """
    from propicks.domain.attribution import (
        aggregate_by_strategy,
        filter_trades_by_period,
        strategy_gate_status,
    )
    from propicks.io.journal_store import load_journal
    from propicks.reports.attribution_report import latest_report_path

    report_path = latest_report_path()

    # Generate inline summary (not read markdown — re-compute per-freshness)
    trades = load_journal()
    closed_30d = filter_trades_by_period(trades, period_days=30)

    lines = ["📊 *ATTRIBUTION SUMMARY*"]
    if report_path:
        import os
        name = os.path.basename(report_path)
        lines.append(f"_Full markdown:_ `{name}`")
    lines.append("")

    if not closed_30d:
        lines.append("_Nessun trade chiuso ultimi 30gg._")
        return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}

    aggs = aggregate_by_strategy(closed_30d)
    gate = strategy_gate_status(
        aggregate_by_strategy(filter_trades_by_period(trades, period_days=180))
    )

    lines.append("*Ultimi 30gg per strategia*")
    for strat, stats in sorted(aggs.items(), key=lambda x: -x[1]["n_trades"]):
        wr = f"{stats['win_rate'] * 100:.0f}%" if stats.get("win_rate") else "—"
        avg = f"{stats['avg_pnl_pct']:+.2f}%" if stats.get("avg_pnl_pct") is not None else "—"
        pf = stats.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None and pf != float("inf") else "—"
        lines.append(
            f"  • `{strat}` — {stats['n_trades']} trade, win {wr}, "
            f"avg {avg}, PF {pf_str}"
        )

    # Gate status (6mo) per strategie
    gate_failed = {k: v for k, v in gate.items() if not v["passed"]}
    if gate_failed:
        lines.append("")
        lines.append("⚠️ *Gate Phase 7 — strategie under threshold (180gg):*")
        for strat, info in gate_failed.items():
            lines.append(f"  • `{strat}` ({info['n_trades']} trade)")

    # Heavy losses ultimi 30gg
    heavy = [t for t in closed_30d if (t.get("pnl_pct") or 0) <= -10.0]
    if heavy:
        lines.append("")
        lines.append(f"🚨 *{len(heavy)} trade con loss > 10% (30gg)*")
        for t in heavy[:3]:
            lines.append(f"  • `{t['ticker']}` {t.get('pnl_pct', 0):+.2f}%")

    return {"text": "\n".join(lines), "parse_mode": _MARKDOWN}


def handle_regime(_args: list[str]) -> dict:
    from propicks.io.db import connect

    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM regime_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "text": "🔄 Regime non ancora registrato. Run: `/history`",
            "parse_mode": _MARKDOWN,
        }

    label = row["regime_label"]
    code = row["regime_code"]
    emoji = {5: "🟢", 4: "🟢", 3: "🟡", 2: "🟠", 1: "🔴"}.get(code, "❓")
    return {
        "text": (
            f"{emoji} *REGIME*: `{label}` ({code}/5)\n"
            f"Rilevato: `{row['date']}`\n"
            f"ADX `{row['adx']}` · RSI `{row['rsi']}`"
        ),
        "parse_mode": _MARKDOWN,
    }


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------
COMMANDS: dict[str, Any] = {
    "start": handle_start,
    "help": handle_help,
    "status": handle_status,
    "portfolio": handle_portfolio,
    "alerts": handle_alerts,
    "ack": handle_ack,
    "ackall": handle_ackall,
    "history": handle_history,
    "cache": handle_cache,
    "regime": handle_regime,
    "report": handle_report,
}
