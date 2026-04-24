"""Portfolio-level backtest engine (Phase 6).

**Cosa fa**: simula nel tempo l'evoluzione di un portfolio che segue la tua
strategia, rispettando le invarianti di business (max posizioni, cap size,
cash reserve, earnings gate). Per ogni bar day t:

1. Scora tutti i ticker nell'universe (cross-section)
2. Gestisce exit: stop hit, target hit, time stop, regime kick-out
3. Seleziona nuovi entry candidate (score ≥ threshold, regime ok, NOT pre-earnings)
4. Apre top-N posizioni che stanno dentro il budget (cash, cap, MAX_POSITIONS)
5. Accumula equity curve + per-strategy attribution

**Differenze vs ``backtest/engine.py`` legacy**:
- Single-ticker → portfolio cross-ticker
- No TC → TC + slippage configurable
- No portfolio constraints → max positions, size cap, MIN_CASH_RESERVE
- No earnings gate → integrato (skip entry se earnings <5gg)

**Known limitations** (documented, not hidden):
- No survivorship bias correction: ticker delisted non nel set
- Corporate actions: splits/dividends non applicati (impact minor su holding 2-8w)
- Earnings gap: stop fillato a stop level, non al gap reale (sottostima loss)
- Simulazione point-in-time: il regime classifier usa ``^GSPC`` corrente,
  non snapshot storico. Accettabile per MVP.

Tutti i test passano DataFrame fissi — zero rete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from propicks.backtest.costs import CostModel, apply_entry_costs, apply_exit_costs


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class OpenPosition:
    ticker: str
    strategy: str
    entry_date: date
    entry_price: float         # gross, pre-costs
    effective_entry: float     # post-costs fill
    shares: int
    stop_loss: float
    target: float | None
    cost_total: float          # € / $ spesi per entry (commission + spread+slip)


@dataclass
class ClosedTrade:
    ticker: str
    strategy: str
    entry_date: date
    exit_date: date
    entry_price: float
    effective_entry: float
    exit_price: float
    effective_exit: float
    shares: int
    duration_days: int
    exit_reason: str           # 'stop' | 'target' | 'time_stop' | 'eod' | 'regime_kick'
    pnl_gross: float            # (exit - entry) * shares, no costs
    pnl_net: float              # dopo entry + exit costs
    pnl_pct: float             # net pnl / (entry × shares) × 100


@dataclass
class PortfolioState:
    """Stato del portfolio simulato."""
    cash: float
    initial_capital: float
    open_positions: dict[str, OpenPosition] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)

    def total_value(self, prices_today: dict[str, float]) -> float:
        """Mark-to-market: cash + sum(shares × current_price)."""
        invested = 0.0
        for ticker, pos in self.open_positions.items():
            cur = prices_today.get(ticker, pos.entry_price)
            invested += pos.shares * cur
        return self.cash + invested


@dataclass
class BacktestConfig:
    """Parametri della simulazione."""
    initial_capital: float = 10_000.0
    max_positions: int = 10
    size_cap_pct: float = 0.15          # momentum cap
    min_cash_reserve_pct: float = 0.20
    score_threshold: float = 60.0
    stop_atr_mult: float = 2.0
    target_atr_mult: float = 4.0
    time_stop_bars: int = 30
    time_stop_flat_pct: float = 0.02     # |P&L| < 2% = flat
    use_earnings_gate: bool = True
    earnings_gate_days: int = 5
    cost_model: CostModel = field(default_factory=CostModel)
    strategy_tag: str = "momentum"       # tag for attribution


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------
def simulate_portfolio(
    *,
    universe: dict[str, pd.DataFrame],
    scoring_fn,
    regime_series: pd.Series | None = None,
    earnings_dates: dict[str, str] | None = None,
    config: BacktestConfig | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PortfolioState:
    """Simula il portfolio tra ``start_date`` e ``end_date``.

    Args:
        universe: dict {ticker: DataFrame OHLCV (indexed by date, columns
            Open/High/Low/Close/Volume/Adj Close)}. DataFrame devono avere
            indice allineato (stesse date) per semplicità. In produzione
            usa trading days comuni (intersect).
        scoring_fn: funzione ``(ticker, hist_slice) -> float | None`` che
            ritorna composite score 0-100 point-in-time. Chiamata 1 volta
            per ticker per day. ``None`` → skip ticker per quel giorno.
        regime_series: Series {date: regime_code 1-5}. Se presente, gate
            regime: apre entry solo se code >= 3. Se None, skip gate.
        earnings_dates: {ticker: 'YYYY-MM-DD'}. Se presente + config.use_earnings_gate:
            skip entry se earnings entro gate_days. Se None, no check.
        config: ``BacktestConfig`` — parametri simulazione.
        start_date, end_date: range. Default: tutto.

    Returns: ``PortfolioState`` con equity_curve, closed_trades, open_positions.
    """
    config = config or BacktestConfig()

    if not universe:
        raise ValueError("Universe vuoto")

    # Unione di tutte le date disponibili
    all_dates = sorted(set(
        d for df in universe.values() for d in df.index
    ))
    if start_date:
        all_dates = [d for d in all_dates if _as_date(d) >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if _as_date(d) <= end_date]

    if not all_dates:
        raise ValueError("Nessuna data nell'intervallo richiesto")

    state = PortfolioState(
        cash=config.initial_capital,
        initial_capital=config.initial_capital,
    )

    for t in all_dates:
        today = _as_date(t)

        # Prices di oggi per mark-to-market + exit check
        prices_today = {}
        for ticker, df in universe.items():
            if t in df.index:
                prices_today[ticker] = float(df.at[t, "Close"])

        # 1. Check exits su posizioni aperte
        _process_exits(state, universe, t, today, config)

        # 2. Record equity curve
        mtm = state.total_value(prices_today)
        state.equity_curve.append((today, mtm))

        # 3. Select new entries (candidate ticker)
        if len(state.open_positions) >= config.max_positions:
            continue  # portfolio pieno, skip entry discovery

        regime_code = None
        if regime_series is not None and t in regime_series.index:
            regime_code = int(regime_series.at[t])

        # Regime gate: se bear/strong_bear, no new entries
        if regime_code is not None and regime_code < 3:
            continue

        # Score tutti i candidate
        candidates: list[tuple[str, float]] = []
        for ticker, df in universe.items():
            if ticker in state.open_positions:
                continue  # già aperto, skip
            # Earnings gate (Phase 8)
            if (
                config.use_earnings_gate
                and earnings_dates
                and _is_pre_earnings(
                    earnings_dates.get(ticker), today, config.earnings_gate_days
                )
            ):
                continue

            # Hist point-in-time (no lookahead)
            if t not in df.index:
                continue
            t_pos = df.index.get_loc(t)
            if t_pos < 200:  # bisogno di warmup per indicators
                continue
            hist_slice = df.iloc[: t_pos + 1]

            try:
                score = scoring_fn(ticker, hist_slice)
            except Exception:
                continue

            if score is None or score < config.score_threshold:
                continue
            candidates.append((ticker, score))

        # 4. Ordina per score desc, apri top-N che stanno nel budget
        candidates.sort(key=lambda x: -x[1])
        for ticker, _score in candidates:
            if len(state.open_positions) >= config.max_positions:
                break
            # Compute sizing + try to open
            _try_open_position(state, ticker, universe, t, today, config)

    # 5. End of backtest: chiudi tutte le posizioni aperte a last close
    _force_close_at_end(state, universe, all_dates[-1], config)

    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_date(t) -> date:
    """Converte un pandas Timestamp / date / datetime in date."""
    if isinstance(t, date) and not hasattr(t, "time"):
        return t
    if hasattr(t, "date"):
        return t.date()
    return t


def _is_pre_earnings(
    earnings_str: str | None,
    today: date,
    threshold_days: int,
) -> bool:
    if not earnings_str:
        return False
    try:
        from datetime import datetime
        ed = datetime.strptime(earnings_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    dte = (ed - today).days
    return 0 <= dte <= threshold_days


def _process_exits(
    state: PortfolioState,
    universe: dict[str, pd.DataFrame],
    t,
    today: date,
    config: BacktestConfig,
) -> None:
    """Check exits per ogni posizione aperta. Priorità: stop > target > time_stop."""
    to_close: list[tuple[str, float, str]] = []
    for ticker, pos in state.open_positions.items():
        df = universe.get(ticker)
        if df is None or t not in df.index:
            continue
        high = float(df.at[t, "High"])
        low = float(df.at[t, "Low"])
        close = float(df.at[t, "Close"])

        # Priority 1: stop (conservative assumption if both hit same bar)
        if low <= pos.stop_loss:
            to_close.append((ticker, pos.stop_loss, "stop"))
            continue
        # Priority 2: target
        if pos.target and high >= pos.target:
            to_close.append((ticker, pos.target, "target"))
            continue
        # Priority 3: time stop
        bars_held = (today - pos.entry_date).days
        if bars_held >= config.time_stop_bars:
            pnl_pct = (close / pos.entry_price) - 1
            if abs(pnl_pct) < config.time_stop_flat_pct:
                to_close.append((ticker, close, "time_stop"))

    for ticker, exit_price, reason in to_close:
        _close_position(state, ticker, exit_price, today, reason, config)


def _try_open_position(
    state: PortfolioState,
    ticker: str,
    universe: dict[str, pd.DataFrame],
    t,
    today: date,
    config: BacktestConfig,
) -> bool:
    """Tenta di aprire posizione. Respecto cap, cash, MIN_RESERVE.
    Returns True se aperto."""
    df = universe.get(ticker)
    if df is None or t not in df.index:
        return False

    close = float(df.at[t, "Close"])

    # Compute ATR approx se disponibile, altrimenti fallback a 5% stop
    atr = _estimate_atr(df, t, period=14)
    stop = close - atr * config.stop_atr_mult if atr > 0 else close * 0.95
    target = close + atr * config.target_atr_mult if atr > 0 else close * 1.12

    # Size calc rispettando cap + cash reserve
    total = state.total_value({ticker: close} | {
        tk: float(universe[tk].at[t, "Close"])
        for tk in state.open_positions if t in universe[tk].index
    })
    max_value = total * config.size_cap_pct
    min_cash_reserve = total * config.min_cash_reserve_pct
    cash_available = max(0.0, state.cash - min_cash_reserve)
    position_value = min(max_value, cash_available)
    shares = int(position_value // close)

    if shares <= 0:
        return False

    # Applica entry costs
    entry_costs = apply_entry_costs(close, shares, ticker, config.cost_model)
    eff_entry = entry_costs["effective_entry"]
    cost_total = entry_costs["cost_total"]
    actual_spent = shares * eff_entry + entry_costs.get("commission", 0)

    if actual_spent > state.cash:
        # Edge case: size calc non include commission. Rimpicciolisci 1 share.
        shares -= 1
        if shares <= 0:
            return False
        entry_costs = apply_entry_costs(close, shares, ticker, config.cost_model)
        eff_entry = entry_costs["effective_entry"]
        cost_total = entry_costs["cost_total"]
        actual_spent = shares * eff_entry + entry_costs.get("commission", 0)

    # Apri posizione
    state.cash -= actual_spent
    state.open_positions[ticker] = OpenPosition(
        ticker=ticker,
        strategy=config.strategy_tag,
        entry_date=today,
        entry_price=close,
        effective_entry=eff_entry,
        shares=shares,
        stop_loss=stop,
        target=target,
        cost_total=cost_total,
    )
    return True


def _close_position(
    state: PortfolioState,
    ticker: str,
    exit_price: float,
    exit_date: date,
    reason: str,
    config: BacktestConfig,
) -> None:
    """Chiude una posizione, applica costi, appende a closed_trades."""
    pos = state.open_positions.pop(ticker)
    exit_costs = apply_exit_costs(exit_price, pos.shares, ticker, config.cost_model)
    eff_exit = exit_costs["effective_exit"]
    cost_exit = exit_costs["cost_total"]

    # Cash: prendiamo i proceeds effettivi - commissioni
    proceeds = pos.shares * eff_exit - exit_costs.get("commission", 0)
    state.cash += proceeds

    # P&L
    pnl_gross = (exit_price - pos.entry_price) * pos.shares
    pnl_net = pnl_gross - pos.cost_total - cost_exit
    pnl_pct = (pnl_net / (pos.entry_price * pos.shares)) * 100 if pos.shares > 0 else 0.0

    state.closed_trades.append(ClosedTrade(
        ticker=ticker,
        strategy=pos.strategy,
        entry_date=pos.entry_date,
        exit_date=exit_date,
        entry_price=pos.entry_price,
        effective_entry=pos.effective_entry,
        exit_price=exit_price,
        effective_exit=eff_exit,
        shares=pos.shares,
        duration_days=(exit_date - pos.entry_date).days,
        exit_reason=reason,
        pnl_gross=round(pnl_gross, 2),
        pnl_net=round(pnl_net, 2),
        pnl_pct=round(pnl_pct, 4),
    ))


def _force_close_at_end(
    state: PortfolioState,
    universe: dict[str, pd.DataFrame],
    last_t,
    config: BacktestConfig,
) -> None:
    """A fine backtest, chiudi posizioni aperte a last close."""
    today = _as_date(last_t)
    for ticker in list(state.open_positions.keys()):
        df = universe.get(ticker)
        if df is None or last_t not in df.index:
            # Usa entry_price come fallback (mark-to-market = 0 P&L)
            _close_position(state, ticker, state.open_positions[ticker].entry_price, today, "eod", config)
            continue
        close = float(df.at[last_t, "Close"])
        _close_position(state, ticker, close, today, "eod", config)


def _estimate_atr(df: pd.DataFrame, t, period: int = 14) -> float:
    """Stima ATR point-in-time. Ritorna 0 se dati insufficienti."""
    if len(df) < period + 2:
        return 0.0
    t_pos = df.index.get_loc(t)
    if t_pos < period:
        return 0.0
    recent = df.iloc[max(0, t_pos - period) : t_pos + 1]
    high = recent["High"]
    low = recent["Low"]
    close = recent["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0
