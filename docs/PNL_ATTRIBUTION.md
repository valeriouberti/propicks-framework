# P&L Attribution + Weekly Report

> Phase 9: decomposizione automatica del P&L per ogni trade chiuso in 4
> componenti additive + Phase 7 strategy gate + report markdown auto-generato.

---

## 1. Decomposizione

```
total_pnl = market (β × SPX_return) + sector (ETF − SPX) + alpha + timing
```

**Perché**: capire *perché* vinci/perdi, non solo *quanto*. Lo stesso +10% può essere:
- +8% market (tutto beta — stai solo prendendo rischio SPX) → **no alpha**
- +8% alpha (selezione ticker ha aggiunto valore indipendente dal mercato) → **vero edge**

### 1.1 Metriche per-trade

- **market (β)**: `beta × spx_return(entry→exit)` — quanto è spiegato dal mercato
- **sector**: `sector_ETF_return − spx_return` — rotazione settoriale (solo US; EU=0)
- **alpha (residuo)**: `total − market − sector − timing` — selection edge
- **timing**: `(actual_bench_return − median_hold_bench_return) × beta` — edge timing exit vs hold passivo

---

## 2. Gate Phase 7 — quando promuovere nuove strategie

Ogni strategia deve raggiungere:

| Criterio              | Soglia |
|-----------------------|--------|
| Trade chiusi          | ≥ 15   |
| Profit factor         | ≥ 1.3  |
| Sharpe (trade-level)  | ≥ 0.8  |
| Win rate momentum     | ≥ 50%  |
| Win rate contrarian   | ≥ 55%  |
| Max drawdown          | ≥ −15% |
| Correlation con SPX   | ≤ 0.70 |

Se dopo 6 mesi una strategia non raggiunge queste soglie, **si ritira** invece
di aggiungere una strategia nuova per compensare. Questo è il gate concreto:
niente Phase 7 (nuove strategie) finché le 3 attuali non mostrano edge.

---

## 3. Weekly report automatico

Job `weekly_attribution_report` gira ogni **sabato 21:00 CET** e genera:

```
reports/attribution_YYYY-WW.md
```

Struttura markdown:
1. **📊 Portfolio KPIs**: total value, cash %, MTD/YTD vs SPX, alpha, max DD
2. **📈 Trade della settimana**: tabella per-trade con decomposition
3. **🎯 Per-strategy**: 30gg / 90gg / 365gg aggregati + Phase 7 gate status
4. **🌊 Per-regime breakdown**: win rate + avg P&L per regime macro (entry date)
5. **🚧 Gate detail**: strategie under threshold con failure reason esplicita
6. **⚠️ Attention**: trade con loss > 10% ultimi 30gg

Dopo la generazione, un alert `report_ready` viene creato → Telegram bot lo
delivera (Phase 4) → il trader riceve la notifica sabato sera mentre pianifica
la settimana.

---

## 4. Comandi

```bash
# CLI on-demand (genera + stampa + salva)
propicks-report attribution

# Scheduler one-shot (backfill o manual trigger)
propicks-scheduler job attribution       # alias: job report

# Dalla chat Telegram (Phase 4)
/report   # summary inline: per-strategy 30gg + gate status + heavy losses
```

---

## 5. Architettura

```
┌────────────────┐   ┌──────────────┐   ┌───────────────────┐
│ trades (Phase 1│   │ portfolio_   │   │ regime_history    │
│  + closed P&L) │   │  snapshots   │   │  (Phase 3)        │
└───────┬────────┘   └──────┬───────┘   └─────────┬─────────┘
        │                   │                     │
        ▼                   ▼                     ▼
       ┌────────────────────────────────────────────────┐
       │  domain/attribution.py                         │
       │  - decompose_trade (con OHLCV cache Phase 2)   │
       │  - aggregate_by_strategy                       │
       │  - aggregate_by_regime                         │
       │  - strategy_gate_status (Phase 7 check)        │
       │  - portfolio_vs_benchmark                      │
       └──────────────────┬─────────────────────────────┘
                          │
                          ▼
                   ┌─────────────────┐
                   │ reports/        │
                   │  attribution_   │
                   │  YYYY-WW.md     │
                   └────────┬────────┘
                            │
                            ▼
                   ┌─────────────────┐      /report
                   │ alert 'report_  │◄─────────┐
                   │  ready'         │          │
                   └────────┬────────┘          │
                            ▼                   │
                   ┌─────────────────┐          │
                   │ Telegram user   │──────────┘
                   └─────────────────┘
```

---

## 6. Design highlights

- **Pure functions**: `domain/attribution.py` testabile senza rete (tutte le series sono injectable).
- **Cache-aware**: benchmark + sector ETF letti direttamente dal OHLCV cache Phase 2. Offline-resilient.
- **Sector mapping**: solo ticker US (via `YF_SECTOR_TO_KEY` + `SECTOR_KEY_TO_US_ETF`). Per .MI/EU il sector component è 0 (skippato, non stimato) per evitare confounders.
- **Regime at entry**: il regime viene assegnato all'entry_date del trade, non exit. Un trade aperto in BULL e chiuso in BEAR resta "BULL" per attribution.
- **Timing computed on benchmark**: il timing è "hai beccato il momento giusto di USCIRE *rispetto al mercato*?", misurato via benchmark return durante actual holding vs median holding — non su ticker specifico.
- **Gate thresholds in `GATE_THRESHOLDS` dict**: modificabili centralmente se l'evidence empirica suggerisce soglie diverse. Win rate differenziato momentum vs contrarian (55% per contrarian riflette il profilo short-gamma).
- **Formatter report alert**: il bot Telegram invia un summary inline via `/report`, non il markdown completo (troppo lungo per Telegram).

---

## 7. Trade-off accettati

- **No Brinson-Hood-Beebower rigorous**: un attribution professional-grade richiede weights timeseries e factor loadings rolling. Qui facciamo trade-level additive decomposition. Sufficiente per retail; insufficiente per fund-level due diligence.
- **Beta statico**: usiamo `market_ticker_meta.beta` (TTL 7gg, da Yahoo 5y monthly). Se il beta è stale o il titolo ha cambiato profile (es. acquisizione), alpha è rumoroso. Accettabile per trader retail.
- **Timing semplice**: confronta total return su actual holding vs total return su median holding sul benchmark. Ignora volatility timing e path-dependence. Sufficient per detection macroscopica.
- **Gate conservativo**: un singolo criterio che fallisce → la strategia è "fail". In realtà molte strategie passano 5/6 criteri ma falliscono 1 (es. correlation 0.72 vs soglia 0.70). Il report mostra tutti i failure esplicitamente per decisione informata.
