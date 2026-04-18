# TRADING SYSTEM PLAYBOOK
## AI-Driven Strategy con Pro Picks, Claude, Perplexity & TradingView

---

## 1. WORKFLOW OVERVIEW

```
Pro Picks Update (mensile)
        │
        ▼
┌─────────────────────┐
│  FASE 1: SCREENING  │  ← Perplexity 2A/2B (news + catalyst, cross-check fondamentale)
│  Nuovi ingressi     │
│  basket mensile     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────────────┐
│  FASE 2: REGIME + SCORING   │  ← Weekly regime (Python + Pine weekly_regime_engine)
│  entry_allowed ≥ NEUTRAL    │  ← propicks-scan (score 0-100, classe A/B/C/D)
│  score tecnico + classifica │  ← propicks-scan --validate (Claude, gate su regime+score)
│  verdict AI strutturato     │
└────────┬────────────────────┘
         │  CLI stampa blocco TRADINGVIEW PINE INPUTS
         │  (Entry / Stop / Target pronti da incollare)
         ▼
┌─────────────────────────────┐
│  FASE 3: EXECUTION          │  ← Perplexity 2C (red flag 24h)
│  Timing real-time su Pine   │  ← Pine daily_signal_engine (BRK/PB/GC/SQZ/DIV → alert push)
│  Entry + sizing + apertura  │  ← propicks-portfolio size + add (validazione hard)
│  Log nel journal            │  ← propicks-journal add
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  FASE 4: GESTIONE           │  ← Review settimanale (Claude 3B)
│  Trailing stop              │  ← propicks-portfolio status / risk
│  Exit management            │  ← Journal Python (append-only)
│  Post-trade (Claude 3D)     │
└─────────────────────────────┘
```

**Divisione dei layer:**
- **Python** → regime weekly (EOD), score tecnico, validazione AI, sizing, journal.
- **TradingView Pine** → timing real-time dei trigger di entry (yfinance vede solo EOD).
- **Perplexity** → cross-check fondamentale indipendente dal verdict Claude (news, catalyst, red flag 24h).

Il contratto tra Python e Pine (indicatori, pesi, soglie) è definito in
`src/propicks/config.py` e replicato nei commenti di testa dei due Pine script.
Vedi **sezione 4** per i dettagli.

---

## 2. PROMPT PER PERPLEXITY — Analisi News e Catalyst

### 2A. Prompt per NUOVI INGRESSI nel basket

```
Sono un trader che valuta se entrare su [TICKER] ([NOME AZIENDA]).
Il titolo è appena stato inserito nella strategia AI "[STRATEGIA]"
di Investing Pro Picks.

Ho bisogno di un'analisi rapida e fattuale:

1. CATALYST RECENTI (ultimi 30 giorni):
   - Ci sono stati earnings recenti? Se sì, beat o miss vs consensus?
   - Ci sono upgrade/downgrade degli analisti nelle ultime 2 settimane?
   - Annunci aziendali rilevanti (buyback, M&A, nuovi prodotti, guidance)?

2. RISCHI IMMINENTI:
   - Quando sono le prossime earnings? (data esatta)
   - Ci sono indagini, cause legali, o rischi regolatori pendenti?
   - Il settore è sotto pressione per motivi macro?

3. SENTIMENT:
   - Qual è il consensus degli analisti (buy/hold/sell) e il target price medio?
   - C'è short interest significativo (>5%)?
   - Ci sono insider buying o selling recenti?

4. CONTESTO SETTORIALE:
   - Il settore di appartenenza è in momentum positivo o negativo?
   - Ci sono competitor diretti che hanno riportato risultati
     che possono influenzare [TICKER]?

Rispondi in modo conciso con dati e date specifiche.
Non mi servono opinioni generiche, solo fatti verificabili.
```

### 2B. Prompt per TITOLI ITALIANI (Migliori Italiane)

