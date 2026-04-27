# Architecture Overview

Sistema layer-cleaned con separation of concerns rigida. Ogni layer ha una
responsabilità ben definita e dipende solo da layer "più puri" di lui.

> **Per gli invarianti business e di codice**, vedi [`CLAUDE.md`](../CLAUDE.md).
> Questo doc descrive l'**architettura tecnica** dei layer e il data flow.

---

## Layer diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  External:  yfinance (rete)   Anthropic API   TradingView   Telegram │
└────────────┬─────────────────┬──────────────┬──────────────┬─────────┘
             │                 │              │              │
   ┌─────────▼────────┐  ┌─────▼────┐         │      ┌───────▼────────┐
   │ market/          │  │ ai/      │         │      │ notifications/ │
   │ yfinance_client  │  │ claude_  │         │      │ telegram_bot   │
   │ index_const.     │  │ client   │         │      │ dispatcher     │
   └─────────┬────────┘  └─────┬────┘         │      └───────┬────────┘
             │                 │              │              │
             │           ┌─────▼─────────┐    │              │
             │           │ ai/           │    │              │
             │           │ thesis_valid. │    │              │
             │           │ etf_valid.    │    │              │
             │           │ contra_valid. │    │              │
             │           └─────┬─────────┘    │              │
             │                 │              │              │
             │   ┌─────────────▼─────────┐    │              │
             │   │ io/  (SQLite)         │◀───┼──────────────┘
             │   │ portfolio_store       │    │
             │   │ journal_store         │    │
             │   │ watchlist_store       │    │
             │   │ ai_verdicts_cache     │    │
             │   │ market_cache          │    │
             │   └─────────┬─────────────┘    │
             │             │                  │
   ┌─────────▼─────────────▼─────────────┐    │
   │ domain/  (puro: no I/O, no rete)    │    │
   │  scoring  contrarian_scoring        │    │
   │  etf_scoring  regime  indicators    │    │
   │  sizing  sizing_v2  risk            │    │
   │  trade_mgmt  exposure  attribution  │    │
   │  calendar  validation  verdict      │    │
   └────────────────┬────────────────────┘    │
                    │                         │
   ┌────────────────▼─────────────────────────▼──┐
   │ Composers — chiamano domain + io + ai       │
   ├─────────────────────────────────────────────┤
   │ cli/        dashboard/      scheduler/      │
   │ reports/    backtest/       notifications/  │
   └─────────────────────────────────────────────┘
                    │
              ┌─────▼─────┐
              │ Utenti    │
              │ trader    │
              └───────────┘
