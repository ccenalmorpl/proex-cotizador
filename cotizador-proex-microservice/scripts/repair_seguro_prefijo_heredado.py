"""Repara los expedientes que heredaron el numero_container de un negocio clonado.

Clonar un negocio en HubSpot copia sus line items con el prefijo del origen intacto
('63756493976-001'). El cotizador regenera contenedores, artículos y fletes con el
prefijo nuevo, pero los servicios heredados se quedan apuntando a contenedores que
no existen en este negocio: no entran en ningún Contenedor, no generan Elemento y
SAP nunca ve el concepto. El importe no se pierde —el reparto lo funde en el precio
unitario del producto— así que el total del contenedor cuadra y nada delata el fallo.

Caso de origen: EL SYRIANO'S 63837047422, clon de 63756493976, con 3 líneas de
SEGURO MERCANCÍA (60 € cada una) que nunca llegaron a SAP. Los SO 65266/67/68 y los
PO del fabricante (P0670) y del transitario (P0761) sí se crearon; falta el PO de la
aseguradora (P0082).

La reparación reasigna las líneas al negocio actual y relanza el re-sync de custom
objects, que recrea los Elementos y pasa por `recalcular_contenedores`: el ancla es
`precio_negociado_tipo_venta`, así que el seguro sale del EXW y vuelve a su línea sin
mover el total del contenedor. Nunca sumar los 60 € por fuera: se cobrarían dos veces.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    python scripts/repair_seguro_prefijo_heredado.py --deal 63837047422
    python scripts/repair_seguro_prefijo_heredado.py --deal 63837047422 --apply
    python scripts/repair_seguro_prefijo_heredado.py --scan       # barre todo el portal
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO
from app.routes.deals import _get_associated_ids, _sanear_prefijos_heredados
from app.services.custom_objects import process_deal_custom_objects_from_line_items
from app.services.hubspot_client import hubspot_client

PREFIJO = re.compile(r"^(\d{6,})-(\d{1,4})$")
PIPELINE_PEDIDOS = "725914649"
ASSOC_DEAL_TO_ELEMENTO = 486
LINE_ITEM_PROPS = [
    "name", "numero_container", "tipo_articulo", "hs_product_type",
    "price", "quantity", "cardcode", "hs_product_id",
]


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}


def _batch_read(client, object_type: str, ids: List[str], props: List[str]) -> List[dict]:
    out = []
    url = f"https://api.hubapi.com/crm/v3/objects/{object_type}/batch/read"
    for i in range(0, len(ids), 100):
        payload = {"properties": props, "inputs": [{"id": str(x)} for x in ids[i:i + 100]]}
        resp = requests.post(url, headers=_headers(client), json=payload, timeout=60)
        resp.raise_for_status()
        out.extend(resp.json().get("results", []))
    return out


def _line_items(client, deal_id: str) -> List[dict]:
    ids = _get_associated_ids(deal_id, "line_items") or []
    return _batch_read(client, "line_items", ids, LINE_ITEM_PROPS) if ids else []


def analizar(client, deal_id: str) -> Dict:
    lineas = _line_items(client, deal_id)
    sufijos_propios, heredadas = set(), []
    for li in lineas:
        props = li.get("properties", {}) or {}
        m = PREFIJO.match((props.get("numero_container") or "").strip())
        if not m:
            continue
        if m.group(1) == str(deal_id):
            if (props.get("hs_product_type") or "").strip().lower() in {"container", "contenedor"}:
                sufijos_propios.add(m.group(2))
        else:
            heredadas.append((li, m.group(1), m.group(2)))

    plan = []
    for li, origen, sufijo in heredadas:
        props = li["properties"]
        destino = f"{deal_id}-{sufijo}"
        accion = "archivar" if sufijos_propios and sufijo not in sufijos_propios else "reasignar"
        plan.append({
            "id": li["id"], "accion": accion, "origen": origen,
            "de": props.get("numero_container"), "a": destino if accion == "reasignar" else None,
            "tipo": props.get("tipo_articulo"), "price": props.get("price"),
            "cardcode": props.get("cardcode"), "name": props.get("name"),
        })
    return {"total_lineas": len(lineas), "sufijos_propios": sorted(sufijos_propios), "plan": plan}


def estado_contenedores(client, deal_id: str) -> List[Dict]:
    cont_ids = _get_associated_ids(deal_id, OBJECT_TYPE_CONTENEDOR) or []
    if not cont_ids:
        return []
    out = []
    for cont in _batch_read(client, OBJECT_TYPE_CONTENEDOR, cont_ids, ["nombre", "price"]):
        elem_ids = _get_associated_ids_from(client, OBJECT_TYPE_CONTENEDOR, cont["id"], OBJECT_TYPE_ELEMENTO)
        elems = _batch_read(client, OBJECT_TYPE_ELEMENTO, elem_ids,
                            ["nombre", "tipo_articulo", "precio_unitario", "precio_total"]) if elem_ids else []
        out.append({
            "id": cont["id"],
            "nombre": cont["properties"].get("nombre"),
            "price": cont["properties"].get("price"),
            "elementos": [e["properties"] for e in elems],
        })
    return sorted(out, key=lambda c: c["nombre"] or "")


def _get_associated_ids_from(client, from_type: str, from_id: str, to_type: str) -> List[str]:
    url = f"https://api.hubapi.com/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}"
    ids, after = [], None
    while True:
        params = {"limit": 500}
        if after:
            params["after"] = after
        resp = requests.get(url, headers=_headers(client), params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        ids += [str(r["toObjectId"]) for r in data.get("results", [])]
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            return ids


def _pedidos_por_contenedor(client, deal_id: str) -> Dict[str, str]:
    """Negocio Pedido de cada contenedor, por el nombre entre paréntesis del dealname."""
    hijos = _get_associated_ids_from(client, "deals", deal_id, "deals")
    if not hijos:
        return {}
    pedidos = {}
    for d in _batch_read(client, "deals", hijos, ["dealname", "pipeline", "facturar_pedido"]):
        props = d.get("properties") or {}
        if (props.get("pipeline") or "") != PIPELINE_PEDIDOS:
            continue
        nombre = props.get("dealname") or ""
        if "(" in nombre and ")" in nombre:
            pedidos[nombre[nombre.rfind("(") + 1:nombre.rfind(")")].strip()] = d["id"]
    return pedidos


def reasociar_elementos_a_pedidos(client, deal_id: str, apply: bool) -> int:
    """El re-sync asocia el Elemento nuevo a su Contenedor y al negocio padre, pero no
    al Negocio Pedido (typeId 486), que es de donde lee el asistente de Pedidos."""
    pedidos = _pedidos_por_contenedor(client, deal_id)
    if not pedidos:
        return 0
    pendientes = 0
    for cont in estado_contenedores(client, deal_id):
        pedido_id = pedidos.get(cont["nombre"])
        if not pedido_id:
            continue
        del_contenedor = set(_get_associated_ids_from(client, OBJECT_TYPE_CONTENEDOR, cont["id"], OBJECT_TYPE_ELEMENTO))
        del_pedido = set(_get_associated_ids_from(client, "deals", pedido_id, OBJECT_TYPE_ELEMENTO))
        faltan = sorted(del_contenedor - del_pedido)
        if not faltan:
            continue
        pendientes += len(faltan)
        print(f"  {cont['nombre']} -> pedido {pedido_id}: faltan {len(faltan)} elementos {faltan}")
        if not apply:
            continue
        inputs = [{
            "from": {"id": str(pedido_id)},
            "to": {"id": str(eid)},
            "types": [{"associationCategory": "USER_DEFINED", "associationTypeId": ASSOC_DEAL_TO_ELEMENTO}],
        } for eid in faltan]
        resp = requests.post(
            f"https://api.hubapi.com/crm/v4/associations/deals/{OBJECT_TYPE_ELEMENTO}/batch/create",
            headers=_headers(client), json={"inputs": inputs}, timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(f"asociación deal→elemento falló: {resp.status_code} {resp.text}")
    return pendientes


def _print_contenedores(titulo: str, contenedores: List[Dict]) -> None:
    print(f"\n{titulo}")
    for c in contenedores:
        suma = sum(float(e.get("precio_total") or 0) for e in c["elementos"])
        print(f"  {c['nombre']}  price={c['price']}  ({len(c['elementos'])} elementos, suman {suma:.2f})")
        for e in sorted(c["elementos"], key=lambda x: x.get("tipo_articulo") or ""):
            print(f"     {str(e.get('tipo_articulo')):<12} pu={str(e.get('precio_unitario')):<9} "
                  f"total={str(e.get('precio_total')):<10} {e.get('nombre')}")


def scan(client) -> None:
    """Barre el portal entero buscando líneas con el prefijo de otro negocio."""
    after, total, afectados = None, 0, {}
    url = "https://api.hubapi.com/crm/v3/objects/line_items"
    while True:
        params = {"limit": 100, "properties": "numero_container,name,tipo_articulo,price",
                  "associations": "deals"}
        if after:
            params["after"] = after
        resp = requests.get(url, headers=_headers(client), params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for li in data.get("results", []):
            total += 1
            m = PREFIJO.match((li["properties"].get("numero_container") or "").strip())
            if not m:
                continue
            deals = [r["id"] for r in ((li.get("associations") or {}).get("deals") or {}).get("results", [])]
            if deals and m.group(1) not in deals:
                for d in deals:
                    afectados.setdefault(d, []).append(li)
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break

    print(f"{total} line items barridos — {len(afectados)} negocios afectados")
    for deal_id, lineas in sorted(afectados.items(), key=lambda x: -len(x[1])):
        origenes = sorted({(l["properties"]["numero_container"] or "").split("-")[0] for l in lineas})
        tipos = sorted({l["properties"].get("tipo_articulo") for l in lineas})
        print(f"  deal {deal_id}: {len(lineas)} líneas {tipos} heredadas de {origenes}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deal", help="Negocio a analizar o reparar")
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (por defecto simula)")
    parser.add_argument("--scan", action="store_true", help="Barre el portal y sale")
    args = parser.parse_args()

    client = hubspot_client.get_client()

    if args.scan:
        scan(client)
        return 0
    if not args.deal:
        parser.error("indica --deal o --scan")

    diag = analizar(client, args.deal)
    print(f"Deal {args.deal}: {diag['total_lineas']} line items, "
          f"contenedores propios {diag['sufijos_propios']}")
    if not diag["plan"]:
        print("Sin líneas heredadas: nada que reparar.")
        return 0

    print(f"\n{len(diag['plan'])} líneas heredadas:")
    for p in diag["plan"]:
        destino = p["a"] or "ARCHIVAR"
        print(f"  {p['id']}  {p['tipo']:<12} {p['de']} -> {destino}  "
              f"({p['price']} €, cardcode {p['cardcode']})")

    antes = estado_contenedores(client, args.deal)
    _print_contenedores("ANTES:", antes)

    if not args.apply:
        print("\n(simulación — repite con --apply para escribir)")
        return 0

    lineas = _line_items(client, args.deal)
    _sanear_prefijos_heredados(
        deal_id=args.deal, all_line_items=lineas, headers=_headers(client), batch_size=100,
    )
    print("\nLíneas reasignadas. Re-sincronizando custom objects…")
    stats = process_deal_custom_objects_from_line_items(args.deal)
    print(f"  Contenedores: +{stats['contenedores_created']} ~{stats['contenedores_updated']} "
          f"-{stats['contenedores_deleted']}")
    print(f"  Elementos:    +{stats['elementos_created']} ~{stats['elementos_updated']} "
          f"-{stats['elementos_deleted']}")
    for err in stats.get("errors", []):
        print(f"  ERROR: {err}")

    print("\nReasociando Elementos a su Negocio Pedido:")
    reasignadas = reasociar_elementos_a_pedidos(client, args.deal, apply=True)
    print(f"  {reasignadas} asociaciones creadas" if reasignadas else "  nada pendiente")

    despues = estado_contenedores(client, args.deal)
    _print_contenedores("DESPUÉS:", despues)

    price_antes = {c["nombre"]: c["price"] for c in antes}
    for c in despues:
        previo = price_antes.get(c["nombre"])
        if previo is not None and float(previo) != float(c["price"] or 0):
            print(f"\n  ¡ATENCIÓN! {c['nombre']} cambió de importe: {previo} -> {c['price']}. "
                  f"El total del contenedor no debía moverse; revísalo antes de tocar SAP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
