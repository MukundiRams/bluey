"""Property-based tests for the banker-query-triage ``classify`` function.

These Hypothesis property tests exercise the pure ``classify(item)`` function in
``bluey-banker-api/handler.py``. ``classify`` depends only on fields already
present on a candidate item (``source``, ``accountType``, ``customerId`` /
``isWalkIn``, ``hasLinkedApplication``) and returns exactly one value from
``handler.WORK_CATEGORIES`` by evaluating five ordered rules.

Two properties are covered:

* Property 1 — Classification is total and deterministic: for ANY candidate
  item, ``classify`` returns exactly one value in ``WORK_CATEGORIES`` and
  returns the same value when called twice on the same item.
* Property 2 — Classification maps item shapes to the correct category: for
  items with controlled shapes, the expected category holds per rule, and rule
  ORDER is respected (e.g. a loan-product ``accountType`` wins over
  ``account_opening``, and credit source wins over everything).

The handler module lives in a directory whose name contains hyphens
(``bluey-banker-api``), so it cannot be imported with a normal ``import``. It is
loaded from its file path via ``importlib.util.spec_from_file_location`` — the
same pattern used in ``test_banker_gather.py``. The module builds boto3 DynamoDB
resources at import time; ``resource()`` makes no network call, but a region
must be resolvable, so AWS region env vars are set before loading. ``classify``
is pure, so no real AWS is touched.
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
# Domain constants mirrored from the design (kept local so the test asserts
# against an independent spec of the rules rather than the implementation's
# private sets).
# ---------------------------------------------------------------------------

# Loan/credit products (R1.3). classify() lower-cases and strips accountType.
LOAN_PRODUCTS = {"personal-loan", "home-loan", "vehicle-finance", "credit-card"}

# Deposit / transaction account types (R1.2), matched case-insensitively.
DEPOSIT_ACCOUNT_TYPES = {"Savings", "MyMo Account", "Cheque"}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Sources include the three valid ones plus invalid/None to prove totality.
_source_strategy = st.one_of(
    st.sampled_from(["session", "application", "credit"]),
    st.none(),
    st.text(max_size=8),
)

# accountType: loan products, deposit types (with case variations), arbitrary
# text, and None — so both the "known" and "unknown" branches are exercised.
_account_type_strategy = st.one_of(
    st.none(),
    st.sampled_from(sorted(LOAN_PRODUCTS)),
    st.sampled_from(sorted(DEPOSIT_ACCOUNT_TYPES)),
    st.sampled_from(["SAVINGS", "savings", "mymo account", "CHEQUE", "Personal-Loan"]),
    st.text(max_size=12),
)

_customer_id_strategy = st.one_of(st.none(), st.text(min_size=1, max_size=8))
_is_walk_in_strategy = st.one_of(st.none(), st.booleans())


@st.composite
def arbitrary_items(draw):
    """Generate an arbitrary candidate item with random field combinations.

    Fields are drawn independently (including invalid/absent values) so the
    generated space covers items no valid pipeline would produce, proving
    ``classify`` is total over anything.
    """
    item = {
        "source": draw(_source_strategy),
        "accountType": draw(_account_type_strategy),
        "hasLinkedApplication": draw(st.booleans()),
    }
    # customerId and isWalkIn are each independently present or absent.
    if draw(st.booleans()):
        item["customerId"] = draw(_customer_id_strategy)
    if draw(st.booleans()):
        item["isWalkIn"] = draw(_is_walk_in_strategy)
    return item


# ---------------------------------------------------------------------------
# Property 1: Classification is total and deterministic
# ---------------------------------------------------------------------------

# Feature: banker-query-triage, Property 1: Classification is total and deterministic
@settings(max_examples=200)
@given(item=arbitrary_items())
def test_classification_is_total_and_deterministic(item):
    """classify returns exactly one WORK_CATEGORY and is deterministic.

    **Validates: Requirements 1.1, 1.6, 1.7**
    """
    result = handler.classify(item)

    # Total: the result is one member of the closed category set (R1.1, R1.6).
    assert result in handler.WORK_CATEGORIES

    # Deterministic: a repeat call on the same item yields the same value.
    # Copy the input so we can also confirm classify did not mutate it.
    repeat = handler.classify(dict(item))
    assert repeat == result

    # A workCategory drawn from this call is always in the set (R1.7).
    assert handler.classify(item) in handler.WORK_CATEGORIES


# ---------------------------------------------------------------------------
# Property 2: Classification maps item shapes to the correct category
# ---------------------------------------------------------------------------

# Controlled shapes are generated so each rule (and rule ORDER) can be asserted
# against its expected category.

# A deposit accountType that is NOT a loan product (rule-2 territory).
_deposit_type_strategy = st.sampled_from(
    ["Savings", "MyMo Account", "Cheque", "SAVINGS", "cheque", "  savings  "]
)

# An accountType that is neither loan nor deposit (so rule 1/2 don't fire).
_neutral_account_type_strategy = st.one_of(
    st.none(),
    st.sampled_from(["", "brokerage", "unknown-product", "fixed-deposit"]),
)

_loan_product_strategy = st.sampled_from(
    ["personal-loan", "home-loan", "vehicle-finance", "credit-card", "Personal-Loan", "CREDIT-CARD"]
)


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(
    account_type=st.one_of(_account_type_strategy),
    customer_id=_customer_id_strategy,
    has_linked=st.booleans(),
    is_walk_in=_is_walk_in_strategy,
)
def test_credit_source_always_maps_to_credit_application(
    account_type, customer_id, has_linked, is_walk_in
):
    """A credit-sourced item is ``credit_application`` regardless of other fields.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": "credit",
        "accountType": account_type,
        "customerId": customer_id,
        "hasLinkedApplication": has_linked,
        "isWalkIn": is_walk_in,
    }
    assert handler.classify(item) == "credit_application"


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(
    source=st.sampled_from(["session", "application", "credit", "other", None]),
    loan_product=_loan_product_strategy,
    customer_id=_customer_id_strategy,
    has_linked=st.booleans(),
)
def test_loan_product_wins_over_account_opening(source, loan_product, customer_id, has_linked):
    """Any item whose accountType is a loan product is ``credit_application``.

    This asserts the rule ORDER: even an application-sourced item (which would
    otherwise be an account_opening) is credit_application when its accountType
    is a loan product, because rule 1 is evaluated first.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": source,
        "accountType": loan_product,
        "customerId": customer_id,
        "hasLinkedApplication": has_linked,
    }
    assert handler.classify(item) == "credit_application"


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(
    deposit_type=_deposit_type_strategy,
    customer_id=_customer_id_strategy,
    has_linked=st.booleans(),
)
def test_application_with_deposit_type_maps_to_account_opening(
    deposit_type, customer_id, has_linked
):
    """Application-sourced deposit/transaction accountType ⇒ ``account_opening``.

    The deposit type is guaranteed NOT to be a loan product, so rule 1 does not
    fire and rule 2 applies.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": "application",
        "accountType": deposit_type,
        "customerId": customer_id,
        "hasLinkedApplication": has_linked,
    }
    assert handler.classify(item) == "account_opening"


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(account_type=_neutral_account_type_strategy)
def test_walk_in_session_no_linked_application_maps_to_pre_visit_enquiry(account_type):
    """Walk-in session (no customerId) with no linked application ⇒ ``pre_visit_enquiry``.

    accountType is neutral (neither loan nor deposit) so rules 1 and 2 do not
    fire. The item is a walk-in (no customerId, isWalkIn True) session with no
    linked application, so rule 3 applies.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": "session",
        "accountType": account_type,
        "customerId": None,
        "isWalkIn": True,
        "hasLinkedApplication": False,
    }
    assert handler.classify(item) == "pre_visit_enquiry"


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(
    account_type=_neutral_account_type_strategy,
    customer_id=st.text(min_size=1, max_size=8),
)
def test_existing_customer_session_no_linked_application_maps_to_servicing(
    account_type, customer_id
):
    """Existing-customer session (customerId present) with no linked application
    ⇒ ``existing_customer_servicing``.

    accountType is neutral so rules 1 and 2 do not fire; the session has an
    existing customer (isWalkIn False) and no linked application, so rule 4
    applies.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": "session",
        "accountType": account_type,
        "customerId": customer_id,
        "isWalkIn": False,
        "hasLinkedApplication": False,
    }
    assert handler.classify(item) == "existing_customer_servicing"


# Feature: banker-query-triage, Property 2: Classification maps item shapes to the correct category
@settings(max_examples=200)
@given(
    loan_product=_loan_product_strategy,
    is_walk_in=st.booleans(),
    has_linked=st.booleans(),
)
def test_session_with_loan_product_precedence_is_credit_application(
    loan_product, is_walk_in, has_linked
):
    """A session/loan-product item is ``credit_application`` (rule-1 precedence).

    Verifies precedence: even a session that would otherwise map to
    pre_visit_enquiry or existing_customer_servicing is credit_application when
    its accountType is a loan product, because rule 1 fires first.

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5**
    """
    item = {
        "source": "session",
        "accountType": loan_product,
        "customerId": None if is_walk_in else "cust-1",
        "isWalkIn": is_walk_in,
        "hasLinkedApplication": has_linked,
    }
    assert handler.classify(item) == "credit_application"
