# Requirements Document

## Introduction

The Banker Workload Allocation feature adds two capabilities to the banker
portal backend (`lambda/bluey-banker-api/handler.py`, seeded by
`scripts/seed_data.py`):

1. A **workload counter** that, for a given banker, computes how many pending
   queue items are currently assigned to that banker. A queue item is any
   pending record produced by the existing pipeline — a pending session
   (`bluey-sessions`), a pending application (`bluey-applications`), or a
   pending credit record (`bluey-credit`) — gathered by `gather_candidates` and
   enriched by `enrich`. Items whose review has been resolved (approved /
   rejected, i.e. no longer pending) are excluded because they leave the queue.

2. An **allocation mechanism** for General_Pool items (applications from
   Unassigned_Customers with no `personalBanker`, and Walk_In_Applicants with no
   `customerId`). When such an item has no Assigned_Banker, allocation assigns it
   to the least-loaded General_Tier banker using the workload counter, breaking
   ties by lowest `bankerId` lexicographically. Premium_Tier bankers are excluded
   from General_Pool allocation, matching the existing `visible_to` routing.

The feature is additive and follows the repository's established pure-function +
injectable-table pattern (mirroring `gather_candidates`, `enrich`, `visible_to`,
`stale_flags`, etc.) so the new logic is property-testable without live AWS.
Allocation is persisted in a **new `bluey-banker-assignments` table** keyed by
`itemId` (partition) with an `assignedBankerId` attribute; no schema change is
made to `bluey-customers`, `bluey-bankers`, or the queue-source tables. Once an
item is allocated, it becomes owned by that banker: allocation records are read
back during enrichment so an allocated item is routed as an Assigned_Item
(visible only to its owner) rather than remaining in the General_Pool.

This feature does not modify the approve/reject decision flow, read/unread state,
staleness, or queue ordering. It reuses the real backend data sources and MUST
NOT depend on `scripts/local_banker_stub.py` as a source of truth.

## Glossary

- **Banker_API**: The request handler in `lambda/bluey-banker-api/handler.py`
  that gathers, enriches, routes, and serializes the pending queue.
- **Queue_Item**: A pending candidate produced by `gather_candidates` from a
  pending session, application, or credit record, carrying a canonical `itemId`.
- **Pending_Item**: A Queue_Item whose source record carries a pending marker
  (`reviewStatus`/`status` in `{"Pending", "pending", "pending_review"}`), i.e.
  a Queue_Item that has not been resolved by approve/reject.
- **Resolved_Item**: A source record whose review has been decided
  (approved/rejected) and therefore no longer appears as a Pending_Item.
- **Banker**: A record in `bluey-bankers` with `bankerId`, `name`, `email`, and
  `tier`.
- **General_Tier**: A Banker whose `tier` equals `"general"`.
- **Premium_Tier**: A Banker whose `tier` equals `"premium"`.
- **Assigned_Banker**: The `bankerId` resolved for a Queue_Item, either from the
  owning customer's `personalBanker.bankerId` or from an Allocation_Record; set
  on the item by `enrich` as `assignedBankerId`.
- **Unassigned_Customer**: A customer whose record has no `personalBanker` (or a
  `personalBanker` without a `bankerId`).
- **Walk_In_Applicant**: A Queue_Item with no `customerId`.
- **General_Pool**: The set of Queue_Items with no Assigned_Banker
  (`assignedBankerId` is `None`/absent); today visible to every General_Tier
  banker via `visible_to`.
- **Assigned_Item**: A Queue_Item whose `assignedBankerId` is truthy; visible
  only to that banker via `visible_to`.
- **Workload_Counter**: The pure function `workload_count(banker_id, items)`
  returning the number of Pending_Items whose `assignedBankerId` equals
  `banker_id`.
- **Workload_Map**: The pure function `workload_by_banker(banker_ids, items)`
  returning a mapping from each `bankerId` to its Workload_Counter value.
- **Allocator**: The pure function `allocate(item, bankers, workload_map)` that
  selects the owning General_Tier banker for a single General_Pool item.
