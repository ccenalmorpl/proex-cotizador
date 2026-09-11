"""Repone los line items que el espejo del publish se lleva del Negocio.

Publicar una cotización hace que el NEGOCIO pase a tener los line items de la
COTIZACIÓN: HubSpot los clona (hs_object_source=CRM_UI) y archiva los propios, con
un tope de 200 por operación. Con 20 contenedores (240 líneas) el corte deja el
negocio a medias: unas líneas duplicadas —la vieja que no llegó a archivarse y su
clon— y otras ausentes, incluidas líneas de contenedor enteras.

La rejilla de Negociación empareja cada artículo del container_config con su line
item por el nombre "{contenedor} - {artículo}", así que lo que falta sale sin
line_item_id y el guardado no puede escribir nada. La verdad de la que se
reconstruye son los Contenedor y las Líneas Contenedor, que el re-sync todavía no
ha tocado cuando esto corre.
"""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import requests

from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO

logger = logging.getLogger(__name__)

ASSOC_TYPE_LINE_ITEM_TO_DEAL = 20

PROPS_NO_CLONABLES = {
    "createdate",
    "hs_origin_key", "hs_group_key", "hs_bundle_id", "hs_position_on_quote",
    "hs_pinned_engagement_id", "hs_owning_teams", "hs_all_assigned_business_unit_ids",
    "hs_ramp_group_name", "hs_ramp_key", "hs_ramp_phase_name",
    "container_config",
    "hs_object_id", "hs_lastmodifieddate",
}

NOMBRE_FLETE = "FLETES"
NOMBRE_SEGURO = "SEGURO MERCANCÍA"


def _headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _api(access_token: str, method: str, path: str, **kw) -> Dict[str, Any]:
    r = requests.request(method, f"https://api.hubapi.com{path}",
                         headers=_headers(access_token), timeout=60, **kw)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code} {method} {path}: {r.text[:500]}")
    return r.json() if r.text else {}


def _assoc_ids(access_token: str, from_type: str, from_id: str, to_type: str) -> List[str]:
    out, after = [], None
    while True:
        params = {"limit": 500}
        if after:
            params["after"] = after
        data = _api(access_token, "GET",
                    f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}",
                    params=params)
        out += [str(r["toObjectId"]) for r in data.get("results", [])]
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return out


def _batch_read(access_token: str, obj: str, ids: List[str], props: List[str]) -> List[Dict[str, Any]]:
    out = []
    for i in range(0, len(ids), 100):
        data = _api(access_token, "POST", f"/crm/v3/objects/{obj}/batch/read",
                    json={"properties": props, "inputs": [{"id": str(x)} for x in ids[i:i + 100]]})
        out += data.get("results", [])
    return out


def _line_item_props(access_token: str) -> List[str]:
    todas = _api(access_token, "GET", "/crm/v3/properties/line_items")["results"]
    escribibles = [
        p["name"] for p in todas
        if not p.get("calculated")
        and not (p.get("modificationMetadata") or {}).get("readOnlyValue")
        and p["name"] not in PROPS_NO_CLONABLES
    ]
    # createdate no se clona, pero se lee: es lo que distingue el clon del espejo de
    # la línea vieja que no llegó a archivarse.
    return escribibles + ["container_config", "createdate"]


def _es_contenedor(props: Dict[str, Any]) -> bool:
    return (props.get("hs_product_type") or "").strip().lower() in ("container", "contenedor")


def _config_desde_elementos(elementos: List[Dict[str, Any]],
                            contenedor: Optional[Dict[str, Any]] = None) -> str:
    """Rehace el container_config de una línea de contenedor perdida.

    Las Líneas Contenedor guardan artículo, cajas y precios de cada fila; la ocupación
    y el peso salen del Contenedor, que los lleva aparte. Los dos son obligatorios en
    el modelo: sin ellos GET /containers no puede leer el config y el contenedor sale
    sin artículos.
    """
    contenedor = contenedor or {}
    articulos = []
    for p in elementos:
        if (p.get("tipo_articulo") or "").strip().lower() != "fabricante":
            continue
        try:
            cajas = int(float(p.get("cantidad") or 0))
        except (TypeError, ValueError):
            cajas = 0
        if cajas <= 0:
            continue
        articulos.append({
            "line_item_id": None,
            "articuloId": str(p.get("product_sku") or ""),
            "articuloName": None,
            "nombre": (p.get("nombre") or "").split(" - ", 1)[-1],
            "numCajas": cajas,
            "precio_de_coste": float(p["precio_de_coste"]) if p.get("precio_de_coste") else None,
            "price": float(p["precio_unitario"]) if p.get("precio_unitario") else 0.0,
            "margen_unitario": float(p["margen_unitario"]) if p.get("margen_unitario") else None,
            "tipo_iva_repercutido": p.get("tipo_iva_repercutido") or None,
            "tipo_iva_soportado": p.get("tipo_iva_soportado") or None,
            "precio_negociado_tipo_venta": None,
            "incoterm_precio_negociado": None,
        })
    return json.dumps({
        "articulos": articulos,
        "ocupacion": float(contenedor.get("ocupacion") or 0),
        "pesoNeto": float(contenedor.get("peso_neto") or 0),
    }, ensure_ascii=False)


