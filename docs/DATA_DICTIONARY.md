# Data Dictionary — SQLite schema

Schema completo di `data/propicks.db`. Source of truth per tutto lo stato
transazionale. Per la filosofia di design (4 aree: stato/storia/riferimenti/
timelines) vedi [STORAGE](STORAGE.md). Per il file SQL canonico vedi
`src/propicks/io/schema.sql`.

> **Idempotenza**: lo schema usa `CREATE TABLE IF NOT EXISTS`. Apri il DB
> per la prima volta e tutto viene creato. Migration aggiuntive sono in
> `db.py::_apply_migrations`.

---

## Index tabelle

| Categoria | Tabelle |
|-----------|---------|
| **Stato** | `portfolio_meta`, `positions` |
| **Storia** | `trades`, `strategy_runs`, `ai_verdicts` |
| **Riferimenti** | `watchlist` |
| **Timelines** | `regime_history`, `portfolio_snapshots` |
| **Market cache** | `market_ohlcv_daily`, `market_ohlcv_weekly`, `market_ticker_meta`, `index_constituents` |
| **Operations** | `daily_budget`, `scheduler_runs`, `alerts` |
| **Versioning** | `schema_version` |

13 tabelle totali.

---

## STATO — modificabile, valore corrente

### `portfolio_meta`
Singleton key-value per metadata portfolio. Evita una tabella a riga unica.

| Colonna | Tipo | Note |
|---------|------|------|
| `key` | TEXT PK | `cash`, `initial_capital`, `last_updated`, ... |
| `value` | TEXT | Serialized as string (deserializza nel layer io/) |
| `updated_at` | TIMESTAMP | Auto |

### `positions`
Posizioni correnti. Una riga per ticker aperto. PK `ticker`.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `ticker` | TEXT PK | Normalizzato yfinance (es. `AAPL`, `ENI.MI`) |
| `strategy` | TEXT | `TechTitans`, `Contrarian`, `SectorETF`, ... |
| `entry_price` | REAL | Prezzo di entry effettivo (con slippage) |
| `entry_date` | DATE | YYYY-MM-DD |
| `shares` | INTEGER | Quantità — sempre intera |
| `stop_loss` | REAL | Livello stop corrente (modificabile) |
| `target` | REAL | Target prima exit (nullable) |
| `highest_price_since_entry` | REAL | Per trailing stop calc |
| `trailing_enabled` | INTEGER | 0/1 — contrarian default 0 (target fisso) |
| `score_claude` | INTEGER | 0-10 conviction al momento dell'entry |
| `score_tech` | INTEGER | 0-100 composite tecnico al momento dell'entry |
| `catalyst` | TEXT | Free-text (es. "earnings beat + AI tailwind") |
| `created_at`, `updated_at` | TIMESTAMP | Auto |

---

## STORIA — append-only

### `trades`
Journal trade. Append-only: `close` aggiorna `exit_*` ma non cancella.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `id` | INTEGER PK | Autoincrement |
| `ticker` | TEXT NOT NULL | |
| `direction` | TEXT NOT NULL | `'long'` \| `'short'` (short non usato) |
| `strategy` | TEXT | Bucket strategia |
| `entry_date`, `entry_price` | DATE, REAL | |
| `shares` | INTEGER | |
| `stop_loss`, `target` | REAL | Snapshot al moment dell'entry |
| `score_claude`, `score_tech` | INTEGER | |
| `catalyst`, `notes` | TEXT | |
| `status` | TEXT NOT NULL DEFAULT `'open'` | `'open'` \| `'closed'` |
| `exit_date`, `exit_price`, `exit_reason` | DATE, REAL, TEXT | NULL fino a chiusura |
| `pnl_pct`, `pnl_per_share`, `duration_days` | REAL, REAL, INTEGER | Calcolati alla chiusura |
| `post_trade_notes` | TEXT | Lessons-learned |
| `created_at` | TIMESTAMP | Auto |

**Indici**: `idx_trades_ticker`, `idx_trades_strategy_entry`, `idx_trades_status`, `idx_trades_exit_date`.

