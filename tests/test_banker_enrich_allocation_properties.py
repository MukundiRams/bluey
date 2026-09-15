"""Property-based tests for allocation-aware ``enrich`` in
``bluey-banker-api/handler.py`` (Property 6).

``enrich`` resolves an item's ``assignedBankerId`` by precedence:
``personalBanker.bankerId`` (an Assigned_Customer) if present, else the
persisted ``Allocation_Record.assignedBankerId`` read back via
``get_allocation`` when a record with a truthy value exists, else ``None``. A
record missing (or with a falsy) ``assignedBankerId`` is treated as no
allocation, and a present ``personalBanker.bankerId`` is NEVER overridden by an
allocation lookup.

These Hypothesis property tests exercise ``enrich`` against injected in-memory
fake tables (a fake customers table and a fake assignments table), so no live
AWS is required. The handler module lives in a directory whose name contains
hyphens (``bluey-banker-api``), so it cannot be imported with a normal
``import``. It is loaded from its file path via
``importlib.util.spec_from_file_location`` — the same ``_load_handler()`` pattern
used across the banker property-test suite. The module builds boto3 DynamoDB
resources at import time; ``resource()`` makes no network call, but a region must
be resolvable, so AWS region env vars are set before loading. The function under
test takes injected fake tables, so no real AWS is touched.
"""
import importlib.util
import os
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st


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


# ---------------------------------------------------------------------------
# In-memory fake tables
# ---------------------------------------------------------------------------


class FakeCustomersTable:
    """Dict-backed stand-in for the ``bluey-customers`` DynamoDB table.

    Implements just the ``get_item(Key={"customerId": ...})`` surface used by
    ``_resolve_assigned_banker_id``: returns ``{"Item": {...}}`` when a customer
    record exists for that ``customerId``, else ``{}`` (no ``Item`` key). A
    stored customer may carry a ``personalBanker`` with a ``bankerId``, a
    ``personalBanker`` without a ``bankerId``, or no ``personalBanker`` at all.
    """

    def __init__(self, customers=None):
        self._store = dict(customers or {})

    def get_item(self, Key):
        customer_id = Key["customerId"]
        if customer_id in self._store:
            return {"Item": dict(self._store[customer_id])}
        return {}


class FakeAssignmentsTable:
    """Dict-backed stand-in for the ``bluey-banker-assignments`` DynamoDB table.

    Implements just the ``get_item(Key={"itemId": ...})`` surface used by
    ``get_allocation``: returns ``{"Item": {...}}`` when a record exists for that
    ``itemId``, else ``{}`` (no ``Item`` key). A stored record may carry a truthy
    ``assignedBankerId``, a missing/falsy ``assignedBankerId``, or no record may
    exist for the ``itemId`` at all.
    """

    def __init__(self, records=None):
        self._store = dict(records or {})

    def get_item(self, Key):
        item_id = Key["itemId"]
        if item_id in self._store:
            return {"Item": dict(self._store[item_id])}
        return {}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_banker_ids = st.text(min_size=1, max_size=12)
_customer_ids = st.text(min_size=1, max_size=12)
_item_ids = st.text(min_size=1, max_size=20)
_sources = st.sampled_from(["session", "application", "credit", "other", None])

# A falsy value that stands in for a missing/empty bankerId (never a valid id).
_falsy_ids = st.sampled_from([None, ""])


@st.composite
def _personal_banker(draw):
    """Draw a personalBanker variant: with bankerId, without bankerId, or None.

    Returns a value suitable for a customer record's ``personalBanker`` key.
    ``None`` signals the caller to omit the key entirely.
    """
    kind = draw(st.sampled_from(["with_banker", "no_banker_id", "absent"]))
    if kind == "with_banker":
        return {"bankerId": draw(_banker_ids)}
    if kind == "no_banker_id":
        # personalBanker present but no (or falsy) bankerId.
        return {"bankerId": draw(_falsy_ids)}
    return None  # absent


