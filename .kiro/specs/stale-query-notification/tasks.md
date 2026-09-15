# Implementation Plan: Stale Query Notification

## Overview

This plan extends the existing `banker-query-triage` feature with staleness
notification. It adds three pure helpers (`stale_age`, `is_stale`, `stale_flags`)
to `lambda/bluey-banker-api/handler.py`, wires them into the `list` action,
surfaces `staleCount` / `isStale` in the payload, renders the notification in
`index.html`, and updates the local stub. Property-based tests (Hypothesis)
encode P1–P6 over the pure helpers; a structural test encodes P7 over the portal.

Each task builds on the previous ones and ends by wiring into the existing
pipeline, leaving no orphaned code. Tests are written close to the code they
validate so regressions surface early.

## Tasks

- [x] 1. Add pure staleness helpers to the banker-api handler
  - [x] 1.1 Implement `STALE_THRESHOLD` and `stale_age(item, now)`
    - Add `STALE_THRESHOLD = timedelta(hours=24)` near the other module constants
    - Implement `stale_age(item, now)` deriving Waiting_Time via the existing
      `_parse_timestamp`: parse `createdAt`, fall back to `updatedAt`; return
      `None` when both are unparseable, else `now - waiting_time`
    - No mutation of `item`, no I/O; deterministic given `(item, now)`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.1, 9.3_

  - [x] 1.2 Implement `is_stale(item, now, threshold=STALE_THRESHOLD)`
    - Return `True` iff `stale_age(item, now)` is not `None` AND strictly
      greater than `threshold`; `False` otherwise (undated, at/below threshold,
      future-dated)
    - Always return a `bool`; total over all dicts; honor a caller-supplied
      threshold
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 4.1, 9.1, 9.2_

  - [x] 1.3 Implement `stale_flags(items, now, threshold=STALE_THRESHOLD)`
    - Set `item["isStale"] = is_stale(item, now, threshold)` for each item in
      one pass and return the running count of stale items
    - Guarantee returned count equals `sum(1 for i in items if i["isStale"])`
      and `0 <= count <= len(items)`; mutate only the `isStale` key; no I/O
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x]* 1.4 Write example/unit tests for the pure helpers
    - Cover `stale_age`/`is_stale`/`stale_flags`: clearly-old (48h) stale,
      fresh not-stale, exact 24h boundary NOT stale, just-past boundary stale,
      `createdAt`→`updatedAt` fallback, undated ⇒ not stale, future-dated ⇒
      not stale, and the mixed-list count from the design's example
    - Load the hyphenated handler via `importlib.util.spec_from_file_location`
      with AWS region env-var setup (as in the existing property tests)
    - _Requirements: 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2_

