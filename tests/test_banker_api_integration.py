"""End-to-end integration tests for the ``bluey-banker-api`` ``lambda_handler``.

These tests exercise the full ``list`` and ``mark_read`` request pipelines
against ``moto``-mocked DynamoDB (moto v5 ``mock_aws``). Unlike the pure-logic
property tests, these drive the actual Lambda entry point end to end:
Cognito-style event in, JSON response out, real (mocked) DynamoDB tables in
between.

The handler module (``bluey/lambda/bluey-banker-api/handler.py``) binds its
boto3 DynamoDB resource and every ``Table(...)`` at MODULE IMPORT time from
env vars. Therefore the mock MUST be started, the region + table-name env vars
set, and the tables created BEFORE the module is imported. The ``handler``
fixture below does exactly that: it enters ``mock_aws()``, sets env vars,
creates + seeds the tables, then loads the module from its file path (its
directory name is hyphenated, so a normal ``import`` is impossible) under a
UNIQUE module name so it never collides with the copy other test modules load
into ``sys.modules``.

Validates (task 9.3 — list end-to-end): Requirements 1.1, 2.1, 3.3, 4.1, 4.2,
5.1, 5.3, 5.4.
Validates (task 9.4 — mark_read end-to-end): Requirements 4.4, 4.5, 4.6, 4.7,
5.6.
"""
import importlib.util
import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Known table names (also written into the env vars the handler reads at import).
# ---------------------------------------------------------------------------
_REGION = "us-east-1"
_TABLE_ENV = {
    "SESSIONS_TABLE": "sessions",
    "CUSTOMERS_TABLE": "customers",
    "BANKERS_TABLE": "bankers",
    "DOCUMENTS_TABLE": "documents",
    "APPLICATIONS_TABLE": "applications",
    "ACCOUNTS_TABLE": "accounts",
    "CREDIT_TABLE": "credit",
    "READ_STATE_TABLE": "bluey-banker-read-state",
    "ASSIGNMENTS_TABLE": "bluey-banker-assignments",
}

# Handful of email constants mirroring the seed data.
_EMAIL_GENERAL = "lindiwe.dube@standardbank.co.za"  # banker-001, tier general
_EMAIL_GENERAL_2 = "sipho.mabaso@standardbank.co.za"  # banker-002, tier general
_EMAIL_PREMIUM = "themba.ndlovu@standardbank.co.za"  # banker-003, tier premium

_HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "lambda" / "bluey-banker-api" / "handler.py"
)
# Unique module name so we never collide with the copy that other test modules
# (e.g. test_banker_gather.py) load into sys.modules under a different name.
_MODULE_NAME = "banker_handler_integration"


def _create_tables(dynamodb):
    """Create every table the handler binds at import, PAY_PER_REQUEST."""

    def table(name, keys):
        key_schema = [{"AttributeName": k, "KeyType": kt} for k, kt in keys]
        attr_defs = [{"AttributeName": k, "AttributeType": "S"} for k, _ in keys]
        return dynamodb.create_table(
            TableName=name,
            KeySchema=key_schema,
            AttributeDefinitions=attr_defs,
            BillingMode="PAY_PER_REQUEST",
        )

    table(_TABLE_ENV["SESSIONS_TABLE"], [("sessionId", "HASH")])
    table(_TABLE_ENV["CUSTOMERS_TABLE"], [("customerId", "HASH")])
    table(_TABLE_ENV["BANKERS_TABLE"], [("bankerId", "HASH")])
    table(_TABLE_ENV["DOCUMENTS_TABLE"], [("sessionId", "HASH"), ("docType", "RANGE")])
    table(_TABLE_ENV["APPLICATIONS_TABLE"], [("reference", "HASH")])
    table(_TABLE_ENV["ACCOUNTS_TABLE"], [("customerId", "HASH"), ("accountId", "RANGE")])
    table(_TABLE_ENV["CREDIT_TABLE"], [("customerId", "HASH"), ("sessionId", "RANGE")])
    table(
        _TABLE_ENV["READ_STATE_TABLE"],
        [("bankerId", "HASH"), ("itemId", "RANGE")],
    )
    table(_TABLE_ENV["ASSIGNMENTS_TABLE"], [("itemId", "HASH")])


