# Strategia Momentum / Quality — Riassunto Teorico

> Strategia core stock momentum: identificare titoli di qualità che mostrano
> **forza che accelera** (trend up + momentum + breakout) entro un regime macro
> favorevole, validata qualitativamente da Claude come secondo paio di occhi.

---

## 1. Tesi di fondo

La strategia momentum cerca **trend in atto, non reversal**. Comprare quello
che già sta salendo, *non* quello che è caduto. Differenze chiave vs contrarian:

| Aspetto       | Momentum                              | Contrarian                                  |
|---------------|---------------------------------------|---------------------------------------------|
| Cosa cerca    | Forza che accelera                    | Qualità temporaneamente oversold            |
| Trigger entry | Breakout / pullback su trend up       | RSI < 30 + stretch ATR sotto EMA50          |
| Target        | Trailing (lasci correre)              | Reversion a EMA50 (target fisso)            |
| Holding       | 2-8 settimane                         | 5-15 giorni                                 |
| Regime ideale | BULL / NEUTRAL                        | NEUTRAL / BEAR (con quality intatta)        |
| Posizione     | Long                                  | Long (mai short)                            |

---

## 2. Scoring engine (composite 0-100)

`domain/scoring.py` calcola **6 sub-score ortogonali** combinati con pesi che
sommano a 1.0 (`config.py`):

```
composite = trend*25% + momentum*20% + volume*15% + distance_high*15% + volatility*10% + ma_cross*15%
```

### 2.1 Trend (25%) — `score_trend(close, ema_fast, ema_slow)`
Posizione del prezzo vs EMA20 / EMA50. Sopra entrambe = trend up confermato.

### 2.2 Momentum (20%) — `score_momentum(rsi)`
RSI(14) sweet spot 50-70 (trend in salita non ipercomprato). Eccessi puniti.

### 2.3 Volume (15%) — `score_volume(current, avg_20d)`
Volume conferma: breakout su volume = 100; rally low-volume = penalità.

### 2.4 Distance from 52w high (15%) — `score_distance_from_high(close, high_52w)`
Vicino all'high = forza dimostrata (-5% / -10% = sweet spot). Troppo vicino
all'high (ATH puro) = potenziale exhaustion.

### 2.5 Volatility (10%) — `score_volatility(atr, close)`
ATR% del prezzo. Volatility moderata premia stabilità del trend; troppo bassa
= no edge, troppo alta = noise.

### 2.6 MA cross (15%) — `score_ma_cross(close, ema_fast, ema_slow, history)`
Golden cross recente / strutturale. Discrimina trend nuovi (entry timing) da
trend vecchi (rischio rollover).

---

## 3. Classificazione

| Tier | Score   | Significato                                                |
|------|---------|------------------------------------------------------------|
| A    | ≥ 75    | AZIONE IMMEDIATA — entry con conviction HIGH (12% size)    |
| B    | 60-74   | WATCHLIST — entry MEDIA o wait per pullback                |
| C    | 45-59   | NEUTRAL — non entry, monitoring                            |
| D    | < 45    | AVOID — trend non confermato o sotto soglia minima         |

Soglia minima per entry: `MIN_SCORE_TECH = 60` (gate hard in `add_position`).

---

## 4. Gate AI — `ai/thesis_validator.py`

Validazione qualitativa **opt-in** via `--validate`. Doppio gate prima della
chiamata Claude:

1. **Score gate**: `score_composite ≥ MIN_SCORE_TECH (60)` → sotto, skip
2. **Regime gate**: regime weekly **≥ NEUTRAL** (code ≥ 3). BEAR (2) e
   STRONG_BEAR (1) skippano `--validate` automaticamente. Override con
   `--force-validate`.

### 4.1 Schema verdict (`ThesisVerdict`)

- `verdict`: CONFIRM / CAUTION / REJECT
- `conviction_score`: 0-10
- `thesis_summary`, `bull_case`, `bear_case`, `key_catalysts`, `key_risks`
- `invalidation_triggers` (specifici, falsificabili)
- `time_horizon`: 1-3M / 3-6M / 6-12M
- `alignment_with_technicals`: STRONG / MIXED / CONTRADICTORY
- `entry_tactic`: MARKET_NOW / LIMIT_PULLBACK / WAIT_VOLUME_CONFIRMATION / SCALE_IN
- `reward_risk_ratio`, `stop_rationale`, `target_rationale`
- 6 confidence dimensions (business_quality, narrative_catalysts,
  sector_macro_fit, crowding_sentiment, risk_asymmetry, technicals_alignment)

### 4.2 Cache + sanity

- Chiave: `<TICKER>_v4_<YYYY-MM-DD>` (TTL 24h)
- Sanity layer: R/R < 2.0 → CONFIRM downgraded a CAUTION; R/R < 1.0 → REJECT
  per qualsiasi verdict (setup strutturalmente rotto).

### 4.3 Web search

Tool `web_search_20250305` server-side Anthropic per spot, earnings date, news
recenti, analyst revisions. Costo $0.01/ricerca + token. Max uses
configurabile via `PROPICKS_AI_WEB_SEARCH_MAX_USES`.

---

## 5. Peer Relative Strength (stock vs sector ETF)

`analyze_ticker` arricchisce l'output con il campo **`rs_vs_sector`** (dict con
`score`/`rs_ratio`/`rs_slope`/`peer_etf`) — la forza relativa del titolo
contro il proprio Select Sector SPDR. Serve a distinguere i leader del settore
dai passeggeri del trend: NVDA +40% YTD vs SPX dice poco se l'intero XLK ha
fatto +35%.

