"""Risk statistics avanzate (Fase A.2 SIGNAL_ROADMAP).

Implementa Probabilistic Sharpe Ratio (PSR) e Deflated Sharpe Ratio (DSR)
da Bailey-Lopez (2014) — *The Deflated Sharpe Ratio: Correcting for Selection
Bias, Backtest Overfitting and Non-Normality*, Journal of Portfolio Management.

## Perché serve

Il Sharpe Ratio empirico (mean / stdev) è una stima rumorosa del vero Sharpe
underlying. Tre fonti di errore:

1. **Sample size finita**: T trade non sono infiniti, intervallo di confidenza
   ampio. ``PSR`` produce un p-value: probabilità che il vero Sharpe ≥ 0
   (o ≥ benchmark) dato il Sharpe osservato e T.

2. **Non-normalità returns**: skewness e kurtosis modificano la varianza dello
   stimatore Sharpe. Returns trading reali hanno fat tail (kurtosis > 3) e
   asimmetria → PSR/DSR correggono.

3. **Multiple testing / selection bias**: se hai testato N strategie e tieni
   la migliore, ``E[max SR]`` su N tentativi è > 0 anche su segnali random.
   ``DSR`` sottrae il Sharpe atteso sotto null hypothesis di N test, dando
   un p-value deflated robusto al curve-fitting.

## Formule

**PSR** (Bailey-Lopez 2012):

    z = (SR - SR_benchmark) * sqrt(T - 1) /
        sqrt(1 - γ3 * SR + (γ4 - 1) / 4 * SR^2)
    PSR = Φ(z)

dove γ3 = skewness annualized, γ4 = kurtosis annualized (4 = normale).
``PSR > 0.95`` → 95% confidence vero Sharpe > benchmark.

**DSR** (Bailey-Lopez 2014):

    SR_expected = sqrt(2 * ln(N)) - (γ_euler + ln(ln(N))) / (2 * sqrt(2 * ln(N)))
    DSR = Φ((SR - SR_expected * stdev(SR_trials)) * sqrt(T-1) / sqrt(...))

dove ``N`` = numero strategie testate, ``γ_euler ≈ 0.5772`` Euler-Mascheroni.
``DSR > 0.95`` → strategia robusta a multiple testing.

## API

- ``probabilistic_sharpe_ratio(returns, sr_benchmark=0.0)`` → (psr, z_stat)
- ``deflated_sharpe_ratio(returns, n_trials, var_sr_trials)`` → (dsr, sr_expected)
- ``sharpe_with_confidence(returns, alpha=0.05)`` → (sr, lower, upper)
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

# Euler-Mascheroni constant (γ_euler) — usato in DSR per sqrt(2 ln N)
# correction. ~0.5772156649
_EULER_GAMMA = 0.5772156649015329


def _sample_stats(returns: Sequence[float]) -> tuple[int, float, float, float, float]:
    """Calcola (n, mean, std, skew, kurtosis) da returns (sample, ddof=1).

    Skewness e kurtosis come da Wikipedia (sample-corrected). Kurtosis qui è
    *raw* (3 = normale), NON excess kurtosis. Usato direttamente in formula
    PSR Bailey-Lopez.
    """
    arr = np.asarray(list(returns), dtype=float)
    n = len(arr)
    if n < 3:
        raise ValueError(f"n={n} insufficiente (min 3 per skew/kurt)")
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std == 0:
        return n, mean, 0.0, 0.0, 3.0
    centered = arr - mean
    m2 = float((centered**2).mean())
    m3 = float((centered**3).mean())
    m4 = float((centered**4).mean())
    skew = m3 / m2**1.5 if m2 > 0 else 0.0
    kurt = m4 / m2**2 if m2 > 0 else 3.0
    return n, mean, std, skew, kurt


def _norm_cdf(x: float) -> float:
    """Φ(x) — CDF normale standard. Stdlib math.erf evita scipy dep."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def sharpe_ratio(returns: Sequence[float]) -> float:
    """Sharpe ratio non-annualizzato. Mean / stdev (sample, ddof=1).

    Per annualizzato: moltiplicare per sqrt(T) dove T = trade per anno
    (es. ~252 per daily, ~50 per weekly trades).
    """
    n, mean, std, _, _ = _sample_stats(returns)
    if std == 0:
        return 0.0
    return mean / std


def probabilistic_sharpe_ratio(
    returns: Sequence[float],
    sr_benchmark: float = 0.0,
) -> tuple[float, float]:
    """Probabilistic Sharpe Ratio (Bailey-Lopez 2012).

    Risponde alla domanda: dato il Sharpe empirico osservato su T returns,
    qual è la probabilità che il vero Sharpe sia ≥ ``sr_benchmark``?

    Args:
        returns: sequence di return per-trade (o per-bar). Almeno 3 osservazioni.
        sr_benchmark: Sharpe di confronto sotto null hypothesis (default 0.0
            = "Sharpe vero > 0?"). Per confronto vs SPY buy-hold, passa il
            Sharpe annualizzato di SPY scalato alla stessa frequenza.

    Returns:
        (psr, z_stat): ``psr`` ∈ [0, 1] è Φ(z). ``psr > 0.95`` = 95% confidence.

    Edge cases:
        - std=0 → ritorna (0.5, 0.0) (no information)
        - denominator non-finite (skew/kurt patologici) → (0.5, 0.0)
    """
    n, mean, std, skew, kurt = _sample_stats(returns)
    if std == 0:
        return 0.5, 0.0
    sr = mean / std
    # Variance dello stimatore Sharpe sotto skew/kurt non-normali.
    # Formula 4 in Bailey-Lopez (2012).
    denom_squared = 1 - skew * sr + ((kurt - 1) / 4) * sr**2
    if denom_squared <= 0 or not math.isfinite(denom_squared):
        return 0.5, 0.0
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom_squared)
    return _norm_cdf(z), z


