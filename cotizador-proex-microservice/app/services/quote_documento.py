"""Calcula el documento de la cotización: la tabla, los totales y las estadísticas.

Hasta ahora `quote.html` sacaba la tabla de productos, los pesos, el número de
contenedores y las páginas del modo desagregado de los line items que la Quote tiene
asociados. Eso no aguanta: HubSpot recorta las cotizaciones a 200 line items y lo hace
minutos DESPUÉS de publicar, cuando ningún control del publish llega a verlo. El
09/09/2026 dos cotizaciones de 240 líneas se quedaron en 200 y el documento imprimió
51.840 y 52.920 bultos de los 62.400 del pedido.

Aquí se calcula todo a partir de los line items del NEGOCIO, que son el motor del
cotizador, y viaja en `quote_data_payload`. La plantilla solo pinta.

Los dos ejes se resuelven en el mismo bloque:

- **Exportación**: la tabla se agrupa por artículo cuando hay más de un contenedor
  (el documento lista 9 artículos, no 180 líneas) y se imprime la escalera
  EXW → FOB → CFR → CIF.
- **Nacional**: una fila por line item, sin agrupar, porque la promoción del mismo
  artículo tiene que quedar aparte (deal 64338364037: 120 cajas a 0,00 € y 240 a
  6,40 € del mismo SKU 42078145060). Los totales van por bucket de (%IVA, %recargo).

Ceuta y Melilla son clientes nacionales fuera del territorio aduanero de la UE, así que
su documentación sale en formato exportación.
"""

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.shared import hubdb_client

logger = logging.getLogger(__name__)

HS_TYPE_CONTAINER = {"container", "contenedor"}
HS_TYPE_ARTICLE = {"inventory", "non_inventory", "articulo"}
HS_TYPE_SERVICE = {"service", "servicio"}

PUERTOS_FUERA_TERRITORIO_ADUANERO = {"CEU", "MEL", "CEUTA", "MELILLA"}

# Propiedades que el documento necesita de cada line item del negocio.
DOCUMENTO_LINE_ITEM_PROPS = [
    "name", "hs_sku", "hs_product_id", "hs_product_type", "tipo_articulo",
    "quantity", "price", "peso_bruto", "peso_neto", "description", "name_en", "name_fr",
    "tipo_iva_repercutido", "precio_negociado_tipo_venta", "incoterm_precio_negociado",
    "numero_container",
]


SEP_REGISTRO = "~"
SEP_CAMPO = "|"


def _limpio(texto: Any) -> str:
    """Quita del texto libre los separadores y las comillas.

    Los nombres de catálogo no llevan ninguna de las tres
    (`ENERGETICA-VITALIS ENERDRINK EVOLUTION-PIÑA & COCO-LATA-24X25CL`), pero un artículo
    nuevo no tiene por qué respetarlo. Las comillas importan porque la plantilla saca la
    cadena del JSON partiendo por `"`, así que una sola cortaría la tabla por ahí.
    """
    limpio = str(texto or "")
    for corta in (SEP_CAMPO, SEP_REGISTRO, '"', "\\"):
        limpio = limpio.replace(corta, " ")
    return limpio.strip()


def _num(value: Any) -> str:
    v = round(_f(value), 3)
    return str(int(v)) if v == int(v) else str(v)


