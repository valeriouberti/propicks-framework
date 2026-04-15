"""CLI per la generazione dei report settimanali e mensili.

I report vengono sia stampati su terminale sia salvati in reports/
con filename datato.

Esempi:
    propicks-report weekly
    propicks-report monthly
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Report settimanali e mensili.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("weekly", help="Report ultimi 7 giorni").set_defaults(func=cmd_weekly)
    sub.add_parser("monthly", help="Report ultimi 30 giorni").set_defaults(func=cmd_monthly)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
