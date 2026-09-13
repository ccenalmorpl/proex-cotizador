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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO
from app.services.custom_objects import (
    batch_update_custom_objects,
    get_elementos_by_deal,
    update_contenedor,
)
from app.shared import hubdb_client
from app.utils.pricing import (
    PricingParams,
    _normalize_incoterm,
    _pricing_kwargs,
    build_deal_pricing_context,
    calcular_precio_por_caja_por_incoterm,
    derivar_exw_desde_final_anclado,
)
from app.utils.property_helpers import (
    _normalize_residennum,
    enforce_line_item_price_rounding,
    round_margin,
    round_price,
)

logger = logging.getLogger(__name__)


def aplica_recargo_equivalencia(company_props: Optional[Dict]) -> bool:
    if not company_props:
        return False
    return (company_props.get("equ") or "").strip().lower() == "si"


def resolver_tipo_residencia(company_props: Optional[Dict]) -> str:
    if not company_props:
        return "NACIONAL"
    return _normalize_residennum(company_props.get("residennum"))


# Componentes incluidos en el precio por incoterm (espejo de
# calcular_precio_por_caja_por_incoterm). Se usan como gates para repercutir el
# IVA de cada concepto solo cuando forma parte de la base. El seguro NO entra en
# FOB/CFR (la fórmula lo resta); la comisión va en todos los incoterms.
_INCLUYE_FLETE_ORIGEN = {"DAP_TERRESTRE", "FOB", "CFR", "CIF", "DAP_MARITIMO"}
_INCLUYE_FLETE_DESTINO = {"CFR", "CIF", "DAP_MARITIMO"}
_INCLUYE_FLETE_DESTINO_LOCALIDAD = {"DAP_MARITIMO"}
_INCLUYE_SERVICIOS = {"DAP_TERRESTRE", "FOB", "CFR", "CIF", "DAP_MARITIMO"}
_INCLUYE_SEGURO = {"DAP_TERRESTRE", "CIF", "DAP_MARITIMO"}


def _to_float(value) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _ancla_vigente(li: Dict, incoterm: str):
    """Precio final anclado de la línea si aplica al incoterm vigente, si no None.
    Ancla 0/vacía no cuenta (promo o línea sin negociar)."""
    ancla = _to_float(li.get("precio_negociado_tipo_venta"))
    if ancla <= 0:
        return None
    if _normalize_incoterm(li.get("incoterm_precio_negociado")) != incoterm:
        return None
    return ancla


def _resolver_precios_linea(li: Dict, pp: PricingParams) -> Dict[str, Any]:
    """Precios efectivos de una línea fabricante honrando el ancla del tipo de
    venta. Con ancla vigente el final por caja es el negociado (fijo) y el EXW
    se deriva restando los add-ons del momento, con suelo margen 0; sin ancla
    (histórico) o con ancla de otro incoterm, manda el EXW persistido y el
    final se deriva como siempre.

    Devuelve {"exw", "final", "anclada", "clamped"}."""
    ancla = _ancla_vigente(li, pp.incoterm)
    if ancla is not None:
        derived = derivar_exw_desde_final_anclado(ancla, li.get("precio_de_coste"), pp)
        return {
            "exw": derived["exw"],
            "final": derived["final_efectivo"],
            "anclada": True,
            "clamped": derived["clamped"],
        }
    precio_exw = _to_float(li.get("price"))
    final = calcular_precio_por_caja_por_incoterm(
        precio_exw,
        pp.incoterm,
        hasta_localidad_destino=pp.hasta_localidad_destino,
        hasta_puerto_salida=pp.hasta_puerto_salida,
        hasta_puerto_destino=pp.hasta_puerto_destino,
        hasta_puerto_destino_localidad=pp.hasta_puerto_destino_localidad,
        servicios_por_caja=pp.servicios_por_caja,
        seguro_por_caja=pp.seguro_por_caja,
        comision_cliente=pp.comision_cliente,
    )
    return {"exw": precio_exw, "final": final, "anclada": False, "clamped": False}