- [x] 2. Property-based tests for the pure helpers (P1–P6)
  - [x]* 2.1 Set up the property test module and strategies
    - Create `tests/test_banker_stale_properties.py` using the loader/env-var
      pattern from `tests/test_banker_read_state_properties.py`
    - Build strategies that draw a tz-aware `now` and item dicts with
      `createdAt`/`updatedAt` offset around `now` (including exactly-threshold
      and future offsets) plus undated variants (`None`/empty/garbage strings)
    - _Requirements: 1.1, 1.5_

  - [x]* 2.2 Encode P1 — Threshold monotonicity / strictness
    - **Property 1: Threshold monotonicity / strictness**
    - `is_stale(i, now)` is `True` iff dated and age strictly > 24h; item at
      exactly `now - 24h` is NOT stale, at `now - 24h - ε` IS stale
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.6, 9.2**

  - [x]* 2.3 Encode P2 — Undated items are never stale
    - **Property 2: Undated items are never stale**
    - For all undated items and every `now`, `is_stale` is `False`
    - **Validates: Requirements 1.3, 2.4, 9.1**

  - [x]* 2.4 Encode P3 — Count consistency
    - **Property 3: Count consistency**
    - After `stale_flags`, returned `count == |{i : i.isStale}|`,
      `0 <= count <= len(items)`, and each `isStale == is_stale(item, now)`
    - **Validates: Requirements 3.1, 3.2, 3.3, 6.4**

  - [x]* 2.5 Encode P4 — Read/stale independence
    - **Property 4: Read/stale independence**
    - Toggling `readState` on an item does not change its `is_stale` result at
      a fixed `now`
    - **Validates: Requirements 4.1, 4.2**

  - [x]* 2.6 Encode P5 — Waiting_Time consistency with ordering
    - **Property 5: Waiting_Time consistency with ordering**
    - For two dated items, older ⇒ `stale_age(older) >= stale_age(newer)`; if
      the newer item is stale then the older item is stale
    - **Validates: Requirements 1.2, 1.4, 5.1, 5.2, 5.3**

  - [x]* 2.7 Encode P6 — Determinism / purity
    - **Property 6: Determinism / purity**
    - `stale_age`/`is_stale` return equal results for equal `(item, now)` and
      do not mutate `item`; `stale_flags` mutates only the `isStale` key
    - **Validates: Requirements 1.1, 1.5, 2.7, 3.4**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Wire staleness into the `list` action
  - [x] 4.1 Capture reference `now` and call `stale_flags`
    - In the `list` action, capture `now = datetime.now(timezone.utc)` once per
      request; call `stale_flags(filtered, now)` on the route-filtered items
      after read-state resolution, without altering `sort_queue` ordering
    - _Requirements: 6.1, 6.5, 6.6_

  - [x] 4.2 Add `staleCount` and per-session `isStale` to the payload
    - Add top-level `staleCount` alongside the existing `unreadCount`; add
      `isStale` (`c.get("isStale", False)`) to each serialized session object;
      preserve all existing fields and ordering
    - _Requirements: 6.2, 6.3, 6.4, 6.7_

  - [x]* 4.3 Extend integration tests to assert staleness in the payload
    - Extend `tests/test_banker_api_integration.py` to assert the `list`
      payload includes a top-level `staleCount`, each session carries a boolean
      `isStale`, and `staleCount` equals the number of stale sessions
    - _Requirements: 6.2, 6.3, 6.4, 6.7_

- [x] 5. Render the stale notification in the portal (`index.html`)
  - [x] 5.1 Update `renderNotice` to accept `staleCount`
    - Change to `renderNotice(unreadCount, staleCount)`; keep the existing
      unread banner and add a stale message segment (e.g. "N item(s) waiting
      over 24 hours") only when `staleCount > 0`; render nothing stale when
      `staleCount === 0`; update the caller to pass `staleCount`
    - _Requirements: 7.1, 7.2_

  - [x] 5.2 Add the per-row stale badge and CSS
    - Add a `badge stale` element in the row template bound to the session's
      `isStale` (analogous to `badge category`); add a `.badge.stale` CSS rule;
      keep rows rendering in server order via `sessions.map(` with no client
      `.sort(`
    - _Requirements: 7.3, 7.4_

  - [x]* 5.3 Extend the portal structural test for P7
    - **Property 7: Frontend stale rendering (structural)**
    - Extend `tests/test_banker_portal_frontend.py` (html.parser + re): assert
      `renderNotice` takes `staleCount` and emits "over 24 hours"-style wording
      gated on `staleCount > 0`, the row template contains a `badge stale`
      bound to `isStale`, and no client-side `.sort(` is present
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4**

- [x] 6. Update the local stub for AWS-free testing
  - [x] 6.1 Compute staleness in `_build_list_payload`
    - In `scripts/local_banker_stub.py`, compute each item's `isStale` and a
      top-level `staleCount` in `_build_list_payload` using a 24h threshold
      against `datetime.now(timezone.utc)` with the same
      `createdAt`→`updatedAt` fallback
    - _Requirements: 8.1, 8.2_

  - [x] 6.2 Seed a clearly-old item
    - Seed at least one item with a `createdAt` more than 24h before the
      current time so the stale banner and badge are visible in the browser
    - _Requirements: 8.3_

- [ ] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a
  faster MVP; core implementation sub-tasks are never optional.
- Each property (P1–P7) is its own sub-task, annotated with its property number
  and the requirement clauses it validates, placed next to the code it exercises.
- Property tests use the `importlib.util.spec_from_file_location` loader and
  AWS region env-var setup from the existing `test_banker_*_properties.py` suite.
- The test suite runs under `.venv` on Windows (pytest, hypothesis, moto already
  installed).
