# Workflow Operativi Day-by-Day

> **Per chi è**: trader retail che usa Propicks AI come supporto decisionale.
> Linguaggio semplice, zero matematica, step-by-step. Ogni use case dice
> esattamente **cosa cliccare in dashboard** e **cosa vedere in TradingView**.

Documento generato: **2026-04-30**.

Per teoria + metodologia tecnica vedi:
- [`USER_GUIDE.md`](USER_GUIDE.md) — guida generale
- [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md) — feature evolution

---

## 0. Prerequisiti (una tantum, 5 min)

**Setup minimo**:

```bash
# 1. Install (se non già fatto)
pip install -e ".[dashboard]"

# 2. Importa membership history S&P 500 (per backtest validi)
python scripts/import_sp500_history.py
# → 30 secondi. Crea 170k row in DB

# 3. Avvia dashboard
propicks-dashboard
# → apri http://localhost:8501
```

**TradingView setup** (una tantum):

1. Crea account TradingView gratis
2. Installa i 5 Pine script (Pine Editor → "New" → incolla contenuto da
   `tradingview/*.pine`):
   - `weekly_regime_engine` — quadro macro
   - `daily_signal_engine` — momentum stock
   - `daily_regime_composite` — regime daily (NEW)
   - `etf_rotation_engine` — sector ETF
   - `contrarian_signal_engine` — mean reversion

---

## 1. Routine giornaliera 5 minuti (mattina)

**Obiettivo**: capire stato mercato + check posizioni aperte.

### Dashboard

1. Apri dashboard → pagina default **"Portfolio Overview"** (root `app.py`)
2. **Guarda il regime corrente** (riquadro in alto):
   - 🟢 BULL/STRONG_BULL → puoi aprire long stock
   - ⚪ NEUTRAL → cauto, solo high-conviction
   - 🔴 BEAR/STRONG_BEAR → no long stock; considera defensive ETF
3. Apri **`13_Regime_Composite`** (NUOVA — Fase B.3):
   - Plot composite z-score giornaliero
   - Se composite era BULL ma sta scendendo verso NEUTRAL → segnale early-warning, riduci esposizione
   - Tipico lead time vs weekly: 1-3 settimane
4. Apri **`3_Portfolio`** → tab "Trade management":
   - Verifica posizioni con stop hit / target hit / time stop
   - Se vedi **"manage --apply"** suggerito, esegui via CLI: `propicks-portfolio manage --apply`

### TradingView

1. Apri chart `SPX` (o `^GSPC`)
2. Applica `weekly_regime_engine.pine` — vedi label corrente regime
3. Applica `daily_regime_composite.pine` (NEW pannello separato sotto):
   - Linea blu = composite z-score
   - Background colore = regime daily
   - Se daily diverge da weekly (es. weekly BULL, daily NEUTRAL) → caution
4. Per ogni stock in portfolio: chart daily + `daily_signal_engine.pine`,
   guarda lo score corrente. Se sceso sotto 60 → review

### Decisione 5 minuti

| Cosa vedi | Cosa fai |
|-----------|----------|
| Regime daily NEUTRAL/BEAR + posizioni in profit | Stringi stop |
| Regime daily STRONG_BEAR + nuovo segnale | NO entry, wait |
| Regime daily BULL stabile + score stock alto | Procedi normale |
| Composite z scende rapido (-0.5 in 3 giorni) | Defensive mode, riduci esposizione |

---

## 2. Use Case A — Titolo da Propicks AI esterno

**Scenario**: ricevi tip da Investing Pro Picks AI (sito esterno) — es. "AAPL
buy". Vuoi validare se accettarlo nel tuo sistema.

### Step-by-step dashboard

#### A.1 — Validazione score quant

1. Vai a **`1_Momentum`**
2. Form input:
   - Ticker: `AAPL`
   - Validate: ✓ ON
   - Force validate: ✗ OFF