```
Sto valutando [TICKER.MI] ([NOME AZIENDA]) per il mio portafoglio
di azioni italiane.

Analisi specifica per il mercato italiano:

1. FONDAMENTALI RAPIDI:
   - P/E attuale vs media settore e vs media storica 5 anni
   - Dividend yield e prossima data stacco dividendo
   - Debito netto / EBITDA
   - Ultima guidance del management

2. CATALYST ITALIA-SPECIFICI:
   - Impatto PNRR o incentivi governativi sul settore?
   - Esposizione a mercati emergenti o rischio geopolitico?
   - Posizione nell'indice FTSE MIB (entrata/uscita recente)?

3. RISCHI SPECIFICI:
   - Concentrazione azionariato (patto parasociale, fondazioni, stato)?
   - Liquidità media giornaliera (volume medio 30gg)?
   - Prossimi eventi: assemblea, aumento capitale, OPA?

4. NEWS RECENTI:
   - Ultimi 3 articoli rilevanti da Sole 24 Ore, MF, Reuters Italia
   - Commenti recenti di analisti italiani (Mediobanca, Intesa, Equita)

Rispondi con dati numerici precisi e fonti.
```

### 2C. Prompt per CHECK RAPIDO pre-entry (da usare prima di comprare)

```
Check rapido su [TICKER] prima di entrare in posizione oggi.

Rispondimi SOLO con:
1. C'è qualche news delle ultime 24 ore che cambia il quadro?
2. Earnings nelle prossime 2 settimane? Se sì, data esatta.
3. Il pre-market/after-hours mostra movimenti anomali?
4. Volume di oggi vs media 30 giorni: normale o anomalo?

Solo fatti, risposte brevi. Se non c'è nulla di rilevante, dimmi
"Nessun red flag nelle ultime 24h".
```

---

## 3. PROMPT PER CLAUDE — Analisi Qualitativa e Decisionale

### 3A. Prompt per VALUTAZIONE COMPLESSIVA del titolo

```
Agisci come un portfolio manager esperto. Ti fornisco le informazioni
raccolte su [TICKER] e ho bisogno della tua analisi decisionale.

CONTESTO:
- Strategia Pro Picks: [TechTitans / Domina Dow / Batti S&P / Italiane]
- Il titolo è stato AGGIUNTO al basket questo mese
- Il mio approccio è momentum + catalyst, timeframe 1-3 mesi
- Non uso leva, posizioni singole

DATI RACCOLTI (incolla output Perplexity):
[INCOLLA QUI L'OUTPUT DI PERPLEXITY]

DATI TECNICI (incolla output Python o TradingView):
[INCOLLA QUI LO SCORE TECNICO O SCREENSHOT DESCRITTO]

DOMANDE:
1. Basandoti su questi dati, il risk/reward è favorevole per un
   entry nei prossimi giorni? Dammi un giudizio da 1 a 10.

2. Qual è lo SCENARIO BULL (cosa deve succedere perché vada bene)
   e lo SCENARIO BEAR (cosa può andare storto)?

3. Se entrassi, dove metteresti:
   - Stop loss (% dal prezzo attuale)
   - Primo target di profitto
   - Livello dove riconsiderare la tesi

4. C'è qualcosa che i dati NON mi dicono e che dovrei approfondire?

5. Su una scala da 1 a 5, quanto è URGENTE entrare ora vs aspettare
   un pullback? (1 = aspetta, 5 = entra subito)

Sii diretto e critico. Preferisco un "non entrare" ben motivato
a un "sì" tiepido.
```

### 3B. Prompt per REVISIONE SETTIMANALE del portafoglio

