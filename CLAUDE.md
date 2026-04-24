# CLAUDE.md — Trading Engine AI-Driven

## Panoramica Progetto

Motore Python per un trading system AI-driven che combina segnali da Investing Pro Picks AI con analisi qualitativa (Claude/Perplexity) e tecnica (TradingView). Il sistema gestisce il ciclo completo: screening → scoring → execution → journaling → review.

## Struttura Progetto

Pacchetto installabile (`pip install -e .`) con layout `src/`. Separazione
netta tra logica pura (`domain/`), persistenza (`io/`), adapter di rete
(`market/`), adapter AI (`ai/`), generatori di report (`reports/`), CLI (`cli/`)
e dashboard Streamlit (`dashboard/`).

```
propicks-ai-framework/
├── CLAUDE.md                  # Questo file — contesto per Claude Code
├── pyproject.toml             # Deps, entry points CLI, tool config (ruff/pytest/mypy)
├── Dockerfile                 # Immagine dashboard (python:3.12-slim + [dashboard] extra)
├── docker-compose.yml         # Lancio con volumi persistenti data/ e reports/
├── .dockerignore              # Esclude .git, .venv, data/, reports/, docs/, tradingview/
├── src/propicks/
│   ├── config.py              # Parametri operativi (capitale, regole, pesi, regime, contract Pine)
│   ├── domain/                # Puro: nessun I/O, nessuna rete
│   │   ├── indicators.py      # EMA, RSI, ATR, ADX, MACD, pct_change
│   │   ├── scoring.py         # 6 sub-score stock + classify + analyze_ticker (momentum)
│   │   ├── contrarian_scoring.py # 4 sub-score contrarian (oversold/quality/context/rr) + analyze_contra_ticker
│   │   ├── etf_scoring.py     # 4 sub-score ETF (RS/regime/mom/trend) + rank_universe + alloc
│   │   ├── etf_universe.py    # Query helpers su SECTOR_ETFS_US/EU
│   │   ├── stock_rs.py        # Peer RS stock vs sector ETF (solo US, campo informativo)
│   │   ├── regime.py          # Classifier macro weekly (5-bucket, mirror Pine weekly)
│   │   ├── attribution.py     # Phase 9: decompose trade (α/β/sector/timing) + gate Phase 7
│   │   ├── sizing.py          # calculate_position_size (stock + ETF cap), portfolio_value
│   │   ├── trade_mgmt.py      # Trailing stop ATR-based + time stop (gestione in-vita)
│   │   ├── exposure.py        # Concentrazione settoriale + beta-weighted + correlazioni
│   │   ├── validation.py      # validate_scores, validate_date
│   │   └── verdict.py         # verdict qualitativo, max_drawdown
│   ├── backtest/              # Walk-forward backtest single-stock (puro su DataFrame)
│   │   ├── engine.py          # backtest_ticker + Trade/BacktestResult dataclasses
│   │   └── metrics.py         # win_rate/PF/CAGR/Sharpe/Sortino/DD + aggregate_metrics
│   ├── io/                    # Persistenza SQLite (source of truth)
│   │   ├── db.py              # SQLite connection + schema init + AI verdict cache helpers
│   │   ├── schema.sql         # Schema DDL (9 tabelle)
│   │   ├── portfolio_store.py # load + add/remove/update/close_position (backend SQLite)
│   │   ├── journal_store.py   # load + add_trade/close_trade (append-only, backend SQLite)
│   │   ├── trade_sync.py      # Coordinator journal+portfolio (open_trade/close_trade)
│   │   └── watchlist_store.py # load + add/remove/update (dedup per PK ticker)
│   ├── market/
│   │   └── yfinance_client.py # Unico modulo che parla con yfinance
│   ├── ai/                    # Adapter Anthropic (validazione tesi via Claude)
│   │   ├── claude_client.py   # SDK anthropic + ThesisVerdict + ETFRotationVerdict + ContrarianVerdict
│   │   ├── prompts.py         # System prompt stock momentum (equity analyst)
│   │   ├── etf_prompts.py     # System prompt ETF (macro strategist)
│   │   ├── contrarian_prompts.py # System prompt contrarian (event-driven/mean-reversion PM)
│   │   ├── thesis_validator.py# Gate + cache 24h + orchestrazione stock momentum
│   │   ├── contrarian_validator.py # Gate inverso + cache 24h + orchestrazione mean reversion
│   │   └── etf_validator.py   # Skip STRONG_BEAR + cache 48h + orchestrazione rotation
│   ├── reports/               # Markdown generators
│   │   ├── benchmark.py       # get_benchmark_performance (^GSPC, FTSEMIB.MI)
│   │   ├── common.py          # parse_date, trades_*_between, fmt_pct
│   │   ├── weekly.py
│   │   ├── monthly.py
│   │   └── attribution_report.py # Phase 9: weekly P&L decomposition markdown
│   ├── cli/                   # Thin argparse wrappers (entry points)
│   │   ├── scanner.py         # propicks-scan (momentum/quality)
│   │   ├── contrarian.py      # propicks-contra (quality-filtered mean reversion)
│   │   ├── portfolio.py       # propicks-portfolio (status/risk/size/add/manage/trail/...)
│   │   ├── journal.py         # propicks-journal
│   │   ├── report.py          # propicks-report
│   │   ├── rotate.py          # propicks-rotate (sector rotation ETF)
│   │   ├── backtest.py        # propicks-backtest (walk-forward single-stock)
│   │   ├── watchlist.py       # propicks-watchlist (add/remove/update/list/status)
│   │   ├── cache.py           # propicks-cache (stats/warm/clear OHLCV cache)
│   │   ├── scheduler.py       # propicks-scheduler (run/job/alerts/history)
│   │   └── bot.py             # propicks-bot (Telegram daemon + queue helpers)
│   ├── scheduler/             # Phase 3 — automazione EOD
│   │   ├── jobs.py            # 6 job functions idempotenti + @run_job decorator
│   │   ├── alerts.py          # alert queue CRUD + dedup_key logic
│   │   ├── history.py         # scheduler_runs audit + stats
│   │   └── runner.py          # APScheduler daemon (BlockingScheduler)
│   ├── notifications/         # Phase 4 — Telegram bot
│   │   ├── formatter.py       # alert dict → Telegram Markdown (pure)
│   │   ├── dispatcher.py      # async poll + send + mark delivered
│   │   ├── bot_commands.py    # handlers /status /alerts /ack /help
│   │   └── bot.py             # Application wiring + polling + dispatcher task
│   └── dashboard/             # UI Streamlit parallela alla CLI (non la sostituisce)
│       ├── launcher.py        # Entry point propicks-dashboard (bootstrap.run)
│       ├── _shared.py         # Cached readers, formatters, UI primitives
│       ├── app.py             # Home / Portfolio Overview
│       └── pages/             # Streamlit multi-page auto-routing
│           ├── 1_Scanner.py           # ≡ propicks-scan [--validate]
│           ├── 2_ETF_Rotation.py      # ≡ propicks-rotate [--region] [--allocate]
│           ├── 3_Portfolio.py         # ≡ propicks-portfolio size/add/update/remove + risk + manage
│           ├── 4_Journal.py           # ≡ propicks-journal add/close/list/stats
│           ├── 5_Reports.py           # ≡ propicks-report weekly/monthly + archive
│           ├── 6_Backtest.py          # ≡ propicks-backtest
│           ├── 7_Watchlist.py         # ≡ propicks-watchlist (live score + READY flag)
│           └── 8_Contrarian.py        # ≡ propicks-contra [--validate]
├── tradingview/               # Pine script (contract con config.py)
│   ├── daily_signal_engine.pine    # Entry triggers in tempo reale (BRK/PB/GC/SQZ/DIV)
│   └── weekly_regime_engine.pine   # Filtro macro (5-bucket) — duplicato visuale di regime.py
├── docs/
│   ├── Trading_System_Playbook.md  # Workflow operativo + prompt Perplexity/Claude
│   └── Weekly_Operating_Framework.md # Cadenza weekly (Sab review, Dom plan, Lun-Ven exec)
├── tests/
│   ├── conftest.py            # Fixture condivise
│   └── unit/                  # Test puri su domain/ (no I/O, no rete)
│       ├── test_indicators.py
│       ├── test_scoring.py
│       ├── test_etf_scoring.py     # Sub-score ETF, regime cap, allocation
│       ├── test_etf_universe.py    # Query helpers universo ETF
│       ├── test_sizing.py
│       ├── test_verdict.py
│       ├── test_regime.py
│       ├── test_stock_rs.py        # Peer RS mapping + gate US-only
│       ├── test_trade_mgmt.py      # Trailing stop + time stop + suggest_stop_update
│       ├── test_exposure.py        # Sector concentration + beta-weighted + correlazioni
│       ├── test_backtest.py        # Engine su DataFrame sintetici + metrics
│       ├── test_thesis_validator.py # SDK Anthropic mockato
│       └── test_watchlist_store.py # CRUD + migrazione schema legacy
├── data/                      # Runtime state (gitignored)
│   ├── propicks.db            # SQLite source of truth (9 tabelle)
│   ├── baskets/YYYY-MM.json   # Storico basket Pro Picks mensili (input, non stato)
│   ├── *.json.bak             # Backup JSON pre-migration (portfolio/journal/watchlist)
│   └── ai_cache.bak/          # Backup cache AI pre-migration
└── reports/                   # Report generati (gitignored)
    └── weekly_YYYY-MM-DD.md
```

## Stack Tecnologico

- **Python 3.10+** con layout `src/` (editable install via pyproject.toml)
- **yfinance** — dati di mercato real-time e storici, **con cache read-through SQLite** (Phase 2, TTL daily 8h / weekly 7gg / meta 7gg)
- **pandas / numpy** — calcoli tecnici e statistici
- **tabulate** — output formattato per terminale
- **SQLite** (stdlib ``sqlite3``) — source of truth per tutto lo stato transazionale: positions, trades, watchlist, AI verdicts, daily budget, strategy runs, regime history, portfolio snapshots. DB file: ``data/propicks.db``. Schema DDL in ``src/propicks/io/schema.sql``. Migrazione one-shot dai JSON legacy via ``propicks-migrate`` (backup ``*.json.bak`` conservato).
- **anthropic** — SDK ufficiale per validazione tesi via Claude Opus 4.6
- **pydantic** — validazione strutturata del verdict AI (`ThesisVerdict`)
- **python-dotenv** — caricamento `.env` per `ANTHROPIC_API_KEY`
- **apscheduler** (Phase 3) — cron-based scheduler per i 6 job EOD (``BlockingScheduler`` + ``CronTrigger`` tz ``Europe/Rome``)
- **python-telegram-bot** (Phase 4, extras ``[telegram]``) — async bot daemon: polling per comandi bidirezionali + task concorrente dispatcher per push notifications
- **pytest-asyncio** (dev) — supporto async test per il dispatcher
- **streamlit** (extra `[dashboard]`) — UI multi-page parallela alla CLI
- **plotly** (extra `[dashboard]`) — chart interattivi opzionali (placeholder)
- **pytest** (dev) — test unit su `domain/` e `ai/` (SDK mockato)
- **ruff + mypy** (dev) — lint e type check
- **Docker** (opzionale) — immagine `python:3.12-slim-bookworm` con `[dashboard]`
  preinstallato, volumi persistenti `data/` e `reports/`

