import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey


def fingerprint(payload: dict[str, Any]) -> str:
    """Hash of the *validated, normalized* request payload — so cosmetic differences
    (whitespace, key order) between two logically-identical retries never look like a
    conflict, but a genuinely different body does."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def run_idempotent[ModelT: BaseModel](
    *,
    session: AsyncSession,
    workspace_id: str,
    route: str,
    idempotency_key: str | None,
    request_payload: dict[str, Any],
    handler: Callable[[], Awaitable[tuple[ModelT, int]]],
    response_model: type[ModelT],
) -> tuple[ModelT, int]:
    """Runs `handler()` at most once per (workspace_id, route, idempotency_key).

    `handler` returns `(response_model_instance, status_code)`. A second call with the
    same key and the same (normalized) body replays the first call's stored response
    *and its original status code* instead of re-running `handler`. The same key with
    a *different* body raises 409. `idempotency_key=None` skips all of this and just
    runs `handler` — callers that don't require idempotency (e.g. decision endpoints,
    where the underlying operation is already safe to retry) can opt out.

    Must be called with a `session` that shares the same transaction as `handler`'s
    own writes (i.e. both resolved from the same `get_db()` dependency in one request)
    so the idempotency record and the mutation it describes commit together.
    """
    if idempotency_key is None:
        return await handler()

    fp = fingerprint(request_payload)

    existing = await session.get(IdempotencyKey, (workspace_id, route, idempotency_key))
    if existing is not None:
        if existing.request_fingerprint != fp:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key was already used with a different request body",
            )
        return response_model.model_validate(existing.response_body), existing.response_status

    result, status_code = await handler()
    body = result.model_dump(mode="json", by_alias=True)

    session.add(
        IdempotencyKey(
            workspace_id=workspace_id,
            route=route,
            idempotency_key=idempotency_key,
            request_fingerprint=fp,
            response_status=status_code,
            response_body=body,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent request with the identical key won the race and committed
        # first. The mutation this request just performed rolls back with the rest
        # of the transaction — no duplicate is left behind, just a safe failure.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key is already being processed by a concurrent request",
        ) from exc

    return result, status_code
