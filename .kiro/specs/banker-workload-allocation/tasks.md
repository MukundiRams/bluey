# Implementation Plan: Banker Workload Allocation

## Overview

This plan implements the workload counter and general-pool allocation mechanism
against the real backend pipeline in `lambda/bluey-banker-api/handler.py`,
seeded by `scripts/seed_data.py`, and the CDK infra in
`infra/stacks/data_stack.py` / `infra/stacks/platform_stack.py`.

Work is ordered so each step builds on the previous: first the module-level
`assignments_table` resource, then the pure helpers (`workload_count`,
`workload_by_banker`, `allocate`) with their property tests, then the injectable
persistence helpers (`get_allocation`/`put_allocation`), then the `enrich`
extension, then the visibility property over the UNCHANGED `visible_to`, then
wiring allocation into the `list` action, and finally the CDK infra plus its
Template assertion test. Every step ends by integrating the new code into the
pipeline so no orphaned code is left behind.

All property tests follow the existing suite conventions: the hyphenated
`bluey-banker-api` handler is loaded via
`importlib.util.spec_from_file_location` (the `_load_handler()` pattern from
`tests/test_banker_classify_properties.py` / `tests/test_banker_ordering_property.py`),
AWS region env vars are set before loading, and Hypothesis runs at
`@settings(max_examples=...)` **>= 100** iterations per the design's Testing
Strategy. Persistence properties use a `moto` / in-memory fake
`Assignments_Table` injected via the `assignments_tbl` parameter. Property tests
are REQUIRED (not marked optional).

## Tasks

- [x] 1. Add the module-level assignments table resource
  - In `lambda/bluey-banker-api/handler.py`, add
    `assignments_table = dynamodb.Table(os.environ.get("ASSIGNMENTS_TABLE", "bluey-banker-assignments"))`
    alongside the existing module-level `Table(...)` bindings, reading the
    `ASSIGNMENTS_TABLE` env var with a sensible default (mirrors
    `read_state_table`).
  - Confirm the module still imports cleanly (the property-test `_load_handler`
    loader must continue to succeed with only region env vars set).
  - _Requirements: 4.2, 4.4_

- [x] 2. Implement the workload counter and its property test
  - [x] 2.1 Implement `workload_count(banker_id, items)`
    - Add the pure helper returning the number of items where
      `item.get("assignedBankerId") == banker_id`; a falsy/absent
      `assignedBankerId` matches no banker. No mutation, no I/O; result is an
      integer in `[0, len(items)]`.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 8.1_

  - [x] 2.2 Write property test for `workload_count`
    - **Property 1: Workload count is a correct assigned-pending count**
    - **Validates: Requirements 1.1, 1.4, 1.5, 1.6, 7.3, 8.1**
    - New file `tests/test_banker_workload_properties.py` using the
      `_load_handler()` loader pattern. Assert the count equals the independently
      computed number of matching items, lies in `[0, len(items)]`, is invariant
      under list permutation, ignores items lacking a truthy `assignedBankerId`,
      and does not mutate `items`. `@settings(max_examples>=100)`.

- [x] 3. Implement the workload map and its property test
  - [x] 3.1 Implement `workload_by_banker(banker_ids, items)`
    - Add the pure helper returning a mapping with exactly one entry per distinct
      supplied `bankerId`, each value equal to `workload_count(banker_id, items)`
      (0 when none assigned). No mutation, no I/O, deterministic.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.2 Write property test for `workload_by_banker`
    - **Property 2: Workload map is consistent with the counter and keyed by the supplied ids**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    - Add to `tests/test_banker_workload_properties.py`. Assert the key set equals
      the set of supplied ids (duplicates collapse), each value equals
      `workload_count(b, items)`, neither argument is mutated, and equal inputs
      yield equal results. `@settings(max_examples>=100)`.

