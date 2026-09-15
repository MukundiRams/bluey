"""DOM/structural example tests for the banker review portal (``bluey/index.html``).

These are **example/structural** tests (NOT property-based). They validate the
static structure and JavaScript wiring in ``bluey/index.html`` that implements
the frontend acceptance criteria of the banker-query-triage feature.

Why structural (Strategy B): this repository has no Node.js / npm toolchain and
no JavaScript test runner (jest/vitest); the test suite is Python + pytest under
``bluey/.venv``. Rather than introduce a Node toolchain, these tests read
``index.html`` as text and assert — using only the Python standard library
(``html.parser`` and ``re``) — that the DOM markup and the ``<script>`` logic
that back each requirement are present and correctly wired.

Requirements verified:

* **1.8** — category labels render: a ``CATEGORY_LABELS`` map exists covering all
  five ``workCategory`` keys mapped to human-readable labels, and the row
  template emits a ``badge category`` bound to the item's category.
* **2.4** — rows appear in server order without re-sorting: ``loadList`` renders
  from ``data.sessions`` via ``.map(`` and does not call ``.sort(`` on the
  sessions array.
* **3.1 / 3.2** — the login banner shows the correct text for non-zero and zero
  ``unreadCount``: a ``renderNotice`` function produces the "unreviewed item …
  waiting" text when count > 0 and the "all caught up" text when count === 0,
  and a ``#notice`` element exists in the markup.
* **4.3** — unread rows are visually distinguished: CSS has a ``.review.unread``
  rule, the row template adds the ``unread`` class when ``readState === 'unread'``,
  and a mark-read control is emitted for unread rows.
* **3.4** — count decrements / list refreshes after a successful ``mark_read``:
  ``markRead`` POSTs ``{ action: 'mark_read', itemId }`` and calls ``loadList()``
  on success; the mark-read handler calls ``stopPropagation`` so it does not open
  the detail pane.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


INDEX_HTML = Path(__file__).resolve().parents[1] / "index.html"

# The five canonical Work_Category keys the portal must be able to label (R1.8).
EXPECTED_CATEGORY_KEYS = {
    "account_opening",
    "credit_application",
    "pre_visit_enquiry",
    "existing_customer_servicing",
    "unassigned_fallback",
}


@pytest.fixture(scope="module")
def html_text() -> str:
    """The full ``index.html`` source as text."""
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script_text(html_text: str) -> str:
    """The contents of the (single) ``<script>`` block in ``index.html``."""
    match = re.search(r"<script>(.*)</script>", html_text, re.DOTALL)
    assert match, "index.html must contain a <script> block with the portal logic"
    return match.group(1)


@pytest.fixture(scope="module")
def style_text(html_text: str) -> str:
    """The contents of the (single) ``<style>`` block in ``index.html``."""
    match = re.search(r"<style>(.*)</style>", html_text, re.DOTALL)
    assert match, "index.html must contain a <style> block with the portal styling"
    return match.group(1)


class _IdCollector(HTMLParser):
    """Collect the set of element ``id`` attributes present in the markup."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)


@pytest.fixture(scope="module")
def element_ids(html_text: str) -> set[str]:
    """The set of ``id`` attributes present in the parsed DOM."""
    parser = _IdCollector()
    parser.feed(html_text)
    return parser.ids


# ---------------------------------------------------------------------------
# R1.8 — category labels render
# ---------------------------------------------------------------------------


def test_category_labels_map_covers_all_five_work_categories(script_text: str):
    """CATEGORY_LABELS maps every workCategory key to a human-readable label. (R1.8)"""
    map_match = re.search(
        r"const\s+CATEGORY_LABELS\s*=\s*\{(.*?)\}", script_text, re.DOTALL
    )
    assert map_match, "script must define a CATEGORY_LABELS map"
    body = map_match.group(1)

    # Every expected category key must be a key in the map.
    for key in EXPECTED_CATEGORY_KEYS:
        assert re.search(rf"\b{re.escape(key)}\s*:", body), (
            f"CATEGORY_LABELS must include a mapping for '{key}'"
        )

    # Each mapping must point at a non-empty human-readable string label.
    label_pairs = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", body))
    for key in EXPECTED_CATEGORY_KEYS:
        assert label_pairs.get(key, "").strip(), (
            f"CATEGORY_LABELS['{key}'] must map to a non-empty label"
        )


