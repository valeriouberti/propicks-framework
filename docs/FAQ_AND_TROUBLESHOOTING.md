# FAQ & Troubleshooting

Errori comuni e soluzioni. Organizzato per area di sintomo.

> Se non trovi qui il tuo problema, controlla `git log` recente per fix simili
> e i commenti nei moduli `domain/` (i bug edge-case sono spesso documentati
> inline).

---

## Setup & Install

### Q: `pip install -e .` fallisce con error `pyproject.toml not found`

A: Stai lanciando da una directory diversa dalla repo root. `cd` al path che
contiene `pyproject.toml` e riprova.

### Q: `pytest collected 0 items`

A: pytest installato in venv diversa. Verifica:

```bash
which pytest
which python
```

Devono entrambi puntare al `.venv/bin/`. Se no:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

### Q: ImportError su `streamlit` quando lancio `propicks-dashboard`

A: Manca extras. Reinstalla con `pip install -e ".[dashboard]"`.

### Q: ImportError su `telegram` quando lancio `propicks-bot`

A: Manca extras. `pip install -e ".[telegram]"`.

---

## Yfinance & dati di mercato

### Q: `DataUnavailable: AAPL: nessun dato OHLCV`

A: Cause possibili:
1. Ticker mistyped (es. `AAPL.US` invece di `AAPL`).
2. Yfinance rate limit temporaneo. Aspetta 30s e riprova.
3. Ticker delisted o cambio simbolo (es. `FB` → `META`).
4. Ticker italiano senza suffisso `.MI`: usa `ENI.MI`, non `ENI`.

Verifica diretto su yfinance:

```bash
python -c "import yfinance as yf; print(yf.Ticker('AAPL').history(period='5d'))"
```

### Q: Cache restituisce dati stale dopo apertura mercato

A: TTL daily è 8h (`MARKET_CACHE_TTL_DAILY_HOURS`). Se hai caricato i dati alle
6am e ora sono le 16, sono ancora freschi. Force refresh:

```bash
propicks-cache clear --ticker AAPL
propicks-momentum AAPL
```

Oppure `propicks-cache warm AAPL --force`.

### Q: Wikipedia fetch fallisce per S&P 500 / FTSE MIB

A: Wikipedia richiede User-Agent custom + parser lxml. Se vedi
`HTTPError: 403`, è un problema di pacchetti. Verifica:

```bash
python -c "import lxml; print(lxml.__version__)"
```

Se manca: `pip install lxml`. Il framework usa `_read_wikipedia_tables` (non
`pd.read_html` nudo) — assicurati di non aver patchato il modulo.

---

## AI validation

### Q: `[ai] AAPL skipped: weekly regime non disponibile (storia insufficiente)`

A: Il ticker ha < 60 settimane di storia (IPO recente). Fail-closed by design
post-SERIO-5 (vedi review history). Per forzare comunque la validazione:

```bash
propicks-momentum AAPL --force-validate
```

### Q: `[ai] daily call limit reached (50/50)`

A: Budget cap giornaliero raggiunto. Reset alle 00:00 UTC. Per alzare:

```bash
PROPICKS_AI_MAX_CALLS_PER_DAY=100 propicks-momentum AAPL --validate
```

O permanente in `.env`. Usa `--force-validate` solo per emergenze: bypassa
gate ma non bypassa cap.

### Q: Verdict CONFIRM ma Pine dice qualcos'altro

