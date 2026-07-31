#!/usr/bin/env python3
"""E2E: confirma que line_item_id es estable entre republishes y que dos líneas
del mismo artículo en un contenedor conviven como dos Elementos distintos (promo 5+1).

Corre el MISMO código que el job de publicación (process_deal_custom_objects_from_line_items),
así que verifica el comportamiento real, no una simulación.

Modos:
  verify    <deal_id>                  Solo lectura. Lista line items y Elementos y comprueba invariantes.
  stability <deal_id> [--publishes N]  Snapshot -> publish xN -> snapshot. Asserta line_item_id estable.
                                       Muta custom objects (idempotente); NO toca line items.

Ejecutar desde la raíz del microservicio con el venv:
  .venv/bin/python scripts/e2e_line_item_id_stable.py verify 46298478914
  .venv/bin/python scripts/e2e_line_item_id_stable.py stability 46298478914 --publishes 2
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from app.services.hubspot_client import hubspot_client
from app.services.custom_objects import (
    process_deal_custom_objects_from_line_items,
    get_elementos_by_deal,
    _fetch_all_line_items_for_deal,
)

ELEM_PROPS = [
    "nombre", "nombre_contenedor", "tipo_articulo",
    "cantidad", "precio_total", "line_item_id",
]


def _is_article(props):
    pt = (props.get("hs_product_type") or "").strip().lower()
    ta = (props.get("tipo_articulo") or "").strip().lower()
    return pt in {"inventory", "non_inventory", "articulo"} and ta in {"", "fabricante"}


def _read_state(client, deal_id):
    line_items = _fetch_all_line_items_for_deal(client, deal_id)
    li_by_id = {}
    for li in line_items:
        props = li.get("properties", {}) or {}
        li_by_id[str(li["id"])] = {
            "name": props.get("name", ""),
            "numero_container": (props.get("numero_container") or "").strip(),
            "is_article": _is_article(props),
        }
    elementos = get_elementos_by_deal(client, deal_id, properties=ELEM_PROPS)
    elems = []
    for el in elementos:
        props = el.get("properties", {}) or {}
        elems.append({
            "id": str(el.get("id")),
            "lid": (props.get("line_item_id") or "").strip(),
            "nombre": (props.get("nombre") or "").strip(),
            "nc": (props.get("nombre_contenedor") or "").strip(),
            "tipo": (props.get("tipo_articulo") or "").strip().lower(),
            "cantidad": props.get("cantidad"),
            "precio_total": props.get("precio_total"),
        })
    return li_by_id, elems


def _check_invariants(li_by_id, elems):
    problems = []
    fab = [e for e in elems if e["tipo"] == "fabricante"]

    sin_lid = [e for e in fab if not e["lid"]]
    if sin_lid:
        problems.append(f"{len(sin_lid)} Elemento(s) fabricante SIN line_item_id (el match caería al fallback por nombre): {[e['id'] for e in sin_lid]}")

    seen = {}
    for e in fab:
        if e["lid"]:
            seen.setdefault(e["lid"], []).append(e["id"])
    dup_lid = {lid: ids for lid, ids in seen.items() if len(ids) > 1}
    if dup_lid:
        problems.append(f"line_item_id compartido por >1 Elemento (NO debería): {dup_lid}")

    huerfanos = [e for e in fab if e["lid"] and e["lid"] not in li_by_id]
    if huerfanos:
        problems.append(f"Elemento(s) con line_item_id que ya no existe como line item: {[(e['id'], e['lid']) for e in huerfanos]}")

    grupos = {}
    for e in fab:
        grupos.setdefault((e["nc"], e["nombre"]), []).append(e)
    colisiones_nombre = {k: v for k, v in grupos.items() if len(v) > 1}

    return problems, colisiones_nombre


def cmd_verify(client, deal_id):
    li_by_id, elems = _read_state(client, deal_id)
    fab = [e for e in elems if e["tipo"] == "fabricante"]
    print(f"\n[verify] deal {deal_id}")
    print(f"  line items totales: {len(li_by_id)} (artículos: {sum(1 for v in li_by_id.values() if v['is_article'])})")
    print(f"  Elementos totales: {len(elems)} (fabricante: {len(fab)})")

    problems, colisiones = _check_invariants(li_by_id, elems)

    if colisiones:
        print("\n  Líneas con el MISMO nombre en el mismo contenedor (el escenario promo 5+1):")
        for (nc, nombre), grp in colisiones.items():
            lids = [e["lid"] for e in grp]
            distinct = len(set(lids)) == len([l for l in lids if l])
            print(f"    '{nc}' / '{nombre}': {len(grp)} líneas, line_item_ids={lids}, distintos={distinct}, precios_total={[e['precio_total'] for e in grp]}")
            if not distinct:
                problems.append(f"Colisión REAL: '{nc}'/'{nombre}' tiene Elementos con line_item_id no distinto")
    else:
        print("  (este deal no tiene aún dos líneas del mismo artículo en un contenedor)")

    print()
    if problems:
        print("  ❌ INVARIANTES ROTOS:")
        for p in problems:
            print(f"     - {p}")
        return 1
    print("  ✅ Invariantes OK: cada Elemento fabricante tiene line_item_id único y existente.")
    return 0


def cmd_stability(client, deal_id, publishes):
    li_before, elems_before = _read_state(client, deal_id)
    map_before = {e["id"]: e["lid"] for e in elems_before if e["tipo"] == "fabricante"}
    ids_before = set(li_before.keys())
    print(f"\n[stability] deal {deal_id} — snapshot inicial: {len(ids_before)} line items, {len(map_before)} Elementos fabricante")

    for i in range(publishes):
        stats = process_deal_custom_objects_from_line_items(deal_id)
        print(f"  publish #{i + 1}: contenedores(c/u/d)={stats['contenedores_created']}/{stats['contenedores_updated']}/{stats['contenedores_deleted']} "
              f"elementos(c/u/d)={stats['elementos_created']}/{stats['elementos_updated']}/{stats['elementos_deleted']} errors={stats['errors']}")

    li_after, elems_after = _read_state(client, deal_id)
    map_after = {e["id"]: e["lid"] for e in elems_after if e["tipo"] == "fabricante"}
    ids_after = set(li_after.keys())

    problems = []
    if ids_before != ids_after:
        problems.append(f"Los line item IDs cambiaron tras republicar: +{ids_after - ids_before} -{ids_before - ids_after}")
    cambiados = {eid: (map_before[eid], map_after.get(eid)) for eid in map_before if map_after.get(eid) != map_before[eid]}
    if cambiados:
        problems.append(f"line_item_id de algún Elemento cambió: {cambiados}")

    print()
    if problems:
        print("  ❌ INESTABLE:")
        for p in problems:
            print(f"     - {p}")
        return 1
    print(f"  ✅ ESTABLE: {len(ids_after)} line item IDs idénticos tras {publishes} republish(es); mapping Elemento→line_item_id sin cambios.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_v = sub.add_parser("verify")
    p_v.add_argument("deal_id")
    p_s = sub.add_parser("stability")
    p_s.add_argument("deal_id")
    p_s.add_argument("--publishes", type=int, default=2)
    args = parser.parse_args()

    client = hubspot_client.get_client()
    if args.cmd == "verify":
        return cmd_verify(client, args.deal_id)
    if args.cmd == "stability":
        return cmd_stability(client, args.deal_id, args.publishes)
    return 2


if __name__ == "__main__":
    sys.exit(main())
