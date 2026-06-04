"""Helpers para aplicar IVA repercutido y recargo de equivalencia al
precio de un contenedor.

El `contenedor.price` se calcula con la MISMA fórmula que `pricing.utils.js`
del frontend (NegociacionTab): `Σ round(precio_caja_incoterm, 3) × cajas`
por línea fabricante, con un único redondeo combinado por caja antes de
multiplicar por cantidad. Los Elementos persisten sus propios `precio_total`
con redondeo a 3 decimales (uso documental), pero NO son la fuente de
verdad para `contenedor.price`."""

import logging
import time
from typing import Any, Dict, List, Optional

from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR
from app.services.custom_objects import (
    batch_update_custom_objects,
    get_elementos_by_deal,
    update_contenedor,
)
from app.shared import hubdb_client
from app.utils.pricing import (
    PricingParams,
    _fetch_pricing_params_for_deal,
    calcular_precio_por_caja_por_incoterm,
)
from app.utils.property_helpers import _normalize_residennum

logger = logging.getLogger(__name__)


def aplica_recargo_equivalencia(company_props: Optional[Dict]) -> bool:
    if not company_props:
        return False
    return (company_props.get("equ") or "").strip().lower() == "si"


def resolver_tipo_residencia(company_props: Optional[Dict]) -> str:
    if not company_props:
        return "NACIONAL"
    return _normalize_residennum(company_props.get("residennum"))


def _compute_price_from_fabricantes(
    fabricantes: List[Dict],
    company_props: Optional[Dict],
    pricing_params: PricingParams,
) -> float:
    """Calcula el precio del contenedor con IVA desde una lista ya filtrada de
    line items fabricante. Función pura (sin I/O). Replica la fórmula de
    `pricing.utils.js`: un único redondeo combinado por línea antes de multiplicar
    por cantidad."""
    residencia = resolver_tipo_residencia(company_props)
    aplica_re = aplica_recargo_equivalencia(company_props)

    total = 0.0
    for li in fabricantes:
        try:
            precio_exw = float(li.get("price") or 0)
        except (TypeError, ValueError):
            precio_exw = 0.0
        try:
            cajas = float(li.get("quantity") or 0)
        except (TypeError, ValueError):
            cajas = 0.0
        if cajas <= 0:
            continue

        precio_caja_inc = calcular_precio_por_caja_por_incoterm(
            precio_exw,
            pricing_params.incoterm,
            hasta_localidad_destino=pricing_params.hasta_localidad_destino,
            hasta_puerto_salida=pricing_params.hasta_puerto_salida,
            hasta_puerto_destino=pricing_params.hasta_puerto_destino,
            hasta_puerto_destino_localidad=pricing_params.hasta_puerto_destino_localidad,
            servicios_por_caja=pricing_params.servicios_por_caja,
            seguro_por_caja=pricing_params.seguro_por_caja,
            comision_cliente=pricing_params.comision_cliente,
        )
        precio_caja_redondeado = round(precio_caja_inc, 3)
        subtotal = precio_caja_redondeado * cajas

        iva_code = (li.get("tipo_iva_repercutido") or "").strip().upper()
        iva_pct, rec_pct = hubdb_client.get_porcentajes_iva_repercutido(iva_code, residencia)
        iva_pct = iva_pct or 0.0
        rec_pct = (rec_pct or 0.0) if aplica_re else 0.0
        total += subtotal * (1.0 + (iva_pct + rec_pct) / 100.0)
    return round(total, 2)


def _compute_peso_from_elementos(elementos: List[Dict]) -> Dict[str, float]:
    """Suma `peso_*_unitario × cantidad` sobre Elementos fabricante. Función pura."""
    peso_neto = 0.0
    peso_bruto = 0.0
    for el in elementos:
        props = el.get("properties") or {}
        if (props.get("tipo_articulo") or "").strip().lower() != "fabricante":
            continue
        try:
            cantidad = float(props.get("cantidad") or 0)
        except (TypeError, ValueError):
            cantidad = 0.0
        try:
            pn = float(props.get("peso_neto_unitario") or 0)
        except (TypeError, ValueError):
            pn = 0.0
        try:
            pb = float(props.get("peso_bruto_unitario") or 0)
        except (TypeError, ValueError):
            pb = 0.0
        peso_neto += pn * cantidad
        peso_bruto += pb * cantidad
    return {"peso_neto": round(peso_neto, 3), "peso_bruto": round(peso_bruto, 3)}