```

---

## Layer-by-layer

### `domain/` — Pure business logic

**Regola**: NESSUN import da `io/`, `market/`, `cli/`, `reports/`, `ai/`. Riceve
dati in input, ritorna dati in output. Testabile senza rete e senza disco.

| Modulo | Responsabilità |
|--------|----------------|
| `indicators.py` | EMA, RSI, ATR, ADX, MACD — primitives su `pd.Series` |
| `scoring.py` | Momentum: 6 sub-score + `analyze_ticker` |
| `contrarian_scoring.py` | Contrarian: 4 sub-score + `analyze_contra_ticker` |
| `contrarian_discovery.py` | 3-stage pipeline su universi ampi |
| `etf_scoring.py` | ETF: 4 sub-score + `rank_universe` + `suggest_allocation` |
| `etf_universe.py` | Query helpers SECTOR_ETFS_US/EU/WORLD |
| `stock_rs.py` | Peer RS stock vs sector ETF (US-only) |
| `regime.py` | Classifier macro weekly (5-bucket) |
| `sizing.py` / `sizing_v2.py` | Position sizing (base + Kelly+vol+corr) |
| `risk.py` | Math puro: kelly_fractional, VaR, vol, correlation |
| `trade_mgmt.py` | Trailing stop + time stop + target hit |
| `exposure.py` | Sector + beta-weighted + correlation matrix |
| `attribution.py` | P&L decomposition α/β/sector/timing |
| `calendar.py` | Earnings hard gate + macro events lookup |
| `validation.py` / `verdict.py` | Schema validators per AI output |

**Eccezioni note**: gli orchestratori `analyze_*` chiamano `market/` per fetch
dati. Questo viola formalmente la layer purity ma è documentato come trade-off
accettato (vedi review SERIO-3 in commit history). Il refactor pulito sarebbe
splittare `analyze_*_pure(hist_df, weekly_df, ...)` + wrapper.

---

### `market/` — Adattatore esterno (yfinance + Wikipedia)

**Regola**: unico modulo che parla con la rete. Se domani si cambia provider, si
tocca solo qui.

| Modulo | Responsabilità |
|--------|----------------|
| `yfinance_client.py` | OHLCV daily/weekly, sector GICS, beta, earnings date, VIX |
| `index_constituents.py` | Wikipedia parsing per S&P 500, FTSE MIB, STOXX 600 |

Read-through cache via `io/market_cache_store.py` (TTL 8h daily, 7gg weekly,
7gg meta). Soglia min barre `MARKET_MIN_DAILY_BARS=155`.

---

### `io/` — Persistenza (SQLite)

**Regola**: source of truth è `data/propicks.db` (WAL mode). I file JSON sono
ritirati post-Phase 1. Vedi [STORAGE](STORAGE.md) e [DATA_DICTIONARY](DATA_DICTIONARY.md).

| Modulo | Responsabilità |
|--------|----------------|
| `db.py` | Connection helpers, schema bootstrap, migration |
| `portfolio_store.py` | CRUD posizioni aperte |
| `journal_store.py` | Append-only journal trade |
| `watchlist_store.py` | CRUD watchlist |
| `regime_history_store.py` | Snapshot regime EOD per attribution |
| `strategy_runs_store.py` | Log run scheduler |
| `market_cache_store.py` | Cache OHLCV via SQL |
| `sync_store.py` | Reconciliation broker portfolio |

Può importare da `domain/` (per type) e `config`. Può chiamare `market/` solo
quando strettamente necessario (es. `unrealized_pl` che serve prezzo corrente).

---

### `ai/` — Adattatore Anthropic

**Regola**: unico modulo che parla con SDK `anthropic`. Espone funzioni
strutturate `validate_thesis()` / `validate_contrarian_thesis()` /
`validate_rotation()` che ritornano dict pydantic-validated.

| Modulo | Responsabilità |
|--------|----------------|
| `claude_client.py` | Wrapper SDK + schema pydantic + `web_search` tool |
| `prompts.py` | System prompt + user template momentum |
| `contrarian_prompts.py` | Idem contrarian |
| `etf_prompts.py` | Idem ETF rotation |
| `thesis_validator.py` | Orchestrazione momentum + cache + R/R sanity floor |
| `contrarian_validator.py` | Orchestrazione contrarian + flush-vs-break sanity |
| `etf_validator.py` | Orchestrazione ETF + cache key con hash top-3 ranked |
| `budget.py` | Daily call/cost cap (`AI_MAX_CALLS_PER_DAY`, `AI_MAX_COST_USD_PER_DAY`) |

**Cache**: tabella `ai_verdicts` (chiave: `<TICKER>_<VERSION>_<DAY>`). TTL 24h
momentum/contrarian, 8h ETF rotation (post-CRIT-5: include hash top-3 ranked).

**Web search**: tool server-side Anthropic `web_search_20250305`. $0.01/ricerca,
max 5 per call (configurabile via `PROPICKS_AI_WEB_SEARCH_MAX_USES`).

---

### `reports/` — Markdown generators

Compone markdown legendi `domain/` + `io/`. Output in `reports/<YYYY-MM>/`.

| Tipo | Comando |
|------|---------|
| Weekly | `propicks-report weekly` |
| Monthly | `propicks-report monthly` |
| Attribution | `propicks-report attribution` |

---

### `cli/` — Thin wrappers argparse

**Regola**: parsing argomenti + chiamata domain/io/ai/reports + formatting
tabellare. **Nessuna logica di business** qui.

14 entry points definiti in `pyproject.toml::[project.scripts]`.
Vedi [CLI_REFERENCE](CLI_REFERENCE.md).

---

### `dashboard/` — Streamlit multi-page

**Regola**: thin come `cli/`. Cached readers in `_shared.py` per evitare
refetch yfinance a ogni rerun. Vedi [DASHBOARD_GUIDE](DASHBOARD_GUIDE.md).

---

### `scheduler/` — APScheduler EOD

Cron-based daemon. 8 job principali (warm cache, regime snapshot, EOD scan,
trailing manage, alerts cleanup, attribution monthly, ...). Vedi
[SCHEDULER](SCHEDULER.md).

---

### `notifications/` — Telegram

Async bot daemon (extras `[telegram]`). Push alert su severity rules + 11
comandi interattivi via chat. Vedi [TELEGRAM_BOT](TELEGRAM_BOT.md).

---

### `tradingview/` — Pine scripts

NON è Python. 4 script che replicano visualmente i motori Python in real-time
sul chart TradingView. Default Pine = config.py byte-per-byte. Vedi
[PINE_SCRIPTS_REFERENCE](PINE_SCRIPTS_REFERENCE.md).

---

## Data flow tipico

### Scan momentum end-to-end

```
User → propicks-scan AAPL --validate
  │
  └─→ cli/scanner.py
       │
       ├─→ domain/scoring.py::analyze_ticker("AAPL")
       │    ├─→ market/yfinance_client::download_history (cache 8h)
       │    ├─→ market/yfinance_client::download_weekly_history (cache 7gg)
       │    ├─→ domain/regime.py::classify_regime
       │    ├─→ domain/stock_rs.py::score_rs_vs_sector
       │    └─→ score_trend, score_momentum, ..., classify
       │
       ├─→ ai/thesis_validator.py::validate_thesis (se --validate)
       │    ├─→ io/db.py::ai_verdict_cache_get
       │    ├─→ ai/budget.py::check_budget
       │    ├─→ ai/claude_client.py::call_validation
       │    ├─→ _enforce_reward_risk (sanity layer)
       │    └─→ io/db.py::ai_verdict_cache_put
       │
       ├─→ io/watchlist_store::add_to_watchlist (auto, classe A/B)
       │
       └─→ tabulate output → stdout