```
Revisione settimanale del mio portafoglio trading.
Oggi è [DATA]. Ecco le mie posizioni aperte:

| Ticker | Entry Date | Entry Price | Current Price | P/L % | Stop Loss | Target |
|--------|-----------|-------------|---------------|-------|-----------|--------|
| [...]  | [...]     | [...]       | [...]         | [...]  | [...]    | [...]  |

WATCHLIST (titoli filtrati ma non ancora entrati):
- [TICKER1]: aspetto pullback a [LIVELLO]
- [TICKER2]: aspetto conferma breakout sopra [LIVELLO]

Per ogni posizione aperta, dimmi:
1. Lo stop loss va aggiornato (alzato/abbassato)?
2. Il target originale è ancora valido o va rivisto?
3. La tesi di investimento è ancora intatta?
4. Azione suggerita: HOLD / TIGHTEN STOP / TAKE PARTIAL / CLOSE

Per la watchlist:
1. I livelli di entry che ho impostato sono ancora validi?
2. Qualcosa è cambiato nel quadro macro che influenza questi titoli?

Sii sintetico, una riga di azione per ogni titolo.
```

### 3C. Prompt per ANALISI COMPARATIVA (quando hai troppi candidati)

```
Ho [N] titoli candidati questo mese dal basket Pro Picks e devo
sceglierne massimo [N] per il mio portafoglio. Aiutami a classificarli.

Budget posizioni disponibili: [N] slot da [X]% ciascuno

CANDIDATI:
[Per ogni titolo elenca: ticker, strategia Pro Picks, score tecnico
Python, sintesi catalyst da Perplexity]

Criteri di selezione in ordine di priorità:
1. Forza del catalyst (evento concreto vs generico momentum)
2. Setup tecnico (entry point chiaro vs prezzo in terra di nessuno)
3. Risk/reward (distanza % dallo stop vs distanza dal target)
4. Diversificazione (evitare troppa concentrazione sullo stesso settore)
5. Timing (urgenza entry vs possibilità di aspettare)

Dammi una classifica ordinata con motivazione breve per ogni titolo.
Per quelli che scarti, dimmi perché in una riga.
```

### 3D. Prompt per ANALISI POST-TRADE (learning)

```
Analisi post-trade per il mio journal di apprendimento.

TRADE COMPLETATO:
- Ticker: [TICKER]
- Direzione: LONG
- Entry: [PREZZO] il [DATA]
- Exit: [PREZZO] il [DATA]
- P/L: [+/-X%]
- Motivo entry: [catalyst / score tecnico / ...]
- Motivo exit: [stop loss / target / rimosso da basket / ...]

Basandoti su questo trade, aiutami a identificare:

1. La TESI era corretta? (il catalyst si è materializzato?)
2. Il TIMING era giusto? (avrei fatto meglio ad aspettare o entrare prima?)
3. Lo STOP LOSS era posizionato correttamente?
4. Il TARGET era realistico?
5. Cosa avrei potuto fare DIVERSAMENTE?
6. Questo trade mi insegna qualcosa che posso sistematizzare
   come regola per il futuro?

Sii brutalmente onesto. Non mi interessa sentirmi meglio,
mi interessa migliorare.
```

---

## 4. SETUP TRADINGVIEW — Pine Script, Alert e Indicatori

### 4A. Pine script committati nel repo

La cartella [`tradingview/`](../tradingview/) contiene due Pine script che
affiancano il motore Python. **Contract**: i parametri di default (EMA/RSI/ATR/volume,
pesi scoring, soglie A/B/C/D, soglie regime) devono corrispondere a
`src/propicks/config.py` — un commento in testa a entrambi i file segna la
regola. Se tocchi un parametro da un lato, aggiornalo anche dall'altro.

| File | Timeframe | Scopo |
|------|-----------|-------|
| `weekly_regime_engine.pine` | Weekly | Filtro macro a 5 bucket (STRONG_BULL → STRONG_BEAR). Stessa logica replicata in `domain/regime.py`. Serve come gate: se regime ≤ BEAR nessun long. |
| `daily_signal_engine.pine` | Daily | Rileva trigger di entry in tempo reale (BREAKOUT, PULLBACK, GOLDEN_CROSS, SQUEEZE, DIVERGENCE) che yfinance (EOD) non può vedere. I livelli Entry/Stop/Target vengono dal blocco **TRADINGVIEW PINE INPUTS** stampato da `propicks-scan`. |

