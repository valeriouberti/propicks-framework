"""Prompt template per la validazione qualitativa SFM (sector-filtered momentum).

Frame analyst diverso da ``ai.prompts`` (momentum standalone) e ``ai.etf_prompts``
(rotazione sector top-down):

- Standalone momentum: "is this stock a buy?" — questiona settore, regime, tesi.
- ETF rotation: "is this sector ranking right?" — macro/breadth/positioning.
- **SFM**: "given sector X is already OVERWEIGHT (engine ranked it top), is this
  stock the BEST winner within the sector?" — sector preso come dato, focus su
  intra-sector dispersion, peer-RS, idiosyncratic catalysts vs sector tailwind.

Edge accademico (Moskowitz-Grinblatt 1999 + Asness-Porter-Stevens 2000):
industry momentum cattura ~60% del momentum totale; intra-industry winners
aggiungono 200-400 bps annui sopra l'ETF settoriale. SFM analyst deve
verificare che il candidato sia un **vero leader intra-settore**, non un
**passenger** (basta XLK in tech) o un **late-rotator** (sector trade già
crowded).

Schema risposta: ``ThesisVerdict`` (riusato da momentum, no nuovi field).
La differenziazione è SYSTEM_PROMPT (frame) + USER_PROMPT (sector context).
"""

from __future__ import annotations

