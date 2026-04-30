"""Job functions dello scheduler — 6 tasks idempotenti daily/weekly.

Ogni job:
- È wrappato da ``@run_job(name)`` → audit trail in ``scheduler_runs``
- Può sollevare: il wrapper logga l'errore, il caller decide cosa fare
- Ritorna un dict ``{n_items, notes}`` → loggato in scheduler_runs
- È **idempotente per giorno**: rigirato lo stesso giorno produce stesso
  output (UPSERT sui target, dedup sui alert)

I job leggono lo stato via le public API (``load_portfolio``, ``load_journal``,
``load_watchlist``) che sotto ora sono SQLite (Phase 1), beneficiano del
cache market data (Phase 2) ad ogni fetch.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from propicks.config import ETF_BENCHMARK
from propicks.domain.regime import classify_regime
from propicks.domain.scoring import analyze_ticker
from propicks.domain.sizing import (
    contrarian_aggregate_exposure,
    is_contrarian_position,
)
from propicks.domain.trade_mgmt import suggest_stop_update
from propicks.io.db import connect, transaction
from propicks.io.portfolio_store import load_portfolio
from propicks.io.watchlist_store import is_stale, load_watchlist
from propicks.market.yfinance_client import (
    DataUnavailable,
    download_history,
    download_weekly_history,
    get_current_prices,
)
from propicks.scheduler.alerts import create_alert
from propicks.scheduler.history import run_job


# ---------------------------------------------------------------------------
# Helper: record strategy run in DB
# ---------------------------------------------------------------------------
def _record_strategy_run(
    strategy: str,
    ticker: str,
    analysis: dict,
    action_taken: str = "scheduled_scan",
) -> None:
    """Insert a row in strategy_runs (populated forward from Phase 3).

    Abilita attribution: "qual era lo score X il giorno Y quando l'ho
    aggiunto in watchlist?" via query storica.
    """
    sub_scores = analysis.get("scores", {})
    regime = analysis.get("regime") or {}

    with transaction() as conn:
        conn.execute(
            """INSERT INTO strategy_runs (
                strategy, ticker, composite_score, classification,
                sub_scores, price, rsi, atr, regime_code, action_taken
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy,
                ticker.upper(),
                analysis.get("score_composite"),
                analysis.get("classification"),
                json.dumps(sub_scores, ensure_ascii=False),
                analysis.get("price"),
                analysis.get("rsi"),
                analysis.get("atr"),
                regime.get("regime_code"),
                action_taken,
            ),
        )


