"""Notification service: consumes order outcomes and 'sends' email. Failing here can never
block checkout, because Order does not call this service (asynchronous, publish-subscribe)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import messaging
from common.app_base import open_channel, open_db
from common.logging_setup import get_logger, log

logger = get_logger()
db = open_db("NOTIFICATION_DB_PATH", "/data/notifications.db")
db.execute("CREATE TABLE IF NOT EXISTS sent (order_id TEXT, kind TEXT)")
db.commit()
idem = messaging.Idempotency(db)


async def on_event(routing_key: str, body: dict) -> None:
    kind = "confirmation" if routing_key == "order.confirmed" else "cancellation"
    db.execute("INSERT INTO sent VALUES (?,?)", (body["order_id"], kind))
    db.commit()
    log(logger, "email_sent", order_id=body["order_id"], kind=kind)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn, ch = await open_channel()
    await messaging.consume(ch, "notification.events", ["order.confirmed", "order.cancelled"],
                            on_event, idem)
    yield
    await conn.close()


app = FastAPI(title="Notification service", lifespan=lifespan)


@app.get("/health")
async def health():
    db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/sent")
async def sent():
    return [dict(r) for r in db.execute("SELECT * FROM sent")]
