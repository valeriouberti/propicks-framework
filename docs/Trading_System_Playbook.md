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
│  entry_allowed ≥ NEUTRAL    │  ← propicks-momentum (score 0-100, classe A/B/C/D)
│  score tecnico + classifica │  ← propicks-momentum --validate (Claude, gate su regime+score)
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
| `daily_signal_engine.pine` | Daily | Rileva trigger di entry in tempo reale (BREAKOUT, PULLBACK, GOLDEN_CROSS, SQUEEZE, DIVERGENCE) che yfinance (EOD) non può vedere. I livelli Entry/Stop/Target vengono dal blocco **TRADINGVIEW PINE INPUTS** stampato da `propicks-momentum`. |

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

Al termine di `propicks-momentum [--validate]`, la CLI stampa un blocco:

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
- [ ] **Regime weekly >= NEUTRAL** (`propicks-momentum` mostra "✓ ENTRY OK", oppure Pine weekly ≥ 3/5). BEAR/STRONG_BEAR blocca l'entry salvo override esplicito.
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

I tematici di interesse passano da `propicks-momentum TICKER` come fossero
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

### Universo completo (whitelist)

L'universo è **curato**, non dinamico: circa 35 ETF tematici "istituzionalizzati"
raggruppati per parent sector GICS. Il filtro d'ingresso è hard e non negoziabile
per swing 4-12w:

| Filtro liquidità/maturità | Soglia |
|---------------------------|--------|
| AUM | ≥ $500M (meglio $1B+) |
| Avg daily volume ($) | ≥ $10M/day |
| Bid/ask spread medio | < 0.15% |
| Età fondo | ≥ 3 anni |
| Expense ratio | < 0.75% |
| Top-10 concentration | 40-65% (evita index-hugger e over-concentrati) |

**Non metto in watchlist attiva tutti 35 contemporaneamente** — vedi sezione
*Selezione attiva* più sotto. Ma tutti quelli in tabella sono ammissibili
quando regime + rotation settoriale li giustifica.

#### Technology (parent XLK)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Semiconductors | **SOXX**, SMH, SOXQ | SXRV.DE, SMGB.DE | SMH più concentrato (top-10 ~70%); SOXX più diversificato |
| Software | IGV, XSW | XSDW.DE | IGV top-heavy (MSFT/ORCL/CRM); XSW equal-weight |
| Cybersecurity | **CIBR**, BUG, HACK | R2SC.DE | CIBR più liquido; HACK alternativa storica |
| AI / machine learning | AIQ, IRBO, ARTY | XAIX.DE | Attento a overlap con semis |
| Robotics / automation | **ROBO**, BOTZ, ARKQ | 2B76.DE | ROBO più globale, BOTZ più US |
| Cloud computing | **SKYY**, CLOU, WCLD | FSKY | Overlap alto con IGV |
| FinTech | **FINX**, IPAY, ARKF | XFNT.DE | ARKF ha turnover alto (discrezionale) |
| Blockchain (equity-based) | BLOK, BKCH | BCHN | Solo equity, NO futures-crypto; volatilità estrema |

#### Healthcare (parent XLV)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Biotech | **XBI**, IBB | IS3N.DE | XBI equal-weight (mid-small), IBB cap-weighted (large) — comportamento molto diverso |
| Medical devices | **IHI** | U5MD | Meno volatile dei biotech, defensive-like |
| Genomics | ARKG, GNOM | GNOM | Volatilità alta, selettivo post-2021 bear |
| Pharma | PJP, IHE | BIOT | Difensivo, sub-industry di XLV |
| Cannabis | MSOS, MJ | — | Regolatorio-sensitive, spread larghi — escludere se AUM < $500M |

#### Industrials (parent XLI)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Aerospace / defense | **ITA**, XAR, PPA | DFND  | ITA cap-weighted (BA/RTX/LMT), XAR equal-weight — scegli in base a momentum leader |
| Infrastructure | **PAVE**, IFRA, GRID | INFR | PAVE il più liquido; catalyst da fiscal policy |
| Transport | IYT, XTN | — | Proxy economia reale (railroad, trucking, air freight) |
| Global water | PHO, FIW, CGW | IH2O | Secular ma flow-sensitive |

#### Energy & Materials (parent XLE / XLB)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Clean energy | **ICLN**, TAN, QCLN, PBW | IQQH.DE, INRG.L | TAN è solar-puro (narrow); ICLN diversificato |
| Uranium / nuclear | **URA**, URNM, NLR | NUCL | Catalyst narrativo forte, illiquido fuori US |
| Battery / EV | LIT, DRIV, KARS | BATT | Overlap forte con semis (chip nei veicoli) |
| Rare earth / strategic metals | **REMX**, COPX | REMX | Catalyst geopolitico (Cina supply chain) |
| Gold miners | **GDX**, GDXJ | GDX | GDXJ è junior (2x beta di GDX), hedge inflazione |
| Silver miners | SIL, SILJ | SILV | Volatilità molto alta |

