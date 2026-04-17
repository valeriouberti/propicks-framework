"""Entry point CLI ``propicks-dashboard`` → lancia streamlit.

Usa l'API programmatica di streamlit (``streamlit.web.bootstrap.run``) invece
di spawnare un subprocess: più pulito, nessun problema di PATH, e propaga
segnali/exit code correttamente.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from streamlit.web import bootstrap
    except ImportError:
        print(
            "[errore] streamlit non installato. Installa la dashboard con:\n"
            "    pip install -e '.[dashboard]'",
            file=sys.stderr,
        )
        return 1

    app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    # args dopo ``streamlit run`` — forward di eventuali flag passati dal trader
    # (es. --server.port 8502)
    flag_options: dict = {}
    bootstrap.run(app_path, is_hello=False, args=sys.argv[1:], flag_options=flag_options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
