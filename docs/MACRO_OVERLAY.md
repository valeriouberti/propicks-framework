# Cross-Asset Macro Overlay — Fase B.5 SIGNAL_ROADMAP

> Sector rotation overlay basato su 5 macro features cross-asset (yield curve,
> USD, HY OAS, copper/gold, oil/gold) → matrix di sensitività per 11 sector
> ETF US.

Documento generato: **2026-04-29**.
Reference roadmap: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §5 Fase B.5.

---

## 1. Razionale

ETF rotation strategia attuale (`domain/etf_scoring.py`) usa principalmente
price momentum interno + regime fit. **Edge macro mancante**: yield curve,
credit spread, USD, commodity ratios sono leading indicators di sector
performance documentati 50y+.

Esempi storici:

- **Steepening yield curve**: XLF + cyclicals favoriti (NIM expansion)
- **USD weakening**: XLB + XLE favoriti (commodity prices)
- **HY OAS spike**: defensive (XLU, XLP) outperformance
- **Copper/gold rising**: industrials/materials confirm global growth

## 2. Architettura

```
┌─────────────────────────┐
│ FRED CSV endpoint       │
│ T10Y2Y, DTWEXBGS,       │
│ BAMLH0A0HYM2            │
└────────────┬────────────┘
             │
┌────────────▼────────────┐  ┌─────────────────────────┐
│ market/fred_client.py   │  │ yfinance commodity      │
│ (cached 24h)            │  │ futures HG=F GC=F CL=F  │
└────────────┬────────────┘  └────────────┬────────────┘
             │                            │
             └─────────────┬──────────────┘
                           │
              ┌────────────▼──────────────┐
              │ domain/macro_overlay.py   │
              │ - compute_macro_zscores   │
              │ - macro_fit_score         │
              │ - SECTOR_SENSITIVITY_MAT  │
              └───────────────────────────┘
```

## 3. Feature

| Feature | Source | Sign convention (z positive) |
|---------|--------|-------------------------------|
| `yield_slope` | FRED `T10Y2Y` | curve steepening |
| `usd_inv` | FRED `DTWEXBGS` (inverted) | dollar weakening |
| `hy_oas_inv` | FRED `BAMLH0A0HYM2` (inverted) | credit calm |
| `copper_gold` | yfinance HG=F / GC=F | global growth |
| `oil_gold` | yfinance CL=F / GC=F | inflation / energy |

Z-score rolling 252d (1y) per ogni feature.

## 4. Sector sensitivity matrix

11 sector ETF × 5 features. Sensitivity ∈ [-1, +1]. Positive = favor.

```
              yield_slope  usd_inv  hy_oas_inv  copper_gold  oil_gold
XLF (Banks)       +1.0      +0.2      +0.5        +0.3        0.0
XLE (Energy)       0.0      +0.5      +0.3        +0.5       +1.0
XLK (Tech)        -0.3      -0.2      +0.5         0.0        0.0
XLU (Utilities)   -0.5       0.0      +0.3         0.0       -0.3
XLI (Industrial)  +0.3      -0.2      +0.4        +0.7       +0.3
XLY (ConsDisc)    +0.3      -0.3      +0.4        +0.3       -0.3
XLP (Staples)     -0.2       0.0      +0.2         0.0       -0.2
XLV (Healthcare)   0.0      -0.2      +0.2         0.0        0.0
XLB (Materials)   +0.2      -0.5      +0.3        +1.0       +0.3
XLRE (REITs)      -0.5       0.0      +0.3         0.0       -0.3
XLC (Comm)         0.0      -0.2      +0.4         0.0        0.0
```

**Macro fit score**:

```
weighted_z = Σ z[f] × sens[etf][f]
norm_factor = Σ |sens[etf][f]|
score = clip(50 + 25 × (weighted_z / norm_factor), 0, 100)
```

Output [0, 100]. 50 = neutral.

## 5. Smoke run results (2026-04-28)

### Latest z-scores

| Feature | z-score | Interpretation |
|---------|---------|---------------|
| yield_slope | −0.62 | curve flattening vs 252d (banks headwind) |
| usd_inv | NaN | USD data delayed 1d |
| hy_oas_inv | +0.65 | credit calm (bull) |
| copper_gold | −0.09 | slight global growth weakness |
| oil_gold | **+1.57** | strong oil regime |

### Sector ranking macro_fit (latest)

