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
│   │   ├── scoring.py         # 6 sub-score stock + classify + analyze_ticker
│   │   ├── etf_scoring.py     # 4 sub-score ETF (RS/regime/mom/trend) + rank_universe + alloc
│   │   ├── etf_universe.py    # Query helpers su SECTOR_ETFS_US/EU
│   │   ├── stock_rs.py        # Peer RS stock vs sector ETF (solo US, campo informativo)
│   │   ├── regime.py          # Classifier macro weekly (5-bucket, mirror Pine weekly)
│   │   ├── sizing.py          # calculate_position_size (stock + ETF cap), portfolio_value
│   │   ├── trade_mgmt.py      # Trailing stop ATR-based + time stop (gestione in-vita)
│   │   ├── exposure.py        # Concentrazione settoriale + beta-weighted + correlazioni
│   │   ├── validation.py      # validate_scores, validate_date
│   │   └── verdict.py         # verdict qualitativo, max_drawdown
│   ├── backtest/              # Walk-forward backtest single-stock (puro su DataFrame)
│   │   ├── engine.py          # backtest_ticker + Trade/BacktestResult dataclasses
│   │   └── metrics.py         # win_rate/PF/CAGR/Sharpe/Sortino/DD + aggregate_metrics
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
│   ├── cli/                   # Thin argparse wrappers (entry points)
│   │   ├── scanner.py         # propicks-scan
│   │   ├── portfolio.py       # propicks-portfolio (status/risk/size/add/manage/trail/...)
│   │   ├── journal.py         # propicks-journal
│   │   ├── report.py          # propicks-report
│   │   ├── rotate.py          # propicks-rotate (sector rotation ETF)
│   │   └── backtest.py        # propicks-backtest (walk-forward single-stock)
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
│           └── 6_Backtest.py          # ≡ propicks-backtest
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

# Test unit (solo domain/ + backtest, nessuna rete)
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

## Estensioni Future (Roadmap)

### v1.1 — Backtest ✅ (completato)
- [x] Subpackage `propicks.backtest` (`engine.py` + `metrics.py`)
- [x] CLI `propicks-backtest` con `--json`, `--no-trades`, `--no-equity`
- [x] Test su DataFrame sintetici (no rete)
- [ ] TODO: walk-forward out-of-sample split + significance test (v1.6)
- [ ] TODO: integrazione filtro regime weekly (`^GSPC` pre-caricato)
- [ ] TODO: simulazione costi (slippage + commissioni broker)

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

### v1.4 — Dashboard Web ✅ (completata via Streamlit)
- [x] UI multi-page Streamlit (`src/propicks/dashboard/`) parallela alla CLI
- [x] Entry point `propicks-dashboard` + immagine Docker con volumi persistenti
- [x] 5 page: Overview, Scanner, ETF Rotation, Portfolio, Journal, Reports
- [ ] TODO: equity curve interattiva (plotly — dep già in `[dashboard]` extra)
- [ ] TODO: heatmap performance per strategia

### v1.5 — Automazione Completa
- Orchestratore che combina scanner + Claude API + journal
- Input: basket Pro Picks mensile
- Output: lista ordinata di trade raccomandati con size e livelli

## Note per Claude Code

- Dopo modifiche a `domain/` esegui `pytest` — tutti i test girano senza rete
- Per modifiche a `cli/` o `reports/`, smoke test con gli entry points
  (`propicks-portfolio status`, `propicks-report weekly`, ecc.)
- Per modifiche a `dashboard/`, smoke test con `propicks-dashboard` o
  `streamlit run src/propicks/dashboard/app.py` — verifica che ogni page
  renda senza eccezioni e che i `st.dataframe` non rompano la serializzazione
  Arrow (colonne con tipi misti `float`/`"—"` vanno tutte a string)
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