def serializar_documento(doc: dict) -> str:
    """El documento como registros delimitados, para que la plantilla lo lea con `split`.

    `fromjson` no responde en el contexto de la plantilla de cotización: el 10/09/2026 el
    payload llegaba entero (12.675 caracteres) y el parseo devolvía nulo en silencio. Los
    macros que parten cadenas sí funcionan, y son los que llevan años sosteniendo el resto
    del documento, así que la tabla viaja por ese mismo camino.

    De paso ocupa bastante menos que el JSON, porque no repite las claves en cada fila:
    las páginas de un pedido de 50 contenedores bajan de 52 KB a unos 20 KB.

    Un registro por línea lógica, separados por `~`, con los campos por `|`. El primer
    campo dice de qué es el registro:

        E  eje                          F  fila de la tabla
        I  incoterm                     C  artículo del catálogo (modo desagregado)
        S  stats                        P  cabecera de página de contenedor
        D  desglose                     Q  fila de una página (apunta al catálogo)
        T  totales                      B  bucket de IVA (nacional)
        N  totales nacionales
    """
    r: List[str] = []
    t = doc.get("totales") or {}
    s = doc.get("stats") or {}
    d = doc.get("desglose") or {}

    r.append(f"E{SEP_CAMPO}{doc.get('eje', '')}")
    r.append(f"I{SEP_CAMPO}{doc.get('incoterm', '')}")
    r.append(SEP_CAMPO.join(["S", _num(s.get("bultos")), _num(s.get("contenedores")),
                             _num(s.get("pesoNeto")), _num(s.get("pesoBruto"))]))
    r.append(SEP_CAMPO.join(["D", _num(d.get("fobExpenses")), _num(d.get("seafreightRate")),
                             _num(d.get("insurance"))]))
    r.append(SEP_CAMPO.join(["T", _num(t.get("tabla")), _num(t.get("exw")), _num(t.get("fob")),
                             _num(t.get("cfr")), _num(t.get("cif"))]))
    if t.get("total") is not None:
        r.append(SEP_CAMPO.join(["N", _num(t.get("base")), _num(t.get("impuestos")),
                                 _num(t.get("total"))]))
    for b in t.get("ivaBuckets") or []:
        r.append(SEP_CAMPO.join(["B", _num(b.get("iva")), _num(b.get("rec")),
                                 _num(b.get("base")), _num(b.get("imp"))]))
    for f in doc.get("filas") or []:
        r.append(SEP_CAMPO.join([
            "F", _limpio(f.get("cod")), _limpio(f.get("es")), _limpio(f.get("en")),
            _limpio(f.get("fr")), _num(f.get("cajas")), _num(f.get("pesoBruto")),
            _num(f.get("precio")), _num(f.get("importe")),
            "1" if f.get("promo") else "0",
            _num(f.get("ivaPct")), _num(f.get("recPct")),
        ]))
    for c in doc.get("catalogo") or []:
        r.append(SEP_CAMPO.join(["C", _limpio(c.get("cod")), _limpio(c.get("es")),
                                 _limpio(c.get("en")), _limpio(c.get("fr")),
                                 _num(c.get("pesoBruto"))]))
    for p in doc.get("paginas") or []:
        ps = p.get("stats") or {}
        pt = p.get("totales") or {}
        r.append(SEP_CAMPO.join(["P", _limpio(p.get("nombre")), _num(ps.get("bultos")),
                                 _num(ps.get("pesoNeto")), _num(ps.get("pesoBruto")),
                                 _num(pt.get("tabla")), _num(pt.get("exw")), _num(pt.get("fob")),
                                 _num(pt.get("cfr")), _num(pt.get("cif"))]))
        for f in p.get("filas") or []:
            r.append(SEP_CAMPO.join(["Q", _num(f.get("a")), _num(f.get("cajas")),
                                     _num(f.get("precio")), _num(f.get("importe")),
                                     "1" if f.get("promo") else "0"]))
    return SEP_REGISTRO.join(r)


