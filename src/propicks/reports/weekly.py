"""Report settimanale in Markdown."""

from __future__ import annotations

from datetime import datetime, timedelta

from propicks.config import DATE_FMT
from propicks.io.journal_store import load_journal
from propicks.io.portfolio_store import load_portfolio, unrealized_pl
from propicks.reports.benchmark import get_benchmark_performance
from propicks.reports.common import (
    fmt_pct,
    trades_closed_between,
    trades_opened_between,
)


def generate_weekly_report() -> str:
    trades = load_journal()
    portfolio = load_portfolio()

    now = datetime.now()
    start = now - timedelta(days=7)
    closed_w = trades_closed_between(trades, start, now)
    opened_w = trades_opened_between(trades, start, now)
    open_positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)

    unrealized_total, prices = unrealized_pl(portfolio)

    sp500 = get_benchmark_performance("^GSPC", 7)
    ftsemib = get_benchmark_performance("FTSEMIB.MI", 7)

    lines: list[str] = []
    lines.append(f"# Report Settimanale — {now.strftime(DATE_FMT)}")
    lines.append("")
    lines.append(f"_Periodo: {start.strftime(DATE_FMT)} → {now.strftime(DATE_FMT)}_")
    lines.append("")

    lines.append("## Sommario")
    lines.append("")
    lines.append(f"- **Posizioni aperte**: {len(open_positions)}")
    lines.append(f"- **Trade aperti nella settimana**: {len(opened_w)}")
    lines.append(f"- **Trade chiusi nella settimana**: {len(closed_w)}")
    lines.append(f"- **P&L unrealized**: {unrealized_total:+.2f}")
    lines.append(f"- **Cash disponibile**: {cash:.2f}")
    lines.append("")

    lines.append("## Benchmark (ultimi 7 giorni)")
    lines.append("")
    lines.append(f"- **S&P 500 (^GSPC)**: {fmt_pct(sp500)}")
    lines.append(f"- **FTSE MIB (FTSEMIB.MI)**: {fmt_pct(ftsemib)}")
    lines.append("")

    lines.append("## Posizioni aperte")
    lines.append("")
    if not open_positions:
        lines.append("_Nessuna posizione aperta._")
    else:
        lines.append("| Ticker | Shares | Entry | Current | P&L € | P&L % | Stop | Target | Strategia |")
        lines.append("|--------|--------|-------|---------|-------|-------|------|--------|-----------|")
        for ticker, p in open_positions.items():
            cur = prices.get(ticker)
            shares = p.get("shares")
            # shares=None su posizioni legacy pre-sync: P&L € non calcolabile.
            pl_abs = (cur - p["entry_price"]) * shares if (cur and shares is not None) else None
            pl_pct = (cur - p["entry_price"]) / p["entry_price"] * 100 if cur else None
            target = p.get("target")
            lines.append(
                f"| {ticker} | {shares if shares is not None else '-'} | {p['entry_price']:.2f} | "
                f"{(f'{cur:.2f}' if cur else 'N/A')} | "
                f"{(f'{pl_abs:+.2f}' if pl_abs is not None else 'N/A')} | "
                f"{(fmt_pct(pl_pct))} | "
                f"{p['stop_loss']:.2f} | "
                f"{(f'{target:.2f}' if target is not None else '-')} | "
                f"{p.get('strategy') or '-'} |"
            )
    lines.append("")

    lines.append("## Trade chiusi questa settimana")
    lines.append("")
    if not closed_w:
        lines.append("_Nessun trade chiuso nel periodo._")
    else:
        lines.append("| Ticker | Dir | Entry | Exit | P&L % | Durata | Motivo | Strategia |")
        lines.append("|--------|-----|-------|------|-------|--------|--------|-----------|")
        for t in closed_w:
            lines.append(
                f"| {t['ticker']} | {t.get('direction', '-')} | "
                f"{t['entry_price']:.2f} | {t['exit_price']:.2f} | "
                f"{t['pnl_pct']:+.2f}% | {t.get('duration_days', '-')} gg | "
                f"{t.get('exit_reason') or '-'} | {t.get('strategy') or '-'} |"
            )
    lines.append("")

    lines.append("## Checklist azioni prossima settimana")
    lines.append("")
    lines.append("- [ ] Rivedere stop su posizioni aperte con profit > 5%")
    lines.append("- [ ] Check earnings in calendar per posizioni aperte")
    lines.append("- [ ] Aggiornare watchlist con nuovi ingressi basket Pro Picks")
    lines.append("- [ ] Verificare che riserva cash sia >= 20%")
    lines.append("- [ ] Rileggere i trade chiusi e annotare lesson learned")
    lines.append("")

    lines.append("---")
    lines.append(f"_Generato il {now.strftime('%Y-%m-%d %H:%M')}_")

    return "\n".join(lines)
