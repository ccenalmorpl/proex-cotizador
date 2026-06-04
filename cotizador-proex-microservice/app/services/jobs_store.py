"""DynamoDB-backed job state for async work dispatched via SQS."""

import logging
import os
import time
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

_table = None
_TTL_SECONDS = 7 * 24 * 3600
_ALLOWED_UPDATE_FIELDS = {
    "status",
    "progress_step",
    "error",
    "result",
    "started_at",
    "completed_at",
}


def _get_table():
    global _table
    if _table is None:
        name = os.environ["JOBS_TABLE_NAME"]
        region = os.environ.get("AWS_REGION", "eu-south-2")
        _table = boto3.resource("dynamodb", region_name=region).Table(name)
    return _table


def create_job(deal_id: str, job_type: str, job_id: str, payload: Optional[dict] = None) -> dict:
    """Inserta un job en estado pending con TTL a 7 días."""
    now = int(time.time())
    item = {
        "job_id": job_id,
        "deal_id": deal_id,
        "job_type": job_type,
        "status": "pending",
        "progress_step": None,
        "error": None,
        "result": None,
        "payload": payload or {},
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "ttl": now + _TTL_SECONDS,
    }
    _get_table().put_item(Item=item)
    logger.info(f"[create_job] {job_id} deal={deal_id} type={job_type}")
    return item


def update_job(job_id: str, **fields: Any) -> None:
    """Actualiza campos permitidos del job; siempre toca updated_at."""
    sets = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
    if not sets and not fields:
        return
    sets["updated_at"] = int(time.time())
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in sets)
    names = {f"#{k}": k for k in sets}
    values = {f":{k}": v for k, v in sets.items()}
    _get_table().update_item(
        Key={"job_id": job_id},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    logger.info(f"[update_job] {job_id} fields={list(sets.keys())}")


def get_job(job_id: str) -> Optional[dict]:
    """Lee un job por su clave primaria."""
    resp = _get_table().get_item(Key={"job_id": job_id})
    return resp.get("Item")


def get_latest_job_by_deal(deal_id: str, job_type: str) -> Optional[dict]:
    """Devuelve el job más reciente para (deal_id, job_type) usando el GSI.

    El GSI tiene SK=`job_type` (string fijo dentro de un mismo dominio), así que
    el orden natural del Query no es determinista. Leemos hasta 50 items y
    ordenamos en cliente por `created_at` descendente.
    """
    resp = _get_table().query(
        IndexName="deal_id-job_type-index",
        KeyConditionExpression=Key("deal_id").eq(deal_id) & Key("job_type").eq(job_type),
        Limit=50,
    )
    items = resp.get("Items") or []
    if not items:
        return None
    items.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
    return items[0]
