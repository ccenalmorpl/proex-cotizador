#!/usr/bin/env python3
"""
Script para ejercitar el ciclo de vida de un pedido KIT contra HubSpot real.

Cierra el hueco entre las funciones puras de `app/utils/kits.py` y HubSpot, sin
desplegar nada: cada subcomando calcula el PATCH con esas funciones y lo ensena.

    recalcular          recalcula la etapa de todos los KIT del pipeline
    marcar              un negocio reserva N cajas de un KIT
    desmarcar           ese negocio suelta su reserva
    validar-consumo     Administracion confirma: la marca pasa a consumo y
                        descuenta cajas disponibles de verdad
    podar               quita marcas de negocios que ya no existen

NO ESCRIBE POR DEFECTO. Sin `--apply` solo imprime lo que haria, igual que los
scripts `repair_*.py` de este mismo directorio. Con `--apply` manda los PATCH.

Uso:
    python scripts/kits_ciclo.py recalcular
    python scripts/kits_ciclo.py marcar --kit 64133402483 --oferta 12345 --cajas 250
    python scripts/kits_ciclo.py validar-consumo --kit 64133402483 --oferta 12345
    python scripts/kits_ciclo.py podar --kit 64133402483
    python scripts/kits_ciclo.py recalcular --apply
"""

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv

from app.core.kits_constants import (
    KIT_DEALSTAGE_LABELS,
    KIT_PIPELINE_ID,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_CONSUMIDO_POR_IDS,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_NUMERO,
    PROP_KIT_VALIDACION_PRODUCCION,
)
from app.utils.kits import (
    aplicar_marca_kit,
    calcular_cajas_disponibles,
    calcular_dealstage_kit,
    parsear_marcas_kit,
    podar_marcas_kit,
    serializar_marcas_kit,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
load_dotenv(root_dir.parent.parent / ".env")

PROPIEDADES = [
    "dealname",
    "dealstage",
    PROP_KIT_NUMERO,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_VALIDACION_PRODUCCION,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_CONSUMIDO_POR_IDS,
]

BASE = "https://api.hubapi.com"


def _token() -> str:
    for var in ("HUBSPOT_API_TOKEN", "PROEX_ACCESS_TOKEN", "HUBSPOT_ACCESS_TOKEN"):
        tok = os.getenv(var)
        if tok:
            return tok.strip()
    raise ValueError("No hay token (HUBSPOT_API_TOKEN / PROEX_ACCESS_TOKEN).")


def _req(method: str, url: str, token: str, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp) if resp.status != 204 else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HubSpot {e.code}: {e.read()[:400].decode('utf-8', 'replace')}") from e


def leer_kit(deal_id: str, token: str) -> dict:
    props = ",".join(PROPIEDADES)
    return _req("GET", f"{BASE}/crm/v3/objects/deals/{deal_id}?properties={props}", token)


def buscar_kits(token: str) -> list:
    kits, after = [], None
    while True:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "pipeline", "operator": "EQ", "value": KIT_PIPELINE_ID}]}
            ],
            "properties": PROPIEDADES,
            "limit": 100,
        }
        if after:
            body["after"] = after
        res = _req("POST", f"{BASE}/crm/v3/objects/deals/search", token, body)
        kits.extend(res.get("results", []))
        after = (res.get("paging") or {}).get("next", {}).get("after")
        if not after:
            return kits


def existe_deal(deal_id: str, token: str) -> bool:
    """Si el negocio existe y no esta archivado. Base de la poda."""
    try:
        _req("GET", f"{BASE}/crm/v3/objects/deals/{deal_id}?properties=dealname", token)
        return True
    except RuntimeError as e:
        if "404" in str(e):
            return False
        raise


def _patch(deal_id: str, cambios: dict, token: str, apply: bool, etiqueta: str) -> None:
    """Unico punto de escritura del script. Sin --apply solo imprime."""
    if not cambios:
        logger.info(f"  {etiqueta}: nada que cambiar")
        return

    logger.info(f"  {etiqueta}: PATCH deal {deal_id}")
    for k, v in cambios.items():
        etiqueta_valor = KIT_DEALSTAGE_LABELS.get(v, "") if k == "dealstage" else ""
        sufijo = f"  ({etiqueta_valor})" if etiqueta_valor else ""
        logger.info(f"      {k} = {v!r}{sufijo}")

    if not apply:
        logger.info("      [dry-run] no se ha enviado")
        return

    _req("PATCH", f"{BASE}/crm/v3/objects/deals/{deal_id}", token, {"properties": cambios})
    logger.info("      ENVIADO")


