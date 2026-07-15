from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import auth_headers, create_approval_request

WORKSPACE = "ws_1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _assert_problem_shape(response, expected_status: int) -> dict:
    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == expected_status
    assert isinstance(body["title"], str) and body["title"]
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["type"] == "about:blank"
    return body


def test_404_is_problem_json(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/ar_missing",
        headers=auth_headers(workspace_id=WORKSPACE, actions=["approval:read"]),
    )
    _assert_problem_shape(response, 404)


def test_401_is_problem_json(client: TestClient) -> None:
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/approval-requests")
    _assert_problem_shape(response, 401)


def test_403_workspace_mismatch_is_problem_json(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        headers=auth_headers(workspace_id="ws_2", actions=["approval:read"]),
    )
    _assert_problem_shape(response, 403)


def test_403_not_a_reviewer_is_problem_json(client: TestClient) -> None:
    created = create_approval_request(client, workspace_id=WORKSPACE, reviewerUserIds=["usr_2"])
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "x"},
        headers=auth_headers(
            workspace_id=WORKSPACE, user_id="usr_stranger", actions=["approval:decide"]
        ),
    )
    _assert_problem_shape(response, 403)


def test_409_already_decided_is_problem_json(client: TestClient) -> None:
    created = create_approval_request(client, workspace_id=WORKSPACE, reviewerUserIds=["usr_2"])
    decide_headers = auth_headers(
        workspace_id=WORKSPACE, user_id="usr_2", actions=["approval:decide"]
    )
    client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "x"},
        headers=decide_headers,
    )

    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "again"},
        headers=decide_headers,
    )
    _assert_problem_shape(response, 409)


def test_422_validation_error_is_problem_json_with_errors_list(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json={"sourceId": "pub_1", "title": "T"},  # missing sourceType, missing Idempotency-Key
        headers=auth_headers(workspace_id=WORKSPACE, actions=["approval:create"]),
    )
    body = _assert_problem_shape(response, 422)
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) > 0
    # Pydantic's `ctx` (internal message-templating context, may hold non-JSON-safe
    # values) must never leak into the response.
    assert all("ctx" not in error for error in body["errors"])


def test_500_unhandled_exception_does_not_leak_internals(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("something exploded with a secret stack trace")

    monkeypatch.setattr("app.domain.service.ApprovalService.get_request", _boom)

    # raise_server_exceptions=False: we're testing the actual HTTP response our 500
    # handler produces, not letting the test runner re-raise for a Python traceback.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE}/approval-requests/ar_whatever",
            headers=auth_headers(workspace_id=WORKSPACE, actions=["approval:read"]),
        )

    body = _assert_problem_shape(response, 500)
    assert "secret" not in body["detail"]
    assert "RuntimeError" not in body["detail"]
    assert "Traceback" not in body["detail"]
