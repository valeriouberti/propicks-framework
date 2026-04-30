# Ablation Fase C — C.0 + C.4 + C.6

Generated: **2026-04-30T10:47:21**
Spec: `momentum_sp500_top100_5y` (100 ticker)
Auto percentile (C.0): **P90** for 100 universe

## Risultati

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR | DSR mt | DSR p |
|--------|---|------------|-----------|----------|-----|--------|-------|
| baseline_v2 | 765 | 0.314 | 15.66 | -15.99 | 0.970 | 0.798 | 0.202 |
| C0_auto_percentile | 758 | 0.328 | 16.39 | -16.93 | 0.995 | 0.932 | 0.068 |
| C4_obv_only | 721 | 0.166 | 6.29 | -21.87 | 0.942 | 0.711 | 0.289 |
| C6_multi_lookback_only | 741 | 0.195 | 8.44 | -18.01 | 0.909 | 0.622 | 0.378 |
| C0_C4 | 726 | 0.301 | 14.51 | -17.20 | 0.918 | 0.648 | 0.352 |
| C0_C6 | 762 | 0.142 | 5.05 | -22.42 | 0.781 | 0.399 | 0.601 |
| C4_C6 | 716 | 0.134 | 4.54 | -24.70 | 0.718 | 0.338 | 0.662 |
| C0_C4_C6 | 708 | 0.372 | 21.78 | -19.37 | 0.878 | 0.565 | 0.435 |

## Decision per config

| Config | Δ Sharpe ann | DSR p | Keep? |
|--------|--------------|-------|-------|
| C0_auto_percentile | +0.013 | 0.068 | ✗ DROP |
| C4_obv_only | -0.148 | 0.289 | ✗ DROP |
| C6_multi_lookback_only | -0.119 | 0.378 | ✗ DROP |
| C0_C4 | -0.013 | 0.351 | ✗ DROP |
| C0_C6 | -0.172 | 0.601 | ✗ DROP |
| C4_C6 | -0.180 | 0.662 | ✗ DROP |
| C0_C4_C6 | +0.058 | 0.435 | ✗ DROP |

## Note

- **C.0**: auto-tuned percentile per universe size. Risolve scaling issue B.1 su universe broader (B.6 finding)
- **C.4 OBV**: sostituisce volume sub-score asymmetric. NON look-ahead.
- **C.6 multi-lookback**: ensemble 1m/3m/6m/12m skip-recent 21. Pure mathematical, NON look-ahead. Standard institutional