## Filosofia di Design

1. **Semplicità operativa**: ogni modulo ha un entry point CLI chiaro. Il trader deve poter eseguire tutto da terminale con comandi rapidi.
2. **Dati puliti**: ogni decisione viene loggata con contesto completo. Il journal è la fonte di verità per valutare la strategia.
3. **Nessuna magia**: il codice è esplicito e leggibile. Meglio 10 righe chiare che 3 righe criptiche.
4. **Modularità**: ogni file è indipendente. Si può usare solo lo scanner, solo il journal, o tutto insieme.

## Setup

```bash
# Installazione editable con dev deps (pytest, ruff, mypy)
pip install -e ".[dev]"

# Solo runtime
pip install -e .

# Runtime + dashboard Streamlit (propicks-dashboard entry point)
pip install -e ".[dashboard]"

# Tutto (dev + dashboard)
pip install -e ".[dev,dashboard]"
```

Per la validazione AI crea un file `.env` in root (già in `.gitignore`):

```bash
ANTHROPIC_API_KEY=sk-ant-...
PROPICKS_AI_MODEL=claude-opus-4-6        # opzionale, default opus
PROPICKS_AI_WEB_SEARCH=1                 # opzionale, 1=on (default), 0=off
PROPICKS_AI_WEB_SEARCH_MAX_USES=5        # opzionale, cap ricerche/chiamata
```

Il file `.env` viene caricato da `propicks.config` via `python-dotenv` con
`override=False` (la shell ha precedenza se la variabile è già esportata).

**Web search:** quando abilitato (default), Claude può invocare il tool
`web_search_20250305` (server-side Anthropic) per recuperare spot di
commodity/FX/indici, date e risultati di earnings, short interest e performance
settoriale recente. Costo: **$0.01 a ricerca** più i token del contenuto
recuperato (conteggiati come input). Il count viene loggato su stderr dopo
ogni chiamata fresca.

## P&L attribution + weekly report (post Phase 9)

Decomposizione automatica del P&L per ogni trade chiuso in **4 componenti additive**:

```
total_pnl = market (β × SPX_return) + sector (ETF - SPX) + alpha + timing
```

**Perché**: capire *perché* vinci/perdi, non solo *quanto*. Lo stesso +10% di P&L
può essere:
- +8% market (tutto beta — stai solo prendendo rischio SPX) → **no alpha**
- +8% alpha (selezione ticker ha aggiunto valore indipendente dal mercato) → **vero edge**

### Metriche per-trade

- **market (β)**: `beta × spx_return(entry→exit)` — quanto è spiegato dal mercato
- **sector**: `sector_ETF_return - spx_return` — rotazione settoriale (solo US; EU=0)
- **alpha (residuo)**: `total - market - sector - timing` — selection edge
- **timing**: `(actual_bench_return - median_hold_bench_return) × beta` — edge del timing exit vs hold passivo

### Gate Phase 7 (quando promuovere nuove strategie)

Ogni strategia deve raggiungere:

| Criterio | Soglia |
|----------|--------|
| Trade chiusi | ≥ 15 |
| Profit factor | ≥ 1.3 |
| Sharpe (trade-level) | ≥ 0.8 |
| Win rate momentum | ≥ 50% |
| Win rate contrarian | ≥ 55% |
| Max drawdown | ≥ -15% |
| Correlation con SPX | ≤ 0.70 |

Se dopo 6 mesi una strategia non raggiunge queste soglie, **si ritira** invece di
aggiungere una strategia nuova per compensare. Questo è il gate concreto:
niente Phase 7 (nuove strategie) finché le 3 attuali non mostrano edge.

### Weekly report automatico

Il job ``weekly_attribution_report`` gira ogni **sabato 21:00 CET** e genera:

```
reports/attribution_YYYY-WW.md
```

Struttura markdown:
1. **📊 Portfolio KPIs**: total value, cash %, MTD/YTD vs SPX, alpha, max DD
2. **📈 Trade della settimana**: tabella per-trade con decomposition
3. **🎯 Per-strategy**: 30gg / 90gg / 365gg aggregati + Phase 7 gate status
4. **🌊 Per-regime breakdown**: win rate + avg P&L per regime macro (entry date)
5. **🚧 Gate detail**: strategie under threshold con failure reason esplicita
6. **⚠️ Attention**: trade con loss > 10% ultimi 30gg

Dopo la generazione, un alert ``report_ready`` viene creato → Telegram bot lo
delivera (Phase 4) → il trader riceve la notifica sabato sera mentre
pianifica la settimana.

### Comandi

```bash
# CLI on-demand (genera + stampa + salva)
propicks-report attribution

# Scheduler one-shot (backfill o manual trigger)
propicks-scheduler job attribution       # alias: job report
# Alert 'report_ready' con dedup_key per-week

# Dalla chat Telegram (Phase 4)
/report   # summary inline: per-strategy 30gg + gate status + heavy losses
```

### Architettura

```
┌────────────────┐   ┌──────────────┐   ┌───────────────────┐
│ trades (Phase 1│   │ portfolio_   │   │ regime_history    │
│  + closed P&L) │   │  snapshots   │   │  (Phase 3)        │
└───────┬────────┘   └──────┬───────┘   └─────────┬─────────┘
        │                   │                     │
        ▼                   ▼                     ▼
       ┌────────────────────────────────────────────────┐
       │  domain/attribution.py                         │
       │  - decompose_trade (con OHLCV cache Phase 2)   │
       │  - aggregate_by_strategy                       │
       │  - aggregate_by_regime                         │
       │  - strategy_gate_status (Phase 7 check)        │
       │  - portfolio_vs_benchmark                      │
       └──────────────────┬─────────────────────────────┘
                          │
                          ▼
                   ┌─────────────────┐
                   │ reports/        │
                   │  attribution_   │
                   │  YYYY-WW.md     │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐      /report
                   │ alert 'report_  │◄─────────┐
                   │  ready'         │          │
                   └────────┬────────┘          │
                            ▼                   │
                   ┌─────────────────┐          │
                   │ Telegram user   │──────────┘
                   └─────────────────┘
```

### Design highlights

- **Pure functions**: `domain/attribution.py` testabile senza rete (tutte le series sono injectable).
- **Cache-aware**: benchmark + sector ETF letti direttamente dal OHLCV cache (Phase 2). Offline-resilient.
- **Sector mapping**: solo ticker US (via `YF_SECTOR_TO_KEY` + `SECTOR_KEY_TO_US_ETF`). Per .MI/EU il sector component è 0 (skippato, non stimato) per evitare confounders.
- **Regime at entry**: il regime viene assegnato all'entry_date del trade, non exit. Un trade aperto in BULL e chiuso in BEAR resta "BULL" per attribution.
- **Timing computed on benchmark**: il timing è "hai beccato il momento giusto di USCIRE *rispetto al mercato*?", misurato via benchmark return durante actual holding vs median holding — non su ticker specifico.
- **Gate thresholds in `GATE_THRESHOLDS` dict**: modificabili centralmente se l'evidence empirica suggerisce soglie diverse. Win rate differenziato momentum vs contrarian (55% per contrarian riflette il profilo short-gamma).
- **Formatter report alert**: il bot Telegram invia un summary inline via `/report`, non il markdown completo (troppo lungo per Telegram).

### Trade-off accettati

- **No Brinson-Hood-Beebower rigorous**: un attribution professional-grade richiede weights timeseries e factor loadings rolling. Qui facciamo trade-level additive decomposition. Sufficiente per retail; insufficiente per fund-level due diligence.
- **Beta statico**: usiamo `market_ticker_meta.beta` (TTL 7gg, da Yahoo 5y monthly). Se il beta è stale o il titolo ha cambiato profile (es. acquisizione), alpha è rumoroso. Accettabile per trader retail.
- **Timing semplice**: confronta total return su actual holding vs total return su median holding sul benchmark. Ignora volatility timing e path-dependence. Sufficient per detection macroscopica.
- **Gate conservativo**: un singolo criterio che fallisce → la strategia è "fail". In realtà molte strategie passano 5/6 criteri ma falliscono 1 (es. correlation 0.72 vs soglia 0.70). Il report mostra tutti i failure esplicitamente per decisione informata.

## Telegram bot (post Phase 4)

Consuma la queue ``alerts`` generata dallo scheduler e invia push notifications
via Telegram + accetta comandi bidirezionali dal bot. **Dep opzionale**:
``pip install -e '.[telegram]'`` (richiede ``python-telegram-bot>=20``).

### Setup BotFather (one-time)

```
1. Telegram → @BotFather → /newbot
2. Scegli nome ("Propicks Personal Bot") e username finito in _bot
3. BotFather ritorna un token tipo 1234567890:ABCdef...
4. Invia /start al tuo bot nuovo
5. Manda un messaggio al tuo bot (anche solo "ciao")
6. Apri https://api.telegram.org/bot<TOKEN>/getUpdates → prendi "chat":{"id": NUMERO}
   Alternativa: @userinfobot → /start → mostra il tuo chat_id
```

### Configurazione env (``.env``)

```bash
PROPICKS_TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
PROPICKS_TELEGRAM_CHAT_ID=123456789           # il tuo chat_id
# PROPICKS_TELEGRAM_CHAT_ID=id1,id2,id3       # CSV per multi-chat (famiglia, co-trader)
PROPICKS_TELEGRAM_POLL_INTERVAL=60             # sec tra cicli dispatcher (default 60)
```

### First setup: silenzia backlog storico

```bash
# Evita spam al primo avvio se hai già alert in DB dallo scheduler
propicks-bot mute-backlog

# Test connettività: manda 1 messaggio di conferma
propicks-bot test

# Avvia bot daemon
propicks-bot run
```

### Comandi bot (invia questi dalla chat Telegram)

| Comando | Azione |
|---------|--------|
| `/status` | Portfolio summary: cash %, posizioni aperte, P&L unrealized |
| `/portfolio` | Dettaglio per ticker con P&L % live |
| `/alerts` | Alert pending (non-ack) con ID per /ack |
| `/ack N` | Acknowledge alert N |
| `/ackall` | Mark all as read |
| `/history` | Ultimi 10 job scheduler con status/duration |
| `/cache` | Stats cache OHLCV (rows, ticker, date max) |
| `/regime` | Regime macro corrente con emoji severity |
| `/help` | Lista comandi |

