"""Unit tests for the banker-query-triage ``itemId`` scheme and candidate gather.

These tests exercise two pieces of the ``bluey-banker-api`` handler:

* ``build_item_id`` and the pure ``normalize_*`` helpers, which produce the
  canonical queue-item identifier in three shapes:
    - session-sourced:     ``session#<sessionId>``
    - application-sourced:  ``application#<reference>``
    - credit-sourced:       ``credit#<customerId>#<sessionId>``
* ``gather_candidates``, which scans sessions (pending review), applications
  (pending), and credit (pending review), normalizes each record, and
  de-duplicates a session already represented by an application so a standalone
  session candidate is not emitted alongside its application.

Validates: Requirements 1.1 (exactly one canonical identity per queue item)
and 1.7 (candidates carry the canonical ``itemId``; de-duplication of a
session already represented by an application).

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It
is loaded from its file path via ``importlib.util.spec_from_file_location``.
The module builds boto3 DynamoDB resources at import time; ``resource()`` does
not make network calls, but a region must be resolvable, so AWS region env
vars are set before loading. ``build_item_id``/``normalize_*`` are pure, and
``gather_candidates`` accepts fake table stubs, so no real AWS is touched.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_handler():
    """Load the hyphenated banker-api handler module from its file path."""
    # Ensure a region is resolvable so boto3.resource() at import succeeds
    # without AWS config files present.
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.setdefault("AWS_REGION_NAME", "us-east-1")

    handler_path = (
        Path(__file__).resolve().parents[1]
        / "lambda"
        / "bluey-banker-api"
        / "handler.py"
    )
    spec = importlib.util.spec_from_file_location("bluey_banker_api_handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    # Register so any internal references resolve; harmless for a leaf module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handler = _load_handler()


class FakeTable:
    """Minimal DynamoDB table stub exposing ``.scan(**kwargs)``.

    Returns a fixed ``{"Items": [...]}`` payload regardless of any
    ``FilterExpression`` passed. The real filtering is DynamoDB's job;
    ``gather_candidates`` layers its own de-duplication on top, which is what
    these tests exercise.
    """

    def __init__(self, items):
        self._items = items

    def scan(self, **kwargs):
        return {"Items": list(self._items)}


# ---------------------------------------------------------------------------
# build_item_id / normalize_* — canonical itemId shapes (Requirement 1.1)
# ---------------------------------------------------------------------------


def test_build_item_id_session_shape():
    """Session-sourced itemId is ``session#<sessionId>``. (Requirement 1.1)"""
    assert handler.build_item_id("session", session_id="sess-1") == "session#sess-1"


def test_build_item_id_application_shape():
    """Application-sourced itemId is ``application#<reference>``. (Requirement 1.1)"""
    assert handler.build_item_id("application", reference="APP-42") == "application#APP-42"


def test_build_item_id_credit_shape():
    """Credit-sourced itemId is ``credit#<customerId>#<sessionId>``. (Requirement 1.1)"""
    result = handler.build_item_id("credit", customer_id="cust-9", session_id="credit-9")
    assert result == "credit#cust-9#credit-9"


def test_build_item_id_unknown_source_raises():
    """An unknown source is rejected so identity is always well-formed. (Requirement 1.1)"""
    with pytest.raises(ValueError):
        handler.build_item_id("mystery")


def test_normalize_session_produces_session_item_id():
    """normalize_session carries the canonical session itemId. (Requirements 1.1, 1.7)"""
    candidate = handler.normalize_session(
        {"sessionId": "sess-7", "customerId": "cust-7", "reference": "REF-7"}
    )
    assert candidate["itemId"] == "session#sess-7"
    assert candidate["source"] == "session"
    assert candidate["customerId"] == "cust-7"


def test_normalize_application_produces_application_item_id():
    """normalize_application carries the canonical application itemId. (Requirements 1.1, 1.7)"""
    candidate = handler.normalize_application(
        {"reference": "APP-100", "sessionId": "sess-100", "accountType": "Savings"}
    )
    assert candidate["itemId"] == "application#APP-100"
    assert candidate["source"] == "application"
    assert candidate["reference"] == "APP-100"
    assert candidate["accountType"] == "Savings"


def test_normalize_credit_produces_credit_item_id():
    """normalize_credit carries the canonical credit itemId. (Requirements 1.1, 1.7)"""
    candidate = handler.normalize_credit(
        {"customerId": "cust-4", "sessionId": "credit-4"}
    )
    assert candidate["itemId"] == "credit#cust-4#credit-4"
    assert candidate["source"] == "credit"
    assert candidate["customerId"] == "cust-4"


