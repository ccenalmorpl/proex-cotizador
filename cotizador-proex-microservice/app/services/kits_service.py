"""Lectura de pedidos KIT desde HubSpot.

Fuente unica de verdad para "que KIT hay disponibles": la usan tanto el endpoint
que consume la card como `scripts/kits_disponibles.py`. Si divergieran, el
comercial veria en la card algo distinto de lo que audita el script.

Solo LEE. Las escrituras del ciclo de vida viven en `scripts/kits_ciclo.py`
mientras no haya decision de llevarlas al backend.

Aislado a proposito: no importa nada de los servicios existentes (ni colas, ni
DynamoDB, ni el cliente de HubSpot compartido), asi que se puede levantar solo.
"""

import json
import logging
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional

from app.core.kits_constants import (
    KIT_DEALSTAGE_ABIERTO,
    KIT_DEALSTAGE_LABELS,
    KIT_PIPELINE_ID,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_CONSUMIDO_POR_IDS,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_NOMBRE,
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_VALIDACION_PRODUCCION,
)
from app.utils.kits import (
    aplicar_marca_kit,
    calcular_cajas_disponibles,
    calcular_dealstage_kit,
    parsear_marcas_kit,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

PROPIEDADES = [
    "dealname",
    "dealstage",
    "amount",
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_NOMBRE,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_VALIDACION_PRODUCCION,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_CONSUMIDO_POR_IDS,
]


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def _patch(deal_id: str, propiedades: dict, token: str) -> dict:
    """Unico punto de escritura del servicio."""
    req = urllib.request.Request(
        f"{BASE_URL}/crm/v3/objects/deals/{deal_id}",
        data=json.dumps({"properties": propiedades}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            logger.info(f"[kits] PATCH deal={deal_id} props={list(propiedades)}")
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code} al escribir deal={deal_id}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def _post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def buscar_kits(token: str) -> List[dict]:
    """Todos los negocios del pipeline KIT, paginando."""
    kits: List[dict] = []
    after: Optional[str] = None

    while True:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "pipeline", "operator": "EQ", "value": KIT_PIPELINE_ID}
                    ]
                }
            ],
            "properties": PROPIEDADES,
            "limit": 100,
        }
        if after:
            body["after"] = after

        res = _post(f"{BASE_URL}/crm/v3/objects/deals/search", body, token)
        kits.extend(res.get("results", []))

        after = (res.get("paging") or {}).get("next", {}).get("after")
        if not after:
            logger.info(f"[kits] {len(kits)} KIT leidos del pipeline {KIT_PIPELINE_ID}")
            return kits


def analizar(kits: List[dict], skus: Optional[List[str]] = None) -> dict:
    """Agrupa por SKU, decide consumibles y reune los descuadres.

    Funcion pura: no llama a HubSpot. Recibe lo que devolvio `buscar_kits`.
    """
    filtro = {s.strip() for s in (skus or []) if s and s.strip()}
    por_sku = defaultdict(list)
    avisos: List[str] = []

    for d in kits:
        p = d.get("properties") or {}
        sku = (p.get(PROP_KIT_SKU) or "").strip()
        numero = p.get(PROP_KIT_NUMERO) or ""
        etapa = p.get("dealstage") or ""
        validado = (p.get(PROP_KIT_VALIDACION_PRODUCCION) or "").lower() == "true"

        disponibles = calcular_cajas_disponibles(
            p.get(PROP_KIT_CAJAS), p.get(PROP_KIT_CONSUMIDO_POR_IDS)
        )
        etapa_derivada = calcular_dealstage_kit(
            validado_produccion=validado,
            cajas_disponibles=disponibles,
            dealstage_actual=etapa,
        )

        if not sku:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): {PROP_KIT_SKU} vacio -> "
                f"la card no lo vera, empareja por SKU"
            )
        if not numero:
            avisos.append(f"deal {d['id']}: {PROP_KIT_NUMERO} vacio")
        if etapa_derivada != etapa:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): etapa descuadra -> esta en "
                f"'{KIT_DEALSTAGE_LABELS.get(etapa, etapa)}' y le toca "
                f"'{KIT_DEALSTAGE_LABELS.get(etapa_derivada, etapa_derivada)}'"
            )

        cacheadas = p.get(PROP_KIT_CAJAS_DISPONIBLES)
        if cacheadas not in (None, "") and int(float(cacheadas)) != disponibles:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): {PROP_KIT_CAJAS_DISPONIBLES}="
                f"{cacheadas} pero el calculo da {disponibles}"
            )
        if p.get("amount"):
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): tiene amount={p['amount']} -> "
                f"un KIT no factura, entraria en el forecast"
            )

        if filtro and sku not in filtro:
            continue

        por_sku[sku].append(
            {
                "deal_id": d["id"],
                "numero": numero,
                "nombre": p.get(PROP_KIT_NOMBRE) or "",
                "cajas_kit": int(float(p.get(PROP_KIT_CAJAS) or 0)),
                "disponibles": disponibles,
                "etapa": etapa,
                "etapa_label": KIT_DEALSTAGE_LABELS.get(etapa, etapa),
                "etapa_derivada": etapa_derivada,
                "etapa_derivada_label": KIT_DEALSTAGE_LABELS.get(etapa_derivada, etapa_derivada),
                "validado": validado,
                "marcas": parsear_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS)),
                "consumos": parsear_marcas_kit(p.get(PROP_KIT_CONSUMIDO_POR_IDS)),
                # Consumible es exactamente "la etapa derivada es Abierto": ya
                # incorpora validado, disponibles > 0 y que Cancelado es pegajoso.
                # Se mide contra la DERIVADA y no la almacenada para que una etapa
                # obsoleta no oculte un KIT que por propiedades si sirve.
                "consumible": etapa_derivada == KIT_DEALSTAGE_ABIERTO,
            }
        )

    return {"por_sku": dict(por_sku), "avisos": avisos}


