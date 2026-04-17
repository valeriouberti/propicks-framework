# ---------------------------------------------------------------------------
# Propicks dashboard — Dockerfile
#
# Immagine autosufficiente che serve la Streamlit UI su :8501.
# La CLI resta disponibile nel container (propicks-scan, propicks-portfolio,
# propicks-journal, propicks-report, propicks-rotate) — è lo stesso package.
#
# Build:
#     docker build -t propicks-dashboard .
#
# Run (monta data/ e reports/ come volumi per persistenza):
#     docker run --rm -p 8501:8501 \
#         -v "$(pwd)/data":/app/data \
#         -v "$(pwd)/reports":/app/reports \
#         --env-file .env \
#         propicks-dashboard
#
# Poi apri http://localhost:8501
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Installazione dipendenze separata dal copy del source per cache layer più efficiente:
# pyproject.toml cambia raramente, i sorgenti cambiano spesso.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --upgrade pip \
 && pip install -e ".[dashboard]"

# Streamlit non deve aprire browser dentro il container, deve servire esternamente
# e non chiedere email al primo launch.
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

# data/ e reports/ sono volume mount expected — i file generati runtime
# devono sopravvivere alla ricreazione del container.
VOLUME ["/app/data", "/app/reports"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3)" || exit 1

CMD ["streamlit", "run", "src/propicks/dashboard/app.py"]
