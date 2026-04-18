# Framework Operativo Settimanale — ETF + Stock

Framework operativo integrato per il trading system. Coordina tre time-horizon
che convivono: **basket Pro Picks mensile**, **rotation ETF settimanale**,
**entry stock alert-driven**. Il lunedì è il punto di sincronizzazione.

Budget tempo totale: ~3-4h/settimana attive + alerts Pine infrasettimana.
Su 10k di capitale è il massimo sostenibile senza trasformare il trading in
un secondo lavoro.

> **Nota:** tutti i comandi `propicks-*` citati in questo documento hanno
> equivalente UI nella dashboard Streamlit (`propicks-dashboard` oppure
> `docker compose up -d`). Scanner, ETF Rotation, Portfolio, Journal e
> Reports sono le pages corrispondenti. CLI e dashboard usano la stessa
> business logic — scegli l'interfaccia che preferisci, lo stato su
> `data/` è condiviso.

---

## Allocazione capitale di riferimento

Su `CAPITAL = 10_000` (config.py):

| Allocazione | % capitale | Note |
|-------------|------------|------|
| **Core ETF WORLD** | 40-60% | 3 settori × 15-20% (cap 20% per ETF, 60% aggregato) |
| **Satellite single-stock** | 25-35% | 2-4 posizioni Pro Picks alta convinzione, max 15% ciascuna |
| **Cash reserve** | ≥ 20% | Hard invariant — mai sotto |

L'allocazione si sposta col regime:
- **STRONG_BULL/BULL:** 50-60% ETF + 25-35% stock
- **NEUTRAL:** 40-50% ETF + 25-30% stock + extra cash
- **BEAR:** 15% ETF (solo top-1 difensivo) + 15-20% stock + resto cash
- **STRONG_BEAR:** 0% ETF + 0-10% stock + cash dominante

---

## Cadenza MENSILE — primo lunedì del mese (~90min)

**Pro Picks basket refresh.** Il momento dove rimpiazzi la watchlist.

```bash
# 1. Salva il nuovo basket in data/baskets/YYYY-MM.json
#    (formato JSON con ticker per strategy)

# 2. Scan batch sul basket completo per strategia
propicks-scan AAPL MSFT NVDA AMZN GOOGL META --strategy TechTitans --brief
propicks-scan JPM BAC WFC V MA --strategy DominaDow --brief
propicks-scan ... --strategy BattiSP500 --brief
propicks-scan ENI.MI ISP.MI UCG.MI --strategy MiglioriItaliane --brief

# 3. Selezione: tieni solo score >= 60 E regime >= NEUTRAL

# 4. Per i top 3-5 per strategy → validazione Claude
propicks-scan AAPL --validate
propicks-scan NVDA --validate
# ... prendi solo verdict CONFIRM + conviction >= 7

# 5. Crea/aggiorna la watchlist TradingView con alerts Pine daily_signal
```

**Prompt Perplexity 2A/2B** sui top candidati (news + catalyst) come cross-check
indipendente a Claude. Non saltarli: Claude e Perplexity hanno bias diversi,
la ridondanza è voluta.

**Output del refresh:** watchlist di **max 8-12 ticker totali** (2-3 per
strategia), ognuno con Pine alert configurato. Meno è meglio — 12 è il massimo
gestibile con ritmo settimanale disciplinato.

---

## Cadenza SETTIMANALE

### Lunedì 18:00-19:15 — "Setup Week" (~75min)

#### Parte A — Macro + ETF (~30min)

```bash
propicks-rotate --region WORLD --allocate
propicks-rotate --region US                 # cross-check leadership
propicks-portfolio status
propicks-portfolio risk
```

**Decisioni ETF:**

| Trigger | Azione |
|---------|--------|
| Regime weekly cambiato vs settimana scorsa | Flag → probabile rebalance |
| Delta top-pick WORLD > 10 punti vs settimana scorsa | Rebalance in 2-3 tranche (mar-gio) |
| Posizione ETF fuori top-3 ma ancora classe B+ | Hold, non forzare uscita |
| Posizione ETF scesa a classe C/D | Exit pianificato in 2-3 sessioni |
| `risk` mostra loss settimanale > 5% | **Stop trading**, solo gestione stop/target |

Se regime change o size rilevante → `propicks-rotate --region WORLD --validate`
per macro view Claude (cache 48h, ~$0.15).

#### Parte B — Stock watchlist health-check (~30min)

```bash
# Re-scan veloce di tutta la watchlist (solo tabella, no validate)
propicks-scan AAPL MSFT NVDA JPM V ENI.MI ISP.MI --brief
```

