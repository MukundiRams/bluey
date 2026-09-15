"""Property-based tests for banker-query-triage read-state store.

Covers three design properties over the read/unread store in
``bluey-banker-api/handler.py``:

* Property 5 — Read state resolution and unread count are consistent.
* Property 6 — Marking read is idempotent and persistent.
* Property 7 — Marking an unrouted item is rejected without side effects.

The pure resolution helpers (``resolve_read_state``, ``unread_count``) are
exercised directly, and the store-backed paths (``read_state_for``,
``mark_read``) run against a small in-memory ``FakeReadStateTable`` that mimics
the subset of the DynamoDB resource ``Table`` API the handler uses
(``.query`` by ``bankerId`` partition key and ``.put_item``). This keeps the
properties fast at 100+ iterations without moto/AWS.

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_routing_property.py``. AWS region env vars
are set before loading so ``boto3.resource()`` at import time succeeds without
config files; no real AWS is touched by these tests.
"""
import importlib.util
import os
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st


def _load_handler():
    """Load the hyphenated banker-api handler module from its file path."""
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
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handler = _load_handler()


# ---------------------------------------------------------------------------
# In-memory fake for the bluey-banker-read-state DynamoDB table
# ---------------------------------------------------------------------------
class FakeReadStateTable:
    """In-memory stand-in for the read-state DynamoDB resource ``Table``.

    Stores rows keyed by ``(bankerId, itemId)`` so ``put_item`` is idempotent
    by primary key (a repeated put overwrites the same row). Supports the two
    operations the handler performs:

    * ``query(KeyConditionExpression="bankerId = :bid",
      ExpressionAttributeValues={":bid": banker_id})`` → ``{"Items": [...]}``
      returning only rows whose ``bankerId`` matches the queried value. No
      pagination is emitted (no ``LastEvaluatedKey``), which the handler
      tolerates.
    * ``put_item(Item={...})`` → stores/overwrites by ``(bankerId, itemId)``.
    """

    def __init__(self):
        # (bankerId, itemId) -> stored Item dict
        self._rows = {}

    def put_item(self, Item):  # noqa: N803 (match boto3 kwarg name)
        key = (Item.get("bankerId"), Item.get("itemId"))
        self._rows[key] = dict(Item)
        return {}

    def query(self, KeyConditionExpression=None, ExpressionAttributeValues=None, **kwargs):  # noqa: N803
        banker_id = (ExpressionAttributeValues or {}).get(":bid")
        items = [
            dict(row)
            for (row_banker, _row_item), row in self._rows.items()
            if row_banker == banker_id
        ]
        return {"Items": items}

    # Test helpers -----------------------------------------------------------
    def row_count(self):
        return len(self._rows)

    def has_row(self, banker_id, item_id):
        return (banker_id, item_id) in self._rows


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
_banker_id_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12
)

# Unique itemId lists: draw a set of ids then order them into a list.
_item_id_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789#-", min_size=1, max_size=16
)


@st.composite
def unique_item_ids(draw, min_size=0, max_size=12):
    """Draw a list of unique item ids."""
    ids = draw(
        st.lists(_item_id_strategy, min_size=min_size, max_size=max_size, unique=True)
    )
    return ids


@st.composite
def items_and_read_subset(draw):
    """Draw a list of items (unique itemIds) and a read subset of their ids."""
    ids = draw(unique_item_ids(min_size=0, max_size=12))
    items = [{"itemId": i} for i in ids]
    # read_ids is any subset of the item ids.
    read_ids = set(
        draw(st.lists(st.sampled_from(ids), unique=True)) if ids else []
    )
    return items, read_ids


# ---------------------------------------------------------------------------
# Property 5: Read state resolution and unread count are consistent
# ---------------------------------------------------------------------------

# Feature: banker-query-triage, Property 5: Read state resolution and unread count are consistent
@settings(max_examples=200)
@given(data=items_and_read_subset())
def test_resolve_read_state_and_unread_count_consistent(data):
    """Pure resolution: each item's readState matches membership, count matches.

    **Validates: Requirements 3.3, 4.1, 4.2**
    """
    items, read_ids = data

    resolved = handler.resolve_read_state(items, read_ids)

    # Each item is "read" iff its itemId is in read_ids, else "unread".
    for item in resolved:
        expected = "read" if item["itemId"] in read_ids else "unread"
        assert item["readState"] == expected

    # unread_count equals the number of items NOT in read_ids.
    expected_unread = sum(1 for it in items if it["itemId"] not in read_ids)
    assert handler.unread_count(resolved) == expected_unread


