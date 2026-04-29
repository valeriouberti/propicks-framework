# Ablation B.1 — Cross-Sectional Rank vs Absolute Threshold

Generated: **2026-04-29T17:07:57**
Spec: `momentum_sp500_top30_5y` (30 ticker resolved)

Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.1.

## Risultati

| Configurazione | N trades | Sharpe ann | Total ret % | Max DD % | Win rate | PSR | Sharpe per-trade |
|----------------|----------|------------|-------------|----------|----------|-----|------------------|
| baseline_v2_absolute_60 | 775 | 0.378 | 20.41 | -19.53 | 0.406 | 0.906 | 0.046 |
| b1_cross_sectional_top_quintile_p80 | 752 | 0.616 | 39.96 | -16.53 | 0.424 | 0.981 | 0.073 |
| b1_cross_sectional_top_tercile_p67 | 765 | 0.575 | 36.17 | -17.21 | 0.413 | 0.968 | 0.065 |
| b1_cross_sectional_top_decile_p90 | 732 | 0.874 | 62.99 | -16.33 | 0.421 | 0.993 | 0.088 |

## Δ vs baseline absolute_60

| Run | Δ Sharpe ann | Δ Total ret % | Δ N trades |
|-----|--------------|---------------|------------|
| b1_cross_sectional_top_quintile_p80 | +0.238 | +19.55 | -23 |
| b1_cross_sectional_top_tercile_p67 | +0.196 | +15.75 | -10 |
| b1_cross_sectional_top_decile_p90 | +0.495 | +42.58 | -43 |

## Interpretation

- **Δ Sharpe positivo** = cross-sectional aggiunge edge
- **N trade ridotto** atteso (filter più restrittivo) — verifica che Sharpe migliora abbastanza per compensare meno diversificazione temporale
- **Top quintile (P80) vs top tercile (P67)**: tradeoff edge vs n_trade. Top decile (P90) può fallire per insufficient samples

## Decision rule SIGNAL_ROADMAP B.6

Mantieni cross-sectional in default solo se delta Sharpe > +0.10 AND DSR p < 0.10 vs baseline. Altrimenti opzionale via flag.