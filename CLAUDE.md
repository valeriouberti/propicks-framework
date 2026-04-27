# CLAUDE.md — Trading Engine AI-Driven

## Panoramica

Motore Python per un trading system AI-driven che combina segnali da Investing
Pro Picks AI con analisi qualitativa (Claude/Perplexity) e tecnica (TradingView).
Il sistema gestisce il ciclo completo: screening → scoring → execution →
journaling → review.

> **Documentazione di dettaglio**: questa CLAUDE.md è il punto d'ingresso per
> orientarsi rapidamente. Per ogni strategia / sottosistema esiste un MD
> dedicato in `docs/` con tesi, architettura, invarianti e trade-off espliciti.
> **Consulta sempre il MD specifico prima di toccare un sottosistema** — qui
> trovi solo l'index e gli invarianti globali.

---

## Index documentazione

### Strategie

| Strategia | MD | Comando CLI | Scoring engine |
|-----------|-----|-------------|----------------|
| Stock momentum / quality | [`MOMENTUM_STRATEGY.md`](docs/MOMENTUM_STRATEGY.md) | `propicks-scan` | 6 sub-score (trend/momentum/volume/dist-high/vol/MA-cross) |
| Quality-filtered mean reversion | [`CONTRARIAN_STRATEGY.md`](docs/CONTRARIAN_STRATEGY.md) | `propicks-contra` | 4 sub-score (oversold/quality/context/reversion) |
| Sector ETF rotation | [`ETF_ROTATION_STRATEGY.md`](docs/ETF_ROTATION_STRATEGY.md) | `propicks-rotate` | 4 sub-score (RS/regime-fit/abs-mom/trend) |

### Sistemi operativi

| Tema | MD |
|------|-----|
| Backtest (single + portfolio + walk-forward + Monte Carlo) | [`BACKTEST_GUIDE.md`](docs/BACKTEST_GUIDE.md) |
| Risk Framework v2 (Kelly + Vol target + VaR + Corr) | [`RISK_FRAMEWORK.md`](docs/RISK_FRAMEWORK.md) |
| P&L Attribution + Phase 7 Gate + Weekly Report | [`PNL_ATTRIBUTION.md`](docs/PNL_ATTRIBUTION.md) |
| Catalyst Calendar (earnings + macro events) | [`CALENDAR.md`](docs/CALENDAR.md) |
| Scheduler EOD + Alerts queue | [`SCHEDULER.md`](docs/SCHEDULER.md) |
| Telegram bot (push + comandi) | [`TELEGRAM_BOT.md`](docs/TELEGRAM_BOT.md) |
| Storage (SQLite + market data cache + index constituents) | [`STORAGE.md`](docs/STORAGE.md) |
| Watchlist + Trade management + Esposizione + Sync | [`WATCHLIST_AND_TRADE_MGMT.md`](docs/WATCHLIST_AND_TRADE_MGMT.md) |

### Workflow operativo

| Doc | Scopo |
|-----|-------|
| [`Trading_System_Playbook.md`](docs/Trading_System_Playbook.md) | Workflow operativo + prompt Perplexity/Claude |
| [`Weekly_Operating_Framework.md`](docs/Weekly_Operating_Framework.md) | Cadenza weekly (Sab review, Dom plan, Lun-Ven exec) |
| [`USER_GUIDE.md`](docs/USER_GUIDE.md) | Guida utente trader |
| [`NEXT_STEPS.md`](docs/NEXT_STEPS.md) | Roadmap |

---

## Struttura Progetto

Pacchetto installabile (`pip install -e .`) con layout `src/`. Separazione netta
tra logica pura (`domain/`), persistenza (`io/`), adapter di rete (`market/`),
adapter AI (`ai/`), generatori di report (`reports/`), CLI (`cli/`) e
dashboard Streamlit (`dashboard/`).

