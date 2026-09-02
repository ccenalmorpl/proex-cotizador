"""Lock de escritura por deal, respaldado por la tabla DynamoDB de jobs.

Los endpoints de contenedores hacen leer→diff→escribir durante ~25 s y crean los
line items de uno en uno, así que dos peticiones simultáneas leen el mismo estado
y ambas crean su juego completo de líneas (incidencia del 2026-08-28: el
contenedor acabó con 22 líneas donde iban 11).

Reusa la tabla `proex-jobs` con la clave prefijada `lock#`: el rol de la Lambda ya
tiene PutItem/UpdateItem/GetItem sobre ella. No tiene DeleteItem, así que liberar
es marcar el lock como expirado, no borrar el item; DynamoDB lo recoge después vía
TTL. Los locks no llevan `deal_id`/`job_type`, así que no aparecen en el GSI ni
contaminan las consultas de jobs.
"""

import logging
import os
import time
import uuid
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_table = None
_DEFAULT_TTL_SECONDS = 120
_ITEM_TTL_SECONDS = 3600

# Marca de que el lock no pudo evaluarse (DynamoDB no disponible). Se deja pasar la
# operación: perder la protección es mejor que tumbar el cotizador entero.
DEGRADED = "__degraded__"


def _get_table():
    global _table
    if _table is None:
        name = os.environ["JOBS_TABLE_NAME"]
        region = os.environ.get("AWS_REGION", "eu-south-2")
        _table = boto3.resource("dynamodb", region_name=region).Table(name)
    return _table


def acquire(resource: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> Optional[str]:
    """Intenta tomar el lock. Devuelve el token si lo consigue, None si está ocupado,
    DEGRADED si DynamoDB no responde."""
    now = int(time.time())
    token = uuid.uuid4().hex
    try:
        _get_table().put_item(
            Item={
                "job_id": f"lock#{resource}",
                "lock_token": token,
                "expires_at": now + ttl_seconds,
                "created_at": now,
                "ttl": now + _ITEM_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(job_id) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return token
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.warning(f"[deal_lock] {resource} ocupado por otra petición en curso")
            return None
        logger.warning(f"[deal_lock] no pude evaluar el lock de {resource}, sigo sin él: {e}")
        return DEGRADED
    except Exception as e:
        logger.warning(f"[deal_lock] no pude evaluar el lock de {resource}, sigo sin él: {e}")
        return DEGRADED


def release(resource: str, token: str) -> None:
    if not token or token == DEGRADED:
        return
    try:
        _get_table().update_item(
            Key={"job_id": f"lock#{resource}"},
            UpdateExpression="SET expires_at = :zero",
            ConditionExpression="lock_token = :token",
            ExpressionAttributeValues={":zero": 0, ":token": token},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.warning(f"[deal_lock] no pude liberar {resource}: {e}")
    except Exception as e:
        logger.warning(f"[deal_lock] no pude liberar {resource}: {e}")


def deal_resource(deal_id: str) -> str:
    return f"deal#{deal_id}"
