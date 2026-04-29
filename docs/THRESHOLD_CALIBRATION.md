# Threshold Calibration — Fase A.2 SIGNAL_ROADMAP

> Smoke calibration run del threshold momentum (gate score ≥ N) con
> Probabilistic Sharpe Ratio + Deflated Sharpe Ratio + Combinatorial Purged
> Cross-Validation.

Documento generato: **2026-04-29**.
Reference roadmap: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §4 Fase A.2.

---

## 1. Contesto

Il framework momentum usa gate hardcoded `MIN_SCORE_TECH = 60` (`config.py`).
Numero scelto a priori, mai calibrato con framework rigoroso. Fase A.2
introduce:

- **PSR** (Bailey-Lopez 2012): probabilità che il vero Sharpe > 0 dato il
  Sharpe osservato + sample size + skew + kurtosis
- **DSR** (Bailey-Lopez 2014): PSR deflated by `E[max SR | N trials]`,
  corregge per multiple testing quando si fa threshold sweep
- **CPCV** (Lopez de Prado 2018): genera `comb(N, k)` test path
  indipendenti via combinatorial folds + purging + embargo, riducendo
  path dependency dello stimatore Sharpe

Threshold calibration = trovare il valore che massimizza DSR subject a
n_trades ≥ min_trades (default 30).

---

## 2. Setup smoke run

**CLI**: `propicks-calibrate AAPL MSFT NVDA GOOGL AMZN JPM JNJ V WMT HD --period 5y`

**Universe**: 10 mega-cap S&P 500 (mix tech, finance, healthcare, consumer).

**Strategia**: momentum core (composite 6 sub-score).

**Periodo**: 5 anni di yfinance daily fetch.

**Survivorship**: `--historical-membership sp500` attivo (Fase A.1 wired).

**Multi-test correction**: `var_sr_trials` calcolata empiricamente cross-threshold;
`E[max SR | n_trials]` da formula Bailey-Lopez eq. 7.

---

## 3. Risultati

### Run A — Single shot (no CPCV), 9 threshold

```
propicks-calibrate AAPL MSFT NVDA GOOGL AMZN JPM JNJ V WMT HD \
    --period 5y --thresholds "40:80:5" --historical-membership sp500
```

| Threshold | N trades | SR/trade | SR ann | Win% | Tot ret% | Max DD% | PSR | DSR |
|-----------|----------|----------|--------|------|----------|---------|-----|-----|
| 40 | 659 | 0.127 | 0.660 | 45.7% | +44.94 | −24.77 | 1.000 | 0.087 |
| 45 | 629 | 0.107 | 0.562 | 44.0% | +35.83 | −28.73 | 0.997 | 0.035 |
| 50 | 607 | 0.089 | 0.367 | 44.0% | +19.76 | −27.94 | 0.988 | 0.013 |
| 55 | 598 | 0.105 | 0.529 | 43.6% | +31.62 | −27.60 | 0.996 | 0.034 |
| **60 (current)** | 577 | 0.124 | 0.474 | 45.4% | +25.74 | −26.43 | 0.999 | 0.092 |
| 65 | 533 | 0.120 | 0.727 | 45.8% | +41.37 | −24.06 | 0.998 | 0.085 |
| 70 | 462 | 0.160 | 1.079 | 47.2% | +64.09 | −17.00 | 1.000 | 0.351 |
| **★ 75** | 436 | 0.174 | 1.200 | 48.6% | +69.49 | −13.87 | 1.000 | 0.472 |
| 80 | 374 | 0.157 | 0.828 | 47.3% | +41.19 | −14.23 | 0.999 | 0.340 |

### Run B — CPCV (5 groups, 2 test, 5d embargo), 5 threshold

```
propicks-calibrate ... --thresholds "60,65,70,75,80" \
    --use-cpcv --cpcv-groups 5 --cpcv-test-groups 2
```

| Threshold | N trades | SR/trade | CPCV mean | CPCV std | Win% | Tot ret% | PSR | DSR |
|-----------|----------|----------|-----------|----------|------|----------|-----|-----|
| **60 (current)** | 274 | 0.168 | 0.105 | 0.090 | 47.1% | +31.44 | 0.998 | 0.622 |
| 65 | 251 | 0.166 | 0.107 | 0.092 | 48.2% | +31.54 | 0.997 | 0.604 |
| 70 | 241 | 0.194 | 0.141 | 0.087 | 49.8% | +38.29 | 0.999 | 0.763 |
| **★ 75** | 221 | 0.215 | 0.163 | 0.094 | 49.8% | +37.41 | 1.000 | 0.848 |
| 80 | 204 | 0.202 | 0.151 | 0.086 | 49.5% | +33.96 | 0.999 | 0.785 |

