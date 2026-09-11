#!/usr/bin/env python3
"""Deja las líneas de artículo de un negocio al precio vigente de su contenedor.

El EXW por caja es del CONTENEDOR (los gastos se reparten entre sus cajas), así que
dentro de un contenedor todas las líneas de artículo van al mismo precio y a la misma
ancla. Cuando una reparación clona el precio de una línea equivocada, unas cuantas
quedan con el precio anterior a la última negociación y el contenedor queda mezclado.

Caso: deal 64731186508 (11/09/2026). Tras reponer lo que el espejo del publish se
llevó, 13 líneas nacieron con el ancla 6,73 anterior a la última negociación; corregir
"por mayoría" empujó además 4 líneas buenas del contenedor -013 hacia atrás, porque
allí las viejas eran mayoría. La tabla de abajo es el estado publicado (ancla 7,34),
leído de los clones del espejo antes de tocar nada.

Uso:
    python scripts/alinear_precios_contenedor.py --deal 64731186508
    python scripts/alinear_precios_contenedor.py --deal 64731186508 --apply
"""

import argparse
import collections
import logging
import os
import sys
from pathlib import Path

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(root_dir / ".env")

import app.services.espejo_publish as ep  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN") or os.getenv("PROEX_ACCESS_TOKEN")

ANCLA = "7.34"
INCOTERM = "CIF"
VIGENTE = {
    "001": "6.415", "002": "6.415", "003": "6.415", "004": "6.415", "005": "6.415",
    "006": "6.596", "007": "6.171", "008": "6.415", "009": "6.415", "010": "6.415",
    "011": "6.573", "012": "5.704", "013": "6.048", "014": "6.396", "015": "6.548",
    "016": "6.415", "017": "6.396", "018": "6.548", "019": "6.396", "020": "6.494",
}

PROPS = ["name", "tipo_articulo", "price", "precio_negociado_tipo_venta",
         "numero_container", "hs_product_type", "quantity"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    ap.add_argument("--apply", action="store_true", help="sin esto solo imprime el plan")
    args = ap.parse_args()

    if not TOKEN:
        raise SystemExit("falta HUBSPOT_ACCESS_TOKEN")

    ids = ep._assoc_ids(TOKEN, "deals", args.deal, "line_items")
    items = ep._batch_read(TOKEN, "line_items", ids, PROPS)

    updates = []
    for x in items:
        p = x["properties"]
        if p.get("tipo_articulo") != "fabricante" or ep._es_contenedor(p):
            continue
        esperado = VIGENTE.get(p["numero_container"].split("-")[-1])
        if not esperado:
            logger.warning(f"  !! {p['name']}: contenedor fuera de la tabla; se salta")
            continue
        if (p.get("price"), p.get("precio_negociado_tipo_venta")) != (esperado, ANCLA):
            logger.info(f"  {p['name'][:50]:<50} "
                        f"{p.get('price')}/{p.get('precio_negociado_tipo_venta')}"
                        f" -> {esperado}/{ANCLA}")
            updates.append({"id": x["id"], "properties": {
                "price": esperado,
                "precio_negociado_tipo_venta": ANCLA,
                "incoterm_precio_negociado": INCOTERM,
            }})

    logger.info(f"\nlíneas a alinear: {len(updates)}")
    if not args.apply:
        logger.info("(dry-run: nada escrito. Añade --apply)")
        return

    for i in range(0, len(updates), 100):
        ep._api(TOKEN, "POST", "/crm/v3/objects/line_items/batch/update",
                json={"inputs": updates[i:i + 100]})

    items = ep._batch_read(TOKEN, "line_items", ids, PROPS)
    fab = [x["properties"] for x in items
           if x["properties"].get("tipo_articulo") == "fabricante"
           and not ep._es_contenedor(x["properties"])]
    por_cont = collections.defaultdict(set)
    for p in fab:
        por_cont[p["numero_container"]].add(
            (p.get("price"), p.get("precio_negociado_tipo_venta")))
    mezcla = {k: v for k, v in por_cont.items() if len(v) > 1}
    total = sum(float(x["properties"].get("price") or 0)
                * float(x["properties"].get("quantity") or 0) for x in items)

    logger.info(f"\ncontenedores con precio no uniforme: {mezcla or 'ninguno'}")
    logger.info(f"anclas: {dict(collections.Counter(p.get('precio_negociado_tipo_venta') for p in fab))}")
    logger.info(f"total del negocio: {total:,.2f} EUR")


if __name__ == "__main__":
    main()