def expected_max_sharpe(n_trials: int, var_sr_trials: float = 1.0) -> float:
    """Expected maximum Sharpe Ratio sotto null hypothesis con N trials.

    Bailey-Lopez (2014) eq. 7: derivato da extreme value theory per Gaussian
    iid samples. ``E[max SR]`` cresce con sqrt(2 ln N).

    Args:
        n_trials: numero strategie / parametrizzazioni testate (es. 20
            threshold values su threshold sweep).
        var_sr_trials: varianza del Sharpe attraverso i trials (stimata
            empiricamente, default 1.0 = unit variance assumption).

    Returns:
        Expected max Sharpe sotto null. Sottrai questo dal Sharpe osservato
        per ottenere il "deflated" Sharpe.
    """
    if n_trials < 2:
        return 0.0
    log_n = math.log(n_trials)
    sqrt_2log_n = math.sqrt(2 * log_n)
    correction = (_EULER_GAMMA + math.log(log_n)) / (2 * sqrt_2log_n)
    e_max = (sqrt_2log_n - correction) * math.sqrt(var_sr_trials)
    return e_max


def deflated_sharpe_ratio(
    returns: Sequence[float],
    n_trials: int,
    var_sr_trials: float = 1.0,
) -> tuple[float, float]:
    """Deflated Sharpe Ratio (Bailey-Lopez 2014).

    Estensione di PSR che corregge per multiple testing: se hai testato
    ``n_trials`` strategie e tieni la migliore, il Sharpe atteso sotto null
    non è 0 ma ``E[max SR | n_trials]``. DSR sostituisce ``sr_benchmark`` di
    PSR con questo expected max.

    Args:
        returns: sequence return della strategia migliore.
        n_trials: numero strategie testate (es. 20 threshold).
        var_sr_trials: varianza Sharpe attraverso i trials. Stima empirica:
            ``var(sharpe_per_trial)`` su tutti i trial. Se 1 trial → no
            correction. Se trial con Sharpe correlati (parameter sweep
            piccolo step) → varianza piccola, correction lieve.

    Returns:
        (dsr, sr_expected): ``dsr`` ∈ [0, 1] = Φ(z_deflated). ``sr_expected``
        = expected max sharpe sotto null. ``dsr > 0.95`` = 95% confidence
        che la strategia non sia un fluke da multiple testing.
    """
    sr_expected = expected_max_sharpe(n_trials, var_sr_trials)
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr_expected)[0], sr_expected


def sharpe_with_confidence(
    returns: Sequence[float],
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """Sharpe ratio + intervallo di confidenza ``1-alpha``.

    Usa standard error asymptotic da Mertens (2002):

        SE(SR) = sqrt((1 + (γ4-1)/4 * SR² - γ3*SR) / (T-1))

    CI = SR ± z_{1-α/2} * SE(SR).

    Args:
        returns: sequence per-trade returns.
        alpha: significance level (default 0.05 → CI 95%).

    Returns:
        (sharpe, ci_lower, ci_upper).
    """
    n, mean, std, skew, kurt = _sample_stats(returns)
    if std == 0:
        return 0.0, 0.0, 0.0
    sr = mean / std
    var_sr = (1 + ((kurt - 1) / 4) * sr**2 - skew * sr) / (n - 1)
    if var_sr <= 0 or not math.isfinite(var_sr):
        return sr, sr, sr
    se = math.sqrt(var_sr)
    # z_{1-α/2} per alpha=0.05 = 1.96. Generic via inverse normal CDF
    # (stdlib non lo offre direttamente; usiamo approximation Beasley-Springer
    # via newton on CDF). Per alpha standard hardcoded.
    z_crit = _z_critical(1 - alpha / 2)
    return sr, sr - z_crit * se, sr + z_crit * se


def _z_critical(p: float) -> float:
    """Inverse normal CDF (quantile). Beasley-Springer-Moro algorithm.

    Sufficiente per p ∈ [0.001, 0.999]. Per casi standard hardcoded:
    p=0.975 → 1.95996, p=0.95 → 1.64485, p=0.99 → 2.32635.
    """
    # Hardcoded per casi più frequenti (alpha 0.10, 0.05, 0.01)
    hardcoded = {
        0.95: 1.6448536269514722,
        0.975: 1.959963984540054,
        0.99: 2.326347874040841,
        0.995: 2.5758293035489004,
    }
    if p in hardcoded:
        return hardcoded[p]
    # Approximation Beasley-Springer per casi non-standard
    if p < 0.5:
        return -_z_critical(1 - p)
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t**3)


def annualize_sharpe(sr_per_period: float, periods_per_year: int) -> float:
    """Scala Sharpe da per-period a annualized: SR_ann = SR_period * sqrt(N).

    Esempi:
        - daily returns, 252 trading day/anno → sqrt(252) ≈ 15.87
        - weekly trade-level returns → sqrt(50)
        - monthly → sqrt(12)

    Per trade-level Sharpe (un return per chiusura), ``periods_per_year`` è
    il numero medio di trade chiusi per anno (turnover dipendente).
    """
    return sr_per_period * math.sqrt(periods_per_year)