3. Click **"Esegui scan"**
4. **Cosa guardare nel risultato**:
   - **Score composite** ≥ 60 → buon segnale tecnico
   - **Score Claude** ≥ 6 → AI conferma tesi
   - **Classification A o B** → entry valid
   - Se C/D → **rifiuta tip** (anche se Pro Picks dice buy)
5. **Stop suggerito** + **Target suggerito** mostrati a destra — annota
6. Se Classification A: click **"Aggiungi a watchlist"**

#### A.2 — Verifica regime

1. Vai a **`13_Regime_Composite`** (NEW)
2. Latest reading visibile in alto:
   - Se regime ≥ BULL → procedi
   - Se NEUTRAL → entry più prudente (size più piccolo)
   - Se BEAR/STRONG_BEAR → **rifiuta entry** anche se score alto

#### A.3 — Position sizing

1. Vai a **`3_Portfolio`** → tab "Sizing"
2. Form: ticker `AAPL`, entry da score, stop da score, score Claude
3. Vedi **size suggerita** (% capitale)
4. Click **"Aggiungi posizione"**

#### A.4 — TradingView visual check

1. Apri chart `AAPL` daily
2. Applica `daily_signal_engine.pine`
3. **Cosa guardare**:
   - Background colore: verde/giallo = OK; rosso = NO
   - Score box (alto destra): deve corrispondere a quello dashboard
   - Linee EMA 20/50: prezzo sopra entrambe = bull trend
4. Cambia chart a weekly, applica `weekly_regime_engine.pine`:
   - Verifica trend weekly stock allineato
5. **Decisione finale**:
   - ✅ Tutti verdi (regime + score + visual) → entry
   - ⚠ Discrepanze → skip, attendi conferma

---

## 3. Use Case B — Titolo da discovery momentum

**Scenario**: vuoi scoprire NUOVI candidati momentum nel S&P 500 oggi.

### Step-by-step dashboard

#### B.1 — Discovery con feature nuove

1. Vai a **`1_Momentum`**
2. Spunta **"Discover S&P 500"**
3. Top N: `30` (default)
4. Min score: `70` (più alto = meno candidati ma migliore qualità)
5. Click **"Esegui discovery"**
6. **Cosa guardare nei risultati**:
   - Tabella ordinata per score desc
   - Colonna **Classification**: scegli solo A/B
   - Colonna **Sector**: per diversificazione, non scegliere 3 ticker stessi sector

#### B.2 — Validazione threshold ottimo (NEW Fase A.2)

1. Vai a **`12_Calibration`** (NEW)
2. Form:
   - Discover SP500: ✓ ON
   - Top N: `30`
   - Threshold spec: `60:80:5`
   - Membership filter: ✓ ON
   - Use CPCV: ✗ OFF (più rapido) o ✓ ON (più rigoroso)
3. Click **"Esegui calibration"**
4. **Cosa guardare**:
   - Riga con **★** = threshold raccomandato
   - Se PSR ≥ 0.95 e DSR ≥ 0.85 → strategia robusta
   - Se DSR < 0.5 → edge non statisticamente significativo, **non usare in discovery**

#### B.3 — Backtest survivorship-correct (NEW Fase A.1)

1. Vai a **`11_Backtest_Portfolio`**
2. Form:
   - Tickers: top 5-10 dalla discovery (B.1)
   - Period: `5y`
   - Threshold: usa quello suggerito da B.2
   - **🛡️ Survivorship-correct** ✓ ON (NEW)
   - **🎯 Cross-sectional rank** ✓ ON (NEW) — threshold ora è percentile
3. Click **"Esegui backtest"**
4. **Cosa guardare**:
   - Sharpe annualized > 0.5 → buona strategia
   - Max DD < 20% → controllo rischio OK
   - PSR > 0.95 → confidence alta

#### B.4 — Position sizing + entry

1. Per ogni ticker A/B selezionato:
   - **`3_Portfolio`** → tab "Sizing"
   - Inserisci entry, stop, score
   - Sizing suggerito rispetta cap 15% per stock
2. Aggiungi posizione

### TradingView visual check

