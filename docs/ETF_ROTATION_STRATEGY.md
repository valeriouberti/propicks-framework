# Strategia ETF Rotation — Parallelo agli Stock

> Motore di rotazione settoriale **parallelo** alla strategia stock momentum.
> Condivide `regime.py` ma diverge su scoring (RS vs benchmark + regime fit) e
> validazione (Claude macro strategist, non equity analyst).

Il branch è determinato dal ticker via `domain.etf_universe.get_asset_type`:
- **STOCK** → flow esistente (`analyze_ticker` → tesi aziendale → Claude)
- **SECTOR_ETF** → flow rotazione (RS + regime fit → Claude macro)

---

## 1. Universo ETF

Tre universi paralleli in `config.py`, selezionabili via `--region`:

### 1.1 US — `SECTOR_ETFS_US`
Select Sector SPDR (11 settori GICS, tickers `XL*`).

### 1.2 EU — `SECTOR_ETFS_EU`
SPDR S&P U.S. Select Sector UCITS (`ZPD*.DE` su Xetra). **Wrapper UCITS degli
stessi Select Sector Index US** — esposizione identica, solo wrapper irlandese
accumulating. Tesi di rotazione unica con US; il trader sceglie il listing in
base a fiscalità e liquidità.

### 1.3 WORLD — `SECTOR_ETFS_WORLD`
Xtrackers MSCI World sector UCITS — serie `XDW*.DE` per i nove settori GICS
"core" più `XWTS.DE` per communications. Perimetro MSCI World (developed
markets, ~65-70% US + ~15% Europa + ~6% Giappone), **non è un mirror dei SPDR**
— settori world includono nomi europei/giapponesi con dinamica diversa
(es. energy con Shell/TotalEnergies vs Chevron/Exxon puri US).

**Real Estate WORLD — eccezione di perimetro**: NON esiste un Xtrackers MSCI
World Real Estate UCITS quotato (la serie XDW*/XWTS copre 10 settori GICS su
11). Il proxy più simile, liquido su Xetra/yfinance, è **`IQQ6.DE`** (iShares
Developed Markets Property Yield UCITS, ISIN IE00B1FZS350). Questo NON è un
fund Xtrackers: issuer iShares (BlackRock) e perimetro REIT developed
**filtrati per dividend yield ≥ 2%**. Esclude REIT senza yield significativo
(growth/non-yield REIT) → composizione income-tilted vs full GICS Real Estate
world. Asimmetria nota e accettata per chiudere il bucket; il sub-score
`regime_fit` per `sector_key="real_estate"` si applica con questo trade-off.

### 1.4 Eccezioni e gotchas

- `XLRE` non ha SPDR US Real Estate Select Sector UCITS equivalente
  (`eu_equivalent=None`). Per la WORLD, vedi nota su `IQQ6.DE` qui sopra:
  proxy iShares Property Yield, non un Xtrackers GICS Real Estate.
- `XWTS.DE` è l'outlier naming della serie WORLD (communications); riflette il
  GICS 2018 reshuffle, include Meta/Alphabet/Netflix come XLC US.
- Listing Xetra `ZPD*`, `XDW*` e `IQQ6` sono accumulating (IE-domiciled).
  Alcuni broker retail EU non quotano `XWTS` o `IQQ6` su Xetra — fallback su
  listing Milano (`.MI`) se disponibile. Varianti distributing su LSE hanno
  ticker diversi e non sono registrate qui.

### 1.5 Benchmark RS per region

`config.get_etf_benchmark`:
- US/EU → `^GSPC` (coerente con Select Sector Index)
- WORLD → `URTH` (iShares MSCI World ETF, stesso perimetro dei XDW*)

Mischiare benchmark e universo confonde outperformance settoriale con
differenze di perimetro geografico. `rank_universe` sceglie automaticamente il
benchmark giusto. **Il regime classifier resta sempre su `^GSPC`** anche per
WORLD (correlazione S&P/MSCI World ≈ 0.95 weekly giustifica l'approssimazione e
`REGIME_FAVORED_SECTORS` è US-calibrata).

---

## 2. Regime → Settori favoriti

