import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from payment_service.config import Settings

REDACTED = "[redacted]"
MAX_REDACTION_DEPTH = 4

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "signature",
        "signing_secret",
        "token",
        "x-api-key",
        "x-payment-signature",
    }
)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_REDACTION_DEPTH or not isinstance(value, MutableMapping):
        return value
    return {
        key: REDACTED if str(key).lower() in SENSITIVE_KEYS else _redact(item, depth + 1)
        for key, item in value.items()
    }


def redact_sensitive(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    """Вырезает секреты из любого лог-события, включая вложенные словари."""
    return _redact(event_dict)  # type: ignore[no-any-return]


def configure_logging(settings: Settings) -> None:
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_sensitive,
    ]

    render: list[Processor]
    if settings.log_format == "json":
        render = [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        render = [structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *render],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiormq").setLevel(logging.WARNING)
