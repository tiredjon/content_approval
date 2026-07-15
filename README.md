# approval-service

Backend service for approving content before publication. Built incrementally — see `CLAUDE.md` for the
full design and progress checklist. This README grows into the final deliverable (run/test commands, API
examples) as phases land; it currently reflects Phase 0-2 (bootstrap, data model, auth).

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages the Python 3.12 interpreter and virtualenv for you)

## Run

```bash
make install   # uv sync
make run       # uvicorn app.main:app --reload, http://localhost:8000
```

Currently exposes `GET /health` (liveness) and `GET /ready` (checks DB connectivity — 503 if the
database is unreachable). By default the app points at a local SQLite file, so this works with no other
services running.

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
