"""Realinea `tipo_iva_soportado` / `tipo_iva_repercutido` de las líneas de
mercancía con el maestro de productos.

El cotizador resuelve el IVA de la ficha del producto al crear la línea y luego
manda lo guardado, así que un saneado posterior del maestro (el del 01/09/2026)
no alcanza a los expedientes ya creados: se quedan con el código que era válido
el día que nacieron. Este script recorre esos expedientes y los pone al día.

La autoridad es `resolve_product_iva` — la misma función que usa el cotizador —,
así que el script no replica el criterio: lo consulta.

Solo toca líneas de expedientes con eje EUROPA o EXTRANJERO. Las de eje NACIONAL
con códigos de exportación son clientes españoles de exportación y Ceuta/Melilla,
donde el valor lo ha puesto una persona a conciencia.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    python -m scripts.repair_iva_mercancia --deal 63361092531
    python -m scripts.repair_iva_mercancia --desde 2026-01-01
    python -m scripts.repair_iva_mercancia --deal 63361092531 --apply

Omite el expediente completo si alguno de sus Negocios Pedido tiene factura
emitida: cambiar el IVA de una línea ya facturada es decisión de Administración.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app.core.custom_objects_constants import OBJECT_TYPE_ELEMENTO
from app.routes.deals import get_associated_companies
from app.services.hubspot_client import hubspot_client
from app.utils.property_helpers import _normalize_residennum, resolve_product_iva

CATEGORIAS = ("soportado", "repercutido")

PRODUCT_PROPS = [
    "name",
    "tipo_iva_soportado", "tipo_iva_soportado_eu", "tipo_iva_soportado_ex",
    "tipo_iva_repercutido", "tipo_iva_repercutido_eu", "tipo_iva_repercutido_ex",
]

LINE_ITEM_PROPS = [
    "name", "numero_container", "tipo_articulo", "hs_product_id",
    "tipo_iva_soportado", "tipo_iva_repercutido", "createdate",
]

ELEMENTO_PROPS = [
    "nombre", "nombre_contenedor", "tipo_articulo", "line_item_id",
    "tipo_iva_soportado", "tipo_iva_repercutido",
]

# Códigos que el arrastre puede haber dejado en la línea. Cualquier otro
# (REXO, S21A…) es una elección manual y se deja intacta.
HEREDABLES = {
    "soportado": {"", "S21", "SEXP", "A21"},
    "repercutido": {"", "R21", "EI21", "EX"},
}


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}


def _batch_read(client, object_type: str, ids: List[str], props: List[str]) -> List[dict]:
    out = []
    for i in range(0, len(ids), 100):
        resp = requests.post(
            f"https://api.hubapi.com/crm/v3/objects/{object_type}/batch/read",
            headers=_headers(client),
            json={"properties": props, "inputs": [{"id": str(x)} for x in ids[i:i + 100]]},
            timeout=60,
        )
        resp.raise_for_status()
        out += resp.json().get("results", [])
    return out


def _batch_update(client, object_type: str, inputs: List[dict]) -> None:
    for i in range(0, len(inputs), 100):
        resp = requests.post(
            f"https://api.hubapi.com/crm/v3/objects/{object_type}/batch/update",
            headers=_headers(client),
            json={"inputs": inputs[i:i + 100]},
            timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(f"update {object_type} falló: {resp.status_code} {resp.text}")


def _associated(client, from_type: str, from_id: str, to_type: str) -> List[str]:
    resp = requests.get(
        f"https://api.hubapi.com/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}",
        headers=_headers(client), params={"limit": 500}, timeout=30,
    )
    resp.raise_for_status()
    return sorted({str(r["toObjectId"]) for r in resp.json().get("results", [])})


def _search_deals(client, desde: str) -> List[str]:
    millis = int(datetime.strptime(desde, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    ids, after = [], None
    while True:
        payload = {
            "filterGroups": [{"filters": [
                {"propertyName": "createdate", "operator": "GTE", "value": str(millis)}
            ]}],
            "properties": ["dealname"],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            headers=_headers(client), json=payload, timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        ids += [o["id"] for o in body.get("results", [])]
        after = body.get("paging", {}).get("next", {}).get("after")
        if not after:
            return ids


def facturas_emitidas(client, deal_id: str) -> List[str]:
    hijos = _associated(client, "deals", deal_id, "deals")
    if not hijos:
        return []
    numeros = set()
    for pedido in _batch_read(client, "deals", hijos, ["dealname", "numero_de_factura"]):
        numero = (pedido.get("properties", {}).get("numero_de_factura") or "").strip()
        if numero and numero.upper() != "ERROR":
            numeros.add(numero)
    return sorted(numeros)


def residennum_del_deal(client, deal_id: str) -> Optional[str]:
    company_ids = get_associated_companies(deal_id)
    if not company_ids:
        return None
    companies = _batch_read(client, "companies", company_ids[:1], ["name", "residennum"])
    if not companies:
        return None
    return companies[0].get("properties", {}).get("residennum") or ""


def analizar(client, deal_id: str) -> dict:
    """Compara el IVA de las líneas de mercancía del deal (y de sus Elementos)
    con lo que dicta el maestro para la residencia del cliente."""
    plan = {
        "deal_id": deal_id,
        "eje": None,
        "facturas": facturas_emitidas(client, deal_id),
        "line_items": [],
        "elementos": [],
        "omitidas": [],
        "snapshot": [],
    }
    if plan["facturas"]:
        return plan

    residennum = residennum_del_deal(client, deal_id)
    plan["eje"] = _normalize_residennum(residennum)
    if plan["eje"] == "NACIONAL":
        return plan

    line_item_ids = _associated(client, "deals", deal_id, "line_items")
    line_items = [
        li for li in _batch_read(client, "line_items", line_item_ids, LINE_ITEM_PROPS)
        if (li.get("properties", {}).get("tipo_articulo") or "").strip().lower() == "fabricante"
    ]
    if not line_items:
        return plan

    product_ids = sorted({
        str(li["properties"]["hs_product_id"]) for li in line_items
        if li["properties"].get("hs_product_id")
    })
    maestro = {
        p["id"]: p.get("properties", {})
        for p in _batch_read(client, "products", product_ids, PRODUCT_PROPS)
    }

    esperado_por_nombre: Dict[str, Dict[str, str]] = {}
    for li in line_items:
        props = li.get("properties", {}) or {}
        product_props = maestro.get(str(props.get("hs_product_id")))
        if not product_props:
            continue
        plan["snapshot"].append({"id": li["id"], "properties": props})

        cambios, esperados = {}, {}
        for categoria in CATEGORIAS:
            prop = f"tipo_iva_{categoria}"
            actual = (props.get(prop) or "").strip()
            esperado = (resolve_product_iva(product_props, residennum, categoria) or "").strip()
            esperados[prop] = esperado or actual
            if not esperado or esperado == actual:
                continue
            if actual not in HEREDABLES[categoria]:
                plan["omitidas"].append({
                    "id": li["id"], "nombre": props.get("name"), "prop": prop,
                    "actual": actual, "esperado": esperado,
                })
                continue
            cambios[prop] = esperado

        esperado_por_nombre[(props.get("name") or "").strip()] = esperados
        if cambios:
            plan["line_items"].append({
                "id": li["id"],
                "nombre": props.get("name"),
                "numero_container": props.get("numero_container"),
                "cambios": cambios,
                "antes": {p: (props.get(p) or "") for p in cambios},
            })

    elemento_ids = _associated(client, "deals", deal_id, OBJECT_TYPE_ELEMENTO)
    for el in _batch_read(client, OBJECT_TYPE_ELEMENTO, elemento_ids, ELEMENTO_PROPS):
        props = el.get("properties", {}) or {}
        if (props.get("tipo_articulo") or "").strip().lower() != "fabricante":
            continue
        esperados = esperado_por_nombre.get((props.get("nombre") or "").strip())
        if not esperados:
            continue
        plan["snapshot"].append({"id": el["id"], "properties": props, "tipo": "elemento"})

        cambios = {}
        for categoria in CATEGORIAS:
            prop = f"tipo_iva_{categoria}"
            actual = (props.get(prop) or "").strip()
            esperado = esperados.get(prop) or ""
            if esperado and esperado != actual and actual in HEREDABLES[categoria]:
                cambios[prop] = esperado.upper() if categoria == "repercutido" else esperado
        if cambios:
            plan["elementos"].append({
                "id": el["id"],
                "nombre": props.get("nombre"),
                "cambios": cambios,
                "antes": {p: (props.get(p) or "") for p in cambios},
            })

    return plan


def imprimir(plan: dict) -> None:
    deal_id = plan["deal_id"]
    if plan["facturas"]:
        print(f"{deal_id}  OMITIDO — factura emitida: {', '.join(plan['facturas'])}")
        return
    if plan["eje"] == "NACIONAL":
        print(f"{deal_id}  OMITIDO — eje NACIONAL (cliente español de exportación / Ceuta)")
        return
    if not plan["line_items"] and not plan["elementos"] and not plan["omitidas"]:
        print(f"{deal_id}  alineado ({plan['eje']})")
        return

    print(f"\n{deal_id}  eje={plan['eje']}  "
          f"líneas={len(plan['line_items'])}  elementos={len(plan['elementos'])}")
    for item in plan["line_items"]:
        detalle = "  ".join(
            f"{prop.replace('tipo_iva_', '')}: {item['antes'][prop] or '(vacío)'} → {valor}"
            for prop, valor in item["cambios"].items()
        )
        print(f"    LI {item['id']}  {detalle}   {(item['nombre'] or '')[:52]}")
    for item in plan["elementos"]:
        detalle = "  ".join(
            f"{prop.replace('tipo_iva_', '')}: {item['antes'][prop] or '(vacío)'} → {valor}"
            for prop, valor in item["cambios"].items()
        )
        print(f"    EL {item['id']}  {detalle}   {(item['nombre'] or '')[:52]}")
    for item in plan["omitidas"]:
        print(f"    -- {item['id']}  {item['prop'].replace('tipo_iva_', '')}="
              f"{item['actual']} es override manual, no se toca "
              f"(el maestro dice {item['esperado']})")


def aplicar(client, plan: dict) -> None:
    if plan["line_items"]:
        _batch_update(client, "line_items", [
            {"id": item["id"], "properties": item["cambios"]} for item in plan["line_items"]
        ])
        print(f"    actualizados {len(plan['line_items'])} line items")
    if plan["elementos"]:
        _batch_update(client, OBJECT_TYPE_ELEMENTO, [
            {"id": item["id"], "properties": item["cambios"]} for item in plan["elementos"]
        ])
        print(f"    actualizados {len(plan['elementos'])} elementos")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", action="append", dest="deals", help="ID de negocio (repetible)")
    parser.add_argument("--desde", help="Recorre los negocios creados desde esta fecha (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios en HubSpot")
    parser.add_argument("--backup-dir", default=".", help="Carpeta donde dejar el snapshot previo")
    args = parser.parse_args()

    if not args.deals and not args.desde:
        parser.error("indica --deal o --desde")

    client = hubspot_client.get_client()
    deal_ids = list(args.deals or [])
    if args.desde:
        deal_ids += [d for d in _search_deals(client, args.desde) if d not in deal_ids]
    print(f"Analizando {len(deal_ids)} negocios\n")

    planes, resumen = [], defaultdict(int)
    for deal_id in deal_ids:
        plan = analizar(client, deal_id)
        imprimir(plan)
        if plan["line_items"] or plan["elementos"]:
            planes.append(plan)
            resumen["line_items"] += len(plan["line_items"])
            resumen["elementos"] += len(plan["elementos"])
            resumen["negocios"] += 1
        if plan["facturas"]:
            resumen["omitidos_factura"] += 1
        if plan["omitidas"]:
            resumen["overrides_respetados"] += len(plan["omitidas"])

    print(f"\nTOTAL  negocios a reparar={resumen['negocios']}  "
          f"line items={resumen['line_items']}  elementos={resumen['elementos']}  "
          f"omitidos por factura={resumen['omitidos_factura']}  "
          f"overrides respetados={resumen['overrides_respetados']}")

    if not planes:
        print("\nNada que reparar.")
        return 0

    if not args.apply:
        print("\n[dry-run] No se ha modificado nada. Repite con --apply para ejecutar.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(args.backup_dir) / f"backup_iva_mercancia_{stamp}.json"
    backup.write_text(json.dumps(planes, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nSnapshot previo en {backup}")

    for plan in planes:
        print(f"  {plan['deal_id']}")
        aplicar(client, plan)

    print("\nHecho. Revisa un expediente en el cotizador y en la card de Pedidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