### Daemon management

```bash
propicks-bot run                # foreground, Ctrl+C per fermare
# In tmux / nohup:
nohup propicks-bot run > /tmp/propicks-bot.log 2>&1 &

# macOS launchd plist (esempio):
# ~/Library/LaunchAgents/com.propicks.bot.plist
# <plist>
#   <ProgramArguments><array>
#     <string>/path/to/.venv/bin/propicks-bot</string>
#     <string>run</string>
#   </array></ProgramArguments>
#   <RunAtLoad><true/></RunAtLoad>
#   <KeepAlive><true/></KeepAlive>
# </plist>
# launchctl load ~/Library/LaunchAgents/com.propicks.bot.plist
```

### Dispatcher: semantica retry

- Alert in `alerts` con ``delivered=0`` → candidati per invio ogni ciclo
- Invio OK → ``delivered=1, delivered_at=now``
- Invio fallito → ``delivery_error='try:N|last_err'``, ``delivered=0`` (retry prossimo ciclo)
- Dopo **3 fallimenti** (`try:3|...`), l'alert viene **skippato** per evitare flood infinito
- Recovery: ``propicks-bot reset-retries`` → azzera counter di tutti i failed, retry dal prossimo ciclo

### Architettura

```
           ┌───────────────┐
           │   Scheduler   │  (Phase 3)
           │ scheduler_runs│
           └───────┬───────┘
                   │ INSERT INTO alerts (delivered=0)
                   ▼
           ┌───────────────┐       polling 60s
           │    alerts     │◄─────────────────┐
           │   (SQLite)    │                  │
           └───────┬───────┘                  │
                   │ SELECT WHERE delivered=0 │
                   ▼                          │
           ┌───────────────┐                  │
           │   Dispatcher  │──────────────────┘
           │ (notifications│
           │   /dispatcher)│ UPDATE delivered=1
           └───────┬───────┘
                   │ send_message()
                   ▼
           ┌───────────────┐       /status /alerts /ack
           │ Telegram Bot  │◄─────────────────┐
           │ (async poll)  │                  │
           └───────┬───────┘                  │
                   ▼                          │
           ┌───────────────┐                  │
           │  User's phone │──────────────────┘
           └───────────────┘
```

### Sicurezza

- **Token** resta in ``.env`` (gitignored)
- **Chat whitelist**: i comandi da chat non in ``PROPICKS_TELEGRAM_CHAT_ID`` sono ignorati silenziosamente (no ack, no error)
- **No inbound webhook**: polling-based, nessun server esposto pubblicamente
- **Scheduler → DB → Bot**: loose coupling. Lo scheduler non sa di Telegram. Il bot non sa dei job. Si parlano via tabella ``alerts``.

### Operations quotidiane

```bash
propicks-bot stats                      # quanti alert pending/delivered/failed
propicks-bot reset-retries              # reset counter per recovery
propicks-bot reset-retries --alert-id 42  # solo uno specifico
propicks-bot mute-backlog               # flag pending come delivered (setup)
```

### Trade-off accettati

- **Nessun rate limiting attivo**: python-telegram-bot gestisce il rate limit API di Telegram (30 msg/sec), ma se accumuli 100+ alert pending il dispatcher li invia tutti nel ciclo — possibile batching spam. Accettabile per trader retail (normalmente <10 alert/giorno).
- **Delivery non garantita cross-instance**: se lanci 2 bot daemon con stesso token, ciascuno processerà gli alert e finirai per ricevere doppio. Un solo daemon per DB (stessa regola del scheduler).
- **Command args quotate male**: parsed da python-telegram-bot come lista di token. `/ack 42` → args=["42"]. Niente string parsing avanzato: i comandi sono intenzionalmente semplici.

## Scheduler + alerts (post Phase 3)

Automazione EOD via **APScheduler** (daemon) + **cron-callable jobs**.
Ogni job idempotente, UPSERT-based, con audit trail in ``scheduler_runs``
e alert queue in ``alerts``. Zero notifiche esterne (sono Phase 4 Telegram).

### I 7 job

| Job | Trigger default | Azione | Alert generati |
|-----|-----------------|--------|----------------|
| ``warm_cache`` | Mon-Fri 17:45 | Prefetch daily+weekly per portfolio + watchlist + benchmarks | — |
| ``record_regime`` | Mon-Fri 18:00 | Classify ^GSPC weekly, UPSERT regime_history | ``regime_change`` |
| ``snapshot_portfolio`` | Mon-Fri 18:30 | Mark-to-market, exposure per bucket, MTD/YTD, benchmark SPX+FTSEMIB | — |
| ``scan_watchlist`` | Mon-Fri 18:30 | Score live, populate strategy_runs, READY detection | ``watchlist_ready`` |
| ``trailing_stop_check`` | Mon-Fri 18:30 | Suggest trailing stop update su posizioni con trailing_enabled | ``trailing_stop_update``, ``stale_position`` |
| ``weekly_attribution_report`` | Sat 21:00 | Phase 9: decomposition α/β/sector/timing + Phase 7 gate check | ``report_ready`` |
| ``cleanup_stale_watchlist`` | Sun 20:00 | Flag watchlist entries > 60gg + contrarian near-cap warning | ``stale_watchlist``, ``contra_near_cap`` |

### Modalità operative

**Daemon (sessione always-on)**:
```bash
propicks-scheduler run        # bloccante, tz=Europe/Rome
```
Run in tmux/nohup per persistenza. SIGINT / SIGTERM → shutdown grazioso.

**Cron-callable (desktop-only, no daemon)**:
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

### Alert workflow

```bash
propicks-scheduler alerts             # lista pending con badge severity
propicks-scheduler alerts --stats     # aggregate per type/severity
propicks-scheduler alerts --ack 42    # acknowledge singolo
propicks-scheduler alerts --ack-all   # mark all as read
```

Dedup: ogni alert ha ``dedup_key`` (es. ``AAPL_ready_2026-04-24``). Se
un alert con stesso key è già pending, il secondo ``create_alert`` no-op.
Questo evita spam quando warm_cache alle 17:45 e scan_watchlist alle
18:30 producono lo stesso READY alert. Gli alert già **acknowledged**
non bloccano la creazione di nuovi con stesso key (permette
ri-triggerare "ready" settimana dopo).

### Audit trail

```bash
propicks-scheduler history             # ultimi 20 run con status/duration
propicks-scheduler history --days 7    # stats aggregate ultimi 7gg
```

Ogni job logga in ``scheduler_runs``: ``started_at``, ``finished_at``,
``status`` (success/error/partial), ``duration_ms``, ``n_items``
processati, ``error`` + traceback se fallito. Abilita:

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

### Benchmark misurati (dati reali)

- ``warm_cache``: 6.0s su 11 ticker (portfolio 1 + watchlist 7 + benchmarks 3)
- ``scan_watchlist``: 5.5s primo run (miss cache residui), **78ms** seconda run (tutto hit cache Phase 2)
- ``record_regime``: 1.2s
- ``snapshot_portfolio``: 2.6s (include fetch benchmark SPX + FTSEMIB)

### Design choices

- **APScheduler BlockingScheduler** + ``CronTrigger`` tz-aware (Europe/Rome). Nessun JobStore persistente — il daemon è stateless, riavvii non perdono cron schedule (sono hardcoded in runner.py).
- **Idempotenza via UPSERT** su ``portfolio_snapshots``, ``regime_history``. Rigirare un job lo stesso giorno aggiorna, non duplica.
- **Idempotenza via dedup_key** su ``alerts``. Rigirare scan_watchlist 3 volte al giorno non spam.
- **Non auto-apply** modifiche di stop/target: il trader resta il decision-maker. I job generano alert informativi; l'applicazione passa da ``propicks-portfolio manage --apply``.
- **Scheduler non è AI-aware**: i job ``scan_watchlist`` e ``trailing_stop_check`` NON chiamano Claude. Risparmio tokens: la validazione AI resta on-demand via flag ``--validate`` dei CLI.

### Trade-off accettati

- **Manual cron wiring**: nessun installer automatico (launchd plist / systemd unit). Il trader copia-incolla le righe crontab dalla doc. Motivo: `launchctl load` richiede permessi e formato XML platform-specific — out-of-scope per MVP.
- **Nessuna retry**: se un job fallisce, resta errore registrato in scheduler_runs, niente retry automatico. Il prossimo trigger giornaliero è il retry naturale. Per fallimenti consecutivi, guardare ``history --days 3``.
- **Nessun lock cross-process**: due daemon in parallelo duplicherebbero scheduler_runs. Non abbiamo PID lock file. Il trader responsabilizzato sull'avvio unico.

## Market data cache (post Phase 2)

Cache **read-through con TTL** per yfinance. Tutti i calls via
``market/yfinance_client.py`` passano dalla cache. Benchmark reali:

- **Scan singolo ticker**: 3.0s → 0.42s (**speedup 7×**)
- **Scan batch 3 ticker**: 4.5s → 0.44s (**speedup 10×**)

Tabelle (in ``data/propicks.db``, stessa SQLite di Phase 1):

| Tabella | Contenuto | TTL | Miss behavior |
|---------|-----------|-----|--------------|
| ``market_ohlcv_daily`` | bar daily (PK ticker, date) | 8h | fetch yfinance.Ticker.history + UPSERT |
| ``market_ohlcv_weekly`` | bar weekly (PK ticker, week_start) | 7gg | fetch interval=1wk + UPSERT |
| ``market_ticker_meta`` | sector, beta, name (PK ticker) | 7gg | fetch yfinance.Ticker.info + UPSERT |

**TTL rationale**: 8h daily copre una sessione intera (scan alle 9am e alle
3pm riusano lo stesso set). 7gg weekly è stabile post-Fri close. 7gg meta
perché Yahoo aggiorna beta settimanale.

**Public API invariata**: ``download_history``, ``download_weekly_history``,
``download_benchmark``, ``download_benchmark_weekly``, ``get_ticker_sector``,
``get_ticker_beta``, ``get_current_prices``, ``download_returns`` —
firme identiche al pre-cache. CLI/domain/dashboard non cambiano.

**CLI ``propicks-cache``** per operations:

```bash
propicks-cache stats                    # righe totali + range date
propicks-cache warm AAPL MSFT NVDA      # prefetch daily+weekly
propicks-cache warm AAPL --force        # invalida + refetch
propicks-cache clear --ticker AAPL      # rimuovi solo AAPL
propicks-cache clear --all              # wipe totale (ricrea al primo scan)
propicks-cache clear --stale            # solo righe fuori TTL
propicks-cache clear --interval daily   # solo una granularità
```

