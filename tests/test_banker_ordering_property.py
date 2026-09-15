"""Property-based test for the banker-query-triage oldest-first ordering.

This Hypothesis property test exercises the pure ordering logic in
``bluey-banker-api/handler.py`` — ``sort_key`` / ``sort_queue`` — which
implements the queue's oldest-first ordering (R2). Rather than re-implementing
``sort_key`` and comparing, the test asserts the STRUCTURAL invariants that a
correct oldest-first sort must satisfy on the sorted output.

Candidate queue items carry:
  - ``createdAt`` (ISO-8601 ``str`` or absent)
  - ``updatedAt`` (ISO-8601 ``str`` or absent)
  - ``reference`` (``str`` or ``None``)
  - ``itemId`` (``str``, unique per item so permutation checks are unambiguous)

``Waiting_Time`` derives from ``createdAt`` falling back to ``updatedAt``;
missing/unparseable timestamps make the item UNDATED.

The handler module lives in a hyphenated directory (``bluey-banker-api``), so it
cannot be imported normally. It is loaded from its file path via
``importlib.util.spec_from_file_location`` — the same loader pattern used in
``test_banker_classify_properties.py``. The module builds boto3 DynamoDB
resources at import time; ``resource()`` makes no network call, but a region
must be resolvable, so AWS region env vars are set before loading. The ordering
functions are pure, so no real AWS is touched.
"""
import importlib.util
import os
import sys
from datetime import datetime, timezone
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
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


handler = _load_handler()


# ---------------------------------------------------------------------------
# Independent spec of the ordering rules (kept local so the test asserts
# against its own understanding of the invariants, not the implementation).
# ---------------------------------------------------------------------------

# A SMALL pool of ISO-8601 timestamps so ties occur frequently. Mixed tz/offset
# forms are all valid ISO-8601 and parse to distinct-or-equal instants.
_TIMESTAMP_POOL = [
    "2026-08-26T13:00:00+00:00",
    "2026-08-27T09:30:00+00:00",
    "2026-08-27T09:30:00+00:00",  # duplicate instant to force frequent ties
    "2026-08-28T00:00:00+00:00",
    "2026-01-01T00:00:00+00:00",
]

# Intentionally unparseable / undated markers.
_BAD_TIMESTAMPS = ["not-a-date", "", "   ", "2026-13-40T99:99:99"]


def _parse(value):
    """Local, independent ISO-8601 parse mirroring the handler's tolerance.

    Returns a tz-aware ``datetime`` or ``None``. Used to compute the EXPECTED
    Waiting_Time and dated/undated partition for the invariant checks.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _waiting_time(item):
    """Expected Waiting_Time: createdAt else updatedAt; None when undated."""
    ts = _parse(item.get("createdAt"))
    if ts is None:
        ts = _parse(item.get("updatedAt"))
    return ts


def _tie_break(item):
    """Expected tie-break key: reference if present, else itemId."""
    reference = item.get("reference")
    key = reference if reference else item.get("itemId")
    return str(key) if key is not None else ""


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A timestamp field is either drawn from the small dated pool, an unparseable
# string, or absent (represented here by a sentinel we later drop).
_ABSENT = object()

_timestamp_field = st.one_of(
    st.sampled_from(_TIMESTAMP_POOL),
    st.sampled_from(_BAD_TIMESTAMPS),
    st.just(_ABSENT),
)

# reference is present (from a small pool, to create ties) or None/absent.
_reference_field = st.one_of(
    st.none(),
    st.sampled_from(["APP-001", "APP-002", "REF-A", "REF-B"]),
    st.just(_ABSENT),
)


@st.composite
def queue_items(draw):
    """Generate a list of queue items with a UNIQUE itemId per item.

    Timestamps are drawn from a small pool (dated), unparseable strings, or
    absent — guaranteeing a mix of dated and undated items with frequent ties.
    ``reference`` is present (small pool → ties) or absent/None. Each item gets
    a unique ``itemId`` so multiset/permutation checks are unambiguous.
    """
    count = draw(st.integers(min_value=0, max_value=12))
    items = []
    for index in range(count):
        item = {"itemId": f"item-{index}"}

        created = draw(_timestamp_field)
        if created is not _ABSENT:
            item["createdAt"] = created

        updated = draw(_timestamp_field)
        if updated is not _ABSENT:
            item["updatedAt"] = updated

        reference = draw(_reference_field)
        if reference is not _ABSENT:
            item["reference"] = reference

        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Property 3: List ordering is a correct oldest-first sort
# ---------------------------------------------------------------------------

# Feature: banker-query-triage, Property 3: List ordering is a correct oldest-first sort
@settings(max_examples=200)
@given(items=queue_items())
def test_ordering_is_a_correct_oldest_first_sort(items):
    """The sorted output satisfies the oldest-first ordering invariants.

    **Validates: Requirements 2.1, 2.2, 2.3**
    """
    # Prefer the named wrapper; it is a thin sorted(items, key=sort_key).
    ordered = handler.sort_queue(items)

    # --- Invariant 0: same length (sanity) --------------------------------
    assert len(ordered) == len(items)

    # --- Invariant 1: PERMUTATION (same multiset of items) ----------------
    # itemIds are unique, so comparing the multiset of itemIds is sufficient
    # and unambiguous — no items added or dropped, no duplication.
    assert sorted(i["itemId"] for i in ordered) == sorted(i["itemId"] for i in items)
    # The exact item objects are preserved (identity-level multiset).
    assert {id(i) for i in ordered} == {id(i) for i in items}

    # --- Invariant 2: every DATED item precedes every UNDATED item --------
    dated_flags = [_waiting_time(i) is not None for i in ordered]
    # Once we hit an undated item, no dated item may follow (undated last).
    seen_undated = False
    for is_dated in dated_flags:
        if not is_dated:
            seen_undated = True
        else:
            assert not seen_undated, "a dated item appeared after an undated item"

    # --- Invariant 3 & 4: among dated items, non-decreasing (timestamp, ---
    # --- tie-break); i.e. oldest first, ties broken by tie-break asc. -----
    dated = [i for i in ordered if _waiting_time(i) is not None]
    for a, b in zip(dated, dated[1:]):
        ta, tb = _waiting_time(a), _waiting_time(b)
        assert ta <= tb, "dated items are not ordered earliest-waiting-time first"
        # Cross-check: adjacent dated items with equal timestamps are ordered
        # by tie-break (reference else itemId) ascending lexicographically.
        if ta == tb:
            assert _tie_break(a) <= _tie_break(b), "tie-break ordering violated"
