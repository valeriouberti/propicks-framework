# Propicks AI Trading Engine — Wiki & Manuale

> **Manuale completo del trading engine**. Sistema Python semi-automatico per
> screening tecnico, scoring AI, risk management, journaling, backtesting e
> integrazione TradingView, costruito sopra Pro Picks AI di Investing.com.
>
> Per la **filosofia di design** e gli invarianti di business vedi
> [`CLAUDE.md`](CLAUDE.md). Questa wiki è il manuale operativo.

---

## Come orientarsi

Il progetto ha **tre layer di interazione** che girano sopra lo stesso motore
Python:

| Layer | Scope | Doc |
|-------|-------|-----|
| **CLI** (`propicks-*`) | Comandi terminale per workflow batch e automation | [CLI_REFERENCE](docs/CLI_REFERENCE.md) |
| **Dashboard** (Streamlit) | UI interattiva multi-page parallela alla CLI | [DASHBOARD_GUIDE](docs/DASHBOARD_GUIDE.md) |
| **Pine** (TradingView) | Real-time scoring + alert on-chart, mirror del Python | [PINE_SCRIPTS_REFERENCE](docs/PINE_SCRIPTS_REFERENCE.md) |

CLI e Dashboard sono **funzionalmente equivalenti** — chiamano le stesse
funzioni del motore Python. La Dashboard è più pratica per esplorazione
visuale; la CLI è la fonte di verità per scripting e automation EOD. I Pine
sono il layer real-time che yfinance (EOD) non copre.

---

## Indice

### Setup & Riferimenti

| Doc | Scope |
|-----|-------|
| [INSTALLATION_AND_SETUP](docs/INSTALLATION_AND_SETUP.md) | Setup completo, .env, Docker, smoke test |
| [USER_GUIDE](docs/USER_GUIDE.md) | Quick start (15 min) per il trader |
| [CLI_REFERENCE](docs/CLI_REFERENCE.md) | Reference esaustivo dei 14 entry points CLI |
| [DASHBOARD_GUIDE](docs/DASHBOARD_GUIDE.md) | Walkthrough delle 11 page Streamlit |
| [PINE_SCRIPTS_REFERENCE](docs/PINE_SCRIPTS_REFERENCE.md) | 4 Pine scripts (weekly regime, daily signal, ETF, contrarian) |
| [SECURITY_AND_SECRETS](docs/SECURITY_AND_SECRETS.md) | API key, .env, segreti, rotation |
| [FAQ_AND_TROUBLESHOOTING](docs/FAQ_AND_TROUBLESHOOTING.md) | Errori comuni, regime offtrack, cache stale, bot down |

### Strategie

| Doc | Comando |
|-----|---------|
| [MOMENTUM_STRATEGY](docs/MOMENTUM_STRATEGY.md) | `propicks-scan` — momentum/quality stock screener |
| [CONTRARIAN_STRATEGY](docs/CONTRARIAN_STRATEGY.md) | `propicks-contra` — quality-filtered mean reversion |
| [ETF_ROTATION_STRATEGY](docs/ETF_ROTATION_STRATEGY.md) | `propicks-rotate` — sector ETF rotation US/EU/WORLD |

### Sottosistemi operativi

| Doc | Scope |
|-----|-------|
| [BACKTEST_GUIDE](docs/BACKTEST_GUIDE.md) | Walk-forward + portfolio + Monte Carlo |
| [RISK_FRAMEWORK](docs/RISK_FRAMEWORK.md) | Kelly + vol target + VaR + correlation |
| [PNL_ATTRIBUTION](docs/PNL_ATTRIBUTION.md) | α/β/sector/timing decomposition + Phase 7 gate |
| [CALENDAR](docs/CALENDAR.md) | Earnings hard gate + macro events |
| [SCHEDULER](docs/SCHEDULER.md) | APScheduler EOD jobs + alert queue |
| [TELEGRAM_BOT](docs/TELEGRAM_BOT.md) | Push daemon + bot commands |
| [STORAGE](docs/STORAGE.md) | SQLite source of truth + cache |
| [WATCHLIST_AND_TRADE_MGMT](docs/WATCHLIST_AND_TRADE_MGMT.md) | Watchlist, trailing/time stop, exposure |
| [DATA_DICTIONARY](docs/DATA_DICTIONARY.md) | Schema SQLite — ogni tabella e colonna |

