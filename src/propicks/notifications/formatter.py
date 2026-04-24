"""Formatter: dict alert → messaggio Telegram Markdown.

**Pura** (nessun I/O). Testabile. Gestisce tutti i tipi alert emessi dai
job di Phase 3:

| Type | Template |
|------|----------|
| ``watchlist_ready`` | 🟢 READY {ticker} — price X vs target Y (Z% off), score S |
| ``regime_change`` | 🔄 REGIME CHANGE: OLD → NEW (icona severity) |
| ``trailing_stop_update`` | 📈 TRAIL {ticker} — stop X → Y |
| ``stale_position`` | ⏳ TIME-STOP {ticker} — flat da molti giorni |
| ``stale_watchlist`` | 🧹 STALE WATCHLIST — N entries > 60gg |
| ``contra_near_cap`` | ⚠️ CONTRA BUCKET — X.X% / 20% cap |
| ``job_failed`` | 🚨 JOB ERROR: {job_name} |

Messaggi usano Markdown Telegram v1 (``_italic_``, ``*bold*``, backticks
per code). Evitiamo Markdown v2 perché richiede escape di `.` in numeri.
"""

from __future__ import annotations

from typing import Any

_SEVERITY_EMOJI = {
    "info": "ℹ️",
    "warning": "⚠️",
    "critical": "🚨",
}

_TYPE_EMOJI = {
    "watchlist_ready": "🟢",
    "regime_change": "🔄",
    "trailing_stop_update": "📈",
    "stale_position": "⏳",
    "stale_watchlist": "🧹",
    "contra_near_cap": "⚠️",
    "job_failed": "🚨",
}


def _fmt_watchlist_ready(alert: dict, meta: dict) -> str:
    """🟢 READY AAPL — price 185.10 vs target 185.50 (0.22% off), score 78.3"""
    ticker = alert.get("ticker", "?")
    price = meta.get("price")
    target = meta.get("target")
    dist = meta.get("distance_pct")
    score = meta.get("score")
    classif = meta.get("classification", "")

    lines = [f"🟢 *READY* `{ticker}`"]
    if price is not None and target is not None:
        lines.append(
            f"Price `{price:.2f}` vs target `{target:.2f}`"
            + (f" (_{dist * 100:.2f}% off_)" if dist is not None else "")
        )
    if score is not None:
        lines.append(f"Score: *{score:.1f}* {classif}")
    lines.append("\n➡️ Se il setup è ancora valido: `propicks-scan {ticker} --validate`")
    return "\n".join(lines)


def _fmt_regime_change(alert: dict, meta: dict) -> str:
    """🔄 REGIME CHANGE: NEUTRAL (3/5) → BULL (4/5)"""
    from_label = meta.get("from", "?")
    from_code = meta.get("from_code", "?")
    to_label = meta.get("to", "?")
    to_code = meta.get("to_code", "?")
    severity = alert.get("severity", "warning")
    sev_emoji = _SEVERITY_EMOJI.get(severity, "🔄")

    # Frecce direzionali: ↗ miglioramento, ↘ peggioramento
    try:
        direction = "↗️" if to_code > from_code else "↘️"
    except TypeError:
        direction = "🔄"

    return (
        f"{sev_emoji} *REGIME CHANGE*\n"
        f"`{from_label}` ({from_code}/5) {direction} `{to_label}` ({to_code}/5)\n\n"
        + _regime_commentary(to_code)
    )


def _regime_commentary(to_code: int | None) -> str:
    """Commento operativo sul nuovo regime."""
    if to_code is None:
        return ""
    commentary = {
        5: "_Risk-on puro: tech + cyclicals + financials_",
        4: "_Mid-cycle: long pullback su qualità_",
        3: "_Quality tilt: healthcare + industrials_",
        2: "_Difensivo: staples + utilities + healthcare. Contrarian favorevole._",
        1: "_Capital preservation: flat, skip long. Contrarian hard-gate a 0._",
    }
    return commentary.get(to_code, "")


def _fmt_trailing_stop_update(alert: dict, meta: dict) -> str:
    """📈 TRAIL AAPL — stop 180.00 → 185.50 (highest 192.30)"""
    ticker = alert.get("ticker", "?")
    cur = meta.get("current_stop")
    new = meta.get("suggested_stop")
    highest = meta.get("highest_price")

    lines = [f"📈 *TRAIL UPDATE* `{ticker}`"]
    if cur is not None and new is not None:
        delta_pct = ((new / cur) - 1) * 100
        lines.append(f"Stop: `{cur:.2f}` → `{new:.2f}` (*+{delta_pct:.2f}%*)")
    if highest is not None:
        lines.append(f"Highest since entry: `{highest:.2f}`")

    rationale = meta.get("rationale")
    if isinstance(rationale, list) and rationale:
        lines.append("_" + rationale[0] + "_")

    lines.append("\n➡️ Applica: `propicks-portfolio manage --apply`")
    return "\n".join(lines)


