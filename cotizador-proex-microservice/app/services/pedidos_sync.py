"""Reconciliación cotizador → pedidos para contenedores añadidos tras ganar.

El microservicio de pedidos crea los Negocios Pedido vía un webhook de HubSpot
que se dispara una sola vez, al pasar la cotización a 'ganado'. Si después se
añade un contenedor a esa cotización ya ganada, ese evento no vuelve a dispararse
y el contenedor se queda sin pedido. Aquí, al terminar de publicar los custom
objects, avisamos a pedidos para que cree los pedidos que falten.
"""

import logging
from typing import Optional

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 30


def reconciliar_pedidos_si_ganada(client, deal_id: str) -> None:
    """Si la cotización ya está ganada, pide a pedidos crear los Negocios Pedido
    de contenedores añadidos después de ganar. La creación en pedidos es
    idempotente (solo crea los que faltan). Best-effort: cualquier fallo se
    registra y no interrumpe la publicación de custom objects.
    """
    webhook_url = settings.pedidos_webhook_url
    secret = settings.hubspot_webhook_secret
    if not webhook_url or not secret:
        logger.warning(
            "[reconciliar_pedidos] PEDIDOS_WEBHOOK_URL/HUBSPOT_WEBHOOK_SECRET "
            "sin configurar; se omite la reconciliación de pedidos para "
            f"deal={deal_id}"
        )
        return

    if not _cotizacion_ganada(client, deal_id):
        return

    try:
        resp = requests.post(
            webhook_url,
            json={"cotizacion_deal_id": deal_id},
            headers={"X-Webhook-Token": secret, "Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        logger.error(
            f"[reconciliar_pedidos] error llamando al webhook de pedidos para "
            f"deal={deal_id}: {e}"
        )
        return

    if resp.ok:
        logger.info(
            f"[reconciliar_pedidos] cotizacion ganada deal={deal_id}: pedidos "
            f"reconciliados (status={resp.status_code})"
        )
    else:
        logger.error(
            f"[reconciliar_pedidos] fallo al reconciliar pedidos de deal={deal_id}: "
            f"status={resp.status_code} body={resp.text[:300]}"
        )


def _cotizacion_ganada(client, deal_id: str) -> bool:
    try:
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id=deal_id, properties=["hs_is_closed_won"]
        )
    except Exception as e:
        logger.warning(
            f"[reconciliar_pedidos] no se pudo leer hs_is_closed_won de "
            f"deal={deal_id}: {e}"
        )
        return False

    is_won: Optional[str] = (deal.properties or {}).get("hs_is_closed_won")
    if str(is_won).lower() != "true":
        logger.info(
            f"[reconciliar_pedidos] cotizacion deal={deal_id} no ganada "
            f"(hs_is_closed_won={is_won!r}); nada que reconciliar"
        )
        return False
    return True
