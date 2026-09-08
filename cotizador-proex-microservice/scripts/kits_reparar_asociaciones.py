#!/usr/bin/env python3
"""
Recompone las asociaciones etiquetadas Cotizacion <-> Pedido KIT a partir de la
propiedad `kit_generado_por_ids`.

POR QUE EXISTE
El vinculo entre un KIT y las ofertas que lo generan esta guardado DOS veces: en
la propiedad serializada y como asociacion etiquetada. Fue decision de Cristobal
mantener las dos ("si puedes mantener esto y tratar de hacer la asociacion
tambien", reunion del 2026-09-03), porque la propiedad no se ve en el registro de
HubSpot y la asociacion si.

Duplicar un dato es duplicar las formas de que se desincronice: una asociacion
que falla al crear el KIT, un borrado a mano desde la interfaz, un KIT creado
antes de que la asociacion existiera. Este script pone las dos versiones de
acuerdo.

LA PROPIEDAD MANDA. Nunca se toca `kit_generado_por_ids` ni se borra ninguna
asociacion: solo se AÑADEN las que faltan. Si una asociacion sobra, se quita a
mano — borrar en automatico un vinculo que alguien puso a proposito es peor que
dejarlo.

Sin `--apply` no escribe nada.

Uso:
    python scripts/kits_reparar_asociaciones.py
    python scripts/kits_reparar_asociaciones.py --kit 63361158165
    python scripts/kits_reparar_asociaciones.py --apply
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from app.core.kits_constants import (  # noqa: E402
    ASOC_COTIZACION_A_KIT,
    PROP_KIT_GENERADO_POR_IDS,
    PROP_KIT_NUMERO,
)
from app.services.kits_service import (  # noqa: E402
    BASE_URL,
    _asociar_etiquetada,
    _get,
    buscar_kits,
)
from app.utils.kits import parsear_marcas_kit  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("kits_reparar")


def negocios_ya_asociados(kit_id: str, token: str) -> set:
    """Ids de negocio ya asociados al KIT con la etiqueta de generador."""
    try:
        res = _get(
            f"{BASE_URL}/crm/v4/objects/deals/{kit_id}/associations/deals", token
        )
    except RuntimeError as e:
        logger.warning(f"  no se pudieron leer las asociaciones de {kit_id}: {e}")
        return set()

    ids = set()
    for fila in res.get("results", []):
        tipos = {t.get("typeId") for t in fila.get("associationTypes", [])}
        if ASOC_COTIZACION_A_KIT in tipos:
            ids.add(str(fila.get("toObjectId")))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recompone las asociaciones de generador desde la propiedad."
    )
    parser.add_argument("--kit", default="", help="Reparar solo este KIT (deal id)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Escribe de verdad. Sin esto solo informa.",
    )
    args = parser.parse_args()

    load_dotenv()
    token = os.getenv("PROEX_ACCESS_TOKEN") or os.getenv("HUBSPOT_ACCESS_TOKEN")
    if not token:
        logger.error("Falta PROEX_ACCESS_TOKEN en el entorno.")
        return 1

    kits = buscar_kits(token)
    if args.kit:
        kits = [k for k in kits if str(k["id"]) == str(args.kit).strip()]
        if not kits:
            logger.error(f"No hay ningun KIT con id {args.kit}.")
            return 1

    sin_propiedad = 0
    total_faltan = 0
    total_puestas = 0

    for d in kits:
        kit_id = str(d["id"])
        p = d.get("properties") or {}
        numero = p.get(PROP_KIT_NUMERO) or kit_id

        generadores = parsear_marcas_kit(p.get(PROP_KIT_GENERADO_POR_IDS))
        if not generadores:
            # Un KIT anterior a la propiedad, o creado a mano en el pipeline. No
            # se inventa nada: sin propiedad no hay nada que reflejar.
            sin_propiedad += 1
            continue

        ya = negocios_ya_asociados(kit_id, token)
        faltan = sorted(set(generadores) - ya)

        logger.info(
            f"{numero} (deal {kit_id}): {len(generadores)} generadores, "
            f"{len(ya)} asociados, {len(faltan)} por asociar"
        )
        if not faltan:
            continue

        total_faltan += len(faltan)
        for deal_id in faltan:
            if not args.apply:
                logger.info(f"    [dry-run] asociaria el negocio {deal_id}")
                continue
            try:
                _asociar_etiquetada(deal_id, kit_id, ASOC_COTIZACION_A_KIT, token)
                total_puestas += 1
                logger.info(f"    asociado el negocio {deal_id}")
            except RuntimeError as e:
                logger.warning(f"    fallo al asociar {deal_id}: {e}")

    logger.info("")
    logger.info(f"KIT revisados: {len(kits)}")
    logger.info(f"KIT sin la propiedad (se dejan como estan): {sin_propiedad}")
    if args.apply:
        logger.info(f"Asociaciones creadas: {total_puestas} de {total_faltan}")
    else:
        logger.info(f"Asociaciones que faltan: {total_faltan}  (usa --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
