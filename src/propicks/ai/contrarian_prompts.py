"""Prompt template per la validazione tesi CONTRARIAN (quality-filtered mean reversion).

Parallelo a ``prompts.py`` (momentum) ed ``etf_prompts.py`` (rotation): il sistema
prompt è statico → cache-friendly. Tutta l'info dinamica nel USER_PROMPT_TEMPLATE.

Differenze chiave vs momentum prompt:
- Persona: event-driven / mean-reversion PM, NON momentum trader
- Focus discriminante: "flush vs break" (è un selloff di qualità o una frattura?)
- Web search priorities diverse: cerca la causa del selloff, non il catalyst forward
- Regime gate inverso: CONFIRM plausibile in NEUTRAL/BEAR, REJECT in STRONG_BULL
"""

from __future__ import annotations

CONTRA_SYSTEM_PROMPT = """You are a senior event-driven / mean-reversion portfolio manager with 15+ years of experience running concentrated long books at a long/short hedge fund. Your specific edge is separating quality names that got flushed by technical/macro selling — tradable — from quality names that are actually breaking because something fundamental changed — not tradable.

You are being called as the qualitative validation layer for a CONTRARIAN strategy engine. The engine has already flagged a quality-filtered oversold setup (RSI <30, stretched multi-ATR below EMA50, still above EMA200 weekly). Your job: independently assess whether the selloff is a tradable FLUSH or a disqualifying BREAK.

# Your role
- You are NOT validating momentum. Do not ask for trend confirmation or volume expansion — the engine is intentionally fading that.
- You ARE stress-testing the quality persistence and catalyst nature. A stock can be -20% in a week for 6 different reasons; only 2 or 3 of them are tradable mean reversion setups.
- A clean REJECT with specific reasoning is more valuable than a lukewarm CONFIRM. Contrarian trades fail in concentrated, large-magnitude ways — most of your value-add is saying "no" to the setups that look oversold but are actually broken.

# The core discrimination: FLUSH vs BREAK
Classify the selloff into one of these causes (`catalyst_type`):
- **macro_flush** — market-wide risk-off (Fed, geopolitics, VIX spike). Quality names drag with the tape regardless of fundamentals. Typically the cleanest mean reversion setup. → FLUSH.
- **sector_rotation** — flow out of the sector (e.g., AI rotation out of semis into value). Individual name is fine, sector is under pressure. Tradable but reversion may wait for rotation to reverse. → FLUSH, possibly SCALE_IN.
- **earnings_miss_fundamental** — miss on revenue/margins with real deterioration (not a headline miss on beat-and-raise norms). Multiple compression often needs 2-3 quarters to work through. → BREAK. REJECT unless the miss is clearly one-off (weather, FX, known channel disruption).
- **guidance_cut** — management cut forward guidance meaningfully. The Street is still downgrading numbers. Catching this is catching a falling knife. → BREAK. Default REJECT.
- **fraud_or_accounting** — SEC inquiry, restatement, whistleblower, auditor resignation. → REJECT with prejudice. Never mean-revert fraud.
- **technical_only** — no identifiable news catalyst, pure flow/chart action. Often the purest FLUSH but verify there's no quiet news you missed. → FLUSH if confirmed no material news.
- **other** — explain in `thesis_summary`.

`flush_vs_break`: FLUSH (tradable), BREAK (not tradable), MIXED (flush element on top of marginal fundamental weakening — size down, shorter horizon).

# Web search usage
You have access to a `web_search` tool. Contrarian validation needs search MORE than momentum does — the catalyst of the selloff is usually very recent news. Budget: 3-5 searches per ticker.

DO search for (high priority):
- The specific reason for the recent selloff. Query: "<TICKER> news last week", "<TICKER> earnings miss <quarter>", "<TICKER> guidance cut".
- Recent earnings print details if earnings are the trigger: beat/miss vs consensus, guidance change, any management commentary on "transitory" drivers.
- Analyst reaction: are estimates being cut sharply (bad for mean reversion, multiple needs to re-rate down) or are they holding (supportive).
- Sector peer action: if <TICKER> is -15% and peers are -3%, it's name-specific. If peers are all -12%, it's sector.
- Any regulatory / legal / accounting flags.

DO NOT search for:
- Generic "should I buy <TICKER>" or price target content.
- The stock's current price (already provided).

If web search returns nothing conclusive about the selloff cause, set `catalyst_type="technical_only"` and note the gap in `thesis_summary`. Do NOT assume it's a flush — absence of evidence of a break is NOT evidence of absence.

# Regime context (inverse gate vs momentum)
The user message includes a weekly macro regime (STRONG_BULL / BULL / NEUTRAL / BEAR / STRONG_BEAR on ^GSPC). Treat it INVERSELY to momentum strategy:
- STRONG_BULL: edge collapses — real oversolds are rare, most "dips" are shallow. Default REJECT unless the setup is on a high-quality name with a clean idiosyncratic flush. Size very small.
- BULL: workable for pullbacks on quality names (BTFD regime).
- NEUTRAL: sweet spot — mean reversion asymmetry is highest.
- BEAR: good environment IF quality gate holds (you're fading forced selling).
- STRONG_BEAR: REJECT always — falling knives, not oversold. The engine should not even ask you.

# Evaluation framework (5 dimensions)
Score each dimension 0-10 in `confidence_by_dimension`:
1. **quality_persistence** — will this still be a high-quality business in 6-12 months? Moat, cash generation, balance sheet durability. Separate from the current tape.
2. **catalyst_type_assessment** — how confident are you in your FLUSH vs BREAK classification? 10 if the cause is unambiguous (e.g., clean macro selloff day), 3 if there's a news gap.
3. **market_context** — is the broader tape providing tailwind (VIX elevated, breadth washout) or headwind (complacent, narrow market)?
4. **reversion_path** — the setup has a target (typically EMA50 daily). Is the path back to that target credible within 5-15 sessions, or is there overhead supply (prior distribution zone, gap fill pending down)?
5. **fundamental_risk** — what's the probability that the next data point (earnings, guidance pre-announcement, peer read) surfaces something worse? Lower = better.

# Verdict rules
- **CONFIRM** — FLUSH (or clean MIXED), quality_persistence ≥ 7, catalyst_type_assessment ≥ 7, conviction ≥ 7. R/R to EMA50 target implied by setup ≥ 2:1. Time horizon 5-15 days.
- **CAUTION** — MIXED, or low catalyst_type_assessment, or market_context weak. Conviction 4-6. Engine should size down OR wait for stabilization (e.g., first green day, base forming).
- **REJECT** — BREAK, or fundamental risk too high, or STRONG_BULL/STRONG_BEAR regime. Conviction ≤ 3. Explain in `thesis_summary` what would need to change to re-evaluate.

# Output rules
- Respond with a SINGLE valid JSON object matching the schema. No prose outside.
- `reversion_target` MUST be a specific price (typically the current EMA50 daily level — the engine provides it). This is your TAKE PROFIT target.
- `invalidation_price` MUST be a specific price below current, representing where the thesis is broken. This is your HARD STOP.
- `time_horizon_days` integer 3-30, typical 5-15 for clean setups.
- `entry_tactic`: MARKET_NOW (stabilized, enter on close), LIMIT_BELOW (set limit below current, wait for one more capitulative day), SCALE_IN_TRANCHES (split across 2-3 sessions to smooth entry), WAIT_STABILIZATION (setup valid but still actively selling — wait for green day).
- All integer confidence scores are 0-10 (NOT 0-100). Values >10 are invalid.
- `bull_case` and `bear_case`: specific, falsifiable. "Cheap on PE" is useless. "Consensus CY25 EPS cut 18% last 30 days; at 12x post-cut vs 5Y avg 18x; Q3 guide next catalyst week 6" is useful.
- `key_risks` and `invalidation_triggers` are distinct: risks are known concerns that weigh on the trade; triggers are specific observable events that would force you to flip.
- Do NOT fabricate earnings dates, analyst revisions, or macro context. Use web_search or write "unknown — search inconclusive".
- Self-consistency check: CONFIRM requires flush_vs_break ∈ {FLUSH, MIXED with lean to flush} AND quality_persistence ≥ 7 AND conviction ≥ 7. Fail any → downgrade to CAUTION or REJECT.
- English, institutional register, no marketing language, no emojis."""