# ---------------------------------------------------------------------------
# gather_candidates — inclusion, exclusion, and de-duplication (Requirement 1.7)
# ---------------------------------------------------------------------------


def test_gather_includes_pending_application():
    """A pending application is emitted as an application candidate. (Requirement 1.7)"""
    apps = FakeTable([
        {"reference": "APP-1", "sessionId": "sess-1", "status": "Pending", "accountType": "Savings"},
    ])
    sessions = FakeTable([])
    credit = FakeTable([])

    candidates = handler.gather_candidates(
        sessions_tbl=sessions, applications_tbl=apps, credit_tbl=credit
    )

    item_ids = {c["itemId"] for c in candidates}
    assert item_ids == {"application#APP-1"}


def test_gather_excludes_non_pending_application():
    """A non-pending application is not surfaced as a candidate. (Requirement 1.7)"""
    apps = FakeTable([
        {"reference": "APP-2", "sessionId": "sess-2", "status": "Approved"},
    ])
    candidates = handler.gather_candidates(
        sessions_tbl=FakeTable([]), applications_tbl=apps, credit_tbl=FakeTable([])
    )
    assert candidates == []


def test_gather_includes_pending_review_session():
    """A pending-review session with no linked application is emitted. (Requirement 1.7)"""
    sessions = FakeTable([
        {"sessionId": "sess-3", "reviewStatus": "pending_review", "customerId": "cust-3"},
    ])
    candidates = handler.gather_candidates(
        sessions_tbl=sessions, applications_tbl=FakeTable([]), credit_tbl=FakeTable([])
    )
    item_ids = {c["itemId"] for c in candidates}
    assert item_ids == {"session#sess-3"}


def test_gather_includes_pending_credit_record():
    """A pending-review credit record is emitted as a credit candidate. (Requirement 1.7)"""
    credit = FakeTable([
        {"customerId": "cust-5", "sessionId": "credit-5", "reviewStatus": "pending_review"},
    ])
    candidates = handler.gather_candidates(
        sessions_tbl=FakeTable([]), applications_tbl=FakeTable([]), credit_tbl=credit
    )
    item_ids = {c["itemId"] for c in candidates}
    assert item_ids == {"credit#cust-5#credit-5"}


def test_gather_excludes_non_pending_credit_record():
    """A credit record without a pending marker is excluded. (Requirement 1.7)"""
    credit = FakeTable([
        {"customerId": "cust-6", "sessionId": "credit-6", "rating": "good"},
    ])
    candidates = handler.gather_candidates(
        sessions_tbl=FakeTable([]), applications_tbl=FakeTable([]), credit_tbl=credit
    )
    assert candidates == []


def test_gather_dedupes_session_represented_by_application():
    """A session already represented by an application is NOT emitted twice.

    The applications table has a pending application linked to ``sess-dup``,
    and the sessions table returns that same ``sess-dup`` as pending review.
    gather_candidates must emit only the application candidate, de-duplicating
    the standalone session (mirroring the handler's ``seen_sessions`` logic).

    Validates: Requirement 1.7
    """
    apps = FakeTable([
        {"reference": "APP-DUP", "sessionId": "sess-dup", "status": "pending", "accountType": "Savings"},
    ])
    sessions = FakeTable([
        {"sessionId": "sess-dup", "reviewStatus": "pending_review", "customerId": "cust-dup"},
        {"sessionId": "sess-solo", "reviewStatus": "pending_review", "customerId": "cust-solo"},
    ])
    credit = FakeTable([])

    candidates = handler.gather_candidates(
        sessions_tbl=sessions, applications_tbl=apps, credit_tbl=credit
    )
    item_ids = sorted(c["itemId"] for c in candidates)

    # The duplicated session is represented only by its application;
    # the unrelated session survives as its own candidate.
    assert item_ids == ["application#APP-DUP", "session#sess-solo"]
    assert "session#sess-dup" not in item_ids


def test_gather_combines_all_three_sources():
    """Pending items from all three sources are gathered together. (Requirement 1.7)"""
    apps = FakeTable([
        {"reference": "APP-A", "sessionId": "sess-a", "status": "Pending"},
    ])
    sessions = FakeTable([
        {"sessionId": "sess-b", "reviewStatus": "pending_review", "customerId": "cust-b"},
    ])
    credit = FakeTable([
        {"customerId": "cust-c", "sessionId": "credit-c", "reviewStatus": "pending_review"},
    ])

    candidates = handler.gather_candidates(
        sessions_tbl=sessions, applications_tbl=apps, credit_tbl=credit
    )
    item_ids = sorted(c["itemId"] for c in candidates)
    assert item_ids == [
        "application#APP-A",
        "credit#cust-c#credit-c",
        "session#sess-b",
    ]
