# CLAUDE.md — Trading Engine AI-Driven

## Panoramica Progetto

Motore Python per un trading system AI-driven che combina segnali da Investing Pro Picks AI con analisi qualitativa (Claude/Perplexity) e tecnica (TradingView). Il sistema gestisce il ciclo completo: screening → scoring → execution → journaling → review.

## Struttura Progetto

Pacchetto installabile (`pip install -e .`) con layout `src/`. Separazione
netta tra logica pura (`domain/`), persistenza (`io/`), adapter di rete
(`market/`), adapter AI (`ai/`), generatori di report (`reports/`) e CLI (`cli/`).

```
propicks-ai-framework/
├── CLAUDE.md                  # Questo file — contesto per Claude Code
├── pyproject.toml             # Deps, entry points CLI, tool config (ruff/pytest/mypy)
├── src/propicks/
│   ├── config.py              # Parametri operativi (capitale, regole, pesi)
│   ├── domain/                # Puro: nessun I/O, nessuna rete
│   │   ├── indicators.py      # EMA, RSI, ATR, pct_change
│   │   ├── scoring.py         # 6 sub-score + classify + analyze_ticker
│   │   ├── sizing.py          # calculate_position_size, portfolio_value
│   │   ├── validation.py      # validate_scores, validate_date
│   │   └── verdict.py         # verdict qualitativo, max_drawdown
│   ├── io/                    # Persistenza JSON (atomic writes)
│   │   ├── atomic.py
│   │   ├── portfolio_store.py # load/save + add/remove/update_position
│   │   └── journal_store.py   # load + add_trade/close_trade (append-only)
│   ├── market/
│   │   └── yfinance_client.py # Unico modulo che parla con yfinance
│   ├── ai/                    # Adapter Anthropic (validazione tesi via Claude)
│   │   ├── claude_client.py   # SDK anthropic + ThesisVerdict (pydantic)
│   │   ├── prompts.py         # System prompt + user template (EN, professional)
│   │   └── thesis_validator.py# Gate + cache giornaliera + orchestrazione
│   ├── reports/               # Markdown generators
│   │   ├── benchmark.py       # get_benchmark_performance (^GSPC, FTSEMIB.MI)
│   │   ├── common.py          # parse_date, trades_*_between, fmt_pct
│   │   ├── weekly.py
│   │   └── monthly.py
│   └── cli/                   # Thin argparse wrappers (entry points)
│       ├── scanner.py         # propicks-scan
│       ├── portfolio.py       # propicks-portfolio
│       ├── journal.py         # propicks-journal
│       └── report.py          # propicks-report
├── tests/
│   ├── conftest.py            # Fixture condivise
│   └── unit/                  # Test puri su domain/ (no I/O, no rete)
│       ├── test_indicators.py
│       ├── test_scoring.py
│       ├── test_sizing.py
│       ├── test_verdict.py
│       └── test_thesis_validator.py # SDK Anthropic mockato
├── data/                      # Runtime state (gitignored)
│   ├── portfolio.json         # Stato corrente del portafoglio
│   ├── journal.json           # Storico completo dei trade
│   ├── watchlist.json         # Titoli in watchlist attiva
│   ├── baskets/YYYY-MM.json   # Storico basket Pro Picks mensili
│   └── ai_cache/              # Cache verdict Claude (TTL 24h)
└── reports/                   # Report generati (gitignored)
    └── weekly_YYYY-MM-DD.md
```

## Stack Tecnologico

- **Python 3.10+** con layout `src/` (editable install via pyproject.toml)
- **yfinance** — dati di mercato real-time e storici
- **pandas / numpy** — calcoli tecnici e statistici
- **tabulate** — output formattato per terminale
- **json** — persistenza dati (volutamente semplice, no database)
- **anthropic** — SDK ufficiale per validazione tesi via Claude Opus 4.6
- **pydantic** — validazione strutturata del verdict AI (`ThesisVerdict`)
- **python-dotenv** — caricamento `.env` per `ANTHROPIC_API_KEY`
- **pytest** (dev) — test unit su `domain/` e `ai/` (SDK mockato)
- **ruff + mypy** (dev) — lint e type check

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

