# Propicks AI Framework

Motore Python per un trading system AI-driven che combina segnali da Investing Pro Picks AI con analisi qualitativa (Claude/Perplexity) e tecnica (yfinance/TradingView). Gestisce il ciclo completo: **screening → scoring → sizing → execution → journaling → review**.

> 📖 **Manuale completo**: parti da **[WIKI.md](WIKI.md)** per il manuale operativo (CLI, dashboard, Pine, setup, security, FAQ).
> Per l'architettura interna e gli invarianti vedi [CLAUDE.md](CLAUDE.md).

Tre strategie parallele condividono regime classifier, sizing e journal:

- **Momentum** ([`propicks-momentum`](docs/MOMENTUM_STRATEGY.md)) — Pro Picks AI mensile + scoring tecnico + thesis validator Claude
- **Contrarian** ([`propicks-contra`](docs/CONTRARIAN_STRATEGY.md)) — quality-filtered mean reversion (long-only)
- **Sector rotation** ([`propicks-rotate`](docs/ETF_ROTATION_STRATEGY.md)) — SPDR Select Sector / UCITS ZPD*.DE / Xtrackers WORLD, scoring RS + regime + momentum + trend

## Requisiti

- Python **3.10+**
- Connessione internet (yfinance per dati di mercato)
- macOS / Linux / Windows (via WSL consigliato)

## Installazione

```bash
# 1. Clona il repository
git clone <repo-url> propicks-ai-framework
cd propicks-ai-framework

# 2. Crea un virtualenv
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Installa in modalità editable con dev deps (pytest, ruff, mypy)
pip install -e ".[dev]"
```

Solo runtime (senza tool di sviluppo):

```bash
pip install -e .
```

### Configurazione chiavi API (opzionale)

Per usare la validazione AI (`propicks-momentum --validate`) serve una chiave Anthropic. Crea un file `.env` nella root del progetto (già in `.gitignore`):

```bash
ANTHROPIC_API_KEY=sk-ant-...
PROPICKS_AI_MODEL=claude-opus-4-6        # opzionale: default opus, usa claude-sonnet-4-6 per costi ridotti
PROPICKS_AI_WEB_SEARCH=1                 # opzionale: 1 (default) abilita web search, 0 disabilita
PROPICKS_AI_WEB_SEARCH_MAX_USES=5        # opzionale: cap alle ricerche per validazione (default 5)
```

Il file `.env` viene caricato automaticamente all'import di `propicks.config` (la shell ha precedenza se la variabile è già esportata).

**Costi stimati (Claude Opus 4.6, web search ON):**
- ~$0.10-$0.15 per validazione fresca (token costs + 2-4 ricerche @ $0.01 ciascuna)
- Score gate (≥60) + cache giornaliera limitano fortemente le chiamate ripetute
- Uso realistico ~10 validazioni/giorno ≈ $1.50/giorno (~$40/mese)
- Su Sonnet 4.6 il costo scende a circa la metà

Il numero di ricerche effettuate per chiamata viene loggato su stderr, così da avere visibilità immediata sulla spesa.

L'install editable registra **6 comandi CLI** nel PATH del virtualenv:

| Comando | Scopo |
|---------|-------|
| `propicks-momentum` | Scoring tecnico 0-100 di uno o più ticker (single-stock) |
| `propicks-rotate` | Rotazione settoriale ETF (RS + regime + momentum + trend) |
| `propicks-portfolio` | Position sizing, stato, rischio + esposizione, trailing/time stop |
| `propicks-journal` | Registrazione trade (append-only), metriche |
| `propicks-report` | Report markdown settimanali/mensili |
| `propicks-backtest` | Walk-forward backtest single-stock (validazione storica strategia) |

