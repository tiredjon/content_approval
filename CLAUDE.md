# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`approval-service` — a backend service that accepts requests to approve content before publication
and records the final decision. It is one microservice in a larger product (which already has
publications, scenarios, materials, users and workspaces); those are **external systems**, referenced
only by opaque ids. This repo does not implement them.

Source of truth for requirements: `Тестовое задание В.md` (original assignment, do not edit).
This is being built **incrementally across multiple sessions** — see "Progress" below before starting
work, and update it (plus the architecture/decisions sections) after every meaningful change.

### Core scenarios
- create an approval request
- list approval requests in a workspace
- get a single approval request
- approve / reject / cancel a request

### Hard constraints (from the assignment — these drive most of the design)
1. **Workspace isolation** — data from one workspace must never be reachable from another, at every layer.
2. **Idempotent writes** — retrying the same client request must not create duplicates.
3. **One-way final state** — once a request reaches a final decision (approved/rejected/cancelled) it can
   never move to another final state.
4. **Audit trail** — every successful mutation must record who changed what.
5. **Event-ready** — the service should be structured so another service can integrate via events later
   (outbox pattern; no real broker required for this exercise).
6. **No sensitive data leakage** — secrets, tokens, emails, storage keys, signed URLs, provider URLs, or
   raw provider payloads must never appear in public responses, logs, or events. (This service never
   touches such data directly, but logging/response layers are built defensively anyway.)

## Progress (living checklist — update as phases complete)

- [x] **Phase 0 — Bootstrap**: repo skeleton, `pyproject.toml` (uv), lint/test tooling, FastAPI app with
      `/health` + `/ready` (trivial, no DB yet), Makefile, `.gitignore`. Verified: `uv sync`, `uv run
      pytest` (2 passed), `uv run ruff check .` / `ruff format --check .` clean. `git init` done, repo not
      yet committed (waiting for an explicit ask). Minor follow-up noted below.
- [x] **Phase 1 — Data model & migrations**: SQLAlchemy models (`approval_requests`,
      `approval_request_reviewers`, `audit_log_entries`, `outbox_events`, `idempotency_keys`) in
      `app/db/models.py`, async engine/session in `app/db/session.py`, Alembic (async template) with a
      hand-reviewed initial migration. `/ready` now pings the DB for real. Verified: `alembic upgrade
      head` / `downgrade base` round-trip cleanly against SQLite, `alembic check` reports no drift
      between models and the migration, composite FK confirmed to reject a
      cross-workspace-referencing row, `uv run pytest` (3 passed) and ruff clean. Several non-obvious
      correctness fixes made along the way — see "Key design decisions" below (enum storage-by-value,
      SQLite FK pragma, composite tenant-isolation FKs, greenlet).
- [x] **Phase 2 — Auth stub & authorization**: `Action` enum + frozen `Principal` (`app/auth/models.py`),
      `AuthProvider` ABC (`app/auth/provider.py`) + `StubAuthProvider` decoding
      `Authorization: Bearer <base64url(json)>` (`app/auth/stub.py`), FastAPI dependencies
      `get_principal` / `require_action` (+ pre-built `require_read`/`require_create`/`require_decide`/
      `require_cancel` singletons) enforcing workspace-path-match and action membership
      (`app/auth/dependencies.py`). Documented in README under "Auth (local stub)". Verified: 20 tests
      passing (17 new in `tests/test_auth.py` — provider edge cases + dependency integration on a
      throwaway probe app), ruff clean.
- [ ] **Phase 3 — Create & read flows**: `ApprovalService.create`, `list`, `get`; repository layer;
      Pydantic schemas; audit log + outbox event on create.
- [ ] **Phase 4 — Decision flows**: approve/reject/cancel with atomic conditional-update state machine,
      reviewer-only enforcement for approve/reject, audit log + outbox event per decision.
- [ ] **Phase 5 — Idempotency & error handling**: shared `Idempotency-Key` handling for all mutating
      endpoints (dedupe by workspace+route+key+body-hash, conflict on reuse with a different body),
      RFC 7807 problem-details error responses.
- [ ] **Phase 6 — Observability & security hardening**: structured JSON logging with a redaction
      processor, request/correlation id middleware.
- [ ] **Phase 7 — Tests**: full suite — state machine edge cases, workspace isolation, idempotency,
      auth/authorization, concurrent-decision race.
