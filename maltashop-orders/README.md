# MaltaShop "place an order" prototype

Prototype for the MaltaShop replacement platform (Unit 5, SOA and Microservices Fundamentals).
It demonstrates one business flow, placing an order, across four independently deployable services that
share no database and communicate through RabbitMQ.

## What the system is

| Service | Port | Owns (own SQLite file in its own volume) | Role |
|---|---|---|---|
| order | 8001 | orders | REST entry point (`POST /orders`), saga participant |
| inventory | 8002 | stock, reservations | Reserves stock; releases it as the compensating action |
| payment | 8003 | payments | Charges the (simulated) external payment provider |
| notification | 8004 | sent notifications | Consumes outcome events and "sends" email |
| rabbitmq | 5672 / 15672 | broker state | Durable topic exchange `maltashop.events` |

The saga is choreographed: `order.placed` -> `inventory.reserved` -> `payment.captured` -> `order.confirmed`.
Failures emit `inventory.rejected` or `payment.failed`; Inventory releases stock and Order cancels.
Every request carries an `X-Correlation-ID` that is copied into message headers and every JSON log line.

## Run it

```bash
cp .env.example .env        # optional, defaults work
docker compose up --build   # starts broker and all four services
```

Place an order and watch the flow:

```bash
curl -i -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" -H "X-Correlation-ID: demo-1234" \
  -d '{"customer_id":"c1","sku":"TV-55","quantity":1,"amount_cents":59900,"card_token":"tok_ok"}'
curl http://localhost:8001/orders/<order_id>        # CONFIRMED after about a second
curl http://localhost:8004/sent                      # Notification consumed the event
docker compose logs | grep demo-1234                 # same correlation ID in every service
```

Use `"card_token":"tok_declined"` to trigger the compensation path, or `"sku":"LAPTOP-X","quantity":5` for out of stock.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_unit_and_contract.py -q   # unit + contract tests
python tests/e2e_smoke.py                              # needs the stack running
```

## Environment variables

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `RABBITMQ_URL` | all services | `amqp://guest:guest@rabbitmq/` | Broker connection (built from `RABBITMQ_USER`/`RABBITMQ_PASS` in Compose) |
| `RABBITMQ_USER`, `RABBITMQ_PASS` | Compose | `guest` | Broker credentials (change outside development) |
| `ORDER_DB_PATH` | order | `/data/orders.db` | Order data store |
| `INVENTORY_DB_PATH` | inventory | `/data/inventory.db` | Inventory data store |
| `PAYMENT_DB_PATH` | payment | `/data/payments.db` | Payment data store |
| `NOTIFICATION_DB_PATH` | notification | `/data/notifications.db` | Notification data store |
| `SERVICE_NAME` | all | set in Dockerfile | Appears in every log line |
| `PORT` | all | set in Dockerfile | HTTP port used by the health check |
| `LOG_LEVEL` | all | `INFO` | Python log level |

## CI/CD

`.github/workflows/order-service.yml`: test -> build once (SHA tag) -> e2e and scan -> staging -> manual approval -> production.

## Known limitations (see report, section 6.3)

SQLite stands in for per-service PostgreSQL; no outbox (a crash between DB commit and publish can lose an event);
no authentication; payment provider is simulated; single broker node.
