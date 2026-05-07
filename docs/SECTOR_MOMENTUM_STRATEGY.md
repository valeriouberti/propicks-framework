# Strategia Sector-Filtered Momentum (SFM) — Riassunto Teorico

> Strategia ibrida top-down + bottom-up: l'engine ETF Rotation seleziona i
> settori OVERWEIGHT (composite ≥ 70 = classe A), poi il momentum scoring
> identifica i top stock dentro il settore vincente. Edge atteso da
> **industry momentum** (Moskowitz-Grinblatt 1999) + **intra-industry
> winners** (Asness-Porter-Stevens 2000).

---

## 1. Tesi di fondo

SFM combina due segnali documentati in letteratura:

1. **Industry momentum** (Moskowitz-Grinblatt 1999, *"Do Industries Explain
   Momentum?"*, JF): i settori in trend up tendono a continuare 3-12 mesi.
   Gli autori mostrano che ~60% dell'edge momentum classico (Jegadeesh-Titman
   1993) è spiegato da industry momentum, non da momentum idiosincratico.
2. **Intra-industry winners** (Asness-Porter-Stevens 2000): dentro un settore
   vincente, i top stock per peer-RS battono l'ETF settoriale di **200-400 bps
   annui** in trend regimes. La dispersione cross-sectional intra-settore è
   alta — comprare l'ETF cattura beta settoriale, comprare i leader cattura
   sia beta che alpha.

### Differenze chiave vs strategie esistenti

| Aspetto         | Momentum                  | ETF Rotation                | **SFM**                          | Contrarian                  |
|-----------------|---------------------------|-----------------------------|----------------------------------|-----------------------------|
| Cosa cerca      | Stock leader globale      | Settore leader              | **Stock leader DENTRO settore leader** | Quality oversold        |
| Top-down        | No (universe-wide)        | Sì (sector ranking)         | **Sì (gate ETF rotation)**       | No (universe-wide)          |
| Bottom-up       | Sì (6 sub-score stock)    | No (solo ETF)               | **Sì (6 sub-score + peer-RS)**   | Sì (4 sub-score)            |
| Edge primario   | Multi-factor stock        | Macro + breadth             | **Peer-RS slope intra-settore**  | RSI + EMA200w               |
| Drawdown atteso | -8% (stop)                | -5%                         | **-6% (high-beta premium)**      | -12%                        |
| Holding         | 2-8 sett                  | 4-8 sett                    | **2-6 sett**                     | 5-15gg                      |
| Regime ideale   | BULL / NEUTRAL            | qualsiasi (con cap)         | **BULL / NEUTRAL**               | NEUTRAL / BEAR              |

**SFM non è**:
- Mean reversion / contrarian (è momentum amplificato).
- Una sostituzione per ETF rotation (è complementare — ETF cattura sector beta,
  SFM cattura sector beta + intra-sector alpha).
- Una bet contro il sector ranking (sector preso come dato).

---

## 2. Pipeline (3 stadi)

`domain/sector_momentum.py` orchestra:

### Stage 1 — Top-down (rotate-driven mode)
1. `etf_scoring.rank_universe(region="US")` → 11 ETF settoriali ordinati
2. `select_top_sectors(top_n=2, min_score=70)` → settori OVERWEIGHT (classe A)

### Stage 2 — Universe filter
1. `get_index_universe_detailed("sp500")` → S&P 500 con metadata sector
2. `filter_universe_by_sector(detailed, sector_key)` → 50-100 stock per settore
3. `normalize_sector_to_key()` mappa Wikipedia GICS ("Information Technology")
   + Yahoo varianti ("Technology", "Consumer Cyclical") in `sector_key`
   interno (lowercase, snake_case).

### Stage 3 — Bottom-up momentum
1. `discover_momentum_candidates` (riusa pipeline 3-stadi momentum):
   prefilter cheap (RSI ≥ 45, dist 52w-high ≤ 35%) → full scoring → top N
2. `enrich_with_sfm_score`: aggiunge `score_sfm` come overlay peer-RS:

```
score_sfm = score_composite × (1 - w) + peer_rs_score × w
```

Default `w = SFM_RS_OVERLAY_WEIGHT = 0.20` (composite × 80% + peer-RS × 20%).