def test_row_template_emits_category_badge_bound_to_work_category(script_text: str):
    """The row template renders a `badge category` derived from workCategory. (R1.8)"""
    # The category label is derived from the item's workCategory via the map.
    assert "CATEGORY_LABELS[s.workCategory]" in script_text, (
        "row rendering must look up the label via CATEGORY_LABELS[s.workCategory]"
    )
    # And a category badge element is emitted for the resolved label.
    assert re.search(r'badge\s+category', script_text), (
        "row template must emit a 'badge category' element for the category label"
    )


# ---------------------------------------------------------------------------
# R2.4 — rows render in server order without re-sorting
# ---------------------------------------------------------------------------


def test_loadlist_renders_sessions_via_map(script_text: str):
    """loadList renders the queue by mapping over the server sessions array. (R2.4)"""
    load_list = _extract_function_body(script_text, "loadList")
    assert "sessions.map(" in load_list, (
        "loadList must render rows by mapping over the server-provided sessions"
    )


def test_loadlist_does_not_client_side_sort_sessions(script_text: str):
    """loadList must NOT re-sort the sessions array on the client. (R2.4)"""
    load_list = _extract_function_body(script_text, "loadList")
    assert ".sort(" not in load_list, (
        "loadList must render in server order and must not call .sort() on sessions"
    )


# ---------------------------------------------------------------------------
# R3.1 / R3.2 — login banner text for non-zero and zero unreadCount
# ---------------------------------------------------------------------------


def test_notice_element_exists_in_markup(element_ids: set[str]):
    """A #notice element exists to host the login banner. (R3.1, R3.2)"""
    assert "notice" in element_ids, "markup must contain an element with id='notice'"


def test_render_notice_function_exists(script_text: str):
    """A renderNotice function drives the banner text. (R3.1, R3.2)"""
    assert re.search(r"function\s+renderNotice\s*\(", script_text), (
        "script must define a renderNotice(unreadCount) function"
    )


def test_render_notice_nonzero_text(script_text: str):
    """Non-zero unreadCount shows the 'unreviewed item(s) waiting' banner. (R3.1)"""
    render_notice = _extract_function_body(script_text, "renderNotice")
    assert "unreviewed item" in render_notice, (
        "renderNotice must state the number of unreviewed items"
    )
    assert "waiting" in render_notice, (
        "the non-zero banner must indicate items are waiting"
    )


def test_render_notice_zero_text(script_text: str):
    """Zero unreadCount shows the 'all caught up' banner. (R3.2)"""
    render_notice = _extract_function_body(script_text, "renderNotice")
    assert "all caught up" in render_notice, (
        "renderNotice must show an 'all caught up' message when there are no unread items"
    )


def test_loadlist_invokes_render_notice_with_unread_count(script_text: str):
    """loadList feeds the server unreadCount into the banner. (R3.1, R3.2)"""
    load_list = _extract_function_body(script_text, "loadList")
    assert "renderNotice(data.unreadCount, data.staleCount)" in load_list, (
        "loadList must call renderNotice with the server-provided unreadCount (and staleCount)"
    )


# ---------------------------------------------------------------------------
# R4.3 — unread rows are visually distinguished
# ---------------------------------------------------------------------------


def test_css_has_unread_review_rule(style_text: str):
    """CSS provides a distinct `.review.unread` treatment. (R4.3)"""
    assert re.search(r"\.review\.unread\b", style_text), (
        "stylesheet must define a distinct .review.unread rule"
    )


def test_row_template_marks_unread_rows(script_text: str):
    """The row template adds the `unread` class when readState is unread. (R4.3)"""
    assert "s.readState === 'unread'" in script_text, (
        "row rendering must detect unread rows via readState === 'unread'"
    )
    # The detected unread state toggles the 'unread' class on the row.
    assert re.search(r"isUnread\s*\?\s*'unread'", script_text), (
        "row template must apply the 'unread' class for unread rows"
    )


def test_row_template_emits_mark_read_control_for_unread_rows(script_text: str):
    """Unread rows emit a mark-read control. (R4.3)"""
    assert "data-mark-read" in script_text, (
        "unread rows must emit a control carrying data-mark-read"
    )
    assert re.search(r"class=\"mark-read\"|mark-read", script_text), (
        "the mark-read control must be identifiable (class/data attribute)"
    )


# ---------------------------------------------------------------------------
# R3.4 — count decrements / list refreshes after a successful mark_read
# ---------------------------------------------------------------------------


def test_mark_read_posts_correct_action_and_item_id(script_text: str):
    """markRead POSTs { action: 'mark_read', itemId }. (R3.4)"""
    mark_read = _extract_function_body(script_text, "markRead")
    assert "method: 'POST'" in mark_read, "markRead must issue a POST request"
    assert "'mark_read'" in mark_read, "markRead must send action 'mark_read'"
    assert "itemId" in mark_read, "markRead must include the itemId in the body"