```
propicks-ai-framework/
├── CLAUDE.md                     # Questo file — index + invarianti
├── pyproject.toml                # Deps, entry points CLI, tool config
├── Dockerfile, docker-compose.yml
├── src/propicks/
│   ├── config.py                 # Parametri operativi (capitale, regole, pesi, regime, contract Pine)
│   ├── domain/                   # Puro: nessun I/O, nessuna rete
│   │   ├── indicators.py         # EMA, RSI, ATR, ADX, MACD, pct_change
│   │   ├── scoring.py            # Momentum: 6 sub-score + analyze_ticker
│   │   ├── contrarian_scoring.py # Contrarian: 4 sub-score + analyze_contra_ticker
│   │   ├── contrarian_discovery.py # 3-stage pipeline su universi ampi
│   │   ├── etf_scoring.py        # ETF: 4 sub-score + rank_universe + alloc
│   │   ├── etf_universe.py       # Query helpers SECTOR_ETFS_US/EU/WORLD
│   │   ├── stock_rs.py           # Peer RS stock vs sector ETF (US-only)
│   │   ├── regime.py             # Classifier macro weekly (5-bucket, mirror Pine)
│   │   ├── attribution.py        # Phase 9: decompose trade (α/β/sector/timing)
│   │   ├── calendar.py           # Phase 8: earnings hard gate + macro events
│   │   ├── sizing.py             # calculate_position_size + bucket caps
│   │   ├── sizing_v2.py          # Phase 5: advanced (Kelly + vol + corr)
│   │   ├── risk.py               # Phase 5: kelly_fractional, VaR, vol, corr (pure math)
│   │   ├── trade_mgmt.py         # Trailing + time stop + target hit
│   │   ├── exposure.py           # Sector + beta-weighted + correlazioni
│   │   ├── validation.py
│   │   └── verdict.py
│   ├── backtest/                 # Walk-forward + portfolio engine + costs + walkforward
│   ├── io/                       # SQLite source of truth (portfolio/journal/watchlist/AI/sync)
│   ├── market/yfinance_client.py # Unico modulo che parla con yfinance
│   ├── market/index_constituents.py # Wikipedia fetch (S&P 500, FTSE MIB, STOXX 600)
│   ├── ai/                       # Adapter Anthropic (claude_client + prompts + validators)
│   ├── reports/                  # Markdown generators (weekly/monthly/attribution)
│   ├── cli/                      # Thin argparse wrappers (entry points)
│   ├── scheduler/                # Phase 3: APScheduler + jobs + alerts + history
│   ├── notifications/            # Phase 4: Telegram dispatcher + bot
│   └── dashboard/                # UI Streamlit multi-page (parallela alla CLI)
├── tradingview/                  # Pine script (contract con config.py)
├── docs/                         # Documentazione di dettaglio (vedi index sopra)
├── tests/unit/                   # Test puri su domain/ (no I/O, no rete)
├── data/propicks.db              # SQLite source of truth (gitignored)
└── reports/                      # Markdown generati (gitignored)
```

---

## Stack Tecnologico

- **Python 3.10+** con layout `src/` (editable install)
- **yfinance** + cache read-through SQLite (TTL 8h daily / 7gg weekly / 7gg meta)
- **pandas / numpy** — calcoli tecnici e statistici
- **lxml** — parser HTML per `pd.read_html` (Wikipedia index constituents)
- **tabulate** — output formattato per terminale
- **SQLite** (stdlib `sqlite3`) — source of truth, file `data/propicks.db`, WAL mode
- **anthropic** — SDK ufficiale per validazione tesi via Claude
- **pydantic** — validazione strutturata verdict AI
- **python-dotenv** — caricamento `.env` per `ANTHROPIC_API_KEY`
- **apscheduler** (Phase 3) — cron-based scheduler EOD jobs
- **python-telegram-bot** (Phase 4, extras `[telegram]`) — async bot daemon
- **streamlit** + **plotly** (extras `[dashboard]`) — UI multi-page
- **pytest / pytest-asyncio / ruff / mypy** (dev)
- **Docker** (opzionale) — `python:3.12-slim-bookworm` + `[dashboard]`

---

## Filosofia di Design

1. **Semplicità operativa**: ogni modulo ha un entry point CLI chiaro.
2. **Dati puliti**: ogni decisione viene loggata con contesto completo. Il
   journal è la fonte di verità per valutare la strategia.
3. **Nessuna magia**: il codice è esplicito e leggibile. Meglio 10 righe chiare
   che 3 righe criptiche.
4. **Modularità**: ogni file è indipendente. Si può usare solo lo scanner, solo
   il journal, o tutto insieme.

---

## Setup