- [ ] **Phase 8 — Docker**: multi-stage `Dockerfile`, `docker-compose.yml` (app + Postgres), migrate-then-serve
      entrypoint.
- [ ] **Phase 9 — Docs**: final `README.md` (run/test commands, auth format, API examples), `DESIGN.md`
      (data model, service boundaries, retry/idempotency handling, events/integration, known compromises).
- [ ] **Phase 10 — Polish pass**: lint/type-check clean, re-read assignment against implementation for
      gaps.

Work one phase at a time; do not jump ahead. Stop and hand control back after finishing a phase rather
than silently continuing into the next one.

**Follow-up noted during Phase 0**: `TestClient` (via `starlette.testclient`) emits a
`StarletteDeprecationWarning` recommending `httpx2` instead of `httpx` on the currently installed
versions (fastapi 0.139, starlette 1.3.1, httpx 0.28.1). Harmless for now (tests pass), but re-check
before building out the Phase 7 test suite in case `httpx2` becomes a required dev dependency.

## Commands

```bash
make install   # uv sync — create venv, install deps + dev deps
make run       # uv run uvicorn app.main:app --reload
make test      # uv run pytest
make lint      # uv run ruff check .
make format    # uv run ruff format .
```

Single test: `uv run pytest tests/test_health.py::test_health_returns_ok`

```bash
uv run alembic upgrade head                        # apply migrations (uses APPROVAL_DATABASE_URL)
uv run alembic revision --autogenerate -m "..."     # generate a migration from model changes
uv run alembic check                                # fail if models.py and migrations have drifted
```

(Docker/Postgres commands land in Phase 8.)

## Architecture

Layered, dependency pointing inward (routes → services → repositories → db), so business rules stay
testable without HTTP or a real database:

```
app/
  main.py           FastAPI app factory, route registration, startup/shutdown, /health + /ready
  config.py         Settings (pydantic-settings, env-driven)
  api/               [Phase 3] HTTP layer: routers, request/response wiring, exception handlers
  auth/
    models.py        Action (StrEnum), Principal (frozen pydantic model, has_action())
    exceptions.py    AuthError, InvalidCredentialsError
    provider.py      AuthProvider ABC
    stub.py          StubAuthProvider (Bearer <base64url(json)> decoder), encode_bearer_token()
    dependencies.py  get_principal, require_action(action) + require_read/create/decide/cancel
  domain/
    enums.py         SourceType, ApprovalStatus (.is_final), AuditAction — framework-agnostic
    ids.py           generate_id(prefix) -> opaque prefixed id
    [Phase 3/4]       entities, state machine, domain exceptions, ApprovalService
  db/
    base.py          Declarative Base + naming convention (stable constraint names for Alembic)
    models.py        ApprovalRequest, ApprovalRequestReviewer, AuditLogEntry, OutboxEvent,
                      IdempotencyKey
    session.py       async engine/session factory, get_db dependency, ping() for /ready
    [Phase 3/4]       repositories
  schemas/           [Phase 3] Pydantic request/response models (never reuse ORM models as API schemas)
  events/            [Phase 4] Outbox writer / event payload shaping
tests/
migrations/          Alembic (async env.py), versions/1fe27f129dda_initial_schema.py
```

Rationale: routes stay thin (parse → call service → map result to response); `domain/` has no FastAPI or
SQLAlchemy imports so the state machine and authorization rules can be unit-tested in isolation; `db/`
is the only place that knows about SQL.

## Key design decisions

- **Package manager**: `uv` with `pyproject.toml`. Fast, single lockfile, and `pip install .` still works
  as a fallback since it's a standard PEP 621 project.
- **IDs**: opaque prefixed strings for our own entities (e.g. `ar_<hex>`), matching the style of the
  external ids in the assignment's example payload (`pub_123`, `usr_1`).
- **Reviewers as a join table**, not a JSON array column — needed to authorize "is this user allowed to
  decide on this request" and to query "requests where I'm a reviewer" without scanning JSON.
- **State transitions via conditional UPDATE** (`UPDATE ... WHERE id = ? AND status = 'pending'`, check
  rowcount) rather than a row lock — avoids races between concurrent approve/reject/cancel calls without
  holding transactions open.
- **Idempotency-Key is a first-class header**, checked generically for every mutating endpoint (create,
  approve, reject, cancel): same key + same body → replay the stored response; same key + different body
  → 409. Scoped per workspace + route so keys can't collide across tenants or endpoints.
