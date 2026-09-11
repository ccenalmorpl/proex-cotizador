#!/usr/bin/env python3
"""Rehace los line items que HubSpot se llevó al publicar la cotización.

Publicar una cotización desde el editor de HubSpot hace que el Negocio pase a tener los
line items de la COTIZACIÓN: los clona sobre el negocio (hs_object_source=CRM_UI, en la
sesión del comercial) y archiva los propios, con un tope de 200 por operación. Por
encima de ahí los dos cortes caen sobre conjuntos distintos y el negocio queda a la vez
con líneas duplicadas (la vieja que no se archivó y su clon) y sin otras, incluidas
líneas de contenedor enteras.

Casos: deal 64836801849 (20 contenedores, 09/09/2026) y deal 64731186508 (20
contenedores, 11/09/2026), este último con 29 de las 162 filas de la rejilla de
Negociación sin line item, lo que devolvía un 422 al guardar.

Desde el 11/09/2026 esto mismo corre solo tras cada publish, en el worker de custom
objects (`app.services.espejo_publish`). El script queda para reparar a mano lo que se
rompió antes o lo que el worker no pudo cerrar.

Uso:
    python scripts/repair_lineitems_espejo_publish.py --deal 64731186508
    python scripts/repair_lineitems_espejo_publish.py --deal 64731186508 --apply
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(root_dir / ".env")

from app.core.custom_objects_constants import OBJECT_TYPE_ELEMENTO  # noqa: E402
from app.services.espejo_publish import (  # noqa: E402
    _api,
    _assoc_ids,
    _batch_read,
    _es_contenedor,
    aplicar,
    planificar,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN") or os.getenv("PROEX_ACCESS_TOKEN")


def cargar_foto(carpeta):
    """container_config por contenedor, de una foto del negocio anterior al publish.

    Solo hace falta cuando ya no quedan Líneas Contenedor de las que rehacer la lista
    de artículos; con ellas la reconstrucción sale sola.
    """
    if not carpeta:
        return {}
    ruta = Path(carpeta) / "line_items.json"
    if not ruta.exists():
        raise SystemExit(f"no encuentro {ruta}")
    configs = {}
    for fila in json.loads(ruta.read_text()):
        props = fila.get("properties", {}) or {}
        config = (props.get("container_config") or "").strip()
        if config not in ("", "{}") and props.get("name"):
            configs[props["name"]] = config
    logger.info(f"  foto: {ruta} con {len(configs)} configuraciones de contenedor")
    return configs


def repuntar_lineas_contenedor(deal_id):
    """Devuelve cada Línea Contenedor a su line item, emparejando por nombre."""
    vivos = {r["id"]: r["properties"] for r in
             _batch_read(TOKEN, "line_items", _assoc_ids(TOKEN, "deals", deal_id, "line_items"),
                         ["name", "numero_container", "tipo_articulo", "hs_product_id", "quantity"])}
    por_nombre = defaultdict(list)
    for lid, pr in vivos.items():
        por_nombre[pr.get("name") or ""].append(lid)

    lcs = _batch_read(TOKEN, OBJECT_TYPE_ELEMENTO,
                      _assoc_ids(TOKEN, "deals", deal_id, OBJECT_TYPE_ELEMENTO),
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
        _api(TOKEN, "POST", f"/crm/v3/objects/{OBJECT_TYPE_ELEMENTO}/batch/update",
             json={"inputs": updates[i:i + 100]})
    logger.info(f"  repuntadas {len(updates)} Líneas Contenedor")
    return len(updates)


def verificar(deal_id):
    props = ["name", "numero_container", "tipo_articulo", "hs_product_type", "quantity",
             "price", "container_config", "tipo_iva_repercutido", "margen_unitario"]
    vivos = [r["properties"] for r in
             _batch_read(TOKEN, "line_items", _assoc_ids(TOKEN, "deals", deal_id, "line_items"), props)]
    conts = [p for p in vivos if _es_contenedor(p)]
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
    ap.add_argument("--desde-foto", dest="foto", default=None,
                    help="carpeta con line_items.json del negocio antes de publicar")
    args = ap.parse_args()

    if not TOKEN:
        raise SystemExit("falta HUBSPOT_ACCESS_TOKEN")

    logger.info(f"=== Negocio {args.deal}: estado actual")
    verificar(args.deal)

    plan = planificar(TOKEN, args.deal, configs_previos=cargar_foto(args.foto))
    por_tipo = defaultdict(int)
    for c in plan["crear"]:
        por_tipo[c["tipo"]] += 1

    logger.info("\n=== Plan")
    logger.info(f"  crear: {len(plan['crear'])} líneas")
    for tipo, n in sorted(por_tipo.items()):
        logger.info(f"      {tipo}: {n}")
    logger.info(f"  archivar: {len(plan['archivar'])} líneas")
    logger.info(f"  repuntar Contenedor.line_item_id: {len(plan['repuntar_co'])}")
    if plan["sin_plantilla"]:
        logger.warning(f"  !! {len(plan['sin_plantilla'])} líneas sin plantilla de la que clonar:")
        for nombre, tipo in plan["sin_plantilla"][:10]:
            logger.warning(f"       {tipo} {nombre}")
        raise SystemExit("no se puede reconstruir sin plantilla; abortado")

    if plan["crear"]:
        ejemplo = plan["crear"][0]
        logger.info(f"\n  ejemplo de línea a crear ({ejemplo['tipo']}):")
        logger.info(json.dumps(ejemplo["properties"], indent=2, ensure_ascii=False)[:1500])

    if not args.apply:
        logger.info("\n(dry-run: nada escrito. Añade --apply para ejecutar)")
        return

    logger.info("\n=== Aplicando")
    stats = aplicar(TOKEN, args.deal, plan)
    logger.info(f"  {stats}")
    repuntar_lineas_contenedor(args.deal)

    logger.info(f"\n=== Negocio {args.deal}: estado final")
    verificar(args.deal)


if __name__ == "__main__":
    main()
