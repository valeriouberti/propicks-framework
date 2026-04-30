# Backtest Guide — come validare la strategia storicamente

Questa guida ti accompagna dal primo `propicks-backtest` al walk-forward
con Monte Carlo per decidere se la strategia ha edge vero o è rumore.

**Non c'è magia nel backtest**. Un backtest brillante non garantisce
niente sul live; un backtest mediocre invece può salvarti dal lanciare
una strategia che perde soldi in produzione. Questa guida ti insegna a
**leggere** i numeri con sospetto sano.

**Per workflow operativo** → [USER_GUIDE.md](./USER_GUIDE.md).  
**Per architettura backtest engine** → [../CLAUDE.md](../CLAUDE.md) sezione "Backtest v2 portfolio-level".  
**Per roadmap** → [NEXT_STEPS.md](./NEXT_STEPS.md).

---

## Indice

1. [Quick start (5 min)](#1-quick-start-5-min)
2. [Le due modalità](#2-le-due-modalità-single-ticker-vs-portfolio-v2)
3. [Il workflow tipico](#3-il-workflow-tipico)
4. [Interpretare i numeri](#4-interpretare-i-numeri)
5. [Walk-forward OOS split](#5-walk-forward-oos-split)
6. [Monte Carlo bootstrap](#6-monte-carlo-bootstrap)
7. [Transaction costs](#7-transaction-costs)
8. [CLI reference](#8-cli-reference)
9. [Dashboard UI](#9-dashboard-ui)
10. [Best practices](#10-best-practices)
11. [Common pitfalls](#11-common-pitfalls--cosa-non-fare)
12. [Troubleshooting](#12-troubleshooting)
13. [Limitations esplicite](#13-limitations--cosa-il-backtest-non-coglie)
14. [**Fase A-D SIGNAL_ROADMAP — survivorship + DSR + cross-sectional**](#14-fase-a-d-signal_roadmap--survivorship--dsr--cross-sectional)

---

## 1. Quick start (5 min)

### Caso 1 — Primo smoke test

```bash
# Single-ticker: più veloce, no portfolio constraint
propicks-backtest AAPL --period 3y

# Portfolio: più realistico, con TC + max positions
propicks-backtest AAPL MSFT NVDA --portfolio --period 3y
```

Output atteso (portfolio mode):
```
PORTFOLIO BACKTEST — Phase 6
Period 2023-04-24 → 2026-04-24 (1096gg)
Capital 10000 → 12450 (+24.5%)
Sharpe ann 1.12  |  Max DD -8.4%  |  N trades 23  |  Win rate 65%
```

### Caso 2 — Con Monte Carlo per robustness

```bash
propicks-backtest AAPL MSFT NVDA GOOGL META AMZN --portfolio \
  --period 5y --threshold 65 --monte-carlo 500
```

Monte Carlo ti dirà se il risultato è **robust** (alto Sharpe + CI stretto)
o **fragile** (alto Sharpe ma CI larghissima = luck).

### Caso 3 — Walk-forward OOS (gold standard)

```bash
propicks-backtest AAPL MSFT NVDA --portfolio \
  --period 5y --oos-split 0.70
```

Train 70% + test 30%. Se test-Sharpe < train-Sharpe → overfitting suspect.

---

## 2. Le due modalità: single-ticker vs portfolio v2

### Legacy single-ticker (`propicks-backtest TICKER`)

**Cosa fa**: per OGNI ticker separatamente, simula la formula scoring
point-in-time. Assume full-cash per trade, 1 posizione per ticker, no TC,
no constraint portfolio.

**Quando usarlo**:
- **Calibrazione iniziale** di un parametro (threshold, stop ATR, time stop)
- **Sanity check** su un singolo ticker (la formula funziona su NVDA?)
- **Debugging** della scoring logic point-in-time

**Output**: metriche trade-level (win rate, profit factor, expectancy)
per ticker + aggregate tra ticker (pool di trade indipendenti).

```bash
propicks-backtest AAPL --period 5y --threshold 60
propicks-backtest AAPL MSFT NVDA --threshold 70 --stop-atr 2.5
```

### Portfolio v2 (`--portfolio`) — **raccomandato per validation seria**

**Cosa fa**: simula UN portfolio che scegli cross-ticker ogni giorno,
rispettando invarianti reali:
- `MAX_POSITIONS=10` simultanee
- Size cap per bucket (15% momentum, 8% contrarian, 20% ETF)
- `MIN_CASH_RESERVE=20%` minimo cash
- Earnings gate opzionale
- **Transaction costs + slippage** (default 5-10 bp)

**Quando usarlo**:
- **Validation pre-production**: la strategia funziona con budget reale?
- **Comparison strategie**: multiple strategie nello stesso framework con costi
- **Gate Phase 7**: prima di aggiungere una strategia nuova, prove di edge

**Output**: metriche portfolio-level (Sharpe annualized, Calmar, max DD sull'
equity curve) + equity curve + exit reasons breakdown.

```bash
propicks-backtest AAPL MSFT NVDA --portfolio --period 5y \
  --threshold 65 --tc-bps 10 --monte-carlo 500
```

### Quale scegliere?

| Scenario | Mode |
|----------|------|
| Devo capire se AAPL è un buon candidato single-name | Legacy |
| Tunare threshold ottimale per momentum | Legacy (iterate fast) |
| Validare la mia strategia complessiva | **Portfolio v2** |
| Gate Phase 7 (nuove strategie) | **Portfolio v2 con OOS + MC** |
| Sanity check pre-release | **Portfolio v2** |

---

## 3. Il workflow tipico

Backtest non è un evento singolo, è un **processo iterativo**. Di solito:

### Fase A — Baseline (10 min)

Misurare come si comporta **senza tuning** la strategia default:

```bash
propicks-backtest AAPL MSFT NVDA GOOGL META AMZN --portfolio \
  --period 5y --threshold 60
```

Annota: total return, Sharpe, max DD, n trades, win rate.

### Fase B — Sensitivity (30 min)

Variare UN parametro alla volta per vedere come reagisce la strategia.
**Non cambiare 3 cose insieme** — non sai cosa ha causato il cambio.

```bash
# Tuning threshold (tieni stop/target/time constant)
propicks-backtest AAPL MSFT NVDA ... --portfolio --threshold 55
propicks-backtest AAPL MSFT NVDA ... --portfolio --threshold 60
propicks-backtest AAPL MSFT NVDA ... --portfolio --threshold 65
propicks-backtest AAPL MSFT NVDA ... --portfolio --threshold 70

# Tuning stop ATR
propicks-backtest AAPL MSFT NVDA ... --portfolio --stop-atr 1.5
propicks-backtest AAPL MSFT NVDA ... --portfolio --stop-atr 2.0
propicks-backtest AAPL MSFT NVDA ... --portfolio --stop-atr 2.5

# Tuning TC (sensitivity)
propicks-backtest ... --portfolio --tc-bps 5   # liquid US
propicks-backtest ... --portfolio --tc-bps 15  # conservative
propicks-backtest ... --portfolio --tc-bps 30  # pessimistic
```

Una strategia che passa da Sharpe 1.5 a 0.3 con TC 15bp non è robusta.

### Fase C — Walk-forward OOS (gold standard)

Dopo aver tunato su Fase B, **valida OOS**:

```bash
propicks-backtest AAPL MSFT NVDA ... --portfolio \
  --period 5y --threshold 65 --stop-atr 2.0 --oos-split 0.70
```

Train su 70% (3.5y), test su 30% (1.5y). Se il test-Sharpe è entro ~20% del
train-Sharpe → OK. Se test-Sharpe crolla → overfitting evidence.

### Fase D — Monte Carlo (robustness check)

Dopo che hai un set di parametri che passa l'OOS, Monte Carlo su tutto il periodo:

```bash
propicks-backtest AAPL MSFT NVDA ... --portfolio \
  --period 5y --threshold 65 --monte-carlo 1000
```

Se CI 95% del Sharpe è `[0.8, 1.5]` con mean 1.1 → robusto. Se è `[-0.5, 2.5]`
con mean 1.0 → luck-dominated.

---

## 4. Interpretare i numeri

### Total return

Semplice: dove è finito il capitale vs initial. **Non guardare solo questo**.
Un total return +50% su 5 anni è CAGR ~8.5%. Un 50% su 3y è ~14.5%.

### CAGR (Compound Annual Growth Rate)

Normalizzazione al tempo. Permette di paragonare periodi diversi.

| CAGR | Giudizio retail |
|------|-----------------|
| > 15% | Eccellente (fund-grade) |
| 10-15% | Buono (sopra S&P passive) |
| 5-10% | Mediocre (circa allineato S&P) |
| < 5% | Sottoperforma il buy&hold |

**Attention**: CAGR su backtest con survivorship bias = overestimate.
Vedi §13.

### Sharpe ratio annualized

**Il numero più importante** dopo CAGR:

```
Sharpe = (mean_return - risk_free) / stdev_return × √252
```

Misura: **return per unità di volatility**. Un portfolio che fa +15% con
vol 20% ha Sharpe 0.75. Uno che fa +12% con vol 10% ha Sharpe 1.2 →
preferibile (più consistency).

| Sharpe ann | Giudizio |
|------------|----------|
| > 2.0 | Very good — top hedge fund |
| 1.0-2.0 | Decent — above market |
| 0.5-1.0 | Mediocre — better than random |
| 0 - 0.5 | Poor |
| < 0 | Losing money |

**Retail realistic**: Sharpe 0.8-1.2 su strategia discretionary + technical
è **ottimo**. Sopra 1.5 in backtest = controlla per curve fitting.

### Sortino ratio

Come Sharpe ma usa solo **downside deviation** (penalizza solo i loss).
Sortino > Sharpe è normale (upside volatility conta come "buona" vol).
Preferito da molti quant perché l'upside non è realmente "rischio".

### Max drawdown

Peggior caduta peak-to-trough sull'equity curve.

| Max DD | Giudizio retail |
|--------|-----------------|
| < -10% | Eccellente (defensive) |
| -10% / -20% | Normale per strategia long equity |
| -20% / -30% | Aggressive — richiede stomaco |
| > -30% | Oltre risk budget retail tipico |

**Trade-off**: tightening stop → max DD migliora MA win rate peggiora.
Balanceing è arte.

### Calmar ratio

```
Calmar = CAGR / |max_drawdown|
```

Quanto rendimento generi per unità di pain. Calmar > 0.5 è decent.
Sopra 1.0 è raro (fund-grade).

### Win rate + profit factor

- **Win rate**: % di trade vincenti. 50% è neutrale. Per mean-reversion
  retail normale è 55-70%. Per momentum retail 40-55%.
- **Profit factor**: sum(wins) / |sum(losses)|. Sopra 1.0 = profittevole.
  Sopra 1.5 = robusta. Sopra 2.0 = eccellente (controlla per bias).

**Combo**: win rate 40% + PF 1.8 ok (asymmetric payoff). Win rate 70% + PF
1.1 ok (small edge frequente). Win rate 70% + PF 0.9 = stai solo perdendo
soldi grandi raramente.

### N trades

Con **meno di 30 trade chiusi**, qualsiasi metrica è rumorosa. Monte Carlo
confermerà CI larghissima. Richiede o più periodo o più ticker in universe.

---

## 5. Walk-forward OOS split

### Cos'è

Dividere il periodo in due:
- **Train (70%)**: finestra dove sei libero di tunare
- **Test (30%)**: misura performance SU DATI MAI VISTI dal tuning

```
period 5y:
├─ train 3.5y ─────────────────┤
                               ├─ test 1.5y ───┤
```

### Perché è gold standard

Il backtest single-pass è **in-sample**: il risultato è ottimistico perché
hai scelto i parametri conoscendo il futuro. OOS test è la cosa più vicina
a "live performance" senza live trading.

### Come interpretare degradation_score

```
degradation_score = test_sharpe - train_sharpe
```

| Score | Interpretazione |
|-------|-----------------|
| ≥ 0 | ✅ OK — test non degrada vs train |
| -0.2 / 0 | 🟡 Warning — qualche degrado ma nei limiti |
| < -0.2 | 🔴 Overfitting suspect — parametri fit al train |

**Action**:
- Score negativo grande → rivedi parametri, magari sei stato troppo aggressivo
- Score vicino a 0 → il sistema è robusto, ship it

### Command

```bash
propicks-backtest AAPL MSFT NVDA --portfolio --period 5y --oos-split 0.70
```

### Tip dal quant

Il 70/30 è standard ma arbitrario. Per strategie con ~10 trade/anno, 70%
di 5y = 3.5y train = ~35 trade. Appena enough. Per strategie più rare,
valuta split 80/20 per dare più training.

---

## 6. Monte Carlo bootstrap

### Cos'è

Dato un set di N trade chiusi, il backtest produce UN percorso equity.
Ma quel percorso è **una realizzazione** dell'ordine storico. In un
universo parallelo i trade si succedono in ordine diverso → equity curve
diversa → metriche diverse.

**Bootstrap**: simula 500-1000 riordini dei trade, ricalcola Sharpe/Win/DD
per ognuno → distribuzione di metriche → CI 95%.

### Perché serve

Distinguere **edge vero** da **luck**:

| Scenario | Sharpe mean | CI 95% | Robusto? |
|----------|-------------|--------|----------|
| Caso A | 1.2 | [0.95, 1.45] | ✅ Sì — CI stretto |
| Caso B | 1.2 | [-0.3, 2.7] | ❌ No — CI larghissima, risultato random |

Nel caso B, in 95/100 universi paralleli avresti potuto avere Sharpe da
-0.3 a 2.7. Il 1.2 "tuo" è solo la media di un distribuzione molto
dispersa. **Edge incerto**.

### Come interpretare robustness_score

```
robustness_score = max(0, min(1, sharpe_CI_lower / sharpe_mean))
```

| Score | Interpretazione |
|-------|-----------------|
| > 0.7 | 🟢 **Robusto** — CI stretto, risultato replicabile |
| 0.4-0.7 | 🟡 **Moderato** — margine incertezza presente |
| < 0.4 | 🔴 **Fragile** — risultato dominato dal luck |

### Command

```bash
propicks-backtest AAPL MSFT NVDA --portfolio --period 5y --monte-carlo 500
```

### Quando usarne 1000 vs 500

- **500 samples**: sufficient per smoke check veloce (5-10s)
- **1000-2000**: per paper finali o decisioni di capitale (~30s)

### Limitation del Monte Carlo

- Non modella **regime shift**: campiona dai trade storici, che includono
  vari regimi. Se i regimi cambiano nel futuro, CI non lo prevede.
- Non cattura **autocorrelation** dei trade: campiona random, ma in
  realtà trade di momentum tendono a clusterare (regime BULL = molti wins
  consecutivi).

Per MVP accettabile. Professional-grade userebbe Stationary Bootstrap
(Politis/Romano 1994) che preserva autocorrelation.

---

## 7. Transaction costs

### Default values (retail IBKR-like)

| Asset class | Commission | Spread (bp) | Slippage (bp) | Roundtrip |
|-------------|-----------|-------------|---------------|-----------|
| Stock US liquid | $0 | 5 | 2 | 9 bp |
| Stock EU (.MI, .DE) | €2 | 10 | 2 | 14 bp + €4 |
| ETF US | $0 | 2 | 2 | 6 bp |
| ETF EU | €2 | 5 | 2 | 9 bp + €4 |

### Override sensitivity

```bash
# Default (5-14 bp roundtrip)
propicks-backtest ... --portfolio

# Conservative (20 bp roundtrip)
propicks-backtest ... --portfolio --tc-bps 10

# Worst case (40 bp roundtrip)
propicks-backtest ... --portfolio --tc-bps 20

# Zero cost (confronto con legacy)
propicks-backtest ... --portfolio --tc-bps 0
```

### Come leggere `Total TC (cost)` nell'output

```
Total TC (cost)      127.35
```

Significa: €127.35 di costo totale (commissioni + spread implicito) sul
periodo. Se initial capital era 10.000, quello è **1.3% drag**.

Su strategia con 50+ trade/anno, drag TC può essere 2-5%/anno. È **grande**
— spiega perché molti edge backtest svaniscono in produzione.

### Rule of thumb

- Se il backtest è positivo **senza** TC e **negativo con TC 10bp** → edge
  troppo sottile per essere tradabile retail
- Se passa ancora con TC 20bp → robusto
- Se passa con TC 30bp → edge eccezionale (controlla per overfitting)

---

## 8. CLI reference

### Argument list completa

```bash
propicks-backtest TICKER [TICKER ...] [OPTIONS]
```

**Common**:
| Option | Default | Effetto |
|--------|---------|---------|
| `--period` | `5y` | Periodo yfinance: `1y`, `3y`, `5y`, `10y`, `max` |
| `--threshold` | `60` | Composite minimo per entry |
| `--stop-atr` | `2.0` | Stop loss in multipli ATR |
| `--target-atr` | `4.0` | Target in multipli ATR |
| `--time-stop` | `30` | Bar max senza progresso |

**Portfolio mode** (Phase 6):
| Option | Default | Effetto |
|--------|---------|---------|
| `--portfolio` | off | **Attiva modalità portfolio** |
| `--tc-bps N` | standard CostModel | Override TC totale (spread+slip) in bps |
| `--oos-split 0.70` | off | Walk-forward train/test (0<X<1) |
| `--monte-carlo 500` | 0 | N samples bootstrap (0 = skip) |
| `--initial-capital` | 10000 | Capitale iniziale portfolio |

**Output**:
| Option | Default | Effetto |
|--------|---------|---------|
| `--json` | off | JSON structured output |
| `--no-trades` | off | Nasconde tabella trade-by-trade |
| `--no-equity` | off | Nasconde ASCII equity curve |

### Esempi annotati

```bash
# Baseline pulito
propicks-backtest AAPL MSFT NVDA --portfolio --period 3y
# → 3y period, threshold 60, stop 2ATR, target 4ATR, TC standard

# Strict: solo setup A+ (threshold 75)
propicks-backtest AAPL MSFT NVDA --portfolio --period 5y --threshold 75

# Wider stop (meno stop-out, più time-stop esit)
propicks-backtest AAPL MSFT NVDA --portfolio --stop-atr 3.0 --time-stop 45

# Conservative TC stress test
propicks-backtest AAPL MSFT NVDA --portfolio --period 5y --tc-bps 20

# Gold standard: OOS + MC con threshold elevato
propicks-backtest AAPL MSFT NVDA GOOGL META AMZN \
  --portfolio --period 5y --threshold 70 \
  --oos-split 0.70 --monte-carlo 1000

# Legacy single-ticker per debug
propicks-backtest AAPL --period 3y --threshold 60 --json
```

---

## 9. Dashboard UI

Page **"Backtest Portfolio v2"** (`propicks-dashboard` → sidebar):

### Form input

- **Universe**: tickers separati da spazio (es. `AAPL MSFT NVDA`)
- **Periodo**: dropdown (1y → max)
- **Score threshold**: slider 40-100
- **Stop/Target ATR**: number input 0.5-20
- **Time stop (bars)**: 5-180
- **Initial capital**: 1000-∞
- **TC (bps)**: 0-100
- **OOS split**: slider 0-0.9 (0 = disabilitato)
- **MC samples**: 0-2000

### Output

1. **KPI cards**: total return, CAGR, Sharpe, max DD (top row) + n trades,
   win rate, profit factor, Calmar (second row)
2. **Equity curve** (line chart)
3. **Drawdown** (area chart)
4. **Exit reasons** table
5. **Per-strategy breakdown** (se > 1 strategia)
6. **Trade table** (sortable, 400px scrollable)
7. **Monte Carlo CI table** + **robustness score emoji**

### Vantaggi dashboard vs CLI

- Visualizzazione equity curve immediata (grafico)
- Form con slider più intuitivo per iterazione
- Output parallelo di OOS + MC + chart in una page

### Limitazioni dashboard

- Tempo di fetch yfinance bloccante (30-60s con universe grande)
- No salvataggio markdown automatico (CLI lo fa)

---

## 10. Best practices

### 🎯 Principi generali

1. **Un parametro alla volta** (sensitivity). Se ne cambi 3 insieme, non
   sai cosa ha causato il delta.
2. **Start conservative**: threshold alto, stop stretto, TC realistic.
   Espandi se la baseline funziona.
3. **Universe diversificato**: 10+ ticker cross-sector riduce
   concentration luck. Non testare solo AAPL MSFT NVDA — sono tutti tech.
4. **Period lungo abbastanza**: min 3 anni per copertura regime.
   Meglio 5-10 anni per multi-cycle.
5. **OOS sempre** prima di decisioni di capitale.
6. **Monte Carlo sempre** se devi decidere se promuovere una strategia.

### 📊 Metriche da guardare nell'ordine

1. **N trades**: sotto 30 → rumore. Stop tuning, allunga periodo.
2. **Sharpe annualized**: è il best single summary metric.
3. **Max DD**: indica lo stomaco necessario. Se tu non reggi un -15%,
   una strategia con max DD -25% **non** è per te (anche se Sharpe è 2).
4. **Win rate + PF**: sanity check. Win rate 30% + PF 0.6 = blow up.
5. **Total TC cost**: se > 3% del capitale è **significant drag**.

### ⚠️ Red flags da fermarsi

- Sharpe > 2.5 su backtest 3y → curve fitting quasi certo
- Win rate > 75% con PF > 3 → troppo bello per essere vero
- Max DD < -5% su universe di 10 ticker 5y → sei troppo restrittivo
  (non tradi mai = no DD ma no return)
- N trades < 15 → dataset insufficiente per conclusioni

### 🔧 Tuning procedure safe

Settaggio consigliato per strategia momentum retail:

```bash
# Step 1: baseline conservativo
propicks-backtest UNIVERSE --portfolio --period 5y --threshold 65

# Step 2: se baseline OK, prova threshold più tight
propicks-backtest UNIVERSE --portfolio --period 5y --threshold 70

# Step 3: OOS con parametri scelti
propicks-backtest UNIVERSE --portfolio --period 5y --threshold 70 --oos-split 0.70

# Step 4: Monte Carlo per robustness
propicks-backtest UNIVERSE --portfolio --period 5y --threshold 70 --monte-carlo 1000

# Step 5: worst-case TC sensitivity
propicks-backtest UNIVERSE --portfolio --period 5y --threshold 70 --tc-bps 20

# Se 2-5 tutti verdi → ship it
```

---

## 11. Common pitfalls — cosa NON fare

### 🚨 Curve fitting

**Sintomo**: continui a cambiare parametri finché il Sharpe sale. Quello
che trovi non ha evidence robusta — è il parametro che massimizza su
quel particolare set di dati.

**Rimedio**: usa `--oos-split`. Se train Sharpe = 2.0 e test Sharpe = 0.3,
hai curve fittato.

**Esempio concreto**: hai 50 trade, testi 20 threshold values (55-75), il
Sharpe migliore è 1.8 con threshold 62. Sembra preciso! Ma hai 20 degrees
of freedom — il tuo "best" threshold è artifact. Un OOS sincero
rivelerebbe Sharpe ~0.8-1.0 random su quell'universe.

### 🚨 Look-ahead bias

**Sintomo**: strategia che usa dati del futuro nel calcolo del segnale
di oggi.

**Rimedio**: il nostro engine è **point-in-time** per design. La
`scoring_fn` riceve solo `hist_slice` fino a bar t. Se tu aggiungi
features future (es. rolling mean su n bar **centered**), stai barando.

### 🚨 Survivorship bias

**Sintomo**: il tuo universe include solo ticker OGGI vivi. Ticker
delisted/mergati/falliti sono invisibili → backtest sovrastima il return.

**Rimedio parziale**: documenta la limitazione. Full fix richiederebbe
CRSP subscription o simili (costano). In retail, accetta come constant
~1-2% drag "invisible" sui backtest.

### 🚨 Period cherry-picking

**Sintomo**: `--period 1y` quando i tuoi return YTD sono ottimi, ma poi
il 5y mostra disastro.

**Rimedio**: **default 5y**. Se vuoi vedere rapido, fai 3y. Mai 1y per
decisioni di capitale.

### 🚨 Universe cherry-picking

**Sintomo**: testi solo i winner ex-post (NVDA +800% vs SPX +40%). Ovvio
che è redditizio.

**Rimedio**: usa l'universe **dichiarato della strategia** (es. basket
Pro Picks del mese scorso, non quello attuale). Minimo 10 ticker
cross-sector. Idealmente un sample random del S&P 500 per avere
diversità.

### 🚨 Ignorare i TC

**Sintomo**: backtest con `--tc-bps 0` (o la mode legacy) mostra Sharpe
1.8. Aggiungi TC 10bp → Sharpe crolla a 0.3.

**Rimedio**: usa **sempre** `--portfolio` che ha TC built-in. Stress test
con `--tc-bps 20` per vedere se regge.

### 🚨 Re-fit continuo

**Sintomo**: ogni settimana cambi i pesi scoring basandoti sui trade
recenti. In 3 mesi hai "ottimizzato" 10 volte — zero signal, tutto fit.

**Rimedio**: fissa i pesi per 6-12 mesi (finestra di calibration
ragionevole). Solo DOPO 30+ trade chiusi per strategia, valuta il
re-calibrate.

---

## 12. Troubleshooting

### "TypeError: Cannot compare tz-naive and tz-aware timestamps"

Fixed in Phase 6 post-release. Update: `pip install -e .` dal repo.

### Backtest produce 0 trade

Cause:
- Threshold troppo alto (prova 55-60 invece di 75)
- Period troppo corto per warmup (servono ≥ 200 bar = ~10 mesi)
- Universe con ticker IPO recenti che non hanno storia
- Regime gate attivo + periodo tutto BEAR

**Debug**: run singolo ticker con threshold basso per verificare la
formula genera signal.

```bash
propicks-backtest AAPL --period 5y --threshold 50
```

### "Dati insufficienti" durante walk-forward

Walk-forward richiede ≥ 100 bar totali. `--period 1y` può essere
insufficiente. Passa a `--period 3y` minimo.

### Monte Carlo CI molto largo (robustness < 0.3)

Non è un bug, è un segnale: hai pochi trade (< 30) → qualsiasi metrica è
rumorosa. Soluzioni:
- Periodo più lungo (`--period 10y`)
- Universe più ampio (10-15 ticker)
- Threshold più basso (più trade, ma attento a false signals)

### Portfolio backtest più lento del single-ticker

È aspettato: con 10 ticker × 5y, l'engine fa score ranking cross-ticker
ogni bar → ~5-15x più lento. Per iterazione veloce, usa single-ticker.
Per validation finale, usa portfolio.

### Yfinance rate-limit

Se vedi `HTTP 429` su fetch:
- Aspetta 15-30 min (Yahoo rate limit window)
- Pre-popola cache: `propicks-cache warm AAPL MSFT NVDA ...`
- Riduci universe size

---

## 13. Limitations — cosa il backtest NON coglie

**Documented esplicitamente perché non siano nascoste**:

### No survivorship bias correction
Ticker delisted non nel set. Backtest **sovrastima** il return reale. Retail
accetta come unavoidable (CRSP data = $5k/anno).

### No earnings gap modeling
Stop su gap post-earnings → l'engine assume fill a stop level, ma il
mercato apre -8% = fill peggiore. **Sottostima** loss in trade con
earnings-triggered drops.

**Workaround**: flag `use_earnings_gate=True` nel portfolio engine
evita entry pre-earnings. Ma non modella exit scenari.

### No corporate actions
Splits + dividendi non applicati. yfinance ritorna prezzi already-adjusted
per splits ma **non** per dividendi. Impact minor su holding 2-8w
(dividendo trimestrale ≈ 0.5-1%). Su holding lunghi, accumula.

### No cash flow timing
L'engine presume cash disponibile il giorno di entry. In realtà broker
settlement è T+2 (US) / T+3 (EU). Effetto: micro-delay nel re-deploy
di cash da exit → impact minimo, ignorable.

### Point-in-time scoring, regime corrente
Il regime classifier usa `^GSPC` weekly **corrente** (non snapshot
storico rielaborato). Se usi `--oos-split`, entrambi train e test usano
lo stesso classifier corrente. In pratica: se il regime oggi pensa che
"2022 era BEAR", quello che il backtest vede non è il regime che il
trader viveva *allora* ma il regime *come lo classifichiamo oggi*.

**Impact**: lieve. La classificazione weekly è robusta across refactor
minori. Documented come accepted.

### Earnings dates mancanti pre-Phase 8
Il backtest portfolio ha `use_earnings_gate=False` default quando
corri `--portfolio` da CLI, perché non abbiamo earnings storici. In
dashboard puoi attivarlo se hai caricato earnings dates manualmente.

### No multi-strategy simulation diretta
Il backtest simula **una** `strategy_tag` alla volta. Per valutare
"momentum + contrarian" nello stesso portfolio (con budget shared),
serve simulazione custom. Future work.

### No leverage / short
Long-only, cash-collateralized. Non modelliamo margin, short selling,
options, futures. **Per design** — il framework è retail direct-stock.

### TC/slippage semplice
Non modelliamo:
- **Market impact** (ordine > 10k€ sposta il mercato) — ignorable retail
- **Intraday volatility** (buy at close, high spread in volatile days)
- **Order book depth** (limit orders unfilled)

Per retail con size retail: marginal. Per large allocators: richiederebbe
microstructure modeling.

---

## 14. Quando fidarsi del backtest

**Checklist pre-production**:

- [ ] Portfolio v2 mode (non legacy single-ticker)
- [ ] Period ≥ 5 anni (copre almeno 1 regime shift)
- [ ] Universe ≥ 10 ticker cross-sector
- [ ] TC realistic (`--tc-bps 10-15`)
- [ ] N trades ≥ 30 (per significance)
- [ ] OOS walk-forward con degradation_score ≥ -0.1
- [ ] Monte Carlo 1000 samples, robustness_score ≥ 0.5
- [ ] Max DD ≤ -15% (o il tuo risk budget)
- [ ] Sharpe ann ≥ 0.8 (retail benchmark)
- [ ] Stress test con TC 20bp ancora positivo

Se tutti ✅ → hai evidence ragionevole. **Non è garanzia** (il futuro non
è il passato), ma è il meglio che il backtest può dirti.

Se 3+ sono ❌ → **non** ship. Continua tuning o accetta che la strategia
non ha edge.

---

## 15. Integration con il gate Phase 7

Il gate Phase 7 ([NEXT_STEPS.md](./NEXT_STEPS.md)) richiede metriche simili:

| Gate criterion | Source |
|---------------|--------|
| ≥ 15 trade chiusi/strategy | Attribution report dal journal reale |
| Profit factor ≥ 1.3 | Backtest + journal (cross-check) |
| Sharpe ≥ 0.8 | Backtest + journal |
| Max DD ≥ -15% | Backtest + journal |

**Pattern**: il backtest è **leading indicator** (puoi avere metriche
già prima dei 15 trade reali). Il journal è **lagging confirmation**.

Quando **backtest** dice edge + **journal** lo conferma su 15+ trade
reali → ok per Phase 7 nuove strategie.

Quando backtest è brillante ma il journal live sotto-performa →
overfitting scoperto nel live = utile learning.

---

## Riferimenti

- **Formula scoring attuale**: `propicks/domain/scoring.py` (momentum)
- **Portfolio engine**: `propicks/backtest/portfolio_engine.py`
- **Cost model**: `propicks/backtest/costs.py`
- **Walk-forward + MC**: `propicks/backtest/walkforward.py`
- **Attribution real**: `propicks/reports/attribution_report.py`

**Letture consigliate** (off-repo, metodologia):
- Aronson, *Evidence-Based Technical Analysis* — Monte Carlo + statistical validation
- Politis & Romano 1994 — stationary bootstrap per time series
- Bailey & López de Prado 2014, "The probability of backtest overfitting" — mathematical framework
- Harvey & Liu 2015, "Backtesting" — data-snooping penalty

---

**Versione guida**: post Phase 6. Copre legacy + portfolio v2.
Ogni bug o ambiguità → issue su GitHub.

---

## 14. Fase A-D SIGNAL_ROADMAP — survivorship + DSR + cross-sectional

Estensioni post-Phase 6 introdotte da [SIGNAL_ROADMAP](SIGNAL_ROADMAP.md).
Tre filoni indipendenti:

### 14.1 Survivorship-correct backtest (Fase A.1)

**Problema**: `--discover-sp500` legge constituents *oggi* da Wikipedia →
look-ahead. Backtest gonfia returns perché esclude ticker delisted (Lehman,
Bear Stearns) e include ticker che *non erano* nell'index allora (TSLA fino
a 2020-12).

**Soluzione**: tabella `index_membership_history` con snapshot mensili
1996-2026 (170k row, 1193 unique ticker mai-stati-S&P). Source: GitHub
`fja05680/sp500` (free, MIT-equivalent).

**Setup una tantum**:

```bash
python scripts/import_sp500_history.py
# → 343 monthly snapshot, 170,764 row in index_membership_history
```

**Uso**:

```bash
propicks-backtest AAPL MSFT NVDA --portfolio --historical-membership sp500
```

Bias misurato su universe 10 ticker / 6y (vedi
[SURVIVORSHIP_BIAS_ANALYSIS](SURVIVORSHIP_BIAS_ANALYSIS.md)):
**+15.4% total return** sovrastimato nel backtest biased. TSLA = 75
phantom trade evitati.

**Limit**: solo SP500 disponibile. STOXX 600 / FTSE MIB / Nasdaq-100
membership history pendenti (no source equivalent fja05680).

### 14.2 DSR + PSR + CPCV (Fase A.2)

**Problema**: Sharpe ratio empirico è stimatore rumoroso. Su sample
finiti + non-normalità returns + multiple testing (threshold sweep), il
Sharpe pubblicato over-states il vero edge.

**Tre tecniche statistical rigor**:

1. **Probabilistic Sharpe Ratio** (Bailey-Lopez 2012): probabilità che il
   vero Sharpe > benchmark dato sample size + skew + kurtosis. Range
   [0, 1]. PSR > 0.95 = 95% confidence.

2. **Deflated Sharpe Ratio** (Bailey-Lopez 2014): PSR deflated by
   `E[max SR | n_trials]`. Corregge per multiple testing (es. threshold
   sweep su 9 valori → DSR severo).

3. **Combinatorial Purged CV** (Lopez de Prado 2018, AFML cap.12):
   genera `comb(N, k)` test path con purging + embargo. Riduce path
   dependency dello stimatore Sharpe.

**Uso via CLI**:

```bash
propicks-calibrate AAPL MSFT NVDA --thresholds "60:80:5" \
    --use-cpcv --historical-membership sp500 --period 5y
```

Output: tabella per threshold con n_trades, Sharpe, PSR, DSR. Recommendation
rule-based (vedi [THRESHOLD_CALIBRATION](THRESHOLD_CALIBRATION.md)).

**API programmatica**:

```python
from propicks.domain.risk_stats import (
    probabilistic_sharpe_ratio, deflated_sharpe_ratio, sharpe_with_confidence,
)
from propicks.backtest.cpcv import cpcv_split, cpcv_dates_split
from propicks.backtest.calibration import calibrate_threshold
```

`compute_portfolio_metrics` ora ritorna anche `psr`, `dsr`,
`sharpe_per_trade_ci_lower/upper`, `n_trials_for_dsr`.

### 14.3 Cross-sectional rank percentile (Fase B.1)

**Problema**: score absolute (`>= 60`) cattura "decent momentum"
universalmente — non distingue regime. In BULL universe medio è 70 →
score 60 è sotto-mediana. Edge momentum vero è cross-sectional (top
quintile vs bottom, Jegadeesh-Titman 1993).

**Uso**:

```bash
# threshold 80 cross-sectional = entry top quintile (P80+) dell'universe ogni giorno
propicks-backtest AAPL MSFT NVDA --portfolio --cross-sectional --threshold 80
```

Edge misurato (universe 10 ticker, 5y):

| Config | Sharpe ann |
|--------|-----------|
| Baseline absolute thr=60 | 0.378 |
| B.1 P80 (top quintile) | 0.616 |
| **B.1 P90 (top decile)** | **0.874** |

Vedi [ABLATION_B1_CROSS_SECTIONAL](ABLATION_B1_CROSS_SECTIONAL.md).

**Caveat scaling**: B.1 non scala bene su universe broader. Top 30 → P80
ottimo. Top 50+ → percentile auto-tuned via
`auto_percentile_for_universe()` (Fase C.0). Top decile P90 estremo
rischia "no trade" su universe < 10.

### 14.4 Workflow consigliato post-Fase A

```bash
# 1. Setup membership (una tantum, ~30s)
python scripts/import_sp500_history.py

# 2. Baseline post-fix
propicks-backtest --portfolio --historical-membership sp500 \
    AAPL MSFT NVDA GOOGL AMZN

# 3. Calibration threshold via DSR
propicks-calibrate --discover-sp500 --top 30 \
    --thresholds "60:80:5" --use-cpcv \
    --historical-membership sp500 --period 5y

# 4. Backtest con threshold ottimo + cross-sectional
propicks-backtest --portfolio \
    --historical-membership sp500 \
    --cross-sectional --threshold 75 \
    AAPL MSFT NVDA GOOGL AMZN

# 5. Re-baseline orchestrato (v1 vs v2 JSON archiviato)
python scripts/baseline_backtest.py --top 50 --period 5y
```

### 14.5 Decision rule + acceptance gate

SIGNAL_ROADMAP §5 B.6 decision rule:
> Mantieni feature solo se +0.10 Sharpe AND DSR p < 0.10 vs baseline_v2.

Acceptance gate end-Fase-A SIGNAL_ROADMAP §9:
> Sharpe gross > 0.4 strategia best, DSR p < 0.10.

Vedi [SIGNAL_ROADMAP](SIGNAL_ROADMAP.md) per status step-by-step + numeri
findings cumulativi.

### 14.6 Cosa NON è cambiato

- TC + slippage realistic ancora out-of-scope (`--tc-bps` semplificato)
- Live broker / execution layer fuori scope
- yfinance fundamentals (B.2 earnings, B.4 quality) **snapshot only** →
  caveat look-ahead bias permanente per backtest historical
- Default `MIN_SCORE_TECH=60` non cambiato — promotion manuale post
  re-validation multi-period
