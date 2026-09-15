# Requirements Document

## Introduction

The Stale Query Notification feature adds an in-app notification to the banker
portal when a pending queue item has gone unreviewed for more than 24 hours. It
extends the existing `banker-query-triage` feature (the `list` action in
`lambda/bluey-banker-api/handler.py` and the portal in `index.html`) without
introducing new infrastructure — no new tables, endpoints, or AWS resources.

The backend derives a per-item stale flag and an aggregate stale count using new
pure helpers (`stale_age`, `is_stale`, `stale_flags`) that take an injected
reference `now` so they are deterministic and property-testable. Staleness is
computed from the same `Waiting_Time` derivation the ordering already uses
(`createdAt`, falling back to `updatedAt`, via `_parse_timestamp`). "Reviewed"
means the item has left the pending queue (approved/rejected); staleness is
orthogonal to per-banker read/unread state. The portal surfaces staleness as an
aggregate banner segment and a per-row badge. The local stub is updated so the
feature is exercisable in the browser without AWS.

## Glossary

- **Banker_API**: The `list` action in `lambda/bluey-banker-api/handler.py` that
  gathers, routes, and serializes the pending queue for the calling banker.
- **Stale_Age_Helper**: The pure function `stale_age(item, now)` returning a
  `timedelta` age or `None` for undated items.
- **Stale_Predicate**: The pure function `is_stale(item, now, threshold)`
  returning a boolean staleness verdict for a single item.
- **Stale_Flags_Helper**: The pure function `stale_flags(items, now, threshold)`
  that sets `isStale` on each item and returns the aggregate stale count.
- **Waiting_Time**: The timestamp an item has been waiting since, derived from
  `createdAt`, falling back to `updatedAt`, parsed by the existing
  `_parse_timestamp`.
- **Stale_Threshold**: The staleness boundary, a `timedelta` of 24 hours
  (`STALE_THRESHOLD`).
- **Undated_Item**: A queue item whose `createdAt` and `updatedAt` are both
  absent, empty, or unparseable (`_parse_timestamp` returns `None` for both).
- **Read_State**: The per-banker `readState` field (`"read"` | `"unread"`)
  tracked independently of staleness.
- **Portal**: The banker portal front end in `index.html`.
- **Render_Notice**: The `renderNotice(unreadCount, staleCount)` function in the
  Portal that renders the unread and stale banner segments.
- **Row_Template**: The Portal session-row template that renders per-item badges.
- **Local_Stub**: The `_build_list_payload` function in
  `scripts/local_banker_stub.py` used for AWS-free browser testing.

## Requirements

### Requirement 1: Compute item age relative to a reference time

**User Story:** As a backend developer, I want a pure helper that computes how
long an item has been waiting relative to an injected reference time, so that
staleness is deterministic and testable without AWS.

#### Acceptance Criteria

1. WHERE a queue item has a parseable `createdAt`, THE Stale_Age_Helper SHALL return the difference between `now` and the parsed `createdAt`.
2. IF a queue item has no parseable `createdAt` but has a parseable `updatedAt`, THEN THE Stale_Age_Helper SHALL return the difference between `now` and the parsed `updatedAt`.
3. IF a queue item is an Undated_Item, THEN THE Stale_Age_Helper SHALL return `None`.
4. THE Stale_Age_Helper SHALL derive Waiting_Time using the existing `_parse_timestamp` function.
5. THE Stale_Age_Helper SHALL return equal results for equal `(item, now)` inputs across repeated calls without mutating the `item` argument or performing input/output.

### Requirement 2: Determine whether a single item is stale

**User Story:** As a backend developer, I want a boolean staleness predicate for
a single item, so that each item can be flagged consistently against the 24 hour
threshold.

#### Acceptance Criteria

1. WHEN an item is dated and its age strictly exceeds the Stale_Threshold, THE Stale_Predicate SHALL return `True`.
2. WHEN an item is dated and its age equals the Stale_Threshold, THE Stale_Predicate SHALL return `False`.
3. WHEN an item is dated and its age is less than the Stale_Threshold, THE Stale_Predicate SHALL return `False`.
4. IF an item is an Undated_Item, THEN THE Stale_Predicate SHALL return `False`.
5. IF an item has a Waiting_Time later than `now` (future-dated), THEN THE Stale_Predicate SHALL return `False`.
6. WHERE a caller supplies a custom threshold, THE Stale_Predicate SHALL use the supplied threshold in place of the default 24 hour Stale_Threshold.
7. THE Stale_Predicate SHALL return a `bool` for every input dict and SHALL return equal results for equal `(item, now, threshold)` inputs without mutating the `item` argument.

### Requirement 3: Flag items and aggregate the stale count

**User Story:** As a backend developer, I want a helper that flags every item
and returns the aggregate stale count in one pass, so that the list payload can
report staleness consistently.

#### Acceptance Criteria

