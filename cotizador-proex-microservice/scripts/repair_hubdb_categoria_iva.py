"""Sanea la columna `categoria` de la HubDB `tipo_de_iva_soportado`.

La tabla es la única fuente que distingue IVA repercutido de soportado (las props
de HubSpot no discriminan: en line items las 6 props comparten el mismo catálogo de
77 opciones y en deals son texto libre). Un volcado del catálogo SAP dejó filas con
la categoría cruzada, así que los selects "(Soportado)" del cotizador ofrecen
códigos repercutidos para clientes EUROPA/EXTRANJERO y al elegirlos SAP rechaza el
registro. NACIONAL no está afectada.

La categoría real de cada fila se deriva de su `name` (nomenclatura SAP), que es
inequívoco; el prefijo del `code` solo desambigua los pares con name idéntico
(REXN/SEXN, RA*/SA*, RIN*/SIN*). Dos acciones:

  BORRAR       la fila cruzada cuya pareja (code, residencia) ya existe en su
               categoría correcta — es un duplicado sin información propia.
  RECLASIFICAR la fila cruzada sin gemela: solo se corrige `categoria`.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    # Detección (NO modifica nada):
    python -m scripts.repair_hubdb_categoria_iva

    # Mutación real sobre el draft + push-live:
    python -m scripts.repair_hubdb_categoria_iva --apply

    # Mutar el draft y revisarlo en HubSpot antes de publicar:
    python -m scripts.repair_hubdb_categoria_iva --apply --no-publish
"""

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.hubspot_client import hubspot_client
from app.shared.hubdb_client import CATEGORIAS_VALIDAS, HUBDB_TABLE_TIPO_IVA

BASE_URL = f"https://api.hubapi.com/cms/v3/hubdb/tables/{HUBDB_TABLE_TIPO_IVA}"

_errores: List[str] = []


def _error(msg: str) -> None:
    _errores.append(msg)
    print(f"  [error] {msg}")


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}


def _val(raw) -> Optional[str]:
    if isinstance(raw, dict):
        return raw.get("name") or raw.get("label") or raw.get("value")
    return raw


def categoria_real(code: str, name: str) -> Optional[str]:
    """Categoría que le corresponde a la fila, o None si no se puede determinar."""
    n = (name or "").strip().lower()
    c = (code or "").strip().upper()

    if "no sujeto" not in n:
        if "repercutid" in n:
            return "repercutido"
        if "soportad" in n:
            return "soportado"

    if n == "no sujeto repercutido":
        return "repercutido"
    if n == "no sujeto soportado":
        return "soportado"

    # Venta (entregas/exportaciones) repercute; compra (adquisiciones/importaciones) soporta.
    if n.startswith("entregas intracomunitarias") or n.startswith("exportaciones"):
        return "repercutido"
    if n.startswith("adquisicion") or n.startswith("iva importacion"):
        return "soportado"
    if n.startswith("recargo de eqv") or n.startswith("igic repercutido"):
        return "repercutido"

    # Pares con name idéntico en ambas categorías: manda el prefijo del code.
    if n.startswith("sujeto exento") or n.startswith("doc.equivalente"):
        return "repercutido" if c.startswith(("REX", "RA")) else "soportado"
    if n.startswith("inversion sujeto pasivo"):
        return "repercutido" if c.startswith("RIN") else "soportado"

    return None


def _fetch_rows(client, draft: bool) -> List[dict]:
    url = f"{BASE_URL}/rows/draft" if draft else f"{BASE_URL}/rows"
    rows: List[dict] = []
    after = None
    while True:
        params = {"limit": 1000}
        if after:
            params["after"] = after
        r = requests.get(url, headers=_headers(client), params=params, timeout=30)
        if not r.ok:
            _error(f"GET {'draft' if draft else 'publicadas'} rows: {r.status_code} {r.text[:300]}")
            return rows
        body = r.json()
        rows.extend(body.get("results", []))
        nxt = (body.get("paging") or {}).get("next") or {}
        if not nxt.get("after"):
            break
        after = nxt["after"]
    return rows


def _opciones_categoria(client) -> Dict[str, dict]:
    r = requests.get(BASE_URL, headers=_headers(client), timeout=30)
    if not r.ok:
        _error(f"GET schema tabla: {r.status_code} {r.text[:300]}")
        return {}
    col = next((c for c in r.json()["columns"] if c["name"] == "categoria"), None)
    if not col:
        _error("la tabla no tiene columna 'categoria'")
        return {}
    return {o["name"]: {"id": o["id"], "name": o["name"], "type": "option"} for o in col["options"]}


def _plan(rows: List[dict]) -> Tuple[List[dict], List[dict], List[dict]]:
    """(a_borrar, a_reclasificar, sin_clasificar)."""
    existentes = set()
    for row in rows:
        v = row["values"]
        existentes.add((_val(v.get("code")), _val(v.get("tipo_residencia")), _val(v.get("categoria"))))

    borrar, reclasificar, sin_clasificar = [], [], []
    for row in rows:
        v = row["values"]
        code = _val(v.get("code"))
        residencia = _val(v.get("tipo_residencia"))
        categoria = _val(v.get("categoria"))
        real = categoria_real(code, _val(v.get("name")))

        item = {
            "id": row["id"], "code": code, "residencia": residencia,
            "categoria": categoria, "real": real, "name": _val(v.get("name")),
        }
        if real is None:
            sin_clasificar.append(item)
        elif real == categoria:
            continue
        elif (code, residencia, real) in existentes:
            borrar.append(item)
        else:
            reclasificar.append(item)

    return borrar, reclasificar, sin_clasificar


