# Ablation B.2 — Earnings Revision Overlay

Generated: **2026-04-29T17:17:48**
Spec: `momentum_sp500_top30_5y` (30 ticker)
Cross-sectional: True

Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) Fase B.2.

## Caveat dataset

yfinance espone earnings revision metrics solo come **current snapshot**. Backtest historical applica lo *stesso* earnings_score per tutti i bar dello stesso ticker. Effetto = ticker-level prior. Per alpha Chan-Jegadeesh-Lakonishok dinamico serve IBES historical (paid).

## Risultati

| Configurazione | Weight | N trades | Sharpe ann | Total ret % | Max DD % | Win rate | PSR |
|----------------|--------|----------|------------|-------------|----------|----------|-----|
| baseline_no_overlay | 0.00 | 795 | 0.319 | 16.72 | -19.17 | 0.399 | 0.892 |
| b2_overlay_0.15 | 0.15 | 775 | 0.602 | 40.59 | -17.69 | 0.422 | 0.992 |
| b2_overlay_0.20 | 0.20 | 783 | 0.554 | 36.26 | -17.08 | 0.420 | 0.993 |
| b2_overlay_0.30 | 0.30 | 763 | 0.776 | 59.98 | -18.46 | 0.444 | 0.998 |

## Δ vs baseline (no overlay)

| Run | Δ Sharpe ann | Δ Total ret % | Δ N trades |
|-----|--------------|---------------|------------|
| b2_overlay_0.15 | +0.283 | +23.87 | -20 |
| b2_overlay_0.20 | +0.235 | +19.54 | -12 |
| b2_overlay_0.30 | +0.457 | +43.26 | -32 |

## Earnings score distribution

| Ticker | Score |
|--------|-------|
| A | 57.2 |
| AAPL | 75.5 |
| ABBV | 45.9 |
| ABNB | 37.2 |
| ABT | 42.9 |
| ACGL | 60.8 |
| ACN | 46.1 |
| ADBE | 47.9 |
| ADI | 73.4 |
| ADM | 77.6 |
| ADP | 56.2 |
| ADSK | 81.6 |
| AEE | 73.2 |
| AEP | 62.0 |
| AES | 73.6 |
| AFL | 44.3 |
| AIG | 47.0 |
| AIZ | 51.2 |
| AJG | 51.2 |
| AKAM | 51.8 |
| ALB | 67.3 |
| ALGN | 74.1 |
| ALL | 46.1 |
| ALLE | 48.3 |
| AMAT | 76.6 |
| AMCR | 50.6 |
| AMD | 73.3 |
| AME | 61.0 |
| AMGN | 50.0 |
| AMP | 59.0 |

## Decision rule SIGNAL_ROADMAP B.6

Mantieni overlay solo se Δ Sharpe > +0.10 + DSR p < 0.10. Considerare caveat ticker-level-prior — alpha vero dipende da dataset historical revisions, non disponibile su yfinance.

---

## ⚠ Look-ahead bias warning

**I numeri sopra sono inflated da look-ahead bias**:

- `earnings_score` snapshot **oggi** include `surprisePercent` dei 4 quarter
  più recenti (es. 2025-Q1 a 2026-Q1)
- Backtest 2021-2026 sovrappone quel range temporale
- Filtro "ticker con high earnings score" usa info **futura** (rispetto a
  trade aperti 2021-2024) per scegliere oggi

Effetto: il backtest premia ticker che hanno avuto good earnings nei trimestri
2024-2026, valutati su trade aperti 2021-2024. Classic data-snooping.

### Mitigazione (richiede effort)

1. **Point-in-time earnings dataset**: per ogni bar t, `earnings_score`
   calcolato con dati ≤ t. Yfinance NON espone. Source paid: IBES, FactSet
2. **Proxy storico**: sliding window su `earnings_history` filtered by date.
   Cattura earnings beat track, non revision flow
3. **Free alternative**: Estimize.com (crowd-sourced) — covering limitato

### Conclusione operativa

- ✓ Feature B.2 **utile in live signal mode** (snapshot truly current)
- ⚠ Numeri ablation backtest **non interpretabili come edge OOS**
- ❌ NON adottare overlay default basandosi su questi numeri
- → Registra overlay come **flag CLI opzionale**, monitor live-trade per N
  mesi prima di promotion default
- → Re-validation futura con dataset point-in-time pendente