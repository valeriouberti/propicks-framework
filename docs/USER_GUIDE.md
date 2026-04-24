# Propicks AI Trading Engine — User Guide

**Target**: trader retail Italiano con capitale €5k-50k che vuole un sistema
semi-automatico di screening + risk management + journaling con AI validation
su Pro Picks AI di Investing.com.

**Cosa NON è**: un robo-advisor. Non genera segnali "clicca qui per comprare".
Ti dà score, regime, rischio e ti lascia la decisione — con disciplina
misurabile.

**Roadmap**: questo manuale copre l'uso operativo. Per il *perché* delle
scelte architetturali e le invarianti di business, vedi
[`CLAUDE.md`](../CLAUDE.md).

---

## Indice

1. [Quick start (15 min)](#1-quick-start-15-min)
2. [Setup completo](#2-setup-completo)
3. [Concetti chiave](#3-concetti-chiave)
4. [Workflow operativo settimanale](#4-workflow-operativo-settimanale)
5. [Comandi CLI — reference](#5-comandi-cli--reference)
6. [Dashboard Streamlit](#6-dashboard-streamlit)
7. [Telegram bot](#7-telegram-bot)
8. [Scheduler automation](#8-scheduler-automation)
9. [Troubleshooting](#9-troubleshooting)
10. [Appendice: invarianti & gate Phase 7](#10-appendice-invarianti--gate-phase-7)

---

## 1. Quick start (15 min)

### Prerequisiti

- **Python 3.10+** (verifica: `python --version`)
- **macOS / Linux** (Windows via WSL)
- **Account Anthropic** — per AI validation (opzionale, ma senza perdi molto valore)
- **Account Telegram** — se vuoi notifiche push (opzionale)

### Install

```bash
# Clone il repo (se non l'hai già)
git clone <repo-url> propicks-ai-framework
cd propicks-ai-framework

# Virtual env
python -m venv .venv
source .venv/bin/activate

# Install editable con dev + dashboard + telegram
pip install -e ".[dev,dashboard,telegram]"
```

### Config `.env`

```bash
cp .env.example .env
# Edita con il tuo editor preferito
```

Al minimo imposta `ANTHROPIC_API_KEY` (per AI validation) — il resto è
opzionale. Senza Telegram, skippa `PROPICKS_TELEGRAM_*`.

### Primo scan

```bash
# Scan tecnico AAPL (senza AI)
propicks-scan AAPL

# Scan con validazione Claude (richiede ANTHROPIC_API_KEY)
propicks-scan AAPL --validate

# Scan multi-ticker compatto
propicks-scan AAPL MSFT NVDA --brief
```

Dovresti vedere score 0-100 + classificazione A/B/C/D + breakdown sub-score.

### Primo portfolio

```bash
# Stato corrente (vuoto inizialmente)
propicks-portfolio status

# Calcola size per un trade (senza eseguirlo)
propicks-portfolio size AAPL --entry 180 --stop 170 \
  --score-claude 7 --score-tech 75

# Apre la posizione (simula — persistita nel DB locale)
propicks-portfolio add AAPL --entry 180 --shares 5 --stop 170 \
  --target 200 --strategy TechTitans --catalyst "Q3 earnings beat"
```

### Dashboard

```bash
propicks-dashboard
# Apri http://localhost:8501
```

Naviga tra Portfolio / Scanner / Journal / Watchlist / Contrarian / ecc.

**Pronto.** Il resto del manuale ti guida in workflow avanzato.

---

## 2. Setup completo

### 2.1 Dipendenze opzionali

Il package ha dep di base (yfinance, pandas, anthropic, apscheduler) +
**3 gruppi optional**:

```bash
pip install -e ".[dashboard]"   # Streamlit + plotly (dashboard web)
pip install -e ".[telegram]"    # python-telegram-bot (push notifications)
pip install -e ".[dev]"         # pytest, ruff, mypy (per contribuire)
# Tutti insieme:
pip install -e ".[dev,dashboard,telegram]"
```

### 2.2 Variabili d'ambiente (.env)

| Variabile | Obbligatoria | Descrizione |
|-----------|--------------|-------------|
| `ANTHROPIC_API_KEY` | Solo per `--validate` | Chiave API Claude (console.anthropic.com) |
| `PROPICKS_AI_MODEL` | No (default `claude-opus-4-6`) | Model ID |
| `PROPICKS_AI_WEB_SEARCH` | No (default 1) | Abilita web_search tool di Claude |
| `PROPICKS_AI_MAX_CALLS_PER_DAY` | No (default 50) | Budget cap |
| `PROPICKS_AI_MAX_COST_USD_PER_DAY` | No (default 5.0) | Budget USD |
| `PROPICKS_TELEGRAM_BOT_TOKEN` | Solo per bot | Da @BotFather |
| `PROPICKS_TELEGRAM_CHAT_ID` | Solo per bot | Il tuo chat_id (CSV per multi-device) |

### 2.3 Migrazione da JSON (se proveniente da versione pre-Phase 1)

Se hai già dati in `data/portfolio.json`, `journal.json`, `watchlist.json`:

```bash
propicks-migrate --dry-run   # anteprima
propicks-migrate              # esegue + backup JSON → *.json.bak
```

Idempotente: se il DB SQLite è già popolato, skippa.

### 2.4 Bot Telegram (opzionale)

**Setup BotFather** (5 min):
1. Telegram → cerca `@BotFather` → `/newbot`
2. Nome: "Propicks Personal Bot". Username: finisce con `_bot`.
3. Token ritornato → copia in `.env` come `PROPICKS_TELEGRAM_BOT_TOKEN`
4. Invia `/start` al tuo bot nuovo
5. Apri `@userinfobot` → `/start` → copia il tuo chat_id
6. `.env`: `PROPICKS_TELEGRAM_CHAT_ID=123456789`

**First-setup** (evita spam backlog):
```bash
propicks-bot mute-backlog  # flag pending come delivered (senza inviare)
propicks-bot test           # invia 1 msg di conferma
propicks-bot run            # daemon (tmux / nohup per persistence)
```

### 2.5 Scheduler automation (opzionale)

Due modalità:

**A. Daemon always-on** (richiede tmux/launchd):
```bash
propicks-scheduler run
```

**B. Cron-callable** (raccomandato per desktop-only):
```bash
crontab -e
```
```
# Propicks daily EOD (Mon-Fri Europe/Rome)
30 17 * * 1-5  /path/to/.venv/bin/propicks-scheduler job earnings_calendar
45 17 * * 1-5  /path/to/.venv/bin/propicks-scheduler job warm
0  18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job regime
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job snapshot
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job scan
30 18 * * 1-5  /path/to/.venv/bin/propicks-scheduler job trailing
# Weekly
0  21 * * 6    /path/to/.venv/bin/propicks-scheduler job attribution
0  20 * * 0    /path/to/.venv/bin/propicks-scheduler job cleanup
```

---

## 3. Concetti chiave

### 3.1 Le 3 strategie

| Strategia | Quando compra | Entry point | Size max |
|-----------|---------------|-------------|----------|
| **Momentum** | Forza che accelera (trend uptrend + RSI 50-65 + pullback EMA) | `propicks-scan` | 15% |
| **Contrarian** | Qualità oversold (RSI<30 + stretch EMA50 + above EMA200w) | `propicks-contra` | 8% |
| **ETF Rotation** | Settori in leadership + regime fit | `propicks-rotate` | 20% (ETF) |

Le tre strategie **coesistono** nello stesso portfolio, con cap di 10 posizioni
totali. La contrarian ha bucket cap separato al 20% aggregato.

### 3.2 Le 5 pilastri dello scoring

Ogni **strategia** ha un proprio composite 0-100 derivato da sub-score pesati:

**Momentum** (`domain/scoring.py`):
- Trend (25%) · Momentum RSI (20%) · Volume (15%) · Distance from high (15%) · Volatility (10%) · MA cross (15%)

**Contrarian** (`domain/contrarian_scoring.py`):
- Oversold (40%) · Quality gate (25%) · Market context (20%) · Reversion R/R (15%)

**ETF Rotation** (`domain/etf_scoring.py`):
- RS vs benchmark (40%) · Regime fit (30%) · Abs momentum (20%) · Trend (10%)

### 3.3 Classification

| Score | Class | Azione (momentum) |
|-------|-------|-------------------|
| ≥ 75 | **A** — AZIONE IMMEDIATA | Entry con size piena |
| 60-74 | **B** — WATCHLIST | Entry con size ridotta o wait |
| 45-59 | **C** — NEUTRALE | Skip (watchlist passiva) |
| < 45 | **D** — SKIP | Ignora |

**Gate per entry reale**: score ≥ 60 **E** regime weekly ≥ NEUTRAL.

### 3.4 Regime macro weekly

Classifier 5-bucket su ^GSPC:

| Code | Label | Operativo |
|------|-------|-----------|
| 5 | STRONG_BULL | Risk-on puro (tech + cyclicals) |
| 4 | BULL | Mid-cycle (pullback su qualità) |
| 3 | NEUTRAL | Quality tilt (healthcare + industrials) |
| 2 | BEAR | Defensive (staples + utilities) |
| 1 | STRONG_BEAR | Capital preservation (flat) |

Viene ri-classificato **daily 18:00 CET** dal scheduler (job `record_regime`).

### 3.5 AI validation (Claude)

Flag `--validate` su `propicks-scan` e `propicks-contra`:
- **Input**: analysis dict + regime (gate: no call se BEAR/STRONG_BEAR per momentum, no call se STRONG_BULL/STRONG_BEAR per contrarian)
- **Output**: verdict CONFIRM/CAUTION/REJECT + conviction 0-10 + thesis summary + suggested adjustments
- **Cache**: 24h per-ticker-per-day (in tabella `ai_verdicts`)
- **Budget cap**: 50 call/giorno, $5 USD/giorno (configurable)

### 3.6 Watchlist

**Incubatrice idee** tra scan e entry:
- Auto-popolata dai ticker classe A/B dello scan
- Target entry: A → current price (READY now); B → manuale dal trader
- Alert READY: score ≥ 60 AND distanza target ≤ 2%
- Stale alert: entries > 60gg → suggerito cleanup

### 3.7 Earnings hard gate

- `add_position` rifiuta se earnings entro **5 giorni**
- Override con `--ignore-earnings` (solo per trade intentional)
- Scheduler job daily fa alert pre-earnings per posizioni aperte

### 3.8 Attribution decomposition

Ogni trade chiuso viene decomposto automaticamente in:
```
total_pnl = market (β × SPX) + sector + alpha + timing
```
Report settimanale **sabato 21:00 CET** → `reports/attribution_YYYY-WW.md`.

---

## 4. Workflow operativo settimanale

### 4.1 Sabato sera — Review

```bash
# 1. Report settimanale (già generato dallo scheduler o manuale)
propicks-report attribution

# 2. Journal stats per strategia
propicks-journal stats

# 3. Regime check
propicks-scheduler job regime   # aggiorna se non c'è automation
```

**Leggi il markdown**:
- `reports/attribution_YYYY-WW.md` — trade della settimana + gate Phase 7
- Eventuali heavy losses (> 10%) in sezione Attention

### 4.2 Domenica sera — Planning

```bash
# 1. Rotation settoriale (regime + leadership)
propicks-rotate --region WORLD --top 3 --allocate

# 2. Scan del basket Pro Picks mensile (se hai IDs)
propicks-scan AAPL MSFT NVDA AMZN GOOGL META --strategy TechTitans --brief

# 3. Watchlist cleanup (manuale se alert stale)
propicks-watchlist list --stale
# per ogni entry vecchia: propicks-watchlist remove TICKER
```

### 4.3 Lunedì mattino — Entry decisions

Per ogni ticker in watchlist con flag READY:

```bash
# 1. Fresh scan + AI validation
propicks-scan AAPL --validate

# 2. Check earnings calendar
propicks-calendar check AAPL

# 3. Size calc (advanced Phase 5 opzionale)
propicks-portfolio size AAPL --entry 180 --stop 170 \
  --score-claude 8 --score-tech 80 --advanced

# 4. Se tutto ok, apri:
propicks-portfolio add AAPL --entry 180.25 --shares 5 \
  --stop 170 --target 200 --strategy TechTitans \
  --catalyst "Q3 beat, guidance raise"

# 5. Log nel journal
propicks-journal add AAPL long --entry-price 180.25 \
  --entry-date 2026-01-20 --stop 170 --target 200 \
  --score-claude 8 --score-tech 80 --strategy TechTitans \
  --catalyst "Q3 beat, guidance raise"
```

### 4.4 Martedì-Venerdì — Monitoring

```bash
# Stato + rischio quotidiano
propicks-portfolio status
propicks-portfolio risk

# Trade management (trailing + time stop)
propicks-portfolio manage           # dry-run suggestions
propicks-portfolio manage --apply   # scrive nuovi stop

# Alerts via Telegram bot (se attivo) o via CLI
propicks-scheduler alerts
```

### 4.5 Close di un trade

```bash
propicks-journal close AAPL --exit-price 198.50 \
  --exit-date 2026-02-10 --reason "Target raggiunto"
```

Oppure per stop-loss:
```bash
propicks-journal close AAPL --exit-price 170.10 \
  --exit-date 2026-01-25 --reason "Stop hit"
```

Il journal calcola automaticamente P&L, duration_days, e il report
attribution di sabato lo decompone in alpha/beta/sector/timing.

---

## 5. Comandi CLI — reference

### 5.1 `propicks-scan` — Momentum scanner

```bash
propicks-scan AAPL                    # singolo ticker dettaglio
propicks-scan AAPL MSFT NVDA          # batch
propicks-scan AAPL --strategy TechTitans
propicks-scan AAPL --validate         # + Claude AI verdict
propicks-scan AAPL --force-validate   # bypass gate + cache
propicks-scan AAPL --brief            # tabella compatta
propicks-scan AAPL --json             # output JSON
propicks-scan AAPL --no-watchlist     # no auto-add watchlist
```

### 5.2 `propicks-contra` — Contrarian scanner

```bash
propicks-contra AAPL                  # oversold setup check
propicks-contra AAPL MSFT --brief
propicks-contra AAPL --validate       # Claude flush-vs-break
```

### 5.3 `propicks-rotate` — ETF rotation

```bash
propicks-rotate                         # US SPDR, top 3
propicks-rotate --region WORLD          # Xtrackers MSCI World
propicks-rotate --region EU             # UCITS SPDR
propicks-rotate --top 5 --allocate      # + proposta allocazione
propicks-rotate --validate              # Claude macro validation
```

### 5.4 `propicks-portfolio`

```bash
propicks-portfolio status                   # snapshot live
propicks-portfolio risk                     # rischio + esposizione

# Sizing
propicks-portfolio size AAPL --entry 180 --stop 170 \
  --score-claude 7 --score-tech 75
propicks-portfolio size AAPL --entry 180 --stop 170 \
  --score-claude 7 --score-tech 75 --advanced --strategy-name TechTitans

# Open position
propicks-portfolio add AAPL --entry 180 --shares 5 --stop 170 \
  --target 200 --strategy TechTitans --catalyst "Q3 beat"
# Con override earnings (Phase 8)
propicks-portfolio add AAPL ... --ignore-earnings

# Update / remove / close
propicks-portfolio update AAPL --stop 175 --target 210
propicks-portfolio remove AAPL           # rimborsa entry cost (undo add)
# Per chiusura reale con P&L → usa propicks-journal close

# Trade management
propicks-portfolio trail enable AAPL      # abilita trailing
propicks-portfolio trail disable AAPL
propicks-portfolio manage                 # dry-run suggestions
propicks-portfolio manage --apply         # applica
```

### 5.5 `propicks-journal`

```bash
propicks-journal add AAPL long --entry-price 180 --entry-date 2026-01-20 \
  --stop 170 --target 200 --shares 5 \
  --score-claude 8 --score-tech 80 --strategy TechTitans \
  --catalyst "Q3 beat"

propicks-journal close AAPL --exit-price 198 --exit-date 2026-02-10 \
  --reason "Target raggiunto"

propicks-journal list                    # tutti i trade
propicks-journal list --open             # solo aperti
propicks-journal list --closed --strategy TechTitans

propicks-journal stats                   # aggregate KPIs
propicks-journal stats --strategy Contrarian
```

### 5.6 `propicks-watchlist`

```bash
propicks-watchlist add AAPL --target 180.50 --note "pullback EMA20"
propicks-watchlist update AAPL --target 185
propicks-watchlist remove AAPL

propicks-watchlist list                  # tabella completa
propicks-watchlist list --stale          # > 60gg
propicks-watchlist status                # score live + READY flag
```

### 5.7 `propicks-report`

```bash
propicks-report weekly                   # last 7gg trade summary
propicks-report monthly                  # last 30gg
propicks-report attribution              # Phase 9: decomposition α/β/sector/timing
```

### 5.8 `propicks-backtest`

```bash
propicks-backtest AAPL                   # 5y default
propicks-backtest AAPL MSFT NVDA --period 3y
propicks-backtest AAPL --threshold 70 --json
propicks-backtest AAPL --stop-atr 2 --target-atr 3
```

### 5.9 `propicks-calendar` (Phase 8)

```bash
propicks-calendar earnings                       # portfolio+watchlist 14gg
propicks-calendar earnings --upcoming 30d --refresh
propicks-calendar macro                          # FOMC/CPI/NFP/ECB
propicks-calendar macro --types FOMC,CPI
propicks-calendar check AAPL                     # gate status singolo
```

### 5.10 `propicks-cache` (Phase 2)

```bash
propicks-cache stats                     # righe totali + date range
propicks-cache warm AAPL MSFT NVDA       # prefetch
propicks-cache clear --ticker AAPL       # wipe singolo
propicks-cache clear --all               # wipe totale
propicks-cache clear --stale             # solo rows fuori TTL
```

### 5.11 `propicks-scheduler` (Phase 3)

```bash
propicks-scheduler run                   # daemon APScheduler
propicks-scheduler job snapshot          # one-shot singolo job
propicks-scheduler job snapshot --date 2026-04-23  # backfill
propicks-scheduler alerts                # queue pending
propicks-scheduler alerts --ack 42
propicks-scheduler alerts --ack-all
propicks-scheduler history               # ultimi 20 run
propicks-scheduler history --days 7      # stats aggregate
```

### 5.12 `propicks-bot` (Phase 4)

```bash
propicks-bot test                        # invia 1 msg di conferma
propicks-bot mute-backlog                # first-setup: flag pending come delivered
propicks-bot run                         # daemon
propicks-bot stats                       # counter queue
propicks-bot reset-retries               # recovery errori
```

### 5.13 `propicks-migrate` (one-shot setup)

```bash
propicks-migrate --dry-run               # anteprima
propicks-migrate                         # JSON → SQLite + backup *.json.bak
```

---

## 6. Dashboard Streamlit

Lancio: `propicks-dashboard` → http://localhost:8501

**Pagine disponibili** (sidebar):

| Page | CLI equivalente | Uso |
|------|-----------------|-----|
| **Home** | `propicks-portfolio status` | Overview: total, cash %, regime badge, alert recenti |
| **Scanner** | `propicks-scan` | Analisi momentum con score breakdown + AI validation on-demand |
| **ETF Rotation** | `propicks-rotate` | Ranking universo + allocation proposta |
| **Portfolio** | `propicks-portfolio` | CRUD positions + risk tab + trade management |
| **Journal** | `propicks-journal` | Add/close/list/stats con filtri |
| **Reports** | `propicks-report` | Viewer markdown archive |
| **Backtest** | `propicks-backtest` | UI walk-forward |
| **Watchlist** | `propicks-watchlist` | CRUD + live score + READY flag |
| **Contrarian** | `propicks-contra` | Scanner mean reversion |

**Sidebar invariants** (live): posizioni aperte, cash %, rischio settimanale,
regole. Cambia contenuto in base alla page (es. contrarian page mostra regole bucket).

### Tips dashboard

- **Cache OHLCV** preloadata → scan rapido anche su batch (~5-10× vs cold).
- **Form pattern**: ogni page ha un `st.form` con bottone submit → nessun rerun accidentale.
- **Expander "Prompt Perplexity"** in Scanner → copia-incolla prompt per cross-check indipendente da Claude.

---

## 7. Telegram bot

Setup: §2.4. Una volta attivo:

### Comandi bot (dalla chat Telegram)

| Comando | Output |
|---------|--------|
| `/start` | Messaggio di benvenuto |
| `/help` | Lista comandi |
| `/status` | Total value + cash % + posizioni + P&L unrealized |
| `/portfolio` | Dettaglio per ticker con P&L% live |
| `/alerts` | Alert pending con ID per `/ack` |
| `/ack 42` | Acknowledge alert 42 |
| `/ackall` | Ack tutti pending |
| `/history` | Ultimi 10 job scheduler |
| `/cache` | Stats cache OHLCV |
| `/regime` | Regime macro corrente + commentary operativo |
| `/report` | Attribution summary ultimi 30gg (inline) |
| `/calendar` | Earnings + macro events upcoming 14gg |

### Alert auto-delivery

Dallo scheduler, alert generati vengono inviati via Telegram ogni 60s.
Tipi di alert:

- **`watchlist_ready`** (info): ticker in watchlist con score + distanza target ≤ 2%
- **`regime_change`** (critical/warning): classifier ^GSPC cambia bucket
- **`trailing_stop_update`** (info): nuovo stop suggerito per posizione trailing
- **`stale_position`** (warning): trade flat da molti giorni (time-stop)
- **`stale_watchlist`** (info): entries > 60gg in watchlist
- **`contra_near_cap`** (warning): bucket contrarian > 75% del cap 20%
- **`earnings_upcoming`** (critical se ≤2gg, warning altrimenti): ticker con earnings entro 5gg
- **`report_ready`** (info): weekly attribution report generato

---

## 8. Scheduler automation

### Il daemon

```bash
propicks-scheduler run
```

Tiene 8 job schedulati (Europe/Rome):

| Orario | Job | Scopo |
|--------|-----|-------|
| Mon-Fri 17:30 | `check_earnings_calendar` | Alert pre-earnings entro 5gg |
| Mon-Fri 17:45 | `warm_cache` | Prefetch OHLCV pre-EOD |
| Mon-Fri 18:00 | `record_regime` | Classify ^GSPC weekly + alert su change |
| Mon-Fri 18:30 | `snapshot_portfolio` | Equity curve + exposure breakdown |
| Mon-Fri 18:30 | `scan_watchlist` | Score live + READY detection |
| Mon-Fri 18:30 | `trailing_stop_check` | Suggest trailing stop updates |
| Sat 21:00 | `weekly_attribution_report` | Report α/β/sector/timing |
| Sun 20:00 | `cleanup_stale_watchlist` | Flag entries > 60gg |

### Cron alternativo

Se non vuoi un daemon always-on, usa cron (vedi §2.5).

### Audit

Ogni esecuzione è loggata in tabella `scheduler_runs`:

```bash
propicks-scheduler history                 # ultimi 20 run
propicks-scheduler history --days 7        # stats aggregate per job (affidabilità)
```

Se un job fallisce 3 volte consecutive → controllo manuale richiesto.

---

## 9. Troubleshooting

### AI validation fallisce con "budget exceeded"

```bash
# Check quota giornaliera
sqlite3 data/propicks.db "SELECT * FROM daily_budget;"

# Reset manuale (emergenza, es. post-test)
sqlite3 data/propicks.db "DELETE FROM daily_budget WHERE date = date('now');"
```

O aumenta cap via env:
```bash
PROPICKS_AI_MAX_CALLS_PER_DAY=100 propicks-scan AAPL --validate
```

### yfinance rate-limited

```bash
propicks-cache stats   # vedi quanti dati sono cached
propicks-cache warm AAPL MSFT NVDA  # prefetch tickers importanti
```

Il cache TTL 8h sui daily → se continui a vedere rate-limit, aspetta 1h
che il throttle Yahoo si resetti.

### Scheduler daemon muore

- Controlla `scheduler_runs` per l'ultimo status error
- Controlla log `/tmp/propicks-*.log` se usi nohup
- Verifica che non ci sia un altro daemon già attivo (un solo daemon per DB)
- Se duplicate daemon: `pkill -f propicks-scheduler` + restart

### Telegram bot non riceve comandi

1. Test connettività: `propicks-bot test`
2. Verifica chat_id: `@userinfobot` → `/start`
3. Verifica env: `echo $PROPICKS_TELEGRAM_CHAT_ID`
4. Check log: `propicks-bot stats` + `propicks-scheduler alerts --stats`

### DB corrotto

SQLite ha PRAGMA integrity_check:
```bash
sqlite3 data/propicks.db "PRAGMA integrity_check;"
```
Se "ok" → problema altrove. Se errori → backup + ri-migra dai `.json.bak`.

### Test falliscono localmente

```bash
# Esegui solo unit
python -m pytest tests/unit

# Con output dettagliato per un test specifico
python -m pytest tests/unit/test_scoring.py::test_nome -v
```

I test usano `tmp_path` SQLite ephemeral + mock yfinance → zero rete. Se
fallisce, è bug locale (imports, schema migration).

### Earnings gate blocca trade che vuoi aprire

Due opzioni:
1. Aspetta che l'evento passi: `propicks-calendar check AAPL`
2. Bypass intentional (solo per trade contrarian post-earnings):
   ```bash
   propicks-portfolio add AAPL --ignore-earnings ...
   propicks-journal add AAPL ... --catalyst "post-earnings flush play"
   ```

### Regime weekly non classifica (dati insufficienti)

Alcuni ticker esteri non hanno ≥ 60 settimane di storia. Il regime viene
skippato silenziosamente → classification resta "N/D" nel display. Non
blocca lo scan, solo l'AI validation per quel ticker.

---

## 10. Appendice: invarianti & gate Phase 7

### Invarianti di business

Hardcoded, **NON** modificabili senza evidenza empirica:

- **Max posizioni aperte**: 10 (incluso momentum + contrarian + ETF)
- **Max size per posizione**: 15% (momentum) / 20% (ETF) / 8% (contrarian)
- **Max esposizione contrarian aggregata**: 20%
- **Max contrarian posizioni simultanee**: 3
- **Min cash reserve**: 20%
- **Max loss per trade**: 8% (momentum) / 12% (contrarian) / 5% (ETF via stop hard)
- **Max loss settimanale**: 5% del capitale → blocco trading
- **Max loss mensile**: 15% del capitale → blocco + revisione
- **Earnings hard gate**: 5 giorni (override con `--ignore-earnings`)
- **Score minimo entry**: Claude ≥ 6, tecnico ≥ 60
- **Regime minimo validazione momentum**: NEUTRAL (code ≥ 3)

### Gate Phase 7 — promuovere nuove strategie

Prima di aggiungere una strategia **nuova** (pair trading, event-driven,
covered calls, etc.), le **3 strategie esistenti** devono dimostrare edge:

| Criterio | Soglia |
|----------|--------|
| Trade chiusi per strategia | ≥ 15 |
| Profit factor | ≥ 1.3 |
| Sharpe (trade-level) | ≥ 0.8 |
| Win rate (momentum) | ≥ 50% |
| Win rate (contrarian) | ≥ 55% |
| Max drawdown | ≥ -15% |
| Correlation con SPX | ≤ 0.70 |

Il **report attribution settimanale** (`propicks-report attribution`)
mostra gate status esplicito per ogni strategia.

Se dopo 6 mesi una strategia non raggiunge le soglie → **si ritira**,
invece di aggiungere una strategia nuova per compensare. Disciplina di
capitale > quantità di strategie.

### Filosofia operativa

1. **Il sistema non decide, misura**. Tu firmi i trade.
2. **Ogni alert è informativa, non instructiva**. READY ≠ "compra". Regime change ≠ "vendi tutto".
3. **La disciplina è nelle regole, non nel motivation**. Le invarianti sono in config.py — niente eccezioni "una volta sola".
4. **Il journal è la fonte di verità**. Ogni trade tracciato con score, catalyst, strategy. Non editare a mano — usa `propicks-journal`.
5. **I modelli sono temporanei, i dati sono permanenti**. Lo scoring può evolvere; le entries nel journal sono immutabili per permettere attribution corretta.

---

## Riferimenti

- **Source code**: layout `src/propicks/` (editable install)
- **Architettura dettagliata**: [`CLAUDE.md`](../CLAUDE.md)
- **Workflow Pro Picks**: [`Trading_System_Playbook.md`](./Trading_System_Playbook.md)
- **Cadenza settimanale**: [`Weekly_Operating_Framework.md`](./Weekly_Operating_Framework.md)

---

**Versione guida**: Phase 1-5 + 8-9 complete. Phase 6 (backtest v2 portfolio-level) in roadmap.

Ogni bug o ambiguità nella guida → issue su GitHub.