**Offline resilience**: se la cache è popolata e fresh, uno scan completo
funziona senza rete. Test: `propicks-cache warm` + disconnetti WiFi +
`propicks-scan` → funziona fino a scadenza TTL (8h daily).

**Data quality**: il cache drop rows con `Close IS NULL` (skip silenzioso
dei bar yfinance parziali). `PRIMARY KEY (ticker, date)` previene
duplicati da fetch ripetuti. UPSERT aggiorna i bar esistenti (refresh
garantito sui close revisionati post-market).

## Storage — SQLite (post Phase 1)

Source of truth: **`data/propicks.db`**. Zero file JSON nel runtime. Schema DDL
in `src/propicks/io/schema.sql`, 9 tabelle:

| Tabella | Scopo | Ruolo |
|---------|-------|-------|
| `positions` | Stato posizioni aperte (PK ticker) | CRUD via `portfolio_store` |
| `portfolio_meta` | KV singleton (cash, initial_capital, last_updated) | Scritto da `portfolio_store` e `watchlist_store` |
| `trades` | Journal append-only (PK auto, mai deleted) | CRUD via `journal_store` |
| `watchlist` | Incubatrice idee (PK ticker) | CRUD via `watchlist_store` |
| `strategy_runs` | Ogni `propicks-scan`/`contra`/`rotate` produce 1 riga | TODO: populating in next phase |
| `ai_verdicts` | Cache + storia verdict Claude (sostituisce data/ai_cache/) | `io/db.py::ai_verdict_cache_*` helpers |
| `daily_budget` | Counter giornaliero spesa AI (UPSERT by date) | `ai/budget.py` |
| `regime_history` | Snapshot giornaliero regime ^GSPC | TODO: popolato da scheduler Phase 3 |
| `portfolio_snapshots` | Equity curve + exposure per strategia (daily) | TODO: popolato da scheduler Phase 3 |
| `schema_version` | Versioning per future migrations DDL | Init a v1 |

**Connection model**: `io/db.py::connect()` apre una connessione per chiamata
(SQLite file locale = open nanosecond-fast). WAL mode + foreign keys ON.
`transaction()` context manager per atomicità di mutazioni multi-row.
Niente PARSE_DECLTYPES: tutti i timestamp / date sono TEXT ISO-formatted.

**Migrazione**: `propicks-migrate` legge i JSON legacy e popola le tabelle.
Idempotente (skip tabella se già popolata). Rinomina i JSON originali a
`.json.bak` per recovery. Backup della folder `ai_cache/` → `ai_cache.bak/`.

**Test isolation**: conftest.py ha fixture autouse `_isolate_db` che monkeypatcha
`config.DB_FILE` su `tmp_path`. Ogni test ha DB ephemeral fresco. Nessun test
tocca mai il DB reale.

## Comandi Principali

Sette entry points definiti in `pyproject.toml` (sei CLI + dashboard).
Funzionano da qualsiasi cwd dopo l'install editable: i path di `data/` e
`reports/` sono ancorati alla root del progetto (ricerca `pyproject.toml`).
La dashboard Streamlit è **parallela** alla CLI, non la sostituisce — stessa
business logic, UI diversa.

```bash
# Analisi tecnica di un ticker
propicks-scan AAPL

# Analisi multipla (batch dal basket Pro Picks)
propicks-scan AAPL MSFT NVDA AMZN --strategy TechTitans

# Output JSON o tabella compatta
propicks-scan AAPL --json
propicks-scan AAPL MSFT --brief

# Validazione AI della tesi (gate doppio: score >= 60 E regime weekly >= NEUTRAL)
propicks-scan AAPL --validate
propicks-scan AAPL --force-validate   # bypassa gate (score + regime) e cache

# Calcolo position size per un trade
propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \
  --score-claude 8 --score-tech 75

# Stato del portafoglio e rischio aggregato (risk include esposizione settori,
# beta-weighted vs SPX, top pair correlate >= 0.7)
propicks-portfolio status
propicks-portfolio risk

# Aprire / aggiornare / rimuovere una posizione
propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \
  --target 210 --strategy TechTitans
propicks-portfolio update AAPL --stop 180 --target 215
propicks-portfolio remove AAPL

# Trade management — trailing stop ATR-based + time stop su posizioni aperte
propicks-portfolio trail enable AAPL          # abilita trailing su AAPL
propicks-portfolio manage                     # dry-run: mostra suggerimenti
propicks-portfolio manage --apply             # scrive nuovi stop su portfolio.json
propicks-portfolio manage --atr-mult 2.5 --time-stop 20 --apply

# Registrare un nuovo trade
propicks-journal add AAPL long --entry-price 185.50 --entry-date 2026-01-15 \
  --stop 171.50 --target 210 --score-claude 8 --score-tech 75 \
  --strategy TechTitans --catalyst "Beat earnings Q4, guidance raised"

# Chiudere un trade
propicks-journal close AAPL --exit-price 208.30 --exit-date 2026-02-10 \
  --reason "Target raggiunto"

# List e metriche aggregate
propicks-journal list
propicks-journal list --open
propicks-journal list --closed --strategy TechTitans
propicks-journal stats
propicks-journal stats --strategy TechTitans

# Report settimanale e mensile (stampa + salva in reports/)
propicks-report weekly
propicks-report monthly
propicks-report attribution           # Phase 9: decomposition α/β/sector/timing
                                       # salva in reports/attribution_YYYY-WW.md

# Rotazione settoriale ETF — ranking dell'universo Select Sector SPDR / UCITS
propicks-rotate                         # US universe, top 3
propicks-rotate --region EU             # SPDR UCITS su Xetra (ZPD*.DE)
propicks-rotate --region WORLD          # Xtrackers MSCI World sector (XDW*/XWTS/XZRE)
propicks-rotate --top 5 --allocate      # top 5 + proposta allocazione
propicks-rotate --validate              # validazione macro via Claude (on-demand)
propicks-rotate --force-validate        # bypassa skip in STRONG_BEAR e cache 48h
propicks-rotate --json --allocate       # output JSON completo

# Backtest walk-forward (validazione storica strategia single-stock)
propicks-backtest AAPL                          # default 5y, threshold 60
propicks-backtest AAPL MSFT NVDA --period 3y    # multi-ticker + aggregate
propicks-backtest AAPL --threshold 70 --json
propicks-backtest AAPL --stop-atr 2 --target-atr 3 --time-stop 20

# Watchlist (incubatrice idee tra scan e entry)
propicks-watchlist add AAPL --target 185.50 --note "pullback EMA20"
propicks-watchlist update AAPL --target 190
propicks-watchlist remove AAPL
propicks-watchlist list                         # tabella completa
propicks-watchlist list --stale                 # solo entry > 60gg
propicks-watchlist status                       # score live + distanza target + flag READY
# NB: propicks-scan aggiunge automaticamente i ticker classe A (score ≥75)
#     e classe B (60-74) alla watchlist. Per la classe A il target entry è
#     impostato al prezzo corrente (preserva target esistenti su re-scan).
#     Usa `propicks-scan TICKER --no-watchlist` per disabilitare l'auto-add.

# Contrarian — quality-filtered mean reversion (strategia parallela al momentum)
propicks-contra AAPL                           # singolo ticker
propicks-contra AAPL MSFT NVDA                 # batch
propicks-contra AAPL --validate                # validazione Claude (flush vs break)
propicks-contra AAPL --force-validate          # bypassa gate + cache
propicks-contra AAPL --json                    # output JSON
propicks-contra AAPL MSFT --brief              # tabella riassuntiva
propicks-contra AAPL --no-watchlist            # disabilita auto-add classe A+B
# Sizing bucket contrarian (cap 8%, max 3 pos, 20% aggregate):
propicks-portfolio size AAPL --entry 180 --stop 162 \
  --score-claude 7 --score-tech 65 --contrarian

# Migrazione one-shot JSON → SQLite (eseguita una volta sola in fase di setup
# post-upgrade. Idempotente: se il DB è già popolato fa no-op su quella tabella)
propicks-migrate --dry-run             # anteprima senza toccare nulla
propicks-migrate                        # esegue + backup JSON → *.json.bak

# Cache OHLCV (Phase 2) — speedup 7-10× su scan ripetuti
propicks-cache stats                    # righe totali + range date + last fetch
propicks-cache warm AAPL MSFT NVDA      # prefetch daily+weekly
propicks-cache warm AAPL --force        # invalida + refetch (garantito fresh)
propicks-cache clear --ticker AAPL      # wipe solo AAPL
propicks-cache clear --all              # wipe totale (si ricrea al primo scan)
propicks-cache clear --stale            # solo righe fuori TTL (8h daily / 7gg weekly)

# Scheduler (Phase 3) — automazione EOD + audit trail + alert queue
propicks-scheduler run                  # daemon APScheduler bloccante (Europe/Rome)
propicks-scheduler job regime           # esegue 1 job one-shot (per OS cron)
propicks-scheduler job snapshot --date 2026-04-23  # backfill snapshot specifico
propicks-scheduler alerts               # lista alert pending con severity
propicks-scheduler alerts --ack 42      # acknowledge singolo
propicks-scheduler alerts --ack-all     # mark all as read
propicks-scheduler history              # ultimi 20 job run
propicks-scheduler history --days 7     # stats aggregate per job ultimi 7gg

# Telegram bot (Phase 4) — push + comandi bidirezionali (extras [telegram])
propicks-bot test                        # test connettività: invia 1 msg
propicks-bot mute-backlog                # first-setup: flag pending come delivered
propicks-bot run                         # daemon bloccante (polling + dispatcher)
propicks-bot stats                       # counters queue delivery
propicks-bot reset-retries               # recovery dopo errori persistenti
propicks-bot reset-retries --alert-id 42 # reset solo uno specifico
# Dalla chat Telegram: /status /portfolio /alerts /ack N /history /cache /regime /help

# Test unit (solo domain/ + backtest, nessuna rete, DB SQLite ephemeral per test)
pytest

# Dashboard Streamlit (richiede `pip install -e ".[dashboard]"`)
propicks-dashboard                         # apre http://localhost:8501
# equivalente diretto:
streamlit run src/propicks/dashboard/app.py

# Docker — dashboard in container con volumi persistenti data/ e reports/
docker compose up -d                       # build + start (dashboard su :8501)
docker compose logs -f dashboard           # stream log
docker compose down                        # stop (volumi preservati)
```

### Mappatura CLI ↔ dashboard