### 4B. Indicatori standard da configurare su ogni titolo in watchlist

```
Setup standard per ogni chart:
- Timeframe principale: Daily    (con daily_signal_engine.pine caricato)
- Timeframe conferma: Weekly     (con weekly_regime_engine.pine caricato)

Indicatori nativi (già coperti dai Pine, li aggiungi solo se vuoi conferma visuale):
1. EMA 20 (blu) + EMA 50 (arancione) — trend direction
2. RSI 14 con livelli 30/70 — momentum
3. Volume con media 20 periodi — conferma movimenti
4. ATR 14 — per calcolo stop loss dinamico

Opzionali ma utili:
5. MACD (12,26,9) — conferma trend
6. Bollinger Bands (20,2) — volatilità e squeeze
```

### 4C. Handoff Python → TradingView (entry/stop/target)

Al termine di `propicks-scan [--validate]`, la CLI stampa un blocco:

```
==============================================================
TRADINGVIEW PINE INPUTS — AAPL
==============================================================
Apri il Pine "AI Trading System — Daily" → Settings → Position:
  Entry Price:   185.50
  Stop Loss:     171.50
  Target:        210.00
```

Questi numeri sono pronti da incollare negli input del Pine daily. Se Claude
(`--validate`) ha suggerito un target, viene usato quello; altrimenti solo
entry e stop (ATR-based). Questo è l'unico punto di accoppiamento tra i due
sistemi — nessuna API, nessun webhook.

### 4B. Alert da impostare per ogni scenario

**Alert BREAKOUT (titolo in consolidamento):**
```
Condizione: Close > [LIVELLO RESISTENZA]
E Volume > SMA(Volume, 20) * 1.5
Messaggio: "BREAKOUT ALERT: [TICKER] sopra [LIVELLO] con volume.
            Verifica score Python e check Perplexity prima di entrare."
```

**Alert PULLBACK su supporto (titolo in trend rialzista):**
```
Condizione: Low <= [LIVELLO SUPPORTO / EMA20]
E RSI(14) > 35 (non in oversold profondo)
Messaggio: "PULLBACK ALERT: [TICKER] tocca supporto [LIVELLO].
            Se RSI tiene sopra 35, valuta entry."
```

**Alert STOP LOSS (posizione aperta):**
```
Condizione: Close < [LIVELLO STOP]
Messaggio: "STOP ALERT: [TICKER] ha chiuso sotto lo stop [LIVELLO].
            Esegui vendita all'apertura prossima sessione."
```

**Alert TAKE PROFIT (posizione aperta):**
```
Condizione: Close > [LIVELLO TARGET]
Messaggio: "TARGET ALERT: [TICKER] ha raggiunto il target [LIVELLO].
            Valuta profit taking parziale (50%) e alza stop a break-even."
```

**Alert VOLUME ANOMALO (watchlist generale):**
```
Condizione: Volume > SMA(Volume, 20) * 2.5
Messaggio: "VOLUME SPIKE: [TICKER] volume 2.5x la media.
            Controlla news su Perplexity immediatamente."
```

---

## 5. REGOLE OPERATIVE — Reference Card

### Entry Rules (tutte devono essere vere)
- [ ] Il titolo è nel basket Pro Picks attivo
- [ ] Perplexity conferma catalyst concreto e nessun red flag
- [ ] **Regime weekly >= NEUTRAL** (`propicks-scan` mostra "✓ ENTRY OK", oppure Pine weekly ≥ 3/5). BEAR/STRONG_BEAR blocca l'entry salvo override esplicito.
- [ ] Score tecnico Python >= 60/100
- [ ] Verdict Claude `--validate` = CONFIRM (o CAUTION con conviction ≥ 6/10)
- [ ] Pine daily ha emesso un trigger (BRK/PB/GC/SQZ/DIV) o il setup è chiaramente definito sul grafico
- [ ] Nessuna earnings nei prossimi 5 giorni di trading
- [ ] Posizione size calcolata e coerente con il piano

