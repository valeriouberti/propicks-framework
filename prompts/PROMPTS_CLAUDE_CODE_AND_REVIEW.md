# PROMPT OPERATIVI — Claude Code, Code Review Python & Pine Script

---

## PARTE 1: PROMPT PER CLAUDE CODE

Questi prompt vanno usati direttamente nel terminale con `claude` (Claude Code CLI).
Sono pensati per sviluppare, estendere e mantenere il trading engine.

---

### 1A. Prompt di BOOTSTRAP — Setup iniziale del progetto

```
Leggi il file CLAUDE.md in questa directory. È il contesto completo
del progetto: un trading engine Python AI-driven che combina segnali
da Investing Pro Picks AI con analisi tecnica e journaling.

Il progetto ha questi moduli:
- config.py (configurazione e costanti)
- scanner.py (scoring tecnico dei ticker)
- portfolio.py (position sizing e gestione posizioni)
- journal.py (trade logging e metriche)
- report.py (report settimanali/mensili)

Verifica che:
1. Tutte le dipendenze siano installate (yfinance, pandas, numpy, tabulate)
2. Le directory data/ e reports/ esistano
3. I file JSON di stato (portfolio.json, journal.json, watchlist.json)
   esistano e siano validi
4. Ogni modulo si esegua senza errori con --help
5. Lo scanner funzioni su un ticker reale (prova AAPL)

Se trovi problemi, correggili. Non chiedere conferma, agisci.
Alla fine dammi un report di cosa funziona e cosa no.
```

---

## PARTE 1-BIS: PROMPT PER COSTRUIRE I MODULI CORE DA ZERO

Questi prompt permettono di ricostruire da zero ogni modulo del trading engine.
Usali se parti da un progetto vuoto, se devi ricreare un file corrotto,
o se vuoi rigenerare un modulo con miglioramenti.

L'ordine di costruzione è importante: config → scanner → portfolio → journal → report → pinescript → playbook.

---

### BUILD-1. Costruisci config.py — Configurazione Globale

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il file config.py — il modulo di configurazione centrale
del trading engine. Tutti i parametri operativi vivono qui.

Deve contenere queste sezioni con questi valori di default:

