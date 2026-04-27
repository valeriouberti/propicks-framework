# Strategia Contrarian — Riassunto Teorico

> Quality-filtered mean reversion: comprare qualità *temporaneamente* venduta, non
> scommettere sul ribasso e non raccogliere coltelli che cadono.

---

## 1. Tesi di fondo

La strategia contrarian è il **motore parallelo** alla strategia momentum/quality.
Le due strategie hanno tesi **opposte**:

| Aspetto       | Momentum                              | Contrarian                                  |
|---------------|---------------------------------------|---------------------------------------------|
| Cosa cerca    | Forza che accelera                    | Qualità temporaneamente oversold            |
| Trigger entry | Breakout / pullback su trend up       | RSI < 30 + stretch ATR sotto EMA50          |
| Target        | Trailing (lasci correre)              | Reversion a EMA50 (target fisso)            |
| Holding       | 2–8 settimane                         | 5–15 giorni                                 |
| Posizione     | Long                                  | Long (mai short)                            |

**Principio chiave:** la contrarian **non è anti-trend**. Il trend strutturale
(EMA200 weekly) deve essere **intatto**. Si compra il *dip* dentro un *uptrend
strutturale*, non il *crash* di un *downtrend*.

---

## 2. I 5 filtri obbligatori

Un setup è valido solo se **tutti** i filtri passano:

1. **Oversold tecnico** — RSI(14) < 30 strict (o <35 warm), price ≥ 2×ATR sotto
   EMA50, almeno 3 sedute rosse consecutive (o un drawdown 5d ≥ 1.5×ATR).
2. **Trend strutturale intatto** — price ≥ EMA200 weekly. Sotto → hard gate:
   composite azzerato (è downtrend, non mean reversion).
3. **Market context favorevole** — VIX > 25 (paura) bonus, VIX < 14 (euforia)
   penalty. Regime weekly NEUTRAL ideale, BULL/BEAR ok, skip STRONG_*.
4. **Qualità aziendale** — universe filter (Pro Picks basket o watchlist
   curata). Enforced nel CLI / workflow trader, non nel domain puro.
5. **Fundamental non rotto** — validazione Claude `flush_vs_break`:
   FLUSH = tradable, BREAK = REJECT.

---

## 3. La discriminante centrale: FLUSH vs BREAK

Un titolo a -20% in una settimana può esserlo per **6 ragioni diverse**.
Solo 2-3 sono mean reversion tradable.

### Tradable (FLUSH)
- **macro_flush** — risk-off market-wide (Fed, geopolitica, VIX spike). Le qualità
  vengono trascinate giù dal tape. Setup più pulito.
- **sector_rotation** — flow fuori dal settore (es. AI rotation out of semis).
  Il singolo nome è ok, il settore è sotto pressione. Tradable ma il timing
  della reversion può richiedere il flip del flow.
- **technical_only** — nessun catalyst news, pure flow/chart action. Spesso il
  flush più puro, ma serve verificare che non ci sia news silenziosa.

### Non tradable (BREAK)
- **earnings_miss_fundamental** — miss su revenue/margini con deterioramento
  reale. Multiple compression richiede 2-3 trimestri.
- **guidance_cut** — guidance forward tagliata. Lo Street sta ancora rivedendo
  giù le stime. = falling knife.
- **fraud_or_accounting** — SEC inquiry, restatement, whistleblower. **REJECT
  with prejudice**: mai mean-revertare la frode.

### Borderline (MIXED)
- Flush element sopra una marginale debolezza fondamentale → size down,
  horizon più corto.

> **Regola operativa Claude**: se la web search non identifica chiaramente la
> causa del selloff, default a `technical_only` con nota esplicita. **Assenza
> di evidenza di break NON è evidenza di assenza** — non assumere mai FLUSH
> senza prova.

---

## 4. Scoring engine (composite 0-100)

```
composite = oversold * 40% + quality * 25% + market_context * 20% + reversion * 15%
```

