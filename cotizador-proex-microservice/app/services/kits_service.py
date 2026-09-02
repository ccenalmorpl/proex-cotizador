"""Lectura de pedidos KIT desde HubSpot.

Fuente unica de verdad para "que KIT hay disponibles": la usan tanto el endpoint
que consume la card como `scripts/kits_disponibles.py`. Si divergieran, el
comercial veria en la card algo distinto de lo que audita el script.

Solo LEE. Las escrituras del ciclo de vida viven en `scripts/kits_ciclo.py`
mientras no haya decision de llevarlas al backend.

Aislado a proposito: no importa nada de los servicios existentes (ni colas, ni
DynamoDB, ni el cliente de HubSpot compartido), asi que se puede levantar solo.
"""

import json
import logging
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional

from app.core.kits_constants import (
    KIT_COMPANY_ID,
    KIT_DEALSTAGE_ABIERTO,
    KIT_DEALSTAGE_LABELS,
    KIT_PIPELINE_ID,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_CONSUMIDO_POR_IDS,
    PROP_KIT_COTIZACION_GENERADORA,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_NOMBRE,
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_VALIDACION_PRODUCCION,
    PROP_PRODUCTO_BLOQUE_MULTIPLO,
    PROP_PRODUCTO_TAMANO_KIT,
)
from app.utils.kits import (
    aplicar_marca_kit,
    construir_payload_kit,
    params_kit_de_producto,
    calcular_cajas_disponibles,
    calcular_dealstage_kit,
    parsear_marcas_kit,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

PROPIEDADES = [
    "dealname",
    "dealstage",
    "amount",
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_NOMBRE,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_VALIDACION_PRODUCCION,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_CONSUMIDO_POR_IDS,
]


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def _patch(deal_id: str, propiedades: dict, token: str) -> dict:
    """Unico punto de escritura del servicio."""
    req = urllib.request.Request(
        f"{BASE_URL}/crm/v3/objects/deals/{deal_id}",
        data=json.dumps({"properties": propiedades}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            logger.info(f"[kits] PATCH deal={deal_id} props={list(propiedades)}")
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code} al escribir deal={deal_id}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def _post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code}: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def buscar_kits(token: str) -> List[dict]:
    """Todos los negocios del pipeline KIT, paginando."""
    kits: List[dict] = []
    after: Optional[str] = None

    while True:
        body = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "pipeline", "operator": "EQ", "value": KIT_PIPELINE_ID}
                    ]
                }
            ],
            "properties": PROPIEDADES,
            "limit": 100,
        }
        if after:
            body["after"] = after

        res = _post(f"{BASE_URL}/crm/v3/objects/deals/search", body, token)
        kits.extend(res.get("results", []))

        after = (res.get("paging") or {}).get("next", {}).get("after")
        if not after:
            logger.info(f"[kits] {len(kits)} KIT leidos del pipeline {KIT_PIPELINE_ID}")
            return kits


def analizar(kits: List[dict], skus: Optional[List[str]] = None) -> dict:
    """Agrupa por SKU, decide consumibles y reune los descuadres.

    Funcion pura: no llama a HubSpot. Recibe lo que devolvio `buscar_kits`.
    """
    filtro = {s.strip() for s in (skus or []) if s and s.strip()}
    por_sku = defaultdict(list)
    avisos: List[str] = []

    for d in kits:
        p = d.get("properties") or {}
        sku = (p.get(PROP_KIT_SKU) or "").strip()
        numero = p.get(PROP_KIT_NUMERO) or ""
        etapa = p.get("dealstage") or ""
        validado = (p.get(PROP_KIT_VALIDACION_PRODUCCION) or "").lower() == "true"

        disponibles = calcular_cajas_disponibles(
            p.get(PROP_KIT_CAJAS), p.get(PROP_KIT_CONSUMIDO_POR_IDS)
        )
        etapa_derivada = calcular_dealstage_kit(
            validado_produccion=validado,
            cajas_disponibles=disponibles,
            dealstage_actual=etapa,
        )

        if not sku:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): {PROP_KIT_SKU} vacio -> "
                f"la card no lo vera, empareja por SKU"
            )
        if not numero:
            avisos.append(f"deal {d['id']}: {PROP_KIT_NUMERO} vacio")
        if etapa_derivada != etapa:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): etapa descuadra -> esta en "
                f"'{KIT_DEALSTAGE_LABELS.get(etapa, etapa)}' y le toca "
                f"'{KIT_DEALSTAGE_LABELS.get(etapa_derivada, etapa_derivada)}'"
            )

        cacheadas = p.get(PROP_KIT_CAJAS_DISPONIBLES)
        if cacheadas not in (None, "") and int(float(cacheadas)) != disponibles:
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): {PROP_KIT_CAJAS_DISPONIBLES}="
                f"{cacheadas} pero el calculo da {disponibles}"
            )
        if p.get("amount"):
            avisos.append(
                f"deal {d['id']} ({numero or 'sin numero'}): tiene amount={p['amount']} -> "
                f"un KIT no factura, entraria en el forecast"
            )

        if filtro and sku not in filtro:
            continue

        por_sku[sku].append(
            {
                "deal_id": d["id"],
                "numero": numero,
                "nombre": p.get(PROP_KIT_NOMBRE) or "",
                "cajas_kit": int(float(p.get(PROP_KIT_CAJAS) or 0)),
                "disponibles": disponibles,
                "etapa": etapa,
                "etapa_label": KIT_DEALSTAGE_LABELS.get(etapa, etapa),
                "etapa_derivada": etapa_derivada,
                "etapa_derivada_label": KIT_DEALSTAGE_LABELS.get(etapa_derivada, etapa_derivada),
                "validado": validado,
                "marcas": parsear_marcas_kit(p.get(PROP_KIT_MARCADO_POR_IDS)),
                "consumos": parsear_marcas_kit(p.get(PROP_KIT_CONSUMIDO_POR_IDS)),
                # Consumible es exactamente "la etapa derivada es Abierto": ya
                # incorpora validado, disponibles > 0 y que Cancelado es pegajoso.
                # Se mide contra la DERIVADA y no la almacenada para que una etapa
                # obsoleta no oculte un KIT que por propiedades si sirve.
                "consumible": etapa_derivada == KIT_DEALSTAGE_ABIERTO,
            }
        )

    return {"por_sku": dict(por_sku), "avisos": avisos}