def _estado(p: dict) -> dict:
    """Estado derivado de un KIT a partir de sus propiedades crudas."""
    disponibles = calcular_cajas_disponibles(
        p.get(PROP_KIT_CAJAS), p.get(PROP_KIT_CONSUMIDO_POR_IDS)
    )
    validado = (p.get(PROP_KIT_VALIDACION_PRODUCCION) or "").lower() == "true"
    etapa = p.get("dealstage") or ""
    return {
        "disponibles": disponibles,
        "validado": validado,
        "etapa": etapa,
        "etapa_derivada": calcular_dealstage_kit(
            validado_produccion=validado,
            cajas_disponibles=disponibles,
            dealstage_actual=etapa,
        ),
        "marcas": parsear_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS)),
        "consumos": parsear_marcas_kit(p.get(PROP_KIT_CONSUMIDO_POR_IDS)),
    }


def _cambios_de_sincronizacion(p: dict, e: dict) -> dict:
    """Etapa y `cajas_disponibles_kit` puestas al dia con lo que dicen las
    propiedades. Se calcula siempre igual, sin importar qué subcomando lo pida:
    así ninguna operación puede dejar el KIT descuadrado."""
    cambios = {}
    if e["etapa_derivada"] != e["etapa"]:
        cambios["dealstage"] = e["etapa_derivada"]

    cacheadas = p.get(PROP_KIT_CAJAS_DISPONIBLES)
    if cacheadas in (None, "") or int(float(cacheadas)) != e["disponibles"]:
        cambios[PROP_KIT_CAJAS_DISPONIBLES] = str(e["disponibles"])
    return cambios


# SUBCOMANDOS


def cmd_recalcular(args, token):
    kits = buscar_kits(token)
    logger.info(f"KIT en el pipeline: {len(kits)}")
    tocados = 0
    for d in kits:
        p = d["properties"]
        e = _estado(p)
        numero = p.get(PROP_KIT_NUMERO) or d["id"]
        cambios = _cambios_de_sincronizacion(p, e)
        if not cambios:
            continue
        tocados += 1
        logger.info("")
        logger.info(f"{numero} (deal {d['id']}) — {e['disponibles']} disponibles")
        _patch(d["id"], cambios, token, args.apply, "sincronizar")
    logger.info("")
    logger.info(f"KIT descuadrados: {tocados}")


def cmd_marcar(args, token):
    kit = leer_kit(args.kit, token)
    p = kit["properties"]
    e = _estado(p)
    numero = p.get(PROP_KIT_NUMERO) or args.kit

    ya_marcado_por_otros = sum(c for d, c in e["marcas"].items() if d != args.oferta)
    libre = e["disponibles"] - ya_marcado_por_otros

    logger.info(f"{numero} (deal {args.kit})")
    logger.info(f"  cajas disponibles      : {e['disponibles']}")
    logger.info(f"  ya marcadas por otros  : {ya_marcado_por_otros}")
    logger.info(f"  margen para esta oferta: {libre}")

    if args.cajas > libre:
        logger.info("")
        logger.info(
            f"  ABORTA: se piden {args.cajas} cajas y solo quedan {libre} sin reservar."
        )
        return

    nuevo = aplicar_marca_kit(p.get(PROP_KIT_MARCADO_POR_IDS), args.oferta, args.cajas)
    logger.info("")
    _patch(args.kit, {PROP_KIT_MARCADO_POR_IDS: nuevo}, token, args.apply, "marcar")
    # Marcar NO mueve etapa ni descuenta: una reserva sin validar puede caerse.
    logger.info("  (la etapa y cajas_disponibles_kit no cambian: marcar es reservar)")


def cmd_desmarcar(args, token):
    kit = leer_kit(args.kit, token)
    p = kit["properties"]
    nuevo = aplicar_marca_kit(p.get(PROP_KIT_MARCADO_POR_IDS), args.oferta, 0)
    logger.info(f"{p.get(PROP_KIT_NUMERO) or args.kit} (deal {args.kit})")
    logger.info(f"  marcas antes : {parsear_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS))}")
    logger.info(f"  marcas ahora : {parsear_marcas_kit(nuevo)}")
    _patch(args.kit, {PROP_KIT_MARCADO_POR_IDS: nuevo}, token, args.apply, "desmarcar")


