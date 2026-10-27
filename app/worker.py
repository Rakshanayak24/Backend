"""Transactional outbox consumer. External notification delivery is supplied by deployment adapters."""
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import OutboxEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("amrutam.worker")


def process_one() -> bool:
    with SessionLocal.begin() as db:
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.status == "pending", OutboxEvent.available_at <= datetime.now(timezone.utc)
        ).order_by(OutboxEvent.created_at).with_for_update(skip_locked=True).limit(1))
        if not event:
            return False
        event.attempts += 1
        try:
            # Adapters must use event.id as an idempotency key when calling external services.
            logger.info("outbox_event_consumed", extra={"event_id": event.id, "event_type": event.event_type,
                                                         "payload": event.payload})
            event.status = "processed"
            event.last_error = None
        except Exception as exc:
            event.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(2 ** event.attempts, 300))
            event.last_error = type(exc).__name__[:500]
            logger.exception("outbox_event_failed", extra={"event_id": event.id})
    return True


def run():
    while True:
        if not process_one():
            time.sleep(1)


if __name__ == "__main__":
    run()