# ---------------------------------------------------------------------------
# 1. Portfolio snapshot (daily)
# ---------------------------------------------------------------------------
@run_job("snapshot_portfolio")
def snapshot_portfolio(snapshot_date: str | None = None) -> dict:
    """Scrive una riga in ``portfolio_snapshots`` con stato corrente del portfolio.

    Computations:
    - total_value mark-to-market = cash + sum(shares * current_price)
    - exposure per bucket: contrarian, momentum (tutto il resto)
    - mtd / ytd return se esistono snapshot precedenti del 1° mese / 1° anno
    - benchmark SPX + FTSEMIB (close del giorno via get_current_prices)

    Idempotente: UPSERT by ``date``. Esegue oggi anche se già eseguito.
    """
    snapshot_date = snapshot_date or date.today().isoformat()
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)

    # Mark-to-market via current prices
    tickers = list(positions.keys())
    prices = get_current_prices(tickers) if tickers else {}

    invested_value = 0.0
    contra_invested = 0.0
    momentum_invested = 0.0
    etf_invested = 0.0

    for ticker, pos in positions.items():
        price = prices.get(ticker) or pos.get("entry_price")
        shares = float(pos.get("shares") or 0)
        value = shares * float(price)
        invested_value += value
        strategy_tag = (pos.get("strategy") or "").lower()
        if is_contrarian_position(pos):
            contra_invested += value
        elif "etf" in strategy_tag or "rotation" in strategy_tag:
            etf_invested += value
        else:
            momentum_invested += value

    total_value = cash + invested_value

    def _pct(val: float) -> float | None:
        return round(val / total_value, 4) if total_value > 0 else None

    # Benchmark close
    benchmarks = get_current_prices([ETF_BENCHMARK, "FTSEMIB.MI"])
    spx_close = benchmarks.get(ETF_BENCHMARK)
    ftsemib_close = benchmarks.get("FTSEMIB.MI")

    # MTD / YTD returns da snapshot precedenti
    today_dt = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    first_of_month = today_dt.replace(day=1).isoformat()
    first_of_year = today_dt.replace(month=1, day=1).isoformat()

    conn = connect()
    try:
        mtd_row = conn.execute(
            """SELECT total_value FROM portfolio_snapshots
               WHERE date >= ? AND date < ?
               ORDER BY date ASC LIMIT 1""",
            (first_of_month, snapshot_date),
        ).fetchone()
        ytd_row = conn.execute(
            """SELECT total_value FROM portfolio_snapshots
               WHERE date >= ? AND date < ?
               ORDER BY date ASC LIMIT 1""",
            (first_of_year, snapshot_date),
        ).fetchone()
    finally:
        conn.close()

    mtd_return = None
    ytd_return = None
    if mtd_row and mtd_row["total_value"]:
        mtd_return = round((total_value / float(mtd_row["total_value"])) - 1, 4)
    if ytd_row and ytd_row["total_value"]:
        ytd_return = round((total_value / float(ytd_row["total_value"])) - 1, 4)

    with transaction() as conn:
        conn.execute(
            """INSERT INTO portfolio_snapshots (
                date, cash, invested_value, total_value, n_positions,
                contra_exposure_pct, momentum_exposure_pct, etf_exposure_pct,
                mtd_return, ytd_return, benchmark_spx, benchmark_ftsemib
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                cash = excluded.cash,
                invested_value = excluded.invested_value,
                total_value = excluded.total_value,
                n_positions = excluded.n_positions,
                contra_exposure_pct = excluded.contra_exposure_pct,
                momentum_exposure_pct = excluded.momentum_exposure_pct,
                etf_exposure_pct = excluded.etf_exposure_pct,
                mtd_return = excluded.mtd_return,
                ytd_return = excluded.ytd_return,
                benchmark_spx = excluded.benchmark_spx,
                benchmark_ftsemib = excluded.benchmark_ftsemib""",
            (
                snapshot_date,
                round(cash, 2),
                round(invested_value, 2),
                round(total_value, 2),
                len(positions),
                _pct(contra_invested),
                _pct(momentum_invested),
                _pct(etf_invested),
                mtd_return,
                ytd_return,
                round(spx_close, 2) if spx_close else None,
                round(ftsemib_close, 2) if ftsemib_close else None,
            ),
        )

    notes = f"total={total_value:.2f} cash_pct={(cash/total_value)*100:.1f}%"
    if mtd_return is not None:
        notes += f" mtd={mtd_return*100:+.2f}%"
    return {"n_items": len(positions), "notes": notes}


# ---------------------------------------------------------------------------
# 2. Regime reclass (daily)
# ---------------------------------------------------------------------------
@run_job("record_regime")
def record_regime(record_date: str | None = None) -> dict:
    """Classifica regime macro weekly su ^GSPC, persiste, genera alert su change.

    Idempotente: UPSERT by date. Confronto con la riga di ieri per detection.
    """
    record_date = record_date or date.today().isoformat()

    weekly = download_weekly_history(ETF_BENCHMARK)
    regime = classify_regime(weekly)
    if regime is None:
        raise DataUnavailable(ETF_BENCHMARK, "regime classification ritornato None")

    # Previous regime
    conn = connect()
    try:
        prev_row = conn.execute(
            """SELECT regime_code, regime_label FROM regime_history
               WHERE date < ?
               ORDER BY date DESC LIMIT 1""",
            (record_date,),
        ).fetchone()
    finally:
        conn.close()

    with transaction() as conn:
        conn.execute(
            """INSERT INTO regime_history (
                date, regime_code, regime_label, adx, rsi, macd_hist,
                ema_fast, ema_slow, ema_200d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                regime_code = excluded.regime_code,
                regime_label = excluded.regime_label,
                adx = excluded.adx,
                rsi = excluded.rsi,
                macd_hist = excluded.macd_hist,
                ema_fast = excluded.ema_fast,
                ema_slow = excluded.ema_slow,
                ema_200d = excluded.ema_200d""",
            (
                record_date,
                regime["regime_code"],
                regime["regime"],
                regime.get("adx"),
                regime.get("rsi"),
                regime.get("macd_hist"),
                regime.get("ema_fast"),
                regime.get("ema_slow"),
                regime.get("ema_200d"),
            ),
        )

    # Alert su regime change
    if prev_row and prev_row["regime_code"] != regime["regime_code"]:
        severity = "critical" if abs(prev_row["regime_code"] - regime["regime_code"]) >= 2 else "warning"
        create_alert(
            alert_type="regime_change",
            severity=severity,
            message=(
                f"Regime changed: {prev_row['regime_label']} "
                f"({prev_row['regime_code']}/5) → {regime['regime']} "
                f"({regime['regime_code']}/5)"
            ),
            metadata={
                "from": prev_row["regime_label"],
                "from_code": prev_row["regime_code"],
                "to": regime["regime"],
                "to_code": regime["regime_code"],
                "date": record_date,
            },
            dedup_key=f"regime_change_{record_date}",
        )

    return {
        "n_items": 1,
        "notes": f"{regime['regime']} ({regime['regime_code']}/5) adx={regime.get('adx')}",
    }


