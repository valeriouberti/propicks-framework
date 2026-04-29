"""Calibration metrics per probabilistic forecasts (Fase D.1 SIGNAL_ROADMAP).

Misure di qualità per previsioni probabilistiche (es. AI verdict score
come proxy di P(trade success)). Usato per quantificare se l'AI gate
aggiunge alpha o se è rumore + costo.

## Metriche

- **Brier score** (Brier 1950): MSE tra forecast probability e binary
  outcome. Range [0, 1], 0 = perfect, 0.25 = random uniform, 1 = always
  wrong. Lower = better.
- **Reliability diagram**: bin-wise mean predicted prob vs mean observed
  outcome. Diagonale = perfect calibration.
- **ECE** (Expected Calibration Error): weighted average miscalibration
  per bin. Lower = better.
- **AI add-value**: Sharpe(AI-passed trades) - Sharpe(AI-rejected trades).
  Decision rule SIGNAL_ROADMAP §7 D.1: drop AI gate se add-value < 0.05.

## API

- ``brier_score(predictions, outcomes) -> float``
- ``reliability_diagram(predictions, outcomes, n_bins=10) -> list[dict]``
- ``expected_calibration_error(predictions, outcomes, n_bins=10) -> float``
- ``ai_add_value_sharpe(returns_passed, returns_rejected) -> float``

Pure functions. Input numpy/list. No I/O.

## Reference

- Brier (1950), "Verification of forecasts expressed in terms of probability"
- Naeini et al. (2015), "Obtaining Well Calibrated Probabilities Using Bayesian Binning"
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _validate(predictions: Sequence[float], outcomes: Sequence[int]) -> tuple[list, list]:
    if len(predictions) != len(outcomes):
        raise ValueError(
            f"length mismatch: pred={len(predictions)} outcomes={len(outcomes)}"
        )
    p_clean = []
    o_clean = []
    for p, o in zip(predictions, outcomes):
        try:
            pf = float(p)
            of = int(o)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pf):
            continue
        if of not in (0, 1):
            continue
        p_clean.append(max(0.0, min(1.0, pf)))
        o_clean.append(of)
    return p_clean, o_clean


def brier_score(
    predictions: Sequence[float],
    outcomes: Sequence[int],
) -> float:
    """Brier score = mean((pred - outcome)^2).

    Args:
        predictions: probabilità predette ∈ [0, 1]. Es. AI verdict normalizzato.
        outcomes: 1 se successo (es. trade win), 0 altrimenti.

    Returns:
        Float in [0, 1]. Lower = better. 0.25 = random uniform baseline.
    """
    p, o = _validate(predictions, outcomes)
    if not p:
        return float("nan")
    return sum((pi - oi) ** 2 for pi, oi in zip(p, o)) / len(p)


def reliability_diagram(
    predictions: Sequence[float],
    outcomes: Sequence[int],
    *,
    n_bins: int = 10,
) -> list[dict]:
    """Bin-wise mean predicted prob vs mean observed outcome.

    Args:
        predictions, outcomes: as ``brier_score``.
        n_bins: number of equal-width bins in [0, 1] (default 10).

    Returns:
        List di dict per bin con keys: ``bin_lo``, ``bin_hi``, ``n``,
        ``mean_pred``, ``mean_obs``, ``gap``. ``gap > 0`` = overconfident
        (prediction > observed). Bin vuoti exclusi.
    """
    p, o = _validate(predictions, outcomes)
    if not p:
        return []
    bins: dict[int, list[tuple[float, int]]] = {}
    for pi, oi in zip(p, o):
        b = min(int(pi * n_bins), n_bins - 1)
        bins.setdefault(b, []).append((pi, oi))
    out = []
    for b in sorted(bins):
        items = bins[b]
        n = len(items)
        if n == 0:
            continue
        mean_pred = sum(x[0] for x in items) / n
        mean_obs = sum(x[1] for x in items) / n
        out.append(
            {
                "bin_lo": b / n_bins,
                "bin_hi": (b + 1) / n_bins,
                "n": n,
                "mean_pred": round(mean_pred, 4),
                "mean_obs": round(mean_obs, 4),
                "gap": round(mean_pred - mean_obs, 4),
            }
        )
    return out


def expected_calibration_error(
    predictions: Sequence[float],
    outcomes: Sequence[int],
    *,
    n_bins: int = 10,
) -> float:
    """Expected Calibration Error: weighted absolute miscalibration.

    ECE = Σ_b (n_b / N) * |mean_pred_b - mean_obs_b|

    Lower = better. ECE = 0 → perfect calibration. ECE 0.10 = ~10% mean gap.
    """
    diag = reliability_diagram(predictions, outcomes, n_bins=n_bins)
    if not diag:
        return float("nan")
    total_n = sum(b["n"] for b in diag)
    if total_n == 0:
        return float("nan")
    return sum(
        (b["n"] / total_n) * abs(b["mean_pred"] - b["mean_obs"])
        for b in diag
    )


def ai_add_value_sharpe(
    returns_passed: Sequence[float],
    returns_rejected: Sequence[float],
) -> dict:
    """AI add-value: confronta Sharpe trade AI-passed vs AI-rejected.

    Razionale: se AI gate identifica i trade che effettivamente performano
    meglio, ``Sharpe(passed) > Sharpe(rejected)``. Altrimenti l'AI è rumore
    + costo (token + latency).

    Args:
        returns_passed: per-trade returns (frazionali) trade approvati AI.
        returns_rejected: per-trade returns trade rejected da AI (ma sennò
            avrebbero passato gate quant). Empty se AI è solo gate, non
            tracciamo rejected.

    Returns:
        Dict con sharpe_passed, sharpe_rejected, add_value, n_passed,
        n_rejected, decision (per SIGNAL_ROADMAP rule: drop se < 0.05).
    """
    def _sharpe(rets):
        if len(rets) < 3:
            return None
        m = sum(rets) / len(rets)
        var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        if var <= 0:
            return None
        return m / math.sqrt(var)

    sr_p = _sharpe(list(returns_passed))
    sr_r = _sharpe(list(returns_rejected))

    add_value = None
    if sr_p is not None and sr_r is not None:
        add_value = sr_p - sr_r

    decision = "INSUFFICIENT_DATA"
    if add_value is not None:
        if add_value >= 0.05:
            decision = "KEEP_AI_GATE"
        elif add_value > 0:
            decision = "MARGINAL — consider drop"
        else:
            decision = "DROP_AI_GATE"

    return {
        "sharpe_passed": round(sr_p, 4) if sr_p is not None else None,
        "sharpe_rejected": round(sr_r, 4) if sr_r is not None else None,
        "add_value": round(add_value, 4) if add_value is not None else None,
        "n_passed": len(list(returns_passed)),
        "n_rejected": len(list(returns_rejected)),
        "decision": decision,
    }
