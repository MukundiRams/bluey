import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION_NAME", os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = dynamodb.Table(os.environ.get("SESSIONS_TABLE", "bluey-sessions"))
customers_table = dynamodb.Table(os.environ.get("CUSTOMERS_TABLE", "bluey-customers"))
bankers_table = dynamodb.Table(os.environ.get("BANKERS_TABLE", "bluey-bankers"))
documents_table = dynamodb.Table(os.environ.get("DOCUMENTS_TABLE", "bluey-documents"))
applications_table = dynamodb.Table(os.environ.get("APPLICATIONS_TABLE", "bluey-applications"))
accounts_table = dynamodb.Table(os.environ.get("ACCOUNTS_TABLE", "bluey-accounts"))
credit_table = dynamodb.Table(os.environ.get("CREDIT_TABLE", "bluey-credit"))
read_state_table = dynamodb.Table(os.environ.get("READ_STATE_TABLE", "bluey-banker-read-state"))
assignments_table = dynamodb.Table(os.environ.get("ASSIGNMENTS_TABLE", "bluey-banker-assignments"))
s3 = boto3.client("s3", region_name=REGION)
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")

# Statuses that mark an application/credit record as awaiting banker review.
_PENDING_STATUSES = {"Pending", "pending", "pending_review"}

# The complete, closed set of Work_Category values a queue item can carry.
# classify() is total over this set (see design → Classification algorithm).
WORK_CATEGORIES = frozenset(
    {
        "account_opening",
        "credit_application",
        "pre_visit_enquiry",
        "existing_customer_servicing",
        "unassigned_fallback",
    }
)

# accountType / product values denoting a credit or loan product (R1.3).
_LOAN_PRODUCTS = frozenset(
    {"personal-loan", "home-loan", "vehicle-finance", "credit-card"}
)

# accountType values denoting a deposit / transaction product (R1.2).
_DEPOSIT_ACCOUNT_TYPES = frozenset({"savings", "mymo account", "cheque"})

# Staleness boundary: a pending item waiting strictly longer than this is stale.
# See design → Stale Query Notification. Exactly 24h is NOT stale (strict >).
STALE_THRESHOLD = timedelta(hours=24)


def build_item_id(source, *, session_id=None, reference=None, customer_id=None):
    """Build the canonical queue-item identifier for a record.

    Schemes (see design → Data Models → Queue item identity):
      - session-sourced:     "session#" + sessionId
      - application-sourced:  "application#" + reference
      - credit-sourced:      "credit#" + customerId + "#" + sessionId

    Pure function: takes primitive values and returns a string. Does not
    touch boto3 so it can be unit/property tested in isolation.
    """
    if source == "session":
        return f"session#{session_id}"
    if source == "application":
        return f"application#{reference}"
    if source == "credit":
        return f"credit#{customer_id}#{session_id}"
    raise ValueError(f"unknown queue-item source: {source!r}")


def normalize_session(record):
    """Normalize a raw ``bluey-sessions`` record into a candidate dict.

    Pure function over a single raw dict; performs no I/O.
    """
    session_id = record.get("sessionId")
    return {
        "itemId": build_item_id("session", session_id=session_id),
        "source": "session",
        "sessionId": session_id,
        "customerId": record.get("customerId"),
        "reference": record.get("reference"),
        "accountType": record.get("accountType"),
        "createdAt": record.get("createdAt"),
        "updatedAt": record.get("updatedAt"),
        "raw": record,
    }


def normalize_application(record):
    """Normalize a raw ``bluey-applications`` record into a candidate dict.

    Pure function over a single raw dict; performs no I/O.
    """
    reference = record.get("reference")
    return {
        "itemId": build_item_id("application", reference=reference),
        "source": "application",
        "sessionId": record.get("sessionId"),
        "customerId": record.get("customerId"),
        "reference": reference,
        "accountType": record.get("accountType"),
        "createdAt": record.get("createdAt"),
        "updatedAt": record.get("updatedAt"),
        "raw": record,
    }


def normalize_credit(record):
    """Normalize a raw ``bluey-credit`` record into a candidate dict.

    Pure function over a single raw dict; performs no I/O.
    """
    customer_id = record.get("customerId")
    session_id = record.get("sessionId")
    return {
        "itemId": build_item_id("credit", customer_id=customer_id, session_id=session_id),
        "source": "credit",
        "sessionId": session_id,
        "customerId": customer_id,
        "reference": record.get("reference"),
        "accountType": record.get("accountType"),
        "createdAt": record.get("createdAt"),
        "updatedAt": record.get("updatedAt"),
        "raw": record,
    }


def _is_pending(record):
    """Return True when a raw record carries a pending-review marker."""
    return (
        record.get("reviewStatus") in _PENDING_STATUSES
        or record.get("status") in _PENDING_STATUSES
    )