`CPCV mean` = media Sharpe per-path su `comb(5,2) = 10` test path indipendenti.
`CPCV std` = deviazione standard cross-path → input per DSR `var_sr_trials`.

---

## 4. Findings

### 4.1 Threshold ottimo: **75** (vs 60 attuale)

Su entrambi i run (single shot + CPCV), threshold 75 maximizza DSR. Δ vs
attuale 60:

- **Sharpe annualizzato**: 1.20 vs 0.47 (single shot) — **+0.73**
- **Total return**: +69.5% vs +25.7% (single shot, 5y) — **+43.8 pp**
- **Max drawdown**: −13.9% vs −26.4% — **migliora di 12.5 pp**
- **Win rate**: 48.6% vs 45.4% — **+3.2 pp**
- **N trade**: 436 vs 577 — **−141 trade** (turnover ridotto del 24%)

Threshold più alto → meno trade ma media qualità migliore. Coerente con
filtering a higher conviction signals.

### 4.2 DSR sotto target 0.95 anche al threshold ottimo

Con CPCV, DSR a thr 75 = **0.848** — sotto soglia 0.95 per recommendation
tier 1. Significa: dato che abbiamo testato 5 threshold (multiple testing
correction), la confidence che il vero Sharpe sia migliore di
`E[max SR | 5 trials]` è 84.8% — buono ma non robusto al 95% richiesto da
acceptance gate end-Fase-A SIGNAL_ROADMAP.

Cause possibili:

1. **Universe troppo piccolo** (10 ticker mega-cap). Con universo 50-100
   ticker, varianza cross-path scende, Sharpe stimator stabile, DSR sale.
2. **Periodo 5y è breve** per CPCV con 5 groups. Più periodo → più sample
   per fold → SR più affidabile.
3. **Single asset class** (US large cap tech-heavy). Concentrazione settore
   gonfia varianza cross-path.

### 4.3 Pattern Sharpe vs threshold

Curva Sharpe(threshold) ha massimo locale a 75 e cala a 80. Pattern
"hunchback" tipico del threshold tuning — coerente con teoria:

- Threshold troppo basso → trade rumorosi, win rate scende, costi turnover
- Threshold troppo alto → segnali troppo rari, n_trade insufficiente, perdi
  diversificazione temporale

Sweet spot dipende da universo. Su mega-cap large universe, 75-80 è il
range. Su small/mid cap probabile shifting verso 60-70 (segnali più rari).

### 4.4 PSR sempre alto, DSR informativo

Tutti i threshold hanno PSR ≥ 0.988. PSR sa solo "Sharpe > 0?" — con
500+ trade è facile rispondere "sì". DSR aggiunge "Sharpe > E[max SR |
N=9 trials]?" — domanda molto più severa che separa thresh 40 (DSR 0.087)
da thresh 75 (DSR 0.472).

**Lesson**: in sweep / parameter tuning, PSR è inadeguato — usa sempre
DSR. Il framework attuale logga Sortino/Calmar ma non DSR; aggiungere a
metrics_v2 in iterazione successiva.

### 4.5 Single shot vs CPCV: variance comparison

| Metrica | Single shot @ thr 75 | CPCV mean @ thr 75 |
|---------|----------------------|--------------------|
| Sharpe per-trade | 0.174 | 0.163 ± 0.094 |
| DSR | 0.472 | 0.848 |
| N trade | 436 | 221 |

CPCV mean Sharpe è **inferiore** al single-shot (0.163 vs 0.174). Differenza
piccola, ma consistente: signal di overfitting moderato. Single shot fa
in-sample fit (vede tutti i 1260 bar), CPCV testa su held-out fold.

DSR più alto in CPCV è apparente paradosso: con CPCV calibration testa solo
5 threshold (vs 9 single shot), `E[max SR | N=5]` è più piccolo, DSR sale.
È correzione realistic: chi calibra con CPCV tipicamente esplora meno
threshold (run più lento), quindi merita correzione minore.

---

## 5. Caveats