def guardar_marcas_de_cotizacion(
    token: str, cotizacion_id: str, marcas: Dict[str, int]
) -> dict:
    """Persiste las marcas de UNA cotizacion sobre los KIT.

    Recibe el conjunto completo de marcas de esa cotizacion (`{kit_id: cajas}`) y
    deja HubSpot igual: escribe las que hay y BORRA la marca de los KIT que ya no
    aparecen. Es idempotente, asi que reenviar lo mismo no cambia nada y volver
    atras en la card desmarca de verdad.

    No valida solapes a proposito. Marcar no bloquea: varios comerciales pueden
    marcar el mismo KIT y todos ven quien lo marco. Quien reparte de verdad es
    Administracion al validar el consumo, que es el unico paso que descuenta.

    Marcar tampoco mueve la etapa ni toca `cajas_disponibles_kit`: una reserva sin
    validar puede caerse, y si descontara, una oferta perdida dejaria el KIT
    bloqueado para siempre.
    """
    cotizacion_id = str(cotizacion_id or "").strip()
    if not cotizacion_id:
        raise ValueError("cotizacion_id es obligatorio")

    pedidas = {str(k).strip(): int(v or 0) for k, v in (marcas or {}).items() if str(k).strip()}

    actualizados, sin_cambio = [], []

    for d in buscar_kits(token):
        kit_id = d["id"]
        p = d.get("properties") or {}
        raw = p.get(PROP_KIT_MARCADO_POR_IDS)

        # Nunca por encima de lo que el KIT tiene libre, aunque llegue de mas.
        tope = calcular_cajas_disponibles(p.get(PROP_KIT_CAJAS), p.get(PROP_KIT_CONSUMIDO_POR_IDS))
        cajas = min(pedidas.get(kit_id, 0), tope)

        nuevo = aplicar_marca_kit(raw, cotizacion_id, cajas)
        if nuevo == (raw or ""):
            sin_cambio.append(kit_id)
            continue

        _patch(kit_id, {PROP_KIT_MARCADO_POR_IDS: nuevo}, token)
        actualizados.append(
            {
                "kit_id": kit_id,
                "numero": p.get(PROP_KIT_NUMERO) or "",
                "cajas": cajas,
                "marcas": parsear_marcas_kit(nuevo),
            }
        )

    logger.info(
        f"[kits] marcas de cotizacion={cotizacion_id}: "
        f"{len(actualizados)} KIT actualizados, {len(sin_cambio)} sin cambio"
    )
    return {"actualizados": actualizados, "sin_cambio": len(sin_cambio)}


