"""Ejecuta el job de publicación de custom objects bajo el worker Lambda."""

import logging

from app.services.custom_objects import process_deal_custom_objects_from_line_items
from app.services.jobs_store import update_job

logger = logging.getLogger(__name__)


def run(deal_id: str, job_id: str) -> dict:
    """Lanza el sync de custom objects reportando progreso en DynamoDB."""
    def _cb(step: str) -> None:
        update_job(job_id, progress_step=step)

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
