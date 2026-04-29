"""Bootstrap della dashboard: bridge ``st.secrets`` → ``os.environ``.

Streamlit Community Cloud espone i secrets via ``st.secrets`` (mapping). Il
resto del codice (config, io/db, ai) legge env vars — questo bridge mantiene
una sola convenzione lato applicativo (env), così il codice è identico in
locale (.env loaded via python-dotenv) e in cloud (secrets → env).

**Importante**: questo modulo deve essere importato come prima linea di
``app.py`` (e di ogni page in ``pages/``) PRIMA di qualsiasi import da
``propicks.*``, perché ``propicks.config`` legge ``ANTHROPIC_API_KEY`` /
``DB_FILE`` / ecc. al primo import a livello di modulo. Una volta importato,
i side-effect (env set) sono visibili al resto del processo.

In locale il modulo è no-op: senza ``.streamlit/secrets.toml`` ``st.secrets``
solleva ``StreamlitSecretNotFoundError`` e il bridge esce silenzioso.
"""

from __future__ import annotations

import os

_BRIDGED = False

# Env vars portate da st.secrets quando in Streamlit Cloud.
_BRIDGE_KEYS = (
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "PROPICKS_AI_MODEL",
    "PROPICKS_AI_WEB_SEARCH",
    "PROPICKS_AI_WEB_SEARCH_MAX_USES",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def bridge_secrets_to_env() -> None:
    """Idempotente: chiamabile da ogni page senza overhead."""
    global _BRIDGED
    if _BRIDGED:
        return
    try:
        import streamlit as st
        secrets = st.secrets
    except Exception:
        # Streamlit non installato (CLI standalone) o secrets.toml assente
        # in dev locale → nessun bridge necessario.
        _BRIDGED = True
        return

    for k in _BRIDGE_KEYS:
        try:
            if k in secrets and not os.environ.get(k):
                os.environ[k] = str(secrets[k])
        except Exception:
            # secrets backend not initialised (es. locale senza secrets.toml).
            break
    _BRIDGED = True


# Esegui al primo import.
bridge_secrets_to_env()
