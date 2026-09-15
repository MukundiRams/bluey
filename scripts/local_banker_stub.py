#!/usr/bin/env python3
"""Local stub for the Banker API, for exercising index.html in a browser.

This is a DEV-ONLY convenience server. It mimics the shape of the deployed
banker API's ``list`` and ``mark_read`` responses (see the banker-query-triage
design) so the static portal (index.html) can be driven locally with NO AWS and
NO Cognito. It uses only the Python standard library.

It intentionally does NOT validate the bearer token — paste any non-empty token
into the portal. Read/unread state is held in memory for the life of the process.

Run (from the bluey/ directory):
    .\\.venv\\Scripts\\python.exe scripts/local_banker_stub.py
    # or any Python 3: python scripts/local_banker_stub.py

Then open index.html (see scripts note) and set:
    Banker API URL: 
    Cognito ID token: anything (e.g. "dev")
"""
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"
PORT = 8010

# Which banker this stub pretends is signed in. Switch to "banker-003"/"premium"
# to see the premium-tier routing (only their own assigned client, no pool).
STUB_BANKER = {"bankerId": "banker-001", "tier": "general"}


def _iso(day, hour):
    return f"2026-08-{day:02d}T{hour:02d}:00:00+00:00"


# Seeded queue items already in the shape the portal consumes. Deliberately NOT
# pre-sorted so you can see the server-side oldest-first ordering take effect.
_SEED_ITEMS = [
    {
        "itemId": "application#APP-2001",
        "sessionId": "sess-app-2001",
        "reference": "APP-2001",
        "customerId": "cust-001",
        "fullName": "Thabo Nkosi",
        "accountType": "Savings",
        "workCategory": "account_opening",
        "routing": "assigned",
        "status": "Pending",
        "createdAt": _iso(24, 13),
        "updatedAt": _iso(24, 13),
    },
    {
        "itemId": "session#sess-walkin-1",
        "sessionId": "sess-walkin-1",
        "reference": None,
        "customerId": None,
        "fullName": "Nomsa Walk-In",
        "accountType": "Enquiry",
        "workCategory": "pre_visit_enquiry",
        "routing": "general_pool",
        "status": "pending_review",
        "createdAt": _iso(20, 9),
        "updatedAt": _iso(20, 9),
    },
    {
        "itemId": "credit#cust-006#credit-006",
        "sessionId": "credit-006",
        "reference": None,
        "customerId": "cust-006",
        "fullName": "Lerato Mokoena",
        "accountType": "personal-loan",
        "workCategory": "credit_application",
        "routing": "general_pool",
        "status": "pending_review",
        "createdAt": _iso(22, 11),
        "updatedAt": _iso(22, 11),
    },
    {
        "itemId": "session#sess-existing-1",
        "sessionId": "sess-existing-1",
        "reference": None,
        "customerId": "cust-003",
        "fullName": "Johan van der Merwe",
        "accountType": "Servicing",
        "workCategory": "existing_customer_servicing",
        "routing": "general_pool",
        "status": "pending_review",
        "createdAt": _iso(26, 15),
        "updatedAt": _iso(26, 15),
    },
]

# In-memory read state for this stub banker: set of itemIds marked read.
_READ_IDS: set[str] = set()


def _sort_oldest_first(items):
    """Oldest-first by createdAt (fallback updatedAt); undated last."""
    def key(it):
        ts = it.get("createdAt") or it.get("updatedAt")
        return (0, ts) if ts else (1, "")
    return sorted(items, key=key)


def _build_list_payload():
    ordered = _sort_oldest_first(_SEED_ITEMS)
    sessions = []
    for it in ordered:
        row = dict(it)
        row["readState"] = "read" if it["itemId"] in _READ_IDS else "unread"
        sessions.append(row)
    unread = sum(1 for s in sessions if s["readState"] == "unread")
    return {
        "bankerId": STUB_BANKER["bankerId"],
        "tier": STUB_BANKER["tier"],
        "unreadCount": unread,
        "sessions": sessions,
        "applications": [],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # CORS so a file:// or localhost-served page can call this stub.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        action = (query.get("action") or [""])[0]
        if action in ("list", "list_applications", "applications"):
            return self._send(200, _build_list_payload())
        return self._send(400, {"error": "unknown action"})

    def do_POST(self):
        global _SEED_ITEMS
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid json"})

        action = body.get("action")
        if action == "mark_read":
            item_id = body.get("itemId")
            if not item_id:
                return self._send(400, {"error": "itemId required"})
            known = {it["itemId"] for it in _SEED_ITEMS}
            if item_id not in known:
                return self._send(403, {"error": "not_authorized_for_item", "itemId": item_id})
            _READ_IDS.add(item_id)
            return self._send(200, {"result": "mark_read", "itemId": item_id, "readState": "read"})

        if action in ("approve", "reject"):
            # Accept and drop the item so the demo behaves sensibly.
            ref = body.get("reference")
            sid = body.get("sessionId")
            _SEED_ITEMS = [
                it for it in _SEED_ITEMS
                if it.get("reference") != ref and it.get("sessionId") != sid
            ]
            return self._send(200, {"result": action, "sessionId": sid, "reference": ref})

        return self._send(400, {"error": "unknown action"})

    def log_message(self, fmt, *args):
        print(f"[{datetime.now(timezone.utc).isoformat()}] " + (fmt % args))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Local banker stub on http://{HOST}:{PORT}/banker")
    print(f"Signed-in stub banker: {STUB_BANKER['bankerId']} ({STUB_BANKER['tier']})")
    print("In the portal set API URL to the above and any non-empty token.")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping stub.")
        server.shutdown()


if __name__ == "__main__":
    main()
