"""
Regression tests for retry visibility in the analysis status stream.

Production job 04dac9c2…: Gemini returned 503 at the capability stage (60 %),
the orchestrator correctly re-queued the job, but the SSE label reported
"Waiting — position 1 in queue", so the UI looked like it had lost its place
instead of saying it was retrying after an error.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

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
from app.routers.analysis import _progress_label  # noqa: E402
from app.schemas.analysis import AnalysisProgressEvent  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _row(status: str, retry_count: int = 0, current_step: str | None = None):
    return SimpleNamespace(status=status, retry_count=retry_count, current_step=current_step)


def test_requeued_job_reports_retry_not_queue_position():
    row = _row("queued", retry_count=2, current_step="Retrying (attempt 2 of 3)…")
    label = _progress_label(row, queue_position=1)
    assert "retrying automatically" in label
    assert "attempt 2 of 3" in label
    assert "position" not in label


def test_fresh_queued_job_still_reports_queue_position():
    label = _progress_label(_row("queued", retry_count=0), queue_position=3)
    assert label == "Waiting — position 3 in queue"


def test_running_job_reports_current_step():
    row = _row("ai_analysis", retry_count=1, current_step="Running AI capability analysis")
    assert _progress_label(row, queue_position=0) == "Running AI capability analysis"


def test_progress_event_carries_retry_count_with_safe_default():
    assert AnalysisProgressEvent(step="queued", label="x", progress=0).retry_count == 0
    event = AnalysisProgressEvent(step="queued", label="x", progress=0, retry_count=2)
    assert '"retry_count":2' in event.model_dump_json()
