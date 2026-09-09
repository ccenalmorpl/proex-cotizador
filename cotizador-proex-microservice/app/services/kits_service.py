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
    ASOC_COTIZACION_A_KIT,
    COTIZACION_PIPELINE_ID,
    KIT_COMPANY_ID,
    KIT_DEALSTAGE_ABIERTO,
    KIT_DEALSTAGE_LABELS,
    KIT_PIPELINE_ID,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_CONSUMIDO_POR_IDS,
    PROP_KIT_COTIZACION_GENERADORA,
    PROP_KIT_GENERADO_POR_IDS,
    PROP_KIT_MARCADO_POR_IDS,
    PROP_KIT_NOMBRE,
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_VALIDACION_PRODUCCION,
    PROP_LINE_ITEM_CANTIDAD,
    PROP_LINE_ITEM_PRODUCTO,
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


def _asociar_etiquetada(desde_id: str, hasta_id: str, type_id: int, token: str) -> None:
    """Asociación negocio↔negocio con etiqueta. `_put` no sirve: no manda cuerpo.

    HubSpot pone el otro sentido del par por su cuenta, así que basta una llamada.
    """
    cuerpo = [{"associationCategory": "USER_DEFINED", "associationTypeId": int(type_id)}]
    req = urllib.request.Request(
        f"{BASE_URL}/crm/v4/objects/deals/{desde_id}/associations/deals/{hasta_id}",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        raise RuntimeError(f"HubSpot {e.code}: {detalle}") from e


def _asociar_generadores(kit_id: str, generadores, token: str) -> Dict[str, int]:
    """Asocia el KIT a cada oferta que lo genera. Espejo de la propiedad.

    No aborta si una falla: la propiedad ya quedó escrita y es la que manda, así
    que un fallo aquí degrada la vista en HubSpot pero no el cálculo. Se devuelve
    el recuento para poder avisar.
    """
    hechas, fallidas = 0, 0
    for deal_id in generadores:
        try:
            _asociar_etiquetada(deal_id, kit_id, ASOC_COTIZACION_A_KIT, token)
            hechas += 1
        except RuntimeError as e:
            fallidas += 1
            logger.warning(
                f"[kits] KIT {kit_id} sin asociar al negocio {deal_id}: {e}"
            )
    return {"hechas": hechas, "fallidas": fallidas}


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

        # Ofertas anotadas: llegan como [{id, cajas}] y aqui se indexan. Un id
        # vacio se descarta en vez de escribir una entrada rota en la propiedad.
        generadores = {
            str(n.get("id") or "").strip(): int(float(n.get("cajas") or 0))
            for n in (k.get("negocios") or [])
            if str(n.get("id") or "").strip()
        }

        payload = construir_payload_kit(
            cotizacion_id=cotizacion_id,
            secuencia=siguiente,
            sku=sku,
            nombre_producto=str(k.get("nombre") or ""),
            cajas_kit=cajas,
            negocios_generadores=generadores,
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

        # Espejo de `kit_generado_por_ids`: la propiedad ya está escrita en el
        # payload, esto solo hace que el vínculo se vea en el registro.
        del_payload = parsear_marcas_kit(payload.get(PROP_KIT_GENERADO_POR_IDS))
        asoc = _asociar_generadores(kit_id, del_payload, token)

        logger.info(
            f"[kits] creado {payload[PROP_KIT_NUMERO]} ({cajas} cajas) "
            f"para la cotizacion {cotizacion_id}, "
            f"{asoc['hechas']} negocios asociados"
        )
        creados.append({
            "kit_id": kit_id,
            "numero": payload[PROP_KIT_NUMERO],
            "sku": sku,
            "cajas": cajas,
            "asociado_a_proex": asociado,
            "negocios_generadores": sorted(del_payload),
            "negocios_asociados": asoc["hechas"],
            "negocios_sin_asociar": asoc["fallidas"],
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


# ─────────────────────────────────────────────────────────────────────────────
# NEGOCIOS GENERADORES DEL KIT
#
# Reunión del 2026-09-03: al pedir un KIT se anotan otras ofertas abiertas de la
# misma referencia y su demanda entra en la producción, para consolidar en una
# tirada en vez de dejar cajas paradas y arrancar otra dentro de un mes.
#
# El camino es referencia → producto → line items → negocios, y no al revés:
# ir por negocios obligaría a leer los line items de los 65 negocios abiertos,
# mientras que partir del producto deja 6 line items para esta referencia.
# ─────────────────────────────────────────────────────────────────────────────


def _productos_por_referencia(token: str, referencias: List[str]) -> Dict[str, str]:
    """Referencia comercial → ID interno del producto en HubSpot.

    Hace falta porque `hs_sku` significa dos cosas distintas: en el producto es
    la referencia ("0303003002-00250") y en el line item es este ID interno.
    """
    limpias = sorted({str(r).strip() for r in (referencias or []) if str(r).strip()})
    if not limpias:
        return {}

    salida: Dict[str, str] = {}
    for i in range(0, len(limpias), 100):
        res = _post(
            f"{BASE_URL}/crm/v3/objects/products/search",
            {
                "filterGroups": [
                    {"filters": [{"propertyName": "hs_sku", "operator": "IN",
                                  "values": limpias[i:i + 100]}]}
                ],
                "properties": ["hs_sku"],
                "limit": 100,
            },
            token,
        )
        for p in res.get("results", []):
            ref = ((p.get("properties") or {}).get("hs_sku") or "").strip()
            if ref:
                salida[ref] = str(p.get("id"))
    return salida


def _cajas_por_negocio(token: str, producto_id: str) -> Dict[str, int]:
    """Negocio → cajas de ese producto, sumando TODOS sus contenedores.

    Un negocio puede llevar la misma referencia en varios contenedores y las
    cajas son la suma ("sumar las cajas de los contenedores que incluyen ese
    artículo"). Los line items de contenedor no estorban: no llevan el ID del
    producto, así que el filtro los deja fuera solo.
    """
    line_items: List[dict] = []
    after = None
    while True:
        cuerpo = {
            "filterGroups": [
                {"filters": [{"propertyName": PROP_LINE_ITEM_PRODUCTO,
                              "operator": "EQ", "value": str(producto_id)}]}
            ],
            "properties": [PROP_LINE_ITEM_CANTIDAD],
            "limit": 100,
        }
        if after:
            cuerpo["after"] = after
        res = _post(f"{BASE_URL}/crm/v3/objects/line_items/search", cuerpo, token)
        line_items += res.get("results", [])
        after = ((res.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break

    if not line_items:
        return {}

    cantidad = {}
    for li in line_items:
        try:
            cantidad[str(li.get("id"))] = int(float(
                (li.get("properties") or {}).get(PROP_LINE_ITEM_CANTIDAD) or 0))
        except (TypeError, ValueError):
            cantidad[str(li.get("id"))] = 0

    cajas: Dict[str, int] = defaultdict(int)
    ids = list(cantidad)
    for i in range(0, len(ids), 100):
        res = _post(
            f"{BASE_URL}/crm/v4/associations/line_items/deals/batch/read",
            {"inputs": [{"id": x} for x in ids[i:i + 100]]},
            token,
        )
        for fila in res.get("results", []):
            origen = str((fila.get("from") or {}).get("id"))
            for destino in fila.get("to") or []:
                cajas[str(destino.get("toObjectId"))] += cantidad.get(origen, 0)

    return dict(cajas)


def _etapas_abiertas_de_cotizacion(token: str) -> Dict[str, str]:
    """Etapas NO cerradas del pipeline de cotización → su etiqueta.

    Se usa `metadata.isClosed` en vez de comparar contra los IDs de ganado y
    perdido: si PROEX añade otra etapa de cierre, queda fuera sin tocar código.
    """
    res = _get(
        f"{BASE_URL}/crm/v3/pipelines/deals/{COTIZACION_PIPELINE_ID}", token
    )
    etapas = {}
    for etapa in res.get("stages") or []:
        cerrada = str((etapa.get("metadata") or {}).get("isClosed", "")).lower() == "true"
        if not cerrada:
            etapas[str(etapa.get("id"))] = etapa.get("label") or ""
    return etapas


def _negocios_con_etapa(ids: List[str], token: str) -> Dict[str, dict]:
    """Nombre, etapa y pipeline de varios negocios en UNA llamada.

    No se reutiliza `_nombres_de_negocios` porque aquella no lee la etapa y
    ampliarla cambiaria lo que ya consumen los KIT disponibles.
    """
    ids = [str(i).strip() for i in (ids or []) if str(i).strip()]
    if not ids:
        return {}

    salida: Dict[str, dict] = {}
    for i in range(0, len(ids), 100):
        try:
            res = _post(
                f"{BASE_URL}/crm/v3/objects/deals/batch/read",
                {"properties": ["dealname", "dealstage", "pipeline"],
                 "inputs": [{"id": x} for x in ids[i:i + 100]]},
                token,
            )
        except RuntimeError as e:
            logger.warning(f"[kits] no se pudieron leer los negocios generadores: {e}")
            continue
        for d in res.get("results", []):
            props = d.get("properties") or {}
            salida[str(d["id"])] = {
                "nombre": props.get("dealname") or "",
                "etapa": str(props.get("dealstage") or ""),
                "pipeline": str(props.get("pipeline") or ""),
            }
    return salida


def negocios_generadores_por_sku(
    token: str,
    skus: List[str],
    excluir_deal_id: str = "",
) -> Dict[str, List[Dict]]:
    """Por referencia, las ofertas abiertas que podrían generar el KIT con ella.

    Devuelve `{sku: [{id, nombre, etapa, cajas}]}`. La cotización actual se
    excluye de la lista porque en la card va siempre dentro y no se puede quitar:
    ofrecerla como opción sería ofrecer desmarcar lo que no se desmarca.

    Un negocio sin cajas de esa referencia no sale: no aportaría demanda y solo
    alargaría una lista que ya puede tener decenas de ofertas.
    """
    productos = _productos_por_referencia(token, skus)
    if not productos:
        return {}

    abiertas = _etapas_abiertas_de_cotizacion(token)
    excluir = str(excluir_deal_id or "").strip()

    salida: Dict[str, List[Dict]] = {}
    for sku, producto_id in productos.items():
        cajas = _cajas_por_negocio(token, producto_id)
        candidatos = [d for d in cajas if d != excluir and cajas[d] > 0]
        if not candidatos:
            salida[sku] = []
            continue

        negocios = _negocios_con_etapa(candidatos, token)
        filas = []
        for deal_id in candidatos:
            info = negocios.get(deal_id) or {}
            # El filtro de la reunión: pipeline de cotización, ni ganado ni
            # perdido. Una etapa que no esté entre las abiertas queda fuera.
            if info.get("pipeline") != COTIZACION_PIPELINE_ID:
                continue
            etapa = info.get("etapa") or ""
            if etapa not in abiertas:
                continue
            filas.append({
                "id": deal_id,
                "nombre": info.get("nombre") or f"Negocio {deal_id}",
                "etapa": abiertas[etapa],
                "cajas": cajas[deal_id],
            })

        filas.sort(key=lambda f: -f["cajas"])
        salida[sku] = filas

    total = sum(len(v) for v in salida.values())
    logger.info(
        f"[kits] negocios generadores: {total} ofertas abiertas "
        f"en {len(salida)} referencias (excluida {excluir or 'ninguna'})"
    )
    return salida
