"""Report mensile in Markdown con confronto benchmark e breakdown strategia."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from propicks.config import DATE_FMT
from propicks.domain.verdict import verdict
from propicks.io.journal_store import load_journal
from propicks.reports.benchmark import get_benchmark_performance
from propicks.reports.common import fmt_pct, trades_closed_between


def generate_monthly_report() -> str:
    trades = load_journal()

    now = datetime.now()
    start = now - timedelta(days=30)
    closed_m = trades_closed_between(trades, start, now)
    closed_all = [t for t in trades if t.get("status") == "closed"]

    sp500_30 = get_benchmark_performance("^GSPC", 30)
    ftsemib_30 = get_benchmark_performance("FTSEMIB.MI", 30)

    pnls_m = [t["pnl_pct"] for t in closed_m]
    wins_m = [p for p in pnls_m if p > 0]
    wr_m = (len(wins_m) / len(pnls_m)) if pnls_m else 0.0
    cum_m = sum(pnls_m) if pnls_m else 0.0
    alpha = (cum_m - sp500_30) if (sp500_30 is not None and pnls_m) else None

    pnls_all = [t["pnl_pct"] for t in closed_all]
    wins_all = [p for p in pnls_all if p > 0]
    losses_all = [p for p in pnls_all if p <= 0]
    wr_all = (len(wins_all) / len(pnls_all)) if pnls_all else 0.0
    avg_win = statistics.mean(wins_all) if wins_all else 0.0
    avg_loss = statistics.mean(losses_all) if losses_all else 0.0
    pf = abs(avg_win / avg_loss) if avg_loss else (float("inf") if wins_all else 0.0)

    lines: list[str] = []
    month_tag = now.strftime("%Y-%m")
    lines.append(f"# Report Mensile — {month_tag}")
    lines.append("")
    lines.append(f"_Periodo: {start.strftime(DATE_FMT)} → {now.strftime(DATE_FMT)}_")
    lines.append("")

    lines.append("## Performance del mese")
    lines.append("")
    lines.append(f"- **Trade chiusi**: {len(closed_m)}")
    lines.append(f"- **Win rate**: {wr_m*100:.1f}%")
    lines.append(f"- **P&L cumulativo**: {cum_m:+.2f}%")
    lines.append("")

    lines.append("## Benchmark (ultimi 30 giorni)")
    lines.append("")
    lines.append(f"- **S&P 500 (^GSPC)**: {fmt_pct(sp500_30)}")
    lines.append(f"- **FTSE MIB (FTSEMIB.MI)**: {fmt_pct(ftsemib_30)}")
    if alpha is not None:
        lines.append(f"- **Alpha vs S&P 500**: {alpha:+.2f}%")
    else:
        lines.append("- **Alpha vs S&P 500**: N/A (dati insufficienti)")
    lines.append("")

    lines.append("## Performance da inizio (tutti i trade chiusi)")
    lines.append("")
    if not closed_all:
        lines.append("_Nessun trade chiuso nel journal._")
    else:
        lines.append(f"- **Trade chiusi totali**: {len(closed_all)}")
        lines.append(f"- **Win rate**: {wr_all*100:.1f}%  ({len(wins_all)}W / {len(losses_all)}L)")
        lines.append(f"- **Avg win**: {avg_win:+.2f}%")
        lines.append(f"- **Avg loss**: {avg_loss:+.2f}%")
        lines.append(f"- **Profit factor**: {pf:.2f}")
    lines.append("")

    lines.append("## Breakdown per strategia Pro Picks")
    lines.append("")
    if not closed_all:
        lines.append("_Nessun dato._")
    else:
        by_strat: dict[str, list[float]] = {}
        for t in closed_all:
            by_strat.setdefault(t.get("strategy") or "-", []).append(t["pnl_pct"])
        lines.append("| Strategia | # trade | Avg P&L | Win rate | Best | Worst |")
        lines.append("|-----------|---------|---------|----------|------|-------|")
        for strat, pls in by_strat.items():
            wr_s = sum(1 for p in pls if p > 0) / len(pls) * 100
            lines.append(
                f"| {strat} | {len(pls)} | {statistics.mean(pls):+.2f}% | "
                f"{wr_s:.1f}% | {max(pls):+.2f}% | {min(pls):+.2f}% |"
            )
    lines.append("")

    lines.append("## Verdetto")
    lines.append("")
    lines.append(f"**{verdict(wr_all, pf, len(closed_all))}**")
    lines.append("")

    lines.append("## Domande guida per la revisione strategica")
    lines.append("")
    lines.append("1. Quali strategie Pro Picks hanno dato i risultati migliori/peggiori?")
    lines.append("2. I trade con score Claude alto hanno effettivamente performato meglio?")
    lines.append("3. Gli stop sono stati rispettati? Quanti trade hanno chiuso oltre lo stop?")
    lines.append("4. La riserva cash minima del 20% è stata rispettata?")
    lines.append("5. Ci sono pattern nei trade perdenti (settore, catalyst, timing)?")
    lines.append("6. Le size effettive sono coerenti con il livello di convinzione?")
    lines.append("7. Cosa cambierei per il mese prossimo?")
    lines.append("")

    lines.append("---")
    lines.append(f"_Generato il {now.strftime('%Y-%m-%d %H:%M')}_")

    return "\n".join(lines)