CAPITALE E SIZING:
- CAPITAL = 10_000.0 (capitale totale, l'utente lo modifica)
- MAX_POSITIONS = 10
- MAX_POSITION_SIZE_PCT = 0.15 (15% per singola posizione)
- MIN_CASH_RESERVE_PCT = 0.20 (20% sempre in cash)
- HIGH_CONVICTION_SIZE_PCT = 0.12 (12% per score >= 8)
- MEDIUM_CONVICTION_SIZE_PCT = 0.08 (8% per score 6-7)
- MIN_SCORE_CLAUDE = 6 (minimo per entry, scala 1-10)
- MIN_SCORE_TECH = 60 (minimo per entry, scala 0-100)

RISK MANAGEMENT:
- MAX_LOSS_PER_TRADE_PCT = 0.08 (8%)
- MAX_LOSS_WEEKLY_PCT = 0.05 (5% → stop trading)
- MAX_LOSS_MONTHLY_PCT = 0.15 (15% → stop + revisione)
- EARNINGS_WARNING_DAYS = 5

INDICATORI TECNICI:
- EMA_FAST = 20, EMA_SLOW = 50
- RSI_PERIOD = 14, RSI_OVERSOLD = 30, RSI_OVERBOUGHT = 70
- ATR_PERIOD = 14
- VOLUME_AVG_PERIOD = 20, VOLUME_SPIKE_MULTIPLIER = 1.5
- LOOKBACK_DAYS = 120

SCORING WEIGHTS (devono sommare a 1.0):
- WEIGHT_TREND = 0.25
- WEIGHT_MOMENTUM = 0.20
- WEIGHT_VOLUME = 0.15
- WEIGHT_DISTANCE_HIGH = 0.15
- WEIGHT_VOLATILITY = 0.10
- WEIGHT_MA_CROSS = 0.15

STRATEGIE PRO PICKS:
- Lista: TechTitans, DominaDow, BattiSP500, MiglioriItaliane

PATHS:
- BASE_DIR, DATA_DIR, REPORTS_DIR, BASKETS_DIR
- PORTFOLIO_FILE, JOURNAL_FILE, WATCHLIST_FILE
- Crea automaticamente le directory se non esistono

Usa os.path per i path. Nessuna dipendenza esterna.
Aggiungi commenti esplicativi per ogni sezione.
```

---

### BUILD-2. Costruisci scanner.py — Signal Scoring Engine

```
Leggi CLAUDE.md per il contesto del progetto.
Leggi config.py per i parametri.

Crea scanner.py — il motore di scoring tecnico.
È il modulo più importante: analizza un ticker e produce uno score
composito 0-100 che determina se vale la pena entrare.

ARCHITETTURA:
Il modulo deve avere queste funzioni helper di calcolo indicatori:
- compute_ema(series, period) → pd.Series
- compute_rsi(series, period) → pd.Series
- compute_atr(high, low, close, period) → pd.Series

E queste funzioni di scoring, ognuna ritorna un float 0-100:
- score_trend(close, ema_fast, ema_slow)
- score_momentum(rsi)
- score_volume(current_volume, avg_volume)
- score_distance_from_high(close, high_52w)
- score_volatility(atr, close)
- score_ma_cross(ema_fast, ema_slow, prev_ema_fast, prev_ema_slow)

LOGICA DEGLI SCORE:

score_trend:
- Prezzo sopra entrambe EMA + EMA fast > slow → 100
- Prezzo sopra entrambe EMA → 80
- Prezzo sopra solo EMA fast → 60
- Prezzo sotto EMA fast, sopra slow → 40
- Prezzo sotto entrambe → 20
- Prezzo sotto entrambe + EMA fast < slow → 0

score_momentum (RSI):
- RSI 50-65 → 100 (sweet spot, momentum senza ipercomprato)
- RSI 65-70 → 75
- RSI 40-50 → 60
- RSI 30-40 → 45
- RSI 70-80 → 40 (ipercomprato)
- RSI < 30 → 20 (oversold, coltello che cade)
- RSI > 80 → 15

score_volume (ratio = volume / avg_volume):
- Ratio 1.2-2.0 → 100 (interesse sano)
- Ratio 2.0-3.0 → 80 (spike interessante)
- Ratio 1.0-1.2 → 70
- Ratio > 3.0 → 60 (spike estremo, cautela)
- Ratio 0.7-1.0 → 50
- Ratio 0.5-0.7 → 30
- Ratio < 0.5 → 15
- Gestisci avg_volume = 0

score_distance_from_high (distanza % dal 52w high):
- < 3% → 75 (quasi ai massimi)
- 3-5% → 85
- 5-10% → 100 (sweet spot, pullback sano)
- 10-15% → 80
- 15-25% → 50
- 25-35% → 30
- > 35% → 10

score_volatility (atr_pct = atr / close):
- 1-3% → 100 (ideale)
- 3-5% → 70
- 0.5-1% → 60
- < 0.5% → 40
- > 5% → 30

score_ma_cross:
- Golden cross appena avvenuto (fast sopra slow, 5gg fa era sotto) → 100
- Fast sopra slow, spread > 2% → 80
- Fast sopra slow → 70
- Fast sotto slow, convergenti → 30
- Fast sotto slow, spread > 2% → 15
- Death cross appena avvenuto → 5

FUNZIONE PRINCIPALE:
analyze_ticker(ticker: str) → Optional[dict]
- Scarica dati con yfinance (LOOKBACK_DAYS + 60 giorni extra per EMA)
- Calcola tutti gli indicatori
- Calcola i 6 sub-score
- Calcola score composito pesato con i pesi di config.py
- Calcola stop loss suggerito = close - (ATR * 2)
- Calcola performance 1w, 1m, 3m
- Classifica: >= 75 "A — AZIONE IMMEDIATA", >= 60 "B — WATCHLIST",
  >= 45 "C — NEUTRALE", sotto "D — SKIP"
- Ritorna dict con tutti i valori

OUTPUT:
- print_analysis(result) — output dettagliato per singolo ticker
- print_summary_table(results) — tabella riassuntiva per batch
- In fondo a tutto: sezione "COPIA/INCOLLA per prompt Claude 3A"
  con formato compatto pronto da incollare

CLI:
  python scanner.py AAPL
  python scanner.py AAPL MSFT NVDA --strategy TechTitans
  python scanner.py AAPL --json
  python scanner.py AAPL MSFT --brief (solo tabella)

Dipendenze: yfinance, pandas, numpy, tabulate.
Gestisci errori yfinance (timeout, ticker non trovato, dati vuoti).
I ticker italiani usano suffisso .MI (es. ENI.MI).

Dopo aver creato il file, testa con almeno 2 ticker reali
e verifica che lo score sia nell'intervallo 0-100.
```

---

### BUILD-3. Costruisci portfolio.py — Position Sizing e Gestione

```
Leggi CLAUDE.md per il contesto del progetto.
Leggi config.py per i parametri di rischio e sizing.

Crea portfolio.py — gestione portafoglio e calcolo position size.

PERSISTENZA:
- Il portafoglio è salvato in data/portfolio.json
- Struttura: {"positions": {}, "cash": CAPITAL, "last_updated": null}
- positions è un dict con ticker come chiave
- Ogni posizione: entry_price, entry_date, shares, stop_loss, target,
  strategy, score_claude, score_tech, catalyst

FUNZIONI PRINCIPALI:

load_portfolio() → dict
save_portfolio(portfolio) → None (aggiorna last_updated)

calculate_position_size(entry_price, stop_price, score_claude=7,
                        score_tech=70, portfolio=None) → dict
  Logica:
  - Calcola risk_per_share = entry - stop
  - Ritorna errore se stop >= entry
  - Calcola avg_score = media tra score_claude (normalizzato a 100)
    e score_tech
  - Se avg_score >= 80: usa HIGH_CONVICTION_SIZE_PCT → "ALTA"
  - Se avg_score >= 60: usa MEDIUM_CONVICTION_SIZE_PCT → "MEDIA"
  - Se sotto: ritorna errore "Score troppo basso"
  - Verifica MAX_POSITIONS non superato
  - Verifica cash disponibile rispettando MIN_CASH_RESERVE_PCT
  - position_value = min(target_value, max_value, cash_disponibile)
  - shares = int(position_value / entry_price)
  - Calcola risk_total, risk_pct_capital
  - Warning se risk_pct_trade > MAX_LOSS_PER_TRADE_PCT
  - Ritorna dict con tutti i valori

get_current_prices(tickers: list) → dict[str, float]
  Scarica prezzi correnti con yfinance per la lista.

show_status(portfolio) → None
  Stampa tabella con posizioni, P/L corrente, totali.
  In fondo genera la tabella markdown per il prompt Claude 3B.

show_risk(portfolio) → None
  Per ogni posizione calcola rischio a stop loss.
  Mostra rischio totale vs limite settimanale.

add_position(portfolio, ticker, entry_price, shares, stop_loss,
             target, strategy, score_claude, score_tech, catalyst) → dict
  Aggiunge posizione, scala cash, salva.

remove_position(portfolio, ticker) → dict
  Rimuove posizione, rimborsa cash (al prezzo di entry come placeholder),
  ricorda all'utente di loggare nel journal.

update_position(portfolio, ticker, stop_loss=None, target=None) → dict
  Aggiorna stop e/o target.

CLI:
  python portfolio.py status
  python portfolio.py risk
  python portfolio.py size AAPL --entry 185.50 --stop 171.50
                              --score-claude 8 --score-tech 75
  python portfolio.py add AAPL --entry 185.50 --shares 25 --stop 171.50
                              --target 210 --strategy TechTitans
  python portfolio.py update AAPL --stop 180 --target 215
  python portfolio.py remove AAPL

Dipendenze: yfinance, tabulate.
Testa il calcolo di position size con diversi scenari
(alta convinzione, bassa convinzione, cash insufficiente, portafoglio pieno).
```

---

### BUILD-4. Costruisci journal.py — Trade Journal e Metriche

```
Leggi CLAUDE.md per il contesto del progetto.

Crea journal.py — il sistema di logging e analisi dei trade.
È la fonte di verità per valutare se la strategia funziona.

PERSISTENZA:
- data/journal.json — array di oggetti trade
- APPEND-ONLY: i trade chiusi non vengono cancellati,
  si aggiungono i campi exit_*
- Ogni trade ha un id incrementale

STRUTTURA TRADE:
{
  "id": 1,
  "ticker": "AAPL",
  "direction": "long",
  "entry_price": 185.50,
  "entry_date": "2025-01-15",
  "stop_loss": 171.50,
  "target": 210.0,
  "score_claude": 8,
  "score_tech": 75,
  "strategy": "TechTitans",
  "catalyst": "Beat earnings Q4",
  "notes": null,
  "status": "open",
  "exit_price": null,
  "exit_date": null,
  "exit_reason": null,
  "pnl_pct": null,
  "pnl_abs": null,
  "duration_days": null,
  "post_trade_notes": null,
  "created_at": "2025-01-15 10:30"
}

FUNZIONI:

add_trade(ticker, direction, entry_price, entry_date, stop_loss,
          target, score_claude, score_tech, strategy, catalyst, notes) → dict
  - Verifica che non esista già un trade aperto per lo stesso ticker
  - Assegna id incrementale
  - Salva

close_trade(ticker, exit_price, exit_date=None, reason=None, notes=None) → dict
  - Trova il trade aperto per quel ticker
  - Calcola P/L: per long = (exit - entry) / entry * 100
  - Calcola durata in giorni
  - Aggiorna status a "closed"
  - Stampa risultato con emoji verde/rosso
  - Suggerisci di usare il prompt Claude 3D per post-trade analysis

list_trades(filter_status=None, filter_strategy=None) → None
  Tabella con tutti i trade, filtrabili per status e strategia.

compute_stats(filter_strategy=None) → None
  Solo su trade chiusi. Calcola e stampa:
  - Win rate (% vincenti)
  - Media vincita e media perdita
  - Profit factor = abs(avg_win / avg_loss)
  - Miglior e peggior trade
  - Max drawdown (peak-to-trough sulla curva cumulativa dei P/L)
  - Durata media vincenti vs perdenti
  - Breakdown per score Claude (>= 8 vs 6-7)
  - Breakdown per strategia Pro Picks
  - Verdetto: se < 20 trade → "dati insufficienti"
              se WR >= 50% e PF >= 1.5 → "PROFITTEVOLE"
              se WR >= 40% e PF >= 1.2 → "MARGINALE"
              altrimenti → "PERDENTE"

CLI:
  python journal.py add AAPL long --entry-price 185.50
    --entry-date 2025-01-15 --stop 171.50 --target 210
    --score-claude 8 --score-tech 75 --strategy TechTitans
    --catalyst "Beat earnings Q4"
  python journal.py close AAPL --exit-price 208.30
    --exit-date 2025-02-10 --reason "Target raggiunto"
  python journal.py list
  python journal.py list --open
  python journal.py list --closed
  python journal.py list --strategy TechTitans
  python journal.py stats
  python journal.py stats --strategy TechTitans

Dipendenze: tabulate.
Testa con almeno 3 trade (2 chiusi, 1 aperto) e verifica
che le stats siano calcolate correttamente.
```

---

### BUILD-5. Costruisci report.py — Report Settimanali e Mensili

```
Leggi CLAUDE.md per il contesto del progetto.

Crea report.py — generazione automatica di report markdown.

REPORT SETTIMANALE (generate_weekly_report):
Genera un file weekly_YYYY-MM-DD.md in reports/ con:
- Sommario: posizioni aperte, trade aperti/chiusi questa settimana,
  P/L unrealized, cash disponibile
- Benchmark: performance S&P 500 e FTSE MIB (ultimi 7 giorni,
  scaricati con yfinance ticker ^GSPC e FTSEMIB.MI)
- Tabella posizioni aperte con P/L corrente
- Tabella trade chiusi questa settimana
- Checklist azioni per la prossima settimana
- Timestamp di generazione

REPORT MENSILE (generate_monthly_report):
Genera un file monthly_YYYY-MM.md in reports/ con:
- Performance mese: trade chiusi, win rate, P/L cumulativo
- Confronto benchmark: S&P 500 e FTSE MIB (ultimi 30 giorni)
- Alpha vs S&P calcolato
- Performance totale da inizio: win rate, avg win/loss,
  profit factor
- Breakdown per strategia Pro Picks (tabella)
- Verdetto con stessa logica di journal.py
- Domande guida per la revisione strategica
- Timestamp

FUNZIONE HELPER:
get_benchmark_performance(ticker, days) → float
  Calcola performance % di un benchmark negli ultimi N giorni.

CLI:
  python report.py weekly
  python report.py monthly

Dipendenze: yfinance. Usa le funzioni di journal.py e portfolio.py
per caricare i dati.
Stampa il report su terminale E lo salva su file.
Testa la generazione di entrambi i report.
```

---

### BUILD-ALL. Prompt per costruire TUTTO il progetto da zero

```
Devi costruire da zero un trading engine Python completo.
Leggi il file CLAUDE.md per il contesto e la struttura del progetto.

Costruisci i moduli in questo ordine esatto, testando ognuno
prima di passare al successivo:

1. config.py — Configurazione (parametri, costanti, paths)
2. scanner.py — Scoring tecnico (EMA, RSI, ATR, volume, score 0-100)
3. portfolio.py — Position sizing e gestione portafoglio
4. journal.py — Trade journal e metriche aggregate
5. report.py — Report settimanali e mensili markdown

Per ogni modulo:
- Leggi le specifiche nel file PROMPTS_CLAUDE_CODE_AND_REVIEW.md
  nella sezione BUILD corrispondente (BUILD-1 per config, BUILD-2
  per scanner, etc.)
- Implementa seguendo le specifiche esattamente
- Testa dopo la creazione
- Verifica che i moduli precedenti non siano rotti

Alla fine fai un test end-to-end:
- Esegui lo scanner su 2 ticker
- Calcola position size per uno di essi
- Aggiungi un trade al journal
- Chiudi il trade
- Genera le stats
- Genera il report settimanale
- Verifica che tutto sia coerente

Dipendenze da installare: pip install yfinance pandas numpy tabulate
Directory da creare: data/, data/baskets/, reports/, pinescript/
File JSON iniziali: portfolio.json, journal.json, watchlist.json

Non chiedere conferma su nessun passaggio. Costruisci, testa, vai avanti.
```

---

## PARTE 1-TER: PROMPT PER NUOVE FEATURE

### 1B. Prompt per NUOVA FEATURE — Backtest Engine

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il modulo backtest.py seguendo le convenzioni del progetto
(type hints, docstring, CLI con argparse, output formattato).

Il backtest deve:

1. Accettare in input:
   - Una lista di ticker
   - Un periodo storico (es. 2024-01-01 a 2024-12-31)
   - I parametri dello scanner (usa quelli di config.py)

2. Per ogni ticker, simulare la strategia:
   - Calcolare lo score composito giorno per giorno
   - Entrare quando lo score supera 75 (classe A)
   - Stop loss: ATR * 2 sotto il prezzo di entry
   - Target: ATR * 4 sopra il prezzo di entry
   - Uscire se lo score scende sotto 40 per 3 giorni consecutivi

3. Produrre in output:
   - Equity curve (come lista di valori)
   - Win rate, profit factor, max drawdown
   - Lista di tutti i trade simulati con entry/exit/P&L
   - Confronto con buy & hold sullo stesso periodo

4. CLI usage:
   python backtest.py AAPL MSFT NVDA --start 2024-01-01 --end 2024-12-31
   python backtest.py AAPL --start 2024-06-01 --end 2024-12-31 --json

Testa il modulo dopo averlo creato. Assicurati che funzioni
su almeno un ticker con dati reali.
```

---

### 1C. Prompt per NUOVA FEATURE — Webhook TradingView

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il modulo alerts.py — un server webhook minimale che riceve
alert da TradingView e li salva nel sistema.

Specifiche:
1. Usa Flask (lightweight, no overhead)
2. Endpoint POST /webhook che accetta JSON da TradingView
3. Formato atteso dal webhook TradingView:
   {
     "ticker": "AAPL",
     "signal": "BREAKOUT",
     "price": 185.50,
     "timeframe": "D",
     "message": "testo dell'alert"
   }

4. Salva ogni alert ricevuto in data/alerts.json (append-only)
5. Opzionale: se il ticker è nel portafoglio, logga un warning
6. Endpoint GET /alerts per vedere gli ultimi 20 alert
7. Endpoint GET /status per health check

8. CLI:
   python alerts.py serve --port 5000
   python alerts.py list
   python alerts.py clear

Includi un esempio di come configurare il webhook su TradingView
nei commenti del file. NON aggiungere autenticazione complessa,
usa un semplice token nell'header per sicurezza base.

Testa con curl dopo aver creato il modulo.
```

---

### 1D. Prompt per NUOVA FEATURE — Dashboard HTML

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il modulo dashboard.py che genera una dashboard HTML statica
(single file, no server necessario) dal portafoglio e journal correnti.

La dashboard deve mostrare:
1. HEADER: capitale totale, P/L totale, cash, numero posizioni
2. TABELLA POSIZIONI: ticker, entry, current, P/L, stop, target
   con colori verde/rosso per P/L
3. EQUITY CURVE: grafico SVG o Chart.js delle performance cumulative
   dei trade chiusi nel tempo
4. METRICHE: win rate, profit factor, max drawdown in cards
5. HEATMAP PER STRATEGIA: performance di ogni strategia Pro Picks
6. ULTIMI 10 TRADE: lista con dettagli

Output: un file dashboard.html in reports/ che si apre nel browser.
Usa Tailwind CSS via CDN per lo styling. Chart.js via CDN per grafici.

CLI:
  python dashboard.py generate
  python dashboard.py generate --open  (apre nel browser)

Il file HTML deve essere self-contained (nessuna dipendenza locale).
Leggi i dati da data/portfolio.json e data/journal.json.
Se il portafoglio è vuoto, mostra uno stato "Nessun trade ancora"
con istruzioni su come iniziare.
```

---

### 1E. Prompt per NUOVA FEATURE — Integrazione Claude API

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il modulo ai_scoring.py che chiama l'API di Claude
per automatizzare lo scoring qualitativo dei ticker.

Specifiche:
1. Usa il pacchetto anthropic (pip install anthropic)
2. Legge ANTHROPIC_API_KEY da variabile d'ambiente
3. Per ogni ticker, compone un prompt che include:
   - Output dello scanner (score tecnico, indicatori)
   - Contesto della strategia Pro Picks
   - Richiesta di score 1-10 con motivazione

4. Il prompt a Claude deve essere quello definito nel Playbook
   (prompt 3A), ma compilato automaticamente con i dati reali

5. Parsea la risposta di Claude per estrarre:
   - Score numerico (1-10)
   - Scenario bull (testo)
   - Scenario bear (testo)
   - Stop loss suggerito
   - Livello di urgenza (1-5)

6. Salva il risultato per uso nel journal

CLI:
  python ai_scoring.py AAPL --strategy TechTitans
  python ai_scoring.py AAPL MSFT NVDA --strategy TechTitans --json

Usa il modello claude-sonnet-4-20250514 per bilanciare costo e qualità.
Gestisci rate limits e errori API con retry.
Il prompt deve richiedere output in formato strutturato JSON
per facilitare il parsing.
```

---

### 1F. Prompt per NUOVA FEATURE — Watchlist Manager

```
Leggi CLAUDE.md per il contesto del progetto.

Crea il modulo watchlist.py per gestire la watchlist attiva
dei titoli filtrati ma non ancora entrati.

Specifiche:
1. Aggiungere titoli con livelli di entry, motivo, scadenza
2. Rimuovere titoli (manualmente o per scadenza)
3. Check automatico: per ogni titolo in watchlist, controlla
   se il prezzo corrente ha raggiunto il livello di entry
4. Integrazione con lo scanner: mostra score corrente di ogni
   titolo in watchlist

CLI:
  python watchlist.py add AAPL --entry-level 180 --reason "Pullback su EMA20" \
    --expiry 2025-02-15 --strategy TechTitans
  python watchlist.py list
  python watchlist.py check  (controlla prezzi vs livelli)
  python watchlist.py remove AAPL
  python watchlist.py clean  (rimuove scaduti)

Salva in data/watchlist.json.
Il comando 'check' deve scaricare i prezzi correnti e segnalare
se qualche titolo è vicino al livello di entry (entro 2%).
```

---

### 1G. Prompt GENERICO per aggiungere feature

```
Leggi CLAUDE.md per il contesto completo del progetto trading engine.

Devo aggiungere [DESCRIZIONE FEATURE].

Regole:
- Segui le convenzioni del progetto (type hints, docstring, CLI argparse)
- Usa gli stessi pattern degli altri moduli per consistenza
- Salva i dati in data/ con formato JSON append-only
- Testa il modulo dopo averlo creato
- Aggiorna CLAUDE.md se aggiungi dipendenze o cambi la struttura
- Non rompere i moduli esistenti

Implementa e testa. Mostrami l'output dei test.
```

---

## PARTE 2: PROMPT DI CODE REVIEW — Python

---

### 2A. Review COMPLETA di un modulo Python

```
Fai una code review professionale del file [FILENAME].py.

Analizza questi aspetti in ordine di priorità:

1. CORRETTEZZA LOGICA
   - I calcoli finanziari sono corretti? (attenzione a divisioni per zero,
     arrotondamenti, percentuali vs decimali)
   - Le condizioni di edge case sono gestite? (portafoglio vuoto,
     dati mancanti, ticker non trovato, API timeout)
   - I tipi sono coerenti? (float vs int per prezzi e shares)

2. RISK MANAGEMENT
   - Le regole di config.py sono rispettate ovunque?
   - Ci sono path dove si potrebbe aggirare un limite di rischio?
   - I file JSON possono corrompersi? (crash durante la scrittura?)
   - I dati sensibili sono protetti? (API key, capitale)

3. ROBUSTEZZA
   - Cosa succede se yfinance non risponde o restituisce dati vuoti?
   - Cosa succede se il file JSON è corrotto o mancante?
   - I calcoli funzionano con dati italiani (ticker .MI, formato numerico)?
   - Il modulo gestisce correttamente i weekend e i giorni festivi?

4. QUALITÀ CODICE
   - Type hints presenti e corretti su tutte le funzioni pubbliche?
   - Docstring chiare e utili?
   - Nomi di variabili e funzioni chiari?
   - Codice DRY (niente duplicazioni)?
   - Complessità ciclomatica accettabile?

5. PERFORMANCE
   - Ci sono chiamate API ridondanti che si possono cacheare?
   - I calcoli su DataFrame sono ottimizzati?
   - Il file JSON viene letto/scritto troppo spesso?

Per ogni problema trovato:
- Severità: CRITICO / ALTO / MEDIO / BASSO
- Riga: indica dove
- Fix: mostra il codice corretto
- Motivo: spiega perché è un problema

Alla fine dai un voto complessivo 1-10 e le top 3 priorità di fix.
```

---

### 2B. Review FOCALIZZATA sui calcoli finanziari

```
Revisiona SOLO la logica finanziaria in [FILENAME].py.

Verifica con attenzione:

1. POSITION SIZING
   - Il calcolo delle shares è corretto? (intero, non frazionario)
   - Il risk per share è calcolato correttamente?
   - I vincoli di capitale vengono rispettati (max size, cash reserve)?
   - Cosa succede se il prezzo di stop è sopra il prezzo di entry?

2. P/L CALCULATIONS
   - Le percentuali sono calcolate sul prezzo di entry (non sul corrente)?
   - Per operazioni short, il P/L è invertito correttamente?
   - Il P/L è per share o totale? È chiaro nel codice?

3. SCORING
   - I pesi sommano a 1.0?
   - I range di ogni sub-score coprono tutti i casi possibili?
   - Non ci sono gap nei range (es. RSI esattamente a 70)?
   - Lo score composito è nell'intervallo 0-100?

4. RISK METRICS
   - Il max drawdown è calcolato correttamente (peak-to-trough)?
   - Il profit factor usa il rapporto giusto (avg win / abs(avg loss))?
   - Il win rate gestisce il caso 0 trade?

Per ogni problema, mostra:
- Il calcolo errato
- Il valore atteso vs il valore prodotto
- Il fix con il codice corretto
```

---

### 2C. Review di SICUREZZA e integrità dati

```
Analizza [FILENAME].py dal punto di vista della sicurezza
e dell'integrità dei dati.

1. FILE HANDLING
   - I file JSON vengono scritti atomicamente? (rischio corruzione
     se il processo viene interrotto durante la scrittura)
   - C'è un backup prima della scrittura?
   - I path sono sanitizzati? (injection via ticker name?)
   - Le permission dei file sono corrette?

2. INPUT VALIDATION
   - I ticker vengono validati prima di passarli a yfinance?
   - I prezzi negativi o zero vengono gestiti?
   - Le date vengono validate (formato, futuro, weekend)?
   - Gli argomenti CLI sono bounds-checked?

3. ERROR HANDLING
   - Ogni chiamata esterna (yfinance, file I/O) è in try/except?
   - Gli errori vengono loggati in modo utile?
   - Il sistema degrada gracefully o crasha?
   - I messaggi di errore non espongono informazioni sensibili?

4. CONCORRENZA
   - Cosa succede se due istanze scrivono sullo stesso JSON?
   - Il portafoglio può finire in stato inconsistente?
   - C'è rischio di race condition sui file?

Suggerisci fix concreti con codice per ogni problema trovato.
Prioritizza per rischio di perdita dati.
```

---

### 2D. Review per ESTENDIBILITÀ e manutenibilità

```
Analizza l'architettura complessiva del trading engine
(tutti i file .py nella directory).

1. ACCOPPIAMENTO
   - I moduli sono indipendenti o c'è troppo coupling?
   - Si può usare lo scanner senza il portfolio? E il journal senza il report?
   - Le dipendenze circolari sono assenti?

2. ESTENDIBILITÀ
   - Quanto è facile aggiungere un nuovo indicatore allo scanner?
   - Quanto è facile aggiungere una nuova strategia Pro Picks?
   - Il sistema supporta asset diversi (crypto, ETF, forex)?
   - Il journal può essere migrato da JSON a SQLite senza riscrivere tutto?

3. CONFIGURABILITÀ
   - Tutti i magic number sono in config.py?
   - Si può cambiare il position sizing senza toccare il codice?
   - I pesi dello scoring sono facilmente modificabili?

4. TESTING
   - Come si testa il sistema senza connessione internet?
   - Servono mock per yfinance?
   - Quali test unitari sarebbero più utili?
   - Suggerisci una struttura tests/ con i test più critici

Dammi un piano di refactoring ordinato per impatto/sforzo:
quali cambiamenti danno il massimo beneficio con il minimo lavoro?
```

---

## PARTE 3: PROMPT DI CODE REVIEW — Pine Script

---

### 3A. Review COMPLETA dello script Pine daily

```
Fai una code review del Pine Script "AI Trading System — Daily Signal Engine".

Analizza:

1. CORRETTEZZA DEI CALCOLI
   - Lo scoring composito replica correttamente la logica Python?
     (confronta i range, i pesi, le condizioni)
   - I segnali di entry (breakout, pullback, golden cross, squeeze,
     divergenza) hanno condizioni corrette e non ambigue?
   - L'ATR stop è calcolato correttamente?
   - Il volume ratio è calcolato senza rischio di divisione per zero?

2. COERENZA CON IL SISTEMA
   - I pesi dello score (0.25, 0.20, 0.15, 0.15, 0.10, 0.15)
     corrispondono a quelli in config.py?
   - Le soglie di classificazione (75/60/45) corrispondono
     a quelle del motore Python?
   - I parametri di default (EMA 20/50, RSI 14, ATR 14)
     corrispondono a config.py?

3. QUALITÀ SEGNALI
   - I segnali di breakout possono generare falsi positivi
     in mercati laterali?
   - Il pullback signal è troppo aggressivo o troppo conservativo?
   - La divergenza RSI è implementata in modo affidabile?
   - I segnali si sovrappongono (stessa candela, più label)?

4. PERFORMANCE PINE SCRIPT
   - Ci sono calcoli ridondanti che rallentano lo script?
   - Il numero di label e lines rispetta i limiti di TradingView?
   - Le security() call (se presenti) sono ottimizzate?
   - Lo script funziona su tutti i timeframe o solo sul daily?

5. UX E VISUALIZZAZIONE
   - I colori sono distinguibili (anche per daltonici)?
   - La tabella è leggibile su schermi piccoli?
   - I segnali sul grafico sono troppi / troppo pochi?
   - Gli alert message sono chiari e actionable?

6. ALERT
   - Ogni alert ha un message utile con le info necessarie?
   - Gli alert possono scattare troppe volte (spam)?
   - Mancano alert importanti?

Per ogni problema:
- Linea approssimativa nel codice
- Severità: CRITICO / ALTO / MEDIO / BASSO
- Fix suggerito con codice Pine Script corretto
```

---

### 3B. Review COMPLETA dello script Pine weekly

```
Fai una code review del Pine Script "AI Trading System — Weekly Regime".

Analizza:

1. CLASSIFICAZIONE REGIME
   - I 5 livelli (Strong Bull → Strong Bear) sono ben definiti?
   - Ci sono condizioni ambigue dove il regime potrebbe oscillare
     troppo frequentemente (whipsaw)?
   - L'ADX è calcolato correttamente con il metodo approssimato?
   - La soglia ADX 25 per "trend forte" è appropriata?

2. MARKET STRUCTURE
   - Il rilevamento di Higher Highs / Higher Lows è robusto?
   - I pivot point con lookback 5 sono troppo sensibili o troppo lenti?
   - Le variabili var mantengono lo stato correttamente tra le barre?
   - Cosa succede nelle prime barre del grafico (dati insufficienti)?

3. ENTRY FILTER
   - La regola "regime >= 3 per entrare long" è corretta?
   - Cosa succede se il regime cambia durante la settimana?
   - L'aggiornamento è solo a chiusura settimanale — è appropriato?

4. COERENZA TRA DAILY E WEEKLY
   - Le EMA weekly (10/30/40) corrispondono correttamente
     alle equivalenti daily (50/150/200)?
   - Il daily e il weekly possono dare segnali contraddittori?
   - Come si gestisce il caso "weekly bullish ma daily in pullback"?

5. PERFORMANCE E LIMITI
   - Il calcolo di 52 settimane di high/low è pesante?
   - Le label di regime change si accumulano troppo?
   - Lo script funziona anche su ticker con meno di 52 settimane di dati?

6. ALERT WEEKLY
   - Gli alert di regime change sono troppo sensibili?
   - L'alert "SOTTO EMA 200d" è appropriato per un grafico weekly?
   - Mancano alert per situazioni importanti?

Per ogni problema, severità e fix con codice.
Alla fine: come miglioreresti l'interazione tra lo script weekly
e quello daily per renderli un sistema più integrato?
```

---

### 3C. Review COMPARATIVA — Consistenza Python ↔ Pine Script

```
Confronta la logica di scoring tra scanner.py (Python)
e daily_signal_engine.pine (Pine Script).

Per OGNI sub-score (trend, momentum, volume, distance_high,
volatility, ma_cross):

1. Estrai le condizioni e i range dal Python
2. Estrai le condizioni e i range dal Pine Script
3. Confronta riga per riga:
   - I range sono identici?
   - Le condizioni di confine (es. RSI = 70 esattamente)
     producono lo stesso score in entrambi?
   - I pesi compositi sono gli stessi?
   - I valori di default degli indicatori corrispondono?

4. Verifica con casi di test:
   Prezzo: 185, EMA20: 180, EMA50: 170
   → Score trend Python: ?
   → Score trend Pine: ?
   → Match?

   RSI: 70.0
   → Score momentum Python: ?
   → Score momentum Pine: ?
   → Match?

   Volume: 15M, Vol avg: 10M (ratio 1.5)
   → Score volume Python: ?
   → Score volume Pine: ?
   → Match?

Elenca TUTTE le discrepanze trovate, anche minori.
Per ogni discrepanza, indica quale versione è corretta
e come allineare l'altra.
```

---

### 3D. Review PRATICA — Simulazione di segnali

```
Simula mentalmente questi scenari e verifica che gli script
Pine generino i segnali corretti:

SCENARIO 1 — BREAKOUT CLASSICO
Un titolo consolida per 3 settimane sotto resistenza a $200.
Weekly regime: BULLISH (EMA fast sopra slow, RSI 58, ADX 28).
Venerdì il daily chiude a $202 con volume 2x la media.
- Il weekly dovrebbe mostrare: [cosa?]
- Il daily dovrebbe mostrare: [quali segnali?]
- Quali alert dovrebbero scattare?
- Lo score composito dovrebbe essere circa: [quanto?]

SCENARIO 2 — FALSO BREAKOUT
Come scenario 1, ma il giorno dopo il titolo torna a $198.
- I segnali cambiano? Come?
- Lo stop ATR dove sarebbe?
- Il sistema gestisce bene questo caso?

SCENARIO 3 — DEATH CROSS IN REGIME BULLISH
Il titolo ha EMA 20 che incrocia sotto EMA 50 sul daily,
ma il weekly è ancora BULLISH (regime 4).
- Segnale contraddittorio: come si gestisce?
- Il daily mostra death cross. Il weekly dice "entry ok".
- Cosa dovrebbe fare il trader secondo il sistema?

SCENARIO 4 — TITOLO ITALIANO ILLIQUIDO
Ticker .MI con volume medio 50.000 azioni/giorno.
Volume oggi: 200.000 (4x).
Prezzo si muove del 5% in un giorno.
- Gli score di volume e volatilità gestiscono bene questo caso?
- Il position sizing tiene conto della bassa liquidità?
- Ci sono rischi specifici non coperti dagli script?

SCENARIO 5 — EARNINGS TRAP
Titolo con score 82 (classe A), regime weekly STRONG BULL.
Earnings tra 3 giorni.
- Il sistema avvisa del rischio earnings?
- Dovrebbe entrare o aspettare?
- Come si potrebbe migliorare lo script per gestire questo caso?

Per ogni scenario, valuta se il sistema si comporta correttamente
e suggerisci miglioramenti specifici con codice.
```

---

## PARTE 4: PROMPT PERIODICI PER MANUTENZIONE

---

### 4A. Health Check mensile del codebase

```
Fai un health check completo del trading engine.

1. Esegui tutti i moduli con --help e verifica che funzionino
2. Controlla che i file JSON in data/ siano validi
3. Verifica che config.py abbia valori sensati
4. Controlla che i pesi dello scoring sommino a 1.0
5. Verifica che non ci siano import non usati o dipendenze mancanti
6. Controlla se ci sono TODO, FIXME, o HACK nel codice
7. Verifica che le metriche del journal siano calcolate correttamente
   con un esempio manuale
8. Controlla che i report generati siano formattati correttamente

Se trovi problemi, correggili direttamente.
Alla fine dammi un summary della salute del progetto.
```

---

### 4B. Aggiornamento dopo cambio strategia

```
Leggi CLAUDE.md per il contesto.

Devo aggiornare il sistema per [DESCRIZIONE CAMBIO].
Esempio: aggiungere una nuova strategia Pro Picks,
cambiare i pesi dello scoring, modificare le regole di risk.

Identifica TUTTI i file che devono essere modificati per
questo cambio. Includi:
- config.py (parametri)
- scanner.py (se cambiano indicatori o pesi)
- Pine Script daily (se cambiano condizioni di scoring)
- Pine Script weekly (se cambiano condizioni di regime)
- Playbook (se cambiano procedure operative)
- CLAUDE.md (se cambia la struttura)

Fai tutte le modifiche in modo atomico e coerente.
Verifica che Python e Pine Script restino allineati.
Testa dopo ogni modifica.
```