```

### Backtest portfolio

```
User → propicks-backtest AAPL MSFT --portfolio --monte-carlo 500
  │
  └─→ cli/backtest.py
       │
       └─→ backtest/portfolio_engine.py
            ├─→ domain/scoring + domain/sizing per ogni ticker
            ├─→ Monte Carlo bootstrap su returns
            ├─→ backtest/walkforward.py (se --oos-split)
            └─→ stats: hit rate, profit factor, max DD, Sharpe
```

---

## Dependency rules (riassunto)

| Layer | Può importare da |
|-------|------------------|
| `domain/` | Solo `config`, `domain/`. Mai `io/`, `market/`, `ai/`, `cli/`, `reports/` |
| `market/` | `config`, `domain/` (per type), `io/market_cache_store` |
| `io/` | `config`, `domain/`, eventualmente `market/` per prezzi correnti |
| `ai/` | `config`, `io/db` (cache), schema pydantic |
| `reports/` | Tutti i layer più puri (compone markdown) |
| `cli/`, `dashboard/`, `scheduler/`, `notifications/` | Tutti i layer più puri (composers thin) |

Violazioni di queste regole sono **bug architetturali** — vanno aperte come
issue o fixate. La principale eccezione documentata sono gli orchestratori
`analyze_*` di `domain/` che chiamano `market/`.

---

## Path runtime

I path `data/` e `reports/` sono ancorati alla root del progetto identificata
da `_find_project_root()` (risale fino al `pyproject.toml`). Conseguenza: tutti
i comandi funzionano da qualunque cwd dopo `pip install -e .`.

| Path | Contenuto |
|------|-----------|
| `data/propicks.db` | SQLite source of truth (gitignored) |
| `data/baskets/` | Output discovery batch (markdown export) |
| `data/ai_cache/usage_YYYY-MM-DD.json` | Daily AI budget tracker |
| `reports/<YYYY-MM>/` | Markdown report generati |

---

## Test strategy

```bash
pytest                       # 544 test, tutti senza rete
pytest tests/unit/test_scoring.py -v
pytest --cov=src/propicks    # coverage
```

I test su `domain/` non toccano rete né disco. Per testare `cli/` o `io/`,
fixture autouse `_isolate_db` in `conftest.py` crea un DB temporaneo per ogni
test.

`ai/` è testato con SDK Anthropic interamente mockato (`unittest.mock.patch`).
