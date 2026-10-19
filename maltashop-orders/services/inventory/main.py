"""Inventory service: reserves stock (saga step 1) and releases it as the compensating action."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import messaging
from common.app_base import open_channel, open_db
from common.logging_setup import get_logger, log

logger = get_logger()
db = open_db("INVENTORY_DB_PATH", "/data/inventory.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS stock (sku TEXT PRIMARY KEY, available INTEGER);
CREATE TABLE IF NOT EXISTS reservations (order_id TEXT PRIMARY KEY, sku TEXT, quantity INTEGER, state TEXT);
""")
db.executemany("INSERT OR IGNORE INTO stock VALUES (?,?)",
               [("TV-55", 10), ("KETTLE-1", 50), ("LAPTOP-X", 2)])
db.commit()
idem = messaging.Idempotency(db)
state: dict = {}


async def on_event(routing_key: str, body: dict) -> None:
    oid = body["order_id"]
    if routing_key == "order.placed":
        cur = db.execute("UPDATE stock SET available = available - ? WHERE sku=? AND available >= ?",
                         (body["quantity"], body["sku"], body["quantity"]))
        if cur.rowcount == 1:  # conditional update = no overselling, no distributed lock
            db.execute("INSERT OR REPLACE INTO reservations VALUES (?,?,?,'RESERVED')",
                       (oid, body["sku"], body["quantity"]))
            db.commit()
            log(logger, "stock_reserved", order_id=oid, sku=body["sku"])
            await messaging.publish(state["ch"], "inventory.reserved", body)
        else:
            db.commit()
            log(logger, "stock_rejected", order_id=oid, sku=body["sku"])
            await messaging.publish(state["ch"], "inventory.rejected", body)
    elif routing_key == "payment.failed":  # COMPENSATING ACTION: undo the reservation
        row = db.execute("SELECT * FROM reservations WHERE order_id=? AND state='RESERVED'",
                         (oid,)).fetchone()
        if row:
            db.execute("UPDATE stock SET available = available + ? WHERE sku=?",
                       (row["quantity"], row["sku"]))
            db.execute("UPDATE reservations SET state='RELEASED' WHERE order_id=?", (oid,))
            db.commit()
            log(logger, "stock_released", order_id=oid, sku=row["sku"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn, ch = await open_channel()
    state["ch"] = ch
    await messaging.consume(ch, "inventory.saga", ["order.placed", "payment.failed"], on_event, idem)
    yield
    await conn.close()


app = FastAPI(title="Inventory service", lifespan=lifespan)


@app.get("/health")
async def health():
    db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/stock/{sku}")
async def stock(sku: str):
    row = db.execute("SELECT * FROM stock WHERE sku=?", (sku,)).fetchone()
    return dict(row) if row else {"sku": sku, "available": 0}
