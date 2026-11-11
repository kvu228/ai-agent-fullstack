"""Logging: the request context on every record, the output formats, and what must never
end up in a log (emails, passwords, tokens)."""

import json
import logging
import re
import sys
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging import (
    JsonFormatter,
    TextFormatter,
    bind_log_context,
    new_log_context,
    record_fields,
    reset_log_context,
    setup_logging,
)
from tests.conftest import FakeToolCallingModel, register_and_login
from tests.test_chat import chat
from tests.test_documents import upload


def records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


def all_output(caplog: pytest.LogCaptureFixture) -> str:
    """Everything captured, rendered the way production writes it."""
    return "\n".join(JsonFormatter().format(r) for r in caplog.records)


# ---- Request context (through the whole app) --------------------------------------------


async def test_every_response_has_a_request_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    [record] = records(caplog, "Request finished")
    assert record_fields(record)["request_id"] == request_id
    assert record_fields(record)["http_status"] == 401
    assert record_fields(record)["http_path"] == "/api/v1/auth/me"


@pytest.mark.parametrize(
    ("sent", "kept"),
    [("lb-7f3a.42_x", True), ("has spaces", False), ("x" * 65, False)],
)
async def test_incoming_request_id_is_reused_only_if_it_looks_like_one(
    client: AsyncClient, sent: str, kept: bool
) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": sent})
    assert (response.headers["X-Request-ID"] == sent) is kept


async def test_health_checks_are_logged_at_debug(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await client.get("/api/v1/health")
    [record] = records(caplog, "Request finished")
    assert record.levelno == logging.DEBUG


async def test_context_follows_the_upload_into_background_ingestion(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await register_and_login(client)
    caplog.set_level(logging.INFO)
    response = await upload(client)
    request_id = response.headers["X-Request-ID"]

    [ingested] = records(caplog, "Document ingested")
    assert record_fields(ingested)["request_id"] == request_id
    assert record_fields(ingested)["document_id"] == response.json()["id"]
    assert record_fields(ingested)["chunks"] > 1
    # user_id was bound by the auth dependency and reaches the request log line too.
    [finished] = [
        r
        for r in records(caplog, "Request finished")
        if record_fields(r)["request_id"] == request_id
    ]
    assert record_fields(finished)["user_id"] == record_fields(ingested)["user_id"]


async def test_agent_failure_is_logged_with_its_conversation(
    client: AsyncClient, fake_llm: FakeToolCallingModel, caplog: pytest.LogCaptureFixture
) -> None:
    await register_and_login(client)
    fake_llm.messages = iter([])  # no scripted reply: the fake model raises

    events = await chat(client, "Hello?")

    assert events[-1][0] == "error"
    [record] = records(caplog, "Agent stream failed")
    assert record.exc_info is not None
    assert record_fields(record)["conversation_id"] == events[0][1]["conversation_id"]
    assert "user_id" in record_fields(record)


async def test_unhandled_error_is_logged_once_and_returns_the_request_id(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.get("/boom")

    assert response.status_code == 500
    [record] = records(caplog, "Unhandled error")
    assert record_fields(record)["request_id"] == response.headers["X-Request-ID"]


# ---- What must never be logged ---------------------------------------------------------


async def test_failed_login_logs_why_but_not_who(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    email, password = "alice@example.com", "correct-horse-battery"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    caplog.set_level(logging.INFO)

    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"}
    )

    assert response.status_code == 401
    [record] = records(caplog, "Login failed")
    assert record_fields(record)["reason"] == "wrong_password"
    output = all_output(caplog)
    assert email not in output
    assert "wrong-password-123" not in output


async def test_tokens_never_reach_the_logs(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await register_and_login(client)
    access_token = client.cookies["access_token"]
    await client.post("/api/v1/auth/refresh")
    await client.get("/api/v1/documents", headers={"Authorization": f"Bearer {access_token}"})

    output = all_output(caplog)
    assert access_token not in output
    assert client.cookies["refresh_token"] not in output


# ---- Formatters (unit) -----------------------------------------------------------------


def make_record(msg: str, *args: Any, exc_info: Any = None, **extra: Any) -> logging.LogRecord:
    # makeRecord goes through the record factory installed by setup_logging().
    return logging.getLogger("test").makeRecord(
        "test", logging.INFO, __file__, 1, msg, args, exc_info, extra=extra
    )


def test_json_has_context_and_extra_fields_with_extra_winning() -> None:
    setup_logging()
    token = new_log_context(request_id="r1", user_id="u1")
    try:
        bind_log_context(conversation_id="c1")
        record = make_record("Stored %s chunks", 3, user_id="u2", chunks=3)
    finally:
        reset_log_context(token)

    payload = json.loads(JsonFormatter({"env": "test"}).format(record))

    assert payload["message"] == "Stored 3 chunks"
    assert payload["level"] == "INFO" and payload["logger"] == "test"
    assert payload["env"] == "test"
    assert payload["request_id"] == "r1" and payload["conversation_id"] == "c1"
    assert payload["user_id"] == "u2" and payload["chunks"] == 3


def test_secrets_are_masked_in_both_formats() -> None:
    setup_logging()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    record = make_record(
        "Got Bearer %s and %s",
        "abc.def-123",
        jwt,
        access_token="t0p-s3cret",
        password="hunter2",
        input_tokens=42,  # a count, not a secret: must stay
    )
    for formatter in (JsonFormatter(), TextFormatter()):
        output = formatter.format(record)
        for secret in ("abc.def-123", jwt, "t0p-s3cret", "hunter2"):
            assert secret not in output, (type(formatter).__name__, secret)
        assert "42" in output


def test_a_traceback_is_one_json_field_on_one_line() -> None:
    setup_logging()
    try:
        raise ValueError("bad input")
    except ValueError:
        record = make_record("Failed", exc_info=sys.exc_info())

    output = JsonFormatter().format(record)

    assert "\n" not in output
    assert "ValueError: bad input" in json.loads(output)["exception"]


def test_setup_logging_is_idempotent_and_keeps_other_handlers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        setup_logging()
        setup_logging("DEBUG", "json")

        root = logging.getLogger()
        ours = [h for h in root.handlers if h.get_name() == "app"]
        assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
        assert caplog.handler in root.handlers
        assert logging.getLogger("uvicorn").handlers == []
        assert logging.getLogger("uvicorn").propagate is True
        assert logging.getLogger("uvicorn.access").disabled is True
    finally:
        setup_logging()
