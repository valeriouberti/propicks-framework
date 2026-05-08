# Strategia Thematic ETF — Sub-Industry / Cross-Sector Tilts

> Bucket satellite parallelo a momentum / contrarian / rotation.
> Discrimina alfa tematico genuino da leveraged sector bet camuffato.

I tematici (semis SMH, biotech XBI, cybersec LOCK.MI/CIBR, robotics ROBO,
clean energy ICLN, defense XAR/ITA, china internet KWEB) **non sono parte
dell'universo `propicks-rotate`** (vedi
[`ETF_ROTATION_STRATEGY.md`](ETF_ROTATION_STRATEGY.md) §8) perché violano
l'invariante GICS-mutually-exclusive della rotation.

Questo subpackage li tratta esplicitamente con scoring **RS-vs-parent**
(non vs broad benchmark) e kill-switch correlation per filtrare leveraged
sector bet senza alfa proprio.

---

## 1. Tesi

Un ETF tematico ha valore solo se porta alfa **distinto** dal parent sector:

- **SMH** ha senso vs **XLK** (≈70% top-10 di XLK è semis): se SMH non batte
  XLK, è solo leverage di XLK senza alfa.
- **XBI** ha senso vs **XLV**: biotech è ≈60% di XLV, stesso ragionamento.
- **LOCK.MI** ha senso vs **XDWT.MI** (parent MSCI World tech): se LOCK
  non batte XDWT, l'edge cybersec è inesistente.

L'asse RS giusto è theme/parent, **NON** theme/^GSPC. SMH che batte SPY è
quasi tautologico in risk-on; SMH che batte XLK discrimina davvero.

---

## 2. Universo

`config.THEMATIC_ETFS` — 31 tematici totali (13 US listing + 18 Borsa
Italiana .MI). Lista USA mantenuta per backtest history più lunga; lista
.MI è il set operativo per il broker retail italiano.

### 2.1 US listings (parent SPDR Select Sector)

| Theme | Tematici | Parent |
|---|---|---|
| Semiconductors | SMH, SOXX | XLK |
| Cybersecurity | CIBR, BUG | XLK |
| Robotics & AI | ROBO, BOTZ | XLI / XLK |
| Biotech | XBI, IBB | XLV |
| Clean energy / solar | ICLN, TAN | XLE |
| Aerospace & defense | XAR, ITA | XLI |
| China internet | KWEB | XLC |

### 2.2 Borsa Italiana (.MI) — universe operativo

Parent: Xtrackers MSCI World sector listati su BIt
(`SECTOR_ETFS_WORLD` blocco .MI). Naming ticker uniforme `XDW*` su tutti
i settori (mirror del listing Xetra `.DE`).

| Sector parent | Ticker parent | Tematici .MI |
|---|---|---|
| Tech | `XDWT.MI` | XAIX, SMH.MI, LOCK.MI, WCLD.MI, DGTL.MI |
| Industrials | `XDWI.MI` | DFND.MI, RBOT.MI, BATT.MI |
| Utilities | `XDWU.MI` | IH2O.MI, NUCL.MI, INRG.MI |
| Healthcare | `XDWH.MI` | GNOM.MI, HEAL.MI, SBIO.MI |
| Energy | `XDW0.MI` | IS0D.MI |
| Financials | `XDWF.MI` | BNKE.MI, DPAY.MI |
| Materials | `XDWM.MI` | REMX.MI |

**INRG.MI nota perimetro**: il fondo è "Global Clean Energy Transition"
— sub-industry utilities (renewables/grid) più che energy fossil.
Mappato a parent `XDWU.MI` per coerenza tematica. Per RS vs energy
fossil (`XDW0.MI`) andrebbe riassegnato — qui vince il GICS sector_key.

Aggiungere un tematico richiede:
1. Parent registrato in `SECTOR_ETFS_{US,EU,WORLD}` (validato da
   `tests/unit/test_thematic_universe.py::test_every_thematic_has_valid_parent`)
