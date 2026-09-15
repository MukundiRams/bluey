"""Property-based tests for the banker-workload-allocation ``allocate`` helper.

These Hypothesis property tests exercise the pure ``allocate(item, bankers,
workload_map)`` selector in ``bluey-banker-api/handler.py``. ``allocate`` picks
the least-loaded ``General_Tier`` banker for a ``General_Pool`` item (ties
broken by the smallest ``bankerId`` lexicographically, ``Premium_Tier`` bankers
excluded), treating a missing ``workload_map`` entry as ``0`` and returning
``None`` when there is no general banker. It is a no-op for an already-owned
item (a truthy ``assignedBankerId`` is returned unchanged). It is pure, does no
I/O, and is directly property-testable under Hypothesis.

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_workload_properties.py``. The module builds
boto3 DynamoDB resources at import time; ``resource()`` makes no network call,
but a region must be resolvable, so AWS region env vars are set before loading.
``allocate`` is pure, so no real AWS is touched.
"""
import copy
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
# Strategies
# ---------------------------------------------------------------------------

# A small pool of banker ids so ties (equal workloads across bankers) are forced
# frequently, exercising the lexicographic tie-break (R3.2).
_BANKER_POOL = ["banker-001", "banker-002", "banker-003", "banker-004"]


@st.composite
def banker_lists(draw):
    """Generate a list of bankers with mixed general/premium tiers.

    ``bankerId`` is drawn from a small pool to force ties; ``tier`` is
    ``general`` or ``premium``. Premium-only sets and the empty set are included
    so the ``None`` path (R3.4, R8.2) and the never-select-premium invariant
    (R3.3, R6.5) are exercised. Duplicate bankerIds may occur.
    """
    count = draw(st.integers(min_value=0, max_value=6))
    bankers = []
    for _ in range(count):
        bankers.append(
            {
                "bankerId": draw(st.sampled_from(_BANKER_POOL)),
                "tier": draw(st.sampled_from(["general", "premium"])),
            }
        )
    return bankers


@st.composite
def workload_maps(draw):
    """Generate a workload map over a SUBSET of the banker id pool.

    Some candidate ids may be absent (→ treated as ``0``, R8.3); loads are small
    integers including differences greater than 1 (R7.2) so a strictly larger
    workload is never chosen over a lower one.
    """
    ids = draw(st.lists(st.sampled_from(_BANKER_POOL), min_size=0, max_size=4, unique=True))
    return {bid: draw(st.integers(min_value=0, max_value=8)) for bid in ids}


@st.composite
def general_pool_items(draw):
    """Generate a General_Pool item: no truthy ``assignedBankerId``.

    ``assignedBankerId`` is omitted, ``None``, or an empty string — all falsy —
    so the item is eligible for allocation (R8.1).
    """
    item = {
        "itemId": f"item-{draw(st.integers(min_value=0, max_value=50))}",
        "source": draw(st.sampled_from(["session", "application", "credit"])),
    }
    choice = draw(st.integers(min_value=0, max_value=2))
    if choice == 1:
        item["assignedBankerId"] = None
    elif choice == 2:
        item["assignedBankerId"] = ""
    return item


# ---------------------------------------------------------------------------
# Property 3: Allocation selects the least-loaded general banker deterministically
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 3: Allocation selects the least-loaded general banker deterministically
@settings(max_examples=200)
@given(item=general_pool_items(), bankers=banker_lists(), workload_map=workload_maps())
def test_allocation_selects_the_least_loaded_general_banker_deterministically(
    item, bankers, workload_map
):
    """allocate returns min(general, key=(load, id)) bankerId, or None; never premium.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 6.5, 7.1, 7.2, 8.2, 8.3**
    """
    # Deep copy inputs so we can prove allocate does not mutate them.
    item_before = copy.deepcopy(item)
    bankers_before = copy.deepcopy(bankers)
    workload_map_before = copy.deepcopy(workload_map)

    result = handler.allocate(item, bankers, workload_map)

    # Independently compute the expected selection over general bankers only,
    # treating a missing workload_map entry as 0 (R8.3) and breaking ties by the
    # smallest bankerId lexicographically (R3.2).
    general = [b for b in bankers if b.get("tier") == "general"]
    if not general:
        # No general banker → None (R3.4, R8.2).
        assert result is None
    else:
        expected = min(
            general,
            key=lambda b: (workload_map.get(b["bankerId"], 0), b["bankerId"]),
        )["bankerId"]
        assert result == expected

        # A premium banker is never selected (R3.3, R6.5): the result must be
        # the id of some general banker.
        general_ids = {b["bankerId"] for b in general}
        assert result in general_ids

        # The selected banker's workload is <= every other general banker's
        # workload (R3.1, R7.1) — never chooses a strictly larger-loaded one.
        selected_load = workload_map.get(result, 0)
        for b in general:
            assert selected_load <= workload_map.get(b["bankerId"], 0)

    # allocate did not mutate any argument (R3.6).
    assert item == item_before
    assert bankers == bankers_before
    assert workload_map == workload_map_before

    # Deterministic: equal inputs yield equal results (R3.6).
    assert handler.allocate(item, bankers, workload_map) == result


# ---------------------------------------------------------------------------
# Strategies for Property 4: already-owned items and arbitrary bankers/maps.
# ---------------------------------------------------------------------------


@st.composite
def owned_items(draw):
    """Generate an item that already carries a truthy ``assignedBankerId``."""
    return {
        "itemId": f"item-{draw(st.integers(min_value=0, max_value=50))}",
        "source": draw(st.sampled_from(["session", "application", "credit"])),
        "assignedBankerId": draw(
            st.sampled_from(_BANKER_POOL + ["banker-999", "premium-banker"])
        ),
    }


# ---------------------------------------------------------------------------
# Property 4: Allocation is a no-op for an already-owned item
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 4: Allocation is a no-op for an already-owned item
@settings(max_examples=200)
@given(item=owned_items(), bankers=banker_lists(), workload_map=workload_maps())
def test_allocation_is_a_no_op_for_an_already_owned_item(item, bankers, workload_map):
    """allocate returns the existing assignedBankerId unchanged, no mutation.

    **Validates: Requirements 3.5, 3.6**
    """
    existing = item["assignedBankerId"]

    # Deep copy inputs so we can prove allocate does not mutate them.
    item_before = copy.deepcopy(item)
    bankers_before = copy.deepcopy(bankers)
    workload_map_before = copy.deepcopy(workload_map)

    result = handler.allocate(item, bankers, workload_map)

    # The existing owner is returned unchanged — neither reallocated nor derived
    # from the bankers / workload_map (R3.5).
    assert result == existing

    # No argument is mutated (R3.6).
    assert item == item_before
    assert bankers == bankers_before
    assert workload_map == workload_map_before
