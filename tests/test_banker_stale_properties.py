"""Property-based tests for the stale-query-notification pure helpers.

These Hypothesis property tests exercise the pure staleness logic added to
``lambda/bluey-banker-api/handler.py`` — ``stale_age``, ``is_stale``, and
``stale_flags`` — which implement the 24-hour staleness notification. Rather
than re-implementing the helpers, each test asserts a structural invariant a
correct implementation must satisfy.

Properties encoded (see design → Correctness Properties):

* P1 — Threshold monotonicity / strictness.
* P2 — Undated items are never stale.
* P3 — Count consistency.
* P4 — Read/stale independence.
* P5 — Waiting_Time consistency with ordering.
* P6 — Determinism / purity.

The handler module lives in a hyphenated directory (``bluey-banker-api``), so it
is loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same loader/env-var pattern used across the ``test_banker_*_properties.py``
suite. The helpers under test are pure, so no real AWS is touched.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
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

# The 24h threshold the helpers default to; mirrored locally so the test asserts
# against its own understanding of the boundary, not the implementation constant.
THRESHOLD = timedelta(hours=24)

# A fixed reference "now" the strategies offset around. Tz-aware so comparisons
# never mix aware/naive datetimes.
_NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Offsets (in seconds) around the threshold: well below, just below, exactly at,
# just above, well above, and far-future (negative age). This makes the strict
# boundary a frequent, targeted case.
_OFFSET_SECONDS = st.one_of(
    st.integers(min_value=-172800, max_value=172800),  # +/- 48h broad band
    st.sampled_from(
        [
            0,                       # age 0
            86400 - 1,               # just below 24h
            86400,                   # exactly 24h (NOT stale)
            86400 + 1,               # just above 24h (stale)
            -3600,                   # future-dated (negative age)
        ]
    ),
)

# Unparseable / undated markers for the undated variants.
_BAD_TIMESTAMPS = st.sampled_from([None, "", "   ", "not-a-date", "2026-13-40T99:99:99"])

_ABSENT = object()


@st.composite
def dated_item(draw):
    """An item with a parseable createdAt (and sometimes updatedAt) near NOW."""
    offset = draw(_OFFSET_SECONDS)
    created = (_NOW - timedelta(seconds=offset)).isoformat()
    item = {"itemId": draw(st.text(min_size=1, max_size=6)), "createdAt": created}
    # Optionally add an unrelated updatedAt to confirm createdAt takes priority.
    if draw(st.booleans()):
        item["updatedAt"] = (_NOW - timedelta(seconds=draw(_OFFSET_SECONDS))).isoformat()
    return item


@st.composite
def fallback_item(draw):
    """An item with no createdAt but a parseable updatedAt (Waiting_Time fallback)."""
    offset = draw(_OFFSET_SECONDS)
    updated = (_NOW - timedelta(seconds=offset)).isoformat()
    item = {"itemId": draw(st.text(min_size=1, max_size=6)), "updatedAt": updated}
    if draw(st.booleans()):
        item["createdAt"] = draw(_BAD_TIMESTAMPS)  # unparseable ⇒ still fallback
    return item


@st.composite
def undated_item(draw):
    """An item whose createdAt and updatedAt are both missing/unparseable."""
    item = {"itemId": draw(st.text(min_size=1, max_size=6))}
    if draw(st.booleans()):
        item["createdAt"] = draw(_BAD_TIMESTAMPS)
    if draw(st.booleans()):
        item["updatedAt"] = draw(_BAD_TIMESTAMPS)
    return item


any_item = st.one_of(dated_item(), fallback_item(), undated_item())

item_lists = st.lists(any_item, min_size=0, max_size=12)

# A reference "now" strategy: NOW plus a small jitter, always tz-aware.
now_strategy = st.builds(
    lambda secs: _NOW + timedelta(seconds=secs),
    st.integers(min_value=-100000, max_value=100000),
)


def _expected_waiting_time(item):
    """Local, independent Waiting_Time: createdAt else updatedAt, else None."""
    ts = handler._parse_timestamp(item.get("createdAt"))
    if ts is None:
        ts = handler._parse_timestamp(item.get("updatedAt"))
    return ts


# ---------------------------------------------------------------------------
# Property 1: Threshold monotonicity / strictness
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 1: Threshold monotonicity / strictness
@settings(max_examples=300)
@given(item=any_item, now=now_strategy)
def test_p1_threshold_strictness(item, now):
    """is_stale is True iff dated AND age strictly > 24h.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.6, 9.2**
    """
    age = handler.stale_age(item, now)
    expected = age is not None and age > THRESHOLD
    assert handler.is_stale(item, now) is expected


# Feature: stale-query-notification, Property 1: Threshold monotonicity / strictness
@settings(max_examples=50)
@given(now=now_strategy)
def test_p1_exact_boundary_is_not_stale(now):
    """An item at exactly now - 24h is NOT stale; one epsilon older IS. (R2.2)"""
    at_threshold = {"createdAt": (now - THRESHOLD).isoformat()}
    just_past = {"createdAt": (now - THRESHOLD - timedelta(seconds=1)).isoformat()}
    assert handler.is_stale(at_threshold, now) is False
    assert handler.is_stale(just_past, now) is True


# ---------------------------------------------------------------------------
# Property 2: Undated items are never stale
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 2: Undated items are never stale
@settings(max_examples=200)
@given(item=undated_item(), now=now_strategy)
def test_p2_undated_never_stale(item, now):
    """Undated items yield stale_age None and is_stale False for any now.

    **Validates: Requirements 1.3, 2.4, 9.1**
    """
    assert handler.stale_age(item, now) is None
    assert handler.is_stale(item, now) is False


# ---------------------------------------------------------------------------
# Property 3: Count consistency
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 3: Count consistency
@settings(max_examples=200)
@given(items=item_lists, now=now_strategy)
def test_p3_count_consistency(items, now):
    """stale_flags count equals the number of flagged items and is in range.

    **Validates: Requirements 3.1, 3.2, 3.3, 6.4**
    """
    count = handler.stale_flags(items, now)
    flagged = sum(1 for i in items if i["isStale"])
    assert count == flagged
    assert 0 <= count <= len(items)
    # Each per-item flag equals the single-item predicate.
    for i in items:
        assert i["isStale"] == handler.is_stale(i, now)


# ---------------------------------------------------------------------------
# Property 4: Read/stale independence
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 4: Read/stale independence
@settings(max_examples=200)
@given(item=any_item, now=now_strategy, read=st.sampled_from(["read", "unread"]))
def test_p4_read_stale_independence(item, now, read):
    """Toggling readState does not change is_stale at a fixed now.

    **Validates: Requirements 4.1, 4.2**
    """
    baseline = handler.is_stale(item, now)
    with_read = dict(item)
    with_read["readState"] = read
    toggled = dict(item)
    toggled["readState"] = "unread" if read == "read" else "read"
    assert handler.is_stale(with_read, now) is baseline
    assert handler.is_stale(toggled, now) is baseline


# ---------------------------------------------------------------------------
# Property 5: Waiting_Time consistency with ordering
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 5: Waiting_Time consistency with ordering
@settings(max_examples=300)
@given(a=any_item, b=any_item, now=now_strategy)
def test_p5_waiting_time_consistency(a, b, now):
    """Older dated item has >= age; if the newer is stale the older is too.

    **Validates: Requirements 1.2, 1.4, 5.1, 5.2, 5.3**
    """
    wa = _expected_waiting_time(a)
    wb = _expected_waiting_time(b)
    # Only meaningful when both are dated.
    if wa is None or wb is None:
        return

    age_a = handler.stale_age(a, now)
    age_b = handler.stale_age(b, now)

    # Identify older (earlier Waiting_Time) vs newer.
    if wa <= wb:
        older, newer = a, b
        age_older, age_newer = age_a, age_b
    else:
        older, newer = b, a
        age_older, age_newer = age_b, age_a

    # Older item has an age >= the newer item's age (R5.2).
    assert age_older >= age_newer

    # Monotonic threshold: if the newer item is stale, the older one is too (R5.3).
    if handler.is_stale(newer, now):
        assert handler.is_stale(older, now)


# ---------------------------------------------------------------------------
# Property 6: Determinism / purity
# ---------------------------------------------------------------------------

# Feature: stale-query-notification, Property 6: Determinism / purity
@settings(max_examples=200)
@given(item=any_item, now=now_strategy)
def test_p6_determinism_and_purity(item, now):
    """stale_age/is_stale are deterministic and do not mutate item.

    **Validates: Requirements 1.1, 1.5, 2.7, 3.4**
    """
    snapshot = dict(item)

    # Determinism across repeated calls.
    assert handler.stale_age(item, now) == handler.stale_age(item, now)
    assert handler.is_stale(item, now) is handler.is_stale(item, now)

    # stale_age / is_stale must not mutate the item.
    assert item == snapshot

    # stale_flags mutates only the isStale key.
    handler.stale_flags([item], now)
    for key, value in snapshot.items():
        assert item[key] == value
    assert set(item.keys()) - set(snapshot.keys()) <= {"isStale"}
    assert isinstance(item["isStale"], bool)