```bash
pip install -e ".[dev]"               # dev (pytest, ruff, mypy)
pip install -e .                       # solo runtime
pip install -e ".[dashboard]"          # + Streamlit
pip install -e ".[dev,dashboard,telegram]"  # tutto
```

Per la validazione AI crea `.env` in root (gitignored):

```bash
ANTHROPIC_API_KEY=sk-ant-...
PROPICKS_AI_MODEL=claude-opus-4-6        # opzionale
PROPICKS_AI_WEB_SEARCH=1                 # opzionale, 1=on (default)
PROPICKS_AI_WEB_SEARCH_MAX_USES=5
```

`.env` viene caricato da `propicks.config` via `python-dotenv` con
`override=False` (la shell ha precedenza).

**Web search Claude**: tool `web_search_20250305` server-side Anthropic per
spot, earnings date, short interest, performance settoriale recente. Costo
**$0.01/ricerca** + token (input). Count loggato su stderr.

---

## Comandi Principali

Entry points CLI definiti in `pyproject.toml`. Funzionano da qualsiasi cwd dopo
l'install editable: i path di `data/` e `reports/` sono ancorati alla root del
progetto. La dashboard Streamlit è **parallela** alla CLI, non la sostituisce.

```bash
# Stock momentum / quality (vedi docs/MOMENTUM_STRATEGY.md)
propicks-scan AAPL                                # singolo
propicks-scan AAPL MSFT NVDA --strategy TechTitans
propicks-scan AAPL --validate                     # gate score≥60 + regime≥NEUTRAL
propicks-scan AAPL --force-validate               # bypassa gate + cache
propicks-scan AAPL --json --brief --no-watchlist

# Contrarian (vedi docs/CONTRARIAN_STRATEGY.md)
propicks-contra AAPL [--validate] [--json] [--brief] [--no-watchlist]
propicks-contra --discover-sp500 [--top N] [--min-score 60]   # ~500 nomi US
propicks-contra --discover-ftsemib                            # 40 large-cap IT
propicks-contra --discover-stoxx600                           # ~600 nomi EU

# ETF Rotation (vedi docs/ETF_ROTATION_STRATEGY.md)
propicks-rotate                                   # US, top 3
propicks-rotate --top 5 --region {US|EU|WORLD}
propicks-rotate --allocate [--validate] [--json]

# Portfolio
propicks-portfolio status / risk
propicks-portfolio size AAPL --entry X --stop Y --score-claude 8 --score-tech 75
propicks-portfolio size AAPL --entry X --stop Y --advanced \
    --strategy-name TechTitans --vol-target 0.15 # Phase 5 (Kelly+vol+corr)
propicks-portfolio size AAPL --entry X --stop Y --contrarian # bucket cap 8%
propicks-portfolio add AAPL --entry X --shares N --stop Y \
    --target Z --strategy TechTitans [--ignore-earnings]
propicks-portfolio update AAPL --stop X --target Y
propicks-portfolio remove AAPL
propicks-portfolio trail enable|disable AAPL     # contrarian rifiutato (target fisso)
propicks-portfolio manage [--apply]              # trailing + time stop + target hit
propicks-portfolio manage --atr-mult 2.5 --time-stop 20 --apply

# Journal
propicks-journal add AAPL long --entry-price X --entry-date YYYY-MM-DD \
    --stop Y --target Z --score-claude 8 --score-tech 75 \
    --strategy TechTitans --catalyst "..."
propicks-journal close AAPL --exit-price X --exit-date YYYY-MM-DD --reason "..."
propicks-journal list [--open|--closed] [--strategy NAME]
propicks-journal stats [--strategy NAME]

# Reports
propicks-report weekly|monthly|attribution      # attribution = Phase 9 (vedi PNL_ATTRIBUTION.md)

# Backtest (vedi docs/BACKTEST_GUIDE.md)
propicks-backtest AAPL [MSFT NVDA] [--period 3y] [--threshold 60]
propicks-backtest AAPL --portfolio --tc-bps 10 --monte-carlo 500
propicks-backtest AAPL --portfolio --oos-split 0.70 --monte-carlo 1000

# Watchlist
propicks-watchlist add AAPL --target X --note "..."
propicks-watchlist update|remove|list [--stale]|status

# Cache OHLCV (Phase 2 — vedi STORAGE.md)
propicks-cache stats / warm TICKERS [--force] / clear [--ticker|--all|--stale|--interval]

# Scheduler (Phase 3 — vedi SCHEDULER.md)
propicks-scheduler run                            # daemon bloccante (Europe/Rome)
propicks-scheduler job {warm|regime|snapshot|scan|trailing|cleanup|attribution}
propicks-scheduler alerts [--ack N|--ack-all|--stats]
propicks-scheduler history [--days 7]

# Telegram bot (Phase 4 — vedi TELEGRAM_BOT.md)
propicks-bot test|run|stats|mute-backlog|reset-retries [--alert-id N]
# Dalla chat: /status /portfolio /alerts /ack N /history /cache /regime /report /calendar /help

# Calendar (Phase 8 — vedi CALENDAR.md)
propicks-calendar earnings [--upcoming 30d] [--refresh]
propicks-calendar macro [--types FOMC,CPI]
propicks-calendar check AAPL [--refresh]

# Migrazione one-shot JSON → SQLite (eseguita una volta sola, idempotente)
propicks-migrate [--dry-run]

# Dashboard Streamlit (extras [dashboard])
propicks-dashboard                                # http://localhost:8501

# Docker
docker compose up -d / logs -f dashboard / down

# Test
pytest                                            # tutti senza rete
```