1. WHEN the Stale_Flags_Helper processes a list of items, THE Stale_Flags_Helper SHALL set `item["isStale"]` to the Stale_Predicate result for each item.
2. WHEN the Stale_Flags_Helper returns a count, THE Stale_Flags_Helper SHALL return a value equal to the number of items whose `isStale` is `True`.
3. THE Stale_Flags_Helper SHALL return a count greater than or equal to 0 and less than or equal to the number of items in the list.
4. THE Stale_Flags_Helper SHALL mutate only the `isStale` key of each item and SHALL perform no input/output.

### Requirement 4: Independence of staleness from read state

**User Story:** As a banker, I want an old un-actioned item to stay flagged stale
even after I open it, so that genuinely un-actioned work is never hidden.

#### Acceptance Criteria

1. THE Stale_Predicate SHALL derive its result only from `createdAt`, `updatedAt`, and `now`.
2. WHEN an item's Read_State changes between `"read"` and `"unread"`, THE Stale_Predicate SHALL return the same result for that item at a fixed `now`.

### Requirement 5: Staleness consistency with queue ordering

**User Story:** As a backend developer, I want staleness to use the same
Waiting_Time the ordering uses, so that flagging is consistent with the
oldest-first queue order.

#### Acceptance Criteria

1. THE Stale_Age_Helper SHALL derive Waiting_Time from the same `createdAt`-then-`updatedAt` timestamp source that the ordering `sort_key` uses.
2. WHEN two items are dated and one is older than the other at a fixed `now`, THE Stale_Age_Helper SHALL return an age for the older item that is greater than or equal to the age of the newer item.
3. WHEN two items are dated, one is older than the other, and the newer item is stale at a fixed `now`, THE Stale_Predicate SHALL also return `True` for the older item.

### Requirement 6: Surface staleness in the list payload

**User Story:** As a portal consumer, I want the `list` response to report an
aggregate stale count and a per-session stale flag, so that the front end can
render the notification.

#### Acceptance Criteria

1. WHEN the Banker_API builds a `list` response, THE Banker_API SHALL capture a single reference `now` from `datetime.now(timezone.utc)` for the request.
2. WHEN the Banker_API builds a `list` response, THE Banker_API SHALL include a top-level `staleCount` integer alongside the existing `unreadCount`.
3. WHEN the Banker_API serializes each session object, THE Banker_API SHALL include an `isStale` boolean.
4. THE Banker_API SHALL set the top-level `staleCount` equal to the number of session objects whose `isStale` is `True`.
5. THE Banker_API SHALL compute staleness only over items already route-filtered by `visible_to`.
6. WHEN the Banker_API returns the ordered sessions, THE Banker_API SHALL preserve the existing oldest-first ordering unchanged.
7. THE Banker_API SHALL set each session object's `isStale` to a `bool` that is never absent and never null.

### Requirement 7: Render the stale notification in the portal

**User Story:** As a banker, I want to see an aggregate banner and per-row badges
for stale items, so that I can identify pending work older than 24 hours.

#### Acceptance Criteria

1. WHEN `staleCount` is greater than 0, THE Render_Notice SHALL display a stale message segment indicating items waiting over 24 hours.
2. WHEN `staleCount` equals 0, THE Render_Notice SHALL display no stale message segment.
3. WHERE a session's `isStale` is truthy, THE Row_Template SHALL render a `badge stale` element for that row.
4. THE Portal SHALL render rows in the server-provided order without performing client-side re-sorting.

### Requirement 8: Exercise the feature locally without AWS

**User Story:** As a developer, I want the local stub to emit staleness fields
and seed an old item, so that I can verify the notification in a browser without
AWS.

#### Acceptance Criteria

1. WHEN the Local_Stub builds a list payload, THE Local_Stub SHALL compute each item's `isStale` using a 24 hour threshold against `datetime.now(timezone.utc)` with the same `createdAt`-then-`updatedAt` fallback.
2. WHEN the Local_Stub builds a list payload, THE Local_Stub SHALL include a top-level `staleCount` and a per-session `isStale` flag.
3. THE Local_Stub SHALL seed at least one item with a `createdAt` more than 24 hours before the current time so the stale banner and badge are visible.

### Requirement 9: Tolerant handling of malformed and edge-case timestamps

**User Story:** As a backend developer, I want staleness to handle missing,
malformed, future, and timezone-naive timestamps without errors, so that the
list action remains robust.

#### Acceptance Criteria

1. IF an item's `createdAt` and `updatedAt` are absent, empty, or not ISO-8601, THEN THE Stale_Predicate SHALL return `False` without raising an error.
2. IF an item's Waiting_Time is later than `now`, THEN THE Stale_Predicate SHALL return `False` without raising an error.
3. WHEN a stored timestamp is timezone-naive, THE Stale_Age_Helper SHALL compare it against the timezone-aware `now` as UTC without raising a mixed aware/naive error.