### Exit Rules (una qualsiasi è sufficiente)
- [ ] Stop loss raggiunto → esci, nessuna eccezione
- [ ] Target raggiunto → prendi profitto parziale (50%), alza stop a break-even
- [ ] Titolo rimosso dal basket Pro Picks → rivedi la tesi, tighten stop
- [ ] Tesi invalidata (catalyst cancellato, news negativa materiale)
- [ ] Claude nella review settimanale dice CLOSE

### Position Sizing
- Capitale totale: [DEFINIRE]
- Max posizioni aperte: 8-10
- Size per posizione: 5-15% del capitale
  - Alta convinzione (score >= 8): 12-15%
  - Media convinzione (score 6-7): 8-10%
  - Bassa convinzione (score < 6): NON ENTRARE
- Cash reserve: minimo 20% sempre disponibile

### Risk Management
- Max loss per singolo trade: 8% della posizione
- Max loss portafoglio settimanale: 5% del capitale totale
- Se raggiungi -5% settimanale: stop trading per il resto della settimana
- Se raggiungi -15% mensile: stop trading, revisione completa strategia
- Regime weekly a BEAR/STRONG_BEAR: `--validate` viene saltato di default (nessun costo API, nessun verdict). Per forzare usa `--force-validate` — ma sappi che stai comprando controtrend.

---

## 5B. THEMATIC ETF — Bucket sperimentale (stock-like)

I tematici (semis SMH/SOXX, biotech XBI/IBB, cybersecurity CIBR/BUG, AI &
robotica ROBO/BOTZ, clean energy ICLN/TAN) **non passano da
`propicks-rotate`**. L'engine sector rotation assume 11 settori GICS
mutuamente esclusivi che sommano al mercato — i tematici violano questa
invariante:

- **Overlap pesante coi parent sector**: SMH ≈ 70% top-10 di XLK
  (NVDA/AVGO/AMD), XBI ≈ 60% biotech-pesante di XLV. Doppio bet camuffato
  da diversificazione.
- **Non mappano su `REGIME_FAVORED_SECTORS`**: semis è sub-industry, non
  GICS sector. Estendere la tabella a temi opinabili (early/late cycle?
  secular?) introduce rumore, non segnale.
- **RS vs `^GSPC` è la metrica sbagliata**: per un tematico l'asse vero è
  RS vs parent sector (SMH vs XLK), non vs SPX.

### Trattamento attuale: stock-like

I tematici di interesse passano da `propicks-scan TICKER` come fossero
single-stock e finiscono nel bucket satellite (max 15% per posizione).
**Quattro regole auto-imposte** (manuali, non codate — vanno rispettate
con disciplina):

1. **Max 2 tematici aperti contemporaneamente.** Verifica con
   `propicks-portfolio status` prima di ogni nuova entry tematica.
2. **Nel campo `--catalyst` del journal scrivi sempre parent sector +
   peso corrente nel portafoglio.** Esempio:
   `--catalyst "Semis secular AI / parent=XLK at 18% / overlap alto"`.
   Serve a forzarti a pensare l'overlap *prima* di entrare, non dopo.
3. **Stop hard 10% invece di 8%.** SMH/SOXX hanno ATR% ~1.8x di XLK; lo
   stop standard ti tira fuori sul rumore. Override manuale al sizing:
   `propicks-portfolio size SMH --entry X --stop Y` con `Y ≈ entry*0.90`.
4. **Hard rule overlap**: `weight(theme) + weight(parent_sector) ≤ 25%`.
   Esempio: se hai XLK al 18% e vuoi aprire SMH, max size SMH = 7% (non 15%).