#### Financials (parent XLF)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Regional banks | **KRE**, KBE, IAT | — | Rate-sensitive, attenzione a stress events (SVB 2023) |
| Insurance | KIE, IAK | — | Sub-industry, meno "tematico" ma categoria distinta |
| Capital markets / brokers | IAI, KCE | — | Beta alto su volumi di trading |

#### Communications (parent XLC)

| Sub-tema | US ticker(s) | UCITS Xetra / LSE | Note |
|----------|--------------|-------------------|------|
| Video games / esports | **ESPO**, GAMR, HERO | XGMD.DE | Ciclico, ma catalyst da release major (NVDA GTC, console cycle) |
| Social media | SOCL | — | Overlap pesante con META/GOOGL |

#### Country / regional equity (non GICS, ma bet tematici single-country)

| Sub-tema | US ticker(s) | Note |
|----------|--------------|------|
| China internet / tech | **KWEB**, CQQQ | KWEB è il più puro; regulatory risk sempre latente |
| China broad | FXI, MCHI, ASHR | ASHR = A-shares on-shore |
| India | **INDA**, INDY, SMIN | Secular demographics; premium di valutazione |
| Korea | EWY | Concentration su Samsung (~20%); proxy semis globali |
| Taiwan | EWT | Concentration su TSMC (~20%); duplica semis bet |
| Japan (hedged) | DXJ, HEWJ | DXJ cap copertura FX ¥, utile se dollaro forte |

**Verifica liquidità e wrapper prima del primo uso.** Molti tematici
Xtrackers/UCITS hanno spread orribili su Xetra retail. Se lo spread
quotato > 0.3% del prezzo o lo volume giornaliero < €500k, usa il listing
US. Per i country ETF (KWEB, INDA, EWY, EWT, DXJ) NON esistono equivalenti
UCITS equivalenti 1:1 — usa il listing US direttamente.

### Esclusioni hard (non entrano mai nell'universo)

- **Leveraged/inverse** (SOXL, LABU, TECL, SQQQ, SPXU, ...): path-dependency
  distrugge il rendimento su holding > pochi giorni. Incompatibili con swing 4-12w.
- **Futures-based commodity** (USO, UNG, DBC, DBA, BNO, UGA): contango decay
  erode la performance indipendentemente dal movimento spot. Se vuoi esposizione
  commodity, passa da equity miners (GDX, URA, COPX).
- **Single-currency FX ETF** (UUP, FXE, FXY): non sono tematici, sono
  speculazione FX pura. Fuori dal framework equity.
- **ETF con AUM < $500M o lanciati < 3 anni fa**: lo spread e lo storico
  per RS sono inaffidabili. ES: il "metaverse ETFs" cycle 2021-2022 è
  documentato come top-of-cycle indicator.
- **"Single-stock ETFs"** (NVDL, TSLL, ...): leveraged single-stock,
  path-dependency massima, concettualmente fuori scope.

### Selezione attiva (max 8-10 in watchlist, scelti contestualmente)

L'universo completo ha 30+ nomi, ma la watchlist tematica **attiva** non
dovrebbe mai eccedere 8-10. Il metodo di selezione è **regime-aware** e
**sector-rotation-aware**:

1. **Parti dalla rotation**: esegui `propicks-rotate` e guarda quali parent
   sector sono classe A (OVERWEIGHT) o B (HOLD). Solo i tematici con parent
   in A/B entrano in watchlist attiva.
2. **Regola regime**:
   - **STRONG_BULL / BULL**: considera disruption + high-beta (semis, clean
     energy, fintech, gaming, battery, rare earth, KWEB, biotech small-cap XBI)
   - **NEUTRAL**: preferisci secular + quality (AI, cybersecurity, medical
     devices IHI, infrastructure PAVE, pharma IHE)
   - **BEAR**: solo defensive themes (defense ITA/XAR, cybersecurity CIBR,
     pharma IHE, gold miners GDX come hedge)
   - **STRONG_BEAR**: **nessun tematico aperto**, coerente con la regola
     `suggest_allocation` che va flat.