@st.composite
def _customer_record(draw):
    """Draw a customer record (may or may not carry a personalBanker)."""
    record = {}
    pb = draw(_personal_banker())
    if pb is not None:
        record["personalBanker"] = pb
    return record


@st.composite
def _allocation_record(draw):
    """Draw an Allocation_Record variant: truthy id, falsy/missing id, or None.

    ``None`` signals the caller to store no record at all for the itemId.
    """
    kind = draw(st.sampled_from(["truthy", "falsy", "absent"]))
    if kind == "truthy":
        return {"assignedBankerId": draw(_banker_ids)}
    if kind == "falsy":
        return {"assignedBankerId": draw(_falsy_ids)}
    return None  # no record stored


@st.composite
def _scenario(draw):
    """Draw a full scenario: item + fake customers + fake assignments tables."""
    item_id = draw(_item_ids)
    source = draw(_sources)

    # Some items have a customerId, some are walk-ins (no customerId).
    has_customer = draw(st.booleans())
    customer_id = draw(_customer_ids) if has_customer else None

    item = {"itemId": item_id, "source": source}
    if customer_id is not None:
        item["customerId"] = customer_id

    # Build the fake customers table: only add a record sometimes so a missing
    # customer record (→ no personalBanker) is exercised too.
    customers = {}
    if customer_id is not None and draw(st.booleans()):
        customers[customer_id] = draw(_customer_record())

    # Build the fake assignments table for this itemId.
    records = {}
    alloc = draw(_allocation_record())
    if alloc is not None:
        records[item_id] = alloc

    return item, FakeCustomersTable(customers), FakeAssignmentsTable(records)


def _expected_personal_banker_id(item, fake_customers):
    """Independently resolve the customer's personalBanker.bankerId, or None."""
    customer_id = item.get("customerId")
    if not customer_id:
        return None
    customer = fake_customers.get_item(Key={"customerId": customer_id}).get("Item") or {}
    personal_banker = customer.get("personalBanker") or {}
    banker_id = personal_banker.get("bankerId")
    return banker_id if banker_id else None


def _expected_allocation_id(item, fake_assignments):
    """Independently resolve the Allocation_Record's assignedBankerId, or None."""
    record = fake_assignments.get_item(Key={"itemId": item.get("itemId")}).get("Item") or {}
    banker_id = record.get("assignedBankerId")
    return banker_id if banker_id else None


# ---------------------------------------------------------------------------
# Property 6: Enrich resolves the assigned banker by precedence, robustly
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 6: Enrich resolves the assigned banker by precedence, robustly
@settings(max_examples=200)
@given(scenario=_scenario())
def test_enrich_resolves_assigned_banker_by_precedence(scenario):
    """enrich sets assignedBankerId by precedence personalBanker > allocation > None.

    **Validates: Requirements 5.1, 5.2, 5.3, 8.4**
    """
    item, fake_customers, fake_assignments = scenario

    # Independently compute the two precedence inputs.
    personal_banker_id = _expected_personal_banker_id(item, fake_customers)
    allocation_id = _expected_allocation_id(item, fake_assignments)

    # Expected precedence: personalBanker.bankerId > Allocation_Record > None.
    if personal_banker_id:
        expected = personal_banker_id
    elif allocation_id:
        expected = allocation_id
    else:
        expected = None

    customer_id = item.get("customerId")
    source = item.get("source")

    result = handler.enrich(
        item,
        customers_tbl=fake_customers,
        assignments_tbl=fake_assignments,
    )

    assert result["assignedBankerId"] == expected

    # A present personalBanker.bankerId is NEVER overridden by an allocation
    # lookup, even when an allocation record exists (R5.1).
    if personal_banker_id:
        assert result["assignedBankerId"] == personal_banker_id

    # A missing/falsy allocation record on the unassigned path stays None (R8.4).
    if not personal_banker_id and not allocation_id:
        assert result["assignedBankerId"] is None

    # isWalkIn / hasLinkedApplication remain unchanged by the allocation logic.
    assert result["isWalkIn"] == (not customer_id)
    assert result["hasLinkedApplication"] == (source == "application")
