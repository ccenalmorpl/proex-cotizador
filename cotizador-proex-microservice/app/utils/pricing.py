"""Cálculo de precio por caja por incoterm (espejo Python de pricing.utils.js
del frontend NegociacionTab). El backend usa esta misma fórmula para
calcular `contenedor.price` con el mismo redondeo combinado que el frontend,
evitando desajustes de redondeo entre lo que se muestra y lo persistido."""

import logging
import unicodedata
from dataclasses import dataclass, field
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
    servicios_por_caja: float
    seguro_por_caja: float
    comision_cliente: float
    total_cajas: float
    total_contenedores: int
    # IVA por concepto — solo para el cálculo del IVA del contenedor, no afecta
    # la base. Flete/comisión llevan un único code; servicios y seguro se
    # desglosan en buckets {code_iva: importe_por_caja} porque cada servicio
    # puede tener un tipo distinto. La suma de los buckets cuadra con
    # servicios_por_caja/seguro_por_caja.
    # tipo_iva_fletes_terrestre grava el tramo origen (doble flete); vacío →
    # cae a tipo_iva_fletes (nunca al default por residencia).
    tipo_iva_fletes: str = ""
    tipo_iva_fletes_terrestre: str = ""
    tipo_iva_comision: str = ""
    servicios_no_seguro_por_iva_por_caja: Dict[str, float] = field(default_factory=dict)
    seguro_por_iva_por_caja: Dict[str, float] = field(default_factory=dict)


def _normalize_incoterm(value) -> str:
    v = (value or "EXW")
    if isinstance(v, dict):
        v = v.get("value") or v.get("internal") or v.get("label") or "EXW"
    # Quita acentos: los values de HubSpot son "DAP MARÍTIMO" / "DAP TERRESTRE"
    # y deben matchear con "DAP_MARITIMO" / "DAP_TERRESTRE".
    s = unicodedata.normalize("NFD", str(v))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().upper().replace(" ", "_")


def calcular_precio_por_caja_por_incoterm(
    precio_exw: float,
    incoterm: str,
    *,
    hasta_localidad_destino: float = 0.0,
    hasta_puerto_salida: float = 0.0,
    hasta_puerto_destino: float = 0.0,
    servicios_por_caja: float = 0.0,
    seguro_por_caja: float = 0.0,
    comision_cliente: float = 0.0,
) -> float:
    """Espejo de `pricing.utils.js:calcularPrecioPorCajaPorIncoterm`."""
    ic = _normalize_incoterm(incoterm)
    # FCA equivalente a EXW: sin flete/servicios/seguro en la base.
    if ic in ("EXW", "FCA"):
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
    # DAP marítimo lleva los mismos tramos que CIF: la ruta no tiene un precio
    # propio para puerto destino → localidad, va incluido en el marítimo.
    if ic in ("CIF", "DAP_MARITIMO"):
        return (
            precio_exw
            + hasta_puerto_salida
            + servicios_por_caja
            + hasta_puerto_destino
            + comision_cliente
        )
    return precio_exw + comision_cliente


def calcular_precio_exw_desde_incoterm(
    precio_final: float,
    incoterm: str,
    *,
    hasta_localidad_destino: float = 0.0,
    hasta_puerto_salida: float = 0.0,
    hasta_puerto_destino: float = 0.0,
    servicios_por_caja: float = 0.0,
    seguro_por_caja: float = 0.0,
    comision_cliente: float = 0.0,
) -> float:
    """Inversa de `calcular_precio_por_caja_por_incoterm`: dado el precio final
    por caja del incoterm, devuelve el precio EXW por caja. Espejo exacto de
    `pricing.utils.js:calcularPrecioEXWDesdeIncoterm` (mismo orden de operaciones
    para que el EXW derivado coincida bit-a-bit con el del frontend)."""
    ic = _normalize_incoterm(incoterm)
    # FCA equivalente a EXW: sin flete/servicios/seguro en la base.
    if ic in ("EXW", "FCA"):
        return precio_final - comision_cliente
    if ic == "DAP_TERRESTRE":
        return precio_final - hasta_localidad_destino - servicios_por_caja - comision_cliente
    if ic == "FOB":
        return (
            precio_final
            - hasta_puerto_salida
            - (servicios_por_caja - seguro_por_caja)
            - comision_cliente
        )
    if ic == "CFR":
        return (
            precio_final
            - hasta_puerto_salida
            - (servicios_por_caja - seguro_por_caja)
            - hasta_puerto_destino
            - comision_cliente
        )
    if ic in ("CIF", "DAP_MARITIMO"):
        cfr = precio_final - comision_cliente
        return cfr - hasta_puerto_salida - servicios_por_caja - hasta_puerto_destino
    return precio_final - comision_cliente


