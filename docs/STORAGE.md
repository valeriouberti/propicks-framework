# Storage — SQLite + Market Data Cache

> **Phase 1**: SQLite source of truth per tutto lo stato transazionale (positions,
> trades, watchlist, AI verdicts, etc.).
>
> **Phase 2**: Cache read-through con TTL per yfinance — speedup 7-10× su scan
> ripetuti, offline resilience.

---

## 1. SQLite — source of truth (Phase 1)

DB file: **`data/propicks.db`**. Zero file JSON nel runtime. Schema DDL in
`src/propicks/io/schema.sql`, **9 tabelle**:

| Tabella | Scopo | Ruolo |
|---------|-------|-------|
| `positions` | Stato posizioni aperte (PK ticker) | CRUD via `portfolio_store` |
| `portfolio_meta` | KV singleton (cash, initial_capital, last_updated) | Scritto da `portfolio_store` e `watchlist_store` |
| `trades` | Journal append-only (PK auto, mai deleted) | CRUD via `journal_store` |
| `watchlist` | Incubatrice idee (PK ticker) | CRUD via `watchlist_store` |
| `strategy_runs` | Ogni `propicks-scan`/`contra`/`rotate` produce 1 riga | Audit trail |
| `ai_verdicts` | Cache + storia verdict Claude (sostituisce `data/ai_cache/`) | `io/db.py::ai_verdict_cache_*` helpers |
| `daily_budget` | Counter giornaliero spesa AI (UPSERT by date) | `ai/budget.py` |
| `regime_history` | Snapshot giornaliero regime ^GSPC | Popolato da scheduler Phase 3 |
| `portfolio_snapshots` | Equity curve + exposure per strategia (daily) | Popolato da scheduler Phase 3 |
| `schema_version` | Versioning per future migrations DDL | Init a v1 |

### 1.1 Connection model

`io/db.py::connect()` apre una connessione per chiamata (SQLite file locale =
open nanosecond-fast). **WAL mode** + foreign keys ON. `transaction()` context
manager per atomicità di mutazioni multi-row. Niente `PARSE_DECLTYPES`: tutti i
timestamp / date sono TEXT ISO-formatted.

### 1.2 Migrazione one-shot

```bash
propicks-migrate --dry-run     # anteprima
propicks-migrate                # esegue + backup JSON → *.json.bak
```

`propicks-migrate` legge i JSON legacy e popola le tabelle. **Idempotente**
(skip tabella se già popolata). Rinomina i JSON originali a `.json.bak` per
recovery. Backup della folder `ai_cache/` → `ai_cache.bak/`.

### 1.3 Test isolation

`conftest.py` ha fixture autouse `_isolate_db` che monkeypatcha `config.DB_FILE`
su `tmp_path`. Ogni test ha DB ephemeral fresco. **Nessun test tocca mai il DB
reale.**

### 1.4 Backup

```bash
cp data/propicks.db data/propicks.db.bak
# oppure
sqlite3 data/propicks.db ".backup data/propicks.db.bak"
```

---

## 2. Market data cache (Phase 2)

Cache **read-through con TTL** per yfinance. Tutti i call via
`market/yfinance_client.py` passano dalla cache.

### 2.1 Performance

- **Scan singolo ticker**: 3.0s → 0.42s (**speedup 7×**)
- **Scan batch 3 ticker**: 4.5s → 0.44s (**speedup 10×**)

### 2.2 Tabelle

In `data/propicks.db` (stessa SQLite di Phase 1):

| Tabella | Contenuto | TTL | Miss behavior |
|---------|-----------|-----|---------------|
| `market_ohlcv_daily` | bar daily (PK ticker, date) | 8h | fetch `yf.Ticker.history` + UPSERT |
| `market_ohlcv_weekly` | bar weekly (PK ticker, week_start) | 7gg | fetch `interval=1wk` + UPSERT |
| `market_ticker_meta` | sector, beta, name, next_earnings_date (PK ticker) | 7gg | fetch `yf.Ticker.info` + UPSERT |

**TTL rationale**:
- 8h daily copre una sessione intera (scan alle 9am e alle 3pm riusano lo stesso set).
- 7gg weekly è stabile post-Fri close.
- 7gg meta perché Yahoo aggiorna beta settimanale.

### 2.3 Public API invariata

`download_history`, `download_weekly_history`, `download_benchmark`,
`download_benchmark_weekly`, `get_ticker_sector`, `get_ticker_beta`,
`get_current_prices`, `download_returns`, `get_next_earnings_date` — firme
identiche al pre-cache. CLI/domain/dashboard non cambiano.

### 2.4 CLI `propicks-cache`

```bash
propicks-cache stats                    # righe totali + range date
propicks-cache warm AAPL MSFT NVDA      # prefetch daily+weekly
propicks-cache warm AAPL --force        # invalida + refetch
propicks-cache clear --ticker AAPL      # rimuovi solo AAPL
propicks-cache clear --all              # wipe totale (ricrea al primo scan)
propicks-cache clear --stale            # solo righe fuori TTL
propicks-cache clear --interval daily   # solo una granularità
```

### 2.5 Offline resilience

Se la cache è popolata e fresh, uno scan completo funziona senza rete. Test:
`propicks-cache warm` + disconnetti WiFi + `propicks-scan` → funziona fino a
scadenza TTL (8h daily).

### 2.6 Data quality

Il cache **drop rows** con `Close IS NULL` (skip silenzioso dei bar yfinance
parziali). `PRIMARY KEY (ticker, date)` previene duplicati da fetch ripetuti.
**UPSERT** aggiorna i bar esistenti (refresh garantito sui close revisionati
post-market).

---

## 3. Index constituents cache

Per i discovery flow su universi ampi (S&P 500, FTSE MIB, STOXX 600), Wikipedia
è la sorgente primaria parsata via `pandas.read_html` + helper UA-aware.

### 3.1 Tabelle

| Tabella | Contenuto | TTL |
|---------|-----------|-----|
| `index_constituents` | (index_name, ticker, company_name, sector, added_date) | 7gg |
| `index_constituents_meta` | last_fetched_at per index | 7gg |

### 3.2 Fallback chain

1. **Cache fresh** entro TTL → ritorno diretto
2. **Wikipedia fetch** + UPSERT atomico
3. **Cache stale** se Wikipedia fail (anche oltre TTL)
4. **Hardcoded snapshot** runtime (mai persistito): SP500 (~50 mega-cap),
   FTSEMIB (40), STOXX600 (~50)

### 3.3 Requisiti tecnici

- `lxml>=4.9` come dependency (richiesto da `pd.read_html`)
- User-Agent custom (Wikipedia ritorna 403 su request anonime). Helper
  `_read_wikipedia_tables(url)` che pre-fetcha via `urllib.request` con UA
  descrittivo conforme alla [Wikipedia User-Agent policy](https://meta.wikimedia.org/wiki/User-Agent_policy).

### 3.4 Sanity check per index

| Index | Min constituents | Note |
|-------|------------------|------|
| S&P 500 | 480 | 503 reali, sotto 480 = format change |
| FTSE MIB | 35 | Esattamente 40 nomi |
| STOXX 600 | 500 | Wikipedia talvolta espone tabelle parziali (~534) |