def calcular_precio_contenedor_con_iva(
    client,
    deal_id: str,
    contenedor_props: Dict,
    company_props: Optional[Dict],
    pricing_params: PricingParams,
) -> float:
    """[LEGACY] Versión que hace fetch propio de los line items fabricante del
    contenedor. Mantenida por callers externos; el path optimizado de
    `recalcular_contenedores` usa `_compute_price_from_fabricantes` con datos
    pre-cacheados."""
    from app.utils.pricing import _read_fabricante_line_items_for_container

    container_name = (contenedor_props or {}).get("nombre") or ""
    fabricantes = _read_fabricante_line_items_for_container(client, deal_id, container_name)
    return _compute_price_from_fabricantes(fabricantes, company_props, pricing_params)


def _batch_read_contenedores_props(client, contenedor_co_ids: List[str]) -> Dict[str, Dict]:
    """Batch read N contenedores con las props necesarias para recálculo.
    Devuelve `{id: properties_dict}` con chunking de 100."""
    if not contenedor_co_ids:
        return {}

    properties = ["nombre", "importe_total_cobrado"]
    result: Dict[str, Dict] = {}
    for i in range(0, len(contenedor_co_ids), 100):
        chunk = contenedor_co_ids[i:i + 100]
        try:
            resp = client.crm.objects.batch_api.read(
                object_type=OBJECT_TYPE_CONTENEDOR,
                batch_read_input_simple_public_object_id={
                    "properties": properties,
                    "inputs": [{"id": str(cid)} for cid in chunk],
                },
            )
            for r in (resp.results or []):
                result[str(r.id)] = r.properties or {}
        except Exception as e:
            logger.warning(f"[recalcular] batch read contenedores chunk [{i}:{i+len(chunk)}] falló: {e}")
    return result


def _batch_read_line_items_superset(client, line_item_ids: List[str]) -> List[Dict]:
    """Batch read de N line items del deal con superset de props (las que
    necesitan tanto `_fetch_pricing_params_for_deal` como
    `_compute_price_from_fabricantes`). Devuelve lista de dicts `properties`."""
    if not line_item_ids:
        return []

    properties = [
        "hs_product_type", "tipo_articulo", "price", "quantity",
        "numero_container", "tipo_iva_repercutido",
    ]
    items: List[Dict] = []
    for i in range(0, len(line_item_ids), 100):
        chunk = line_item_ids[i:i + 100]
        try:
            resp = client.crm.line_items.batch_api.read(
                batch_read_input_simple_public_object_id={
                    "properties": properties,
                    "inputs": [{"id": str(lid)} for lid in chunk],
                },
            )
            for li in (resp.results or []):
                items.append(li.properties or {})
        except Exception as e:
            logger.warning(f"[recalcular] batch read line items chunk [{i}:{i+len(chunk)}] falló: {e}")
    return items


def _index_fabricantes_by_container(line_items: List[Dict]) -> Dict[str, List[Dict]]:
    """Filtra line items fabricante y los agrupa por `numero_container`."""
    from app.routes.deals import _line_item_is_article

    index: Dict[str, List[Dict]] = {}
    for props in line_items:
        if not _line_item_is_article(props):
            continue
        nc = (props.get("numero_container") or "").strip()
        if not nc:
            continue
        index.setdefault(nc, []).append(props)
    return index


def _index_elementos_by_container(elementos: List[Dict]) -> Dict[str, List[Dict]]:
    """Agrupa elementos por la prop `nombre_contenedor`."""
    index: Dict[str, List[Dict]] = {}
    for el in elementos:
        props = el.get("properties") or {}
        nc = (props.get("nombre_contenedor") or "").strip()
        if not nc:
            continue
        index.setdefault(nc, []).append(el)
    return index


