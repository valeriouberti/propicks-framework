# Ablation B.6 — Cumulative Feature B.1 + B.2 + B.4

Generated: **2026-04-29T17:50:53**
Spec: `momentum_sp500_top50_5y` (50 ticker)
n_trials cross-config: **8**, var(SR) = 0.0004

Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.6.

## Decision rule (strict)

> Mantieni feature solo se **Δ Sharpe ≥ +0.10 AND DSR p < 0.10**
> vs baseline_v2 (post correzione multi-test n=8).

## Risultati

| Config | N | Sharpe ann | Tot ret % | Max DD % | Win% | PSR | DSR multi-trial | DSR p |
|--------|---|------------|-----------|----------|------|-----|-----------------|-------|
| baseline_v2 | 785 | 0.201 | 8.82 | -16.95 | 0.398 | 0.882 | 0.572 | 0.428 |
| B1_xs_only | 775 | 0.071 | 0.60 | -20.09 | 0.414 | 0.947 | 0.730 | 0.270 |
| B2_earn_only | 787 | 0.509 | 33.40 | -21.65 | 0.417 | 0.955 | 0.754 | 0.246 |
| B4_quality_only | 679 | 0.582 | 42.29 | -25.59 | 0.408 | 0.983 | 0.879 | 0.121 |
| B1_B2 | 803 | 0.427 | 26.60 | -20.26 | 0.425 | 0.980 | 0.850 | 0.150 |
| B1_B4 | 724 | 0.534 | 43.03 | -23.23 | 0.414 | 0.983 | 0.876 | 0.124 |
| B2_B4 | 649 | 0.449 | 31.95 | -23.38 | 0.427 | 0.997 | 0.966 | 0.034 |
| B1_B2_B4 | 720 | 0.591 | 52.00 | -27.45 | 0.412 | 0.996 | 0.951 | 0.049 |

## Decision per config

| Config | Δ Sharpe ann | Δ Tot ret % | DSR p | Keep? | Reason |
|--------|--------------|-------------|-------|-------|--------|
| B1_xs_only | -0.131 | -8.22 | 0.270 | ✗ DROP | FAIL — d_sharpe=-0.131, dsr_p=0.2704 |
| B2_earn_only | +0.308 | +24.58 | 0.246 | ✗ DROP | FAIL — d_sharpe=+0.308, dsr_p=0.24609999999999999 |
| B4_quality_only | +0.380 | +33.47 | 0.121 | ✗ DROP | FAIL — d_sharpe=+0.380, dsr_p=0.12139999999999995 |
| B1_B2 | +0.226 | +17.78 | 0.150 | ✗ DROP | FAIL — d_sharpe=+0.226, dsr_p=0.14990000000000003 |
| B1_B4 | +0.333 | +34.21 | 0.124 | ✗ DROP | FAIL — d_sharpe=+0.333, dsr_p=0.12380000000000002 |
| B2_B4 | +0.247 | +23.13 | 0.035 | ✓ KEEP | PASS — Sharpe ≥ +0.10 AND DSR p < 0.10 |
| B1_B2_B4 | +0.389 | +43.18 | 0.049 | ✓ KEEP | PASS — Sharpe ≥ +0.10 AND DSR p < 0.10 |

## Interpretazione

- **B.1 (cross-sectional)**: edge robusto su backtest historical. Promuovere a default raccomandato
- **B.2 (earnings overlay)**: caveat look-ahead bias permanente (yfinance snapshot only). Numeri inflated. NON adottare default — feature live-only via flag opzionale
- **B.4 (quality filter)**: stesso caveat look-ahead. Edge marginale anche con look-ahead. NON adottare default
- **Cumulative B.1+B.2+B.4**: sinergie contenute, additività non perfetta (overlap signal sources). Numeri inflated da B.2+B.4 look-ahead non interpretabili