"""
Regression tests for Gemini quota handling in the orchestrator.

Production failure (job 61ddc7df…): the configured model had a free-tier quota
limit of 0, so every call returned 429 RESOURCE_EXHAUSTED. The orchestrator
treated it as transient and retried three times (~4 minutes in "queued")
before failing with the raw provider error.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ENV = {
    "DATABASE_URL":              "postgresql+asyncpg://x:x@localhost/test",
    "SUPABASE_URL":              "https://testref.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    "SUPABASE_JWT_SECRET":       "test-jwt-secret-at-least-32-chars!!",
    "GITHUB_CLIENT_ID":          "test-client-id",
    "GITHUB_CLIENT_SECRET":      "test-client-secret",
    "GEMINI_API_KEY":            "test-gemini-key",
    "ENVIRONMENT":               "test",
    "DEBUG":                     "false",
}
for k, v in _ENV.items():
    os.environ.setdefault(k, v)

from app.config import get_settings  # noqa: E402
from app.pipeline.orchestrator import (  # noqa: E402
    _is_quota_exhausted,
    _is_retryable,
    run_analysis,
)

# Verbatim shape of the production error (trimmed), as raised by google-genai.
ZERO_LIMIT_ERROR = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
    "current quota, please check your plan and billing details. \\n* Quota exceeded "
    "for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 0, model: gemini-3.1-pro\\nPlease retry in 4h11m16.06s.', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc."
    "QuotaFailure', 'violations': [{'quotaId': "
    "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}, {'@type': "
    "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '15076s'}]}}"
)

# A normal per-minute rate limit: non-zero limit, short provider retry delay.
TRANSIENT_RATE_LIMIT_ERROR = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'Quota exceeded for "
    "metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 15, model: gemini-3.6-flash\\nPlease retry in 20s.', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': "
    "[{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '20s'}]}}"
)

PER_DAY_ERROR = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'limit: 1500, "
    "model: gemini-3.6-flash', 'details': [{'violations': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}, "
    "{'retryDelay': '45s'}]}}"
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(run_side_effect, retry_count: int = 0):
    """Run run_analysis() with a patched _run(); return (factory_calls, db2)."""
    mock_job = MagicMock()
    mock_job.retry_count = retry_count
    db2 = AsyncMock()
    db2.get = AsyncMock(return_value=mock_job)

    calls = []

    def factory():
        calls.append(1)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=AsyncMock() if len(calls) == 1 else db2)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    with patch("app.pipeline.orchestrator._run", side_effect=run_side_effect):
        asyncio.run(run_analysis(uuid.uuid4(), factory))
    return calls, db2


def _compiled_update(db2) -> str:
    stmt = db2.execute.call_args[0][0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# ── Classification ────────────────────────────────────────────────────────────

class TestClassification:
    def test_zero_limit_is_quota_exhausted(self):
        assert _is_quota_exhausted(Exception(ZERO_LIMIT_ERROR))

    def test_per_day_quota_is_quota_exhausted(self):
        assert _is_quota_exhausted(Exception(PER_DAY_ERROR))

    def test_long_retry_delay_is_quota_exhausted(self):
        exc = Exception("429 RESOURCE_EXHAUSTED limit: 15 {'retryDelay': '900s'}")
        assert _is_quota_exhausted(exc)

    def test_transient_rate_limit_is_not_quota_exhausted(self):
        exc = Exception(TRANSIENT_RATE_LIMIT_ERROR)
        assert not _is_quota_exhausted(exc)
        assert _is_retryable(exc)

    def test_non_429_errors_are_not_quota_exhausted(self):
        assert not _is_quota_exhausted(Exception("connection reset, limit: 0"))
        assert not _is_quota_exhausted(asyncio.TimeoutError())


# ── run_analysis behaviour ────────────────────────────────────────────────────

class TestQuotaExhaustedFailsImmediately:
    def test_zero_limit_marks_failed_on_first_attempt(self):
        _, db2 = _run(Exception(ZERO_LIMIT_ERROR), retry_count=0)
        sql = _compiled_update(db2)
        assert "'failed'" in sql
        assert "'queued'" not in sql
        db2.commit.assert_called_once()

    def test_failure_message_is_actionable_and_hides_provider_payload(self):
        _, db2 = _run(Exception(ZERO_LIMIT_ERROR), retry_count=0)
        sql = _compiled_update(db2)
        assert "AI provider quota exhausted for model gemini-3.1-pro" in sql
        assert "administrator" in sql
        assert "googleapis.com" not in sql
        assert "retryDelay" not in sql


class TestTransientRateLimitStillRetries:
    def test_transient_429_is_requeued(self):
        _, db2 = _run(Exception(TRANSIENT_RATE_LIMIT_ERROR), retry_count=0)
        sql = _compiled_update(db2)
        assert "'queued'" in sql
        assert "retry_count" in sql

    def test_transient_429_fails_after_max_retries(self):
        _, db2 = _run(Exception(TRANSIENT_RATE_LIMIT_ERROR), retry_count=3)
        sql = _compiled_update(db2)
        assert "'failed'" in sql
        # Not a quota-exhaustion failure, so the raw error is preserved.
        assert "quota exhausted" not in sql.lower()


class TestSuccessfulAnalysis:
    def test_success_opens_no_failure_session(self):
        async def ok(job_id, db, bound):
            return None

        calls, db2 = _run(ok)
        assert len(calls) == 1
        db2.execute.assert_not_called()