- [x] 4. Implement the allocator and its property tests
  - [x] 4.1 Implement `allocate(item, bankers, workload_map)`
    - Add the pure selector: if the item already has a truthy `assignedBankerId`
      return it unchanged; otherwise consider only `tier == "general"` bankers and
      return `min(general, key=lambda b: (workload_map.get(b["bankerId"], 0), b["bankerId"]))`,
      returning `None` when there is no general banker. Missing map entry treated
      as `0`. No mutation, no I/O, deterministic.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.5, 7.1, 7.2, 8.2, 8.3_

  - [x] 4.2 Write property test for allocator selection
    - **Property 3: Allocation selects the least-loaded general banker deterministically**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 6.5, 7.1, 7.2, 8.2, 8.3**
    - New file `tests/test_banker_allocate_properties.py` using the
      `_load_handler()` loader. Strategies cover mixed general/premium tiers
      (including premium-only and empty banker sets), a small `bankerId` pool to
      force ties, and workload maps over a subset of ids (some candidates absent
      => treated as 0) with load differences > 1. Assert the result equals the
      independently computed `min(general, key=(load, id))`, `None` when no
      general banker, premium never selected, and arguments unmutated.
      `@settings(max_examples>=100)`.

  - [x] 4.3 Write property test for already-owned no-op
    - **Property 4: Allocation is a no-op for an already-owned item**
    - **Validates: Requirements 3.5, 3.6**
    - Add to `tests/test_banker_allocate_properties.py`. For any item with a
      truthy `assignedBankerId` and any bankers/workload_map, assert `allocate`
      returns that existing id unchanged without consulting the map or mutating
      arguments. `@settings(max_examples>=100)`.

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement injectable allocation persistence helpers and their property test
  - [x] 6.1 Implement `get_allocation(item_id, assignments_tbl=None)`
    - Add the injectable read helper defaulting to the module-level
      `assignments_table`; `get_item(Key={"itemId": item_id})`, return the
      record's `assignedBankerId` when present and truthy, else `None`; return
      `None` (no error) when no record exists.
    - _Requirements: 4.2, 4.4, 8.4, 8.5_

  - [x] 6.2 Implement `put_allocation(item_id, banker_id, assignments_tbl=None)`
    - Add the injectable write helper defaulting to the module-level
      `assignments_table`; conditional `put_item` with
      `ConditionExpression="attribute_not_exists(itemId)"` writing
      `{"itemId": item_id, "assignedBankerId": banker_id}`; catch
      `ClientError` / `ConditionalCheckFailedException` and treat as success
      (first-writer-wins, idempotent, non-reallocating).
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 6.3 Write property test for allocation persistence round-trip/idempotence
    - **Property 5: Allocation persistence round-trips and is idempotent / non-reallocating**
    - **Validates: Requirements 4.1, 4.3, 4.5, 8.5**
    - New file `tests/test_banker_allocation_persistence_properties.py`. Provide a
      `moto`/in-memory fake `Assignments_Table` (dict-backed `get_item`/`put_item`
      honoring `attribute_not_exists(itemId)` and raising
      `ConditionalCheckFailedException` on conflict) injected via
      `assignments_tbl`. Assert write->read round-trips the `bankerId`, repeated
      identical writes equal a single write, a second write with a different
      banker leaves the stored id unchanged, and `get_allocation` returns `None`
      without raising for an absent record. `@settings(max_examples>=100)`.

- [x] 7. Extend enrichment with allocation precedence and its property test
  - [x] 7.1 Extend `enrich(item, customers_tbl=None, assignments_tbl=None)`
    - Add the `assignments_tbl` parameter (defaulting to the module-level table).
      Keep the existing `personalBanker.bankerId` branch; when
      `_resolve_assigned_banker_id` yields `None`, call
      `get_allocation(item["itemId"], assignments_tbl)` and set `assignedBankerId`
      to a truthy returned id, else leave it `None`. `isWalkIn` /
      `hasLinkedApplication` unchanged. Backward-compatible signature.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 8.4_

  - [x] 7.2 Write property test for enrich precedence
    - **Property 6: Enrich resolves the assigned banker by precedence, robustly**
    - **Validates: Requirements 5.1, 5.2, 5.3, 8.4**
    - New file `tests/test_banker_enrich_allocation_properties.py` with injected
      fake customers and assignments tables. Assert precedence
      personalBanker.bankerId > Allocation_Record.assignedBankerId > None, that a
      record with a missing/falsy `assignedBankerId` is treated as no allocation
      (stays `None`), and that a present `personalBanker.bankerId` is never
      overridden by an allocation lookup. `@settings(max_examples>=100)`.