def test_mark_read_refreshes_list_on_success(script_text: str):
    """markRead calls loadList() after a successful POST to refresh the count. (R3.4)"""
    mark_read = _extract_function_body(script_text, "markRead")
    assert "loadList()" in mark_read, (
        "markRead must call loadList() on success so the row flips and the count updates"
    )


def test_mark_read_handler_stops_propagation(script_text: str):
    """The mark-read handler stops propagation so it doesn't open the detail pane. (R3.4)"""
    assert "stopPropagation" in script_text, (
        "the mark-read click handler must call event.stopPropagation()"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_function_body(script: str, name: str) -> str:
    """Return the body of a top-level ``function name(...) { ... }`` via brace matching.

    Supports both ``function name(`` and ``async function name(`` declarations.
    Uses a simple brace counter starting at the function's opening ``{``; this is
    sufficient for the well-formed portal script (which contains no unbalanced
    braces inside string/regex literals that would break counting for these
    functions).
    """
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", script)
    assert match, f"script must define a function named {name}"
    start = match.end() - 1  # position of the opening brace
    depth = 0
    for i in range(start, len(script)):
        ch = script[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return script[start : i + 1]
    raise AssertionError(f"unbalanced braces while extracting function {name}")


# ---------------------------------------------------------------------------
# stale-query-notification — P7: frontend stale rendering (structural)
# ---------------------------------------------------------------------------
# Feature: stale-query-notification, Property 7: Frontend stale rendering (structural)


def test_stale_notice_element_exists_in_markup(element_ids: set[str]):
    """A #staleNotice element exists to host the stale banner. (R7.1, R7.2)"""
    assert "staleNotice" in element_ids, (
        "markup must contain an element with id='staleNotice'"
    )


def test_render_notice_accepts_stale_count(script_text: str):
    """renderNotice takes a staleCount parameter. (R7.1, R7.2)"""
    assert re.search(r"function\s+renderNotice\s*\(\s*unreadCount\s*,\s*staleCount\s*\)", script_text), (
        "renderNotice must accept (unreadCount, staleCount)"
    )


def test_render_notice_shows_over_24_hours_wording_gated_on_stale_count(script_text: str):
    """renderNotice emits an 'over 24 hours' message gated on staleCount > 0. (R7.1, R7.2)"""
    render_notice = _extract_function_body(script_text, "renderNotice")
    # Gate: the stale segment is conditioned on a positive stale count.
    assert re.search(r"stale\s*>\s*0", render_notice), (
        "renderNotice must gate the stale segment on stale > 0"
    )
    # Wording: the stale message references the 24-hour threshold.
    assert "over 24 hours" in render_notice, (
        "renderNotice must mention items waiting over 24 hours"
    )


def test_loadlist_passes_stale_count_to_render_notice(script_text: str):
    """loadList feeds the server staleCount into the banner. (R7.1, R7.2)"""
    load_list = _extract_function_body(script_text, "loadList")
    assert "renderNotice(data.unreadCount, data.staleCount)" in load_list, (
        "loadList must call renderNotice with the server-provided unreadCount and staleCount"
    )


def test_row_template_emits_stale_badge_bound_to_is_stale(script_text: str):
    """The row template renders a `badge stale` when the session isStale. (R7.3)"""
    # The badge is conditioned on the session's isStale flag.
    assert re.search(r"s\.isStale\s*\?", script_text), (
        "row rendering must condition the stale badge on s.isStale"
    )
    # And a stale badge element is emitted for stale rows.
    assert re.search(r'badge\s+stale', script_text), (
        "row template must emit a 'badge stale' element for stale items"
    )


def test_css_has_badge_stale_rule(style_text: str):
    """CSS provides a distinct `.badge.stale` treatment. (R7.3)"""
    assert re.search(r"\.badge\.stale\b", style_text), (
        "stylesheet must define a distinct .badge.stale rule"
    )


def test_loadlist_still_renders_in_server_order_without_sort(script_text: str):
    """Rows still render via sessions.map with no client-side sort. (R7.4)"""
    load_list = _extract_function_body(script_text, "loadList")
    assert "sessions.map(" in load_list, (
        "loadList must render rows by mapping over the server-provided sessions"
    )
    assert ".sort(" not in load_list, (
        "loadList must render in server order and must not call .sort() on sessions"
    )