1. Per ogni candidato:
   - Chart daily + `daily_signal_engine.pine`
   - Verifica score visibile = quello da dashboard (sanity check)
   - **Toggle "Use multi-lookback momentum"** (NEW Fase C.6) per check robustness:
     - Se score crolla con multi-lookback ON → momentum debole, skip
     - Se score resta alto → momentum solido
2. Chart weekly + `weekly_regime_engine.pine`:
   - Trend weekly bull = procedi
   - Trend weekly cambio = caution
3. Apri `daily_regime_composite.pine` (NEW):
   - Composite z BULL = entry favorevole
   - Composite z NEUTRAL = considera size più piccolo

---

## 4. Use Case C — Titolo contrarian (mean reversion)

**Scenario**: cerchi stock di qualità in oversold temporaneo.

### Step-by-step dashboard

#### C.1 — Discovery contrarian

1. Vai a **`8_Contrarian`**
2. Spunta **"Discover S&P 500"**
3. Min score: `60`
4. Click **"Esegui discovery"**
5. **Cosa guardare**:
   - Score composite alto = oversold + quality
   - **Quality gate** ✓ in result → stock è above EMA200d (trend long-term intatto)
   - Se quality gate ✗ → **falling knife rischio**, skip

#### C.2 — Verifica regime (CRITICO contrarian)

1. Vai a **`13_Regime_Composite`** (NEW)
2. Contrarian funziona meglio in **NEUTRAL** o **BEAR moderato** (recovery setup):
   - **STRONG_BULL** → contrarian non utile, skip (mercato non oversold)
   - **STRONG_BEAR** → falling knife rischio, skip (anche se score alto)
   - **NEUTRAL** o **BEAR** → setup ideale
3. Daily composite più affidabile del weekly per timing

#### C.3 — Validazione AI (importante contrarian)

1. Nel risultato discovery, click ticker desiderato
2. Pagina dettaglio: **Validate** → spunta ✓
3. Click **"Esegui"**
4. **Cosa guardare AI verdict**:
   - **CONFIRM** → tesi recovery solida, entry OK
   - **CAUTION** → potenziale catalyst negativo, size ridotto
   - **REJECT** → AI vede red flags, **skip**

#### C.4 — Position sizing contrarian

1. **`3_Portfolio`** → tab "Sizing"
2. Spunta **"Contrarian"** (cap 8% invece di 15%)
3. Stop = recent low − 1×ATR (auto-suggested)
4. Target = entry + R/R 1:2 minimo
5. **Importante**: contrarian **NO trailing stop** (target fisso, vedi
   invariant in CLAUDE.md)

### TradingView visual check

1. Chart daily + `contrarian_signal_engine.pine`
2. **Cosa guardare**:
   - **Quality gate verde** (label box) → stock above EMA 200d weekly
   - **Setup READY** alert → oversold + reversal pattern (hammer, key reversal)
   - **Score components**: oversold + quality + context + reversion = composite
3. Chart weekly + `weekly_regime_engine.pine`:
   - Verifica trend long-term intatto (no breakdown weekly)
4. **Decisione**:
   - Quality gate verde + score ≥ 70 + AI CONFIRM → entry
   - Quality gate rosso → skip (falling knife)
   - Score basso ma AI CONFIRM → wait better setup

---

## 5. Use Case D — ETF Rotation (sector momentum)

**Scenario**: scegli i 2-3 sector ETF migliori della settimana.

### Step-by-step dashboard

#### D.1 — Rotation scan

1. Vai a **`2_ETF_Rotation`**
2. Region: `US` (o EU/WORLD)
3. Top: `5` (vedi i top 5 settori)
4. Click **"Esegui rotation"**
5. **Cosa guardare nei risultati**:
   - Tabella top sector per **score_composite**
   - Sub-score: RS (relative strength), regime fit, momentum, trend
   - Classification A/B → tradabile

#### D.2 — Allocation

