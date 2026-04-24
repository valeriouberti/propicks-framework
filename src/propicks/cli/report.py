"""CLI per la generazione dei report settimanali, mensili e attribution.

I report vengono sia stampati su terminale sia salvati in reports/
con filename datato.

Esempi:
    propicks-report weekly
    propicks-report monthly
    propicks-report attribution
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from propicks.config import DATE_FMT, REPORTS_DIR
from propicks.reports.monthly import generate_monthly_report
from propicks.reports.weekly import generate_weekly_report


def _write_report(content: str, filename: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def cmd_weekly(_: argparse.Namespace) -> int:
    content = generate_weekly_report()
    path = _write_report(content, f"weekly_{datetime.now().strftime(DATE_FMT)}.md")
    print(content)
    print(f"\n>>> report salvato in: {path}")
    return 0


def cmd_monthly(_: argparse.Namespace) -> int:
    content = generate_monthly_report()
    path = _write_report(content, f"monthly_{datetime.now().strftime('%Y-%m')}.md")
    print(content)
    print(f"\n>>> report salvato in: {path}")
    return 0


def cmd_attribution(_: argparse.Namespace) -> int:
    """Genera il weekly attribution report (Phase 9)."""
    from propicks.reports.attribution_report import weekly_attribution_report

    result = weekly_attribution_report()
    path = result["path"]
    # Stampa il contenuto come per gli altri report
    with open(path, encoding="utf-8") as f:
        content = f.read()
    print(content)
    print(f"\n>>> report salvato in: {path}")
    print(
        f">>> {result['n_closed_this_week']} trade chiusi questa settimana, "
        f"{result['n_trades']} totali nel journal"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report settimanali, mensili e attribution.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("weekly", help="Report ultimi 7 giorni").set_defaults(func=cmd_weekly)
    sub.add_parser("monthly", help="Report ultimi 30 giorni").set_defaults(func=cmd_monthly)
    sub.add_parser(
        "attribution",
        help="Attribution report (Phase 9): decomposition alpha/beta/sector/timing",
    ).set_defaults(func=cmd_attribution)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