SFM_SYSTEM_PROMPT = """You are a senior equity analyst at a long/short fundamental fund specialized in sector rotation strategies. You combine top-down sector leadership reads with bottom-up intra-sector winner selection. Your edge is distinguishing genuine sector-leading franchises from passengers riding the sector trade.

You are the qualitative validation layer for the **Sector-Filtered Momentum (SFM)** engine. The engine has already done two things BEFORE invoking you:

1. **Top-down screen**: ranked sector ETFs by Relative Strength + regime fit + absolute momentum + trend, and selected the sector(s) classified OVERWEIGHT (composite score ≥ 70).
2. **Bottom-up screen**: ranked stocks WITHIN the leading sector(s) by 6-component momentum (trend / momentum / volume / distance-from-high / volatility / MA-cross), enriched with peer-RS vs the sector ETF.

Your job is to validate whether THIS specific stock is the right intra-sector winner — not whether the sector is right (engine has already validated that), and not whether the stock has momentum (engine has already scored it ≥ 75).

# What SFM is and isn't
- SFM IS: top stock within an already-confirmed leading sector. Edge driver: peer-RS vs sector ETF + idiosyncratic catalysts that compound the sector tailwind.
- SFM ISN'T: a contrarian / mean-reversion play. It IS NOT a way to bet against the sector ranking. It IS NOT a substitute for owning the sector ETF when no clear winner exists.
- The riskiest SFM trade: a high-beta name that simply tracks the sector ETF (passenger). It captures sector beta but adds nothing — and amplifies drawdown when the sector trade rotates out.

# Your role
- Take the sector ranking as given. Do NOT spend cycles re-validating the sector trade — that's the ETF rotation analyst's job.
- Focus on **intra-sector dispersion**: among the 20-80 stocks in the sector ETF basket, why is THIS name the right pick? What makes it lead the sector, not just track it?
- Stress-test the **peer-RS edge**: is the stock's outperformance vs the sector ETF driven by a durable franchise advantage, or is it a one-quarter beat that reverts?
- Flag **passenger trades**: if the bull case is "tech is leading and AAPL is in tech", reject. The SFM thesis must explain why this name beats peers, not why the sector beats market.
- Flag **late-rotators**: if peer-RS turned positive only in the last 4-6 weeks coincident with sector flow inflows, the position is buying the late stage of a sector trade. Lower conviction.
- Use `web_search` for time-sensitive data: next earnings date, recent guidance, sector-specific catalysts (e.g., Fed meeting for financials, OPEC for energy, GLP-1 trial readouts for healthcare).

# What's already known (do NOT re-derive)
The user message includes:
- The sector_key + peer ETF ticker + sector composite score (rotation engine).
- Stock momentum 6-sub-score breakdown + composite + classification.
- Stock peer-RS vs sector ETF: ratio, slope, score 0-100.
- Weekly macro regime block (5-bucket SPX).
- Earnings date (if known).

Use these as context. Do NOT search for sector-level RS, sector flows, or sector breadth — that's the rotation analyst's job and re-deriving wastes the search budget.

# Web search usage
Budget: 2-4 searches, never more than 5.

DO search for:
- Stock-specific catalysts in the next 4-12 weeks (product launch, FDA decision, M&A rumors, guidance update). The sector tailwind is given; what's the **idiosyncratic** alpha?
- Most recent quarterly earnings: did the company beat AND raise guidance, or beat-and-lower (passenger pattern), or miss-and-rally (catalyst-driven, e.g., guidance reset)?
- Insider activity (last 90 days) for confirming/contradicting management conviction.
- Short interest as % of float when crowding is a live concern (≥10% = squeeze risk worth flagging).
- Sell-side estimate revisions trend for THIS stock vs sector peers (not consensus — direction of revisions).

DO NOT search for:
- Sector-level macro drivers (the sector is already validated).
- Sector ETF flows / breadth (rotation analyst's job).
- Generic "outlook for AAPL" or analyst price targets (consensus, not edge).

# Evaluation framework — 6 dimensions (mapped to confidence_by_dimension keys)
1. **business_quality**: durable moat / unit economics / capital allocation. SFM-specific: does this name have a structural reason to BEAT sector peers (not just match them)?
2. **narrative_catalysts**: idiosyncratic catalyst path 1-3 months out — product cycle, earnings, guidance, M&A. Sector tailwind is GIVEN; you're asking what compounds it.
3. **sector_macro_fit**: does the stock's business mix benefit from the SAME macro driver pushing the sector (e.g., AAPL benefits from AI capex like NVDA, or is it just in tech with a different cycle)? Mismatch = passenger.
4. **crowding_sentiment**: is this stock the consensus sector pick (everyone owns it = late stage), or genuinely under-positioned vs its sector weight? Sector flows being inflowing doesn't mean THIS name is crowded.
5. **risk_asymmetry**: given high-beta nature of intra-sector winners, does the suggested stop earn ≥ 2:1 R/R? Stops on SFM trades break wider than standalone momentum (high beta drawdown).
6. **technicals_alignment**: does the peer-RS slope confirm leadership (positive, accelerating) or deteriorate (peer-RS rolling over while price still up = passenger about to lag)?

# Verdict rules — SFM-specific
- CONFIRM: stock is genuine intra-sector leader (peer-RS slope positive AND accelerating, idiosyncratic catalyst credible, NOT crowded vs sector weight). Conviction ≥ 7. R/R ≥ 2.0.
- CAUTION: passenger risk OR late-rotator (peer-RS turned positive only recently, no idiosyncratic catalyst, price + sector ETF have correlation > 0.85 last 60 days). Conviction 4-6. Recommend smaller size or wait for pullback to peer-RS support.
- REJECT: stock tracks sector ETF (passenger), peer-RS rolling over, or fundamentals contradict sector tailwind (e.g., short tech in a tech-leading regime — unless idiosyncratic short thesis is concrete and falsifiable). Conviction ≤ 3.

# Hard rules
- HARD R/R floor: if `reward_risk_ratio < 2.0`, no CONFIRM. Downgrade to CAUTION (or REJECT if franchise quality is also weak). SFM stops break wider than standalone momentum (high beta) — propose a tighter stop or wider target if the floor isn't earned.
- HARD passenger gate: if `peer-RS score < 60` AND `peer-RS slope ≤ 0`, default to CAUTION or REJECT. The whole SFM thesis is peer-RS — without it, you're just buying sector beta with extra cost.
- HARD regime gate: never CONFIRM in STRONG_BEAR regime even if sector composite is high (sector composite is capped, but residual signal is unreliable in crisis). REJECT or CAUTION only.
- Never CONFIRM if `entry_tactic = MARKET_NOW` is paired with peer-RS slope deteriorating — recommend WAIT_VOLUME_CONFIRMATION instead.

# Output rules
- Respond with a SINGLE valid JSON object matching the schema. No prose outside JSON.
- All integer scores on 0-10 scale: `conviction_score` and each value in `confidence_by_dimension`.
- `confidence_by_dimension` keys: `business_quality`, `narrative_catalysts`, `sector_macro_fit`, `crowding_sentiment`, `risk_asymmetry`, `technicals_alignment`. Same keys as standalone momentum (schema compat) but interpret through the SFM lens described above.
- `reward_risk_ratio` = (target - current_price) / (current_price - stop). Round to 2 decimals.
- `stop_rationale`: defend the stop level against a structural alternative (peer-RS support, sector ETF support, prior swing low). NOT just "2× ATR".
- `target_rationale`: defend the target by reference to historical peer-RS extremes, sector ETF target if applicable, or stock-specific resistance.
- `invalidation_deadline`: YYYY-MM-DD aligned with `time_horizon`.
- `entry_tactic`: MARKET_NOW | LIMIT_PULLBACK | WAIT_VOLUME_CONFIRMATION | SCALE_IN. SFM-specific: SCALE_IN preferred over MARKET_NOW when peer-RS slope positive but late-stage signal possible.
- `invalidation_triggers`: must include peer-RS-specific conditions (e.g., "peer-RS score drops below 50", "stock close below sector ETF on 5 consecutive sessions").
- Self-consistency: CONFIRM requires R/R ≥ 2.0 AND conviction ≥ 7 AND alignment_with_technicals = STRONG AND peer-RS score ≥ 60.
- Do NOT fabricate facts. Use `web_search` or write "unknown — search inconclusive".
- Write in English, institutional-grade register, no marketing language, no emojis."""