### `strategy_runs`
Ogni esecuzione `propicks-momentum` / `propicks-contra` / `propicks-rotate`
produce una row qui. Abilita "qual era lo score di X il Y?" + attribution.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `id` | INTEGER PK | Autoincrement |
| `run_timestamp` | TIMESTAMP | |
| `strategy` | TEXT NOT NULL | `'Momentum'` \| `'Contrarian'` \| `'SectorETF'` |
| `ticker` | TEXT NOT NULL | |
| `composite_score` | REAL | 0-100 |
| `classification` | TEXT | `'A — AZIONE IMMEDIATA'`, ecc. |
| `sub_scores` | TEXT | JSON blob (trend/momentum/oversold/...) |
| `price`, `rsi`, `atr` | REAL | Snapshot indicators |
| `regime_code` | INTEGER | 1-5 |
| `action_taken` | TEXT | `'ignored'` \| `'watchlist_add'` \| `'validated'` \| `'entry'` |

**Indici**: `idx_runs_strategy_ticker_ts`, `idx_runs_timestamp`.

### `ai_verdicts`
Cache verdict Anthropic. Sostituisce la vecchia folder `data/ai_cache/`.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `id` | INTEGER PK | Autoincrement |
| `run_timestamp` | TIMESTAMP | Per TTL via `WHERE run_timestamp > ?` |
| `strategy` | TEXT | `'momentum'` \| `'contrarian'` \| `'etf_rotation'` |
| `ticker` | TEXT | Per ETF rotation: `rotation:US` se `top_sector_verdict` è "FLAT" |
| `cache_key` | TEXT NOT NULL | Es. `AAPL_v4_2026-04-25` (momentum), `AAPL_contra_v1_2026-04-25` (contra), `rotation_US_3_xlk_xlf_xlv_etf-v2_2026-04-25` (ETF post-CRIT-5: hash dei top-3 ranked) |
| `verdict` | TEXT | `CONFIRM` \| `CAUTION` \| `REJECT` |
| `conviction` | INTEGER | 0-10 |
| `payload` | TEXT NOT NULL | Full JSON serializzato del verdict pydantic |
| `tokens_in`, `tokens_out` | INTEGER | Telemetria costo |
| `cost_usd` | REAL | Stima cost calcolato |

**Indici**: `idx_verdicts_cache_key`, `idx_verdicts_strategy_ticker_ts`.

**TTL**:
- Momentum/Contrarian: 24h (`AI_CACHE_TTL_HOURS`, `CONTRA_AI_CACHE_TTL_HOURS`)
- ETF rotation: 8h (post-CRIT-5)

---

## RIFERIMENTI — stato editabile

### `watchlist`
Incubatrice idee. Dedup per ticker.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `ticker` | TEXT PK | |
| `added_date` | DATE NOT NULL | |
| `target_entry` | REAL | Livello prezzo desiderato (nullable) |
| `note` | TEXT | Free-text |
| `score_at_add` | REAL | Composite al momento dell'add |
| `regime_at_add` | TEXT | Label regime al moment dell'add |
| `classification_at_add` | TEXT | A/B/C/D al moment dell'add |
| `source` | TEXT NOT NULL DEFAULT `'manual'` | `'manual'` \| `'auto_scan'` \| `'auto_scan_contra'` |
| `last_updated` | TIMESTAMP | |

---

## TIMELINES — snapshot giornalieri

### `regime_history`
Regime macro weekly su ^GSPC, snapshot daily per attribution.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `date` | DATE PK | YYYY-MM-DD |
| `regime_code` | INTEGER NOT NULL | 1-5 |
| `regime_label` | TEXT NOT NULL | STRONG_BULL/BULL/NEUTRAL/BEAR/STRONG_BEAR |
| `adx`, `rsi`, `macd_hist` | REAL | Snapshot weekly |
| `ema_fast`, `ema_slow`, `ema_200d` | REAL | Snapshot weekly |
| `recorded_at` | TIMESTAMP | |

### `portfolio_snapshots`
Equity curve + breakdown per strategia. 1 riga per giorno.