### Mode B — Sector-explicit (manual override)
Salta Stage 1 (rotate ranking). Util per:
- Backtest deterministici (sector fissato)
- Override discrezionale ("voglio sondare tech anche se rotate non lo dà A")
- Debug intra-settore

---

## 3. Scoring formula

```
score_sfm ∈ [0, 100]
        = base_composite × (1 - SFM_RS_OVERLAY_WEIGHT)
        + peer_rs_score × SFM_RS_OVERLAY_WEIGHT
```

dove:
- `base_composite` = 6 sub-score momentum classico (trend, momentum, volume,
  distance_high, volatility, ma_cross — vedi MOMENTUM_STRATEGY.md §2)
- `peer_rs_score` = RS vs sector ETF, da `stock_rs.score_rs_vs_sector` →
  riusa `etf_scoring.score_rs` (level × slope, 26w lookback, EMA 10w slope)

**Razionale overlay 20%**: il composite momentum classico è già un buon proxy
dell'edge stock-specifico (45% del peso = trend + momentum, 30% del peso =
volume + distance_high). Il peer-RS aggiunge la dimensione cross-sectional
intra-settore senza ridurre troppo il peso degli altri sub-score. Test
empirici (TODO Fase 2 backtest) calibreranno il weight ottimale.

### Esempi numerici

| Stock | Composite | Peer-RS | SFM (w=0.20) | Class. |
|-------|-----------|---------|--------------|--------|
| AAPL  | 80        | 90      | 82.0         | A      |
| MSFT  | 85        | 60      | 80.0         | A      |
| TECH-laggard | 78 | 30      | 68.4         | B      |

AAPL e MSFT hanno score classic simili (80 vs 85), ma AAPL è leader intra-settore
(peer-RS 90) → SFM premia AAPL come prima scelta. TECH-laggard ha composite alto
(78 = classe A momentum standalone) ma peer-RS basso (30 = passenger trade) →
SFM downgrade a 68.4 (classe B watchlist).

---

## 4. Classificazione

Stessa scala del momentum (compatibilità Pine sync, dashboard):

| Tier | Score (SFM) | Significato                                                  |
|------|-------------|--------------------------------------------------------------|
| A    | ≥ 75        | AZIONE IMMEDIATA — leader confermato, entry con conviction   |
| B    | 60-74       | WATCHLIST — leader emergente, wait per pullback / confirm    |
| C    | 45-59       | NEUTRAL — passenger risk, monitoring solo                    |
| D    | < 45        | AVOID — segnale rotto                                         |

**Gate hard SFM CLI** (`SFM_MIN_STOCK_SCORE = 75`): solo classe A ammessa nel
discovery default. Più stretto del momentum standalone (60) perché in SFM
il filtro top-down ha già ridotto l'universo ai settori OVERWEIGHT — vogliamo
solo i top stock dentro, non i borderline.

---

## 5. Gate AI — `ai/sfm_validator.py`

Validazione qualitativa opt-in via `--validate`. **Tre gate hard** prima di
spendere budget AI:

### 5.1 Score gate
`score_sfm ≥ SFM_MIN_STOCK_SCORE (75)` → sotto, skip.

### 5.2 Regime gate
Regime weekly **≥ NEUTRAL** (code ≥ 3, `entry_allowed = True`). STRONG_BEAR
skippa anche con sector composite alto (cap esistente, ma residual signal
inaffidabile in crisi).

### 5.3 Passenger gate (SFM-specific)
`peer_rs.score < 60 AND peer_rs.slope ≤ 0` → skip. La tesi SFM senza
peer-RS leadership degrade a sector beta puro — comprare l'ETF è più economico.
Quando questo gate trigger, l'output dashboard mostra badge 🟡 *passenger risk*
con suggerimento di sostituire con altro nome del settore.

### 5.4 Schema verdict
**Riusa `ThesisVerdict`** (compat con momentum). 6 confidence dimensions
identiche, ma il system prompt SFM-specifico le interpreta diversamente:

- `business_quality`: durable franchise advantage **vs sector peers** (non vs
  market). Un nome con moat ma simile a tutti i peers = score basso qui.
