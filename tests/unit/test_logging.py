import json
import logging

import pytest
import structlog

from payment_service.config import Settings
from payment_service.logging import REDACTED, configure_logging, redact_sensitive


def redact(event: dict[str, object]) -> dict[str, object]:
    return dict(redact_sensitive(None, "info", event))


@pytest.mark.parametrize(
    "key",
    ["api_key", "API_KEY", "authorization", "password", "token", "signing_secret"],
)
def test_sensitive_keys_are_removed(key: str) -> None:
    assert redact({key: "s3cret"})[key] == REDACTED


@pytest.mark.parametrize("key", ["x-api-key", "X-API-Key", "x_api_key"])
def test_separators_do_not_hide_a_secret(key: str) -> None:
    """Имена заголовков пишут и через дефис, и через подчёркивание."""
    assert redact({key: "s3cret"})[key] == REDACTED


def test_nested_values_are_redacted() -> None:
    event = redact({"headers": {"X-API-Key": "s3cret", "Accept": "json"}})

    assert event["headers"] == {"X-API-Key": REDACTED, "Accept": "json"}


def test_harmless_fields_survive() -> None:
    event = redact({"event": "payment.created", "payment_id": "abc", "amount": "10.00"})

    assert event == {"event": "payment.created", "payment_id": "abc", "amount": "10.00"}


def test_json_format_carries_bound_context(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(settings.model_copy(update={"log_format": "json"}))
    structlog.contextvars.bind_contextvars(payment_id="p-1")

    structlog.get_logger("test").info("payment.processed", api_key="s3cret")
    structlog.contextvars.clear_contextvars()

    record = json.loads(capsys.readouterr().out.strip())
    assert record["event"] == "payment.processed"
    assert record["payment_id"] == "p-1"
    assert record["api_key"] == REDACTED


def test_stdlib_logs_go_through_the_same_pipeline(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(settings.model_copy(update={"log_format": "json"}))

    logging.getLogger("uvicorn").warning("что-то случилось")

    record = json.loads(capsys.readouterr().out.strip())
    assert record["logger"] == "uvicorn"
    assert record["level"] == "warning"


def test_console_format_is_human_readable(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(settings.model_copy(update={"log_format": "console"}))

    structlog.get_logger("test").info("payment.created")

    assert "payment.created" in capsys.readouterr().out