Tabella **opinabile** in `config.py::REGIME_FAVORED_SECTORS` — view ciclica
classica (early→late cycle → defensives → capital preservation). Va rivista a
ogni regime change verificando che i leader reali confermino la tabella.

| Regime | Settori favoriti |
|--------|------------------|
| 5 STRONG_BULL | tech, consumer disc., comms, financials, industrials |
| 4 BULL        | tech, consumer disc., industrials, materials, financials |
| 3 NEUTRAL     | healthcare, industrials, financials, tech |
| 2 BEAR        | consumer staples, utilities, healthcare |
| 1 STRONG_BEAR | consumer staples, utilities |

---

## 3. Scoring engine (composite 0-100)

`domain/etf_scoring.py` è il parallelo di `domain/scoring.py` ma con formula
diversa — il problema è diverso. Sugli ETF settoriali non ha senso cercare
pullback vicino all'ATH di un single-name: si cerca **leadership relativa** e
**fit col regime macro**.

```
composite_etf = RS * 0.40 + regime_fit * 0.30 + abs_momentum * 0.20 + trend * 0.10
```

### 3.1 RS vs benchmark (40%)
`close(ETF)/close(^GSPC)` normalizzato su 26 weeks, poi slope sulla EMA(10
weeks) della RS line.
- Leader in accelerazione = 100
- Ex-leader in distribuzione (RS alto ma slope negativo) = 55
- Lagger in distribuzione = 10

### 3.2 Regime fit (30%)
Lookup su `REGIME_FAVORED_SECTORS`:
- Favorito nel regime corrente = 100
- Favorito nel regime adiacente (transizione) = 60
- Non favorito = 20
- Regime ignoto = 50

### 3.3 Absolute momentum (20%)
Perf 3M del settore (non RS, assoluto). +15%+ = 100, scala a step fino a -5%+ = 10.

### 3.4 Trend (10%)
Price vs EMA30 weekly (stesso livello del regime classifier) + slope EMA a 4
settimane. Price sopra EMA in salita = 100.

### 3.5 Regime hard-gate

Oltre al peso 30% nella formula, il regime applica un cap superiore allo score
dei settori non favoriti — `domain.etf_scoring.apply_regime_cap`:

- **STRONG_BEAR** + non-favored → score forzato a **0** (no long ciclicali in crisi)
- **BEAR** + non-favored → score capped a **50** (no overweight cicliche)
- **NEUTRAL+** → nessun cap, ranking libero

Questo evita che un XLK con momentum forte esca top-ranked in un regime di
drawdown — coerente col gate regime già usato in `validate_thesis`.

---

## 4. CLI `propicks-rotate`

Entry point dedicato (non un branch di `propicks-scan`): la rotazione è un
workflow diverso dal setup single-stock e merita un comando suo.

```bash
propicks-rotate                        # US (SPDR Select Sector), top 3
propicks-rotate --top 5                # US, top 5
propicks-rotate --region EU            # SPDR UCITS (ZPD*.DE)
propicks-rotate --region WORLD         # Xtrackers MSCI World 10 settori (XDW*/XWTS) + IQQ6.DE proxy RE
propicks-rotate --allocate             # include proposta allocazione
propicks-rotate --validate             # validazione macro via Claude
propicks-rotate --json                 # output JSON
```

**Output**: tabella ranking 11 settori con score + sub-score + RS ratio + perf
3M + classification (A OVERWEIGHT, B HOLD, C NEUTRAL, D AVOID) + dettaglio del
top-pick. Con `--allocate`: proposta equal-weight 20% per ETF sui top-N, capped
al 60% aggregato.

---

## 5. Portfolio construction ETF

`suggest_allocation` codifica le regole di costruzione:

- **NEUTRAL+**: top-N (default 3) equal-weight 20% ciascuno, cap aggregato 60%
- **BEAR**: top-1 difensivo, 15% max (N ridotto automaticamente)
- **STRONG_BEAR**: allocazione vuota (flat, cash)
- Esclusi classi C (NEUTRAL) e D (AVOID) dalla selezione

La rotazione a tranche su regime change (2-3 tranche su 5 sessioni) è una
regola operativa manuale — non ancora codificata nello store.

---

## 6. AI validation (on-demand, non default)

