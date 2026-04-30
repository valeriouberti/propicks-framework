# Strategy Decay Monitor — Fase D.4 SIGNAL_ROADMAP

> Framework early-warning per detection di edge decay in produzione.
> Combina rolling Sharpe + CUSUM + SPRT.

Documento generato: **2026-04-29**.
Reference: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) §7 Fase D.4.

---

## 1. Razionale

Strategia in produzione può degradare per:

- **Edge arbitraggiato** (overcrowding momentum 2018-19, value 2017-19)
- **Regime change** (rate hikes 2022 hurt growth strategies)
- **Liquidity shift** / market microstructure change
- **Strategy-specific issue** (parameter overfit revealed OOS)

Drawdown detect tardivo → loss material. Decay monitor genera **early
warning** prima di drawdown sostanziale, abilitando pause / review.

## 2. Architettura

3 detector composabili in `domain/decay_monitor.py`:

| Detector | Razionale | Use case |
|----------|-----------|----------|
| **Rolling Sharpe** | confronto Sharpe ultimi N trade vs expected | quick visual check |
| **CUSUM** (Page 1954) | cumulative sum deviation da mean | gradual + abrupt drift |
| **SPRT** (Wald 1945) | log-likelihood ratio H0=dead vs H1=alive | binary stopping decision |

Composite `decay_alert_summary()` combina tutti 3 con semplice rule:
- ANY of CUSUM alarm OR SPRT EDGE_DEAD → ``ALERT_DECAY``
- Rolling < 50% expected → ``WARNING``
- SPRT EDGE_ALIVE → ``ALIVE``
- Else → ``MONITOR``

## 3. Smoke test su scenari sintetici

Run: `scripts/test_decay_monitor.py`

| Scenario | Composite decision | CUSUM @ | SPRT |
|----------|--------------------|---------|------|
| **ALIVE**: stable +0.5%/trade | `ALIVE` | — | EDGE_ALIVE@156 |
| **DEAD**: zero mean throughout | `ALERT_DECAY` | @65 | CONTINUE |
| **GRADUAL_DECAY**: 0.5% → 0.1% over 200 | `MONITOR` ⚠ | — | CONTINUE |
| **ABRUPT_DECAY**: 0.5% then −0.3% | `ALERT_DECAY` | @150 | CONTINUE |
| **REGIME_SHIFT**: 0.5% → 0% mid | `ALERT_DECAY` | @178 | EDGE_ALIVE@26 |

### Findings

- **Edge alive**: SPRT decision @ trade 156 = EDGE_ALIVE confermato early.
  Coerente con +0.5% mean su σ 2% (Sharpe per-trade 0.25).
- **Dead from start**: CUSUM alarm @ 65 trade. Detection tempestiva.
- **Abrupt decay** (post-trade 100): CUSUM alarm @ 150 = 50 trade dopo
  cambio. Latency tipica.
- **Gradual decay**: CUSUM **NON triggera** con sensitivity 0.5σ default.
  Limitazione known: CUSUM ottimizzato per cambio abrupt > 1σ. Per gradual
  drift serve sensitivity ridotta (0.2σ) ma falso positive aumenta.
- **Regime shift mid**: CUSUM @ 178 (78 trade dopo shift). SPRT bloccata
  su EDGE_ALIVE early — **edge case importante**: SPRT decision early
  può essere stale rispetto a regime corrente.

## 4. Caveat

### Critici

1. **CUSUM gradual decay miss**: sensitivity default 0.5σ ottimizzata per
   abrupt change. Per drift gradual servirebbe tuning. Trade-off
   sensibility vs falso positive.

2. **SPRT decision sticky**: una volta EDGE_ALIVE/DEAD, SPRT non
   "ri-decide" su regime change. Per detection regime shift serve reset
   periodico (es. ogni 100 trade riavvia SPRT) o sliding window.

3. **Sample insufficient real DB**: 4 closed trades insufficient per
   testing reale. Decay monitor è framework forward-looking — utility
   reale dopo accumulo 50+ trade in produzione.

### Minori

4. **Sigma-stationarity assumption**: CUSUM/SPRT assume σ stabile.
   Vol regime change (low vol → high vol) può triggerare false alarm.
   Considerare normalize by realized vol.

5. **No multi-strategy support**: framework opera su singola sequenza
   returns. Per tracking decay multi-strategy servono N detector paralleli +
   correlation tra alarm.

## 5. Public API

```python
from propicks.domain.decay_monitor import (
    rolling_sharpe,           # (returns, window) → list[float]
    cusum_decay_detector,     # → dict con alarm_index
    sprt_test,                # → dict con decision + log_lr_series
    decay_alert_summary,      # → dict composite con decision finale
)
```

Pure functions. No I/O. Input list/numpy.

## 6. Integration produzione (future)

### D.4 estensione (next iteration)

- Cron job daily: legge closed trades ultime 30/90 giorni da DB,
  esegue `decay_alert_summary`, push alert su Telegram se ALERT_DECAY
- Thresholds tuneable via config: `expected_sharpe_per_trade` per strategia
  (momentum / contrarian / etf), CUSUM sensitivity, SPRT alpha/beta
- Dashboard panel: rolling Sharpe trend + CUSUM series live

### Audit trail

Decisione decay deve essere log immutable:
- `decay_runs` table (date, decision, n_trades, evidence)
- Manual override path (utente conferma ignore false positive)

## 7. Conclusione

✓ Framework decay completo + testato su scenari sintetici

✓ Detection abrupt + regime shift funzionante (CUSUM, SPRT)

⚠ Gradual decay sub-optimal con default sensitivity. Tuning empirical
pendente

⚠ Sample reale DB insufficient (4 trades) — utility reale forward-looking

**Acceptance gate D.4**: **pass operativo**. Critical per live deploy
dopo accumulo trade history. Pre-deploy obbligatorio configurare:

1. `expected_sharpe_per_trade` per strategia
2. Cron daily monitor + Telegram push
3. Manual review process per ALERT_DECAY events