In aggiunta, la **dashboard web** (Streamlit) espone gli stessi workflow via browser — installazione e launch sono documentati nella sezione [Dashboard](#dashboard) più avanti. CLI e dashboard operano sullo **stesso stato** (`data/portfolio.json`, `data/journal.json`), quindi si alternano liberamente.

## Quickstart

### 1. Analizzare un ticker

```bash
propicks-momentum AAPL
```

Stampa tabella con **regime macro weekly** (STRONG_BULL / BULL / NEUTRAL / BEAR / STRONG_BEAR), indicatori (EMA, RSI, ATR, volume), sei sub-score, score composito 0-100, classificazione A/B/C/D e un **blocco pronto da incollare negli input del Pine daily** (`tradingview/daily_signal_engine.pine`) con entry / stop / target.

Per i **ticker US** l'output include anche una riga **RS vs settore**: forza relativa del titolo contro il proprio sector ETF (SPDR XL*), es. `AAPL vs XLK | ratio 1.042 | slope +0.012 | score 85/100`. È un campo **informativo** — non entra nel composite — che distingue i leader del settore dai passeggeri del trend. Per ticker non-US (.MI/.DE/.L/.PA/…) la riga è omessa: la rotazione geografica inquinerebbe il segnale peer-relative.

Batch multi-ticker:

```bash
propicks-momentum AAPL MSFT NVDA AMZN --strategy TechTitans
propicks-momentum AAPL --json      # output JSON
propicks-momentum AAPL MSFT --brief # solo tabella riassuntiva
```

Validazione AI della tesi (richiede `ANTHROPIC_API_KEY`):

```bash
propicks-momentum AAPL --validate         # gate su score ≥ 60 E regime weekly ≥ NEUTRAL, cache giornaliera
propicks-momentum AAPL --force-validate   # bypassa gate e cache, forza la chiamata
```

La validazione viene **saltata** se il regime weekly è BEAR o STRONG_BEAR (mirror del filtro Pine `entryAllowed = regime >= NEUTRAL`): nessuna chiamata Claude, nessun costo. Usa `--force-validate` se vuoi comunque un'opinione su un setup controtrend.

Output: verdict (CONFIRM/CAUTION/REJECT), conviction 0-10, bull/bear case, catalizzatori, rischi, trigger di invalidazione e allineamento con il setup tecnico. I risultati vengono cacheati per 24h in `data/ai_cache/`.

Ticker italiani: usa il suffisso `.MI` (es. `ENI.MI`, `ISP.MI`).

### 2. Rotazione settoriale ETF

```bash
propicks-rotate                        # US universe (SPDR Select Sector), top 3
propicks-rotate --top 5                # US, top 5
propicks-rotate --region EU            # SPDR UCITS su Xetra (ZPD*.DE)
propicks-rotate --region WORLD         # Xtrackers MSCI World (XDW*/XWTS/XZRE)
propicks-rotate --allocate             # include proposta allocazione
propicks-rotate --validate             # validazione macro via Claude
propicks-rotate --json                 # output JSON
```

Tre universi paralleli, selezionabili via `--region`:

| Region | Tickers | Perimetro | Benchmark RS |
|--------|---------|-----------|--------------|
| `US` (default) | SPDR Select Sector (`XL*`) | S&P 500, 11 settori GICS | `^GSPC` |
| `EU` | SPDR UCITS (`ZPD*.DE`) | Stesso Select Sector Index, wrapper UCITS | `^GSPC` |
| `WORLD` | Xtrackers MSCI World (`XDW*.DE`, `XWTS.DE`, `XZRE.DE`) | MSCI World (~65% US + ~15% EU + ~6% JP) | `URTH` |

L'universo `WORLD` **non è un mirror** dei SPDR: i settori world includono nomi europei/giapponesi con dinamica diversa (es. energy: Shell/TotalEnergies/BP accanto a Chevron/Exxon). Tesi di rotazione globale separata, utile per diversificazione geografica.

Ranking basato su uno score composito (stesso per tutti gli universi):

- **RS (40%)** — forza relativa vs benchmark per region (ratio ETF/bench 26w + EMA10w slope)
- **Regime fit (30%)** — bonus/malus in base al regime weekly: favored=100, adjacent=60, not_favored=20
- **Abs momentum (20%)** — performance 3 mesi
- **Trend (10%)** — prezzo vs EMA30w + slope

**Regime hard-gate:** in STRONG_BEAR i settori non-favoriti vengono azzerati (score=0), in BEAR vengono cappati a 50. Impedisce che un settore con momentum forte ma controtrend macro finisca nei top pick.

Proposta allocazione (`--allocate`):
- STRONG_BEAR → flat (no ETF settoriali, cash)
- BEAR → solo top-1 su difensivi
- NEUTRAL/BULL/STRONG_BULL → top-N equal-weight (default N=3), cap 15% per ETF, 60% aggregato

Validazione AI (`--validate`, richiede `ANTHROPIC_API_KEY`): macro strategist prompt (non equity analyst) — focus su macro drivers, breadth, positioning/flows, rotation stage, alternative, consistency con il regime. Cache 48h in `data/ai_cache/` (la view macro muove più lenta del setup single-stock).

### 3. Calcolare la size di un nuovo trade

```bash
propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \
  --score-claude 8 --score-tech 75
```

Calcola il numero di azioni rispettando:
- **Max 15%** del capitale per singola posizione
- **Min 20%** di cash reserve sempre mantenuta
- Size differenziata per convinzione: HIGH (avg score ≥ 80) = 12%, MEDIUM (≥ 60) = 8%
- Warning se lo stop è oltre l'**8%** di loss per trade

### 4. Aprire la posizione

```bash
propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \
  --target 210 --strategy TechTitans --score-claude 8 --score-tech 75
```

Hard validation: vengono rifiutate aperture che violano le regole di rischio (size > 15%, riserva cash < 20%, stop > 8%, score < soglie minime).

### 5. Registrare il trade nel journal

```bash
propicks-journal add AAPL long --entry-price 185.50 --entry-date 2026-01-15 \
  --stop 171.50 --target 210 --score-claude 8 --score-tech 75 \
  --strategy TechTitans --catalyst "Beat earnings Q4, guidance raised"
```

### 6. Gestire il trade in vita (trailing + time stop)

```bash
# Abilita il trailing stop su una posizione (opt-in esplicito)
propicks-portfolio trail enable AAPL

# Suggerimenti dry-run: ATR trailing + flag time stop su tutte le aperte
propicks-portfolio manage

# Applica i nuovi stop a portfolio.json
propicks-portfolio manage --apply

# Personalizza moltiplicatore ATR e finestra time stop
propicks-portfolio manage --atr-mult 2.5 --time-stop 20 --apply
```

Trailing **ratchet-up only**: il nuovo stop è `highest_price - k×ATR` ma non scende mai. Si attiva solo quando il prezzo supera `entry + 1R` (per evitare di stoppare swing legittimi nel rumore iniziale). Time stop: trade flat (|P&L| < 2%) da ≥ 30 giorni → flag `TIME-STOP` in tabella; il trader poi chiude manualmente con `remove` + `journal close` per evitare chiusure accidentali.

### 7. Chiudere il trade

Rimuovi la posizione dal portafoglio e registra la chiusura nel journal:

```bash
propicks-portfolio remove AAPL
propicks-journal close AAPL --exit-price 208.30 --exit-date 2026-02-10 \
  --reason "Target raggiunto"
```

### 8. Monitorare e riflettere

```bash
# Stato portafoglio con P&L live
propicks-portfolio status

# Rischio aggregato a stop + esposizione (settori, beta-weighted, correlazioni)
propicks-portfolio risk

# Lista trade (tutti / aperti / chiusi per strategia)
propicks-journal list
propicks-journal list --open
propicks-journal list --closed --strategy TechTitans

# Metriche aggregate (win rate, profit factor, drawdown, verdict)
propicks-journal stats
propicks-journal stats --strategy TechTitans

# Report markdown (stampa + salva in reports/)
propicks-report weekly
propicks-report monthly
```

Il comando `risk` ora include tre dimensioni di esposizione oltre al rischio a stop: **concentrazione settoriale** (% capitale per settore GICS, warning > 30%), **beta-weighted gross long** vs SPX (sensibilità del portfolio al mercato), e **pair correlate** (`|corr| ≥ 0.7` su daily returns 6 mesi — pair sopra soglia sono effettivamente la stessa scommessa, anche se diversificate per ticker).

### 9. Validare la strategia con un backtest

```bash
# Walk-forward su singolo ticker (default 5 anni, threshold 60)
propicks-backtest AAPL

# Multi-ticker con metriche aggregate (pool di tutti i trade)
propicks-backtest AAPL MSFT NVDA --period 3y

# Custom: soglia + livelli stop/target in multipli di ATR
propicks-backtest AAPL --threshold 70 --stop-atr 2 --target-atr 3 --time-stop 20

# Output JSON (per ulteriore analisi in Python/notebook)
propicks-backtest AAPL --json

# Solo summary (nasconde la tabella trade-by-trade e l'ASCII equity)
propicks-backtest AAPL --no-trades --no-equity
```

Output: tabella riassuntiva (win rate, profit factor, expectancy, CAGR, Sharpe, Sortino, max drawdown, exit reason breakdown), tabella trade-by-trade e una equity curve ASCII. Nessuna calibrazione fine dei pesi: scopo dichiarato è validare il **segno** della strategia (genera expectancy positiva su un universo liquido), non produrre proiezioni di P&L. Limiti noti documentati nel docstring di `backtest/engine.py`: no slippage, no commissioni, no survivorship bias, no earnings gap.

## Configurazione

Tutti i parametri operativi vivono in **`src/propicks/config.py`**. Modifica lì per:

- Cambiare il capitale di riferimento (`CAPITAL`)
- Regolare le soglie di rischio (`MAX_LOSS_PER_TRADE_PCT`, ecc.)
- Modificare i pesi dello scoring tecnico (devono sommare a 1.0)
- Aggiungere strategie (`STRATEGIES`)

I path di `data/` e `reports/` sono ancorati alla root del progetto: i comandi funzionano da qualsiasi cwd dopo l'install editable.

## Test

```bash
pytest
```

I test unit sono puri (nessuna rete, nessun filesystem mutato) e coprono indicatori, scoring, sizing e verdict logic. Tempo di esecuzione: < 0.5s.

## Dashboard

UI web opzionale basata su **Streamlit** che espone gli stessi workflow della CLI via browser. **Non sostituisce la CLI** — i due layer condividono `domain/`, `io/`, `ai/` e `reports/` e operano sullo stesso `data/portfolio.json` e `data/journal.json`. Ogni pagina è il parallelo diretto di un gruppo di comandi:

| Pagina | Equivalente CLI |
|--------|-----------------|
| **Overview** | `propicks-portfolio status` + `risk` + regime weekly |
| **Momentum** | `propicks-momentum [TICKER ...] [--validate]` |
| **ETF Rotation** | `propicks-rotate --region <R> [--allocate] [--validate]` |
| **Portfolio** | `propicks-portfolio size` + `add` + `update` + `remove` |
| **Journal** | `propicks-journal add` + `close` + `list` + `stats` |
| **Reports** | `propicks-report weekly` + `monthly` |

### Installazione locale

```bash
pip install -e ".[dashboard]"
propicks-dashboard                  # apre http://localhost:8501
propicks-dashboard --server.port 8502    # override porta
```

### Docker

Per un setup portatile (una immagine contiene package + CLI + dashboard):

```bash
# Build
docker build -t propicks-dashboard .

# Run con volumi persistenti (data/ e reports/ vivono sull'host)
docker run --rm -p 8501:8501 \
    -v "$(pwd)/data":/app/data \
    -v "$(pwd)/reports":/app/reports \
    --env-file .env \
    propicks-dashboard

# Oppure via docker-compose
docker compose up --build
```

La CLI resta disponibile **dentro** il container:

```bash
docker compose exec dashboard propicks-momentum AAPL --validate
docker compose exec dashboard propicks-journal stats
```

I volumi `./data` e `./reports` sono montati sull'host — portfolio, journal, cache AI e report persistono tra ricreazioni del container. Il file `.env` (`ANTHROPIC_API_KEY` etc.) è letto runtime, non finisce nell'immagine.

## Struttura progetto

```
propicks-ai-framework/
├── src/propicks/
│   ├── config.py         # Parametri operativi (invarianti + contract Pine + universo ETF)
│   ├── domain/           # Logica pura (indicators, scoring, sizing, regime, etf_scoring, stock_rs, trade_mgmt, exposure, verdict)
│   ├── backtest/         # Walk-forward engine + metrics (CAGR, Sharpe, profit factor, ...)
│   ├── io/               # Persistenza JSON atomica (portfolio, journal)
│   ├── market/           # Adapter yfinance (unico punto che parla con la rete)
│   ├── ai/               # Adapter Anthropic: thesis_validator (stock) + etf_validator (rotation)
│   ├── reports/          # Generatori markdown (weekly, monthly, benchmark)
│   ├── cli/              # Thin argparse wrappers (scanner, rotate, portfolio, journal, report, backtest)
│   └── dashboard/        # UI Streamlit parallela alla CLI (app.py + pages/ + launcher)
├── tradingview/          # Pine scripts (daily_signal + weekly_regime)
├── docs/                 # Playbook + Weekly Operating Framework
├── tests/unit/           # Test puri su domain/ (stock + ETF scoring)
├── data/                 # Stato runtime (portfolio.json, journal.json, ai_cache/)
├── reports/              # Report markdown generati
├── Dockerfile            # Immagine dashboard (python:3.12-slim + [dashboard] extra)
├── docker-compose.yml    # Lancio con volumi persistenti data/ e reports/
└── .dockerignore
```

La separazione dei layer è strict: `domain/` non importa da `io/market/cli/reports`. Questo consente:

- Test senza rete (il core logic non dipende da yfinance)
- Sostituzione futura del data provider (basta cambiare `market/`)
- Riutilizzo del domain in contesti diversi (API, backtest, webhook)

`ai/` è l'unico modulo che parla con l'SDK Anthropic (parallelo a `market/` per yfinance): la CLI chiama `validate_thesis` e riceve un verdict strutturato. Nessun altro layer importa `anthropic` direttamente.

## Integrazione con TradingView

La cartella [`tradingview/`](./tradingview/) contiene due Pine script che affiancano il motore Python:

| File | Timeframe | Scopo |
|------|-----------|-------|
| `weekly_regime_engine.pine` | Weekly | Filtro macro (5-bucket: STRONG_BULL → STRONG_BEAR). Stessa logica replicata in `domain/regime.py`. |
| `daily_signal_engine.pine` | Daily | Rileva trigger di entry in tempo reale (BREAKOUT, PULLBACK, GOLDEN_CROSS, SQUEEZE, DIV) che yfinance (EOD) non vede. |

**Contract**: i parametri di default dei Pine (EMA/RSI/ATR/volume, pesi scoring, soglie A/B/C/D, soglie regime) **devono** corrispondere a `src/propicks/config.py`. Un commento in testa a entrambi i file Pine segna la regola; se tocchi i parametri da un lato aggiorna anche l'altro.

**Divisione del lavoro**:
- Python calcola regime weekly + score tecnico + validazione AI + sizing/journal.
- TradingView osserva il prezzo in tempo reale e lancia gli alert di entry.
- `propicks-momentum --validate` stampa a fine output un blocco **TRADINGVIEW PINE INPUTS** con i livelli (entry / stop / target) da incollare direttamente nei settings del Pine daily.

## Regole di business (invarianti)

Queste regole sono hardcoded e applicate dalla validazione di `add_position`:

- Max posizioni aperte: **10**
- Max size singola posizione: **15%** del capitale (single-stock), **20%** per sector ETF (diversificati)
- Max esposizione aggregata su sector ETF: **60%** del capitale
- Min cash reserve: **20%** del capitale
- Max loss per trade: **8%** della posizione
- Max loss settimanale: **5%** del capitale → stop trading
- Max loss mensile: **15%** del capitale → stop trading + revisione
- Score minimo per entry: Claude **≥ 6/10**, Tecnico **≥ 60/100**
- Regime weekly minimo per validazione AI: **NEUTRAL** (code ≥ 3). BEAR/STRONG_BEAR blocca `--validate` (override con `--force-validate`).
- Regime hard-gate ETF: in STRONG_BEAR i settori non-favoriti hanno score=0; in BEAR sono cappati a 50.

## Workflow con AI

Gli output CLI includono blocchi pronti da incollare nei prompt Claude:

1. `propicks-momentum` → **prompt Claude 3A** (analisi qualitativa del ticker)
2. `propicks-portfolio status` → **prompt Claude 3B** (review del portafoglio)
3. `propicks-journal stats` → **prompt Claude 3D** (post-trade analysis)
4. `propicks-report weekly|monthly` → contesto per qualsiasi prompt

In alternativa al copia/incolla manuale, due validatori AI paralleli chiamano direttamente l'API Anthropic (Claude Opus 4.6 di default):

- `propicks-momentum --validate` → **thesis validator** (single-stock, prompt equity analyst, cache 24h, focus su earnings/catalyst/setup tecnico)
- `propicks-rotate --validate` → **rotation validator** (macro strategist, cache 48h, focus su macro drivers/breadth/positioning/rotation stage, niente earnings)

Entrambi restituiscono verdict strutturati JSON-validated via pydantic. Il prompt di sistema è statico (prompt caching lato Anthropic) e il contenuto dinamico vive nel user prompt.

## Documentazione estesa

- **[CLAUDE.md](./CLAUDE.md)** — convenzioni del codebase, roadmap, note per future modifiche.
- **[docs/Weekly_Operating_Framework.md](./docs/Weekly_Operating_Framework.md)** — framework operativo settimanale (allocazione capitale, cadenza lunedì/sabato/domenica, regole di disciplina cross-asset).
- **[docs/Trading_System_Playbook.md](./docs/Trading_System_Playbook.md)** — workflow dettagliato + prompt Perplexity/Claude.

## Licenza

Proprietario — uso personale dell'autore.
