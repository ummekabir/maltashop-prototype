"""Thin RabbitMQ helper: durable topic exchange, publisher confirms, manual acks,
dead-letter queue and an idempotent-consumer wrapper."""
import asyncio, json, os, sqlite3, uuid
from typing import Awaitable, Callable

import aio_pika

from common.logging_setup import correlation_id, get_logger, log

EXCHANGE = "maltashop.events"
DLX = "maltashop.dlx"
RABBIT_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq/")


async def connect(retries: int = 30):
    last = None
    for _ in range(retries):  # the broker may start after us: retry (factor IV, backing service)
        try:
            return await aio_pika.connect_robust(RABBIT_URL)
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(2)
    raise RuntimeError(f"broker unreachable: {last}")


async def publish(channel, routing_key: str, body: dict, message_id: str | None = None) -> None:
    exchange = await channel.get_exchange(EXCHANGE)
    msg = aio_pika.Message(
        body=json.dumps(body).encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # survive broker restart
        message_id=message_id or str(uuid.uuid4()),      # the idempotency key for consumers
        headers={"correlation_id": correlation_id.get()},
    )
    await exchange.publish(msg, routing_key=routing_key)  # channel uses publisher confirms


class Idempotency:
    """Per-service table of processed message IDs, in the service's OWN database."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS processed_messages (message_id TEXT PRIMARY KEY)")
        db.commit()

    def first_time(self, message_id: str) -> bool:
        try:
            self.db.execute("INSERT INTO processed_messages VALUES (?)", (message_id,))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False


async def consume(channel, queue_name: str, bindings: list[str],
                  handler: Callable[[str, dict], Awaitable[None]], idem: Idempotency) -> None:
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    await channel.declare_exchange(DLX, aio_pika.ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
    await dlq.bind(DLX)
    queue = await channel.declare_queue(
        queue_name, durable=True, arguments={"x-dead-letter-exchange": DLX})
    for key in bindings:
        await queue.bind(exchange, routing_key=key)
    logger = get_logger()

    async def on_message(message: aio_pika.IncomingMessage) -> None:
        token = correlation_id.set((message.headers or {}).get("correlation_id", "-"))
        try:
            if not idem.first_time(message.message_id):  # duplicate delivery: ack and skip
                log(logger, "duplicate_ignored", message_id=message.message_id)
                await message.ack()
                return
            await handler(message.routing_key, json.loads(message.body))
            await message.ack()                           # ack only after the work is done
        except Exception as exc:  # noqa: BLE001
            log(logger, "handler_failed", error=str(exc))
            await message.reject(requeue=False)           # goes to the dead-letter queue
        finally:
            correlation_id.reset(token)

    await queue.consume(on_message)
