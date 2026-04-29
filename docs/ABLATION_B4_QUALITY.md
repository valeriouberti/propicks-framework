# Ablation B.4 — Quality Filter (Asness QMJ)

Generated: **2026-04-29T17:38:28**
Spec: `momentum_sp500_top30_5y` (30 ticker)
Cross-sectional: True

Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.4.

## ⚠ Caveat look-ahead bias

yfinance ``info`` ritorna **snapshot oggi** dei fundamentals (TTM). Backtest historical applica filter 'current quality' a entry passate → look-ahead bias. Numeri sotto NON interpretabili come edge OOS reale.

## Risultati

| Configurazione | Q% | N trades | Sharpe ann | Tot ret % | Max DD % | Win % | PSR |
|----------------|----|----------|------------|-----------|----------|-------|-----|
| baseline_no_quality_filter | None | 795 | 0.317 | 16.60 | -19.17 | 0.399 | 0.890 |
| b4_top_half_T50 | 50.0 | 703 | 0.204 | 9.83 | -28.73 | 0.410 | 0.956 |
| b4_top_tercile_T67 | 67.0 | 614 | 0.300 | 19.04 | -22.58 | 0.401 | 0.925 |
| b4_top_quintile_T80 | 80.0 | 380 | 0.342 | 19.25 | -18.38 | 0.400 | 0.873 |

## Δ vs baseline

| Run | Δ Sharpe | Δ Tot ret % | Δ N trades |
|-----|----------|-------------|------------|
| b4_top_half_T50 | -0.114 | -6.77 | -92 |
| b4_top_tercile_T67 | -0.018 | +2.44 | -181 |
| b4_top_quintile_T80 | +0.025 | +2.65 | -415 |

## Quality scores distribution

| Ticker | Quality score |
|--------|---------------|
| A | 74.3 |
| AAPL | 73.7 |
| ABBV | 85.6 |
| ABNB | 84.9 |
| ABT | 84.0 |
| ACGL | 72.4 |
| ACN | 74.1 |
| ADBE | 89.4 |
| ADI | 78.8 |
| ADM | 54.8 |
| ADP | 66.7 |
| ADSK | 76.5 |
| AEE | 52.8 |
| AEP | 51.1 |
| AES | 33.2 |
| AFL | 68.9 |
| AIG | 67.1 |
| AIZ | 57.6 |
| AJG | 64.5 |
| AKAM | 62.0 |
| ALB | 57.3 |
| ALGN | 54.0 |
| ALL | 70.0 |
| ALLE | 66.4 |
| AMAT | 81.4 |
| AMCR | 44.8 |
| AMD | 77.4 |
| AME | 73.0 |
| AMGN | 55.2 |
| AMP | 70.4 |

## Decision rule

Mantieni quality filter solo se Δ Sharpe > +0.10 senza inflate look-ahead. Per validation OOS proper serve dataset historical fundamentals (paid). Feature utile in **live signal mode** (snapshot truly current); **NON adottare default basandosi su backtest**.