def gather_candidates(
    sessions_tbl=None,
    applications_tbl=None,
    credit_tbl=None,
):
    """Gather queue-item candidates from sessions, applications, and credit.

    Scans:
      - sessions where ``reviewStatus == "pending_review"``
      - applications with a pending status (Pending/pending/pending_review)
      - credit with a pending ``reviewStatus``/``status``

    Each raw record is normalized (via the pure ``normalize_*`` helpers) into a
    candidate dict carrying: ``itemId``, ``source``, ``customerId``,
    ``reference``, ``accountType``, ``createdAt``, ``updatedAt``, and the raw
    ``raw`` record. Sessions already represented by an application (same
    ``sessionId``) are de-duplicated so a standalone session candidate is not
    emitted alongside its application (mirrors the existing ``seen_sessions``
    logic in the ``list`` action).

    Table resources are accepted as parameters so the module stays importable
    and testable without live AWS; they default to the module-level tables.
    """
    sessions_tbl = sessions_tbl if sessions_tbl is not None else sessions_table
    applications_tbl = applications_tbl if applications_tbl is not None else applications_table
    credit_tbl = credit_tbl if credit_tbl is not None else credit_table

    candidates = []

    # Applications first so we can record which sessions they already represent.
    app_resp = applications_tbl.scan()
    sessions_with_application = set()
    for app in app_resp.get("Items", []):
        sid = app.get("sessionId")
        if sid:
            sessions_with_application.add(sid)
        if _is_pending(app):
            candidates.append(normalize_application(app))

    # Sessions pending review, skipping any already represented by an application.
    session_resp = sessions_tbl.scan(
        FilterExpression="reviewStatus = :rs",
        ExpressionAttributeValues={":rs": "pending_review"},
    )
    for record in session_resp.get("Items", []):
        if record.get("sessionId") in sessions_with_application:
            continue
        candidates.append(normalize_session(record))

    # Credit records marked for review.
    credit_resp = credit_tbl.scan()
    for record in credit_resp.get("Items", []):
        if _is_pending(record):
            candidates.append(normalize_credit(record))

    return candidates


def _resolve_assigned_banker_id(customer_id, customers_tbl):
    """Resolve a customerId to its Assigned_Banker (personalBanker.bankerId).

    Returns the ``bankerId`` string for an existing customer whose customer
    record carries a ``personalBanker`` object with a ``bankerId``, or ``None``
    for a missing customerId, a missing customer record, an Unassigned_Customer
    (no ``personalBanker``/no ``bankerId``), or a walk-in.

    The customers table is looked up here (the only I/O in enrich); the caller
    may inject a fake table so enrich is testable without live AWS.
    """
    if not customer_id:
        return None
    customer = customers_tbl.get_item(Key={"customerId": customer_id}).get("Item") or {}
    personal_banker = customer.get("personalBanker") or {}
    return personal_banker.get("bankerId")


def enrich(item, customers_tbl=None, assignments_tbl=None):
    """Enrich a gathered candidate with routing/classification inputs.

    Adds three fields to (a copy-updated) ``item``:
      - ``assignedBankerId`` (str | None): the item's Assigned_Banker, resolved
        by this precedence (R5.1, R5.2, R5.3):
          1. the owning customer's ``personalBanker.bankerId`` when the customer
             record carries one (an Assigned_Customer) — this ALWAYS wins and is
             NEVER overridden by an allocation lookup (R5.1); the allocation
             read is skipped entirely in this case.
          2. else, for an Unassigned_Customer / Walk_In_Applicant (no
             ``personalBanker.bankerId``), the persisted
             ``Allocation_Record.assignedBankerId`` read back via
             ``get_allocation(itemId, assignments_tbl)`` when a record with a
             truthy value exists (R5.2).
          3. else ``None`` — including when the allocation record is absent or
             carries a missing/falsy ``assignedBankerId`` (R5.3, R8.4).
      - ``isWalkIn`` (bool): ``True`` when the candidate has no ``customerId``
        (a Walk_In_Applicant), else ``False``.
      - ``hasLinkedApplication`` (bool): ``True`` for application-sourced items
        (an application inherently IS the application); ``False`` for surviving
        session items (a session already represented by an application was
        de-duplicated away in ``gather_candidates``) and for credit items.

    Both AWS-touching lookups are factored behind injectable parameters so
    enrich stays testable without live AWS (R5.4): ``customers_tbl`` (the
    ``personalBanker`` lookup, defaulting to the module-level
    ``customers_table``) and ``assignments_tbl`` (the ``Allocation_Record``
    read-back, defaulting to the module-level ``assignments_table``). The
    customer ``get_item`` always runs; the assignments ``get_item`` runs only on
    the unassigned path. The field-extraction logic is otherwise pure.
    """
    customers_tbl = customers_tbl if customers_tbl is not None else customers_table
    assignments_tbl = assignments_tbl if assignments_tbl is not None else assignments_table

    customer_id = item.get("customerId")
    source = item.get("source")

    assigned_banker_id = _resolve_assigned_banker_id(customer_id, customers_tbl)
    if not assigned_banker_id:
        # Unassigned_Customer / Walk_In_Applicant: consult the persisted
        # Allocation_Record. A present personalBanker.bankerId is never reached
        # here, so it is never overridden (R5.1, R5.2, R5.3, R8.4).
        allocated = get_allocation(item.get("itemId"), assignments_tbl)
        if allocated:
            assigned_banker_id = allocated

    # Normalize a falsy result (e.g. an empty personalBanker.bankerId with no
    # truthy allocation) to None so an unassigned item is reported as None
    # (R5.3, R8.4), consistent with visible_to/workload_count treating falsy as
    # unassigned.
    item["assignedBankerId"] = assigned_banker_id or None
    item["isWalkIn"] = not customer_id
    item["hasLinkedApplication"] = source == "application"
    return item