def recalcular_contenedores(
    client,
    deal_id: str,
    contenedor_co_ids,
    company_props: Optional[Dict] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Recalcula `price`, `importe_pendiente_cobro`, `peso_neto` y `peso_bruto`
    para los contenedores indicados en 3 fases optimizadas:

    1. **Fetch compartido** (4 batch reads): pricing_params, contenedores,
       line items del deal, elementos del deal. Cualquier dato que antes se
       refetcheaba por contenedor se carga una sola vez.
    2. **Compute en memoria**: por cada contenedor, calcular precio y peso
       desde los índices ya cargados. Sin I/O.
    3. **Batch update final** (1 sola call): actualizar todos los contenedores
       afectados en una única llamada a HubSpot.

    Pasa de O(N) calls secuenciales a O(1) llamadas batch.
    """
    if not contenedor_co_ids:
        return {}
    contenedor_co_ids = [str(c) for c in contenedor_co_ids]

    overall_start = time.time()

    fetch_start = time.time()

    from app.routes.deals import _get_associated_ids
    try:
        line_item_ids = _get_associated_ids(deal_id, "line_items") or []
    except Exception as e:
        logger.warning(f"[recalcular] no pude listar line items del deal {deal_id}: {e}")
        line_item_ids = []

    line_items_all = _batch_read_line_items_superset(client, line_item_ids)
    pricing_params = _fetch_pricing_params_for_deal(client, deal_id, line_items_precached=line_items_all)
    fabricantes_by_container = _index_fabricantes_by_container(line_items_all)

    contenedores_by_id = _batch_read_contenedores_props(client, contenedor_co_ids)

    try:
        elementos_all = get_elementos_by_deal(
            client,
            deal_id,
            properties=[
                "tipo_articulo", "cantidad",
                "peso_neto_unitario", "peso_bruto_unitario",
                "nombre_contenedor",
            ],
        )
    except Exception as e:
        logger.warning(f"[recalcular] no pude listar elementos del deal {deal_id}: {e}")
        elementos_all = []
    elementos_by_container = _index_elementos_by_container(elementos_all)

    logger.info(
        f"⏱️  [TIMER] recalcular fetch_phase: {time.time() - fetch_start:.2f}s "
        f"(line_items={len(line_items_all)}, contenedores={len(contenedores_by_id)}, "
        f"elementos={len(elementos_all)})"
    )

    compute_start = time.time()
    updates: List[Dict[str, Any]] = []
    resultados: Dict[str, Optional[Dict[str, Any]]] = {}

    for cid in contenedor_co_ids:
        cont_props = contenedores_by_id.get(cid)
        if not cont_props:
            logger.warning(f"[recalcular] contenedor {cid} no encontrado en batch read; salto")
            resultados[cid] = None
            continue
        container_name = (cont_props.get("nombre") or "").strip()

        new_price = None
        nuevo_pendiente = None
        try:
            fabricantes = fabricantes_by_container.get(container_name, [])
            new_price = _compute_price_from_fabricantes(fabricantes, company_props, pricing_params)
            cobrado_raw = cont_props.get("importe_total_cobrado")
            try:
                cobrado = float(cobrado_raw) if cobrado_raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                cobrado = 0.0
            nuevo_pendiente = max(0.0, round(new_price - cobrado, 2))
        except Exception as e:
            logger.warning(f"[recalcular] price calc fallo en {cid}: {e}")

        pesos: Optional[Dict[str, float]] = None
        try:
            elems_cid = elementos_by_container.get(container_name, [])
            pesos = _compute_peso_from_elementos(elems_cid)
        except Exception as e:
            logger.warning(f"[recalcular] peso calc fallo en {cid}: {e}")

        props_update: Dict[str, str] = {}
        if new_price is not None:
            props_update["price"] = str(new_price)
            props_update["importe_pendiente_cobro"] = str(nuevo_pendiente)
        if pesos is not None:
            props_update["peso_neto"] = str(pesos["peso_neto"])
            props_update["peso_bruto"] = str(pesos["peso_bruto"])
        if props_update:
            updates.append({"id": cid, "properties": props_update})

        resultado: Dict[str, Any] = {}
        if new_price is not None:
            resultado["price"] = new_price
            resultado["importe_pendiente_cobro"] = nuevo_pendiente
        if pesos:
            resultado.update(pesos)
        resultados[cid] = resultado or None

    logger.info(
        f"⏱️  [TIMER] recalcular compute_phase: {time.time() - compute_start:.2f}s "
        f"(computed {len(updates)} updates)"
    )

    if updates:
        update_start = time.time()
        try:
            batch_update_custom_objects(client, OBJECT_TYPE_CONTENEDOR, updates)
        except Exception as e:
            logger.warning(f"[recalcular] batch update fallo: {e}")
        logger.info(f"⏱️  [TIMER] recalcular update_phase: {time.time() - update_start:.2f}s")
    else:
        logger.info("⏱️  [TIMER] recalcular update_phase: 0.00s (sin updates)")

    logger.info(
        f"⏱️  [TIMER] recalcular_contenedores TOTAL: {time.time() - overall_start:.2f}s "
        f"(N={len(contenedor_co_ids)})"
    )
    return resultados


def recalcular_contenedor(
    client,
    deal_id: str,
    contenedor_co_id: str,
    company_props: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Wrapper singular sobre `recalcular_contenedores` para preservar la firma
    pública usada en otras rutas (p.ej. update de un único contenedor)."""
    cid = str(contenedor_co_id)
    return (recalcular_contenedores(client, deal_id, [cid], company_props) or {}).get(cid)