| Colonna | Tipo | Semantica |
|---------|------|-----------|
| `date` | DATE PK | |
| `cash` | REAL NOT NULL | Liquidità disponibile |
| `invested_value` | REAL NOT NULL | Σ posizioni @ market |
| `total_value` | REAL NOT NULL | cash + invested |
| `n_positions` | INTEGER NOT NULL | Posizioni aperte EOD |
| `contra_exposure_pct` | REAL | % invested in contrarian |
| `momentum_exposure_pct` | REAL | % invested in momentum |
| `etf_exposure_pct` | REAL | % invested in sector ETF |
| `mtd_return`, `ytd_return` | REAL | Cumulative |
| `benchmark_spx`, `benchmark_ftsemib` | REAL | Per attribution α/β |
| `recorded_at` | TIMESTAMP | |

---

## MARKET CACHE (Phase 2)

### `market_ohlcv_daily`
Bar OHLCV daily. PK `(ticker, date)`. TTL applicato via `fetched_at`.

| Colonna | Tipo |
|---------|------|
| `ticker` | TEXT NOT NULL |
| `date` | DATE NOT NULL |
| `open`, `high`, `low`, `close`, `adj_close` | REAL |
| `volume` | INTEGER |
| `fetched_at` | TIMESTAMP |

**Index**: `idx_ohlcv_daily_ticker`. **TTL**: 8h (`MARKET_CACHE_TTL_DAILY_HOURS`).

### `market_ohlcv_weekly`
Bar weekly. PK `(ticker, week_start)`. Fetched separatamente da yfinance per
preservare l'allineamento del calendario weekly (week ending Fri).

Stessa struttura di `market_ohlcv_daily` ma con `week_start` invece di `date`.
TTL 7gg.

### `market_ticker_meta`
Sector GICS, beta, name, earnings date. PK `ticker`. TTL 7gg.

| Colonna | Tipo | Note |
|---------|------|------|
| `ticker` | TEXT PK | |
| `sector` | TEXT | GICS Yahoo-style |
| `beta` | REAL | vs SPX 5y monthly |
| `name` | TEXT | Fallback display name |
| `fetched_at` | TIMESTAMP | TTL meta 7gg |
| `next_earnings_date` | DATE | Phase 8 |
| `earnings_fetched_at` | TIMESTAMP | TTL earnings 7gg separato |

**Index**: `idx_meta_next_earnings` (creato da migration).

### `index_constituents`
Membri di un index. PK `(index_name, ticker)` per supportare più indici.

| Colonna | Tipo |
|---------|------|
| `index_name` | TEXT NOT NULL — `'sp500'`, `'nasdaq100'`, `'ftsemib'`, `'stoxx600'` |
| `ticker` | TEXT NOT NULL — già normalizzato yfinance |
| `company_name`, `sector` | TEXT |
| `added_date` | DATE |
| `fetched_at` | TIMESTAMP |

**Index**: `idx_constituents_index`. **TTL**: 7gg.

---

## OPERATIONS

### `daily_budget`
Budget AI giornaliero. 1 riga per giorno (date PK).

| Colonna | Tipo |
|---------|------|
| `date` | DATE PK |
| `calls` | INTEGER NOT NULL DEFAULT 0 |
| `est_cost_usd` | REAL NOT NULL DEFAULT 0.0 |
| `updated_at` | TIMESTAMP |

Reset implicito al cambio data (nuova row). Verifica caps in `ai/budget.py`.

### `scheduler_runs`
Audit trail job scheduler. Per stats SLA + debugging.

| Colonna | Tipo |
|---------|------|
| `id` | INTEGER PK |
| `job_name` | TEXT NOT NULL |
| `started_at`, `finished_at` | TIMESTAMP |
| `status` | TEXT — `'success'` \| `'error'` \| `'partial'` |
| `duration_ms` | INTEGER |
| `n_items` | INTEGER — count item processati |
| `error`, `notes` | TEXT |

**Index**: `idx_scheduler_runs_job`.

### `alerts`
Coda alert. Consumata da CLI + Telegram bot.

| Colonna | Tipo |
|---------|------|
| `id` | INTEGER PK |
| `created_at` | TIMESTAMP |
| `type` | TEXT NOT NULL — `'watchlist_ready'`, `'regime_change'`, `'stale_position'`, `'trailing_stop_update'`, `'stale_watchlist'`, `'job_failed'` |
| `severity` | TEXT NOT NULL — `'info'` \| `'warning'` \| `'critical'` |
| `ticker` | TEXT — nullable |
| `message` | TEXT NOT NULL |
| `metadata` | TEXT — JSON blob |
| `dedup_key` | TEXT — es. `AAPL_ready_2026-04-25` per evitare duplicati same-day |
| `acknowledged` | INTEGER DEFAULT 0 |
| `acknowledged_at` | TIMESTAMP |
| `delivered` | INTEGER DEFAULT 0 — Phase 4 (Telegram) |
| `delivered_at` | TIMESTAMP |
| `delivery_error` | TEXT |