def _compute_price_from_fabricantes(
    fabricantes: List[Dict],
    company_props: Optional[Dict],
    pricing_params: PricingParams,
) -> float:
    """Calcula el precio del contenedor con IVA desde una lista ya filtrada de
    line items fabricante. Función pura (sin I/O salvo el cache HubDB de IVA).

    La base (sin IVA) replica `pricing.utils.js`: precio combinado por caja SIN
    redondear multiplicado por las cajas (el redondeo se aplica solo al total, a 2
    decimales), para que la suma cuadre con SAP. El IVA se descompone por concepto
    —cada uno con su propio tipo (producto, flete, servicios, seguro, comisión)—
    en vez de gravar todo el precio combinado con el IVA del producto."""
    residencia = resolver_tipo_residencia(company_props)
    aplica_re = aplica_recargo_equivalencia(company_props)
    pp = pricing_params
    ic = pp.incoterm

    _pct_cache: Dict[str, float] = {}

    def pct_efectivo(code: Optional[str]) -> float:
        code = (code or "").strip().upper()
        if code in _pct_cache:
            return _pct_cache[code]
        iva_pct, rec_pct = hubdb_client.get_porcentajes_iva_repercutido(code, residencia)
        val = (iva_pct or 0.0) + ((rec_pct or 0.0) if aplica_re else 0.0)
        _pct_cache[code] = val
        return val

    # IVA por caja de los conceptos compartidos del deal (constante por caja,
    # gateado por el incoterm). Se multiplica luego por las cajas de cada línea.
    flete_origen_por_caja = (
        pp.hasta_localidad_destino if ic == "DAP_TERRESTRE" else pp.hasta_puerto_salida
    )
    # Tramo terrestre/origen con su propio IVA (doble flete); vacío → el del
    # flete marítimo, nunca el default por residencia. Destino y localidad de
    # destino salen de precioDestino → IVA marítimo.
    tipo_iva_flete_origen = pp.tipo_iva_fletes_terrestre or pp.tipo_iva_fletes
    iva_conceptos_por_caja = pp.comision_cliente * pct_efectivo(pp.tipo_iva_comision) / 100.0
    if ic in _INCLUYE_FLETE_ORIGEN:
        iva_conceptos_por_caja += flete_origen_por_caja * pct_efectivo(tipo_iva_flete_origen) / 100.0
    if ic in _INCLUYE_FLETE_DESTINO:
        iva_conceptos_por_caja += pp.hasta_puerto_destino * pct_efectivo(pp.tipo_iva_fletes) / 100.0
    if ic in _INCLUYE_FLETE_DESTINO_LOCALIDAD:
        iva_conceptos_por_caja += pp.hasta_puerto_destino_localidad * pct_efectivo(pp.tipo_iva_fletes) / 100.0
    if ic in _INCLUYE_SERVICIOS:
        for code, importe in pp.servicios_no_seguro_por_iva_por_caja.items():
            iva_conceptos_por_caja += importe * pct_efectivo(code) / 100.0
    if ic in _INCLUYE_SEGURO:
        for code, importe in pp.seguro_por_iva_por_caja.items():
            iva_conceptos_por_caja += importe * pct_efectivo(code) / 100.0

    total_base = 0.0
    total_iva = 0.0
    for li in fabricantes:
        cajas = _to_float(li.get("quantity"))
        if cajas <= 0:
            continue
        # Línea promocional (EXW 0 sin ancla): regalo, no repercute nada.
        if _to_float(li.get("price")) == 0 and _ancla_vigente(li, ic) is None:
            continue

        resuelto = _resolver_precios_linea(li, pp)
        total_base += resuelto["final"] * cajas

        iva_producto_por_caja = resuelto["exw"] * pct_efectivo(li.get("tipo_iva_repercutido")) / 100.0
        total_iva += (iva_producto_por_caja + iva_conceptos_por_caja) * cajas

    return round(total_base + total_iva, 2)


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
        "name", "precio_de_coste", "margen_unitario",
        "precio_negociado_tipo_venta", "incoterm_precio_negociado",
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
                props = li.properties or {}
                props["hs_object_id"] = str(li.id)
                items.append(props)
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


def _index_elementos_by_lid(elementos: List[Dict]) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    for el in elementos:
        props = el.get("properties") or {}
        lid = str(props.get("line_item_id") or "").strip()
        if lid:
            index[lid] = el
    return index


@dataclass
class EmparejamientoElementos:
    by_lid: Dict[str, Dict] = field(default_factory=dict)
    lid_repairs: List[Dict[str, Any]] = field(default_factory=list)
    resueltos: int = 0
    sin_resolver: List[Dict[str, str]] = field(default_factory=list)


