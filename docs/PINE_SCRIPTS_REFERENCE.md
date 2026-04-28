# Pine Scripts Reference — TradingView Layer

I 4 Pine script in `tradingview/` replicano visualmente il motore Python sul
chart TradingView in **real-time**. Questo è il layer che yfinance (EOD) non
copre: timing, alert push, scoring intra-day.

> **Contratto Pine ↔ Python**: i default Pine devono matchare `src/propicks/config.py`
> byte-per-byte. Se modifichi una soglia in Python, aggiornare anche il Pine
> (e viceversa). Le sezioni "Contratto" di ogni script lo dichiarano esplicitamente.

---

## I 4 script

| Script | Timeframe | Replica del Python | Scopo |
|--------|-----------|--------------------|-------|
| [weekly_regime_engine](#weekly_regime_engine) | Weekly | `domain/regime.py` | Classificazione macro 5-bucket (regime gate) |
| [daily_signal_engine](#daily_signal_engine) | Daily | `domain/scoring.py` | Scoring momentum stock + alert breakout/pullback |
| [etf_rotation_engine](#etf_rotation_engine) | Weekly (preferito) | `domain/etf_scoring.py` | Sector ETF rotation (RS + regime fit) |
| [contrarian_signal_engine](#contrarian_signal_engine) | Daily | `domain/contrarian_scoring.py` | Quality-filtered mean reversion |

---

## Setup TradingView

1. Apri TradingView → chart del simbolo target.
2. Pine Editor (in basso): "Open" → "Pine Script™".
3. Incolla il contenuto del file `.pine` corrispondente.
4. "Save" → assegna nome (es. "Propicks Daily").
5. "Add to chart" → l'indicatore appare overlay sui prezzi + pannello score in alto a destra.
6. **Configura gli alert**: tasto destro → "Add Alert on indicator" → seleziona la condition (es. "BREAKOUT", "CONTRA SETUP READY").

**Importante**: ogni script ha **timeframe nativo**. Su un timeframe diverso i
calcoli sballano (ATR/EMA periodi differenti). Il contrarian e il daily hanno
runtime check (`runtime.error`) se non sei su daily.

---

## weekly_regime_engine

**Scopo**: classifica il regime macro a 5 bucket (STRONG_BULL / BULL / NEUTRAL /
BEAR / STRONG_BEAR) — mirror esatto di `domain/regime.py`.

**Quando usarlo**:
- Aprilo su `^GSPC` (S&P 500) per leggere il regime macro che il motore
  Python applica a TUTTE le strategie. Non aprirlo su un singolo titolo
  aspettandoti il regime macro globale.
- Per Pro Picks europee, applicalo separatamente a `^STOXX50E` per un
  cross-check, ma il framework lo ignora.

**Default chiave**:

| Input | Default | Mirror Python |
|-------|---------|---------------|
| EMA Fast (weekly) | 10 | `REGIME_WEEKLY_EMA_FAST` |
| EMA Slow (weekly) | 30 | `REGIME_WEEKLY_EMA_SLOW` |
| EMA 200d-equiv (weekly) | 40 | `REGIME_WEEKLY_EMA_200D` |
| RSI period | 14 | `RSI_PERIOD` |
| ADX period | 14 | `REGIME_ADX_PERIOD` |
| ADX strong | 25 | `REGIME_ADX_STRONG` |

**Logica bucket** (mirror Python post-fix MED-2):

| Code | Label | Condizione |
|------|-------|-----------|
| 5 | STRONG_BULL | `above_all AND trend_bull AND ADX>25 AND momentum_bull` |
| 4 | BULL | `trend_bull AND momentum_bull` |
| 3 | NEUTRAL | tutto il resto |
| 2 | BEAR | `trend_bear AND momentum_bear` |
| 1 | STRONG_BEAR | `below_all AND trend_bear AND ADX>25 AND momentum_bear` |

**Alert disponibili**:
- `REGIME UPGRADE` / `DOWNGRADE` (cambio bucket)
- `REGIME BULLISH` (≥4) / `REGIME BEARISH` (≤2)
- `WEEKLY GOLDEN/DEATH CROSS`
- `NUOVO 52w HIGH`
- `SOTTO EMA 200d` (cross weekly EMA40)

---

## daily_signal_engine

**Scopo**: scoring momentum 0-100 + segnali entry/exit operativi — mirror di
`domain/scoring.py`.

**Quando usarlo**:
- Su ogni stock della watchlist Pro Picks dopo l'output di `propicks-momentum`.
- Configura gli `Entry Price`, `Stop Loss Price`, `Target Price` (sezione
  "Position") con i valori dal blocco copiato dalla CLI per attivare gli
  alert posizionali.

**Default chiave**:

| Input | Default | Mirror Python |
|-------|---------|---------------|
| EMA Fast | 20 | `EMA_FAST` |
| EMA Slow | 50 | `EMA_SLOW` |
| RSI period | 14 | `RSI_PERIOD` |
| ATR period | 14 | `ATR_PERIOD` |
| Volume MA period | 20 | `VOLUME_AVG_PERIOD` |
| Volume Spike Mult | 1.5 | `VOLUME_SPIKE_MULTIPLIER` |
| ATR Stop Mult | 2.0 | (stop = price - 2×ATR) |

**Sub-score** (pesi mirror Python):
- Trend 25% — close vs EMA fast/slow
- Momentum 20% — RSI bucket
- Volume 15% — **asimmetrico up/down day** (post-SERIO-2)
- Distance from 52wH 15% — **piecewise lineare** smooth peak a -7.5% (post-SERIO-1)
- Volatility 10% — ATR%
- MA cross 15% — golden/death + spread

**Alert principali**:
- `BREAKOUT` (close > EMA slow + volume spike)
- `PULLBACK su EMA` (touch EMA fast in uptrend)
- `GOLDEN CROSS` / `DEATH CROSS`
- `VOLUME SPIKE` (≥ extreme threshold)
- `SQUEEZE BREAKOUT` (BB squeeze + breakout)
- `RSI DIVERGENZA BULLISH`
- `STOP LOSS HIT` / `TARGET HIT` (richiede position fields configurati)
- `SCORE UPGRADE A` (composite cross sopra 75)

---

## etf_rotation_engine

**Scopo**: scoring rotazione settoriale ETF — mirror di `domain/etf_scoring.py`.

**Quando usarlo**:
- Su ogni ETF dell'universo (`XLK`, `XLF`, `ZPDT.DE`, `XDWT.DE`, `IQQ6.DE`, ...).
- Timeframe **weekly preferito** (lo scoring è basato su RS weekly).

**Setup obbligatorio**:
1. **Benchmark Symbol**: `SPX` per US/EU, `URTH` per WORLD.
2. **Sector Key**: scegli il GICS sector (technology / financials / energy / ...). Senza, il regime_fit ricade a 50 (neutro) e perdi il match con Python.

**Default chiave**:

| Input | Default | Mirror Python |
|-------|---------|---------------|
| RS Lookback (weeks) | 26 | `ETF_RS_LOOKBACK_WEEKS` |
| RS Slope window (weeks) | 10 | `ETF_RS_EMA_WEEKS` |
| Momentum lookback (days) | 63 | `ETF_MOMENTUM_LOOKBACK_DAYS` |
| EMA Slow weekly | 30 | `REGIME_WEEKLY_EMA_SLOW` |
| ADX strong | 25 | `REGIME_ADX_STRONG` |
| ETF stop % | 5.0 | `ETF_STOP_LOSS_PCT` |
| Weight RS / RegFit / AbsMom / Trend | 0.40/0.30/0.20/0.10 | `ETF_WEIGHT_*` |

**Sub-score**:
- **RS** 40% — vera slope settimanale `(rs_norm[-1] − rs_norm[-N])/N` (post-CRIT-3, non spread vs EMA)
- **Regime fit** 30% — lookup `REGIME_FAVORED_SECTORS` per il bucket regime corrente
- **Abs momentum** 20% — perf 3M
- **Trend** 10% — close vs EMA30 weekly + slope EMA su 4 weeks

**Cap regime** (mirror `apply_regime_cap` post-CRIT-1):
- STRONG_BEAR non-favored → 0
- BEAR non-favored → cap 50
- NEUTRAL non-favored → cap 65 (soft cap)
- BULL+ → no cap

Marker `*` accanto allo score quando il cap è attivo.

**Alert**:
- `ETF UPGRADE A` (composite cross ≥70)
- `ETF DOWNGRADE D` (composite cross <40)
- `ETF STOP -5%`
- `ETF RS CROSS UP` / `DOWN` (rs_ratio attraversa 1.0)
- `ETF REGIME CHANGE` (cambio macro)

---

## contrarian_signal_engine

**Scopo**: scoring quality-filtered mean reversion — mirror di
`domain/contrarian_scoring.py`. **Long-only**, NON è uno short engine.

**Quando usarlo**:
- Su single stock dopo `propicks-contra` o discovery batch.
- Timeframe **DAILY obbligatorio** — il Pine emette `runtime.error` se non daily.

**Default chiave**:

| Input | Default | Mirror Python |
|-------|---------|---------------|
| EMA 200d-equiv (weekly, quality) | 40 | `REGIME_WEEKLY_EMA_200D` |
| RSI Oversold strict | 30 | `CONTRA_RSI_OVERSOLD` |
| RSI Warm | 35 | `CONTRA_RSI_WARM` |
| EMA Slow (target) | 50 | `EMA_SLOW` / `CONTRA_TARGET_EMA_PERIOD` |
| Min ATR distance from EMA50 | 2.0 | `CONTRA_ATR_DISTANCE_MIN` |
| Min consecutive down days | 3 | `CONTRA_CONSECUTIVE_DOWN_DAYS` |
| VIX Spike | 25 | `CONTRA_VIX_SPIKE` |
| VIX Complacent | 14 | `CONTRA_VIX_COMPLACENT` |
| Stop ATR mult | 1.0 | `CONTRA_STOP_ATR_MULT` |
| Time stop days | 15 | `CONTRA_TIME_STOP_DAYS` |
| Weight Oversold / Quality / Mkt / Reversion | 0.40/0.25/0.20/0.15 | `CONTRA_WEIGHT_*` |

**Sub-score**:
- **Oversold** 40% — RSI 0-40pt + ATR distance 0-40pt + capitulation 0-20pt (max di drawdown_5d_atr e consecutive_down)
- **Quality** 25% — **HARD GATE**: price < EMA40 weekly → score 0 → composite 0 (falling knife filter). Sopra: bonus profondità correzione (peak a -10%/-25%).
- **Market context** 20% — `CONTRA_REGIME_FIT` lookup inverso ({5:25, 4:70, 3:100, 2:85, 1:0}) + VIX adjustment (≥25 → +20, ≤14 → −30).
- **Reversion** 15% — R/R teorico (target=EMA50, stop=recent_low−1×ATR).

**Hard gates**:
1. Quality = 0 → composite forzato a 0 (richiede `aboveEma200w`).
2. Regime cap STRONG_BULL/STRONG_BEAR (1 o 5) → composite forzato a 0.

**Marker on-chart**:
- `SETUP` (triangolo verde) — composite ≥75 + quality intact + RSI ≤35 + ATR distance ≥2
- `incub` (cerchio giallo) — composite 60-74 + quality intact
- `REV` (triangolo blu) — cross sopra EMA50 con RSI<50 (possibile inizio reversion)
- `BROKEN` (X rosso) — cross sotto EMA40 weekly (quality gate broken)

**Alert**:
- `CONTRA SETUP READY` (transizione a setupReady)
- `CONTRA INCUBATING`
- `CONTRA REVERSION` (cross EMA50 dal basso con RSI<50)
- `CONTRA QUALITY BROKEN` (cross under EMA40w)
- `CONTRA TIME STOP` (15gg dal setup)
- `CONTRA STOP HIT`

**Pannello score** (top-right):
- Composite + class
- Quality gate INTACT/BROKEN
- Regime label (NEUTRAL=sweet, STRONG_BULL/BEAR=skip)
- 4 sub-score
- RSI / ATR distance / VIX / R/R / Stop-Target levels

---

## Note sui contratti Pine ↔ Python

**Cosa garantisce il mirror byte-byte**:
- I default degli input Pine sono identici alle costanti `config.py`.
- Le formule di tutti i sub-score sono trascritte una a una.
- Hard gates (quality, regime cap) replicati con la stessa precedence.

**Cosa diverge per natura del medium**:
- Il Pine valuta in real-time (live bar partial); Python valuta su EOD daily.
  Durante la sessione, il Pine può avere RSI/ATR che oscillano; Python è
  stabile fino al close del giorno.
- Pine `request.security` con `lookahead=barmerge.lookahead_off` è il pattern
  corretto per matchare yfinance nel time-of-evaluation. **Mai usare
  `lookahead_on`** (introduce future leak).

**Quando il Pine NON matcha Python**:
1. Verifica timeframe (daily per momentum/contrarian, weekly per ETF rotation/regime).
2. Verifica i default input non siano stati modificati.
3. Per il regime, verifica che il sector key (ETF rotation) sia impostato.
4. Per il contrarian, verifica VIX symbol risolva (default `CBOE:VIX`).
5. Vedi [FAQ_AND_TROUBLESHOOTING](FAQ_AND_TROUBLESHOOTING.md) sezione "Pine vs Python drift".

---

## Esempio workflow giornaliero

```
06:00 — propicks-scheduler ha già fatto warm cache + scan EOD
08:00 — review CLI: propicks-momentum AAPL (output A — 78/100)
        Output mostra blocco "TradingView Pine inputs":
            Entry: 185.50, Stop: 177.90, Target: 220.00
08:05 — TradingView, chart AAPL daily:
        - daily_signal_engine.pine già in chart
        - Settings → Position: incolla 185.50 / 177.90 / 220.00
        - Salva, alert "TARGET HIT" e "STOP LOSS HIT" attivati
08:30 — Decision: enter o wait pullback. Pine alert via mobile = timing layer.
EOD — propicks-portfolio manage --apply (trailing stop autoupdate)
```
