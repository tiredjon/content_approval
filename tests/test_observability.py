import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability.logging import JSONFormatter
from tests.helpers import create_approval_request

WORKSPACE = "ws_1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["x-request-id"]


def test_request_id_is_echoed_when_provided(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-Id": "caller-supplied-id"})
    assert response.headers["x-request-id"] == "caller-supplied-id"


def test_request_id_differs_across_requests_when_not_supplied(client: TestClient) -> None:
    first = client.get("/health").headers["x-request-id"]
    second = client.get("/health").headers["x-request-id"]
    assert first != second


def test_authorization_header_never_appears_in_logs(client: TestClient, caplog) -> None:
    secret_token_body = "super-secret-token-value-should-never-leak"
    token = f"Bearer {secret_token_body}"

    with caplog.at_level(logging.DEBUG):
        create_approval_request(
            client, workspace_id=WORKSPACE, created_by_user_id="usr_1", idempotency_key="obs-key-1"
        )
        # Deliberately invalid token, to exercise the auth-failure logging path too.
        client.get(
            f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
            headers={"Authorization": token},
        )

    for record in caplog.records:
        assert secret_token_body not in record.getMessage()
        assert secret_token_body not in json.dumps(getattr(record, "context", {}), default=str)


def test_json_formatter_redacts_sensitive_context_keys() -> None:
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something happened",
        args=(),
        exc_info=None,
    )
    record.context = {
        "workspace_id": "ws_1",
        "authorization": "Bearer abc123",
        "user_email": "person@example.com",
        "storage_key": "s3-key-value",
    }

    payload = json.loads(formatter.format(record))

    assert payload["workspace_id"] == "ws_1"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["user_email"] == "[REDACTED]"
    assert payload["storage_key"] == "[REDACTED]"


def test_json_formatter_includes_request_id_from_context_var() -> None:
    from app.observability.context import request_id_var

    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    token = request_id_var.set("req-abc-123")
    try:
        payload = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)

    assert payload["request_id"] == "req-abc-123"