def _seed(dynamodb):
    """Seed a representative multi-banker scenario (mirrors seed_data shapes).

    - bankers: banker-001 (general), banker-003 (premium).
    - customers: cust-001 -> banker-001; cust-002 -> banker-003.
    - a pending-review WALK-IN session (no customerId) -> general pool.
    - a pending application APP-1 for cust-001 (Savings) -> banker-001,
      account_opening.
    - a pending credit record for cust-002 -> banker-003, credit_application.
    - varied createdAt so oldest-first ordering is checkable.
    """
    bankers = dynamodb.Table(_TABLE_ENV["BANKERS_TABLE"])
    bankers.put_item(Item={"bankerId": "banker-001", "email": _EMAIL_GENERAL, "tier": "general"})
    bankers.put_item(Item={"bankerId": "banker-002", "email": _EMAIL_GENERAL_2, "tier": "general"})
    bankers.put_item(Item={"bankerId": "banker-003", "email": _EMAIL_PREMIUM, "tier": "premium"})

    customers = dynamodb.Table(_TABLE_ENV["CUSTOMERS_TABLE"])
    customers.put_item(
        Item={
            "customerId": "cust-001",
            "fullName": "Thabo Nkosi",
            "personalBanker": {"bankerId": "banker-001", "tier": "general"},
        }
    )
    customers.put_item(
        Item={
            "customerId": "cust-002",
            "fullName": "Priya Singh",
            "personalBanker": {"bankerId": "banker-003", "tier": "premium"},
        }
    )

    sessions = dynamodb.Table(_TABLE_ENV["SESSIONS_TABLE"])
    # Walk-in session (no customerId) -> general pool, pre_visit_enquiry.
    # Oldest timestamp so it sorts first among general-visible dated items.
    sessions.put_item(
        Item={
            "sessionId": "sess-walkin",
            "applicantName": "Nomsa Walk-In",
            "reviewStatus": "pending_review",
            "createdAt": "2026-08-20T09:00:00+00:00",
            "updatedAt": "2026-08-20T09:00:00+00:00",
        }
    )

    applications = dynamodb.Table(_TABLE_ENV["APPLICATIONS_TABLE"])
    # Pending application for cust-001 (Savings) -> banker-001, account_opening.
    # Newer than the walk-in so oldest-first puts the walk-in ahead of it.
    applications.put_item(
        Item={
            "reference": "APP-1",
            "sessionId": "sess-app-1",
            "customerId": "cust-001",
            "accountType": "Savings",
            "status": "Pending",
            "createdAt": "2026-08-24T13:00:00+00:00",
            "updatedAt": "2026-08-24T13:00:00+00:00",
        }
    )

    credit = dynamodb.Table(_TABLE_ENV["CREDIT_TABLE"])
    # Pending credit for cust-002 -> banker-003, credit_application.
    credit.put_item(
        Item={
            "customerId": "cust-002",
            "sessionId": "credit-002",
            "creditScore": 705,
            "reviewStatus": "pending_review",
            "createdAt": "2026-08-22T10:00:00+00:00",
            "updatedAt": "2026-08-22T10:00:00+00:00",
        }
    )