3. **RS vs parent sector**: per i tematici che superano gli step 1-2,
   controlla manualmente su TradingView il ratio `THEME/PARENT` (es. `SMH/XLK`)
   su weekly. Cerca accelerazione (breakout della RS line sopra l'EMA10w) —
   NON performance assoluta vs SPX, che è tautologica in risk-on.
4. **Cap di diversificazione tematica**: max 2 tematici aperti, con sub-tema
   diverso. ES: se hai SMH aperto (semis), non aprire SOXX in contemporanea
   né IGV (software) se il parent XLK è già sovrappesato.

### Aggiornamento della whitelist

Rivaluta l'universo **ogni 6 mesi** (Jan/Jul):
- Rimuovi ETF che hanno perso AUM sotto $300M o ADV sotto $5M (drift di liquidità).
- Aggiungi nuovi tematici che hanno maturato 3 anni di storia e soddisfano i
  filtri hard.
- Documenta le modifiche nel commit message (`docs: thematic universe review YYYY-H1`).

Non aggiungere tematici "perché stanno performando" — il performance chase
nei tematici è il modo più affidabile per comprare il top del ciclo.

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

## 5C. WATCHLIST — Incubatrice di idee

La watchlist è lo spazio tra lo scanner e l'entry. Non impegna capitale,
non ha regole di sizing, serve a tenere in coda setup che non sono ancora
maturi ma meritano di essere monitorati.

### Popolamento automatico (classe A+B)

`propicks-momentum` aggiunge **automaticamente** alla watchlist ogni ticker
classificato **A** (score ≥75, `"A — AZIONE IMMEDIATA"`) o **B** (60-74,
`"B — WATCHLIST"`). Il comportamento è:

- `source="auto_scan"`, `added_date` = oggi
- snapshot di `score_at_add`, `regime_at_add`, `classification_at_add`
- re-scan dello stesso ticker **aggiorna** i metadati, NON duplica né
  azzera `added_date` / `source` originali
- disabilitabile con `propicks-momentum TICKER --no-watchlist`

**Policy target_entry:**

- **Classe A, nuova entry** → `target_entry = prezzo corrente` (distanza
  0% → il ticker appare subito come **READY** al prossimo `status`). Senso
  operativo: un setup A è tradable ora, la watchlist lo riflette senza
  forzare il trader a settare un target manuale.
- **Classe A, entry già esistente con target** → target preservato. Non
  sovrascriviamo un target impostato manualmente dal trader né un target
  auto-generato da uno scan precedente (il prezzo potrebbe essere salito
  e non vogliamo perdere il livello originale di validazione).
- **Classe B** → senza target. Sta al trader impostarlo quando individua
  il livello specifico (pullback EMA20, breakout di resistenza, gap-fill,
  earnings pullback).
- **Classe C/D** → skip auto-add (sarebbero rumore in watchlist). Restano
  aggiungibili manualmente via `propicks-watchlist add` o via il bottone
  "📋 Aggiungi a watchlist" nella dashboard Momentum per i casi in cui
  *sai* che vuoi tenerlo d'occhio nonostante lo score basso.

### Popolamento manuale

```bash
propicks-watchlist add AAPL --target 185.50 --note "pullback EMA20"
propicks-watchlist update AAPL --target 190
propicks-watchlist remove AAPL
```

Usa `--target` per registrare il livello di entry atteso (breakout,
pullback su EMA, support level). La nota è testo libero per il catalyst
o la ragione dell'attesa.

### Ready signal

```bash
propicks-watchlist status
```

Mostra per ogni entry: prezzo corrente, distanza % dal target, score
live, regime corrente. Un entry è **READY** quando:

- Score corrente ≥ 60 (setup ancora valido, non è decaduto)
- `|current − target| / target ≤ 2%` (prezzo arrivato al livello)

Il flag READY **non** apre la posizione. È segnale visivo che invita a:

1. `propicks-momentum TICKER --validate` (re-analisi completa + Claude)
2. Verifica regime weekly (entry gate)
3. `propicks-portfolio size` + `propicks-portfolio add`

### Pulizia (stale cleanup)

`propicks-watchlist list --stale` (o tab **Stale** nella dashboard) mostra
entry da più di **60 giorni**. Regola operativa: se un setup non si è
materializzato in 2 mesi, la tesi era sbagliata o il regime è cambiato.
Rimuovi in blocco dalla dashboard o via CLI.

### Workflow integrato

```
propicks-momentum TICKER                   → se classe A o B: auto-add a watchlist
  ↓                                      (A con target=price → subito READY)
(aspetti giorni/settimane)               (B senza target → setti manualmente)
  ↓
propicks-watchlist status              → vedi flag READY su TICKER
  ↓
propicks-momentum TICKER --validate        → re-analisi completa + Claude
  ↓
propicks-portfolio size + add          → apertura
  ↓
propicks-watchlist remove TICKER       → rimozione manuale post-entry
propicks-journal add TICKER ...        → log del trade
```

La rimozione dalla watchlist **non** è automatica quando apri la
posizione: è un'azione esplicita del trader per evitare stati inconsistenti
se decidi di chiudere la posizione e volerla tenere ancora monitorata.

---

## 6. CALENDARIO OPERATIVO

### Giorno Update Pro Picks (mensile)
```
□ Scarica lista nuovi ingressi e uscite
□ Per ogni nuovo ingresso:
  □ Esegui prompt Perplexity 2A (o 2B per italiane) — cross-check fondamentale
  □ Esegui propicks-momentum TICKER --validate
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
    (rilancia propicks-momentum TICKER se dubbi — il regime cambia lentamente)
  □ Se tutto ok: propicks-portfolio size + add
  □ Logga con propicks-journal add
□ Controlla P/L posizioni aperte (propicks-portfolio status — solo guardare, non agire d'impulso)
```

### Venerdì sera — Review settimanale (30 minuti)
```
□ propicks-portfolio status + propicks-portfolio risk → snapshot P/L e rischio a stop
□ Rilancia propicks-momentum su ogni posizione aperta per aggiornare regime weekly
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