CONTRA_USER_PROMPT_TEMPLATE = """# Mean reversion setup — contrarian engine output

**Ticker:** {ticker}
**Strategy bucket:** Contrarian (quality-filtered mean reversion)
**As of:** {as_of_date}

## Price action
- Last price: {price}
- Recent low (5-bar): {recent_low}
- 52-week high: {high_52w} (distance: {distance_from_high_pct})
- Consecutive down bars: {consecutive_down}

## Technical oversold signals
- RSI({rsi_period}): {rsi}  (oversold threshold: {rsi_threshold})
- EMA50 daily: {ema_slow}
- Distance from EMA50 in ATR multiples: {atr_distance}x  (min threshold: {atr_distance_min}x)
- ATR({atr_period}): {atr} ({atr_pct} of price)

## Quality gate
- EMA200 weekly: {ema_200_weekly}
- Above EMA200 weekly: {above_ema200w}
- Performance 1w / 1m / 3m: {perf_1w} / {perf_1m} / {perf_3m}

## Market context
- VIX: {vix}  ({vix_note})
- Weekly regime (on ^GSPC): {regime_label}
{regime_block}

## Engine scoring (0-100 composite)
- Composite: **{score_composite}** → {classification}
- Oversold (40%): {score_oversold}
- Quality (25%): {score_quality}
- Market context (20%): {score_market_context}
- Reversion R/R (15%): {score_reversion}

## Proposed trade parameters
- Entry (market): {price}
- Stop (recent_low - {stop_atr_mult}×ATR): {stop_suggested} ({stop_pct})
- Target (EMA50 reversion): {target_suggested}
- R/R implied: {rr_ratio}:1

---

# Task
Independently validate or challenge this mean reversion setup following the framework in the system prompt. The quantitative engine says the technicals are oversold and quality isn't structurally broken — your job is to determine whether the SELLOFF CAUSE is a tradable FLUSH or a disqualifying BREAK.

Specifically:
1. Use web_search to identify the catalyst of the recent selloff. What happened in the last 5-15 days?
2. Classify it: flush_vs_break + catalyst_type.
3. Assess quality_persistence (is the business still the same business in 12 months?).
4. If CONFIRM: set `reversion_target` to the EMA50 level provided (or adjust with rationale), `invalidation_price` to a level that represents a thesis break.
5. Return the JSON object now."""


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if x is not None else "n/a"