- `narrative_catalysts`: catalyst **idiosincratico** che compounds il sector
  tailwind (no "tech is up").
- `sector_macro_fit`: SAME macro driver del settore? Mismatch = passenger.
- `crowding_sentiment`: consensus pick intra-settore = late stage.
- `risk_asymmetry`: high-beta drawdown atteso → R/R ≥ 2.0 floor enforce.
- `technicals_alignment`: peer-RS slope confirma o deteriora?

### 5.5 Cache
Chiave: `<TICKER>_<SECTOR>_sfm-v1_<YYYY-MM-DD>` (sector_key incluso per
invalidare su GICS reshuffle, es. META Tech→Communications 2018).
TTL 24h (`SFM_AI_CACHE_TTL_HOURS`). Strategy tag `sfm` separato dalla cache
momentum (stesso ticker valutato con prior diverso).

### 5.6 Sanity post-AI
R/R floor 2.0 riusato da `thesis_validator._enforce_reward_risk` (DRY).
CONFIRM downgrade a CAUTION se R/R < 2.0 dopo recompute aritmetico.

---

## 6. Invarianti (hardcoded in `config.py`)

| Constant                              | Valore  | Razionale |
|---------------------------------------|---------|-----------|
| `SFM_MAX_AGGREGATE_EXPOSURE_PCT`      | 25%     | Bucket cap: somma SFM positions |
| `SFM_MAX_POSITION_SIZE_PCT`           | 10%     | Vs 15% momentum — beta inflation premium |
| `SFM_MAX_STOCKS_PER_SECTOR`           | 3       | Evita over-concentration intra-bucket |
| `SFM_CROSS_BUCKET_SECTOR_CAP_PCT`     | 35%     | Sum SFM + ETF rotation + momentum stesso settore |
| `SFM_MAX_LOSS_PER_TRADE_PCT`          | 6%      | Vs 8% momentum — high-beta drawdown |
| `SFM_RS_OVERLAY_WEIGHT`               | 0.20    | Peer-RS weight in score_sfm |
| `SFM_MIN_SECTOR_SCORE`                | 70      | Solo settori classe A OVERWEIGHT |
| `SFM_MIN_STOCK_SCORE`                 | 75      | Solo stock classe A intra-settore |
| `SFM_DEFAULT_TOP_SECTORS`             | 2       | Diversification vs concentration trade-off |
| `SFM_AI_CACHE_TTL_HOURS`              | 24      | Stesso TTL momentum |

### 6.1 Cross-bucket sector cap (enforcement)

`io/portfolio_store.add_position` applica il cap quando `sector_key` è passato:

1. Risolve sector per ogni posizione esistente:
   - **Path A**: `position["sector_key"]` salvato esplicitamente (SFM, ETF rotation, o momentum con flag)
   - **Path B**: resolver runtime via `etf_universe.get_sector_key(ticker)` per ETF +
     `yfinance.get_ticker_sector(ticker)` + `normalize_sector_to_key` per stock.
     Se resolver ritorna `None` (sector sconosciuto), posizione esclusa
     (conservativo: non penalizza per metadata mancante).
2. Somma esposizione del settore target.
3. Se `(current + nuova) > 35%` → ValueError.

Posizioni legacy (pre-migration) hanno `sector_key = NULL`. La migration
aggiunge la colonna ma non backfilla — il resolver runtime gestisce.

### 6.2 Esempi enforcement

```bash
# OK: SFM tech 10% + momentum tech 15% + ETF XLK 8% = 33% < 35%
propicks-portfolio add MSFT --entry 100 --shares 15 --stop 95 \
    --strategy Momentum --sector-key technology
propicks-portfolio add AAPL --entry 100 --shares 10 --stop 96 \
    --strategy SFM --sector-key technology
propicks-portfolio add XLK  --entry 100 --shares 8  --stop 95 \
    --strategy "ETF rotation" --sector-key technology

# BLOCCA: aggiungere altro tech porta sector aggregato a 38% > cap 35%
propicks-portfolio add NVDA --entry 100 --shares 5  --stop 95 \
    --strategy SFM --sector-key technology
# → ValueError: Cross-bucket sector cap...
```

---

## 7. CLI + dashboard