def _pricing_kwargs(pp: "PricingParams") -> Dict[str, float]:
    return {
        "hasta_localidad_destino": pp.hasta_localidad_destino,
        "hasta_puerto_salida": pp.hasta_puerto_salida,
        "hasta_puerto_destino": pp.hasta_puerto_destino,
        "servicios_por_caja": pp.servicios_por_caja,
        "seguro_por_caja": pp.seguro_por_caja,
        "comision_cliente": pp.comision_cliente,
    }


def derivar_exw_desde_final_anclado(
    precio_final: float,
    precio_coste,
    pp: "PricingParams",
) -> Dict[str, float]:
    """EXW implícito de un precio final anclado, con suelo de margen 0 (nunca
    por debajo del coste). Si el final anclado no cubre coste + add-ons, el EXW
    se clampa al coste y el final efectivo sube lo justo; en ese caso el precio
    del tipo de venta deja de ser exactamente el negociado.

    Devuelve {"exw", "final_efectivo", "clamped"}."""
    kwargs = _pricing_kwargs(pp)
    exw = calcular_precio_exw_desde_incoterm(precio_final, pp.incoterm, **kwargs)
    suelo = max(0.0, _safe_float(precio_coste))
    if exw >= suelo:
        return {"exw": exw, "final_efectivo": precio_final, "clamped": False}
    final_efectivo = calcular_precio_por_caja_por_incoterm(suelo, pp.incoterm, **kwargs)
    return {"exw": suelo, "final_efectivo": final_efectivo, "clamped": True}


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
                "tipo_iva_fletes",
                "tipo_iva_fletes_terrestre",
                "tipo_iva_comision",
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
    tipo_iva_fletes = (deal_props.get("tipo_iva_fletes") or "").strip().upper()
    tipo_iva_fletes_terrestre = (
        (deal_props.get("tipo_iva_fletes_terrestre") or "").strip().upper() or tipo_iva_fletes
    )
    tipo_iva_comision = (deal_props.get("tipo_iva_comision") or "").strip().upper()

    total_cajas = 0.0
    total_contenedores = 0
    total_servicios = 0.0
    total_seguro = 0.0
    servicios_no_seguro_por_iva: Dict[str, float] = {}
    seguro_por_iva: Dict[str, float] = {}

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
            code = (props.get("tipo_iva_repercutido") or "").strip().upper()
            if ta == "seguro":
                total_seguro += price
                seguro_por_iva[code] = seguro_por_iva.get(code, 0.0) + price
            else:
                servicios_no_seguro_por_iva[code] = servicios_no_seguro_por_iva.get(code, 0.0) + price
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
            "tipo_iva_repercutido",
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
            servicios_por_caja=0.0,
            seguro_por_caja=0.0,
            comision_cliente=0.0,
            total_cajas=total_cajas,
            total_contenedores=total_contenedores,
            tipo_iva_fletes=tipo_iva_fletes,
            tipo_iva_fletes_terrestre=tipo_iva_fletes_terrestre,
            tipo_iva_comision=tipo_iva_comision,
        )

    factor_caja = total_contenedores / total_cajas
    hasta_puerto_salida = precio_origen * factor_caja
    hasta_puerto_destino = precio_destino * factor_caja
    servicios_por_caja = total_servicios / total_cajas
    seguro_por_caja = total_seguro / total_cajas
    # Mismo factor que servicios_por_caja/seguro_por_caja para que la suma de
    # buckets cuadre con el agregado.
    servicios_no_seguro_por_iva_por_caja = {
        code: importe / total_cajas for code, importe in servicios_no_seguro_por_iva.items()
    }
    seguro_por_iva_por_caja = {
        code: importe / total_cajas for code, importe in seguro_por_iva.items()
    }

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
        servicios_por_caja=servicios_por_caja,
        seguro_por_caja=seguro_por_caja,
        comision_cliente=comision_cliente,
        total_cajas=total_cajas,
        total_contenedores=total_contenedores,
        tipo_iva_fletes=tipo_iva_fletes,
        tipo_iva_fletes_terrestre=tipo_iva_fletes_terrestre,
        tipo_iva_comision=tipo_iva_comision,
        servicios_no_seguro_por_iva_por_caja=servicios_no_seguro_por_iva_por_caja,
        seguro_por_iva_por_caja=seguro_por_iva_por_caja,
    )