A: Vedi sezione [Pine vs Python drift](#pine-vs-python-drift) sotto.

### Q: `Anthropic API error: invalid_api_key`

A: La key in `.env` è scaduta o sbagliata. Genera nuova in
https://console.anthropic.com/settings/keys e aggiorna `.env`.

### Q: Verdict cached con dati che il modello non poteva avere visti (es. earnings di domani)

A: Cache TTL 24h momentum, 8h ETF rotation. Se l'earnings è stato annunciato dopo
la generazione del verdict cached, il verdict è stale. Force refresh:

```bash
propicks-momentum AAPL --force-validate
```

O delete cache row:

```bash
sqlite3 data/propicks.db "DELETE FROM ai_verdicts WHERE cache_key = 'AAPL_v4_2026-04-25'"
```

---

## Database SQLite

### Q: `OperationalError: database is locked`

A: Due processi stanno scrivendo simultaneamente. SQLite WAL mode permette read
concurrent ma non write-write. Cause comuni:
- Daemon scheduler in run + CLI in foreground sullo stesso DB
- Dashboard + CLI insieme

Fix temporaneo: killa il secondo processo. Fix strutturale: usa il daemon
scheduler come unico writer e il CLI per read-only ops mentre gira.

### Q: Errore `no such table: portfolio` al primo run

A: Migration non applicata. Forza bootstrap:

```bash
python -c "from propicks.io.db import connect; connect()"
```

Apre la connection, invoca `_apply_migrations()`, esce. Verifica:

```bash
sqlite3 data/propicks.db ".tables"
```

Devi vedere portfolio, journal, watchlist, ai_verdicts, ecc.

### Q: Voglio resettare tutto

A: Backup prima!

```bash
sqlite3 data/propicks.db ".backup data/before-reset.db"
rm data/propicks.db
python -c "from propicks.io.db import connect; connect()"
```

Ora hai un DB vuoto con schema fresh.

---

## Pine vs Python drift

Sezione critica perché il contratto Pine ↔ Python è la cosa più fragile del
sistema (due implementazioni della stessa logica in linguaggi diversi).

### Q: Pine score diverso da `propicks-momentum` Python sullo stesso ticker

A: Checklist in ordine di probabilità:

1. **Timeframe sbagliato sul chart**. Daily Pine va su daily, Contrarian Pine va
   su daily, ETF rotation Pine va su weekly (preferito), Regime Pine va su
   weekly. Il Contrarian Pine ha `runtime.error()` se non daily — gli altri no.
2. **Default modificati nei Pine inputs**. Verifica che EMA Fast/Slow, RSI
   period, ATR period, soglie di score siano i default. Se hai cambiato un
   numero, hai rotto il contratto.
3. **Cache stale Python**. Su titoli con dati recenti (post-market events), il
   Pine vede live data e Python vede cache vecchia 8h. `propicks-cache clear --ticker T` e riprova.
4. **Live bar partial vs EOD chiuso**. Durante la sessione il Pine si muove con
   ogni tick; Python si stabilizza al close. Confronto rigoroso solo dopo close
   US (~22:00 CET).
5. **Quality gate (contrarian)** usa weekly EMA40, non daily EMA200 (vedi
   storia review CRIT-quality-gate). Se il tuo Pine è una vecchia versione,
   aggiorna da `tradingview/contrarian_signal_engine.pine` corrente.
6. **Regime classifier (contrarian)** ora calcola tutto nel weekly context via
   helper `classify_weekly_regime`. Le vecchie versioni usavano
   `ta.ema(bClose, 10)` con weekly close proiettata sul daily — sbagliato. Usa
   sempre l'ultima versione del file.

### Q: Pine ETF rotation dice OVERWEIGHT ma Python dice HOLD

A: Probabile mismatch del **Sector Key** input nel Pine. Il regime_fit lookup
dipende dal sector_key. Verifica nel pannello "Sector" che corrisponda al
GICS sector mappato in `config.SECTOR_ETFS_*`.

### Q: Contrarian Pine dice SKIP ma Python dice A

A: Causa più probabile (post-fix BUG quality gate): il chart è su un timeframe
non-daily. Il Pine emette `runtime.error` esplicito se non sei su daily.
Cambia timeframe a 1D.

Se il chart è già su daily, possibili cause secondarie:
- Quality gate broken in Pine ma intact in Python: differenza weekly EMA40 calcolato vs ricevuto. Verifica `request.security` non sia stato modificato.
- Regime cap STRONG_BULL/BEAR attivato in Pine: pannello mostra "STRONG BULL (skip)" o "STRONG BEAR (skip)". Confronta con `propicks-momentum` regime panel — se differiscono, il chart Pine non è ancora warmed up (richiede ≥60 weekly bars).

### Q: Pine valid solo per US? Funziona anche con `.MI` o `.DE`?

A: Funziona per qualsiasi ticker che TradingView ha. Ma:
- **regime classifier** in Python è hardcoded su `^GSPC`. Per allineare, applica `weekly_regime_engine.pine` su SPX, non sul singolo titolo italiano.
- **stock RS** in Python è solo per US (`is_us_ticker`). Non disponibile per `.MI` o `.DE`.

---

## Telegram bot

### Q: `Conflict: terminated by other getUpdates request`

A: Due istanze di `propicks-bot run` in parallelo. Killa la seconda:

```bash
pgrep -f "propicks-bot run"
kill <pid>
```

### Q: Bot risponde a `/status` con "Unauthorized"

A: Il chat_id da cui scrivi non corrisponde a `TELEGRAM_CHAT_ID` in `.env`.
Verifica con `@myidbot` su Telegram → `/getid` e aggiorna `.env`.

### Q: Push alert non arrivano dopo restart

A: Probabile alert "muted" durante downtime. Il bot ha logic anti-spam: al
primo run dopo gap >X minuti silenzia il backlog per non annegare l'utente.
Override:

```bash
propicks-bot mute-backlog          # silenzia esplicitamente
propicks-bot run                   # riprende push normalmente
```

Per riprocessare manualmente alert pending:

```bash
propicks-scheduler alerts          # vedi pending
propicks-scheduler alerts --ack-all
```

---

## Scheduler

### Q: Scheduler daemon parte ma non esegue job all'orario previsto

A: Verifica timezone. APScheduler usa `Europe/Rome` di default. Se il tuo
server è UTC, gli orari di trigger nei job sono sbagliati. Vedi
`src/propicks/scheduler/scheduler.py::_make_scheduler` per il TZ.

### Q: Job manuale (`scheduler job warm`) gira, ma daemon non lo schedula

A: Cron expression del job potrebbe escludere il giorno corrente. Es. job
`scan` schedulato Lun-Ven solo: di sabato non gira. Vedi `scheduler/jobs.py`.

### Q: Voglio loggare tutti gli output del scheduler

A: Lancia con redirect:

```bash
propicks-scheduler run > /var/log/propicks-scheduler.log 2>&1
```

O con systemd: `journalctl -u propicks-scheduler -f`.

---

## Dashboard Streamlit

### Q: `Address already in use: 8501`

A: Altra istanza Streamlit attiva.

```bash
pkill -f streamlit
# o usa porta diversa:
streamlit run src/propicks/dashboard/app.py --server.port 8502
```

### Q: Dashboard mostra dati stale dopo modifica via CLI

A: Streamlit cache. Click "Refresh data" nelle page o usa `st.cache_data.clear()`
manuale. La cache TTL è allineata al market cache (8h daily).

### Q: PyArrow error su `st.dataframe`

A: Mix di tipi `float`/sentinel `"—"` in una colonna. Vedi `_shared.py::_safe_str`
— tutto va serializzato a string omogenea. Se il bug è ricorrente, apri issue
con stack trace.

---

## Performance

### Q: Discovery contrarian su S&P 500 ci mette 30 minuti

A: 500 ticker × 5s di network = 40 min worst case. Ottimizzazioni:
- Cache pre-warmata: `propicks-cache warm $(propicks-watchlist list --json | jq -r '.[].ticker')` la sera prima.
- `--top N` con `N=20`: il pipeline 3-stage skippa early i ticker che non passano stage 1.
- Riduci universo: usa FTSE MIB (40 nomi) o STOXX 600 (~600 ma early-prune più aggressivo).

### Q: `propicks-momentum AAPL` ci mette >10s

A: Cache miss + AI validation. Senza `--validate` deve scendere sotto i 3s.
Se cache miss persiste:

```bash
propicks-cache stats          # verifica hit rate
```

Se hit rate < 50%, potrebbe esserci un bug di caching (es. TTL configurato
male). Prova reset:

```bash
propicks-cache clear --all
propicks-cache warm AAPL MSFT NVDA
```

---

## Comportamenti by-design (NON sono bug)

- **`--validate` non gira con regime BEAR**: by design. Il regime gate skippa
  validazioni momentum sotto NEUTRAL. Override con `--force-validate`.
- **Score A in Python ma classe B in Pine durante la sessione**: il Pine vede
  intra-day tick partial. Il match perfetto è solo a EOD US chiuso.
- **AI verdict diverso lo stesso giorno con `--force-validate`**: il modello
  non è deterministico. Cache identica entro 24h ma `--force-validate` forza
  nuovo call → risultato leggermente diverso possibile.
- **Watchlist auto-add anche per ticker già in portfolio**: by design — la
  watchlist registra l'idea, il portfolio la posizione. Sono entità separate.
- **Earnings hard gate blocca anche se il trade è chiaramente long-term**: by
  design. Override esplicito con `--ignore-earnings` per trade contrarian
  intentional su earnings flush.

---

## Reportare un bug

Se trovi un comportamento non documentato qui:

1. Riproduci in modo minimale (un singolo comando + output completo).
2. Verifica con `git log -1 --pretty=format:"%h %s"` la versione corrente.
3. `pytest` è verde? Se no, è un bug regression — riportalo.
4. Apri issue con: comando esatto, output stderr/stdout, version commit hash,
   contenuto rilevante di `data/propicks.db` (esporta solo schema, NON dati).

```bash
sqlite3 data/propicks.db ".schema" > schema.sql
```

In caso di drift Pine ↔ Python, includi screenshot pannello Pine + output JSON
del comando Python equivalente.
