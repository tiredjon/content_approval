from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import AuditLogEntry, OutboxEvent
from app.db.session import get_sessionmaker
from app.main import app
from tests.helpers import DEFAULT_APPROVAL_REQUEST_PAYLOAD as BASE_PAYLOAD
from tests.helpers import auth_headers
from tests.helpers import create_approval_request as _create


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# --- create --------------------------------------------------------------------------


def test_create_returns_expected_fields(client: TestClient) -> None:
    body = _create(client)

    assert body["id"].startswith("ar_")
    assert body["workspaceId"] == "ws_1"
    assert body["sourceType"] == "publication"
    assert body["sourceId"] == "pub_123"
    assert body["title"] == "Instagram reel draft"
    assert body["description"] == "Needs final approval"
    assert body["reviewerUserIds"] == ["usr_1", "usr_2"]
    assert body["status"] == "pending"
    assert body["createdByUserId"] == "usr_1"
    assert body["decidedByUserId"] is None
    assert body["decidedAt"] is None


async def test_create_writes_audit_log_and_outbox_event() -> None:
    # Async end-to-end (httpx.AsyncClient over ASGITransport) rather than the sync
    # TestClient, so the DB assertions below run on the same event loop as the request
    # that wrote the rows — no sync/async event-loop bridging.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/workspaces/ws_1/approval-requests",
            json=BASE_PAYLOAD,
            headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
        )
    assert response.status_code == 201, response.text
    body = response.json()

    async with get_sessionmaker()() as session:
        audit = (
            await session.execute(
                select(AuditLogEntry).where(AuditLogEntry.approval_request_id == body["id"])
            )
        ).scalar_one()
        event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.approval_request_id == body["id"])
            )
        ).scalar_one()

    assert audit.workspace_id == "ws_1"
    assert audit.actor_user_id == "usr_1"
    assert audit.action.value == "created"

    assert event.workspace_id == "ws_1"
    assert event.event_type == "approval_request.created"
    assert event.payload["id"] == body["id"]
    assert event.published_at is None


def test_create_requires_auth(client: TestClient) -> None:
    response = client.post("/api/v1/workspaces/ws_1/approval-requests", json=BASE_PAYLOAD)
    assert response.status_code == 401


def test_create_requires_create_action(client: TestClient) -> None:
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json=BASE_PAYLOAD,
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    assert response.status_code == 403


def test_create_rejects_workspace_mismatch(client: TestClient) -> None:
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json=BASE_PAYLOAD,
        headers=auth_headers(workspace_id="ws_2", actions=["approval:create"]),
    )
    assert response.status_code == 403


def test_create_rejects_missing_required_field(client: TestClient) -> None:
    payload = {k: v for k, v in BASE_PAYLOAD.items() if k != "sourceType"}
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json=payload,
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 422


def test_create_rejects_invalid_source_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json={**BASE_PAYLOAD, "sourceType": "not-a-real-type"},
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 422


def test_create_rejects_blank_title(client: TestClient) -> None:
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json={**BASE_PAYLOAD, "title": "   "},
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 422


def test_create_rejects_duplicate_reviewer_ids(client: TestClient) -> None:
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json={**BASE_PAYLOAD, "reviewerUserIds": ["usr_1", "usr_1"]},
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 422


def test_create_defaults_reviewer_user_ids_to_empty_list(client: TestClient) -> None:
    payload = {k: v for k, v in BASE_PAYLOAD.items() if k != "reviewerUserIds"}
    response = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json=payload,
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 201
    assert response.json()["reviewerUserIds"] == []


# --- get -------------------------------------------------------------------------------


def test_get_returns_created_request(client: TestClient) -> None:
    created = _create(client)

    response = client.get(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )

    assert response.status_code == 200
    assert response.json() == created


def test_get_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests/ar_does_not_exist",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    assert response.status_code == 404


def test_get_returns_404_across_workspaces(client: TestClient) -> None:
    created = _create(client, workspace_id="ws_1")

    response = client.get(
        f"/api/v1/workspaces/ws_2/approval-requests/{created['id']}",
        headers=auth_headers(workspace_id="ws_2", actions=["approval:read"]),
    )

    assert response.status_code == 404


def test_get_requires_auth(client: TestClient) -> None:
    created = _create(client)
    response = client.get(f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}")
    assert response.status_code == 401


def test_get_requires_read_action(client: TestClient) -> None:
    created = _create(client)
    response = client.get(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:create"]),
    )
    assert response.status_code == 403


# --- list ------------------------------------------------------------------------------


def test_list_empty_workspace_returns_empty(client: TestClient) -> None:
    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_returns_items_newest_first(client: TestClient) -> None:
    first = _create(client, title="first")
    second = _create(client, title="second")

    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )

    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]


def test_list_respects_limit_and_offset(client: TestClient) -> None:
    created = [_create(client, title=f"item {i}") for i in range(5)]
    newest_first_ids = [c["id"] for c in reversed(created)]

    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests?limit=2&offset=1",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )

    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert [item["id"] for item in body["items"]] == newest_first_ids[1:3]


def test_list_filters_by_status(client: TestClient) -> None:
    _create(client)

    matching = client.get(
        "/api/v1/workspaces/ws_1/approval-requests?status=pending",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    non_matching = client.get(
        "/api/v1/workspaces/ws_1/approval-requests?status=approved",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )

    assert matching.json()["total"] == 1
    assert non_matching.json()["total"] == 0


def test_list_rejects_invalid_status(client: TestClient) -> None:
    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests?status=not-a-status",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    assert response.status_code == 422


def test_list_rejects_out_of_range_limit(client: TestClient) -> None:
    response = client.get(
        "/api/v1/workspaces/ws_1/approval-requests?limit=101",
        headers=auth_headers(workspace_id="ws_1", actions=["approval:read"]),
    )
    assert response.status_code == 422


def test_list_is_isolated_per_workspace(client: TestClient) -> None:
    _create(client, workspace_id="ws_1")

    response = client.get(
        "/api/v1/workspaces/ws_2/approval-requests",
        headers=auth_headers(workspace_id="ws_2", actions=["approval:read"]),
    )

    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/workspaces/ws_1/approval-requests")
    assert response.status_code == 401
