"""Walk-forward backtest engine per la strategia single-stock.

Rigira le stesse ``score_*`` pure di ``domain.scoring`` su ogni bar storico,
usando solo dati passati (no lookahead). Se il composite supera la soglia e
non c'è già un trade aperto, apre a close. Chiude a:

1. **Stop loss** hit (intraday low <= stop level) → fill a stop level
2. **Target** hit (intraday high >= target) → fill a target
3. **Time stop**: trade flat (|P&L| < flat_threshold) da N giorni → exit a close
4. **Fine storia**: posizione aperta al termine → exit a last close (mark-to-market)

Con priorità stop > target se entrambi toccati nello stesso bar (assunzione
conservativa: worst-case sul run).

KNOWN_LIMITATIONS (esplicite, non nascoste):
- No slippage, no commissioni. Fill esatto sui livelli teorici.
- No survivorship bias correction: se il ticker oggi è delisted/merged non
  entra nel set. Se è vivo, il backtest lo vede come vivo anche durante
  drawdown storici che potevano portarlo allo zero.
- Universo statico: il set di ticker lo decide l'utente. Nessuna replica del
  fatto che l'S&P 500 è un index dinamico.
- Regime gate opzionale ma usa il regime ricalcolato point-in-time sul
  ticker stesso, non su ^GSPC scaricato separatamente (approssimazione
  ragionevole per MVP; produzione userebbe ^GSPC weekly pre-caricato).
- Position sizing: full-size ogni trade, 1 posizione per ticker alla volta.
  Niente correlation/concentration budget cross-ticker.
- Earnings gap non filtrati: se uno stop viene gappato il giorno dopo un
  report, l'engine compila il fill a stop level, che sottostima la perdita
  reale. Nella realtà c'è un gap-down che eccede lo stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from propicks.config import (
    ATR_PERIOD,
    EMA_FAST,
    EMA_SLOW,
    MIN_SCORE_TECH,
    RSI_PERIOD,
    VOLUME_AVG_PERIOD,
    WEIGHT_DISTANCE_HIGH,
    WEIGHT_MA_CROSS,
    WEIGHT_MOMENTUM,
    WEIGHT_TREND,
    WEIGHT_VOLATILITY,
    WEIGHT_VOLUME,
)
from propicks.domain.indicators import compute_atr, compute_ema, compute_rsi
from propicks.domain.scoring import (
    score_distance_from_high,
    score_ma_cross,
    score_momentum,
    score_trend,
    score_volatility,
    score_volume,
)
from propicks.market.yfinance_client import DataUnavailable, download_history

# Warm-up: servono abbastanza bar per EMA50 stabile (3x period) + 52w high
MIN_WARMUP_BARS = EMA_SLOW * 3 + 5


@dataclass
class Trade:
    ticker: str
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float
    shares: float
    entry_score: float
    # Exit
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "stop" | "target" | "time" | "eod"
    pnl_pct: float | None = None
    bars_held: int | None = None

    def close(self, exit_date: date, exit_price: float, reason: str, bars: int) -> None:
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.exit_reason = reason
        self.pnl_pct = (exit_price - self.entry_price) / self.entry_price
        self.bars_held = bars


@dataclass
class BacktestResult:
    ticker: str
    period_start: date
    period_end: date
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    signals_generated: int = 0
    signals_taken: int = 0


def _compute_composite(
    close: float,
    ema_fast: float,
    ema_slow: float,
    rsi: float,
    atr: float,
    volume: float,
    avg_volume: float,
    high_52w: float,
    prev_ema_fast: float,
    prev_ema_slow: float,
) -> float:
    """Stessa formula di analyze_ticker ma a partire da scalari puri."""
    sub = (
        score_trend(close, ema_fast, ema_slow) * WEIGHT_TREND
        + score_momentum(rsi) * WEIGHT_MOMENTUM
        + score_volume(volume, avg_volume) * WEIGHT_VOLUME
        + score_distance_from_high(close, high_52w) * WEIGHT_DISTANCE_HIGH
        + score_volatility(atr, close) * WEIGHT_VOLATILITY
        + score_ma_cross(ema_fast, ema_slow, prev_ema_fast, prev_ema_slow) * WEIGHT_MA_CROSS
    )
    return max(0.0, min(100.0, sub))


def backtest_ticker(
    ticker: str,
    history: pd.DataFrame | None = None,
    period: str = "5y",
    threshold: float = MIN_SCORE_TECH,
    stop_atr_mult: float = 2.0,
    target_atr_mult: float = 4.0,
    time_stop_bars: int = 30,
    time_stop_flat_pct: float = 0.02,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """Rigira la strategia su un singolo ticker.

    Parametri:
        history: se None, scarica via yfinance. Per test puri passa DataFrame.
        threshold: composite minimo per aprire.
        stop_atr_mult / target_atr_mult: livelli relativi all'ATR al momento
            dell'entry (stop = entry - k*ATR, target = entry + k*ATR).
            Default stop -2*ATR, target +4*ATR → R:R 2.0 teorico.
        time_stop_bars: bar max senza progresso prima di uscire flat.
        time_stop_flat_pct: sotto questa |P&L|% il trade è considerato flat.
    """
    if history is None:
        hist = download_history(ticker, period=period)
    else:
        hist = history

    if len(hist) < MIN_WARMUP_BARS + 10:
        raise DataUnavailable(
            ticker, f"storia insufficiente per backtest: {len(hist)} bar"
        )

    # yfinance ritorna DatetimeIndex tz-aware (US/Eastern). Lookup successivi
    # via pd.Timestamp(date) sono tz-naive → KeyError. Normalizziamo qui una
    # sola volta. Test sintetici (date_range tz-naive) restano compatibili.
    if isinstance(hist.index, pd.DatetimeIndex) and hist.index.tz is not None:
        hist = hist.copy()
        hist.index = hist.index.tz_localize(None)

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]

    # Indicatori calcolati una sola volta su tutta la storia. Il lookahead
    # viene evitato accedendo solo a iloc[i] al bar i (EMA/RSI/ATR a t usano
    # solo close[:t], quindi il valore al bar i è point-in-time corretto).
    ema_fast = compute_ema(close, EMA_FAST)
    ema_slow = compute_ema(close, EMA_SLOW)
    rsi = compute_rsi(close, RSI_PERIOD)
    atr = compute_atr(high, low, close, ATR_PERIOD)

    trades: list[Trade] = []
    equity_points: list[tuple[date, float]] = []

    cash = initial_capital
    position: Trade | None = None
    signals_generated = 0

    for i in range(MIN_WARMUP_BARS, len(hist)):
        bar_date = hist.index[i].date()
        c = float(close.iloc[i])
        h = float(high.iloc[i])
        low_i = float(low.iloc[i])

        # ------------------------------------------------------------------
        # Gestione posizione aperta: check exit prima di check entry
        # ------------------------------------------------------------------
        if position is not None:
            bars = i - hist.index.get_loc(pd.Timestamp(position.entry_date))
            exited = False

            # Priorità: stop > target se entrambi toccati (conservativo)
            if low_i <= position.stop_price:
                position.close(bar_date, position.stop_price, "stop", bars)
                cash += position.shares * position.stop_price
                trades.append(position)
                position = None
                exited = True
            elif h >= position.target_price:
                position.close(bar_date, position.target_price, "target", bars)
                cash += position.shares * position.target_price
                trades.append(position)
                position = None
                exited = True
            elif bars >= time_stop_bars:
                pnl_now = (c - position.entry_price) / position.entry_price
                if abs(pnl_now) < time_stop_flat_pct:
                    position.close(bar_date, c, "time", bars)
                    cash += position.shares * c
                    trades.append(position)
                    position = None
                    exited = True

            if not exited:
                # Mark-to-market equity
                equity_points.append((bar_date, cash + position.shares * c))
                continue

        # ------------------------------------------------------------------
        # Nessuna posizione aperta: valuta signal
        # ------------------------------------------------------------------
        avg_vol = float(volume.iloc[max(0, i - VOLUME_AVG_PERIOD + 1) : i + 1].mean())
        cur_vol = float(volume.iloc[i])
        high_52w = float(high.iloc[max(0, i - 251) : i + 1].max())

        ef_now = float(ema_fast.iloc[i])
        es_now = float(ema_slow.iloc[i])
        ef_prev = float(ema_fast.iloc[i - 5]) if i >= 5 else float("nan")
        es_prev = float(ema_slow.iloc[i - 5]) if i >= 5 else float("nan")

        composite = _compute_composite(
            close=c,
            ema_fast=ef_now,
            ema_slow=es_now,
            rsi=float(rsi.iloc[i]),
            atr=float(atr.iloc[i]),
            volume=cur_vol,
            avg_volume=avg_vol,
            high_52w=high_52w,
            prev_ema_fast=ef_prev,
            prev_ema_slow=es_prev,
        )

        if composite >= threshold:
            signals_generated += 1
            atr_entry = float(atr.iloc[i])
            stop = c - stop_atr_mult * atr_entry
            target = c + target_atr_mult * atr_entry

            if stop <= 0 or stop >= c:
                equity_points.append((bar_date, cash))
                continue

            shares = cash / c
            position = Trade(
                ticker=ticker,
                entry_date=bar_date,
                entry_price=c,
                stop_price=round(stop, 2),
                target_price=round(target, 2),
                shares=shares,
                entry_score=round(composite, 1),
            )
            cash = 0.0
            equity_points.append((bar_date, position.shares * c))
        else:
            equity_points.append((bar_date, cash))

    # Chiusura forzata a fine storia
    if position is not None:
        last_date = hist.index[-1].date()
        last_price = float(close.iloc[-1])
        bars = len(hist) - 1 - hist.index.get_loc(pd.Timestamp(position.entry_date))
        position.close(last_date, last_price, "eod", bars)
        cash += position.shares * last_price
        trades.append(position)

    equity = pd.Series(
        {d: v for d, v in equity_points},
        name="equity",
    ).astype(float)

    return BacktestResult(
        ticker=ticker,
        period_start=hist.index[0].date(),
        period_end=hist.index[-1].date(),
        trades=trades,
        equity_curve=equity,
        signals_generated=signals_generated,
        signals_taken=len(trades),
    )


def run_backtest(
    tickers: list[str],
    period: str = "5y",
    threshold: float = MIN_SCORE_TECH,
    **kwargs,
) -> dict[str, BacktestResult]:
    """Backtest batch. Ritorna dict ticker -> BacktestResult.

    Ticker che falliscono (dati insufficienti) sono skippati con None nel
    dict — il caller filtra.
    """
    results: dict[str, BacktestResult] = {}
    for t in tickers:
        try:
            results[t] = backtest_ticker(t, period=period, threshold=threshold, **kwargs)
        except DataUnavailable as err:
            print(f"[backtest] skip {t}: {err}")
    return results
