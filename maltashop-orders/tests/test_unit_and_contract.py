"""Test pyramid, base and middle layers (no broker needed): unit tests of idempotency and
saga logic, plus the OrderPlaced consumer-driven contract between Order and Inventory."""
import asyncio, json, pathlib, sqlite3

import jsonschema
from fastapi.testclient import TestClient

from common import messaging
from services.inventory import main as inventory
from services.order import main as order

SCHEMA = json.loads((pathlib.Path(__file__).parent.parent / "contracts/order.placed.schema.json").read_text())
BODY = {"customer_id": "c1", "sku": "TV-55", "quantity": 1, "amount_cents": 59900, "card_token": "tok_ok"}


def test_idempotency_blocks_second_delivery():  # UNIT
    idem = messaging.Idempotency(sqlite3.connect(":memory:"))
    assert idem.first_time("m1") is True
    assert idem.first_time("m1") is False


def test_order_rejects_invalid_quantity():  # UNIT (validation)
    client = TestClient(order.app)
    r = client.post("/orders", json={**BODY, "quantity": 0}, headers={"Idempotency-Key": "bad"})
    assert r.status_code == 422


def _capture(monkeypatch):
    sent = []

    async def fake_publish(channel, key, body, message_id=None):
        sent.append((key, body))
    monkeypatch.setattr(messaging, "publish", fake_publish)
    order.state["ch"] = object()
    return sent


def test_same_idempotency_key_creates_one_order(monkeypatch):  # INTEGRATION (in-process)
    sent = _capture(monkeypatch)
    client = TestClient(order.app)
    first = client.post("/orders", json=BODY, headers={"Idempotency-Key": "k-abc"}).json()
    second = client.post("/orders", json=BODY, headers={"Idempotency-Key": "k-abc"}).json()
    assert first["order_id"] == second["order_id"] and second["replayed"] is True
    assert len([s for s in sent if s[0] == "order.placed"]) == 1


def test_contract_order_placed_matches_consumer_schema(monkeypatch):  # CONTRACT: Order -> Inventory
    sent = _capture(monkeypatch)
    TestClient(order.app).post("/orders", json=BODY, headers={"Idempotency-Key": "k-contract"})
    key, payload = next(s for s in sent if s[0] == "order.placed")
    jsonschema.validate(payload, SCHEMA)  # provider side: what Order really publishes


def test_contract_inventory_accepts_schema_example(monkeypatch):  # CONTRACT: consumer side
    sent = []

    async def fake_publish(channel, key, body, message_id=None):
        sent.append(key)
    monkeypatch.setattr(messaging, "publish", fake_publish)
    inventory.state["ch"] = object()
    event = {"order_id": "o-1", **BODY}
    jsonschema.validate(event, SCHEMA)
    asyncio.run(inventory.on_event("order.placed", event))
    assert sent == ["inventory.reserved"]


def test_compensation_releases_stock(monkeypatch):  # UNIT of the saga's compensating action
    async def fake_publish(*a, **k):
        pass
    monkeypatch.setattr(messaging, "publish", fake_publish)
    inventory.state["ch"] = object()
    before = inventory.db.execute("SELECT available FROM stock WHERE sku='KETTLE-1'").fetchone()[0]
    ev = {"order_id": "o-2", "customer_id": "c", "sku": "KETTLE-1", "quantity": 4,
          "amount_cents": 1, "card_token": "t"}
    asyncio.run(inventory.on_event("order.placed", ev))
    asyncio.run(inventory.on_event("payment.failed", ev))
    after = inventory.db.execute("SELECT available FROM stock WHERE sku='KETTLE-1'").fetchone()[0]
    assert before == after
