#!/usr/bin/env python3
"""Rehace los line items que HubSpot se llevó al publicar la cotización.

Publicar una cotización desde el editor de HubSpot hace que el Negocio pase a tener los
line items de la COTIZACIÓN: los clona sobre el negocio (hs_object_source=CRM_UI, en la
sesión del comercial) y archiva los propios, con un tope de 200 por operación. Mientras
la copia se agrupaba por artículo, el negocio heredaba esa forma agregada y perdía sus
líneas per-contenedor.

Caso: deal 64836801849 (20 contenedores, 09/09/2026). De sus 240 líneas quedaron 71: 30
de producto, 30 de contenedor (10 originales + 20 clones, con 10 números duplicados) y 11
agregadas sin `numero_container`. La verdad sobrevive en los 20 Contenedor y las 220
Líneas Contenedor, que es de donde se reconstruye aquí.

Reconstruir importa por dos motivos: el cotizador lee la rejilla de Negociación de los
line items (sin ellos, coste 0 e IVA vacío), y el re-sync de objetos personalizados borra
toda Línea Contenedor que no tenga un line item vivo detrás, que es la única copia del
coste con el que se cotizó.

Uso:
    python scripts/repair_lineitems_espejo_publish.py --deal 64836801849
    python scripts/repair_lineitems_espejo_publish.py --deal 64836801849 --apply
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
load_dotenv(root_dir / ".env")

from app.core.custom_objects_constants import (  # noqa: E402
    OBJECT_TYPE_CONTENEDOR,
    OBJECT_TYPE_ELEMENTO,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BASE = "https://api.hubapi.com"
TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN") or os.getenv("PROEX_ACCESS_TOKEN")
ASSOC_TYPE_LINE_ITEM_TO_DEAL = 20  # HUBSPOT_DEFINED line_item -> deal

# De la hermana se clona solo lo que HubSpot admite escribir (lo dice el esquema:
# calculated / modificationMetadata.readOnlyValue), y de eso se quitan las que pertenecen
# al objeto original y no a la línea nueva.
PROPS_NO_CLONABLES = {
    "createdate",                    # la nueva línea es de hoy, no del lote que se perdió
    "hs_origin_key", "hs_group_key", "hs_bundle_id", "hs_position_on_quote",
    "hs_pinned_engagement_id", "hs_owning_teams", "hs_all_assigned_business_unit_ids",
    "hs_ramp_group_name", "hs_ramp_key", "hs_ramp_phase_name",
    "container_config",              # solo la lleva la línea de contenedor
    # El esquema declara estas dos escribibles, pero identifican al objeto de origen.
    "hs_object_id", "hs_lastmodifieddate",
}

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})


def api(method, path, **kw):
    for intento in range(4):
        resp = session.request(method, BASE + path, timeout=60, **kw)
        if resp.status_code == 429:
            time.sleep(2 * (intento + 1))
            continue
        if resp.status_code >= 400:
            raise SystemExit(f"{resp.status_code} {method} {path}\n{resp.text[:2000]}")
        return resp.json() if resp.text else {}
    raise SystemExit(f"rate limited en {method} {path}")


def assoc_ids(from_type, oid, to_type):
    out, after = [], None
    while True:
        params = {"limit": 500}
        if after:
            params["after"] = after
        data = api("GET", f"/crm/v4/objects/{from_type}/{oid}/associations/{to_type}", params=params)
        out += [str(r["toObjectId"]) for r in data.get("results", [])]
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return out


def batch_read(obj, ids, props):
    out = []
    for i in range(0, len(ids), 100):
        data = api("POST", f"/crm/v3/objects/{obj}/batch/read",
                   json={"properties": props, "inputs": [{"id": str(x)} for x in ids[i:i + 100]]})
        out += data.get("results", [])
    return out


def line_item_props():
    """Las propiedades que se leen y clonan, según lo que el esquema declara escribible."""
    todas = api("GET", "/crm/v3/properties/line_items")["results"]
    escribibles = [
        p["name"] for p in todas
        if not p.get("calculated")
        and not (p.get("modificationMetadata") or {}).get("readOnlyValue")
        and p["name"] not in PROPS_NO_CLONABLES
    ]
    # container_config se lee (de ahí sale la lista de artículos) aunque no se clone.
    return escribibles + ["container_config"]


def es_contenedor(props):
    return (props.get("hs_product_type") or "").strip().lower() in ("container", "contenedor")


def plan(deal_id):
    props_li = line_item_props()
    vivos = {r["id"]: r["properties"] for r in
             batch_read("line_items", assoc_ids("deals", deal_id, "line_items"), props_li)}
    contenedores = {r["id"]: r["properties"] for r in
                    batch_read(OBJECT_TYPE_CONTENEDOR, assoc_ids("deals", deal_id, OBJECT_TYPE_CONTENEDOR),
                               ["nombre", "numero_container", "line_item_id", "price", "total_cajas"])}
    lineas_co = {r["id"]: r["properties"] for r in
                 batch_read(OBJECT_TYPE_ELEMENTO, assoc_ids("deals", deal_id, OBJECT_TYPE_ELEMENTO),
                            ["nombre", "nombre_contenedor", "tipo_articulo", "cantidad", "precio_de_coste",
                             "precio_unitario", "margen_unitario", "tipo_iva_repercutido",
                             "tipo_iva_soportado", "line_item_id", "product_sku"])}

    # Las líneas de contenedor vivas, por número. Se conserva la que su Contenedor
    # referencia; si esa está archivada, la que quede (el clon del espejo).
    lineas_cont = defaultdict(list)
    for lid, p in vivos.items():
        if es_contenedor(p):
            lineas_cont[(p.get("numero_container") or p.get("name") or "").strip()].append(lid)

    conservar_cont, archivar, repuntar_co = {}, [], {}
    for co_id, co in contenedores.items():
        nombre = (co.get("nombre") or "").strip()
        candidatas = sorted(lineas_cont.get(nombre, []))
        if not candidatas:
            logger.warning(f"  !! {nombre}: no queda ninguna línea de contenedor viva")
            continue
        referida = str(co.get("line_item_id") or "")
        elegida = referida if referida in candidatas else candidatas[0]
        conservar_cont[nombre] = elegida
        archivar += [x for x in candidatas if x != elegida]
        if referida != elegida:
            repuntar_co[co_id] = elegida

    # Las agregadas del espejo (sin numero_container) se van: la forma sana no las tiene.
    agregadas = [lid for lid, p in vivos.items() if not (p.get("numero_container") or "").strip()]
    archivar += agregadas

    # Índices para clonar: una hermana viva por producto, y las agregadas de flete/seguro.
    hermana_por_producto, plantilla_flete, plantilla_seguro = {}, None, None
    for lid, p in vivos.items():
        pid = str(p.get("hs_product_id") or "")
        tipo = (p.get("tipo_articulo") or "").strip().lower()
        if tipo == "fabricante" and pid and pid not in hermana_por_producto:
            hermana_por_producto[pid] = (lid, p)
        if lid in agregadas and tipo == "seguro":
            plantilla_seguro = (lid, p)
        if lid in agregadas and tipo == "transitario" and not es_contenedor(p):
            plantilla_flete = (lid, p)

    lc_por_clave = {}
    for eid, p in lineas_co.items():
        clave = ((p.get("nombre_contenedor") or "").strip(), str(p.get("product_sku") or "").strip(),
                 (p.get("tipo_articulo") or "").strip().lower())
        lc_por_clave[clave] = (eid, p)

    # Se compara el `name` CRUDO: arrastra el espacio final del nombre de catálogo,
    # igual que el `nombre` del container_config y el de la Línea Contenedor. Recortarlo
    # deja fuera a los 8 de los 9 artículos que lo llevan.
    vivos_por_nombre = defaultdict(list)
    for lid, p in vivos.items():
        if lid not in archivar:
            vivos_por_nombre[p.get("name") or ""].append(lid)

    crear, sin_plantilla = [], []
    for nombre_cont, cont_lid in sorted(conservar_cont.items()):
        cfg = json.loads(vivos[cont_lid].get("container_config") or "{}")
        objetivo = [
            (str(a.get("articuloId")), f"{nombre_cont} - {a.get('nombre')}", a.get("numCajas"), "fabricante")
            for a in cfg.get("articulos", []) if (a.get("numCajas") or 0) > 0
        ]
        objetivo.append((None, f"{nombre_cont} - FLETES", 1, "flete"))
        objetivo.append((None, f"{nombre_cont} - SEGURO MERCANCÍA", 1, "seguro"))

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
                sin_plantilla.append((nombre, tipo, pid))
                continue

            _, base = plantilla
            nueva = {k: v for k, v in base.items()
                     if v not in (None, "") and k not in PROPS_NO_CLONABLES}
            nueva["name"] = nombre
            nueva["numero_container"] = nombre_cont
            nueva["quantity"] = str(cajas)
            nueva["hs_line_item_currency_code"] = "EUR"

            sku = pid or str(base.get("hs_product_id") or "")
            lc = lc_por_clave.get((nombre_cont, sku, tipo))
            if lc:
                _, lcp = lc
                for campo_li, campo_lc in (
                    ("price", "precio_unitario"),
                    ("margen_unitario", "margen_unitario"),
                    ("tipo_iva_repercutido", "tipo_iva_repercutido"),
                    ("tipo_iva_soportado", "tipo_iva_soportado"),
                ):
                    val = lcp.get(campo_lc)
                    if val not in (None, ""):
                        nueva[campo_li] = str(val)
                if tipo == "fabricante" and lcp.get("cantidad"):
                    nueva["quantity"] = str(int(float(lcp["cantidad"])))
            crear.append({"nombre_contenedor": nombre_cont, "tipo": tipo, "properties": nueva})

    return {
        "vivos": vivos, "contenedores": contenedores, "lineas_co": lineas_co,
        "conservar_cont": conservar_cont, "archivar": sorted(set(archivar)),
        "agregadas": agregadas, "repuntar_co": repuntar_co,
        "crear": crear, "sin_plantilla": sin_plantilla,
    }


def aplicar(deal_id, p):
    creados = []
    for i in range(0, len(p["crear"]), 100):
        chunk = p["crear"][i:i + 100]
        data = api("POST", "/crm/v3/objects/line_items/batch/create",
                   json={"inputs": [{"properties": c["properties"]} for c in chunk]})
        nuevos = [r["id"] for r in data.get("results", [])]
        creados += nuevos
        logger.info(f"  creadas {len(nuevos)} líneas [{i}:{i + len(chunk)}]")
        api("POST", "/crm/v4/associations/line_items/deals/batch/create", json={"inputs": [
            {"from": {"id": str(lid)}, "to": {"id": str(deal_id)},
             "types": [{"associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": ASSOC_TYPE_LINE_ITEM_TO_DEAL}]}
            for lid in nuevos
        ]})
        logger.info(f"  asociadas {len(nuevos)} al negocio {deal_id}")

    if p["archivar"]:
        for i in range(0, len(p["archivar"]), 100):
            chunk = p["archivar"][i:i + 100]
            api("POST", "/crm/v3/objects/line_items/batch/archive",
                json={"inputs": [{"id": x} for x in chunk]})
        logger.info(f"  archivadas {len(p['archivar'])} líneas")

    if p["repuntar_co"]:
        api("POST", f"/crm/v3/objects/{OBJECT_TYPE_CONTENEDOR}/batch/update", json={"inputs": [
            {"id": co_id, "properties": {"line_item_id": str(lid)}}
            for co_id, lid in p["repuntar_co"].items()
        ]})
        logger.info(f"  repuntados {len(p['repuntar_co'])} Contenedor")
    return creados


def repuntar_lineas_contenedor(deal_id):
    """Devuelve cada Línea Contenedor a su line item, emparejando por nombre."""
    props_li = ["name", "numero_container", "tipo_articulo", "hs_product_id", "quantity"]
    vivos = {r["id"]: r["properties"] for r in
             batch_read("line_items", assoc_ids("deals", deal_id, "line_items"), props_li)}
    por_nombre = defaultdict(list)
    for lid, pr in vivos.items():
        por_nombre[pr.get("name") or ""].append(lid)

    lcs = batch_read(OBJECT_TYPE_ELEMENTO, assoc_ids("deals", deal_id, OBJECT_TYPE_ELEMENTO),
                     ["nombre", "line_item_id"])
    updates = []
    for lc in lcs:
        nombre = lc["properties"].get("nombre") or ""
        candidatas = por_nombre.get(nombre) or []
        if not candidatas:
            logger.warning(f"  !! Línea Contenedor {lc['id']} '{nombre}' sin line item que casar")
            continue
        elegida = candidatas.pop(0)
        if str(lc["properties"].get("line_item_id") or "") != elegida:
            updates.append({"id": lc["id"], "properties": {"line_item_id": elegida}})
    for i in range(0, len(updates), 100):
        api("POST", f"/crm/v3/objects/{OBJECT_TYPE_ELEMENTO}/batch/update",
            json={"inputs": updates[i:i + 100]})
    logger.info(f"  repuntadas {len(updates)} Líneas Contenedor")
    return len(updates)


def verificar(deal_id):
    props = ["name", "numero_container", "tipo_articulo", "hs_product_type", "quantity",
             "price", "container_config", "tipo_iva_repercutido", "margen_unitario"]
    vivos = [r["properties"] for r in
             batch_read("line_items", assoc_ids("deals", deal_id, "line_items"), props)]
    conts = [p for p in vivos if es_contenedor(p)]
    arts = defaultdict(list)
    for p in vivos:
        tipo = (p.get("tipo_articulo") or "").strip().lower()
        if tipo == "fabricante" and (p.get("name") or ""):
            arts[p["name"]].append(p)

    filas = hit = 0
    for c in conts:
        cfg = json.loads(c.get("container_config") or "{}")
        for a in cfg.get("articulos", []):
            filas += 1
            clave = f"{(c.get('name') or '').strip()} - {a.get('nombre')}"
            if arts.get(clave):
                hit += 1

    numeros = [(p.get("numero_container") or "").strip() for p in conts]
    importe = sum(float(p.get("price") or 0) * float(p.get("quantity") or 0) for p in vivos)
    logger.info(f"  line items: {len(vivos)}")
    logger.info(f"  líneas de contenedor: {len(conts)} ({len(set(numeros))} números, "
                f"{len(numeros) - len(set(numeros))} duplicados)")
    logger.info(f"  rejilla: {hit}/{filas} filas con line item")
    logger.info(f"  sin numero_container: {sum(1 for p in vivos if not (p.get('numero_container') or '').strip())}")
    logger.info(f"  suma price x quantity: {importe:,.2f} EUR")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    ap.add_argument("--apply", action="store_true", help="sin esto solo imprime el plan")
    args = ap.parse_args()

    if not TOKEN:
        raise SystemExit("falta HUBSPOT_ACCESS_TOKEN en el .env")

    logger.info(f"=== Negocio {args.deal}: estado actual")
    verificar(args.deal)

    p = plan(args.deal)
    logger.info("\n=== Plan")
    logger.info(f"  crear: {len(p['crear'])} líneas")
    por_tipo = defaultdict(int)
    for c in p["crear"]:
        por_tipo[c["tipo"]] += 1
    for tipo, n in sorted(por_tipo.items()):
        logger.info(f"      {tipo}: {n}")
    logger.info(f"  archivar: {len(p['archivar'])} líneas "
                f"({len(p['agregadas'])} agregadas sin contenedor, "
                f"{len(p['archivar']) - len(p['agregadas'])} de contenedor duplicadas)")
    logger.info(f"  repuntar Contenedor.line_item_id: {len(p['repuntar_co'])}")
    if p["sin_plantilla"]:
        logger.error(f"  !! {len(p['sin_plantilla'])} líneas sin hermana de la que clonar:")
        for n, t, pid in p["sin_plantilla"][:10]:
            logger.error(f"       {t} {pid} {n}")
        raise SystemExit("no se puede reconstruir sin plantilla; abortado")

    muestra = p["crear"][0] if p["crear"] else None
    if muestra:
        logger.info(f"\n  ejemplo de línea a crear ({muestra['tipo']}):")
        logger.info(json.dumps({k: v for k, v in sorted(muestra["properties"].items())},
                               indent=2, ensure_ascii=False))

    if not args.apply:
        logger.info("\n(dry-run: nada escrito. Añade --apply para ejecutar)")
        return

    logger.info("\n=== Aplicando")
    aplicar(args.deal, p)
    repuntar_lineas_contenedor(args.deal)
    logger.info(f"\n=== Negocio {args.deal}: estado final")
    verificar(args.deal)


if __name__ == "__main__":
    main()
