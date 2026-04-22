"""Logger JSON strutturato su stderr.

Ogni record è una singola riga JSON con ``ts`` (UTC ISO8601), ``level``,
``logger``, ``msg`` e un dizionario ``ctx`` opzionale per i campi
applicativi (ticker, duration_ms, cost_usd, ...). Scrivere su stderr
lascia libero stdout per l'output utile della CLI (tabelle, JSON di
dati) — i log non si mescolano con i dati.

Sink di default = stderr. In ambiente CLI/dashboard è sufficiente; in
produzione con container il runtime (docker/journald) cattura stderr.
Niente dipendenze esterne: stdlib ``logging`` + un custom formatter.

Il livello minimo è pilotato da ``PROPICKS_LOG_LEVEL`` (default INFO);
``PROPICKS_LOG_FORMAT=text`` degrada a formato human-readable, utile
in dev locale.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_LOGGER_NAME_ROOT = "propicks"
_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """Formatter che emette una singola riga JSON per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict) and ctx:
            payload["ctx"] = ctx
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    """Fallback human-readable per dev locale (PROPICKS_LOG_FORMAT=text)."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        head = f"{ts} {record.levelname:5s} {record.name} {record.getMessage()}"
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict) and ctx:
            kvs = " ".join(f"{k}={v}" for k, v in ctx.items())
            head = f"{head} [{kvs}]"
        if record.exc_info:
            head = f"{head}\n{self.formatException(record.exc_info)}"
        return head


def _configure_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("PROPICKS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("PROPICKS_LOG_FORMAT", "json").lower()

    root = logging.getLogger(_LOGGER_NAME_ROOT)
    root.setLevel(level)
    root.propagate = False

    if not root.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(_TextFormatter() if fmt == "text" else _JsonFormatter())
        root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Restituisce un logger namespaced sotto ``propicks.*``.

    Esempio: ``get_logger("ai.claude")`` → logger ``propicks.ai.claude``.
    """
    _configure_once()
    if name.startswith(_LOGGER_NAME_ROOT):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME_ROOT}.{name}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **ctx: Any,
) -> None:
    """Helper per emettere un evento strutturato.

    ``event`` è il nome logico dell'evento (es. ``ai_call_success``,
    ``yf_benchmark_unavailable``). I kwargs finiscono in ``ctx``.
    """
    logger.log(level, event, extra={"ctx": ctx})