- **Auth stub shape**: `Authorization: Bearer <base64url(json)>` where the JSON is
  `{"workspace_id", "user_id", "actions": [...]}`. Chosen over plain custom headers so the code path looks
  like real bearer-token auth and `StubAuthProvider` can be swapped for a real verifier later without
  touching route code — routes only ever depend on the `AuthProvider` ABC. Unknown action strings in the
  token are rejected outright (401), not silently dropped, since a client sending a typo'd/unrecognized
  action is a bug worth surfacing loudly rather than quietly granting fewer permissions than intended.
  `require_action(action)` is a dependency *factory*; routes use the pre-built `require_read` /
  `require_create` / `require_decide` / `require_cancel` singletons instead of calling it inline in a
  parameter default (`Depends(require_action(Action.READ))`), both for a nicer call site and because ruff
  (correctly) flags function calls in argument defaults — `fastapi.Depends` itself is allow-listed via
  `tool.ruff.lint.flake8-bugbear.extend-immutable-calls`, but a further nested call inside it still isn't.
  Full rationale repeated in `DESIGN.md` once Phase 9 lands.
- **Authorization nuance beyond the coarse action list**: `approval:decide` alone is not enough to
  approve/reject a *specific* request — the acting user must also be one of that request's
  `reviewerUserIds` (when the list is non-empty). This is a deliberate interpretation beyond the literal
  spec, documented as such in `DESIGN.md`, because ignoring the reviewer list would let any user with the
  coarse permission decide any request in the workspace.
- **Events**: no real broker. An `outbox_event` table is written in the same transaction as every
  mutation; a `LoggingEventPublisher` stub stands in for a future real publisher. This satisfies
  "ready for integration via events" without introducing infra the assignment says not to add.
- **Database, dev vs. test vs. prod**: `DATABASE_URL` defaults to a local SQLite file so `make run` works
  with zero external services; docker-compose (Phase 8) points it at Postgres instead; tests override it
  to `sqlite+aiosqlite:///:memory:` (see `tests/conftest.py`). Alembic migrations are hand-reviewed after
  autogeneration and are the deliverable that targets Postgres; SQLite is only for fast tests, so if a
  future migration needs a Postgres-only feature, either guard it or add a Postgres-specific test path —
  don't let SQLite compatibility constrain the schema.
- **Schema-level tenant isolation**: `approval_requests` has `UNIQUE(workspace_id, id)` in addition to its
  PK, so every child table (`approval_request_reviewers`, `audit_log_entries`, `outbox_events`) declares
  its FK as composite `(workspace_id, approval_request_id) -> approval_requests(workspace_id, id)`. This
  makes it a schema-enforced impossibility — not just an application convention — for a row to reference
  an approval request from a different workspace. On SQLite this only actually gets enforced because
  `app/db/session.py` turns on `PRAGMA foreign_keys=ON` per connection (off by default in SQLite; Postgres
  always enforces FKs). Repository queries (Phase 3+) must still filter by `workspace_id` explicitly —
  this is defense in depth, not a replacement for that.
- **Enums stored by value, not by name**: SQLAlchemy's `Enum` type persists a Python enum member's
  `.name` by default (e.g. `"PENDING"`), not its `.value` (`"pending"`). All three enum columns pass
  `values_callable=lambda cls: [m.value for m in cls]` to store the lowercase value that matches the
  wire format. Also `native_enum=False` everywhere (renders as `VARCHAR` + `CHECK`, not a Postgres native
  `ENUM` type) so adding a new enum member later is a plain migration, not an `ALTER TYPE ... ADD VALUE`.
- **JSON columns**: `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` — real `JSONB` (indexable,
  containment queries) on Postgres, portable plain `JSON` everywhere else including SQLite tests.
- **`greenlet` is an explicit dependency**, not left implicit — SQLAlchemy's async engine raises at
  runtime ("the greenlet library is required...") without it; it doesn't always get pulled transitively.

## Conventions

- `domain/` must not import from `api/`, `db/`, or FastAPI/SQLAlchemy — keep business rules storage- and
  transport-agnostic.
- Never return ORM models directly from routes; always go through a `schemas/` Pydantic model.
- Every mutating service method writes its audit log entry and outbox event in the same DB transaction
  as the state change it describes — never as a separate best-effort step.