def guardar_marcas_de_cotizacion(
    token: str, cotizacion_id: str, marcas: Dict[str, int]
) -> dict:
    """Persiste las marcas de UNA cotizacion sobre los KIT.

    Recibe el conjunto completo de marcas de esa cotizacion (`{kit_id: cajas}`) y
    deja HubSpot igual: escribe las que hay y BORRA la marca de los KIT que ya no
    aparecen. Es idempotente, asi que reenviar lo mismo no cambia nada y volver
    atras en la card desmarca de verdad.

    No valida solapes a proposito. Marcar no bloquea: varios comerciales pueden
    marcar el mismo KIT y todos ven quien lo marco. Quien reparte de verdad es
    Administracion al validar el consumo, que es el unico paso que descuenta.

    Marcar tampoco mueve la etapa ni toca `cajas_disponibles_kit`: una reserva sin
    validar puede caerse, y si descontara, una oferta perdida dejaria el KIT
    bloqueado para siempre.
    """
    cotizacion_id = str(cotizacion_id or "").strip()
    if not cotizacion_id:
        raise ValueError("cotizacion_id es obligatorio")

    pedidas = {str(k).strip(): int(v or 0) for k, v in (marcas or {}).items() if str(k).strip()}

    actualizados, sin_cambio = [], []

    for d in buscar_kits(token):
        kit_id = d["id"]
        p = d.get("properties") or {}
        raw = p.get(PROP_KIT_MARCADO_POR_IDS)

        # Nunca por encima de lo que el KIT tiene libre, aunque llegue de mas.
        tope = calcular_cajas_disponibles(p.get(PROP_KIT_CAJAS), p.get(PROP_KIT_CONSUMIDO_POR_IDS))
        cajas = min(pedidas.get(kit_id, 0), tope)

        nuevo = aplicar_marca_kit(raw, cotizacion_id, cajas)
        if nuevo == (raw or ""):
            sin_cambio.append(kit_id)
            continue

        _patch(kit_id, {PROP_KIT_MARCADO_POR_IDS: nuevo}, token)
        actualizados.append(
            {
                "kit_id": kit_id,
                "numero": p.get(PROP_KIT_NUMERO) or "",
                "cajas": cajas,
                "marcas": parsear_marcas_kit(nuevo),
            }
        )

    logger.info(
        f"[kits] marcas de cotizacion={cotizacion_id}: "
        f"{len(actualizados)} KIT actualizados, {len(sin_cambio)} sin cambio"
    )
    return {"actualizados": actualizados, "sin_cambio": len(sin_cambio)}


def kits_disponibles_para_card(token: str, skus: Optional[List[str]] = None) -> List[Dict]:
    """KIT consumibles con la forma exacta que pinta `PanelKitsDisponibles`.

    `marcas` va vacio por ahora: el nombre del negocio marcador y su propietario
    exigen leer cada negocio y su owner, y eso es una decision de coste (N+1
    llamadas) que conviene tomar aparte, no colarla aqui.
    """
    analisis = analizar(buscar_kits(token), skus)

    return [
        {
            "id": f["deal_id"],
            "numeroDeKit": f["numero"],
            "productoSku": sku,
            "productoNombre": f["nombre"],
            "cajasDisponibles": f["disponibles"],
            "cajasKit": f["cajas_kit"],
            "marcas": [],
        }
        for sku, filas in sorted(analisis["por_sku"].items())
        for f in filas
        if f["consumible"]
    ]
