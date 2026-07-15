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
- [x] **Phase 3 — Create & read flows**: domain entity + ports-and-adapters repository
      (`app/domain/entities.py`, `repository.py`, `app/db/repository.py`), `ApprovalService`
      (create/get/list, `app/domain/service.py`), camelCase Pydantic schemas
      (`app/schemas/`), routes mounted at `/api/v1/workspaces/{workspace_id}/approval-requests`
      (`app/api/v1/approval_requests.py`). `get_db` now wraps each request in one transaction
      (`session.begin()`) so create + audit log + outbox event commit atomically. Documented in
      README under "API". Verified: 43 tests passing, ruff clean, alembic upgrade/downgrade/check
      still clean after a schema fix (below). Two real bugs found and fixed by the tests
      themselves — see "Key design decisions": (1) `created_at` tie-breaking for list ordering,
      (2) naive-vs-aware datetime inconsistency between a freshly-created and a re-fetched row.
- [x] **Phase 4 — Decision flows**: `ApprovalRequestRepository.transition()` — one conditional
      `UPDATE ... WHERE status = 'pending'` (`app/db/repository.py`) — backs `ApprovalService`'s
      `approve_request`/`reject_request`/`cancel_request` (`app/domain/service.py`). Reviewer-only
      enforcement for approve/reject, creator-only enforcement for cancel (`NotAuthorizedForDecisionError`),
      already-decided detection (`InvalidTransitionError`). Routes at `.../approve`, `.../reject`,
      `.../cancel` (`app/api/v1/approval_requests.py`) via a shared `_map_domain_errors()` context
      manager (404/403/409). Documented in README under "API". Verified: 67 tests passing (24 new in
      `tests/test_decisions.py`, including a real concurrent-request race test), ruff clean. Confirmed
      empirically before trusting it: (1) SQLAlchemy's `synchronize_session="auto"` correctly keeps an
      already-loaded ORM object in sync after the bulk `UPDATE`, so re-fetching in the same session
      never returns stale data; (2) two decisions fired concurrently via `asyncio.gather` against the
      single-connection SQLite StaticPool resolve to exactly one `200` and one `409`, never an error.
- [x] **Phase 5 — Idempotency & error handling**: `run_idempotent()` (`app/api/idempotency.py`) — required
      `Idempotency-Key` on create, optional on approve/reject/cancel; replays on same key+body, `409` on
      same key+different body, `409` (not a crash) on a genuine concurrent-same-key race. Global RFC 7807
      handlers (`app/api/errors.py`) replaced the Phase 4 per-router `_map_domain_errors()` — now cover
      `HTTPException` (incl. existing auth 401/403), `RequestValidationError` (422, `ctx` stripped since
      it can hold a raw non-JSON-serializable exception for custom validators), the 3 domain exceptions
      (404/403/409), and a catch-all `Exception` → 500 that logs full details server-side only.
- [x] **Phase 6 — Observability & security hardening**: `app/observability/` — `JSONFormatter` (stdlib
      `logging`, one JSON object per line, redacts sensitive-looking field names), `RequestIdMiddleware`
      (echoes/mints `X-Request-Id`, exposed to logs via a contextvar), `redaction.py` shared by both the
      logger and the 422 handler. `ApprovalService` logs INFO on every successful mutation; auth
      dependencies log WARNING on 401/403 (never the raw `Authorization` value). Done together with
      Phase 5 per explicit request. Verified: 90 tests passing (23 new — idempotency replay/conflict/
      concurrency, RFC 7807 shape for 401/403/404/409/422/500, request-id generation/echo, and a
      caplog-based test that a real bearer token value never appears in log output), ruff clean.
