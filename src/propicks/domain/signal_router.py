"""Cross-strategy signal conflict resolution (Fase D.3 SIGNAL_ROADMAP).

Quando momentum e contrarian generano segnale BUY su STESSO ticker, è
indicatore di **incoerenza signal** (mercato sia in trend che in
oversold-mean-reversion = contraddizione). Reject entrambi.

## Rules

1. **Same ticker on both strategies** → REJECT (incoerenza)
2. **Already in portfolio** + new signal → REJECT (no double-entry)
3. **ETF rotation** vs **stock momentum**: ETF ha precedenza per sector
   exposure (semplifica position sizing aggregato sector)

## API

- ``resolve_signal_conflicts(momentum, contrarian, etf, open_positions)``
  → dict {momentum, contrarian, etf} filtered

Pure function. No I/O.
"""

from __future__ import annotations


def resolve_signal_conflicts(
    momentum_signals: dict[str, float] | None = None,
    contrarian_signals: dict[str, float] | None = None,
    etf_signals: dict[str, float] | None = None,
    open_positions: set[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Filter signals incompatible cross-strategy.

    Args:
        momentum_signals: {ticker: score} per momentum candidate.
        contrarian_signals: {ticker: score} per contrarian candidate.
        etf_signals: {ticker: score} per ETF rotation candidate.
        open_positions: set ticker già in portfolio (any strategy).

    Returns:
        Dict {strategy_name: {ticker: score}} con conflict-resolved signals.
        Keys: ``momentum``, ``contrarian``, ``etf``, ``conflicts`` (audit).
    """
    momentum = dict(momentum_signals or {})
    contrarian = dict(contrarian_signals or {})
    etf = dict(etf_signals or {})
    open_set = set(open_positions or [])

    conflicts: list[dict] = []

    # Rule 2: open positions → reject all new signals on those tickers
    for tk in list(momentum.keys()):
        if tk in open_set:
            conflicts.append({
                "ticker": tk, "rule": "already_open",
                "rejected_from": "momentum",
            })
            momentum.pop(tk)
    for tk in list(contrarian.keys()):
        if tk in open_set:
            conflicts.append({
                "ticker": tk, "rule": "already_open",
                "rejected_from": "contrarian",
            })
            contrarian.pop(tk)
    for tk in list(etf.keys()):
        if tk in open_set:
            conflicts.append({
                "ticker": tk, "rule": "already_open",
                "rejected_from": "etf",
            })
            etf.pop(tk)

    # Rule 1: same ticker su momentum E contrarian → reject entrambi
    momentum_set = set(momentum.keys())
    contra_set = set(contrarian.keys())
    incoherent = momentum_set & contra_set
    for tk in incoherent:
        conflicts.append({
            "ticker": tk, "rule": "incoherent_momentum_contrarian",
            "rejected_from": "both",
            "momentum_score": momentum[tk],
            "contrarian_score": contrarian[tk],
        })
        momentum.pop(tk, None)
        contrarian.pop(tk, None)

    # Rule 3: ETF vs stock momentum overlap su sector ETF
    # NOTE: questa rule richiede mapping ticker → sector.
    # Implementazione semplificata: rimuovi momentum signal su ETF ticker
    # (ETF tratti separatamente). Rilevante solo se utente passa erroneamente
    # ETF in momentum_signals.
    etf_tickers = set(etf.keys())
    for tk in list(momentum.keys()):
        if tk in etf_tickers:
            conflicts.append({
                "ticker": tk, "rule": "etf_priority_over_momentum",
                "rejected_from": "momentum",
            })
            momentum.pop(tk)

    return {
        "momentum": momentum,
        "contrarian": contrarian,
        "etf": etf,
        "conflicts": conflicts,
    }


def has_signal_conflicts(
    momentum_signals: dict[str, float] | None = None,
    contrarian_signals: dict[str, float] | None = None,
    etf_signals: dict[str, float] | None = None,
    open_positions: set[str] | None = None,
) -> bool:
    """True se almeno un conflict trovato. Convenience boolean."""
    result = resolve_signal_conflicts(
        momentum_signals, contrarian_signals, etf_signals, open_positions,
    )
    return bool(result["conflicts"])
