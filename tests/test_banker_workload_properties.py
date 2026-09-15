"""Property-based tests for the banker-workload-allocation ``workload_count`` helper.

These Hypothesis property tests exercise the pure ``workload_count(banker_id, items)``
helper in ``bluey-banker-api/handler.py``. ``workload_count`` counts how many
pending queue items are currently assigned to a banker via the truthy
``assignedBankerId`` field set by ``enrich``. It is pure, takes already-enriched
items, does no I/O, and is directly property-testable under Hypothesis.

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_classify_properties.py``. The module builds
boto3 DynamoDB resources at import time; ``resource()`` makes no network call,
but a region must be resolvable, so AWS region env vars are set before loading.
``workload_count`` is pure, so no real AWS is touched.
"""
import copy
import importlib.util
import os
import random
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
# Strategies
# ---------------------------------------------------------------------------

# A small pool of banker ids so assigned items collide across bankers and the
# generated banker_id frequently matches (or does not match) real assignments.
_BANKER_POOL = ["banker-001", "banker-002", "banker-003"]

# Sources include the three valid ones plus junk, so the count is proven
# independent of an item's source.
_source_strategy = st.one_of(
    st.sampled_from(["session", "application", "credit"]),
    st.text(max_size=8),
)

# assignedBankerId: a real banker id, None, empty string, or the field omitted
# entirely (handled in the composite). Empty string / None are falsy and must
# never be counted.
_assigned_banker_strategy = st.one_of(
    st.sampled_from(_BANKER_POOL),
    st.none(),
    st.just(""),
)

# The banker_id passed to workload_count: usually one from the pool, but also a
# falsy value ("" / None) to confirm falsy assignments are never matched.
_banker_id_strategy = st.one_of(
    st.sampled_from(_BANKER_POOL),
    st.just(""),
    st.none(),
    st.text(max_size=8),
)


@st.composite
def enriched_items(draw):
    """Generate a list of enriched queue items with mixed assignment shapes.

    Each item has a unique ``itemId`` and a ``source`` drawn from the valid set
    plus junk. ``assignedBankerId`` is a real banker id, ``None``, an empty
    string, or omitted entirely — so missing/falsy assignments (R8.1) are
    exercised.
    """
    count = draw(st.integers(min_value=0, max_value=25))
    items = []
    for i in range(count):
        item = {
            "itemId": f"item-{i}",
            "source": draw(_source_strategy),
            "junk": draw(st.integers()),
        }
        # Decide whether to include the assignedBankerId key at all.
        if draw(st.booleans()):
            item["assignedBankerId"] = draw(_assigned_banker_strategy)
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Property 1: Workload count is a correct assigned-pending count
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 1: Workload count is a correct assigned-pending count
@settings(max_examples=200)
@given(banker_id=_banker_id_strategy, items=enriched_items())
def test_workload_count_is_a_correct_assigned_pending_count(banker_id, items):
    """workload_count returns the exact truthy assigned count, order-independent.

    **Validates: Requirements 1.1, 1.4, 1.5, 1.6, 7.3, 8.1**
    """
    # Deep copy the input so we can prove workload_count does not mutate it.
    before = copy.deepcopy(items)

    result = handler.workload_count(banker_id, items)

    # Independently compute the expected count: an item counts only when its
    # assignedBankerId is truthy AND equals banker_id (R1.1, R8.1).
    expected = sum(
        1
        for item in items
        if item.get("assignedBankerId") and item.get("assignedBankerId") == banker_id
    )
    assert result == expected

    # The result is an integer in [0, len(items)] (R1.5).
    assert isinstance(result, int)
    assert 0 <= result <= len(items)

    # Items lacking a truthy assignedBankerId are never counted. When banker_id
    # itself is falsy ("" or None), nothing can match it (R8.1).
    if not banker_id:
        assert result == 0

    # Invariant under list permutation: shuffling yields the same count (R1.4).
    shuffled = list(items)
    random.Random(len(items)).shuffle(shuffled)
    assert handler.workload_count(banker_id, shuffled) == result

    # The call did not mutate items (R1.6).
    assert items == before


# ---------------------------------------------------------------------------
# Strategy: collections of banker ids drawn from a small pool, possibly with
# duplicates and sometimes empty, so duplicate ids collapse to a single key and
# the empty-collection case (→ empty map) is exercised.
# ---------------------------------------------------------------------------
_banker_ids_strategy = st.lists(
    st.sampled_from(_BANKER_POOL), min_size=0, max_size=6
)


# ---------------------------------------------------------------------------
# Property 2: Workload map is consistent with the counter and keyed by the supplied ids
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 2: Workload map is consistent with the counter and keyed by the supplied ids
@settings(max_examples=200)
@given(banker_ids=_banker_ids_strategy, items=enriched_items())
def test_workload_map_is_consistent_with_the_counter_and_keyed_by_the_supplied_ids(
    banker_ids, items
):
    """workload_by_banker keys by the supplied ids and agrees with workload_count.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    """
    # Deep copy both inputs so we can prove workload_by_banker does not mutate them.
    banker_ids_before = copy.deepcopy(banker_ids)
    items_before = copy.deepcopy(items)

    result = handler.workload_by_banker(banker_ids, items)

    # The key set equals the SET of supplied ids: duplicates collapse so there is
    # exactly one entry per distinct banker (R2.1).
    assert set(result.keys()) == set(banker_ids)

    # For every supplied id b the mapped value equals workload_count(b, items) —
    # in particular 0 for a banker with no assigned pending items (R2.2, R2.3).
    for b in banker_ids:
        assert result[b] == handler.workload_count(b, items)

    # Neither argument is mutated (R2.4).
    assert banker_ids == banker_ids_before
    assert items == items_before

    # Deterministic: equal inputs yield equal results (R2.4).
    assert handler.workload_by_banker(banker_ids, items) == result
