# TRADING SYSTEM PLAYBOOK
## AI-Driven Strategy con Pro Picks, Claude, Perplexity & TradingView

---

## 1. WORKFLOW OVERVIEW

```
Pro Picks Update (mensile)
        │
        ▼
┌─────────────────────┐
│  FASE 1: SCREENING  │  ← Perplexity (news + catalyst)
│  Nuovi ingressi     │  ← Claude (analisi qualitativa)
│  basket mensile     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  FASE 2: SCORING    │  ← Motore Python (score tecnico)
│  Classificazione    │  ← TradingView (conferma grafica)
│  A / B / Skip       │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  FASE 3: EXECUTION  │  ← Alert TradingView (timing)
│  Entry + sizing     │  ← Python (position size)
│  Stop + Target      │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  FASE 4: GESTIONE   │  ← Review settimanale
│  Trailing stop      │  ← Journal Python
│  Exit management    │
└─────────────────────┘
```

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

## 4. SETUP TRADINGVIEW — Alert e Indicatori

### 4A. Indicatori da configurare su ogni titolo in watchlist

```
Setup standard per ogni chart:
- Timeframe principale: Daily
- Timeframe conferma: Weekly

Indicatori:
1. EMA 20 (blu) + EMA 50 (arancione) — trend direction
2. RSI 14 con livelli 30/70 — momentum
3. Volume con media 20 periodi — conferma movimenti
4. ATR 14 — per calcolo stop loss dinamico

Opzionali ma utili:
5. MACD (12,26,9) — conferma trend
6. Bollinger Bands (20,2) — volatilità e squeeze
```

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
- [ ] Claude assegna score >= 6/10
- [ ] Score tecnico Python >= 60/100
- [ ] Setup TradingView mostra entry point definito
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

---

## 6. CALENDARIO OPERATIVO

### Giorno Update Pro Picks (mensile)
```
□ Scarica lista nuovi ingressi e uscite
□ Per ogni nuovo ingresso:
  □ Esegui prompt Perplexity 2A (o 2B per italiane)
  □ Esegui score Python
  □ Esegui prompt Claude 3A
  □ Classifica: A (azione immediata) / B (watchlist) / C (skip)
□ Per ogni uscita:
  □ Se in portafoglio: valuta se tenere con trailing stop o chiudere
□ Se troppi candidati: esegui prompt Claude 3C
□ Imposta alert TradingView per tutti i titoli classe A e B
```

### Giornaliero (15 minuti)
```
□ Controlla alert TradingView scattati
□ Se alert scattato:
  □ Esegui prompt Perplexity 2C (check rapido)
  □ Se tutto ok: esegui trade con size calcolata
  □ Logga nel journal Python
□ Controlla P/L posizioni aperte (solo guardare, non agire d'impulso)
```

### Venerdì sera — Review settimanale (30 minuti)
```
□ Aggiorna tabella posizioni con prezzi correnti
□ Esegui prompt Claude 3B (revisione settimanale)
□ Aggiorna stop loss dove necessario
□ Aggiorna alert TradingView
□ Controlla se qualche titolo della watchlist si è avvicinato all'entry
□ Calcola P/L settimanale e aggiorna journal
□ Se un trade è stato chiuso: esegui prompt Claude 3D (post-trade)
```

### Fine mese — Review mensile (1 ora)
```
□ Calcola performance mensile totale
□ Confronta vs S&P 500 e FTSE MIB dello stesso periodo
□ Analizza win rate e profit factor dal journal
□ Identifica pattern: quali strategie Pro Picks performano meglio?
□ Rivedi le regole: qualcosa da aggiustare?
□ Prepara per il prossimo update Pro Picks
```

---

## 7. METRICHE DA TRACCIARE (Journal Python)

### Per singolo trade
- Ticker, direzione, strategia Pro Picks di origine
- Data e prezzo entry / exit
- P/L in % e in valore assoluto
- Durata del trade (giorni)
- Score Claude (1-10) al momento dell'entry
- Score tecnico Python al momento dell'entry
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