### Mappatura CLI ↔ dashboard

| CLI | Dashboard page |
|-----|----------------|
| *(home — no CLI equivalent)* | `app.py` Portfolio Overview |
| `propicks-scan [--validate]` | `pages/1_Scanner.py` |
| `propicks-rotate [--region]` | `pages/2_ETF_Rotation.py` |
| `propicks-portfolio size/add/update/remove` | `pages/3_Portfolio.py` (tabs base) |
| `propicks-portfolio risk` | `pages/3_Portfolio.py` → tab "Rischio & esposizione" |
| `propicks-portfolio manage [--apply]` / `trail enable|disable` | `pages/3_Portfolio.py` → tab "Trade management" |
| `propicks-journal add/close/list/stats` | `pages/4_Journal.py` |
| `propicks-report weekly/monthly` | `pages/5_Reports.py` + archivio |
| `propicks-backtest` | `pages/6_Backtest.py` (+ `pages/11_Backtest_Portfolio.py`) |
| `propicks-watchlist add/remove/update/list/status` | `pages/7_Watchlist.py` |
| `propicks-contra [--validate]` | `pages/8_Contrarian.py` |

---

## Regole di Business (Invarianti)

Queste regole sono hardcoded e NON devono essere aggirate. Per i dettagli e le
ragioni di ogni soglia, vedi il MD della strategia rilevante.

- **Max posizioni aperte**: **10** (shared cap, include momentum + contrarian + ETF)
- **Max size singola posizione**:
  - Stock momentum: **15%**
  - Sector ETF: **20%**
  - Contrarian: **8%**
- **Max esposizione aggregata sector ETF**: **60%** del capitale
- **Max esposizione aggregata contrarian**: **20%** (bucket cap indipendente)
- **Max posizioni contrarian simultanee**: **3** (cap interno al bucket)
- **Min cash reserve**: **20%** del capitale
- **Max loss per trade**:
  - Stock momentum: **8%**
  - Sector ETF: **5%**
  - Contrarian: **12%** (stop = `recent_low − 1×ATR`, vedi CONTRARIAN_STRATEGY.md)
- **Max loss settimanale**: 5% del capitale → blocco trading
- **Max loss mensile**: 15% del capitale → blocco trading e revisione
- **Earnings hard gate**: blocco entry se earnings entro **5 giorni**
  (override esplicito con `--ignore-earnings` per trade contrarian intentional)
- **Score minimo per entry**: Claude ≥ 6/10, Tecnico ≥ 60/100
- **Regime gate validazione AI**:
  - **Momentum**: skip BEAR / STRONG_BEAR (richiede regime ≥ NEUTRAL)
  - **Contrarian**: skip STRONG_BULL / STRONG_BEAR (edge collassa agli estremi)
  - **ETF Rotation**: in STRONG_BEAR i settori non favoriti sono forzati a 0;
    in BEAR sono capped a 50

---

## Convenzioni Codice