def _nombres_de_negocios(ids, token: str) -> Dict[str, dict]:
    """Nombre y propietario de varios negocios en UNA llamada.

    `kit_marcado_por_ids` guarda sólo ids, y un id suelto no le dice nada a nadie:
    Administración necesita ver de qué oferta se trata. Se usa batch/read en vez
    de una lectura por marca para no hacer N+1.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return {}

    try:
        res = _post(
            f"{BASE_URL}/crm/v3/objects/deals/batch/read",
            {"properties": ["dealname", "hubspot_owner_id"],
             "inputs": [{"id": i} for i in ids]},
            token,
        )
    except RuntimeError as e:
        # Un fallo aquí no debe tumbar el listado: se devuelven los ids a secas.
        logger.warning(f"[kits] no se pudieron leer los negocios marcadores: {e}")
        return {}

    return {
        d["id"]: {
            "nombre": (d.get("properties") or {}).get("dealname") or "",
            "owner_id": (d.get("properties") or {}).get("hubspot_owner_id") or "",
        }
        for d in res.get("results", [])
    }


def _propietarios(token: str) -> Dict[str, str]:
    """Índice ownerId -> nombre. Una sola llamada para todos."""
    try:
        res = _get(f"{BASE_URL}/crm/v3/owners?limit=500", token)
    except RuntimeError as e:
        logger.warning(f"[kits] no se pudieron leer los propietarios: {e}")
        return {}

    idx = {}
    for o in res.get("results", []):
        nombre = " ".join(x for x in (o.get("firstName"), o.get("lastName")) if x).strip()
        idx[str(o.get("id"))] = nombre or o.get("email") or ""
    return idx


def kits_disponibles_para_card(token: str, skus: Optional[List[str]] = None) -> List[Dict]:
    """KIT consumibles con la forma exacta que pinta `PanelKitsDisponibles`.

    Cada KIT viaja con sus marcas resueltas a nombre de negocio y propietario:
    sin eso, la columna "Marcado por" no puede cumplir la regla acordada de que
    varios comerciales marquen el mismo KIT y todos vean quién lo hizo.
    """
    analisis = analizar(buscar_kits(token), skus)

    consumibles = [
        (sku, f)
        for sku, filas in sorted(analisis["por_sku"].items())
        for f in filas
        if f["consumible"]
    ]

    # Todos los negocios marcadores de una vez, y los propietarios sólo si hay
    # alguna marca: en el caso normal —ningún KIT marcado— no se gasta ninguna
    # llamada extra.
    ids = {d for _, f in consumibles for d in f["marcas"]}
    negocios = _nombres_de_negocios(sorted(ids), token) if ids else {}
    owners = _propietarios(token) if negocios else {}

    def marcas_de(fila) -> List[Dict]:
        salida = []
        for deal_id, cajas in fila["marcas"].items():
            info = negocios.get(deal_id, {})
            salida.append({
                "negocioId": deal_id,
                "cajas": cajas,
                "negocio": info.get("nombre", ""),
                "propietario": owners.get(info.get("owner_id", ""), ""),
            })
        return salida

    return [
        {
            "id": f["deal_id"],
            "numeroDeKit": f["numero"],
            "productoSku": sku,
            "productoNombre": f["nombre"],
            "cajasDisponibles": f["disponibles"],
            "cajasKit": f["cajas_kit"],
            "marcas": marcas_de(f),
        }
        for sku, f in consumibles
    ]


def _put(url: str, token: str) -> None:
    """PUT sin cuerpo, para las asociaciones por defecto de la v4."""
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        logger.error(f"[kits] HubSpot {e.code} al asociar: {detalle}")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def kits_ya_generados(token: str, cotizacion_id: str) -> Dict[str, dict]:
    """KIT que esta cotizacion ya generó, indexados por SKU.

    Es lo que hace la creación idempotente. Republicar una cotización es normal, y
    sin esta comprobación cada publicación crearía KIT nuevos para el mismo
    déficit: al tercer intento habría tres KIT de 644 cajas donde debía haber uno.
    """
    cotizacion_id = str(cotizacion_id or "").strip()
    if not cotizacion_id:
        return {}

    res = _post(
        f"{BASE_URL}/crm/v3/objects/deals/search",
        {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "pipeline", "operator": "EQ", "value": KIT_PIPELINE_ID},
                        {
                            "propertyName": PROP_KIT_COTIZACION_GENERADORA,
                            "operator": "EQ",
                            "value": cotizacion_id,
                        },
                    ]
                }
            ],
            "properties": [PROP_KIT_NUMERO, PROP_KIT_SKU, PROP_KIT_CAJAS, "dealstage"],
            "limit": 100,
        },
        token,
    )

    return {
        (d["properties"].get(PROP_KIT_SKU) or "").strip(): {
            "deal_id": d["id"],
            "numero": d["properties"].get(PROP_KIT_NUMERO) or "",
            "cajas": int(float(d["properties"].get(PROP_KIT_CAJAS) or 0)),
        }
        for d in res.get("results", [])
        if (d["properties"].get(PROP_KIT_SKU) or "").strip()
    }


def crear_kits_de_cotizacion(token: str, cotizacion_id: str, kits: List[Dict]) -> dict:
    """Crea un negocio KIT por artículo deficitario.

    Idempotente por (cotizacion, SKU): si ya existe un KIT de esa cotización para
    ese producto, no se crea otro. Se informa de él y se sigue.

    Cada KIT nace en `Pendiente de validación` y sin importe: las cajas las calcula
    el cotizador, pero sólo Administración confirma que la fábrica las va a
    producir, y un KIT no factura.

    La asociación con la Company de PROEX se hace aquí y no se deja para después:
    un KIT creado automáticamente que quede huérfano no aparece en las vistas con
    las que trabaja Administración.
    """
    cotizacion_id = str(cotizacion_id or "").strip()
    if not cotizacion_id:
        raise ValueError("cotizacion_id es obligatorio")

    existentes = kits_ya_generados(token, cotizacion_id)
    creados, omitidos = [], []
    # La secuencia continúa donde quedó: si la cotización ya generó el -01 para
    # otro producto, el siguiente es el -02.
    siguiente = len(existentes) + 1

    for k in kits or []:
        sku = str(k.get("sku") or "").strip()
        cajas = int(float(k.get("cajas") or 0))

        if not sku or cajas <= 0:
            omitidos.append({"sku": sku, "motivo": "sin SKU o sin cajas"})
            continue

        ya = existentes.get(sku)
        if ya:
            omitidos.append({
                "sku": sku,
                "motivo": "esta cotizacion ya generó un KIT de este producto",
                "kit_id": ya["deal_id"],
                "numero": ya["numero"],
                "cajas": ya["cajas"],
            })
            continue

        payload = construir_payload_kit(
            cotizacion_id=cotizacion_id,
            secuencia=siguiente,
            sku=sku,
            nombre_producto=str(k.get("nombre") or ""),
            cajas_kit=cajas,
        )
        creado = _post(f"{BASE_URL}/crm/v3/objects/deals", {"properties": payload}, token)
        kit_id = creado["id"]

        # Si la asociación falla, el KIT ya existe: se avisa pero no se deshace la
        # creación, que es el dato importante.
        try:
            _put(
                f"{BASE_URL}/crm/v4/objects/deals/{kit_id}/associations/default/companies/"
                f"{KIT_COMPANY_ID}",
                token,
            )
            asociado = True
        except RuntimeError as e:
            logger.warning(f"[kits] KIT {kit_id} creado pero sin asociar a PROEX: {e}")
            asociado = False

        logger.info(
            f"[kits] creado {payload[PROP_KIT_NUMERO]} ({cajas} cajas) "
            f"para la cotizacion {cotizacion_id}"
        )
        creados.append({
            "kit_id": kit_id,
            "numero": payload[PROP_KIT_NUMERO],
            "sku": sku,
            "cajas": cajas,
            "asociado_a_proex": asociado,
        })
        existentes[sku] = {"deal_id": kit_id, "numero": payload[PROP_KIT_NUMERO], "cajas": cajas}
        siguiente += 1

    return {"creados": creados, "omitidos": omitidos}


def params_kit_por_sku(token: str, skus: List[str]) -> Dict[str, dict]:
    """`tamano_kit` y `bloque_multiplo` de varios productos, indexados por SKU.

    Existe porque el catalogo (`ProductItem`) no sirve esas dos propiedades, y
    mientras no lleguen la card tira de un fichero escrito a mano con un solo SKU:
    aunque PROEX rellene el lote minimo en los 326 productos, la card no se
    enteraria.

    Con esto cada producto se activa solo en cuanto tenga el dato, sin desplegar
    nada ni tocar el catalogo.

    Los productos sin `tamano_kit` NO se devuelven: quien llama no debe inventarse
    un bloque, y la ausencia significa "este articulo no se fabrica en bloques, o
    aun no se sabe".
    """
    limpios = sorted({str(s).strip() for s in (skus or []) if str(s).strip()})
    if not limpios:
        return {}

    salida: Dict[str, dict] = {}
    # HubSpot limita los valores de un filtro IN, asi que se pide por tandas.
    for i in range(0, len(limpios), 100):
        lote = limpios[i:i + 100]
        res = _post(
            f"{BASE_URL}/crm/v3/objects/products/search",
            {
                "filterGroups": [
                    {"filters": [{"propertyName": "hs_sku", "operator": "IN", "values": lote}]}
                ],
                "properties": ["hs_sku", PROP_PRODUCTO_TAMANO_KIT, PROP_PRODUCTO_BLOQUE_MULTIPLO],
                "limit": 100,
            },
            token,
        )
        for p in res.get("results", []):
            props = p.get("properties") or {}
            sku = (props.get("hs_sku") or "").strip()
            params = params_kit_de_producto(props)
            if sku and params:
                salida[sku] = params

    logger.info(f"[kits] params de KIT: {len(salida)} de {len(limpios)} SKU tienen tamano_kit")
    return salida
