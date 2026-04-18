"""Backtest module — walk-forward simulation del sistema di scoring.

Scopo MVP: validare il segno dei pesi scoring e capire se la regola di
entry (composite >= threshold) produce un edge positivo su storia
EOD. NON è un framework professionale: niente slippage, niente
commissioni, niente point-in-time universe, niente survivorship-bias
correction. È un primo stress test per capire se i parametri hanno senso.

I limiti noti sono elencati in ``engine.py::KNOWN_LIMITATIONS``.
"""

from propicks.backtest.engine import backtest_ticker, run_backtest
from propicks.backtest.metrics import compute_metrics

__all__ = ["backtest_ticker", "compute_metrics", "run_backtest"]