- [x] 8. Add the allocated-item visibility property test (UNCHANGED predicate)
  - [x] 8.1 Write property test for allocated-item visibility
    - **Property 7: An allocated item is visible only to its owner (unchanged predicate)**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    - New file `tests/test_banker_allocation_visibility_properties.py` exercising
      the existing UNCHANGED `visible_to` / `routing_designation`. For an item
      whose `assignedBankerId` is set to a selected general banker, assert
      `visible_to` is `True` for that banker at any tier, `False` for every other
      general banker at `tier == "general"`, `routing_designation` is `"assigned"`,
      and that an item allocated vs one assigned via `personalBanker` with the
      same `assignedBankerId` are indistinguishable to `visible_to`. Do NOT modify
      `visible_to`. `@settings(max_examples>=100)`.

- [x] 9. Wire allocation into the list action and add the integration test
  - [x] 9.1 Wire allocation into the `list` action of `lambda_handler`
    - After enriching every candidate (now allocation-aware via Component 6) and
      before the route-filter: scan `bankers_table` for `tier == "general"`
      bankers, compute `workload_map = workload_by_banker(general_ids, candidates)`;
      for each `General_Pool` candidate (falsy `assignedBankerId`) compute
      `selected = allocate(item, bankers, workload_map)`, and when `selected` is
      not `None` call `put_allocation(item["itemId"], selected)`, set
      `item["assignedBankerId"] = selected`, and increment
      `workload_map[selected]` so a burst of unassigned items in one request
      spreads across general bankers. Leave classify/routing, `visible_to`,
      read-state, staleness, ordering, and payload build unchanged.
    - _Requirements: 6.1, 6.2, 6.3, 7.1, 7.2_

  - [x] 9.2 Write moto-backed integration test for the end-to-end list allocation flow
    - Extend `tests/test_banker_api_integration.py`: add the new
      `bluey-banker-assignments` table (PK `itemId`) to `_create_tables`, set the
      `ASSIGNMENTS_TABLE` env var in the `handler` fixture, and seed a
      walk-in/unassigned pending item with no `personalBanker` plus two general
      bankers. Assert the first `list` allocates the item to a general banker and
      writes an `Allocation_Record`, and a second `list` routes the item only to
      the selected banker (invisible to the other general banker). 1-2 examples;
      this is an integration test, not a property test.
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Add the assignments table infra and CDK Template assertion test
  - [x] 11.1 Add the `bluey-banker-assignments` table in `data_stack.py`
    - In `BlueyDataStack`, add
      `self.tables["assignments"] = table("bluey-banker-assignments", "itemId")`
      (partition key `itemId`, no sort key), inheriting the shared
      `PAY_PER_REQUEST` billing and `RETAIN`-outside-`dev` removal policy.
    - _Requirements: 4.1, 4.2_

  - [x] 11.2 Grant access and set the env var in `platform_stack.py`
    - Add `data_stack.tables["assignments"].grant_read_write_data(banker_role)`
      alongside the other banker-role grants, and
      `self.banker_fn.add_environment("ASSIGNMENTS_TABLE", data_stack.tables["assignments"].table_name)`
      alongside the existing `READ_STATE_TABLE` env wiring.
    - _Requirements: 4.1, 4.4_

  - [x] 11.3 Write CDK Template assertion test for the assignments table
    - New file `tests/test_banker_assignments_table.py` mirroring
      `tests/test_banker_read_state_table.py`: synthesize `BlueyDataStack` and
      assert exactly one `AWS::DynamoDB::Table` named
      `bluey-banker-assignments-test` with `BillingMode: PAY_PER_REQUEST`,
      `KeySchema` `[{itemId, HASH}]`, and `AttributeDefinitions` `[{itemId, S}]`.
    - _Requirements: 4.1, 4.2_

- [x] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass (run the property tests at >= 100 iterations), ask the
    user if questions arise.

## Notes

- Property tests (2.2, 3.2, 4.2, 4.3, 6.3, 7.2, 8.1) are REQUIRED and are NOT
  marked optional, per the design's Testing Strategy.
- Every property test uses the `_load_handler()` importlib file-path loader
  (hyphenated `bluey-banker-api` directory) and runs at
  `@settings(max_examples>=100)`.
- Persistence and enrich property tests inject a `moto`/in-memory fake
  `Assignments_Table` (and fake customers table) so no live AWS is required.
- The `visible_to` predicate is UNCHANGED; Property 7 verifies routing purely by
  setting `assignedBankerId`.
- Each task references specific requirement clauses and, where applicable, the
  design property number it implements for traceability.