**Decisioni stock:**

| Trigger | Azione |
|---------|--------|
| Score sceso da >=60 a <60 | Rimuovi da watchlist, **disabilita Pine alert** |
| Earnings entro 5 giorni (`EARNINGS_WARNING_DAYS`) | Flag "no new entry" fino a post-earnings |
| Regime weekly sceso a BEAR | Freeze tutte le entry stock, gestisci solo aperte |
| Nuovo candidato da Perplexity 2A/2B mid-month | Scan + validate, se CONFIRM → aggiungi |

#### Parte C — Update stop su posizioni aperte (~15min)

Per ogni posizione stock aperta con P&L > +5%:

```bash
propicks-portfolio update TICKER --stop NEW_STOP
```

**Trailing stop rules (stock, più aggressivi degli ETF):**

| P&L | Stop target |
|-----|-------------|
| >= +5% | break-even + 1% |
| >= +10% | +3% (locked-in profit) |
| >= +20% | +10% **O** chiusura parziale 50% |

**ETF:** mantieni stop -5% fisso (meno whipsaw sulla bassa volatilità).

---

### Martedì-Giovedì — "Execution" (alert-driven)

Non aprire il terminale per guardare il mercato. Il trigger di engagement è
**solo** un alert Pine push.

#### Stock entry workflow (~10-30min per alert)

```bash
# 1. Alert Pine arriva push → 15min di tempo per decidere
propicks-scan TICKER --validate          # score + Claude + web search
# → se CONFIRM + conviction >= 7: continua
# → se CAUTION/REJECT: salta

# 2. Perplexity prompt 2C (red flag ultime 24h) — SEMPRE, anche con Claude CONFIRM

# 3. Sizing
propicks-portfolio size TICKER --entry X --stop Y --score-claude N --score-tech M

# 4. Apertura
propicks-portfolio add TICKER --entry X --shares N --stop Y --target Z \
  --strategy TechTitans --score-claude N --score-tech M

# 5. Journal (append-only, source of truth)
propicks-journal add TICKER long --entry-price X --entry-date YYYY-MM-DD \
  --stop Y --target Z --strategy TechTitans --score-claude N --score-tech M \
  --catalyst "<breve: earnings beat, guidance raise, ecc>"
```

#### ETF tranche (se rebalance deciso lunedì)

Esegui la tranche prevista con **ordini limite**, non mercato. Le rotazioni
settoriali non sono mai urgenti.

#### Regola ferrea di disciplina

Se non chiudi validate → size → add in **15 minuti per uno stock, salta il
trade**. La FOMO sui singoli trigger è la causa più frequente di violazione
regole di rischio.

Per un ETF hai tutta la settimana — nessun motivo di correre.

---

### Venerdì EOD — "Close & Check" (~20min)

```bash
propicks-portfolio status
propicks-portfolio risk
propicks-journal list --open
```

**Check obbligatori:**
- `risk` → loss settimanale > 5%? → **stop trading lunedì prossimo** (hard invariant)
- Qualche posizione ha toccato stop durante la settimana? → verifica chiusura, aggiorna journal
- Ultimo venerdì del mese? → `propicks-report monthly`

**Stock-specifici:** check earnings date settimana successiva. Su posizioni
con earnings entro 5 giorni:

- Chiudere metà posizione prima del report (se P&L >= +10%)
- Hold full (se posizione piccola o conviction alta)
- Non aprire nuove posizioni su quel ticker

---

### Sabato mattina — "Review & Reflect" (~45min)

Il momento dove si impara. Senza questo ritmo il journal è solo data entry.

```bash
propicks-report weekly

# Stats per strategia — fondamentale per capire dove stai vincendo
propicks-journal stats                        # aggregato
propicks-journal stats --strategy TechTitans
propicks-journal stats --strategy DominaDow
propicks-journal stats --strategy BattiSP500
propicks-journal stats --strategy MiglioriItaliane

# Chiusure settimana → post-trade analysis
propicks-journal list --closed
```

**Quattro domande fisse** (scrivile, non solo pensale):

1. **Quale strategy ha performato meglio questa settimana/mese?** Stai
   allocando verso lì o sei inerziale?
2. **Quali violazioni di framework?** (size > 15%, skippato validate,
   entry senza alert Pine, ignorato earnings warning)
3. **Stock vs ETF:** dove sta il tuo edge? Se single-stock performano ma
   ETF no (o viceversa) → tilt allocazione prossimo mese.
4. **Regime macro sta cambiando?** Confronta output `rotate` vs lunedì scorso.