def _emparejar_elementos_fabricante(
    container_name: str,
    fabricantes: List[Dict],
    elems_contenedor: List[Dict],
    elem_by_lid: Dict[str, Dict],
    deal_id: str = "",
) -> EmparejamientoElementos:
    """Vista `line_item_id` vivo → Elemento de un contenedor, con fallback por
    nombre y corrección del lid cuando el Elemento apunta a un line item que ya
    se archivó.

    Un Elemento creado contra una generación de line items que luego se archiva
    deja de emparejar por lid, y sin este fallback ni el EXW ni el `precio_total`
    se sincronizan nunca más (SAP acaba leyendo importes congelados). El fallback
    solo actúa si el nombre es único en ambos lados, igual que
    `scripts/repair_elemento_lid_cruzado.py`: con promo 5+1 el nombre se repite y
    el lid es el único discriminador."""
    emp = EmparejamientoElementos()

    li_counts: Dict[str, int] = {}
    for li in fabricantes:
        nombre = (li.get("name") or "").strip()
        if nombre:
            li_counts[nombre] = li_counts.get(nombre, 0) + 1

    elem_counts: Dict[str, int] = {}
    fabricante_by_nombre: Dict[str, Dict] = {}
    for el in elems_contenedor:
        props = el.get("properties") or {}
        nombre = (props.get("nombre") or "").strip()
        if not nombre:
            continue
        elem_counts[nombre] = elem_counts.get(nombre, 0) + 1
        if (props.get("tipo_articulo") or "").strip().lower() == "fabricante":
            fabricante_by_nombre[nombre] = el

    reclamados: set = set()
    for li in fabricantes:
        lid = str(li.get("hs_object_id") or "").strip()
        if not lid:
            continue
        nombre_li = (li.get("name") or "").strip()

        motivo = ""
        elem = elem_by_lid.get(lid)
        if elem:
            elem_props = elem.get("properties") or {}
            nombre_elem = (elem_props.get("nombre") or "").strip()
            nc_elem = (elem_props.get("nombre_contenedor") or "").strip()
            if nombre_li and nombre_elem and nombre_li != nombre_elem:
                elem, motivo = None, "nombre_cruzado"
            elif nc_elem and container_name and nc_elem != container_name:
                elem, motivo = None, "otro_contenedor"
        else:
            motivo = "lid_no_resuelve"

        if not elem and nombre_li:
            if li_counts.get(nombre_li, 0) == 1 and elem_counts.get(nombre_li, 0) == 1:
                elem = fabricante_by_nombre.get(nombre_li)
                if not elem:
                    motivo = "sin_elemento_fabricante"
            elif li_counts.get(nombre_li, 0) > 1 or elem_counts.get(nombre_li, 0) > 1:
                motivo = "nombre_ambiguo"

        if elem and str(elem.get("id")) in reclamados:
            elem, motivo = None, "ya_reclamado"

        if not elem:
            emp.sin_resolver.append({"lid": lid, "nombre": nombre_li, "motivo": motivo})
            logger.warning(
                f"[recalcular] deal={deal_id} cont={container_name} line_item={lid} "
                f"nombre={nombre_li!r}: sin Elemento emparejado (motivo={motivo}); "
                f"no se sincroniza precio_unitario ni precio_total"
            )
            continue

        eid = str(elem.get("id"))
        reclamados.add(eid)
        emp.by_lid[lid] = elem
        emp.resueltos += 1

        lid_actual = str((elem.get("properties") or {}).get("line_item_id") or "").strip()
        if lid_actual != lid:
            emp.lid_repairs.append({"id": eid, "properties": {"line_item_id": lid}})
            logger.info(
                f"[recalcular] elemento {eid} reemparejado por nombre {nombre_li!r}: "
                f"line_item_id {lid_actual or '(vacío)'} -> {lid}"
            )

    return emp