### Universo curato (max 8-10 nomi considerati)

| Tema | US ticker | UCITS Xetra | Parent sector |
|------|-----------|-------------|---------------|
| Semiconductors | SOXX, SMH | SXRV.DE | technology (XLK) |
| Biotech | XBI, IBB | IS3N.DE | healthcare (XLV) |
| Cybersecurity | CIBR, BUG | R2SC.DE | technology (XLK) |
| AI / robotics | ROBO, BOTZ | XAIX.DE | technology (XLK) |
| Clean energy | ICLN, TAN | IQQH.DE | utilities/industrials |
| China internet | KWEB | (usa US) | discretionary (estero) |
| Aerospace/defense | XAR, ITA | (usa US) | industrials (XLI) |

**Verifica liquidità prima del primo uso.** Molti tematici Xtrackers UCITS
hanno spread orribili su Xetra retail. Se lo spread quotato > 0.3% del
prezzo, usa il listing US.

### Gate quantitativo di promozione a satellite bucket dedicato

Dopo **6 mesi** o **15 trade tematici chiusi** (whichever comes first),
runna:

```bash
propicks-journal stats --strategy ThematicETF
```

Promuovi a subpackage dedicato (`propicks/thematic/` con scoring proprio
RS-vs-parent, CLI `propicks-themes`, dashboard page) **solo se TUTTE** e
quattro le condizioni sono vere:

```
n_trades_thematic                   ≥ 15
win_rate_thematic                   ≥ win_rate_stock_baseline
avg_pnl_thematic                    > avg_pnl_stock_baseline + 0.5%
corr(thematic_ret, parent_sect_ret) < 0.85
```

L'ultima condizione è quella **critica**: se i tematici performano *come*
i parent sector (corr ≥ 0.9), stai solo facendo leveraged sector bet
senza alfa proprio — il satellite bucket dedicato non serve, basta size
più aggressivo su `propicks-rotate`.

**Se le condizioni NON sono soddisfatte:** killa l'esperimento, no sunk
cost. Marca i trade come `--strategy ThematicETF_discontinued` nel journal
e torna a XLK/XLV puri. Il framework vale come scartare un'ipotesi tanto
quanto come confermarla.

---

## 6. CALENDARIO OPERATIVO

### Giorno Update Pro Picks (mensile)
```
□ Scarica lista nuovi ingressi e uscite
□ Per ogni nuovo ingresso:
  □ Esegui prompt Perplexity 2A (o 2B per italiane) — cross-check fondamentale
  □ Esegui propicks-scan TICKER --validate
    ↳ include regime weekly + score tecnico + verdict Claude
    ↳ stampa il blocco TRADINGVIEW PINE INPUTS (entry/stop/target)
  □ Esegui prompt Claude 3A solo se vuoi approfondire (il verdict --validate copre già la tesi)
  □ Classifica: A (azione immediata) / B (watchlist) / C (skip)
    ↳ Skip automatico se regime BEAR/STRONG_BEAR o score < 60 o verdict REJECT
□ Per ogni uscita dal basket:
  □ Se in portafoglio: valuta se tenere con trailing stop o chiudere
□ Se troppi candidati: esegui prompt Claude 3C
□ Carica Pine weekly_regime_engine + daily_signal_engine sui grafici dei titoli A/B
□ Incolla entry/stop/target dei titoli A nei Settings del Pine daily
□ Imposta alert push del Pine daily (BRK/PB/GC/SQZ/DIV)
```

### Giornaliero (15 minuti)
```
□ Controlla alert push dal Pine daily (trigger real-time su watchlist)
□ Se alert scattato su un titolo classe A/B:
  □ Esegui prompt Perplexity 2C (check red flag ultime 24h)
  □ Verifica che il regime weekly sia ancora ≥ NEUTRAL
    (rilancia propicks-scan TICKER se dubbi — il regime cambia lentamente)
  □ Se tutto ok: propicks-portfolio size + add
  □ Logga con propicks-journal add
□ Controlla P/L posizioni aperte (propicks-portfolio status — solo guardare, non agire d'impulso)
```