| CLI                              | Dashboard page                  |
|----------------------------------|---------------------------------|
| *(home — no CLI equivalent)*     | `app.py` Portfolio Overview     |
| `propicks-scan [--validate]`     | `pages/1_Scanner.py`            |
| `propicks-rotate [--region]`     | `pages/2_ETF_Rotation.py`       |
| `propicks-portfolio size/add/update/remove` | `pages/3_Portfolio.py` (tabs base) |
| `propicks-portfolio risk` (rischio + esposizione) | `pages/3_Portfolio.py` → tab "Rischio & esposizione" |
| `propicks-portfolio manage [--apply]` / `trail enable\|disable` | `pages/3_Portfolio.py` → tab "Trade management" |
| `propicks-journal add/close/list/stats` | `pages/4_Journal.py`       |
| `propicks-report weekly/monthly` | `pages/5_Reports.py` + archivio |
| `propicks-backtest`              | `pages/6_Backtest.py`           |
| `propicks-watchlist add/remove/update/list/status` | `pages/7_Watchlist.py` |
| `propicks-contra [--validate]`   | `pages/8_Contrarian.py`         |

## Regole di Business (Invarianti)

Queste regole sono hardcoded e NON devono essere aggirate:

- **Max posizioni aperte**: 10 (shared cap, include momentum + contrarian + ETF)
- **Max size singola posizione**: 15% capitale (stock momentum) / 20% (sector ETF) / **8% (contrarian)**
- **Max esposizione aggregata sector ETF**: 60% del capitale
- **Max esposizione aggregata contrarian**: **20% del capitale** (bucket cap indipendente)
- **Max posizioni contrarian simultanee**: **3** (cap interno al bucket)
- **Min cash reserve**: 20% del capitale
- **Max loss per trade**: 8% (stock momentum) / 5% (sector ETF) / **12% (contrarian, stop più largo a -3×ATR)**
- **Max loss settimanale**: 5% del capitale totale → blocco trading
- **Max loss mensile**: 15% del capitale totale → blocco trading e revisione
- **No entry se earnings entro 5 giorni** (warning, non blocco — il trader decide)
- **Score minimo per entry**: Claude >= 6/10, Tecnico >= 60/100
- **Regime weekly minimo per validazione AI stock momentum**: NEUTRAL (code >= 3). BEAR/STRONG_BEAR skippano `--validate`.
- **Regime gate contrarian (INVERSO)**: `--validate` skippa STRONG_BULL (5) e STRONG_BEAR (1) — edge collassa agli estremi. Override con `--force-validate`.
- **Regime hard gate ETF rotation**: in STRONG_BEAR (1) i settori non favoriti sono forzati a score 0; in BEAR (2) sono capped a 50. In NEUTRAL+ il ranking è libero.

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
- **`market/`** è l'unico modulo che parla con yfinance/rete. Se in futuro si
  cambia provider, si tocca solo qui.
- **`ai/`** è l'unico modulo che parla con l'SDK Anthropic (parallelo a
  `market/`). Espone `validate_thesis(analysis)` che ritorna un dict strutturato;
  nessun altro layer importa `anthropic` direttamente. Include gate su
  `score_composite` **e sul regime weekly** (skip se BEAR/STRONG_BEAR),
  cache giornaliera in tabella `ai_verdicts` e tool `web_search`
  server-side per dati real-time (spot, earnings, news).
- **`reports/`** può importare da tutti gli altri layer per comporre i markdown.
- **`cli/`** è thin: parsing argparse + chiamata a funzioni di domain/io/ai/reports
  + formatting tabellare. Nessuna logica di business qui.
- **`dashboard/`** è thin come `cli/`: UI Streamlit che chiama le stesse funzioni
  di `domain/io/ai/reports`. Nessuna logica di business qui. Cached readers in
  `_shared.py` (`cached_analyze`, `cached_rank`, `cached_current_prices`) per
  evitare refetch yfinance a ogni rerun. Quando le colonne di un `st.dataframe`
  possono contenere valori `None` o sentinel `"—"`, serializzarle come stringhe
  omogenee (altrimenti PyArrow fallisce la conversione a double).
- **`tradingview/`** NON è Python — sono Pine script che replicano visualmente
  il motore. Il Pine è il layer real-time (timing + alert) che yfinance (EOD)
  non copre. I parametri di default devono matchare `config.py` byte per byte.
  Contract documentato in testa a entrambi i file `.pine`.

## Workflow di Integrazione con AI

Il sistema è progettato per essere usato in combinazione con prompt AI:

1. **Scanner** → produce output strutturato che il trader incolla nel prompt Claude 3A
2. **Portfolio status** → produce la tabella che il trader incolla nel prompt Claude 3B
3. **Journal stats** → dati per il prompt Claude 3D (post-trade analysis)
4. **Report** → sommario formattato da usare come contesto per qualsiasi prompt

In alternativa al copia/incolla manuale, `propicks-scan --validate` chiama
direttamente l'API Anthropic (`claude-opus-4-6` di default) e restituisce un
verdict strutturato (CONFIRM/CAUTION/REJECT) validato via pydantic. Il prompt di
sistema è statico (prompt caching abilitato), il contenuto dinamico viene
inserito nel user prompt per non invalidare la cache lato server.

**Il prompt Perplexity resta in pipeline come cross-check indipendente**:
il prompt 2C (check news/earnings ultime 24h) viene eseguito manualmente
prima dell'entry anche se Claude ha già dato CONFIRM. Perplexity e Claude
hanno fonti e bias diversi — la ridondanza è intenzionale, non overhead.

## Pipeline end-to-end (Perplexity → Python → TradingView)

La pipeline è **manuale** ma con contract rigidi tra gli stadi:

```
Pro Picks (mensile)
  → Perplexity 2A/2B (news + catalyst, cross-check fondamentale)
  → propicks-scan --validate  ← regime weekly + score + verdict Claude
  → copy/paste del blocco TRADINGVIEW PINE INPUTS nei settings del Pine daily
  → Pine daily (timing real-time: BRK/PB/GC/SQZ/DIV → alert push)
  → Perplexity 2C (check red flag ultime 24h)
  → propicks-portfolio size + add
  → propicks-journal add
```

**Consistency garantita da:**
- `domain/regime.py` = replica Python del Pine weekly (stessa classificazione 5-bucket)
- `tradingview/*.pine` hanno header che punta a `config.py` come source of truth per EMA/RSI/ATR/volume/soglie
- `propicks-scan` stampa sempre il blocco Pine-ready a fine output così il trader copia-incolla i livelli invece di digitarli
- Il gate regime in `validate_thesis` impedisce chiamate Claude quando il Pine weekly direbbe NO ENTRY

## Peer Relative Strength (stock vs sector ETF)

`analyze_ticker` arricchisce l'output con il campo **`rs_vs_sector`** (dict
con `score`/`rs_ratio`/`rs_slope`/`peer_etf`) — la forza relativa del titolo
contro il proprio Select Sector SPDR. Serve a distinguere i leader del
settore dai passeggeri del trend: NVDA +40% YTD vs SPX dice poco se l'intero
XLK ha fatto +35%.

**Gating architetturale:**
- **Solo US tickers** (`domain.stock_rs.is_us_ticker`). Per `.MI`/`.DE`/`.L`/`.PA`/...
  il campo è `None`: la rotazione geografica inquinerebbe il segnale di peer
  RS (es. ISP.MI vs XLF US mescola banche italiane e banche USA).
- Mapping GICS via `yf.Ticker(t).info['sector']` → `SECTOR_KEY_TO_US_ETF`
  (Technology→XLK, Energy→XLE, ecc.). La taxonomy Yahoo differisce da GICS
  puro (es. "Consumer Cyclical" per "Consumer Discretionary"): vedi
  `YF_SECTOR_TO_KEY` per le normalizzazioni.
- Engine: riuso diretto di `etf_scoring.score_rs` (stessa formula level×slope
  su 26w / EMA10w). Nessuna duplicazione di logica.

**Informativo, non nel composite:** il campo **non** entra nello score
tecnico 0-100 del titolo. Calibrare un 7° sub-score richiederebbe
ri-validare i pesi esistenti sui trade storici. Se emerge correlazione
forte tra `rs_vs_sector.score` alto e winner nel journal, si può
promuovere a sub-score con reshuffling dei pesi.

**Overhead:** aggiunge 2 chiamate yfinance per ticker US (`.info` + weekly
del peer ETF). Su batch scan grandi ci sono ripetizioni — se diventa un
collo di bottiglia, cache del weekly ETF al livello di CLI/dashboard (9
download invece di N×9). Per ora non cachato: yfinance client resta thin.

## Strategia ETF Settoriali (parallela agli stock)

Il framework supporta due strategie parallele che condividono `regime.py` ma
divergono su scoring e validazione. Il branch è determinato dal ticker via
`domain.etf_universe.get_asset_type`:

- **STOCK** → flow esistente (`analyze_ticker` → tesi aziendale → Claude)
- **SECTOR_ETF** → flow rotazione (RS vs benchmark + regime fit → Claude macro)

### Universo ETF (Fase 1 completata)

Tre universi paralleli in `config.py`, selezionabili via `--region`:

1. **US** — `SECTOR_ETFS_US`: Select Sector SPDR (11 settori GICS, tickers `XL*`)
2. **EU** — `SECTOR_ETFS_EU`: SPDR S&P U.S. Select Sector UCITS (`ZPD*.DE` su
   Xetra). Wrapper UCITS degli stessi Select Sector Index US — esposizione
   identica, solo wrapper irlandese accumulating. Tesi di rotazione unica con
   US; il trader sceglie il listing in base a fiscalità e liquidità.
3. **WORLD** — `SECTOR_ETFS_WORLD`: Xtrackers MSCI World sector UCITS
   (serie `XDW*.DE`, più `XWTS.DE` per communications e `XZRE.DE` per real
   estate). Perimetro MSCI World (developed markets, ~65-70% US + ~15% Europa
   + ~6% Giappone), non è un mirror dei SPDR — settori world includono nomi
   europei/giapponesi con dinamica diversa (es. energy con Shell/TotalEnergies
   vs Chevron/Exxon puri US). Tesi di rotazione globale separata.

Eccezioni:
- `XLRE` non ha SPDR US Real Estate Select Sector UCITS equivalente
  (`eu_equivalent=None`). Serie WORLD invece copre real_estate via `XZRE.DE`
  (lanciato 2021 post-GICS reshuffle, ISIN separato dalla serie XDW* core).
- `XWTS.DE` è l'outlier naming della serie WORLD (communications); riflette
  il GICS 2018 reshuffle, include Meta/Alphabet/Netflix come XLC US.

**Verifica ticker prima del primo uso**: i listing Xetra `ZPD*` e `XDW*` sono
accumulating (IE-domiciled). Alcuni broker retail EU non quotano `XWTS` o
`XZRE` su Xetra — fallback su listing Milano (`.MI`) se disponibile. Varianti
distributing su LSE hanno ticker diversi e non sono registrate qui.