def classify(item):
    """Classify a candidate into exactly one Work_Category (pure, no I/O).

    Evaluates the five ordered rules from the design so the result is total
    and deterministic; depends only on fields already present on the candidate
    (``source``, ``accountType``, ``customerId``/``isWalkIn``,
    ``hasLinkedApplication``), so it can be tested directly:

      1. ``credit_application`` if source == "credit" OR the item relates to a
         loan product (accountType in the loan-product set).             (R1.3)
      2. else ``account_opening`` if source == "application" with a
         deposit/transaction accountType.                                (R1.2)
      3. else ``pre_visit_enquiry`` if it is a walk-in session (no customerId)
         with no linked application.                                     (R1.4)
      4. else ``existing_customer_servicing`` if it is a session for an
         existing customer with no linked application.                   (R1.5)
      5. else ``unassigned_fallback``.                                   (R1.6)

    Returns one of the values in ``WORK_CATEGORIES`` (R1.1).
    """
    source = item.get("source")
    account_type = item.get("accountType")
    account_type_norm = account_type.strip().lower() if isinstance(account_type, str) else ""

    # isWalkIn may already be present from enrich; fall back to customerId.
    is_walk_in = item.get("isWalkIn")
    if is_walk_in is None:
        is_walk_in = not item.get("customerId")
    has_linked_application = bool(item.get("hasLinkedApplication"))

    # Rule 1: credit-sourced or loan-product item.
    if source == "credit" or account_type_norm in _LOAN_PRODUCTS:
        return "credit_application"

    # Rule 2: application-sourced deposit/transaction account opening.
    if source == "application" and account_type_norm in _DEPOSIT_ACCOUNT_TYPES:
        return "account_opening"

    # Rule 3: walk-in session with no linked application.
    if source == "session" and is_walk_in and not has_linked_application:
        return "pre_visit_enquiry"

    # Rule 4: existing-customer session with no linked application.
    if source == "session" and not is_walk_in and not has_linked_application:
        return "existing_customer_servicing"

    # Rule 5: everything else.
    return "unassigned_fallback"


def resolve_banker(claims, bankers_tbl=None):
    """Resolve a signed-in banker to a ``{bankerId, tier}`` identity (R5.6).

    Reads the Cognito ID-token ``email`` claim and scans ``bluey-bankers`` for a
    record whose ``email`` matches (case-insensitive — seed emails are lowercase,
    but comparing case-insensitively is safer). On a match, returns
    ``{"bankerId": ..., "tier": ...}``; on a missing/empty email claim or no
    matching record, returns ``None`` (the caller turns ``None`` into a
    ``403 identity_unresolved``).

    The bankers table is accepted as a parameter (defaulting to the module-level
    ``bankers_table``) so this function is testable without live AWS. This is the
    only I/O in the routing/identity path (a scan of ``bluey-bankers``).
    """
    bankers_tbl = bankers_tbl if bankers_tbl is not None else bankers_table

    email = claims.get("email")
    if not email or not str(email).strip():
        return None
    email_norm = str(email).strip().lower()

    resp = bankers_tbl.scan()
    for record in resp.get("Items", []):
        record_email = record.get("email")
        if isinstance(record_email, str) and record_email.strip().lower() == email_norm:
            return {"bankerId": record.get("bankerId"), "tier": record.get("tier")}
    return None


def visible_to(item, banker_id, tier):
    """The single routing predicate used by both ``list`` and ``mark_read`` (R5).

    PURE function (no I/O). Decides whether a queue ``item`` is visible to the
    requesting banker ``(banker_id, tier)`` using the item's Assigned_Banker
    ``A = item.get("assignedBankerId")`` (set by ``enrich``):

      - If ``A`` is set (truthy): visible exactly when ``A == banker_id`` — an
        assigned item goes only to its assigned banker.            (R5.1)
      - If ``A`` is ``None``/absent (General_Pool): visible exactly when
        ``tier == "general"`` — general-pool items go to general-tier bankers
        only, and non-general tiers never see the general pool.  (R5.2–R5.4)

    Reads ``assignedBankerId`` off the item (does NOT do a customer lookup), so
    it stays pure and can be property-tested in isolation.
    """
    assigned = item.get("assignedBankerId")
    if assigned:
        return assigned == banker_id
    return tier == "general"


def routing_designation(item):
    """Return the reporting ``routing`` designation for a queue item (R5.5).

    PURE function (no I/O). ``"assigned"`` when the item has a truthy
    ``assignedBankerId`` (Assigned_Banker set), else ``"general_pool"`` (the
    ``unassigned_fallback`` reporting designation for unassigned customers and
    walk-ins). Reads only ``assignedBankerId`` so it can be asserted directly by
    the Property 4 test.
    """
    return "assigned" if item.get("assignedBankerId") else "general_pool"


# Sentinel used as tuple element 2 for undated items. It is a timezone-aware
# datetime (datetime.max) so element 2 is ALWAYS a datetime — dated and undated
# items never mix types under comparison (avoids Python 3 TypeError). Element 1
# (0/1) already partitions dated vs undated, so this sentinel only orders
# undated items internally (all equal here → tie-break by element 3).
_UNDATED_TIMESTAMP = datetime.max.replace(tzinfo=timezone.utc)