def _borrar(client, ids: List[str]) -> int:
    hechos = 0
    for row_id in ids:
        r = requests.delete(f"{BASE_URL}/rows/{row_id}/draft", headers=_headers(client), timeout=30)
        if r.ok or r.status_code == 404:
            hechos += 1
        else:
            _error(f"DELETE row {row_id}: {r.status_code} {r.text[:200]}")
    return hechos


def _reclasificar(client, items: List[dict], opciones: Dict[str, dict]) -> int:
    hechos = 0
    for it in items:
        opt = opciones.get(it["real"])
        if not opt:
            _error(f"row {it['id']}: la columna no tiene opción {it['real']!r}")
            continue
        r = requests.patch(
            f"{BASE_URL}/rows/{it['id']}/draft",
            headers=_headers(client),
            json={"values": {"categoria": opt}},
            timeout=30,
        )
        if r.ok:
            hechos += 1
        else:
            _error(f"PATCH row {it['id']}: {r.status_code} {r.text[:200]}")
    return hechos


def _publicar(client) -> bool:
    r = requests.post(f"{BASE_URL}/draft/push-live", headers=_headers(client), timeout=60)
    if not r.ok:
        _error(f"push-live: {r.status_code} {r.text[:300]}")
        return False
    return True


def _resumen_por_residencia(rows: List[dict]) -> None:
    conteo = Counter()
    for row in rows:
        v = row["values"]
        conteo[(_val(v.get("tipo_residencia")), _val(v.get("categoria")))] += 1
    print("\n  Estado resultante (residencia × categoría):")
    for key in sorted(conteo, key=lambda k: (str(k[0]), str(k[1]))):
        print(f"    {str(key[0]):12} {str(key[1]):12} {conteo[key]:3} códigos")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="mutar el draft (por defecto: solo detectar)")
    ap.add_argument("--no-publish", action="store_true", help="con --apply, no hacer push-live")
    args = ap.parse_args()

    client = hubspot_client.get_client()

    if not args.apply:
        print("** MODO DETECCIÓN (dry-run). Usa --apply para corregir la tabla. **")

    publicadas = _fetch_rows(client, draft=False)
    draft = _fetch_rows(client, draft=True)
    if not publicadas:
        print("No se pudo leer la tabla.")
        return 1

    print(f"\nTabla {HUBDB_TABLE_TIPO_IVA}: {len(publicadas)} filas publicadas, {len(draft)} en draft")
    if len(publicadas) != len(draft):
        print("  [aviso] draft y publicado difieren: hay cambios sin publicar en HubSpot")

    borrar, reclasificar, sin_clasificar = _plan(draft or publicadas)

    if sin_clasificar:
        print(f"\n=== {len(sin_clasificar)} fila(s) SIN CLASIFICAR — se dejan intactas, revísalas a mano ===")
        for it in sin_clasificar:
            print(f"  row {it['id']}  {it['residencia']:12} {it['code']:6} categoria={it['categoria']:12} [{it['name']}]")

    print(f"\n=== {len(borrar)} fila(s) a BORRAR (duplicado: ya existe en su categoría correcta) ===")
    for it in sorted(borrar, key=lambda x: (str(x["residencia"]), str(x["code"]))):
        print(f"  row {it['id']}  {it['residencia']:12} {it['code']:6} categoria={it['categoria']:12} "
              f"-> ya existe como {it['real']}")

    print(f"\n=== {len(reclasificar)} fila(s) a RECLASIFICAR (sin gemela: solo cambia categoria) ===")
    for it in sorted(reclasificar, key=lambda x: (str(x["residencia"]), str(x["code"]))):
        print(f"  row {it['id']}  {it['residencia']:12} {it['code']:6} "
              f"{it['categoria']} -> {it['real']}   [{it['name']}]")

    total = len(borrar) + len(reclasificar)
    if not total:
        print("\nNada que corregir.")
        return 0

    supervivientes = [
        r for r in (draft or publicadas) if r["id"] not in {it["id"] for it in borrar}
    ]
    for it in reclasificar:
        for r in supervivientes:
            if r["id"] == it["id"]:
                r["values"]["categoria"] = {"name": it["real"]}
    _resumen_por_residencia(supervivientes)

    if not args.apply:
        print(f"\n** dry-run: {total} fila(s) afectadas, tabla intacta. Ejecuta con --apply. **")
        return 0

    print(f"\nAplicando sobre el draft ({total} filas)...")
    n_borradas = _borrar(client, [it["id"] for it in borrar])
    n_reclas = _reclasificar(client, reclasificar, _opciones_categoria(client))
    print(f"  borradas: {n_borradas}/{len(borrar)} | reclasificadas: {n_reclas}/{len(reclasificar)}")

    if args.no_publish:
        print("\n  Draft modificado sin publicar. Revísalo en HubSpot y haz push-live desde allí.")
    elif _publicar(client):
        print("\n  Tabla publicada (push-live).")
        print("  IMPORTANTE: el cache de IVA del microservicio se carga una vez por proceso;")
        print("  hay que redeployar/reiniciar la Lambda para que lea la tabla corregida.")

    if _errores:
        print(f"\n{len(_errores)} error(es):")
        for e in _errores:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