def _derivar_writebacks_lineas(
    fabricantes: List[Dict],
    pp: PricingParams,
    elem_by_lid: Dict[str, Dict],
):
    """Write-backs derivados del ancla para las líneas fabricante de un contenedor.

    Ancla vigente (mismo incoterm): el final negociado queda fijo y se re-derivan
    `price` (EXW) y `margen_unitario` del line item y de su Elemento espejo, con
    suelo de margen 0 (nunca por debajo del coste). Ancla de otro incoterm: el
    deal cambió de tipo de venta, así que manda el EXW persistido y se re-ancla
    el final del nuevo incoterm como precio negociado. Líneas sin ancla
    (históricas) no se tocan. Solo emite updates cuando el valor difiere."""
    li_updates: List[Dict[str, Any]] = []
    elem_updates: List[Dict[str, Any]] = []

    for li in fabricantes:
        lid = str(li.get("hs_object_id") or "").strip()
        if not lid:
            continue
        ancla = _to_float(li.get("precio_negociado_tipo_venta"))
        if ancla <= 0:
            continue

        if _normalize_incoterm(li.get("incoterm_precio_negociado")) != pp.incoterm:
            resuelto = _resolver_precios_linea(li, pp)
            nuevo_final = round_price(resuelto["final"])
            if nuevo_final is None:
                continue
            li_updates.append({"id": lid, "properties": {
                "precio_negociado_tipo_venta": str(nuevo_final),
                "incoterm_precio_negociado": pp.incoterm,
            }})
            logger.info(
                f"[recalcular] re-ancla line item {lid}: incoterm "
                f"{li.get('incoterm_precio_negociado')!r} → {pp.incoterm} "
                f"final={nuevo_final}"
            )
            continue

        derived = derivar_exw_desde_final_anclado(ancla, li.get("precio_de_coste"), pp)
        nuevo_exw = round_price(max(0.0, derived["exw"]))
        coste = _to_float(li.get("precio_de_coste"))
        nuevo_margen = (
            round_margin((derived["exw"] - coste) / coste) if coste > 0 else None
        )
        if derived["clamped"]:
            logger.warning(
                f"[recalcular] line item {lid}: ancla {ancla} no cubre coste+add-ons; "
                f"EXW clampado al coste ({coste}), final efectivo "
                f"{round(derived['final_efectivo'], 3)}"
            )

        props: Dict[str, str] = {}
        if nuevo_exw is not None and round_price(li.get("price")) != nuevo_exw:
            props["price"] = str(nuevo_exw)
        if nuevo_margen is not None and round_margin(li.get("margen_unitario")) != nuevo_margen:
            props["margen_unitario"] = str(nuevo_margen)
        if props:
            li_updates.append({"id": lid, "properties": props})

        # El Elemento se sincroniza aunque el line item ya esté al día: es el caso
        # normal tras un cambio de flete, y gatearlo dejaba el espejo desfasado.
        elem = elem_by_lid.get(lid)
        if not elem:
            continue
        elem_props = elem.get("properties") or {}
        nombre_li = (li.get("name") or "").strip()
        nombre_elem = (elem_props.get("nombre") or "").strip()
        # lid cruzado (rotación batch heredada): no sincronizar a un Elemento ajeno.
        if nombre_li and nombre_elem and nombre_li != nombre_elem:
            logger.warning(
                f"[recalcular] elemento {elem.get('id')} lid={lid} nombre "
                f"{nombre_elem!r} ≠ line item {nombre_li!r}; salto sync"
            )
            continue
        # precio_total NO se escribe aquí: lo fija _derivar_precio_total_fabricantes
        # a nivel de contenedor (cuadre con el precio negociado, todas las líneas).
        target = {"precio_unitario": nuevo_exw}
        if nuevo_margen is not None:
            target["margen_unitario"] = nuevo_margen
        elem_diff = {
            k: str(v) for k, v in target.items()
            if v is not None and _to_float(elem_props.get(k)) != _to_float(v)
        }
        if elem_diff:
            elem_updates.append({"id": elem.get("id"), "properties": elem_diff})

    return li_updates, elem_updates