# ---------------------------------------------------------------------------
# 3. Warm cache (daily, pre-scan)
# ---------------------------------------------------------------------------
@run_job("warm_cache")
def warm_cache() -> dict:
    """Prefetch daily + weekly per i ticker "attivi" (positions + watchlist).

    Scopo: il prossimo scan EOD trova cache fresh, scan = 0.4s invece di 3s.
    Non fallisce sul singolo ticker degenere — logga e prosegue.
    """
    portfolio = load_portfolio()
    watchlist = load_watchlist()

    tickers = set(portfolio.get("positions", {}).keys())
    tickers.update(watchlist.get("tickers", {}).keys())

    # Aggiungi benchmarks comuni per ETF rotation e regime
    tickers.update([ETF_BENCHMARK, "FTSEMIB.MI", "URTH"])

    ok = 0
    failed: list[str] = []
    for t in sorted(tickers):
        try:
            download_history(t)
            download_weekly_history(t)
            ok += 1
        except DataUnavailable as exc:
            failed.append(f"{t}:{exc.message[:40]}")

    notes = f"ok={ok}/{len(tickers)}"
    if failed:
        notes += f" failed={','.join(failed[:3])}"
    return {"n_items": ok, "notes": notes}


# ---------------------------------------------------------------------------
# 4. Scan watchlist (daily) — score live + READY detection
# ---------------------------------------------------------------------------
@run_job("scan_watchlist")
def scan_watchlist(ready_distance_pct: float = 0.02) -> dict:
    """Scan tutti i ticker in watchlist, persiste strategy_runs, alert READY.

    READY = (score_composite >= 60) AND
            (|current_price - target_entry| / target_entry <= ready_distance_pct)

    Per ogni ticker con target_entry settato:
    - Esegue analyze_ticker (momentum) — lo scoring "canonico" della watchlist
    - Scrive riga strategy_runs con action_taken='watchlist_scan'
    - Se READY: crea alert dedup_key=f"{ticker}_ready_{date}"

    Rate-limit friendly: beneficia della cache Phase 2, zero hit yfinance
    se cache fresh (TTL 8h).
    """
    watchlist = load_watchlist()
    entries = watchlist.get("tickers", {})
    today = date.today().isoformat()

    n_scanned = 0
    n_ready = 0
    n_skipped = 0
    errors: list[str] = []

    for ticker, entry in entries.items():
        try:
            analysis = analyze_ticker(ticker)
            if analysis is None:
                n_skipped += 1
                continue
            _record_strategy_run(
                strategy="momentum",
                ticker=ticker,
                analysis=analysis,
                action_taken="watchlist_scan",
            )
            n_scanned += 1

            target = entry.get("target_entry")
            score = analysis.get("score_composite") or 0
            price = analysis.get("price")

            # READY check richiede target + score + price
            if target and price and score >= 60:
                dist = abs(price - target) / target
                if dist <= ready_distance_pct:
                    n_ready += 1
                    create_alert(
                        alert_type="watchlist_ready",
                        severity="info",
                        ticker=ticker,
                        message=(
                            f"{ticker} READY — price {price:.2f} vs target "
                            f"{target:.2f} ({dist*100:.2f}% off), score {score:.1f}"
                        ),
                        metadata={
                            "price": price,
                            "target": target,
                            "distance_pct": round(dist, 4),
                            "score": score,
                            "classification": analysis.get("classification"),
                        },
                        dedup_key=f"{ticker}_ready_{today}",
                    )
        except Exception as exc:
            errors.append(f"{ticker}:{type(exc).__name__}")

    notes = f"scanned={n_scanned} ready={n_ready} skipped={n_skipped}"
    if errors:
        notes += f" errors={len(errors)}"
    return {"n_items": n_scanned, "notes": notes}