- [x] **Phase 7 — Test suite audit**: re-read the assignment's 6 constraints one by one against actual
      coverage (not a rewrite — 90 tests already existed from Phases 2-6). Gaps found and closed, all in
      `tests/test_decisions.py` unless noted: reject/cancel had no cross-workspace-404 test (only approve
      did) → added both; reject/cancel had no audit-log/outbox-event test (only create/approve did) →
      added both, refactored the duplicated async create-then-decide-then-fetch-trail setup into one
      `_create_and_decide()` helper used by all three; "one-way final state" was only tested as
      same-action-twice for reject/cancel (approve alone had the full matrix) → added
      reject-after-approved and cancel-after-rejected; `reviewerUserIds` validation only covered
      duplicates, not blank entries → added (`test_approval_requests.py`); pagination validation only
      covered over-limit, not `limit=0`/negative `offset` → added (`test_approval_requests.py`); approve's
      `comment` being optional was never tested with the key fully *omitted* (vs. `null`) → added; no
      regression test existed for constraint #6 on the events/audit side specifically (logging was
      already covered in Phase 6) → new `tests/test_no_sensitive_data_leakage.py`, one static check
      (response schema field names) and one dynamic check (audit/outbox rows across a
      create+approve+reject+cancel run) against `looks_sensitive()`. Also resolved Phase 0's open
      `httpx2` follow-up: it's a real package, but migrating is a dependency upgrade with an unknown API
      surface, not a coverage gap — deferred as a deliberate decision, not an oversight, since it's only
      a deprecation warning and all 102 tests pass either way. Postgres-backed concurrency verification
      (noted as a maybe) deferred to whenever Phase 8's docker-compose exists, since it's not needed to
      close a gap — the SQLite-caveat is already fully documented. Verified: 102 tests passing (12 new),
      ruff clean.
- [x] **Phase 8 — Docker**: multi-stage `Dockerfile` (uv builder stage installs deps then the project in
      two layers for cache efficiency; `python:3.12-slim-bookworm` runtime stage, non-root `appuser`,
      `HEALTHCHECK` hitting `/health`), `docker-entrypoint.sh` (`alembic upgrade head` then
      `exec uvicorn`), `docker-compose.yml` (`postgres:16-alpine` with a named volume + `pg_isready`
      healthcheck; `app` waits for `condition: service_healthy`). Verified end-to-end against the real
      stack, not just reviewed: image builds clean; `docker compose up` brings up Postgres, waits for
      healthy, runs migrations (`PostgresqlImpl`, not `SQLiteImpl` — the first time this project's
      migration has ever touched real Postgres), starts the app; full create → get → list → approve/
      cancel flow works over HTTP; container runs as uid 1000 (`appuser`), not root; cross-workspace
      isolation and idempotency replay both still hold against Postgres; `uv run pytest` (102 tests)
      still green while the stack was up. **A real, meaningful bug surfaced by this verification and
      fixed** — see "Key design decisions": `native_enum=False` alone does not add a CHECK constraint
      (needs `create_constraint=True` too), so every enum column had silently had *no* DB-level
      validation since Phase 1 without SQLite ever being able to reveal it.
- [x] **Phase 9 — Docs**: new `DESIGN.md` — data model (table purposes, the composite-FK tenant-isolation
      trick, a mermaid state diagram), service boundaries (ownership vs. opaque-external-id references,
      the 3-layer tenant isolation argument, the reviewer/creator authorization nuance), retry/idempotency
      handling (required-vs-optional split, replay/conflict mechanics, the SQLite-StaticPool concurrent-
      same-key caveat restated for a human reader), events/integration readiness (outbox pattern, minimal
      payload rationale), and known compromises (stub auth, no audit-read endpoint, single worker,
      offset pagination, SQLite-vs-Postgres migration caveat including the Phase 8 CHECK-constraint bug).
      Written to stand alone for a reader who hasn't seen `CLAUDE.md`'s session-by-session log — distilled,
      not copied. `README.md` polished: top-of-file links to `DESIGN.md`/`CLAUDE.md`, a table-of-contents
      with hand-verified GitHub anchor links, a test-coverage summary in the Test section, one factual
      fix (Docker section said `python:3.12-slim`, corrected to match the Dockerfile's actual
      `python:3.12-slim-bookworm`).
- [ ] **Phase 10 — Polish pass**: lint/type-check clean, re-read assignment against implementation for
      gaps.

Work one phase at a time; do not jump ahead. Stop and hand control back after finishing a phase rather
than silently continuing into the next one.

