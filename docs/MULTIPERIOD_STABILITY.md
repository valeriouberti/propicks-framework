# Multi-Period Stability — P0.2 SIGNAL_ROADMAP

Generated: **2026-04-30T10:50:29**
Universe: top 50 SP500 (auto_percentile P88)

## Risultati per periodo

### 2018_2020: Pre-COVID + Q4 2018 correction

Period: `2018-01-01` → `2019-12-31`. Universe: 48 ticker.

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR |
|--------|---|------------|-----------|----------|-----|
| baseline_v2 | 171 | 2.327 | 36.14 | -4.05 | 1.000 |
| C0_C4_C6 | 140 | 1.513 | 23.97 | -6.22 | 1.000 |

### 2020_2022: COVID + reflation rally

Period: `2020-01-01` → `2021-12-31`. Universe: 49 ticker.

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR |
|--------|---|------------|-----------|----------|-----|
| baseline_v2 | 188 | 0.630 | 10.86 | -8.26 | 0.994 |
| C0_C4_C6 | 180 | 0.413 | 7.44 | -7.64 | 0.911 |

### 2022_2024: Rate shock + bear + recovery

Period: `2022-01-01` → `2023-12-31`. Universe: 50 ticker.

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR |
|--------|---|------------|-----------|----------|-----|
| baseline_v2 | 189 | 0.489 | 7.75 | -11.82 | 0.923 |
| C0_C4_C6 | 191 | 0.533 | 9.88 | -8.43 | 0.891 |

### 2024_2026: AI rally + recent

Period: `2024-01-01` → `2026-04-30`. Universe: 50 ticker.

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR |
|--------|---|------------|-----------|----------|-----|
| baseline_v2 | 249 | 0.193 | 3.09 | -11.01 | 0.822 |
| C0_C4_C6 | 232 | 0.566 | 13.99 | -15.54 | 0.897 |

## Stability metrics cross-period

| Config | N periods | Sharpe mean | Sharpe std | Sharpe min | Sharpe max |
|--------|-----------|-------------|------------|------------|------------|
| baseline_v2 | 4 | 0.910 | 0.962 | 0.193 | 2.327 |
| C0_C4_C6 | 4 | 0.756 | 0.509 | 0.413 | 1.513 |

## Lettura

- **Sharpe std cross-period basso** = strategia stabile across regime
- **Sharpe min**: worst-case observed. Se < 0 in qualche periodo, edge non robust
- **C0_C4_C6 vs baseline**: differenza Sharpe stable across period = edge incrementale robusto. Se differenza varia molto, edge regime-dependent (concerning)

---

## Findings P0.2 critici

### Δ Sharpe cross-period (C0_C4_C6 vs baseline)

| Period | baseline | C0_C4_C6 | Δ |
|--------|----------|----------|---|
| 2018-2020 | 2.33 | 1.51 | **−0.82** ❌ |
| 2020-2022 | 0.63 | 0.41 | −0.22 ❌ |
| 2022-2024 | 0.49 | 0.53 | +0.05 ✓ |
| 2024-2026 | 0.19 | 0.57 | **+0.38** ✓ |

**Edge è REGIME-DEPENDENT**. C0_C4_C6 sotto-performa in 2018-2022 (low-vol
bull period) e sovra-performa in 2022-2026 (post rate-shock).

### Implicazioni

1. **Backtest 5y "current" finestra biased**: i numeri Fase B+C (Sharpe
   +0.24/+0.39 vs baseline) erano tutti su 2021-2026 → sovrappongono
   regime favorevole a cumulative C. Non rappresentativo di full cycle
2. **C0_C4_C6 è high-vol regime filter**: funziona quando volatilità
   strutturale è alta (post-2022 rate shock). In low-vol bull (2018-2019)
   filter elimina opportunities → underperforms baseline
3. **Trade-off return vs consistency**: baseline std 0.96 (high upside,
   high variance), C0_C4_C6 std 0.51 (plateau medio, floor migliore)

### Sospetto overfitting recente

C0_C4_C6 best Sharpe (0.57) è proprio nel **periodo più recente** dove
parameter tuning + ablation eseguiti. Pattern monotonico aumento edge
2018→2026 = potential overfit OR genuine regime shift. Senza OOS post-2026
non distinguibile.

### Verdict P0.2

**Edge real ma regime-dependent + sospetto overfit recente.**

- ❌ NON promotere C0_C4_C6 a default — rischio overfit
- ✓ Mantieni come **opt-in** per uso retail mega-cap
- ⚠ Ri-validare ogni 6 mesi multi-period rolling
- ⚠ Decay monitor (D.4) critico — primo segnale edge 2024-2026 svanisce

Per edge robust servirebbe:
- Test 2008-2018 (yfinance pre-2010 quality issues)
- Forward OOS post 2026-04 — wait-and-see 6-12 mesi
- Re-tuning parametri su sub-period diverso + cross-validation