def _comision_por_caja_intra(
    tipo_comision: str,
    importe_comision: float,
    cajas_contenedor: float,
    total_contenedores: int,
) -> float:
    """Comisión por caja con reparto INTRA-contenedor.

    - importe_por_negocio: el importe del negocio se reparte entre los
      contenedores (importe / num_contenedores) y esa cuota entre las cajas del
      contenedor. Es el único modo en el que añadir un contenedor mueve a los
      previos (rediluye la cuota), por decisión de negocio.
    - importe_por_contenedor: el importe ya es por contenedor → entre sus cajas.
    - importe_por_cajas: importe directo por caja.
    """
    if not tipo_comision or importe_comision <= 0 or cajas_contenedor <= 0:
        return 0.0
    tc = (tipo_comision or "").strip().lower()
    if tc == "importe_por_negocio":
        if total_contenedores <= 0:
            return 0.0
        return (importe_comision / total_contenedores) / cajas_contenedor
    if tc == "importe_por_contenedor":
        return importe_comision / cajas_contenedor
    if tc == "importe_por_cajas":
        return importe_comision
    return 0.0


@dataclass
class DealPricingContext:
    """Datos del deal para construir un `PricingParams` POR CONTENEDOR.

    El reparto de flete/servicios/seguro/comisión es intra-contenedor: cada
    contenedor reparte SUS importes entre SUS propias cajas, no sobre el total
    del negocio. Así, añadir un contenedor no altera el precio de los previos
    (salvo la comisión `importe_por_negocio`; ver `_comision_por_caja_intra`).

    Flete: `precio_origen`/`precio_destino` son el flete POR contenedor (deal
    properties, mismas para todos). Servicios y seguro se indexan por
    `numero_container` (cada contenedor lleva sus líneas)."""

    incoterm: str
    precio_origen: float
    precio_destino: float
    repercutir: bool
    tiene_intermediario: bool
    tipo_comision: str
    importe_comision: float
    num_contenedores: int
    tipo_iva_fletes: str
    tipo_iva_comision: str
    servicios_por_contenedor: Dict[str, float]
    seguro_por_contenedor: Dict[str, float]
    servicios_buckets_por_contenedor: Dict[str, Dict[str, float]]
    seguro_buckets_por_contenedor: Dict[str, Dict[str, float]]
    tipo_iva_fletes_terrestre: str = ""

    def for_container(self, container_name: str, cajas_contenedor: float) -> PricingParams:
        """PricingParams del contenedor: importes repartidos entre SUS cajas."""
        nc = (container_name or "").strip()
        if not cajas_contenedor or cajas_contenedor <= 0:
            return PricingParams(
                incoterm=self.incoterm,
                hasta_localidad_destino=0.0,
                hasta_puerto_salida=0.0,
                hasta_puerto_destino=0.0,
                servicios_por_caja=0.0,
                seguro_por_caja=0.0,
                comision_cliente=0.0,
                total_cajas=0.0,
                total_contenedores=self.num_contenedores,
                tipo_iva_fletes=self.tipo_iva_fletes,
                tipo_iva_fletes_terrestre=self.tipo_iva_fletes_terrestre,
                tipo_iva_comision=self.tipo_iva_comision,
            )

        flete_salida = self.precio_origen / cajas_contenedor
        flete_destino = self.precio_destino / cajas_contenedor
        servicios_por_caja = self.servicios_por_contenedor.get(nc, 0.0) / cajas_contenedor
        seguro_por_caja = self.seguro_por_contenedor.get(nc, 0.0) / cajas_contenedor
        servicios_buckets = {
            code: importe / cajas_contenedor
            for code, importe in self.servicios_buckets_por_contenedor.get(nc, {}).items()
        }
        seguro_buckets = {
            code: importe / cajas_contenedor
            for code, importe in self.seguro_buckets_por_contenedor.get(nc, {}).items()
        }
        comision_cliente = 0.0
        if self.repercutir and self.tiene_intermediario:
            comision_cliente = _comision_por_caja_intra(
                self.tipo_comision, self.importe_comision,
                cajas_contenedor, self.num_contenedores,
            )

        return PricingParams(
            incoterm=self.incoterm,
            hasta_localidad_destino=flete_salida,
            hasta_puerto_salida=flete_salida,
            hasta_puerto_destino=flete_destino,
            servicios_por_caja=servicios_por_caja,
            seguro_por_caja=seguro_por_caja,
            comision_cliente=comision_cliente,
            total_cajas=cajas_contenedor,
            total_contenedores=self.num_contenedores,
            tipo_iva_fletes=self.tipo_iva_fletes,
            tipo_iva_fletes_terrestre=self.tipo_iva_fletes_terrestre,
            tipo_iva_comision=self.tipo_iva_comision,
            servicios_no_seguro_por_iva_por_caja=servicios_buckets,
            seguro_por_iva_por_caja=seguro_buckets,
        )


