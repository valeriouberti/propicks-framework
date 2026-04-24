"""Advanced sizing (Phase 5) — composizione di Kelly + vol target + corr penalty.

**Principio di sicurezza**: questa funzione **non può MAI** produrre un size
superiore a quello che produrrebbe ``calculate_position_size`` standard.
Può solo scalare down. I gate hardcoded (8% contrarian, 15% momentum, 20% ETF,
min cash, max loss, contrarian bucket cap) restano intatti.

## Flow

1. Chiama ``calculate_position_size`` standard → ``base`` size con tutti i gate
2. Se ``use_kelly``: calcola Kelly per la strategia da journal. Se valido
   (>= 15 trade chiusi), scala down il base a ``min(base, kelly_pct × capital)``
3. Se ``use_corr_penalty``: riduce ulteriormente se il ticker è correlato ≥0.7
   con posizioni esistenti
4. Se ``use_vol_target``: scala down se portfolio vol attuale già ≥ target
5. Ritorna ``final_value``, ``final_shares``, + breakdown dict per trasparenza

## Naming convention

Il breakdown esplicita *quale* fattore ha determinato la size finale. Per
ogni passo mostriamo: "base_X%", "kelly_Y%", "corr_scale_Z", "vol_scale_W",
"binding_constraint": str.
"""

from __future__ import annotations

import pandas as pd

from propicks.domain.risk import (
    correlation_adjusted_size,
    portfolio_vol_annualized,
    strategy_kelly_from_trades,
    vol_target_scale,
)
from propicks.domain.sizing import (
    AssetTypeLiteral,
    StrategyBucket,
    calculate_position_size,
)

DEFAULT_TARGET_VOL_ANNUALIZED = 0.15  # 15% annual vol — conservative retail