**Benchmark RS per region** (vedi `config.get_etf_benchmark`):
- US/EU → `^GSPC` (coerente con Select Sector Index)
- WORLD → `URTH` (iShares MSCI World ETF, stesso perimetro dei XDW*)

Mischiare benchmark e universo confonde outperformance settoriale con
differenze di perimetro geografico. `rank_universe` sceglie automaticamente
il benchmark giusto. Il regime classifier resta sempre su `^GSPC` anche per
WORLD (correlazione S&P/MSCI World ≈ 0.95 weekly giustifica l'approssimazione
e `REGIME_FAVORED_SECTORS` è US-calibrata).

### Regime → Settori Favoriti

Tabella opinabile in `config.py::REGIME_FAVORED_SECTORS` — view ciclica
classica (early→late cycle → defensives → capital preservation). Va rivista
a ogni regime change verificando che i leader reali confermino la tabella.

| Regime | Settori favoriti |
|--------|------------------|
| 5 STRONG_BULL | tech, consumer disc., comms, financials, industrials |
| 4 BULL        | tech, consumer disc., industrials, materials, financials |
| 3 NEUTRAL     | healthcare, industrials, financials, tech |
| 2 BEAR        | consumer staples, utilities, healthcare |
| 1 STRONG_BEAR | consumer staples, utilities |

### Scoring engine ETF (Fase 2 completata)

`domain/etf_scoring.py` è il parallelo di `domain/scoring.py` ma con una
formula diversa — il problema è diverso. Sugli ETF settoriali non ha senso
cercare pullback vicino all'ATH di un single-name: si cerca *leadership
relativa* e *fit col regime macro*.

**Formula composite (0-100):**

```
composite_etf = RS * 0.40 + regime_fit * 0.30 + abs_momentum * 0.20 + trend * 0.10
```

- **RS vs benchmark (40%)** — `close(ETF)/close(^GSPC)` normalizzato su 26 weeks,
  poi slope sulla EMA(10 weeks) della RS line. Leader in accelerazione = 100.
  Ex-leader in distribuzione (RS alto ma slope negativo) = 55. Lagger in
  distribuzione = 10.
- **Regime fit (30%)** — lookup su `REGIME_FAVORED_SECTORS`. Favorito nel regime
  corrente = 100, favorito nel regime adiacente (transizione) = 60, non
  favorito = 20, regime ignoto = 50.
- **Absolute momentum (20%)** — perf 3M del settore (non RS, assoluto).
  +15%+ = 100, scala a step fino a -5%+ = 10.
- **Trend (10%)** — price vs EMA30 weekly (stesso livello del regime classifier)
  + slope EMA a 4 settimane. Price sopra EMA in salita = 100.

**Regime hard-gate (architetturale):** oltre al peso 30% nella formula, il
regime applica un cap superiore allo score dei settori non favoriti —
`domain.etf_scoring.apply_regime_cap`:

- STRONG_BEAR + non-favored → score forzato a **0** (no long ciclicali in crisi)
- BEAR + non-favored → score capped a **50** (no overweight cicliche)
- NEUTRAL+ → nessun cap, ranking libero

Questo evita che un XLK con momentum forte esca top-ranked in un regime
di drawdown — coerente col gate regime già usato in `validate_thesis`.

### CLI `propicks-rotate`

Entry point dedicato (non un branch di `propicks-scan`): la rotazione è un
workflow diverso dal setup single-stock e merita un comando suo.

```bash
propicks-rotate                        # US (SPDR Select Sector), top 3
propicks-rotate --top 5                # US, top 5
propicks-rotate --region EU            # SPDR UCITS (ZPD*.DE)
propicks-rotate --region WORLD         # Xtrackers MSCI World (XDW*/XWTS/XZRE)
propicks-rotate --allocate             # include proposta allocazione
propicks-rotate --validate             # validazione macro via Claude
propicks-rotate --json                 # output JSON
```

**Output:** tabella ranking 11 settori con score + sub-score + RS ratio +
perf 3M + classification (A OVERWEIGHT, B HOLD, C NEUTRAL, D AVOID) +
dettaglio del top-pick. Con `--allocate`: proposta equal-weight 20% per
ETF sui top-N, capped al 60% aggregato.

### Portfolio construction ETF

`suggest_allocation` codifica le regole di costruzione:

- **NEUTRAL+**: top-N (default 3) equal-weight 20% ciascuno, cap aggregato 60%
- **BEAR**: top-1 difensivo, 15% max (N ridotto automaticamente)
- **STRONG_BEAR**: allocazione vuota (flat, cash)
- Esclusi classi C (NEUTRAL) e D (AVOID) dalla selezione

La rotazione a tranche su regime change (2-3 tranche su 5 sessioni) è una
regola operativa manuale — non ancora codificata nello store.

### AI validation ETF (on-demand, non default)

Parallelo a `ai/thesis_validator.py` ma con assunzioni diverse:

- **`ai/etf_prompts.py`** — `ETF_SYSTEM_PROMPT` da macro strategist, non da
  equity analyst. Zero focus su earnings / moat / unit economics. Focus su
  macro drivers (yields, DXY, commodities), breadth, positioning, rotation
  stage, flows. Web search mirata: spot macro, ETF flows, sector breadth
  — NON earnings date.
- **`ai/etf_validator.py::validate_rotation`** — cache 48h (vs 24h stock: la
  view macro si muove più lenta), chiave `(region, regime_code, YYYY-MM-DD)`.
  Skip automatico in STRONG_BEAR (la risposta è ovvia: flat), override con
  `--force-validate`.
- **Schema verdict** — `ETFRotationVerdict` in `ai/claude_client.py` con
  campi diversi: `top_sector_verdict`, `alternative_sector`, `stage`
  (EARLY/MID/LATE), `rebalance_horizon_weeks`, `entry_tactic`
  (ALLOCATE_NOW / STAGGER_3_TRANCHES / WAIT_PULLBACK / ...). Niente
  `reward_risk_ratio`: su rotation settoriale non ha senso.
- **Non default**: `--validate` su `propicks-rotate` è opt-in. La rotazione
  weekly è meno sensibile al noise qualitativo — spendere token ogni
  weekly rebalance è eccessivo. Usare quando c'è un regime change o una
  decisione di entry con size rilevante.

### Invarianti ETF

- ETF settoriali: max 20% del capitale (vs 15% dei single-stock)
- Rotazione graduale: cambio regime BULL→BEAR = uscita in 2-3 tranche su 5
  sessioni per evitare whipsaw (regola operativa, non ancora codificata)
- Nessun ETF futures-based (USO, UNG, DBC): contango decay incompatibile con
  holding > 2 settimane — non verranno mai aggiunti all'universo

### Thematic ETF (fuori scope di `propicks-rotate`)

I tematici (semis SMH/SOXX, biotech XBI/IBB, cybersecurity CIBR/BUG, AI &
robotica ROBO/BOTZ, clean energy ICLN/TAN, KWEB, XAR/ITA) **non sono parte
dell'universo ETF rotation** e non vanno aggiunti a `SECTOR_ETFS_*`. Tre
ragioni architetturali:

1. **Violano l'invariante GICS-mutuamente-esclusivi** della rotation:
   SMH ≈ 70% top-10 di XLK, XBI ≈ 60% biotech-pesante di XLV. Inserirli
   nello stesso universo significa avere doppio bet camuffato da
   diversificazione, e l'allocator equal-weight non sa che due posizioni
   diverse hanno la stessa scommessa sottostante.
2. **Non mappano su `REGIME_FAVORED_SECTORS`**: semis è sub-industry, non
   GICS sector. Estendere la tabella regime a temi opinabili (semis è
   early-cycle? secular AI play? cyclical?) introduce rumore.
3. **L'asse RS giusto è vs parent sector, non vs `^GSPC`**: SMH che batte
   SPX è quasi tautologico in un mercato risk-on; SMH che batte XLK
   discrimina davvero.

**Approccio attuale (MVP)**: i tematici di interesse passano da
`propicks-scan` come single-stock e finiscono nel bucket satellite
(max 15%/posizione). Quattro regole auto-imposte manuali (non codate):
max 2 tematici aperti, campo `catalyst` con parent sector + peso, stop
hard 10% (non 8%, ATR% più alto), hard rule
`weight(theme) + weight(parent_sector) ≤ 25%`.

**Promozione a subpackage dedicato** (`propicks/thematic/` con scoring
RS-vs-parent + CLI `propicks-themes`) **gated da journal evidence**: dopo
15 trade tematici chiusi, promuovi solo se win rate ≥ baseline single-stock,
avg P&L > baseline + 0.5%, **e** correlation con parent sector < 0.85.
Se la corr è ≥ 0.85, sono solo leveraged sector bet senza alfa proprio →
killa l'esperimento. Stesso pattern di `rs_vs_sector`: informativo finché
i trade non giustificano la promozione.

Documentazione operativa completa in `docs/Trading_System_Playbook.md` §5B.

## Strategia Contrarian (quality-filtered mean reversion)

Motore **parallelo** alla strategia momentum/quality attuale, **additivo** —
non modifica `propicks-scan` né i pesi di `domain/scoring.py`. Il momentum
cerca forza che accelera; la contrarian compra qualità temporaneamente
oversold. Le due strategie hanno tesi opposte, e i trade sono taggati
`strategy="Contrarian"` nel journal per isolare le metriche.

### Tesi operativa

Setup valido solo se **tutti** i filtri passano:
1. **Oversold tecnico** — RSI(14) < 30 strict (o <35 warm) **e** prezzo
   ≥ 2×ATR sotto EMA50 **e** almeno 3 sedute rosse consecutive.
2. **Trend strutturale intatto** — prezzo ≥ EMA200 weekly. Sotto → hard gate:
   composite azzerato (è downtrend, non mean reversion).
3. **Market context favorevole** — VIX > 25 (paura) bonus, VIX < 14 (euforia)
   penalty. Regime weekly NEUTRAL ideale, BULL/BEAR ok, skip STRONG_*.
4. **Qualità aziendale** — universe filter (Pro Picks basket o watchlist
   curata). Enforced nel CLI / workflow trader, non nel domain puro.
5. **Fundamental non rotto** — validazione Claude `flush_vs_break`:
   FLUSH = tradable, BREAK = REJECT. Catalyst type da classificare
   (macro_flush / sector_rotation / earnings_miss_fundamental / guidance_cut /
   fraud / technical_only).

### Scoring engine (`domain/contrarian_scoring.py`)

4 sub-score ortogonali, composite 0-100:

```
composite = oversold*40% + quality*25% + market_context*20% + reversion*15%
```