def _derivar_precio_total_fabricantes(
    fabricantes: List[Dict],
    pp: PricingParams,
    elems_contenedor: List[Dict],
    elem_by_lid: Dict[str, Dict],
) -> Dict[str, str]:
    """precio_total corregido de los Elementos fabricante de un contenedor, tal que
    Σ precio_total(fabricantes) + Σ precio_total(resto de líneas del contenedor)
    = Σ final×cajas (el total real sin IVA, el mismo que ancla contenedor.price).

    El EXW por caja se persiste a 3 decimales, así que EXW×cajas arrastra el
    residuo del redondeo (+0,40 € en 1.660 cajas) y SAP, que suma los precio_total
    de las líneas, descuadraba frente al precio negociado. Aquí cada línea vale su
    EXW exacto (final − gastos/caja) × cajas a céntimos, y el residuo restante se
    asigna a la línea con más cajas para clavar la suma. Las promos van a 0 (regalo)
    y no absorben residuo. Devuelve {elemento_id: valor} solo de los que cambian."""
    gastos_pc = calcular_precio_por_caja_por_incoterm(0.0, pp.incoterm, **_pricing_kwargs(pp))

    filas = []  # [elem_id, precio_total_persistido, cajas, base, valor]
    total_base = 0.0
    for li in fabricantes:
        cajas = _to_float(li.get("quantity"))
        if cajas <= 0:
            continue
        es_promo = _to_float(li.get("price")) == 0 and _ancla_vigente(li, pp.incoterm) is None
        base = 0.0 if es_promo else _resolver_precios_linea(li, pp)["final"] * cajas
        # La base cuenta TODAS las líneas del contenedor, también las que no se van a
        # reescribir: su precio_total viejo sigue sumando en lo que lee SAP, así que
        # cuadrar solo contra las emparejadas descuadraba el total.
        total_base += base

        lid = str(li.get("hs_object_id") or "").strip()
        elem = elem_by_lid.get(lid) if lid else None
        if not elem:
            continue
        elem_props = elem.get("properties") or {}
        nombre_li = (li.get("name") or "").strip()
        nombre_elem = (elem_props.get("nombre") or "").strip()
        # lid cruzado (rotación batch heredada): mismo guard que el writeback del ancla.
        if nombre_li and nombre_elem and nombre_li != nombre_elem:
            continue
        persistido = _to_float(elem_props.get("precio_total"))
        if es_promo:
            filas.append([str(elem.get("id")), persistido, cajas, 0.0, 0.0])
            continue
        filas.append([str(elem.get("id")), persistido, cajas, base, round(base - gastos_pc * cajas, 2)])

    if not filas:
        return {}

    ids_a_reescribir = {f[0] for f in filas}
    otras = sum(
        _to_float((el.get("properties") or {}).get("precio_total"))
        for el in elems_contenedor
        if str(el.get("id")) not in ids_a_reescribir
    )
    target = round(round(total_base, 2) - otras, 2)
    residuo = round(target - sum(f[4] for f in filas), 2)
    con_importe = [f for f in filas if f[3] > 0]
    if abs(residuo) >= 0.01:
        if not con_importe:
            logger.warning(
                f"[recalcular] precio_total fabricantes: residuo {residuo} sin línea "
                f"que lo absorba (todas promo); la suma no cuadrará con el target {target}"
            )
        else:
            if abs(residuo) > 0.05:
                # Más que céntimos de redondeo: algún add-on del incoterm no tiene
                # línea de Elemento propia (p.ej. tramo localidad DAP marítimo legacy).
                logger.warning(
                    f"[recalcular] precio_total fabricantes: residuo {residuo} "
                    f"(gastos sin línea de Elemento); se reparte igual para cuadrar"
                )
            ajuste = max(con_importe, key=lambda f: (f[2], f[0]))
            ajuste[4] = round(ajuste[4] + residuo, 2)

    return {
        elem_id: str(valor)
        for elem_id, persistido, _cajas, _base, valor in filas
        if persistido != valor
    }


DESCUENTO_DECIMALES_SAP = 4
_PASO_DESCUENTO = 10 ** -DESCUENTO_DECIMALES_SAP
# Pasos a probar alrededor del descuento derivado. Con ±3 sobra: el objetivo
# nunca está a más de medio paso, y los extremos solo entran para desempatar.
_PASOS_ALREDEDOR = (0, -1, 1, -2, 2, -3, 3)