def calculate_position_size_advanced(
    entry_price: float,
    stop_price: float,
    score_claude: int = 7,
    score_tech: int = 70,
    portfolio: dict | None = None,
    asset_type: AssetTypeLiteral = "STOCK",
    strategy_bucket: StrategyBucket = "momentum",
    *,
    strategy_name: str | None = None,
    # Risk feature flags (opt-in)
    use_kelly: bool = True,
    use_corr_penalty: bool = True,
    use_vol_target: bool = True,
    # External data (iniettabili per test)
    trades: list[dict] | None = None,
    returns_df: pd.DataFrame | None = None,
    corr_matrix: pd.DataFrame | None = None,
    target_vol: float = DEFAULT_TARGET_VOL_ANNUALIZED,
) -> dict:
    """Advanced sizing con Kelly + correlation penalty + vol targeting.

    **Safety guarantee**: il risultato ha SEMPRE ``final_size_pct <= base_size_pct``.
    I gate hardcoded restano autorità finale.

    Args:
        entry_price, stop_price, score_claude, score_tech, portfolio,
        asset_type, strategy_bucket: passati direttamente a
        ``calculate_position_size``.
        strategy_name: tag strategia specifico (es. "TechTitans", "Contrarian")
            per matching in trades journal. Se None, usa il bucket.
        use_kelly: applica Kelly downscaling se journal ha >=15 trade chiusi.
        use_corr_penalty: applica correlation penalty se corr_matrix fornita.
        use_vol_target: applica vol target scale se returns_df fornito.
        trades: journal per Kelly. None → skip Kelly step.
        returns_df: daily returns per vol. None → skip vol step.
        corr_matrix: correlation matrix per corr penalty. None → skip.
        target_vol: vol annualized target (default 15%).

    Returns: dict con:
        - ``ok``: bool (match base sizing, plus se stop≥entry o score<min)
        - ``shares``: int finale dopo tutti i downscales
        - ``final_value``, ``final_size_pct``, ``base_size_pct``
        - ``breakdown``: dict con ogni step (kelly/corr/vol), valore + motivo
        - ``binding_constraint``: str, quale factor ha determinato la size
        - ``warnings``: list (dal base + aggiuntivi)
    """
    # Step 1: base sizing con tutti i gate hardcoded
    base = calculate_position_size(
        entry_price=entry_price,
        stop_price=stop_price,
        score_claude=score_claude,
        score_tech=score_tech,
        portfolio=portfolio,
        asset_type=asset_type,
        strategy_bucket=strategy_bucket,
    )

    if not base.get("ok"):
        # Rejected at gate level — return with advanced=False flag
        base["advanced"] = False
        return base

    base_shares = int(base["shares"])
    base_value = float(base["position_value"])
    base_size_pct = float(base["position_pct"])
    total_capital_proxy = base_value / base_size_pct if base_size_pct > 0 else None

    breakdown: dict = {
        "base": {
            "shares": base_shares,
            "value": base_value,
            "size_pct": base_size_pct,
            "conviction": base.get("conviction"),
            "source": "calculate_position_size (hard caps + MIN_CASH + risk-per-trade)",
        }
    }

    current_size_pct = base_size_pct
    binding = "base_cap"
    extra_warnings: list[str] = []

    # Step 2: Kelly downscaling
    if use_kelly and trades is not None:
        kelly_strategy = strategy_name or ("Contrarian" if strategy_bucket == "contrarian" else "momentum")
        # Kelly è advisory per quella strategia. Se il bucket è "momentum" generico,
        # tenti un match con la strategia specifica del trade (es. "TechTitans").
        kelly = strategy_kelly_from_trades(trades, kelly_strategy)
        breakdown["kelly"] = kelly
        if kelly.get("usable") and kelly["kelly_pct"] > 0:
            if kelly["kelly_pct"] < current_size_pct:
                current_size_pct = kelly["kelly_pct"]
                binding = "kelly_fractional"
        elif kelly.get("usable") and kelly["kelly_pct"] == 0:
            # Edge ≤ 0 sul journal recente: warn pesante
            extra_warnings.append(
                f"Kelly suggerisce 0% (edge negativo stimato da {kelly['n_trades']} trade)"
            )

    # Step 3: Correlation penalty — handled post-hoc da ``apply_correlation_penalty``
    # poiché richiede il ``new_ticker`` esplicito, non disponibile qui. Il CLI
    # chiama prima questa funzione, poi ``apply_correlation_penalty`` con il
    # ticker noto.

    # Step 4: Vol target scaling
    if use_vol_target and returns_df is not None and portfolio:
        positions = portfolio.get("positions", {})
        total_cap = float(portfolio.get("cash") or 0) + sum(
            float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
            for p in positions.values()
        )
        existing_weights = {}
        if total_cap > 0:
            for ticker, p in positions.items():
                invested = float(p.get("shares") or 0) * float(p.get("entry_price") or 0)
                existing_weights[ticker] = invested / total_cap

        if existing_weights:
            vol_info = portfolio_vol_annualized(returns_df, existing_weights)
            breakdown["current_vol"] = vol_info
            if vol_info["vol_annualized"] > 0:
                vt = vol_target_scale(vol_info["vol_annualized"], target_vol)
                breakdown["vol_target"] = vt
                # Applichiamo solo se SCALE DOWN (safety: non scaliamo up via vol target)
                if vt["scale_factor"] < 1.0:
                    scaled = current_size_pct * vt["scale_factor"]
                    if scaled < current_size_pct:
                        current_size_pct = scaled
                        binding = "vol_target_scale_down"

    # Finalize
    if total_capital_proxy:
        final_value = current_size_pct * total_capital_proxy
        final_shares = int(final_value // entry_price)
    else:
        final_shares = 0
        final_value = 0.0

    # Guardia: final_shares non può superare base_shares (safety)
    final_shares = min(final_shares, base_shares)
    final_value = final_shares * entry_price

    # Recalc size_pct dopo il clamping shares
    if total_capital_proxy:
        final_size_pct_actual = final_value / total_capital_proxy
    else:
        final_size_pct_actual = 0.0

    return {
        "ok": True,
        "advanced": True,
        "shares": final_shares,
        "asset_type": asset_type,
        "strategy_bucket": strategy_bucket,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "final_value": round(final_value, 2),
        "final_size_pct": round(final_size_pct_actual, 4),
        "base_size_pct": round(base_size_pct, 4),
        "base_shares": base_shares,
        "shares_reduction": base_shares - final_shares,
        "binding_constraint": binding,
        "breakdown": breakdown,
        "warnings": (base.get("warnings") or []) + extra_warnings,
        # Forward originali campi per compat
        "risk_pct_trade": base.get("risk_pct_trade"),
        "risk_total": base.get("risk_total") * (final_shares / base_shares) if base_shares > 0 else 0,
        "conviction": base.get("conviction"),
        "position_cap_pct": base.get("position_cap_pct"),
    }


def apply_correlation_penalty(
    base_result: dict,
    new_ticker: str,
    existing_weights: dict[str, float],
    corr_matrix: pd.DataFrame | None,
    corr_threshold: float = 0.7,
) -> dict:
    """Applica correlation penalty su un risultato di sizing esistente.

    Separato da ``calculate_position_size_advanced`` perché richiede
    ``new_ticker`` che viene conosciuto solo al call site (CLI / dashboard).

    Args:
        base_result: dict ritornato da calculate_position_size[_advanced]
        new_ticker: ticker che si sta aggiungendo
        existing_weights: dict {ticker: weight} esistenti
        corr_matrix: correlation DataFrame
        corr_threshold: 0.7 default

    Returns: nuovo dict con final_shares scalato. Se base non ha `ok`,
    ritorna base invariato.
    """
    if not base_result.get("ok"):
        return base_result

    base_shares = int(base_result.get("shares", 0))
    base_size_pct = float(base_result.get("final_size_pct") or base_result.get("position_pct") or 0)

    penalty = correlation_adjusted_size(
        base_size_pct=base_size_pct,
        new_ticker=new_ticker.upper(),
        existing_weights=existing_weights,
        corr_matrix=corr_matrix,
        corr_threshold=corr_threshold,
    )

    # Merge into breakdown
    result = dict(base_result)
    breakdown = dict(result.get("breakdown", {}))
    breakdown["corr_penalty"] = penalty
    result["breakdown"] = breakdown

    scale = penalty.get("scale_factor", 1.0)
    if scale < 1.0:
        new_shares = int(base_shares * scale)
        entry = float(result.get("entry_price", 0))
        result["shares"] = new_shares
        result["final_value"] = round(new_shares * entry, 2)
        if base_size_pct > 0:
            result["final_size_pct"] = round(base_size_pct * scale, 4)
        result["binding_constraint"] = "corr_penalty"
        result["shares_reduction"] = result.get("base_shares", base_shares) - new_shares

    return result
