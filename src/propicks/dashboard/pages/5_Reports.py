"""Reports — genera e visualizza report weekly/monthly.

Equivalent UI di ``propicks-report weekly/monthly``. I report salvati in
``reports/`` restano la source of truth (gitignored) — la dashboard li elenca
e ne mostra il contenuto markdown.
"""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st

from propicks.config import DATE_FMT, REPORTS_DIR
from propicks.dashboard._shared import invariants_note, page_header
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

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Genera weekly report", type="primary", use_container_width=True):
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
            )

    with col2:
        if st.button("Genera monthly report", use_container_width=True):
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
            )