### 7.1 CLI — `propicks-sector-momentum`

```bash
# Mode A — rotate-driven (default): scopri top 2 settori → top 3 stock ognuno
propicks-sector-momentum

# Custom: top 3 settori, top 5 stock, peer-RS overlay 30%
propicks-sector-momentum --top-sectors 3 --top-stocks 5 --rs-weight 0.30

# Mode B — sector esplicito: skip rotation
propicks-sector-momentum --sector XLK
propicks-sector-momentum --sector technology --top-stocks 5

# Validation AI (SFM-specific prompt + 3 gate hard)
propicks-sector-momentum --validate

# Force bypass tutti i gate
propicks-sector-momentum --validate --force-validate

# Output JSON / brief
propicks-sector-momentum --json --brief
```

### 7.2 Dashboard — page `15_Sector_Momentum.py`

Equivalent UI completo, parallela a Momentum / ETF Rotation. Tab:
- **Rotate-driven**: form con top_sectors / top_stocks / rs_weight / min_sector_score / min_stock_score
- **Sector esplicito**: dropdown sector_key + top_stocks

Per-ticker expander mostra:
- Score SFM + score momentum + regime badge
- **Peer-RS deep dive** (4 metric: score, ratio, slope, peer ETF) con badge
  🟢 leader confermato / 🟡 passenger risk
- 6 sub-score momentum (trend, momentum, volume, dist_high, volatility, ma_cross)
- Trade params (entry, stop, max loss SFM, max size SFM)
- Earnings warning + watchlist add button
- AI validation panel (verdict + confidence_by_dimension + bull/bear case)

Sidebar `invariants_note(strategy_bucket="sfm")` mostra:
- SFM positions count + bucket exposure live
- Regole SFM-specifiche (cap 25%, 10%, 3-per-sector, 6%, cross-bucket 35%)

---

## 8. Architettura — layer separation

```
domain/sector_momentum.py     ← Puro: scoring + filter + orchestrator (no I/O)
ai/sfm_prompts.py             ← System prompt SFM + render user prompt
ai/sfm_validator.py           ← Cache + 3 gate + R/R sanity
cli/sector_momentum.py        ← Argparse thin wrapper
dashboard/pages/15_*.py       ← Streamlit UI parallela
io/portfolio_store.py         ← add_position con sector_key + 3 SFM gate + cross-bucket
domain/sizing.py              ← Helper: is_sfm_position, sfm_aggregate_exposure,
                                sfm_positions_in_sector, sector_aggregate_exposure
```

Nessuna dipendenza circolare. `domain/sector_momentum` non importa da
`io/`, `ai/`, `cli/`, `dashboard/`. Test puri offline (mock fetch + analyze).

### 8.1 Schema DB

`positions.sector_key TEXT` — colonna aggiunta via migration in `_apply_migrations`
(idempotente). Posizioni legacy hanno `NULL`, non backfillate (resolver runtime).

---

## 9. Trade-off accettati

### 9.1 Universe S&P 500 only (fase 1)

**Limitazione**: NASDAQ-100, STOXX 600, FTSE MIB non supportati come universe SFM.

**Razionale**:
- `stock_rs.SECTOR_KEY_TO_US_ETF` mappa solo XL* (US Select Sector SPDR).
  EU/STOXX600 richiederebbe peer ETF EU (ZPDT.DE etc.) + currency normalize.
- `index_membership` ha point-in-time history solo per S&P 500 (fja05680
  dataset). NASDAQ100 / STOXX600 → backtest survivorship-corrected impossibile.
- STOXX 600 mid-cap thin liquidity → peer RS noisy.

**Roadmap**:
- Fase 2 NASDAQ100 (1gg lavoro: stesso GICS, stessi peer XL*)
- Fase 3 STOXX600 (3-5gg: ICB→GICS normalizer, ZPD* peer mapping, EUR/USD)

### 9.2 No factor concentration enforcement (oltre sector)

SFM cap sector ma NON cap factor (growth vs value vs quality vs low-vol).
Nel BULL trend tipico SFM concentra su growth high-beta (es. XLK + AAPL +
NVDA + MSFT = tutti high-beta growth). Drawdown in regime shift può essere
1.5-2x ETF.

