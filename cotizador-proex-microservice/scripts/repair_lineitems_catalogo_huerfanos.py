"""Detecta y archiva line items de catálogo huérfanos: artículos con
tipo_articulo=fabricante SIN numero_container que quedaron en el deal después de
crear contenedores (los creaba update_deal_articles al guardar ArticlesTab y el
merge incremental de update_deal_containers no los archivaba; ver
_catalogo_huerfano_ids en app/routes/deals.py).

Solo actúa sobre deals CON contenedores configurados y solo archiva huérfanos
cuyo producto está COLOCADO en alguna línea per-contenedor viva: la línea de
catálogo de un artículo sin colocar es su única persistencia (GET /articles
reconstruye la selección desde ella) y se conserva — de la cotización publicada
ya la excluye el backstop de publish_quote. La exclusión por prefijo
'{contenedor} - ' (por name Y numero_container, cubre el formato legacy) protege
líneas per-contenedor que hubieran perdido numero_container. Las cotizaciones ya
publicadas conservan sus propias copias de line items (asociadas al quote, no al
deal): este script no las modifica; la corrección aplica a publicaciones futuras.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    # Detección (NO modifica nada):
    python -m scripts.repair_lineitems_catalogo_huerfanos --deal 61763928105
    python -m scripts.repair_lineitems_catalogo_huerfanos --all

    # Archivar (mutación real):
    python -m scripts.repair_lineitems_catalogo_huerfanos --deal 61763928105 --apply
    python -m scripts.repair_lineitems_catalogo_huerfanos --all --apply
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.routes.deals import (
    _catalogo_huerfano_ids,
    _get_associated_ids,
    _is_container_product_type,
    _line_item_is_article,
)
from app.services.hubspot_client import hubspot_client

LINE_ITEM_PROPS = [
    "name", "numero_container", "tipo_articulo", "hs_product_type",
    "hs_product_id", "price", "quantity",
]

_errores: List[str] = []


def _error(msg: str) -> None:
    _errores.append(msg)
    print(f"  [error] {msg}")


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}


def _fetch_line_items(client, deal_id: str) -> List[dict]:
    """Line items del deal con las propiedades que necesita _catalogo_huerfano_ids."""
    ids = _get_associated_ids(deal_id, "line_items")
    line_items: List[dict] = []
    read_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        payload = {"properties": LINE_ITEM_PROPS, "inputs": [{"id": str(x)} for x in chunk]}
        r = requests.post(read_url, headers=_headers(client), json=payload, timeout=30)
        if not r.ok:
            _error(f"batch read line items deal {deal_id}: {r.status_code} {r.text}")
            continue
        line_items.extend(r.json().get("results", []))
    return line_items


def _analizar_deal(client, deal_id: str) -> Optional[dict]:
    line_items = _fetch_line_items(client, deal_id)
    if not line_items:
        return None

    # numero_container Y name: en contenedores legacy numero_container es el
    # entero ("1") y el prefijo de las líneas per-contenedor sale del name.
    container_names = set()
    for li in line_items:
        props = li.get("properties", {}) or {}
        if _is_container_product_type(props.get("hs_product_type") or ""):
            for nc in ((props.get("numero_container") or "").strip(), (props.get("name") or "").strip()):
                if nc:
                    container_names.add(nc)

    placed_product_ids = set()
    for li in line_items:
        props = li.get("properties", {}) or {}
        if _line_item_is_article(props) and (props.get("numero_container") or "").strip():
            pid = props.get("hs_product_id")
            if pid:
                placed_product_ids.add(str(pid))

    huerfano_ids = set(_catalogo_huerfano_ids(line_items, container_names)) if container_names else set()
    huerfanos, sin_colocar = [], []
    for li in line_items:
        if str(li.get("id")) not in huerfano_ids:
            continue
        props = li.get("properties", {}) or {}
        entry = {
            "id": str(li.get("id")),
            "name": (props.get("name") or "").strip(),
            "price": props.get("price"),
        }
        if str(props.get("hs_product_id") or "") in placed_product_ids:
            huerfanos.append(entry)
        else:
            sin_colocar.append(entry)

    return {
        "deal_id": deal_id,
        "total": len(line_items),
        "containers": sorted(container_names),
        "huerfanos": huerfanos,
        "sin_colocar": sin_colocar,
    }


def _scan_all_deal_ids(client) -> List[str]:
    """Deals con candidatos: line items fabricante sin numero_container, resueltos
    a su deal por asociación (las copias de quotes publicadas no tienen deal y
    quedan fuera)."""
    search_url = "https://api.hubapi.com/crm/v3/objects/line_items/search"
    candidate_ids: List[str] = []
    after: Optional[str] = None
    while True:
        payload = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "tipo_articulo", "operator": "EQ", "value": "fabricante"},
                    {"propertyName": "numero_container", "operator": "NOT_HAS_PROPERTY"},
                ]
            }],
            "properties": ["name"],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        r = requests.post(search_url, headers=_headers(client), json=payload, timeout=30)
        if not r.ok:
            sys.exit(f"[abort] search line items: {r.status_code} {r.text}")
        body = r.json()
        candidate_ids.extend(str(it.get("id")) for it in body.get("results", []))
        after = (((body.get("paging") or {}).get("next") or {}).get("after"))
        if not after:
            break

    print(f"[scan] {len(candidate_ids)} line items fabricante sin numero_container")

    deal_ids: set = set()
    assoc_url = "https://api.hubapi.com/crm/v4/associations/line_items/deals/batch/read"
    for i in range(0, len(candidate_ids), 100):
        chunk = candidate_ids[i:i + 100]
        payload = {"inputs": [{"id": x} for x in chunk]}
        r = requests.post(assoc_url, headers=_headers(client), json=payload, timeout=30)
        if not r.ok:
            _error(f"batch read asociaciones: {r.status_code} {r.text}")
            continue
        for res in r.json().get("results", []):
            for to in res.get("to", []):
                did = to.get("toObjectId")
                if did is not None:
                    deal_ids.add(str(did))
    print(f"[scan] -> {len(deal_ids)} deals con candidatos")
    return sorted(deal_ids)


def _archivar(client, ids: List[str]) -> bool:
    archive_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
    ok = True
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        r = requests.post(archive_url, headers=_headers(client), json={"inputs": [{"id": x} for x in chunk]}, timeout=30)
        if not r.ok:
            _error(f"batch archive: {r.status_code} {r.text}")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deal", help="deal id de la cotización (p.ej. 61763928105)")
    g.add_argument("--all", action="store_true", help="escanear todos los deals con candidatos")
    ap.add_argument("--apply", action="store_true", help="archivar los huérfanos (por defecto: solo detectar)")
    args = ap.parse_args()

    client = hubspot_client.get_client()

    deal_ids = [args.deal] if args.deal else _scan_all_deal_ids(client)

    if not args.apply:
        print("** MODO DETECCIÓN (dry-run). Usa --apply para archivar. **")

    total_huerfanos = 0
    total_sin_colocar = 0
    deals_afectados = 0
    for deal_id in deal_ids:
        plan = _analizar_deal(client, deal_id)
        if plan is None:
            print(f"[skip] deal {deal_id}: sin line items")
            continue
        if not plan["containers"]:
            print(f"[skip] deal {deal_id}: sin contenedores — el catálogo es legítimo "
                  f"({plan['total']} line items)")
            continue
        if not plan["huerfanos"] and not plan["sin_colocar"]:
            continue

        deals_afectados += 1
        total_huerfanos += len(plan["huerfanos"])
        total_sin_colocar += len(plan["sin_colocar"])
        print(f"\n=== Deal {deal_id} — {len(plan['huerfanos'])} huérfano(s) de {plan['total']} line items, "
              f"contenedores: {', '.join(plan['containers'])} ===")
        for h in plan["huerfanos"]:
            print(f"  [HUERFANO] line_item={h['id']} [{h['name'][:60]}] price={h['price']}")
        for s in plan["sin_colocar"]:
            print(f"  [CONSERVADO · sin colocar] line_item={s['id']} [{s['name'][:60]}] "
                  f"— única persistencia de la selección; publish ya lo excluye del quote")

        if args.apply and plan["huerfanos"]:
            if _archivar(client, [h["id"] for h in plan["huerfanos"]]):
                print(f"  -> {len(plan['huerfanos'])} archivado(s)")

    print(f"\n[fin] {total_huerfanos} huérfano(s) archivable(s) y {total_sin_colocar} conservado(s) "
          f"en {deals_afectados} deal(s) "
          f"({'aplicado' if args.apply else 'dry-run — sin cambios'})")
    if _errores:
        print(f"[fin] {len(_errores)} error(es) HTTP — resultado potencialmente incompleto")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
