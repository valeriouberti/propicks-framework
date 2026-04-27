# Scheduler + Alerts — EOD Automation (Phase 3)

> Automazione end-of-day via **APScheduler** (daemon) + **cron-callable jobs**.
> Ogni job idempotente, UPSERT-based, con audit trail in `scheduler_runs` e
> alert queue in `alerts`. Zero notifiche esterne — la delivery è Phase 4 (Telegram).

---

## 1. I 7 job

| Job | Trigger default | Azione | Alert generati |
|-----|-----------------|--------|----------------|
| `warm_cache` | Mon-Fri 17:45 | Prefetch daily+weekly per portfolio + watchlist + benchmarks | — |
| `record_regime` | Mon-Fri 18:00 | Classify ^GSPC weekly, UPSERT regime_history | `regime_change` |
| `snapshot_portfolio` | Mon-Fri 18:30 | Mark-to-market, exposure per bucket, MTD/YTD, benchmark SPX+FTSEMIB | — |
| `scan_watchlist` | Mon-Fri 18:30 | Score live, populate strategy_runs, READY detection | `watchlist_ready` |
| `trailing_stop_check` | Mon-Fri 18:30 | Suggest trailing stop update su posizioni con trailing_enabled | `trailing_stop_update`, `stale_position` |
| `weekly_attribution_report` | Sat 21:00 | Phase 9: decomposition α/β/sector/timing + Phase 7 gate check | `report_ready` |
| `cleanup_stale_watchlist` | Sun 20:00 | Flag watchlist entries > 60gg + contrarian near-cap warning | `stale_watchlist`, `contra_near_cap` |

---

## 2. Modalità operative

### 2.1 Daemon (sessione always-on)

```bash
propicks-scheduler run        # bloccante, tz=Europe/Rome
```

Run in tmux/nohup per persistenza. SIGINT / SIGTERM → shutdown grazioso.

### 2.2 Cron-callable (desktop-only, no daemon)

```bash
# macOS (launchd) o Linux (crontab -e). Esempio crontab:
# Warm cache pre-EOD EU
45 17 * * 1-5  /path/to/.venv/bin/propicks-scheduler job warm
# Regime + snapshot + scan post EU close
0  18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job regime
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job snapshot
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job scan
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job trailing
# Weekly cleanup Sunday 20:00
0  20 * * 0    /path/to/.venv/bin/propicks-scheduler job cleanup
```

---

## 3. Alert workflow

```bash
propicks-scheduler alerts             # lista pending con badge severity
propicks-scheduler alerts --stats     # aggregate per type/severity
propicks-scheduler alerts --ack 42    # acknowledge singolo
propicks-scheduler alerts --ack-all   # mark all as read
```

**Dedup**: ogni alert ha `dedup_key` (es. `AAPL_ready_2026-04-24`). Se un alert
con stesso key è già pending, il secondo `create_alert` no-op. Questo evita
spam quando warm_cache alle 17:45 e scan_watchlist alle 18:30 producono lo
stesso READY alert. Gli alert già **acknowledged** non bloccano la creazione di
nuovi con stesso key (permette ri-triggerare "ready" settimana dopo).

---

## 4. Audit trail

```bash
propicks-scheduler history             # ultimi 20 run con status/duration
propicks-scheduler history --days 7    # stats aggregate ultimi 7gg
```

Ogni job logga in `scheduler_runs`: `started_at`, `finished_at`, `status`
(success/error/partial), `duration_ms`, `n_items` processati, `error` +
traceback se fallito. Abilita query come:

```sql
-- Job affidabilità ultimo mese
SELECT job_name, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)*1.0/COUNT(*) AS rate
FROM scheduler_runs
WHERE started_at > datetime('now', '-30 days')
GROUP BY job_name;

-- Warm cache più lento del solito? (regression detection)
SELECT DATE(started_at), AVG(duration_ms) AS avg_ms
FROM scheduler_runs WHERE job_name='warm_cache'
GROUP BY 1 ORDER BY 1 DESC LIMIT 14;
```

---

## 5. Benchmark misurati (dati reali)

- `warm_cache`: 6.0s su 11 ticker (portfolio 1 + watchlist 7 + benchmarks 3)
- `scan_watchlist`: 5.5s primo run (miss cache residui), **78ms** seconda run (tutto hit cache Phase 2)
- `record_regime`: 1.2s
- `snapshot_portfolio`: 2.6s (include fetch benchmark SPX + FTSEMIB)

---

## 6. Design choices

- **APScheduler BlockingScheduler** + `CronTrigger` tz-aware (Europe/Rome).
  Nessun JobStore persistente — il daemon è stateless, riavvii non perdono
  cron schedule (sono hardcoded in runner.py).
- **Idempotenza via UPSERT** su `portfolio_snapshots`, `regime_history`.
  Rigirare un job lo stesso giorno aggiorna, non duplica.
- **Idempotenza via `dedup_key`** su `alerts`. Rigirare scan_watchlist 3 volte
  al giorno non spamma.
- **Non auto-apply** modifiche di stop/target: il trader resta il
  decision-maker. I job generano alert informativi; l'applicazione passa da
  `propicks-portfolio manage --apply`.
- **Scheduler non è AI-aware**: i job `scan_watchlist` e `trailing_stop_check`
  NON chiamano Claude. Risparmio tokens: la validazione AI resta on-demand via
  flag `--validate` dei CLI.

---

## 7. Trade-off accettati

- **Manual cron wiring**: nessun installer automatico (launchd plist / systemd
  unit). Il trader copia-incolla le righe crontab dalla doc. Motivo:
  `launchctl load` richiede permessi e formato XML platform-specific —
  out-of-scope per MVP.
- **Nessuna retry**: se un job fallisce, resta errore registrato in
  `scheduler_runs`, niente retry automatico. Il prossimo trigger giornaliero è
  il retry naturale. Per fallimenti consecutivi, guardare `history --days 3`.
- **Nessun lock cross-process**: due daemon in parallelo duplicherebbero
  `scheduler_runs`. Non abbiamo PID lock file. Il trader responsabilizzato
  sull'avvio unico.
