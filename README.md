# approval-service

Backend service for approving content before publication. Built incrementally — see `CLAUDE.md` for the
full design and progress checklist. This README grows into the final deliverable (run/test commands, API
examples) as phases land; it currently reflects Phase 0-4 (bootstrap, data model, auth, create/read,
approve/reject/cancel).

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

**Create** (requires `approval:create`):

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
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

All three: `404` if the request doesn't exist in this workspace, `403` if the caller isn't authorized to
decide on this specific request, `409` if it has already reached a final state (approved/rejected/
cancelled never transitions again — retrying an already-applied decision also returns `409` for now;
safe retries land with idempotency support in a later phase).