@pytest.fixture()
def handler(monkeypatch):
    """Start moto, set env, create + seed tables, then load the handler.

    The handler binds its tables at import, so it is loaded INSIDE the mock
    context AFTER env vars are set and tables created, under a unique module
    name (popped afterwards) to avoid sys.modules collisions.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("AWS_REGION_NAME", _REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    for env_name, table_name in _TABLE_ENV.items():
        monkeypatch.setenv(env_name, table_name)

    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=_REGION)
        _create_tables(dynamodb)
        _seed(dynamodb)

        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _HANDLER_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.modules.pop(_MODULE_NAME, None)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------
def _make_event(email, *, group="Bankers", query=None, body=None):
    """Build a Cognito-style HTTP-API event for the banker handler."""
    claims = {"cognito:groups": group}
    if email is not None:
        claims["email"] = email
    return {
        "requestContext": {"authorizer": {"jwt": {"claims": claims}}},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def _list_event(email):
    return _make_event(email, query={"action": "list"})


def _mark_read_event(email, item_id=None, **extra_body):
    body = {"action": "mark_read"}
    if item_id is not None:
        body["itemId"] = item_id
    body.update(extra_body)
    return _make_event(email, body=body)


def _parse(resp):
    return resp["statusCode"], json.loads(resp["body"])


_VALID_CATEGORIES = {
    "account_opening",
    "credit_application",
    "pre_visit_enquiry",
    "existing_customer_servicing",
    "unassigned_fallback",
}


# ===========================================================================
# Task 9.3 — list end-to-end
# ===========================================================================
def test_list_general_banker_sees_categorized_routed_ordered_unread(handler):
    """banker-001 (general) list is categorized, correctly routed, ordered, unread.

    With workload allocation wired in, the unassigned walk-in is allocated to
    the least-loaded general banker. banker-001 already owns APP-1 (workload 1)
    while banker-002 owns nothing (workload 0), so the walk-in is allocated to
    banker-002 and banker-001 therefore sees only its own assigned APP-1.

    Validates: Requirements 1.1, 2.1, 3.3, 4.1, 4.2, 5.1, 5.3, 5.4
    """
    status, body = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))

    assert status == 200
    assert body["bankerId"] == "banker-001"
    assert body["tier"] == "general"

    sessions = body["sessions"]
    item_ids = {s["itemId"] for s in sessions}

    # Routing (R5.1, R5.3, R5.4): banker-001 sees its own assigned application,
    # but NOT the walk-in (allocated to banker-002) nor banker-003's credit item.
    assert "application#APP-1" in item_ids
    assert "session#sess-walkin" not in item_ids
    assert "credit#cust-002#credit-002" not in item_ids
    assert len(sessions) == 1

    by_id = {s["itemId"]: s for s in sessions}
    # Categorization (R1.1): every item carries a valid workCategory.
    for s in sessions:
        assert s["workCategory"] in _VALID_CATEGORIES
    assert by_id["application#APP-1"]["workCategory"] == "account_opening"

    # Routing designation reporting: APP-1 is assigned to banker-001.
    assert by_id["application#APP-1"]["routing"] == "assigned"

    # Ordering (R2.1): oldest-first — createdAt non-decreasing across dated items.
    dated = [s["createdAt"] for s in sessions if s.get("createdAt")]
    assert dated == sorted(dated)

    # Read state (R4.1, R4.2): initially every item is unread and the count matches.
    for s in sessions:
        assert s["readState"] == "unread"
    assert body["unreadCount"] == 1


def test_list_premium_banker_sees_only_assigned_credit(handler):
    """banker-003 (premium) sees ONLY their assigned credit item, not the general pool.

    Validates: Requirements 5.1, 5.4
    """
    status, body = _parse(handler.lambda_handler(_list_event(_EMAIL_PREMIUM), None))

    assert status == 200
    assert body["bankerId"] == "banker-003"
    assert body["tier"] == "premium"

    sessions = body["sessions"]
    item_ids = {s["itemId"] for s in sessions}

    # Premium banker: exactly their assigned credit item, nothing from the pool.
    assert item_ids == {"credit#cust-002#credit-002"}
    assert "session#sess-walkin" not in item_ids
    assert "application#APP-1" not in item_ids

    credit_item = sessions[0]
    assert credit_item["workCategory"] == "credit_application"
    assert credit_item["routing"] == "assigned"
    assert credit_item["readState"] == "unread"
    assert body["unreadCount"] == 1


def test_list_unknown_email_is_identity_unresolved(handler):
    """An unknown banker email yields 403 identity_unresolved with no items.

    Validates: Requirement 5.6
    """
    status, body = _parse(
        handler.lambda_handler(_list_event("nobody@standardbank.co.za"), None)
    )
    assert status == 403
    assert body["error"] == "identity_unresolved"


# ===========================================================================
# Task 9.4 — mark_read end-to-end
# ===========================================================================
def test_mark_read_routed_item_persists_and_drops_unread_count(handler):
    """Marking a routed item read persists and drops unreadCount on re-list.

    Validates: Requirements 4.4, 4.5, 4.6
    """
    # List first to pick a routed itemId and record the baseline count.
    _, list_body = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    baseline_unread = list_body["unreadCount"]
    target_id = list_body["sessions"][0]["itemId"]

    # Mark it read.
    status, body = _parse(
        handler.lambda_handler(_mark_read_event(_EMAIL_GENERAL, target_id), None)
    )
    assert status == 200
    assert body["readState"] == "read"
    assert body["itemId"] == target_id

    # Re-list: that item now reads "read" and the count dropped by exactly one.
    _, relist = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    by_id = {s["itemId"]: s for s in relist["sessions"]}
    assert by_id[target_id]["readState"] == "read"
    assert relist["unreadCount"] == baseline_unread - 1

    # Idempotency (R4.6): marking again still succeeds and stays read.
    status2, body2 = _parse(
        handler.lambda_handler(_mark_read_event(_EMAIL_GENERAL, target_id), None)
    )
    assert status2 == 200
    assert body2["readState"] == "read"
    _, relist2 = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    assert relist2["unreadCount"] == baseline_unread - 1


def test_mark_read_unrouted_item_is_rejected_without_side_effects(handler):
    """Marking an item not routed to the banker returns 403 and changes nothing.

    banker-001 (general) attempts to mark banker-003's assigned credit item.

    Validates: Requirements 4.7
    """
    unrouted_id = "credit#cust-002#credit-002"

    status, body = _parse(
        handler.lambda_handler(_mark_read_event(_EMAIL_GENERAL, unrouted_id), None)
    )
    assert status == 403
    assert body["error"] == "not_authorized_for_item"
    assert body["itemId"] == unrouted_id

    # State unchanged: banker-003's list still shows the item as unread.
    _, premium = _parse(handler.lambda_handler(_list_event(_EMAIL_PREMIUM), None))
    by_id = {s["itemId"]: s for s in premium["sessions"]}
    assert by_id[unrouted_id]["readState"] == "unread"
    assert premium["unreadCount"] == 1


def test_mark_read_missing_item_id_is_400(handler):
    """mark_read without an itemId returns 400 itemId required.

    Validates: Requirement 4.4
    """
    status, body = _parse(
        handler.lambda_handler(_mark_read_event(_EMAIL_GENERAL, item_id=None), None)
    )
    assert status == 400
    assert body["error"] == "itemId required"


def test_mark_read_unknown_email_is_identity_unresolved(handler):
    """mark_read with an unknown email returns 403 identity_unresolved.

    Validates: Requirement 5.6
    """
    status, body = _parse(
        handler.lambda_handler(
            _mark_read_event("nobody@standardbank.co.za", "session#sess-walkin"), None
        )
    )
    assert status == 403
    assert body["error"] == "identity_unresolved"


def test_mark_read_non_bankers_group_is_forbidden(handler):
    """A non-Bankers group request returns 403 forbidden.

    Validates: Requirement 5.6 (guard preserved)
    """
    event = _make_event(
        _EMAIL_GENERAL,
        group="Customers",
        body={"action": "mark_read", "itemId": "session#sess-walkin"},
    )
    status, body = _parse(handler.lambda_handler(event, None))
    assert status == 403
    assert "forbidden" in body["error"]


# ===========================================================================
# stale-query-notification — staleness surfaced in the list payload
# ===========================================================================
def test_list_payload_includes_stale_count_and_per_session_is_stale(handler):
    """The list payload carries a top-level staleCount and per-session isStale.

    Every session object exposes a boolean ``isStale`` and the top-level
    ``staleCount`` equals the number of stale sessions.

    Validates: Requirements 6.2, 6.3, 6.4, 6.7
    """
    status, body = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    assert status == 200

    # Top-level staleCount present and well-formed (R6.2).
    assert "staleCount" in body
    assert isinstance(body["staleCount"], int)
    assert body["staleCount"] >= 0

    sessions = body["sessions"]
    # Every session carries a boolean isStale that is never absent/null (R6.3, R6.7).
    for s in sessions:
        assert "isStale" in s
        assert isinstance(s["isStale"], bool)

    # staleCount equals the number of stale sessions (R6.4).
    assert body["staleCount"] == sum(1 for s in sessions if s["isStale"])
    assert 0 <= body["staleCount"] <= len(sessions)


def test_list_seeded_items_over_24h_are_flagged_stale(handler):
    """The seeded 2026-08 items are far older than 24h, so all are stale.

    Confirms real ISO timestamps flow through the Waiting_Time derivation into
    the staleness flag end to end.

    Validates: Requirements 6.4
    """
    status, body = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    assert status == 200

    sessions = body["sessions"]
    assert sessions, "expected at least one seeded session"
    # All seeded createdAt values are well in the past relative to now, so every
    # dated session must be flagged stale and staleCount must equal that total.
    for s in sessions:
        assert s["isStale"] is True
    assert body["staleCount"] == len(sessions)


# ===========================================================================
# banker-workload-allocation — general-pool item allocation end to end
# ===========================================================================
def test_list_allocates_walkin_to_general_banker_and_persists_and_flips_ownership(handler):
    """An unassigned walk-in is allocated to a general banker, persisted, and flips to assigned.

    The seeded walk-in session (``sess-walkin``) has no ``customerId`` and thus
    no ``personalBanker``, so it starts in the General_Pool. banker-001 already
    owns APP-1 (workload 1) while banker-002 owns nothing (workload 0), so the
    least-loaded general banker is banker-002.

    Asserts:
      1. A ``list`` call as a general banker allocates the walk-in to a general
         banker (routing == "assigned" and it appears for the owning banker),
         and an Allocation_Record now exists in the assignments table for that
         itemId (read the assignments table directly to confirm).
      2. A ``list`` call as the OTHER general banker does NOT show the walk-in,
         confirming it flipped from general_pool to assigned (owned by the
         allocated banker only).

    Validates: Requirements 6.1, 6.2, 6.3
    """
    walkin_id = "session#sess-walkin"

    # (1) List as banker-002 (least loaded) — the walk-in is allocated to it.
    status, body = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL_2), None))
    assert status == 200
    assert body["bankerId"] == "banker-002"
    assert body["tier"] == "general"

    by_id = {s["itemId"]: s for s in body["sessions"]}
    assert walkin_id in by_id, "walk-in should be allocated to and visible for banker-002"
    assert by_id[walkin_id]["routing"] == "assigned"

    # An Allocation_Record now exists for the walk-in, owned by banker-002.
    dynamodb = boto3.resource("dynamodb", region_name=_REGION)
    assignments = dynamodb.Table(_TABLE_ENV["ASSIGNMENTS_TABLE"])
    record = assignments.get_item(Key={"itemId": walkin_id}).get("Item")
    assert record is not None, "an Allocation_Record must be persisted for the walk-in"
    assert record["assignedBankerId"] == "banker-002"

    # (2) List as the OTHER general banker (banker-001) — it must NOT see the
    # walk-in, confirming ownership flipped from general_pool to assigned.
    _, other = _parse(handler.lambda_handler(_list_event(_EMAIL_GENERAL), None))
    other_ids = {s["itemId"] for s in other["sessions"]}
    assert walkin_id not in other_ids, "the allocated walk-in must not be visible to banker-001"
