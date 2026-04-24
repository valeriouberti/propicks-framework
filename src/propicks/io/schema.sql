-- Propicks — schema SQLite v1
--
-- Source of truth per tutto lo stato transazionale del trading engine.
-- Separato in 4 aree concettuali:
--   1. STATO (positions, portfolio_meta) — modificabile, un solo valore corrente
--   2. STORIA (trades, strategy_runs, ai_verdicts) — append-mostly, mai cancellato
--   3. RIFERIMENTI (watchlist) — stato modificabile ma rigoroso (dedup per ticker)
--   4. TIMELINES (regime_history, portfolio_snapshots) — snapshot giornalieri
--
-- Tutti gli indici sono definiti inline. Ogni tabella ha ``created_at`` o
-- ``run_timestamp`` per audit trail.

-- ----------------------------------------------------------------------------
-- 1. STATO
-- ----------------------------------------------------------------------------

-- Portfolio meta: key-value singleton per cash, initial_capital, last_updated.
-- Evita una tabella a singola riga che è awkward in SQL.
CREATE TABLE IF NOT EXISTS portfolio_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Posizioni correnti (stato). Una riga per ticker aperto.
CREATE TABLE IF NOT EXISTS positions (
  ticker TEXT PRIMARY KEY,
  strategy TEXT,
  entry_price REAL NOT NULL,
  entry_date DATE NOT NULL,
  shares INTEGER NOT NULL,
  stop_loss REAL,
  target REAL,
  highest_price_since_entry REAL,
  trailing_enabled INTEGER DEFAULT 0,
  score_claude INTEGER,
  score_tech INTEGER,
  catalyst TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 2. STORIA (append-mostly)
-- ----------------------------------------------------------------------------

-- Journal trade — append-only. Le chiusure aggiornano exit_* campi sulla
-- stessa riga, ma nessuna riga viene mai cancellata.
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL,          -- 'long' | 'short'
  strategy TEXT,
  entry_date DATE NOT NULL,
  entry_price REAL NOT NULL,
  shares INTEGER,
  stop_loss REAL,
  target REAL,
  score_claude INTEGER,
  score_tech INTEGER,
  catalyst TEXT,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'closed'
  exit_date DATE,
  exit_price REAL,
  exit_reason TEXT,
  pnl_pct REAL,
  pnl_per_share REAL,
  duration_days INTEGER,
  post_trade_notes TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_entry ON trades(strategy, entry_date);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_exit_date ON trades(exit_date);

-- Ogni esecuzione di propicks-scan / propicks-contra / propicks-rotate produce
-- righe qui. Abilita "qual era lo score di X il Y?" e attribution analysis.
-- Popolato forward (no storicizzazione retroattiva in questa fase).
CREATE TABLE IF NOT EXISTS strategy_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  strategy TEXT NOT NULL,           -- 'Momentum' | 'Contrarian' | 'SectorETF'
  ticker TEXT NOT NULL,
  composite_score REAL,
  classification TEXT,
  sub_scores TEXT,                  -- JSON blob (trend, momentum, oversold, ...)
  price REAL,
  rsi REAL,
  atr REAL,
  regime_code INTEGER,
  action_taken TEXT                 -- 'ignored' | 'watchlist_add' | 'validated' | 'entry'
);

CREATE INDEX IF NOT EXISTS idx_runs_strategy_ticker_ts
  ON strategy_runs(strategy, ticker, run_timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON strategy_runs(run_timestamp);

-- AI verdicts — sostituisce la folder data/ai_cache/ con storia completa.
-- Il cache lookup usa cache_key (es. "AAPL_momentum_v4_2026-04-24").
-- TTL viene applicato a runtime via WHERE run_timestamp > ...
CREATE TABLE IF NOT EXISTS ai_verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  strategy TEXT NOT NULL,           -- 'momentum' | 'contrarian' | 'etf_rotation'
  ticker TEXT NOT NULL,
  cache_key TEXT NOT NULL,
  verdict TEXT,                     -- CONFIRM | CAUTION | REJECT
  conviction INTEGER,
  payload TEXT NOT NULL,            -- full JSON della verdict
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd REAL
);

CREATE INDEX IF NOT EXISTS idx_verdicts_cache_key ON ai_verdicts(cache_key, run_timestamp);
CREATE INDEX IF NOT EXISTS idx_verdicts_strategy_ticker_ts
  ON ai_verdicts(strategy, ticker, run_timestamp);

-- ----------------------------------------------------------------------------
-- 3. RIFERIMENTI (stato editabile)
-- ----------------------------------------------------------------------------

-- Watchlist — incubatrice di idee. Dedup per ticker (PRIMARY KEY).
CREATE TABLE IF NOT EXISTS watchlist (
  ticker TEXT PRIMARY KEY,
  added_date DATE NOT NULL,
  target_entry REAL,
  note TEXT,
  score_at_add REAL,
  regime_at_add TEXT,
  classification_at_add TEXT,
  source TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'auto_scan' | 'auto_scan_contra'
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 4. TIMELINES (snapshot giornalieri)
-- ----------------------------------------------------------------------------

-- Regime macro weekly su ^GSPC, snapshot giornaliero per attribution analysis.
CREATE TABLE IF NOT EXISTS regime_history (
  date DATE PRIMARY KEY,
  regime_code INTEGER NOT NULL,     -- 1..5
  regime_label TEXT NOT NULL,
  adx REAL,
  rsi REAL,
  macd_hist REAL,
  ema_fast REAL,
  ema_slow REAL,
  ema_200d REAL,
  recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Equity curve: 1 riga per giorno. Valore totale + breakdown per strategia.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  date DATE PRIMARY KEY,
  cash REAL NOT NULL,
  invested_value REAL NOT NULL,
  total_value REAL NOT NULL,
  n_positions INTEGER NOT NULL,
  contra_exposure_pct REAL,
  momentum_exposure_pct REAL,
  etf_exposure_pct REAL,
  mtd_return REAL,
  ytd_return REAL,
  benchmark_spx REAL,
  benchmark_ftsemib REAL,
  recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Daily AI budget counter — 1 riga per giorno.
-- Popolato da ``ai/budget.py`` ad ogni chiamata all'API; TTL implicito del
-- bucket giornaliero è la data key stessa (domani = nuova riga, reset a 0).
CREATE TABLE IF NOT EXISTS daily_budget (
  date DATE PRIMARY KEY,
  calls INTEGER NOT NULL DEFAULT 0,
  est_cost_usd REAL NOT NULL DEFAULT 0.0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- Schema versioning
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  description TEXT
);

INSERT OR IGNORE INTO schema_version (version, description)
  VALUES (1, 'initial schema — migration from JSON stores');
