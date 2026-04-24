# Roadmap Next Steps

Stato al **Phase 6 start**. Questo documento traccia le fasi rimaste, i gate,
e le dipendenze. Da consultare quando si decide dove investire lavoro
incrementale.

Per lo stato operativo live e i comandi → [USER_GUIDE.md](./USER_GUIDE.md).  
Per architettura + invarianti → [../CLAUDE.md](../CLAUDE.md).

---

## Stato complessivo

### Completate ✅

| Phase | Deliverable | Output |
|-------|-------------|--------|
| **1** | DB SQLite + migration | Source of truth unificata, 9 tabelle |
| **2** | Market data cache | Speedup 7-10× su scan, offline resilience |
| **3** | Scheduler + 8 job | Automazione EOD + alert queue |
| **4** | Telegram bot | Push + command bidirezionale |
| **5** | Risk framework v2 | Kelly + VaR + vol + corr penalty (advisory) |
| **8** | Catalyst calendar | Earnings hard gate + macro events 2026 |
| **9** | Attribution + weekly report | Decomposition α/β/sector/timing + gate Phase 7 |

**Output cumulativo**:
- 12 CLI entry points
- 10 dashboard pages (post alignment check)
- 8 scheduler jobs
- 11 bot commands
- 460+ unit tests

### In corso 🚧

| Phase | Deliverable | Perché ora |
|-------|-------------|------------|
| **6** | Backtest v2 portfolio-level | Validation sistema complessivo prima di Phase 7 |

### Gated / on-hold 🔒

| Phase | Gate condition |
|-------|----------------|
| **7** — Nuove strategie | ≥ 15 trade chiusi/strategy + gate Phase 7 criteria met (profit factor, sharpe, correlation) |

### Eliminate ❌

| Phase | Motivo |
|-------|--------|
| **10** — Paper trading mode | Out-of-scope — il DB già permette separazione via strategy tag |
| **11** — Broker IBKR integration | Out-of-scope — decisione trader, desktop-only non ha bisogno |
| **12** — Intraday real-time layer | Out-of-scope — EOD è sufficiente per holding 2-8 settimane |
| **FE dedicato** | Eliminata — Streamlit è fit-for-purpose, no duplicate surface |

---

## Phase 6 — Backtest v2 portfolio-level

**Scope**: sostituire backtest single-ticker attuale con engine portfolio-level
che rispetti le invarianti di business (max posizioni, cap size, cash reserve,
earnings gate) e produca metriche realistiche con TC + slippage.

### Deliverables

1. **`domain/backtest_portfolio.py`** — engine portfolio-level:
   - Simula portfolio state nel tempo (cash, positions)
   - Ranking cross-ticker + top-N qualified entries per day
   - Rispetta invarianti: `MAX_POSITIONS=10`, size cap per bucket, `MIN_CASH_RESERVE=20%`, earnings gate
   - Multi-strategy tag (momentum, contrarian, ETF) con attribution breakdown
   - Exit rules: stop hit, target hit, time stop, end-of-period

2. **`backtest/costs.py`** — transaction cost model:
   - IBKR commissioni: $0 stock US, €2 .MI (ESMA), variable bp options
   - Spread bid-ask: 5bp stock liquid US, 10bp .MI, 2bp ETF
   - Slippage: 2bp extra su market orders
   - Model configurable via `--tc-bps N --slip-bps N` flags

3. **`backtest/walkforward.py`** — OOS split + Monte Carlo:
   - Train/test split (default 70/30) per calibrare pesi scoring
   - Rolling window re-calibration (default 6 mesi)
   - Monte Carlo bootstrap (1000 samples) su trade sequence → CI su Sharpe, WinRate, MaxDD
   - Robustness score = (point estimate - CI lower) / CI width

4. **`backtest/metrics_v2.py`** — portfolio-level KPIs:
   - Equity curve daily → Sharpe/Sortino annualized
   - Max DD portfolio, Calmar ratio
   - Correlation con SPX/FTSEMIB
   - Per-strategy breakdown derivato da simulated trades

5. **CLI** — estende `propicks-backtest`:
   - `--portfolio` flag per abilitare engine v2 (default: single-ticker legacy)
   - `--tc-bps N`, `--slip-bps N`, `--oos-split 0.7`, `--monte-carlo 1000`
   - `--strategies momentum,contrarian,etf` filter
   - Output: markdown report in `reports/backtest_YYYY-MM-DD.md`

6. **Dashboard** — tab "Portfolio backtest" in `6_Backtest.py`:
   - Form: universe tickers, date range, TC/slippage, OOS split
   - Chart equity curve + drawdown subplot (plotly)
   - Tabella per-strategy breakdown
   - Monte Carlo CI display (robustness check)

### Architettura