- **Oversold (40%)** — RSI pts (0-40) + ATR distance pts (0-40) + consecutive
  down pts (0-20). Il massimo 100 richiede tutti e tre: RSI<30, ≥3×ATR sotto
  EMA50, ≥5 barre rosse. Un RSI a 28 ma price ancora sopra EMA50 **non**
  qualifica (potrebbe essere un dip già riassorbito).
- **Quality (25%)** — hard gate su EMA200 weekly: sotto → 0 (no mean reversion
  su trend rotto). Sopra → modulato sulla profondità della correzione
  (-10/-25% = 100, -3% = 30, -50%+ = 20).
- **Market context (20%)** — lookup `CONTRA_REGIME_FIT` + aggiustamento VIX
  (+20 se ≥25, -30 se ≤14).
- **Reversion (15%)** — R/R teorico reward=EMA50-price / risk=price-stop.
  ≥3:1 → 100, ≥2:1 → 80, sotto 1:1 → 10.

**Regime fit inverso al momentum:**

| Regime | Momentum (thesis_validator) | Contrarian (contrarian_validator) |
|--------|---------------------------|-----------------------------------|
| 5 STRONG_BULL | CONFIRM plausibile | skip (fit 25) — no vere oversold |
| 4 BULL        | tailwind               | workable (fit 70) |
| 3 NEUTRAL     | CAUTION default        | sweet spot (fit 100) |
| 2 BEAR        | REJECT default         | ok se quality regge (fit 85) |
| 1 STRONG_BEAR | skip (no entry)        | skip (fit 0) — falling knives |

### Invarianti (diverse dal momentum)

| Parametro | Momentum | Contrarian | Rationale |
|-----------|----------|------------|-----------|
| Size max per posizione | 15% | **8%** | Hit rate più basso (setup short-gamma) |
| Max posizioni simultanee nel bucket | — | **3** | Cap indipendente (share cap globale 10) |
| Max esposizione aggregata | — | **20%** | Esposizione totale contrarian ≤ 20% capitale |
| Stop loss | ATR × 2 | `recent_low - 3×ATR` | Wider, ancorato a capitulation low |
| Max loss per trade (soglia warning) | 8% | **12%** | Stop naturalmente più largo |
| Target | trailing | **EMA50 fisso** | Mean reversion = target fisso, NO trailing |
| Holding tipico | 2-8 settimane | **5-15 giorni** | Reversion rapida o thesis wrong |
| Time stop | 30 gg flat | **15 gg** | Finestra di reversion corta |

**Cap globale `MAX_POSITIONS=10` condiviso**: momentum + contrarian insieme
non possono superare 10 posizioni aperte. Il bucket contrarian ha un cap
indipendente interno (3 max).

### CLI `propicks-contra`

Entry point dedicato (parallelo a `propicks-scan`), non un flag dello
scanner. Rotazione settoriale, momentum, e contrarian sono flussi operativi
distinti e meritano comandi separati.

```bash
propicks-contra AAPL                        # singolo ticker
propicks-contra AAPL MSFT NVDA              # batch
propicks-contra AAPL --validate             # + validation Claude (flush vs break)
propicks-contra AAPL --force-validate       # bypassa gate + cache
propicks-contra AAPL --json                 # output JSON
propicks-contra AAPL MSFT --brief           # solo tabella riassuntiva
propicks-contra AAPL --no-watchlist         # disabilita auto-add classe A+B
```

**Output:** tabella dettagliata con oversold metrics (RSI / ATR distance /
consecutive down), quality gate (sopra EMA200w? distanza 52w high),
market context (VIX + regime), reversion R/R, parametri di trade proposti
(entry / stop a -3×ATR dal recent_low / target EMA50).

**Auto-watchlist**: classe A (≥75) e B (60-74) sono aggiunte con
`source="auto_scan_contra"` per tracciare separatamente le idee generate
dalla strategia contrarian nell'audit watchlist.

### AI validation contrarian (`ai/contrarian_validator.py`)

Persona del prompt: **senior event-driven / mean-reversion PM**, non
momentum trader. Focus discriminante: **flush vs break**.

- `FLUSH` → tradable mean reversion (macro_flush, sector_rotation,
  technical_only se cause verified).
- `BREAK` → non tradable (earnings_miss_fundamental, guidance_cut,
  fraud_or_accounting). Default REJECT.
- `MIXED` → size down, shorter horizon.

**Schema verdict** `ContrarianVerdict` (in `claude_client.py`):
- `verdict` CONFIRM/CAUTION/REJECT
- `flush_vs_break` FLUSH/BREAK/MIXED
- `catalyst_type` (7 categorie)
- `reversion_target` float (take-profit specifico)
- `invalidation_price` float (hard stop)
- `time_horizon_days` int 3-30
- `entry_tactic` MARKET_NOW / LIMIT_BELOW / SCALE_IN_TRANCHES / WAIT_STABILIZATION
- 5 confidence dimensions (quality_persistence, catalyst_type_assessment,
  market_context, reversion_path, fundamental_risk)

**Cache separata**: chiave `<TICKER>_contra_v1_<YYYY-MM-DD>.json` (vs
momentum `<TICKER>_v4_<YYYY-MM-DD>.json`). Lo stesso ticker può essere
scansionato da entrambe le strategie nello stesso giorno senza collisione
di verdict.

**Gate inverso**: skip STRONG_BULL (5) e STRONG_BEAR (1) — edge collassa
agli estremi. In NEUTRAL/BULL/BEAR la validation gira. Override con
`--force-validate`.

**Web search bias**: il prompt contrarian istruisce Claude a cercare
specificamente la **causa del selloff** (earnings print, guidance,
regulatory), non il catalyst forward. 3-5 query per ticker, indicazione
esplicita di scrivere "unknown — search inconclusive" se la causa non è
identificabile (mai assumere FLUSH senza evidenza).

### Sizing integration (`domain/sizing.py`)

`calculate_position_size` accetta `strategy_bucket: Literal["momentum",
"contrarian"]`. Con `"contrarian"`:
- `position_cap_pct` override a 8%
- Gate `contrarian_position_count(portfolio) < 3`
- Gate `contrarian_aggregate_exposure(portfolio) < 20%`
- `max_value` cap ulteriore sul headroom bucket (evita superamento marginale)
- `loss_threshold` warning a 12% invece di 8%

Il bucket si riconosce via `p["strategy"].lower().startswith("contra")`:
match-first-word è case-insensitive e tollera tag come "Contrarian",
"contrarian-pullback", "Contra — macro_flush".

### CLI integration `propicks-portfolio size --contrarian`

Flag opzionale su `propicks-portfolio size`:

```bash
# Size contrarian: cap 8%, verifica max 3 pos + 20% aggregate
propicks-portfolio size AAPL --entry 180 --stop 162 \
  --score-claude 7 --score-tech 65 --contrarian
```

Per `propicks-portfolio add`, il bucket è determinato implicitamente dal
tag `--strategy Contrarian`: il sizing al momento dell'`add_position` è
già stato calcolato separatamente, il portfolio store persiste la
strategia tag che poi viene letta dai gate contrarian.

### Non-goal e scope esplicito

- **No short selling**. Tutte le posizioni restano long. La contrarian
  cerca entry long asimmetriche su titoli venduti, non bet ribassisti.
- **No pair trading / long-short**. Resta universo single-leg.
- **No crypto / futures**. Universo invariato rispetto al momentum
  (stock Pro Picks + basket curato).
- **No position adding on losing trade** ("averaging down"). Se lo stop
  viene triggered, la posizione si chiude. Un nuovo setup richiede un
  nuovo trade (e un nuovo entry nel journal).
- **No rebalance automatico su regime change**. Il gate inverso è
  applicato al momento dello scan (decisione di entry), non come
  trigger di chiusura su posizioni aperte. Le posizioni contrarian
  aperte si chiudono a target EMA50, stop hard, o time stop 15gg.

## Backtest walk-forward (`propicks.backtest`)

Subpackage dedicato (non in `domain/`) perché composto da engine **+** metrics
e ha bisogno dell'adapter `market/yfinance_client` come fonte default —
mentre il `domain/` puro non importa mai da `market/`. Quando il caller
fornisce esplicitamente un `history` DataFrame, l'engine non tocca la rete:
i test girano senza HTTP.

**Engine** (`backtest/engine.py::backtest_ticker`) rigira **le stesse**
funzioni `score_*` di `domain.scoring` su ogni bar storico, point-in-time:

- Indicatori (EMA/RSI/ATR) calcolati una volta su tutta la storia, ma il
  bar i accede solo a `iloc[i]` → no lookahead.
- Composite ricalcolato bar-by-bar; sopra `threshold` (default 60) apre a
  close della stessa bar (assunzione: ordine market on close).
- Stop = entry − k×ATR, target = entry + k×ATR (default k=2 stop, k=4 target
  → R:R teorico 2.0).
- Exit priority sullo stesso bar: **stop > target** se entrambi toccati
  intraday (assunzione conservativa worst-case).
- Time stop: se trade flat (|P&L| < 2%) da `time_stop_bars` (default 30),
  exit a close.
- Posizione aperta a fine storia → forced close al last close (`exit_reason="eod"`).

**Metrics** (`backtest/metrics.py`) produce dict pronto per CLI/JSON:
win rate, profit factor, avg win/loss, expectancy per trade, max drawdown,
CAGR, Sharpe (252 trading days/anno), Sortino, exit_reason breakdown,
avg bars held. `aggregate_metrics(results)` poola i trade su batch
multi-ticker (NON è un portfolio simulato — è la pool di trade
indipendenti, utile per validare che la formula funzioni in media).

**KNOWN_LIMITATIONS** (esplicite nel docstring di `engine.py`):
- No slippage, no commissioni → fill esatto sui livelli teorici (ottimista).
- No survivorship bias correction: ticker delisted/merged non sono nel set;
  vivi sono visti come vivi anche durante drawdown storici.
- Earnings gap non filtrati: stop gappato post-earnings viene compilato a
  stop level invece che al gap-down reale → sottostima della loss.
- Position sizing: full-cash ogni trade, 1 posizione/ticker. Niente
  cross-ticker correlation budget.

**CLI** (`propicks-backtest`): walk-forward su uno o più ticker, output
tabellare + tabella trade-by-trade + ASCII equity curve, oppure `--json`.

**Scopo dichiarato**: validare che il *segno* dei pesi e dei sub-score sia
corretto (la strategia genera expectancy positiva su un universo di
ticker liquidi), non produrre un'equity curve da prendere literally
come previsione futura. Per la calibrazione fine dei pesi serve
walk-forward con out-of-sample split + significance test (TODO v1.6).

## Trade management (trailing + time stop)

`domain/trade_mgmt.py` è puro: prende numeri/stringhe e ritorna dict di
suggerimenti. L'applicazione (update di `portfolio.json`) è responsabilità
della CLI (`propicks-portfolio manage --apply`).