# ---------------------------------------------------------------------------
# 5. Trailing stop check (daily)
# ---------------------------------------------------------------------------
@run_job("trailing_stop_check")
def trailing_stop_check() -> dict:
    """Per posizioni con trailing_enabled, suggerisce update dello stop.

    NON applica l'update automaticamente (principle: il trader decide).
    Genera alert quando lo suggested_stop differisce dal current di almeno 1%.
    """
    from propicks.config import ATR_PERIOD
    from propicks.domain.indicators import compute_atr

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})

    n_checked = 0
    n_suggested = 0
    errors: list[str] = []

    for ticker, pos in positions.items():
        if not pos.get("trailing_enabled"):
            continue
        n_checked += 1
        try:
            hist = download_history(ticker)
            current_price = float(hist["Close"].iloc[-1])
            current_atr = float(
                compute_atr(hist["High"], hist["Low"], hist["Close"], ATR_PERIOD).iloc[-1]
            )
            suggestion = suggest_stop_update(
                position=pos,
                current_price=current_price,
                current_atr=current_atr,
            )
            new_stop = suggestion.get("new_stop")
            current_stop = pos.get("stop_loss")
            time_stop_triggered = suggestion.get("time_stop_triggered", False)

            if time_stop_triggered:
                create_alert(
                    alert_type="stale_position",
                    severity="warning",
                    ticker=ticker,
                    message=(
                        f"{ticker} time-stop triggered: trade flat da molti giorni. "
                        f"Considera chiusura."
                    ),
                    metadata={
                        "price": current_price,
                        "entry_price": pos.get("entry_price"),
                        "entry_date": pos.get("entry_date"),
                        "rationale": suggestion.get("rationale"),
                    },
                    dedup_key=f"{ticker}_timestop_{date.today().isoformat()}",
                )

            if (
                new_stop is not None
                and current_stop is not None
                and abs(new_stop - current_stop) / current_stop > 0.01  # >1% diff
            ):
                n_suggested += 1
                create_alert(
                    alert_type="trailing_stop_update",
                    severity="info",
                    ticker=ticker,
                    message=(
                        f"{ticker} trailing update suggested: "
                        f"{current_stop:.2f} → {new_stop:.2f} "
                        f"(highest {suggestion.get('highest_price', current_price):.2f})"
                    ),
                    metadata={
                        "current_stop": current_stop,
                        "suggested_stop": new_stop,
                        "highest_price": suggestion.get("highest_price"),
                        "rationale": suggestion.get("rationale"),
                    },
                    dedup_key=f"{ticker}_trail_{date.today().isoformat()}",
                )
        except Exception as exc:
            errors.append(f"{ticker}:{type(exc).__name__}")

    notes = f"checked={n_checked} suggested={n_suggested}"
    if errors:
        notes += f" errors={len(errors)}"
    return {"n_items": n_checked, "notes": notes}


