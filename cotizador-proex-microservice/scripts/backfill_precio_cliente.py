"""Carga inicial de `precio_unitario_cliente` y `total_linea_cliente` en los
Elementos de los contenedores ya publicados (UDF de línea para el EDI, petición de
Gonzalo del 14/10/2026). A partir del despliegue los mantiene
`recalcular_contenedores`; esto solo rellena lo que había antes.

Calcula exactamente lo mismo que recalcular, pero escribe SOLO esas dos props: no
toca precio_total, descuento, line items ni Contenedores, y no sella
`actualizacion_pedido`, así que SAP no las lee hasta el siguiente aviso normal.

Se salta, y lo informa:
- BLOQUEADO: line items y Elementos no describen el mismo contenedor (recalcular
  tampoco lo escribiría).
- PENDIENTE: la suma de precio × cajas se separa de los precio_total que ya tiene
  SAP más de lo que explica el redondeo. Falta un recálculo, y el valor nuevo iría
  por delante de SAP.
- FACTURADO: su Negocio Pedido ya tiene número de factura real (salvo
  --incluir-facturados).

Uso (desde el microservicio, con PROEX_ACCESS_TOKEN en el entorno):

    python -m scripts.backfill_precio_cliente --deal 65027387977
    python -m scripts.backfill_precio_cliente --all --informe /tmp/precio_cliente.json
    python -m scripts.backfill_precio_cliente --all --apply

Con --apply, mejor fuera de horario: lee y escribe con segundos de diferencia y
un recalcular que caiga en medio quedaría pisado. Después, otra simulación: lo
que siga saliendo OK con Elementos a escribir es lo que no entró.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO
from app.routes.deals import _get_associated_ids
from app.services.custom_objects import (
    batch_update_custom_objects,
    get_contenedores_by_deal,
    get_elementos_by_deal,
)
from app.services.hubspot_client import hubspot_client
from app.utils.iva_helpers import (
    PROPS_PRECIO_CLIENTE,
    _batch_read_line_items_superset,
    _derivar_precio_cliente_elementos,
    _emparejar_elementos_fabricante,
    _index_elementos_by_container,
    _index_elementos_by_lid,
    _index_fabricantes_by_container,
    _index_lineas_gasto_by_container,
    _motivo_bloqueo_sap,
    _precio_cliente_contenedor,
    _to_float,
    build_deal_pricing_context,
)

# Pipeline de los Negocios Pedido (PEDIDO_PIPELINE_ID del micro de pedidos).
PEDIDO_PIPELINE_ID = "725914649"

ELEMENTO_PROPS = [
    "tipo_articulo", "cantidad", "nombre_contenedor", "line_item_id", "nombre",
    "precio_unitario", "precio_total", "descuento", *PROPS_PRECIO_CLIENTE,
]


_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=Retry(
    total=5, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=None,
)))


def _tiene_factura(numero_de_factura: Optional[str]) -> bool:
    # El workflow escribe "ERROR" cuando el middleware falla y no lo limpia: ni
    # eso ni el vacío prueban que exista la factura (misma regla que Pedidos).
    return (numero_de_factura or "").strip().lower() not in {"", "error"}


def _deal_id_desde_nombre(nombre: str) -> Optional[str]:
    """'65027387977-001' -> '65027387977'."""
    prefijo, _, sufijo = (nombre or "").strip().rpartition("-")
    return prefijo if prefijo.isdigit() and sufijo.isdigit() else None


def _todos_los_deals(client) -> List[str]:
    deal_ids: Set[str] = set()
    after = None
    while True:
        req = {"filterGroups": [], "properties": ["nombre"], "limit": 100}
        if after:
            req["after"] = after
        resp = client.crm.objects.search_api.do_search(
            object_type=OBJECT_TYPE_CONTENEDOR, public_object_search_request=req,
        )
        for r in resp.results or []:
            did = _deal_id_desde_nombre((r.properties or {}).get("nombre"))
            if did:
                deal_ids.add(did)
        paging = getattr(resp, "paging", None)
        after = getattr(getattr(paging, "next", None), "after", None) if paging else None
        if not after:
            break
    return sorted(deal_ids)


def _contenedores_facturados(client, contenedor_ids: List[str]) -> Set[str]:
    """Contenedores cuyo Negocio Pedido ya tiene un número de factura real."""
    headers = {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}
    deals_por_cid: Dict[str, List[str]] = {}
    for i in range(0, len(contenedor_ids), 100):
        r = _http.post(
            f"https://api.hubapi.com/crm/v4/associations/{OBJECT_TYPE_CONTENEDOR}/deals/batch/read",
            headers=headers, json={"inputs": [{"id": c} for c in contenedor_ids[i:i + 100]]}, timeout=30,
        )
        r.raise_for_status()
        for res in r.json().get("results", []):
            deals_por_cid[str(res["from"]["id"])] = [str(t["toObjectId"]) for t in res.get("to", [])]

    deal_ids = sorted({d for ds in deals_por_cid.values() for d in ds})
    con_factura: Set[str] = set()
    for i in range(0, len(deal_ids), 100):
        r = _http.post(
            "https://api.hubapi.com/crm/v3/objects/deals/batch/read", headers=headers, timeout=30,
            json={"inputs": [{"id": d} for d in deal_ids[i:i + 100]], "properties": ["pipeline", "numero_de_factura"]},
        )
        r.raise_for_status()
        for d in r.json().get("results", []):
            p = d.get("properties") or {}
            if p.get("pipeline") == PEDIDO_PIPELINE_ID and _tiene_factura(p.get("numero_de_factura")):
                con_factura.add(str(d["id"]))
    return {cid for cid, ds in deals_por_cid.items() if con_factura & set(ds)}


def _analizar_deal(client, deal_id: str, incluir_facturados: bool) -> List[dict]:
    line_items = _batch_read_line_items_superset(client, _get_associated_ids(deal_id, "line_items") or [])
    pricing_ctx = build_deal_pricing_context(client, deal_id, line_items_precached=line_items)
    fabricantes_by_container = _index_fabricantes_by_container(line_items)
    gastos_by_container = _index_lineas_gasto_by_container(line_items)
    elementos = get_elementos_by_deal(client, deal_id, properties=ELEMENTO_PROPS)
    elementos_by_container = _index_elementos_by_container(elementos)
    elem_by_lid = _index_elementos_by_lid(elementos)
    contenedores = get_contenedores_by_deal(client, deal_id, properties=["nombre"])
    facturados = _contenedores_facturados(client, [str(c["id"]) for c in contenedores]) if contenedores else set()

    filas = []
    for cont in contenedores:
        cid = str(cont["id"])
        nombre = ((cont.get("properties") or {}).get("nombre") or "").strip()
        fila = {"deal_id": deal_id, "contenedor_id": cid, "nombre": nombre, "updates": {}, "valores": {}}
        filas.append(fila)

        fabricantes = fabricantes_by_container.get(nombre, [])
        elems = elementos_by_container.get(nombre, [])
        cajas = sum(_to_float(li.get("quantity")) for li in fabricantes)
        pp = pricing_ctx.for_container(nombre, cajas)
        emp = _emparejar_elementos_fabricante(nombre, fabricantes, elems, elem_by_lid, deal_id)
        motivo = _motivo_bloqueo_sap(fabricantes, gastos_by_container.get(nombre, []), elems, emp.sin_resolver, {})
        if motivo:
            fila.update(estado="BLOQUEADO", motivo=motivo)
            continue

        calculo = _precio_cliente_contenedor(fabricantes, pp, elems, emp.by_lid)
        fila["valores"] = {
            eid: {"precio_unitario_cliente": str(precio), "total_linea_cliente": str(total)}
            for eid, (precio, total) in calculo.valores.items()
        }
        fila.update(
            suma_cliente=round(sum(total for _, total in calculo.valores.values()), 2),
            suma_sap=calculo.total_documento, residuo=calculo.residuo,
            tolerancia=round(calculo.tolerancia, 2), cargado_en=calculo.cargado_en,
        )
        # Un residuo que no es de redondeo quiere decir que los precio_total que
        # tiene SAP esperan un recálculo: el valor nuevo iría por delante.
        if abs(calculo.residuo) > calculo.tolerancia:
            fila["estado"] = "PENDIENTE"
        elif cid in facturados and not incluir_facturados:
            fila["estado"] = "FACTURADO"
        else:
            fila["estado"] = "OK"
            fila["updates"] = _derivar_precio_cliente_elementos(fabricantes, pp, elems, emp.by_lid)
    return filas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deal", action="append", help="deal id de la cotización (repetible)")
    g.add_argument("--all", action="store_true", help="todos los negocios con Contenedores")
    ap.add_argument("--apply", action="store_true", help="escribir las props (por defecto solo informa)")
    ap.add_argument("--incluir-facturados", action="store_true", help="rellenar también los contenedores ya facturados")
    ap.add_argument("--informe", help="ruta del informe JSON con el detalle por contenedor y Elemento")
    args = ap.parse_args()

    client = hubspot_client.get_client()
    deal_ids = args.deal or _todos_los_deals(client)
    if not args.apply:
        print("** SIMULACIÓN: no se escribe nada. Usa --apply para rellenar las props. **")

    informe: List[dict] = []
    escritos = 0
    try:
        for deal_id in deal_ids:
            try:
                filas = _analizar_deal(client, deal_id, args.incluir_facturados)
            except Exception as e:
                print(f"[error] deal {deal_id}: {e}")
                informe.append({"deal_id": deal_id, "estado": "ERROR", "motivo": str(e)})
                continue
            for fila in filas:
                informe.append(fila)
                extra = fila.get("motivo") or (
                    f"Σ cliente={fila.get('suma_cliente')} Σ SAP={fila.get('suma_sap')} "
                    f"residuo={fila.get('residuo')}"
                )
                print(f"[{fila['estado']}] {fila['nombre'] or fila['contenedor_id']}: "
                      f"{len(fila['updates'])} Elementos a escribir · {extra}")
                if args.apply and fila["updates"]:
                    fila["escritos"] = batch_update_custom_objects(
                        client, OBJECT_TYPE_ELEMENTO,
                        [{"id": eid, "properties": props} for eid, props in fila["updates"].items()],
                    )
                    escritos += fila["escritos"]
                    if fila["escritos"] < len(fila["updates"]):
                        print(f"[AVISO] {fila['nombre']}: solo {fila['escritos']} de "
                              f"{len(fila['updates'])} Elementos escritos")
    finally:
        resumen: Dict[str, int] = {}
        for fila in informe:
            resumen[fila["estado"]] = resumen.get(fila["estado"], 0) + 1
        a_escribir = sum(len(f.get("updates") or {}) for f in informe)
        print(f"\n[fin] {len(deal_ids)} deals · contenedores por estado {resumen} · "
              f"Elementos a escribir {a_escribir}"
              f"{f' · escritos {escritos}' if args.apply else ' (simulación)'}")
        if args.informe:
            Path(args.informe).write_text(json.dumps(informe, ensure_ascii=False, indent=1))
            print(f"[informe] {args.informe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
