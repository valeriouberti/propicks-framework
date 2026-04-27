# Security & Secrets

Gestione delle credenziali, .env, API key, e best practice di sicurezza per
deployment locale e produzione.

---

## Cosa va protetto

| Segreto | Dove vive | Severity |
|---------|-----------|----------|
| `ANTHROPIC_API_KEY` | `.env` o env var | 🔴 Critica — accesso fatturabile + dati prompt |
| `TELEGRAM_BOT_TOKEN` | `.env` o env var | 🟠 Alta — chiunque controlla il bot |
| `TELEGRAM_CHAT_ID` | `.env` o env var | 🟡 Media — non è auth ma routing destinatario |
| `data/propicks.db` | Filesystem locale | 🟠 Alta — contiene posizioni reali, P&L, journal |
| `data/ai_cache/usage_*.json` | Filesystem locale | 🟢 Bassa — solo telemetria budget |

---

## File `.env`

**Posizione**: root del progetto (stesso livello di `pyproject.toml`).
**Permission**: `chmod 600 .env` (solo owner può leggere/scrivere).
**Versionamento**: SEMPRE in `.gitignore`. Mai committare.

Verifica gitignore:

```bash
grep -E "^\.env$" .gitignore || echo ".env" >> .gitignore
```

`propicks.config` carica `.env` con `python-dotenv` con `override=False` — la
shell ha precedenza. Conseguenza pratica: puoi sovrascrivere temporaneamente
una key per una sessione senza toccare `.env`:

```bash
PROPICKS_AI_MODEL=claude-haiku-4-5 propicks-scan AAPL --validate
```

---

## Anthropic API key

### Setup

1. Crea key su https://console.anthropic.com/settings/keys
2. Aggiungi a `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxx
   ```
3. Verifica: `propicks-scan AAPL --validate --brief`

### Budget cap

Il framework applica due limiti **per giorno**, persistiti nella tabella
`daily_budget`:

| Cap | Default | Override env var |
|-----|---------|------------------|
| Calls/day | 50 | `PROPICKS_AI_MAX_CALLS_PER_DAY` |
| Cost USD/day | 5.0 | `PROPICKS_AI_MAX_COST_USD_PER_DAY` |

Il counter incrementa solo su **cache miss reali** (cache hit non contano).
Reset automatico al cambio data (UTC). Quando un cap è raggiunto:

```
[ai] AAPL skipped: daily call limit reached (50/50)
```

Il comando ritorna senza errore — la CLI continua a funzionare per gli altri
ticker o senza `--validate`.

### Rotation

```bash
# 1. Genera nuova key in console Anthropic
# 2. Aggiorna .env
# 3. Revoca la vecchia (sempre in console)
# 4. Verifica
propicks-scan AAPL --force-validate --brief
```

Nessun restart richiesto — `.env` viene riletto al prossimo run del CLI.
Per il scheduler/bot daemon: `systemctl restart propicks-scheduler`.

### Web search costo

Il tool `web_search_20250305` server-side Anthropic costa **$0.01/ricerca**
oltre i token. Configurabile:

```bash
PROPICKS_AI_WEB_SEARCH=0          # disabilita completamente
PROPICKS_AI_WEB_SEARCH_MAX_USES=3 # default 5
```

Il count effettivo per call è loggato su stderr: `[ai] ... web_search_count=N`.

---

## Telegram bot token

### Setup BotFather

1. Apri Telegram → cerca `@BotFather`
2. `/newbot` → segui prompt → ottieni token `123456789:ABC-DEF...`
3. Aggiungi a `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABC-DEF...
   ```
4. Apri il tuo bot → invia `/start`
5. Trova il tuo chat_id: cerca `@myidbot` su Telegram → `/getid`
6. Aggiungi a `.env`:
   ```
   TELEGRAM_CHAT_ID=123456789
   ```
7. Verifica: `propicks-bot test`

### Permission model

Il bot risponde a comandi (`/status`, `/portfolio`, ...) **solo dal chat_id
configurato**. Tentativi da altri chat sono ignorati silenziosamente.

Non c'è un modello multi-utente: il framework è single-trader. Per condividere
con un secondo trader: deploy separato con DB suo.

### Rotation

1. BotFather → `/revoke` → genera nuovo token (vecchio invalidato)
2. Update `.env`
3. Restart daemon: `systemctl restart propicks-bot` (o killa + riavvia)
4. `propicks-bot test`

---

## Database SQLite

### Cosa contiene

`data/propicks.db` è la source of truth di **tutto** lo stato transazionale:

- Posizioni aperte (entry, shares, stop, target)
- Journal trade (storico chiusi)
- Watchlist
- AI verdicts cache
- Cache OHLCV (solo dati pubblici)
- Strategy runs history
- Daily budget tracker
- Regime history snapshots

