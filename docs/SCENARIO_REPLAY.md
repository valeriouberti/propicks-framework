# Historical Scenario Replay — Fase E.1 SIGNAL_ROADMAP

Generated: **2026-04-30T08:51:23**
Universe stable: 20 mega-cap

Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §8 Fase E.1.

## Scenari testati

### 2020_COVID

**2020 COVID crash + V-shape recovery**
Period: `2019-07-01` → `2021-06-30`. Key event: 2020-03-23 SP500 bottom.
Universe resolved: 20 ticker. Auto percentile: P70.

| Config | N | Sharpe ann | Tot ret % | Max DD % | Recovery days | PSR |
|--------|---|------------|-----------|----------|---------------|-----|
| baseline_v2 | 185 | 1.547 | 27.60 | -7.96 | — | 0.999 |
| B1_xs_auto_pct | 182 | 1.044 | 17.09 | -7.83 | — | 0.999 |
| B1_C6_full | 166 | 1.286 | 24.05 | -9.40 | — | 0.996 |

## Lettura

- **Recovery days** = giorni dal trough al ritorno a peak pre-crash. Più rapido = strategia robusta a recovery
- **Max DD scenario** vs Max DD baseline 5y: discrepanza grande = drawdown protection sub-optimal
- **B.1 + C.6 vs baseline**: cross-sectional + multi-lookback dovrebbero ridurre DD su crash event (mom flip earlier)

## Caveat

- Universe stable (top 20 mega-cap) sotto-rappresentativo: durante GFC 2008 banks/financials hanno fatto −60%, mega-cap tech +30% (Apple+google). Bias positivo per stable selection
- yfinance pre-2010 può avere data quality issue (split adjustment, delisted not present)
- Earnings gate disabilitato (storical earnings not available) → trades opened during earnings