Su ogni trade chiuso: **Claude prompt 3D** (post-trade analysis) → cosa
mostrava il setup 2 settimane dopo? Pattern ricorrenti?

---

### Domenica sera — "Prep Next Week" (~15min)

- **Calendar macro:** CPI, FOMC minutes, ECB, payrolls. Blocca i giorni
  a rischio (niente nuove entry 24h prima di eventi binari).
- **Earnings calendar:** chi reporta settimana prossima nella watchlist?
  Flag per `EARNINGS_WARNING_DAYS`.
- **Se primo del mese in arrivo:** prepara il nuovo basket Pro Picks.
- **Nulla di più.** Non guardare grafici. Ricarichi capacità mentale per lunedì.

---

## Budget tempo totale

| Slot | Tempo | Quando |
|------|-------|--------|
| Mensile basket refresh | ~90min | Primo lunedì del mese |
| Lunedì Setup Week | ~75min | Ogni settimana |
| Alert execution | 10-30min × 1-4 alerts | Martedì-Giovedì |
| Venerdì Close & Check | ~20min | Venerdì EOD |
| Sabato Review | ~45min | Sabato mattina |
| Domenica Prep | ~15min | Domenica sera |
| **Totale settimana** | **~3-4h** | senza contare mensile |

Se supera 5h/settimana stai overtrading — riduci watchlist o alza soglia
conviction.

---

## Regole di disciplina cross-asset

### 1. Non aprire stock se gli ETF sono in sofferenza aggregata

Se il core ETF WORLD è a -3% nella settimana, il mercato ti sta dicendo
qualcosa. **Sospendi nuove entry stock per 3-5 sessioni** anche se Pine
lancia alert.

### 2. Hard invariants (no override)

- Max posizioni aperte: **10**
- Max size singola posizione: **15%** (stock) / **20%** (ETF)
- Max esposizione aggregata ETF: **60%**
- Min cash reserve: **20%**
- Max loss per trade: **8%** (stock) / **5%** hard stop (ETF)
- Max loss settimanale: **5%** → stop trading
- Max loss mensile: **15%** → stop trading + revisione
- Score minimo entry: Claude **>= 6/10**, Tecnico **>= 60/100**

### 3. Eccezione legittima per rompere il framework

**Evento di mercato straordinario** (flash crash, evento geopolitico,
Fed emergency cut). In quel caso:

1. Apri il portfolio
2. Metti stop a break-even su tutte le profit
3. Aspetta **48h** prima di qualsiasi nuova entry
4. Il framework riparte il lunedì successivo

### 4. Regola di coerenza framework ↔ tools

- Ogni entry deve essere passata da `propicks-portfolio add` (validation hard)
- Ogni trade deve essere registrato in `propicks-journal add` (append-only)
- Ogni chiusura deve essere registrata in `propicks-journal close`
- Nessuna entry manuale dal broker senza corrispondente riga nel journal

Il journal è la **source of truth** per valutare la strategia. Se buchi
le entry, le metriche `propicks-journal stats` diventano inutili.

### 5. Thematic ETF — bucket sperimentale dentro il satellite single-stock

I tematici (SMH/SOXX, XBI/IBB, CIBR, ROBO, ICLN, KWEB, XAR) **non passano
da `propicks-rotate`**: l'engine rotation assume 11 GICS settori
mutuamente esclusivi e i tematici overlap pesantemente coi parent sector
(SMH ≈ 70% top-10 di XLK). Trattamento attuale **stock-like** via
`propicks-scan` + `propicks-portfolio add`, dentro il budget satellite
single-stock (max 15%/posizione).

**Quattro regole auto-imposte** (manuali, da rispettare con disciplina):

1. **Max 2 tematici aperti contemporaneamente** (`propicks-portfolio status`
   per verifica).
2. **Campo `--catalyst` del journal**: scrivi sempre parent sector + peso
   corrente nel portafoglio. Es. `"Semis / parent=XLK at 18%"`.
3. **Stop hard 10%** (vs 8% standard) — ATR% dei tematici è ~1.8x dei
   parent sector, lo stop default ti stoppa sul rumore.
4. **Hard rule overlap**: `weight(theme) + weight(parent_sector) ≤ 25%`.
   Esempio: XLK al 18% → max SMH = 7%, non 15%.

**Gate di promozione** a subpackage dedicato dopo 6 mesi / 15 trade
tematici chiusi: vedi `Trading_System_Playbook.md` §5B per le 4 condizioni
quantitative (win rate, avg P&L vs baseline, correlation < 0.85 col
parent sector). Se non soddisfatte → killa l'esperimento, no sunk cost.