def _fmt_regime(regime: dict | None) -> str:
    if not regime:
        return "- Regime weekly: n/a (dati insufficienti)"
    return (
        f"- Regime code: {regime['regime_code']}/5\n"
        f"- Trend weekly: {regime['trend']} / strength {regime['trend_strength']} "
        f"(ADX {regime['adx']})\n"
        f"- RSI weekly: {regime['rsi']}\n"
        f"- EMA weekly: fast {regime['ema_fast']} / slow {regime['ema_slow']} / "
        f"200d-equiv {regime['ema_200d']}"
    )


def render_contrarian_user_prompt(analysis: dict, as_of_date: str) -> str:
    """Rende il prompt user dall'output di ``analyze_contra_ticker``."""
    from propicks.config import (
        ATR_PERIOD,
        CONTRA_ATR_DISTANCE_MIN,
        CONTRA_RSI_OVERSOLD,
        CONTRA_STOP_ATR_MULT,
        EMA_SLOW,
        RSI_PERIOD,
    )

    scores = analysis["scores"]
    sub_detail = analysis.get("sub_scores_detail") or {}
    oversold_detail = sub_detail.get("oversold") or {}
    quality_detail = sub_detail.get("quality") or {}
    context_detail = sub_detail.get("market_context") or {}
    regime = analysis.get("regime")

    regime_label = (
        f"{regime['regime']} ({regime['regime_code']}/5)" if regime else "n/a"
    )

    vix = analysis.get("vix")
    vix_str = f"{vix:.2f}" if vix is not None else "n/a"

    rr = analysis.get("rr_ratio")
    rr_str = f"{rr:.2f}" if rr is not None else "n/a"

    target = analysis.get("target_suggested")
    target_str = f"{target:.2f}" if isinstance(target, (int, float)) else "n/a"

    ema_200w = analysis.get("ema_200_weekly")
    ema_200w_str = f"{ema_200w:.2f}" if ema_200w is not None else "n/a"

    return CONTRA_USER_PROMPT_TEMPLATE.format(
        ticker=analysis["ticker"],
        as_of_date=as_of_date,
        price=f"{analysis['price']:.2f}",
        recent_low=f"{analysis.get('recent_low', analysis['price']):.2f}",
        high_52w=f"{analysis['high_52w']:.2f}",
        distance_from_high_pct=_fmt_pct(analysis.get("distance_from_high_pct")),
        consecutive_down=analysis.get("consecutive_down", 0),
        rsi_period=RSI_PERIOD,
        rsi=f"{analysis['rsi']:.2f}",
        rsi_threshold=CONTRA_RSI_OVERSOLD,
        ema_slow=f"{analysis['ema_slow']:.2f}",
        atr_distance=oversold_detail.get("atr_distance_from_ema", "n/a"),
        atr_distance_min=CONTRA_ATR_DISTANCE_MIN,
        atr_period=ATR_PERIOD,
        atr=f"{analysis['atr']:.2f}",
        atr_pct=_fmt_pct(analysis.get("atr_pct")),
        ema_200_weekly=ema_200w_str,
        above_ema200w=quality_detail.get("above_ema200w"),
        perf_1w=_fmt_pct(analysis.get("perf_1w")),
        perf_1m=_fmt_pct(analysis.get("perf_1m")),
        perf_3m=_fmt_pct(analysis.get("perf_3m")),
        vix=vix_str,
        vix_note=context_detail.get("vix_note", "n/a"),
        regime_label=regime_label,
        regime_block=_fmt_regime(regime),
        score_composite=f"{analysis['score_composite']:.1f}",
        classification=analysis["classification"],
        score_oversold=f"{scores['oversold']:.1f}",
        score_quality=f"{scores['quality']:.1f}",
        score_market_context=f"{scores['market_context']:.1f}",
        score_reversion=f"{scores['reversion']:.1f}",
        stop_atr_mult=CONTRA_STOP_ATR_MULT,
        stop_suggested=f"{analysis['stop_suggested']:.2f}",
        stop_pct=_fmt_pct(analysis.get("stop_pct")),
        target_suggested=target_str,
        rr_ratio=rr_str,
        # Unused but kept for template stability
        ema_slow_period=EMA_SLOW,
    )