def _importe_expresable(unitario: float, cantidad: float, objetivo: float):
    """`(descuento, importe)` que SAP reconstruye más cerca del objetivo.

    SAP no guarda el importe de línea: guarda `Price` (3 dec) + `DiscPrcnt`
    (4 dec) y lo deriva, así que el importe solo se mueve a saltos de
    `precio_unitario × cantidad / 1.000.000`. Por debajo de 10.000 € de bruto ese
    salto es menor que el céntimo y cualquier importe es alcanzable; por encima
    hay céntimos que **no se pueden expresar** y el más cercano se queda a 0,01
    (43 de las 873 líneas del portal). A igualdad de acierto gana el descuento
    cuyo importe cae más lejos del medio céntimo: si SAP redondea al revés que
    nosotros, ese es el que aguanta."""
    bruto = unitario * cantidad
    derivado = round((1 - objetivo / bruto) * 100, DESCUENTO_DECIMALES_SAP)
    mejor = None
    for paso in _PASOS_ALREDEDOR:
        pct = round(derivado + paso * _PASO_DESCUENTO, DESCUENTO_DECIMALES_SAP) + 0.0
        exacto = bruto * (1 - pct / 100)
        importe = round(exacto, 2)
        clave = (abs(round(importe - objetivo, 2)), abs(exacto - importe))
        if mejor is None or clave < mejor[0]:
            mejor = (clave, pct, importe)
    return mejor[1], mejor[2]


