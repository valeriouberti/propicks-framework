# Catalyst Calendar — Earnings Hard Gate + Macro Events

> Phase 8: gate ticker-specific (earnings) + warning whole-market (macro events).
> Implementato come safety check pre-`add_position`.

---

## 1. Earnings hard gate

- **Soglia**: `EARNINGS_HARD_GATE_DAYS = 5` giorni
- **Source**: `yfinance.Ticker(t).calendar` / `.get_earnings_dates()`
- **Cache**: tabella `market_ticker_meta.next_earnings_date` TTL 7gg
- **Behavior**:
  - `add_position` → `ValueError` se earnings entro 5gg
  - `ignore_earnings=True` bypass per trade intentional (es. contrarian post-earnings flush)
  - Fail-open su yfinance error (non blocca operatività se data source giù)
- **CLI flag**: `propicks-portfolio add ... --ignore-earnings`

---

## 2. Macro events (hardcoded 2026)

Tabella `MACRO_EVENTS_2026` in `config.py`:
- **8 FOMC** meetings (Tue-Wed, Jan/Mar/Apr/Jun/Jul/Sep/Oct/Dec)
- **12 CPI** releases (2nd Tue/Wed of month)
- **12 NFP** (1st Fri of month)
- **8 ECB** monetary policy decisions

**Soft warning**: `macro_warning_check(entry_date, warning_days=2)` ritorna info
+ lista eventi imminenti. **Non blocca** (coinvolge tutto il mercato, non
ticker-specific) ma alerta il trader. Macro events a frequenza stabile →
hardcoded è accettabile (vs API scraping fragile). **Aggiornare annualmente
`config.MACRO_EVENTS_2026`** quando Fed pubblica nuovo calendar.

---

## 3. CLI `propicks-calendar`

```bash
propicks-calendar earnings                       # portfolio+watchlist, finestra 14gg
propicks-calendar earnings --upcoming 30d --refresh
propicks-calendar macro                          # FOMC/CPI/NFP/ECB 14gg
propicks-calendar macro --upcoming 30d --types FOMC,CPI
propicks-calendar check AAPL                     # gate check per ticker specifico
propicks-calendar check AAPL --refresh           # forza fetch yfinance
```

---

## 4. Scheduler

Job `check_earnings_calendar` (daily Mon-Fri 17:30, pre warm_cache):
- Fetch earnings per portfolio + watchlist tickers
- Genera alert `earnings_upcoming` per ticker entro 5gg
- Dedup per-ticker per-week (ISO week tag)
- Severity `critical` se ≤ 2gg, `warning` altrimenti
- Telegram bot delivery via Phase 4 dispatcher

---

## 5. Bot command

```
/calendar     # summary earnings + macro upcoming nei prossimi 14gg
```

---

## 6. Architettura

```
┌──────────────────┐  daily 17:30
│ check_earnings_  │────────┐
│  calendar (job)  │        │
└──────────────────┘        │
                            ▼
                   ┌─────────────────────┐
                   │ yf.Ticker.calendar  │─┐
                   └─────────────────────┘ │
                            │              │ cache 7gg
                            ▼              ▼
                   ┌────────────────────────────┐
                   │ market_ticker_meta         │
                   │   next_earnings_date       │
                   │   earnings_fetched_at      │
                   └──────────┬─────────────────┘
                              │
              ┌───────────────┼──────────────────┐
              │               │                  │
              ▼               ▼                  ▼
    ┌─────────────┐  ┌────────────────┐  ┌──────────────┐
    │ add_position│  │ scheduler job  │  │ CLI calendar │
    │ HARD GATE   │  │ alert generator│  │ query        │
    └─────────────┘  └────────┬───────┘  └──────────────┘
                              │ create_alert
                              ▼
                     ┌──────────────┐
                     │ alerts queue │───→ Telegram bot
                     └──────────────┘
```

---

## 7. Trade-off accettati

- **Macro events hardcoded**: fragile su aggiornamenti manuali annuali ma
  deterministic (no API scraping che può rompersi). Marcato in doc per update
  ogni dicembre.
- **Earnings fetch yfinance**: non sempre affidabile (missing data per ticker
  esteri/ETF). Fail-open accettato: `add_position` procede se earnings unknown
  — il warning arriva comunque via scheduler alert dopo 24h.
- **Nessun hard gate su macro events**: block di 2gg pre-FOMC significherebbe no
  entry per 20% dell'anno. Troppo restrittivo. Warning è il bilanciamento
  giusto — il trader decide.
- **Earnings TTL 7gg**: se una company riporta earnings e la prossima data si
  sblocca il giorno successivo, il cache resta stale fino a 7gg. Workaround:
  `propicks-calendar earnings --refresh` per forzare update.
