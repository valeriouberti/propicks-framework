# Dashboard Guide — Streamlit Multi-Page

Guida alle 11 page della dashboard Streamlit. La dashboard è **parallela alla
CLI** (vedi [CLI_REFERENCE](CLI_REFERENCE.md)) — chiama le stesse funzioni del
motore Python e legge/scrive lo stesso `data/propicks.db`.

---

## Avvio

```bash
pip install -e ".[dashboard]"
propicks-dashboard                # http://localhost:8501
# Oppure via Docker:
docker compose up -d dashboard
```

Layout: una **home** (Portfolio Overview) + 11 page numerate nella sidebar
sinistra. Le page sono navigabili con click; lo stato è persistito su DB
quindi non si perde tra refresh.

---

## Architettura della dashboard

```
src/propicks/dashboard/
├── app.py                    # Home — Portfolio Overview
├── _shared.py                # Cached readers (st.cache_data) per riusare fetch yfinance
├── launcher.py               # Entry point propicks-dashboard
├── cadence.py                # Helper countdown/markup per stato job scheduler
└── pages/
    ├── 1_Scanner.py
    ├── 2_ETF_Rotation.py
    ├── 3_Portfolio.py
    ├── 4_Journal.py
    ├── 5_Reports.py
    ├── 6_Backtest.py
    ├── 7_Watchlist.py
    ├── 8_Contrarian.py
    ├── 9_Calendar.py
    ├── 10_Scheduler.py
    └── 11_Backtest_Portfolio.py
```

Le page sono **thin** come la CLI: parsing input UI + chiamata a domain/io/ai +
formatting tabellare via `st.dataframe`/`st.metric`. **Nessuna logica di business**
nelle page — tutto in `domain/`.

---

## Home — Portfolio Overview (`app.py`)

**Vista a colpo d'occhio del portfolio + sistema**.

Sezioni:

- **KPI bar**: Capitale corrente, P&L unrealized, P&L realized YTD, posizioni aperte/cap (es. 7/10), cash reserve %.
- **Posizioni aperte**: tabella con ticker, strategia, entry, current, %ret, stop distance, target distance, days held, classification.
- **Distribution sector** (donut chart): esposizione per sector_key.
- **Distribution strategy** (donut): % capitale per TechTitans / DominaDow / ETF / Contrarian / etc.
- **Status sistema**: regime corrente (`^GSPC` weekly), VIX, last cache update, last scheduler run, alert pending count.

CLI equivalente: `propicks-portfolio status` + `propicks-portfolio risk`.

---

## 1. Scanner — `pages/1_Scanner.py`