### Architettura & Workflow

| Doc | Scope |
|-----|-------|
| [ARCHITECTURE_OVERVIEW](docs/ARCHITECTURE_OVERVIEW.md) | Layer separation, data flow, dependency graph |
| [Trading_System_Playbook](docs/Trading_System_Playbook.md) | Workflow narrativo end-to-end + Perplexity prompts |
| [Weekly_Operating_Framework](docs/Weekly_Operating_Framework.md) | Cadenza weekly (Sab review, Dom plan, Lun-Ven exec) |
| [NEXT_STEPS](docs/NEXT_STEPS.md) | Roadmap fasi |

---

## Quick start in 5 minuti

```bash
# 1. Install
pip install -e ".[dev,dashboard,telegram]"

# 2. Configura .env in root
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Smoke test (no rete)
pytest

# 4. Primo scan
propicks-scan AAPL --validate

# 5. Dashboard
propicks-dashboard            # http://localhost:8501
```

Per setup completo (Docker, scheduler daemon, bot Telegram) vedi
[INSTALLATION_AND_SETUP](docs/INSTALLATION_AND_SETUP.md).

---

## Mappa CLI ↔ Dashboard ↔ Pine

| Workflow | CLI | Dashboard page | Pine script |
|----------|-----|----------------|-------------|
| Screening momentum stock | `propicks-scan` | `1_Scanner.py` | `daily_signal_engine.pine` |
| Mean reversion contrarian | `propicks-contra` | `8_Contrarian.py` | `contrarian_signal_engine.pine` |
| Rotazione settoriale ETF | `propicks-rotate` | `2_ETF_Rotation.py` | `etf_rotation_engine.pine` |
| Regime macro weekly | *(parte del scan)* | *(visibile in Scanner)* | `weekly_regime_engine.pine` |
| Sizing & risk | `propicks-portfolio size --advanced` | `3_Portfolio.py` → tab Rischio | — |
| Trade management | `propicks-portfolio manage --apply` | `3_Portfolio.py` → tab Management | — |
| Journaling | `propicks-journal add/close/list/stats` | `4_Journal.py` | — |
| Reporting | `propicks-report weekly/monthly/attribution` | `5_Reports.py` | — |
| Backtest single | `propicks-backtest --period 3y` | `6_Backtest.py` | — |
| Backtest portfolio | `propicks-backtest --portfolio --monte-carlo` | `11_Backtest_Portfolio.py` | — |
| Watchlist | `propicks-watchlist add/list/status` | `7_Watchlist.py` | — |
| Calendar | `propicks-calendar earnings/macro/check` | `9_Calendar.py` | — |
| Scheduler | `propicks-scheduler run/job/alerts` | `10_Scheduler.py` | — |
| Telegram bot | `propicks-bot run` | — *(bot esterno)* | — |

---

## Convenzioni della wiki

- **Comandi CLI** in code block con prefisso `$` per comandi shell
- **Default Python** sono SEMPRE in `src/propicks/config.py` — la wiki cita le costanti, non duplica i valori
- **Date** sempre in formato ISO 8601 (`YYYY-MM-DD`)
- **Percentuali** come float (`0.08` = 8%)
- **Linking interno** relativo (`docs/...md`)
- **Esempi** preferibili a teoria — quasi ogni sezione ha almeno un comando d'esempio testato

Se trovi un doc che diverge dal codice o dal contratto Pine↔Python, è un bug:
apri issue o sistemalo.
