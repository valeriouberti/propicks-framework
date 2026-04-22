"""Test del logger JSON strutturato."""

from __future__ import annotations

import json
import logging

from propicks.obs.log import get_logger


def test_get_logger_is_namespaced_under_propicks():
    logger = get_logger("ai.claude")
    assert logger.name == "propicks.ai.claude"


def test_get_logger_idempotent():
    a = get_logger("test.idempotent")
    b = get_logger("test.idempotent")
    assert a is b


def test_root_logger_has_json_handler():
    """Configurazione idempotente: un solo handler sul root, formatter JSON."""
    from propicks.obs.log import _JsonFormatter

    # trigger configurazione
    get_logger("test.handler")
    root = logging.getLogger("propicks")
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, _JsonFormatter)
    assert not root.propagate


def test_json_formatter_emits_valid_json():
    from propicks.obs.log import _JsonFormatter

    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="propicks.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event_x",
        args=(),
        exc_info=None,
    )
    record.ctx = {"k": "v", "n": 1}
    line = formatter.format(record)

    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "propicks.test"
    assert parsed["msg"] == "event_x"
    assert parsed["ctx"] == {"k": "v", "n": 1}
    assert "ts" in parsed


def test_json_formatter_omits_empty_ctx():
    from propicks.obs.log import _JsonFormatter

    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="propicks.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="plain",
        args=(),
        exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert "ctx" not in parsed
