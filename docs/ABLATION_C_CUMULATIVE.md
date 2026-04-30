# Ablation Fase C — C.0 + C.4 + C.6

Generated: **2026-04-29T18:02:44**
Spec: `momentum_sp500_top50_5y` (50 ticker)
Auto percentile (C.0): **P88** for 50 universe

## Risultati

| Config | N | Sharpe ann | Tot ret % | Max DD % | PSR | DSR mt | DSR p |
|--------|---|------------|-----------|----------|-----|--------|-------|
| baseline_v2 | 775 | 0.354 | 18.58 | -15.55 | 0.637 | 0.205 | 0.795 |
| C0_auto_percentile | 734 | 0.197 | 8.49 | -16.22 | 0.917 | 0.587 | 0.413 |
| C4_obv_only | 741 | 0.259 | 11.84 | -20.87 | 0.789 | 0.361 | 0.639 |
| C6_multi_lookback_only | 778 | 0.299 | 16.21 | -16.85 | 0.992 | 0.889 | 0.111 |
| C0_C4 | 748 | 0.217 | 9.73 | -15.40 | 0.942 | 0.655 | 0.345 |
| C0_C6 | 741 | 0.505 | 32.13 | -16.86 | 0.990 | 0.873 | 0.127 |
| C4_C6 | 736 | 0.276 | 14.61 | -19.16 | 0.956 | 0.705 | 0.295 |
| C0_C4_C6 | 745 | 0.591 | 41.28 | -17.46 | 0.921 | 0.596 | 0.404 |

## Decision per config

| Config | Δ Sharpe ann | DSR p | Keep? |
|--------|--------------|-------|-------|
| C0_auto_percentile | -0.157 | 0.413 | ✗ DROP |
| C4_obv_only | -0.095 | 0.639 | ✗ DROP |
| C6_multi_lookback_only | -0.054 | 0.111 | ✗ DROP |
| C0_C4 | -0.137 | 0.344 | ✗ DROP |
| C0_C6 | +0.151 | 0.127 | ✗ DROP |
| C4_C6 | -0.078 | 0.295 | ✗ DROP |
| C0_C4_C6 | +0.237 | 0.404 | ✗ DROP |

## Note

- **C.0**: auto-tuned percentile per universe size. Risolve scaling issue B.1 su universe broader (B.6 finding)
- **C.4 OBV**: sostituisce volume sub-score asymmetric. NON look-ahead.
- **C.6 multi-lookback**: ensemble 1m/3m/6m/12m skip-recent 21. Pure mathematical, NON look-ahead. Standard institutional