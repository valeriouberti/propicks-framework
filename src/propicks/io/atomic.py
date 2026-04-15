"""Scritture JSON atomiche.

Isolare questa funzione qui permette a domain/ di restare puro e a tutti
i moduli di persistenza di condividere un'unica implementazione.
"""

from __future__ import annotations

import json
import os
from typing import Any


def atomic_write_json(path: str, data: Any) -> None:
    """Scrive JSON in modo atomico: tmp + fsync + rename.

    Se il processo crasha durante la scrittura, il file originale resta
    intatto. Necessario per portfolio.json e journal.json che sono la
    single source of truth del trading engine.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
