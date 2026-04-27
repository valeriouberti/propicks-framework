# Telegram Bot — Push Notifications + Bidirectional Commands

> Phase 4: consuma la queue `alerts` generata dallo scheduler e invia push
> notifications via Telegram + accetta comandi bidirezionali.
> **Dep opzionale**: `pip install -e '.[telegram]'` (richiede `python-telegram-bot>=20`).

---

## 1. Setup BotFather (one-time)

```
1. Telegram → @BotFather → /newbot
2. Scegli nome ("Propicks Personal Bot") e username finito in _bot
3. BotFather ritorna un token tipo 1234567890:ABCdef...
4. Invia /start al tuo bot nuovo
5. Manda un messaggio al tuo bot (anche solo "ciao")
6. Apri https://api.telegram.org/bot<TOKEN>/getUpdates → prendi "chat":{"id": NUMERO}
   Alternativa: @userinfobot → /start → mostra il tuo chat_id
```

---

## 2. Configurazione env (`.env`)

```bash
PROPICKS_TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
PROPICKS_TELEGRAM_CHAT_ID=123456789           # il tuo chat_id
# PROPICKS_TELEGRAM_CHAT_ID=id1,id2,id3       # CSV per multi-chat (famiglia, co-trader)
PROPICKS_TELEGRAM_POLL_INTERVAL=60            # sec tra cicli dispatcher (default 60)
```

---

## 3. First setup: silenzia backlog storico

```bash
# Evita spam al primo avvio se hai già alert in DB dallo scheduler
propicks-bot mute-backlog

# Test connettività: manda 1 messaggio di conferma
propicks-bot test

# Avvia bot daemon
propicks-bot run
```

---

## 4. Comandi bot (dalla chat Telegram)

| Comando      | Azione |
|--------------|--------|
| `/status`    | Portfolio summary: cash %, posizioni aperte, P&L unrealized |
| `/portfolio` | Dettaglio per ticker con P&L % live |
| `/alerts`    | Alert pending (non-ack) con ID per /ack |
| `/ack N`     | Acknowledge alert N |
| `/ackall`    | Mark all as read |
| `/history`   | Ultimi 10 job scheduler con status/duration |
| `/cache`     | Stats cache OHLCV (rows, ticker, date max) |
| `/regime`    | Regime macro corrente con emoji severity |
| `/report`    | Summary attribution: per-strategy 30gg + gate status |
| `/calendar`  | Earnings + macro upcoming nei prossimi 14gg |
| `/help`      | Lista comandi |

---

## 5. Daemon management

```bash
propicks-bot run                # foreground, Ctrl+C per fermare
# In tmux / nohup:
nohup propicks-bot run > /tmp/propicks-bot.log 2>&1 &

# macOS launchd plist (esempio):
# ~/Library/LaunchAgents/com.propicks.bot.plist
# <plist>
#   <ProgramArguments><array>
#     <string>/path/to/.venv/bin/propicks-bot</string>
#     <string>run</string>
#   </array></ProgramArguments>
#   <RunAtLoad><true/></RunAtLoad>
#   <KeepAlive><true/></KeepAlive>
# </plist>
# launchctl load ~/Library/LaunchAgents/com.propicks.bot.plist
```

---

## 6. Dispatcher: semantica retry

- Alert in `alerts` con `delivered=0` → candidati per invio ogni ciclo
- Invio OK → `delivered=1, delivered_at=now`
- Invio fallito → `delivery_error='try:N|last_err'`, `delivered=0` (retry prossimo ciclo)
- Dopo **3 fallimenti** (`try:3|...`), l'alert viene **skippato** per evitare flood infinito
- Recovery: `propicks-bot reset-retries` → azzera counter di tutti i failed, retry dal prossimo ciclo

---

## 7. Architettura

```
           ┌───────────────┐
           │   Scheduler   │  (Phase 3)
           │ scheduler_runs│
           └───────┬───────┘
                   │ INSERT INTO alerts (delivered=0)
                   ▼
           ┌───────────────┐       polling 60s
           │    alerts     │◄─────────────────┐
           │   (SQLite)    │                  │
           └───────┬───────┘                  │
                   │ SELECT WHERE delivered=0 │
                   ▼                          │
           ┌───────────────┐                  │
           │   Dispatcher  │──────────────────┘
           │ (notifications│
           │   /dispatcher)│ UPDATE delivered=1
           └───────┬───────┘
                   │ send_message()
                   ▼
           ┌───────────────┐       /status /alerts /ack
           │ Telegram Bot  │◄─────────────────┐
           │ (async poll)  │                  │
           └───────┬───────┘                  │
                   ▼                          │
           ┌───────────────┐                  │
           │  User's phone │──────────────────┘
           └───────────────┘
```

---

## 8. Sicurezza

- **Token** resta in `.env` (gitignored)
- **Chat whitelist**: i comandi da chat non in `PROPICKS_TELEGRAM_CHAT_ID` sono ignorati silenziosamente (no ack, no error)
- **No inbound webhook**: polling-based, nessun server esposto pubblicamente
- **Scheduler → DB → Bot**: loose coupling. Lo scheduler non sa di Telegram. Il bot non sa dei job. Si parlano via tabella `alerts`.

---

## 9. Operations quotidiane

```bash
propicks-bot stats                       # quanti alert pending/delivered/failed
propicks-bot reset-retries               # reset counter per recovery
propicks-bot reset-retries --alert-id 42 # solo uno specifico
propicks-bot mute-backlog                # flag pending come delivered (setup)
```

---

## 10. Trade-off accettati

- **Nessun rate limiting attivo**: python-telegram-bot gestisce il rate limit API di Telegram (30 msg/sec), ma se accumuli 100+ alert pending il dispatcher li invia tutti nel ciclo — possibile batching spam. Accettabile per trader retail (normalmente <10 alert/giorno).
- **Delivery non garantita cross-instance**: se lanci 2 bot daemon con stesso token, ciascuno processerà gli alert e finirai per ricevere doppio. **Un solo daemon per DB** (stessa regola dello scheduler).
- **Command args quotate male**: parsed da python-telegram-bot come lista di token. `/ack 42` → args=["42"]. Niente string parsing avanzato: i comandi sono intenzionalmente semplici.
