"""Schema versioning per i JSON store.

Ogni store (portfolio / journal / watchlist) dichiara un ``CURRENT_VERSION``.
Al load, ``migrate(payload, store)`` legge ``schema_version`` e applica la
catena di migrazioni fino alla versione corrente. Al save, ogni store scrive
``schema_version: CURRENT_VERSION``.

Le migrazioni legacy preesistenti (watchlist lista→dict, portfolio positions
lista→dict, journal ``pnl_abs→pnl_per_share``) restano nei rispettivi
``load_*``: sono già testate, non vale la pena duplicarle qui. Il versioning
parte da qui in avanti — ogni breaking change futuro aggiunge una migration
registrata in ``_MIGRATIONS``.
"""

from __future__ import annotations

from collections.abc import Callable

# Versione corrente per ogni store. Incrementa solo su breaking change dello
# schema (nuovo campo obbligatorio, rinomina, rimozione). Aggiunte backward-
# compatible (campo opzionale nuovo) NON richiedono bump.
CURRENT_VERSIONS: dict[str, int] = {
    "portfolio": 1,
    "journal": 1,
    "watchlist": 1,
}

# Mapping store → lista di migrations. ``_MIGRATIONS[store][i]`` porta dalla
# versione ``i+1`` alla ``i+2``. La migration a indice 0 porta da v1 → v2,
# etc. Il payload v0 (schema_version assente) viene trattato come v1: il
# load dei singoli store ha già gestito la forma legacy.
Migration = Callable[[dict], dict]
_MIGRATIONS: dict[str, list[Migration]] = {
    "portfolio": [],
    "journal": [],
    "watchlist": [],
}


class SchemaMigrationError(RuntimeError):
    """Payload non migrabile (versione futura sconosciuta o chain rotta)."""


def migrate(payload: dict, store: str) -> dict:
    """Porta ``payload`` all'ultima versione nota per ``store``.

    Se ``schema_version`` non è presente, assume v1 (baseline). Se è già alla
    versione corrente, no-op. Se è superiore alla versione corrente, solleva
    ``SchemaMigrationError`` — significa che un binario vecchio sta leggendo
    file scritti da un binario nuovo, situazione che non deve restare
    silenziosa (rischio di scritture che perdono campi).
    """
    if store not in CURRENT_VERSIONS:
        raise SchemaMigrationError(f"Store sconosciuto: {store}")

    current = CURRENT_VERSIONS[store]
    version = payload.get("schema_version", 1)

    if not isinstance(version, int) or version < 1:
        raise SchemaMigrationError(
            f"schema_version invalida in {store}: {version!r}"
        )
    if version > current:
        raise SchemaMigrationError(
            f"schema_version {version} di {store} è superiore alla versione "
            f"corrente {current}. Binario obsoleto? Aggiorna il package."
        )

    chain = _MIGRATIONS[store]
    while version < current:
        migration = chain[version - 1]
        payload = migration(payload)
        version += 1

    payload["schema_version"] = current
    return payload


def stamp_version(payload: dict, store: str) -> dict:
    """Scrive ``schema_version`` corrente in-place. Chiamato da ``save_*``."""
    if store not in CURRENT_VERSIONS:
        raise SchemaMigrationError(f"Store sconosciuto: {store}")
    payload["schema_version"] = CURRENT_VERSIONS[store]
    return payload