### 4.1 Oversold (40%)
Tre dimensioni combinate (max 100 richiede tutti e tre):
- **RSI** (0-40 pts): <30 = 40, <35 = 25, <40 = 10
- **Distanza ATR da EMA50** (0-40 pts): ≥3 ATR = 40, ≥2 ATR = 30, ≥1 ATR = 15
- **Capitulation** (0-20 pts): max tra drawdown 5d in ATR e consecutive_down.
  Cattura sia *flush verticali* (1 big red candle) sia *slow bleed* (5+ piccole).

### 4.2 Quality (25%) — hard gate
- Sotto EMA200 weekly → **score = 0** → composite forzato a 0 (no falling knives)
- Sopra EMA200w: modulato sulla profondità della correzione
  - Sweet spot -10% / -25% dal 52w high = 100
  - <-5% = 30 (troppo poco stretched)
  - >-40% = 20 (rischio downtrend, non più pullback)

### 4.3 Market context (20%)
- Lookup `CONTRA_REGIME_FIT` (regime fit inverso al momentum)
- Aggiustamento VIX: ≥25 = +20, ≤14 = -30

### 4.4 Reversion R/R (15%)
- Reward = `EMA50 - price`, Risk = `price - stop`
- R/R ≥ 3.0 → 100, ≥ 2.0 → 80, < 1.0 → 10 (setup rotto)

---

## 5. Regime fit INVERSO al momentum

| Regime         | Momentum                | Contrarian                       |
|----------------|-------------------------|----------------------------------|
| 5 STRONG_BULL  | CONFIRM plausibile      | **SKIP** (fit 25) — no oversold  |
| 4 BULL         | tailwind                | workable (fit 70) — BTFD regime  |
| 3 NEUTRAL      | CAUTION default         | **sweet spot** (fit 100)         |
| 2 BEAR         | REJECT default          | ok se quality regge (fit 85)     |
| 1 STRONG_BEAR  | skip                    | **SKIP** (fit 0) — falling knives|

**Hard gate:** in STRONG_BULL e STRONG_BEAR il composite viene forzato a 0
(`apply_regime_cap`). L'edge contrarian collassa agli estremi del ciclo.

---

## 6. Invarianti di sizing (più strette del momentum)

| Parametro                           | Momentum | Contrarian |
|-------------------------------------|----------|------------|
| Size max per posizione              | 15%      | **8%**     |
| Max posizioni simultanee nel bucket | —        | **3**      |
| Max esposizione aggregata bucket    | —        | **20%**    |
| Stop loss                           | ATR × 2  | `recent_low - 3×ATR` |
| Max loss per trade (warning)        | 8%       | **12%**    |
| Target                              | trailing | **EMA50 fisso** (no trail) |
| Holding tipico                      | 2-8 sett | **5-15 giorni** |
| Time stop                           | 30 gg    | **15 gg**  |

> **Cap globale `MAX_POSITIONS=10` condiviso**: momentum + contrarian + ETF
> insieme non possono superare 10 posizioni aperte. Il bucket contrarian ha
> un cap interno indipendente (3 max).

**Perché size più piccola?** Hit rate strutturalmente più basso del momentum
(setup short-gamma). Il sizing al 8% riflette la maggiore probabilità di
loss per singolo trade.

---

## 7. Classificazione

| Tier | Score   | Significato                                          |
|------|---------|------------------------------------------------------|
| A    | ≥ 75    | OVERSOLD READY — entry con size piena (8%)           |
| B    | 60–74   | OVERSOLD INCUBATING — entry ridotta o wait           |
| C    | 45–59   | MARGINAL — non abbastanza tirato o quality marginale |
| D    | < 45    | SKIP — trend rotto, market bullish, R/R inadeguato   |

---

## 8. AI validation (`ai/contrarian_validator.py`)

Persona del prompt: **senior event-driven / mean-reversion PM**, NON momentum
trader. Focus: **flush vs break**.

### Schema verdict (`ContrarianVerdict`)
- `verdict`: CONFIRM / CAUTION / REJECT
- `flush_vs_break`: FLUSH / BREAK / MIXED
- `catalyst_type`: 7 categorie
- `reversion_target` (take-profit specifico)
- `invalidation_price` (hard stop)
- `time_horizon_days` (3-30, tipico 5-15)
- `entry_tactic`: MARKET_NOW / LIMIT_BELOW / SCALE_IN_TRANCHES / WAIT_STABILIZATION
- 5 confidence dimensions (quality_persistence, catalyst_type_assessment,
  market_context, reversion_path, fundamental_risk)

