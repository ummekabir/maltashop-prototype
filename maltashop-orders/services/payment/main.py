"""Payment service: talks to the external provider (simulated) and emits the outcome."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import messaging
from common.app_base import open_channel, open_db
from common.logging_setup import get_logger, log

logger = get_logger()
db = open_db("PAYMENT_DB_PATH", "/data/payments.db")
db.execute("CREATE TABLE IF NOT EXISTS payments (order_id TEXT PRIMARY KEY, amount_cents INTEGER, status TEXT)")
db.commit()
idem = messaging.Idempotency(db)
state: dict = {}


def provider_charge(card_token: str, amount_cents: int) -> bool:
    """Stand-in for the external payment provider. Raw card data never reaches MaltaShop;
    only a provider token does. Token 'tok_declined' simulates a declined card."""
    return card_token != "tok_declined"


async def on_event(routing_key: str, body: dict) -> None:
    oid = body["order_id"]
    ok = provider_charge(body["card_token"], body["amount_cents"])
    db.execute("INSERT OR REPLACE INTO payments VALUES (?,?,?)",
               (oid, body["amount_cents"], "CAPTURED" if ok else "FAILED"))
    db.commit()
    log(logger, "payment_captured" if ok else "payment_failed", order_id=oid)
    safe = {k: v for k, v in body.items() if k != "card_token"}  # do not fan the token out
    await messaging.publish(state["ch"], "payment.captured" if ok else "payment.failed", safe)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn, ch = await open_channel()
    state["ch"] = ch
    await messaging.consume(ch, "payment.saga", ["inventory.reserved"], on_event, idem)
    yield
    await conn.close()


app = FastAPI(title="Payment service", lifespan=lifespan)


@app.get("/health")
async def health():
    db.execute("SELECT 1")
    return {"status": "ok"}