2. `parent_sector_key` GICS-valid per il regime_fit lookup
3. `region` coerente col listing (US listing → parent SPDR US, .DE/.MI →
   parent Xtrackers WORLD)

---

## 3. Scoring engine (composite 0-100)

`domain/thematic_scoring.py`:

```
composite = rs_vs_parent * 50% + abs_momentum * 25% + trend * 15% + parent_regime_fit * 10%
```

### 3.1 RS-vs-parent (50%)
`close(theme)/close(parent)` normalizzato su 26 weeks, slope a 10 weeks.
Stessa scala di `etf_scoring.score_rs` ma reference è il parent sector ETF,
non il broad benchmark. Scoring level × slope:
- level ≥ 1.05 + slope > 0 → 100 (alfa accelerando)
- level ≥ 1.0 + slope ≤ 0 → 55 (alfa stanco)
- level < 0.95 + slope ≤ 0 → 10 (lagger, solo leverage parent)

### 3.2 Abs momentum 3M (25%)
Perf 63 days assoluta. Stessa scala dell'ETF rotation.

### 3.3 Trend (15%)
Price vs EMA30 weekly (coerente con regime classifier) + slope a 4 weeks.

### 3.4 Parent regime fit (10%)
Eredita dal parent: se XLK è favored in regime corrente, SMH ha edge
regime. Sub-industry non mappa GICS direttamente, quindi peso basso.
Stessa scala di `etf_scoring.score_regime_fit`.

---

## 4. Gate hard

### 4.1 Correlation kill-switch
Se `corr_60d(theme, parent) ≥ 0.85` (`THEMATIC_CORR_KILL_THRESHOLD`):
**composite forzato a 0**.

Razionale (Antonacci dual-momentum framework + AFP 2013): a quella
correlazione il tematico non porta alfa — è solo concentration più alta
del parent. Comprare SMH a corr 0.92 con XLK è leverage 1.3× XLK senza
edge proprio.

### 4.2 Regime gate
- **STRONG_BEAR (1)** → composite forzato a 0 (no tematici growth in crash)
- **BEAR (2)** → composite cap a 40 (max class C NEUTRAL — no overweight tematico)
- **NEUTRAL+ (3-5)** → pass-through

Più stringente del momentum stock cap (BEAR cap solo penalty score) perché
i tematici sono growth/cyclical-tilt — non hanno mai senso in capital
preservation regime.

---

## 5. CLI `propicks-themes`

```bash
# Singolo
propicks-themes LOCK.MI
propicks-themes LOCK.MI --validate

# Batch + ranking
propicks-themes SMH XBI CIBR
propicks-themes --rank
propicks-themes --rank --region WORLD
propicks-themes --rank --theme cybersecurity
propicks-themes --json
```

Output: ranking con score + sub-score + corr 60d con parent + classification
(A OVERWEIGHT, B HOLD, C NEUTRAL, D AVOID) + flag ⚠C (corr-kill) ⚠R (regime gate).

---

## 6. AI validation (Claude)

`ai/thematic_validator.py` con system prompt **thematic specialist**
(non macro strategist, non equity analyst). Focus su:

- Discriminare alfa tematico genuino da beta-leveraged trade
- Theme stage (EARLY 3-6M / MID 6-18M / LATE 18M+)
- Crowding / flows / AUM trend
- Concentration risk (top 3-5 holdings %)
- Theme-specific catalysts (FDA pipeline, defense procurement, NIS2,
  semis capex, china stimulus)
- Wrapper alternatives (SMH vs SOXX, XBI vs IBB)

Schema verdict (`ThematicVerdict` in `claude_client.py`):
- `verdict`: CONFIRM / CAUTION / REJECT
- `theme_stage`: EARLY / MID / LATE / UNKNOWN
- `alternative_ticker`: nullable, validato vs `THEMATIC_ETFS` (sanity)
- `crowding_read`, `concentration_read`, `catalysts`
- `entry_tactic`: ALLOCATE_NOW / STAGGER_3_TRANCHES / WAIT_PULLBACK / WAIT_CONFIRMATION / HOLD_CASH
- `time_horizon_weeks`: 4-26
- `confidence_by_dimension`: thematic_alpha, crowding_flows, concentration_risk,
  catalyst_strength, parent_alignment, regime_consistency

