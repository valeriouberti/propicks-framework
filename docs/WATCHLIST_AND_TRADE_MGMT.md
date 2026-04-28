# Watchlist + Trade Management + Esposizione

> Tre layer paralleli di domain logic operativa post-entry: incubatrice idee
> (watchlist), gestione di trade in vita (trailing/time/target), misure di
> rischio aggregato (sector/beta/correlations).

---

## 1. Watchlist — incubatrice idee tra scan e entry

`io/watchlist_store.py` è il parallelo di `portfolio_store.py` ma con semantica
diversa: la watchlist **non impegna capitale**, non ha regole di sizing, non
blocca l'entry. È l'incubatrice dove i setup attendono il loro momento
(pullback, breakout, catalyst, rerating di regime).

### 1.1 Schema per entry

```json
{
  "AAPL": {
    "added_date": "2026-04-20",
    "target_entry": 185.50,
    "note": "pullback EMA20 post earnings beat",
    "score_at_add": 72.3,
    "regime_at_add": "BULL",
    "classification_at_add": "B — WATCHLIST",
    "source": "manual" | "auto_scan" | "auto_scan_contra"
  }
}
```

### 1.2 Auto-populate da `propicks-momentum` / `propicks-contra`

Lo scanner aggiunge automaticamente:
- **Classe A** (score ≥ 75, `"A — AZIONE IMMEDIATA"`)
- **Classe B** (60-74, `"B — WATCHLIST"`)

Con `source="auto_scan"` (momentum) o `"auto_scan_contra"` (contrarian) e
snapshot di score/regime/classification al momento dello scan.

### 1.3 Policy `target_entry`

- **Classe A nuove entry**: `target_entry = current_price` (distanza 0% →
  immediatamente READY al prossimo `status`). Un setup A è tradable *ora*.
- **Classe A entry esistenti con target già settato**: target preservato
  (non sovrascriviamo né input manuali del trader né target di scan
  precedenti quando il prezzo è salito).
- **Classe B**: senza target — il trader lo imposta manualmente quando
  individua il livello (pullback EMA20, breakout, catalyst date).
- **Classe C/D**: skip dell'auto-add (rumore). Restano disponibili via bottone
  manuale "→ Aggiungi a watchlist" nella dashboard Momentum.

Disabilitabile con `--no-watchlist`. La dashboard Momentum replica la stessa
policy A+B con toast di conferma + bottone manuale per ticker di qualunque
classe.

### 1.4 Ready signal

`propicks-watchlist status` / tab Attiva della dashboard:
- Score corrente ≥ 60 **E**
- `|current_price − target_entry| / target_entry ≤ 2%`

Un entry READY **non** apre la posizione automaticamente: è flag visivo che
invita a passare da `propicks-momentum` (re-analisi con regime + AI) e
`propicks-portfolio size/add` con sizing esplicito.

### 1.5 Dedup e stale handling

- **Dedup**: `add_to_watchlist` normalizza il ticker a uppercase. Se esiste
  già, aggiorna solo i campi non-None, preservando `added_date` e `source`.
- **Stale**: `is_stale(entry, days=60)` marca come stale le entry da più di 60
  giorni. La dashboard ha un tab dedicato con multi-select per pulizia in
  blocco. Rationale: se un setup non si è materializzato in 2 mesi,
  probabilmente la tesi era sbagliata o il regime è cambiato.

### 1.6 Schema legacy

`load_watchlist` migra automaticamente `{"tickers": []}` e
`{"tickers": ["AAPL", "MSFT"]}` (lista di stringhe) a dict con campi default.

---

## 2. Trade management — trailing + time stop + target hit

`domain/trade_mgmt.py` è puro: prende numeri/stringhe e ritorna dict di
suggerimenti. L'applicazione (update DB) è responsabilità della CLI
(`propicks-portfolio manage --apply`).

### 2.1 Trailing stop ATR-based, ratchet-up only

- Stop iniziale resta invariato finché `highest_price < entry + 1R`
  (1R = `entry − initial_stop`). Rationale: muovere lo stop troppo presto
  trasforma uno swing legittimo in stop-out rumoroso.
- Sopra soglia: `proposed = highest − atr_mult × current_atr` (default 2.0).
  Il nuovo stop è `max(current, proposed)` — **mai scende**.
- **Bloccato per contrarian**: `cmd_trail enable` rifiuta posizioni
  contrarian (strategia mean reversion usa target fisso, non trailing).

### 2.2 Time stop bucket-aware

Se trade flat (`|P&L%| < flat_threshold_pct`, default 2%) da almeno
`max_days_flat` giorni:
- **Momentum**: 30gg default (`DEFAULT_TIME_STOP_DAYS`)
- **Contrarian**: 15gg (`CONTRA_TIME_STOP_DAYS`) — auto-applicato in
  `suggest_stop_update` quando `is_contrarian_position(pos)`.

Override esplicito: `propicks-portfolio manage --time-stop N` rispetta il
valore custom anche per contrarian.

### 2.3 Target hit + dynamic target (per contrarian)

Per posizioni con campo `target` valorizzato:
- **Target hit detection**: `current_price ≥ target` → `target_hit_triggered=True`
  → `cmd_manage` flagga `TARGET-HIT` → trader chiude manualmente via
  `propicks-journal close`.