## Comandi Principali

Quattro entry points CLI definiti in `pyproject.toml`. Funzionano da qualsiasi
cwd dopo l'install editable: i path di `data/` e `reports/` sono ancorati alla
root del progetto (ricerca `pyproject.toml`).

```bash
# Analisi tecnica di un ticker
propicks-scan AAPL

# Analisi multipla (batch dal basket Pro Picks)
propicks-scan AAPL MSFT NVDA AMZN --strategy TechTitans

# Output JSON o tabella compatta
propicks-scan AAPL --json
propicks-scan AAPL MSFT --brief

# Validazione AI della tesi (gated su score_composite >= 60, cache 24h)
propicks-scan AAPL --validate
propicks-scan AAPL --force-validate   # bypassa gate e cache

# Calcolo position size per un trade
propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \
  --score-claude 8 --score-tech 75

# Stato del portafoglio e rischio aggregato
propicks-portfolio status
propicks-portfolio risk

# Aprire / aggiornare / rimuovere una posizione
propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \
  --target 210 --strategy TechTitans
propicks-portfolio update AAPL --stop 180 --target 215
propicks-portfolio remove AAPL

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

# Test unit (solo domain/, nessuna rete)
pytest
```

## Regole di Business (Invarianti)

Queste regole sono hardcoded e NON devono essere aggirate:

- **Max posizioni aperte**: 10
- **Max size singola posizione**: 15% del capitale
- **Min cash reserve**: 20% del capitale
- **Max loss per trade**: 8% della posizione
- **Max loss settimanale**: 5% del capitale totale → blocco trading
- **Max loss mensile**: 15% del capitale totale → blocco trading e revisione
- **No entry se earnings entro 5 giorni** (warning, non blocco — il trader decide)
- **Score minimo per entry**: Claude >= 6/10, Tecnico >= 60/100

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
  `score_composite`, cache giornaliera su disco (`data/ai_cache/`) e tool
  `web_search` server-side per dati real-time (spot, earnings, news).
- **`reports/`** può importare da tutti gli altri layer per comporre i markdown.
- **`cli/`** è thin: parsing argparse + chiamata a funzioni di domain/io/ai/reports
  + formatting tabellare. Nessuna logica di business qui.

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

## Estensioni Future (Roadmap)

### v1.1 — Backtest
- Nuovo modulo `propicks.backtest` (puro, in `domain/` o subpackage dedicato)
- Input: lista di trade ipotetici, regole stop/target
- Output: equity curve, drawdown, win rate simulato

### v1.2 — Webhook TradingView
- Endpoint Flask/FastAPI in `propicks.server` che riceve webhook
- Salva alert in `data/alerts.json`
- Opzionale: notifica Telegram

### v1.3 — API Anthropic Integration ✅ (parziale)
- [x] Adapter `propicks.ai.claude_client` parallelo a `market/yfinance_client`
- [x] Pipeline: `analyze_ticker` → `validate_thesis` → verdict strutturato
- [x] Gate su score tecnico + cache giornaliera on-disk
- [ ] TODO: integrazione in `propicks-portfolio add` (validazione pre-apertura)
- [ ] TODO: salvataggio verdict nel journal al momento dell'entry

### v1.4 — Dashboard Web
- React/HTML dashboard per visualizzare portafoglio e metriche
- Equity curve interattiva
- Heatmap performance per strategia

### v1.5 — Automazione Completa
- Orchestratore che combina scanner + Claude API + journal
- Input: basket Pro Picks mensile
- Output: lista ordinata di trade raccomandati con size e livelli

## Note per Claude Code

- Dopo modifiche a `domain/` esegui `pytest` — tutti i test girano senza rete
- Per modifiche a `cli/` o `reports/`, smoke test con gli entry points
  (`propicks-portfolio status`, `propicks-report weekly`, ecc.)
- I file JSON in `data/` sono la source of truth — non cancellare mai, solo appendere
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
- I verdict cacheati in `data/ai_cache/<TICKER>_<YYYY-MM-DD>.json` hanno TTL
  24h. Cancella il file se vuoi forzare una rivalidazione, oppure usa
  `--force-validate`
