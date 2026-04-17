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
│   ├── config.py              # Parametri operativi (capitale, regole, pesi, regime, contract Pine)
│   ├── domain/                # Puro: nessun I/O, nessuna rete
│   │   ├── indicators.py      # EMA, RSI, ATR, ADX, MACD, pct_change
│   │   ├── scoring.py         # 6 sub-score stock + classify + analyze_ticker
│   │   ├── etf_scoring.py     # 4 sub-score ETF (RS/regime/mom/trend) + rank_universe + alloc
│   │   ├── etf_universe.py    # Query helpers su SECTOR_ETFS_US/EU
│   │   ├── regime.py          # Classifier macro weekly (5-bucket, mirror Pine weekly)
│   │   ├── sizing.py          # calculate_position_size (stock + ETF cap), portfolio_value
│   │   ├── validation.py      # validate_scores, validate_date
│   │   └── verdict.py         # verdict qualitativo, max_drawdown
│   ├── io/                    # Persistenza JSON (atomic writes)
│   │   ├── atomic.py
│   │   ├── portfolio_store.py # load/save + add/remove/update_position
│   │   └── journal_store.py   # load + add_trade/close_trade (append-only)
│   ├── market/
│   │   └── yfinance_client.py # Unico modulo che parla con yfinance
│   ├── ai/                    # Adapter Anthropic (validazione tesi via Claude)
│   │   ├── claude_client.py   # SDK anthropic + ThesisVerdict + ETFRotationVerdict
│   │   ├── prompts.py         # System prompt stock (equity analyst)
│   │   ├── etf_prompts.py     # System prompt ETF (macro strategist)
│   │   ├── thesis_validator.py# Gate + cache 24h + orchestrazione stock
│   │   └── etf_validator.py   # Skip STRONG_BEAR + cache 48h + orchestrazione rotation
│   ├── reports/               # Markdown generators
│   │   ├── benchmark.py       # get_benchmark_performance (^GSPC, FTSEMIB.MI)
│   │   ├── common.py          # parse_date, trades_*_between, fmt_pct
│   │   ├── weekly.py
│   │   └── monthly.py
│   └── cli/                   # Thin argparse wrappers (entry points)
│       ├── scanner.py         # propicks-scan
│       ├── portfolio.py       # propicks-portfolio
│       ├── journal.py         # propicks-journal
│       ├── report.py          # propicks-report
│       └── rotate.py          # propicks-rotate (sector rotation ETF)
├── tradingview/               # Pine script (contract con config.py)
│   ├── daily_signal_engine.pine    # Entry triggers in tempo reale (BRK/PB/GC/SQZ/DIV)
│   └── weekly_regime_engine.pine   # Filtro macro (5-bucket) — duplicato visuale di regime.py
├── docs/
│   └── Trading_System_Playbook.md  # Workflow operativo + prompt Perplexity/Claude
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

Cinque entry points CLI definiti in `pyproject.toml`. Funzionano da qualsiasi
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

# Validazione AI della tesi (gate doppio: score >= 60 E regime weekly >= NEUTRAL)
propicks-scan AAPL --validate
propicks-scan AAPL --force-validate   # bypassa gate (score + regime) e cache

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

# Rotazione settoriale ETF — ranking dell'universo Select Sector SPDR / UCITS
propicks-rotate                         # US universe, top 3
propicks-rotate --region EU             # UCITS su Xetra (ZPD*.DE)
propicks-rotate --top 5 --allocate      # top 5 + proposta allocazione
propicks-rotate --validate              # validazione macro via Claude (on-demand)
propicks-rotate --force-validate        # bypassa skip in STRONG_BEAR e cache 48h
propicks-rotate --json --allocate       # output JSON completo

# Test unit (solo domain/, nessuna rete)
pytest
```

## Regole di Business (Invarianti)

Queste regole sono hardcoded e NON devono essere aggirate:

- **Max posizioni aperte**: 10
- **Max size singola posizione**: 15% del capitale (stock) / 20% (sector ETF)
- **Max esposizione aggregata sector ETF**: 60% del capitale
- **Min cash reserve**: 20% del capitale
- **Max loss per trade**: 8% della posizione (stock) / 5% (sector ETF, via stop hard)
- **Max loss settimanale**: 5% del capitale totale → blocco trading
- **Max loss mensile**: 15% del capitale totale → blocco trading e revisione
- **No entry se earnings entro 5 giorni** (warning, non blocco — il trader decide)
- **Score minimo per entry**: Claude >= 6/10, Tecnico >= 60/100
- **Regime weekly minimo per validazione AI stock**: NEUTRAL (code >= 3). BEAR/STRONG_BEAR skippano `--validate` senza spendere token (override con `--force-validate`).
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
  cache giornaliera su disco (`data/ai_cache/`) e tool `web_search`
  server-side per dati real-time (spot, earnings, news).
- **`reports/`** può importare da tutti gli altri layer per comporre i markdown.
- **`cli/`** è thin: parsing argparse + chiamata a funzioni di domain/io/ai/reports
  + formatting tabellare. Nessuna logica di business qui.
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

## Strategia ETF Settoriali (parallela agli stock)

Il framework supporta due strategie parallele che condividono `regime.py` ma
divergono su scoring e validazione. Il branch è determinato dal ticker via
`domain.etf_universe.get_asset_type`:

- **STOCK** → flow esistente (`analyze_ticker` → tesi aziendale → Claude)
- **SECTOR_ETF** → flow rotazione (RS vs benchmark + regime fit → Claude macro)

### Universo ETF (Fase 1 completata)

Definito in `config.py::SECTOR_ETFS_US` e `SECTOR_ETFS_EU`. Select Sector SPDR
US (11 settori GICS) mappati sui rispettivi **SPDR S&P U.S. Select Sector
UCITS** (tickers `ZPD*.DE` su Xetra). Gli UCITS tracciano lo **stesso Select
Sector Index** degli SPDR US — esposizione identica, solo wrapper irlandese
per accesso da broker europei. Non sono sector europei: la tesi di rotazione
è unica, il trader sceglie il listing in base a fiscalità e liquidità del
proprio broker.

Eccezione: `XLRE` non ha un SPDR US Real Estate Select Sector UCITS
equivalente — campo `eu_equivalent=None`, alternativa esterna documentata
in `eu_equivalent_note` (IUSP.L traccia però un indice diverso).

**Verifica ticker prima del primo uso**: i listing Xetra ZPD* sono
accumulating (IE-domiciled). Varianti distributing su LSE (serie SXR*) hanno
ticker diversi e non sono registrate qui.

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
propicks-rotate                        # US, top 3, solo ranking
propicks-rotate --top 5                # US, top 5
propicks-rotate --region EU            # universo UCITS (ZPD*.DE)
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