### 5.1 Gating architetturale

- **Solo US tickers** (`domain.stock_rs.is_us_ticker`). Per `.MI`/`.DE`/`.L`/`.PA`/...
  il campo è `None`: la rotazione geografica inquinerebbe il segnale (es.
  ISP.MI vs XLF US mescola banche italiane e banche USA).
- Mapping GICS via `yf.Ticker(t).info['sector']` → `SECTOR_KEY_TO_US_ETF`
  (Technology→XLK, Energy→XLE, ecc.). La taxonomy Yahoo differisce da GICS
  puro (es. "Consumer Cyclical" per "Consumer Discretionary"): vedi
  `YF_SECTOR_TO_KEY` per le normalizzazioni.
- Engine: riuso diretto di `etf_scoring.score_rs` (stessa formula level×slope
  su 26w / EMA10w). Nessuna duplicazione di logica.

### 5.2 Informativo, non nel composite

Il campo **non** entra nello score tecnico 0-100. Calibrare un 7° sub-score
richiederebbe ri-validare i pesi esistenti sui trade storici. Se emerge
correlazione forte tra `rs_vs_sector.score` alto e winner nel journal, si può
promuovere a sub-score con reshuffling dei pesi.

### 5.3 Overhead

Aggiunge 2 chiamate yfinance per ticker US (`.info` + weekly del peer ETF). Su
batch scan grandi ci sono ripetizioni — se diventa un collo di bottiglia,
cache del weekly ETF al livello di CLI/dashboard (9 download invece di N×9).

---

## 6. Invarianti di sizing momentum

| Parametro                           | Valore        |
|-------------------------------------|---------------|
| Size max per posizione              | **15%**       |
| Conviction HIGH (avg_score ≥ 80)    | 12% target    |
| Conviction MEDIUM (avg_score ≥ 60)  | 8% target     |
| Stop loss                           | ATR × 2       |
| Max loss per trade (warning)        | **8%**        |
| Target                              | trailing      |
| Holding tipico                      | 2-8 settimane |
| Time stop (flat)                    | 30 gg         |

**Cap globale `MAX_POSITIONS=10` condiviso** con contrarian + ETF.

---

## 7. Pipeline end-to-end (Perplexity → Python → TradingView)

La pipeline è **manuale** ma con contract rigidi tra gli stadi:

```
Pro Picks (mensile)
  → Perplexity 2A/2B (news + catalyst, cross-check fondamentale)
  → propicks-scan --validate          ← regime weekly + score + verdict Claude
  → copy/paste TRADINGVIEW PINE INPUTS nei settings del Pine daily
  → Pine daily (timing real-time: BRK/PB/GC/SQZ/DIV → alert push)
  → Perplexity 2C (check red flag ultime 24h)
  → propicks-portfolio size + add
  → propicks-journal add
```

### 7.1 Consistency garantita da

- `domain/regime.py` = replica Python del Pine weekly (stessa classificazione 5-bucket)
- `tradingview/*.pine` hanno header che punta a `config.py` come source of
  truth per EMA/RSI/ATR/volume/soglie
- `propicks-scan` stampa sempre il blocco Pine-ready a fine output così il
  trader copia-incolla i livelli invece di digitarli
- Il gate regime in `validate_thesis` impedisce chiamate Claude quando il Pine
  weekly direbbe NO ENTRY

### 7.2 Workflow di integrazione AI manuale (alternativa a `--validate`)

1. **Scanner** → produce output strutturato che il trader incolla nel prompt Claude 3A
2. **Portfolio status** → tabella per il prompt Claude 3B
3. **Journal stats** → dati per il prompt Claude 3D (post-trade analysis)
4. **Report** → sommario formattato come contesto per qualsiasi prompt

In alternativa al copia/incolla manuale, `propicks-scan --validate` chiama
direttamente l'API Anthropic e restituisce verdict strutturato. Il prompt di
sistema è statico (prompt caching abilitato), il contenuto dinamico nel user
prompt per non invalidare la cache lato server.

> **Il prompt Perplexity resta in pipeline come cross-check indipendente**: il
> prompt 2C (check news/earnings ultime 24h) viene eseguito manualmente prima
> dell'entry anche se Claude ha già dato CONFIRM. Perplexity e Claude hanno
> fonti e bias diversi — la ridondanza è intenzionale, non overhead.

---

## 8. CLI `propicks-scan`

```bash
propicks-scan AAPL                              # singolo ticker
propicks-scan AAPL MSFT NVDA                    # batch
propicks-scan AAPL --validate                   # + Claude validation (gate score≥60 + regime≥NEUTRAL)
propicks-scan AAPL --force-validate             # bypass gate + cache
propicks-scan AAPL --json                       # output JSON
propicks-scan AAPL MSFT --brief                 # solo summary
propicks-scan AAPL --strategy TechTitans        # strategy tag custom
propicks-scan AAPL --no-watchlist               # disabilita auto-add classe A+B
```

---

## 9. Filosofia in una frase

> **"Compra qualità che ha già iniziato a correre nel regime macro giusto, con
> conferma multi-timeframe (weekly regime + daily score + Pine intraday) e
> validazione qualitativa indipendente, accettando che il prezzo ti pagherà
> per aver aspettato il setup invece di anticiparlo."**
