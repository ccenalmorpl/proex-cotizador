"""Detecta y repara Elementos (objeto personalizado 2-54989407) cuyo `line_item_id`
quedó CRUZADO o stale respecto a su contenido (rotación por orden de batch de una
versión anterior; ver el guard añadido en negociación/edición/publicación).

La identidad autoritativa del Elemento es su `nombre` (= `name` del line item,
"{deal}-{NNN} - {producto}") junto con `nombre_contenedor`. El `line_item_id`
correcto es el del line item cuyo (numero_container, name) coincide con
(nombre_contenedor, nombre) del Elemento.

Reconciliación idempotente y SEGURA: solo corrige cuando el par
(nombre_contenedor, nombre) es ÚNICO en ambos lados (line items y Elementos). Con
líneas del mismo artículo repetidas en un contenedor (promo 5+1) el nombre es
ambiguo y el `line_item_id` es el único discriminador, así que NO se toca: se
reporta como AMBIGUO y se deja como está.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    # Detección (NO modifica nada):
    python -m scripts.repair_elemento_lid_cruzado --deal 60316963081
    python -m scripts.repair_elemento_lid_cruzado --all

    # Reparar (mutación real):
    python -m scripts.repair_elemento_lid_cruzado --deal 60316963081 --apply
    python -m scripts.repair_elemento_lid_cruzado --all --apply
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.custom_objects_constants import OBJECT_TYPE_ELEMENTO
from app.services.custom_objects import get_elementos_by_deal, update_elemento
from app.services.hubspot_client import hubspot_client


def _deal_id_from_nombre_contenedor(nc: str) -> Optional[str]:
    """'60316963081-002' -> '60316963081'. None si no encaja el patrón."""
    nc = (nc or "").strip()
    if "-" not in nc:
        return None
    prefijo, sufijo = nc.rsplit("-", 1)
    return prefijo if prefijo and sufijo.isdigit() else None


def _fetch_line_items(client, deal_id: str) -> List[Dict[str, str]]:
    """Line items del deal con (id, name, numero_container)."""
    access_token = client.access_token
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    assoc_url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/line_items"
    ids: List[str] = []
    after: Optional[str] = None
    while True:
        url = assoc_url + (f"?after={after}" if after else "")
        r = requests.get(url, headers=headers, timeout=30)
        if not r.ok:
            print(f"  [error] leer line items de deal {deal_id}: {r.status_code} {r.text}")
            break
        body = r.json()
        for item in body.get("results", []):
            val = item.get("toObjectId") or item.get("id")
            if val is not None:
                ids.append(str(val))
        after = (((body.get("paging") or {}).get("next") or {}).get("after"))
        if not after:
            break

    line_items: List[Dict[str, str]] = []
    read_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        payload = {"properties": ["name", "numero_container"], "inputs": [{"id": x} for x in chunk]}
        r = requests.post(read_url, headers=headers, json=payload, timeout=30)
        if not r.ok:
            print(f"  [error] batch read line items deal {deal_id}: {r.status_code} {r.text}")
            continue
        for it in r.json().get("results", []):
            props = it.get("properties", {}) or {}
            line_items.append({
                "id": str(it.get("id")),
                "name": (props.get("name") or "").strip(),
                "numero_container": (props.get("numero_container") or "").strip(),
            })
    return line_items


def _list_all_deal_ids(client) -> List[str]:
    """deal ids distintos derivados del nombre_contenedor de todos los Elementos."""
    deal_ids: set = set()
    after: Optional[str] = None
    total = 0
    while True:
        req = {
            "filterGroups": [],
            "properties": ["nombre_contenedor"],
            "limit": 100,
        }
        if after:
            req["after"] = after
        resp = client.crm.objects.search_api.do_search(
            object_type=OBJECT_TYPE_ELEMENTO,
            public_object_search_request=req,
        )
        for r in (resp.results or []):
            total += 1
            did = _deal_id_from_nombre_contenedor((r.properties or {}).get("nombre_contenedor") or "")
            if did:
                deal_ids.add(did)
        paging = getattr(resp, "paging", None)
        after = getattr(getattr(paging, "next", None), "after", None) if paging else None
        if not after:
            break
    print(f"[scan] {total} Elementos -> {len(deal_ids)} deals con Elementos")
    return sorted(deal_ids)


def _analizar_deal(client, deal_id: str) -> Optional[dict]:
    elementos = get_elementos_by_deal(
        client, deal_id,
        properties=["nombre", "nombre_contenedor", "line_item_id", "tipo_articulo"],
    )
    if not elementos:
        return None

    line_items = _fetch_line_items(client, deal_id)
    li_by_key: Dict[tuple, List[str]] = defaultdict(list)
    live_ids = set()
    for li in line_items:
        li_by_key[(li["numero_container"], li["name"])].append(li["id"])
        live_ids.add(li["id"])

    elem_key_counts: Dict[tuple, int] = defaultdict(int)
    for el in elementos:
        props = el.get("properties", {}) or {}
        elem_key_counts[((props.get("nombre_contenedor") or "").strip(), (props.get("nombre") or "").strip())] += 1

    fixes, ambiguos, no_encontrados, ok = [], [], [], 0
    for el in elementos:
        props = el.get("properties", {}) or {}
        nc = (props.get("nombre_contenedor") or "").strip()
        nombre = (props.get("nombre") or "").strip()
        cur_lid = str(props.get("line_item_id") or "").strip()
        key = (nc, nombre)
        cand = li_by_key.get(key, [])

        if len(cand) == 1 and elem_key_counts[key] == 1:
            correct = cand[0]
            if cur_lid != correct:
                # CRUCE: el lid actual apunta a OTRO line item vivo del deal (swap real,
                # tu caso). STALE: apunta a un line item viejo/borrado (re-publicación).
                clase = "CRUCE" if cur_lid and cur_lid in live_ids else "STALE"
                fixes.append({"id": el.get("id"), "nombre": nombre, "clase": clase,
                              "actual": cur_lid or "(vacío)", "correcto": correct})
            else:
                ok += 1
        elif not cand:
            no_encontrados.append({"id": el.get("id"), "nombre": nombre, "actual": cur_lid or "(vacío)"})
        else:
            ambiguos.append({"id": el.get("id"), "nombre": nombre, "actual": cur_lid or "(vacío)",
                             "candidatos": cand, "n_elem": elem_key_counts[key]})

    return {"deal_id": deal_id, "fixes": fixes, "ambiguos": ambiguos,
            "no_encontrados": no_encontrados, "ok": ok, "total": len(elementos)}


def _reparar_deal(client, plan: dict, apply: bool, only_cross: bool) -> tuple:
    deal_id = plan["deal_id"]
    fixes = plan["fixes"]
    n_cruce = sum(1 for f in fixes if f["clase"] == "CRUCE")
    n_stale = len(fixes) - n_cruce
    print(f"\n=== Deal {deal_id} — {plan['total']} Elementos "
          f"(ok={plan['ok']}, fix={len(fixes)} [cruce={n_cruce}, stale={n_stale}], "
          f"ambiguo={len(plan['ambiguos'])}, sin_line_item={len(plan['no_encontrados'])}) ===")

    for f in fixes:
        nom = f["nombre"].split(" - ", 1)[-1][:40]
        print(f"  [FIX·{f['clase']}] elemento={f['id']} [{nom}] line_item_id {f['actual']} -> {f['correcto']}")
    for a in plan["ambiguos"]:
        nom = a["nombre"].split(" - ", 1)[-1][:40]
        print(f"  [AMBIGUO · no se toca] elemento={a['id']} [{nom}] "
              f"actual={a['actual']} candidatos={a['candidatos']} n_elem={a['n_elem']}")
    for n in plan["no_encontrados"]:
        nom = n["nombre"].split(" - ", 1)[-1][:40]
        print(f"  [SIN LINE ITEM · no se toca] elemento={n['id']} [{nom}] actual={n['actual']}")

    a_aplicar = [f for f in fixes if (not only_cross or f["clase"] == "CRUCE")]
    if not apply or not a_aplicar:
        return n_cruce, n_stale

    aplicados = 0
    for f in a_aplicar:
        try:
            if update_elemento(client, f["id"], {"line_item_id": f["correcto"]}):
                aplicados += 1
            else:
                print(f"      [error FIX] elemento={f['id']}: update_elemento devolvió False")
        except Exception as e:
            print(f"      [error FIX] elemento={f['id']}: {e}")
    print(f"  -> {aplicados}/{len(a_aplicar)} Elementos reparados"
          f"{' (solo cruces)' if only_cross else ''}")
    return n_cruce, n_stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deal", help="deal id de la cotización (p.ej. 60316963081)")
    g.add_argument("--all", action="store_true", help="escanear todos los deals con Elementos")
    ap.add_argument("--apply", action="store_true", help="ejecutar la reparación (por defecto: solo detectar)")
    ap.add_argument("--only-cross", action="store_true",
                    help="aplicar SOLO los CRUCE (lid apunta a otro line item vivo); los STALE se reportan pero no se tocan")
    args = ap.parse_args()

    client = hubspot_client.get_client()

    if args.deal:
        deal_ids = [args.deal]
    else:
        deal_ids = _list_all_deal_ids(client)

    if not args.apply:
        print("** MODO DETECCIÓN (dry-run). Usa --apply para reparar. **")
    if args.only_cross:
        print("** Solo se aplicarán los CRUCE; los STALE se reportan pero NO se tocan. **")

    total_cruce = 0
    total_stale = 0
    deals_con_fix = 0
    for deal_id in deal_ids:
        plan = _analizar_deal(client, deal_id)
        if plan is None:
            print(f"[skip] deal {deal_id}: sin Elementos")
            continue
        nc, ns = _reparar_deal(client, plan, apply=args.apply, only_cross=args.only_cross)
        total_cruce += nc
        total_stale += ns
        if nc or ns:
            deals_con_fix += 1

    print(f"\n[fin] CRUCE={total_cruce}, STALE={total_stale} en {deals_con_fix} deal(s) "
          f"({'aplicado' if args.apply else 'dry-run — sin cambios'}"
          f"{', solo cruces' if args.only_cross else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