# ---------------------------------------------------------------------------
# 7. Earnings calendar check (Phase 8)
# ---------------------------------------------------------------------------
@run_job("check_earnings_calendar")
def check_earnings_calendar(days_threshold: int = 5) -> dict:
    """Daily: fetch earnings dates per portfolio + watchlist, alert se entro 5gg.

    - Per ogni ticker in portfolio + watchlist: refresh earnings_date (cache TTL 7gg)
    - Alert 'earnings_upcoming' per ogni ticker con earnings entro threshold
    - Dedup: una alert per ticker per settimana (stessa week ISO → no duplicati)
    """
    from propicks.config import EARNINGS_HARD_GATE_DAYS
    from propicks.domain.calendar import earnings_gate_check
    from propicks.market.yfinance_client import get_next_earnings_date

    portfolio = load_portfolio()
    watchlist = load_watchlist()

    tickers = set(portfolio.get("positions", {}).keys())
    tickers.update(watchlist.get("tickers", {}).keys())

    if not tickers:
        return {"n_items": 0, "notes": "no tickers"}

    threshold = days_threshold or EARNINGS_HARD_GATE_DAYS
    n_upcoming = 0
    n_errors = 0
    iso_year, iso_week, _ = date.today().isocalendar()
    week_tag = f"{iso_year}-W{iso_week:02d}"

    for ticker in sorted(tickers):
        try:
            earnings_date = get_next_earnings_date(ticker)
        except Exception:
            n_errors += 1
            continue
        check = earnings_gate_check(ticker, earnings_date, threshold)
        if not check["blocked"]:
            continue
        n_upcoming += 1
        # Dedup per ticker per settimana (evita spam giornaliero)
        dedup = f"earnings_upcoming_{ticker}_{week_tag}"
        is_critical = check["days_to_earnings"] is not None and check["days_to_earnings"] <= 2
        create_alert(
            alert_type="earnings_upcoming",
            severity="critical" if is_critical else "warning",
            ticker=ticker,
            message=(
                f"{ticker}: earnings in {check['days_to_earnings']}gg "
                f"({earnings_date})"
            ),
            metadata={
                "ticker": ticker,
                "earnings_date": earnings_date,
                "days_to_earnings": check["days_to_earnings"],
                "in_portfolio": ticker in portfolio.get("positions", {}),
                "in_watchlist": ticker in watchlist.get("tickers", {}),
            },
            dedup_key=dedup,
        )

    notes = f"checked={len(tickers)} upcoming={n_upcoming}"
    if n_errors:
        notes += f" errors={n_errors}"
    return {"n_items": n_upcoming, "notes": notes}


# ---------------------------------------------------------------------------
# 8. Weekly attribution report (Phase 9)
# ---------------------------------------------------------------------------
@run_job("weekly_attribution_report")
def weekly_attribution_report_job() -> dict:
    """Genera il weekly attribution report e crea alert con path per /report bot.

    Scope: trade chiusi + portfolio_snapshots + regime_history + OHLCV cache.
    Output: reports/attribution_YYYY-WW.md + alert 'report_ready'.
    """
    from propicks.reports.attribution_report import weekly_attribution_report

    result = weekly_attribution_report()
    path = result["path"]

    create_alert(
        alert_type="report_ready",
        severity="info",
        message=(
            f"📊 Weekly attribution report pronto ({result['iso_week']}) — "
            f"{result['n_closed_this_week']} trade chiusi questa settimana, "
            f"{result['n_trades']} totali"
        ),
        metadata={
            "path": path,
            "iso_week": result["iso_week"],
            "n_trades": result["n_trades"],
            "n_closed_this_week": result["n_closed_this_week"],
        },
        dedup_key=f"report_ready_{result['iso_week']}",
    )

    return {
        "n_items": result["n_trades"],
        "notes": f"saved={path} week={result['iso_week']}",
    }


