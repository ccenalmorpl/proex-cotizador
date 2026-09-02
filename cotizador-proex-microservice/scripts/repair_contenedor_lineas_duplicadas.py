"""Repara contenedores con líneas de fabricante DUPLICADAS por doble ejecución
concurrente de `PATCH /deals/{id}/containers/{lid}` (`_edit_container_in_place`).

Las dos ejecuciones leen el mismo estado antes de que ninguna escriba, así que
ambas crean su juego completo de líneas: el contenedor acaba con 2 line items
(y 2 Elementos) por artículo. El cotizador sigue viendo lo correcto porque lee
el JSON `container_config`, pero Pedidos lee los Elementos y muestra el doble
de referencias, peso, price e importe_pendiente_cobro.

La autoridad es `container_config`: si un producto aparece N veces en el JSON,
debe tener exactamente N line items vivos. Los que sobran se archivan quedándose
con los de `hs_lastmodifieddate` más reciente (los que recogieron el último
repreciado de /negociacion). Un producto repetido en el JSON (promo 5+1) tiene
tantas líneas como le corresponde y NO se toca.

Repara además el daño colateral: al recrearse las líneas, los Elementos nuevos
se quedaron sin la asociación a su Negocio Pedido (typeId 486).

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    # Detección (NO modifica nada):
    python -m scripts.repair_contenedor_lineas_duplicadas --deal 63436230289

    # Reparar:
    python -m scripts.repair_contenedor_lineas_duplicadas --deal 63436230289 --apply

Aborta si algún Negocio Pedido del deal ya tiene `facturar_pedido`: a partir de
ahí SAP ya ha leído las líneas y la reparación deja de ser inocua.
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
from app.routes.deals import _get_associated_ids
from app.services.hubspot_client import hubspot_client
from app.utils.iva_helpers import recalcular_contenedores

OBJECT_TYPE_CONTENEDOR = "2-54989406"
PIPELINE_PEDIDOS = "725914649"
ASSOC_DEAL_TO_ELEMENTO = 486

LINE_ITEM_PROPS = [
    "name", "hs_sku", "hs_product_id", "hs_product_type", "tipo_articulo",
    "numero_container", "quantity", "price", "precio_de_coste",
    "margen_unitario", "container_config", "hs_lastmodifieddate",
]


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


def _batch_archive(client, object_type: str, ids: List[str]) -> None:
    for i in range(0, len(ids), 100):
        resp = requests.post(
            f"https://api.hubapi.com/crm/v3/objects/{object_type}/batch/archive",
            headers=_headers(client),
            json={"inputs": [{"id": str(x)} for x in ids[i:i + 100]]},
            timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(f"archive {object_type} falló: {resp.status_code} {resp.text}")


def _associated(client, from_type: str, from_id: str, to_type: str) -> List[str]:
    resp = requests.get(
        f"https://api.hubapi.com/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}",
        headers=_headers(client), params={"limit": 500}, timeout=30,
    )
    resp.raise_for_status()
    return sorted({str(r["toObjectId"]) for r in resp.json().get("results", [])})


def _mtime(props: dict) -> str:
    return props.get("hs_lastmodifieddate") or ""


def analizar(client, deal_id: str) -> dict:
    """Clasifica las líneas de fabricante de cada contenedor del deal en las que
    respalda el container_config y las sobrantes."""
    line_item_ids = _get_associated_ids(deal_id, "line_items") or []
    line_items = _batch_read(client, "line_items", line_item_ids, LINE_ITEM_PROPS)

    configs: Dict[str, dict] = {}
    fabricantes: Dict[str, List[dict]] = defaultdict(list)
    for li in line_items:
        props = li.get("properties", {}) or {}
        numero = (props.get("numero_container") or "").strip()
        if not numero:
            continue
        if props.get("container_config"):
            try:
                configs[numero] = {
                    "line_item_id": li["id"],
                    "articulos": json.loads(props["container_config"]).get("articulos", []),
                }
            except json.JSONDecodeError:
                print(f"  [aviso] container_config ilegible en {numero} (line item {li['id']})")
        if (props.get("tipo_articulo") or "").strip().lower() == "fabricante":
            fabricantes[numero].append(li)

    contenedores_co = {}
    for co_id in _associated(client, "deals", deal_id, OBJECT_TYPE_CONTENEDOR):
        contenedores_co[co_id] = None
    if contenedores_co:
        for co in _batch_read(client, OBJECT_TYPE_CONTENEDOR, list(contenedores_co), ["numero_container"]):
            numero = (co.get("properties", {}) or {}).get("numero_container")
            if numero:
                contenedores_co[co["id"]] = f"{deal_id}-{int(numero):03d}"

    plan = {"deal_id": deal_id, "contenedores": [], "line_items_snapshot": line_items}
    for numero, lineas in sorted(fabricantes.items()):
        config = configs.get(numero)
        if not config:
            print(f"  [aviso] {numero}: sin container_config, se omite")
            continue

        esperado = defaultdict(int)
        for art in config["articulos"]:
            esperado[str(art.get("articuloId"))] += 1

        conservar, archivar = [], []
        for product_id, grupo in sorted(_group_by_product(lineas).items()):
            n = esperado.get(str(product_id), 0)
            if n == 0:
                print(f"  [aviso] {numero}: producto {product_id} con {len(grupo)} línea(s) "
                      f"y 0 entradas en container_config; NO se toca")
                conservar += grupo
                continue
            ordenadas = sorted(grupo, key=lambda li: _mtime(li.get("properties", {})), reverse=True)
            conservar += ordenadas[:n]
            archivar += ordenadas[n:]

        co_id = next((c for c, nom in contenedores_co.items() if nom == numero), None)
        plan["contenedores"].append({
            "numero": numero,
            "co_id": co_id,
            "container_line_item_id": config["line_item_id"],
            "esperado": sum(esperado.values()),
            "vivas": len(lineas),
            "conservar": [li["id"] for li in conservar],
            "archivar": [li["id"] for li in archivar],
            "cajas_conservadas": sum(float(li["properties"].get("quantity") or 0) for li in conservar),
            "cajas_config": sum(float(a.get("numCajas") or 0) for a in config["articulos"]),
            "detalle_archivar": [
                {
                    "id": li["id"],
                    "producto": li["properties"].get("hs_product_id"),
                    "cajas": li["properties"].get("quantity"),
                    "price": li["properties"].get("price"),
                    "modificado": _mtime(li["properties"]),
                    "nombre": (li["properties"].get("name") or "").split(" - ", 1)[-1],
                }
                for li in archivar
            ],
        })
    return plan


def _group_by_product(lineas: List[dict]) -> Dict[str, List[dict]]:
    grupos = defaultdict(list)
    for li in lineas:
        grupos[str((li.get("properties", {}) or {}).get("hs_product_id"))].append(li)
    return grupos


def elementos_a_archivar(client, deal_id: str, line_item_ids_archivados: List[str]) -> List[str]:
    elementos = _associated(client, "deals", deal_id, OBJECT_TYPE_ELEMENTO)
    if not elementos:
        return []
    objetivo = {str(x) for x in line_item_ids_archivados}
    leidos = _batch_read(client, OBJECT_TYPE_ELEMENTO, elementos, ["line_item_id", "nombre", "tipo_articulo"])
    return [e["id"] for e in leidos if str((e["properties"] or {}).get("line_item_id") or "") in objetivo]


def pedidos_del_deal(client, deal_id: str) -> Dict[str, dict]:
    """Negocios Pedido del deal indexados por el contenedor al que pertenecen.

    El contenedor sale de los Elementos que el pedido ya tenga asociados (flete y
    seguro sobreviven a la recreación de las líneas de fabricante); si no tiene
    ninguno, del sufijo del dealname."""
    asociados = _associated(client, "deals", deal_id, "deals")
    if not asociados:
        return {}
    deals = _batch_read(client, "deals", asociados, ["dealname", "pipeline", "dealstage", "facturar_pedido"])
    pedidos = {}
    for d in deals:
        props = d.get("properties", {}) or {}
        if props.get("pipeline") != PIPELINE_PEDIDOS:
            continue
        elementos = _associated(client, "deals", d["id"], OBJECT_TYPE_ELEMENTO)
        numero = None
        if elementos:
            leidos = _batch_read(client, OBJECT_TYPE_ELEMENTO, elementos, ["nombre_contenedor"])
            nombres = {(e["properties"] or {}).get("nombre_contenedor") for e in leidos}
            nombres.discard(None)
            if len(nombres) == 1:
                numero = nombres.pop()
        if not numero:
            nombre = props.get("dealname") or ""
            if "(" in nombre and ")" in nombre:
                numero = nombre[nombre.rfind("(") + 1:nombre.rfind(")")].strip()
        if numero:
            pedidos[numero] = {
                "deal_id": d["id"],
                "dealname": props.get("dealname"),
                "facturar_pedido": props.get("facturar_pedido"),
                "elementos_asociados": elementos,
            }
    return pedidos


def asociaciones_pendientes(client, deal_id: str, pedidos: dict, co_por_numero: dict) -> List[dict]:
    """Elementos que cuelgan de un contenedor pero no de su Negocio Pedido."""
    pendientes = []
    for numero, pedido in sorted(pedidos.items()):
        co_id = co_por_numero.get(numero)
        if not co_id:
            continue
        del_contenedor = set(_associated(client, OBJECT_TYPE_CONTENEDOR, co_id, OBJECT_TYPE_ELEMENTO))
        faltan = sorted(del_contenedor - set(pedido["elementos_asociados"]))
        if faltan:
            pendientes.append({"numero": numero, "pedido_deal_id": pedido["deal_id"], "elementos": faltan})
    return pendientes


def _asociar(client, deal_id: str, elemento_ids: List[str]) -> None:
    inputs = [{
        "from": {"id": str(deal_id)},
        "to": {"id": str(eid)},
        "types": [{"associationCategory": "USER_DEFINED", "associationTypeId": ASSOC_DEAL_TO_ELEMENTO}],
    } for eid in elemento_ids]
    for i in range(0, len(inputs), 100):
        resp = requests.post(
            f"https://api.hubapi.com/crm/v4/associations/deals/{OBJECT_TYPE_ELEMENTO}/batch/create",
            headers=_headers(client), json={"inputs": inputs[i:i + 100]}, timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(f"asociación deal→elemento falló: {resp.status_code} {resp.text}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deal", required=True, help="deal id de la cotización")
    ap.add_argument("--apply", action="store_true", help="ejecutar la reparación (por defecto: solo detectar)")
    ap.add_argument("--force-facturado", action="store_true",
                    help="continuar aunque algún Negocio Pedido ya tenga facturar_pedido")
    ap.add_argument("--backup-dir", default=".", help="directorio donde dejar el snapshot previo")
    args = ap.parse_args()

    client = hubspot_client.get_client()
    deal_id = str(args.deal)

    print(f"Deal {deal_id}\n")
    plan = analizar(client, deal_id)

    pedidos = pedidos_del_deal(client, deal_id)
    facturados = {n: p for n, p in pedidos.items() if (p.get("facturar_pedido") or "").strip()}
    if facturados and not args.force_facturado:
        print("ABORTA: hay Negocios Pedido ya marcados para facturar; SAP puede haber leído las líneas.")
        for numero, p in sorted(facturados.items()):
            print(f"  {numero}: {p['dealname']} facturar_pedido={p['facturar_pedido']}")
        print("Repasa el caso a mano o repite con --force-facturado si sabes lo que haces.")
        return 2

    total_archivar = []
    print("CONTENEDORES")
    for c in plan["contenedores"]:
        estado = "OK" if not c["archivar"] else f"SOBRAN {len(c['archivar'])}"
        print(f"  {c['numero']:24} config={c['esperado']:3} vivas={c['vivas']:3}  {estado}")
        if c["cajas_conservadas"] != c["cajas_config"]:
            print(f"      [aviso] cajas tras limpiar={c['cajas_conservadas']:.0f} "
                  f"≠ container_config={c['cajas_config']:.0f}")
        for d in c["detalle_archivar"]:
            print(f"      archivar {d['id']}  cajas={d['cajas']!s:6} price={d['price']!s:8} "
                  f"mod={d['modificado']}  {d['nombre'][:45]}")
        total_archivar += c["archivar"]

    if not total_archivar:
        print("\nNo hay líneas duplicadas que archivar.")

    elementos = elementos_a_archivar(client, deal_id, total_archivar) if total_archivar else []
    print(f"\nELEMENTOS a archivar: {len(elementos)}")

    co_por_numero = {c["numero"]: c["co_id"] for c in plan["contenedores"] if c["co_id"]}
    pendientes = asociaciones_pendientes(client, deal_id, pedidos, co_por_numero)
    a_reasociar = {p["numero"]: [e for e in p["elementos"] if e not in elementos] for p in pendientes}
    a_reasociar = {k: v for k, v in a_reasociar.items() if v}
    print("\nASOCIACIONES Elemento → Negocio Pedido que faltan")
    if not a_reasociar:
        print("  ninguna")
    for numero, eids in sorted(a_reasociar.items()):
        print(f"  {numero}: {len(eids)} elementos → pedido {pedidos[numero]['deal_id']}")

    if not args.apply:
        print("\n[dry-run] No se ha modificado nada. Repite con --apply para ejecutar.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(args.backup_dir) / f"backup_{deal_id}_{stamp}.json"
    backup.write_text(json.dumps({
        "deal_id": deal_id,
        "plan": {k: v for k, v in plan.items() if k != "line_items_snapshot"},
        "line_items": plan["line_items_snapshot"],
        "elementos_archivados": elementos,
        "reasociaciones": a_reasociar,
    }, indent=1, ensure_ascii=False))
    print(f"\nSnapshot previo en {backup}")

    if total_archivar:
        _batch_archive(client, "line_items", total_archivar)
        print(f"  archivados {len(total_archivar)} line items")
    if elementos:
        _batch_archive(client, OBJECT_TYPE_ELEMENTO, elementos)
        print(f"  archivados {len(elementos)} elementos")

    co_ids = [c["co_id"] for c in plan["contenedores"] if c["co_id"] and c["archivar"]]
    if co_ids:
        resultados = recalcular_contenedores(client, deal_id, co_ids) or {}
        for co_id, res in resultados.items():
            print(f"  recalculado contenedor {co_id}: price={(res or {}).get('price')}")

    for numero, eids in sorted(a_reasociar.items()):
        _asociar(client, pedidos[numero]["deal_id"], eids)
        print(f"  asociados {len(eids)} elementos a {pedidos[numero]['dealname']}")

    print("\nHecho. Verifica el contenedor en el cotizador y en la card de Pedidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
