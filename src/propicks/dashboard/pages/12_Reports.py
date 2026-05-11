"""Reports — genera e visualizza report weekly/monthly.

Equivalent UI di ``propicks-report weekly/monthly``. I report salvati in
``reports/`` restano la source of truth (gitignored) — la dashboard li elenca
e ne mostra il contenuto markdown.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import streamlit as st

# Bridge st.secrets → env vars (precede import propicks.config).
from propicks.dashboard import _bootstrap  # noqa: F401
from propicks.config import DATE_FMT, REPORTS_DIR
from propicks.dashboard._shared import invariants_note, page_header
from propicks.reports.attribution_report import weekly_attribution_report
from propicks.reports.monthly import generate_monthly_report
from propicks.reports.weekly import generate_weekly_report

st.set_page_config(page_title="Reports · Propicks", layout="wide")
page_header(
    "Reports",
    "Genera report weekly/monthly. I file vengono salvati in `reports/` e sono "
    "la fonte per la review del sabato.",
)
invariants_note()


def _save_report(content: str, filename: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


tab_gen, tab_view = st.tabs(["Genera nuovo", "Sfoglia archivio"])

# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
with tab_gen:
    st.caption("La generazione scarica i prezzi correnti per l'unrealized P&L — può richiedere qualche secondo.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("Genera weekly report", type="primary", width="stretch"):
            with st.spinner("Genero report settimanale…"):
                content = generate_weekly_report()
                path = _save_report(
                    content, f"weekly_{datetime.now().strftime(DATE_FMT)}.md"
                )
            st.success(f"Salvato: `{path}`")
            st.markdown("---")
            st.markdown(content)
            st.download_button(
                "Download .md",
                data=content,
                file_name=os.path.basename(path),
                mime="text/markdown",
                key="download_weekly_generated",
            )

    with col2:
        if st.button("Genera monthly report", width="stretch"):
            with st.spinner("Genero report mensile…"):
                content = generate_monthly_report()
                path = _save_report(
                    content, f"monthly_{datetime.now().strftime('%Y-%m')}.md"
                )
            st.success(f"Salvato: `{path}`")
            st.markdown("---")
            st.markdown(content)
            st.download_button(
                "Download .md",
                data=content,
                file_name=os.path.basename(path),
                mime="text/markdown",
                key="download_monthly_generated",
            )

    with col4:
        if st.button(
            "📄 PDF Weekly Review",
            width="stretch",
            help="PDF stampa-friendly: equity curve + bucket pie + sector + top positions + recent closings",
            key="btn_pdf_weekly",
        ):
            from propicks.reports.pdf_weekly_review import generate_weekly_pdf
            with st.spinner("Generating PDF weekly review…"):
                pdf_bytes, pdf_path = generate_weekly_pdf()
            st.success(f"PDF salvato: `{pdf_path}`")
            st.download_button(
                "📄 Download PDF",
                data=pdf_bytes,
                file_name=os.path.basename(pdf_path),
                mime="application/pdf",
                key="download_pdf_weekly",
            )

    with col3:
        if st.button(
            "Genera attribution (Phase 9)",
            width="stretch",
            help="Decomposition α/β/sector/timing + Phase 7 gate status",
        ):
            with st.spinner("Genero attribution report…"):
                result = weekly_attribution_report()
                path = result["path"]
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            st.success(
                f"Salvato: `{path}` — {result['n_closed_this_week']} trade chiusi questa settimana"
            )
            st.markdown("---")
            st.markdown(content)
            st.download_button(
                "Download .md",
                data=content,
                file_name=os.path.basename(path),
                mime="text/markdown",
                key="download_attribution_generated",
            )

    # ─── AI Weekly Review (Phase 2 time-series) ─────────────────────────────
    st.divider()
    st.subheader("🤖 AI Weekly Review (portfolio-level)")
    st.caption(
        "Review meta-level del portfolio (NON per-ticker entry validation). "
        "Confronta settimana corrente vs ultime N settimane → action items "
        "prioritizzati. Phase 2: time-series comparison."
    )

    with st.expander("📤 Genera prompt → 📥 Incolla risposta LLM", expanded=False):
        from propicks.ai.user_prompts import (
            llm_generic_weekly_review_full,
            perplexity_weekly_review_full,
            sonar_weekly_review_full,
        )
        from propicks.io.weekly_review_store import (
            build_portfolio_snapshot,
            load_review_history,
            load_weekly_review_verdicts,
            save_weekly_review,
        )

        wr_target = st.radio(
            "Target LLM",
            options=[
                "Sonar (Perplexity nativo) — Recommended",
                "Perplexity Pro (Claude/GPT/Gemini via Pro)",
                "Claude.ai / ChatGPT / Gemini diretto",
            ],
            index=0,
            horizontal=False,
            key="wr_target",
        )

        wr_n_weeks = st.slider(
            "N settimane storiche da includere (Phase 2 time-series)",
            min_value=0, max_value=12, value=4, step=1,
            key="wr_n_weeks",
            help="0 = no history. Più alto = trend più rilevanti ma prompt più lungo.",
        )

        if st.button("🔄 Genera prompt", type="primary", key="btn_gen_wr_prompt"):
            with st.spinner("Building snapshot + prior history…"):
                snapshot = build_portfolio_snapshot()
                history = (
                    load_review_history(n_weeks=int(wr_n_weeks))
                    if wr_n_weeks > 0 else []
                )
                st.session_state["wr_snapshot"] = snapshot
                st.session_state["wr_history_count"] = len(history)
                today = datetime.now().strftime(DATE_FMT)
                if wr_target.startswith("Sonar"):
                    prompt = sonar_weekly_review_full(
                        snapshot, history, as_of_date=today,
                    )
                elif wr_target.startswith("Perplexity Pro"):
                    prompt = perplexity_weekly_review_full(
                        snapshot, history, as_of_date=today,
                    )
                else:
                    prompt = llm_generic_weekly_review_full(
                        snapshot, history, as_of_date=today,
                    )
                st.session_state["wr_prompt"] = prompt

        if st.session_state.get("wr_prompt"):
            st.success(
                f"Prompt generato ({len(st.session_state['wr_prompt']):,} char · "
                f"~{len(st.session_state['wr_prompt']) // 4:,} token · "
                f"{st.session_state.get('wr_history_count', 0)} settimane storiche)"
            )
            st.code(st.session_state["wr_prompt"], language="markdown")

        st.markdown("##### 📥 Incolla risposta LLM")
        wr_source = st.selectbox(
            "Source LLM",
            options=["perplexity_pro", "sonar", "claude_web", "gpt", "gemini", "other"],
            key="wr_source",
        )
        wr_paste = st.text_area(
            "Risposta LLM (full text con JSON)",
            placeholder="Incolla risposta LLM qui...",
            height=200,
            key="wr_paste",
        )
        if wr_paste and wr_paste.strip():
            from propicks.io.manual_verdicts_store import parse_paste
            preview = parse_paste(wr_paste)
            try:
                _payload = json.loads(preview.get("parsed_payload") or "{}")
            except (json.JSONDecodeError, ValueError):
                _payload = {}
            health = _payload.get("health_verdict") or "—"
            conviction = _payload.get("conviction_score") or "—"
            n_actions = len(_payload.get("action_items") or [])
            pcc = st.columns(3)
            pcc[0].metric("Health verdict", health)
            pcc[1].metric("Conviction", f"{conviction}/10" if conviction != "—" else "—")
            pcc[2].metric("Action items", n_actions)

            if st.button("💾 Save weekly review", type="primary", key="btn_save_wr"):
                snap = st.session_state.get("wr_snapshot")
                if snap is None:
                    st.error("Snapshot non disponibile — clicca prima '🔄 Genera prompt'.")
                else:
                    try:
                        vid = save_weekly_review(
                            snap, wr_paste, source=wr_source,
                        )
                        st.success(
                            f"✓ Weekly review salvato (verdict #{vid}). "
                            "Visibile in History qui sotto."
                        )
                        # Clear paste
                        st.session_state.pop("wr_prompt", None)
                        st.session_state.pop("wr_snapshot", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Save fallito: {exc}")

    # ─── History timeline ─────────────────────────────────────────────────────
    with st.expander("📅 History weekly reviews", expanded=False):
        from propicks.io.weekly_review_store import load_weekly_review_verdicts as _lwv

        wr_history = _lwv(limit=12)
        if not wr_history:
            st.info("Nessun weekly review salvato. Genera + salva il primo sopra.")
        else:
            # Timeline cards
            for r in wr_history:
                health = r.get("health_verdict") or "—"
                color_map = {
                    "HEALTHY": "🟢", "REBALANCE": "🟡",
                    "CONCERN": "🟠", "CRITICAL": "🔴",
                }
                emoji = color_map.get(health, "⚪")
                snap = r.get("snapshot") or {}
                wk_avg = snap.get("week_avg_pnl") or 0
                pf_val = snap.get("portfolio_value") or 0

                with st.container(border=True):
                    cc1, cc2, cc3, cc4 = st.columns([2, 1, 1, 1])
                    cc1.markdown(
                        f"**{emoji} {health}**  ·  {r.get('pasted_at', '—')}  ·  "
                        f"source: `{r.get('source', '—')}`"
                    )
                    cc2.metric("Conviction", f"{r.get('conviction', '—')}/10")
                    cc3.metric("Pf value", f"€ {pf_val:,.0f}")
                    cc4.metric("Wk avg P&L", f"{wk_avg:+.2f}%")
                    if r.get("executive_summary"):
                        st.markdown(f"_{r['executive_summary']}_")
                    if r.get("action_items"):
                        with st.expander(
                            f"📋 Action items ({len(r['action_items'])})",
                            expanded=False,
                        ):
                            for a in r["action_items"]:
                                pri = a.get("priority", "—")
                                pri_emoji = {
                                    "HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢",
                                }.get(pri, "⚪")
                                st.markdown(
                                    f"- {pri_emoji} **{pri}** — {a.get('action', '—')}  \n"
                                    f"  _Why_: {a.get('rationale', '—')}  \n"
                                    f"  _Deadline_: `{a.get('deadline', '—')}`"
                                )

# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------
with tab_view:
    if not os.path.isdir(REPORTS_DIR):
        st.info("Nessun report generato.")
    else:
        files = sorted(
            (f for f in os.listdir(REPORTS_DIR) if f.endswith(".md")),
            reverse=True,
        )
        if not files:
            st.info("Nessun report markdown in `reports/`.")
        else:
            selected = st.selectbox("Seleziona report", files)
            path = os.path.join(REPORTS_DIR, selected)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            st.caption(f"Ultima modifica: {mtime.strftime('%Y-%m-%d %H:%M')}")
            with open(path) as f:
                content = f.read()
            st.markdown(content)
            st.download_button(
                "Download .md",
                data=content,
                file_name=selected,
                mime="text/markdown",
                key=f"download_archive_{selected}",
            )