1. Spunta **"Allocate"** + click "Run"
2. Vedi posizioni suggerite con peso %
3. **Cosa guardare**:
   - In **STRONG_BEAR**: allocation vuota di default (flat)
   - **NEW Fase C.7**: se attivo `enable_defensive_switch` (in CLI/code), in
     STRONG_BEAR allocazione automatica IEF + GLD + XLU + XLP (40% capital)

#### D.3 — Verifica macro overlay (NEW Fase B.5)

1. Vai a **`13_Regime_Composite`**:
   - Latest reading: composite z + sub-features (HY OAS, breadth, VIX)
2. Per inferire macro fit settori, usa CLI standalone:
   ```bash
   python scripts/test_macro_overlay.py --start 2024-01-01
   ```
3. **Cosa guardare**:
   - Sector ETF macro_fit alto (es. XLE 73, XLF 45) → conferma signal
   - Se sector tech ma macro_fit basso → caution (yield curve flat = headwind)

#### D.4 — Position sizing ETF

1. **`3_Portfolio`** → tab "Sizing"
2. Per ETF: cap 20% per single ETF, max 60% aggregate
3. Stop = −5% hard (gli ETF hanno vol bassa, no ATR-based)
4. Aggiungi posizione

### TradingView visual check

1. Per ogni ETF top (es. XLK):
   - Chart **WEEKLY** (timeframe nativo ETF rotation)
   - Applica `etf_rotation_engine.pine`
   - **Configura nel form**:
     - Benchmark: `SPX` (US) o `URTH` (WORLD)
     - Sector key: `technology` (o quello giusto)
2. **Cosa guardare**:
   - Score box (alto destra): conferma quello dashboard
   - Trend weekly EMA: prezzo sopra EMA 30w = bull
   - **Note B.5/C.7** (commenti header): ricorda che macro overlay è in
     dashboard/script Python, non in Pine real-time
3. Chart `SPX` weekly + `weekly_regime_engine.pine`:
   - Regime BULL/NEUTRAL → ETF rotation procede
   - STRONG_BEAR → considera defensive switch

---

## 6. Routine settimanale 30 minuti (Sabato sera)

**Obiettivo**: review settimana + plan prossima.

### Step-by-step

1. **Backtest re-validation** (mensile, opzionale):
   - **`12_Calibration`** (NEW) — verifica threshold ottimo non drifted
   - Se DSR scende sotto 0.85 → considera review parametri

2. **Decay monitor** (NEW Fase D.4):
   - **`14_Decay_Monitor`** (NEW)
   - Strategy filter: `momentum` (poi `contrarian`)
   - Expected Sharpe: 0.20 default
   - Click "Run decay analysis"
   - **Cosa guardare**:
     - 🟢 ALIVE → strategia funziona
     - ⚪ MONITOR → niente di anomalo
     - 🟡 WARNING → rolling Sharpe basso, attento
     - 🔴 ALERT_DECAY → **pause + review**, edge potenzialmente morto

3. **Journal review**:
   - **`4_Journal`** → tab "Stats"
   - Win rate, profit factor, avg duration
   - Trade peggiori → leggi notes, lezioni

4. **Reports**:
   - **`5_Reports`** → genera weekly/monthly
   - Markdown salvato in `reports/`

5. **Watchlist clean-up**:
   - **`7_Watchlist`** → "Stale" tab
   - Rimuovi ticker obsoleti (>30 giorni senza azione)

6. **Plan settimana prossima**:
   - Discovery momentum + contrarian per nuovi candidati
   - ETF rotation per esposizione settoriale
   - Annota in journal

---

## 7. Cosa fare se ricevi ALERT

### ALERT regime change (Telegram bot)

**Scenario**: bot manda "Regime daily switched to STRONG_BEAR".

**Azione immediata**:
1. Apri `13_Regime_Composite` per conferma + componenti (HY OAS spike?)
2. **Stop nuove entry long**
3. Apri `3_Portfolio` → tab "Trade management":
   - Stringi stop su tutte le posizioni open
   - Considera close partial trade in profit
4. Per ETF rotation con `enable_defensive_switch`: rotazione automatica
   verso IEF/GLD/XLU/XLP