def _parse_timestamp(value):
    """Parse an ISO-8601 timestamp string into a tz-aware datetime, or None.

    Tolerant of missing/empty/unparseable values (returns ``None``) and of
    naive datetimes (assumes UTC) so comparisons never mix aware/naive. Pure:
    no I/O. Used only by ``sort_key``.
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


def sort_key(item):
    """Ordering key for a queue item implementing oldest-first ordering (R2).

    PURE function (no I/O). Returns a 3-tuple that, when the queue is sorted
    ascending, yields dated-oldest-first, then undated last, ties broken by
    ``reference`` (falling back to canonical ``itemId``) ascending:

      (has_no_timestamp, timestamp_or_MAX, reference_or_itemId)

    - element 1 (``0`` | ``1``): ``0`` when the item has a parseable timestamp,
      ``1`` when undated — so undated sorts AFTER dated ascending (R2.3).
    - element 2 (``datetime``): the parsed Waiting_Time for dated items;
      ``_UNDATED_TIMESTAMP`` (tz-aware ``datetime.max``) for undated items. It
      is ALWAYS a ``datetime`` so dated/undated never mix types (R2.1).
    - element 3 (``str``): the tie-break — ``reference`` if present, else the
      canonical ``itemId`` — compared ascending lexicographically (R2.2).

    Waiting_Time derives from ``createdAt``, falling back to ``updatedAt`` when
    ``createdAt`` is absent/empty; unparseable/missing timestamps are treated as
    UNDATED.
    """
    timestamp = _parse_timestamp(item.get("createdAt"))
    if timestamp is None:
        timestamp = _parse_timestamp(item.get("updatedAt"))

    has_no_timestamp = 0 if timestamp is not None else 1
    timestamp_or_max = timestamp if timestamp is not None else _UNDATED_TIMESTAMP

    reference = item.get("reference")
    tie_break = reference if reference else item.get("itemId")
    tie_break = str(tie_break) if tie_break is not None else ""

    return (has_no_timestamp, timestamp_or_max, tie_break)


def sort_queue(items):
    """Return a new list of ``items`` ordered oldest-first (R2) via ``sort_key``.

    Thin, pure wrapper over ``sorted(items, key=sort_key)`` so callers have a
    single named entry point. Does not mutate the input list. Wiring this into
    the ``list`` action is task 9.1 — it is intentionally NOT wired here.
    """
    return sorted(items, key=sort_key)


def stale_age(item, now):
    """Age of a queue item at reference time ``now`` (PURE, no I/O).

    Derives Waiting_Time exactly as ``sort_key`` does: parse ``createdAt`` via
    the existing tolerant ``_parse_timestamp``, falling back to ``updatedAt``.
    Returns ``now - waiting_time`` (a ``timedelta``) when a timestamp is
    parseable, else ``None`` for an UNDATED item (both timestamps missing/
    unparseable). Naive stored timestamps are treated as UTC by
    ``_parse_timestamp`` so the subtraction never mixes aware/naive datetimes.

    Pure and deterministic: does not mutate ``item`` and returns equal results
    for equal ``(item, now)`` inputs. This is the single source of truth for an
    item's "age" and underpins ``is_stale`` (R1).
    """
    waiting_time = _parse_timestamp(item.get("createdAt"))
    if waiting_time is None:
        waiting_time = _parse_timestamp(item.get("updatedAt"))
    if waiting_time is None:
        return None
    return now - waiting_time


def is_stale(item, now, threshold=STALE_THRESHOLD):
    """Boolean staleness predicate for a single item (PURE, no I/O) (R2).

    Returns ``True`` iff the item is dated (``stale_age`` is not ``None``) AND
    its age STRICTLY exceeds ``threshold`` (default 24h). Returns ``False`` for:
      - UNDATED items (``stale_age`` is ``None``),
      - items whose age is exactly the threshold (strict ``>``), and
      - future-dated items (negative age).

    Depends only on ``createdAt``/``updatedAt``/``now`` (never ``readState``),
    so staleness is orthogonal to read/unread state (R4). Always returns a
    ``bool``, is total over all dicts, and honors a caller-supplied ``threshold``.
    """
    age = stale_age(item, now)
    return age is not None and age > threshold


def stale_flags(items, now, threshold=STALE_THRESHOLD):
    """Flag each item's ``isStale`` and return the stale count (PURE) (R3).

    Mirrors ``resolve_read_state`` + ``unread_count``: in one pass sets
    ``item["isStale"] = is_stale(item, now, threshold)`` for every item and
    returns the number of stale items. By construction the returned count equals
    ``sum(1 for i in items if i["isStale"])`` and lies in ``[0, len(items)]``.
    Mutates only the ``isStale`` key of each item; performs no I/O.
    """
    count = 0
    for item in items:
        stale = is_stale(item, now, threshold)
        item["isStale"] = stale
        if stale:
            count += 1
    return count


def read_state_for(banker_id, item_ids, read_state_tbl=None):
    """Return the SET of ``itemId``s marked read for ``banker_id`` (R4.1, R4.2, R4.5).

    Read/unread state lives in ``bluey-banker-read-state`` keyed by ``bankerId``
    (partition) + ``itemId`` (sort). This queries by the ``bankerId`` partition
    key — a single call returning every read record for the banker — then
    intersects those read ``itemId``s with the requested ``item_ids``. An
    ``itemId`` absent from the returned set is therefore ``unread`` (R4.2); a
    present one is ``read`` (R4.5).

    Only ``item_ids`` in the requested collection are returned, so callers get a
    set they can directly hand to the PURE ``resolve_read_state``.

    The table resource is accepted as a parameter (defaulting to the
    module-level ``read_state_table``) so this function is testable against an
    in-memory fake / ``moto`` without live AWS. This (a ``query`` on the read
    -state table) is the only I/O in read-state resolution.
    """
    read_state_tbl = read_state_tbl if read_state_tbl is not None else read_state_table

    requested = set(item_ids)
    if not requested:
        return set()

    read_ids = set()
    resp = read_state_tbl.query(
        KeyConditionExpression="bankerId = :bid",
        ExpressionAttributeValues={":bid": banker_id},
    )
    for record in resp.get("Items", []):
        item_id = record.get("itemId")
        if item_id is not None:
            read_ids.add(item_id)

    # Paginate defensively in case a banker has more read records than one page.
    while resp.get("LastEvaluatedKey"):
        resp = read_state_tbl.query(
            KeyConditionExpression="bankerId = :bid",
            ExpressionAttributeValues={":bid": banker_id},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        for record in resp.get("Items", []):
            item_id = record.get("itemId")
            if item_id is not None:
                read_ids.add(item_id)

    return read_ids & requested


def resolve_read_state(items, read_ids):
    """Join read state onto each item as ``readState`` (PURE, no I/O) (R4.1, R4.2).

    Mutates each item in ``items`` in place, setting
    ``item["readState"] = "read"`` when its ``itemId`` is in the ``read_ids``
    set, else ``"unread"`` (absence ⇒ unread, R4.2). ``read_ids`` is supplied by
    the caller (typically ``read_state_for``), so this function performs no I/O
    and can be property-tested directly (Property 5). Returns ``items`` for
    convenience.
    """
    read_ids = read_ids or set()
    for item in items:
        item["readState"] = "read" if item.get("itemId") in read_ids else "unread"
    return items


def unread_count(items):
    """Count items whose ``readState`` is ``"unread"`` (PURE, no I/O) (R3.3).

    Derived directly from the resolved ``readState`` on each item (so it is
    consistent with ``resolve_read_state`` by construction), enabling the
    Property 5 consistency check. Items without a ``readState`` are treated as
    ``unread``.
    """
    return sum(1 for item in items if item.get("readState", "unread") == "unread")


def workload_count(banker_id, items):
    """Count Pending_Items assigned to ``banker_id`` (PURE, no I/O) (R1).

    Returns the number of items ``i`` in ``items`` whose
    ``i.get("assignedBankerId")`` equals ``banker_id`` (R1.1). Every supplied
    item is treated as a Pending_Item — the list comes from
    ``gather_candidates``, which emits only pending records, so resolved
    (approved/rejected) items are already absent (R1.2, R1.3, R1.4). A
    falsy/absent ``assignedBankerId`` matches no banker, so unassigned items are
    never counted (R8.1). The result is an integer in ``[0, len(items)]``
    (R1.5). Reads only ``assignedBankerId`` off each item; does not mutate
    ``items`` and returns equal results for equal inputs (R1.6).
    """
    return sum(
        1
        for item in items
        if item.get("assignedBankerId") and item.get("assignedBankerId") == banker_id
    )


def workload_by_banker(banker_ids, items):
    """Map each supplied ``bankerId`` to its ``workload_count`` (PURE, no I/O) (R2).

    Returns a mapping with exactly one entry per DISTINCT supplied ``bankerId``
    (R2.1); duplicate ids collapse to a single key because a mapping cannot hold
    a key twice. Each banker's value is ``workload_count(banker_id, items)`` over
    the same ``items`` list (R2.2), which is ``0`` for a banker with no assigned
    pending items (R2.3). Reads only ``assignedBankerId`` off each item (via
    ``workload_count``); does not mutate either argument, performs no I/O, and
    returns equal results for equal inputs (R2.4).
    """
    return {banker_id: workload_count(banker_id, items) for banker_id in banker_ids}


def allocate(item, bankers, workload_map):
    """Select the owning General_Tier banker for a General_Pool item (PURE) (R3).

    If ``item`` already carries a truthy ``assignedBankerId``, returns it
    unchanged — an idempotent no-op for an already-owned item, without
    consulting ``workload_map`` (R3.5). Otherwise considers only
    ``General_Tier`` bankers (``tier == "general"``) as candidates (R3.3, R6.5)
    and returns the Least_Loaded_Banker: the candidate minimizing the key
    ``(workload_map.get(bankerId, 0), bankerId)``, so a missing map entry is
    treated as ``0`` (R3.1, R7.1, R8.3), ties are broken by the smallest
    ``bankerId`` lexicographically (R3.2), and a Premium_Tier banker is never
    selected. Returns ``None`` when there is no general banker (R3.4, R8.2).
    Reads only ``assignedBankerId`` off the item and ``tier``/``bankerId`` off
    each banker; does not mutate its arguments, performs no I/O, and returns
    equal results for equal inputs (R3.6).
    """
    assigned = item.get("assignedBankerId")
    if assigned:
        return assigned

    general = [b for b in bankers if b.get("tier") == "general"]
    if not general:
        return None

    return min(
        general,
        key=lambda b: (workload_map.get(b["bankerId"], 0), b["bankerId"]),
    )["bankerId"]


def get_allocation(item_id, assignments_tbl=None):
    """Read back the persisted ``assignedBankerId`` for ``item_id`` (R4).

    Looks up the Assignments_Table by ``itemId`` (partition key) via
    ``get_item`` (R4.2) and returns the record's ``assignedBankerId`` when
    present and truthy, else ``None`` (R8.4). When no record exists for the
    ``itemId``, ``get_item`` returns no ``Item`` and this returns ``None``
    without raising (R8.5).

    The Assignments_Table resource is accepted as a parameter (defaulting to the
    module-level ``assignments_table``) so enrichment stays testable without
    live AWS (R4.4). This ``get_item`` is the only I/O in the read path.
    """
    assignments_tbl = assignments_tbl if assignments_tbl is not None else assignments_table

    record = assignments_tbl.get_item(Key={"itemId": item_id}).get("Item") or {}
    assigned = record.get("assignedBankerId")
    return assigned if assigned else None


def put_allocation(item_id, banker_id, assignments_tbl=None):
    """Idempotently persist an Allocation_Record for ``item_id`` (R4).

    Writes ``{"itemId": item_id, "assignedBankerId": banker_id}`` keyed by
    ``itemId`` (R4.1, R4.2) with a conditional put
    (``attribute_not_exists(itemId)``) so an existing record is never
    overwritten — allocation is first-writer-wins, idempotent, and does not
    reallocate (R4.3, R4.5). When the condition fails, DynamoDB raises a
    ``ConditionalCheckFailedException``; that specific ``ClientError`` is caught
    and treated as success (the stored ``assignedBankerId`` stays whatever was
    written first). Any other ``ClientError`` is re-raised.

    The Assignments_Table resource is accepted as a parameter (defaulting to the
    module-level ``assignments_table``) so this stays testable without live AWS
    (R4.4). This ``put_item`` is the only I/O in the write path.
    """
    assignments_tbl = assignments_tbl if assignments_tbl is not None else assignments_table

    try:
        assignments_tbl.put_item(
            Item={"itemId": item_id, "assignedBankerId": banker_id},
            ConditionExpression="attribute_not_exists(itemId)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            # First-writer-wins: an allocation already exists → treat as success.
            return
        raise


def routed_item_ids_for(
    banker_id,
    tier,
    sessions_tbl=None,
    applications_tbl=None,
    credit_tbl=None,
    customers_tbl=None,
):
    """Return the SET of ``itemId``s currently routed to ``(banker_id, tier)``.

    Recomputes the same routed queue the ``list`` action would produce for this
    banker, reusing the shared pipeline so authorization stays consistent with
    visibility by construction (design → Why this shape):

      gather_candidates -> enrich each -> keep those ``visible_to`` the banker
      -> collect their canonical ``itemId``s.

    The DynamoDB table resources are accepted as parameters (defaulting to the
    module-level tables) so this is testable without live AWS. Used by
    ``mark_read`` for R4.7 authorization; a caller may instead pass a
    precomputed set to ``mark_read`` to avoid recomputing.
    """
    candidates = gather_candidates(
        sessions_tbl=sessions_tbl,
        applications_tbl=applications_tbl,
        credit_tbl=credit_tbl,
    )
    routed = set()
    for candidate in candidates:
        enriched = enrich(candidate, customers_tbl=customers_tbl)
        if visible_to(enriched, banker_id, tier):
            item_id = enriched.get("itemId")
            if item_id is not None:
                routed.add(item_id)
    return routed


# Sentinel returned by ``mark_read`` when the item is not routed to the banker
# (R4.7). The caller (task 9.2) maps this to ``403 not_authorized_for_item``
# and performs no write. Using a distinct string keeps the contract simple and
# exception-free for the wiring layer.
NOT_AUTHORIZED = "not_authorized"


def mark_read(
    banker_id,
    tier,
    item_id,
    *,
    routed_item_ids=None,
    read_state_tbl=None,
    sessions_tbl=None,
    applications_tbl=None,
    credit_tbl=None,
    customers_tbl=None,
):
    """Idempotently mark ``item_id`` read for ``banker_id`` after authorization.

    Contract (design → mark_read request flow, R4.4/R4.5/R4.6/R4.7):

      - Authorization (R4.7): the item must be routed to ``(banker_id, tier)``.
        The routed set is either supplied via ``routed_item_ids`` (a set, for
        tests / to avoid recomputation) or recomputed with
        ``routed_item_ids_for``. If ``item_id`` is NOT in the routed set, this
        returns the sentinel string ``NOT_AUTHORIZED`` and performs NO write,
        leaving read state unchanged. The caller turns this into a
        ``403 not_authorized_for_item``.
      - On success: performs an idempotent ``PutItem`` to the read-state table
        with ``{"bankerId": banker_id, "itemId": item_id, "readAt": <iso now>}``
        (R4.4). A second call simply overwrites ``readAt`` and the state stays
        ``read`` (idempotent, R4.6). Returns ``{"readState": "read"}`` on
        success.

    The read-state and pipeline table resources are accepted as parameters
    (defaulting to the module-level tables) so this is testable without live
    AWS. Wiring into the ``mark_read`` ACTION is task 9.2 — not done here.
    """
    read_state_tbl = read_state_tbl if read_state_tbl is not None else read_state_table

    if routed_item_ids is None:
        routed_item_ids = routed_item_ids_for(
            banker_id,
            tier,
            sessions_tbl=sessions_tbl,
            applications_tbl=applications_tbl,
            credit_tbl=credit_tbl,
            customers_tbl=customers_tbl,
        )

    if item_id not in routed_item_ids:
        return NOT_AUTHORIZED

    now = datetime.now(timezone.utc).isoformat()
    read_state_tbl.put_item(
        Item={"bankerId": banker_id, "itemId": item_id, "readAt": now}
    )
    return {"readState": "read"}


def _resolve_full_name(customer_id, raw, customers_tbl=None):
    """Resolve a human-friendly display name for a queue item (list display).

    For a candidate carrying a ``customerId``, look up the existing customer's
    ``fullName`` from ``bluey-customers``. For a walk-in (no ``customerId``, or a
    customer record without a ``fullName``), fall back to the raw record's
    ``applicantName`` / ``applicantData.fullName``, else ``"New applicant"``.

    The customers table is accepted as a parameter (defaulting to the module
    -level ``customers_table``) so this is testable without live AWS.
    """
    customers_tbl = customers_tbl if customers_tbl is not None else customers_table
    raw = raw or {}

    if customer_id:
        customer = customers_tbl.get_item(Key={"customerId": customer_id}).get("Item") or {}
        full_name = customer.get("fullName")
        if full_name:
            return full_name

    applicant_data = raw.get("applicantData") or {}
    return (
        raw.get("applicantName")
        or applicant_data.get("fullName")
        or "New applicant"
    )


def lambda_handler(event, context):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    groups = claims.get("cognito:groups", "")
    if "Bankers" not in groups:
        return _response(403, {"error": "forbidden — bankers only"})

    params = event.get("queryStringParameters") or {}
    body = json.loads(event.get("body") or "{}")
    action = params.get("action") or body.get("action")

    if action in ("list", "list_applications", "applications"):
        # 1. Resolve banker identity (R5.6). No identity => no items.
        identity = resolve_banker(claims)
        if identity is None:
            return _response(403, {"error": "identity_unresolved"})
        banker_id = identity.get("bankerId")
        tier = identity.get("tier")

        # 2. Gather candidates from sessions, applications, and credit.
        candidates = gather_candidates()

        # 3. Enrich each candidate (assignedBankerId, isWalkIn, hasLinkedApplication).
        for candidate in candidates:
            enrich(candidate)

        # 3b. Allocate general-pool items to the least-loaded general banker and
        #     persist ownership (banker-workload-allocation R6.1, R6.2, R6.3,
        #     R7.1, R7.2). Candidates are already enriched, so assignedBankerId
        #     reflects personalBanker plus any persisted Allocation_Record.
        #       - Scan bankers_table for the full banker set and derive the
        #         general-tier candidate ids.
        #       - Compute the current workload per general banker over the
        #         enriched candidates.
        #       - For each still-unassigned (General_Pool) candidate, select the
        #         least-loaded general banker, persist the allocation idempotently,
        #         mark the in-memory item owned, and bump the workload so a burst
        #         of unassigned items in one request spreads across general bankers
        #         rather than piling onto one (R7.2).
        bankers = [
            {"bankerId": b.get("bankerId"), "tier": b.get("tier")}
            for b in bankers_table.scan().get("Items", [])
        ]
        general_ids = [b["bankerId"] for b in bankers if b.get("tier") == "general"]
        workload_map = workload_by_banker(general_ids, candidates)
        for candidate in candidates:
            if not candidate.get("assignedBankerId"):
                selected = allocate(candidate, bankers, workload_map)
                if selected is not None:
                    put_allocation(candidate["itemId"], selected)
                    candidate["assignedBankerId"] = selected
                    workload_map[selected] = workload_map.get(selected, 0) + 1

        # 4. Classify + routing designation.
        for candidate in candidates:
            candidate["workCategory"] = classify(candidate)
            candidate["routing"] = routing_designation(candidate)

        # 5. Route-filter: keep only items visible to this banker (R5).
        filtered = [c for c in candidates if visible_to(c, banker_id, tier)]

        # 6. Read-join: resolve per-item read state for this banker (R4.1, R4.2).
        item_ids = [c["itemId"] for c in filtered]
        read_ids = read_state_for(banker_id, item_ids)
        resolve_read_state(filtered, read_ids)

        # 6b. Staleness: flag items pending > 24h against a single reference now
        #     (stale-query-notification R6.1, R6.5). Ordering is unaffected.
        now = datetime.now(timezone.utc)
        stale = stale_flags(filtered, now)

        # 7. Sort oldest-first (R2).
        ordered = sort_queue(filtered)

        # 8. Build clean, JSON-serializable session objects (no "raw" key).
        sessions = []
        for c in ordered:
            raw = c.get("raw") or {}
            customer_id = c.get("customerId")
            full_name = _resolve_full_name(customer_id, raw)
            sessions.append({
                "itemId": c.get("itemId"),
                "sessionId": c.get("sessionId"),
                "reference": c.get("reference"),
                "customerId": customer_id,
                "fullName": full_name,
                "accountType": c.get("accountType"),
                "workCategory": c.get("workCategory"),
                "routing": c.get("routing"),
                "status": raw.get("reviewStatus") or raw.get("status") or "pending_review",
                "readState": c.get("readState", "unread"),
                "isStale": bool(c.get("isStale", False)),
                "createdAt": c.get("createdAt"),
                "updatedAt": c.get("updatedAt"),
            })

        # 9. Unread count for the login notification (R3.3).
        unread = unread_count(ordered)

        # 10. Retain raw applications for backward compatibility.
        app_list = applications_table.scan().get("Items", [])

        return _response(200, {
            "bankerId": banker_id,
            "tier": tier,
            "unreadCount": unread,
            "staleCount": stale,
            "sessions": sessions,
            "applications": app_list,
        })

    if action == "detail":
        session_id = params.get("sessionId") or body.get("sessionId")
        reference = params.get("reference") or body.get("reference")

        item = None
        if session_id:
            item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")

        # Also find matching application
        application = None
        if reference:
            application = applications_table.get_item(Key={"reference": reference}).get("Item")
        elif session_id:
            # Look up application by sessionId if not keyed directly
            app_scan = applications_table.scan(
                FilterExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": session_id},
            )
            apps = app_scan.get("Items", [])
            if apps:
                application = apps[0]

        if not item and application:
            session_id = application.get("sessionId", session_id)
            if session_id:
                item = sessions_table.get_item(Key={"sessionId": session_id}).get("Item")

        if not item and not application:
            return _response(404, {"error": "not found"})

        item = item or {}
        customer_id = item.get("customerId") or (application.get("customerId") if application else None)
        customer = {}
        accounts = []
        credit_info = {}

        if customer_id:
            customer = customers_table.get_item(Key={"customerId": customer_id}).get("Item", {})
            acc_resp = accounts_table.query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            accounts = acc_resp.get("Items", [])
            cred_resp = credit_table.query(
                KeyConditionExpression="customerId = :cid",
                ExpressionAttributeValues={":cid": customer_id},
            )
            credit_info = cred_resp.get("Items", [{}])[0] if cred_resp.get("Items") else {}
        else:
            applicant_data = (application.get("applicantData", {}) if application else {})
            customer = {
                "fullName": applicant_data.get("fullName") or item.get("applicantName", "New applicant"),
                "phone": applicant_data.get("phone") or item.get("applicantPhone"),
                "email": applicant_data.get("email") or item.get("applicantEmail"),
                "idNumber": applicant_data.get("idNumber"),
                "address": applicant_data.get("address"),
            }

        documents = []
        doc_sid = session_id or (application.get("sessionId") if application else None)
        if doc_sid:
            resp = documents_table.query(
                KeyConditionExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": doc_sid},
            )
            for doc in resp.get("Items", []):
                s3_key = doc.get("s3Key") or f"documents/{doc_sid}/{doc.get('docType')}"
                if DOCUMENTS_BUCKET:
                    doc["viewUrl"] = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": DOCUMENTS_BUCKET, "Key": s3_key},
                        ExpiresIn=3600,
                    )
                documents.append(doc)

        return _response(200, {
            "session": item,
            "customer": customer,
            "documents": documents,
            "application": application,
            "accounts": accounts,
            "credit": credit_info,
        })

    if action in ("approve", "reject"):
        session_id = body.get("sessionId")
        reference = body.get("reference")
        now = datetime.now(timezone.utc).isoformat()
        decision_status = "approved" if action == "approve" else "rejected"
        app_status = "Approved" if action == "approve" else "Rejected"

        if session_id:
            sessions_table.update_item(
                Key={"sessionId": session_id},
                UpdateExpression="SET reviewStatus = :rs, reviewedAt = :ra",
                ExpressionAttributeValues={":rs": decision_status, ":ra": now},
            )

        if reference:
            applications_table.update_item(
                Key={"reference": reference},
                UpdateExpression="SET #s = :s, reviewedAt = :ra",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": app_status, ":ra": now},
            )
        elif session_id:
            # Also update any application linked to this session
            app_scan = applications_table.scan(
                FilterExpression="sessionId = :sid",
                ExpressionAttributeValues={":sid": session_id},
            )
            for app in app_scan.get("Items", []):
                applications_table.update_item(
                    Key={"reference": app["reference"]},
                    UpdateExpression="SET #s = :s, reviewedAt = :ra",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": app_status, ":ra": now},
                )

        return _response(200, {"result": action, "sessionId": session_id, "reference": reference})

    if action == "mark_read":
        # 1. Resolve banker identity (R5.6). No identity => not authorized.
        identity = resolve_banker(claims)
        if identity is None:
            return _response(403, {"error": "identity_unresolved"})

        # 2. Require the canonical queue-item id to mark.
        item_id = body.get("itemId") or params.get("itemId")
        if not item_id:
            return _response(400, {"error": "itemId required"})

        # 3. Authorize + idempotent write via the shared routing predicate (R4.4/R4.6/R4.7).
        result = mark_read(identity.get("bankerId"), identity.get("tier"), item_id)
        if result == NOT_AUTHORIZED:
            return _response(403, {"error": "not_authorized_for_item", "itemId": item_id})

        return _response(200, {"result": "mark_read", "itemId": item_id, "readState": "read"})

    return _response(400, {"error": "unknown action"})


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}
