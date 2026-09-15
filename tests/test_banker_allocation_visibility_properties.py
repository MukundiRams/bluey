"""Property-based tests for the banker-workload-allocation Property 7.

Property 7 asserts that once an item is allocated to a selected general banker
(its ``assignedBankerId`` set to that banker), the UNCHANGED ``visible_to``
predicate routes it as an ``Assigned_Item``: visible to the selected banker at
ANY tier, invisible to every OTHER general banker at ``tier == "general"``, and
``routing_designation`` reports ``"assigned"``. Moreover ``visible_to``'s result
depends ONLY on ``assignedBankerId`` — an item assigned via allocation and one
assigned via ``personalBanker`` with the SAME ``assignedBankerId`` are
indistinguishable to ``visible_to`` (existing assigned-customer routing is
preserved).

This test exercises ``visible_to`` and ``routing_designation`` AS-IS; it does
NOT modify them. The handler module lives in a directory whose name contains
hyphens (``bluey-banker-api``), so it cannot be imported with a normal
``import``. It is loaded from its file path via
``importlib.util.spec_from_file_location`` — the same pattern used across the
banker property-test suite. The module builds boto3 DynamoDB resources at import
time; ``resource()`` makes no network call, but a region must be resolvable, so
AWS region env vars are set before loading. ``visible_to`` and
``routing_designation`` are pure, so no real AWS is touched.
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
# Strategies
# ---------------------------------------------------------------------------

# A small pool of general banker ids so the selected banker and the "other"
# general bankers are drawn from a constrained, overlapping space.
_BANKER_POOL = ["banker-001", "banker-002", "banker-003", "banker-004", "banker-005"]

# The tiers ``visible_to`` accepts for the requesting banker.
_TIERS = ["general", "premium"]


@st.composite
def selection(draw):
    """Draw a selected banker, a set of OTHER general banker ids, and a tier.

    Returns ``(selected_id, other_ids, selected_tier)`` where ``other_ids`` is a
    set of general banker ids all DISTINCT from ``selected_id``, and
    ``selected_tier`` is any of ``"general"``/``"premium"`` (the selected banker
    must be visible at ANY tier, R6.3).
    """
    selected_id = draw(st.sampled_from(_BANKER_POOL))
    others = draw(
        st.lists(
            st.sampled_from(_BANKER_POOL),
            min_size=0,
            max_size=len(_BANKER_POOL),
            unique=True,
        )
    )
    other_ids = [b for b in others if b != selected_id]
    selected_tier = draw(st.sampled_from(_TIERS))
    return selected_id, other_ids, selected_tier


# ---------------------------------------------------------------------------
# Property 7: An allocated item is visible only to its owner (unchanged predicate)
# ---------------------------------------------------------------------------

# Feature: banker-workload-allocation, Property 7: An allocated item is visible only to its owner (unchanged predicate)
@settings(max_examples=200)
@given(sel=selection(), source=st.sampled_from(["session", "application", "credit"]))
def test_allocated_item_is_visible_only_to_its_owner(sel, source):
    """An allocated item routes as an Assigned_Item under the unchanged predicate.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    """
    selected_id, other_ids, selected_tier = sel

    # An allocated item: assignedBankerId set to the selected general banker
    # (simulating what the list wiring does after allocate + put_allocation).
    item = {
        "itemId": f"{source}#DEMO-001",
        "source": source,
        "assignedBankerId": selected_id,
    }

    # R6.3: visible to the selected banker at ANY tier.
    assert handler.visible_to(item, selected_id, selected_tier) is True

    # R6.2: invisible to every OTHER general banker (distinct from selected).
    for other_id in other_ids:
        assert handler.visible_to(item, other_id, "general") is False

    # R6.1: an allocated (owned) item reports the "assigned" routing designation.
    assert handler.routing_designation(item) == "assigned"

    # R6.4 (indistinguishability): visible_to depends ONLY on assignedBankerId.
    # Build a SECOND item with the SAME assignedBankerId but assigned via a
    # personalBanker-style customer with different other fields; visible_to must
    # agree for the same (banker_id, tier) inputs.
    personal_banker_item = {
        "itemId": f"application#OTHER-777",
        "source": "application",
        "assignedBankerId": selected_id,
        # Different, extra fields that visible_to must ignore.
        "customerId": "cust-abc",
        "isWalkIn": True,
        "hasLinkedApplication": False,
        "reference": "REF-XYZ",
    }

    # The two items agree for the selected banker at any tier and for every
    # other general banker — i.e. only assignedBankerId matters.
    assert handler.visible_to(personal_banker_item, selected_id, selected_tier) is (
        handler.visible_to(item, selected_id, selected_tier)
    )
    for other_id in other_ids:
        assert handler.visible_to(personal_banker_item, other_id, "general") is (
            handler.visible_to(item, other_id, "general")
        )
    # And also across both tiers for the selected banker explicitly.
    for tier in _TIERS:
        assert handler.visible_to(personal_banker_item, selected_id, tier) is (
            handler.visible_to(item, selected_id, tier)
        )
