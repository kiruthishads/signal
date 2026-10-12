"""
Regression tests for the GitHub OAuth token flow:

  POST /v1/auth/sync      — persists the GitHub provider token on the user
  POST /v1/analysis/start — requires that token before enqueuing a job

The DB session and authenticated user are replaced with in-memory fakes so the
tests exercise the real routers, auth dependency wiring and response models.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest

_ENV_DEFAULTS = {
    "DATABASE_URL":             "postgresql+asyncpg://x:x@localhost/test",
    "SUPABASE_URL":             "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    "SUPABASE_JWT_SECRET":      "test-jwt-secret-32-chars-minimum!!",
    "GITHUB_CLIENT_ID":         "test-client-id",
    "GITHUB_CLIENT_SECRET":     "test-client-secret",
    "GEMINI_API_KEY":           "test-gemini-key",
    "ENVIRONMENT":              "test",
    "DEBUG":                    "false",
}

for key, value in _ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)

_FAKE_TOKEN = "gho_test_token_not_real"


class _FakeResult:
    """No active jobs, zero jobs used today."""

    def scalar_one_or_none(self):
        return None

    def scalar_one(self):
        return 0


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.flushes = 0

    async def execute(self, *_args, **_kwargs):
        return _FakeResult()

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1
        # Emulate server-side defaults the real database would populate.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if hasattr(obj, "started_at") and obj.started_at is None:
                obj.started_at = datetime.now(timezone.utc)

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


def _make_user(token: str | None):
    from app.models.user import User

    return User(
        id=uuid.uuid4(),
        github_id=252216474,
        github_username="venkatcharan-ds",
        github_access_token=token,
        is_super_admin=False,
    )


@pytest.fixture(scope="module")
def app():
    from app.config import get_settings
    get_settings.cache_clear()

    from app.main import app as fastapi_app
    yield fastapi_app
    get_settings.cache_clear()


@pytest.fixture
def ctx(app):
    """Yields (client, session, set_user) with dependency overrides installed."""
    from fastapi.testclient import TestClient
    from app.core.deps import get_current_user
    from app.database import get_db

    session = _FakeSession()
    state: dict = {"user": None}

    async def _get_db():
        yield session

    async def _get_user():
        return state["user"]

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _get_user

    def set_user(user):
        state["user"] = user
        return user

    with TestClient(app) as client:
        yield client, session, set_user

    app.dependency_overrides.clear()


# ── /v1/analysis/start ────────────────────────────────────────────────────────

def test_start_analysis_with_github_token_enqueues_job(ctx):
    client, session, set_user = ctx
    user = set_user(_make_user(_FAKE_TOKEN))

    res = client.post("/v1/analysis/start", headers={"Authorization": "Bearer x"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "queued"
    assert body["user_id"] == str(user.id)
    assert len(session.added) == 1
    # The token must never be echoed back to the client.
    assert _FAKE_TOKEN not in res.text


def test_start_analysis_without_github_token_returns_422(ctx):
    client, session, set_user = ctx
    set_user(_make_user(None))

    res = client.post("/v1/analysis/start", headers={"Authorization": "Bearer x"})

    assert res.status_code == 422
    assert "No GitHub token on file" in res.json()["detail"]
    assert session.added == []


def test_start_analysis_requires_authentication(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        res = client.post("/v1/analysis/start")

    assert res.status_code in (401, 403)


# ── /v1/auth/sync (token persistence) ─────────────────────────────────────────

def test_sync_persists_provider_token(ctx):
    client, session, set_user = ctx
    user = set_user(_make_user(None))

    res = client.post(
        "/v1/auth/sync",
        headers={"Authorization": "Bearer x"},
        json={"provider_token": _FAKE_TOKEN},
    )

    assert res.status_code == 200, res.text
    assert user.github_access_token == _FAKE_TOKEN
    assert session.flushes >= 1
    assert res.json()["github_username"] == "venkatcharan-ds"
    assert _FAKE_TOKEN not in res.text


def test_sync_without_provider_token_keeps_existing_token(ctx):
    client, _session, set_user = ctx
    user = set_user(_make_user(_FAKE_TOKEN))

    res = client.post(
        "/v1/auth/sync",
        headers={"Authorization": "Bearer x"},
        json={"provider_token": None},
    )

    assert res.status_code == 200
    assert user.github_access_token == _FAKE_TOKEN


def test_sync_then_start_analysis_succeeds(ctx):
    """End-to-end: a user with no token syncs one, then can start an analysis."""
    client, _session, set_user = ctx
    set_user(_make_user(None))
    headers = {"Authorization": "Bearer x"}

    assert client.post("/v1/analysis/start", headers=headers).status_code == 422
    assert client.post(
        "/v1/auth/sync", headers=headers, json={"provider_token": _FAKE_TOKEN}
    ).status_code == 200
    assert client.post("/v1/analysis/start", headers=headers).status_code == 200


def test_unversioned_sync_path_is_not_routed(app):
    """The production bug: the frontend called /auth/sync (no /v1) and got 404."""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        res = client.post("/auth/sync", json={"provider_token": _FAKE_TOKEN})

    assert res.status_code == 404
