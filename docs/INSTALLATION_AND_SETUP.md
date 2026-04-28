# Installation & Setup

Guida completa al setup, dalla virtual env iniziale al daemon scheduler in
produzione. Per l'uso quotidiano dopo il setup vedi [USER_GUIDE](USER_GUIDE.md).

---

## Prerequisiti

| Strumento | Versione | Note |
|-----------|----------|------|
| Python | ≥ 3.10 | Tipi annotati moderni richiesti |
| pip | ≥ 23 | `pip install -e .` con extras |
| git | ≥ 2.30 | Per clone + tag versioning |
| sqlite3 | ≥ 3.35 | Built-in con Python; usa CLI per backup manuali |
| (opzionale) Docker | ≥ 24 | Per dashboard e bot in container |

**Account/key esterni** (necessari solo per le feature relative):

- **Anthropic API key**: per `--validate` AI. Senza chiave, scan/contra/rotate funzionano ma `--validate` fallisce con errore esplicito.
- **Telegram BotFather**: per il push bot. Senza, il bot daemon non parte ma scheduler/CLI restano funzionali.
- **TradingView account**: per i Pine script. Free è sufficiente per i 4 script forniti.

---

## Installazione

### Opzione 1 — venv locale (consigliata per dev e single-user)

```bash
git clone <repo-url> propicks-ai-framework
cd propicks-ai-framework

python3.10 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e ".[dev,dashboard,telegram]"
```

Variants extras:

| Comando | Cosa installa |
|---------|---------------|
| `pip install -e .` | Solo runtime (scan/contra/rotate/portfolio/journal/report/backtest/watchlist/calendar/cache/scheduler/migrate) |
| `pip install -e ".[dev]"` | + pytest, ruff, mypy |
| `pip install -e ".[dashboard]"` | + streamlit, plotly |
| `pip install -e ".[telegram]"` | + python-telegram-bot |
| `pip install -e ".[dev,dashboard,telegram]"` | Tutto |

### Opzione 2 — Docker (consigliata per dashboard sempre-on)

```bash
docker compose up -d              # avvia dashboard
docker compose logs -f dashboard  # log live
docker compose down               # stop
```

Il `Dockerfile` parte da `python:3.12-slim-bookworm` + extras `[dashboard]`.
Volume `data/` è bind-mounted: il DB sopravvive ai restart del container.

---

## Configurazione `.env`

Crea `.env` in **root del progetto** (gitignored). `propicks.config` lo carica
automaticamente via `python-dotenv` con `override=False` (la shell ha precedenza
sulle vars già esportate).

Template completo:

```bash
# ==== Anthropic AI ====
ANTHROPIC_API_KEY=sk-ant-api03-...

# (opzionali)
PROPICKS_AI_MODEL=claude-opus-4-6              # default
PROPICKS_AI_WEB_SEARCH=1                       # 0/false/no/off per disabilitare
PROPICKS_AI_WEB_SEARCH_MAX_USES=5              # max tool calls per validation
PROPICKS_AI_MAX_CALLS_PER_DAY=50               # daily budget cap
PROPICKS_AI_MAX_COST_USD_PER_DAY=5.0
PROPICKS_AI_EST_COST_PER_CALL_USD=0.10

# ==== Telegram (solo se usi propicks-bot) ====
TELEGRAM_BOT_TOKEN=123456789:ABC-DEF...
TELEGRAM_CHAT_ID=123456789                     # tuo chat_id (vedi /id su @myidbot)
TELEGRAM_ADMIN_USER=mariorossi                 # username che può eseguire /ack
```

**Sicurezza**: vedi [SECURITY_AND_SECRETS](SECURITY_AND_SECRETS.md). Mai
committare `.env`. Mai stampare le var nei log.

---

## Smoke test post-install

Verifica step-by-step che ogni layer funzioni.

### 1. Test domain (no rete)

```bash
pytest                              # ~544 test, deve girare in <5s
```

Se fallisce: dipendenze non installate correttamente. Re-run pip install.

### 2. Test market (rete yfinance)

```bash
propicks-momentum AAPL --no-watchlist --json | jq '.[0].score_composite'
```

Output atteso: un float 0-100. Se errore `DataUnavailable`: yfinance ha problemi
con quel ticker o sei rate-limited (raro con cache 8h).

### 3. Test AI (Anthropic API)

```bash
propicks-momentum AAPL --validate --no-watchlist --brief
```

Output atteso: tabella con colonna AI verdict (CONFIRM/CAUTION/REJECT). Se
errore `ANTHROPIC_API_KEY non impostata`: re-check `.env`. Se errore
`Anthropic API error`: verifica la key sia valida e non scaduta.

### 4. Test DB

```bash
propicks-portfolio status
```

Output atteso: tabella vuota (no posizioni) + capitale corrente. Se errore
SQLite: il DB `data/propicks.db` viene creato al primo run. Verifica che
`data/` sia scrivibile.

### 5. Test cache

```bash
propicks-cache stats
```

Output atteso: tabella con entry count per intervallo (daily/weekly/meta).

### 6. Test dashboard

```bash
propicks-dashboard
```

