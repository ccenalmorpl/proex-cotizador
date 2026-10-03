"""Ejecuta el job de publicación de custom objects bajo el worker Lambda."""

import logging

from app.services.custom_objects import process_deal_custom_objects_from_line_items
from app.services.jobs_store import update_job

logger = logging.getLogger(__name__)


def run(deal_id: str, job_id: str, reconciliar_espejo: bool = False) -> dict:
    """Lanza el sync de custom objects reportando progreso en DynamoDB.

    `reconciliar_espejo` solo llega a True desde el publish: es el único momento en
    que HubSpot espeja las líneas de la cotización sobre el negocio.
    """
    def _cb(step: str) -> None:
        update_job(job_id, progress_step=step)

    # Antes del re-sync: el espejo del publish deja el negocio a medias por encima de
    # 200 líneas, y el re-sync borra toda Línea Contenedor sin line item vivo detrás,
    # que es la única copia del coste con el que se cotizó.
    # Fuera del publish no hay espejo que reparar, y el reconciliador toma por perdido
    # lo que el usuario acaba de borrar: 65489559709 bajó de 2x20 a 1x20 y el -002
    # volvió a nacer antes de que el re-sync borrase su Contenedor (02/10/2026).
    if reconciliar_espejo:
        try:
            from app.services.espejo_publish import reconciliar_lineas_tras_espejo
            from app.services.hubspot_client import hubspot_client
            _cb("reconciliando_lineas")
            reconciliar_lineas_tras_espejo(hubspot_client.get_client().access_token, deal_id)
        except Exception as e:
            logger.error(
                f"[custom_objects_worker.run] reconciliación del espejo falló para "
                f"deal={deal_id}: {e}", exc_info=True
            )

    stats = process_deal_custom_objects_from_line_items(deal_id, progress_callback=_cb)

    errors = stats.get("errors") or []
    if errors:
        joined = "; ".join(str(e) for e in errors)
        logger.error(f"[custom_objects_worker.run] job {job_id} terminó con errores: {joined}")
        raise RuntimeError(joined)

    try:
        from app.services.hubspot_client import hubspot_client
        from app.services.pedidos_sync import reconciliar_pedidos_si_ganada
        reconciliar_pedidos_si_ganada(hubspot_client.get_client(), deal_id)
    except Exception as e:
        logger.warning(
            f"[custom_objects_worker.run] reconciliación de pedidos best-effort "
            f"falló para deal={deal_id}: {e}"
        )

    return stats
