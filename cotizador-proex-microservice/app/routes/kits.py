"""Endpoints de pedidos KIT que consume la card.

Envoltorio fino sobre `app/services/kits_service.py`: toda la logica vive alli y
en `app/utils/kits.py`, para que el endpoint y `scripts/kits_disponibles.py`
devuelvan lo mismo por construccion.

Lee KIT disponibles, guarda las marcas de una cotizacion y crea los KIT que esa
cotizacion genera. Lo que NO hace es validar consumos: eso descuenta cajas de
verdad y vive en el asistente de pedidos, que es quien lo confirma.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.kits_service import (
    analizar,
    buscar_kits,
    crear_kits_de_cotizacion,
    guardar_marcas_de_cotizacion,
    kits_disponibles_para_card,
    negocios_generadores_por_sku,
    params_kit_por_sku,
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


class KitACrear(BaseModel):
    """Un artículo que no completa bloque y necesita su pedido KIT."""

    sku: str
    nombre: str = ""
    cajas: int = Field(gt=0)


class CrearKitsRequest(BaseModel):
    cotizacion_id: str
    kits: List[KitACrear] = Field(default_factory=list)


@router.post("/crear")
def crear_kits(payload: CrearKitsRequest):
    """Crea los pedidos KIT de una cotización.

    Lo llama la card DESPUES de publicar, no la propia publicación: así el flujo
    de publicar cotizaciones se queda como está y un fallo creando KIT no puede
    tumbarlo.

    Idempotente por (cotizacion, SKU): republicar no duplica. Los KIT que ya
    existían se devuelven en `omitidos` con su número, para que la card pueda
    decir "ya estaba creado" en vez de fingir que hizo algo.
    """
    try:
        resultado = crear_kits_de_cotizacion(
            settings.proex_access_token,
            payload.cotizacion_id,
            [k.model_dump() for k in payload.kits],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "peticion_invalida", "mensaje": str(e)})
    except RuntimeError as e:
        logger.error(f"[kits] fallo creando KIT: {e}")
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    return {"success": True, **resultado}


@router.get("/params")
def parametros_de_kit(
    skus: Optional[str] = Query(
        default=None,
        description="SKUs separados por coma. Devuelve solo los que tengan tamano_kit.",
    ),
):
    """`tamano_kit` y `bloque_multiplo` de los productos, leidos de HubSpot.

    Sustituye al fichero de parametros escrito a mano en la card. Un producto sin
    `tamano_kit` NO aparece en la respuesta: la ausencia es informacion, y la card
    no debe inventarse un bloque.
    """
    lista = _skus(skus)
    if not lista:
        return {"success": True, "params": {}, "total": 0}

    try:
        params = params_kit_por_sku(settings.proex_access_token, lista)
    except RuntimeError as e:
        logger.error(f"[kits] fallo leyendo params: {e}")
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    return {"success": True, "params": params, "total": len(params)}


@router.get("/negocios")
def negocios_generadores(
    skus: Optional[str] = Query(
        default=None,
        description="SKUs separados por coma.",
    ),
    excluir: Optional[str] = Query(
        default=None,
        description="ID de la cotizacion actual: va siempre dentro y no se ofrece.",
    ),
):
    """Ofertas abiertas que podrian generar el KIT junto con esta cotizacion.

    Un pedido KIT no nace de una sola oferta: PROEX suma las cajas de varias
    ofertas abiertas de la misma referencia y pide un unico KIT por la diferencia.
    Devuelve, por SKU, el negocio y sus cajas —sumadas de todos sus contenedores—
    para que la card pueda recalcular la produccion al anotarlos.

    El filtro es el de la reunion del 2026-09-03: pipeline de cotizacion, ni
    ganado ni perdido, y con cajas de esa referencia.

    Una lista vacia es una respuesta valida y frecuente: significa que esta oferta
    es la unica abierta con esa referencia.
    """
    lista = _skus(skus)
    if not lista:
        return {"success": True, "negocios": {}, "total": 0}

    try:
        negocios = negocios_generadores_por_sku(
            settings.proex_access_token, lista, excluir_deal_id=excluir or ""
        )
    except RuntimeError as e:
        logger.error(f"[kits] fallo leyendo negocios generadores: {e}")
        raise HTTPException(status_code=502, detail={"error": "hubspot_error", "mensaje": str(e)})

    return {
        "success": True,
        "negocios": negocios,
        "total": sum(len(v) for v in negocios.values()),
    }


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