# ---------------------------------------------------------------------------
# 8b. Decay monitor (weekly, Fase D.4 SIGNAL_ROADMAP)
# ---------------------------------------------------------------------------
@run_job("decay_monitor_check")
def decay_monitor_check(
    *, expected_sharpe_per_trade: float = 0.20,
    rolling_window: int = 30,
) -> dict:
    """Run decay detection on closed trades per strategy.

    Per ogni strategia (momentum/contrarian/etf): query closed trades
    ultimi 365 giorni, esegui ``decay_alert_summary``, crea alert se
    decision è ALERT_DECAY o WARNING. Audit trail via @run_job decorator.

    Args:
        expected_sharpe_per_trade: Sharpe atteso da baseline_v2 (default 0.20).
        rolling_window: window per rolling Sharpe (default 30 trade).
    """
    import json as _json

    from propicks.domain.decay_monitor import decay_alert_summary
    from propicks.io.db import connect, transaction

    strategies = ["momentum", "contrarian", "etf"]
    alerts_created = 0
    summaries: list[dict] = []

    conn = connect()
    try:
        for strat in strategies:
            rows = conn.execute(
                """SELECT pnl_pct FROM trades
                   WHERE status='closed' AND pnl_pct IS NOT NULL
                     AND strategy = ?
                     AND exit_date >= date('now', '-365 days')
                   ORDER BY exit_date ASC""",
                (strat,),
            ).fetchall()
            returns = [r["pnl_pct"] / 100.0 for r in rows]
            if len(returns) < 5:
                summaries.append({
                    "strategy": strat,
                    "n_trades": len(returns),
                    "decision": "INSUFFICIENT_DATA",
                })
                continue

            summary = decay_alert_summary(
                returns,
                expected_sharpe_per_trade=expected_sharpe_per_trade,
                rolling_window=rolling_window,
            )
            summary["strategy"] = strat
            summaries.append(summary)

            decision = summary["decision"]

            # Audit trail: persist decay_runs (P3.12 SIGNAL_ROADMAP)
            with transaction() as conn_audit:
                conn_audit.execute(
                    """INSERT INTO decay_runs (
                        strategy, decision, n_trades, rolling_sharpe,
                        cusum_alarm_index, sprt_decision, expected_sharpe, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        strat,
                        decision,
                        summary.get("n_obs"),
                        summary.get("rolling_sharpe_latest"),
                        summary.get("cusum_alarm_index"),
                        summary.get("sprt_decision"),
                        expected_sharpe_per_trade,
                        _json.dumps(summary, default=str),
                    ),
                )
            if decision in ("ALERT_DECAY", "WARNING"):
                severity = "critical" if decision == "ALERT_DECAY" else "warning"
                emoji = "🔴" if decision == "ALERT_DECAY" else "🟡"
                msg = (
                    f"{emoji} Decay {decision} su strategia **{strat}** — "
                    f"n_trades={summary['n_obs']}, "
                    f"rolling_SR={summary.get('rolling_sharpe_latest')}, "
                    f"CUSUM_alarm={summary.get('cusum_alarm_index')}, "
                    f"SPRT={summary.get('sprt_decision')}"
                )
                create_alert(
                    alert_type="decay_alert",
                    severity=severity,
                    message=msg,
                    metadata={
                        "strategy": strat,
                        "decision": decision,
                        "n_trades": summary["n_obs"],
                        "rolling_sharpe": summary.get("rolling_sharpe_latest"),
                        "cusum_alarm": summary.get("cusum_alarm_index"),
                        "sprt_decision": summary.get("sprt_decision"),
                        "expected_sharpe": expected_sharpe_per_trade,
                    },
                    dedup_key=f"decay_{strat}_{decision}",
                )
                alerts_created += 1
    finally:
        conn.close()

    return {
        "n_items": len(summaries),
        "notes": (
            f"decay check {len(strategies)} strategies, {alerts_created} alerts. "
            f"Decisions: "
            + ", ".join(f"{s['strategy']}={s['decision']}" for s in summaries)
        ),
    }


# ---------------------------------------------------------------------------
# 8. Cleanup stale watchlist (weekly)
# ---------------------------------------------------------------------------
@run_job("cleanup_stale_watchlist")
def cleanup_stale_watchlist(days: int = 60) -> dict:
    """Flag entries in watchlist più vecchie di N giorni.

    NON cancella automaticamente (principle: il trader decide). Genera 1 alert
    aggregato con la lista degli stale per permettere bulk cleanup manuale.
    """
    watchlist = load_watchlist()
    entries = watchlist.get("tickers", {})

    stale: list[str] = []
    for ticker, entry in entries.items():
        if is_stale(entry, days=days):
            stale.append(ticker)

    if stale:
        create_alert(
            alert_type="stale_watchlist",
            severity="info",
            message=(
                f"{len(stale)} entries in watchlist > {days} giorni: "
                f"{', '.join(stale[:10])}"
                + ("..." if len(stale) > 10 else "")
            ),
            metadata={"tickers": stale, "days_threshold": days},
            dedup_key=f"stale_watchlist_{date.today().isoformat()}",
        )

    # Anche: alert per esposizione contrarian sopra soglia (75% del cap)
    portfolio = load_portfolio()
    contra_expo = contrarian_aggregate_exposure(portfolio)
    from propicks.config import CONTRA_MAX_AGGREGATE_EXPOSURE_PCT
    if contra_expo >= CONTRA_MAX_AGGREGATE_EXPOSURE_PCT * 0.75:
        create_alert(
            alert_type="contra_near_cap",
            severity="warning",
            message=(
                f"Bucket contrarian al {contra_expo*100:.1f}% "
                f"(cap {CONTRA_MAX_AGGREGATE_EXPOSURE_PCT*100:.0f}%)"
            ),
            metadata={"exposure": contra_expo, "cap": CONTRA_MAX_AGGREGATE_EXPOSURE_PCT},
            dedup_key=f"contra_cap_{date.today().isoformat()}",
        )

    return {"n_items": len(stale), "notes": f"stale={len(stale)} contra_expo={contra_expo*100:.1f}%"}
