# approval-service

Backend service for approving content before publication. Built incrementally — see `CLAUDE.md` for the
full design and progress checklist. This README grows into the final deliverable (run/test commands, API
examples) as phases land; it currently reflects Phase 0-6 (bootstrap, data model, auth, create/read,
approve/reject/cancel, idempotency, error format, observability).

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages the Python 3.12 interpreter and virtualenv for you)

## Run

```bash
make install                  # uv sync
uv run alembic upgrade head   # create the schema (local SQLite file by default)
make run                      # uvicorn app.main:app --reload, http://localhost:8000
```

Exposes `GET /health` (liveness) and `GET /ready` (checks DB connectivity — 503 if the database is
unreachable), plus the approval-requests endpoints below. By default the app points at a local SQLite
file, so this works with no other services running.

## Test

```bash
make test      # uv run pytest
make lint      # uv run ruff check .
```

## Database & migrations

```bash
uv run alembic upgrade head                        # apply migrations
uv run alembic revision --autogenerate -m "..."     # generate a migration from model changes
```

`DATABASE_URL` (env var `APPROVAL_DATABASE_URL`) defaults to a local SQLite file. docker-compose (coming
in a later phase) will point it at Postgres instead.

## Auth (local stub)

There's no real identity provider — the assignment asks for an auth stub, chosen and documented here.
Every request must carry:

```
Authorization: Bearer <base64url(json)>
```

where the JSON payload is:

```json
{"workspace_id": "ws_1", "user_id": "usr_1", "actions": ["approval:read", "approval:create"]}
```

`actions` is any subset of `approval:read`, `approval:create`, `approval:decide`, `approval:cancel` (see
the assignment's action table). The token is **not signed** — this is intentionally not real security,
just a stand-in so the service can be exercised locally and in tests without a real auth provider. Routes
depend only on the `AuthProvider` interface (`app/auth/provider.py`), so a real verifier can replace the
stub later without route changes.

Two checks are enforced on every request: the `workspace_id` in the token must match the `{workspace_id}`
in the URL path (401/403 otherwise), and the token's `actions` must include whatever the endpoint
requires.

To mint a token from a shell for manual testing:

```bash
uv run python -c "
from app.auth.stub import encode_bearer_token
print(encode_bearer_token(workspace_id='ws_1', user_id='usr_1', actions=['approval:read']))
"
```

Then use the printed value directly as the `Authorization` header, e.g.:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/workspaces/ws_1/approval-requests
```

## API

All endpoints are scoped under `/api/v1/workspaces/{workspace_id}/approval-requests`. Request bodies and
responses are camelCase JSON.

**Create** (requires `approval:create` and an `Idempotency-Key` header):

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -H "Idempotency-Key: <any-unique-string-per-attempt>" \
  -d '{
    "sourceType": "publication",
    "sourceId": "pub_123",
    "title": "Instagram reel draft",
    "description": "Needs final approval",
    "reviewerUserIds": ["usr_1", "usr_2"]
  }'
```

`sourceType` is one of `publication`, `scenario`, `edit`, `external`. `reviewerUserIds` is optional
(defaults to empty) and must not contain blanks or duplicates. Returns `201` with the created request
(`status: "pending"`).

**List** (requires `approval:read`), with optional `status` filter and `limit`/`offset` pagination
(`limit` 1-100, default 20; newest first):

```bash
curl "http://localhost:8000/api/v1/workspaces/ws_1/approval-requests?status=pending&limit=20&offset=0" \
  -H "Authorization: Bearer <token>"
```

Returns `{"items": [...], "total": N, "limit": 20, "offset": 0}`.

**Get one** (requires `approval:read`):

```bash
curl http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/ar_xxx \
  -H "Authorization: Bearer <token>"
```

Returns `404` if the id doesn't exist *or* belongs to a different workspace — the response never reveals
whether a request exists in someone else's workspace.

**Approve** (requires `approval:decide`, and — if `reviewerUserIds` was non-empty at creation — the
caller must be one of them):

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/ar_xxx/approve \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"comment": "Approved"}'
```

`comment` is optional. Returns the updated request (`status: "approved"`).

**Reject** (same `approval:decide` + reviewer rule as approve; `reason` is required):

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/ar_xxx/reject \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"reason": "Brand tone is wrong"}'
```

**Cancel** (requires `approval:cancel`, **and the caller must be the request's creator** — being a
reviewer is not enough; `reason` is required):

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/ar_xxx/cancel \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"reason": "Draft was removed"}'
```

All three accept an optional `Idempotency-Key` header too (see below). All three: `404` if the request
doesn't exist in this workspace, `403` if the caller isn't authorized to decide on this specific request,
`409` if it has already reached a final state (approved/rejected/cancelled never transitions again).

## Idempotency

Retrying the same client request must never create a duplicate. `Idempotency-Key` is:

- **Required** on `POST .../approval-requests` (create) — without one, a retry would create a second,
  visibly duplicate resource, which is exactly what must never happen.
- **Optional** on approve/reject/cancel — the state machine already makes retrying safe on its own (a
  bare retry of an already-applied decision just gets `409`); the key upgrades that into a clean replay
  of the original response instead.

Behavior, scoped per `(workspace_id, endpoint, idempotency_key)`:

- Same key + same request body → the original response is replayed (same status code and body), the
  operation is **not** repeated.
- Same key + a *different* request body → `409` (the key was reused for a different request — almost
  certainly a client bug).
- A key is never required to be globally unique — the same string can be reused across workspaces or
  across different resources without colliding.

## Errors

Every error response (auth failures, validation errors, not-found, forbidden, conflict, and unexpected
server errors) is `application/problem+json` ([RFC 7807](https://www.rfc-editor.org/rfc/rfc7807)):

```json
{"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Approval request not found: ar_xxx"}
```

`422` validation errors additionally include an `errors` array (Pydantic's per-field error list). Server
errors (`500`) never expose internal details (stack traces, exception messages) in the response — those
go to the server-side log only.

## Observability

Every response carries an `X-Request-Id` header — either echoed back from the request if the caller sent
one, or freshly generated. Logs are single-line JSON to stdout, each one tagged with the `request_id` of
the request that produced it, so a specific call's full server-side story can be grepped out of the logs
by that id. Log lines never contain secrets/tokens/emails/etc. — any field whose name looks sensitive
(`password`, `token`, `authorization`, `email`, `storage_key`, `signed_url`, `provider_url`, ...) is
redacted before being written, both in logs and in `422` validation error bodies.