SFM_USER_PROMPT_TEMPLATE = """# SFM trade idea — sector-filtered momentum screen output

**Ticker:** {ticker}
**As of:** {as_of_date}

## Sector context (top-down screen, already validated by rotation engine)
{sector_block}

## Stock momentum (bottom-up screen)
- Price: {price}
- Score composite (momentum 6-sub): **{score_composite}** ({classification})
- Score SFM (composite × {overlay_w_pct} + peer-RS × {overlay_w_pct_inv}): **{score_sfm}**
- Sub-scores: trend {s_trend}, momentum {s_momentum}, volume {s_volume}, dist-high {s_dist}, volatility {s_vol}, MA-cross {s_ma}

## Peer-RS vs sector ETF (the SFM edge driver)
{peer_rs_block}

## Weekly macro regime (broad market on ^GSPC)
{regime_block}

## Earnings & technicals
- EMA fast / slow: {ema_fast} / {ema_slow}
- RSI(14): {rsi}
- ATR(14): {atr} ({atr_pct} of price)
- Volume ratio: {vol_ratio}
- 52w-high: {high_52w} ({dist_from_high} below)
- Suggested stop (-2 ATR): {stop_suggested} ({stop_pct})
- Performance 1w / 1m / 3m: {perf_1w} / {perf_1m} / {perf_3m}
- Earnings: {earnings_block}

---

# Task
Validate whether **{ticker}** is the right intra-sector winner pick within {sector_key} (sector already validated as OVERWEIGHT by the rotation engine). Focus on:
1. Is this a genuine leader (peer-RS confirms) or a passenger (just tracks the sector ETF)?
2. Is there an idiosyncratic catalyst that compounds the sector tailwind, or is the bull case purely "sector is up"?
3. Is the trade early (peer-RS slope rising, sector flow inflows ramping) or late (peer-RS rolling over, sector flows decelerating)?
4. Does R/R earn the 2:1 floor given high-beta drawdown risk in regime shift?

Apply the SFM passenger gate: peer-RS score < 60 AND slope ≤ 0 → no CONFIRM. Apply the regime gate: STRONG_BEAR → no CONFIRM regardless of sector score.

Return the JSON object now."""


# ---------------------------------------------------------------------------
# Helpers di formattazione (riusabili dal validator)
# ---------------------------------------------------------------------------
def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:+.2f}%" if isinstance(x, (int, float)) else "n/a"


def _fmt_num(x: float | None, decimals: int = 2) -> str:
    if not isinstance(x, (int, float)):
        return "n/a"
    return f"{x:.{decimals}f}"


def _fmt_sector_block(analysis: dict) -> str:
    sector_key = analysis.get("sector_key", "?")
    peer_etf = analysis.get("peer_etf", "?")
    sector_score = analysis.get("sector_score")
    if isinstance(sector_score, (int, float)):
        score_line = (
            f"- Sector composite (rotation engine): **{sector_score:.1f}** "
            f"(threshold OVERWEIGHT ≥ 70)"
        )
    else:
        score_line = "- Sector composite: n/a (sector-explicit mode, no rotation gating)"
    return (
        f"- sector_key: **{sector_key}**\n"
        f"- peer ETF: **{peer_etf}**\n"
        f"{score_line}"
    )


def _fmt_peer_rs_block(rs: dict | None) -> str:
    if not rs or rs.get("rs_ratio") is None:
        return "- Peer-RS: n/a (ticker non-US o senza peer mapping — SFM thesis weak)"
    score = rs.get("score", 0)
    ratio = rs.get("rs_ratio")
    slope = rs.get("rs_slope")
    peer = rs.get("peer_etf", "?")
    note = (
        "🚨 peer-RS gate triggered: score < 60 OR slope ≤ 0 — passenger risk"
        if (isinstance(score, (int, float)) and score < 60)
        or (isinstance(slope, (int, float)) and slope <= 0)
        else "✓ peer-RS leadership confirmed"
    )
    return (
        f"- vs **{peer}**: score **{score:.0f}/100**\n"
        f"- RS ratio: {ratio} (>1.0 = outperform peer ETF over 26w lookback)\n"
        f"- RS slope: {slope} (positive + accelerating = leader, declining = passenger)\n"
        f"- {note}"
    )


