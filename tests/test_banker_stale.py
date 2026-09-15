"""Example/unit tests for the stale-query-notification pure helpers.

Exercises the pure staleness helpers added to
``lambda/bluey-banker-api/handler.py`` for the stale-query-notification feature:

* ``stale_age(item, now)``    — Waiting_Time age or ``None`` for undated items.
* ``is_stale(item, now, threshold)`` — strict ``> 24h`` staleness predicate.
* ``stale_flags(items, now, threshold)`` — per-item flag + aggregate count.

These are EXAMPLE tests (the invariants are covered separately by the
Hypothesis property tests in ``test_banker_stale_properties.py``). They pin the
concrete boundary behaviour called out in the design: exactly 24h is NOT stale,
just past IS stale, the ``createdAt``→``updatedAt`` fallback, undated ⇒ not
stale, and future-dated ⇒ not stale.

The handler module lives in a hyphenated directory (``bluey-banker-api``), so it
is loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used across the ``test_banker_*`` suite. AWS region env vars are set
before loading so ``boto3.resource()`` at import succeeds without config files;
the helpers under test are pure so no real AWS is touched.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


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

NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# stale_age
# ---------------------------------------------------------------------------


def test_stale_age_uses_created_at():
    """stale_age derives age from createdAt when present. (R1.1)"""
    item = {"createdAt": _iso(NOW - timedelta(hours=48))}
    assert handler.stale_age(item, NOW) == timedelta(hours=48)


def test_stale_age_falls_back_to_updated_at():
    """stale_age falls back to updatedAt when createdAt is absent. (R1.2, R5.1)"""
    item = {"updatedAt": _iso(NOW - timedelta(hours=10))}
    assert handler.stale_age(item, NOW) == timedelta(hours=10)


def test_stale_age_prefers_created_at_over_updated_at():
    """createdAt wins over updatedAt when both are present. (R1.1)"""
    item = {
        "createdAt": _iso(NOW - timedelta(hours=48)),
        "updatedAt": _iso(NOW - timedelta(hours=1)),
    }
    assert handler.stale_age(item, NOW) == timedelta(hours=48)


def test_stale_age_undated_returns_none():
    """Undated items yield None. (R1.3)"""
    assert handler.stale_age({}, NOW) is None
    assert handler.stale_age({"createdAt": None, "updatedAt": None}, NOW) is None
    assert handler.stale_age({"createdAt": "not-a-date"}, NOW) is None


def test_stale_age_does_not_mutate_item():
    """stale_age is pure and does not mutate its argument. (R1.5)"""
    item = {"createdAt": _iso(NOW - timedelta(hours=48))}
    before = dict(item)
    handler.stale_age(item, NOW)
    assert item == before


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


def test_is_stale_clearly_old_is_stale():
    """A 48h-old item is stale. (R2.1)"""
    assert handler.is_stale({"createdAt": _iso(NOW - timedelta(hours=48))}, NOW) is True


def test_is_stale_exactly_threshold_is_not_stale():
    """Exactly 24h is NOT stale (strict >). (R2.2)"""
    assert handler.is_stale({"createdAt": _iso(NOW - timedelta(hours=24))}, NOW) is False


def test_is_stale_just_past_threshold_is_stale():
    """Just past 24h IS stale. (R2.1)"""
    item = {"createdAt": _iso(NOW - (timedelta(hours=24) + timedelta(seconds=1)))}
    assert handler.is_stale(item, NOW) is True


def test_is_stale_fresh_is_not_stale():
    """A fresh (12h) item is not stale. (R2.3)"""
    assert handler.is_stale({"createdAt": _iso(NOW - timedelta(hours=12))}, NOW) is False


def test_is_stale_undated_is_not_stale():
    """Undated items are never stale. (R2.4)"""
    assert handler.is_stale({}, NOW) is False


def test_is_stale_future_dated_is_not_stale():
    """Future-dated items are not stale. (R2.5, R9.2)"""
    assert handler.is_stale({"createdAt": _iso(NOW + timedelta(hours=48))}, NOW) is False


def test_is_stale_honors_custom_threshold():
    """A caller-supplied threshold overrides the 24h default. (R2.6)"""
    item = {"createdAt": _iso(NOW - timedelta(hours=2))}
    assert handler.is_stale(item, NOW, threshold=timedelta(hours=1)) is True
    assert handler.is_stale(item, NOW, threshold=timedelta(hours=3)) is False


def test_is_stale_returns_bool():
    """is_stale always returns a bool. (R2.7)"""
    assert isinstance(handler.is_stale({"createdAt": _iso(NOW)}, NOW), bool)
    assert isinstance(handler.is_stale({}, NOW), bool)


# ---------------------------------------------------------------------------
# stale_flags
# ---------------------------------------------------------------------------


def test_stale_flags_mixed_list_from_design_example():
    """The design's worked example: 2 of 4 items stale. (R3.1, R3.2)"""
    items = [
        {"itemId": "a", "createdAt": _iso(NOW - timedelta(hours=48))},  # stale
        {"itemId": "b", "createdAt": _iso(NOW - timedelta(hours=12))},  # not stale
        {"itemId": "c", "createdAt": None, "updatedAt": None},          # undated
        {"itemId": "d", "updatedAt": _iso(NOW - timedelta(hours=48))},  # fallback, stale
    ]
    count = handler.stale_flags(items, NOW)

    assert items[0]["isStale"] is True
    assert items[1]["isStale"] is False
    assert items[2]["isStale"] is False
    assert items[3]["isStale"] is True
    assert count == 2
    assert count == sum(1 for i in items if i["isStale"])


def test_stale_flags_count_bounds_and_consistency():
    """Count is within [0, len] and matches the per-item flags. (R3.2, R3.3)"""
    items = [{"createdAt": _iso(NOW - timedelta(hours=48))} for _ in range(3)]
    count = handler.stale_flags(items, NOW)
    assert count == 3
    assert 0 <= count <= len(items)


def test_stale_flags_empty_list():
    """An empty list yields a zero count. (R3.3)"""
    assert handler.stale_flags([], NOW) == 0


def test_stale_flags_only_mutates_is_stale_key():
    """stale_flags mutates only the isStale key. (R3.4)"""
    item = {"itemId": "x", "createdAt": _iso(NOW - timedelta(hours=48)), "readState": "read"}
    handler.stale_flags([item], NOW)
    assert item["itemId"] == "x"
    assert item["readState"] == "read"
    assert item["isStale"] is True