Apre `http://localhost:8501`. Sidebar con 11 page navigabili. Se errore
`ModuleNotFoundError: streamlit`: install `[dashboard]` extras.

### 7. Test Telegram (opzionale)

```bash
propicks-bot test
```

Output atteso: messaggio "Test from propicks-bot" arriva sul tuo chat. Se
errore `Unauthorized`: token sbagliato. Se `Chat not found`: chat_id sbagliato.

### 8. Test scheduler (opzionale)

```bash
propicks-scheduler job snapshot     # one-shot, NON daemon
```

Output atteso: snapshot regime + portfolio + cache stats stampati su stdout +
loggato in `strategy_runs`.

---

## Setup TradingView (Pine scripts)

I 4 Pine script in `tradingview/` non sono pacchettizzati con Python. Setup
manuale per ognuno:

1. TradingView → chart del simbolo target (es. `AAPL` per momentum, `^GSPC` per
   regime macro).
2. Pine Editor (panel inferiore) → "Open" → "Pine Script™".
3. Incolla il contenuto del `.pine` corrispondente (vedi
   [PINE_SCRIPTS_REFERENCE](PINE_SCRIPTS_REFERENCE.md)).
4. "Save" → assegna nome (es. "Propicks Daily").
5. "Add to chart". L'indicatore appare overlay + pannello score top-right.
6. Tasto destro → "Add Alert on indicator" per attivare i push (mobile).

I default Pine sono allineati a `config.py` byte-per-byte. Non modificarli a
meno di non aggiornare anche Python (vedi [PINE_SCRIPTS_REFERENCE](PINE_SCRIPTS_REFERENCE.md)).

---

## Setup scheduler in produzione

Per far girare il daemon EOD su un server (VPS/Raspberry):

### Systemd unit (Linux)

`/etc/systemd/system/propicks-scheduler.service`:

```ini
[Unit]
Description=Propicks Scheduler EOD
After=network.target

[Service]
Type=simple
User=propicks
WorkingDirectory=/home/propicks/propicks-ai-framework
EnvironmentFile=/home/propicks/propicks-ai-framework/.env
ExecStart=/home/propicks/propicks-ai-framework/.venv/bin/propicks-scheduler run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable propicks-scheduler
sudo systemctl start propicks-scheduler
sudo systemctl status propicks-scheduler
journalctl -u propicks-scheduler -f
```

Stessa logica per `propicks-bot run` se vuoi il bot daemon parallelo.

### Cron alternativa

Se preferisci cron al daemon APScheduler:

```cron
# m h dom mon dow command
30 22 * * 1-5  cd /home/propicks/propicks-ai-framework && .venv/bin/propicks-scheduler job snapshot
0  23 * * 1-5  cd /home/propicks/propicks-ai-framework && .venv/bin/propicks-scheduler job warm
```

Ma il `propicks-scheduler run` daemon ha alert queue e history tracker
integrati — preferibile.

---

## Backup

Il DB `data/propicks.db` è la source of truth. Backup periodico:

```bash
# Manual snapshot
sqlite3 data/propicks.db ".backup data/propicks-backup-$(date +%Y%m%d).db"

# Cron giornaliero alle 23:30
30 23 * * * cd /home/propicks/propicks-ai-framework && sqlite3 data/propicks.db ".backup /backup/propicks-$(date +\%Y\%m\%d).db"
```

Per restore:

```bash
cp /backup/propicks-20260415.db data/propicks.db
sqlite3 data/propicks.db "PRAGMA integrity_check;"
```

---

## Aggiornamenti

```bash
git pull
pip install -e ".[dev,dashboard,telegram]" --upgrade
pytest                              # smoke test post-upgrade
```

Se ci sono migration schema, vengono applicate al primo `connect()` dopo
l'upgrade (vedi `io/db.py::_apply_migrations`). I dati esistenti sono
preservati.

---

## Troubleshooting setup

| Sintomo | Diagnosi | Fix |
|---------|---------|-----|
| `ModuleNotFoundError: propicks` | Editable install non riuscito | `pip install -e .` da repo root |
| `pytest collected 0 items` | pytest installato ma in venv diverso | Verifica `which pytest` corrisponda al `.venv/bin/` |
| `ANTHROPIC_API_KEY non impostata` | `.env` non in root o non caricato | Verifica `pwd` quando lanci, o esporta in shell |
| `DataUnavailable: ...` per ticker valido | Rate limit yfinance temporaneo | Riprova tra 10s; cache mitiga |
| `OperationalError: database is locked` | Due processi scrivono insieme | Killa il secondo; SQLite WAL gestisce reads concurrent ma write-write no |
| Streamlit "Address already in use" 8501 | Altra istanza già attiva | `pkill -f streamlit` o cambia port: `streamlit run ... --server.port 8502` |
| Telegram "Conflict: terminated by other getUpdates" | Due `propicks-bot run` in parallelo | Killa il duplicato |

Vedi [FAQ_AND_TROUBLESHOOTING](FAQ_AND_TROUBLESHOOTING.md) per più dettagli.

---

## Verifica versione

```bash
pip show propicks-ai-framework | grep Version
git log -1 --pretty=format:"%h %s (%cd)" --date=short
```
