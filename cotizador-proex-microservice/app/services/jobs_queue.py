"""SQS publisher para encolar jobs hacia la worker Lambda."""

import json
import logging
import os
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        region = os.environ.get("AWS_REGION", "eu-south-2")
        _client = boto3.client("sqs", region_name=region)
    return _client


def enqueue_job(job_id: str, job_type: str, payload: Optional[dict] = None) -> str:
    """Publica el job en la cola y devuelve el MessageId de SQS."""
    body = {"job_id": job_id, "job_type": job_type, "payload": payload or {}}
    resp = _get_client().send_message(
        QueueUrl=os.environ["JOBS_QUEUE_URL"],
        MessageBody=json.dumps(body),
    )
    message_id = resp["MessageId"]
    logger.info(f"[enqueue_job] {job_id} type={job_type} message_id={message_id}")
    return message_id
