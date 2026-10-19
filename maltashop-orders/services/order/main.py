"""Order service: REST entry point and owner of order state. Saga participant (choreography)."""
import json, os, uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from common import messaging
from common.app_base import open_channel, open_db
from common.logging_setup import correlation_id, get_logger, log

logger = get_logger()
db = open_db("ORDER_DB_PATH", "/data/orders.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, customer_id TEXT, sku TEXT, quantity INTEGER, amount_cents INTEGER,
  card_token TEXT, status TEXT, idempotency_key TEXT UNIQUE, reason TEXT);
""")
idem = messaging.Idempotency(db)
state: dict = {}


class OrderIn(BaseModel):
    customer_id: str
    sku: str
    quantity: int = Field(gt=0, le=20)
    amount_cents: int = Field(gt=0)
    card_token: str


async def on_event(routing_key: str, body: dict) -> None:
    """Saga step: move the order to its final state when Payment or Inventory reports."""
    oid = body["order_id"]
    if routing_key == "payment.captured":
        db.execute("UPDATE orders SET status='CONFIRMED' WHERE id=?", (oid,))
        db.commit()
        log(logger, "order_confirmed", order_id=oid)
        await messaging.publish(state["ch"], "order.confirmed", body)
    elif routing_key in ("inventory.rejected", "payment.failed"):
        db.execute("UPDATE orders SET status='CANCELLED', reason=? WHERE id=?", (routing_key, oid))
        db.commit()
        log(logger, "order_cancelled", order_id=oid, reason=routing_key)
        await messaging.publish(state["ch"], "order.cancelled", {**body, "reason": routing_key})


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn, ch = await open_channel()
    state["conn"], state["ch"] = conn, ch
    await messaging.consume(ch, "order.saga", ["payment.captured", "payment.failed",
                                               "inventory.rejected"], on_event, idem)
    yield
    await conn.close()


app = FastAPI(title="Order service", lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    token = correlation_id.set(cid)
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response
    finally:
        correlation_id.reset(token)


@app.get("/health")
async def health():
    db.execute("SELECT 1")  # readiness: own data store reachable
    return {"status": "ok"}


@app.post("/orders", status_code=202)
async def place_order(order: OrderIn, idempotency_key: str = Header(...)):
    existing = db.execute("SELECT id, status FROM orders WHERE idempotency_key=?",
                          (idempotency_key,)).fetchone()
    if existing:  # a retried request returns the same order, never a duplicate
        return {"order_id": existing["id"], "status": existing["status"], "replayed": True}
    oid = str(uuid.uuid4())
    db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,NULL)",
               (oid, order.customer_id, order.sku, order.quantity, order.amount_cents,
                order.card_token, "PENDING", idempotency_key))
    db.commit()
    log(logger, "order_placed", order_id=oid, sku=order.sku)
    await messaging.publish(state["ch"], "order.placed", {
        "order_id": oid, "customer_id": order.customer_id, "sku": order.sku,
        "quantity": order.quantity, "amount_cents": order.amount_cents,
        "card_token": order.card_token})
    return {"order_id": oid, "status": "PENDING", "replayed": False}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    row = db.execute("SELECT id, status, reason FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "order not found")
    return dict(row)
