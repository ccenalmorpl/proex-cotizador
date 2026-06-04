"""Cálculo de precio por caja por incoterm (espejo Python de pricing.utils.js
del frontend NegociacionTab). El backend usa esta misma fórmula para
calcular `contenedor.price` con el mismo redondeo combinado que el frontend,
evitando desajustes de redondeo entre lo que se muestra y lo persistido."""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PricingParams:
    """Parámetros derivados del deal para calcular el precio por caja por
    incoterm. Espejo del `pricingParams` de NegociacionTab."""

    incoterm: str
    hasta_localidad_destino: float
    hasta_puerto_salida: float
    hasta_puerto_destino: float
    hasta_puerto_destino_localidad: float
    servicios_por_caja: float
    seguro_por_caja: float
    comision_cliente: float
    total_cajas: float
    total_contenedores: int


def _normalize_incoterm(value) -> str:
    v = (value or "EXW")
    if isinstance(v, dict):
        v = v.get("value") or v.get("internal") or v.get("label") or "EXW"
    return str(v).strip().upper().replace(" ", "_")


def calcular_precio_por_caja_por_incoterm(
    precio_exw: float,
    incoterm: str,
    *,
    hasta_localidad_destino: float = 0.0,
    hasta_puerto_salida: float = 0.0,
    hasta_puerto_destino: float = 0.0,
    hasta_puerto_destino_localidad: float = 0.0,
    servicios_por_caja: float = 0.0,
    seguro_por_caja: float = 0.0,
    comision_cliente: float = 0.0,
) -> float:
    """Espejo de `pricing.utils.js:calcularPrecioPorCajaPorIncoterm`."""
    ic = _normalize_incoterm(incoterm)
    if ic == "EXW":
        return precio_exw + comision_cliente
    if ic == "DAP_TERRESTRE":
        return precio_exw + hasta_localidad_destino + servicios_por_caja + comision_cliente
    if ic == "FOB":
        return (
            precio_exw
            + hasta_puerto_salida
            + servicios_por_caja
            - seguro_por_caja
            + comision_cliente
        )
    if ic == "CFR":
        return (
            precio_exw
            + hasta_puerto_salida
            + servicios_por_caja
            - seguro_por_caja
            + hasta_puerto_destino
            + comision_cliente
        )
    if ic == "CIF":
        return (
            precio_exw
            + hasta_puerto_salida
            + servicios_por_caja
            + hasta_puerto_destino
            + comision_cliente
        )
    if ic == "DAP_MARITIMO":
        return (
            precio_exw
            + hasta_puerto_salida
            + servicios_por_caja
            + hasta_puerto_destino
            + hasta_puerto_destino_localidad
            + comision_cliente
        )
    return precio_exw + comision_cliente


def _safe_float(value) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _comision_por_caja(
    tipo_comision: str,
    importe_comision: float,
    total_cajas: float,
    total_contenedores: int,
) -> float:
    """Espejo de NegociacionTab `comisionPorCaja` (líneas 226-255)."""
    if not tipo_comision or importe_comision <= 0 or total_cajas <= 0:
        return 0.0
    tc = (tipo_comision or "").strip().lower()
    if tc == "importe_por_negocio":
        return importe_comision / total_cajas
    if tc == "importe_por_contenedor":
        return (importe_comision * total_contenedores) / total_cajas
    if tc == "importe_por_cajas":
        return importe_comision
    return 0.0


