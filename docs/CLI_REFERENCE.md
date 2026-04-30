# CLI Reference

Reference esaustivo dei 14 entry points CLI definiti in `pyproject.toml`. Tutti
funzionano da qualsiasi cwd dopo `pip install -e .` perché i path di `data/` e
`reports/` sono ancorati alla root del progetto.

> **Linee guida**:
> - Argomenti opzionali sono in `[parentesi quadre]`. Argomenti `<obbligatori>`.
> - Default sono in `src/propicks/config.py` — qui citati senza duplicare.
> - JSON output (`--json`) è sempre supportato dove sensato per scripting.

---

## Index

| Comando | Sezione |
|---------|---------|
| `propicks-momentum` | [Momentum momentum](#propicks-momentum) |
| `propicks-contra` | [Contrarian mean reversion](#propicks-contra) |
| `propicks-rotate` | [ETF sector rotation](#propicks-rotate) |
| `propicks-portfolio` | [Portfolio & sizing](#propicks-portfolio) |
| `propicks-journal` | [Journal trades](#propicks-journal) |
| `propicks-report` | [Markdown reports](#propicks-report) |
| `propicks-backtest` | [Backtest engine](#propicks-backtest) |
| `propicks-calibrate` | [Threshold calibration DSR + CPCV](#propicks-calibrate) |
| `propicks-watchlist` | [Watchlist](#propicks-watchlist) |
| `propicks-calendar` | [Earnings & macro](#propicks-calendar) |
| `propicks-cache` | [Market data cache](#propicks-cache) |
| `propicks-scheduler` | [APScheduler daemon](#propicks-scheduler) |
| `propicks-bot` | [Telegram bot](#propicks-bot) |
| `propicks-dashboard` | [Streamlit launcher](#propicks-dashboard) |
| `propicks-migrate` | [JSON → SQLite one-shot](#propicks-migrate) |

---

## propicks-momentum

Momentum momentum/quality stock — replica l'engine `domain/scoring.py`.
Vedi [MOMENTUM_STRATEGY](MOMENTUM_STRATEGY.md).

```bash
propicks-momentum <TICKER> [TICKER ...] [opzioni]
```

| Opzione | Default | Effetto |
|---------|---------|---------|
| `--strategy NAME` | `None` | Tag strategia Pro Picks (TechTitans, DominaDow, ...) |
| `--validate` | off | Valida tesi via Claude (gate score≥60 + regime≥NEUTRAL) |
| `--force-validate` | off | Come `--validate` ma bypassa gate + cache |
| `--json` | off | Output JSON |
| `--brief` | off | Solo tabella riassuntiva |
| `--no-watchlist` | off | Disabilita auto-add classe A/B alla watchlist |

**Esempi**:

```bash
propicks-momentum AAPL                           # singolo, output dettagliato
propicks-momentum AAPL MSFT NVDA --brief         # tabella batch
propicks-momentum AAPL --strategy TechTitans --validate
propicks-momentum AAPL --json | jq '.[0].score_composite'
```

**Output**: 6 sub-score (trend/momentum/volume/dist-high/volatility/MA-cross) + composite + classification A/B/C/D + regime weekly + RS vs settore (US-only) + AI verdict opzionale + blocco TradingView Pine inputs.

---

## propicks-contra

Contrarian mean reversion — replica `domain/contrarian_scoring.py`.
Vedi [CONTRARIAN_STRATEGY](CONTRARIAN_STRATEGY.md).

```bash
propicks-contra <TICKER> [TICKER ...] [opzioni]
propicks-contra --discover-{sp500|ftsemib|stoxx600} [opzioni]
```

| Opzione | Default | Effetto |
|---------|---------|---------|
| `--validate` | off | Valida tesi flush-vs-break via Claude |
| `--json` / `--brief` | off | Format output |
| `--no-watchlist` | off | Disabilita auto-add |
| `--discover-sp500` | — | Pipeline 3-stage su S&P 500 (~500 nomi) |
| `--discover-ftsemib` | — | Su FTSE MIB (40 large-cap IT) |
| `--discover-stoxx600` | — | Su STOXX 600 (~600 nomi EU) |
| `--top N` | 20 | Top-N risultati discovery |
| `--min-score N` | 60 | Score minimo per inclusione discovery |

**Esempi**:

```bash
propicks-contra ENI.MI --validate
propicks-contra --discover-sp500 --top 10 --min-score 70
propicks-contra --discover-ftsemib --json > contra_it.json
```

**Output**: 4 sub-score (oversold/quality/market_context/reversion) + composite + class A/B/C/D + stop (`recent_low − 1×ATR`) + target (EMA50) + R/R + verdict AI.

---

## propicks-rotate

Sector ETF rotation — replica `domain/etf_scoring.py`.
Vedi [ETF_ROTATION_STRATEGY](ETF_ROTATION_STRATEGY.md).

```bash
propicks-rotate [opzioni]
```

| Opzione | Default | Effetto |
|---------|---------|---------|
| `--region {US\|EU\|WORLD\|ALL}` | `US` | Universo: SPDR XL*, UCITS ZPD*.DE, Xtrackers XDW* + IQQ6.DE proxy RE, o ALL |
| `--top N` | 3 | Numero settori in proposta allocazione |
| `--allocate` | off | Stampa proposta allocation con cap 20%/60% |
| `--validate` | off | Valida rotation macro via Claude |
| `--force-validate` | off | Bypass cache + skip in STRONG_BEAR |
| `--json` | off | Output JSON |
| `--no-top-detail` | off | Salta dettaglio top-pick |

**Esempi**:

```bash
propicks-rotate                              # US, top 3
propicks-rotate --region WORLD --top 5
propicks-rotate --allocate --validate
```

---

## propicks-portfolio

Sizing, risk, esposizione, trade management.

```bash
propicks-portfolio <SUBCOMMAND> [opzioni]
```

| Subcommand | Scope |
|------------|-------|
| `status` | Stampa portfolio corrente + P&L unrealized |
| `risk` | Esposizione sector/beta/correlations + utilizzo capitale |
| `size <TICKER> --entry X --stop Y` | Calcola size base (% capitale) |
| `size ... --advanced --strategy-name N --vol-target 0.15` | Sizing Phase 5 (Kelly+vol+corr) |
| `size ... --contrarian` | Sizing contrarian (cap 8%, bucket 20%) |
| `add <TICKER> --entry X --shares N --stop Y --target Z --strategy NAME` | Aggiunge posizione |
| `add ... --ignore-earnings` | Bypassa earnings hard gate (5gg) |
| `update <TICKER> --stop X --target Y` | Modifica livelli posizione |
| `remove <TICKER>` | Chiude posizione manualmente |
| `trail enable\|disable <TICKER>` | Attiva/disattiva trailing stop (contrarian: solo disable) |
| `manage [--apply]` | Trailing + time stop + target hit (`--apply` = scrivi al DB) |
| `manage --atr-mult 2.5 --time-stop 20 --apply` | Override default trailing |

**Esempi**:

```bash
propicks-portfolio status
propicks-portfolio risk
propicks-portfolio size AAPL --entry 185 --stop 175 --score-claude 8 --score-tech 75
propicks-portfolio size AAPL --entry 185 --stop 175 --advanced \
    --strategy-name TechTitans --vol-target 0.15
propicks-portfolio add AAPL --entry 185 --shares 10 --stop 175 --target 220 \
    --strategy TechTitans --catalyst "earnings beat + AI tailwind"
propicks-portfolio manage           # dry-run
propicks-portfolio manage --apply   # scrivi
```

---

## propicks-journal

Journal append-only — i trade chiusi non vengono cancellati, viene aggiunto
`exit_*`.

```bash
propicks-journal <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `add <TICKER> long\|short --entry-price X --entry-date YYYY-MM-DD --stop Y --target Z --score-claude 8 --score-tech 75 --strategy NAME --catalyst "..."` | Aggiunge entry |
| `close <TICKER> --exit-price X --exit-date YYYY-MM-DD --reason "..."` | Chiude trade (entry resta) |
| `list [--open\|--closed] [--strategy NAME]` | Elenca |
| `stats [--strategy NAME]` | Hit rate, R/R medio, P&L per strategia |

**Esempi**:

```bash
propicks-journal add AAPL long --entry-price 185 --entry-date 2026-04-15 \
    --stop 175 --target 220 --score-claude 8 --score-tech 78 \
    --strategy TechTitans --catalyst "AI iPhone refresh + Services growth"
propicks-journal close AAPL --exit-price 218 --exit-date 2026-06-20 --reason "target hit"
propicks-journal stats --strategy TechTitans
```

---

## propicks-report

Genera markdown in `reports/`. Vedi [PNL_ATTRIBUTION](PNL_ATTRIBUTION.md).

```bash
propicks-report <KIND>
```

| Kind | Output |
|------|--------|
| `weekly` | Report settimanale: P&L, posizioni, classifiche A/B, alert pending |
| `monthly` | Report mensile + attribution + Phase 7 gate evaluation |
| `attribution` | P&L decomposition α/β/sector/timing |

---

## propicks-backtest

Backtester walk-forward + portfolio + Monte Carlo. Vedi [BACKTEST_GUIDE](BACKTEST_GUIDE.md).

```bash
propicks-backtest <TICKER> [TICKER ...] [opzioni]
```

| Opzione | Default | Effetto |
|---------|---------|---------|
| `--period {1y\|3y\|5y\|10y}` | `3y` | Periodo lookback |
| `--threshold N` | 60 | Score minimo per entry |
| `--portfolio` | off | Switch a portfolio engine (multi-ticker) |
| `--tc-bps N` | 0 | Transaction cost basis points |
| `--monte-carlo N` | 0 | N simulazioni Monte Carlo (>0 = on) |
| `--oos-split 0.70` | off | Walk-forward train/test split |
| `--historical-membership INDEX` | off | **Fase A.1**: filter ticker eligible at-time-T via membership history (es. `sp500`). Risolve survivorship bias. Solo modalità `--portfolio` |
| `--cross-sectional` | off | **Fase B.1**: interpreta `--threshold` come **percentile rank** (0-100) cross-sectional invece di score assoluto. Es. `--threshold 80 --cross-sectional` = entry top quintile (P80+) |

**Esempi**:

```bash
propicks-backtest AAPL --period 5y --threshold 65
propicks-backtest AAPL MSFT NVDA --portfolio --tc-bps 10 --monte-carlo 500
propicks-backtest AAPL --portfolio --oos-split 0.70 --monte-carlo 1000
# Fase A.1 — survivorship-correct (richiede import sp500 history una volta)
propicks-backtest AAPL MSFT NVDA --portfolio --historical-membership sp500
# Fase B.1 — cross-sectional rank top quintile
propicks-backtest AAPL MSFT NVDA --portfolio --cross-sectional --threshold 80
# Combinato A.1 + B.1
propicks-backtest --portfolio --historical-membership sp500 \
    --cross-sectional --threshold 80 AAPL MSFT NVDA GOOGL AMZN
```

**Setup membership** (una tantum):

```bash
python scripts/import_sp500_history.py
# → 343 monthly snapshot 1996-2026 (170k row in index_membership_history)
```

---

## propicks-calibrate

**Fase A.2** SIGNAL_ROADMAP. Threshold sweep + Probabilistic Sharpe Ratio (PSR)
+ Deflated Sharpe Ratio (DSR, Bailey-Lopez 2014) + Combinatorial Purged CV
(Lopez de Prado 2018). Output: tabella per threshold + recommendation
rule-based. Vedi [THRESHOLD_CALIBRATION](THRESHOLD_CALIBRATION.md).

```bash
propicks-calibrate <TICKER ...> [opzioni]
propicks-calibrate --discover-sp500 --top N [opzioni]
```

| Opzione | Default | Effetto |
|---------|---------|---------|
| `--discover-sp500` | off | Universe S&P 500 corrente |
| `--top N` | 20 | Limita universe a top N |
| `--thresholds SPEC` | `40:80:5` | Range (`start:end:step`) o lista (`60,65,70`) |
| `--period {1y..10y}` | `5y` | yfinance fetch period |
| `--strategy {momentum}` | `momentum` | (contrarian/etf in roadmap) |
| `--use-cpcv` | off | Combinatorial Purged CV (~10x slower) |
| `--cpcv-groups N` | 6 | CPCV partitions |
| `--cpcv-test-groups N` | 2 | held-out per fold (C(6,2)=15 path) |
| `--cpcv-embargo N` | 5 | embargo days |
| `--historical-membership INDEX` | off | Fase A.1 survivorship filter |
| `--start YYYY-MM-DD` | none | filter date inizio |
| `--end YYYY-MM-DD` | none | filter date fine |
| `--min-trades N` | 30 | Minimo trade per recommendation |
| `--target-dsr 0-1` | 0.95 | DSR threshold per recommendation tier 1 |

**Esempi**:

```bash
# Sweep singolo
propicks-calibrate AAPL MSFT NVDA --thresholds "60:80:5" --period 5y

# Universe S&P top 50 + CPCV + survivorship
propicks-calibrate --discover-sp500 --top 50 \
    --thresholds "55:80:5" --use-cpcv \
    --historical-membership sp500 --period 5y
```

**Output**: tabella con N trades / Sharpe ann / Win% / Tot ret% / PSR / DSR
per threshold. Recommendation marcata con ★. Decision rule:

- Tier 1: max DSR tra threshold con DSR ≥ 0.95 e n_trades ≥ min_trades
- Tier 2: max DSR sopra min_trades anche se DSR < target
- Tier 3: max Sharpe (caveat: trade insufficienti)

**Note**: il CLI è informativo — NON modifica `config.MIN_SCORE_TECH`
automaticamente. Decisione promotion default = manuale post-validation
multi-period.

---

## propicks-watchlist

Incubatrice idee. Vedi [WATCHLIST_AND_TRADE_MGMT](WATCHLIST_AND_TRADE_MGMT.md).

```bash
propicks-watchlist <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `add <TICKER> --target X --note "..."` | Aggiunge entry |
| `update <TICKER> --target X --note "..."` | Modifica |
| `remove <TICKER>` | Rimuove |
| `list [--stale]` | Elenca (`--stale` = entry vecchie >30gg senza azione) |
| `status` | Distance-from-target per ogni ticker (READY se ≤1%) |

**Auto-fill**: `propicks-momentum` aggiunge automaticamente classe A/B (override con `--no-watchlist`).

---

## propicks-calendar

Earnings hard gate + macro events. Vedi [CALENDAR](CALENDAR.md).

```bash
propicks-calendar <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `earnings [--upcoming 30d] [--refresh]` | Lista earnings dei ticker in portfolio + watchlist |
| `macro [--types FOMC,CPI,NFP,ECB]` | Lista macro events 2026 hardcoded |
| `check <TICKER> [--refresh]` | Earnings date + days_to_earnings + gate status |

---

## propicks-cache

Cache OHLCV (Phase 2). Vedi [STORAGE](STORAGE.md).

```bash
propicks-cache <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `stats` | Hit rate + size + entry count per intervallo |
| `warm <TICKER> [TICKER ...] [--force]` | Pre-fetch (per scheduler EOD) |
| `clear [--ticker T] [--all] [--stale] [--interval daily\|weekly\|meta]` | Pulisci cache |

**Esempi**:

```bash
propicks-cache stats
propicks-cache warm AAPL MSFT NVDA --force
propicks-cache clear --stale
```

---

## propicks-scheduler

Daemon APScheduler EOD jobs (timezone Europe/Rome). Vedi [SCHEDULER](SCHEDULER.md).

```bash
propicks-scheduler <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `run` | Daemon bloccante (Ctrl-C per fermare) |
| `job {warm\|regime\|snapshot\|scan\|trailing\|cleanup\|attribution}` | Esegue un job una tantum |
| `alerts [--ack N\|--ack-all\|--stats]` | Gestisce coda alert |
| `history [--days 7]` | Storico esecuzioni job |

**Esempi**:

```bash
propicks-scheduler run                   # daemon
propicks-scheduler job snapshot          # one-shot
propicks-scheduler alerts --stats
propicks-scheduler alerts --ack 42
```

---

## propicks-bot

Telegram bot (extras `[telegram]`). Vedi [TELEGRAM_BOT](TELEGRAM_BOT.md).

```bash
propicks-bot <SUBCOMMAND>
```

| Subcommand | Scope |
|------------|-------|
| `test` | Smoke test connessione + send messaggio test |
| `run` | Daemon bloccante con polling + push alert |
| `stats` | Conteggi alert delivered/failed/muted |
| `mute-backlog` | Silenzia alert pending al primo run dopo downtime |
| `reset-retries [--alert-id N]` | Reset counter retry (single o all) |

**Comandi in chat** (via `/propicks-bot run`):

| Comando | Effetto |
|---------|---------|
| `/status` | Portfolio status |
| `/portfolio` | Posizioni dettaglio |
| `/alerts` | Alert pending |
| `/ack N` | Conferma alert N |
| `/history` | Storico ultimo run scheduler |
| `/cache` | Stato cache |
| `/regime` | Regime weekly corrente |
| `/report` | Genera weekly report on-demand |
| `/calendar` | Earnings + macro upcoming |
| `/help` | Lista comandi |

---

## propicks-dashboard

Launcher Streamlit (extras `[dashboard]`).

```bash
propicks-dashboard
```

Apre `http://localhost:8501`. Per il walkthrough delle 11 page vedi
[DASHBOARD_GUIDE](DASHBOARD_GUIDE.md).

---

## propicks-migrate

Migrazione one-shot JSON → SQLite. **Già eseguita al completamento di Phase 1**;
mantenuta per riproducibilità.

```bash
propicks-migrate [--dry-run]
```

Idempotente: rilanciarla è no-op se già migrato.

---

## Convenzioni globali

- **Tutti i comandi** rispettano `data/propicks.db` come source of truth.
- **JSON output** è sempre `default=str` (date/datetime serializzate come ISO).
- **Exit codes**: `0` success, `1` errore generico (ticker non trovato, dati assenti). Specifici nei singoli moduli quando rilevanti.
- **`stderr`** per warning/errori non fatali; `stdout` per il payload utile (così `| jq` o `> file.json` funzionano).
- **Cwd-independent**: dopo `pip install -e .` puoi lanciare da qualunque directory.