def _fmt_stale_position(alert: dict, meta: dict) -> str:
    """⏳ TIME-STOP AAPL — trade flat, considera chiusura"""
    ticker = alert.get("ticker", "?")
    price = meta.get("price")
    entry = meta.get("entry_price")
    entry_date = meta.get("entry_date", "?")

    lines = [f"⏳ *TIME-STOP* `{ticker}`"]
    if price is not None and entry is not None and entry > 0:
        pnl = (price / entry - 1) * 100
        lines.append(f"Entry `{entry:.2f}` ({entry_date}) → price `{price:.2f}` (*{pnl:+.2f}%*)")
    lines.append("_Trade flat da molti giorni. Considera chiusura._")
    lines.append(f"\n➡️ Close: `propicks-journal close {ticker} --exit-price X --reason 'time-stop'`")
    return "\n".join(lines)


def _fmt_stale_watchlist(alert: dict, meta: dict) -> str:
    """🧹 STALE WATCHLIST — 5 entries > 60gg"""
    tickers = meta.get("tickers", [])
    days = meta.get("days_threshold", 60)

    lines = [f"🧹 *STALE WATCHLIST* — {len(tickers)} entries > {days}gg"]
    if tickers:
        preview = ", ".join(f"`{t}`" for t in tickers[:10])
        if len(tickers) > 10:
            preview += f" + altri {len(tickers) - 10}"
        lines.append(preview)
    lines.append("\n_Considera cleanup manuale via dashboard watchlist page._")
    return "\n".join(lines)


def _fmt_contra_near_cap(alert: dict, meta: dict) -> str:
    """⚠️ CONTRA BUCKET — 16.2% / 20% cap"""
    expo = meta.get("exposure")
    cap = meta.get("cap")
    lines = ["⚠️ *CONTRARIAN BUCKET NEAR CAP*"]
    if expo is not None and cap is not None:
        lines.append(f"Esposizione: *{expo * 100:.1f}%* / {cap * 100:.0f}% cap")
    lines.append("_Nuovi entry contrarian verranno rifiutati quando al 20%._")
    return "\n".join(lines)


def _fmt_job_failed(alert: dict, meta: dict) -> str:
    """🚨 JOB ERROR"""
    job = meta.get("job_name", "?")
    err = meta.get("error", alert.get("message", "unknown"))
    return f"🚨 *JOB FAILED*: `{job}`\n`{err[:200]}`"


def _fmt_report_ready(alert: dict, meta: dict) -> str:
    """📊 WEEKLY REPORT READY"""
    iso_week = meta.get("iso_week", "?")
    n_closed = meta.get("n_closed_this_week", 0)
    n_total = meta.get("n_trades", 0)

    lines = [
        f"📊 *ATTRIBUTION REPORT* — {iso_week}",
        f"{n_closed} trade chiusi questa settimana ({n_total} totali).",
        "",
        "_Invia_ `/report` _per il summary inline, o apri il file markdown_",
        "_in `reports/attribution_*.md` per il dettaglio completo._",
    ]
    return "\n".join(lines)


def _fmt_generic(alert: dict, meta: dict) -> str:
    """Fallback: usa il `message` plain. Non dovrebbe accadere in produzione."""
    sev = alert.get("severity", "info")
    emoji = _SEVERITY_EMOJI.get(sev, "📢")
    ticker = alert.get("ticker")
    header = f"{emoji} *{alert.get('type', 'alert').upper()}*"
    if ticker:
        header += f" `{ticker}`"
    return f"{header}\n{alert.get('message', '')}"


_FORMATTERS = {
    "watchlist_ready": _fmt_watchlist_ready,
    "regime_change": _fmt_regime_change,
    "trailing_stop_update": _fmt_trailing_stop_update,
    "stale_position": _fmt_stale_position,
    "stale_watchlist": _fmt_stale_watchlist,
    "contra_near_cap": _fmt_contra_near_cap,
    "job_failed": _fmt_job_failed,
    "report_ready": _fmt_report_ready,
}


def alert_to_markdown(alert: dict) -> str:
    """Formatta un alert dict in messaggio Telegram Markdown.

    Input: dict con keys ``type``, ``severity``, ``ticker``, ``message``,
    ``metadata`` (dict), ``id`` (opzionale).

    Output: string Markdown (MarkdownV1) con emoji, ticker/price inline code,
    commentary italic, e CTA finale ("come agire") dove applicabile.

    Footer: ``Ack con /ack N`` se ``id`` presente — permette di completare
    il workflow dall'app Telegram senza passare da CLI.
    """
    meta: Any = alert.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}

    fn = _FORMATTERS.get(alert.get("type"), _fmt_generic)
    body = fn(alert, meta)

    # Footer con ack command se ID disponibile
    alert_id = alert.get("id")
    if alert_id is not None:
        body += f"\n\n_Ack con_ `/ack {alert_id}`"

    return body