- **Allocation_Record**: A record in the Assignments_Table keyed by `itemId`
  with an `assignedBankerId` attribute, persisting an allocation.
- **Assignments_Table**: The new `bluey-banker-assignments` DynamoDB table
  storing Allocation_Records, keyed by `itemId` (partition key).
- **Least_Loaded_Banker**: Among a candidate set of General_Tier bankers, the
  banker with the smallest Workload_Counter value; ties broken by the smallest
  `bankerId` compared lexicographically.

## Requirements

### Requirement 1: Count a banker's assigned pending workload

**User Story:** As a backend developer, I want a pure helper that counts how many
pending items are assigned to a given banker, so that allocation can compare
banker loads deterministically and without live AWS.

#### Acceptance Criteria

1. WHEN the Workload_Counter is given a `banker_id` and a list of enriched Queue_Items, THE Workload_Counter SHALL return the number of items whose `assignedBankerId` equals the given `banker_id`.
2. THE Workload_Counter SHALL count an item only when the item is a Pending_Item.
3. IF an item is a Resolved_Item, THEN THE Workload_Counter SHALL exclude that item from the count.
4. THE Workload_Counter SHALL count Pending_Items sourced from `bluey-sessions`, `bluey-applications`, and `bluey-credit` using the same `assignedBankerId` field set by `enrich`.
5. THE Workload_Counter SHALL return an integer greater than or equal to 0 and less than or equal to the number of items in the list.
6. THE Workload_Counter SHALL return equal results for equal `(banker_id, items)` inputs without mutating the `items` argument or performing input/output.

### Requirement 2: Compute the workload for a set of bankers

**User Story:** As a backend developer, I want a helper that returns each
banker's workload in a single pass, so that the allocator can pick the
least-loaded banker consistently.

#### Acceptance Criteria

1. WHEN the Workload_Map is given a collection of `bankerId` values and a list of enriched Queue_Items, THE Workload_Map SHALL return a mapping that contains exactly one entry per supplied `bankerId`.
2. THE Workload_Map SHALL set each banker's mapped value equal to the Workload_Counter result for that `bankerId` over the same list of items.
3. WHERE a supplied `bankerId` has no assigned Pending_Items, THE Workload_Map SHALL map that `bankerId` to 0.
4. THE Workload_Map SHALL return equal results for equal `(banker_ids, items)` inputs without mutating either argument or performing input/output.

### Requirement 3: Select the owning banker for a general-pool item

**User Story:** As a banker manager, I want an unassigned or walk-in application
to be allocated to the least-loaded general banker, so that new work is
distributed evenly across the general team.

#### Acceptance Criteria

1. WHEN the Allocator is given a General_Pool item, the set of Bankers, and a Workload_Map, THE Allocator SHALL return the `bankerId` of the Least_Loaded_Banker among the General_Tier bankers.
2. WHEN two or more General_Tier bankers share the smallest Workload_Counter value, THE Allocator SHALL return the banker whose `bankerId` is smallest compared lexicographically.
3. THE Allocator SHALL consider only General_Tier bankers as allocation candidates.
4. IF no General_Tier banker exists, THEN THE Allocator SHALL return `None`.
5. IF the item already has a truthy `assignedBankerId`, THEN THE Allocator SHALL return that existing `assignedBankerId` unchanged.
6. THE Allocator SHALL return equal results for equal `(item, bankers, workload_map)` inputs without mutating its arguments or performing input/output.

### Requirement 4: Persist an allocation as an assignment record

**User Story:** As a backend developer, I want a chosen allocation written to a
dedicated assignments table, so that the assignment is durable and can be read
back on later requests.

#### Acceptance Criteria

1. WHEN an allocation is persisted for a Queue_Item, THE Banker_API SHALL write an Allocation_Record to the Assignments_Table containing the item's `itemId` and the selected `assignedBankerId`.
2. THE Assignments_Table SHALL use `itemId` as its partition key so at most one Allocation_Record exists per Queue_Item.
3. WHEN an Allocation_Record already exists for an `itemId`, THE Banker_API SHALL leave the existing `assignedBankerId` unchanged rather than reallocating the item.
4. THE persistence function SHALL accept the Assignments_Table resource as an injectable parameter defaulting to the module-level table so it is testable without live AWS.
5. WHEN the persistence function writes an Allocation_Record for the same `(itemId, assignedBankerId)` more than once, THE persistence function SHALL leave the stored Allocation_Record equal to a single write of that pair.

