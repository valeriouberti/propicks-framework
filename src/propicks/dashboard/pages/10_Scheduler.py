"""Scheduler page — alerts queue + job history (Phase 3 + 8).

Mirror dashboard di ``propicks-scheduler alerts/history/job``. Mostra:
- Tab Alerts: pending queue + bulk ack
- Tab History: ultimi job run + stats affidabilità (success rate, avg duration)
- Tab Manual trigger: lancio one-shot di un job (per backfill o test)
"""

from __future__ import annotations

import streamlit as st

from propicks.dashboard._shared import invariants_note, page_header

st.set_page_config(page_title="Scheduler · Propicks", layout="wide")
page_header(
    "Scheduler",
    "Alert queue + job history + manual trigger. Mirror di `propicks-scheduler`.",
)
invariants_note()

tab_alerts, tab_hist, tab_trigger = st.tabs([
    "🔔 Alert pending",
    "📜 Job history",
    "▶️ Manual trigger",
])

# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
with tab_alerts:
    from propicks.scheduler.alerts import (
        acknowledge_alert,
        acknowledge_all,
        list_pending_alerts,
        stats,
    )

    col1, col2, col3 = st.columns([1, 1, 2])
    if col1.button("✅ Ack all", type="primary"):
        n = acknowledge_all()
        st.toast(f"Acknowledged {n} alert", icon="✅")
        st.rerun()

    alert_stats = stats()
    col2.metric("Pending totale", alert_stats["pending_total"])

    if alert_stats["by_type"]:
        with st.expander("Breakdown per tipo", expanded=False):
            st.dataframe(alert_stats["by_type"], width="stretch", hide_index=True)

    alerts = list_pending_alerts(limit=100)
    if not alerts:
        st.success("Nessun alert pending — sei aggiornato.")
    else:
        sev_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
        for alert in alerts:
            sev = alert.get("severity", "info")
            emoji = sev_emoji.get(sev, "📢")
            ticker = alert.get("ticker", "")
            msg_first_line = (alert.get("message") or "").split("\n")[0]

            with st.container(border=True):
                col_a, col_b, col_c = st.columns([3, 1, 1])
                col_a.markdown(
                    f"{emoji} **{alert.get('type', 'alert')}** "
                    f"{'`' + ticker + '`' if ticker else ''}  \n"
                    f"_{msg_first_line}_  \n"
                    f"ID: `{alert['id']}` · {alert['created_at']} · sev: {sev}"
                )
                metadata = alert.get("metadata")
                if metadata:
                    with col_a.expander("Metadata", expanded=False):
                        st.json(metadata)
                if col_c.button("Ack", key=f"ack_{alert['id']}"):
                    acknowledge_alert(alert["id"])
                    st.toast(f"Alert {alert['id']} ack", icon="✅")
                    st.rerun()

# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
with tab_hist:
    from propicks.scheduler.history import list_recent_runs, stats_by_job

    st.subheader("Stats per job (ultimi 30gg)")
    stats_rows = stats_by_job(days=30)
    if not stats_rows:
        st.caption("_Nessun run registrato negli ultimi 30gg._")
    else:
        _SUCCESS_EMOJI = lambda rate: (  # noqa: E731
            "🟢" if rate >= 0.95 else "🟡" if rate >= 0.80 else "🔴"
        )
        display_rows = []
        for r in stats_rows:
            rate = (r["success"] / r["total"]) if r["total"] else 0
            display_rows.append({
                "Job": r["job_name"],
                "Total": r["total"],
                "Success": r["success"],
                "Errors": r["errors"],
                "Rate": f"{_SUCCESS_EMOJI(rate)} {rate * 100:.0f}%",
                "Avg duration": f"{r['avg_duration_ms'] or 0:.0f}ms",
            })
        st.dataframe(display_rows, width="stretch", hide_index=True)

    st.divider()
    st.subheader("Ultimi 20 run")
    recent = list_recent_runs(limit=20)
    if not recent:
        st.caption("_Nessun run._")
    else:
        _STATUS_BADGE = {
            "success": "✅", "error": "❌", "running": "⏳", "partial": "◐",
        }
        recent_rows = [
            {
                "ID": r["id"],
                "Job": r["job_name"],
                "Started": r["started_at"],
                "Status": f"{_STATUS_BADGE.get(r['status'], '?')} {r['status']}",
                "Duration": f"{r['duration_ms'] or 0}ms" if r["duration_ms"] else "—",
                "Items": r["n_items"] if r["n_items"] is not None else "—",
                "Error": (r["error"] or "")[:60] if r["error"] else "—",
            }
            for r in recent
        ]
        st.dataframe(recent_rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Manual trigger
# ---------------------------------------------------------------------------
with tab_trigger:
    st.caption(
        "Lancia un job scheduler one-shot. Utile per backfill (es. snapshot "
        "per una data passata) o test manuale prima di automation. "
        "Il job gira **SINCRONO** — la page si blocca fino al completamento."
    )

    from propicks.scheduler import jobs as _jobs

    job_map = {
        "record_regime": "Regime macro weekly ^GSPC",
        "snapshot_portfolio": "Snapshot portfolio (equity + exposure)",
        "warm_cache": "Warm cache OHLCV per tutti i ticker attivi",
        "scan_watchlist": "Scan live + populate strategy_runs",
        "trailing_stop_check": "Suggest trailing stop updates",
        "check_earnings_calendar": "Earnings upcoming alerts",
        "cleanup_stale_watchlist": "Flag watchlist > 60gg",
        "weekly_attribution_report_job": "Attribution report sabato",
    }

    with st.form("manual_trigger_form", border=True):
        selected = st.selectbox(
            "Job da eseguire",
            options=list(job_map.keys()),
            format_func=lambda k: f"{k} — {job_map[k]}",
        )
        # Backfill date per jobs che lo supportano
        use_date = st.checkbox("Override data (solo snapshot/regime)")
        custom_date = st.text_input(
            "Data (YYYY-MM-DD)",
            value="",
            disabled=not use_date,
            placeholder="2026-04-23",
        )
        submitted = st.form_submit_button("▶️ Esegui job", type="primary")

    if submitted:
        fn = getattr(_jobs, selected, None)
        if fn is None:
            st.error(f"Job `{selected}` non trovato")
        else:
            kwargs: dict = {}
            if use_date and custom_date:
                if selected == "snapshot_portfolio":
                    kwargs["snapshot_date"] = custom_date
                elif selected == "record_regime":
                    kwargs["record_date"] = custom_date

            with st.spinner(f"Eseguo {selected}…"):
                try:
                    result = fn(**kwargs)
                    st.success(
                        f"✅ **{selected}** completato — "
                        f"items={result.get('n_items', '?')}, "
                        f"notes: _{result.get('notes', '—')}_"
                    )
                except Exception as exc:
                    st.error(f"❌ **{selected}** fallito: {type(exc).__name__}: {exc}")
