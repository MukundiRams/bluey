"""Property-based tests for allocation persistence (``get_allocation`` /
``put_allocation``) in ``bluey-banker-api/handler.py``.

These Hypothesis property tests exercise the injectable persistence helpers
against an in-memory fake ``Assignments_Table`` (dict-backed) so no live AWS is
required. ``put_allocation`` writes an ``Allocation_Record``
``{"itemId": ..., "assignedBankerId": ...}`` with a conditional
``attribute_not_exists(itemId)`` put (first-writer-wins, idempotent,
non-reallocating); ``get_allocation`` reads back the stored ``assignedBankerId``
or ``None`` when no record exists.

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same ``_load_handler()`` pattern used across the banker property-test suite. The
module builds boto3 DynamoDB resources at import time; ``resource()`` makes no
network call, but a region must be resolvable, so AWS region env vars are set
before loading. The helpers under test take an injected fake table, so no real
AWS is touched.
"""
import importlib.util
import os
import sys
from pathlib import Path

from botocore.exceptions import ClientError
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
# In-memory fake Assignments_Table
# ---------------------------------------------------------------------------


class FakeAssignmentsTable:
    """Dict-backed stand-in for the ``bluey-banker-assignments`` DynamoDB table.

    Implements just enough of the boto3 ``Table`` surface used by the helpers:

      - ``get_item(Key={"itemId": ...})`` returns ``{"Item": {...}}`` when a
        record exists for that ``itemId``, else ``{}`` (no ``Item`` key), so
        ``get_allocation`` returns ``None`` for an absent record.
      - ``put_item(Item=..., ConditionExpression=...)`` honors the
        ``attribute_not_exists(itemId)`` guard: when a record already exists for
        that ``itemId`` AND the guard is present, it raises a botocore
        ``ClientError`` with error Code ``"ConditionalCheckFailedException"``
        (mirroring DynamoDB's conditional-write failure), so writes never
        overwrite an existing record.
    """

    def __init__(self):
        self._store = {}

    def get_item(self, Key):
        item_id = Key["itemId"]
        if item_id in self._store:
            # Return a copy so callers cannot mutate internal state.
            return {"Item": dict(self._store[item_id])}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        item_id = Item["itemId"]
        if (
            ConditionExpression == "attribute_not_exists(itemId)"
            and item_id in self._store
        ):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "conditional request failed",
                    }
                },
                "PutItem",
            )
        self._store[item_id] = dict(Item)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty (truthy) itemIds and bankerIds so the round-trip and no-realloc
# assertions are meaningful (falsy stored ids are treated as "no allocation").
_item_ids = st.text(min_size=1, max_size=20)
_banker_ids = st.text(min_size=1, max_size=20)


# ---------------------------------------------------------------------------
# Property 5: Allocation persistence round-trips and is idempotent / non-reallocating
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 5: Allocation persistence round-trips and is idempotent / non-reallocating
@settings(max_examples=200)
@given(
    item_id=_item_ids,
    banker_id=_banker_ids,
    other_banker=_banker_ids,
    repeats=st.integers(min_value=1, max_value=5),
    absent_item_id=_item_ids,
)
def test_allocation_persistence_round_trips_and_is_idempotent_non_reallocating(
    item_id, banker_id, other_banker, repeats, absent_item_id
):
    """put_allocation then get_allocation round-trips; repeats/other bankers never reallocate.

    **Validates: Requirements 4.1, 4.3, 4.5, 8.5**
    """
    table = FakeAssignmentsTable()

    # get_allocation returns None (no raise) for an absent itemId (R8.5).
    # Use an id we have not written yet.
    if absent_item_id != item_id:
        assert handler.get_allocation(absent_item_id, assignments_tbl=table) is None

    # Round-trip: write then read returns the same bankerId (R4.1).
    handler.put_allocation(item_id, banker_id, assignments_tbl=table)
    assert handler.get_allocation(item_id, assignments_tbl=table) == banker_id

    # Snapshot the stored record after a single write.
    single_write_record = table.get_item(Key={"itemId": item_id})["Item"]

    # Idempotence: repeated identical writes leave the stored record equal to a
    # single write (R4.5).
    for _ in range(repeats):
        handler.put_allocation(item_id, banker_id, assignments_tbl=table)
    assert table.get_item(Key={"itemId": item_id})["Item"] == single_write_record

    # No reallocation: a second put with a DIFFERENT banker leaves the stored
    # assignedBankerId unchanged (R4.3, R4.5) — first-writer-wins.
    handler.put_allocation(item_id, other_banker, assignments_tbl=table)
    assert handler.get_allocation(item_id, assignments_tbl=table) == banker_id
    assert table.get_item(Key={"itemId": item_id})["Item"] == single_write_record