def _mas_antigua_primero(ids: List[str], vivos: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(ids, key=lambda lid: (vivos[lid].get("createdate") or "", lid))


def planificar(access_token: str, deal_id: str,
               configs_previos: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Qué líneas hay que crear y cuáles archivar para devolver el negocio a su forma.

    `configs_previos` son container_config conocidos por nombre de contenedor (una foto
    del negocio antes de publicar): mandan sobre lo que se pueda reconstruir de las
    Líneas Contenedor, que no guardan ocupación ni pesos.
    """
    configs_previos = configs_previos or {}
    props_li = _line_item_props(access_token)
    vivos = {r["id"]: r["properties"] for r in
             _batch_read(access_token, "line_items",
                         _assoc_ids(access_token, "deals", deal_id, "line_items"), props_li)}
    contenedores = {r["id"]: r["properties"] for r in
                    _batch_read(access_token, OBJECT_TYPE_CONTENEDOR,
                                _assoc_ids(access_token, "deals", deal_id, OBJECT_TYPE_CONTENEDOR),
                                ["nombre", "numero_container", "line_item_id", "container_config",
                                 "ocupacion", "peso_neto"])}
    elementos = {r["id"]: r["properties"] for r in
                 _batch_read(access_token, OBJECT_TYPE_ELEMENTO,
                             _assoc_ids(access_token, "deals", deal_id, OBJECT_TYPE_ELEMENTO),
                             ["nombre", "nombre_contenedor", "tipo_articulo", "cantidad",
                              "precio_de_coste", "precio_unitario", "margen_unitario",
                              "tipo_iva_repercutido", "tipo_iva_soportado", "line_item_id",
                              "product_sku"])}

    elementos_por_contenedor = defaultdict(list)
    for p in elementos.values():
        elementos_por_contenedor[(p.get("nombre_contenedor") or "").strip()].append(p)

    lineas_cont = defaultdict(list)
    for lid, p in vivos.items():
        if _es_contenedor(p):
            lineas_cont[(p.get("numero_container") or p.get("name") or "").strip()].append(lid)

    archivar, repuntar_co, crear = [], {}, []
    cont_a_crear: Dict[str, str] = {}
    co_id_por_cont: Dict[str, str] = {}
    config_por_cont: Dict[str, str] = {}

    for co_id, co in contenedores.items():
        nombre = (co.get("nombre") or "").strip()
        if not nombre:
            continue
        candidatas = _mas_antigua_primero(lineas_cont.get(nombre, []), vivos)
        if candidatas:
            # Se conserva la más reciente: con el espejo es el clon, que trae el
            # estado publicado; la vieja es la que no llegó a archivarse.
            elegida = candidatas[-1]
            config_por_cont[nombre] = vivos[elegida].get("container_config") or "{}"
            archivar += candidatas[:-1]
            if str(co.get("line_item_id") or "") != elegida:
                repuntar_co[co_id] = elegida
            continue

        config = (configs_previos.get(nombre) or co.get("container_config") or "").strip()
        if config in ("", "{}"):
            config = _config_desde_elementos(elementos_por_contenedor.get(nombre, []), co)
        if not json.loads(config).get("articulos"):
            logger.error(f"[espejo] {nombre}: sin línea de contenedor viva y sin artículos "
                         f"de los que rehacer su configuración; se salta")
            continue
        config_por_cont[nombre] = config
        cont_a_crear[nombre] = config
        co_id_por_cont[nombre] = co_id

    # Las agregadas del espejo (sin numero_container) se van: la forma sana no las tiene.
    agregadas = [lid for lid, p in vivos.items()
                 if not (p.get("numero_container") or "").strip()]
    archivar += agregadas

    # De la más reciente a la más vieja: las plantillas tienen que salir de líneas que
    # van a sobrevivir. Si se clona el precio de la vieja de un duplicado, la línea
    # nueva nace con el precio anterior a la última negociación y la vieja se archiva.
    plantilla_cont, plantilla_flete, plantilla_seguro = None, None, None
    hermana_por_producto: Dict[str, Dict[str, Any]] = {}
    hermana_por_contenedor: Dict[str, Dict[str, Any]] = {}
    for lid in reversed(_mas_antigua_primero(list(vivos), vivos)):
        p = vivos[lid]
        if lid in archivar:
            continue
        tipo = (p.get("tipo_articulo") or "").strip().lower()
        cont = (p.get("numero_container") or "").strip()
        if _es_contenedor(p):
            plantilla_cont = plantilla_cont or p
            continue
        if tipo == "fabricante":
            pid = str(p.get("hs_product_id") or "")
            if pid:
                hermana_por_producto.setdefault(pid, p)
            if cont:
                hermana_por_contenedor.setdefault(cont, p)
        elif tipo == "seguro":
            plantilla_seguro = plantilla_seguro or p
        elif tipo == "transitario":
            plantilla_flete = plantilla_flete or p

    elemento_por_clave = {}
    for p in elementos.values():
        clave = ((p.get("nombre_contenedor") or "").strip(),
                 str(p.get("product_sku") or "").strip(),
                 (p.get("tipo_articulo") or "").strip().lower())
        elemento_por_clave[clave] = p

    sin_plantilla = []

    # Ordenadas por antigüedad: se consume la última (el clon del espejo, que trae el
    # estado publicado) y lo que quede sin sitio es la vieja, que es la que se archiva.
    vivos_por_nombre = defaultdict(list)
    for lid, p in vivos.items():
        if lid not in archivar:
            vivos_por_nombre[p.get("name") or ""].append(lid)
    for nombre in vivos_por_nombre:
        vivos_por_nombre[nombre] = _mas_antigua_primero(vivos_por_nombre[nombre], vivos)

    # Las que se llevó el espejo se rehacen sobre cualquier línea de contenedor viva.
    for nombre_cont in sorted(cont_a_crear):
        if not plantilla_cont:
            sin_plantilla.append((nombre_cont, "contenedor"))
            continue
        nueva = {k: v for k, v in plantilla_cont.items()
                 if v not in (None, "") and k not in PROPS_NO_CLONABLES}
        nueva["name"] = nombre_cont
        nueva["numero_container"] = nombre_cont
        nueva["quantity"] = "1"
        nueva["price"] = "0"
        nueva["container_config"] = cont_a_crear[nombre_cont]
        nueva["hs_line_item_currency_code"] = "EUR"
        crear.append({"nombre_contenedor": nombre_cont, "tipo": "contenedor",
                      "properties": nueva, "co_id": co_id_por_cont[nombre_cont]})

    for nombre_cont in sorted(config_por_cont):
        cfg = json.loads(config_por_cont[nombre_cont] or "{}")
        objetivo = [
            (str(a.get("articuloId") or ""), f"{nombre_cont} - {a.get('nombre')}",
             a.get("numCajas"), "fabricante")
            for a in cfg.get("articulos", []) if (a.get("numCajas") or 0) > 0
        ]
        # La Línea Contenedor llama `flete` a lo que el line item marca como
        # `transitario`; el nombre de la línea es lo que empareja a los dos.
        tipos_elemento = {(p.get("tipo_articulo") or "").strip().lower()
                          for p in elementos_por_contenedor.get(nombre_cont, [])}
        if "flete" in tipos_elemento:
            objetivo.append((None, f"{nombre_cont} - {NOMBRE_FLETE}", 1, "flete"))
        if "seguro" in tipos_elemento:
            objetivo.append((None, f"{nombre_cont} - {NOMBRE_SEGURO}", 1, "seguro"))

        for pid, nombre, cajas, tipo in objetivo:
            if vivos_por_nombre.get(nombre):
                vivos_por_nombre[nombre].pop()
                continue
            if tipo == "fabricante":
                plantilla = hermana_por_producto.get(pid)
            elif tipo == "flete":
                plantilla = plantilla_flete
            else:
                plantilla = plantilla_seguro
            if not plantilla:
                sin_plantilla.append((nombre, tipo))
                continue

            nueva = {k: v for k, v in plantilla.items()
                     if v not in (None, "") and k not in PROPS_NO_CLONABLES}
            nueva["name"] = nombre
            nueva["numero_container"] = nombre_cont
            nueva["quantity"] = str(cajas)
            nueva["hs_line_item_currency_code"] = "EUR"

            # El precio por caja y el ancla son del CONTENEDOR, no del artículo: una
            # hermana viva del mismo contenedor es la que los tiene al día.
            vecina = hermana_por_contenedor.get(nombre_cont)
            if tipo == "fabricante" and vecina:
                for campo in ("price", "precio_negociado_tipo_venta", "incoterm_precio_negociado"):
                    if vecina.get(campo) not in (None, ""):
                        nueva[campo] = str(vecina[campo])

            elem = elemento_por_clave.get((nombre_cont, pid or "", tipo))
            if elem:
                for campo_li, campo_el in (
                    ("precio_de_coste", "precio_de_coste"),
                    ("margen_unitario", "margen_unitario"),
                    ("tipo_iva_repercutido", "tipo_iva_repercutido"),
                    ("tipo_iva_soportado", "tipo_iva_soportado"),
                ):
                    val = elem.get(campo_el)
                    if val not in (None, ""):
                        nueva[campo_li] = str(val)
                if tipo != "fabricante" and elem.get("precio_unitario"):
                    nueva["price"] = str(elem["precio_unitario"])

            crear.append({"nombre_contenedor": nombre_cont, "tipo": tipo, "properties": nueva})

    # Lo que queda vivo sin sitio en el objetivo son duplicados del espejo.
    for nombre, restantes in vivos_por_nombre.items():
        if restantes and not _es_contenedor(vivos[restantes[0]]):
            archivar += restantes

    return {
        "vivos": vivos,
        "crear": crear,
        "archivar": sorted(set(archivar)),
        "repuntar_co": repuntar_co,
        "sin_plantilla": sin_plantilla,
    }


def aplicar(access_token: str, deal_id: str, plan: Dict[str, Any]) -> Dict[str, int]:
    creados = []
    for i in range(0, len(plan["crear"]), 100):
        chunk = plan["crear"][i:i + 100]
        data = _api(access_token, "POST", "/crm/v3/objects/line_items/batch/create",
                    json={"inputs": [{"properties": c["properties"]} for c in chunk]})
        nuevos = [r["id"] for r in data.get("results", [])]
        creados += nuevos
        # Los resultados vuelven en el orden de entrada, así que la línea de contenedor
        # recién creada se puede devolver a su Contenedor.
        for entrada, lid in zip(chunk, nuevos):
            if entrada.get("co_id"):
                plan["repuntar_co"][entrada["co_id"]] = lid
        _api(access_token, "POST", "/crm/v4/associations/line_items/deals/batch/create",
             json={"inputs": [
                 {"from": {"id": str(lid)}, "to": {"id": str(deal_id)},
                  "types": [{"associationCategory": "HUBSPOT_DEFINED",
                             "associationTypeId": ASSOC_TYPE_LINE_ITEM_TO_DEAL}]}
                 for lid in nuevos
             ]})

    for i in range(0, len(plan["archivar"]), 100):
        _api(access_token, "POST", "/crm/v3/objects/line_items/batch/archive",
             json={"inputs": [{"id": x} for x in plan["archivar"][i:i + 100]]})

    if plan["repuntar_co"]:
        inputs = [{"id": co_id, "properties": {"line_item_id": str(lid)}}
                  for co_id, lid in plan["repuntar_co"].items()]
        for i in range(0, len(inputs), 100):
            _api(access_token, "POST", f"/crm/v3/objects/{OBJECT_TYPE_CONTENEDOR}/batch/update",
                 json={"inputs": inputs[i:i + 100]})

    return {"creadas": len(creados), "archivadas": len(plan["archivar"]),
            "repuntados": len(plan["repuntar_co"])}


def reconciliar_lineas_tras_espejo(access_token: str, deal_id: str) -> Dict[str, Any]:
    """Devuelve al negocio las líneas que el espejo del publish se llevó o duplicó."""
    plan = planificar(access_token, deal_id)

    # Esto corre solo tras cada publish: si el objetivo sale mal calculado, archivar a
    # ciegas se lleva por delante el negocio. Un espejo roto deja duplicados y ausencias
    # en proporciones parecidas; archivar mucho más de lo que se repone no es eso.
    archivar, crear = len(plan["archivar"]), len(plan["crear"])
    if archivar > crear + max(5, 0.1 * len(plan["vivos"])):
        logger.error(
            f"[espejo] deal {deal_id}: plan desproporcionado sobre {len(plan['vivos'])} "
            f"líneas vivas (archivaría {archivar} y crearía {crear}); no se toca nada"
        )
        return {"creadas": 0, "archivadas": 0, "repuntados": 0, "abortado": True}

    if plan["sin_plantilla"]:
        logger.error(f"[espejo] deal {deal_id}: {len(plan['sin_plantilla'])} líneas sin "
                     f"plantilla de la que clonar: {plan['sin_plantilla'][:5]}")
    if not plan["crear"] and not plan["archivar"] and not plan["repuntar_co"]:
        logger.info(f"[espejo] deal {deal_id}: el negocio está entero, nada que reponer")
        return {"creadas": 0, "archivadas": 0, "repuntados": 0}

    logger.warning(
        f"[espejo] deal {deal_id}: el publish dejó el negocio a medias; "
        f"creando {len(plan['crear'])} líneas, archivando {len(plan['archivar'])}, "
        f"repuntando {len(plan['repuntar_co'])} Contenedor"
    )
    stats = aplicar(access_token, deal_id, plan)
    logger.warning(f"[espejo] deal {deal_id}: reconciliado {stats}")
    return stats