**NON** contiene credenziali, ma contiene **dati finanziari personali**.
Trattalo come un wallet: backup criptati, no commit, no condivisione.

### Backup criptato

```bash
# Snapshot + AES encryption
sqlite3 data/propicks.db ".backup /tmp/snapshot.db"
gpg --symmetric --cipher-algo AES256 /tmp/snapshot.db
mv /tmp/snapshot.db.gpg /backup/propicks-$(date +%Y%m%d).db.gpg
shred -u /tmp/snapshot.db
```

Restore:

```bash
gpg --decrypt /backup/propicks-20260415.db.gpg > data/propicks.db
sqlite3 data/propicks.db "PRAGMA integrity_check;"
```

### Cloud sync

Se vuoi sync cross-device, evita Dropbox/iCloud nudi sul `data/` (race
condition con WAL mode). Preferisci:

- `restic` con backend S3/B2 (snapshot incrementali criptati)
- `rclone` con `--vfs-cache-mode writes` (mai live editing su cloud mount)

---

## Logging — cosa NON loggare

Il logger `propicks.obs.log` segue queste regole:

- **MAI** loggare il valore di una API key, token, o stringa che inizi con `sk-`.
- **MAI** loggare il payload completo di una chiamata Anthropic (può contenere prompt sensibili in lower environments).
- **OK** loggare model name, token count, durata, http status, web_search_count.

Verifica:

```bash
propicks-scan AAPL --validate 2>&1 | grep -iE "sk-|bearer|token" || echo "clean"
```

Se vedi una key nel log, è un bug — apri issue.

---

## Network considerations

### Outbound traffic

Il framework si connette solo a:

- `query2.finance.yahoo.com` (yfinance)
- `en.wikipedia.org` (index constituents)
- `api.anthropic.com` (AI validation)
- `api.telegram.org` (bot push, polling)

Per ambienti enterprise dietro proxy: imposta `HTTPS_PROXY` env var. Anthropic
SDK e python-telegram-bot rispettano `HTTPS_PROXY`. yfinance lo rispetta via
`requests`.

### Inbound traffic

Solo se `propicks-dashboard` è esposto. Streamlit di default lega a
`localhost:8501`. Per esporre su LAN:

```bash
streamlit run src/propicks/dashboard/app.py --server.address 0.0.0.0
```

⚠️ **Senza auth**, chiunque sulla rete può vedere posizioni e P&L. Mai esporre
su internet pubblico nudo. Per accesso remoto sicuro:

- SSH tunnel: `ssh -L 8501:localhost:8501 user@server`
- Reverse proxy con basic auth (nginx + htpasswd)
- Tailscale / WireGuard VPN (preferibile)

---

## Modello di minaccia tipico

| Minaccia | Probabilità | Impatto | Mitigazione |
|----------|-------------|---------|-------------|
| `.env` committato per errore | Media | Critico | Pre-commit hook, gitignore audit |
| Laptop perso/rubato senza FDE | Media | Critico | FileVault/BitLocker abilitato + backup criptati |
| Phishing → API key esposta | Bassa | Alto | Rotation 90gg, monitoring console Anthropic per anomalie |
| Dashboard esposta su LAN insicura | Bassa | Medio | Bind localhost-only, VPN per remoto |
| Bot token compromesso | Bassa | Medio | Permission model single chat_id, rotation se sospetto |
| DB locale leak (multi-utente macOS) | Bassa | Alto | `chmod 600 data/propicks.db` + FDE |

---

## Pre-commit hook (consigliato)

Installa `pre-commit` per evitare commit accidentali di `.env`:

```bash
pip install pre-commit
cat > .pre-commit-config.yaml <<EOF
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: detect-private-key
      - id: check-added-large-files
        args: [--maxkb=1024]
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
EOF
pre-commit install
```

Ora `git commit` blocca se rileva chiavi nel diff.

---

## Checklist di sicurezza

- [ ] `.env` esiste, ha `chmod 600`, ed è in `.gitignore`
- [ ] `data/` è in `.gitignore` (verificalo: `git check-ignore data/propicks.db`)
- [ ] Nessuna API key hardcoded nel codice (`grep -r "sk-ant" src/`)
- [ ] Backup DB cifrato attivo (cron daily)
- [ ] FDE attiva sul disco del laptop
- [ ] Dashboard NON esposta su internet pubblico
- [ ] `propicks-bot` permission model verificato (chat_id corretto)
- [ ] Anthropic console: limit per la API key impostato (cap mensile)
- [ ] Pre-commit hook installato (opzionale ma consigliato)

Audit periodico (ogni 90gg):

```bash
# Rotazione chiavi
# Verifica gitignore
git ls-files | grep -E "\.env$" && echo "WARNING: .env tracked" || echo "ok"
# Audit log usage
ls -la data/ai_cache/
```