Parallelo a `ai/thesis_validator.py` ma con assunzioni diverse:

- **`ai/etf_prompts.py`** — `ETF_SYSTEM_PROMPT` da macro strategist, non da
  equity analyst. Zero focus su earnings / moat / unit economics. Focus su
  macro drivers (yields, DXY, commodities), breadth, positioning, rotation
  stage, flows. Web search mirata: spot macro, ETF flows, sector breadth — NON
  earnings date.
- **`ai/etf_validator.py::validate_rotation`** — cache **48h** (vs 24h stock:
  la view macro si muove più lenta), chiave `(region, regime_code, YYYY-MM-DD)`.
  Skip automatico in STRONG_BEAR (la risposta è ovvia: flat), override con
  `--force-validate`.
- **Schema verdict** — `ETFRotationVerdict` in `ai/claude_client.py` con campi
  diversi: `top_sector_verdict`, `alternative_sector`, `stage`
  (EARLY/MID/LATE), `rebalance_horizon_weeks`, `entry_tactic` (ALLOCATE_NOW /
  STAGGER_3_TRANCHES / WAIT_PULLBACK / ...). Niente `reward_risk_ratio`: su
  rotation settoriale non ha senso.
- **Non default**: `--validate` su `propicks-rotate` è opt-in. La rotazione
  weekly è meno sensibile al noise qualitativo — spendere token ogni weekly
  rebalance è eccessivo. Usare quando c'è un regime change o una decisione di
  entry con size rilevante.

---

## 7. Invarianti ETF

- ETF settoriali: max **20%** del capitale (vs 15% dei single-stock)
- Rotazione graduale: cambio regime BULL→BEAR = uscita in 2-3 tranche su 5
  sessioni per evitare whipsaw (regola operativa, non ancora codificata)
- **Nessun ETF futures-based** (USO, UNG, DBC): contango decay incompatibile
  con holding > 2 settimane — non verranno mai aggiunti all'universo

---

## 8. Thematic ETF — fuori scope di `propicks-rotate`

I tematici (semis SMH/SOXX, biotech XBI/IBB, cybersecurity CIBR/BUG, AI &
robotica ROBO/BOTZ, clean energy ICLN/TAN, KWEB, XAR/ITA) **non sono parte
dell'universo ETF rotation** e non vanno aggiunti a `SECTOR_ETFS_*`. Tre
ragioni architetturali:

1. **Violano l'invariante GICS-mutuamente-esclusivi** della rotation: SMH ≈
   70% top-10 di XLK, XBI ≈ 60% biotech-pesante di XLV. Inserirli nello stesso
   universo significa avere doppio bet camuffato da diversificazione, e
   l'allocator equal-weight non sa che due posizioni diverse hanno la stessa
   scommessa sottostante.
2. **Non mappano su `REGIME_FAVORED_SECTORS`**: semis è sub-industry, non GICS
   sector. Estendere la tabella regime a temi opinabili (semis è early-cycle?
   secular AI play? cyclical?) introduce rumore.
3. **L'asse RS giusto è vs parent sector, non vs `^GSPC`**: SMH che batte SPX è
   quasi tautologico in un mercato risk-on; SMH che batte XLK discrimina
   davvero.

### 8.1 Approccio attuale (MVP)

I tematici di interesse passano da `propicks-scan` come single-stock e
finiscono nel bucket satellite (max 15%/posizione). Quattro regole auto-imposte
manuali (non codate):
- Max 2 tematici aperti
- Campo `catalyst` con parent sector + peso
- Stop hard 10% (non 8%, ATR% più alto)
- Hard rule `weight(theme) + weight(parent_sector) ≤ 25%`

### 8.2 Promozione a subpackage dedicato

`propicks/thematic/` con scoring RS-vs-parent + CLI `propicks-themes`, **gated
da journal evidence**: dopo 15 trade tematici chiusi, promuovi solo se win rate
≥ baseline single-stock, avg P&L > baseline + 0.5%, **e** correlation con
parent sector < 0.85. Se la corr è ≥ 0.85, sono solo leveraged sector bet
senza alfa proprio → killa l'esperimento.

Documentazione operativa completa in `Trading_System_Playbook.md` §5B.