**Mitigazione attuale**: stop tighter (6% vs 8%) + bucket cap 25%.
**Mitigazione futura**: factor exposure cap (vedi `domain/exposure.py` per
beta-weighted). Out of scope fase 1.

### 9.3 Peer-RS weight 20% non calibrato empiricamente

`SFM_RS_OVERLAY_WEIGHT = 0.20` è un default ragionevole basato su pattern
overlay precedenti (earnings revision Fase B.2 = 0.20, quality QMJ Fase B.4 =
similar range). **Non è ottimizzato su backtest SFM**.

**Roadmap Fase 2**: walk-forward + DSR per calibrare il weight ottimale per
universe / regime. Range esplorazione 0.10-0.40.

### 9.4 Schema risposta AI riusa ThesisVerdict (no nuovi field)

Pro: zero breaking change, zero refactor pydantic schema, cache compatible.
Contro: alcuni field SFM-specifici (peer-RS confirmation, passenger flag,
late-rotator stage) vivono solo nel system prompt instructions, non in
strutture machine-readable. Dashboard estrae info dal prompt → fragile a
prompt drift.

**Razionale**: tradeoff accettato per fase 1. Se in produzione vogliamo
machine-readable peer-RS verdict, aggiungere `SFMVerdict(ThesisVerdict)` con
`peer_rs_status: LEADER|MID|PASSENGER` + `late_rotator: bool`. Out of scope
fase 1.

---

## 10. Backtest (Fase 2 — TODO)

Aspettative empiriche da literatura:

- **Sharpe target**: 0.9-1.2 (vs momentum standalone 0.6-0.8 fase B.6 baseline).
- **DSR ≥ 0.95** dopo multi-trial penalty (Bailey-Lopez 2014, Fase A.2).
- **Max DD**: 18-25% (high-beta amplification in regime shift).
- **Hit rate**: 50-55% (lower del momentum 55-60% — più volatile).
- **Avg holding**: 4-6 settimane.

**Decision rule**:
- Se Sharpe < 0.8 net costs → l'edge è già dentro ETF rotation + momentum
  separati, SFM non aggiunge valore → kill switch.
- Se Sharpe 0.8-1.0 → marginal edge, mantieni come overlay opzionale ma
  non bucket separato (rimuovi cap dedicato).
- Se Sharpe > 1.0 con DSR significativo → confermato edge, mantieni bucket
  con invarianti correnti.

---

## 11. Bibliografia

- **Moskowitz, T. J., & Grinblatt, M. (1999)**. *Do industries explain
  momentum?* The Journal of Finance, 54(4), 1249-1290.
- **Asness, C. S., Porter, R. B., & Stevens, R. L. (2000)**. *Predicting stock
  returns using industry-relative firm characteristics.* AQR Working Paper.
- **Jegadeesh, N., & Titman, S. (1993)**. *Returns to buying winners and
  selling losers: Implications for stock market efficiency.* JF, 48(1), 65-91.
- **Bailey, D. H., & López de Prado, M. (2014)**. *The deflated Sharpe ratio:
  correcting for selection bias.* JPM, 40(5), 94-107.
- **Antonacci, G. (2014)**. *Dual Momentum Investing.* McGraw-Hill. (Per la
  sezione defensive switch in STRONG_BEAR — non SFM ma referenza ETF Rotation
  C.7.)

---

## Cross-reference

- [`MOMENTUM_STRATEGY.md`](MOMENTUM_STRATEGY.md) — momentum standalone
  (sub-score base riusati da SFM)
- [`ETF_ROTATION_STRATEGY.md`](ETF_ROTATION_STRATEGY.md) — engine top-down
  che alimenta SFM
- [`CONTRARIAN_STRATEGY.md`](CONTRARIAN_STRATEGY.md) — strategia parallela,
  diversa filosofia (mean reversion vs momentum amplificato)
- [`RISK_FRAMEWORK.md`](RISK_FRAMEWORK.md) — sizing v2 (Kelly + Vol + Corr),
  applicabile a SFM con cap aggiuntivi
- [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) — Fase B.1 cross-sectional rank,
  pattern overlay analogo a SFM peer-RS