```
          ┌──────────────────────────────────────┐
          │ propicks-backtest --portfolio        │
          └──────────────────────────────────────┘
                           │
                           ▼
          ┌──────────────────────────────────────┐
          │ backtest/walkforward.py              │
          │  - train/test split                  │
          │  - rolling window re-calibration     │
          └──────────────────┬───────────────────┘
                             │
                             ▼
          ┌──────────────────────────────────────┐
          │ domain/backtest_portfolio.py         │
          │  (engine simulation nel tempo)       │
          │  - cash/positions state machine      │
          │  - score ranking + select top-N      │
          │  - earnings gate + macro warning     │
          │  - Kelly Phase 5 opt-in              │
          └──────┬────────────────────┬──────────┘
                 │                    │
                 ▼                    ▼
       ┌──────────────┐   ┌────────────────────┐
       │ backtest/    │   │ backtest/          │
       │  costs.py    │   │  metrics_v2.py     │
       │ (TC+slip)    │   │ (portfolio KPIs)   │
       └──────────────┘   └────────────────────┘
                             │
                             ▼
          ┌──────────────────────────────────────┐
          │ reports/backtest_YYYY-MM-DD.md       │
          └──────────────────────────────────────┘
```

### Trade-off preview

- **No survivorship bias correction**: richiederebbe elenco ticker delisted
  (non disponibile gratis). Documented limitation.
- **No earnings gap modeling**: stop su gap post-earnings viene fillato
  a stop level, non al gap reale. Sottostima loss. Documented.
- **Simple cost model**: non differenzia per order size / time-of-day.
  Accettabile per retail.
- **Re-calibration window fissa**: 6 mesi hardcoded. In-sample curve fit
  possibile se il trader ri-fitta continuamente. Gate: Monte Carlo CI.

### Tests attesi

- Invariant check: con 100 ticker in universe, il portfolio NON supera mai
  `MAX_POSITIONS` aperte simultanee
- Cost math: 10 trade × €2 commissione = €20 di drag, esattamente
- OOS split: train set + test set = total, no leakage (dates disjoint)
- Monte Carlo bootstrap: distribuzione Sharpe con CI 95%

### Effort stimato
5-7 giorni part-time. Il più sostanzioso perché implica design di
simulazione temporale + edge cases (partial fills, weekend gaps, corporate
actions).

---

## Phase 7 — Nuove strategie (GATED)

**Status**: on hold fino a evidence Phase 9 attribution che le 3 strategie
attuali (momentum, contrarian, ETF rotation) producano edge.

### Gate Phase 7 — criteri misurabili

Ogni strategia esistente deve raggiungere:

| Criterio | Soglia | Sorgente |
|----------|--------|----------|
| Trade chiusi | ≥ 15 | `trades` table |
| Profit factor | ≥ 1.3 | attribution report |
| Sharpe (trade-level) | ≥ 0.8 | attribution report |
| Win rate (momentum) | ≥ 50% | attribution report |
| Win rate (contrarian) | ≥ 55% | attribution report |
| Max drawdown | ≥ -15% | attribution report |
| Correlation con SPX | ≤ 0.70 | attribution report |

Il **gate check** è nel report settimanale (`propicks-report attribution`).
Quando **tutte** le strategie passano → OK per Phase 7.

**Timeline realistica**: assumendo ~1-2 trade chiusi/settimana per strategia,
15 trade = 8-15 settimane di trading reale. Con 3 strategie in parallelo,
il gate è tipicamente raggiungibile in 4-6 mesi.

### Strategie candidate (priorità)

Ordine suggerito da senior quant, quando il gate è passato:

#### 1. **Event-driven: PEAD (Post-Earnings Announcement Drift)**

Anomalia pubblicata dal 1968 (Ball & Brown). Long post-earnings beat + upward
guidance revision, exit a 45-60gg. Perché:
- Dati facili: usiamo già earnings date (Phase 8)
- Ortogonale al momentum/contrarian — diverso trigger, diverso horizon
- Letteratura robusta, edge documentato su retail

**Implementazione**:
- `domain/pead_scoring.py` — score basato su earnings surprise + analyst revisions
- `cli/pead.py` — `propicks-pead` entry point
- Integration con `calendar.py`: query earnings recenti

**Effort**: 3-4 giorni.

#### 2. **Pair trading su sector ETF**

Mean-reversion pair (es. XLE-XLB, XLK-XLY) con cointegration test Engle-Granger.
Long spread when 2σ below 60d mean, exit al ritorno.

**Implementazione**:
- `domain/pair_trading.py` — cointegration + z-score
- Universe: coppie highly-correlated (già presenti in ETF rotation)
- **Vantaggio**: market-neutral, no regime-dependent

**Effort**: 4-5 giorni (cointegration testing robusto è la parte lenta).

#### 3. **Covered call overlay**

Vendita call OTM su top holdings momentum (>1% weight, low vol). Income
strategy, riduce drawdown vs pure long.

