import base64
import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import require_read
from app.auth.exceptions import InvalidCredentialsError
from app.auth.models import Action, Principal
from app.auth.stub import StubAuthProvider, encode_bearer_token

# --- StubAuthProvider --------------------------------------------------------------


def test_authenticate_valid_token_grants_expected_actions() -> None:
    provider = StubAuthProvider()
    token = encode_bearer_token(
        workspace_id="ws_1", user_id="usr_1", actions=[Action.READ, Action.CREATE]
    )

    principal = provider.authenticate(token)

    assert principal.workspace_id == "ws_1"
    assert principal.user_id == "usr_1"
    assert principal.has_action(Action.READ)
    assert principal.has_action(Action.CREATE)
    assert not principal.has_action(Action.DECIDE)


def test_authenticate_defaults_to_no_actions_when_omitted() -> None:
    provider = StubAuthProvider()
    token = encode_bearer_token(workspace_id="ws_1", user_id="usr_1")

    principal = provider.authenticate(token)

    assert principal.actions == frozenset()


@pytest.mark.parametrize("scheme", ["bearer", "Bearer", "BEARER", "BeArEr"])
def test_authenticate_scheme_is_case_insensitive(scheme: str) -> None:
    provider = StubAuthProvider()
    token = encode_bearer_token(workspace_id="ws_1", user_id="usr_1")
    _, _, raw = token.partition(" ")

    principal = provider.authenticate(f"{scheme} {raw}")

    assert principal.workspace_id == "ws_1"


def test_authenticate_missing_header_raises() -> None:
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate(None)


def test_authenticate_wrong_scheme_raises() -> None:
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate("Basic dXNlcjpwYXNz")


def test_authenticate_malformed_base64_raises() -> None:
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate("Bearer not-valid-base64!!!")


def test_authenticate_valid_base64_invalid_json_raises() -> None:
    token = base64.urlsafe_b64encode(b"not json").decode("ascii").rstrip("=")
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate(f"Bearer {token}")


def test_authenticate_missing_required_field_raises() -> None:
    payload = json.dumps({"workspace_id": "ws_1"}).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate(f"Bearer {token}")


def test_authenticate_unknown_action_raises() -> None:
    payload = json.dumps(
        {"workspace_id": "ws_1", "user_id": "usr_1", "actions": ["approval:superadmin"]}
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate(f"Bearer {token}")


def test_authenticate_non_dict_json_raises() -> None:
    payload = json.dumps(["ws_1", "usr_1"]).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    with pytest.raises(InvalidCredentialsError):
        StubAuthProvider().authenticate(f"Bearer {token}")


# --- require_action dependency (FastAPI integration) --------------------------------


def _build_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/workspaces/{workspace_id}/probe")
    async def probe(principal: Principal = Depends(require_read)) -> dict[str, str]:
        return {"user_id": principal.user_id}

    return app


@pytest.fixture
def probe_client() -> TestClient:
    return TestClient(_build_probe_app())


def test_require_action_rejects_missing_auth(probe_client: TestClient) -> None:
    response = probe_client.get("/workspaces/ws_1/probe")
    assert response.status_code == 401


def test_require_action_rejects_workspace_mismatch(probe_client: TestClient) -> None:
    token = encode_bearer_token(workspace_id="ws_1", user_id="usr_1", actions=[Action.READ])

    response = probe_client.get("/workspaces/ws_2/probe", headers={"Authorization": token})

    assert response.status_code == 403


def test_require_action_rejects_missing_action(probe_client: TestClient) -> None:
    token = encode_bearer_token(workspace_id="ws_1", user_id="usr_1", actions=[Action.CREATE])

    response = probe_client.get("/workspaces/ws_1/probe", headers={"Authorization": token})

    assert response.status_code == 403


def test_require_action_allows_matching_principal(probe_client: TestClient) -> None:
    token = encode_bearer_token(workspace_id="ws_1", user_id="usr_1", actions=[Action.READ])

    response = probe_client.get("/workspaces/ws_1/probe", headers={"Authorization": token})

    assert response.status_code == 200
    assert response.json() == {"user_id": "usr_1"}
