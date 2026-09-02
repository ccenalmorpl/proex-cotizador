"""Endpoints de pedidos KIT que consume la card.

Envoltorio fino sobre `app/services/kits_service.py`: toda la logica vive alli y
en `app/utils/kits.py`, para que el endpoint y `scripts/kits_disponibles.py`
devuelvan lo mismo por construccion.

De momento solo LECTURA. Crear KIT y marcar cajas siguen fuera del backend
mientras no se decida desplegarlo.

Este modulo NO esta registrado en `main.py`: se monta desde `kits_local.py` para
poder probarlo en local con ngrok sin tocar el microservicio en produccion.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.kits_service import (
    analizar,
    buscar_kits,
    guardar_marcas_de_cotizacion,
    kits_disponibles_para_card,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _skus(skus: Optional[str]) -> List[str]:
    """Acepta `?skus=a,b,c`. Vacio significa "todos"."""
    if not skus:
        return []
    return [s.strip() for s in skus.split(",") if s.strip()]


@router.get("/disponibles")
def listar_disponibles(
    skus: Optional[str] = Query(
        default=None,
        description="SKUs separados por coma. Vacio devuelve todos los KIT consumibles.",
    ),
):
    """KIT abiertos con cajas libres, listos para que la card los pinte.

    Sincrono a proposito (`def`, no `async def`): la llamada a HubSpot es
    bloqueante y FastAPI la saca a su threadpool, en vez de bloquear el event loop.
    """
    lista = _skus(skus)
    try:
        kits = kits_disponibles_para_card(settings.proex_access_token, lista)
    except RuntimeError as e:
        logger.error(f"[kits] fallo listando disponibles: {e}")
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    logger.info(f"[kits] {len(kits)} disponibles para skus={lista or 'todos'}")
    # `success` no es decorativo: `fetchFromAPI` de la card lo exige y trata su
    # ausencia como error, asi que todos los endpoints del cotizador lo llevan.
    return {"success": True, "kits": kits, "total": len(kits)}


class MarcaKit(BaseModel):
    """Una marca: cuantas cajas reserva la cotizacion sobre un KIT."""

    kit_id: str
    cajas: int = Field(ge=0)


class GuardarMarcasRequest(BaseModel):
    """Conjunto COMPLETO de marcas de una cotizacion.

    Completo y no incremental a proposito: los KIT que no vengan en la lista se
    desmarcan. Asi la card puede mandar su estado tal cual y HubSpot queda igual,
    sin tener que llevar la cuenta de que se ha quitado.
    """

    cotizacion_id: str
    marcas: List[MarcaKit] = Field(default_factory=list)


@router.post("/marcas")
def guardar_marcas(payload: GuardarMarcasRequest):
    """Guarda las marcas de la cotizacion. Lo llama la card al pulsar Siguiente.

    Se persiste al avanzar y no en cada clic: el comercial ajusta cantidades
    varias veces antes de decidir, y escribir en cada pulsacion llenaria HubSpot
    de estados intermedios que nadie ha confirmado.
    """
    try:
        resultado = guardar_marcas_de_cotizacion(
            settings.proex_access_token,
            payload.cotizacion_id,
            {m.kit_id: m.cajas for m in payload.marcas},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "peticion_invalida", "mensaje": str(e)})
    except RuntimeError as e:
        logger.error(f"[kits] fallo guardando marcas: {e}")
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    return {"success": True, **resultado}


@router.get("/auditoria")
def auditar(
    skus: Optional[str] = Query(default=None, description="SKUs separados por coma."),
):
    """Mismo analisis que el script, descuadres incluidos.

    Existe para poder ver desde el navegador por que un KIT no aparece en la card
    (SKU vacio, etapa sin recalcular...) sin tener que abrir una terminal.
    """
    try:
        analisis = analizar(buscar_kits(settings.proex_access_token), _skus(skus))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    return {
        "success": True,
        "por_sku": analisis["por_sku"],
        "avisos": analisis["avisos"],
        "total_avisos": len(analisis["avisos"]),
    }
