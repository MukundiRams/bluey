"""Unit tests for banker-query-triage identity resolution (Requirement 5.6).

Exercises ``resolve_banker(claims, bankers_tbl=None)`` in
``bluey-banker-api/handler.py``. It reads the Cognito ID-token ``email`` claim,
scans ``bluey-bankers`` for a record whose ``email`` matches (case-insensitive),
and returns ``{"bankerId", "tier"}`` on a match or ``None`` on a missing/empty
email claim or no matching record. The caller turns ``None`` into a
``403 identity_unresolved`` with no items (R5.6).

These are plain example unit tests (no Hypothesis). A ``FakeTable`` stub exposes
``.scan()`` returning seeded banker rows, injected via ``bankers_tbl=...`` so no
real AWS is touched.

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_gather.py`` /
``test_banker_classify_properties.py``. The module builds boto3 DynamoDB
resources at import time; ``resource()`` makes no network call, but a region
must be resolvable, so AWS region env vars are set before loading.
"""
import importlib.util
import os
import sys
from pathlib import Path


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


class FakeTable:
    """Minimal DynamoDB table stub exposing ``.scan(**kwargs)``.

    Returns a fixed ``{"Items": [...]}`` payload regardless of any arguments.
    ``resolve_banker`` scans and matches emails in Python, so this stub is
    sufficient to exercise the real matching logic without live AWS.
    """

    def __init__(self, items):
        self._items = items

    def scan(self, **kwargs):
        return {"Items": list(self._items)}


# Seeded banker rows mirroring the demo seed data (scripts/seed_data.py):
# banker-001 (general) and banker-003 (premium).
_SEEDED_BANKERS = [
    {
        "bankerId": "banker-001",
        "name": "Lindiwe Dube",
        "email": "lindiwe.dube@standardbank.co.za",
        "tier": "general",
    },
    {
        "bankerId": "banker-003",
        "name": "Themba Ndlovu",
        "email": "themba.ndlovu@standardbank.co.za",
        "tier": "premium",
    },
]


def _seeded_table():
    return FakeTable(_SEEDED_BANKERS)


# ---------------------------------------------------------------------------
# Known email resolves to the correct {bankerId, tier} (Requirement 5.6)
# ---------------------------------------------------------------------------


def test_known_general_banker_email_resolves():
    """A known general-tier banker email resolves to its identity. (R5.6)"""
    result = handler.resolve_banker(
        {"email": "lindiwe.dube@standardbank.co.za"}, bankers_tbl=_seeded_table()
    )
    assert result == {"bankerId": "banker-001", "tier": "general"}


def test_known_premium_banker_email_resolves():
    """A known premium-tier banker email resolves to its identity. (R5.6)"""
    result = handler.resolve_banker(
        {"email": "themba.ndlovu@standardbank.co.za"}, bankers_tbl=_seeded_table()
    )
    assert result == {"bankerId": "banker-003", "tier": "premium"}


# ---------------------------------------------------------------------------
# Case-insensitive matching (Requirement 5.6)
# ---------------------------------------------------------------------------


def test_uppercased_known_email_still_resolves():
    """An uppercased version of a known email still resolves. (R5.6)"""
    result = handler.resolve_banker(
        {"email": "LINDIWE.DUBE@STANDARDBANK.CO.ZA"}, bankers_tbl=_seeded_table()
    )
    assert result == {"bankerId": "banker-001", "tier": "general"}


def test_mixed_case_known_email_still_resolves():
    """A mixed-case known email still resolves. (R5.6)"""
    result = handler.resolve_banker(
        {"email": "Themba.Ndlovu@StandardBank.co.za"}, bankers_tbl=_seeded_table()
    )
    assert result == {"bankerId": "banker-003", "tier": "premium"}


# ---------------------------------------------------------------------------
# Unknown / missing / empty email returns None (Requirement 5.6)
# ---------------------------------------------------------------------------


def test_unknown_email_returns_none():
    """An email with no matching banker record returns None. (R5.6)"""
    result = handler.resolve_banker(
        {"email": "nobody@standardbank.co.za"}, bankers_tbl=_seeded_table()
    )
    assert result is None


def test_missing_email_claim_returns_none():
    """A claims dict with no email key returns None. (R5.6)"""
    result = handler.resolve_banker({}, bankers_tbl=_seeded_table())
    assert result is None


def test_empty_email_claim_returns_none():
    """An empty-string email claim returns None. (R5.6)"""
    result = handler.resolve_banker({"email": ""}, bankers_tbl=_seeded_table())
    assert result is None


def test_whitespace_email_claim_returns_none():
    """A whitespace-only email claim returns None. (R5.6)"""
    result = handler.resolve_banker({"email": "   "}, bankers_tbl=_seeded_table())
    assert result is None