def _f(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _normalizar_incoterm(value: Any) -> str:
    return (str(value or "EXW")).upper().replace(" ", "_").replace("Í", "I")


def _tipo(props: dict) -> str:
    return (props.get("hs_product_type") or "").strip().lower()


def _es_articulo(props: dict) -> bool:
    return (
        _tipo(props) in HS_TYPE_ARTICLE
        and (props.get("tipo_articulo") or "").strip().lower() in ("", "fabricante")
    )


def precio_caja_final(props: dict, delta: float, incoterm: str) -> float:
    """El precio por caja que se imprime: el que fijó el comercial, o EXW + delta.

    `precio_negociado_tipo_venta` es el precio final que el comercial teclea en
    Negociación y manda siempre que sea del tipo de venta que se cotiza. Antes solo
    ganaba si el recompuesto no lo superaba, y cuando los gastos subían después de
    negociar el PDF imprimía más de lo pactado: el 64524566294 sacaba 6,978 €/caja
    donde el comercial había fijado 6,95, 100,80 € de más en el total. Una subida de
    gastos posterior se come el margen, no se le pasa al cliente.

    El filtro por incoterm sí hace falta: el 64577031505 vende CIF con un ancla que
    quedó en EXW, y ahí el precio se recompone. Precio 0 es una línea promocional y no
    recibe reparto de gastos.
    """
    exw = _f(props.get("price"))
    if exw == 0:
        return 0.0
    ancla = _f(props.get("precio_negociado_tipo_venta"))
    if ancla > 0 and _normalizar_incoterm(props.get("incoterm_precio_negociado")) == incoterm:
        return ancla
    # Mismo redondeo que `redondear3` de la card (pricing.utils.js): el precio por caja
    # se cierra a 3 decimales ANTES de multiplicar por las cajas.
    return round(exw + delta, 3)


def _delta_por_caja(
    incoterm: str,
    hasta_salida: float,
    hasta_destino: float,
    servicios: float,
    seguro: float,
    comision: float,
) -> float:
    if incoterm in ("EXW", "FCA"):
        return comision
    if incoterm == "DAP_TERRESTRE":
        return hasta_salida + servicios + comision
    if incoterm == "FOB":
        return hasta_salida + servicios - seguro + comision
    if incoterm == "CFR":
        return hasta_salida + servicios - seguro + hasta_destino + comision
    if incoterm == "CIF":
        return hasta_salida + servicios + hasta_destino + comision
    if incoterm == "DAP_MARITIMO":
        return hasta_salida + servicios + (2 * hasta_destino) + comision
    return comision


def _escalera(incoterm: str, tabla: float, fob_exp: float, flete: float, seguro: float):
    """(exw, fob, cfr, cif) derivados de la suma de la tabla, que es el total del
    tipo de venta cotizado.

    Las dos ramas DAP no descontaban el seguro del EXW aunque el delta por caja sí lo
    reparte (`servicios_caja` lo incluye), así que el EXW impreso salía 60 € alto en 18
    de los 65 negocios auditados: el 64134026617 imprimía 23.751 donde la card calcula
    23.691,84 y la suma de los precios EXW negociados da 23.691,84.
    """
    if incoterm == "CIF":
        cif = tabla; cfr = cif - seguro; fob = cfr - flete; exw = fob - fob_exp
    elif incoterm == "CFR":
        cfr = tabla; cif = cfr + seguro; fob = cfr - flete; exw = fob - fob_exp
    elif incoterm == "FOB":
        fob = tabla; cfr = fob + flete; cif = cfr + seguro; exw = fob - fob_exp
    elif incoterm == "DAP_MARITIMO":
        exw = tabla - fob_exp - seguro - (2 * flete); fob = exw + fob_exp; cfr = fob + flete; cif = cfr + seguro
    elif incoterm == "DAP_TERRESTRE":
        exw = tabla - fob_exp - seguro; fob = exw + fob_exp; cfr = fob + flete; cif = cfr + seguro
    else:
        exw = tabla; fob = exw + fob_exp; cfr = fob + flete; cif = cfr + seguro
    return exw, fob, cfr, cif


def total_incoterm(incoterm: str, exw: float, fob: float, cfr: float, cif: float, flete: float) -> float:
    """El importe que la cotización vende, el que tiene que cuadrar con la suma de la
    tabla. Es la inversa de `_escalera` y existe para poder afirmarlo en los tests."""
    if incoterm == "CIF":
        return cif
    if incoterm == "CFR":
        return cfr
    if incoterm == "FOB":
        return fob
    if incoterm == "DAP_MARITIMO":
        return cif + flete
    if incoterm == "DAP_TERRESTRE":
        return cif - flete
    return exw


def repartir_por_paginas(total: float, pesos: List[float]) -> List[float]:
    """Parte `total` en céntimos proporcionalmente a `pesos`, mayor resto primero.

    Dividir entre el número de contenedores daba por supuesto que todos llevan la misma
    carga, y una sola línea promocional en un contenedor ya descuadraba la suma de las
    páginas contra el agregado.
    """
    if not pesos:
        return []
    centimos = int(round(total * 100))
    suma = sum(pesos)
    if suma <= 0:
        exactos = [centimos / len(pesos)] * len(pesos)
    else:
        exactos = [centimos * p / suma for p in pesos]
    base = [math.floor(x) for x in exactos]
    resto = centimos - sum(base)
    orden = sorted(range(len(pesos)), key=lambda i: exactos[i] - base[i], reverse=True)
    paso = 1 if resto >= 0 else -1
    for k in range(abs(resto)):
        base[orden[k % len(orden)]] += paso
    return [c / 100.0 for c in base]


def _comision_por_caja(deal: dict, num_contenedores: int, total_cajas: float) -> float:
    """Réplica de `pricing.py:_comision_por_caja` desde las props del negocio."""
    importe = _f(deal.get("importe_comision"))
    if importe <= 0 or total_cajas <= 0:
        return 0.0
    if not (deal.get("codigo_intermediario") or "").strip():
        return 0.0
    repercutir = (deal.get("repercutir_a_cliente") or "").strip().lower().split(" ")[0]
    if repercutir not in ("si", "sí", "yes"):
        return 0.0
    tipo = (deal.get("tipo_comision") or "").strip().lower().replace(" ", "_")
    if tipo == "importe_por_negocio":
        return importe / total_cajas
    if tipo == "importe_por_contenedor":
        return (importe * num_contenedores) / total_cajas
    if tipo == "importe_por_cajas":
        return importe
    return 0.0


def es_nacional(tipo_cliente: Optional[str], puerto_destino: Optional[str]) -> bool:
    tipo = (tipo_cliente or "").strip().lower()
    puerto = (puerto_destino or "").split(",")[0].strip().upper()
    return tipo == "nacional" and puerto not in PUERTOS_FUERA_TERRITORIO_ADUANERO


def _nombres(props: dict) -> Dict[str, str]:
    return {
        "es": (props.get("description") or "").strip(),
        "en": (props.get("name_en") or "").strip(),
        "fr": (props.get("name_fr") or "").strip(),
    }


def construir_documento(
    *,
    line_items: List[dict],
    deal: dict,
    tipo_cliente: Optional[str],
    residennum: Optional[str],
    equ: Optional[str],
    desgloses: Dict[str, float],
    total_cajas: float = 0.0,
    num_contenedores: int = 0,
    porcentajes_iva: Optional[Callable[[str, str], Tuple[Optional[float], Optional[float]]]] = None,
) -> dict:
    """El bloque `documento` del payload, listo para que la plantilla lo pinte.

    `line_items` son los del negocio, ya sin los huérfanos de catálogo (misma lista que
    se copia a la Quote). `desgloses` trae fobExpenses/seafreightRate/insurance del
    payload de la card; `total_cajas` y `num_contenedores`, sus estadísticas. Lo que
    llegue a 0 se recalcula desde los line items y las rutas del negocio.
    """
    if porcentajes_iva is None:
        porcentajes_iva = hubdb_client.get_porcentajes_iva_repercutido

    props_por_li = [li.get("properties", {}) or {} for li in line_items]
    articulos = [p for p in props_por_li if _es_articulo(p)]
    contenedores = [p for p in props_por_li if _tipo(p) in HS_TYPE_CONTAINER]

    nacional = es_nacional(tipo_cliente, deal.get("puerto_destino"))
    incoterm = _normalizar_incoterm(deal.get("tipo_de_venta"))

    cajas = total_cajas or sum(_f(p.get("quantity")) for p in articulos)
    nc = int(num_contenedores) or len(contenedores) or 1

    servicios_sin_seguro = seguro_lineas = 0.0
    for p in props_por_li:
        if _tipo(p) not in HS_TYPE_SERVICE:
            continue
        tipo_art = (p.get("tipo_articulo") or "").strip().lower()
        if tipo_art in ("transitario", "comisiones"):
            continue
        if tipo_art == "seguro":
            seguro_lineas += _f(p.get("price"))
        else:
            servicios_sin_seguro += _f(p.get("price"))

    precio_origen = _f(deal.get("precio_ruta_hasta_puerto_de_salida"))
    precio_destino = _f(deal.get("precio_ruta_hasta_puerto_destino"))

    # Los gastos FOB se calculan aquí en vez de tomarlos del payload. La card reparte el
    # coste entre las cajas, redondea ese importe por caja a 3 decimales y vuelve a
    # multiplicar, y el residuo crece con el pedido: 1,34 € en las 4.836 cajas del deal
    # 64836202383 (855 x 2 contenedores + 50 x 2 de certificado = 1.810 que se imprimían
    # como 1.808,66) y 25,60 € en las 62.400 del 64836801849. El TOTAL no se mueve porque
    # va anclado a la suma de la tabla, así que el residuo se lo come el EXW y la escalera
    # imprime un reparto que no cuadra con lo que Administración tiene contratado.
    #
    # El prorrateo por las cajas que pagan no es un ajuste: las promocionales van a 0 y no
    # recogen gastos, así que el importe cobrado es menor que el coste (deal 64134026617:
    # 1.350 de ruta sobre 3.480 cajas de pago de 3.600 = 1.305).
    cajas_que_pagan = sum(_f(p.get("quantity")) for p in articulos if _f(p.get("price")) > 0)

    def _cobrado(coste: float, clave: str) -> float:
        if cajas and cajas_que_pagan and coste:
            return round(coste * cajas_que_pagan / cajas, 2)
        return round(_f(desgloses.get(clave)), 2) or round(coste, 2)

    fob_exp = _cobrado(servicios_sin_seguro + precio_origen * nc, "fobExpenses")
    # El flete arrastra el mismo residuo: la card guarda 0,603 €/caja donde el reparto
    # real son 0,602564 y al volver a multiplicar por las 62.400 cajas del 64836801849
    # cobra 37.627,20 por un flete de 37.600 contratado. Difiere en 8 de los 65 negocios
    # auditados, de 0,08 a 27,20 €.
    flete = _cobrado(precio_destino * nc, "seafreightRate")
    # El seguro no se recalcula a propósito: `precios.insurance` es la prima contratada y
    # llega limpia a dos decimales en los 65 negocios auditados, mientras que las líneas
    # de seguro no son fuente fiable (el 64519499938 vende FOB con 420 € en líneas y 0 en
    # el payload, y el 64133336498 perdió sus líneas al corregirlo en SAP).
    seguro = round(_f(desgloses.get("insurance")), 2) or seguro_lineas

    divisor = cajas or 1
    hasta_salida = (precio_origen * nc) / divisor
    hasta_destino = (precio_destino * nc) / divisor
    # Servicios y seguro salen de los desgloses, no de sumar los line items de servicio:
    # los fletes ya se reparten con precio_origen/destino × nc, que cuenta los
    # contenedores del pedido. `fobExpenses` = servicios sin seguro + precio_origen × nc.
    servicios_caja = (fob_exp - (precio_origen * nc) + seguro) / divisor
    seguro_caja = seguro / divisor
    comision_caja = _comision_por_caja(deal, nc, divisor)

    delta = _delta_por_caja(
        incoterm, hasta_salida, hasta_destino, servicios_caja, seguro_caja, comision_caja
    )

    # En exportación multicontenedor se agrupa por artículo, pero también por precio: el
    # mismo SKU puede ir a precios distintos en dos contenedores y fundirlos se comía la
    # diferencia (deal 64736817288: 1.920 cajas a 6,910 y 480 a 6,900 salían como 2.400 a
    # 6,900, 19,20 € menos). Y el precio que ganaba era el de la primera línea que
    # devolviera HubSpot, así que el mismo pedido podía imprimirse de más o de menos.
    agrupar = nc > 1 and not nacional
    if agrupar:
        grupos: Dict[Tuple[str, float], List] = {}
        for p in articulos:
            precio = precio_caja_final(p, delta, incoterm)
            grupo = grupos.setdefault((p.get("hs_sku") or "", precio), [p, 0.0, precio])
            grupo[1] += _f(p.get("quantity"))
        origen = [tuple(g) for g in grupos.values()]
    else:
        origen = [
            (p, _f(p.get("quantity")), precio_caja_final(p, delta, incoterm))
            for p in articulos
        ]

    # Orden estable: el de las asociaciones de HubSpot cambia entre publicaciones y la
    # tabla salía en un orden distinto en cada PDF de la misma cotización. Por artículo
    # y precio descendente, que además deja la línea promocional junto a la de pago.
    origen.sort(key=lambda fila: ((fila[0].get("hs_sku") or ""), -fila[2]))

    residencia = (residennum or "").strip().upper()
    if residencia not in ("EUROPA", "EXTRANJERO"):
        residencia = "NACIONAL"
    aplica_recargo = (equ or "").strip().lower() == "si"

    filas: List[dict] = []
    tabla = 0.0
    for props, num_cajas, precio in origen:
        # El importe se cierra a 2 decimales ANTES de sumarlo: el TOTAL acumulaba el
        # importe crudo y las filas impresas no llegaban a sumarlo (deal 64736753079,
        # 7,646 x 806 = 6.162,676: las tres filas daban 18.618,61 y el TOTAL 18.618,60).
        importe = round(precio * num_cajas, 2)
        tabla += importe
        fila = {
            "cod": props.get("hs_sku") or "",
            **_nombres(props),
            "cajas": num_cajas,
            "pesoBruto": _f(props.get("peso_bruto")),
            "precio": round(precio, 3),
            "importe": importe,
        }
        if _f(props.get("price")) == 0:
            fila["promo"] = True
        if nacional:
            code = (props.get("tipo_iva_repercutido") or "").split(" ")[0].strip().upper()
            pct_iva, pct_rec = porcentajes_iva(code, residencia)
            if pct_iva is None:
                # Sin fila en HubDB el porcentaje sale del propio código (R21 → 21),
                # igual que hacía la plantilla.
                digitos = "".join(c for c in code if c.isdigit())
                pct_iva, pct_rec = (_f(digitos), 0.0)
            fila["ivaCode"] = code
            fila["ivaPct"] = pct_iva
            fila["recPct"] = (pct_rec or 0.0) if aplica_recargo else 0.0
        filas.append(fila)

    exw, fob, cfr, cif = _escalera(incoterm, tabla, fob_exp, flete, seguro)

    buckets: List[dict] = []
    if nacional:
        acumulado: Dict[Tuple[float, float], dict] = {}
        for fila in filas:
            clave = (fila["ivaPct"], fila["recPct"])
            bucket = acumulado.setdefault(
                clave, {"iva": clave[0], "rec": clave[1], "base": 0.0, "imp": 0.0}
            )
            bucket["base"] += fila["importe"]
            bucket["imp"] += fila["importe"] * (clave[0] + clave[1]) / 100.0
        buckets = [
            {"iva": b["iva"], "rec": b["rec"], "base": round(b["base"], 2), "imp": round(b["imp"], 2)}
            for b in acumulado.values()
        ]

    totales = {
        "tabla": round(tabla, 2),
        "exw": round(exw, 2),
        "fob": round(fob, 2),
        "cfr": round(cfr, 2),
        "cif": round(cif, 2),
    }
    if buckets:
        base = sum(b["base"] for b in buckets)
        impuestos = sum(b["imp"] for b in buckets)
        totales.update({
            "base": round(base, 2),
            "impuestos": round(impuestos, 2),
            "total": round(base + impuestos, 2),
            "ivaBuckets": buckets,
        })

    documento = {
        "eje": "nacional" if nacional else "exportacion",
        "incoterm": incoterm,
        "filas": filas,
        "totales": totales,
        "desglose": {"fobExpenses": fob_exp, "seafreightRate": flete, "insurance": seguro},
        "stats": {
            "bultos": cajas,
            "contenedores": nc,
            "pesoNeto": round(sum(_f(p.get("quantity")) * _f(p.get("peso_neto")) for p in props_por_li), 2),
            "pesoBruto": round(sum(_f(p.get("quantity")) * _f(p.get("peso_bruto")) for p in props_por_li), 2),
        },
    }

    desagregado = (str(deal.get("desagregar_cotizacion") or "").strip().lower() in ("true", "yes", "si", "sí"))
    if desagregado and nc > 1:
        catalogo, indice_por_sku, paginas = [], {}, []
        for cont in sorted(contenedores, key=lambda p: (p.get("name") or "")):
            nombre = (cont.get("name") or "").strip()
            prefijo = f"{nombre} - "
            propios = [p for p in articulos if (p.get("name") or "").startswith(prefijo)]
            if not propios:
                continue
            pag_filas, pag_tabla, pag_cajas, pag_neto, pag_bruto = [], 0.0, 0.0, 0.0, 0.0
            pag_pagan = 0.0
            for props in propios:
                num_cajas = _f(props.get("quantity"))
                precio = precio_caja_final(props, delta, incoterm)
                importe = round(precio * num_cajas, 2)
                sku = props.get("hs_sku") or ""
                if sku not in indice_por_sku:
                    indice_por_sku[sku] = len(catalogo)
                    catalogo.append({
                        "cod": sku,
                        **_nombres(props),
                        "pesoBruto": _f(props.get("peso_bruto")),
                    })
                # La fila apunta al catálogo por índice: repetir los tres nombres en
                # cada contenedor multiplicaba por 50 la parte más pesada del bloque y
                # se pasaba de los 65.536 caracteres que admite la propiedad.
                entrada = {
                    "a": indice_por_sku[sku],
                    "cajas": num_cajas,
                    "precio": round(precio, 3),
                    "importe": importe,
                }
                if _f(props.get("price")) == 0:
                    entrada["promo"] = True
                pag_filas.append(entrada)
                pag_tabla += importe
                pag_cajas += num_cajas
                if _f(props.get("price")) > 0:
                    pag_pagan += num_cajas
                pag_neto += num_cajas * _f(props.get("peso_neto"))
                pag_bruto += num_cajas * _f(props.get("peso_bruto"))
            paginas.append({
                "nombre": nombre,
                "filas": pag_filas,
                "stats": {
                    "bultos": pag_cajas,
                    "pesoNeto": round(pag_neto, 2),
                    "pesoBruto": round(pag_bruto, 2),
                },
                "tabla": pag_tabla,
                "pagan": pag_pagan,
            })

        # Los tres importes se reparten entre las páginas en proporción a las cajas que
        # pagan de cada contenedor, no a partes iguales: el reparto por caja solo lo
        # recogen las líneas con precio, así que un contenedor con promoción carga menos
        # gastos que el resto. Los céntimos van al mayor resto para que la suma de las
        # páginas sea exactamente el agregado.
        pesos = [p["pagan"] for p in paginas]
        rep_fob = repartir_por_paginas(fob_exp, pesos)
        rep_flete = repartir_por_paginas(flete, pesos)
        rep_seguro = repartir_por_paginas(seguro, pesos)
        for idx, pagina in enumerate(paginas):
            pag_tabla = pagina.pop("tabla")
            pagina.pop("pagan")
            p_exw, p_fob, p_cfr, p_cif = _escalera(
                incoterm, pag_tabla, rep_fob[idx], rep_flete[idx], rep_seguro[idx]
            )
            pagina["totales"] = {
                "tabla": round(pag_tabla, 2), "exw": round(p_exw, 2),
                "fob": round(p_fob, 2), "cfr": round(p_cfr, 2), "cif": round(p_cif, 2),
            }
        documento["catalogo"] = catalogo
        documento["paginas"] = paginas

    return documento