| Rank | ETF | Score | Top drivers |
|------|-----|-------|-------------|
| 1 | **XLE** | **73.9** | oil_gold(+1.0), usd_inv(+0.5) |
| 2 | XLV | 66.2 | hy_oas_inv(+0.2) |
| 3 | XLC | 66.2 | hy_oas_inv(+0.4) |
| 4 | XLK | 65.9 | hy_oas_inv(+0.5), yield_slope(-0.3) |
| 5 | XLI | 57.1 | copper_gold(+0.7), hy_oas_inv(+0.4) |
| 6 | XLB | 56.3 | copper_gold(+1.0), usd_inv(-0.5) |
| 7 | XLU | 50.7 | yield_slope(-0.5), hy_oas_inv(+0.3) |
| 8 | XLRE | 50.7 | yield_slope(-0.5), hy_oas_inv(+0.3) |
| 9 | XLP | 47.5 | yield_slope(-0.2) |
| 10 | XLF | 45.6 | yield_slope(+1.0), hy_oas_inv(+0.5) |
| 11 | **XLY** | **41.9** | yield_slope(+0.3), oil_gold(-0.3) |

### Reading

- **XLE top**: oil/gold z = +1.57 (extremely high oil regime) drives energy.
  Coherent: oil $105 vs gold $4557 ratio elevated
- **XLF penalty**: yield slope flattening (-0.62) hurts banks NIM. Sensible
  con curve quasi piatta T10Y2Y = 0.52 (normal but flat vs historical norm)
- **XLK/XLC favored**: HY OAS basso = credit calm helps refinancing of
  long duration tech
- **XLU/XLRE neutral**: rate-sensitive defensive, mixed signals
- **XLY bottom**: oil cara = consumer hurt, yield slope ambiguous

## 6. Caveats

### Critici

1. **USD data delay**: DTWEXBGS pubblicato T+1, latest può essere NaN.
   Workaround: usa `ffill()` o `DXY` da yfinance come fallback.

2. **Sensitivity matrix arbitraria**: pesi default basati su rationale
   qualitativo (Faber-style). Tuning rigoroso con regression / DSR
   pendente (Fase B.6 ablation).

3. **Single-period z-score window 252d**: non adattivo a regime change. In
   periodi di vol estrema (2020-03, 2022) z-score più volatili. Considerare
   regime-conditional z (es. standardize per current vol bucket).

4. **Commodity futures vs spot**: HG=F / GC=F / CL=F sono front-month
   futures. Roll yield introduce noise. Per spot pure: ETF (CPER, GLD, USO)
   ma più costoso in TER/tracking error.

### Minori

5. **Solo 5 macro features**: NAAIM exposure, AAII bull-bear, put/call
   ratio rinviati (B.3.5 estensione). Aggiungerebbero leading sentiment.
6. **Sector matrix US-only**: STOXX Europe sector ETF (SXLE.MI, etc) avrebbe
   sensitivity matrix diversa (ECB rate vs Fed, EUR/USD, Brent vs WTI).
7. **No interaction terms**: matrix è purely additive. In realtà yield
   slope × HY OAS può avere effect non-lineare (recession signal).

## 7. Integrazione futura

### B.5 estensione (next iteration)

- B.5.5: Integrazione in `domain/etf_scoring.py` come 5° sub-score
  `macro_fit` (peso 20-25%)
- B.5.6: Ablation on rotation backtest 2010-2024 con/senza macro overlay,
  tuning weights via DSR
- B.5.7: Add NAAIM, AAII (free scraping fragile), put/call ratio CBOE

### B.6 ablation framework

- Tuning pesi sensitivity matrix via regression cross-sectional sector
  returns ~ macro features (Fama-MacBeth style)
- Confronto Sharpe rotation strategy con/senza overlay in 4 regimes
  (bull, bear, low-vol, high-vol)

## 8. API summary

```python
from propicks.domain.macro_overlay import (
    SECTOR_SENSITIVITY_MATRIX,
    compute_copper_gold_ratio,
    compute_oil_gold_ratio,
    compute_macro_zscores,         # dict[str, Series] → DataFrame
    macro_fit_score,                # (etf, z_dict) → [0, 100]
    macro_fit_series,               # (etf, z_df) → Series
)
```

Pure functions. Input data fornito dal caller (FRED + yfinance fetch
separati).

## 9. Conclusione

✓ Cross-asset overlay implementato + sensitivity matrix per 11 sector ETF

✓ Smoke test produce ranking sector coerente con macro regime corrente
(oil regime → XLE top; flat curve → XLF bottom; credit calm → tech favored)

⚠ Sensitivity matrix arbitraria (rationale qualitativo) — tuning rigoroso
pendente Fase B.6

⚠ USD data delay edge case (workaround necessario per production)

⚠ Integrazione in `etf_scoring.py` non ancora fatta — solo standalone API

**Acceptance gate B.5**: **pass operativo** — implementation funziona,
ranking sensible. Edge OOS misurabile solo dopo integration in rotation
strategy + backtest historic con DSR (B.6 ablation).

**Next**: B.6 — Ablation framework cumulativo. Re-backtest each B.1-B.5
feature isolato + cumulative. Decision rule: mantenere solo +0.10 Sharpe
ANI DSR p < 0.10.
