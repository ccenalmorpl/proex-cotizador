#!/usr/bin/env python3
"""
Script de SOLO LECTURA para auditar los pedidos KIT en HubSpot.

Usa el MISMO servicio que el endpoint que consume la card
(`app/services/kits_service.py`), asi que lo que ves aqui es exactamente lo que
la card recibe. Si algun dia divergen, es un bug.

Avisa de los descuadres que impedirian que un KIT se vea en la card:
    - SKU vacio        -> invisible, la card empareja por SKU
    - etapa incoherente con las propiedades (segun `calcular_dealstage_kit`)
    - cajas disponibles cacheadas que no cuadran con el calculo
    - amount relleno, que colaria el KIT en el forecast

NO escribe nada. Ni una propiedad, ni una asociacion, ni una etapa.
Para escribir esta `scripts/kits_ciclo.py`.

Uso:
    python scripts/kits_disponibles.py
    python scripts/kits_disponibles.py --sku 0303003002-00250
    python scripts/kits_disponibles.py --json
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv

from app.core.kits_constants import KIT_PIPELINE_ID
from app.services.kits_service import analizar, buscar_kits, kits_disponibles_para_card

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
# El .env de la raiz del proyecto usa HUBSPOT_API_TOKEN; el del microservicio,
# PROEX_ACCESS_TOKEN. Se acepta cualquiera para poder correrlo desde ambos sitios.
load_dotenv(root_dir.parent.parent / ".env")


def _token() -> str:
    for var in ("PROEX_ACCESS_TOKEN", "HUBSPOT_API_TOKEN", "HUBSPOT_ACCESS_TOKEN"):
        tok = os.getenv(var)
        if tok:
            return tok.strip()
    raise ValueError(
        "No hay token. Se busca PROEX_ACCESS_TOKEN, HUBSPOT_API_TOKEN o "
        "HUBSPOT_ACCESS_TOKEN en el entorno o en un .env."
    )


def main():
    parser = argparse.ArgumentParser(description="Audita los pedidos KIT (solo lectura).")
    parser.add_argument("--sku", default="", help="Filtrar por SKU de producto")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emitir los KIT consumibles con la forma que consume la card, para "
        "volcarlos al fixture mientras la card no llame al endpoint.",
    )
    args = parser.parse_args()

    token = _token()
    skus = [args.sku.strip()] if args.sku.strip() else []

    if args.json:
        print(json.dumps(kits_disponibles_para_card(token, skus), indent=2, ensure_ascii=False))
        return

    kits = buscar_kits(token)
    logger.info("=" * 78)
    logger.info(f"PEDIDOS KIT en pipeline {KIT_PIPELINE_ID}: {len(kits)} negocio(s)")
    logger.info("=" * 78)

    res = analizar(kits, skus)

    for sku, filas in sorted(res["por_sku"].items()):
        logger.info("")
        logger.info(f"SKU {sku or '(VACIO)'}")
        consumibles = [f for f in filas if f["consumible"]]
        for f in filas:
            marca = "OK " if f["consumible"] else "-- "
            extra = ""
            if f["marcas"]:
                extra += f"  marcado_por={f['marcas']}"
            if f["consumos"]:
                extra += f"  consumido_por={f['consumos']}"
            logger.info(
                f"  {marca}{f['numero'] or f['deal_id']:16} "
                f"{f['disponibles']:>6} / {f['cajas_kit']:<6} cajas   "
                f"[{f['etapa_label']}]{extra}"
            )
        total = sum(f["disponibles"] for f in consumibles)
        logger.info(
            f"  -> consumible ahora para este SKU: {total} cajas en {len(consumibles)} KIT(s)"
        )

    logger.info("")
    if res["avisos"]:
        logger.info("=" * 78)
        logger.info(f"AVISOS ({len(res['avisos'])})")
        logger.info("=" * 78)
        for a in res["avisos"]:
            logger.info(f"  ! {a}")
    else:
        logger.info("Sin descuadres.")


if __name__ == "__main__":
    main()