def cmd_validar_consumo(args, token):
    """Administracion confirma la reserva: la marca se convierte en consumo.

    Es el unico paso que descuenta cajas de verdad, y por eso el unico que puede
    llevar un KIT a Agotado.
    """
    kit = leer_kit(args.kit, token)
    p = kit["properties"]
    e = _estado(p)
    numero = p.get(PROP_KIT_NUMERO) or args.kit

    cajas = e["marcas"].get(args.oferta)
    logger.info(f"{numero} (deal {args.kit})")
    if not cajas:
        logger.info(f"  ABORTA: el negocio {args.oferta} no tiene marca en este KIT.")
        logger.info(f"  marcas actuales: {e['marcas'] or '(ninguna)'}")
        return

    marcas = dict(e["marcas"])
    marcas.pop(args.oferta)
    consumos = dict(e["consumos"])
    consumos[args.oferta] = consumos.get(args.oferta, 0) + cajas

    raw_consumos = serializar_marcas_kit(consumos)
    disponibles_despues = calcular_cajas_disponibles(p.get(PROP_KIT_CAJAS), raw_consumos)
    etapa_despues = calcular_dealstage_kit(
        validado_produccion=e["validado"],
        cajas_disponibles=disponibles_despues,
        dealstage_actual=e["etapa"],
    )

    logger.info(f"  consume {cajas} cajas del negocio {args.oferta}")
    logger.info(f"  disponibles: {e['disponibles']} -> {disponibles_despues}")
    logger.info(
        f"  etapa      : {KIT_DEALSTAGE_LABELS.get(e['etapa'], e['etapa'])} -> "
        f"{KIT_DEALSTAGE_LABELS.get(etapa_despues, etapa_despues)}"
    )

    cambios = {
        PROP_KIT_MARCADO_POR_IDS: serializar_marcas_kit(marcas),
        PROP_KIT_CONSUMIDO_POR_IDS: raw_consumos,
        PROP_KIT_CAJAS_DISPONIBLES: str(disponibles_despues),
    }
    if etapa_despues != e["etapa"]:
        cambios["dealstage"] = etapa_despues

    logger.info("")
    _patch(args.kit, cambios, token, args.apply, "validar-consumo")


def cmd_podar(args, token):
    objetivo = [leer_kit(args.kit, token)] if args.kit else buscar_kits(token)
    logger.info(f"Revisando marcas de {len(objetivo)} KIT")

    for kit in objetivo:
        p = kit["properties"]
        marcas = parsear_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS))
        if not marcas:
            continue

        numero = p.get(PROP_KIT_NUMERO) or kit["id"]
        vivos = [d for d in marcas if existe_deal(d, token)]
        muertos = [d for d in marcas if d not in vivos]

        logger.info("")
        logger.info(f"{numero} (deal {kit['id']})")
        logger.info(f"  marcas: {marcas}")
        if not muertos:
            logger.info("  todas apuntan a negocios vivos: nada que podar")
            continue

        logger.info(f"  negocios inexistentes: {muertos}")
        nuevo = podar_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS), vivos)
        _patch(kit["id"], {PROP_KIT_MARCADO_POR_IDS: nuevo}, token, args.apply, "podar")


def main():
    parser = argparse.ArgumentParser(description="Ciclo de vida de los pedidos KIT.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Enviar los cambios a HubSpot. Sin esta bandera solo se imprimen.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("recalcular", help="Pone al dia etapa y cajas disponibles de todos los KIT")

    m = sub.add_parser("marcar", help="Un negocio reserva cajas de un KIT")
    m.add_argument("--kit", required=True)
    m.add_argument("--oferta", required=True)
    m.add_argument("--cajas", type=int, required=True)

    dm = sub.add_parser("desmarcar", help="Un negocio suelta su reserva")
    dm.add_argument("--kit", required=True)
    dm.add_argument("--oferta", required=True)

    v = sub.add_parser("validar-consumo", help="La marca pasa a consumo y descuenta cajas")
    v.add_argument("--kit", required=True)
    v.add_argument("--oferta", required=True)

    po = sub.add_parser("podar", help="Quita marcas de negocios inexistentes")
    po.add_argument("--kit", default="", help="Un KIT concreto; por defecto, todos")

    args = parser.parse_args()
    token = _token()

    if not args.apply:
        logger.info(">>> DRY-RUN: no se escribe nada. Anade --apply para enviar.")
        logger.info("")

    {
        "recalcular": cmd_recalcular,
        "marcar": cmd_marcar,
        "desmarcar": cmd_desmarcar,
        "validar-consumo": cmd_validar_consumo,
        "podar": cmd_podar,
    }[args.cmd](args, token)


if __name__ == "__main__":
    main()