**Trailing stop ATR-based, ratchet-up only.** Logica:
- Stop iniziale resta invariato finché `highest_price < entry + 1R`
  (1R = `entry - initial_stop`). Rationale: muovere lo stop troppo presto
  trasforma uno swing legittimo in stop-out rumoroso.
- Sopra soglia: `proposed = highest - atr_mult * current_atr`
  (default `atr_mult=2.0`). Il nuovo stop è `max(current, proposed)`:
  **mai scende**.

**Time stop**: se trade flat (`|P&L%| < flat_threshold_pct`, default 2%)
da almeno `max_days_flat` giorni (default 30) → suggerisci chiusura.
Rationale: il costo-opportunità di tenere capitale fermo è reale anche
se il P&L mark-to-market è nullo.

**Schema portfolio esteso (backward-compatible):**
- `highest_price_since_entry: float | None` — tracking del massimo
  raggiunto post-entry, aggiornato a ogni `manage` run
- `trailing_enabled: bool` — opt-in esplicito tramite `propicks-portfolio
  trail enable <TICKER>`. Default OFF: il trader decide caso per caso
  quali setup meritano trailing (momentum) vs hard stop (mean-reversion).

`suggest_stop_update(position, current_price, current_atr, ...)` orchestra
trailing + time stop e ritorna `{new_stop, stop_changed, time_stop_triggered,
highest_price, rationale: list[str]}`. Il `manage --apply` applica solo
`stop_loss` e `highest_price`; le posizioni con `time_stop_triggered=True`
vanno chiuse manualmente (l'engine non scrive il close per evitare
chiusure accidentali su trade marginali — il trader vede il flag e decide).

## Esposizione aggregata (settori, beta, correlazioni)

`domain/exposure.py` è puro: prende `positions` + dati esterni iniettati
(prezzi correnti, mappa sector, beta, returns DataFrame). I download
yfinance (sector via `info`, beta via `info`, returns via `download`)
vivono nella CLI che chiama queste funzioni — coerente con il pattern
di separazione dei layer.

**Tre dimensioni** misurate da `propicks-portfolio risk`:

1. **Concentrazione settoriale (GICS)** — `compute_sector_exposure` somma
   il % capitale per `sector_key` (mapping Yahoo→interno via
   `domain.stock_rs.YF_SECTOR_TO_KEY`). Le regole single-name cappano la
   posizione al 15%, ma due tech stock a 15% ciascuno = 30% effettivi su
   technology. `compute_concentration_warnings` flagga sector > 30%
   (default cap, opinabile). Cash NON è incluso (esposizione zero).

2. **Beta-weighted gross long exposure** —
   `compute_beta_weighted_exposure` calcola `sum(weight_i * beta_i)`.
   Misura la sensibilità del portfolio al mercato (SPX): beta-weighted
   0.78 con gross long 0.65 = portfolio 65% investito che si muove come
   il 78% di SPX (ha titoli più volatili della media). Per ticker senza
   beta noto (IPO recenti, ETF, ticker esteri illiquidi) usa
   `default_beta=1.0` e ne logga l'elenco.

3. **Matrice correlazioni pairwise** — `compute_correlation_matrix` su
   daily returns (default 6 mesi via `download_returns`) +
   `find_correlated_pairs` estrae upper-triangle con `|corr| >= 0.7`.
   Pair sopra soglia sono effettivamente la stessa scommessa (rischio
   concentrato camuffato da diversificazione). Limit interno della CLI:
   top 10 pair per non saturare l'output.

Tutte le funzioni gestiscono input degenere: `total_capital=0`, posizioni
senza prezzo corrente (DataUnavailable), beta None, correlazioni con
osservazioni < `min_observations` (default 30, ritorna None invece di
una matrice rumorosa).

## Watchlist (incubatrice idee tra scan e entry)

`io/watchlist_store.py` è il parallelo di `portfolio_store.py` ma con
semantica diversa: la watchlist **non impegna capitale**, non ha regole
di sizing, non blocca l'entry. È l'incubatrice dove i setup attendono il
loro momento (pullback, breakout, catalyst, rerating di regime).

**Schema per entry:**

```json
{
  "AAPL": {
    "added_date": "2026-04-20",
    "target_entry": 185.50,
    "note": "pullback EMA20 post earnings beat",
    "score_at_add": 72.3,
    "regime_at_add": "BULL",
    "classification_at_add": "B — WATCHLIST",
    "source": "manual" | "auto_scan"
  }
}
```

**Auto-populate da `propicks-scan`:** lo scanner aggiunge automaticamente
i ticker **classe A** (score ≥75, `"A — AZIONE IMMEDIATA"`) e **classe B**
(60-74, `"B — WATCHLIST"`) alla watchlist, con `source="auto_scan"` e
snapshot di score/regime/classification al momento dello scan. Policy per
il `target_entry`:

- **Classe A nuove entry**: `target_entry = current_price` (distanza 0% →
  immediatamente READY al prossimo `status`). Rationale: un setup A è
  tradable *ora*, ha senso che la watchlist lo flaggi come tale senza
  forzare il trader a settare un target manualmente.
- **Classe A entry esistenti con target già settato**: target preservato
  (non sovrascriviamo né input manuali del trader né target di scan
  precedenti quando il prezzo è salito).
- **Classe B**: senza target — il trader lo imposta manualmente quando
  individua il livello (pullback EMA20, breakout, catalyst date).
- **Classe C/D**: skip dell'auto-add (rumore). Restano disponibili via
  bottone manuale "→ Aggiungi a watchlist" nella dashboard Scanner.

Disabilitabile con `--no-watchlist`. La dashboard Scanner page replica la
stessa policy A+B con toast di conferma, più un bottone manuale per
ticker di qualunque classe (utile quando *sai* che vuoi tenerlo d'occhio
nonostante il setup non sia pronto).

**Ready signal** (`propicks-watchlist status` / tab Attiva della dashboard):
- Score corrente ≥ 60 **E**
- `|current_price − target_entry| / target_entry ≤ 2%`

Un entry READY **non** apre la posizione automaticamente: è flag visivo che
invita a passare da `propicks-scan` (re-analisi completa con regime + AI)
e `propicks-portfolio size/add` con sizing esplicito.

**Dedup e update-on-add:** `add_to_watchlist` normalizza il ticker a
uppercase e se esiste già aggiorna solo i campi non-None, preservando
`added_date` e `source` originali. Questo permette a `propicks-scan` di
girare ripetutamente su un ticker senza azzerare i metadati della prima
aggiunta.

**Stale entries:** `is_stale(entry, days=60)` marca come stale le entry
da più di 60 giorni. La dashboard ha un tab dedicato con multi-select per
pulizia in blocco. Rationale operativo: se un setup non si è materializzato
in 2 mesi, probabilmente la tesi era sbagliata o il regime è cambiato.

**Schema legacy:** `load_watchlist` migra automaticamente
`{"tickers": []}` e `{"tickers": ["AAPL", "MSFT"]}` (lista di stringhe)
a dict con campi default. Nessuna azione manuale richiesta.

## Sync journal ↔ portfolio

I due store restano indipendenti (separation of concerns: journal è l'append-log
immutabile con tutte le meta di analisi, portfolio è lo stato corrente con cash
e shares), ma `propicks-journal add`/`close` e le corrispondenti dashboard form
passano dal **coordinator `io/trade_sync.py`** che scrive in entrambi.

**Schema journal esteso:** campo `shares: int | None`. Obbligatorio via CLI
(`--shares N`) e dashboard (numeric input), `None` sui record legacy che
non vengono migrati (nessuna sync post-hoc).

**Policy di robustezza** (nessun rollback magico):

- **Apertura** — `trade_sync.open_trade`: journal scritto per primo. Se
  `add_position` fallisce (cash insufficiente, size > 15%, stop > 8%, posizione
  già presente), il journal resta scritto con `warning` informativo. Il trade
  reale *è* aperto sul broker — il record deve esistere a prescindere da cosa
  dice il tracker. Correggi manualmente con `propicks-portfolio add`.
- **Chiusura** — `trade_sync.close_trade`: journal exit scritto per primo. Se
  la posizione non è nel portfolio (mai sincronizzata o già rimossa), il journal
  viene comunque chiuso. P&L vive nel journal, non nel portfolio.
- **Idempotenza** — se apri un trade e il portfolio ha già quel ticker (creato
  via `propicks-portfolio add` prima), il journal viene scritto ma il portfolio
  non duplicato.

**Cash accounting fix:** `portfolio_store.close_position(exit_price)` rimborsa
`shares × exit_price` (proventi reali dalla vendita). La vecchia
`remove_position` rimborsa `shares × entry_price` (undo di add_position) e
serve solo per correggere errori di data entry. Usare `close_position` quando
chiudi un trade reale con P&L — il coordinator lo fa già automaticamente.

## Note per Claude Code

- Dopo modifiche a `domain/` esegui `pytest` — tutti i test girano senza rete
- Per modifiche a `cli/` o `reports/`, smoke test con gli entry points
  (`propicks-portfolio status`, `propicks-report weekly`, ecc.)
- Per modifiche a `dashboard/`, smoke test con `propicks-dashboard` o
  `streamlit run src/propicks/dashboard/app.py` — verifica che ogni page
  renda senza eccezioni e che i `st.dataframe` non rompano la serializzazione
  Arrow (colonne con tipi misti `float`/`"—"` vanno tutte a string)
- Il DB SQLite `data/propicks.db` è la source of truth — backup con `cp` o `sqlite3 .backup`
- Il journal è append-only: i trade chiusi non vengono cancellati, viene aggiunto il campo `exit_*`
- Nuove dipendenze → aggiungi in `[project.dependencies]` di `pyproject.toml`
  e documenta qui nello stack tecnologico
- Per i ticker italiani, usa il suffisso `.MI` (es. `ENI.MI`, `ISP.MI`)
- Non importare da `domain/` verso `io/market/ai/cli/reports`: rompe la purezza
  del layer e blocca i test senza rete
- `ANTHROPIC_API_KEY` va in `.env` (gitignored) o esportata in shell. Senza la
  chiave, `--validate` fallisce con errore esplicito ma il resto della CLI
  (scan, portfolio, journal, report) continua a funzionare normalmente
- Quando si modifica `ai/prompts.py::SYSTEM_PROMPT`, ricorda che ogni byte
  cambia invalida la prompt cache lato Anthropic: mantieni dinamico solo il
  user prompt
- I verdict sono cacheati in tabella `ai_verdicts` del DB con TTL 24h
  (48h per ETF). Usa `--force-validate` per forzare una rivalidazione, oppure
  `DELETE FROM ai_verdicts WHERE cache_key = '<TICKER>_v4_<YYYY-MM-DD>'` via sqlite3
