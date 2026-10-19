"""End-to-end smoke test: run against a live stack (docker compose up). Not collected by default."""
import json, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


key = f"smoke-{time.time()}"
created = call("POST", "/orders", {"customer_id": "c9", "sku": "KETTLE-1", "quantity": 1,
                                   "amount_cents": 2500, "card_token": "tok_ok"}, {"Idempotency-Key": key})
for _ in range(20):
    status = call("GET", f"/orders/{created['order_id']}")["status"]
    if status != "PENDING":
        break
    time.sleep(0.5)
assert status == "CONFIRMED", status
print("SMOKE OK", created["order_id"], status)
