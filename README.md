# Propicks AI Framework

Motore Python per un trading system AI-driven che combina segnali da Investing Pro Picks AI con analisi qualitativa (Claude/Perplexity) e tecnica (yfinance/TradingView). Gestisce il ciclo completo: **screening → scoring → sizing → execution → journaling → review**.

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

Per usare la validazione AI (`propicks-scan --validate`) serve una chiave Anthropic. Crea un file `.env` nella root del progetto (già in `.gitignore`):

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

L'install editable registra **4 comandi CLI** nel PATH del virtualenv:

| Comando | Scopo |
|---------|-------|
| `propicks-scan` | Scoring tecnico 0-100 di uno o più ticker |
| `propicks-portfolio` | Position sizing, stato portafoglio, rischio |
| `propicks-journal` | Registrazione trade (append-only), metriche |
| `propicks-report` | Report markdown settimanali/mensili |

## Quickstart

### 1. Analizzare un ticker

```bash
propicks-scan AAPL
```

Stampa tabella con indicatori (EMA, RSI, ATR, volume), sei sub-score, score composito 0-100 e classificazione A/B/C/D. Include il blocco **COPIA/INCOLLA per prompt Claude 3A** da passare all'analisi qualitativa.

Batch multi-ticker:

```bash
propicks-scan AAPL MSFT NVDA AMZN --strategy TechTitans
propicks-scan AAPL --json      # output JSON
propicks-scan AAPL MSFT --brief # solo tabella riassuntiva
```

Validazione AI della tesi (richiede `ANTHROPIC_API_KEY`):

```bash
propicks-scan AAPL --validate         # solo se score_composite >= 60 (gate), con cache giornaliera
propicks-scan AAPL --force-validate   # bypassa gate e cache, forza la chiamata
```

Output: verdict (CONFIRM/CAUTION/REJECT), conviction 0-10, bull/bear case, catalizzatori, rischi, trigger di invalidazione e allineamento con il setup tecnico. I risultati vengono cacheati per 24h in `data/ai_cache/`.

Ticker italiani: usa il suffisso `.MI` (es. `ENI.MI`, `ISP.MI`).

### 2. Calcolare la size di un nuovo trade

```bash
propicks-portfolio size AAPL --entry 185.50 --stop 171.50 \
  --score-claude 8 --score-tech 75
```

Calcola il numero di azioni rispettando:
- **Max 15%** del capitale per singola posizione
- **Min 20%** di cash reserve sempre mantenuta
- Size differenziata per convinzione: HIGH (avg score ≥ 80) = 12%, MEDIUM (≥ 60) = 8%
- Warning se lo stop è oltre l'**8%** di loss per trade

### 3. Aprire la posizione

```bash
propicks-portfolio add AAPL --entry 185.50 --shares 25 --stop 171.50 \
  --target 210 --strategy TechTitans --score-claude 8 --score-tech 75
```

Hard validation: vengono rifiutate aperture che violano le regole di rischio (size > 15%, riserva cash < 20%, stop > 8%, score < soglie minime).

### 4. Registrare il trade nel journal

```bash
propicks-journal add AAPL long --entry-price 185.50 --entry-date 2026-01-15 \
  --stop 171.50 --target 210 --score-claude 8 --score-tech 75 \
  --strategy TechTitans --catalyst "Beat earnings Q4, guidance raised"
```

### 5. Chiudere il trade

Rimuovi la posizione dal portafoglio e registra la chiusura nel journal:

```bash
propicks-portfolio remove AAPL
propicks-journal close AAPL --exit-price 208.30 --exit-date 2026-02-10 \
  --reason "Target raggiunto"
```

### 6. Monitorare e riflettere

```bash
# Stato portafoglio con P&L live
propicks-portfolio status

# Rischio aggregato a stop
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

## Struttura progetto

```
propicks-ai-framework/
├── src/propicks/
│   ├── config.py         # Parametri operativi
│   ├── domain/           # Logica pura (indicators, scoring, sizing, verdict)
│   ├── io/               # Persistenza JSON atomica (portfolio, journal)
│   ├── market/           # Adapter yfinance (unico punto che parla con la rete)
│   ├── ai/               # Adapter Anthropic (validazione tesi via Claude)
│   ├── reports/          # Generatori markdown (weekly, monthly, benchmark)
│   └── cli/              # Thin argparse wrappers (entry points)
├── tests/unit/           # Test puri su domain/
├── data/                 # Stato runtime (portfolio.json, journal.json)
└── reports/              # Report markdown generati
```

La separazione dei layer è strict: `domain/` non importa da `io/market/cli/reports`. Questo consente:

- Test senza rete (il core logic non dipende da yfinance)
- Sostituzione futura del data provider (basta cambiare `market/`)
- Riutilizzo del domain in contesti diversi (API, backtest, webhook)

`ai/` è l'unico modulo che parla con l'SDK Anthropic (parallelo a `market/` per yfinance): la CLI chiama `validate_thesis` e riceve un verdict strutturato. Nessun altro layer importa `anthropic` direttamente.

## Regole di business (invarianti)

Queste regole sono hardcoded e applicate dalla validazione di `add_position`:

- Max posizioni aperte: **10**
- Max size singola posizione: **15%** del capitale
- Min cash reserve: **20%** del capitale
- Max loss per trade: **8%** della posizione
- Max loss settimanale: **5%** del capitale → stop trading
- Max loss mensile: **15%** del capitale → stop trading + revisione
- Score minimo per entry: Claude **≥ 6/10**, Tecnico **≥ 60/100**

## Workflow con AI

Gli output CLI includono blocchi pronti da incollare nei prompt Claude:

1. `propicks-scan` → **prompt Claude 3A** (analisi qualitativa del ticker)
2. `propicks-portfolio status` → **prompt Claude 3B** (review del portafoglio)
3. `propicks-journal stats` → **prompt Claude 3D** (post-trade analysis)
4. `propicks-report weekly|monthly` → contesto per qualsiasi prompt

In alternativa al copia/incolla manuale, `propicks-scan --validate` chiama direttamente l'API Anthropic (Claude Opus 4.6 di default) e restituisce un verdict strutturato JSON-validated con bull/bear case, catalizzatori e trigger di invalidazione.

## Documentazione estesa

Per contesto approfondito su convenzioni, roadmap, e note per future modifiche vedi **[CLAUDE.md](./CLAUDE.md)**.

## Licenza

Proprietario — uso personale dell'autore.