1. **Universe ridotto (10 ticker)**: DSR non interpretabile come edge
   "production-ready". Servono universe 50-100+ ticker per stima affidabile.
2. **Single strategy (momentum)**: Contrarian e ETF rotation hanno DSR
   diversi. Ripetere calibration per ognuna.
3. **No transaction cost / slippage**: out-of-scope dichiarato (SIGNAL_ROADMAP
   §11). Ma `--tc-bps` su `propicks-backtest` esiste; integrarlo in
   `propicks-calibrate` se serve.
4. **Periodo 2021-2026 specifico**: include fase post-COVID + AI rally
   2023-2024 + corrective 2025. Threshold 75 è ottimo *in questo regime*.
   Su backtest 2008-2020 (regime diverso), threshold ottimo potrebbe
   spostarsi. Acceptance gate richiede `--start 2010-01-01 --end 2020-12-31`
   con `--historical-membership` per test out-of-sample temporale.
5. **Seed yfinance**: dati yfinance possono variare leggermente (split
   adjustment, bonus data). Re-run può dare numeri diversi al 2-5% level.
6. **No nested CV**: CPCV qui ha test path **out-of-sample** ma threshold
   sweep usa stesso CPCV split. Strict nested CV richiederebbe outer CPCV
   per threshold + inner CPCV per Sharpe estimation. Fuori scope A.2;
   raccomandato in Fase B.6 ablation.

---

## 6. Raccomandazioni operative

### 6.1 Cambiare il default config?

**Non ancora**. Il smoke run è su universe ridotto + DSR < 0.95. Prima di
toccare `config.MIN_SCORE_TECH`:

1. Re-run su universe S&P 500 top 50-100
2. Re-run su periodi multipli (2010-2015, 2015-2020, 2020-2025) per
   verificare stability
3. CPCV con outer/inner nested
4. Confronto cost-aware (con `--tc-bps`) — threshold più alto = meno turnover
   = costi più bassi, potenzialmente sposta il punto ottimo

Solo dopo questi check, propose aggiornamento `config.py`.

### 6.2 Workflow attuale

```bash
# Calibration baseline
propicks-calibrate --discover-sp500 --top 50 --period 5y \
    --thresholds "55:80:5" --historical-membership sp500 --use-cpcv

# Compare across regimes
propicks-calibrate --discover-sp500 --top 50 \
    --period 5y --start 2015-01-01 --end 2020-12-31 \
    --historical-membership sp500 --use-cpcv
```

### 6.3 Aggiungi DSR a metrics standard

`backtest/metrics_v2.compute_portfolio_metrics()` oggi calcola
sharpe_annualized e sortino_annualized ma **non PSR/DSR**. Aggiungere
nella prossima iterazione (banale, sono pure functions in
`domain/risk_stats.py`).

---

## 7. Conclusione

✓ Framework calibration end-to-end attivo: PSR + DSR + CPCV + recommendation
rule-based.

✓ Smoke run reale individua threshold 75 come ottimale (vs 60 attuale).
Sharpe annualizzato +0.73, max DD migliora 12 pp.

⚠ DSR < 0.95 anche al meglio → **edge marginale post multiple testing**
sull'universe 10-ticker. Servono test su universe più ampio prima di
cambiare default.

⚠ Threshold 60 attuale **non ottimale** ma neanche pessimo — DSR 0.62
con CPCV (= "62% confidence non è fluke"). Strategia funziona ma
sub-optimal.

**Acceptance gate end-Fase-A** (SIGNAL_ROADMAP §9):
> Sharpe gross > 0.4 strategia best, DSR p < 0.10. Else: ripensa universe.

Sharpe ann a thr 75 = **1.20** (single shot) / **0.16 cpcv mean × √50 ≈ 1.13**
annualizzato. **Sopra 0.4** ✓.

DSR p-value = 1 - DSR. A thr 75 CPCV: 1 - 0.848 = **0.152**. Sopra soglia
0.10 → **non passa** acceptance gate stretto. Per soddisfare, bisogna:

- Universe più grande (50+ ticker) per ridurre var(SR)
- Test multi-period
- O accettare gate più morbido (0.20 invece di 0.10) data la natura retail
  del framework

**Decisione**: documento questi findings, mantengo threshold 60 come default
fino a re-validation su universe 50+. Pagina di lavoro Fase A.2 chiusa con
caveat espliciti.
