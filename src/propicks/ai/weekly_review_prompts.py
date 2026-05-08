"""Weekly portfolio review — system + user prompts.

Persona: senior portfolio manager / risk consultant — meta-level review,
NON per-ticker entry verdict. Valuta SISTEMA portfolio:
- Strategy mix (momentum / contrarian / ETF / thematic)
- Bucket cap saturation
- Sector concentration drift
- Position ageing (time-stop watch)
- AI verdict accuracy trend
- Cadence health (n_trade settimana, win rate)

**Phase 2 Time-series**: include comparison settimana N vs N-1 / N-4 quando
storia disponibile. Se prima review della history → graceful fallback "no
prior data, baseline review".

Schema output: ``WeeklyReviewVerdict`` in ``claude_client.py``.
Cadence: weekly (sabato review). Cache key: (year, ISO_week).
"""

from __future__ import annotations

from datetime import date


WEEKLY_REVIEW_SYSTEM_PROMPT = """You are a senior portfolio manager and risk consultant (20+ years, multi-asset book at long-only institutional fund). Your role: weekly meta-review of a retail trader's portfolio, NOT per-ticker entry validation.

# Your role
- Strategic review: portfolio health, balance, drift, action items.
- Time-series perspective: compare current week vs prior 1-4 weeks when data provided.
- Bias toward concrete actionable findings over generic advice.
- Talk like a PM mentor: direct, specific, no fluff.

# What you analyze (priority order)
1. **Bucket cap saturation**: Stock bucket (momentum + contrarian) ≤ 40%, ETF bucket (rotation + thematic) ≤ 60%, Cash ≥ 20%. If ≥80% of any cap → action HIGH.
2. **Sector concentration**: any single sector > 30% of mtm capital → diversification action.
3. **Strategy mix balance**: portfolio diversified across momentum / contrarian / ETF rotation / thematic, OR over-concentrated in one strategy?
4. **Position ageing**: positions held > 60gg without P&L progress → time-stop candidates. Contrarian > 15gg → stale (mean reversion expired). Momentum > 90gg flat → consider rotation.
5. **Drawdown discipline**: weekly P&L vs 5% loss cap, monthly P&L vs 15% cap.
6. **Regime alignment**: open positions consistent with current regime weekly? Stock momentum positions in BEAR regime = misaligned.
7. **AI verdict accuracy trend**: if manual_ai_verdicts data provided, comment on directional accuracy trend over time.
8. **Cash deployment**: cash idle > 30% in BULL regime = under-invested opportunity cost. Cash < 20% always = invariant violation.

# Time-series comparison (Phase 2)
When user message includes prior_weeks data:
- Compare equity curve / P&L trend (improving / stable / deteriorating)
- Compare drawdown evolution
- Detect strategy mix shift over weeks
- AI accuracy trend if available

If no prior weeks (first review): treat as baseline, set time_series_trend fields to "INSUFFICIENT_DATA" or descriptive "first review — baseline".

# Hard computable rules
- IF stock_bucket_pct > 35% AND regime ∈ {BEAR, STRONG_BEAR} → action_item HIGH "reduce stock exposure to ≤25%"
- IF cash_pct < 25% AND regime ∈ {BEAR, STRONG_BEAR} → action_item HIGH "increase cash buffer to 30%"
- IF any single position > 12% mtm → action_item MEDIUM "trim concentration on TICKER"
- IF weekly_pnl_pct < -4% AND weekly_cap_pct = 5% → health_verdict CRITICAL
- IF n_positions ≥ 9/10 → action_item MEDIUM "review marginal positions for closure"
- IF sector_concentration_top > 30% → action_item HIGH "diversify sector exposure"
- IF same_strategy_pct > 60% of invested → action_item LOW "consider strategy diversification"
- IF n_thematic = 2/2 cap AND parent_aggregate ≥ 22% → action_item MEDIUM "thematic bucket near cap, no new entries possible"
- health_verdict CRITICAL requires AT LEAST 2 HIGH action items + (weekly breach OR cap saturation)

# Anti-fluff
- Do NOT say "diversify your portfolio" — say "reduce TKR1 from 14% to ≤10%, redirect to TKR2 or cash".
- Do NOT say "monitor closely" — say "if TKR1 closes below STOP this week, exit and review".
- Specific tickers, percentages, deadlines. No generic advice.

# Output rules
- Single valid JSON object matching schema. No prose outside.
- All counts/percentages from data provided in user message — do not fabricate.
- `action_items`: 3-7 items max, prioritized HIGH first.
- `strengths` and `weaknesses`: 3-5 items each, balanced view.
- `executive_summary`: 2-3 sentences, plain English, no jargon.
- Self-consistency: HEALTHY requires 0 HIGH action items + bucket caps OK + cash ≥ 20%. CRITICAL requires ≥2 HIGH + breach.

# Time horizon
You evaluate the LAST WEEK (closed). Action items for THE COMING WEEK.
Reference dates explicitly when comparing trends."""