**Implementazione**:
- Richiede options data (delayed IV da yfinance — limitato ma workable)
- `domain/covered_call.py` — strike selection + expiry
- **Vantaggio**: yield enhancement, defensive overlay

**Effort**: 5-7 giorni (options API integration + sizing delta-adjusted).

### Non implementeremo (out-of-scope)

- **Crypto** — diverso profilo vol/liquidità, regulation instabile
- **Futures/leverage** — cambia la math del Kelly e della tail risk
- **Deep learning price prediction** — 50 trade/anno non basta per training
- **HFT/scalping** — infra richiesta (bassa-latency) out of scope

---

## Miglioramenti infrastrutturali (quando servono)

Lista di *nice-to-have* che emergeranno dall'uso reale. Non prioritari finché
non c'è un bisogno concreto.

### Database

- **Postgres migration**: se il sistema si accede da più device/cloud.
  Attualmente SQLite locale è sufficiente. Migrazione = 1-2 giorni.
- **TimescaleDB extension**: se il cache OHLCV passa 10M righe (anni × centinaia
  di ticker). Attualmente 750 righe × 3 ticker = trivial.

### Data quality

- **Survivorship bias**: integrare elenco ticker delisted da CRSP o
  quandl (costano, currently out-of-scope).
- **Corporate actions**: yfinance fornisce `splits` e `dividends`. Attualmente
  ignorati — impact minore su strategie 2-8 settimane ma documented.
- **Multi-source redundancy**: aggiungere Alpha Vantage / IEX come fallback
  quando yfinance è down. Attualmente fail-fast; fallback è 1 giornata di lavoro.

### AI validation

- **Multi-model ensemble**: oltre Claude, aggiungere GPT-5 / Gemini 2.5 Pro
  come second opinion. Costo: +1 chiamata/ticker. Utilità: riduce model-specific bias.
- **Fine-tuning su journal**: se ci sono >200 verdict con outcome noto,
  fine-tune un modello piccolo su `(analysis, verdict, outcome)`. Overkill
  fino a evidence di valore.

### Osservabilità

- **Metrics export**: Prometheus/Grafana per sistemi con dashboard dedicata.
  Attualmente stderr + DB logs sono sufficienti per retail.
- **Alert routing**: supporto Slack / email / SMS oltre Telegram. Facile
  aggiunta al dispatcher Phase 4 — pattern già stabilito.

### Dashboard UX

- **Real-time updates**: Streamlit rerun automatico su DB change.
  Attualmente manual refresh. Non critico.
- **Mobile responsive**: la dashboard funziona ma non è ottimizzata.
  Decisione consapevole — il bot Telegram è il mobile-first entry.

### Scheduler

- **Retry automatico**: attualmente retry solo sul ciclo successivo.
  Aggiungere backoff exponential per fallimenti transient.
- **Dependency graph**: jobs dipendenti (snapshot dopo warm_cache). Attualmente
  gestito via cron ordering.

### Strategie (minor tweaks)

- **Rolling Kelly calibration**: attualmente Kelly è stimato su tutto il journal.
  Rolling window ultimi N mesi darebbe Kelly regime-aware.
- **Correlation-based position sizing** (diverse da Phase 5 corr_penalty):
  risk parity allocation tra ticker.

---

## Criteri per decidere il "next most valuable thing"

Ogni 4 settimane, prima di iniziare un nuovo work stream, chiediti:

1. **C'è evidence di edge?** Se attribution report mostra PF < 1.3 → priorità
   a debug/calibration, NON a nuove strategie.

2. **Il sistema fallisce su un caso d'uso reale?** Scheduler crash, bot down,
   dati stale — fixare questi prima di feature nuove.

3. **Il ROI del lavoro è chiaro?** 5 giorni di pair trading vs 2 giorni di
   miglior attribution report. Se il ROI è ambiguo, fai il più piccolo.

4. **Stiamo introducendo complessità senza beneficio proporzionale?** Phase 7
   gate esiste per questo: niente nuove strategie se le esistenti non
   dimostrano edge. Same principle ovunque.

5. **Hai abbastanza dati per decidere?** Meno di 15 trade = sample troppo
   piccolo per trarre conclusioni. Aspetta dati.

---

## Processo di release (quando Phase 6 completa)

1. Full test suite verde: `python -m pytest`
2. Lint clean: `python -m ruff check src/propicks/ tests/`
3. Smoke test CLI: ogni entry point funziona su dati reali
4. Smoke test dashboard: tutte le pages rendono senza error
5. Update `USER_GUIDE.md` con nuovi comandi Phase 6
6. Update `CLAUDE.md` con architettura + trade-off
7. Update `NEXT_STEPS.md` (questo file) con nuovo stato
8. Git commit: `feat(phase6): backtest v2 portfolio-level`
9. Manual review: 1 giornata di uso reale prima di considerare stable