def build_deal_pricing_context(
    client,
    deal_id: str,
    line_items_precached: Optional[List[Dict]] = None,
) -> DealPricingContext:
    """Lee el deal y agrupa servicios/seguro POR `numero_container` para el
    reparto intra-contenedor. Sustituye al reparto global de
    `_fetch_pricing_params_for_deal` en el recálculo de precios."""
    from app.routes.deals import (
        _get_associated_ids,
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
                "tipo_iva_fletes",
                "tipo_iva_fletes_terrestre",
                "tipo_iva_comision",
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
    tipo_iva_fletes = (deal_props.get("tipo_iva_fletes") or "").strip().upper()
    tipo_iva_fletes_terrestre = (
        (deal_props.get("tipo_iva_fletes_terrestre") or "").strip().upper() or tipo_iva_fletes
    )
    tipo_iva_comision = (deal_props.get("tipo_iva_comision") or "").strip().upper()

    num_contenedores = 0
    servicios_por_contenedor: Dict[str, float] = {}
    seguro_por_contenedor: Dict[str, float] = {}
    servicios_buckets: Dict[str, Dict[str, float]] = {}
    seguro_buckets: Dict[str, Dict[str, float]] = {}

    def _aggregate(props: Dict) -> None:
        nonlocal num_contenedores
        pt = (props.get("hs_product_type") or "").strip()
        ta = (props.get("tipo_articulo") or "").strip().lower()
        price = _safe_float(props.get("price"))
        if _is_container_product_type(pt):
            num_contenedores += 1
            return
        if _line_item_is_service(props):
            if ta in ("transitario", "comisiones", "flete"):
                return
            nc = (props.get("numero_container") or "").strip()
            if not nc:
                return
            code = (props.get("tipo_iva_repercutido") or "").strip().upper()
            # servicios_por_contenedor incluye el seguro (misma invariante que
            # _fetch_pricing_params_for_deal, el frontend y quote.html): las fórmulas
            # por incoterm restan seguro_por_caja de una base que YA lo incorpora
            # (FOB/CFR), así que si el escalar lo excluye el EXW derivado sale inflado
            # y el seguro se cuenta dos veces. Los buckets de IVA sí van separados.
            servicios_por_contenedor[nc] = servicios_por_contenedor.get(nc, 0.0) + price
            if ta == "seguro":
                seguro_por_contenedor[nc] = seguro_por_contenedor.get(nc, 0.0) + price
                bucket = seguro_buckets.setdefault(nc, {})
                bucket[code] = bucket.get(code, 0.0) + price
            else:
                bucket = servicios_buckets.setdefault(nc, {})
                bucket[code] = bucket.get(code, 0.0) + price

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
            "numero_container", "tipo_iva_repercutido",
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

    return DealPricingContext(
        incoterm=incoterm,
        precio_origen=precio_origen,
        precio_destino=precio_destino,
        repercutir=repercutir,
        tiene_intermediario=tiene_intermediario,
        tipo_comision=tipo_comision,
        importe_comision=importe_comision,
        num_contenedores=num_contenedores,
        tipo_iva_fletes=tipo_iva_fletes,
        tipo_iva_fletes_terrestre=tipo_iva_fletes_terrestre,
        tipo_iva_comision=tipo_iva_comision,
        servicios_por_contenedor=servicios_por_contenedor,
        seguro_por_contenedor=seguro_por_contenedor,
        servicios_buckets_por_contenedor=servicios_buckets,
        seguro_buckets_por_contenedor=seguro_buckets,
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
        "precio_de_coste", "precio_negociado_tipo_venta", "incoterm_precio_negociado",
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
