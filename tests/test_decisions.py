import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import AuditLogEntry, OutboxEvent
from app.db.session import get_sessionmaker
from app.main import app
from tests.helpers import auth_headers, create_approval_request

WORKSPACE = "ws_1"
CREATOR = "usr_creator"
REVIEWER = "usr_reviewer"
STRANGER = "usr_stranger"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _create(client: TestClient, **overrides) -> dict:
    overrides.setdefault("reviewerUserIds", [REVIEWER])
    return create_approval_request(
        client,
        workspace_id=WORKSPACE,
        created_by_user_id=CREATOR,
        **overrides,
    )


def _decide_headers(user_id: str, action: str) -> dict[str, str]:
    return auth_headers(workspace_id=WORKSPACE, user_id=user_id, actions=[action])


def _approve(client: TestClient, request_id: str, user_id: str = REVIEWER, **body):
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/approve",
        json={"comment": "Approved", **body},
        headers=_decide_headers(user_id, "approval:decide"),
    )


def _reject(client: TestClient, request_id: str, user_id: str = REVIEWER, **body):
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/reject",
        json={"reason": "Brand tone is wrong", **body},
        headers=_decide_headers(user_id, "approval:decide"),
    )


def _cancel(client: TestClient, request_id: str, user_id: str = CREATOR, **body):
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/cancel",
        json={"reason": "Draft was removed", **body},
        headers=_decide_headers(user_id, "approval:cancel"),
    )


_DECISION_ACTIONS = {
    "approve": "approval:decide",
    "reject": "approval:decide",
    "cancel": "approval:cancel",
}