**Resolved during Phase 7**: the `httpx2`/`StarletteDeprecationWarning` noted since Phase 0 is a real,
published package, but adopting it is a dependency upgrade with an unreviewed API surface, not a test
gap — deliberately deferred rather than migrated. All 102 tests pass either way; revisit only if a future
starlette/fastapi upgrade actually drops `httpx` support (not just warns about it).

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

```bash
make docker-up      # docker compose up --build — real Postgres, migrations run automatically
make docker-down     # docker compose down
make docker-logs      # docker compose logs -f app
```

## Architecture

Layered, dependency pointing inward (routes → services → repositories → db), so business rules stay
testable without HTTP or a real database:

```
app/
  main.py           FastAPI app factory: configure_logging(), RequestIdMiddleware, router, exception
                     handlers, /health + /ready, startup log line
  config.py         Settings (pydantic-settings, env-driven), incl. log_level
  api/
    deps.py          get_approval_service (wires SqlApprovalRequestRepository to the request's session)
    errors.py        register_exception_handlers() — RFC 7807 for HTTPException,
                      RequestValidationError, the 3 domain exceptions, and a catch-all 500
    idempotency.py   run_idempotent() + fingerprint() — the Idempotency-Key mechanism
    v1/
      approval_requests.py   create/list/get/approve/reject/cancel routes
  auth/
    models.py        Action (StrEnum), Principal (frozen pydantic model, has_action())
    exceptions.py    AuthError, InvalidCredentialsError
    provider.py      AuthProvider ABC
    stub.py          StubAuthProvider (Bearer <base64url(json)> decoder), encode_bearer_token()
    dependencies.py  get_principal, require_action(action) + require_read/create/decide/cancel;
                      logs WARNING on 401/403 (never the raw Authorization value)
  domain/
    enums.py         SourceType, ApprovalStatus (.is_final), AuditAction, OutboxEventType —
                      framework-agnostic
    ids.py           generate_id(prefix) -> opaque prefixed id
    entities.py      ApprovalRequest — frozen dataclass, the only type services/routes pass around
    exceptions.py    DomainError, ApprovalRequestNotFoundError, InvalidTransitionError,
                      NotAuthorizedForDecisionError
    repository.py    ApprovalRequestRepository ABC — the "port" (incl. transition()); no SQLAlchemy
    service.py       ApprovalService: create/get/list_request(s) + approve/reject/cancel_request —
                      takes the port + plain scalar args, never a Principal or an API schema; logs
                      INFO on every successful mutation
  db/
    base.py          Declarative Base + naming convention (stable constraint names for Alembic)
    models.py        ORM rows: ApprovalRequest, ApprovalRequestReviewer, AuditLogEntry, OutboxEvent,
                      IdempotencyKey
    repository.py    SqlApprovalRequestRepository — the "adapter"; transition() is one conditional
                      UPDATE (WHERE status='pending') for atomic state changes
    session.py       async engine/session factory, get_db (one transaction per request), ping()
  schemas/
    common.py        CamelModel (alias_generator=to_camel, populate_by_name, from_attributes)
    approval_request.py   ApprovalRequestCreate/Out/ListOut, ApproveRequest,
                           DecisionReasonRequest -> RejectRequest/CancelRequest
  observability/
    context.py       request_id_var (contextvar) — set by the middleware, read by the formatter
    redaction.py      looks_sensitive()/redact_mapping() — shared by logging.py and api/errors.py
    logging.py        JSONFormatter, configure_logging()
    middleware.py      RequestIdMiddleware — echoes/mints X-Request-Id
tests/
  helpers.py         auth_headers(), create_approval_request() — shared across test files
migrations/          Alembic (async env.py), versions/1fe27f129dda_initial_schema.py
Dockerfile           multi-stage (uv builder -> python:3.12-slim runtime), non-root user, HEALTHCHECK
docker-entrypoint.sh alembic upgrade head, then exec uvicorn — migrate-then-serve
docker-compose.yml   app + postgres:16-alpine (named volume, pg_isready healthcheck)
.dockerignore        keeps tests/, dev tooling, and local state out of the image
README.md            run/test commands, auth format, full API reference
DESIGN.md            data model, service boundaries, retry/idempotency, events, known compromises
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
- **Idempotency-Key: required for create, optional for decisions** — revised from the original plan
  ("required for every mutating endpoint") once Phase 4's state machine existed: create is the only
  operation where skipping it lets a retry create a real, visible duplicate; approve/reject/cancel are
  already retry-safe via the conditional `UPDATE` (a bare retry just gets `409`), so the key there is a
  UX upgrade (clean replay instead of `409`) rather than a correctness requirement. Same key + same body
  → replay the stored response, not re-run; same key + different body → 409. Scoped per
  `(workspace_id, route, idempotency_key)` — `route` is an app-built logical key (`"create"`,
  `f"approve:{request_id}"`, ...), not the literal HTTP path, so keys can't collide across tenants,
  endpoints, or resources.
- **Idempotency storage lives in the same transaction as the mutation** (`run_idempotent()` in
  `app/api/idempotency.py`, called with the same `session` the route's service call uses — verified via
  FastAPI's dependency caching, which resolves `Depends(get_db)` once per request regardless of how many
  places declare it). The idempotency-key row is inserted and explicitly `flush()`-ed *inside* the
  handler, specifically so an `IntegrityError` from a genuine concurrent-same-key race is caught right
  there and turned into `409` — letting it surface later at the transaction's implicit commit (outside
  the route's own control flow) would be much harder to handle cleanly.
- **Concurrent identical-idempotency-key requests: verified, with a documented SQLite-only caveat.**
  Isolated (just the idempotency-key insert, nothing else), two concurrent attempts behave exactly as
  designed — one succeeds, the other gets a real `IntegrityError` → `409`. Through the full HTTP endpoint
  (extra flushes from `create_request`'s own DB round-trip in between), the same test against the SQLite
  `StaticPool` test harness instead produces `409` for *both* attempts, with zero rows surviving — safe
  (no duplicate, no 500) but not the idealized "one clean winner" story. This traces to `StaticPool`
  sharing one literal DBAPI connection across sessions, which SQLAlchemy documents as intended for
  single-owner test scenarios, not genuinely concurrent transactions — not a flaw in the mechanism, which
  targets Postgres (real per-connection isolation) in production. `tests/test_idempotency.py` asserts the
  property that holds on *any* backend (never a duplicate, never a 500), not the exact status-code split.
  Revisit once Phase 8's docker-compose Postgres exists, per Phase 7's note.
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
  coarse permission decide any request in the workspace. Same idea for cancel: `approval:cancel` alone
  isn't enough — the actor must be the request's *creator*. A listed reviewer with `approval:cancel` in
  their token still gets 403 on cancel; reviewers reject, only the submitter withdraws. Both rules raise
  the same `NotAuthorizedForDecisionError` (403), parameterized with a human-readable reason.
- **Events**: no real broker, and no publisher process either — deliberately out of scope (the
  assignment explicitly says not to add real external services). The `outbox_events` table (one row
  per mutation, same transaction, `published_at` nullable) *is* the "ready for integration" deliverable:
  a future worker can poll `WHERE published_at IS NULL ORDER BY created_at` and publish to a real
  broker without any change to the tables or the service that writes them. Note this as a known
  compromise in `DESIGN.md` (Phase 9) rather than building an unused publisher stub now.
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
  wire format. Also `native_enum=False` everywhere (renders as `VARCHAR`, not a Postgres native `ENUM`
  type) so adding a new enum member later is a plain migration, not an `ALTER TYPE ... ADD VALUE`.
- **Bug found by Phase 8's real-Postgres verification: `native_enum=False` does not imply a CHECK
  constraint.** `create_constraint` is a *separate* `Enum(...)` parameter that defaults to `False`
  independently of `native_enum` — so from Phase 1 through Phase 7, every enum column
  (`source_type`/`status`/`action`) was a bare `VARCHAR(32)` with **zero** DB-level validation, and
  nothing in the SQLite-only test suite could have revealed this (SQLite has no native `ENUM` either
  way, so the two configurations look identical there). Confirmed via `CreateTable(...).compile(dialect=
  postgresql.dialect())` before touching anything, then fixed by adding `create_constraint=True` to all
  three columns in `app/db/models.py` *and* the not-yet-deployed migration (per the Conventions rule —
  Alembic's autogenerate does not reliably detect `CHECK` constraint differences, so this had to be
  edited by hand, not regenerated). Verified after the fix, against real Postgres: `\d` shows
  `ck_approval_requests_source_type` etc., and a raw `INSERT ... VALUES ('not_a_real_type', ...)` via
  `psql` is rejected with `violates check constraint`. This is exactly the kind of gap the "verify
  empirically, don't trust memory" discipline from earlier phases exists to catch — it just took a real
  Postgres instance, not SQLite, to surface it.
- **JSON columns**: `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")` — real `JSONB` (indexable,
  containment queries) on Postgres, portable plain `JSON` everywhere else including SQLite tests.
