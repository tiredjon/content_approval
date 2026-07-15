"""Regression tests for constraint #6: secrets, tokens, emails, storage keys, signed
URLs, provider URLs, and raw provider payloads must never appear in public responses,
logs, or events. Logging is covered in tests/test_observability.py; this file covers
outbox events, the audit trail, and the public response schema itself.
"""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLogEntry, OutboxEvent
from app.db.session import get_sessionmaker
from app.main import app
from app.observability.redaction import looks_sensitive
from app.schemas.approval_request import ApprovalRequestOut
from tests.helpers import auth_headers, create_approval_request

WORKSPACE = "ws_1"
CREATOR = "usr_1"
REVIEWER = "usr_2"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _decide(client: TestClient, action: str, request_id: str, body: dict, user_id: str) -> None:
    decide_action = "approval:cancel" if action == "cancel" else "approval:decide"
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/approval-requests/{request_id}/{action}",
        json=body,
        headers=auth_headers(workspace_id=WORKSPACE, user_id=user_id, actions=[decide_action]),
    )
    assert response.status_code == 200, response.text


def test_response_schema_has_no_sensitive_looking_field_names() -> None:
    """Static guard: if a field that looks like a secret is ever added to the public
    response schema, this fails immediately rather than relying on someone noticing."""
    for field_name in ApprovalRequestOut.model_fields:
        assert not looks_sensitive(field_name), f"suspicious field name: {field_name}"


def test_audit_log_and_outbox_never_contain_sensitive_keys(client: TestClient) -> None:
    approve_id = create_approval_request(
        client,
        workspace_id=WORKSPACE,
        created_by_user_id=CREATOR,
        reviewerUserIds=[REVIEWER],
        idempotency_key=str(uuid.uuid4()),
    )["id"]
    _decide(client, "approve", approve_id, {"comment": "Approved"}, REVIEWER)

    reject_id = create_approval_request(
        client,
        workspace_id=WORKSPACE,
        created_by_user_id=CREATOR,
        reviewerUserIds=[REVIEWER],
        idempotency_key=str(uuid.uuid4()),
    )["id"]
    _decide(client, "reject", reject_id, {"reason": "Brand tone is wrong"}, REVIEWER)

    cancel_id = create_approval_request(
        client,
        workspace_id=WORKSPACE,
        created_by_user_id=CREATOR,
        reviewerUserIds=[REVIEWER],
        idempotency_key=str(uuid.uuid4()),
    )["id"]
    _decide(client, "cancel", cancel_id, {"reason": "Draft was removed"}, CREATOR)

    async def _fetch() -> tuple[list[AuditLogEntry], list[OutboxEvent]]:
        async with get_sessionmaker()() as session:
            audit_entries = (
                (
                    await session.execute(
                        select(AuditLogEntry).where(
                            AuditLogEntry.workspace_id == WORKSPACE,
                            AuditLogEntry.approval_request_id.in_(
                                [approve_id, reject_id, cancel_id]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.workspace_id == WORKSPACE,
                            OutboxEvent.approval_request_id.in_([approve_id, reject_id, cancel_id]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return audit_entries, events

    audit_entries, events = asyncio.run(_fetch())

    assert len(audit_entries) == 6  # created + one decision, times 3 requests
    assert len(events) == 6

    for entry in audit_entries:
        for key in entry.details or {}:
            assert not looks_sensitive(key), f"suspicious audit detail key: {key}"

    for event in events:
        for key in event.payload:
            assert not looks_sensitive(key), f"suspicious outbox payload key: {key}"