def _derivar_descuento_elementos(
    elems_contenedor: List[Dict],
    updates_por_elem: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    """`descuento` de cada Elemento del contenedor —y el `precio_total` retocado
    donde haga falta— para que SAP reconstruya el importe exacto de cada línea y
    la suma del contenedor siga cuadrando con el precio negociado.

    Hoy ese porcentaje lo calcula el middleware al crear el pedido y NO lo
    refresca al repreciar: un cambio de flete deja el descuento del estado
    anterior aplicado sobre el precio nuevo, y SAP factura de más o de menos
    (2026300364: 24.145,37 contra los 24.144,00 de HubSpot). Publicándolo aquí el
    middleware puede leerlo en vez de derivarlo.

    Cuando una línea cae en un céntimo inexpresable (ver `_importe_expresable`)
    se reparte: esa línea toma el importe alcanzable y el céntimo sobrante pasa a
    otra línea de fabricante de grano más fino, que sí puede absorberlo — el total
    del contenedor no se mueve. Si no hay dónde ponerlo (contenedor de un solo
    producto grande, 43 de los 221 del portal) **no se toca ningún importe**: el
    precio negociado manda y SAP factura 0,01 de menos, que es el techo del
    formato. El reparto solo mueve fabricantes: en servicio/flete/seguro el
    `precio_total` es el precio de la línea y lo sincroniza el re-sync. Promos y
    líneas sin importe salen a descuento 0."""
    lineas = []
    target = 0.0
    for el in elems_contenedor:
        eid = str(el.get("id") or "").strip()
        if not eid:
            continue
        props = el.get("properties") or {}
        final = {**props, **((updates_por_elem.get(eid) or {}).get("properties") or {})}
        unitario = _to_float(final.get("precio_unitario"))
        cantidad = _to_float(final.get("cantidad"))
        objetivo = round(_to_float(final.get("precio_total")), 2)
        target += objetivo
        lineas.append({
            "eid": eid,
            "unitario": unitario,
            "cantidad": cantidad,
            "bruto": unitario * cantidad,
            "objetivo": objetivo,
            "descuento_persistido": props.get("descuento"),
            "es_fabricante": (
                str(props.get("tipo_articulo") or "").strip().lower() == "fabricante"
            ),
        })

    resueltas: Dict[str, tuple] = {}
    suma = 0.0
    for ln in lineas:
        if ln["bruto"] <= 0:
            resueltas[ln["eid"]] = (0.0, ln["objetivo"])
        else:
            resueltas[ln["eid"]] = _importe_expresable(
                ln["unitario"], ln["cantidad"], ln["objetivo"]
            )
        suma += resueltas[ln["eid"]][1]

    residuo = round(round(target, 2) - round(suma, 2), 2)
    reparto_cuadra = not residuo
    if residuo:
        absorbentes = sorted(
            (l for l in lineas if l["bruto"] > 0 and l["es_fabricante"]),
            key=lambda l: l["bruto"],
        )
        for ln in absorbentes:
            objetivo = round(resueltas[ln["eid"]][1] + residuo, 2)
            pct, importe = _importe_expresable(ln["unitario"], ln["cantidad"], objetivo)
            if importe == objetivo:
                resueltas[ln["eid"]] = (pct, importe)
                reparto_cuadra = True
                break
        if not reparto_cuadra:
            aviso = logger.info if abs(residuo) <= 0.01 else logger.warning
            aviso(
                f"[recalcular] descuento: {residuo} sin línea que pueda expresarlo "
                f"(techo de los 4 decimales de DiscPrcnt); los importes se dejan "
                f"como están y SAP facturará esa diferencia"
            )

    cambios: Dict[str, Dict[str, str]] = {}
    for ln in lineas:
        pct, importe = resueltas[ln["eid"]]
        props: Dict[str, str] = {}
        persistido = ln["descuento_persistido"]
        vacio = persistido is None or str(persistido).strip() == ""
        if vacio or _to_float(persistido) != pct:
            props["descuento"] = str(pct)
        # Solo se mueve el importe cuando el céntimo tiene destino: si no, bajar
        # la línea dejaría el contenedor por debajo del precio negociado. Va
        # aunque coincida con lo persistido, para cancelar el precio_total
        # inexpresable que trae este mismo lote.
        if reparto_cuadra and ln["es_fabricante"] and importe != ln["objetivo"]:
            props["precio_total"] = str(importe)
        if props:
            cambios[ln["eid"]] = props
    return cambios


def _base_sin_iva_fabricantes(fabricantes: List[Dict], pp: PricingParams) -> float:
    """Base sin IVA del contenedor: Σ final×cajas de sus líneas fabricante (las
    promos no repercuten). Es el importe que debe igualar la suma de los
    `precio_total` de TODOS los Elementos del contenedor, y el que ancla
    `contenedor.price`."""
    total = 0.0
    for li in fabricantes:
        cajas = _to_float(li.get("quantity"))
        if cajas <= 0:
            continue
        if _to_float(li.get("price")) == 0 and _ancla_vigente(li, pp.incoterm) is None:
            continue
        total += _resolver_precios_linea(li, pp)["final"] * cajas
    return round(total, 2)


def verificar_suma_precio_total_elementos(
    deal_id: str,
    container_name: str,
    fabricantes: List[Dict],
    pp: PricingParams,
    elems_contenedor: List[Dict],
    pt_upd: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, float]]:
    """Avisa si la suma de los `precio_total` de los Elementos no cuadra con la
    base sin IVA del contenedor. Es lo que suma SAP, así que un delta aquí es
    dinero que se factura de más o de menos.

    Cuenta el valor ya persistido de los Elementos que este recálculo no va a
    tocar: así detecta también el cuadre parcial (algunas líneas sincronizadas y
    otras congeladas), que es el caso más caro. Solo informa; nunca lanza."""
    pt_upd = pt_upd or {}
    esperado = _base_sin_iva_fabricantes(fabricantes, pp)
    suma = 0.0
    for el in elems_contenedor:
        eid = str(el.get("id"))
        if eid in pt_upd:
            suma += _to_float(pt_upd[eid])
        else:
            suma += _to_float((el.get("properties") or {}).get("precio_total"))
    suma = round(suma, 2)
    delta = round(suma - esperado, 2)
    if abs(delta) < 0.01:
        return None
    logger.warning(
        f"[recalcular] INVARIANTE deal={deal_id} cont={container_name}: "
        f"Σ precio_total Elementos={suma} != base sin IVA={esperado} (delta={delta}); "
        f"SAP facturará esa diferencia"
    )
    return {"esperado": esperado, "suma": suma, "delta": delta}


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
    pricing_ctx = build_deal_pricing_context(client, deal_id, line_items_precached=line_items_all)
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
                "line_item_id", "nombre",
                "precio_unitario", "precio_total", "margen_unitario",
                "descuento",
            ],
        )
    except Exception as e:
        logger.warning(f"[recalcular] no pude listar elementos del deal {deal_id}: {e}")
        elementos_all = []
    elementos_by_container = _index_elementos_by_container(elementos_all)
    elem_by_lid = _index_elementos_by_lid(elementos_all)

    logger.info(
        f"⏱️  [TIMER] recalcular fetch_phase: {time.time() - fetch_start:.2f}s "
        f"(line_items={len(line_items_all)}, contenedores={len(contenedores_by_id)}, "
        f"elementos={len(elementos_all)})"
    )

    compute_start = time.time()
    updates: List[Dict[str, Any]] = []
    line_item_updates: List[Dict[str, Any]] = []
    elemento_updates: List[Dict[str, Any]] = []
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
            cajas_contenedor = 0.0
            for f in fabricantes:
                try:
                    cajas_contenedor += float(f.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
            pp_contenedor = pricing_ctx.for_container(container_name, cajas_contenedor)
            elems_contenedor = elementos_by_container.get(container_name, [])
            emp = _emparejar_elementos_fabricante(
                container_name, fabricantes, elems_contenedor, elem_by_lid, deal_id
            )
            li_upd, el_upd = _derivar_writebacks_lineas(
                fabricantes, pp_contenedor, emp.by_lid
            )
            line_item_updates.extend(li_upd)
            # Merge por Elemento: el writeback del ancla, el precio_total corregido y
            # la reparación del lid pueden tocar el mismo Elemento, y el batch no
            # admite ids duplicados.
            el_by_id = {str(u["id"]): u for u in el_upd}
            pt_upd = _derivar_precio_total_fabricantes(
                fabricantes,
                pp_contenedor,
                elems_contenedor,
                emp.by_lid,
            )
            for eid, valor in pt_upd.items():
                if eid in el_by_id:
                    el_by_id[eid]["properties"]["precio_total"] = valor
                else:
                    el_by_id[eid] = {"id": eid, "properties": {"precio_total": valor}}
            for rep in emp.lid_repairs:
                eid = str(rep["id"])
                if eid in el_by_id:
                    el_by_id[eid]["properties"].update(rep["properties"])
                else:
                    el_by_id[eid] = {"id": eid, "properties": dict(rep["properties"])}
            # Va el último: necesita el precio_unitario y el precio_total finales,
            # los de este mismo lote incluidos, y puede retocar el precio_total
            # que acaba de fijar el cuadre.
            for eid, props_dto in _derivar_descuento_elementos(elems_contenedor, el_by_id).items():
                if eid in el_by_id:
                    el_by_id[eid]["properties"].update(props_dto)
                else:
                    el_by_id[eid] = {"id": eid, "properties": dict(props_dto)}
            elemento_updates.extend(el_by_id.values())

            if not emp.resueltos and _base_sin_iva_fabricantes(fabricantes, pp_contenedor) > 0:
                logger.warning(
                    f"[recalcular] deal={deal_id} cont={container_name}: "
                    f"0/{len(fabricantes)} Elementos fabricante emparejados; el cuadre de "
                    f"precio_total NO se aplica y SAP leerá valores desfasados"
                )
            verificar_suma_precio_total_elementos(
                deal_id, container_name, fabricantes, pp_contenedor,
                elems_contenedor, pt_upd,
            )
            new_price = _compute_price_from_fabricantes(fabricantes, company_props, pp_contenedor)
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

    if line_item_updates:
        for upd in line_item_updates:
            enforce_line_item_price_rounding(upd.get("properties", {}))
        for i in range(0, len(line_item_updates), 100):
            chunk = line_item_updates[i:i + 100]
            try:
                client.crm.line_items.batch_api.update(
                    batch_input_simple_public_object_batch_input={"inputs": chunk}
                )
            except Exception as e:
                logger.warning(
                    f"[recalcular] batch update line items [{i}:{i+len(chunk)}] falló: {e}"
                )
        logger.info(
            f"[recalcular] write-back ancla: {len(line_item_updates)} line items"
        )

    if elemento_updates:
        # Un id repetido (mismo Elemento alcanzado desde dos contenedores) hace que
        # HubSpot rechace el chunk entero y se pierdan todas las correcciones.
        merged: Dict[str, Dict[str, Any]] = {}
        for upd in elemento_updates:
            eid = str(upd["id"])
            if eid in merged:
                logger.warning(
                    f"[recalcular] elemento {eid} actualizado desde dos contenedores; "
                    f"fusiono props"
                )
                merged[eid]["properties"].update(upd.get("properties") or {})
            else:
                merged[eid] = {"id": eid, "properties": dict(upd.get("properties") or {})}
        elemento_updates = list(merged.values())
        try:
            batch_update_custom_objects(client, OBJECT_TYPE_ELEMENTO, elemento_updates)
        except Exception as e:
            logger.warning(f"[recalcular] batch update elementos falló: {e}")
        logger.info(
            f"[recalcular] write-back ancla: {len(elemento_updates)} elementos"
        )

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
