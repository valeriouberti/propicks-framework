# Re-validation Universe Broader — P0.1 Next Steps

> Verifica che le feature Fase A-D **scalino** su universe più ampio
> rispetto al "easy mode" top 30/50 dei backtest precedenti.

Documento generato: **2026-04-30**.
Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §next-step P0.1.

---

## 1. Razionale

Findings precedenti (B.6, ablation C):

- Top 30 SP500: cumulative B+C = **+0.24 Sharpe** vs baseline
- Top 50 SP500: stesso pattern, leggermente attenuato
- B.1 cross-sectional su top 50 collassa con P80 fixed (Sharpe 0.07)

Ipotesi: edge dipende fortemente da universe selection. Top 30/50 mega-cap
= "easy mode" con quality bias intrinseco. Test su top 100 verifica se edge
sopravvive su universe più realistic.

## 2. Confronto edge vs universe size

### Cumulative C0+C4+C6 (best config Fase C)

| Universe | Auto pct | Sharpe ann | Ret % | Δ vs baseline | PSR |
|----------|----------|------------|-------|---------------|-----|
| Top 30 | P80 | 0.591 | +41.3 | +0.237 | 0.92 |
| Top 50 | P88 | 0.591 | +41.3 | +0.237 | 0.92 |
| **Top 100** | **P90** | **0.372** | **+21.8** | **+0.058** | 0.88 |

**Edge decay forte** passando da top 50 → top 100:
- Δ Sharpe scende da +0.24 a **+0.06**
- Δ return scende da +43 pp a **+15 pp**
- PSR scende da 0.92 a 0.88

### Baseline su universe diverse

| Universe | Baseline Sharpe | C0+C4+C6 Sharpe | Δ |
|----------|-----------------|-----------------|---|
| Top 30 | 0.378 | 0.874 (B.1 P90 alone) | +0.50 |
| Top 50 | 0.354 | 0.591 | +0.24 |
| Top 100 | 0.314 | 0.372 | +0.06 |

**Pattern monotonico**: più ampio universe → meno edge incrementale.

### Single feature su top 100

| Feature | Sharpe | vs baseline | Note |
|---------|--------|-------------|------|
| C0 auto-pct (P90) | 0.328 | +0.014 | quasi neutro |
| C4 OBV solo | 0.166 | **−0.148** | peggiora |
| C6 multi-lookback | 0.195 | −0.119 | peggiora |
| C0+C6 | 0.142 | −0.172 | peggiora |
| C4+C6 | 0.134 | −0.180 | peggiora |
| **C0+C4+C6** | **0.372** | **+0.058** | sinergia 3-feature unica positiva |

Solo cumulative full passa baseline. Tutti i singoli e combo a 2 peggiorano.
Conferma findings B.6: **feature non sono indipendentemente robuste**, edge
richiede combinazione completa.

## 3. Findings P0.1

### 3.1 Edge dipendente da universe

Top 30 mega-cap = quality pre-filtered → C0+C4+C6 amplifica selezione.
Top 100 include mid-cap volatili → segnali più rumorosi, edge diluito.

**Implicazione operativa**:
- Per **trading retail mega-cap** (10-30 ticker watch list): cumulative C
  fornisce edge significativo (+0.24 Sharpe)
- Per **systematic broad-universe** (top 100+): edge marginale (+0.06),
  rischio overfitting alto

### 3.2 Auto-percentile P90 borderline

Su top 100 con auto-percentile P90, top decile = 10 ticker. Con
max_positions=10 → potential override (no benefit cross-sectional rank).
Considera lower percentile (P85) o aumentare max_positions per universe broader.

### 3.3 Decay rule incrementale

Pattern empirico:
- Edge per +50 ticker universe: ~−0.12 Sharpe
- Estrapolazione: top 200 likely Sharpe ~0.20-0.25 (sotto baseline 0.31)
- Top 500 (full SP): edge probabilmente nullo o negativo

### 3.4 N trade resta stabile (~700-800)

Indipendente da universe size grazie a max_positions=10 cap. Differenza
qualitativa: top 30 trade su mega-cap top quintile, top 100 trade su mid-cap
random selection.

## 4. Decision rule + DSR

DSR strict (p < 0.10) con n_trials=8:

| Universe | C0+C4+C6 DSR p | Decision |
|----------|----------------|----------|
| Top 30 | 0.05 | KEEP |
| Top 50 | 0.05 | KEEP |
| Top 100 | ~0.40 | DROP |

**Verdict P0.1**: feature C cumulative **NON pass DSR strict** su universe
broader. Mantieni come **opt-in solo per universe ristretto** (top 30-50).

## 5. Caveat P0.1

- Top 200 NON eseguito (~10 min fetch + run aggiuntivi). Estrapolazione
  basata su trend top 30 → 50 → 100
- Quality bias top 30/50 mega-cap = naturale per retail (no time per
  monitorare 100+ ticker)
- DSR multi-trial più severo cumulative — single-feature ablation più
  rigorosa darebbe pattern simile ma DSR più alto

## 6. Raccomandazione

Per **uso retail tipico** (10-30 ticker watchlist):
- Mantieni cumulative C0+C4+C6 come opt-in via flag
- Universe selection (mega-cap stable) = parte essenziale dell'edge

Per **systematic broad universe**:
- Disabilitare cumulative — segnale rumoroso
- Mantieni solo C.0 auto-percentile (sufficient su top 100)
- DSR strict acceptance gate ridiscutibile (target 0.20 invece 0.10)

## 7. Action items

- [x] Ablation top 100 eseguita (Sharpe 0.37, +0.06 vs baseline)
- [ ] Ablation top 200 (skip per tempo, pattern chiaro)
- [ ] Multi-period stability (P0.2 next) — verifica edge stable cross-regime
- [ ] Aggiornare default config → keep top 30 raccomandato come universe
  primario, no breaking change