Sanity layer (`_enforce_thematic_sanity`):
1. `alternative_ticker` non in `THEMATIC_ETFS` → set null + flag override
2. `theme_stage=LATE` + `verdict=CONFIRM` → downgrade CAUTION (late stage
   richiede staggered entry, non allocate now)

Cache TTL **24h** (parallelo momentum stock — narrative tematica si muove
veloce su catalyst sub-industry, non come macro-rotation).

Gate validation:
- `score_composite ≥ 60` (`THEMATIC_AI_MIN_SCORE_FOR_VALIDATION`)
- skip BEAR / STRONG_BEAR (regime gate already forced score basso)
- skip se `corr_kill_applied=True` (composite=0, niente da validare)

---

## 7. Invarianti

Hardcoded in `config.py`:

- **Max posizioni tematiche aperte**: 2 (`THEMATIC_MAX_POSITIONS`)
- **Max size singola**: 15% capitale (`THEMATIC_MAX_POSITION_SIZE_PCT`)
- **Stop hard**: 10% (`THEMATIC_STOP_LOSS_PCT`) — più largo del momentum
  (8%) e dell'ETF rotation (5%) perché ATR% tematici tipicamente più alto
- **Aggregate cap**: `weight(theme) + weight(parent_ETF) ≤ 25%`
  (`THEMATIC_PARENT_AGGREGATE_CAP_PCT`) — evita doppio bet camuffato
- **Corr kill**: 0.85 (`THEMATIC_CORR_KILL_THRESHOLD`) — sotto, alfa
  considerato genuino; sopra, score=0

Constraint **non codato** (manuale, da rispettare in `propicks-portfolio add`):
- Non aggiungere LOCK.MI se XDWT.MI è in portfolio sopra il 10% — il
  combined cap 25% si saturerebbe troppo.

---

## 8. Pine script

`tradingview/thematic_signal_engine.pine` replica visualmente il composite.

Configurazione:
1. Apri chart del tematico (es. SMH) sul timeframe **WEEKLY**
2. Set "Parent Sector ETF" (es. AMEX:XLK)
3. Set "Parent Sector Key" (technology, healthcare, ...)
4. Set "Macro Benchmark" (default SPX, per regime classifier)

Output on-chart:
- EMA30w line, stop -10% line
- Background bias verde/rosso da composite
- Panel top-right: composite + sub-score + corr 60d (⚠KILL flag se ≥ 0.85)
- Alert su cross OVERWEIGHT (≥70) / AVOID (<40) / corr-kill triggered

Default Pine matchano `config.py` byte per byte (vedi commento header).

---

## 9. Promozione & decay

Il subpackage è promosso da `propicks-momentum` bucket satellite (vedi
`ETF_ROTATION_STRATEGY.md` §8.2) ma il **gate journal-evidence resta**:
dopo 15 trade tematici chiusi, valuta se mantenere il subpackage:

- win rate ≥ baseline single-stock momentum
- avg P&L > baseline + 0.5%
- correlation media(theme, parent) < 0.85 sui trade chiusi
- decay monitor (vedi `DECAY_MONITOR.md`) non flagga rolling Sharpe degrade

Se uno qualsiasi fallisce: rollback a flow `propicks-momentum` con bucket
satellite manuale.

---

## 10. Cross-reference

- Architettura layer separation: `CLAUDE.md`
- Parent universo: `ETF_ROTATION_STRATEGY.md`
- Backtest framework (per validation pre-promotion): `BACKTEST_GUIDE.md`
- Threshold calibration con DSR: `THRESHOLD_CALIBRATION.md`
- Decay monitoring post-deployment: `DECAY_MONITOR.md`
