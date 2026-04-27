# Risk Framework v2 — Advanced Sizing Layer

> Strato di sizing avanzato (Phase 5) che **estende** il sizing classico con 4
> risk metrics matematici. **Principio di sicurezza inviolabile**: i hard cap
> (8% contrarian, 15% momentum, 20% ETF, 20% bucket cap, 80% max cash, 8/12%
> max loss) **sempre vincono**. Il layer v2 può solo **scalare down**, mai up.

---

## 1. Le 4 metriche

### 1.1 Kelly fractional 25%
- Da journal storico trade chiusi.
- Formula: `f* = (p×b − q)/b`, poi ×0.25 (quarter Kelly, industry retail).
- Safety floor: cap a **20%** anche con fractional (`KELLY_MAX`).
- Richiede ≥ **15 trade chiusi** per strategia → sotto soglia = "non usabile".
- Degenerate sample (tutti win o tutti loss) = "non usabile".

### 1.2 Portfolio vol annualized
- `σ = sqrt(w'Σw) × sqrt(252)`.
- Covariance da 6 mesi daily returns (cache Phase 2).
- Display + usato per vol target scaling.

### 1.3 Portfolio VaR 95%
- Bootstrap su 6mo daily returns.
- 500 simulazioni, horizon configurabile (default 5gg).
- VaR + Expected Shortfall + worst case osservato.
- **Display-only**, zero hard gate (info per decisione trader).

### 1.4 Correlation penalty
- Scala down se nuovo ticker correlato ≥ 0.7 con esistenti.
- `effective_exposure = sum(weight_i × corr(new, i))` per corr ≥ threshold.
- `scale_factor = max(0, 1 − effective × penalty_factor)` (default 0.5).
- Previene "diversificazione camuffata" (AAPL+MSFT+GOOGL = 1 scommessa tech 3x).

---

## 2. Flow sizing advanced

```
propicks-portfolio size AAPL --entry 180 --stop 168 --advanced
    │
    ▼
[1] calculate_position_size (hard caps + MIN_CASH + risk-per-trade)
    ▼ base_size_pct, base_shares
[2] strategy_kelly_from_trades (se use_kelly + journal ≥ 15 trade)
    ▼ Kelly < base? → scale down. Kelly > base? → ignora (safety).
[3] portfolio_vol_annualized + vol_target_scale (se use_vol_target)
    ▼ Vol corrente > target? → scale down. Vol < target? → ignora (safety).
[4] apply_correlation_penalty (se corr_matrix + new_ticker)
    ▼ Corr ≥ 0.7 con esistenti? → scale down proporzionale.
[5] final_shares = min(final_shares, base_shares)  ← safety hardstop
```

**Safety invariant (verified by test):** `final_shares ≤ base_shares` sempre.

---

## 3. CLI

```bash
propicks-portfolio size AAPL --entry 180 --stop 168 \
  --score-claude 7 --score-tech 75 --advanced \
  --strategy-name TechTitans --vol-target 0.12
```

Output con **breakdown completo**:
- Base sizing (shares, size%, conviction, source)
- Kelly (n_trades, win_rate, W/L ratio, kelly_pct) o reason if non usable
- Current portfolio vol + scale factor vs target
- Correlation penalty (pairs, effective exposure, scale factor)
- Binding constraint: **quale** factor ha limitato la size finale

---

## 4. Dashboard

Tab "Rischio & esposizione" di `3_Portfolio.py`:
- **Vol annualized**, **VaR 95% (5gg)**, **Expected Shortfall**, **Worst case** come metric card
- **Kelly per strategia** table: n_trades, win rate, W/L ratio, kelly %, usable status
- Legenda integrata nell'expander esistente

Tutte derivate dalla cache Phase 2 — zero chiamate yfinance extra quando il
portfolio è già caricato.

---

## 5. Config defaults

```python
# domain/risk.py
MIN_TRADES_FOR_KELLY = 15        # sotto → skip Kelly (sample insufficient)
KELLY_FRACTION_DEFAULT = 0.25    # quarter Kelly
KELLY_MAX = 0.20                 # safety cap anche con fractional
TRADING_DAYS_PER_YEAR = 252

# domain/sizing_v2.py
DEFAULT_TARGET_VOL_ANNUALIZED = 0.15  # 15% target (retail conservative)
```

Override via CLI: `--vol-target 0.10` (più conservativo).

---

## 6. Decisioni di design

### 6.1 Perché Kelly fractional e non full
- **Input stimati, non noti**: P(win) e W/L sono stime storiche con sample basso.
  Full Kelly su input stimati è historicamente disastroso (volatility blowup).
- **25% "quarter Kelly"** è industry standard retail (Thorp, Vince, MAN AHL).
- **Cap 20% assoluto**: anche con fractional, mai oltre 20% su singola posizione.

### 6.2 Perché vol target scaling solo DOWN
- Scaling **up** via vol target richiederebbe assumption che il target vol è
  *raggiungibile* — se il portfolio è già poco volatile, aumentare le posizioni
  per "riempire" il budget di vol è ottimizzazione aggressiva.
- Per retail: **safety-first**, scale down OK, scale up mai. Il trader decide
  manualmente se aumentare esposizione quando vol < target.

---

## 7. Trade-off accettati

- **Beta statico** per VaR/vol: usiamo beta da `market_ticker_meta` (TTL 7gg).
  Se il titolo ha cambiato profile recentemente, VaR può sottostimare.
- **Correlation 6mo fissa**: non rolling — 6mo è sweet spot tra realismo e
  stabilità delle stime.
- **Kelly non regime-aware**: un trade in BEAR non usa Kelly stimato su BULL.
  Miglioramento futuro: Kelly condizionale a regime corrente.
- **Corr penalty lineare**: `1 − eff × penalty_factor`. Su correlazioni
  multiple compound, il penalty si somma linearmente. Non riflette correttamente
  la "saturazione". Accettabile per MVP.
- **Nessun drawdown scaling dinamico**: Vegas-style "scale up after losses" o
  "scale down after wins" NON implementati — out of scope Phase 5.