**Indici**: `idx_alerts_pending`, `idx_alerts_dedup`, `idx_alerts_undelivered` (creato da migration).

---

## Versioning

### `schema_version`
Tracking migration. Pre-popolata `version=1`. Future migration aggiungono row.

| Colonna | Tipo |
|---------|------|
| `version` | INTEGER PK |
| `applied_at` | TIMESTAMP |
| `description` | TEXT |

---

## Query frequenti

### Posizioni aperte ordinate per P&L

```sql
SELECT p.ticker, p.strategy, p.entry_price, p.shares,
       (m.close - p.entry_price) * p.shares AS pnl_unrealized
FROM positions p
JOIN market_ohlcv_daily m ON m.ticker = p.ticker
WHERE m.date = (SELECT MAX(date) FROM market_ohlcv_daily WHERE ticker = p.ticker)
ORDER BY pnl_unrealized DESC;
```

### Hit rate per strategy ultimi 90gg

```sql
SELECT strategy,
       COUNT(*) AS total,
       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
       ROUND(100.0 * SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS hit_rate_pct,
       ROUND(AVG(pnl_pct), 3) AS avg_pnl_pct
FROM trades
WHERE status = 'closed' AND exit_date >= date('now', '-90 days')
GROUP BY strategy;
```

### Cache hit rate ultimi 7gg

```sql
SELECT
  COUNT(DISTINCT cache_key) AS unique_keys,
  COUNT(*) AS total_writes,
  ROUND(100.0 * (1 - COUNT(DISTINCT cache_key) * 1.0 / COUNT(*)), 1) AS hit_rate_proxy_pct
FROM ai_verdicts
WHERE run_timestamp >= datetime('now', '-7 days');
```

### Alert pending per severity

```sql
SELECT severity, type, COUNT(*) AS n
FROM alerts
WHERE acknowledged = 0
GROUP BY severity, type
ORDER BY severity DESC, n DESC;
```

### Regime history ultimo mese

```sql
SELECT date, regime_label, adx, rsi
FROM regime_history
WHERE date >= date('now', '-30 days')
ORDER BY date DESC;
```

---

## Operations comuni

### Backup atomico

```bash
sqlite3 data/propicks.db ".backup data/snap-$(date +%Y%m%d).db"
```

### Vacuum + integrity check

```bash
sqlite3 data/propicks.db "VACUUM;"
sqlite3 data/propicks.db "PRAGMA integrity_check;"
```

### Pulizia cache ai_verdicts > 30gg

```bash
sqlite3 data/propicks.db \
  "DELETE FROM ai_verdicts WHERE run_timestamp < datetime('now', '-30 days');"
```

### Reset solo strategy_runs (telemetria)

```bash
sqlite3 data/propicks.db "DELETE FROM strategy_runs;"
```

### Export trades a CSV

```bash
sqlite3 -header -csv data/propicks.db \
  "SELECT * FROM trades WHERE status='closed' ORDER BY exit_date" \
  > trades_closed.csv
```

---

## Convenzioni

- **Date**: sempre ISO 8601 (`YYYY-MM-DD`).
- **Timestamp**: ISO 8601 with seconds (`YYYY-MM-DD HH:MM:SS`). SQLite default.
- **Percentuali in storage**: come float decimal (es. `0.08` per 8%, NON 8.0).
- **NULLABLE**: la maggior parte dei campi non `NOT NULL` accetta NULL come "non noto" (vs valore default).
- **Boolean**: come INTEGER 0/1 (SQLite non ha BOOLEAN nativo).
- **JSON blob**: serializzato in colonne TEXT (es. `sub_scores`, `metadata`, `payload`). Decodifica nel layer io/.

Per modificare lo schema (aggiungere tabella o colonna), edit
`src/propicks/io/schema.sql` E aggiungi una migration in
`db.py::_apply_migrations`. Mai modificare il DB esistente direttamente —
serve una migration idempotente.