def render_weekly_review_user_prompt(
    portfolio_data: dict,
    prior_weeks: list[dict] | None = None,
    *,
    as_of_date: str | None = None,
) -> str:
    """Costruisce user prompt per portfolio review settimanale.

    Args:
        portfolio_data: dict con keys richiesti:
            - portfolio_value, cost_basis, cash, cash_pct
            - stock_pct, etf_pct
            - n_positions, max_positions
            - unrealized_eur, unrealized_pct
            - positions: list[dict per posizione]
            - sector_exp: dict sector → frazione
            - week_pnls: list of P&L% closed this week
            - week_avg_pnl, week_win_rate
            - recent_closings: list trade chiusi recenti
            - accuracy: dict da compute_accuracy() o None
            - regime: dict regime corrente o None
            - bucket_cap_status: dict (stock_cap=0.40, etf_cap=0.60, cash_min=0.20)
        prior_weeks: list di portfolio_data dei N weeks precedenti (più
            recente in coda). None o [] = first review.
        as_of_date: ISO YYYY-MM-DD per ancorare la review.
    """
    today = as_of_date or date.today().isoformat()
    out = [f"# Portfolio Weekly Review — as of {today}\n"]

    # ─── Current week snapshot ───
    out.append("## 📊 Current week snapshot")
    out.append(f"- Portfolio value (mtm): € {portfolio_data.get('portfolio_value', 0):,.2f}")
    out.append(f"- Cost basis: € {portfolio_data.get('cost_basis', 0):,.2f}")
    out.append(f"- Unrealized P&L: € {portfolio_data.get('unrealized_eur', 0):+,.2f} ({portfolio_data.get('unrealized_pct', 0):+.2f}%)")
    out.append(f"- Cash: € {portfolio_data.get('cash', 0):,.2f} ({portfolio_data.get('cash_pct', 0):.1f}%)")
    out.append(f"- Positions: {portfolio_data.get('n_positions', 0)}/{portfolio_data.get('max_positions', 10)}")
    out.append("")

    out.append("## 🪣 Bucket allocation vs cap")
    cap_status = portfolio_data.get("bucket_cap_status") or {}
    out.append(
        f"- Stock bucket (mom+contra): **{portfolio_data.get('stock_pct', 0):.1f}% / "
        f"{cap_status.get('stock_cap', 0.40) * 100:.0f}% cap**"
    )
    out.append(
        f"- ETF bucket (rotation+thematic): **{portfolio_data.get('etf_pct', 0):.1f}% / "
        f"{cap_status.get('etf_cap', 0.60) * 100:.0f}% cap**"
    )
    out.append(
        f"- Cash: **{portfolio_data.get('cash_pct', 0):.1f}% / "
        f"min {cap_status.get('cash_min', 0.20) * 100:.0f}%**"
    )
    out.append("")

    # Regime
    regime = portfolio_data.get("regime")
    if regime:
        out.append(f"## 🌡 Regime macro")
        out.append(
            f"- {regime.get('regime', '?')} ({regime.get('regime_code', '?')}/5) · "
            f"entry allowed: {regime.get('entry_allowed')}"
        )
        out.append("")

    # Open positions detail
    positions = portfolio_data.get("positions") or []
    if positions:
        out.append("## 📂 Open positions")
        out.append("| Ticker | Strategy | Sector | Days | Cost € | MV € | P&L% | % cap |")
        out.append("|---|---|---|---:|---:|---:|---:|---:|")
        for p in positions:
            out.append(
                f"| {p.get('ticker')} | {p.get('strategy', '—')} | "
                f"{p.get('sector', '—')} | {p.get('days_held', '—')} | "
                f"{p.get('cost_basis', 0):.2f} | {p.get('mv', 0):.2f} | "
                f"{p.get('pnl_pct', 0):+.2f}% | {p.get('size_pct', 0):.1f}% |"
            )
        out.append("")

    # Sector concentration
    sector_exp = portfolio_data.get("sector_exp") or {}
    if sector_exp:
        out.append("## 🏢 Sector concentration")
        sorted_sec = sorted(sector_exp.items(), key=lambda x: x[1], reverse=True)
        for sec, pct in sorted_sec:
            out.append(f"- {sec}: {pct * 100:.1f}%")
        out.append("")

    # Weekly performance
    week_pnls = portfolio_data.get("week_pnls") or []
    if week_pnls:
        out.append("## 📈 Performance settimanale (trade chiusi 7gg)")
        out.append(f"- N closed: {len(week_pnls)}")
        out.append(f"- Win rate: {portfolio_data.get('week_win_rate', 0) * 100:.1f}%")
        out.append(f"- Avg P&L: {portfolio_data.get('week_avg_pnl', 0):+.2f}%")
        if week_pnls:
            out.append(f"- Best/Worst: {max(week_pnls):+.2f}% / {min(week_pnls):+.2f}%")
        out.append("")

    # Recent closings
    recent = portfolio_data.get("recent_closings") or []
    if recent:
        out.append("## 💰 Ultime chiusure (top 5)")
        out.append("| Date | Ticker | Strategy | Days | P&L% | Reason |")
        out.append("|---|---|---|---:|---:|---|")
        for t in recent[:5]:
            out.append(
                f"| {t.get('exit_date', '—')} | {t.get('ticker', '—')} | "
                f"{t.get('strategy', '—')} | {t.get('duration_days', '—')} | "
                f"{t.get('pnl_pct', 0):+.2f}% | "
                f"{(t.get('exit_reason') or '—')[:40]} |"
            )
        out.append("")

    # AI accuracy
    accuracy = portfolio_data.get("accuracy") or {}
    if accuracy.get("n_total", 0) > 0:
        out.append("## 🤖 AI verdict accuracy (manual paste)")
        out.append(f"- N directional verdicts: {accuracy.get('n_directional', 0)}")
        if accuracy.get('accuracy') is not None:
            out.append(f"- Accuracy: {accuracy['accuracy'] * 100:.1f}%")
        if accuracy.get('brier_score') is not None:
            out.append(f"- Brier score: {accuracy['brier_score']:.3f}")
        out.append(
            f"- Confusion: CONFIRM+WIN={accuracy.get('n_confirm_win', 0)}, "
            f"CONFIRM+LOSS={accuracy.get('n_confirm_loss', 0)}, "
            f"REJECT+LOSS={accuracy.get('n_reject_loss', 0)}, "
            f"REJECT+WIN={accuracy.get('n_reject_win', 0)}"
        )
        out.append("")

    # ─── Phase 2: Time-series prior weeks ───
    if prior_weeks:
        out.append("## 📅 Time-series — settimane precedenti")
        out.append(f"_{len(prior_weeks)} weeks of prior history. Più recente in fondo._\n")
        out.append("| Week | Pf value | Stock% | ETF% | Cash% | Wk P&L | Wk WR | N pos |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for w in prior_weeks:
            wk_avg = w.get('week_avg_pnl', 0)
            wk_wr = w.get('week_win_rate', 0)
            out.append(
                f"| {w.get('as_of', '—')} | "
                f"{w.get('portfolio_value', 0):,.0f} | "
                f"{w.get('stock_pct', 0):.1f}% | "
                f"{w.get('etf_pct', 0):.1f}% | "
                f"{w.get('cash_pct', 0):.1f}% | "
                f"{wk_avg:+.2f}% | "
                f"{wk_wr * 100:.0f}% | "
                f"{w.get('n_positions', 0)} |"
            )
        out.append("")
        out.append(
            "_Trend comparison nel verdict: pnl_trend, drawdown_evolution, "
            "strategy_mix_shift, ai_accuracy_trend._"
        )
    else:
        out.append("## 📅 Time-series")
        out.append(
            "_First review — no prior weeks data. "
            "Set time_series_trend fields to 'INSUFFICIENT_DATA — first baseline review'._"
        )
    out.append("")

    # Task
    out.append("---\n# Task")
    out.append(
        "Analyze the portfolio state above and produce a weekly review verdict "
        "matching the JSON schema. Focus on: bucket cap drift, concentration "
        "risk, strategy mix balance, regime alignment, AI accuracy trend "
        f"({len(prior_weeks) if prior_weeks else 0} prior weeks available). "
        "Return the JSON object now."
    )

    return "\n".join(out)