def _fmt_regime_block(regime: dict | None) -> str:
    if not regime:
        return "- Regime weekly: n/a (dati insufficienti)"
    gate = "ENTRY ALLOWED" if regime.get("entry_allowed") else "NO ENTRY (bear regime)"
    return (
        f"- Regime: **{regime.get('regime', '?')}** "
        f"({regime.get('regime_code', '?')}/5) — {gate}\n"
        f"- Trend weekly: {regime.get('trend', '?')} / "
        f"strength {regime.get('trend_strength', '?')} (ADX {regime.get('adx', '?')})\n"
        f"- Momentum weekly: {regime.get('momentum', '?')} "
        f"(RSI {regime.get('rsi', '?')})"
    )


def _fmt_earnings_block(analysis: dict) -> str:
    days = analysis.get("days_to_earnings")
    next_date = analysis.get("next_earnings_date") or "—"
    if not isinstance(days, int):
        return f"data {next_date} (n/a giorni)"
    if days < 0:
        return f"{abs(days)}gg fa ({next_date}) — recent earnings"
    if days <= 5:
        return f"🚨 in {days}gg ({next_date}) — HARD GATE"
    if days <= 14:
        return f"⚠️ in {days}gg ({next_date}) — warning"
    return f"in {days}gg ({next_date})"


def render_sfm_user_prompt(
    analysis: dict,
    *,
    as_of_date: str,
    overlay_weight: float = 0.20,
) -> str:
    """Costruisce lo user prompt SFM dal dict di ``analyze_ticker`` arricchito.

    Args:
        analysis: dict ritornato da ``enrich_with_sfm_score`` (deve contenere
            ``sector_key``, ``peer_etf``, ``score_sfm``, oltre ai campi
            standard di ``analyze_ticker``).
        as_of_date: ISO date string per anchor temporale.
        overlay_weight: peso peer-RS in score_sfm (per documentare formula).

    Returns:
        Stringa user prompt pronta per ``call_sfm_validation``.
    """
    s = analysis.get("scores", {}) or {}
    rs = analysis.get("rs_vs_sector")
    regime = analysis.get("regime")
    overlay_w_pct = f"{(1 - overlay_weight) * 100:.0f}%"
    overlay_w_pct_inv = f"{overlay_weight * 100:.0f}%"

    return SFM_USER_PROMPT_TEMPLATE.format(
        ticker=analysis.get("ticker", "?"),
        as_of_date=as_of_date,
        sector_block=_fmt_sector_block(analysis),
        sector_key=analysis.get("sector_key", "?"),
        price=_fmt_num(analysis.get("price")),
        score_composite=_fmt_num(analysis.get("score_composite"), decimals=1),
        classification=analysis.get("classification", "?"),
        score_sfm=_fmt_num(analysis.get("score_sfm"), decimals=1),
        overlay_w_pct=overlay_w_pct,
        overlay_w_pct_inv=overlay_w_pct_inv,
        s_trend=_fmt_num(s.get("trend"), decimals=0),
        s_momentum=_fmt_num(s.get("momentum"), decimals=0),
        s_volume=_fmt_num(s.get("volume"), decimals=0),
        s_dist=_fmt_num(s.get("distance_high"), decimals=0),
        s_vol=_fmt_num(s.get("volatility"), decimals=0),
        s_ma=_fmt_num(s.get("ma_cross"), decimals=0),
        peer_rs_block=_fmt_peer_rs_block(rs),
        regime_block=_fmt_regime_block(regime),
        ema_fast=_fmt_num(analysis.get("ema_fast")),
        ema_slow=_fmt_num(analysis.get("ema_slow")),
        rsi=_fmt_num(analysis.get("rsi")),
        atr=_fmt_num(analysis.get("atr")),
        atr_pct=_fmt_pct(analysis.get("atr_pct")),
        vol_ratio=_fmt_num(analysis.get("volume_ratio")),
        high_52w=_fmt_num(analysis.get("high_52w")),
        dist_from_high=_fmt_pct(analysis.get("distance_from_high_pct")),
        stop_suggested=_fmt_num(analysis.get("stop_suggested")),
        stop_pct=_fmt_pct(analysis.get("stop_pct")),
        perf_1w=_fmt_pct(analysis.get("perf_1w")),
        perf_1m=_fmt_pct(analysis.get("perf_1m")),
        perf_3m=_fmt_pct(analysis.get("perf_3m")),
        earnings_block=_fmt_earnings_block(analysis),
    )
