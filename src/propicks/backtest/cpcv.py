"""Combinatorial Purged Cross-Validation (Fase A.2 SIGNAL_ROADMAP).

Implementa CPCV da Lopez de Prado, *Advances in Financial Machine Learning*
cap. 12. Estensione di Purged k-fold (cap. 7) che genera ``C(N, k)`` test
path indipendenti invece di ``N`` fold seriali.

## Perché CPCV su finance time series

K-fold standard non funziona su time series:

1. **Leakage temporale**: train e test possono essere intercalati. Una osservazione
   in train può sapere il futuro di una in test (quando le label sono forward
   returns N-bar).

2. **Path dependency**: una singola sequenza train/test produce 1 stima Sharpe
   del modello. Variance dello stimatore underestimata.

CPCV risolve entrambi:

- **Purging**: rimuove dal train le osservazioni i cui label window si
  sovrappongono al test window
- **Embargo**: aggiunge buffer post-test (default 1-5% del sample) per
  prevenire leakage da serial autocorrelation
- **Combinatorial paths**: con ``N`` group e ``k`` held-out per fold, genera
  ``comb(N, k)`` test path. Es. (N=6, k=2) → 15 path vs 6 fold standard.
  Distribuzione di Sharpe → CI realistic + Deflated Sharpe più stabile.

## API

- ``cpcv_split(n_samples, n_groups, n_test_groups, embargo)`` → iterator di
  ``(train_idx, test_idx)`` numpy array
- ``cpcv_dates_split(dates, n_groups, n_test_groups, embargo_days)`` →
  variant time-aware con embargo in giorni invece di sample count

## Reference

Lopez de Prado (2018), *Advances in Financial Machine Learning*, cap. 12
"Backtesting through Cross-Validation".
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from itertools import combinations

import numpy as np
import pandas as pd


def cpcv_split(
    n_samples: int,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo: int = 5,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Genera ``comb(n_groups, n_test_groups)`` split (train, test).

    Args:
        n_samples: numero totale osservazioni (bar / trade).
        n_groups: in quanti gruppi contigui dividere il sample (default 6).
        n_test_groups: quanti gruppi held-out per fold (default 2).
            Vincolo: ``1 <= n_test_groups < n_groups``.
        embargo: numero osservazioni di embargo prima/dopo ogni gruppo test
            (rimosse anche dal train). Default 5. Mette 0 = no embargo.

    Yields:
        ``(train_idx, test_idx)`` come numpy array di int.
        ``train_idx`` esclude test groups + embargo bands.
        ``test_idx`` = union ordered dei gruppi held-out.

    Esempio:
        >>> for train, test in cpcv_split(100, n_groups=5, n_test_groups=2, embargo=2):
        ...     print(len(train), len(test))
        # → 10 split (C(5,2) = 10), test=40 (2 group da 20 ognuno),
        # train ~50-56 a seconda dell'embargo overlap

    Raises:
        ValueError: se i parametri sono inconsistenti.
    """
    if n_samples < n_groups:
        raise ValueError(f"n_samples ({n_samples}) < n_groups ({n_groups})")
    if not 1 <= n_test_groups < n_groups:
        raise ValueError(
            f"n_test_groups ({n_test_groups}) deve essere in [1, {n_groups - 1}]"
        )
    if embargo < 0:
        raise ValueError(f"embargo deve essere >= 0, ricevuto {embargo}")

    # Suddividi indici in n_groups partizioni contigue (~uguali, diff massimo 1)
    group_sizes = [n_samples // n_groups] * n_groups
    for i in range(n_samples % n_groups):
        group_sizes[i] += 1
    boundaries = [0]
    for s in group_sizes:
        boundaries.append(boundaries[-1] + s)
    # group_indices[i] = (start, end) inclusivo-esclusivo del gruppo i
    group_indices = [(boundaries[i], boundaries[i + 1]) for i in range(n_groups)]

    full = np.arange(n_samples)

    for test_groups in combinations(range(n_groups), n_test_groups):
        # Concatena indici dei gruppi held-out (ordinati naturalmente perché
        # itertools.combinations è ordinato)
        test_idx_list = []
        for g in test_groups:
            start, end = group_indices[g]
            test_idx_list.extend(range(start, end))
        test_idx = np.array(test_idx_list, dtype=int)

        # Train = complement, MENO embargo bands
        excluded = set(test_idx_list)
        for g in test_groups:
            start, end = group_indices[g]
            # Embargo before: rimuovi [start - embargo, start)
            for k in range(max(0, start - embargo), start):
                excluded.add(k)
            # Embargo after: rimuovi [end, end + embargo)
            for k in range(end, min(n_samples, end + embargo)):
                excluded.add(k)

        train_idx = np.array(
            [i for i in full if i not in excluded], dtype=int
        )

        yield train_idx, test_idx


def cpcv_dates_split(
    dates: list[date] | pd.DatetimeIndex,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo_days: int = 5,
) -> Iterator[tuple[list[date], list[date]]]:
    """Variant time-aware: input ``dates`` invece di indici.

    Yields ``(train_dates, test_dates)`` con embargo in giorni di calendario
    (non bar count). Più realistico per OHLCV daily con festivi/weekend.

    Args:
        dates: sequence di date (ordinate ASC). Trading days tipicamente.
        n_groups: gruppi contigui (default 6).
        n_test_groups: held-out per fold (default 2).
        embargo_days: giorni calendario rimossi attorno a test (default 5).

    Yields:
        ``(train_dates_list, test_dates_list)``.
    """
    dates_list = [
        d.date() if hasattr(d, "date") else d
        for d in dates
    ]
    n = len(dates_list)
    if n < n_groups:
        raise ValueError(f"n_dates ({n}) < n_groups ({n_groups})")

    for train_idx, test_idx in cpcv_split(
        n, n_groups=n_groups, n_test_groups=n_test_groups, embargo=0
    ):
        # Apply embargo by day count, not by bar count.
        test_dates = [dates_list[i] for i in test_idx]
        # Convertibile a set per fast lookup
        excluded_dates: set[date] = set(test_dates)
        # Embargo: per ogni test date, escludi ±embargo_days giorni calendario
        for td in test_dates:
            for delta in range(1, embargo_days + 1):
                excluded_dates.add(td - timedelta(days=delta))
                excluded_dates.add(td + timedelta(days=delta))
        train_dates = [d for d in dates_list if d not in excluded_dates]
        yield train_dates, test_dates


def n_cpcv_paths(n_groups: int, n_test_groups: int) -> int:
    """Numero totale di test path generati da CPCV.

    = ``comb(n_groups, n_test_groups)``. Convenience per logging /
    progress bars.
    """
    from math import comb
    return comb(n_groups, n_test_groups)


def cpcv_summary(
    metrics_per_path: list[float],
) -> dict[str, float]:
    """Aggrega metric (es. Sharpe) attraverso i CPCV path.

    Returns:
        Dict con: mean, std, min, max, p25, p50, p75. Std è la varianza
        cross-path utile per Deflated Sharpe Ratio (``var_sr_trials``).
    """
    if not metrics_per_path:
        return {}
    arr = np.asarray(metrics_per_path, dtype=float)
    return {
        "n_paths": len(arr),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
        "var": float(arr.var(ddof=1)) if len(arr) > 1 else 0.0,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
    }
