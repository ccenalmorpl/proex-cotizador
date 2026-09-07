"""Escribe la ficha de empresa remitente de PROEX en los quotes que nacieron sin ella.

El asistente de publicación de HubSpot pinta "TU EMPRESA" leyendo las propiedades
`hs_sender_company_*` del propio quote. Los quotes creados a mano por la UI reciben
los Predeterminados de la cuenta; los creados por API nacen vacíos, y el cotizador
además escribía `hs_sender_company_name = "Pymext"` (el literal hardcodeado que
mandaba la card), que es lo que veían los comerciales al publicar.

El fix en `deals.py` solo cubre los quotes nuevos: este script pone al día los ya
creados con los mismos valores de `SENDER_COMPANY_PROPERTIES`, es decir, los que
HubSpot pone en un quote nativo de este portal.

No toca el PDF: la plantilla de PROEX lleva su pie de página propio y nunca leyó
estas propiedades. El cambio se ve en el CRM y en el asistente, nada más.

Solo entran los borradores: HubSpot congela los quotes publicados y devuelve
"Published Quote cannot be edited" a cualquier PATCH, así que los ya publicados se
listan pero se dejan como están. Despublicarlos para editarlos rompería los enlaces
que el cliente pueda tener, y su PDF no muestra el dato.

Uso (desde el microservicio, con su venv y PROEX_ACCESS_TOKEN en el entorno):

    python scripts/backfill_quote_sender_company.py               # simulación
    python scripts/backfill_quote_sender_company.py --limit 5 --apply
    python scripts/backfill_quote_sender_company.py --apply
    python scripts/backfill_quote_sender_company.py --rollback <json> --apply
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.constants import SENDER_COMPANY_PROPERTIES
from app.services.hubspot_client import hubspot_client

SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/quotes/search"
BATCH_UPDATE_URL = "https://api.hubapi.com/crm/v3/objects/quotes/batch/update"
UPDATE_URL = "https://api.hubapi.com/crm/v3/objects/quotes"
PROPS = ["hs_title", "hs_status", "hs_createdate", *SENDER_COMPANY_PROPERTIES]


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}


def listar_quotes(client) -> List[dict]:
    quotes, after = [], None
    while True:
        body = {
            "limit": 100,
            "properties": PROPS,
            "sorts": [{"propertyName": "hs_createdate", "direction": "DESCENDING"}],
        }
        if after:
            body["after"] = after
        resp = requests.post(SEARCH_URL, headers=_headers(client), json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        quotes.extend(data.get("results", []))
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return quotes


def desviaciones(quote: dict) -> Dict[str, str]:
    props = quote.get("properties", {}) or {}
    return {
        clave: valor
        for clave, valor in SENDER_COMPANY_PROPERTIES.items()
        if (props.get(clave) or "") != valor
    }


def _aplicar_lote(client, lote: List[dict]) -> List[str]:
    """Actualiza un lote y, si HubSpot lo rechaza entero, aísla el quote culpable."""
    payload = {"inputs": [{"id": q["id"], "properties": q["cambios"]} for q in lote]}
    resp = requests.post(BATCH_UPDATE_URL, headers=_headers(client), json=payload, timeout=60)
    if resp.ok:
        return []

    fallos = []
    for q in lote:
        r = requests.patch(
            f"{UPDATE_URL}/{q['id']}",
            headers=_headers(client),
            json={"properties": q["cambios"]},
            timeout=30,
        )
        if not r.ok:
            fallos.append(f"{q['id']} ({q['titulo']}): HTTP {r.status_code} {r.text[:200]}")
    return fallos


def deshacer(client, ruta: Path, apply: bool) -> int:
    """Devuelve los quotes de un JSON de respaldo a los valores que tenían."""
    respaldo = json.loads(ruta.read_text(encoding="utf-8"))
    lote = [
        {
            "id": q["id"],
            "titulo": q["titulo"],
            # HubSpot no acepta None: un valor ausente se restaura como cadena vacía.
            "cambios": {clave: (valor or "") for clave, valor in q["antes"].items()},
        }
        for q in respaldo
    ]
    print(f"{len(lote)} quotes a revertir desde {ruta}")
    for q in lote[:10]:
        print(f"  {q['id']}  {q['cambios'].get('hs_sender_company_name')!r}  {q['titulo'][:50]}")
    if not apply:
        print("\n(simulación — repite con --apply para escribir)")
        return 0

    fallos = []
    for i in range(0, len(lote), 100):
        fallos.extend(_aplicar_lote(client, lote[i : i + 100]))
    for f in fallos:
        print(f"  ERROR: {f}")
    print(f"Revertidos {len(lote) - len(fallos)} quotes.")
    return 1 if fallos else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="escribe en HubSpot (sin esto, simula)")
    parser.add_argument("--limit", type=int, help="procesa solo los N primeros (prueba controlada)")
    parser.add_argument(
        "--backup",
        default=None,
        help="ruta del JSON con los valores previos (por defecto, junto al script con timestamp)",
    )
    parser.add_argument("--rollback", help="JSON de respaldo a restaurar en lugar de corregir")
    args = parser.parse_args()

    client = hubspot_client.get_client()

    if args.rollback:
        return deshacer(client, Path(args.rollback), args.apply)

    print("Valores objetivo:")
    for clave, valor in SENDER_COMPANY_PROPERTIES.items():
        print(f"  {clave} = {valor}")

    quotes = listar_quotes(client)
    print(f"\n{len(quotes)} quotes en el portal")

    pendientes = []
    for q in quotes:
        cambios = desviaciones(q)
        if not cambios:
            continue
        props = q.get("properties", {}) or {}
        pendientes.append({
            "id": q["id"],
            "titulo": props.get("hs_title") or "",
            "estado": props.get("hs_status") or "",
            "antes": {clave: props.get(clave) for clave in SENDER_COMPANY_PROPERTIES},
            "cambios": cambios,
        })

    if not pendientes:
        print("Todos los quotes tienen ya la ficha de PROEX. Nada que hacer.")
        return 0

    por_nombre = Counter(
        (p["antes"].get("hs_sender_company_name"), p["estado"]) for p in pendientes
    )
    print(f"\n{len(pendientes)} quotes con la ficha incorrecta:")
    for (nombre, estado), n in sorted(por_nombre.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  empresa={nombre!r}  estado={estado}")

    publicados = [p for p in pendientes if p["estado"] != "DRAFT"]
    pendientes = [p for p in pendientes if p["estado"] == "DRAFT"]
    if publicados:
        print(f"\n{len(publicados)} ya publicados: HubSpot no deja editarlos, se dejan como están")
    if not pendientes:
        print("Ningún borrador que corregir.")
        return 0
    print(f"{len(pendientes)} borradores a corregir")

    if args.limit:
        pendientes = pendientes[: args.limit]
        print(f"\n--limit {args.limit}: se procesarán solo {len(pendientes)}")

    print("\nPrimeros afectados:")
    for p in pendientes[:10]:
        print(f"  {p['id']}  {p['estado']:<22} {p['titulo'][:60]}")

    if not args.apply:
        print("\n(simulación — repite con --apply para escribir)")
        return 0

    destino = Path(args.backup) if args.backup else Path(__file__).with_name(
        f"backfill_quote_sender_company_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    destino.write_text(json.dumps(pendientes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nValores previos guardados en {destino}")

    fallos = []
    for i in range(0, len(pendientes), 100):
        lote = pendientes[i : i + 100]
        fallos.extend(_aplicar_lote(client, lote))
        print(f"  actualizados {min(i + len(lote), len(pendientes))}/{len(pendientes)}")

    if fallos:
        print(f"\n{len(fallos)} quotes NO se pudieron actualizar:")
        for f in fallos:
            print(f"  {f}")
    print(f"\nHecho: {len(pendientes) - len(fallos)} quotes con la ficha de PROEX.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
