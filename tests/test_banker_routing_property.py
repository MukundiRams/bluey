"""Property-based test for banker-query-triage routing (Property 4).

Exercises the pure routing predicate ``visible_to(item, banker_id, tier)`` and
the pure reporting helper ``routing_designation(item)`` in
``bluey-banker-api/handler.py``. Both read only the item's Assigned_Banker
``A = item.get("assignedBankerId")`` (set upstream by ``enrich``) and perform
no I/O, so they can be property-tested directly.

Property 4 — Routing partitions items by assignment and tier:

* For ANY item and ANY requesting banker ``(banker_id, tier)``:
    - if the item has an assigned banker ``A`` (truthy ``assignedBankerId``),
      ``visible_to`` is True EXACTLY when ``banker_id == A``;
    - if the item has NO assigned banker (``assignedBankerId`` None/absent =
      General_Pool), ``visible_to`` is True EXACTLY when ``tier == "general"``.
    - ``routing_designation(item)`` is ``"assigned"`` when the item has a
      truthy ``assignedBankerId`` else ``"general_pool"``.
* Set-level equalities over a generated queue of items and a banker:
    - a general-tier banker's visible set == the general-pool items UNION the
      items assigned to that banker;
    - a non-general-tier banker's visible set == EXACTLY the items assigned to
      that banker (no general-pool items).

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_classify_properties.py`` /
``test_banker_gather.py``. The module builds boto3 DynamoDB resources at import
time; ``resource()`` makes no network call, but a region must be resolvable, so
AWS region env vars are set before loading. ``visible_to`` /
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

# A small, fixed pool of banker ids. ``None`` denotes the General_Pool (an item
# with no assigned banker). Drawing assigned-banker and requesting-banker ids
# from the SAME small pool guarantees frequent matches AND mismatches, so both
# branches of the predicate are exercised densely.
_BANKER_IDS = ["banker-001", "banker-002", "banker-003"]

# assignedBankerId: one of the pool ids, or None (general pool).
_assigned_banker_strategy = st.sampled_from([*_BANKER_IDS, None])

# The requesting banker's own id is always a concrete pool id.
_banker_id_strategy = st.sampled_from(_BANKER_IDS)

# Tier: the special "general" value plus other tiers so the general vs
# non-general partition is thoroughly exercised.
_tier_strategy = st.sampled_from(["general", "premium", "priority", "other"])


@st.composite
def items_with_unique_ids(draw):
    """Generate a list of items, each with a unique ``itemId`` and an
    ``assignedBankerId`` drawn from the pool (or ``None`` for general pool).

    Unique ``itemId``s let the test reason about visible/assigned sets as sets
    of ids without collisions.
    """
    n = draw(st.integers(min_value=0, max_value=8))
    items = []
    for i in range(n):
        items.append(
            {
                "itemId": f"item-{i}",
                "assignedBankerId": draw(_assigned_banker_strategy),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Property 4: Routing partitions items by assignment and tier
# ---------------------------------------------------------------------------

# Feature: banker-query-triage, Property 4: Routing partitions items by assignment and tier
@settings(max_examples=200)
@given(
    assigned=_assigned_banker_strategy,
    banker_id=_banker_id_strategy,
    tier=_tier_strategy,
)
def test_visible_to_and_routing_per_item(assigned, banker_id, tier):
    """Per-item routing predicate and designation match the partition rule.

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**
    """
    item = {"itemId": "item-x", "assignedBankerId": assigned}

    visible = handler.visible_to(item, banker_id, tier)

    if assigned:
        # Assigned item: visible EXACTLY to its assigned banker (R5.1).
        assert visible == (banker_id == assigned)
    else:
        # General-pool item: visible EXACTLY to general-tier bankers
        # (R5.2, R5.3); non-general tiers never see it (R5.4).
        assert visible == (tier == "general")

    # Reporting designation reflects presence of an assigned banker (R5.5).
    expected_routing = "assigned" if assigned else "general_pool"
    assert handler.routing_designation(item) == expected_routing


# Feature: banker-query-triage, Property 4: Routing partitions items by assignment and tier
@settings(max_examples=200)
@given(
    items=items_with_unique_ids(),
    banker_id=_banker_id_strategy,
    tier=_tier_strategy,
)
def test_visible_set_equals_partition(items, banker_id, tier):
    """The visible set over a queue equals the assignment/tier partition.

    - general-tier banker: visible set == general-pool items UNION items
      assigned to that banker;
    - non-general-tier banker: visible set == EXACTLY items assigned to that
      banker.

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**
    """
    visible_ids = {
        it["itemId"] for it in items if handler.visible_to(it, banker_id, tier)
    }

    assigned_to_me = {
        it["itemId"] for it in items if it.get("assignedBankerId") == banker_id
    }
    general_pool = {
        it["itemId"] for it in items if not it.get("assignedBankerId")
    }

    if tier == "general":
        assert visible_ids == assigned_to_me | general_pool
    else:
        assert visible_ids == assigned_to_me
        # A non-general banker never sees any general-pool item.
        assert visible_ids & general_pool == set()
