"""Lambda handler para consumir SQS y despachar jobs por job_type."""

import json
import logging
import time
import traceback

from app.services.jobs_store import get_job, update_job
from app.workers import custom_objects_worker

logger = logging.getLogger()
logger.setLevel(logging.INFO)


JOB_HANDLERS = {
    "quotes_publish_custom_objects": custom_objects_worker.run,
}

_TERMINAL_STATUSES = {"processing", "completed"}


def _process_record(record: dict) -> None:
    body = json.loads(record["body"])
    job_id = body["job_id"]
    job_type = body["job_type"]
    payload = body.get("payload") or {}

    logger.info(f"[worker] received job_id={job_id} type={job_type}")

    existing = get_job(job_id)
    if existing and existing.get("status") in _TERMINAL_STATUSES:
        logger.warning(
            f"[worker] skipping job_id={job_id} already in status={existing.get('status')}"
        )
        return

    handler_fn = JOB_HANDLERS.get(job_type)
    if handler_fn is None:
        msg = f"unknown job_type={job_type}"
        update_job(job_id, status="failed", error=msg, completed_at=int(time.time()))
        raise ValueError(msg)

    update_job(job_id, status="processing", started_at=int(time.time()))

    try:
        result = handler_fn(**payload, job_id=job_id)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        logger.error(f"[worker] job_id={job_id} failed: {err}\n{traceback.format_exc()}")
        update_job(job_id, status="failed", error=err, completed_at=int(time.time()))
        raise

    update_job(
        job_id,
        status="completed",
        result=result if isinstance(result, dict) else {"value": result},
        completed_at=int(time.time()),
        progress_step="done",
    )
    logger.info(f"[worker] job_id={job_id} completed")


def handler(event, context):
    """Itera Records de SQS y despacha. Re-lanza para que SQS reintente y caiga en DLQ tras 3 intentos."""
    records = event.get("Records") or []
    logger.info(f"[worker] handler invoked with {len(records)} records")
    for record in records:
        _process_record(record)
    return {"processed": len(records)}