- **`greenlet` is an explicit dependency**, not left implicit — SQLAlchemy's async engine raises at
  runtime ("the greenlet library is required...") without it; it doesn't always get pulled transitively.
- **Ports and adapters for persistence**: `app/domain/repository.py` defines `ApprovalRequestRepository`
  as an ABC (the port); `app/db/repository.py`'s `SqlApprovalRequestRepository` is the only adapter. Audit
  log + outbox writes are methods on the *same* port/adapter (not split into separate repositories) since
  in this codebase they always travel together with the state change they describe — splitting them would
  be abstraction without a use case. `ApprovalService` depends only on the port, so its business logic
  (and Phase 4's state machine) can be unit-tested against an in-memory fake without a database.
- **Service methods take plain scalar ids, never a `Principal`**: e.g. `created_by_user_id: str`, not the
  whole auth `Principal` object. `domain/` must not depend on `auth/` — coarse-grained authorization
  (`require_action`) is checked at the API layer before the service is ever called; fine-grained business
  rules that need identity (Phase 4's "must be a listed reviewer to decide") take just the actor's user id
  and are pure domain logic, not an auth concern.
- **One DB transaction per request**: `get_db()` wraps the session in `session.begin()`, so it commits
  once when the route handler returns normally and rolls back on any exception. This is what makes
  "insert approval_request + reviewers, then audit log, then outbox event" atomic without the service or
  repository having to manage transaction boundaries themselves.
- **List response is a `{items, total, limit, offset}` envelope**, not a bare array — the assignment
  doesn't specify a shape, and returning an unbounded array with no pagination would be a real scalability
  problem. Default `limit=20`, capped at 100; sorted newest-first (`created_at DESC, id DESC` tie-break).
- **Cross-workspace lookups return 404, not 403**: `GET .../approval-requests/{id}` for a request that
  belongs to a *different* workspace than the one in the URL is indistinguishable from a nonexistent id.
  Returning 403 would leak "this id exists, just not for you," which is exactly the kind of cross-tenant
  information leak constraint #1 rules out. (403 is still correct and used for the *auth* case — token's
  workspace doesn't match the URL's workspace — since that's about the caller's credentials, not about
  whether the target resource exists.)
- **Timestamp precision bug (found by tests, fixed)**: columns originally used `server_default=func.now()`.
  On SQLite this is second-granularity, so two rows created in the same test collided on `created_at`, and
  since ids are random (not sortable), `ORDER BY created_at DESC, id DESC` silently stopped reflecting
  insertion order. Fixed by switching every `created_at`/`updated_at` to a Python-side
  `default=lambda: datetime.now(UTC)` (microsecond precision on every dialect) — required editing the
  not-yet-deployed initial migration directly (see Conventions) rather than adding a second migration.
- **Naive-vs-aware datetime bug (found by tests, fixed)**: SQLite drops `tzinfo` on round-trip through the
  DB (Postgres doesn't), so a freshly-created row's in-memory object serialized `created_at` with a `Z`
  suffix while the *same* row re-fetched via `GET` serialized it without one — the same resource looked
  different depending on how you got it. Fixed once, at the ORM-row → domain-entity boundary
  (`_as_utc` in `app/db/repository.py`), so the domain entity's invariant is "all datetimes are UTC-aware,"
  and every consumer (schemas, future event payloads) gets that for free.
- **`transition()`'s atomicity, verified not assumed**: the conditional `UPDATE ... WHERE status =
  'pending'` is the entire concurrency-safety mechanism for approve/reject/cancel — no row locks, no
  `SELECT ... FOR UPDATE`. Two things had to be checked empirically rather than trusted from memory
  before relying on this: (1) SQLAlchemy's ORM-enabled bulk `UPDATE` defaults to
  `synchronize_session="auto"`, which (for this simple equality `WHERE` clause) updates any
  already-loaded in-session object's attributes in place — so a `get()` called right after `transition()`
  in the *same* session correctly sees the new state, not a stale cached one; (2) firing two decisions
  concurrently via `asyncio.gather` against the single-connection SQLite `StaticPool` resolves cleanly to
  one `200` and one `409`, never a connection-contention error. Both are exercised as real tests, not
  just asserted.
- **Exception-to-HTTP mapping evolved from a local helper to global handlers, as planned.** Phase 4 used
  a small `_map_domain_errors()` contextmanager in `approval_requests.py` (documented then as a stopgap
  until Phase 5's idempotency errors existed too). Phase 5 replaced it with `register_exception_handlers()`
  (`app/api/errors.py`) — `@app.exception_handler(...)` for `HTTPException` (catches every existing inline
  `HTTPException` raise, including auth's 401/403, for free), `RequestValidationError`, the 3 domain
  exceptions, and a catch-all `Exception` → 500. Routes now just call the service directly; nothing in
  `app/api/v1/approval_requests.py` catches exceptions itself anymore.
- **RFC 7807 (`application/problem+json`) for every error, uniformly** — `{type, title, status, detail}`,
  `422` adds an `errors` array. Two things the tests caught that would otherwise have shipped as bugs:
  (1) Pydantic's `exc.errors()` puts the *raw exception object* in `ctx.error` for custom
  `@field_validator`s (e.g. our blank-string checks) — passing that straight to `JSONResponse` raised
  `TypeError: Object of type ValueError is not JSON serializable` the first time a blank-title test hit
  the new handler; fixed by stripping `ctx` entirely (`msg` already has the resolved text, and `ctx` isn't
  meant for API consumers anyway). (2) The unhandled-exception handler must log full details server-side
  (`logger.exception(...)`) but return a body with *zero* internals — verified with a test that
  monkeypatches a service method to raise and asserts the exception's message never appears in the
  response (constraint #6: no internals in public responses).
- **Structured logging: stdlib `logging` + a custom `JSONFormatter`, no new dependency** (not `structlog`
  or similar) — one JSON object per line (timestamp/level/logger/message/request_id/exc_info), keeping
  the dependency list unchanged for something this codebase's log volume doesn't need a dedicated library
  for. `configure_logging()` replaces `root.handlers` once at app startup; call sites just use
  `logging.getLogger(__name__)` — `domain/service.py` using it doesn't violate "no FastAPI/SQLAlchemy in
  domain/" since `logging` is stdlib, not a web/ORM framework concern.
- **One redaction utility (`app/observability/redaction.py`), used by both the logger and the 422
  handler** — rather than duplicating the sensitive-key-marker list in two places. Substring match,
  case-insensitive, deliberately broad (catches `signedUrl`, `storage_key`, `providerUrl`, ...) — this
  service never actually handles secrets/tokens/emails/storage keys/signed URLs/provider URLs directly
  (external systems are referenced by opaque id only, per the assignment's own scoping), so today this is
  belt-and-suspenders defense-in-depth rather than something that fires in normal operation; it exists so
  a future field that *does* carry something sensitive is caught by construction instead of by someone
  remembering to redact it by hand.
- **Correlation id via `contextvars`, not thread-locals or explicit threading** — `RequestIdMiddleware`
  sets a `ContextVar` for the duration of each request; `JSONFormatter` reads it when formatting *any* log
  record, so code arbitrarily deep in the call stack (e.g. `ApprovalService`) gets automatic correlation
  without threading a `request_id` parameter through every function signature. Contextvars are the
  correct primitive here (unlike thread-locals) because they follow asyncio task boundaries correctly.
- **`X-Request-Id`: echo if provided, mint if not.** Letting a caller supply their own id (rather than
  always generating a fresh one) lets a request be traced end-to-end across service boundaries in a
  larger system, which matches constraint #5's "ready for integration" spirit even outside the
  events/outbox mechanism specifically.
- **`OutboxEventType` centralized once it had ≥2 real uses**: Phase 3 used a single literal string
  (`"approval_request.created"`) since introducing an enum for one value would've been premature; Phase
  4 added three more (`.approved`/`.rejected`/`.cancelled`), at which point the duplication/typo risk
  became real, so all four now live in `app/domain/enums.py`.
- **Multi-stage Dockerfile, uv builder + slim runtime**: dependency-install and project-install are two
  separate `uv sync` calls in their own `COPY`+`RUN` layers, specifically so editing application code
  doesn't invalidate the (much slower) dependency-resolution layer on rebuild. The runtime stage starts
  fresh from `python:3.12-slim-bookworm` (no `uv`, no build tooling, no apt cache) and just copies the
  already-built `.venv` + app code — smaller image, smaller attack surface. Runs as a non-root `appuser`
  (uid 1000); `--chown` on the `COPY` avoids a separate `chown -R` layer over the whole tree.
- **Migrate-then-serve entrypoint, not a startup event**: `docker-entrypoint.sh` runs
  `alembic upgrade head` before `exec uvicorn`, rather than running migrations from inside the app's own
  FastAPI startup handler. This keeps "apply schema changes" a distinct, visible step in container logs
  (and a distinct failure mode — `set -e` means a bad migration stops the container before it ever binds
  a port) separate from "serve requests," and matches how migrations are already run for local/manual use
  (`uv run alembic upgrade head`) — one mechanism, two invocation paths.
- **`HEALTHCHECK` hits `/health` (liveness), not `/ready` (readiness)**: Docker's `HEALTHCHECK` and
  compose's `depends_on: condition: service_healthy` model "is the container up," not "is every
  downstream dependency reachable right now" — that distinction already exists at the `/health` vs
  `/ready` layer (Phase 1). Nothing in this compose file needs to wait on the *app* being ready (only the
  app needs to wait on *Postgres*), so liveness is the right check here. Uses `python -c
  "urllib.request..."` rather than installing `curl` into the slim image — stdlib is already there.
- **Tests stay out of the image** (`.dockerignore` excludes `tests/`): the image is a lean runtime
  artifact; `uv run pytest` locally (or in CI) is the test-running path, matching how the assignment's
  README run/test commands are already split (`make run` vs `make test`). Running tests *inside* the
  built container was considered and rejected — it would mean shipping dev dependencies (pytest, ruff)
  into the runtime image, which is exactly the attack-surface/image-bloat tradeoff multi-stage builds
  exist to avoid.
- **Postgres data in a named volume, port 5432 published to the host**: the volume means
  `docker compose down` (without `-v`) preserves data across restarts, matching real deployment behavior
  more closely than an ephemeral container filesystem; publishing 5432 is a deliberate local-dev
  convenience (connect with `psql`/a GUI client directly) accepted as a minor, documented tradeoff against
  the small risk of colliding with a host Postgres already using that port.

## Conventions

- `domain/` must not import from `api/`, `db/`, or FastAPI/SQLAlchemy — keep business rules storage- and
  transport-agnostic.
- Never return ORM models directly from routes; always go through a `schemas/` Pydantic model.
- Every mutating service method writes its audit log entry and outbox event in the same DB transaction
  as the state change it describes — never as a separate best-effort step.
- While a migration has never been applied anywhere outside this local checkout (i.e. no real environment
  has run it), fix it in place and re-verify upgrade/downgrade/`alembic check` rather than stacking a
  second migration on top. Once something is deployed, this no longer applies — new schema changes always
  get a new migration.
