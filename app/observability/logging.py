import json
import logging
from typing import Any

from app.observability.context import request_id_var
from app.observability.redaction import redact_mapping


class JSONFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, the current
    request's correlation id (if any), and any structured `extra={"context": {...}}`
    passed to the log call — with sensitive-looking keys redacted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        context = getattr(record, "context", None)
        if context:
            payload.update(redact_mapping(context))

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