- **Dynamic target tracking** (solo contrarian): `cmd_manage` ricalcola EMA50
  daily corrente e lo passa come `dynamic_target`. Se drift > 0.5% rispetto al
  target persistito → `manage --apply` aggiorna il target nel DB.
- **Skip se trailing attivo**: il trailing manage il take profit, non il
  target statico.

### 2.4 Schema portfolio esteso (backward-compatible)

- `highest_price_since_entry: float | None` — tracking del massimo raggiunto
  post-entry, aggiornato a ogni `manage` run
- `trailing_enabled: bool` — opt-in esplicito tramite `propicks-portfolio
  trail enable <TICKER>`. Default OFF.
- `target: float | None` — take profit level (per contrarian: EMA50 daily,
  drift-tracked).

`suggest_stop_update(position, current_price, current_atr, ..., dynamic_target=None)`
ritorna:
```python
{
    "new_stop": float | None,
    "stop_changed": bool,
    "new_target": float | None,
    "target_changed": bool,
    "target_hit_triggered": bool,
    "time_stop_triggered": bool,
    "highest_price": float,
    "rationale": list[str],
}
```

### 2.5 Apply behavior

`manage --apply` scrive `stop_loss`, `target`, `highest_price`. Le posizioni
con `time_stop_triggered=True` o `target_hit_triggered=True` vanno chiuse
manualmente (l'engine non scrive il close per evitare chiusure accidentali —
il trader vede il flag e decide).

---

## 3. Esposizione aggregata — sector/beta/correlations

`domain/exposure.py` è puro: prende `positions` + dati esterni iniettati
(prezzi correnti, mappa sector, beta, returns DataFrame). I download yfinance
vivono nella CLI che chiama queste funzioni — coerente col pattern di
separazione dei layer.

### 3.1 Concentrazione settoriale (GICS)

`compute_sector_exposure` somma il % capitale per `sector_key` (mapping
Yahoo→interno via `domain.stock_rs.YF_SECTOR_TO_KEY`). Le regole single-name
cappano la posizione al 15%, ma due tech stock a 15% ciascuno = 30% effettivi
su technology. `compute_concentration_warnings` flagga sector > 30% (default
cap, opinabile). **Cash NON è incluso** (esposizione zero).

### 3.2 Beta-weighted gross long exposure

`compute_beta_weighted_exposure` calcola `sum(weight_i * beta_i)`. Misura la
sensibilità del portfolio al mercato (SPX): beta-weighted 0.78 con gross long
0.65 = portfolio 65% investito che si muove come il 78% di SPX (titoli più
volatili della media). Per ticker senza beta noto (IPO recenti, ETF, ticker
esteri illiquidi) usa `default_beta=1.0` e ne logga l'elenco.

### 3.3 Matrice correlazioni pairwise

`compute_correlation_matrix` su daily returns (default 6 mesi via
`download_returns`) + `find_correlated_pairs` estrae upper-triangle con
`|corr| ≥ 0.7`. Pair sopra soglia sono effettivamente la stessa scommessa
(rischio concentrato camuffato da diversificazione). Limit interno della CLI:
top 10 pair per non saturare l'output.

### 3.4 Robustezza

Tutte le funzioni gestiscono input degenere: `total_capital=0`, posizioni
senza prezzo corrente (DataUnavailable), beta None, correlazioni con
osservazioni < `min_observations` (default 30, ritorna None invece di una
matrice rumorosa).

---

## 4. Sync journal ↔ portfolio

I due store restano indipendenti (separation of concerns: journal è l'append-log
immutabile con tutte le meta di analisi, portfolio è lo stato corrente con cash
e shares), ma `propicks-journal add`/`close` e le dashboard form passano dal
**coordinator `io/trade_sync.py`** che scrive in entrambi.

### 4.1 Schema journal esteso

Campo `shares: int | None`. Obbligatorio via CLI (`--shares N`) e dashboard
(numeric input), `None` sui record legacy che non vengono migrati.

### 4.2 Policy di robustezza (nessun rollback magico)

- **Apertura** (`trade_sync.open_trade`): journal scritto per primo. Se
  `add_position` fallisce (cash insufficiente, size > 15%, stop > 8%, posizione
  già presente), il journal resta scritto con `warning` informativo. Il trade
  reale *è* aperto sul broker — il record deve esistere a prescindere da cosa
  dice il tracker. Correggi manualmente con `propicks-portfolio add`.
- **Chiusura** (`trade_sync.close_trade`): journal exit scritto per primo. Se
  la posizione non è nel portfolio (mai sincronizzata o già rimossa), il
  journal viene comunque chiuso. **P&L vive nel journal, non nel portfolio**.
- **Idempotenza**: se apri un trade e il portfolio ha già quel ticker (creato
  via `propicks-portfolio add` prima), il journal viene scritto ma il
  portfolio non duplicato.

### 4.3 Cash accounting fix

`portfolio_store.close_position(exit_price)` rimborsa `shares × exit_price`
(proventi reali dalla vendita). La vecchia `remove_position` rimborsa
`shares × entry_price` (undo di `add_position`) e serve solo per correggere
errori di data entry. **Usare `close_position` quando chiudi un trade reale
con P&L** — il coordinator lo fa già automaticamente.
