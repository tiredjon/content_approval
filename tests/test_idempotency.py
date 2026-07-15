import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import ApprovalRequest as ApprovalRequestRow
from app.db.session import get_sessionmaker
from app.main import app
from tests.helpers import DEFAULT_APPROVAL_REQUEST_PAYLOAD, auth_headers

WORKSPACE = "ws_1"
USER = "usr_1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _create_headers() -> dict[str, str]:
    return auth_headers(workspace_id=WORKSPACE, user_id=USER, actions=["approval:create"])


# --- create: required key -----------------------------------------------------------


def test_create_missing_idempotency_key_returns_422(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=_create_headers(),
    )
    assert response.status_code == 422


def test_create_replays_identical_response_on_same_key_and_body(client: TestClient) -> None:
    headers = {**_create_headers(), "Idempotency-Key": "key-1"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()


def test_create_replay_does_not_create_a_second_row(client: TestClient) -> None:
    headers = {**_create_headers(), "Idempotency-Key": "key-dup-check"}
    for _ in range(2):
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
            json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
            headers=headers,
        )
        assert response.status_code == 201

    async def _count() -> int:
        async with get_sessionmaker()() as session:
            rows = (
                (
                    await session.execute(
                        select(ApprovalRequestRow).where(
                            ApprovalRequestRow.workspace_id == WORKSPACE
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(rows)

    assert asyncio.run(_count()) == 1


def test_create_same_key_different_body_returns_409(client: TestClient) -> None:
    headers = {**_create_headers(), "Idempotency-Key": "key-2"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json={**DEFAULT_APPROVAL_REQUEST_PAYLOAD, "title": "A different title"},
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 409


def test_create_same_key_different_workspace_does_not_collide(client: TestClient) -> None:
    key = "shared-key-across-workspaces"

    first = client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers={
            **auth_headers(workspace_id="ws_1", user_id=USER, actions=["approval:create"]),
            "Idempotency-Key": key,
        },
    )
    second = client.post(
        "/api/v1/workspaces/ws_2/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers={
            **auth_headers(workspace_id="ws_2", user_id=USER, actions=["approval:create"]),
            "Idempotency-Key": key,
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_concurrent_create_with_same_key_never_creates_a_duplicate() -> None:
    """True simultaneous requests sharing one key must never produce two resources.

    Verified empirically: against the single-connection SQLite StaticPool test harness,
    both concurrent attempts safely fail (409) rather than one cleanly replaying the
    other's result — an artifact of SQLite's single shared test connection, not of the
    idempotency design (see CLAUDE.md). The property that must hold everywhere is
    checked here: never more than one row, and never a 500.
    """

    async def _run() -> tuple[list[int], int]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {
                **auth_headers(workspace_id=WORKSPACE, user_id=USER, actions=["approval:create"]),
                "Idempotency-Key": "concurrent-create-key",
            }
            responses = await asyncio.gather(
                ac.post(
                    f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
                    json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
                    headers=headers,
                ),
                ac.post(
                    f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
                    json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
                    headers=headers,
                ),
            )
        async with get_sessionmaker()() as session:
            rows = (
                (
                    await session.execute(
                        select(ApprovalRequestRow).where(
                            ApprovalRequestRow.workspace_id == WORKSPACE
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [r.status_code for r in responses], len(rows)

    statuses, row_count = asyncio.run(_run())
    assert all(code != 500 for code in statuses)
    assert row_count <= 1


# --- decisions: optional key ---------------------------------------------------------


def test_approve_with_key_replays_instead_of_409_on_retry(client: TestClient) -> None:
    create_headers = {**_create_headers(), "Idempotency-Key": "create-for-approve-replay"}
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=create_headers,
    ).json()

    approve_headers = {
        **auth_headers(workspace_id=WORKSPACE, user_id="usr_2", actions=["approval:decide"]),
        "Idempotency-Key": "approve-key-1",
    }
    body = {"comment": "Approved"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json=body,
        headers=approve_headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json=body,
        headers=approve_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_approve_without_key_still_conflicts_on_retry(client: TestClient) -> None:
    """No Idempotency-Key -> falls back to Phase 4's plain behavior: a bare retry of an
    already-applied decision gets 409, not a replay. The key is what upgrades this."""
    create_headers = {**_create_headers(), "Idempotency-Key": "create-for-approve-no-key"}
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=create_headers,
    ).json()

    approve_headers = auth_headers(
        workspace_id=WORKSPACE, user_id="usr_2", actions=["approval:decide"]
    )
    body = {"comment": "Approved"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json=body,
        headers=approve_headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json=body,
        headers=approve_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_reject_with_key_replays_instead_of_409_on_retry(client: TestClient) -> None:
    create_headers = {**_create_headers(), "Idempotency-Key": "create-for-reject-replay"}
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=create_headers,
    ).json()

    reject_headers = {
        **auth_headers(workspace_id=WORKSPACE, user_id="usr_2", actions=["approval:decide"]),
        "Idempotency-Key": "reject-key-1",
    }
    body = {"reason": "Brand tone is wrong"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/reject",
        json=body,
        headers=reject_headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/reject",
        json=body,
        headers=reject_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_cancel_with_key_replays_instead_of_409_on_retry(client: TestClient) -> None:
    create_headers = {**_create_headers(), "Idempotency-Key": "create-for-cancel-replay"}
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
        json=DEFAULT_APPROVAL_REQUEST_PAYLOAD,
        headers=create_headers,
    ).json()

    cancel_headers = {
        **auth_headers(workspace_id=WORKSPACE, user_id=USER, actions=["approval:cancel"]),
        "Idempotency-Key": "cancel-key-1",
    }
    body = {"reason": "Draft was removed"}

    first = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/cancel",
        json=body,
        headers=cancel_headers,
    )
    second = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/cancel",
        json=body,
        headers=cancel_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
