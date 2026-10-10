"""Structured (JSON) logging so logs can be shipped to Loki / CloudWatch and queried by field."""

import json
import logging
import sys
from datetime import UTC, datetime

_CONTEXT_FIELDS = ("service", "event_type", "event_id", "order_id")
_service_name = "unknown"
_factory_installed = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _CONTEXT_FIELDS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging(service: str, level: str = "INFO") -> None:
    global _service_name, _factory_installed
    _service_name = service

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    if not _factory_installed:
        base_factory = logging.getLogRecordFactory()

        def factory(*args, **kwargs):
            record = base_factory(*args, **kwargs)
            record.service = _service_name
            return record

        logging.setLogRecordFactory(factory)
        _factory_installed = True