async def _create_and_decide(
    decision: str, body: dict, actor_user_id: str
) -> tuple[str, int, list, list]:
    """Creates a request then applies one decision to it via a real async HTTP call,
    then reads back its audit log + outbox trail directly from the DB. Returns
    (request_id, decision_status_code, audit_entries, events)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
            json={
                "sourceType": "publication",
                "sourceId": "pub_1",
                "title": "T",
                "reviewerUserIds": [REVIEWER],
            },
            headers={
                **auth_headers(
                    workspace_id=WORKSPACE, user_id=CREATOR, actions=["approval:create"]
                ),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        request_id = create_resp.json()["id"]

        decision_resp = await ac.post(
            f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/{decision}",
            json=body,
            headers=auth_headers(
                workspace_id=WORKSPACE,
                user_id=actor_user_id,
                actions=[_DECISION_ACTIONS[decision]],
            ),
        )

    async with get_sessionmaker()() as session:
        audit_entries = (
            (
                await session.execute(
                    select(AuditLogEntry)
                    .where(AuditLogEntry.approval_request_id == request_id)
                    .order_by(AuditLogEntry.created_at)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.approval_request_id == request_id)
                    .order_by(OutboxEvent.created_at)
                )
            )
            .scalars()
            .all()
        )

    return request_id, decision_resp.status_code, audit_entries, events


# --- approve -----------------------------------------------------------------------


def test_approve_success(client: TestClient) -> None:
    created = _create(client)

    response = _approve(client, created["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decidedByUserId"] == REVIEWER
    assert body["decisionComment"] == "Approved"
    assert body["decisionReason"] is None
    assert body["decidedAt"] is not None


def test_approve_by_non_reviewer_returns_403(client: TestClient) -> None:
    created = _create(client)

    response = _approve(client, created["id"], user_id=STRANGER)

    assert response.status_code == 403


def test_approve_allowed_when_no_reviewers_specified(client: TestClient) -> None:
    created = _create(client, reviewerUserIds=[])

    response = _approve(client, created["id"], user_id=STRANGER)

    assert response.status_code == 200


def test_approve_requires_decide_action(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "Approved"},
        headers=auth_headers(workspace_id=WORKSPACE, user_id=REVIEWER, actions=["approval:read"]),
    )
    assert response.status_code == 403


def test_approve_requires_auth(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "Approved"},
    )
    assert response.status_code == 401


def test_approve_rejects_workspace_mismatch(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={"comment": "Approved"},
        headers=auth_headers(workspace_id="ws_2", user_id=REVIEWER, actions=["approval:decide"]),
    )
    assert response.status_code == 403


def test_approve_returns_404_for_unknown_id(client: TestClient) -> None:
    response = _approve(client, "ar_does_not_exist")
    assert response.status_code == 404


def test_approve_returns_404_across_workspaces(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/ws_2/approval-requests/{created['id']}/approve",
        json={"comment": "Approved"},
        headers=auth_headers(workspace_id="ws_2", user_id=REVIEWER, actions=["approval:decide"]),
    )
    assert response.status_code == 404


def test_approve_already_approved_returns_409(client: TestClient) -> None:
    created = _create(client)
    _approve(client, created["id"])

    response = _approve(client, created["id"])

    assert response.status_code == 409


def test_approve_already_rejected_returns_409(client: TestClient) -> None:
    created = _create(client)
    _reject(client, created["id"])

    response = _approve(client, created["id"])

    assert response.status_code == 409


def test_approve_already_cancelled_returns_409(client: TestClient) -> None:
    created = _create(client)
    _cancel(client, created["id"])

    response = _approve(client, created["id"])

    assert response.status_code == 409


def test_approve_writes_audit_log_and_outbox_event() -> None:
    _, status_code, audit_entries, events = asyncio.run(
        _create_and_decide("approve", {"comment": "Approved"}, REVIEWER)
    )

    assert status_code == 200
    assert [e.action.value for e in audit_entries] == ["created", "approved"]
    assert audit_entries[1].actor_user_id == REVIEWER
    assert audit_entries[1].details == {"comment": "Approved"}

    assert [e.event_type for e in events] == [
        "approval_request.created",
        "approval_request.approved",
    ]
    assert events[1].payload["status"] == "approved"
    assert events[1].payload["decided_by_user_id"] == REVIEWER


def test_approve_succeeds_when_comment_field_is_omitted(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/approve",
        json={},
        headers=_decide_headers(REVIEWER, "approval:decide"),
    )
    assert response.status_code == 200
    assert response.json()["decisionComment"] is None


# --- reject ------------------------------------------------------------------------


def test_reject_success(client: TestClient) -> None:
    created = _create(client)

    response = _reject(client, created["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["decisionReason"] == "Brand tone is wrong"
    assert body["decisionComment"] is None


def test_reject_by_non_reviewer_returns_403(client: TestClient) -> None:
    created = _create(client)
    response = _reject(client, created["id"], user_id=STRANGER)
    assert response.status_code == 403


def test_reject_requires_non_blank_reason(client: TestClient) -> None:
    created = _create(client)
    response = _reject(client, created["id"], reason="   ")
    assert response.status_code == 422


def test_reject_requires_reason_field(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/reject",
        json={},
        headers=_decide_headers(REVIEWER, "approval:decide"),
    )
    assert response.status_code == 422


def test_reject_already_decided_returns_409(client: TestClient) -> None:
    created = _create(client)
    _reject(client, created["id"])
    response = _reject(client, created["id"])
    assert response.status_code == 409


def test_reject_after_approved_returns_409(client: TestClient) -> None:
    """One-way final state must hold across *different* decisions too, not just a
    repeat of the same one."""
    created = _create(client)
    _approve(client, created["id"])
    response = _reject(client, created["id"])
    assert response.status_code == 409


def test_reject_returns_404_across_workspaces(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/ws_2/approval-requests/{created['id']}/reject",
        json={"reason": "Brand tone is wrong"},
        headers=auth_headers(workspace_id="ws_2", user_id=REVIEWER, actions=["approval:decide"]),
    )
    assert response.status_code == 404


def test_reject_writes_audit_log_and_outbox_event() -> None:
    _, status_code, audit_entries, events = asyncio.run(
        _create_and_decide("reject", {"reason": "Brand tone is wrong"}, REVIEWER)
    )

    assert status_code == 200
    assert [e.action.value for e in audit_entries] == ["created", "rejected"]
    assert audit_entries[1].actor_user_id == REVIEWER
    assert audit_entries[1].details == {"reason": "Brand tone is wrong"}

    assert [e.event_type for e in events] == [
        "approval_request.created",
        "approval_request.rejected",
    ]
    assert events[1].payload["status"] == "rejected"
    assert events[1].payload["decided_by_user_id"] == REVIEWER


# --- cancel ------------------------------------------------------------------------


def test_cancel_success_by_creator(client: TestClient) -> None:
    created = _create(client)

    response = _cancel(client, created["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["decisionReason"] == "Draft was removed"
    assert body["decidedByUserId"] == CREATOR


def test_cancel_by_non_creator_returns_403_even_for_reviewer(client: TestClient) -> None:
    created = _create(client)

    response = _cancel(client, created["id"], user_id=REVIEWER)

    assert response.status_code == 403


def test_cancel_requires_cancel_action(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{created['id']}/cancel",
        json={"reason": "Draft was removed"},
        headers=auth_headers(workspace_id=WORKSPACE, user_id=CREATOR, actions=["approval:read"]),
    )
    assert response.status_code == 403


def test_cancel_requires_non_blank_reason(client: TestClient) -> None:
    created = _create(client)
    response = _cancel(client, created["id"], reason="")
    assert response.status_code == 422


def test_cancel_already_decided_returns_409(client: TestClient) -> None:
    created = _create(client)
    _approve(client, created["id"])
    response = _cancel(client, created["id"])
    assert response.status_code == 409


def test_cancel_after_rejected_returns_409(client: TestClient) -> None:
    """One-way final state must hold across *different* decisions too, not just a
    repeat of the same one."""
    created = _create(client)
    _reject(client, created["id"])
    response = _cancel(client, created["id"])
    assert response.status_code == 409


def test_cancel_returns_404_for_unknown_id(client: TestClient) -> None:
    response = _cancel(client, "ar_does_not_exist")
    assert response.status_code == 404


def test_cancel_returns_404_across_workspaces(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/api/v1/workspaces/ws_2/approval-requests/{created['id']}/cancel",
        json={"reason": "Draft was removed"},
        headers=auth_headers(workspace_id="ws_2", user_id=CREATOR, actions=["approval:cancel"]),
    )
    assert response.status_code == 404


def test_cancel_writes_audit_log_and_outbox_event() -> None:
    _, status_code, audit_entries, events = asyncio.run(
        _create_and_decide("cancel", {"reason": "Draft was removed"}, CREATOR)
    )

    assert status_code == 200
    assert [e.action.value for e in audit_entries] == ["created", "cancelled"]
    assert audit_entries[1].actor_user_id == CREATOR
    assert audit_entries[1].details == {"reason": "Draft was removed"}

    assert [e.event_type for e in events] == [
        "approval_request.created",
        "approval_request.cancelled",
    ]
    assert events[1].payload["status"] == "cancelled"
    assert events[1].payload["decided_by_user_id"] == CREATOR


# --- concurrency ---------------------------------------------------------------------


def test_concurrent_decisions_only_one_wins() -> None:
    """The atomic conditional-update state machine must let exactly one of two
    simultaneous decisions on the same pending request succeed."""

    async def _run() -> tuple[int, int]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WORKSPACE}/approval-requests",
                json={
                    "sourceType": "publication",
                    "sourceId": "pub_1",
                    "title": "T",
                    "reviewerUserIds": [REVIEWER],
                },
                headers={
                    **auth_headers(
                        workspace_id=WORKSPACE, user_id=CREATOR, actions=["approval:create"]
                    ),
                    "Idempotency-Key": str(uuid.uuid4()),
                },
            )
            request_id = create_resp.json()["id"]
            headers = auth_headers(
                workspace_id=WORKSPACE, user_id=REVIEWER, actions=["approval:decide"]
            )

            approve_coro = ac.post(
                f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/approve",
                json={"comment": "race"},
                headers=headers,
            )
            reject_coro = ac.post(
                f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/reject",
                json={"reason": "race"},
                headers=headers,
            )
            approve_resp, reject_resp = await asyncio.gather(approve_coro, reject_coro)
        return approve_resp.status_code, reject_resp.status_code

    statuses = sorted(asyncio.run(_run()))
    assert statuses == [200, 409]