### Venerdì sera — Review settimanale (30 minuti)
```
□ propicks-portfolio status + propicks-portfolio risk → snapshot P/L e rischio a stop
□ Rilancia propicks-scan su ogni posizione aperta per aggiornare regime weekly
  ↳ se una posizione è scivolata a regime BEAR, tighten stop o chiudi
□ Esegui prompt Claude 3B (revisione settimanale) con la tabella di status
□ Aggiorna stop loss dove necessario (propicks-portfolio update)
□ Aggiorna i livelli negli input del Pine daily se lo stop è stato spostato
□ Controlla se qualche titolo della watchlist si è avvicinato all'entry
□ Calcola P/L settimanale (propicks-journal stats) e aggiorna journal
□ Se un trade è stato chiuso: esegui prompt Claude 3D (post-trade)
```

### Fine mese — Review mensile (1 ora)
```
□ propicks-report monthly → report markdown con performance vs ^GSPC e FTSEMIB.MI
□ Analizza win rate e profit factor dal journal (propicks-journal stats)
□ Identifica pattern: quali strategie Pro Picks performano meglio?
□ Cross-check regime: i trade con verdict CONFIRM su regime STRONG_BULL hanno davvero vinto di più?
□ Rivedi le regole: qualcosa da aggiustare? (ricorda: modifiche al config.py vanno replicate nei Pine)
□ Prepara per il prossimo update Pro Picks
```

---

## 7. METRICHE DA TRACCIARE (Journal Python)

### Per singolo trade
- Ticker, direzione, strategia Pro Picks di origine
- Data e prezzo entry / exit
- P/L in % e in valore assoluto
- Durata del trade (giorni)
- Score Claude (1-10) al momento dell'entry (da verdict `--validate`)
- Score tecnico Python al momento dell'entry
- Regime weekly al momento dell'entry (STRONG_BULL / BULL / NEUTRAL)
- Trigger Pine che ha scatenato l'entry (BRK / PB / GC / SQZ / DIV)
- Motivo entry (catalyst specifico)
- Motivo exit (stop/target/altro)
- Note qualitative post-trade

### Metriche aggregate (calcolate automaticamente)
- Win rate (% trade in profitto)
- Average win vs average loss (profit factor)
- Max drawdown (peggior sequenza di perdite)
- Sharpe ratio semplificato
- Performance per strategia Pro Picks
- Performance per score Claude (i trade con score alto vanno meglio?)
- Tempo medio in posizione per trade vincenti vs perdenti
- Hit rate degli alert TradingView (quanti alert portano a trade?)

---

## 8. NOTE FINALI

### Errori comuni da evitare
1. **Non comprare tutto il basket il giorno dell'update.** Filtra sempre.
2. **Non ignorare lo stop loss.** Mai. Per nessun motivo.
3. **Non aggiungere a una posizione in perdita** (averaging down).
4. **Non fare trading nei primi 30 minuti di apertura** (troppo rumore).
5. **Non entrare su un titolo solo perché sta salendo** senza aver fatto il processo.
6. **Non saltare il journal.** Senza dati non puoi migliorare.

### Quando fermarsi
- Se dopo 3 mesi il win rate è sotto il 40%: rivedi completamente la strategia
- Se il profit factor è sotto 1.0: stai perdendo soldi sistematicamente
- Se scopri che ignori regolarmente le tue stesse regole: fai una pausa

### Evoluzione futura
- Dopo 50+ trade: analizza se la leva 2x migliorerebbe i risultati
- Dopo 100+ trade: valuta automazione parziale degli alert
- Dopo 6 mesi: considera se aggiungere le strategie italiane o togliere quelle che non performano