**Screening momentum stock** (= [`propicks-momentum`](CLI_REFERENCE.md#propicks-momentum)).

Form input:
- Tickers (textarea, uno per riga)
- Strategy bucket (dropdown: TechTitans, DominaDow, BattiSP500, MiglioriItaliane)
- Toggle "Validate via Claude" (richiede `ANTHROPIC_API_KEY`)
- Toggle "Force validate" (bypass cache + gate)

Output:
- Tabella riassuntiva ordinata per score (compare anche regime weekly + RS settoriale)
- Per ogni ticker, expander con 6 sub-score, classification, AI verdict (se validato), e blocco "TradingView Pine inputs" copia-incollabile.

Interazione speciale:
- Click "→ Watchlist" su ogni row aggiunge alla watchlist senza tornare in CLI.
- Click "→ Sizing" passa il ticker pre-compilato alla page Portfolio.

---

## 2. ETF Rotation — `pages/2_ETF_Rotation.py`

**Ranking + allocation settoriale** (= [`propicks-rotate`](CLI_REFERENCE.md#propicks-rotate)).

Selettori:
- Region: US / EU / WORLD / ALL (radio)
- Top N (slider 3-7)
- Toggle "Include allocation" + "Validate macro via Claude"

Output:
- Ranking table (rank, ticker, sector_key, score composite, sub-score breakdown, RS ratio, perf 3M, regime cap flag `*`)
- Top-pick detail card con sub-score chart radar
- Proposta allocation (se attivata): % per ETF, esposizione aggregata, regime-aware (BEAR → top-1 only, STRONG_BEAR → flat)
- AI verdict macro view (se validato)

---

## 3. Portfolio — `pages/3_Portfolio.py`

**Trade lifecycle completo** in tab.

### Tab "Posizioni"
Lista posizioni aperte con sort/filter. Per ogni row:
- Edit inline: stop / target / catalyst note
- Action: Close, Remove, Recompute sizing

### Tab "Sizing nuovo trade"
Form con ticker / entry / stop / score Claude / score Tech / strategy. Toggle "Advanced sizing" abilita Phase 5 (Kelly + vol target + correlation).

CLI equivalente: `propicks-portfolio size` + `propicks-portfolio add`.

### Tab "Rischio & esposizione"
Tabelle:
- Esposizione per sector (% capitale + cap)
- Esposizione per strategy bucket
- Beta-weighted exposure (se yfinance ha beta)
- Correlation matrix tra posizioni aperte (≥30 giorni storico)

CLI equivalente: `propicks-portfolio risk`.

### Tab "Trade management"
- Trailing stop status per posizione (active/disabled, current ATR-stop)
- Time stop countdown (per contrarian: 15gg)
- Target hit checker
- Bottone "Apply manage" (= `propicks-portfolio manage --apply`)
- Override input ATR mult e time stop window

---

## 4. Journal — `pages/4_Journal.py`

**Storico trade append-only** (= [`propicks-journal`](CLI_REFERENCE.md#propicks-journal)).

Filtri:
- Status: Open / Closed / All
- Strategy bucket
- Date range entry
- Min/max R/R realized

Output:
- Tabella trade con entry/exit/P&L/duration/reason
- Stats panel: hit rate per strategy, R/R medio, P&L cumulativo, max drawdown
- Grafico equity curve (da journal data)

Action:
- Add new trade (form simile alla CLI `journal add`)
- Close trade aperto inline

---

## 5. Reports — `pages/5_Reports.py`

**Genera + visualizza markdown reports**.

Pulsanti:
- "Generate weekly" (= `propicks-report weekly`)
- "Generate monthly" (= `propicks-report monthly`)
- "Generate attribution" (= `propicks-report attribution`)

Archivio:
- Lista report storici in `reports/` con preview
- Download MD direct
- Diff tra due report (utile per vedere cosa è cambiato week-over-week)

---

## 6. Backtest — `pages/6_Backtest.py`

**Backtest single-ticker** (= [`propicks-backtest`](CLI_REFERENCE.md#propicks-backtest)).

Form:
- Ticker (singolo)
- Period (1y / 3y / 5y / 10y)
- Threshold score (slider 50-90)
- Strategy bucket

Output:
- Equity curve chart
- Trades table (entry/exit/score/P&L)
- Stats: hit rate, profit factor, max DD, Sharpe, Sortino
- Walk-forward windows visual (se abilitato)

---

## 11. Backtest Portfolio — `pages/11_Backtest_Portfolio.py`

**Backtest multi-ticker portfolio** + Monte Carlo.

Form:
- Ticker basket (multi-select)
- Transaction cost bps
- Monte Carlo iterations (slider 0-2000)
- OOS split toggle (0.70 default)

Output:
- Equity curve aggregata + benchmark `^GSPC`
- Distribution Monte Carlo (5°/50°/95° percentile)
- Per-ticker contribution breakdown
- Drawdown decomposition

---

## 7. Watchlist — `pages/7_Watchlist.py`

**Idee in incubazione** (= [`propicks-watchlist`](CLI_REFERENCE.md#propicks-watchlist)).

Tabella con sort:
- Ticker, target_entry, current price, distance %, score_at_add, classification_at_add, source (auto_scan / manual), age days
- Status colorato: READY (≤1% dal target), CLOSE (≤5%), FAR (>5%)

Action:
- Edit inline target / note
- Remove
- Move to Portfolio (apre form Sizing pre-compilato)
- "Show stale" toggle (>30gg senza azione)

---

## 8. Contrarian — `pages/8_Contrarian.py`

**Mean reversion screener** (= [`propicks-contra`](CLI_REFERENCE.md#propicks-contra)).

Sezioni:

### "Single ticker"
Stesso input form dello Scanner ma usa contrarian engine. Mostra:
- 4 sub-score (oversold/quality/market_context/reversion)
- Quality gate INTACT/BROKEN
- Stop suggested + target EMA50 + R/R

### "Discovery batch"
Selettori:
- Universe: SP500 / FTSEMIB / STOXX600
- Top N
- Min score
- Toggle "Validate top results"

Output: ranked list con expander per dettaglio + AI verdict flush-vs-break.

---

## 9. Calendar — `pages/9_Calendar.py`

**Earnings + macro events**.

Tab "Earnings":
- Lista ticker portfolio + watchlist con next earnings date + days to
- Hard gate flag (≤5gg → BLOCKED entry, override `--ignore-earnings` da CLI)
- Refresh button (yfinance fetch)

Tab "Macro 2026":
- Calendar grid: FOMC / CPI / NFP / ECB
- Filtro per type
- Highlight eventi nei prossimi 7gg

Tab "Check ticker":
- Input ticker → mostra earnings + days_to + gate status

---

## 10. Scheduler — `pages/10_Scheduler.py`

**APScheduler EOD jobs + alert queue** (= [`propicks-scheduler`](CLI_REFERENCE.md#propicks-scheduler)).

Tab "Status":
- Job list con next-run countdown (cadence helper in `cadence.py`)
- Last run timestamp + duration + outcome (ok/error)
- "Run now" buttons per job (= `scheduler job <name>`)

Tab "History":
- Storico esecuzioni ultimi 7gg
- Filter per job name + status

Tab "Alerts":
- Coda alert pending con severity / ticker / message / age
- Bulk ack / single ack
- Stats: delivered / muted / failed last 24h

---

## Convenzioni dashboard

- **State**: tutto su DB. Streamlit refresh non perde stato.
- **Caching**: `_shared.py` usa `@st.cache_data` con TTL allineato ai TTL del market cache (8h daily). Click "Refresh" cancella la cache.
- **Tipi misti `float`/sentinel `"—"`**: `st.dataframe` con PyArrow fail su double misti → tutto serializzato a string omogenea (vedi `_shared.py::_safe_str`).
- **Performance**: per ETF rotation o discovery contrarian (universi ampi), usa lo spinner Streamlit; il batch scarica via cache (riusa daily se <8h).

---

## Quando usare CLI vs Dashboard

| Caso | Preferisci |
|------|------------|
| Esplorazione interattiva, "vorrei vedere se..." | Dashboard |
| Scripting, automation, cron | CLI |
| Generazione report markdown da committare | CLI (output file in `reports/`) |
| Discovery batch su 500+ ticker | CLI con `--json > out.json` (Streamlit ha re-run sull'input change) |
| Configurazione iniziale + setup | CLI (più verboso e debuggabile) |
| Demo a un collega o review weekly | Dashboard (visual) |

Entrambi vivono sopra lo **stesso DB e lo stesso domain layer** — non c'è
divergenza di stato. Solo l'UX cambia.