- Type hints su tutte le funzioni pubbliche
- Docstring su ogni classe e funzione pubblica
- f-string per formatting
- Costanti in MAIUSCOLO in `propicks.config`
- Date in formato ISO 8601 (YYYY-MM-DD)
- Prezzi in float con 2 decimali
- Percentuali come float (es. 0.08 = 8%)

### Separazione dei layer (importante)

- **`domain/`** non importa da `io/`, `market/`, `cli/`, `reports/`. È puro:
  riceve dati in input, ritorna dati in output. Testabile senza rete né disco.
- **`io/`** può importare da `domain/` e `config`. Può chiamare `market/` solo
  quando strettamente necessario (es. `unrealized_pl` che serve prezzi correnti).
- **`market/`** è l'unico modulo che parla con yfinance/rete (+ Wikipedia per
  index constituents). Se in futuro si cambia provider, si tocca solo qui.
- **`ai/`** è l'unico modulo che parla con l'SDK Anthropic. Espone
  `validate_thesis(...)` / `validate_contrarian_thesis(...)` /
  `validate_rotation(...)` che ritornano dict strutturati. Nessun altro layer
  importa `anthropic` direttamente. Include gate su `score_composite` e regime
  (varia per strategia), cache giornaliera in tabella `ai_verdicts`, tool
  `web_search` server-side.
- **`reports/`** può importare da tutti gli altri layer per comporre i markdown.
- **`cli/`** è thin: parsing argparse + chiamata a funzioni
  domain/io/ai/reports + formatting tabellare. **Nessuna logica di business.**
- **`dashboard/`** è thin come `cli/`: UI Streamlit che chiama le stesse
  funzioni. Cached readers in `_shared.py` per evitare refetch yfinance a ogni
  rerun. Per `st.dataframe` con tipi misti `float`/`"—"`: serializza tutto a
  string omogenea (PyArrow fail su double misti).
- **`tradingview/`** NON è Python — sono Pine script che replicano visualmente
  il motore. Il Pine è il layer real-time (timing + alert) che yfinance (EOD)
  non copre. Default Pine devono matchare `config.py` byte per byte.

---

## Note per Claude Code

- Dopo modifiche a `domain/` esegui `pytest` — tutti i test girano senza rete.
- Per modifiche a `cli/` o `reports/`, smoke test con gli entry points
  (`propicks-portfolio status`, `propicks-report weekly`, ecc.).
- Per modifiche a `dashboard/`, smoke test con `propicks-dashboard` — verifica
  che ogni page renda senza eccezioni e che i `st.dataframe` non rompano la
  serializzazione Arrow (tipi misti `float`/sentinel `"—"` → tutti a string).
- Il DB SQLite `data/propicks.db` è la source of truth — backup con `cp` o
  `sqlite3 .backup`.
- Il journal è append-only: i trade chiusi non vengono cancellati, viene
  aggiunto il campo `exit_*`.
- Nuove dipendenze → aggiungi in `[project.dependencies]` di `pyproject.toml` e
  documenta nello stack tecnologico.
- Per ticker italiani usa il suffisso `.MI` (es. `ENI.MI`, `ISP.MI`).
- **Non importare da `domain/` verso `io/market/ai/cli/reports`**: rompe la
  purezza del layer e blocca i test senza rete.
- `ANTHROPIC_API_KEY` va in `.env` (gitignored) o esportata in shell. Senza la
  chiave, `--validate` fallisce con errore esplicito ma il resto della CLI
  continua a funzionare normalmente.
- Quando modifichi `ai/prompts.py::SYSTEM_PROMPT` (o qualsiasi system prompt),
  ogni byte cambiato invalida la prompt cache lato Anthropic: mantieni dinamico
  solo il user prompt.
- I verdict sono cacheati in tabella `ai_verdicts` del DB con TTL 24h
  (48h per ETF). Usa `--force-validate` per forzare rivalidazione, oppure
  `DELETE FROM ai_verdicts WHERE cache_key = '<TICKER>_v4_<YYYY-MM-DD>'` via sqlite3.
- **Prima di toccare un sottosistema, leggi il MD specifico in `docs/`** —
  contiene tesi, architettura, decisioni di design, trade-off accettati e
  invarianti che non sono replicati qui.