### Requirement 5: Resolve an assigned banker from allocation records during enrichment

**User Story:** As a backend developer, I want enrichment to honor a persisted
allocation, so that an allocated item is routed as owned work on later requests.

#### Acceptance Criteria

1. WHEN `enrich` resolves a Queue_Item's Assigned_Banker and the owning customer has a `personalBanker.bankerId`, THE Banker_API SHALL set `assignedBankerId` to that `personalBanker.bankerId`.
2. IF a Queue_Item has no `personalBanker.bankerId` but an Allocation_Record exists for the item's `itemId`, THEN THE Banker_API SHALL set `assignedBankerId` to the Allocation_Record's `assignedBankerId`.
3. IF a Queue_Item has neither a `personalBanker.bankerId` nor an Allocation_Record, THEN THE Banker_API SHALL leave `assignedBankerId` as `None`.
4. THE allocation-lookup function SHALL accept the Assignments_Table resource as an injectable parameter defaulting to the module-level table so enrichment stays testable without live AWS.

### Requirement 6: Allocate general-pool items so they become owned work

**User Story:** As a general banker, I want an allocated item to appear only in
the assigned banker's queue, so that two general bankers do not both action the
same application.

#### Acceptance Criteria

1. WHEN the Banker_API allocates a General_Pool item to a banker, THE Banker_API SHALL cause subsequent `visible_to` evaluation for that item to treat the item as an Assigned_Item owned by the selected banker.
2. WHEN an item is allocated to a banker, THE Banker_API SHALL cause `visible_to(item, other_banker_id, "general")` to return `False` for every General_Tier banker other than the selected banker.
3. WHEN an item is allocated to a banker, THE Banker_API SHALL cause `visible_to(item, selected_banker_id, tier)` to return `True` for the selected banker.
4. THE Banker_API SHALL leave the existing `visible_to` predicate behavior unchanged for items that carry a `personalBanker`-derived Assigned_Banker.
5. THE Banker_API SHALL exclude Premium_Tier bankers from General_Pool allocation, preserving the existing rule that Premium_Tier bankers see only their own Assigned_Items.

### Requirement 7: Consistency between workload and allocation

**User Story:** As a banker manager, I want repeated allocations to keep loads
balanced, so that no single general banker is overloaded while others are idle.

#### Acceptance Criteria

1. WHEN a General_Pool item is allocated using a Workload_Map, THE Allocator SHALL select a banker whose Workload_Counter value is less than or equal to the Workload_Counter value of every other General_Tier banker in that Workload_Map.
2. WHEN two General_Tier bankers have Workload_Counter values differing by more than 1 and a single General_Pool item is allocated, THE Allocator SHALL not select the banker with the strictly larger Workload_Counter value.
3. THE Workload_Counter value for a banker SHALL equal the number of that banker's assigned Pending_Items regardless of the order in which items appear in the list.

### Requirement 8: Robust handling of missing and malformed inputs

**User Story:** As a backend developer, I want workload counting and allocation
to tolerate absent or malformed fields without errors, so that the list action
remains robust against real data.

#### Acceptance Criteria

1. IF a Queue_Item has no `assignedBankerId` field, THEN THE Workload_Counter SHALL treat the item as unassigned and SHALL NOT count it for any banker.
2. IF the list of Bankers contains no General_Tier banker, THEN THE Allocator SHALL return `None` without raising an error.
3. IF the Workload_Map has no entry for a candidate banker, THEN THE Allocator SHALL treat that banker's workload as 0.
4. IF an Allocation_Record is missing its `assignedBankerId` attribute, THEN THE Banker_API SHALL treat the item as having no persisted allocation and SHALL leave `assignedBankerId` as `None`.
5. IF the Assignments_Table has no Allocation_Record for an `itemId`, THEN THE allocation-lookup function SHALL return `None` without raising an error.