### ALERT decay monitor

**Scenario**: `14_Decay_Monitor` mostra ALERT_DECAY su momentum.

**Azione**:
1. **Non aprire nuove posizioni momentum** finché decay confirmed/refuted
2. Verifica con scenario replay: `python scripts/scenario_replay.py`
3. Re-calibrate threshold: `12_Calibration` con nuovi dati
4. Se decay confirmed: pause strategia momentum 1 mese, aspetta normalize

### ALERT score upgrade A (TradingView)

**Scenario**: alert "SCORE UPGRADE A" su un ticker watchlist.

**Azione**:
1. Apri `1_Momentum` con quel ticker → Validate ON
2. Se Classification A confermata + regime ≥ NEUTRAL → entry
3. Position sizing → aggiungi posizione

---

## 8. Glossario rapido (per principianti)

| Termine | Significato semplice |
|---------|---------------------|
| Score composite | Numero 0-100 che riassume "quanto buono è questo titolo ora" |
| Regime | Stato del mercato: BULL (sale), BEAR (scende), NEUTRAL (laterale) |
| Stop loss | Prezzo a cui esci automaticamente se va contro |
| Target | Prezzo obiettivo per chiudere in profit |
| ATR | Volatilità tipica giornaliera del titolo |
| Sharpe ratio | Quanto guadagno per unità di rischio (più alto = meglio) |
| PSR | Probabilità che la strategia abbia edge reale (>0.95 = high confidence) |
| DSR | PSR corretto per multiple testing (>0.95 = robust strategy) |
| Cross-sectional | Confronto tra titoli (top 20% del momento) invece di soglia fissa |
| Survivorship bias | Errore di backtest che ignora titoli falliti — Fase A.1 fix |
| Look-ahead bias | Errore: sapere il futuro per filtrare il passato |
| Decay | Strategia che ha perso edge nel tempo |
| Drawdown | Caduta da picco a minimo dell'equity curve |

---

## 9. Cheatsheet final — quando usare cosa

| Situazione | Strumento principale | Cosa cliccare |
|------------|---------------------|---------------|
| Validare tip esterno (Pro Picks AI) | Dashboard `1_Momentum` --validate | Scan ticker + AI verdict |
| Trovare nuovi momentum | Dashboard `1_Momentum` discover | Discover SP500 + threshold da `12_Calibration` |
| Trovare nuovi contrarian | Dashboard `8_Contrarian` discover | Verifica regime + AI verdict |
| Sector rotation | Dashboard `2_ETF_Rotation` | Scan top sector + macro check |
| Backtest robust | Dashboard `11_Backtest_Portfolio` | Membership + Cross-sectional ON |
| Calibrare threshold | Dashboard `12_Calibration` (NEW) | Threshold sweep + DSR |
| Check regime daily | Dashboard `13_Regime_Composite` (NEW) | Latest z + plot |
| Check decay strategia | Dashboard `14_Decay_Monitor` (NEW) | Run analysis |
| Position sizing | Dashboard `3_Portfolio` → tab Sizing | Bucket cap auto |
| Trade management | Dashboard `3_Portfolio` → tab Management | manage --apply |
| Visual check timing | TradingView Pine scripts | Chart daily/weekly |

---

## 10. Note sicurezza & disclaimer

- **Trading reale = rischio reale**. Sistema è supporto decisionale, non
  garanzia profitto.
- **Backtest = passato**. Edge storico non garantisce edge futuro.
- **Look-ahead caveat** su Fase B.2 (earnings revision) e B.4 (quality):
  numeri backtest **inflati**, feature utili solo live mode.
- **Decision rule strict** (DSR p < 0.10) raramente passa: edge OOS
  realistic stimato 0.40-0.60 Sharpe lordo.
- **No live broker integration** — esecuzione manuale via broker proprio.
- **Backup DB regolari**: `cp data/propicks.db data/backup_$(date +%F).db`

Per dettagli tecnici + caveat completi: [`SIGNAL_ROADMAP.md`](SIGNAL_ROADMAP.md).
