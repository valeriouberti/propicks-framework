#!/usr/bin/env python3
"""Import S&P 500 historical membership da fja05680/sp500 (Fase A.1.3).

Sorgente: https://github.com/fja05680/sp500 — file CSV
``S&P 500 Historical Components & Changes(MM-DD-YYYY).csv`` con snapshot
daily dal 1996. Il file è gratuito (MIT-equivalent) e mantenuto attivamente.

## Cosa fa

1. Scarica il CSV master (~5MB) dalla raw URL GitHub
2. Parse: ogni riga è ``date,"TICKER1,TICKER2,..."``
3. Filtra a granularità mensile (primo trading day del mese) per ridurre
   storage 30x senza perdita signal — l'S&P 500 cambia ~5-10 nomi/anno
4. Normalizza ticker per yfinance: "BRK.B" → "BRK-B" (dot → dash)
5. Bulk insert via ``io.index_membership.bulk_insert_snapshots`` con
   source='fja05680'

Idempotente: re-run aggiorna snapshot esistenti senza duplicare righe.

## Usage

    # DB di default (data/propicks.db)
    python scripts/import_sp500_history.py

    # DB custom (consigliato per test pre-produzione)
    python scripts/import_sp500_history.py --db /tmp/propicks_test.db

    # Da CSV locale (skip download)
    python scripts/import_sp500_history.py --csv /tmp/sp500_hist.csv

    # Granularità diversa (default 'monthly')
    python scripts/import_sp500_history.py --granularity quarterly
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

# Permette esecuzione "python scripts/x.py" da repo root senza pip install
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from propicks.io.db import init_schema  # noqa: E402
from propicks.io.index_membership import (  # noqa: E402
    bulk_insert_snapshots,
    count_membership_rows,
    get_membership_date_range,
    get_snapshot_dates,
)


# URL raw del file CSV master più recente. Aggiornare quando fja05680
# pubblica nuova versione (~ogni 2-3 mesi). Il filename contiene la data
# di update; l'URL hardcoded resta valido finché il file esiste sul repo.
FJA_CSV_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes(01-17-2026).csv"
)

# UA descrittivo per evitare 403 da github raw (raro ma capita).
_HTTP_UA = (
    "PropicksAI/0.1 (+https://github.com/valeriouberti/propicks-ai-framework) "
    "membership-import"
)


def _normalize_ticker(symbol: str) -> str:
    """Normalizza ticker fja05680 → yfinance.

    fja05680 usa il formato Wikipedia "BRK.B" / "BF.B" (dot per share class),
    yfinance richiede dash. Stesso pattern di
    ``market.index_constituents._normalize_yf_ticker`` ma con stripping aggressivo
    per gestire caratteri whitespace/quote del CSV.
    """
    s = symbol.strip().strip('"').upper()
    # Skip token vuoti che capitano quando trailing comma in lista
    if not s:
        return ""
    return s.replace(".", "-")


def _download_csv(url: str, dest: Path) -> None:
    """Scarica il CSV su ``dest`` con UA custom. Idempotente: se ``dest``
    esiste e size > 1MB, riusa senza re-download.
    """
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"[skip-download] CSV già presente: {dest} ({dest.stat().st_size:,} B)")
        return
    print(f"[download] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())
    print(f"[download] saved → {dest} ({dest.stat().st_size:,} B)")


def _parse_csv(csv_path: Path) -> OrderedDict[str, list[str]]:
    """Parse del CSV master in dict {date_iso: [ticker, ...]}.

    Schema atteso: ``date,tickers`` dove ``tickers`` è stringa quoted con
    comma-separated symbols. Il parser CSV stdlib gestisce le quote.

    Returns:
        OrderedDict ordinato per data ASC. Date in formato ISO YYYY-MM-DD.
    """
    import csv

    snapshots: OrderedDict[str, list[str]] = OrderedDict()
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if header[:2] != ["date", "tickers"]:
            raise ValueError(
                f"Schema CSV inatteso: header={header[:2]} (atteso ['date', 'tickers'])"
            )
        for row in reader:
            if len(row) < 2:
                continue
            date_str = row[0].strip()
            tickers_blob = row[1]
            try:
                # Sanity: parsable come date
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            tickers = [
                _normalize_ticker(t) for t in tickers_blob.split(",") if t.strip()
            ]
            tickers = [t for t in tickers if t]  # drop empty post-normalize
            if tickers:
                snapshots[date_str] = tickers
    return snapshots


def _filter_granularity(
    snapshots: OrderedDict[str, list[str]],
    granularity: str,
) -> OrderedDict[str, list[str]]:
    """Filtra a granularità ``monthly`` | ``quarterly`` | ``daily``.

    Strategia: per ogni periodo (year-month per monthly, year-quarter per
    quarterly), tieni il PRIMO snapshot disponibile. ``daily`` = passthrough.
    """
    if granularity == "daily":
        return snapshots
    out: OrderedDict[str, list[str]] = OrderedDict()
    seen_periods: set[str] = set()
    for date_str, tickers in snapshots.items():
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if granularity == "monthly":
            period_key = f"{dt.year}-{dt.month:02d}"
        elif granularity == "quarterly":
            quarter = (dt.month - 1) // 3 + 1
            period_key = f"{dt.year}-Q{quarter}"
        else:
            raise ValueError(f"Granularità non supportata: {granularity}")
        if period_key not in seen_periods:
            seen_periods.add(period_key)
            out[date_str] = tickers
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("##")[0].strip())
    parser.add_argument(
        "--db",
        default=None,
        help="Path SQLite custom. Default: config.DB_FILE (data/propicks.db).",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="Path CSV locale. Se omesso, scarica da GitHub.",
    )
    parser.add_argument(
        "--granularity",
        choices=["monthly", "quarterly", "daily"],
        default="monthly",
        help="Granularità snapshot (default: monthly = ~12 snapshot/anno).",
    )
    parser.add_argument(
        "--cache-dir",
        default="/tmp",
        help="Cartella per CSV scaricato (default: /tmp).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parsa il CSV ma non scrive nel DB.",
    )
    args = parser.parse_args()

    # 1. Source CSV
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"[ERROR] CSV non trovato: {csv_path}", file=sys.stderr)
            return 1
    else:
        cache_dir = Path(args.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        csv_path = cache_dir / "sp500_historical_components_fja05680.csv"
        _download_csv(FJA_CSV_URL, csv_path)

    # 2. Parse
    print(f"[parse] {csv_path}")
    raw_snapshots = _parse_csv(csv_path)
    print(f"[parse] daily snapshots: {len(raw_snapshots)}")

    # 3. Filter granularity
    snapshots = _filter_granularity(raw_snapshots, args.granularity)
    print(
        f"[filter] {args.granularity} snapshots: {len(snapshots)} "
        f"({list(snapshots.keys())[0]} → {list(snapshots.keys())[-1]})"
    )

    # Stat ticker totali (cardinality)
    all_tickers = set()
    for tk_list in snapshots.values():
        all_tickers.update(tk_list)
    total_rows = sum(len(v) for v in snapshots.values())
    print(
        f"[filter] unique tickers ever in S&P 500: {len(all_tickers)} | "
        f"rows to insert: {total_rows:,}"
    )

    if args.dry_run:
        print("[dry-run] DB skip — exit before write")
        return 0

    # 4. DB init + insert
    init_schema(args.db)
    print(f"[db] schema initialized: {args.db or '<default>'}")

    n = bulk_insert_snapshots(
        "sp500", snapshots, source="fja05680", path=args.db
    )
    print(f"[db] inserted/updated rows: {n:,}")

    # 5. Verify
    total = count_membership_rows("sp500", path=args.db)
    n_snapshots = len(get_snapshot_dates("sp500", path=args.db))
    rng = get_membership_date_range("sp500", path=args.db)
    print(
        f"[verify] sp500 rows={total:,} snapshots={n_snapshots} range={rng}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