# Feature: banker-query-triage, Property 5: Read state resolution and unread count are consistent
@settings(max_examples=200)
@given(
    banker_id=_banker_id_strategy,
    data=items_and_read_subset(),
)
def test_read_state_store_path_consistent(banker_id, data):
    """End-to-end store path: read_state_for → resolve_read_state → unread_count.

    Inserts a subset of read rows for the banker, then asserts read_state_for
    returns exactly the inserted read ids intersected with the queried item
    ids, and that the resolved readState / unread count are consistent with the
    rows inserted.

    **Validates: Requirements 3.3, 4.1, 4.2**
    """
    items, read_ids = data
    item_ids = [it["itemId"] for it in items]

    fake = FakeReadStateTable()
    for rid in read_ids:
        fake.put_item(Item={"bankerId": banker_id, "itemId": rid, "readAt": "2026-01-01T00:00:00+00:00"})
    # Insert a read row for ANOTHER banker to confirm partitioning by bankerId.
    fake.put_item(
        Item={"bankerId": banker_id + "-other", "itemId": "not-mine", "readAt": "2026-01-01T00:00:00+00:00"}
    )

    resolved_read = handler.read_state_for(banker_id, item_ids, read_state_tbl=fake)

    # read_state_for returns exactly inserted read ids intersected with item_ids.
    assert resolved_read == (read_ids & set(item_ids))

    handler.resolve_read_state(items, resolved_read)
    for item in items:
        expected = "read" if item["itemId"] in read_ids else "unread"
        assert item["readState"] == expected

    expected_unread = sum(1 for i in item_ids if i not in read_ids)
    assert handler.unread_count(items) == expected_unread


# ---------------------------------------------------------------------------
# Property 6: Marking read is idempotent and persistent
# ---------------------------------------------------------------------------

# Feature: banker-query-triage, Property 6: Marking read is idempotent and persistent
@settings(max_examples=200)
@given(
    banker_id=_banker_id_strategy,
    tier=st.sampled_from(["general", "premium", "priority"]),
    item_id=_item_id_strategy,
)
def test_mark_read_idempotent_and_persistent(banker_id, tier, item_id):
    """Marking a routed item once then twice both report read; store has one row.

    **Validates: Requirements 4.4, 4.5, 4.6**
    """
    fake = FakeReadStateTable()
    routed = {item_id}  # item is routed to this banker → authorized

    # First mark: succeeds and reports read.
    first = handler.mark_read(
        banker_id, tier, item_id, routed_item_ids=routed, read_state_tbl=fake
    )
    assert first == {"readState": "read"}
    assert handler.read_state_for(banker_id, [item_id], read_state_tbl=fake) == {item_id}
    assert fake.row_count() == 1

    # Second mark: still reports read, still exactly one row (idempotent).
    second = handler.mark_read(
        banker_id, tier, item_id, routed_item_ids=routed, read_state_tbl=fake
    )
    assert second == {"readState": "read"}
    assert handler.read_state_for(banker_id, [item_id], read_state_tbl=fake) == {item_id}
    assert fake.row_count() == 1


# ---------------------------------------------------------------------------
# Property 7: Marking an unrouted item is rejected without side effects
# ---------------------------------------------------------------------------

@st.composite
def banker_item_and_unrouted_set(draw):
    """Draw a banker, an item, and a routed set NOT containing that item."""
    item_id = draw(_item_id_strategy)
    # A set of OTHER ids that excludes item_id (may be empty).
    others = draw(
        st.lists(
            _item_id_strategy.filter(lambda x: x != item_id),
            max_size=5,
            unique=True,
        )
    )
    return item_id, set(others)


# Feature: banker-query-triage, Property 7: Marking an unrouted item is rejected without side effects
@settings(max_examples=200)
@given(
    banker_id=_banker_id_strategy,
    tier=st.sampled_from(["general", "premium", "priority"]),
    data=banker_item_and_unrouted_set(),
)
def test_mark_unrouted_rejected_no_side_effects(banker_id, tier, data):
    """Marking an item not routed to the banker returns NOT_AUTHORIZED, no write.

    **Validates: Requirements 4.7**
    """
    item_id, routed = data
    assert item_id not in routed  # precondition: item is unrouted

    fake = FakeReadStateTable()

    result = handler.mark_read(
        banker_id, tier, item_id, routed_item_ids=routed, read_state_tbl=fake
    )

    # Rejected with the authorization sentinel.
    assert result == handler.NOT_AUTHORIZED

    # Store is unchanged: no row written, item still resolves as unread.
    assert fake.row_count() == 0
    assert not fake.has_row(banker_id, item_id)
    assert handler.read_state_for(banker_id, [item_id], read_state_tbl=fake) == set()