### Regole verdict
- **CONFIRM** richiede: FLUSH (o MIXED clean), quality_persistence ≥ 7,
  catalyst_type_assessment ≥ 7, conviction ≥ 7, R/R ≥ 2:1.
- **CAUTION** = MIXED, o low catalyst confidence, o market_context debole.
- **REJECT** = BREAK, o STRONG_BULL/STRONG_BEAR regime, o fundamental risk alto.

### Web search bias
3-5 query mirate alla **causa del selloff** (NON al catalyst forward come nel
momentum). Cerca: news ultime 5-15 gg, dettagli earnings, reazione analisti,
peer action, regulatory flags. Se inconcluso → `technical_only` con nota.

### Cache
Chiave separata `<TICKER>_contra_v1_<YYYY-MM-DD>` (TTL 24h). Lo stesso ticker
può essere scansionato da momentum e contrarian lo stesso giorno senza
collisione di verdict.

---

## 9. Non-goal espliciti

La strategia contrarian **NON include**:

- ❌ **Short selling** — tutte le posizioni restano long
- ❌ **Pair trading / long-short** — single-leg only
- ❌ **Crypto / futures** — universo invariato (Pro Picks basket)
- ❌ **Averaging down** — se lo stop è triggered, la posizione si chiude.
  Un nuovo setup richiede un nuovo trade.
- ❌ **Rebalance automatico su regime change** — il gate inverso si applica
  all'**entry** (decisione di apertura), non come trigger di chiusura. Le
  posizioni aperte si chiudono a target EMA50, stop hard, o time stop 15gg.

---

## 10. Workflow operativo end-to-end

### 10.1 Manual flow (ticker conosciuti)

```
1. propicks-contra TICKER             → analisi + composite + classificazione
2. propicks-contra TICKER --validate   → Claude flush vs break
3. propicks-portfolio size TICKER \
     --entry X --stop Y --contrarian   → sizing 8% cap + gate 3-pos / 20%
4. propicks-portfolio add TICKER \
     --strategy "Contrarian — <type>"  → apertura posizione
5. propicks-journal add ...            → log con strategy tag
6. Exit: target EMA50 / stop hard / time stop 15gg
```

### 10.2 Discovery flow (universe-wide screening)

```
1. propicks-contra --discover-sp500 --top 10 --min-score 60
   → fetch S&P 500 da Wikipedia (cache 7gg)
   → stage 1 prefilter cheap (RSI<35, distance≥1×ATR) ~10s su 500 nomi
   → stage 2 full scoring sui ~30 sopravvissuti
   → ranking top N per composite, classificazione A/B/C/D
2. propicks-contra TICKER --validate   → AI validation sui top picks
3. → step 3-6 del manual flow
```

**Pipeline a 3 stadi a costo decrescente:**
- Stage 1 (~5-15ms/ticker, cache-hit): RSI + distanza ATR da daily — elimina 80-90%
- Stage 2 (~200-400ms/ticker): full `analyze_contra_ticker` con weekly + regime + R/R
- Stage 3 (post-scoring): ranking + tagliato a `--top N`

**Costo tipico:** ~$1-2/giorno se schedulato daily con `--validate`, latency 5-10 min con cache calda. Output sempre in summary table compatta.

**Universe management:** Wikipedia è la fonte de facto, parsata via `pandas.read_html`. Cache SQLite TTL 7gg. Sanity check: < 480 nomi → fallback su cache stale → fallback su snapshot hardcoded `SP500_FALLBACK` (~50 mega-cap stabili). Mai hardcoded da soli — Wikipedia è sempre il primary path.

---

## 11. Filosofia in una frase

> **"Compra qualità che il mercato sta vendendo per la ragione sbagliata,
> nella finestra corta in cui il prezzo è disconnesso dalla tesi
> fondamentale, con size che riflette il rischio asimmetrico di sbagliare
> la diagnosi flush-vs-break."**
