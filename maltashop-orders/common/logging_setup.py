"""Structured JSON logging with a correlation ID (12-factor XI: logs as event streams)."""
import contextvars, json, logging, os, sys, time

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
SERVICE = os.getenv("SERVICE_NAME", "unknown")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "service": SERVICE,
            "correlation_id": correlation_id.get(),
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def get_logger() -> logging.Logger:
    logger = logging.getLogger(SERVICE)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)  # stdout only; the platform ships logs
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
        logger.propagate = False
    return logger


def log(logger: logging.Logger, event: str, **fields) -> None:
    logger.info(event, extra={"fields": fields})