def _fetch_pricing_params_for_deal(
    client,
    deal_id: str,
    line_items_precached: Optional[List[Dict]] = None,
) -> PricingParams:
    """Construye `PricingParams` a partir del deal y sus line items en HubSpot.
    Replica exactamente los cálculos de NegociacionTab (`gastosPorTramo`,
    `serviciosPorCaja`, `seguroPorCaja`, `comisionPorCaja`).

    `line_items_precached` permite saltarse el batch read interno cuando el
    caller ya tiene los props de los line items del deal cargados en memoria
    (mínimo `hs_product_type`, `tipo_articulo`, `price`, `quantity`).
    """
    from app.routes.deals import (
        _get_associated_ids,
        _line_item_is_article,
        _line_item_is_service,
        _is_container_product_type,
    )

    deal_props: Dict[str, str] = {}
    try:
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id=deal_id,
            properties=[
                "tipo_de_venta",
                "precio_ruta_hasta_puerto_de_salida",
                "precio_ruta_hasta_puerto_destino",
                "repercutir_a_cliente",
                "tipo_comision",
                "importe_comision",
                "codigo_intermediario",
            ],
        )
        deal_props = getattr(deal, "properties", None) or {}
    except Exception as e:
        logger.warning(f"[pricing] no pude leer deal {deal_id}: {e}")

    incoterm = _normalize_incoterm(deal_props.get("tipo_de_venta"))
    precio_origen = _safe_float(deal_props.get("precio_ruta_hasta_puerto_de_salida"))
    precio_destino = _safe_float(deal_props.get("precio_ruta_hasta_puerto_destino"))
    repercutir = (deal_props.get("repercutir_a_cliente") or "").strip().lower() == "si"
    tipo_comision = deal_props.get("tipo_comision") or ""
    importe_comision = _safe_float(deal_props.get("importe_comision"))
    tiene_intermediario = bool((deal_props.get("codigo_intermediario") or "").strip())

    total_cajas = 0.0
    total_contenedores = 0
    total_servicios = 0.0
    total_seguro = 0.0

    def _aggregate(props: Dict) -> None:
        nonlocal total_cajas, total_contenedores, total_servicios, total_seguro
        pt = (props.get("hs_product_type") or "").strip()
        ta = (props.get("tipo_articulo") or "").strip().lower()
        price = _safe_float(props.get("price"))
        qty = _safe_float(props.get("quantity"))
        if _is_container_product_type(pt):
            total_contenedores += 1
            return
        if _line_item_is_article(props):
            total_cajas += qty
            return
        if _line_item_is_service(props):
            if ta in ("transitario", "comisiones", "flete"):
                return
            if ta == "seguro":
                total_seguro += price
            total_servicios += price

    if line_items_precached is not None:
        for props in line_items_precached:
            _aggregate(props or {})
    else:
        line_item_ids: List[str] = []
        try:
            line_item_ids = _get_associated_ids(deal_id, "line_items")
        except Exception as e:
            logger.warning(f"[pricing] no pude listar line items del deal {deal_id}: {e}")

        properties = [
            "hs_product_type", "tipo_articulo", "price", "quantity",
        ]
        chunk = 100
        for i in range(0, len(line_item_ids), chunk):
            ids = line_item_ids[i:i + chunk]
            try:
                resp = client.crm.line_items.batch_api.read(
                    batch_read_input_simple_public_object_id={
                        "properties": properties,
                        "inputs": [{"id": str(lid)} for lid in ids],
                    },
                )
            except Exception as e:
                logger.warning(f"[pricing] batch read line items falló: {e}")
                continue
            for li in (resp.results or []):
                _aggregate(li.properties or {})

    if total_cajas <= 0 or total_contenedores <= 0:
        return PricingParams(
            incoterm=incoterm,
            hasta_localidad_destino=0.0,
            hasta_puerto_salida=0.0,
            hasta_puerto_destino=0.0,
            hasta_puerto_destino_localidad=0.0,
            servicios_por_caja=0.0,
            seguro_por_caja=0.0,
            comision_cliente=0.0,
            total_cajas=total_cajas,
            total_contenedores=total_contenedores,
        )

    factor_caja = total_contenedores / total_cajas
    hasta_puerto_salida = precio_origen * factor_caja
    hasta_puerto_destino = precio_destino * factor_caja
    servicios_por_caja = total_servicios / total_cajas
    seguro_por_caja = total_seguro / total_cajas

    comision_cliente = 0.0
    if repercutir and tiene_intermediario:
        comision_cliente = _comision_por_caja(
            tipo_comision, importe_comision, total_cajas, total_contenedores,
        )

    return PricingParams(
        incoterm=incoterm,
        hasta_localidad_destino=hasta_puerto_salida,
        hasta_puerto_salida=hasta_puerto_salida,
        hasta_puerto_destino=hasta_puerto_destino,
        hasta_puerto_destino_localidad=hasta_puerto_destino,
        servicios_por_caja=servicios_por_caja,
        seguro_por_caja=seguro_por_caja,
        comision_cliente=comision_cliente,
        total_cajas=total_cajas,
        total_contenedores=total_contenedores,
    )


def _read_fabricante_line_items_for_container(
    client,
    deal_id: str,
    container_name: str,
) -> List[Dict]:
    """Devuelve los line items fabricante asociados al contenedor concreto
    del deal. Mismas reglas que `_line_item_is_article` (hs_product_type
    article + tipo_articulo=fabricante) y filtrados por que el
    `numero_container` del line item coincida con `container_name`
    (formato `{deal_id}-{seq:03d}`, idéntico a `Contenedor.nombre`)."""
    from app.routes.deals import _get_associated_ids, _line_item_is_article

    if not container_name:
        return []
    target = container_name.strip()
    line_item_ids: List[str] = []
    try:
        line_item_ids = _get_associated_ids(deal_id, "line_items")
    except Exception as e:
        logger.warning(f"[pricing] no pude listar line items del deal {deal_id}: {e}")
        return []

    properties = [
        "hs_product_type", "tipo_articulo", "numero_container",
        "price", "quantity", "tipo_iva_repercutido",
    ]
    fabricantes: List[Dict] = []
    chunk = 100
    for i in range(0, len(line_item_ids), chunk):
        ids = line_item_ids[i:i + chunk]
        try:
            resp = client.crm.line_items.batch_api.read(
                batch_read_input_simple_public_object_id={
                    "properties": properties,
                    "inputs": [{"id": str(lid)} for lid in ids],
                },
            )
        except Exception as e:
            logger.warning(f"[pricing] batch read fabricantes falló: {e}")
            continue
        for li in (resp.results or []):
            props = li.properties or {}
            if not _line_item_is_article(props):
                continue
            nc = (props.get("numero_container") or "").strip()
            if nc != target:
                continue
            fabricantes.append(props)
    return fabricantes
