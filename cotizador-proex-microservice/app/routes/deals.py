import json
import logging
import re
import requests
import time
from typing import Any, Dict, Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Path
from hubspot import HubSpot
from hubspot.crm.associations import BatchInputPublicObjectId
from hubspot.crm.associations.models import PublicObjectId
from hubspot.crm.objects import PublicObjectSearchRequest, Filter, FilterGroup
from itertools import islice

from app.models.schemas import (
    ClientDataResponse,
    ContactData,
    CompanyData,
    DealClientData,
    PropertyWithOptions,
    PropertyOption,
    UpdateClientDataRequest,
    UpdateContactData,
    UpdateCompanyData,
    UpdateDealData,
    UpdateDocumentacionRequest,
    UpdateDocumentacionData,
    DealDocumentacionResponse,
    DealDocumentacionData,
    EmpresaAsociadaItem,
    EmpresasAsociadasResponse,
    BaseResponse,
    UpdateArticlesRequest,
    UpdateServicesRequest,
    DealServicesResponse,
    ServiceItem,
    ProviderSummary,
    DealArticlesResponse,
    ArticleWithProvider,
    UpdateContainersRequest,
    UpdateContainersResponse,
    ContainerLineItemInput,
    UpdateSingleContainerRequest,
    UpdateSingleContainerResponse,
    ContainerConfigEdit,
    DealContainersResponse,
    ContainerItem,
    UniqueContainerItem,
    ContainerConfig,
    UniqueContainerConfig,
    UniqueContainerArticle,
    DealTransitResponse,
    DealTransitUpdateRequest,
    DealTransitDataResponse,
    DealRutasResponse,
    RutaItem,
    UpdateNegociacionRequest,
    TextosCotizacionRequest,
    TextosCotizacionResponse,
    TextoCotizacionItem,
    TipoIVARequest,
    TipoIVAResponse,
    TipoIVAFilter,
    TipoIVAItem,
    PublishQuoteRequest,
    PublishQuoteResponse
)
from app.services.hubspot_client import hubspot_client
from app.services.jobs_store import create_job, update_job, get_latest_job_by_deal
from app.services.jobs_queue import enqueue_job
from app.services import deal_lock
from app.shared import hubdb_client
from app.core.custom_objects_constants import (
    OBJECT_TYPE_CONTENEDOR,
    OBJECT_TYPE_ELEMENTO,
    PROVEEDOR_ASSOC_TYPE_ID_TRANSITARIO_TERRESTRE,
    ASSOC_CATEGORY_USER_DEFINED,
    COMPANY_PROP_ADDRESS_ENTREGA,
)
from app.services.quote_helpers import (
    build_association,
    create_quote_via_api,
    create_line_item_via_api,
    associate_line_item_to_quote_v4,
    batch_create_line_items_via_api,
    batch_associate_line_items_to_quote_v4,
    normalize_hs_date,
)
from app.services.custom_objects import (
    get_contenedores_by_deal,
    get_elementos_by_contenedor,
    get_elementos_by_deal,
    update_elemento,
    delete_elemento,
    update_contenedor,
    batch_create_elementos_with_associations,
    batch_update_custom_objects,
    touch_pagos_for_contenedores,
    sync_contenedor_proveedor_associations,
    sync_deal_ruta_associations,
    process_deal_custom_objects_from_line_items,
    _normalize_for_diff,
)
from app.core.constants import AssociationType, SENDER_COMPANY_PROPERTIES
from app.utils.fletes import (
    FLETE_SUFIJO_MARITIMO,
    FLETE_SUFIJO_TERRESTRE,
    TRAMO_LEGACY,
    TRAMO_MARITIMO,
    TRAMO_TERRESTRE,
    clasificar_tramo_flete,
)
from app.utils.iva_helpers import recalcular_contenedores
from app.utils.pricing import _normalize_incoterm
from app.utils.tipologia import es_camion, es_destino_fuera_territorio_aduanero_ue
from app.utils.property_helpers import safe_convert, build_update_properties, parse_hubspot_properties, compute_tipo_iva_repercutido, compute_default_tipo_iva, resolve_product_iva, round_price, round_margin, enforce_line_item_price_rounding, resolve_almacen_para_elemento

logger = logging.getLogger(__name__)
router = APIRouter()

CUSTOM_OBJECTS_JOB_TYPE = "quotes_publish_custom_objects"


def parse_fecha_caducidad(value) -> Optional[int]:
    """Parsea una propiedad date de HubSpot a timestamp UTC en ms (acepta entero/string en ms o fecha YYYY-MM-DD)."""
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(str(value), "%Y-%m-%d")
            dt_utc = dt.replace(tzinfo=timezone.utc)
            return int(dt_utc.timestamp() * 1000)
        except Exception as e:
            logger.warning(f"Could not parse fecha_caducidad value '{value}': {e}")
            return None


def _get_associated_ids(deal_id: str, to_object_type: str) -> List[str]:
    """
    Devuelve TODOS los IDs asociados a un deal para el tipo indicado.

    Pagina la v4 hasta agotar los resultados: la batch API v3 corta en 100 por
    objeto sin avisar, y con eso el merge de contenedores solo veía una parte de
    los line items de una cotización grande (archivaba contenedores a medias y
    dejaba fantasmas). Si la v4 falla se cae a la batch API, que puede truncar.
    """
    client = hubspot_client.get_client()
    ids = []
    truncado = False

    try:
        access_token = client.access_token
        url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/{to_object_type}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        after = None
        paginas = 0

        while True:
            params = {"limit": 500}
            if after:
                params["after"] = after
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text}")

            data = r.json() or {}
            for item in data.get("results", []):
                val = item.get("toObjectId") or item.get("id")
                if val is not None:
                    ids.append(str(val))

            paginas += 1
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break
            if paginas >= 40:
                logger.error(
                    f"[HTTP v4] corte de seguridad tras {paginas} páginas de {to_object_type} "
                    f"en deal {deal_id}; puede faltar contenido"
                )
                break

        logger.warning(f"[HTTP v4] {len(ids)} {to_object_type} asociados al deal {deal_id} ({paginas} pág.)")
        return ids

    except Exception as e:
        logger.warning(f"[HTTP v4] asociaciones {to_object_type} falló ({e}); pruebo Batch API")

    try:
        deal_object_id = PublicObjectId(id=deal_id)
        batch_input = BatchInputPublicObjectId(inputs=[deal_object_id])

        associations_response = client.crm.associations.batch_api.read(
            from_object_type="deals",
            to_object_type=to_object_type,
            batch_input_public_object_id=batch_input
        )

        if hasattr(associations_response, 'results') and associations_response.results:
            for result in associations_response.results:
                if hasattr(result, 'to') and result.to:
                    for assoc in result.to:
                        assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                        if assoc_id:
                            ids.append(str(assoc_id))

        if ids:
            logger.warning(
                f"[Batch API] {len(ids)} {to_object_type} asociados al deal {deal_id} "
                f"(sin paginar: puede estar truncado a 100)"
            )
            truncado = len(ids) == 100

    except Exception as batch_error:
        logger.error(f"[Batch API] asociaciones {to_object_type} falló: {batch_error}")

    # 100 exactos es la firma del truncado, no un dato: esta rama no pagina, y devolver
    # la lista corta como si estuviera completa es lo que publicó una cotización a la
    # que le faltaban 20 líneas y 25.843,20 € (07/09/2026).
    if truncado:
        raise RuntimeError(
            f"[Batch API] {to_object_type} del deal {deal_id}: 100 asociaciones exactas "
            f"sin paginar, la lista está truncada y no se puede usar"
        )

    return ids

def get_associated_contacts(deal_id: str) -> List[str]:
    return _get_associated_ids(deal_id, "contacts")

def get_associated_companies(deal_id: str) -> List[str]:
    return _get_associated_ids(deal_id, "companies")

def _get_line_item_to_product_ids(line_item_ids: List[str], access_token: str) -> dict:
    """
    Get the associated product ID for each line item (when hs_product_id property is empty).
    Uses HubSpot v4 batch associations: line_items -> products; los line items sin
    producto asociado no salen en el dict.
    """
    if not line_item_ids:
        return {}
    out = {}
    url = "https://api.hubapi.com/crm/v4/associations/line_items/products/batch/read"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    # Batch read accepts up to 1000 inputs
    for i in range(0, len(line_item_ids), 100):
        batch = line_item_ids[i:i + 100]
        payload = {"inputs": [{"id": str(lid)} for lid in batch]}
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if not r.ok:
                logger.warning(f"[associations] line_items->products batch read failed: {r.status_code} {r.text[:200]}")
                continue
            data = r.json()
            results = data.get("results", [])
            if not results and batch:
                logger.warning(f"[associations] line_items->products batch read returned 0 results; raw keys={list(data.keys())} status={data.get('status')}")
            for item in results:
                from_id = (item.get("from") or {}).get("id")
                to_list = item.get("to") or []
                if from_id and to_list:
                    to_first = to_list[0]
                    to_id = to_first.get("toObjectId") or to_first.get("id")
                    if to_id is not None:
                        out[str(from_id)] = str(to_id)
        except Exception as e:
            logger.warning(f"[associations] line_items->products batch read error: {e}")
    return out

def _get_line_items_by_tipo(deal_id: str, tipo_articulo: str) -> List[str]:
    """
    Get line item IDs associated with a deal, filtered by tipo_articulo.
    """
    client = hubspot_client.get_client()

    all_line_item_ids = _get_associated_ids(deal_id, "line_items")
    logger.warning(f"[Filter] Found {len(all_line_item_ids)} total line items for deal {deal_id}")

    filtered_ids = []
    for line_item_id in all_line_item_ids:
        try:
            li = client.crm.line_items.basic_api.get_by_id(
                line_item_id,
                properties=["tipo_articulo"]
            )

            item_tipo = (li.properties.get("tipo_articulo") or "").strip().lower()
            filter_tipo = tipo_articulo.strip().lower()

            if item_tipo == filter_tipo:
                filtered_ids.append(line_item_id)
                logger.warning(f"[Filter] Line item {line_item_id} matches tipo_articulo={filter_tipo}")
            else:
                logger.warning(f"[Filter] Line item {line_item_id} skipped (tipo_articulo={item_tipo})")

        except Exception as e:
            logger.warning(f"[Filter] Error getting line item {line_item_id}: {e}")

    logger.warning(f"[Filter] Filtered to {len(filtered_ids)} line items with tipo_articulo={tipo_articulo}")
    return filtered_ids

def _get_providers_by_tipo(deal_id: str, tipo_proveedor: str) -> List[str]:
    """
    Optimized: Batch-read providers associated to a deal and filter by tipo_proveedor.
    Avoids doing one GET per provider (massive speed improvement).
    """
    client = hubspot_client.get_client()
    access_token = client.access_token

    all_provider_ids = _get_associated_ids(deal_id, "2-43108800")
    logger.warning(f"[providers-batch-filter] Found {len(all_provider_ids)} providers for deal {deal_id}")

    if not all_provider_ids:
        return []

    def chunk(arr, size=100):
        for i in range(0, len(arr), size):
            yield arr[i:i+size]

    matched_ids = []
    filter_tipo = tipo_proveedor.strip().lower()

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    for batch_ids in chunk(all_provider_ids):
        try:
            logger.warning(f"[providers-batch-filter] Reading batch of {len(batch_ids)} providers")

            read_payload = {
                "properties": ["tipo_proveedor", "cardcode"],
                "inputs": [{"id": str(pid)} for pid in batch_ids]
            }

            read_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/batch/read"

            resp = requests.post(read_url, headers=headers, json=read_payload, timeout=20)

            if not resp.ok:
                logger.error(f"[providers-batch-filter] Batch read failed: {resp.status_code} - {resp.text}")
                continue

            data = resp.json()
            results = data.get("results", [])

            for provider in results:
                pid = provider.get("id")
                props = provider.get("properties", {})

                prov_tipo = (props.get("tipo_proveedor") or "").strip().lower()

                if prov_tipo == filter_tipo:
                    matched_ids.append(str(pid))
                    logger.warning(f"[providers-batch-filter] Provider {pid} matches tipo_proveedor={filter_tipo}")

        except Exception as e:
            logger.error(f"[providers-batch-filter] Error reading provider batch: {e}")

    logger.warning(f"[providers-batch-filter] Total filtered providers: {len(matched_ids)}")
    return matched_ids

# Container SKU mapping (tipo from frontend -> HubSpot Product SKU)
CONTAINER_SKU_MAP = {
    "D20DR": "CONTAINER-D20DR",
    "D40DR": "CONTAINER-D40DR",
    "TRAILER80": "CONTAINER-TRAILER80",
    "TRAILER 80": "CONTAINER-TRAILER80",  # alias: HubSpot property value includes space
}

# hs_product_type: values accepted when classifying line items (EN + ES; compare in lowercase)
HS_PRODUCT_TYPE_CONTAINER = {"container", "contenedor"}
HS_PRODUCT_TYPE_SERVICE = {"service", "servicio", "flete"}
HS_PRODUCT_TYPE_ARTICLE = {"inventory", "non_inventory", "articulo"}

def _normalize_hs_product_type(value: Any) -> str:
    """Normalize hs_product_type for comparison (lowercase, stripped)."""
    if value is None:
        return ""
    return str(value).strip().lower()

def _is_container_product_type(product_type: str) -> bool:
    """True if hs_product_type indicates a container (container, contenedor)."""
    return _normalize_hs_product_type(product_type) in HS_PRODUCT_TYPE_CONTAINER

def _is_service_product_type(product_type: str) -> bool:
    """True if hs_product_type indicates a service (service, servicio, flete)."""
    return _normalize_hs_product_type(product_type) in HS_PRODUCT_TYPE_SERVICE

def _is_article_product_type(product_type: str) -> bool:
    """True if hs_product_type indicates an article (inventory, non_inventory, articulo)."""
    return _normalize_hs_product_type(product_type) in HS_PRODUCT_TYPE_ARTICLE

# Reglas unificadas para READ: filtrar line items por propiedades del line item (hs_product_type + tipo_articulo)
def _line_item_is_article(props: dict) -> bool:
    """True si el line item es artículo: hs_product_type = inventory y tipo_articulo = fabricante."""
    pt = _normalize_hs_product_type((props.get("hs_product_type") or ""))
    ta = (props.get("tipo_articulo") or "").strip().lower()
    return pt in HS_PRODUCT_TYPE_ARTICLE and ta == "fabricante"

def _line_item_is_service(props: dict) -> bool:
    """True si el line item es servicio: hs_product_type = service (solo)."""
    pt = _normalize_hs_product_type((props.get("hs_product_type") or ""))
    return pt in HS_PRODUCT_TYPE_SERVICE

def _line_item_is_container(props: dict) -> bool:
    """True si el line item es contenedor: hs_product_type = container y tipo_articulo = transitario."""
    pt = _normalize_hs_product_type((props.get("hs_product_type") or ""))
    ta = (props.get("tipo_articulo") or "").strip().lower()
    return pt in HS_PRODUCT_TYPE_CONTAINER and ta == "transitario"

def _quote_line_item_names(access_token: str, quote_id: str):
    """Nombres de los line items que la Quote tiene REALMENTE asociados, o None si no
    se pudieron leer.

    Se lee de HubSpot en vez de fiarse del recuento que devuelve al crearlos: con
    cotizaciones grandes confirma más líneas de las que luego aparecen. Devolver un
    conjunto vacío ante un error haría que el reintento recreara la copia entera."""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    ids, after = [], None
    while True:
        params = {"limit": 500}
        if after:
            params["after"] = after
        resp = requests.get(
            f"https://api.hubapi.com/crm/v4/objects/quotes/{quote_id}/associations/line_items",
            headers=headers, params=params, timeout=30,
        )
        if not resp.ok:
            logger.error(f"[publish] no pude releer las líneas del quote {quote_id}: {resp.status_code}")
            return None
        data = resp.json() or {}
        ids += [str(x.get("toObjectId")) for x in data.get("results", []) if x.get("toObjectId")]
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break

    nombres = set()
    for i in range(0, len(ids), 100):
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/line_items/batch/read",
            headers=headers,
            json={"properties": ["name"], "inputs": [{"id": x} for x in ids[i:i + 100]]},
            timeout=30,
        )
        if not resp.ok:
            logger.error(f"[publish] batch read de nombres falló: {resp.status_code}")
            return None
        for o in (resp.json() or {}).get("results", []):
            nombres.add((o.get("properties") or {}).get("name") or "")
    return nombres


def _catalogo_huerfano_ids(line_items: list, container_names) -> list:
    """Ids de line items de catálogo huérfanos: artículo fabricante sin numero_container
    y sin prefijo '{contenedor} - ' en el name. Los crea update_deal_articles al guardar
    ArticlesTab; con contenedores configurados son redundantes (el catálogo se reconstruye
    desde las líneas per-contenedor) y contaminan la cotización publicada.

    La exclusión por prefijo protege las líneas per-contenedor regeneradas por HubSpot
    que pierden numero_container pero conservan el name '{contenedor} - {artículo}'."""
    prefixes = tuple(f"{str(n).strip()} - " for n in container_names if n and str(n).strip())
    ids = []
    for li in line_items:
        props = li.get("properties", {}) or {}
        if (props.get("numero_container") or "").strip():
            continue
        if not _line_item_is_article(props):
            continue
        name = (props.get("name") or "").strip()
        if prefixes and name.startswith(prefixes):
            continue
        ids.append(str(li.get("id")))
    return ids

_CLONE_TIPO_ARTICULO = {"servicio", "seguro", "transitario"}
_CLONE_HS_PRODUCT_TYPE = {"servicio", "flete"}
_CLONE_PROPS = [
    "name", "price", "cardcode", "numero_container", "tipo_articulo", "hs_product_type",
    "tipo_iva_repercutido", "tipo_iva_soportado", "almacen", "hs_product_id", "hs_sku",
    "quantity", "hs_currency", "precio_de_coste",
]

def _line_item_is_clonable_service(props: dict) -> bool:
    """True si la línea es servicio/flete/seguro clonable (excluye fabricante, comisiones y el propio contenedor)."""
    ta = (props.get("tipo_articulo") or "").strip().lower()
    pt = (props.get("hs_product_type") or "").strip().lower()
    if _is_container_product_type(props.get("hs_product_type") or ""):
        return False
    if ta in {"fabricante", "comisiones"}:
        return False
    return ta in _CLONE_TIPO_ARTICULO or pt in _CLONE_HS_PRODUCT_TYPE

def _remap_clone_name(plantilla_name: str, nuevo_name: str) -> str:
    """Reemplaza el prefijo del contenedor en el name de la plantilla: '{plantilla} - SUFIJO' -> '{nuevo} - SUFIJO'."""
    name = (plantilla_name or "").strip()
    if " - " in name:
        sufijo = name.split(" - ", 1)[1]
        return f"{nuevo_name} - {sufijo}"
    return f"{nuevo_name} - {name}" if name else nuevo_name

_PREFIJO_CONTENEDOR = re.compile(r"^(\d{6,})-(\d{1,4})$")

def _sanear_prefijos_heredados(
    *,
    deal_id: str,
    all_line_items: list,
    headers: dict,
    batch_size: int = 100,
) -> list:
    """Reasigna al negocio actual los line items que arrastran el numero_container de otro.

    Clonar un negocio en HubSpot copia sus line items tal cual, con el prefijo del
    negocio origen en `numero_container` y `name` ('63756493976-001'). El cotizador
    regenera contenedores, artículos y fletes con el prefijo nuevo, pero los servicios
    heredados se quedan apuntando a contenedores que no existen aquí: no entran en
    ningún Contenedor CO, no generan Elemento y SAP nunca ve el concepto, mientras su
    importe se cuela en el precio unitario del producto al repartir. El total cuadra,
    así que el descuadre no se ve por ninguna parte.

    El sufijo se conserva: '{otro}-002' pasa a '{deal_id}-002'. Solo se archiva la línea
    cuando el negocio ya tiene contenedores propios y ninguno lleva ese sufijo.
    """
    ajenas = []
    sufijos_propios = set()
    for li in all_line_items:
        props = li.get("properties", {}) or {}
        m = _PREFIJO_CONTENEDOR.match((props.get("numero_container") or "").strip())
        if not m:
            continue
        if m.group(1) == str(deal_id):
            if _is_container_product_type(props.get("hs_product_type") or ""):
                sufijos_propios.add(m.group(2))
            continue
        ajenas.append((li, m.group(2)))

    if not ajenas:
        return all_line_items

    to_update, to_archive, archivados = [], [], set()
    for li, sufijo in ajenas:
        props = li.get("properties", {}) or {}
        if sufijos_propios and sufijo not in sufijos_propios:
            to_archive.append(str(li["id"]))
            archivados.add(str(li["id"]))
            continue
        nuevo_numero = f"{deal_id}-{sufijo}"
        nuevo_name = _remap_clone_name(props.get("name") or "", nuevo_numero)
        to_update.append({"id": str(li["id"]), "properties": {
            "numero_container": nuevo_numero,
            "name": nuevo_name,
        }})
        props["numero_container"] = nuevo_numero
        props["name"] = nuevo_name

    if to_update:
        url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
        for i in range(0, len(to_update), batch_size):
            chunk = to_update[i:i + batch_size]
            try:
                resp = requests.post(url, headers=headers, json={"inputs": chunk}, timeout=60)
                if not resp.ok:
                    logger.error(f"[heredadas] batch update failed: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"[heredadas] error batch updating: {e}")

    if to_archive:
        url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
        for i in range(0, len(to_archive), batch_size):
            chunk = to_archive[i:i + batch_size]
            try:
                resp = requests.post(url, headers=headers, json={"inputs": [{"id": x} for x in chunk]}, timeout=30)
                if not resp.ok:
                    logger.error(f"[heredadas] batch archive failed: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"[heredadas] error batch archiving: {e}")

    logger.warning(
        f"[heredadas] deal={deal_id}: {len(to_update)} líneas reasignadas al negocio actual, "
        f"{len(to_archive)} archivadas sin contenedor destino"
    )
    return [li for li in all_line_items if str(li.get("id")) not in archivados]

def _clone_template_lines_for_new_containers(
    *,
    nuevos_container_names: List[str],
    previos: list,
    existing_containers_by_lid: dict,
    line_items_by_numero: dict,
    deal_id: str,
    headers: dict,
    batch_size: int = 100,
) -> list:
    """Blindaje: clona las líneas de servicio/flete/seguro de un contenedor previo (plantilla) hacia los contenedores nuevos.

    La plantilla es el contenedor previo con menor numero_container. Solo LEE las líneas de la plantilla
    para copiarlas; nunca actualiza ni archiva las de los previos. Las líneas clonadas conservan
    (hs_product_id, numero_container) coherentes con /services y /transit, de modo que un paso posterior
    por esos tabs hace upsert en vez de duplicar.
    """
    if not nuevos_container_names or not previos:
        return []

    def _numero_key(lid: str) -> tuple:
        numero = existing_containers_by_lid.get(lid, {}).get("numero_container") or ""
        try:
            return (0, int(str(numero).split("-")[-1]))
        except (ValueError, IndexError):
            return (1, str(numero))

    plantilla_lid = min((lid for lid, _ in previos), key=_numero_key)
    plantilla_numero = existing_containers_by_lid.get(plantilla_lid, {}).get("numero_container") or ""

    plantilla_line_ids = [
        li["id"]
        for li in line_items_by_numero.get(plantilla_numero, [])
        if _line_item_is_clonable_service(li.get("properties", {}) or {})
    ]
    if not plantilla_line_ids:
        logger.warning(f"[clone] plantilla {plantilla_numero} sin líneas de servicio/flete/seguro; nada que clonar")
        return []

    batch_read_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
    plantilla_lines = []
    for i in range(0, len(plantilla_line_ids), batch_size):
        chunk = plantilla_line_ids[i:i + batch_size]
        payload = {"properties": _CLONE_PROPS, "inputs": [{"id": str(x)} for x in chunk]}
        try:
            resp = requests.post(batch_read_url, headers=headers, json=payload, timeout=30)
            if resp.ok:
                plantilla_lines.extend(resp.json().get("results", []))
            else:
                logger.error(f"[clone] batch read plantilla lines failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"[clone] error batch reading plantilla lines: {e}")

    cloned = []
    for cname in nuevos_container_names:
        for li in plantilla_lines:
            src = li.get("properties", {}) or {}
            new_props = {k: v for k, v in src.items() if v is not None and k in _CLONE_PROPS}
            new_props["numero_container"] = cname
            new_props["name"] = _remap_clone_name(src.get("name") or "", cname)
            cloned.append({
                "properties": new_props,
                "associations": [{
                    "to": {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}],
                }],
            })

    logger.warning(
        f"[clone] plantilla={plantilla_numero}: {len(plantilla_lines)} líneas x "
        f"{len(nuevos_container_names)} contenedores nuevos = {len(cloned)} líneas clonadas"
    )
    return cloned

# Valores exactos que HubSpot acepta al escribir (enums). No usar "Service" ni "ex" minúscula.
def _hs_product_type_for_hubspot(value: Any) -> str:
    """Mapea hs_product_type al valor exacto que acepta HubSpot: inventory, non_inventory, service, container, Servicio, Contenedor, Articulo, Flete."""
    v = _normalize_hs_product_type(value)
    if not v:
        return "service"
    if v in HS_PRODUCT_TYPE_SERVICE:  # service, servicio, flete
        return "Flete" if v == "flete" else "service"
    if v in HS_PRODUCT_TYPE_ARTICLE:
        return "inventory"
    if v in HS_PRODUCT_TYPE_CONTAINER:
        return "container"
    return "service"

def _tipo_iva_repercutido_for_hubspot(value: Any) -> str:
    """HubSpot espera tipo_iva_repercutido en mayúsculas (EX, R21, EI21, etc.)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "R21"
    s = str(value).strip().upper()
    return s or "R21"

def sanitize_string_for_json(value: str) -> str:
    """
    Sanitize string values to prevent JSON serialization errors.
    Handles problematic characters like unescaped quotes, backslashes, etc.
    """
    if not value:
        return ""

    # Replace problematic characters
    # Replace double quotes with single quotes
    sanitized = value.replace('"', "'")
    # Remove backslashes
    sanitized = sanitized.replace("\\", "")
    # Remove control characters (ASCII < 32)
    sanitized = "".join(char for char in sanitized if ord(char) >= 32)

    return sanitized

def create_line_item_with_association(
    client: HubSpot,
    deal_id: str,
    product_id: str,
    sku: str,
    product_name: str,
    product_price: str,
    quantity: str = "1",
    margen_unitario: Optional[float] = None,
    cardcode: Optional[str] = None,
    almacen: Optional[str] = None,
    tipo_iva_repercutido: Optional[str] = None,
    tipo_iva_soportado: Optional[str] = None,
    tipo_articulo: Optional[str] = None,
    hs_product_type: Optional[str] = None,
    peso_neto: Optional[str] = None,
    peso_bruto: Optional[str] = None,
    name_en: Optional[str] = None,
    name_fr: Optional[str] = None,
    description: Optional[str] = None,
    formato: Optional[str] = None,
    marca: Optional[str] = None
) -> Optional[str]:
    """
    Create a HubSpot line item and associate it with a deal
    """
    try:
        logger.warning(f"Creating line item for product {product_id} (SKU: {sku})")

        line_item_properties = {
            "hs_product_id": str(product_id),
            "quantity": quantity,
            "price": product_price,
            "name": product_name,
            "hs_currency": "EUR"
        }

        # Add hs_sku (HubSpot product internal ID)
        if sku:
            line_item_properties["hs_sku"] = str(sku)

        if margen_unitario is not None:
            margen_round = round_margin(margen_unitario)
            if margen_round is not None:
                line_item_properties["margen_unitario"] = str(margen_round)

        # Add cardcode if provided (used for FLETES line items with transitario cardcode)
        if cardcode is not None:
            line_item_properties["cardcode"] = str(cardcode)

        # Add almacen if provided (warehouse code from deal)
        if almacen is not None:
            line_item_properties["almacen"] = str(almacen)

        if tipo_iva_repercutido is not None:
            line_item_properties["tipo_iva_repercutido"] = str(tipo_iva_repercutido)

        if tipo_iva_soportado is not None:
            line_item_properties["tipo_iva_soportado"] = str(tipo_iva_soportado)

        # Add tipo_articulo and hs_product_type so line items are correctly classified in HubSpot
        if tipo_articulo is not None and str(tipo_articulo).strip():
            line_item_properties["tipo_articulo"] = str(tipo_articulo).strip().lower()
        if hs_product_type is not None and str(hs_product_type).strip():
            line_item_properties["hs_product_type"] = str(hs_product_type).strip()

        if peso_neto is not None:
            line_item_properties["peso_neto"] = str(peso_neto)
        if peso_bruto is not None:
            line_item_properties["peso_bruto"] = str(peso_bruto)
        if name_en is not None:
            line_item_properties["name_en"] = str(name_en)
        if name_fr is not None:
            line_item_properties["name_fr"] = str(name_fr)
        if description is not None:
            line_item_properties["description"] = str(description)
        if formato is not None:
            line_item_properties["formato"] = str(formato)
        if marca is not None:
            line_item_properties["marca_certificado"] = str(marca)

        enforce_line_item_price_rounding(line_item_properties)

        created_line_item = client.crm.line_items.basic_api.create(
            simple_public_object_input_for_create={
                "properties": line_item_properties
            }
        )

        line_item_id = created_line_item.id
        logger.warning(f"Created line item {line_item_id} for product {product_id}")

        # Associate line item with deal using batch API
        try:
            from hubspot.crm.associations import BatchInputPublicAssociation
            from hubspot.crm.associations.models import PublicAssociation

            association = PublicAssociation(
                _from={"id": str(line_item_id)},
                to={"id": deal_id},
                type="line_item_to_deal"
            )
            batch_input = BatchInputPublicAssociation(inputs=[association])

            client.crm.associations.batch_api.create(
                from_object_type="line_items",
                to_object_type="deals",
                batch_input_public_association=batch_input
            )
            logger.warning(f"Successfully associated line item {line_item_id} with deal {deal_id}")
            return line_item_id

        except Exception as assoc_error:
            logger.warning(f"Batch association failed, trying HTTP fallback: {assoc_error}")

            # HTTP fallback for association
            try:
                access_token = client.access_token

                assoc_url = f"https://api.hubapi.com/crm/v4/objects/line_items/{line_item_id}/associations/deals/{deal_id}"
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                assoc_payload = [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 20  # Line Item to Deal association type
                    }
                ]

                response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                if response.ok:
                    logger.warning(f"Successfully associated line item {line_item_id} with deal {deal_id} via HTTP")
                    return line_item_id
                else:
                    logger.error(f"HTTP association failed: {response.status_code} - {response.text}")
                    return None

            except Exception as http_error:
                logger.error(f"Failed to associate line item {line_item_id} with deal {deal_id}: {http_error}")
                return None

    except Exception as e:
        logger.error(f"Error creating line item for product {product_id}: {e}")
        return None

def create_container_line_item(
    client: HubSpot,
    deal_id: str,
    product_id: str,
    sku: str,
    product_price: str,
    quantity: int,
    container_number: int,
    container_config: dict
) -> Optional[str]:
    """
    Create a container line item with custom properties and associate it with a deal.
    """
    try:
        import json
        import copy

        logger.warning(f"Creating container line item for product {product_id} (SKU: {sku}), container #{container_number}")

        # Sanitize container_config to prevent JSON serialization errors
        # Deep copy to avoid modifying the original dict
        sanitized_config = copy.deepcopy(container_config)

        # Sanitize article names to prevent issues with quotes and special characters
        if "articulos" in sanitized_config and isinstance(sanitized_config["articulos"], list):
            for articulo in sanitized_config["articulos"]:
                if "nombre" in articulo and isinstance(articulo["nombre"], str):
                    articulo["nombre"] = sanitize_string_for_json(articulo["nombre"])

        # Regla unificada: contenedores siempre hs_product_type = container y tipo_articulo = transitario
        line_item_properties = {
            "hs_product_id": str(product_id),
            "quantity": str(quantity),
            "price": product_price,
            "name": f"{deal_id}-{container_number:03d}",
            "hs_product_type": "container",
            "tipo_articulo": "transitario",
            "numero_container": str(container_number),  # Sequential container number
            "ocupacion": str(sanitized_config.get("ocupacion", 0)),  # Occupancy percentage from config
            "peso_neto": str(sanitized_config.get("pesoNeto", 0)),  # Net weight in kg from config
            "container_config": json.dumps(sanitized_config, ensure_ascii=False),  # JSON string of sanitized config
            "hs_currency": "EUR"
        }

        enforce_line_item_price_rounding(line_item_properties)

        created_line_item = client.crm.line_items.basic_api.create(
            simple_public_object_input_for_create={
                "properties": line_item_properties
            }
        )

        line_item_id = created_line_item.id
        logger.warning(f"Created container line item {line_item_id} for product {product_id}")

        # Associate line item with deal using batch API
        try:
            from hubspot.crm.associations import BatchInputPublicAssociation
            from hubspot.crm.associations.models import PublicAssociation

            association = PublicAssociation(
                _from={"id": str(line_item_id)},
                to={"id": deal_id},
                type="line_item_to_deal"
            )
            batch_input = BatchInputPublicAssociation(inputs=[association])

            client.crm.associations.batch_api.create(
                from_object_type="line_items",
                to_object_type="deals",
                batch_input_public_association=batch_input
            )
            logger.warning(f"Successfully associated container line item {line_item_id} with deal {deal_id}")
            return line_item_id

        except Exception as assoc_error:
            logger.warning(f"Batch association failed, trying HTTP fallback: {assoc_error}")

            # HTTP fallback for association
            try:
                access_token = client.access_token

                assoc_url = f"https://api.hubapi.com/crm/v4/objects/line_items/{line_item_id}/associations/deals/{deal_id}"
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                assoc_payload = [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 20  # Line Item to Deal association type
                    }
                ]

                response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                if response.ok:
                    logger.warning(f"Successfully associated container line item {line_item_id} with deal {deal_id} via HTTP")
                    return line_item_id
                else:
                    logger.error(f"HTTP association failed: {response.status_code} - {response.text}")
                    return None

            except Exception as http_error:
                logger.error(f"Failed to associate container line item {line_item_id} with deal {deal_id}: {http_error}")
                return None

    except Exception as e:
        logger.error(f"Error creating container line item for product {product_id}: {e}")
        return None

def create_article_line_item(
    client: HubSpot,
    deal_id: str,
    product_id: str,
    sku: str,
    product_name: str,
    product_price: str,
    quantity: int,
    container_number: int
) -> Optional[str]:
    """
    Create an article line item associated with a specific container.
    """
    try:
        logger.warning(f"Creating article line item for product {product_id} (SKU: {sku}), quantity={quantity}, container #{container_number}")

        # Create line item properties; set tipo_articulo and hs_product_type for correct classification
        # Name format: "DEAL_ID-XXX - Product Name"
        line_item_properties = {
            "hs_product_id": str(product_id),
            "quantity": str(quantity),
            "price": product_price,
            "name": f"{deal_id}-{container_number:03d} - {product_name}",
            "tipo_articulo": "fabricante",
            "hs_product_type": "Articulo",
            "numero_container": str(container_number),
            "hs_currency": "EUR"
        }

        enforce_line_item_price_rounding(line_item_properties)

        created_line_item = client.crm.line_items.basic_api.create(
            simple_public_object_input_for_create={
                "properties": line_item_properties
            }
        )

        line_item_id = created_line_item.id
        logger.warning(f"Created article line item {line_item_id} for product {product_id}")

        # Associate line item with deal using batch API
        try:
            from hubspot.crm.associations import BatchInputPublicAssociation
            from hubspot.crm.associations.models import PublicAssociation

            association = PublicAssociation(
                _from={"id": str(line_item_id)},
                to={"id": deal_id},
                type="line_item_to_deal"
            )
            batch_input = BatchInputPublicAssociation(inputs=[association])

            client.crm.associations.batch_api.create(
                from_object_type="line_items",
                to_object_type="deals",
                batch_input_public_association=batch_input
            )
            logger.warning(f"Successfully associated article line item {line_item_id} with deal {deal_id}")
            return line_item_id

        except Exception as assoc_error:
            logger.warning(f"Batch association failed, trying HTTP fallback: {assoc_error}")

            # HTTP fallback for association
            try:
                access_token = client.access_token

                assoc_url = f"https://api.hubapi.com/crm/v4/objects/line_items/{line_item_id}/associations/deals/{deal_id}"
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                assoc_payload = [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 20  # Line Item to Deal association type
                    }
                ]

                response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                if response.ok:
                    logger.warning(f"Successfully associated article line item {line_item_id} with deal {deal_id} via HTTP")
                    return line_item_id
                else:
                    logger.error(f"HTTP association failed: {response.status_code} - {response.text}")
                    return None

            except Exception as http_error:
                logger.error(f"Failed to associate article line item {line_item_id} with deal {deal_id}: {http_error}")
                return None

    except Exception as e:
        logger.error(f"Error creating article line item for product {product_id}: {e}")
        return None

VALID_PREPRINTS_LANGUAGES = ("ES", "EN", "FR")


def determine_preprints_language(
    deal_language: Optional[str],
    contact_data: Optional[ContactData],
    company_data: Optional[CompanyData]
) -> str:
    """Idioma para imprimir documentos. Precedencia: deal.preprints_language → contact.hs_language → company.langcode → "ES". Sólo ES/EN/FR son válidos."""
    if deal_language:
        normalized = deal_language.strip().upper()
        if normalized in VALID_PREPRINTS_LANGUAGES:
            return normalized

    if contact_data and contact_data.hs_language:
        normalized = contact_data.hs_language.strip().upper()
        if normalized in VALID_PREPRINTS_LANGUAGES:
            return normalized

    if company_data and company_data.langcode:
        normalized = company_data.langcode.strip().upper()
        if normalized in VALID_PREPRINTS_LANGUAGES:
            return normalized

    return "ES"

def determine_proyecto(
    contact_data: Optional[ContactData],
    company_data: Optional[CompanyData]
) -> Optional[str]:
    """Proyecto/canal de origen del lead. Precedencia: contact.proyecto__canal_de_origen_ → company.project."""
    if contact_data and contact_data.proyecto__canal_de_origen_:
        return contact_data.proyecto__canal_de_origen_

    if company_data and company_data.project:
        return company_data.project

    return None

def get_property_options(property_name: str, object_type: str = "deals") -> List[PropertyOption]:
    """Devuelve las opciones (label, value) de una propiedad SELECT/RADIO de HubSpot."""
    try:
        client = hubspot_client.get_client()

        property_info = client.crm.properties.core_api.get_by_name(
            object_type=object_type,
            property_name=property_name
        )

        if hasattr(property_info, 'options') and property_info.options:
            return [
                PropertyOption(
                    label=option.label,
                    value=option.value
                )
                for option in property_info.options
            ]
        else:
            logger.warning(f"No options found for property {property_name} in {object_type}")
            return []

    except Exception as e:
        logger.error(f"Error getting property options for {property_name}: {e}")
        return []

@router.get("/{deal_id}/client", response_model=ClientDataResponse)
async def get_deal_client_data(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve los datos cliente del deal: contact (readonly), company y propiedades del deal usadas en ClienteTab.

    Las propiedades de documentación están en el endpoint GET /{deal_id}/documentacion.
    """
    try:

        client = hubspot_client.get_client()

        deal_properties = [
            'tipo_de_venta',
            'tipo_contenedor',
            'carga_por_palets',
            'margen_cliente',
            'dias_libres',
            'naviera',
            'preprints_language',
            'project',
            'codigo_intermediario',
            'repercutir_a_cliente',
            'tipo_comision',
            'importe_comision',
            'fecha_caducidad_cotizacion',
            'tipo_iva_comision',
            'tipo_iva_comision_soportado'
        ]

        logger.warning(f"Fetching deal {deal_id} with properties: {deal_properties}")
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties,
            associations=['contacts', 'companies']
        )

        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Define all properties to fetch upfront
        all_properties_to_fetch = [
            ('country', 'companies'),
            ('pais_de_operaciones', 'companies'),
            ('tipo_cliente', 'companies'),
            ('tipo_de_venta', 'deals'),
            ('tipo_contenedor', 'deals'),
            ('naviera', 'deals'),
            ('preprints_language', 'deals'),
            ('project', 'deals'),
            ('repercutir_a_cliente', 'deals'),
            ('tipo_comision', 'deals'),
        ]

        property_options_cache = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_property = {
                executor.submit(get_property_options, prop_name, obj_type): (prop_name, obj_type)
                for prop_name, obj_type in all_properties_to_fetch
            }

            for future in as_completed(future_to_property):
                prop_name, obj_type = future_to_property[future]
                try:
                    property_options_cache[prop_name] = future.result()
                except Exception as e:
                    logger.error(f"Error fetching options for {prop_name}: {e}")
                    property_options_cache[prop_name] = []

        contact_data = None

        try:
            contact_ids = get_associated_contacts(deal_id)

            if contact_ids:
                contact_id = contact_ids[0]  # Get primary contact
                logger.warning(f"Found associated contact: {contact_id}")

                contact = client.crm.contacts.basic_api.get_by_id(
                    contact_id,
                    properties=['hs_language', 'proyecto__canal_de_origen_']
                )

                contact_data = ContactData(
                    hs_language=contact.properties.get('hs_language'),
                    proyecto__canal_de_origen_=contact.properties.get('proyecto__canal_de_origen_')
                )
            else:
                logger.warning(f"No contacts associated with deal {deal_id}")

        except Exception as contact_error:
            logger.warning(f"Error fetching contact data: {contact_error}")

        company_data = None

        try:
            company_ids = get_associated_companies(deal_id)

            if company_ids:
                company_id = company_ids[0]  # Get primary company
                logger.warning(f"Found associated company: {company_id}")

                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=['name', 'pais_de_operaciones', 'country_entrega', 'country', 'langcode', 'project', 'residennum', 'cif', 'address', 'tipo_cliente']
                )

                # Get pais_de_operaciones label from options (using cache)
                pais_de_operaciones_value = company.properties.get('pais_de_operaciones')
                pais_de_operaciones_label = pais_de_operaciones_value
                if pais_de_operaciones_value:
                    pais_options = property_options_cache.get('pais_de_operaciones', [])
                    for option in pais_options:
                        if option.value == pais_de_operaciones_value:
                            pais_de_operaciones_label = option.label
                            break

                # Get tipo_cliente with options (using cache)
                tipo_cliente_value = company.properties.get('tipo_cliente')
                tipo_cliente_options = property_options_cache.get('tipo_cliente', [])
                tipo_cliente_property = PropertyWithOptions(
                    value=tipo_cliente_value,
                    options=tipo_cliente_options
                ) if tipo_cliente_options else None

                # Get country label from options (using cache)
                country_value = company.properties.get('country')
                country_label = country_value
                if country_value:
                    country_options = property_options_cache.get('country', [])
                    for option in country_options:
                        if option.value == country_value:
                            country_label = option.label
                            break

                company_data = CompanyData(
                    name=company.properties.get('name'),
                    pais_de_operaciones=pais_de_operaciones_label,  # Use label instead of value
                    country_entrega=company.properties.get('country_entrega'),
                    country=country_label,  # Use label instead of value
                    langcode=company.properties.get('langcode'),
                    project=company.properties.get('project'),
                    residennum=company.properties.get('residennum'),
                    cif=company.properties.get('cif'),
                    address=company.properties.get('address'),
                    tipo_cliente=tipo_cliente_property
                )
            else:
                logger.warning(f"No companies associated with deal {deal_id}")

        except Exception as company_error:
            logger.warning(f"Error fetching company data: {company_error}")

        tipo_venta_value = deal.properties.get('tipo_de_venta')
        tipo_contenedor_value = deal.properties.get('tipo_contenedor')
        naviera_value = deal.properties.get('naviera')
        preprints_language_value = deal.properties.get('preprints_language')
        deal_project_value = deal.properties.get('project')

        # Convert string values to appropriate types using helper
        margen_cliente_raw = deal.properties.get('margen_cliente')
        logger.warning(f"margen_cliente RAW from HubSpot: {margen_cliente_raw} (type: {type(margen_cliente_raw)})")
        margen_cliente = safe_convert(margen_cliente_raw, float)
        logger.warning(f"margen_cliente after safe_convert (final value): {margen_cliente}")
        # HubSpot stores margen_cliente as percentage already (12 for 12%), no conversion needed
        dias_libres = safe_convert(deal.properties.get('dias_libres'), int)

        # Determine language for document printing
        determined_language = determine_preprints_language(
            preprints_language_value,
            contact_data,
            company_data
        )

        # Implement fallback logic for project: deal.project -> company.project
        company_project_value = company_data.project if company_data else None
        final_project_value = deal_project_value or company_project_value

        # Get property options from cache (already fetched in parallel at Step 1.5)
        tipo_venta_options = property_options_cache.get('tipo_de_venta', [])
        tipo_contenedor_options = property_options_cache.get('tipo_contenedor', [])
        naviera_options = property_options_cache.get('naviera', [])
        preprints_language_options = property_options_cache.get('preprints_language', [])
        project_options = property_options_cache.get('project', [])
        repercutir_a_cliente_options = property_options_cache.get('repercutir_a_cliente', [])
        tipo_comision_options = property_options_cache.get('tipo_comision', [])

        # Priority 1: Deal fields (if already saved)
        # Priority 2: Commission provider data (if associated)
        codigo_intermediario_value = deal.properties.get('codigo_intermediario')
        repercutir_a_cliente_value = deal.properties.get('repercutir_a_cliente')
        tipo_comision_value = deal.properties.get('tipo_comision')
        importe_comision_value = safe_convert(deal.properties.get('importe_comision'), float)

        # Also fetch provider name and id if codigo_intermediario exists
        commission_provider_info = None

        try:
            CUSTOM_OBJECT_PROVEEDORES = "2-43108800"

            # If codigo_intermediario exists, search for the provider by cardcode
            if codigo_intermediario_value:
                logger.info(f"Searching for commission provider with cardcode: {codigo_intermediario_value}")

                # Search provider by cardcode
                filters = [
                    Filter(property_name="cardcode", operator="EQ", value=codigo_intermediario_value),
                    Filter(property_name="tipo_proveedor", operator="EQ", value="comisiones")
                ]
                filter_group = FilterGroup(filters=filters)
                search_request = PublicObjectSearchRequest(
                    filter_groups=[filter_group],
                    properties=["name", "cardcode", "tipo_proveedor", "repercutir_a_cliente", "tipo_comision", "importe_comision"],
                    limit=1
                )

                search_results = client.crm.objects.search_api.do_search(
                    object_type=CUSTOM_OBJECT_PROVEEDORES,
                    public_object_search_request=search_request
                )

                if search_results.results:
                    provider = search_results.results[0]
                    props = provider.properties
                    commission_provider_info = {
                        "id": provider.id,
                        "name": props.get("name", ""),
                        "cardcode": props.get("cardcode", ""),
                        "repercutir_a_cliente": props.get("repercutir_a_cliente", ""),
                        "tipo_comision": props.get("tipo_comision", ""),
                        "importe_comision": safe_convert(props.get("importe_comision"), float) or 0.0
                    }
                    logger.info(f"Found commission provider by cardcode: {commission_provider_info['name']}")

            # Fallback: If deal fields are not set, try to get from associated commission provider
            if not codigo_intermediario_value or not repercutir_a_cliente_value or not tipo_comision_value or importe_comision_value is None:
                provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                # BATCH READ: Search for a provider with tipo_proveedor = "comision"
                commission_provider_data = None

                if provider_ids:
                    access_token = client.access_token
                    batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

                    batch_payload = {
                        "properties": ["name", "cardcode", "tipo_proveedor", "repercutir_a_cliente", "tipo_comision", "importe_comision"],
                        "inputs": [{"id": str(pid)} for pid in provider_ids]
                    }

                    try:
                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            batch_data = batch_response.json()

                            # Find first commission provider
                            for provider in batch_data.get("results", []):
                                props = provider.get("properties", {}) or {}

                                # Check if this is a commission provider
                                if props.get("tipo_proveedor") == "comisiones":
                                    prov_id = provider.get("id")
                                    commission_provider_data = props
                                    commission_provider_info = {
                                        "id": prov_id,
                                        "name": props.get("name", ""),
                                        "cardcode": props.get("cardcode", ""),
                                        "repercutir_a_cliente": props.get("repercutir_a_cliente", ""),
                                        "tipo_comision": props.get("tipo_comision", ""),
                                        "importe_comision": safe_convert(props.get("importe_comision"), float) or 0.0
                                    }
                                    logger.info(f"Found commission provider {prov_id} for deal {deal_id}")
                                    break
                        else:
                            logger.error(f"Batch read providers failed: {batch_response.status_code}")
                    except Exception as e:
                        logger.warning(f"Error in batch read providers: {e}")

                # Preload values from commission provider if deal fields are empty
                if commission_provider_data:
                    if not codigo_intermediario_value:
                        codigo_intermediario_value = commission_provider_data.get('cardcode')
                    if not repercutir_a_cliente_value:
                        repercutir_a_cliente_value = commission_provider_data.get('repercutir_a_cliente')
                    if not tipo_comision_value:
                        tipo_comision_value = commission_provider_data.get('tipo_comision')
                    if importe_comision_value is None:
                        importe_comision_value = safe_convert(commission_provider_data.get('importe_comision'), float)

        except Exception as e:
            logger.warning(f"Error fetching commission provider for deal {deal_id}: {e}")

        # Resolve IVA defaults from comisiones product (Fase 4E)
        iva_comision_defaults = None
        residennum = company_data.residennum if company_data else None
        try:
            comision_search = client.crm.products.search_api.do_search(
                public_object_search_request={
                    "filterGroups": [{"filters": [{"propertyName": "tipo_articulo", "operator": "EQ", "value": "comisiones"}]}],
                    "properties": [
                        "name", "tipo_iva_soportado", "tipo_iva_repercutido",
                        "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                        "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex"
                    ],
                    "limit": 1
                }
            )
            if comision_search.results:
                comision_props = comision_search.results[0].properties
                iva_comision_defaults = {
                    "repercutido": resolve_product_iva(comision_props, residennum, "repercutido"),
                    "soportado": resolve_product_iva(comision_props, residennum, "soportado")
                }
                logger.info(f"[client-data] iva_comision_defaults resolved: {iva_comision_defaults}")
        except Exception as e:
            logger.warning(f"[client-data] Error resolving iva_comision_defaults: {e}")

        deal_client_data = DealClientData(
            tipo_de_venta=PropertyWithOptions(
                value=tipo_venta_value,
                options=tipo_venta_options
            ),
            tipo_contenedor=PropertyWithOptions(
                value=tipo_contenedor_value,
                options=tipo_contenedor_options
            ),
            margen_cliente=margen_cliente,
            dias_libres=dias_libres,
            naviera=PropertyWithOptions(
                value=naviera_value,
                options=naviera_options
            ),
            preprints_language=PropertyWithOptions(
                value=determined_language,
                options=preprints_language_options
            ),
            project=PropertyWithOptions(
                value=final_project_value,
                options=project_options
            ),
            codigo_intermediario=codigo_intermediario_value,
            repercutir_a_cliente=PropertyWithOptions(
                value=repercutir_a_cliente_value,
                options=repercutir_a_cliente_options
            ),
            tipo_comision=PropertyWithOptions(
                value=tipo_comision_value,
                options=tipo_comision_options
            ),
            importe_comision=importe_comision_value,
            carga_por_palets=safe_convert(deal.properties.get('carga_por_palets'), bool),
            fecha_caducidad_cotizacion=parse_fecha_caducidad(deal.properties.get('fecha_caducidad_cotizacion')),
            tipo_iva_comision=deal.properties.get('tipo_iva_comision') or None,
            tipo_iva_comision_soportado=deal.properties.get('tipo_iva_comision_soportado') or None,
            commission_provider_info=commission_provider_info,
            iva_comision_defaults=iva_comision_defaults
        )

        clasificacion = None
        if company_data and company_data.tipo_cliente:
            # Get the label for clasificacion (to match frontend expectations)
            tipo_cliente_value = company_data.tipo_cliente.value
            if tipo_cliente_value:
                for option in company_data.tipo_cliente.options:
                    if option.value == tipo_cliente_value:
                        clasificacion = option.label
                        break

        proyecto = determine_proyecto(contact_data, company_data)

        return ClientDataResponse(
            success=True,
            company=company_data,
            deal=deal_client_data,
            clasificacion=clasificacion,
            proyecto=proyecto
        )

    except Exception as e:
        logger.error(f"Error in get_deal_client_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deal_id}/client", response_model=BaseResponse)
async def update_deal_client_data(
    data: UpdateClientDataRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Actualiza el cliente del deal desde ClienteTab: company.tipo_cliente y deal.{tipo_de_venta, tipo_contenedor, margen_cliente}. Sólo se aplican los campos no-null. country (contact) es readonly y dias_libres se gestiona en TransitarioTab."""
    try:
        client = hubspot_client.get_client()

        if data.contact is not None:
            try:
                contact_ids = get_associated_contacts(deal_id)

                if contact_ids:
                    contact_id = contact_ids[0]  # Get primary contact
                    logger.warning(f"Updating contact {contact_id} for deal {deal_id}")

                    contact_properties = build_update_properties(data.contact, UpdateContactData)

                    if contact_properties:
                        client.crm.contacts.basic_api.update(
                            contact_id=contact_id,
                            simple_public_object_input={
                                "properties": contact_properties
                            }
                        )
                        logger.warning(f"Contact {contact_id} updated successfully")
                else:
                    logger.warning(f"No contacts associated with deal {deal_id}, skipping contact update")

            except Exception as contact_error:
                logger.error(f"Error updating contact data: {contact_error}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error updating contact: {str(contact_error)}")

        if data.company is not None:
            try:
                company_ids = get_associated_companies(deal_id)

                if company_ids:
                    company_id = company_ids[0]  # Get primary company
                    logger.warning(f"Updating company {company_id} for deal {deal_id}")

                    company_properties = build_update_properties(data.company, UpdateCompanyData)

                    if company_properties:
                        client.crm.companies.basic_api.update(
                            company_id=company_id,
                            simple_public_object_input={
                                "properties": company_properties
                            }
                        )
                        logger.warning(f"Company {company_id} updated successfully with properties: {company_properties}")
                else:
                    logger.warning(f"No companies associated with deal {deal_id}, skipping company update")

            except Exception as company_error:
                logger.error(f"Error updating company data: {company_error}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error updating company: {str(company_error)}")

        if data.deal is not None:
            deal_properties = build_update_properties(data.deal, UpdateDealData)

            if deal_properties:
                logger.warning(f"Updating deal {deal_id} with properties: {deal_properties}")
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={
                        "properties": deal_properties
                    }
                )
                logger.warning(f"Deal {deal_id} updated successfully")

            if data.deal.codigo_intermediario is not None:
                codigo_intermediario = data.deal.codigo_intermediario.strip()

                try:
                    CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
                    access_token = client.access_token
                    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

                    if codigo_intermediario:
                        # Search for provider with tipo_proveedor="comisiones" and matching cardcode
                        logger.info(f"[client] Searching for commission provider with cardcode: {codigo_intermediario}")

                        search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/search"
                        search_payload = {
                            "filterGroups": [{
                                "filters": [
                                    {"propertyName": "cardcode", "operator": "EQ", "value": codigo_intermediario},
                                    {"propertyName": "tipo_proveedor", "operator": "EQ", "value": "comisiones"}
                                ]
                            }],
                            "properties": ["name", "cardcode", "tipo_proveedor"],
                            "limit": 1
                        }

                        search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

                        if search_response.ok:
                            search_data = search_response.json()
                            if search_data.get("results"):
                                provider = search_data["results"][0]
                                provider_id = provider["id"]
                                provider_name = provider["properties"].get("name", "")

                                logger.info(f"[client] Found commission provider: id={provider_id}, name={provider_name}")

                                existing_provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                                # Remove all existing commission provider associations
                                if existing_provider_ids:
                                    batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                                    batch_payload = {
                                        "properties": ["tipo_proveedor"],
                                        "inputs": [{"id": str(pid)} for pid in existing_provider_ids]
                                    }

                                    try:
                                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                                        if batch_response.ok:
                                            for prov in batch_response.json().get("results", []):
                                                if prov.get("properties", {}).get("tipo_proveedor") == "comisiones":
                                                    prov_id = prov.get("id")
                                                    # Remove association
                                                    delete_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{prov_id}/associations/deals/{deal_id}"
                                                    requests.delete(delete_url, headers=headers, timeout=10)
                                                    logger.info(f"[client] Removed existing commission provider association: {prov_id}")
                                    except Exception as e:
                                        logger.error(f"[client] Error removing existing commission providers: {e}")

                                # Create new association (associationTypeId: 430 = "Intermediario")
                                assoc_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{provider_id}/associations/deals/{deal_id}"
                                assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 430}]

                                assoc_response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                                if assoc_response.ok:
                                    logger.info(f"[client] Successfully associated commission provider {provider_id} with deal {deal_id} (associationTypeId: 430 - Intermediario)")
                                else:
                                    logger.error(f"[client] Failed to associate provider: {assoc_response.status_code} - {assoc_response.text}")

                            else:
                                logger.warning(f"[client] No commission provider found with cardcode={codigo_intermediario}")
                        else:
                            logger.error(f"[client] Search for commission provider failed: {search_response.status_code}")

                    else:
                        # codigo_intermediario is empty, remove all commission provider associations
                        logger.info(f"[client] codigo_intermediario is empty, removing all commission provider associations")

                        existing_provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                        if existing_provider_ids:
                            batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                            batch_payload = {
                                "properties": ["tipo_proveedor"],
                                "inputs": [{"id": str(pid)} for pid in existing_provider_ids]
                            }

                            try:
                                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                                if batch_response.ok:
                                    for prov in batch_response.json().get("results", []):
                                        if prov.get("properties", {}).get("tipo_proveedor") == "comisiones":
                                            prov_id = prov.get("id")
                                            # Remove association
                                            delete_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{prov_id}/associations/deals/{deal_id}"
                                            requests.delete(delete_url, headers=headers, timeout=10)
                                            logger.info(f"[client] Removed commission provider association: {prov_id}")
                            except Exception as e:
                                logger.error(f"[client] Error removing commission providers: {e}")

                except Exception as e:
                    logger.error(f"[client] Error managing commission provider association: {e}")

        return BaseResponse(
            success=True,
            error=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_client_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _get_empresas_asociadas_ids(company_id: str) -> List[str]:
    """IDs de empresas asociadas a `company_id` por una asociación USER_DEFINED
    empresa↔empresa (etiqueta "Proveedor-Cliente", typeId 711, en cualquier
    sentido). Filtra por category USER_DEFINED para excluir la jerarquía
    matriz/filial estándar de HubSpot (HUBSPOT_DEFINED). Pagina."""
    client = hubspot_client.get_client()
    ids: List[str] = []
    seen = set()
    after = None
    page = 0

    while True:
        page += 1
        kwargs = {
            "object_type": "companies",
            "object_id": company_id,
            "to_object_type": "companies",
            "limit": 100,
        }
        if after:
            kwargs["after"] = after

        try:
            resp = client.crm.associations.v4.basic_api.get_page(**kwargs)
        except Exception as e:
            logger.warning(f"[empresas_asociadas] error company={company_id} página={page}: {e}")
            break

        for assoc in (resp.results or []):
            cid = str(assoc.to_object_id)
            if cid in seen:
                continue
            es_user_defined = any(
                getattr(at, "category", None) == ASSOC_CATEGORY_USER_DEFINED
                for at in (assoc.association_types or [])
            )
            if es_user_defined:
                seen.add(cid)
                ids.append(cid)

        if resp.paging and resp.paging.next:
            after = resp.paging.next.after
        else:
            break

    return ids


@router.get("/{deal_id}/empresas-asociadas", response_model=EmpresasAsociadasResponse)
async def get_deal_empresas_asociadas(
    deal_id: str = Path(..., description="HubSpot deal ID", example="46298478914")
):
    """Empresas asociadas a la empresa del negocio por la relación "Proveedor-Cliente",
    con su dirección de entrega. Alimenta el selector de autorrelleno de la
    "Dirección de Entrega" en DocumentacionTab."""
    try:
        client = hubspot_client.get_client()

        company_ids = get_associated_companies(deal_id)
        if not company_ids:
            return EmpresasAsociadasResponse(success=True, empresas=[])

        ids = _get_empresas_asociadas_ids(company_ids[0])
        if not ids:
            return EmpresasAsociadasResponse(success=True, empresas=[])

        empresas = []
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            batch_response = client.crm.companies.batch_api.read(
                batch_read_input_simple_public_object_id={
                    "properties": ["name", COMPANY_PROP_ADDRESS_ENTREGA],
                    "inputs": [{"id": cid} for cid in chunk],
                }
            )
            for result in (batch_response.results or []):
                props = result.properties or {}
                empresas.append(EmpresaAsociadaItem(
                    id=str(result.id),
                    name=props.get("name") or None,
                    address_entrega=props.get(COMPANY_PROP_ADDRESS_ENTREGA) or None,
                ))

        empresas.sort(key=lambda e: (e.name or "").lower())
        return EmpresasAsociadasResponse(success=True, empresas=empresas)

    except Exception as e:
        logger.error(f"Error in get_deal_empresas_asociadas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/documentacion", response_model=DealDocumentacionResponse)
async def get_deal_documentacion_data(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve los datos de documentación del deal para DocumentacionTab (idioma, dirección fiscal, documentación logística, certificado de origen, método de pago y textos generados)."""
    try:
        client = hubspot_client.get_client()

        deal_properties = [
            'preprints_language',
            'direccion_fiscal',
            'direccion_de_entrega',
            'desagregar_cotizacion',
            'documentos_a_generar',
            'texto_en_bl',
            'comentarios_logistica',
            'comentarios_transitario',
            'consignee',
            'shipper',
            'notify',
            'certificado_origen_mercancia',
            'observaciones',
            'metodo_pago_plantilla',
            'banco_cobro',
            'cotizacion_rapida',
            'texto_adicional_metodo_pago',
            'texto_banco',
            'texto_forma_pago',
            'texto_base_oferta',
            'cif',
            'referencia_cotizacion'
        ]

        logger.warning(f"Fetching documentacion for deal {deal_id}")
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties,
            associations=['companies']
        )

        # Fetch property options for select fields
        from concurrent.futures import ThreadPoolExecutor, as_completed

        properties_to_fetch = [
            ('preprints_language', 'deals'),
            ('documentos_a_generar', 'deals'),
            ('metodo_pago_plantilla', 'deals'),
            ('banco_cobro', 'deals'),
        ]

        property_options_cache = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_property = {
                executor.submit(get_property_options, prop_name, obj_type): (prop_name, obj_type)
                for prop_name, obj_type in properties_to_fetch
            }

            for future in as_completed(future_to_property):
                prop_name, obj_type = future_to_property[future]
                try:
                    property_options_cache[prop_name] = future.result()
                except Exception as e:
                    logger.error(f"Error fetching options for {prop_name}: {e}")
                    property_options_cache[prop_name] = []

        # langcode (idioma fallback).
        company_address = None
        company_address_entrega = None
        company_cif = None
        company_langcode = None
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=['address', 'cif', 'langcode', COMPANY_PROP_ADDRESS_ENTREGA]
                )
                company_address = company.properties.get('address')
                company_address_entrega = company.properties.get(COMPANY_PROP_ADDRESS_ENTREGA)
                company_cif = company.properties.get('cif')
                company_langcode = company.properties.get('langcode')
        except Exception as e:
            logger.warning(f"Error fetching company for fallback fields: {e}")

        # Contact hs_language como segundo fallback para preprints_language.
        contact_hs_language = None
        try:
            contact_ids = get_associated_contacts(deal_id)
            if contact_ids:
                contact = client.crm.contacts.basic_api.get_by_id(
                    contact_ids[0],
                    properties=['hs_language']
                )
                contact_hs_language = contact.properties.get('hs_language')
        except Exception as e:
            logger.warning(f"Error fetching contact for hs_language fallback: {e}")

        # Extract values
        direccion_fiscal_value = deal.properties.get('direccion_fiscal')
        if not direccion_fiscal_value and company_address:
            direccion_fiscal_value = company_address

        # Precarga: si el deal no tiene dirección de entrega propia, se muestra la
        # de la empresa de la cotización (se persiste al guardar el tab).
        direccion_entrega_value = deal.properties.get('direccion_de_entrega') or company_address_entrega or None

        cif_value = deal.properties.get('cif') or company_cif or None

        resolved_preprints_language = determine_preprints_language(
            deal.properties.get('preprints_language'),
            ContactData(hs_language=contact_hs_language) if contact_hs_language else None,
            CompanyData(langcode=company_langcode) if company_langcode else None,
        )

        # Build response
        deal_documentacion_data = DealDocumentacionData(
            # Idioma global — alineado con /client: precedencia deal → contact → company → "ES".
            preprints_language=PropertyWithOptions(
                value=resolved_preprints_language,
                options=property_options_cache.get('preprints_language', [])
            ) if property_options_cache.get('preprints_language') else None,
            # Documentación
            direccion_fiscal=direccion_fiscal_value,
            direccion_de_entrega=direccion_entrega_value,
            # Documentación Logística
            desagregar_cotizacion=safe_convert(deal.properties.get('desagregar_cotizacion'), bool),
            documentos_a_generar=PropertyWithOptions(
                value=deal.properties.get('documentos_a_generar'),
                options=property_options_cache.get('documentos_a_generar', [])
            ) if property_options_cache.get('documentos_a_generar') else None,
            texto_en_bl=deal.properties.get('texto_en_bl'),
            comentarios_logistica=deal.properties.get('comentarios_logistica'),
            comentarios_transitario=deal.properties.get('comentarios_transitario'),
            # Certificado de Origen
            consignee=deal.properties.get('consignee'),
            shipper=deal.properties.get('shipper'),
            notify=deal.properties.get('notify'),
            certificado_origen_mercancia=deal.properties.get('certificado_origen_mercancia'),
            observaciones=deal.properties.get('observaciones'),
            # Método de Pago & Textos
            metodo_pago_plantilla=PropertyWithOptions(
                value=deal.properties.get('metodo_pago_plantilla'),
                options=property_options_cache.get('metodo_pago_plantilla', [])
            ) if property_options_cache.get('metodo_pago_plantilla') else None,
            banco_cobro=PropertyWithOptions(
                value=deal.properties.get('banco_cobro'),
                options=property_options_cache.get('banco_cobro', [])
            ) if property_options_cache.get('banco_cobro') else None,
            cotizacion_rapida=safe_convert(deal.properties.get('cotizacion_rapida'), bool),
            texto_adicional_metodo_pago=deal.properties.get('texto_adicional_metodo_pago'),
            # Textos Generados
            texto_banco=deal.properties.get('texto_banco'),
            texto_forma_pago=deal.properties.get('texto_forma_pago'),
            texto_base_oferta=deal.properties.get('texto_base_oferta'),
            # Identificación y Referencia
            cif=cif_value,
            referencia_cotizacion=deal.properties.get('referencia_cotizacion')
        )

        return DealDocumentacionResponse(
            success=True,
            deal=deal_documentacion_data
        )

    except Exception as e:
        logger.error(f"Error in get_deal_documentacion_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deal_id}/documentacion", response_model=BaseResponse)
async def update_deal_documentacion_data(
    data: UpdateDocumentacionRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Actualiza los campos de documentación del deal desde DocumentacionTab. Los null limpian la prop."""
    try:
        client = hubspot_client.get_client()

        if data.deal is not None:
            deal_properties = build_update_properties(data.deal, UpdateDocumentacionData)

            if deal_properties:
                logger.warning(f"Updating deal {deal_id} with documentacion properties: {deal_properties}")
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={
                        "properties": deal_properties
                    }
                )
                logger.warning(f"Deal {deal_id} documentacion updated successfully")

                # Propaga documentos_a_generar a los contenedores ya existentes
                # para mantenerlos en sync con el deal (su valor se fija al
                # crearlos). Best-effort: el deal ya quedó guardado, así que un
                # fallo aquí no debe tumbar la respuesta.
                if "documentos_a_generar" in deal_properties:
                    try:
                        valor = deal_properties["documentos_a_generar"]
                        contenedores = get_contenedores_by_deal(
                            client, deal_id, properties=["nombre"]
                        )
                        if contenedores:
                            items = [
                                {
                                    "id": c["id"],
                                    "properties": {"documentos_a_generar": valor},
                                }
                                for c in contenedores
                            ]
                            actualizados = batch_update_custom_objects(
                                client, OBJECT_TYPE_CONTENEDOR, items
                            )
                            logger.info(
                                f"[documentacion] documentos_a_generar propagado a "
                                f"{actualizados}/{len(contenedores)} contenedores del deal {deal_id}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[documentacion] no se pudo propagar documentos_a_generar "
                            f"a contenedores del deal {deal_id}: {e}",
                            exc_info=True,
                        )

        return BaseResponse(
            success=True,
            error=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_documentacion_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{deal_id}/articles", response_model=DealArticlesResponse)
async def get_deal_articles(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve dos arrays separados para el deal: artículos con tipo_articulo=fabricante (con cardcode del proveedor habitual) y proveedores con tipo_proveedor=fabricante (deduplicados)."""
    try:
        client = hubspot_client.get_client()

        logger.warning(f"[articles] Fetching articles with tipo_articulo=fabricante for deal {deal_id}")

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=["lugar_carga"]
        )
        lugar_carga_value = deal.properties.get("lugar_carga")
        logger.info(f"[articles] Deal lugar_carga value: {lugar_carga_value}")

        line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[articles] READ deal_id={deal_id} -> line_item_ids count={len(line_item_ids)} ids={line_item_ids[:20]}{'...' if len(line_item_ids) > 20 else ''}")

        # También guardamos el tipo_iva_repercutido de cada line item
        product_ids: list[str] = []
        line_item_tipo_iva: dict[str, str] = {}  # product_id -> tipo_iva_repercutido del line item
        line_item_tipo_iva_sop: dict[str, str] = {}  # product_id -> tipo_iva_soportado del line item

        # Split into batches of 100 (HubSpot limit) - Define here for use throughout the function
        batch_size = 100

        # Prepare authentication headers
        access_token = client.access_token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        if not line_item_ids:
            logger.warning(f"[articles] No line_item_ids for deal {deal_id} -> will return empty articles")
        if line_item_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            all_line_items = []

            for i in range(0, len(line_item_ids), batch_size):
                batch_ids = line_item_ids[i:i + batch_size]

                batch_payload = {
                    "properties": ["hs_product_id", "hs_product_type", "tipo_articulo", "tipo_iva_repercutido", "tipo_iva_soportado"],
                    "inputs": [{"id": str(lid)} for lid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        batch_data = batch_response.json()
                        all_line_items.extend(batch_data.get("results", []))
                    else:
                        logger.error(f"[articles] Batch read line items failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"[articles] Error in batch read line items: {e}")

            logger.warning(f"[articles] Batch fetched {len(all_line_items)} line items")
            for idx, li in enumerate(all_line_items[:5]):
                props = li.get("properties", {})
                logger.warning(f"[articles] line_item[{idx}] id={li.get('id')} hs_product_type={props.get('hs_product_type')!r} tipo_articulo={props.get('tipo_articulo')!r} hs_product_id={props.get('hs_product_id')}")

            # Regla unificada: artículos = line item con hs_product_type = inventory y tipo_articulo = fabricante
            for line_item in all_line_items:
                props = line_item.get("properties", {})
                if not _line_item_is_article(props):
                    continue
                product_id = props.get("hs_product_id")
                if not product_id:
                    continue
                product_id = str(product_id)
                product_ids.append(product_id)
                tipo_iva = props.get("tipo_iva_repercutido")
                if tipo_iva:
                    line_item_tipo_iva[product_id] = tipo_iva
                tipo_iva_sop = props.get("tipo_iva_soportado")
                if tipo_iva_sop:
                    line_item_tipo_iva_sop[product_id] = tipo_iva_sop

            product_ids = list(dict.fromkeys(product_ids))

        logger.warning(f"[articles] After fabricante filter: product_ids count={len(product_ids)} product_ids={product_ids[:15]}{'...' if len(product_ids) > 15 else ''}")

        articles: list[ArticleWithProvider] = []

        if product_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
            all_products = []

            for i in range(0, len(product_ids), batch_size):
                batch_ids = product_ids[i:i + batch_size]

                batch_payload = {
                    "properties": [
                        "name", "name_es", "name_en", "name_fr", "description",
                        "hs_sku", "cardcode", "codigo_proveedor", "peso_neto",
                        # Price fields for negotiation
                        "hs_price_eur", "ultimo_precio_compra", "ultimo_precio_evaluado", "margen_unitario",
                        # VAT types
                        "tipo_iva_repercutido", "tipo_iva_soportado",
                        # Container capacity fields
                        "unidades_cam16", "unidades_d20dr", "unidades_d40dr",
                        "unidades_palet", "unidades_tra80"
                    ],
                    "inputs": [{"id": str(pid)} for pid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        batch_data = batch_response.json()
                        all_products.extend(batch_data.get("results", []))
                    else:
                        logger.error(f"[articles] Batch read products failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"[articles] Error in batch read products: {e}")

            logger.warning(f"[articles] Batch fetched {len(all_products)} products")

            for product in all_products:
                try:
                    product_id = product.get("id")
                    props = product.get("properties", {})

                    product_data = parse_hubspot_properties(product_id, props, ArticleWithProvider)

                    # HubSpot stores margen_unitario as percentage already (12 for 12%), no conversion needed

                    # Override provider fields (set to None initially, will be matched later)
                    product_data["provider_name"] = None
                    product_data["provider_id"] = None

                    # Override tipo_iva_repercutido/soportado with the value from the line item (if exists)
                    if product_id in line_item_tipo_iva:
                        product_data["tipo_iva_repercutido"] = line_item_tipo_iva[product_id]
                        logger.info(f"[articles] Using line item tipo_iva_repercutido for product {product_id}: {line_item_tipo_iva[product_id]}")
                    if product_id in line_item_tipo_iva_sop:
                        product_data["tipo_iva_soportado"] = line_item_tipo_iva_sop[product_id]
                        logger.info(f"[articles] Using line item tipo_iva_soportado for product {product_id}: {line_item_tipo_iva_sop[product_id]}")

                    articles.append(ArticleWithProvider(**product_data))

                except Exception as e:
                    logger.error(f"[articles] Error processing product {product.get('id')}: {e}")

        provider_ids = _get_associated_ids(deal_id, "2-43108800")
        logger.warning(f"[articles] Found {len(provider_ids)} provider associations (pre-filter)")

        providers: list[ProviderSummary] = []
        providers_by_cardcode: dict[str, ProviderSummary] = {}

        lugar_carga_options = {}
        try:
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
                logger.info(f"[articles] Loaded {len(lugar_carga_options)} lugar_de_la_carga_envio options")
        except Exception as e:
            logger.warning(f"[articles] Error fetching lugar_de_la_carga_envio options: {e}")

        # BATCH READ proveedores
        if provider_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/batch/read"
            all_providers = []

            for i in range(0, len(provider_ids), batch_size):
                batch_ids = provider_ids[i:i + batch_size]

                batch_payload = {
                    "properties": ["cardcode", "name", "lugar_de_la_carga_envio", "tipo_proveedor"],
                    "inputs": [{"id": str(pid)} for pid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        batch_data = batch_response.json()
                        all_providers.extend(batch_data.get("results", []))
                    else:
                        logger.error(f"[articles] Batch read providers failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"[articles] Error in batch read providers: {e}")

            logger.warning(f"[articles] Batch fetched {len(all_providers)} providers")

            for provider in all_providers:
                try:
                    prov_id = provider.get("id")
                    props = provider.get("properties", {}) or {}

                    cardcode = props.get("cardcode")
                    name = props.get("name")
                    lugar_value = props.get("lugar_de_la_carga_envio")
                    tipo_proveedor = (props.get("tipo_proveedor") or "").strip().lower()

                    if not cardcode:
                        # si no hay cardcode no podemos deduplicar bien; lo saltamos
                        logger.warning(f"[articles] Provider {prov_id} has no cardcode; skipping")
                        continue

                    # Filtrar: solo incluir si tipo_proveedor == "fabricante"
                    if tipo_proveedor != "fabricante":
                        logger.warning(f"[articles] Skipping provider {cardcode} with tipo_proveedor={tipo_proveedor}")
                        continue

                    lugar_label = lugar_carga_options.get(lugar_value, lugar_value) if lugar_value else lugar_value

                    prov_obj = ProviderSummary(
                        id=str(prov_id),
                        cardcode=cardcode,
                        name=name,
                        lugar_de_la_carga_envio=lugar_label  # Use label instead of value
                    )

                    if cardcode not in providers_by_cardcode:
                        providers_by_cardcode[cardcode] = prov_obj

                except Exception as e:
                    logger.warning(f"[articles] Error processing provider {provider.get('id')}: {e}")

        providers = list(providers_by_cardcode.values())
        logger.warning(f"[articles] Remaining {len(providers)} providers after filtering by tipo_proveedor=fabricante")

        # 4.b) (Opcional) Enriquecer artículos con nombre/id si su cardcode coincide con un proveedor asociado
        if providers:
            prov_index = {p.cardcode: p for p in providers}
            for art in articles:
                if art.provider_cardcode and art.provider_cardcode in prov_index:
                    art.provider_id = prov_index[art.provider_cardcode].id
                    art.provider_name = prov_index[art.provider_cardcode].name

        lugar_carga_property_options: List[PropertyOption] = [
            PropertyOption(value=value, label=label)
            for value, label in lugar_carga_options.items()
        ]

        logger.info(f"[articles] Built {len(lugar_carga_property_options)} lugar_carga options")

        logger.warning(f"[articles] READ returning articles={len(articles)} providers={len(providers)}")
        return DealArticlesResponse(
            success=True,
            error=None,
            articles=articles,
            providers=providers,
            total_articles=len(articles),
            total_providers=len(providers),
            lugar_carga=lugar_carga_value,
            lugar_carga_options=lugar_carga_property_options
        )

    except Exception as e:
        logger.error(f"Error in get_deal_articles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deal_id}/articles", response_model=BaseResponse)
async def update_deal_articles(
    data: UpdateArticlesRequest,
    deal_id: str = Path(..., description="HubSpot deal ID")
):
    """Reemplaza los line items de tipo artículo y los proveedores asociados al deal a partir del payload de ArticlesTab."""

    try:
        client = hubspot_client.get_client()
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        if not isinstance(data, UpdateArticlesRequest):

            if isinstance(data, dict) and "line_items" in data:
                data = UpdateArticlesRequest(**data)

            elif isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                data = UpdateArticlesRequest(**data["data"])

            else:
                raise HTTPException(status_code=400, detail="Invalid payload structure")

        logger.warning(f"[articles] Processing {len(data.line_items)} SKUs and {len(data.providers)} providers")

        deal_almacen = None
        residennum = None

        try:
            deal = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["almacen"]
            )
            deal_almacen = deal.properties.get("almacen")
            if deal_almacen:
                logger.warning(f"[articles] Deal almacen: {deal_almacen}")
        except Exception as e:
            logger.warning(f"[articles] Could not fetch deal almacen: {e}")

        # Ensure deal has EUR as currency so all line items inherit it
        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": {"deal_currency_code": "EUR"}}
            )
        except Exception as e:
            logger.warning(f"[articles] Could not set deal_currency_code: {e}")

        if data.lugar_carga:
            try:
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={"properties": {"lugar_carga": data.lugar_carga}}
                )
                logger.info(f"[articles] Saved lugar_carga '{data.lugar_carga}' to deal {deal_id}")
            except Exception as e:
                logger.warning(f"[articles] Could not save lugar_carga to deal: {e}")

        # Get company residence type to determine IVA
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id=company_id,
                    properties=["residennum"]
                )
                residennum = company.properties.get("residennum")
                logger.warning(f"[articles] Company residence: {residennum or 'NACIONAL'}")
            else:
                logger.warning(f"[articles] No company found, using default IVA")
        except Exception as e:
            logger.warning(f"[articles] Error getting company residence, using default IVA: {e}")

        hs_skus = list({item.id for item in data.line_items})
        cardcodes = list({p.cardcode for p in data.providers})

        logger.warning(f"[articles] Unique SKUs={len(hs_skus)}, unique providers={len(cardcodes)}")

        # hs_sku en el payload contiene el hs_object_id del producto
        # (el campo hs_sku en HubSpot está vacío en productos nuevos)
        def chunk(arr, size):
            it = iter(arr)
            return iter(lambda: list(islice(it, size)), [])

        # Mapa identidad: "hs_sku" (= hs_object_id) → product_id
        sku_to_product_id = {pid: pid for pid in hs_skus}
        logger.warning(f"[products] Using product IDs directly: {hs_skus}")

        cardcode_to_provider_id = {}
        prov_search_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/search"

        logger.warning("[providers] Searching providers in OR batches")

        for batch_cards in chunk(cardcodes, 3):
            filter_groups = [
                {"filters": [{"propertyName": "cardcode", "operator": "EQ", "value": cc}]}
                for cc in batch_cards
            ]

            payload = {
                "filterGroups": filter_groups,
                "properties": ["cardcode", "name"],
                "limit": 100
            }

            resp = requests.post(prov_search_url, headers=headers, json=payload, timeout=15)
            if not resp.ok:
                logger.error(f"[providers] Search failed: {resp.status_code} - {resp.text}")
                continue

            for item in resp.json().get("results", []):
                props = item.get("properties", {})
                cc = props.get("cardcode")
                if not cc:
                    continue
                raw_id = str(item.get("id"))
                provider_id = raw_id.split("-")[-1]
                cardcode_to_provider_id[cc] = provider_id
                logger.warning(f"[providers] cardcode={cc} -> provider={provider_id}")

        # 4+5. SINGLE BATCH READ → DELETE fabricante + containers

        all_line_items = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[cleanup] Total line items associated: {len(all_line_items)}")

        fabricantes_to_delete = []
        containers_to_delete = []

        if all_line_items:
            resultados_clasificar = []
            for i in range(0, len(all_line_items), 100):
                batch_read = {
                    "properties": ["tipo_articulo", "hs_product_type"],
                    "inputs": [{"id": str(li)} for li in all_line_items[i:i + 100]]
                }

                resp = client.crm.line_items.batch_api.read(
                    batch_read_input_simple_public_object_id=batch_read
                )
                resultados_clasificar.extend(resp.results or [])

            for item in resultados_clasificar:
                props = item.properties or {}

                tipo = (props.get("tipo_articulo") or "").strip().lower()
                ptype = (props.get("hs_product_type") or "").strip().lower()

                if tipo == "fabricante":
                    fabricantes_to_delete.append(item.id)

                if _is_container_product_type(ptype):
                    containers_to_delete.append(item.id)

        for i in range(0, len(fabricantes_to_delete), 100):
            chunk = fabricantes_to_delete[i:i + 100]
            try:
                client.crm.line_items.batch_api.archive(
                    batch_input_simple_public_object_id={"inputs": [{"id": fid} for fid in chunk]}
                )
                logger.warning(f"[cleanup] Deleted {len(chunk)} fabricante line items")
            except Exception as e:
                logger.error(f"[cleanup] fabricante batch delete [{i}:{i + len(chunk)}] failed: {e}")

        for i in range(0, len(containers_to_delete), 100):
            chunk = containers_to_delete[i:i + 100]
            try:
                client.crm.line_items.batch_api.archive(
                    batch_input_simple_public_object_id={"inputs": [{"id": cid} for cid in chunk]}
                )
                logger.warning(f"[cleanup] Deleted {len(chunk)} containers")
            except Exception as e:
                logger.error(f"[cleanup] container batch delete [{i}:{i + len(chunk)}] failed: {e}")

        fabricante_ids = _get_providers_by_tipo(deal_id, "fabricante")
        logger.warning(f"[providers-delete] fabricante providers={len(fabricante_ids)}")

        for provider_id in fabricante_ids:
            try:
                delete_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
                resp = requests.delete(delete_url, headers=headers, timeout=10)

                if resp.ok:
                    logger.warning(f"[providers-delete] removed provider {provider_id}")
                else:
                    logger.error(f"[providers-delete] failed {provider_id}: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"[providers-delete] error deleting {provider_id}: {e}")

        product_ids = list(sku_to_product_id.values())

        batch_read = {
            "properties": [
                "name", "hs_sku", "hs_price_eur", "ultimo_precio_compra",
                "ultimo_precio_evaluado", "peso_neto", "peso_bruto", "name_en", "name_fr",
                "description",
                "formato", "marca_certificado",
                "tipo_iva_soportado", "tipo_iva_repercutido",
                "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex"
            ],
        }

        product_details = {}
        for i in range(0, len(product_ids), 100):
            batch_read["inputs"] = [{"id": pid} for pid in product_ids[i:i + 100]]
            resp = client.crm.products.batch_api.read(
                batch_read_input_simple_public_object_id=batch_read
            )
            product_details.update({item.id: item.properties for item in (resp.results or [])})

        sku_to_margin = {item.id: item.margen_unitario for item in data.line_items}
        line_items_created = []

        for sku, product_id in sku_to_product_id.items():

            props = product_details.get(product_id, {})
            if not props:
                logger.error(f"[products] Missing product details for {product_id}")
                continue

            price = props.get("hs_price_eur") or props.get("ultimo_precio_compra") or props.get("ultimo_precio_evaluado") or "0"
            price = round_price(price) or 0
            margin = sku_to_margin.get(sku)

            logger.warning(
                f"[articles] product={product_id} sku={sku} RAW_PRODUCT_PROPS={dict(props)}"
            )

            # Resolve IVA from product properties per residency
            iva_rep = resolve_product_iva(props, residennum, "repercutido")
            iva_sop = resolve_product_iva(props, residennum, "soportado")

            line_item_id = create_line_item_with_association(
                client=client,
                deal_id=deal_id,
                product_id=product_id,
                sku=sku,
                product_name=props.get("name", f"Product {sku}"),
                product_price=str(price),
                margen_unitario=margin,
                almacen=deal_almacen,
                tipo_iva_repercutido=iva_rep,
                tipo_iva_soportado=iva_sop,
                tipo_articulo="fabricante",
                hs_product_type="Articulo",
                peso_neto=props.get("peso_neto"),
                peso_bruto=props.get("peso_bruto"),
                name_en=props.get("name_en"),
                name_fr=props.get("name_fr"),
                description=props.get("description"),
                formato=props.get("formato"),
                marca=props.get("marca_certificado")
            )

            if line_item_id:
                line_items_created.append(line_item_id)

        for provider_id in cardcode_to_provider_id.values():
            assoc_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
            payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 286}]

            resp = requests.put(assoc_url, headers=headers, json=payload, timeout=10)
            if not resp.ok:
                logger.error(f"[providers-assoc] Failed provider {provider_id}: {resp.status_code} - {resp.text}")

        return BaseResponse(success=True, error=None)

    except Exception as e:
        logger.error(f"Error in update_deal_articles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{deal_id}/services", response_model=DealServicesResponse)
async def get_deal_services(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Get service line items for a deal (ServiciosTab).

    Regla unificada: servicios = line items con hs_product_type = service, excluyendo tipo_articulo = transitario.
    """
    try:
        client = hubspot_client.get_client()
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        logger.warning(f"[services] READ deal_id={deal_id} fetching services")

        line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[services] READ line_item_ids count={len(line_item_ids)} ids={line_item_ids[:20]}{'...' if len(line_item_ids) > 20 else ''}")

        services = []

        if not line_item_ids:
            logger.warning(f"[services] No line items for deal {deal_id} -> returning empty services")
            return DealServicesResponse(success=True, error=None, services=[])

        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_size = 100
        all_line_items = []

        for i in range(0, len(line_item_ids), batch_size):
            batch_ids = line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": ["hs_product_id", "hs_product_type", "price", "name", "cardcode", "tipo_articulo", "tipo_iva_repercutido", "tipo_iva_soportado"],
                "inputs": [{"id": str(lid)} for lid in batch_ids]
            }

            try:
                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                if batch_response.ok:
                    batch_data = batch_response.json()
                    all_line_items.extend(batch_data.get("results", []))
                else:
                    logger.error(f"[services] Batch read line items failed: {batch_response.status_code}")
            except Exception as e:
                logger.error(f"[services] Error in batch read line items: {e}")

        logger.warning(f"[services] Batch fetched {len(all_line_items)} line items")
        for idx, li in enumerate(all_line_items[:5]):
            props = li.get("properties", {})
            logger.warning(f"[services] line_item[{idx}] id={li.get('id')} hs_product_type={props.get('hs_product_type')!r} hs_product_id={props.get('hs_product_id')}")

        # Regla unificada: servicios = hs_product_type = service, excluyendo tipo_articulo = transitario/comisiones
        service_line_items = []
        unique_cardcodes = set()
        product_ids_to_fetch = []

        for line_item in all_line_items:
            props = line_item.get("properties", {})
            if not _line_item_is_service(props):
                continue
            tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()
            if tipo_articulo in {"transitario", "comisiones"}:
                continue  # Los de tipo_articulo=transitario/comisiones quedan fuera del read de servicios
            product_id_raw = props.get("hs_product_id")
            product_id = str(product_id_raw) if product_id_raw else str(line_item.get("id") or "")
            if not product_id:
                continue
            if product_id_raw:
                pid_str = str(product_id_raw)
                if pid_str not in product_ids_to_fetch:
                    product_ids_to_fetch.append(pid_str)
            tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()
            product_name = props.get("name")
            service_line_items.append({
                "line_item": line_item,
                "product_id": product_id,
                "product_name": product_name,
                "tipo_articulo": tipo_articulo
            })
            if props.get("cardcode"):
                unique_cardcodes.add(props.get("cardcode"))

        # Batch read products solo para rellenar product_name cuando venga vacío en el line item
        product_map = {}
        if product_ids_to_fetch:
            batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
            for i in range(0, len(product_ids_to_fetch), batch_size):
                batch_ids = product_ids_to_fetch[i:i + batch_size]
                batch_payload = {"properties": ["name"], "inputs": [{"id": str(pid)} for pid in batch_ids]}
                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        for product in batch_response.json().get("results", []):
                            pid = product.get("id")
                            if pid is not None:
                                product_map[str(pid)] = product.get("properties", {})
                except Exception as e:
                    logger.error(f"[services] Error batch read products: {e}")
            for s in service_line_items:
                # Usar siempre el nombre del catálogo de productos (no el nombre del line item,
                # que puede incluir el prefijo del contenedor: "55475214336-001 - SEGURO MERCANCÍA")
                if s["product_id"] in product_map:
                    s["product_name"] = product_map[s["product_id"]].get("name") or s["product_name"]
                elif not s["product_name"]:
                    s["product_name"] = ""

        logger.warning(f"[services] Filtered to {len(service_line_items)} service line items (hs_product_type=service)")

        cardcode_to_provider = {}  # cardcode -> {id, name}

        if unique_cardcodes:
            # Build filter groups for batch search (OR condition)
            filter_groups = []
            for cardcode in list(unique_cardcodes)[:100]:  # Limit to 100 per search
                filter_groups.append({
                    "filters": [{"propertyName": "cardcode", "operator": "EQ", "value": cardcode}]
                })

            if filter_groups:
                search_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/search"
                search_payload = {
                    "filterGroups": filter_groups,
                    "properties": ["cardcode", "name"],
                    "limit": 100
                }

                try:
                    search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=30)
                    if search_response.ok:
                        search_data = search_response.json()
                        for result in search_data.get("results", []):
                            cardcode = result.get("properties", {}).get("cardcode")
                            if cardcode:
                                cardcode_to_provider[cardcode] = {
                                    "id": str(result.get("id")),
                                    "name": result.get("properties", {}).get("name")
                                }
                        logger.info(f"[services] Batch search found {len(cardcode_to_provider)} providers")
                    else:
                        logger.error(f"[services] Batch search providers failed: {search_response.status_code}")
                except Exception as e:
                    logger.error(f"[services] Error in batch search providers: {e}")

            # Handle remaining cardcodes if > 100
            remaining_cardcodes = list(unique_cardcodes)[100:]
            if remaining_cardcodes:
                logger.info(f"[services] Searching remaining {len(remaining_cardcodes)} cardcodes individually")
                for cardcode in remaining_cardcodes:
                    try:
                        search_payload = {
                            "filterGroups": [{"filters": [{"propertyName": "cardcode", "operator": "EQ", "value": cardcode}]}],
                            "properties": ["cardcode", "name"],
                            "limit": 1
                        }
                        search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)
                        if search_response.ok:
                            search_data = search_response.json()
                            if search_data.get("results"):
                                cardcode_to_provider[cardcode] = {
                                    "id": str(search_data["results"][0]["id"]),
                                    "name": search_data["results"][0].get("properties", {}).get("name")
                                }
                    except Exception as e:
                        logger.warning(f"[services] Error searching cardcode {cardcode}: {e}")

        # Since each service is now replicated per container, we need to deduplicate
        unique_services_map = {}  # key: (product_id, cardcode) -> service data

        for service_data in service_line_items:
            props = service_data["line_item"].get("properties", {})
            cardcode = props.get("cardcode") or ""
            product_id = str(service_data["product_id"])

            unique_key = (product_id, cardcode)

            # Only add if not already in map (first occurrence)
            if unique_key not in unique_services_map:
                provider_info = cardcode_to_provider.get(cardcode, {}) if cardcode else {}
                price = float(props.get("price") or 0)
                tipo_iva_repercutido = props.get("tipo_iva_repercutido") or None
                tipo_iva_soportado = props.get("tipo_iva_soportado") or None

                unique_services_map[unique_key] = {
                    "product_id": product_id,
                    "product_name": service_data["product_name"] or props.get("name") or "",
                    "tipo_articulo": service_data["tipo_articulo"],
                    "provider_id": provider_info.get("id"),
                    "provider_name": provider_info.get("name"),
                    "provider_cardcode": cardcode if cardcode else None,
                    "price": price,
                    "tipo_iva_repercutido": tipo_iva_repercutido,
                    "tipo_iva_soportado": tipo_iva_soportado
                }

        for service_data in unique_services_map.values():
            services.append(ServiceItem(
                product_id=service_data["product_id"],
                product_name=service_data["product_name"],
                tipo_articulo=service_data["tipo_articulo"],
                provider_id=service_data["provider_id"],
                provider_name=service_data["provider_name"],
                provider_cardcode=service_data["provider_cardcode"],
                price=service_data["price"],
                tipo_iva_repercutido=service_data.get("tipo_iva_repercutido"),
                tipo_iva_soportado=service_data.get("tipo_iva_soportado")
            ))
            logger.info(f"[services] Unique service: {service_data['product_name']} with provider {service_data['provider_cardcode']}")

        logger.warning(f"[services] READ returning services={len(services)} (deduplicated from {len(service_line_items)} line items)")

        return DealServicesResponse(
            success=True,
            error=None,
            services=services
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_deal_services: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deal_id}/services", response_model=BaseResponse)
async def update_deal_services(
    data: UpdateServicesRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Sustituye los line items de servicios y los proveedores asociados al deal a partir de ServiciosTab. Crea un line item por servicio × contenedor con tipo_iva_repercutido derivado de la residencia de la empresa."""
    try:
        client = hubspot_client.get_client()
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        batch_size = 100

        if isinstance(data, dict):
            data = UpdateServicesRequest(**data)

        deal_almacen = None
        tipo_contenedor = ""
        try:
            deal_basic = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["almacen", "tipo_contenedor"]
            )
            deal_almacen = deal_basic.properties.get("almacen")
            tipo_contenedor = deal_basic.properties.get("tipo_contenedor") or ""
        except Exception as e:
            logger.warning(f"[services] Could not fetch deal almacen: {e}")

        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": {"deal_currency_code": "EUR"}}
            )
        except Exception as e:
            logger.warning(f"[services] Could not set deal_currency_code: {e}")

        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")

        servicios_existentes = {}  # (hs_product_id, numero_container) -> {id, properties}
        container_line_items = []
        line_items_del_deal = []

        if existing_line_item_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            for i in range(0, len(existing_line_item_ids), batch_size):
                batch_ids = existing_line_item_ids[i:i + batch_size]
                batch_payload = {
                    "properties": [
                        "name", "hs_product_id", "hs_product_type",
                        "tipo_articulo", "numero_container",
                        "price", "precio_de_coste", "cardcode",
                        "tipo_iva_repercutido", "tipo_iva_soportado",
                        "almacen",
                    ],
                    "inputs": [{"id": str(lid)} for lid in batch_ids],
                }
                try:
                    resp = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if not resp.ok:
                        logger.error(f"[services] batch read failed: {resp.status_code} - {resp.text}")
                        continue
                    line_items_del_deal.extend(resp.json().get("results", []))
                except Exception as e:
                    logger.error(f"[services] error in batch read line items: {e}")

        line_items_del_deal = _sanear_prefijos_heredados(
            deal_id=deal_id,
            all_line_items=line_items_del_deal,
            headers=headers,
            batch_size=batch_size,
        )

        for item in line_items_del_deal:
            props = item.get("properties", {}) or {}
            tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()
            product_type = (props.get("hs_product_type") or "").strip().lower()
            if _line_item_is_service(props) and tipo_articulo not in {"transitario", "comisiones"}:
                key = (
                    str(props.get("hs_product_id") or "").strip(),
                    (props.get("numero_container") or "").strip(),
                )
                servicios_existentes[key] = {
                    "id": item.get("id"),
                    "properties": props,
                }
            if _is_container_product_type(product_type):
                container_line_items.append({
                    "id": item.get("id"),
                    "name": props.get("name", ""),
                    "numero_container": props.get("numero_container", ""),
                })

        residennum = ""
        equ = ""
        tipo_cliente = ""
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                company_response = requests.get(
                    company_url, headers=headers,
                    params={"properties": "residennum,equ,tipo_cliente"}, timeout=10,
                )
                if company_response.ok:
                    company_props = company_response.json().get("properties", {}) or {}
                    residennum = company_props.get("residennum") or ""
                    equ = company_props.get("equ") or ""
                    tipo_cliente = company_props.get("tipo_cliente") or ""
        except Exception as e:
            logger.error(f"[services] error fetching company residence: {e}")

        service_product_tipo_map = {}
        service_product_hs_type_map = {}
        service_product_iva_props = {}
        service_product_price_map = {}  # pid -> round_price(hs_price_eur)
        if data.services:
            service_product_ids = list({str(s.product_id) for s in data.services})
            try:
                prod_batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
                iva_props_list = [
                    "tipo_iva_soportado", "tipo_iva_repercutido",
                    "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                    "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex",
                ]
                for i in range(0, len(service_product_ids), batch_size):
                    batch_ids = service_product_ids[i:i + batch_size]
                    batch_payload = {
                        "properties": ["tipo_articulo", "hs_product_type", "hs_price_eur"] + iva_props_list,
                        "inputs": [{"id": pid} for pid in batch_ids],
                    }
                    resp = requests.post(prod_batch_url, headers=headers, json=batch_payload, timeout=30)
                    if resp.ok:
                        for product in resp.json().get("results", []):
                            pid = str(product.get("id") or "")
                            if not pid:
                                continue
                            props = product.get("properties", {}) or {}
                            service_product_tipo_map[pid] = (props.get("tipo_articulo") or "").strip().lower() or "servicio"
                            hs_type = (props.get("hs_product_type") or "").strip()
                            service_product_hs_type_map[pid] = hs_type if hs_type else "Servicio"
                            service_product_iva_props[pid] = {k: props.get(k) for k in iva_props_list}
                            service_product_price_map[pid] = round_price(props.get("hs_price_eur"))
            except Exception as e:
                logger.warning(f"[services] Error fetching product properties: {e}")

        containers_iter = container_line_items if container_line_items else [{"name": "", "numero_container": ""}]
        target_specs = {}  # (product_id, numero_container) -> {service, container, props_target}

        for service in data.services:
            pid = str(service.product_id)
            iva_props = service_product_iva_props.get(pid, {})
            tipo_iva_rep_input = service.tipo_iva or resolve_product_iva(iva_props, residennum, "repercutido")
            tipo_iva_sop = service.tipo_iva_soportado or resolve_product_iva(iva_props, residennum, "soportado") or ""
            tipo_iva_rep = _tipo_iva_repercutido_for_hubspot(tipo_iva_rep_input)
            service_tipo = service_product_tipo_map.get(pid, "servicio")
            service_hs_type = _hs_product_type_for_hubspot(service_product_hs_type_map.get(pid) or "Servicio")
            precio_round = round_price(service.price)
            if precio_round is None:
                logger.warning(
                    f"[services] servicio product_id={pid} sin price — "
                    f"precio_unitario y precio_de_coste quedarán vacíos"
                )
                continue

            for c in containers_iter:
                numero_container = (c.get("numero_container") or "").strip()
                key = (pid, numero_container)
                container_name = c.get("name") or ""
                nombre_servicio = f"{container_name} - {service.product_name}" if container_name else service.product_name

                props_target = {
                    "name": nombre_servicio,
                    "price": str(precio_round),
                    "cardcode": service.provider_cardcode or "",
                    "numero_container": numero_container,
                    "tipo_iva_repercutido": tipo_iva_rep,
                    "tipo_iva_soportado": tipo_iva_sop,
                    "tipo_articulo": service_tipo,
                    "hs_product_type": service_hs_type,
                }
                almacen_servicio = resolve_almacen_para_elemento(deal_almacen, service_tipo, tipo_contenedor)
                if almacen_servicio:
                    props_target["almacen"] = str(almacen_servicio)

                target_specs[key] = {
                    "service": service,
                    "container": c,
                    "props_target": props_target,
                }

        target_keys = set(target_specs.keys())

        to_update = []
        to_create = []
        to_delete_ids = []

        for key, spec in target_specs.items():
            existing = servicios_existentes.get(key)
            if existing:
                cur = existing["properties"]
                update_props = {}
                for prop, target_val in spec["props_target"].items():
                    cur_val = cur.get(prop)
                    if prop == "price":
                        if _safe_float(cur_val) != _safe_float(target_val):
                            update_props[prop] = target_val
                    else:
                        if (cur_val or "") != (target_val or ""):
                            update_props[prop] = target_val
                if update_props:
                    to_update.append({"id": existing["id"], "properties": update_props, "_target_key": key})
            else:
                pid = key[0]
                create_props = {
                    "hs_product_id": pid,
                    "hs_sku": pid,
                    "quantity": "1",
                    "hs_currency": "EUR",
                    **spec["props_target"],
                }
                to_create.append({
                    "properties": create_props,
                    "associations": [{
                        "to": {"id": deal_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}],
                    }],
                    "_target_key": key,
                })

        for key, existing in servicios_existentes.items():
            if key not in target_keys:
                to_delete_ids.append(existing["id"])

        persisted_by_key = {key: existing["id"] for key, existing in servicios_existentes.items() if key in target_keys}

        if to_update:
            upd_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
            for i in range(0, len(to_update), batch_size):
                chunk = to_update[i:i + batch_size]
                for x in chunk:
                    enforce_line_item_price_rounding(x["properties"])
                payload = {"inputs": [{"id": x["id"], "properties": x["properties"]} for x in chunk]}
                try:
                    resp = requests.post(upd_url, headers=headers, json=payload, timeout=60)
                    if not resp.ok:
                        logger.error(f"[services] batch update failed: {resp.status_code} - {resp.text}")
                except Exception as e:
                    logger.error(f"[services] error batch updating: {e}")

        created_ids_by_key = {}
        if to_create:
            cr_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
            for i in range(0, len(to_create), batch_size):
                chunk = to_create[i:i + batch_size]
                for x in chunk:
                    enforce_line_item_price_rounding(x["properties"])
                payload = {"inputs": [{"properties": x["properties"], "associations": x["associations"]} for x in chunk]}
                try:
                    resp = requests.post(cr_url, headers=headers, json=payload, timeout=60)
                    if resp.ok:
                        # HubSpot no garantiza que los results salgan en el orden de los
                        # inputs: correlacionamos por (hs_sku, numero_container) —que es el
                        # propio _target_key— leído de la respuesta, no por posición.
                        key_by_ident = {
                            (x["properties"].get("hs_sku"), (x["properties"].get("numero_container") or "").strip()): x["_target_key"]
                            for x in chunk
                        }
                        for created in resp.json().get("results", []):
                            new_id = created.get("id")
                            cprops = created.get("properties") or {}
                            ident = (cprops.get("hs_sku"), (cprops.get("numero_container") or "").strip())
                            tk = key_by_ident.get(ident)
                            if new_id and tk is not None:
                                created_ids_by_key[tk] = new_id
                                persisted_by_key[tk] = new_id
                            elif new_id:
                                logger.error(f"[services] line item creado id={new_id} no casa con ningún input ident={ident}")
                    else:
                        logger.error(f"[services] batch create failed: {resp.status_code} - {resp.text}")
                except Exception as e:
                    logger.error(f"[services] error batch creating: {e}")

        if to_delete_ids:
            del_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
            for i in range(0, len(to_delete_ids), batch_size):
                chunk = to_delete_ids[i:i + batch_size]
                try:
                    resp = requests.post(del_url, headers=headers, json={"inputs": [{"id": str(x)} for x in chunk]}, timeout=30)
                    if not resp.ok:
                        logger.error(f"[services] batch archive failed: {resp.status_code} - {resp.text}")
                except Exception as e:
                    logger.error(f"[services] error batch archiving: {e}")

        desired_provider_ids = {str(s.provider_id) for s in data.services if s.provider_id}
        existing_provider_ids = _get_associated_ids(deal_id, "2-43108800")
        actual_service_provider_ids = set()

        if existing_provider_ids:
            prov_batch_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/batch/read"
            for i in range(0, len(existing_provider_ids), batch_size):
                batch_ids = existing_provider_ids[i:i + batch_size]
                payload = {
                    "properties": ["tipo_proveedor"],
                    "inputs": [{"id": str(pid)} for pid in batch_ids],
                }
                try:
                    resp = requests.post(prov_batch_url, headers=headers, json=payload, timeout=30)
                    if resp.ok:
                        for provider in resp.json().get("results", []):
                            tipo_proveedor = (provider.get("properties", {}).get("tipo_proveedor") or "").strip().lower()
                            if tipo_proveedor not in ("fabricante", "transitario", "comisiones", "seguro"):
                                actual_service_provider_ids.add(str(provider.get("id")))
                except Exception as e:
                    logger.error(f"[services] error reading providers: {e}")

        to_associate = desired_provider_ids - actual_service_provider_ids
        to_disassociate = actual_service_provider_ids - desired_provider_ids

        for provider_id in to_associate:
            try:
                assoc_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
                assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 448}]
                resp = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)
                if not resp.ok:
                    logger.error(f"[services] associate provider {provider_id} failed: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"[services] error associating provider {provider_id}: {e}")

        for provider_id in to_disassociate:
            try:
                del_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
                resp = requests.delete(del_url, headers=headers, timeout=10)
                if not resp.ok:
                    logger.error(f"[services] disassociate provider {provider_id} failed: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"[services] error disassociating provider {provider_id}: {e}")

        try:
            elementos = get_elementos_by_deal(
                client, deal_id,
                properties=[
                    "nombre", "tipo_articulo", "cantidad", "precio_unitario",
                    "precio_de_coste", "precio_total", "cardcode",
                    "tipo_iva_repercutido", "tipo_iva_soportado", "line_item_id",
                    "nombre_contenedor",
                ],
            )
        except Exception as e:
            logger.warning(f"[services] could not load ELEMENTOS for deal {deal_id}: {e}")
            elementos = []

        non_service_tipos = {
            "flete", "fabricante", "transitario", "comisiones", "comision",
            "container", "contenedor", "articulo", "inventory",
        }
        elementos_svc_by_lid = {}
        elementos_svc_by_nombre = {}
        for el in elementos:
            props = el.get("properties", {}) or {}
            tipo = (props.get("tipo_articulo") or "").strip().lower()
            if tipo in non_service_tipos:
                continue
            lid = (props.get("line_item_id") or "").strip()
            nombre = (props.get("nombre") or "").strip()
            entry = {"id": el.get("id"), "properties": props}
            if lid:
                elementos_svc_by_lid[lid] = entry
            if nombre:
                elementos_svc_by_nombre[nombre] = entry

        contenedores_co_by_numero = _resolve_contenedor_co_by_numero(client, deal_id)
        contact_id = (get_associated_contacts(deal_id) or [None])[0]
        company_id = (get_associated_companies(deal_id) or [None])[0]
        affected_contenedor_co_ids = set()

        def _co_for_spec(spec):
            container_name = spec["container"].get("name") or ""
            numero = (spec["container"].get("numero_container") or "").strip()
            container_lid = str(spec["container"].get("id") or "")
            return (
                contenedores_co_by_numero.get(numero)
                or contenedores_co_by_numero.get(container_name)
                or contenedores_co_by_numero.get(f"lid:{container_lid}")
            )

        def _co_id_for_key(key):
            if not key:
                return None
            numero = (key[1] or "").strip() if len(key) > 1 else ""
            co = contenedores_co_by_numero.get(numero) if numero else None
            return co.get("id") if co else None

        def _create_servicio_elemento(spec, lid_for_elem):
            co = _co_for_spec(spec)
            if not co:
                logger.warning(
                    f"[services] no CONTENEDOR CO matched for spec; available keys={list(contenedores_co_by_numero.keys())}"
                )
                return False
            precio = _safe_float(spec["props_target"]["price"])
            if precio is None:
                precio = service_product_price_map.get(str(spec["service"].product_id))
            if precio is None:
                logger.warning(
                    f"[services] create elemento sin price para line_item_id={lid_for_elem}"
                )
                return False
            container_name = spec["container"].get("name") or ""
            elem_data = [{
                "nombre": spec["props_target"]["name"],
                "nombre_contenedor": container_name,
                "tipo_articulo": _normalize_tipo_articulo_for_elemento(spec["props_target"]["tipo_articulo"]),
                "cantidad": 1,
                "precio_unitario": precio,
                "precio_de_coste": precio,
                "precio_total": precio,
                "cardcode": spec["service"].provider_cardcode or "",
                "line_item_id": str(lid_for_elem),
            }]
            if spec["props_target"]["tipo_iva_repercutido"]:
                elem_data[0]["tipo_iva_repercutido"] = spec["props_target"]["tipo_iva_repercutido"]
            if spec["props_target"]["tipo_iva_soportado"]:
                elem_data[0]["tipo_iva_soportado"] = spec["props_target"]["tipo_iva_soportado"]
            if spec["props_target"].get("almacen"):
                elem_data[0]["almacen"] = spec["props_target"]["almacen"]
            try:
                created = batch_create_elementos_with_associations(
                    client=client,
                    elementos_data=elem_data,
                    deal_id=deal_id,
                    contenedor_id=co.get("id"),
                    contact_id=contact_id,
                    company_id=company_id,
                )
                if created:
                    affected_contenedor_co_ids.add(co.get("id"))
                    return True
                return False
            except Exception as e:
                logger.warning(f"[services] failed creating elemento for {lid_for_elem}: {e}")
                return False

        elementos_updated = 0
        elementos_created = 0
        new_lids_set = {str(x) for x in created_ids_by_key.values()}
        for key, lid in persisted_by_key.items():
            target_lid = str(lid).strip()
            if target_lid in new_lids_set:
                continue
            spec = target_specs.get(key)
            if not spec:
                continue

            elem = elementos_svc_by_lid.get(target_lid)
            if not elem:
                target_name = spec["props_target"]["name"]
                if target_name and target_name in elementos_svc_by_nombre:
                    elem = elementos_svc_by_nombre[target_name]
            if not elem:
                existing = servicios_existentes.get(key)
                nombre_existing = (existing["properties"].get("name") or "").strip() if existing else ""
                if nombre_existing and nombre_existing in elementos_svc_by_nombre:
                    elem = elementos_svc_by_nombre[nombre_existing]
            if not elem:
                if _create_servicio_elemento(spec, target_lid):
                    elementos_created += 1
                else:
                    logger.warning(
                        f"[services] could not create missing ELEMENTO for line_item_id={target_lid} key={key!r}"
                    )
                continue

            cur_props = elem["properties"]
            target_name = spec["props_target"]["name"]
            target_cardcode = spec["service"].provider_cardcode or ""
            target_iva_rep = spec["props_target"]["tipo_iva_repercutido"]
            target_iva_sop = spec["props_target"]["tipo_iva_soportado"]
            precio_de_coste_target = _safe_float(spec["props_target"]["price"])
            if precio_de_coste_target is None:
                precio_de_coste_target = service_product_price_map.get(key[0])
            if precio_de_coste_target is None:
                logger.warning(
                    f"[services] update elemento sin price ni hs_price_eur del producto {key[0]} "
                    f"para line_item_id={target_lid}"
                )
                continue

            elem_update_props = {}
            if (cur_props.get("nombre") or "").strip() != target_name:
                elem_update_props["nombre"] = target_name
            if _safe_float(cur_props.get("precio_unitario")) != precio_de_coste_target:
                elem_update_props["precio_unitario"] = precio_de_coste_target
            if _safe_float(cur_props.get("precio_total")) != precio_de_coste_target:
                elem_update_props["precio_total"] = precio_de_coste_target
            if _safe_float(cur_props.get("precio_de_coste")) != precio_de_coste_target:
                elem_update_props["precio_de_coste"] = precio_de_coste_target
            if (cur_props.get("cardcode") or "") != target_cardcode:
                elem_update_props["cardcode"] = target_cardcode
            if target_iva_rep and cur_props.get("tipo_iva_repercutido") != target_iva_rep:
                elem_update_props["tipo_iva_repercutido"] = target_iva_rep
            if (cur_props.get("tipo_iva_soportado") or "") != (target_iva_sop or ""):
                elem_update_props["tipo_iva_soportado"] = target_iva_sop
            if str(cur_props.get("line_item_id") or "") != target_lid:
                elem_update_props["line_item_id"] = target_lid

            if not elem_update_props:
                continue
            if update_elemento(client, elem["id"], elem_update_props):
                elementos_updated += 1
                co_id_upd = _co_id_for_key(key)
                if co_id_upd:
                    affected_contenedor_co_ids.add(co_id_upd)

        if to_create and created_ids_by_key:
            for x in to_create:
                key = x["_target_key"]
                new_lid = created_ids_by_key.get(key)
                if not new_lid:
                    continue
                spec = target_specs[key]
                container_name = spec["container"].get("name") or ""
                numero = (spec["container"].get("numero_container") or "").strip()
                container_lid = str(spec["container"].get("id") or "")
                co = (
                    contenedores_co_by_numero.get(numero)
                    or contenedores_co_by_numero.get(container_name)
                    or contenedores_co_by_numero.get(f"lid:{container_lid}")
                )
                if not co:
                    continue
                precio_de_coste_serv = _safe_float(spec["props_target"]["price"])
                if precio_de_coste_serv is None:
                    precio_de_coste_serv = service_product_price_map.get(key[0])
                if precio_de_coste_serv is None:
                    logger.warning(
                        f"[services] create elemento sin price ni hs_price_eur del producto {key[0]} "
                        f"para line_item_id={new_lid}"
                    )
                    continue
                elementos_data = [{
                    "nombre": spec["props_target"]["name"],
                    "nombre_contenedor": container_name,
                    "tipo_articulo": _normalize_tipo_articulo_for_elemento(spec["props_target"]["tipo_articulo"]),
                    "cantidad": 1,
                    "precio_unitario": precio_de_coste_serv,
                    "precio_de_coste": precio_de_coste_serv,
                    "precio_total": precio_de_coste_serv,
                    "cardcode": spec["service"].provider_cardcode or "",
                    "line_item_id": str(new_lid),
                }]
                if spec["props_target"]["tipo_iva_repercutido"]:
                    elementos_data[0]["tipo_iva_repercutido"] = spec["props_target"]["tipo_iva_repercutido"]
                if spec["props_target"]["tipo_iva_soportado"]:
                    elementos_data[0]["tipo_iva_soportado"] = spec["props_target"]["tipo_iva_soportado"]
                if spec["props_target"].get("almacen"):
                    elementos_data[0]["almacen"] = spec["props_target"]["almacen"]
                created = batch_create_elementos_with_associations(
                    client=client,
                    elementos_data=elementos_data,
                    deal_id=deal_id,
                    contenedor_id=co.get("id"),
                    contact_id=contact_id,
                    company_id=company_id,
                )
                if created:
                    elementos_created += len(created)
                    affected_contenedor_co_ids.add(co.get("id"))

        elementos_deleted = 0
        for lid in to_delete_ids:
            elem_lid = str(lid).strip()
            elem = elementos_svc_by_lid.get(elem_lid)
            if not elem:
                deleted_existing = next(
                    (e for e in servicios_existentes.values() if str(e["id"]) == elem_lid),
                    None,
                )
                if deleted_existing:
                    nombre_deleted = (deleted_existing["properties"].get("name") or "").strip()
                    if nombre_deleted:
                        elem = elementos_svc_by_nombre.get(nombre_deleted)
            if elem and delete_elemento(client, elem.get("id")):
                elementos_deleted += 1
                nc_del = ((elem.get("properties") or {}).get("nombre_contenedor") or "").strip()
                co_del = contenedores_co_by_numero.get(nc_del) if nc_del else None
                if co_del:
                    affected_contenedor_co_ids.add(co_del.get("id"))

        if to_update or to_create or to_delete_ids or elementos_updated or elementos_created or elementos_deleted or to_associate or to_disassociate:
            logger.info(
                f"[services] sync done: line_items updated={len(to_update)} "
                f"created={len(to_create)} deleted={len(to_delete_ids)} | "
                f"providers associate={len(to_associate)} disassociate={len(to_disassociate)} | "
                f"ELEMENTOS updated={elementos_updated} created={elementos_created} deleted={elementos_deleted}"
            )

        if affected_contenedor_co_ids:
            try:
                await hubdb_client.preload_iva_table()
                recalcular_contenedores(
                    client,
                    deal_id,
                    list(affected_contenedor_co_ids),
                    {"equ": equ, "residennum": residennum},
                )
            except Exception as e:
                logger.warning(f"[services] recalcular_contenedores falló: {e}")
            touch_pagos_for_contenedores(client, list(affected_contenedor_co_ids))

        return BaseResponse(success=True, error=None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_services: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _take_article_entry(candidates, num_cajas):
    """Devuelve la primera entrada de line item no consumida (preferentemente la
    de igual numCajas) y la marca como consumida. Permite que dos líneas del mismo
    artículo en un contenedor (promo 5+1) conserven cada una su line_item_id en vez
    de colapsar. Devuelve {} si no hay candidatos disponibles."""
    if not candidates:
        return {}
    disponibles = [e for e in candidates if not e.get("_consumed")]
    if not disponibles:
        return {}
    elegido = next(
        (e for e in disponibles if e.get("quantity") == num_cajas),
        disponibles[0],
    )
    elegido["_consumed"] = True
    return elegido


@router.get("/{deal_id}/containers", response_model=DealContainersResponse)
async def get_deal_containers(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve los contenedores del deal para ContenedoresTab, agrupados por configuración (los line items individuales tienen quantity=1 y se devuelven agregados para la UI)."""
    try:
        import json
        client = hubspot_client.get_client()

        logger.warning(f"[containers] READ deal_id={deal_id} fetching container line items")

        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[containers] READ line_item_ids count={len(existing_line_item_ids)} ids={existing_line_item_ids[:20]}{'...' if len(existing_line_item_ids) > 20 else ''}")

        # We'll collect all individual container line items first, then group them
        individual_containers = []
        article_line_items = []

        # BATCH REQUEST: Fetch all line items at once
        if not existing_line_item_ids:
            logger.warning(f"[containers] No line items found for deal {deal_id} -> will return empty containers")
        else:
            # Prepare batch read for line items
            access_token = client.access_token
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }

            # Split into batches of 100 (HubSpot limit)
            batch_size = 100
            all_line_items = []

            for i in range(0, len(existing_line_item_ids), batch_size):
                batch_ids = existing_line_item_ids[i:i + batch_size]

                batch_payload = {
                    "properties": [
                        "hs_product_type",
                        "numero_container",
                        "quantity",
                        "ocupacion",
                        "peso_neto",
                        "container_config",
                        "hs_product_id",
                        "name",
                        "tipo_articulo",
                        "hs_sku",
                        "price",
                        "precio_de_coste",
                        "margen_unitario",
                        "tipo_iva_repercutido",
                        "tipo_iva_soportado",
                        "precio_negociado_tipo_venta",
                        "incoterm_precio_negociado"
                    ],
                    "inputs": [{"id": str(lid)} for lid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        batch_data = batch_response.json()
                        all_line_items.extend(batch_data.get("results", []))
                    else:
                        logger.error(f"Batch read line items failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"Error in batch read line items: {e}")

            logger.warning(f"[containers] Batch fetched {len(all_line_items)} line items")
            # Log sample of raw line items
            for idx, li in enumerate(all_line_items[:5]):
                props = li.get("properties", {})
                logger.warning(f"[containers] line_item[{idx}] id={li.get('id')} hs_product_type={props.get('hs_product_type')!r} hs_product_id={props.get('hs_product_id')} tipo_articulo={props.get('tipo_articulo')!r}")

            # Product IDs para obtener hs_sku (tipo de contenedor D20DR, etc.)
            product_ids_for_sku = list(dict.fromkeys(
                li.get("properties", {}).get("hs_product_id")
                for li in all_line_items
                if li.get("properties", {}).get("hs_product_id")
            ))
            product_sku_map = {}
            if product_ids_for_sku:
                prod_batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
                for i in range(0, len(product_ids_for_sku), batch_size):
                    batch_ids = product_ids_for_sku[i:i + batch_size]
                    batch_payload = {"properties": ["hs_sku"], "inputs": [{"id": str(pid)} for pid in batch_ids]}
                    try:
                        batch_response = requests.post(prod_batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            for product in batch_response.json().get("results", []):
                                pid = product.get("id")
                                if pid:
                                    product_sku_map[str(pid)] = (product.get("properties", {}).get("hs_sku") or "").strip()
                    except Exception as e:
                        logger.error(f"Error batch reading products for container SKU: {e}")

            # Regla unificada: contenedores = hs_product_type = container y tipo_articulo = transitario
            container_line_items = []
            article_line_items = []
            for line_item in all_line_items:
                props = line_item.get("properties", {})
                if _line_item_is_container(props):
                    container_line_items.append(line_item)
                elif _line_item_is_article(props):
                    article_line_items.append(line_item)

            logger.warning(f"[containers] Classified: container_line_items={len(container_line_items)} article_line_items={len(article_line_items)}")

            # Collect unique product IDs from container line items (for SKU lookup when building container type)
            product_ids = []
            for line_item in container_line_items:
                props = line_item.get("properties", {})
                product_id = props.get("hs_product_id", "")
                if product_id and product_id not in product_ids:
                    product_ids.append(product_id)

            # BATCH REQUEST: Fetch product SKUs for container type (reuse product_sku_map from above; add any missing)
            if product_ids:
                batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"

                for i in range(0, len(product_ids), batch_size):
                    batch_ids = product_ids[i:i + batch_size]

                    batch_payload = {
                        "properties": ["hs_sku"],
                        "inputs": [{"id": str(pid)} for pid in batch_ids]
                    }

                    try:
                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            batch_data = batch_response.json()
                            for product in batch_data.get("results", []):
                                pid = product.get("id")
                                sku = product.get("properties", {}).get("hs_sku", "")
                                if pid:
                                    product_sku_map[pid] = sku
                        else:
                            logger.error(f"Batch read products failed: {batch_response.status_code} - {batch_response.text}")
                    except Exception as e:
                        logger.error(f"Error in batch read products: {e}")

                logger.warning(f"Batch fetched {len(product_sku_map)} product SKUs")

            for line_item in container_line_items:
                try:
                    line_item_id = line_item.get("id")
                    props = line_item.get("properties", {})

                    container_config_str = props.get("container_config", "{}")
                    try:
                        container_config_dict = json.loads(container_config_str)
                        container_config = ContainerConfig(**container_config_dict)
                    except (json.JSONDecodeError, Exception) as e:
                        logger.error(f"Error parsing container_config for line item {line_item_id}: {e}")
                        # Use empty config as fallback
                        container_config = ContainerConfig(
                            articulos=[],
                            ocupacion=0,
                            pesoNeto=0
                        )

                    product_id = props.get("hs_product_id", "")
                    container_type = "UNKNOWN"
                    if product_id:
                        sku = product_sku_map.get(product_id, "")
                        # Reverse lookup from SKU to tipo
                        for tipo, container_sku in CONTAINER_SKU_MAP.items():
                            if container_sku == sku:
                                container_type = tipo
                                break

                    cantidad = int(props.get("quantity", "1"))

                    # Get container_name from name field (e.g., "55586411915-001")
                    container_name = props.get("name", "")

                    # Extract container_number from the end of container_name (e.g., "001" -> 1)
                    try:
                        # Split by '-' and get the last part, convert to int
                        container_number = int(container_name.split("-")[-1]) if container_name else 0
                    except (ValueError, IndexError):
                        logger.warning(f"Could not parse container_number from name '{container_name}', using 0")
                        container_number = 0

                    individual_containers.append({
                        "container_name": container_name,
                        "line_item_id": line_item_id,
                        "container_type": container_type,
                        "container_number": container_number,
                        "cantidad": cantidad,
                        "container_config": container_config,
                        "container_config_str": container_config_str
                    })

                    logger.warning(f"Loaded individual container #{container_number}: {container_type} x{cantidad}")

                except Exception as e:
                    logger.error(f"Error processing line item {line_item.get('id')}: {e}")

        # Key: (container_type, container_config_json)
        # Value: list of individual container dicts
        grouped_containers = {}

        for container_data in individual_containers:
            # Create a grouping key based on type and config
            group_key = (
                container_data["container_type"],
                container_data["container_config_str"]
            )

            if group_key not in grouped_containers:
                grouped_containers[group_key] = []

            grouped_containers[group_key].append(container_data)

        containers = []
        total_units = 0
        total_colis = 0
        total_peso_neto = 0.0

        for group_key, container_list in grouped_containers.items():
            # Sort by container_number to get the first one
            container_list.sort(key=lambda c: c["container_number"])

            # Use the first container's data as representative
            first_container = container_list[0]

            # Sum up quantities (should be 1 for each, so count = total)
            total_cantidad = len(container_list)

            min_container_number = first_container["container_number"]

            container_item = ContainerItem(
                line_item_id=first_container["line_item_id"],
                container_type=first_container["container_type"],
                container_number=min_container_number,
                cantidad=total_cantidad,
                container_config=first_container["container_config"]
            )
            containers.append(container_item)

            # Update totals
            total_units += total_cantidad

            # Calculate colis (total boxes) for this container group
            colis_per_container = sum(art.numCajas for art in first_container["container_config"].articulos)
            total_colis += colis_per_container * total_cantidad

            total_peso_neto += first_container["container_config"].pesoNeto * total_cantidad

            logger.warning(f"Grouped containers: {first_container['container_type']} x{total_cantidad} (containers #{min_container_number}-{container_list[-1]['container_number']})")

        # Sort containers by container_number
        containers.sort(key=lambda c: c.container_number)

        # We already have article line items from the batch request above
        # Indexado por nombre como LISTA: en nacional un contenedor puede tener
        # varias líneas del mismo artículo (promo 5+1) que comparten nombre. Cada
        # line item conserva su propia entrada para no colapsar su line_item_id.
        article_data_map = {}
        for line_item in article_line_items:
            try:
                line_item_id = line_item.get("id")
                props = line_item.get("properties", {})

                # Use the name property as unique key (contains container name + article name)
                unique_name = props.get("name", "")
                if unique_name:
                    article_data_map.setdefault(unique_name, []).append({
                        "line_item_id": line_item_id,
                        "hs_sku": props.get("hs_sku", ""),
                        "hs_product_id": props.get("hs_product_id"),
                        "tipo_articulo": (props.get("tipo_articulo") or "").strip().lower(),
                        "quantity": float(props.get("quantity")) if props.get("quantity") else None,
                        "price": float(props.get("price", 0)) if props.get("price") else None,
                        "precio_de_coste": float(props.get("precio_de_coste", 0)) if props.get("precio_de_coste") else None,
                        "margen_unitario": float(props.get("margen_unitario", 0)) if props.get("margen_unitario") else None,
                        "tipo_iva_repercutido": props.get("tipo_iva_repercutido", None),
                        "tipo_iva_soportado": props.get("tipo_iva_soportado", None),
                        "precio_negociado_tipo_venta": float(props.get("precio_negociado_tipo_venta", 0)) if props.get("precio_negociado_tipo_venta") else None,
                        "incoterm_precio_negociado": props.get("incoterm_precio_negociado") or None,
                        "_consumed": False,
                    })
                    logger.warning(f"Mapped article line item {line_item_id}: name='{unique_name}'")
            except Exception as e:
                logger.error(f"Error mapping article line item: {e}")

        total_mapped = sum(len(v) for v in article_data_map.values())
        logger.warning(f"Mapped data for {total_mapped} article line items ({len(article_data_map)} unique names)")

        # When HubSpot publishes a quote and syncs line items back to the deal, it may
        # create new deal line items from standard properties only, losing custom props
        # like precio_de_coste and margen_unitario. Detect and restore from product catalog.
        articles_needing_repair = [
            (name, data)
            for name, entries in article_data_map.items()
            for data in entries
            if data.get("tipo_articulo") == "fabricante"
            and data.get("precio_de_coste") is None
            and data.get("hs_product_id") is not None
        ]

        if articles_needing_repair:
            logger.warning(
                f"[containers] HubSpot sync degradation detected: "
                f"{len(articles_needing_repair)} fabricante articles missing precio_de_coste. "
                f"Restoring from product catalog (current hs_price_eur, not historical cost)."
            )
            try:
                product_ids_to_fetch = list({data["hs_product_id"] for _, data in articles_needing_repair})
                product_price_map = {}  # hs_product_id -> hs_price_eur

                products_batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
                for i in range(0, len(product_ids_to_fetch), 100):
                    batch_ids = product_ids_to_fetch[i:i + 100]
                    batch_payload = {
                        "properties": ["hs_price_eur"],
                        "inputs": [{"id": str(pid)} for pid in batch_ids]
                    }
                    try:
                        prod_resp = requests.post(products_batch_url, headers=headers, json=batch_payload, timeout=15)
                        if prod_resp.ok:
                            for prod in prod_resp.json().get("results", []):
                                pid = prod.get("id")
                                hs_price_eur = prod.get("properties", {}).get("hs_price_eur")
                                if pid and hs_price_eur:
                                    product_price_map[pid] = float(hs_price_eur)
                        else:
                            logger.error(f"[containers] Auto-repair: product batch read failed {prod_resp.status_code}")
                    except Exception as e:
                        logger.error(f"[containers] Auto-repair: error fetching products: {e}")

                # El precio_de_coste vive en el producto, no en el line item.
                # Solo rehidratamos article_data_map y, si el margen quedó perdido,
                # lo reescribimos en el line item porque ahí es donde lo guardamos.
                line_item_updates = []
                for name, data in articles_needing_repair:
                    prod_id = data["hs_product_id"]
                    hs_price_eur = round_price(product_price_map.get(prod_id))
                    if hs_price_eur is None:
                        logger.warning(f"[containers] Auto-repair: no hs_price_eur for product {prod_id}, skipping")
                        continue

                    data["precio_de_coste"] = hs_price_eur

                    price_exw = data.get("price")
                    if price_exw is not None and hs_price_eur > 0:
                        margen = round_margin((price_exw - hs_price_eur) / hs_price_eur)
                        if margen is not None:
                            data["margen_unitario"] = margen
                            line_item_updates.append({
                                "id": data["line_item_id"],
                                "properties": {"margen_unitario": str(margen)}
                            })

                if line_item_updates:
                    update_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
                    restaurados = 0
                    for i in range(0, len(line_item_updates), 100):
                        chunk = line_item_updates[i:i + 100]
                        try:
                            upd_resp = requests.post(update_url, headers=headers, json={"inputs": chunk}, timeout=15)
                            if upd_resp.ok:
                                restaurados += len(chunk)
                            else:
                                logger.error(f"[containers] Auto-repair: batch update [{i}:{i+len(chunk)}] failed {upd_resp.status_code}: {upd_resp.text}")
                        except Exception as e:
                            logger.error(f"[containers] Auto-repair: error updating line items [{i}:{i+len(chunk)}]: {e}")
                    logger.warning(f"[containers] Auto-repair: restored margen_unitario for {restaurados}/{len(line_item_updates)} line items")

            except Exception as e:
                logger.error(f"[containers] Auto-repair failed: {e}", exc_info=True)
                # Non-fatal: continue with potentially null precio_de_coste

        unique_containers = []
        for container_data in individual_containers:
            container_name = container_data["container_name"]

            unique_articles = []
            for articulo in container_data["container_config"].articulos:
                articulo_name = f"{container_name} - {articulo.nombre}"

                # Cada línea del JSON consume su propio line item de la lista.
                # Con duplicados (mismo nombre) hay varias entradas y elegimos una
                # no consumida (preferentemente la de igual numCajas) para que cada
                # línea conserve su line_item_id en vez de colapsar.
                candidates = article_data_map.get(articulo_name)

                # Fallback: if not found by exact name, try normalizing quotes
                if not candidates:
                    # Normalize quotes: replace '' with "" to match HubSpot's storage
                    normalized_name = articulo_name.replace("''", '""')
                    candidates = article_data_map.get(normalized_name)
                    if candidates:
                        logger.warning(f"Article found after normalizing quotes: '{normalized_name}'")

                # Second fallback: if still not found, try by SKU (backwards compatibility)
                if not candidates:
                    logger.warning(f"Article not found by unique name '{articulo_name}', trying SKU '{articulo.articuloId}'")
                    for name, entries in article_data_map.items():
                        if name.startswith(container_name) and any(
                            e.get("hs_sku") == articulo.articuloId for e in entries
                        ):
                            candidates = entries
                            logger.warning(f"Found article by SKU in same container: {name}")
                            break

                article_extra_data = _take_article_entry(candidates, articulo.numCajas)

                unique_article = UniqueContainerArticle(
                    articuloId=articulo.articuloId,
                    nombre=articulo.nombre,
                    articuloName=articulo_name,
                    numCajas=articulo.numCajas,
                    line_item_id=article_extra_data.get("line_item_id"),
                    price=article_extra_data.get("price"),
                    precio_de_coste=article_extra_data.get("precio_de_coste"),
                    margen_unitario=article_extra_data.get("margen_unitario"),
                    tipo_iva_repercutido=article_extra_data.get("tipo_iva_repercutido"),
                    tipo_iva_soportado=article_extra_data.get("tipo_iva_soportado"),
                    precio_negociado_tipo_venta=article_extra_data.get("precio_negociado_tipo_venta"),
                    incoterm_precio_negociado=article_extra_data.get("incoterm_precio_negociado")
                )
                unique_articles.append(unique_article)

            unique_config = UniqueContainerConfig(
                articulos=unique_articles,
                ocupacion=container_data["container_config"].ocupacion,
                pesoNeto=container_data["container_config"].pesoNeto
            )

            unique_container = UniqueContainerItem(
                line_item_id=container_data["line_item_id"],
                container_type=container_data["container_type"],
                container_number=container_data["container_number"],
                container_name=container_name,
                cantidad=container_data["cantidad"],
                container_config=unique_config
            )
            unique_containers.append(unique_container)

        # Sort unique_containers by container_number
        unique_containers.sort(key=lambda c: c.container_number)

        logger.warning(f"[containers] READ returning containers={len(containers)} unique_containers={len(unique_containers)} total_units={total_units} total_colis={total_colis}")

        return DealContainersResponse(
            success=True,
            error=None,
            containers=containers,
            unique_containers=unique_containers,
            total_containers=len(containers),
            total_units=total_units,
            total_colis=total_colis,
            total_peso_neto=total_peso_neto
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_deal_containers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _fetch_deal_container_context(deal_id: str, client) -> dict:
    """Load shared context needed to edit container line items.

    Caches deal almacen, company residennum/equ and fabricante cardcode so
    callers that need to edit several containers in one request reuse the
    same lookups.
    """
    ctx = {"deal_almacen": None, "residennum": None, "equ": None, "tipo_cliente": "", "tipo_contenedor": "", "fabricante_cardcode": None}

    try:
        deal_basic = client.crm.deals.basic_api.get_by_id(deal_id=deal_id, properties=["almacen", "tipo_contenedor"])
        ctx["deal_almacen"] = deal_basic.properties.get("almacen") or None
        ctx["tipo_contenedor"] = deal_basic.properties.get("tipo_contenedor") or ""
    except Exception as e:
        logger.warning(f"[edit_container] could not fetch deal almacen: {e}")

    try:
        company_ids = get_associated_companies(deal_id)
        if company_ids:
            company = client.crm.companies.basic_api.get_by_id(
                company_ids[0], properties=["residennum", "equ", "tipo_cliente"]
            )
            ctx["residennum"] = company.properties.get("residennum") or ""
            ctx["equ"] = company.properties.get("equ") or ""
            ctx["tipo_cliente"] = company.properties.get("tipo_cliente") or ""
    except Exception as e:
        logger.warning(f"[edit_container] could not fetch company residennum/equ: {e}")

    try:
        access_token = client.access_token
        provider_ids = _get_associated_ids(deal_id, "2-43108800")
        if provider_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/batch/read"
            batch_payload = {
                "properties": ["tipo_proveedor", "cardcode"],
                "inputs": [{"id": str(pid)} for pid in provider_ids]
            }
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            resp = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
            if resp.ok:
                for prov in resp.json().get("results", []):
                    props = prov.get("properties", {})
                    if (props.get("tipo_proveedor") or "").strip().lower() == "fabricante":
                        ctx["fabricante_cardcode"] = props.get("cardcode") or None
                        break
    except Exception as e:
        logger.warning(f"[edit_container] could not fetch fabricante cardcode: {e}")

    return ctx

_VALID_TIPO_ARTICULO_ELEM = {"fabricante", "servicio", "flete", "intermediario", "seguro"}

def _normalize_tipo_articulo_for_elemento(tipo_raw: str) -> str:
    """Map a line-item `tipo_articulo` to the elemento CO enum.
    HubSpot products may carry custom values (e.g. 'inspeccion', 'comisiones')
    that are invalid for the elemento custom object. Mirrors the mapping used
    in process_deal_custom_objects_from_line_items."""
    t = (tipo_raw or "").strip().lower()
    if t in _VALID_TIPO_ARTICULO_ELEM:
        return t
    if t == "comisiones":
        return "intermediario"
    if t == "transitario":
        return "flete"
    return "servicio"

def _find_intermediario_product(client) -> Optional[dict]:
    """Search the unique product with tipo_articulo='comisiones'. Returns its
    id + iva-related properties, or None if not found."""
    try:
        search = client.crm.products.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{"filters": [{"propertyName": "tipo_articulo", "operator": "EQ", "value": "comisiones"}]}],
                "properties": [
                    "name", "hs_sku",
                    "tipo_iva_soportado", "tipo_iva_repercutido",
                    "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                    "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex",
                ],
                "limit": 1,
            }
        )
        if not search.results:
            return None
        product = search.results[0]
        return {
            "id": str(product.id),
            "name": product.properties.get("name", "INTERMEDIARIO"),
            "sku": product.properties.get("hs_sku", "INTERMEDIARIO"),
            "properties": product.properties,
        }
    except Exception as e:
        logger.warning(f"[intermediario] product search failed: {e}")
        return None

def _parse_numero_contenedor(name: str) -> Optional[int]:
    """Número de un contenedor a partir de su nombre ('{deal_id}-007' → 7)."""
    try:
        return int(str(name or "").split("-")[-1])
    except (ValueError, IndexError, TypeError):
        return None

def _asignar_numeros_contenedor(ocupados, cantidad: int) -> List[int]:
    """Numera `cantidad` contenedores nuevos ocupando los huecos libres más bajos.

    `ocupados` son los números de los contenedores que sobreviven al merge. Los que
    se archivan en esta misma llamada liberan el suyo: numerar desde el máximo
    histórico hacía que rehacer los contenedores de una cotización los desplazara
    hacia arriba en cada intento (-013, -025, -035...) en lugar de volver a -001.
    """
    libres = []
    candidato = 1
    while len(libres) < cantidad:
        if candidato not in ocupados:
            libres.append(candidato)
        candidato += 1
    return libres

def _resolve_contenedor_co_by_numero(client, deal_id: str) -> dict:
    """Map every natural CO key (nombre, numero_container raw + padded + prefixed,
    line_item_id) → CONTENEDOR custom object ({id, properties}). The CO stores
    numero_container as an int ('1', '2'...) while line items use the full name
    ('{deal_id}-001'); indexing both forms lets callers lookup either way."""
    try:
        contenedores = get_contenedores_by_deal(
            client, deal_id, properties=["nombre", "numero_container", "line_item_id"]
        )
    except Exception as e:
        logger.warning(f"[contenedor-co] could not list custom objects for deal {deal_id}: {e}")
        return {}

    out = {}
    for co in contenedores:
        props = co.get("properties", {}) or {}
        entry = {"id": co.get("id"), "properties": props}
        numero_raw = (props.get("numero_container") or "").strip()
        nombre = (props.get("nombre") or "").strip()
        lid = (props.get("line_item_id") or "").strip()
        if numero_raw:
            out[numero_raw] = entry
            try:
                padded = f"{int(numero_raw):03d}"
                out[padded] = entry
                out[f"{deal_id}-{padded}"] = entry
            except (ValueError, TypeError):
                pass
        if nombre:
            out[nombre] = entry
        if lid:
            out[f"lid:{lid}"] = entry
    return out

def _fetch_intermediario_context(deal_id: str, client, residennum: Optional[str]) -> Optional[dict]:
    """Resolve intermediario product + commission settings for a deal.

    Returns None when intermediario is not active (repercutir_a_cliente != 'si'
    or importe_comision <= 0). Otherwise returns the data needed to recompute
    `importe_por_cajas` commissions in place.
    """
    try:
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=[
                "repercutir_a_cliente", "tipo_comision", "importe_comision",
                "codigo_intermediario", "tipo_iva_comision", "tipo_iva_comision_soportado"
            ]
        )
        repercutir = (deal.properties.get("repercutir_a_cliente") or "").strip().lower()
        tipo_comision = (deal.properties.get("tipo_comision") or "").strip().lower()
        importe = float(deal.properties.get("importe_comision") or 0)

        if repercutir != "si" or importe <= 0:
            return None

        product = _find_intermediario_product(client)
        if not product:
            return None

        deal_iva_rep = (deal.properties.get("tipo_iva_comision") or "").strip() or None
        deal_iva_sop = (deal.properties.get("tipo_iva_comision_soportado") or "").strip() or None

        return {
            "tipo_comision": tipo_comision,
            "importe_comision": importe,
            "codigo_intermediario": (deal.properties.get("codigo_intermediario") or "").strip(),
            "product_id": product["id"],
            "product_name": product["name"],
            "product_sku": product["sku"],
            "iva_repercutido": resolve_product_iva(product["properties"], residennum, "repercutido", deal_iva_rep),
            "iva_soportado": resolve_product_iva(product["properties"], residennum, "soportado", deal_iva_sop),
        }
    except Exception as e:
        logger.warning(f"[edit_container] could not fetch intermediario context: {e}")
        return None

def _lineas_cambiaron(actuales: Optional[set], previas) -> bool:
    """True solo si se pudo releer Y el conjunto difiere. Un fallo de lectura
    (actuales=None) no es prueba de cambio y no debe abortar la edición."""
    if actuales is None:
        return False
    return actuales != set(previas)


def _current_article_ids(deal_id: str, container_name: str, headers: dict) -> Optional[set]:
    """Ids de los line items de artículo vivos del contenedor. None si no se pudo leer
    (el caller no debe interpretar el fallo como 'cambió')."""
    try:
        line_item_ids = _get_associated_ids(deal_id, "line_items") or []
    except Exception as e:
        logger.warning(f"[edit_container] no pude releer los line items de {deal_id}: {e}")
        return None

    ids = set()
    batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
    for i in range(0, len(line_item_ids), 100):
        chunk = line_item_ids[i:i + 100]
        try:
            resp = requests.post(batch_url, headers=headers, json={
                "properties": ["numero_container", "tipo_articulo", "hs_product_type", "hs_sku"],
                "inputs": [{"id": str(x)} for x in chunk],
            }, timeout=30)
            if not resp.ok:
                logger.warning(f"[edit_container] releer line items falló: {resp.status_code}")
                return None
        except Exception as e:
            logger.warning(f"[edit_container] releer line items falló: {e}")
            return None
        for li in resp.json().get("results", []):
            props = li.get("properties", {}) or {}
            if (props.get("numero_container") or "").strip() != str(container_name).strip():
                continue
            if (props.get("tipo_articulo") or "").strip().lower() == "fabricante" and _line_item_is_article(props):
                ids.add(li.get("id"))
    return ids


def _edit_container_in_place(
    deal_id: str,
    container_line_item_id: str,
    new_config: ContainerConfigEdit,
    *,
    client=None,
    headers: Optional[dict] = None,
    ctx: Optional[dict] = None,
    intermediario_ctx: Optional[dict] = None,
    cont_map: Optional[dict] = None,
    num_contenedores_total: Optional[int] = None,
    defer_custom_objects: bool = False,
) -> dict:
    """Apply edit-in-place modifications to a single container.

    Diffs the new article list against the current line items (matched by
    `numero_container`), then updates / creates / deletes line items keeping
    HubSpot IDs stable. Mirrors the same diff onto the ELEMENTO custom objects
    when they already exist (cotización publicada). Returns the updated
    container in the same shape used by `unique_containers`.
    """
    if client is None:
        client = hubspot_client.get_client()
    if headers is None:
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    if ctx is None:
        ctx = _fetch_deal_container_context(deal_id, client)

    deal_almacen = ctx.get("deal_almacen")
    residennum = ctx.get("residennum")
    tipo_contenedor = ctx.get("tipo_contenedor") or ""
    fabricante_cardcode = ctx.get("fabricante_cardcode")

    container_li = requests.get(
        f"https://api.hubapi.com/crm/v3/objects/line_items/{container_line_item_id}",
        headers=headers,
        params={"properties": "name,numero_container,hs_product_id,hs_product_type,quantity"},
        timeout=30
    )
    if not container_li.ok:
        raise HTTPException(status_code=404, detail=f"Container line item {container_line_item_id} not found")

    container_props = container_li.json().get("properties", {})
    container_name = container_props.get("name") or container_props.get("numero_container")
    if not container_name:
        raise HTTPException(status_code=400, detail="Container line item missing name/numero_container")
    if not _is_container_product_type(container_props.get("hs_product_type") or ""):
        raise HTTPException(status_code=400, detail=f"Line item {container_line_item_id} is not a container")

    container_product_id = container_props.get("hs_product_id")
    container_type = "UNKNOWN"
    if container_product_id:
        try:
            prod_resp = requests.get(
                f"https://api.hubapi.com/crm/v3/objects/products/{container_product_id}",
                headers=headers,
                params={"properties": "hs_sku"},
                timeout=15
            )
            if prod_resp.ok:
                sku = (prod_resp.json().get("properties", {}).get("hs_sku") or "").strip()
                for tipo, cand in CONTAINER_SKU_MAP.items():
                    if cand == sku:
                        container_type = tipo
                        break
        except Exception as e:
            logger.warning(f"[edit_container] could not resolve container_type: {e}")

    try:
        container_number = int(str(container_name).split("-")[-1])
    except (ValueError, IndexError):
        container_number = 0

    existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
    current_articles = []
    current_intermediario = None

    if existing_line_item_ids:
        batch_size = 100
        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        for i in range(0, len(existing_line_item_ids), batch_size):
            chunk = existing_line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": [
                    "hs_product_type", "tipo_articulo", "numero_container",
                    "hs_product_id", "hs_sku", "name", "quantity",
                    "price", "precio_de_coste", "hs_price_eur", "margen_unitario",
                    "tipo_iva_repercutido", "tipo_iva_soportado",
                    "peso_neto", "peso_bruto", "cardcode",
                    "almacen", "name_en", "name_fr", "description", "formato", "marca_certificado"
                ],
                "inputs": [{"id": str(lid)} for lid in chunk]
            }
            resp = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
            if not resp.ok:
                logger.error(f"[edit_container] batch read failed: {resp.status_code} {resp.text}")
                continue
            for li in resp.json().get("results", []):
                props = li.get("properties", {})
                if (props.get("numero_container") or "").strip() != str(container_name).strip():
                    continue
                tipo_art = (props.get("tipo_articulo") or "").strip().lower()
                if tipo_art == "fabricante" and _line_item_is_article(props):
                    current_articles.append({"id": li.get("id"), "properties": props})
                elif tipo_art == "comisiones":
                    current_intermediario = {"id": li.get("id"), "properties": props}

    current_by_id = {a["id"]: a for a in current_articles}
    payload_articles = list(new_config.articulos or [])

    payload_with_id = [a for a in payload_articles if a.line_item_id and a.line_item_id in current_by_id]
    payload_new = [a for a in payload_articles if not a.line_item_id or a.line_item_id not in current_by_id]
    keep_ids = {a.line_item_id for a in payload_with_id}
    to_delete_ids = [aid for aid in current_by_id if aid not in keep_ids]

    new_product_ids = list({str(a.articuloId) for a in payload_new if a.articuloId})
    new_product_map = {}
    if new_product_ids:
        try:
            batch_read = client.crm.products.batch_api.read(
                batch_read_input_simple_public_object_id={
                    "properties": [
                        "name", "price", "hs_price_eur", "cardcode",
                        "peso_neto", "peso_bruto",
                        "name_en", "name_fr", "description", "formato", "marca_certificado",
                        "tipo_iva_soportado", "tipo_iva_repercutido",
                        "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                        "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex"
                    ],
                    "inputs": [{"id": pid} for pid in new_product_ids]
                }
            )
            for product in (batch_read.results or []):
                new_product_map[str(product.id)] = product.properties or {}
        except Exception as e:
            logger.error(f"[edit_container] error reading new article products: {e}")

    repair_product_ids = []
    for art in payload_with_id:
        cur = current_by_id[art.line_item_id]["properties"]
        if _safe_float(cur.get("precio_de_coste")) is None and cur.get("hs_product_id"):
            repair_product_ids.append(str(cur.get("hs_product_id")))
    repair_product_ids = list({pid for pid in repair_product_ids if pid})

    repair_price_map = {}
    if repair_product_ids:
        try:
            repair_batch = client.crm.products.batch_api.read(
                batch_read_input_simple_public_object_id={
                    "properties": ["hs_price_eur"],
                    "inputs": [{"id": pid} for pid in repair_product_ids],
                }
            )
            for product in (repair_batch.results or []):
                hs_price = _safe_float(product.properties.get("hs_price_eur"))
                if hs_price is not None:
                    repair_price_map[str(product.id)] = hs_price
        except Exception as e:
            logger.warning(f"[edit_container] repair batch read failed: {e}")

    update_inputs = []
    for art in payload_with_id:
        cur = current_by_id[art.line_item_id]["properties"]
        props = {"quantity": str(art.numCajas)}

        if _safe_float(cur.get("precio_de_coste")) is None:
            repaired = round_price(repair_price_map.get(str(cur.get("hs_product_id"))))
            if repaired is not None:
                cur["precio_de_coste"] = str(repaired)
                cur_price_existing = _safe_float(cur.get("price"))
                if cur_price_existing is not None and repaired > 0 and _safe_float(cur.get("margen_unitario")) is None:
                    margen = round_margin((cur_price_existing - repaired) / repaired)
                    if margen is not None:
                        cur["margen_unitario"] = str(margen)
                        props["margen_unitario"] = str(margen)

        if art.precio_de_coste is not None:
            props["price"] = str(round_price(art.precio_de_coste))
        elif art.price is not None:
            props["price"] = str(round_price(art.price))
        if art.margen_unitario is not None:
            margen_round = round_margin(art.margen_unitario)
            if margen_round is not None:
                props["margen_unitario"] = str(margen_round)
        if art.tipo_iva_repercutido:
            props["tipo_iva_repercutido"] = art.tipo_iva_repercutido
        if art.tipo_iva_soportado:
            props["tipo_iva_soportado"] = art.tipo_iva_soportado
        # Ancla del tipo de venta: si el payload no la trae se preserva la
        # persistida (el recálculo posterior re-deriva price desde ella); 0 la
        # limpia explícitamente (línea convertida a promo).
        ancla_edit = round_price(art.precio_negociado_tipo_venta)
        if ancla_edit is not None:
            ancla_ic_edit = _normalize_incoterm(art.incoterm_precio_negociado) if art.incoterm_precio_negociado else ""
            if ancla_edit > 0 and ancla_ic_edit:
                props["precio_negociado_tipo_venta"] = str(ancla_edit)
                props["incoterm_precio_negociado"] = ancla_ic_edit
            elif ancla_edit == 0:
                props["precio_negociado_tipo_venta"] = ""
                props["incoterm_precio_negociado"] = ""
        update_inputs.append({"id": art.line_item_id, "properties": props})

    if update_inputs:
        upd_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
        for i in range(0, len(update_inputs), 100):
            chunk = update_inputs[i:i + 100]
            for x in chunk:
                enforce_line_item_price_rounding(x.get("properties", {}))
            resp = requests.post(upd_url, headers=headers, json={"inputs": chunk}, timeout=60)
            if not resp.ok:
                logger.error(f"[edit_container] batch update failed: {resp.status_code} {resp.text}")
                raise HTTPException(status_code=500, detail=f"Failed to update articles: {resp.text}")

    create_inputs = []
    new_articles_resolved = []
    for art in payload_new:
        sku = str(art.articuloId)
        product_props = new_product_map.get(sku, {})
        article_name = product_props.get("name") or art.articuloName or art.nombre or sku
        unique_name = f"{container_name} - {article_name}"

        iva_rep = art.tipo_iva_repercutido or resolve_product_iva(product_props, residennum, "repercutido")
        iva_sop = art.tipo_iva_soportado or resolve_product_iva(product_props, residennum, "soportado")
        cardcode = fabricante_cardcode or product_props.get("cardcode") or ""
        hs_price_eur = round_price(product_props.get("hs_price_eur"))
        precio_coste = round_price(
            art.precio_de_coste if art.precio_de_coste is not None else hs_price_eur
        )
        if precio_coste is None:
            logger.warning(
                f"[edit_container] producto sku={sku} sin hs_price_eur — "
                f"precio_de_coste quedará vacío en line item y elemento"
            )

        properties = {
            "hs_product_id": sku,
            "hs_sku": sku,
            "name": unique_name,
            "price": "" if art.price is None else str(round_price(art.price)),
            "quantity": str(art.numCajas),
            "cardcode": cardcode,
            "tipo_articulo": "fabricante",
            "hs_product_type": "Articulo",
            "numero_container": container_name,
            "tipo_iva_repercutido": iva_rep,
            "tipo_iva_soportado": iva_sop,
            "hs_currency": "EUR",
        }
        if art.margen_unitario is not None:
            margen_round = round_margin(art.margen_unitario)
            if margen_round is not None:
                properties["margen_unitario"] = str(margen_round)
        for src, dst in [("peso_neto", "peso_neto"), ("peso_bruto", "peso_bruto"),
                         ("name_en", "name_en"), ("name_fr", "name_fr"),
                         ("description", "description"),
                         ("formato", "formato"), ("marca_certificado", "marca_certificado")]:
            val = product_props.get(src)
            if val is not None and str(val).strip() != "":
                properties[dst] = str(val)
        # Producto (fabricante): conserva el almacén del deal (San Roque incluido)
        almacen_articulo = resolve_almacen_para_elemento(deal_almacen, "fabricante", tipo_contenedor)
        if almacen_articulo:
            properties["almacen"] = str(almacen_articulo)

        create_inputs.append({
            "properties": properties,
            "associations": [{
                "to": {"id": deal_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
            }]
        })
        new_articles_resolved.append({
            "edit": art,
            "product_props": product_props,
            "article_name": article_name,
            "unique_name": unique_name,
            "iva_rep": iva_rep,
            "iva_sop": iva_sop,
            "precio_coste": precio_coste,
            "cardcode": cardcode,
        })

    if create_inputs:
        # Segunda barrera además del lock por deal del endpoint: si otra petición se
        # coló entre la lectura inicial y este punto, crear aquí duplicaría el
        # contenedor entero. Releer es barato comparado con ese daño.
        actuales = _current_article_ids(deal_id, container_name, headers)
        if _lineas_cambiaron(actuales, current_by_id):
            logger.error(
                f"[edit_container] {container_name}: las líneas cambiaron durante la edición "
                f"(antes={sorted(current_by_id)}, ahora={sorted(actuales)}); aborto sin crear"
            )
            raise HTTPException(status_code=409, detail={
                "error_code": "edit_in_progress",
                "message": "El contenedor cambió mientras se guardaba. Vuelve a abrirlo y repite el cambio.",
            })

        # Create de uno en uno: con la promo 5+1 el mismo artículo se repite en el
        # contenedor (sku, nombre y numero_container idénticos), así que ninguna
        # propiedad distingue las líneas dentro de un batch. El create individual
        # devuelve el id exacto de cada input —correlación 1:1 con new_articles_resolved,
        # construidos en el mismo orden— y evita que un line_item_id quede colgado
        # del artículo equivocado si HubSpot reordena los results del batch.
        cr_url = "https://api.hubapi.com/crm/v3/objects/line_items"
        for resolved, inp in zip(new_articles_resolved, create_inputs):
            enforce_line_item_price_rounding(inp.get("properties", {}))
            resp = requests.post(cr_url, headers=headers, json=inp, timeout=60)
            if not resp.ok:
                logger.error(f"[edit_container] create failed: {resp.status_code} {resp.text}")
                raise HTTPException(status_code=500, detail=f"Failed to create articles: {resp.text}")
            resolved["new_line_item_id"] = resp.json().get("id")

    if to_delete_ids:
        del_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
        for i in range(0, len(to_delete_ids), 100):
            chunk = to_delete_ids[i:i + 100]
            payload = {"inputs": [{"id": str(x)} for x in chunk]}
            resp = requests.post(del_url, headers=headers, json=payload, timeout=30)
            if not resp.ok:
                logger.error(f"[edit_container] batch delete failed: {resp.status_code} {resp.text}")

    final_articles = []
    for art in payload_with_id:
        cur_props = current_by_id[art.line_item_id]["properties"]
        nombre = art.nombre or art.articuloName or cur_props.get("name", "").split(" - ", 1)[-1]
        cur_price = _safe_float(cur_props.get("price"))
        cur_coste = _safe_float(cur_props.get("precio_de_coste"))
        cur_margen = _safe_float(cur_props.get("margen_unitario"))
        final_articles.append({
            "line_item_id": art.line_item_id,
            "articuloId": art.articuloId,
            "nombre": nombre,
            "numCajas": art.numCajas,
            "price": art.price if art.price is not None else cur_price,
            "precio_de_coste": (
                art.precio_de_coste if art.precio_de_coste is not None else cur_coste
            ),
            "margen_unitario": (
                art.margen_unitario if art.margen_unitario is not None else cur_margen
            ),
            "tipo_iva_repercutido": art.tipo_iva_repercutido or cur_props.get("tipo_iva_repercutido"),
            "tipo_iva_soportado": art.tipo_iva_soportado or cur_props.get("tipo_iva_soportado"),
        })
    for resolved in new_articles_resolved:
        art = resolved["edit"]
        final_articles.append({
            "line_item_id": resolved.get("new_line_item_id"),
            "articuloId": art.articuloId,
            "nombre": resolved["article_name"],
            "numCajas": art.numCajas,
            "price": art.price,
            "precio_de_coste": resolved["precio_coste"],
            "margen_unitario": art.margen_unitario,
            "tipo_iva_repercutido": resolved["iva_rep"],
            "tipo_iva_soportado": resolved["iva_sop"],
        })

    container_config_serializable = {
        "articulos": [
            {"articuloId": a["articuloId"], "nombre": a["nombre"], "numCajas": a["numCajas"]}
            for a in final_articles
        ],
        "ocupacion": new_config.ocupacion,
        "pesoNeto": new_config.pesoNeto,
    }

    container_update_props = {
        "container_config": json.dumps(container_config_serializable),
        "ocupacion": str(new_config.ocupacion),
    }
    # La línea de contenedor no es producto: se reubica fuera de San Roque (10/12)
    almacen_contenedor = resolve_almacen_para_elemento(deal_almacen, None, tipo_contenedor)
    if almacen_contenedor:
        container_update_props["almacen"] = str(almacen_contenedor)
    try:
        client.crm.line_items.basic_api.update(
            line_item_id=container_line_item_id,
            simple_public_object_input={"properties": container_update_props}
        )
    except Exception as e:
        logger.error(f"[edit_container] failed updating container line item: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update container line item: {e}")

    total_cajas_new = sum(a["numCajas"] for a in final_articles)
    if intermediario_ctx is None:
        intermediario_ctx = _fetch_intermediario_context(deal_id, client, residennum)

    elementos_synced = False
    contenedor_co_id = None
    numero_container = (container_props.get("numero_container") or "").strip() or container_name
    if cont_map is None:
        cont_map = _resolve_contenedor_co_by_numero(client, deal_id)
    co = (
        cont_map.get(numero_container)
        or cont_map.get(container_name)
        or cont_map.get(f"lid:{container_line_item_id}")
    )
    if co:
        contenedor_co_id = co.get("id")

    had_intermediario = current_intermediario is not None
    deal_wants_intermediario = None
    try:
        deal_int = client.crm.deals.basic_api.get_by_id(
            deal_id, properties=["repercutir_a_cliente", "importe_comision"]
        )
        deal_wants_intermediario = (
            (deal_int.properties.get("repercutir_a_cliente") or "").strip().lower() == "si"
            and float(deal_int.properties.get("importe_comision") or 0) > 0
        )
    except Exception as e:
        logger.warning(f"[edit_container] could not read deal commission state: {e}")

    wants_intermediario = (
        deal_wants_intermediario if deal_wants_intermediario is not None
        else intermediario_ctx is not None
    )
    intermediario_state_change = None
    new_intermediario_lid = None
    intermediario_recalculated = False
    intermediario_price_for_create = None

    if wants_intermediario and intermediario_ctx:
        tipo_com = intermediario_ctx.get("tipo_comision") or ""
        importe_com = float(intermediario_ctx.get("importe_comision") or 0)
        if tipo_com == "importe_por_negocio":
            # Reparte la comisión entre TODOS los contenedores del negocio. El caller
            # pasa el total final (previos + nuevos); si no, se cuentan los COs
            # distintos (cont_map indexa cada contenedor bajo varias claves, así que
            # len(cont_map) no sirve como recuento).
            if num_contenedores_total:
                num_total = max(num_contenedores_total, 1)
            else:
                num_total = max(len({e.get("id") for e in cont_map.values() if e.get("id")}), 1)
            intermediario_price_for_create = round_price(importe_com / num_total)
        elif tipo_com == "importe_por_cajas":
            intermediario_price_for_create = round_price(importe_com * total_cajas_new)
        else:
            intermediario_price_for_create = round_price(importe_com)

    if wants_intermediario and had_intermediario and intermediario_ctx:
        if intermediario_ctx.get("tipo_comision") == "importe_por_cajas":
            nuevo_importe = round_price(float(intermediario_ctx["importe_comision"]) * total_cajas_new)
            try:
                client.crm.line_items.basic_api.update(
                    line_item_id=current_intermediario["id"],
                    simple_public_object_input={"properties": {"price": str(nuevo_importe)}}
                )
                intermediario_recalculated = True
            except Exception as e:
                logger.error(f"[edit_container] failed updating intermediario line item: {e}")
    elif wants_intermediario and not had_intermediario and intermediario_ctx:
        intermediario_props = {
            "hs_product_id": intermediario_ctx["product_id"],
            "hs_sku": intermediario_ctx["product_id"],
            "name": f"{container_name} - {intermediario_ctx['product_name']}",
            "price": str(intermediario_price_for_create or 0),
            "quantity": "1",
            "cardcode": intermediario_ctx.get("codigo_intermediario") or "",
            "numero_container": container_name,
            "tipo_articulo": "comisiones",
            "hs_product_type": "Servicio",
            "hs_currency": "EUR",
        }
        if intermediario_ctx.get("iva_repercutido"):
            intermediario_props["tipo_iva_repercutido"] = intermediario_ctx["iva_repercutido"]
        if intermediario_ctx.get("iva_soportado"):
            intermediario_props["tipo_iva_soportado"] = intermediario_ctx["iva_soportado"]
        # Comisiones/intermediario: no es producto → se reubica fuera de San Roque (10/12)
        almacen_intermediario = resolve_almacen_para_elemento(deal_almacen, "comisiones", tipo_contenedor)
        if almacen_intermediario:
            intermediario_props["almacen"] = str(almacen_intermediario)
        enforce_line_item_price_rounding(intermediario_props)
        create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
        payload = {"inputs": [{
            "properties": intermediario_props,
            "associations": [{
                "to": {"id": deal_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}],
            }],
        }]}
        try:
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            if resp.ok:
                created = (resp.json().get("results") or [{}])[0]
                new_intermediario_lid = created.get("id")
                if new_intermediario_lid:
                    intermediario_state_change = "created"
                    logger.info(f"[edit_container] created intermediario line item {new_intermediario_lid} for {container_name}")
            else:
                logger.error(f"[edit_container] create intermediario failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"[edit_container] error creating intermediario line item: {e}")
    elif had_intermediario and not wants_intermediario:
        try:
            client.crm.line_items.basic_api.archive(line_item_id=current_intermediario["id"])
            intermediario_state_change = "deleted"
            logger.info(f"[edit_container] archived intermediario line item {current_intermediario['id']} for {container_name}")
        except Exception as e:
            logger.error(f"[edit_container] failed archiving intermediario line item: {e}")

    # defer_custom_objects: el CRUD de line items (arriba) es síncrono; el trabajo
    # derivado (Elemento/Contenedor CO, proveedores, y aguas abajo recalcular precios
    # y touch_pagos SAP) lo reconstruye el worker de re-sync desde los line items.
    if contenedor_co_id and not defer_custom_objects:
        elementos_synced = _sync_elementos_for_container(
            client=client,
            deal_id=deal_id,
            contenedor_co_id=contenedor_co_id,
            container_name=container_name,
            payload_with_id=payload_with_id,
            new_articles_resolved=new_articles_resolved,
            to_delete_line_item_ids=to_delete_ids,
            current_by_id=current_by_id,
            intermediario_ctx=intermediario_ctx,
            current_intermediario=current_intermediario,
            intermediario_recalculated=intermediario_recalculated,
            intermediario_state_change=intermediario_state_change,
            new_intermediario_lid=new_intermediario_lid,
            intermediario_price_for_create=intermediario_price_for_create,
            total_cajas_new=total_cajas_new,
            deal_almacen=deal_almacen,
            tipo_contenedor=tipo_contenedor,
        )

        try:
            update_contenedor(client, contenedor_co_id, {
                "ocupacion": new_config.ocupacion,
                "total_cajas": total_cajas_new,
                "container_config": json.dumps(container_config_serializable),
            })
        except Exception as e:
            logger.warning(f"[edit_container] could not update CONTENEDOR custom object: {e}")

    return {
        "line_item_id": container_line_item_id,
        "contenedor_co_id": contenedor_co_id,
        "container_name": container_name,
        "container_type": container_type,
        "container_number": container_number,
        "cantidad": int(container_props.get("quantity") or 1),
        "container_config": {
            "articulos": [
                {
                    "articuloId": a["articuloId"],
                    "nombre": a["nombre"],
                    "articuloName": f"{container_name} - {a['nombre']}",
                    "numCajas": a["numCajas"],
                    "line_item_id": a["line_item_id"],
                    "price": a["price"],
                    "precio_de_coste": a["precio_de_coste"],
                    "margen_unitario": a["margen_unitario"],
                    "tipo_iva_repercutido": a["tipo_iva_repercutido"],
                    "tipo_iva_soportado": a["tipo_iva_soportado"],
                }
                for a in final_articles
            ],
            "ocupacion": new_config.ocupacion,
            "pesoNeto": new_config.pesoNeto,
        },
        "elementos_synced": elementos_synced,
        "intermediario_recalculated": intermediario_recalculated,
    }

def _sync_elementos_for_container(
    *,
    client,
    deal_id: str,
    contenedor_co_id: str,
    container_name: str,
    payload_with_id: list,
    new_articles_resolved: list,
    to_delete_line_item_ids: list,
    current_by_id: dict,
    intermediario_ctx: Optional[dict],
    current_intermediario: Optional[dict],
    intermediario_recalculated: bool,
    total_cajas_new: int,
    deal_almacen: Optional[str] = None,
    tipo_contenedor: str = "",
    intermediario_state_change: Optional[str] = None,
    new_intermediario_lid: Optional[str] = None,
    intermediario_price_for_create: Optional[float] = None,
) -> bool:
    """Sync ELEMENTO custom objects with the just-applied line item changes."""
    try:
        elementos = get_elementos_by_contenedor(
            client, contenedor_co_id,
            properties=["nombre", "tipo_articulo", "cantidad", "precio_unitario",
                        "precio_de_coste", "precio_total", "margen_unitario",
                        "peso_neto_unitario", "peso_bruto_unitario",
                        "tipo_iva_repercutido", "tipo_iva_soportado", "line_item_id"]
        )
    except Exception as e:
        logger.error(f"[sync_elementos] failed loading ELEMENTOS for {contenedor_co_id}: {e}")
        return False

    elementos_by_lid = {}
    elementos_by_nombre = {}
    nombre_counts = {}
    intermediario_elemento = None
    for el in elementos:
        props = el.get("properties", {}) or {}
        lid = props.get("line_item_id")
        nombre = props.get("nombre")
        tipo_art = (props.get("tipo_articulo") or "").lower()
        entry = {"id": el.get("id"), "properties": props}
        if lid:
            elementos_by_lid[str(lid).strip()] = entry
        if nombre:
            nombre_counts[nombre.strip()] = nombre_counts.get(nombre.strip(), 0) + 1
            elementos_by_nombre[nombre.strip()] = entry
        if tipo_art in ("comisiones", "intermediario"):
            if current_intermediario and str(lid) == str(current_intermediario.get("id")):
                intermediario_elemento = entry
            elif intermediario_elemento is None:
                intermediario_elemento = entry

    contact_id = None
    company_id = None
    try:
        cids = get_associated_contacts(deal_id)
        if cids:
            contact_id = cids[0]
    except Exception:
        pass
    try:
        coids = get_associated_companies(deal_id)
        if coids:
            company_id = coids[0]
    except Exception:
        pass

    matched = 0
    orphan_created = 0
    for art in payload_with_id:
        target_lid = str(art.line_item_id).strip()
        li_name_actual = (
            (current_by_id.get(art.line_item_id, {}).get("properties", {}) or {}).get("name") or ""
        ).strip()
        elem = elementos_by_lid.get(target_lid)
        # lid cruzado (rotación batch previa): el Elemento resuelto por lid no es el
        # de este line item. Se descarta para reemparejar por contenido y, más abajo,
        # autocorregir el line_item_id.
        if elem and li_name_actual and (elem["properties"].get("nombre") or "").strip() != li_name_actual:
            elem = None
        if not elem:
            cur_props = current_by_id.get(art.line_item_id, {}).get("properties", {}) or {}
            li_name = (cur_props.get("name") or "").strip()
            # Solo emparejar por nombre si es único en el contenedor; con líneas
            # duplicadas (promo 5+1) el nombre es ambiguo y crearíamos/borraríamos
            # el Elemento equivocado, así que dejamos que se cree por su line_item_id.
            if li_name and nombre_counts.get(li_name, 0) <= 1:
                elem = elementos_by_nombre.get(li_name)
            if (
                not elem
                and art.articuloName
                and nombre_counts.get(str(art.articuloName).strip(), 0) <= 1
            ):
                elem = elementos_by_nombre.get(str(art.articuloName).strip())
        if not elem:
            cur_props = current_by_id.get(art.line_item_id, {}).get("properties", {}) or {}
            li_name = (cur_props.get("name") or "").strip() or (
                str(art.articuloName).strip() if art.articuloName else ""
            )
            precio_unit_orphan = round_price(art.price) if art.price is not None else _safe_float(cur_props.get("price"))
            precio_coste_orphan = round_price(art.precio_de_coste) if art.precio_de_coste is not None else round_price(
                cur_props.get("hs_price_eur") or cur_props.get("precio_de_coste")
            )
            cantidad_orphan = float(art.numCajas or 0) or _safe_float(cur_props.get("quantity")) or 1
            precio_total_orphan = round((precio_unit_orphan or 0) * cantidad_orphan, 3)
            orphan_data = [{
                "nombre": li_name,
                "nombre_contenedor": container_name,
                "tipo_articulo": "fabricante",
                "cantidad": cantidad_orphan,
                "product_sku": cur_props.get("hs_sku") or "",
                "precio_unitario": precio_unit_orphan,
                "precio_de_coste": precio_coste_orphan,
                "precio_total": precio_total_orphan,
                "margen_unitario": round_margin(art.margen_unitario) if art.margen_unitario is not None else _safe_float(cur_props.get("margen_unitario")),
                "peso_neto_unitario": _safe_float(cur_props.get("peso_neto")),
                "peso_bruto_unitario": _safe_float(cur_props.get("peso_bruto")),
                "cardcode": cur_props.get("cardcode") or "",
                "tipo_iva_repercutido": art.tipo_iva_repercutido or cur_props.get("tipo_iva_repercutido"),
                "tipo_iva_soportado": art.tipo_iva_soportado or cur_props.get("tipo_iva_soportado"),
                "line_item_id": target_lid,
                "nombre_es": cur_props.get("description"),
                "nombre_en": cur_props.get("name_en"),
                "nombre_fr": cur_props.get("name_fr"),
                "formato": cur_props.get("formato"),
                "marca": cur_props.get("marca_certificado"),
            }]
            almacen_orphan = resolve_almacen_para_elemento(deal_almacen, "fabricante", tipo_contenedor)
            if almacen_orphan:
                orphan_data[0]["almacen"] = str(almacen_orphan)
            try:
                created = batch_create_elementos_with_associations(
                    client=client,
                    elementos_data=orphan_data,
                    deal_id=deal_id,
                    contenedor_id=contenedor_co_id,
                    contact_id=contact_id,
                    company_id=company_id,
                )
                if created:
                    orphan_created += 1
                else:
                    logger.warning(
                        f"[sync_elementos] orphan fabricante create returned empty for line_item_id={target_lid}"
                    )
            except Exception as e:
                logger.warning(
                    f"[sync_elementos] orphan fabricante create failed for line_item_id={target_lid}: {e}"
                )
            continue

        if elem["properties"].get("line_item_id") != str(art.line_item_id):
            try:
                update_elemento(
                    client, elem["id"], {"line_item_id": str(art.line_item_id)}
                )
            except Exception as e:
                logger.warning(
                    f"[sync_elementos] failed to reconcile ELEMENTO {elem['id']} line_item_id: {e}"
                )

        precio_unit = art.price if art.price is not None else (
            _safe_float(elem["properties"].get("precio_unitario"))
        )
        precio_unit_round = round_price(precio_unit)
        # precio_total no se toca: lo fija recalcular_contenedores tras la edición
        # (cuadre con el precio negociado a nivel de contenedor).
        update_props = {
            "cantidad": art.numCajas,
        }
        if precio_unit_round is not None:
            update_props["precio_unitario"] = precio_unit_round

        precio_coste_target = None
        if art.precio_de_coste is not None:
            precio_coste_target = round_price(art.precio_de_coste)
        else:
            cur_li_props = current_by_id.get(art.line_item_id, {}).get("properties", {}) or {}
            precio_coste_target = round_price(
                cur_li_props.get("hs_price_eur") or cur_li_props.get("precio_de_coste")
            )
            if precio_coste_target is None:
                logger.warning(
                    f"[sync_elementos] line_item_id={art.line_item_id} sin hs_price_eur — "
                    f"precio_de_coste del elemento no se reconcilia"
                )
        if precio_coste_target is not None:
            update_props["precio_de_coste"] = precio_coste_target

        if art.margen_unitario is not None:
            margen_round = round_margin(art.margen_unitario)
            if margen_round is not None:
                update_props["margen_unitario"] = margen_round
        if art.tipo_iva_repercutido:
            update_props["tipo_iva_repercutido"] = art.tipo_iva_repercutido
        if art.tipo_iva_soportado:
            update_props["tipo_iva_soportado"] = art.tipo_iva_soportado

        if update_elemento(client, elem["id"], update_props):
            matched += 1

    logger.info(
        f"[sync_elementos] updated={matched}/{len(payload_with_id)} "
        f"orphan_created={orphan_created} "
        f"new={len(new_articles_resolved)} deleted={len(to_delete_line_item_ids)}"
    )

    new_elementos_data = []
    for resolved in new_articles_resolved:
        art = resolved["edit"]
        product_props = resolved["product_props"]
        precio_unit = round_price(art.price) if art.price is not None else None
        precio_total = round((precio_unit or 0) * (art.numCajas or 0), 3) if precio_unit is not None else None
        peso_neto_unit = _safe_float(product_props.get("peso_neto"))
        peso_bruto_unit = _safe_float(product_props.get("peso_bruto"))
        new_elementos_data.append({
            "nombre": resolved["unique_name"],
            "nombre_contenedor": container_name,
            "tipo_articulo": "fabricante",
            "cantidad": art.numCajas,
            "product_sku": str(art.articuloId),
            "precio_unitario": precio_unit,
            "precio_de_coste": round_price(resolved["precio_coste"]),
            "precio_total": precio_total,
            "margen_unitario": art.margen_unitario,
            "peso_neto_unitario": peso_neto_unit,
            "peso_bruto_unitario": peso_bruto_unit,
            "cardcode": resolved["cardcode"],
            "tipo_iva_repercutido": resolved["iva_rep"],
            "tipo_iva_soportado": resolved["iva_sop"],
            "line_item_id": resolved.get("new_line_item_id"),
            "nombre_es": product_props.get("description"),
            "nombre_en": product_props.get("name_en"),
            "nombre_fr": product_props.get("name_fr"),
            "formato": product_props.get("formato"),
            "marca": product_props.get("marca_certificado"),
        })
        almacen_nuevo = resolve_almacen_para_elemento(deal_almacen, "fabricante", tipo_contenedor)
        if almacen_nuevo:
            new_elementos_data[-1]["almacen"] = str(almacen_nuevo)
    if new_elementos_data:
        batch_create_elementos_with_associations(
            client=client,
            elementos_data=new_elementos_data,
            deal_id=deal_id,
            contenedor_id=contenedor_co_id,
            contact_id=contact_id,
            company_id=company_id,
        )

    for lid in to_delete_line_item_ids:
        elem = elementos_by_lid.get(str(lid).strip())
        if elem:
            delete_elemento(client, elem["id"])

    if intermediario_recalculated and intermediario_elemento and intermediario_ctx:
        nuevo_importe = round_price(float(intermediario_ctx["importe_comision"]) * total_cajas_new)
        update_elemento(client, intermediario_elemento["id"], {
            "cantidad": 1,
            "precio_unitario": nuevo_importe,
            "precio_de_coste": nuevo_importe,
            "precio_total": nuevo_importe,
        })

    if intermediario_state_change == "created" and new_intermediario_lid and intermediario_ctx:
        precio_int = round_price(intermediario_price_for_create or 0)
        elem_data = [{
            "nombre": f"{container_name} - {intermediario_ctx['product_name']}",
            "nombre_contenedor": container_name,
            "tipo_articulo": "intermediario",
            "cantidad": 1,
            "precio_unitario": precio_int,
            "precio_de_coste": precio_int,
            "precio_total": precio_int,
            "cardcode": intermediario_ctx.get("codigo_intermediario") or "",
            "line_item_id": str(new_intermediario_lid),
        }]
        if intermediario_ctx.get("iva_repercutido"):
            elem_data[0]["tipo_iva_repercutido"] = intermediario_ctx["iva_repercutido"]
        if intermediario_ctx.get("iva_soportado"):
            elem_data[0]["tipo_iva_soportado"] = intermediario_ctx["iva_soportado"]
        # Intermediario: no es producto → se reubica fuera de San Roque (10/12)
        almacen_int_elem = resolve_almacen_para_elemento(deal_almacen, "intermediario", tipo_contenedor)
        if almacen_int_elem:
            elem_data[0]["almacen"] = str(almacen_int_elem)
        try:
            batch_create_elementos_with_associations(
                client=client,
                elementos_data=elem_data,
                deal_id=deal_id,
                contenedor_id=contenedor_co_id,
                contact_id=contact_id,
                company_id=company_id,
            )
        except Exception as e:
            logger.warning(f"[sync_elementos] failed creating intermediario elemento: {e}")
    elif intermediario_state_change == "deleted" and intermediario_elemento:
        try:
            delete_elemento(client, intermediario_elemento["id"])
        except Exception as e:
            logger.warning(f"[sync_elementos] failed deleting intermediario elemento: {e}")

    try:
        sync_contenedor_proveedor_associations(client, deal_id, [contenedor_co_id])
    except Exception as e:
        logger.warning(f"[sync_elementos] sync_contenedor_proveedor_associations falló: {e}")

    return True

def _safe_float(value: Any) -> Optional[float]:
    """Convert HubSpot string property to float when possible."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _resolve_container_line_item_id(
    deal_id: str,
    container_line_item_id: str,
    numero_container: Optional[str],
    *,
    client,
    headers: Optional[dict] = None,
) -> str:
    """Devuelve el line_item_id del contenedor a editar. Si el id recibido ya no
    existe (stale tras una recreación previa) y se conoce su `numero_container`/
    nombre, lo resuelve buscando el line item de contenedor del deal con ese
    numero_container. Si no se puede resolver, devuelve el id original (el caller
    fallará con 404, comportamiento previo)."""
    if headers is None:
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    try:
        resp = requests.get(
            f"https://api.hubapi.com/crm/v3/objects/line_items/{container_line_item_id}",
            headers=headers, params={"properties": "hs_product_type"}, timeout=15,
        )
        if resp.ok:
            return container_line_item_id
    except Exception as e:
        logger.warning(f"[resolve_container] GET {container_line_item_id} falló: {e}")

    numero = (numero_container or "").strip()
    if not numero:
        return container_line_item_id

    try:
        line_item_ids = _get_associated_ids(deal_id, "line_items") or []
    except Exception as e:
        logger.warning(f"[resolve_container] no pude listar line items de {deal_id}: {e}")
        return container_line_item_id

    batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
    for i in range(0, len(line_item_ids), 100):
        chunk = line_item_ids[i:i + 100]
        try:
            r = requests.post(batch_url, headers=headers, json={
                "properties": ["hs_product_type", "numero_container", "name"],
                "inputs": [{"id": str(x)} for x in chunk],
            }, timeout=30)
            if not r.ok:
                continue
            for li in r.json().get("results", []):
                p = li.get("properties", {}) or {}
                if not _is_container_product_type(p.get("hs_product_type") or ""):
                    continue
                if (p.get("numero_container") or "").strip() == numero or (p.get("name") or "").strip() == numero:
                    resolved = str(li.get("id"))
                    logger.warning(
                        f"[resolve_container] line_item_id stale {container_line_item_id} → "
                        f"resuelto {resolved} por numero_container={numero!r}"
                    )
                    return resolved
        except Exception as e:
            logger.warning(f"[resolve_container] batch read falló: {e}")

    logger.warning(f"[resolve_container] no se resolvió numero_container={numero!r}; se usa id original {container_line_item_id}")
    return container_line_item_id

@router.patch("/{deal_id}/containers/{container_line_item_id}", response_model=UpdateSingleContainerResponse)
async def edit_single_container(
    data: UpdateSingleContainerRequest,
    deal_id: str = Path(..., description="HubSpot deal ID"),
    container_line_item_id: str = Path(..., description="Container line item ID to edit"),
):
    """Edit one container in place: articles + box counts + ELEMENTO custom objects.

    Replaces the clear+replace flow when a container already exists, so SAP-bound
    line item and ELEMENTO IDs stay stable. Recomputes the matching commission
    line item when `tipo_comision == importe_por_cajas`.

    Serializado por deal: dos peticiones simultáneas leerían el mismo estado y
    ambas crearían su juego completo de líneas (incidencia 2026-08-28).
    """
    lock_resource = deal_lock.deal_resource(deal_id)
    lock_token = deal_lock.acquire(lock_resource)
    if lock_token is None:
        raise HTTPException(status_code=409, detail={
            "error_code": "edit_in_progress",
            "message": "Hay otra edición de este negocio en curso. Espera a que termine.",
        })
    try:
        client = hubspot_client.get_client()
        if isinstance(data, dict):
            data = UpdateSingleContainerRequest(**data)

        logger.info(
            f"[edit_single_container] PATCH deal_id={deal_id} "
            f"container_line_item_id={container_line_item_id} "
            f"articulos_count={len(data.container_config.articulos)}"
        )

        effective_lid = _resolve_container_line_item_id(
            deal_id,
            container_line_item_id,
            getattr(data, "numero_container", None),
            client=client,
        )

        ctx = _fetch_deal_container_context(deal_id, client)
        result = _edit_container_in_place(
            deal_id=deal_id,
            container_line_item_id=effective_lid,
            new_config=data.container_config,
            client=client,
            ctx=ctx,
        )

        co_id = result.get("contenedor_co_id")
        if co_id:
            try:
                await hubdb_client.preload_iva_table()
                recalcular_contenedores(
                    client,
                    deal_id,
                    [co_id],
                    {"equ": ctx.get("equ") or "", "residennum": ctx.get("residennum") or ""},
                )
            except Exception as e:
                logger.warning(f"[edit_single_container] recalcular_contenedores falló: {e}")
            touch_pagos_for_contenedores(client, [co_id])

        unique_articles = [
            UniqueContainerArticle(
                articuloId=a["articuloId"],
                nombre=a["nombre"],
                articuloName=a["articuloName"],
                numCajas=a["numCajas"],
                line_item_id=a.get("line_item_id"),
                price=a.get("price"),
                precio_de_coste=a.get("precio_de_coste"),
                margen_unitario=a.get("margen_unitario"),
                tipo_iva_repercutido=a.get("tipo_iva_repercutido"),
                tipo_iva_soportado=a.get("tipo_iva_soportado"),
            )
            for a in result["container_config"]["articulos"]
        ]
        unique_config = UniqueContainerConfig(
            articulos=unique_articles,
            ocupacion=result["container_config"]["ocupacion"],
            pesoNeto=result["container_config"]["pesoNeto"],
        )
        unique_container = UniqueContainerItem(
            line_item_id=result["line_item_id"],
            container_type=result["container_type"],
            container_number=result["container_number"],
            container_name=result["container_name"],
            cantidad=result["cantidad"],
            container_config=unique_config,
        )

        return UpdateSingleContainerResponse(
            success=True,
            error=None,
            container=unique_container,
            elementos_synced=result["elementos_synced"],
            intermediario_recalculated=result["intermediario_recalculated"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[edit_single_container] error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        deal_lock.release(lock_resource, lock_token)

def _enqueue_custom_objects_resync_if_published(client, deal_id: str) -> Optional[str]:
    """Re-sincroniza Contenedor/Elemento del deal con sus line items actuales, pero
    SOLO si el deal ya fue publicado (ya tiene Contenedor custom objects). Como el
    CRUD de line items corrió con defer_custom_objects=True, sin este re-sync los
    Elemento quedarían apuntando a line items muertos (STALE). Antes de la primera
    publicación no hay custom objects y no hay nada que sincronizar.

    Devuelve el job_id encolado (para que el frontend lo pollee) o None si el deal no
    está publicado. Si el deal SÍ está publicado pero el encolado falla (SQS/DynamoDB
    caídos, o entorno local sin credenciales AWS), hace fallback a un re-sync SÍNCRONO
    inline para no dejar los COs stale, y devuelve None."""
    try:
        contenedores = get_contenedores_by_deal(client, deal_id, properties=["nombre"])
    except Exception as e:
        logger.warning(f"[containers] no pude comprobar si el deal {deal_id} está publicado: {e}")
        return None
    if not contenedores:
        return None

    job_id = f"{CUSTOM_OBJECTS_JOB_TYPE}_{deal_id}_{int(time.time() * 1000)}"
    payload = {"deal_id": deal_id}
    try:
        create_job(deal_id=deal_id, job_type=CUSTOM_OBJECTS_JOB_TYPE, job_id=job_id, payload=payload)
        enqueue_job(job_id=job_id, job_type=CUSTOM_OBJECTS_JOB_TYPE, payload=payload)
        logger.info(f"[containers] re-sync custom objects encolado job_id={job_id} deal={deal_id} (deal ya publicado)")
        return job_id
    except Exception as e:
        logger.warning(f"[containers] enqueue de re-sync falló para deal {deal_id}; fallback síncrono inline: {e}")
        try:
            update_job(job_id, status="failed", error=f"enqueue_failed: {e}", completed_at=int(time.time()))
        except Exception:
            pass
        try:
            process_deal_custom_objects_from_line_items(deal_id)
        except Exception as e2:
            logger.error(f"[containers] fallback síncrono de re-sync también falló para deal {deal_id}: {e2}")
        return None

def _create_container_with_articles(
    *,
    deal_id: str,
    container_name: str,
    container_input: ContainerLineItemInput,
    product_info: dict,
    article_sku_to_product: dict,
    fabricante_cardcode: Optional[str],
    residennum: Optional[str],
    deal_almacen: Optional[str],
    tipo_contenedor: str,
    headers: dict,
) -> tuple[list, list]:
    """Build the line item create payloads (container + fabricante articles) for
    one brand-new container. New articles use catalogue price (hs_price_eur).
    Returns (container_inputs, article_inputs) ready for batch create."""
    container_config_dict = container_input.container_config.model_dump(by_alias=True)

    container_properties = {
        "hs_product_id": product_info["id"],
        "hs_sku": product_info["sku"],
        "name": container_name,
        "price": product_info["price"],
        "quantity": "1",
        "hs_product_type": "container",
        "hs_currency": "EUR",
        "numero_container": container_name,
        "container_config": json.dumps(container_config_dict),
        "ocupacion": str(container_input.container_config.ocupacion),
    }
    almacen_contenedor = resolve_almacen_para_elemento(deal_almacen, None, tipo_contenedor)
    if almacen_contenedor:
        container_properties["almacen"] = str(almacen_contenedor)

    container_inputs = [{
        "properties": container_properties,
        "associations": [{
            "to": {"id": deal_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
        }]
    }]

    article_inputs = []
    for articulo in container_input.container_config.articulos:
        article_sku = str(articulo.articuloId) if articulo.articuloId is not None else None
        if not article_sku:
            continue
        num_cajas = articulo.numCajas or 0
        if num_cajas <= 0:
            continue

        article_product_info = article_sku_to_product.get(article_sku)
        if not article_product_info:
            logger.warning(f"Article product with SKU '{article_sku}' not found in batch read, skipping")
            continue

        article_name = article_product_info["name"]
        unique_article_name = f"{container_name} - {article_name}"
        article_cardcode = fabricante_cardcode or article_product_info.get("cardcode", "")

        product_props = article_product_info.get("_product_properties", {})
        iva_rep = resolve_product_iva(product_props, residennum, "repercutido")
        iva_sop = resolve_product_iva(product_props, residennum, "soportado")

        hs_price_eur = round_price(article_product_info.get("hs_price_eur"))
        if hs_price_eur is None:
            logger.warning(
                f"[cotizacion] producto sku={article_sku} sin hs_price_eur — "
                f"precio_de_coste y hs_price_eur quedarán vacíos en line item"
            )

        article_line_item_properties = {
            "hs_product_id": article_product_info["id"],
            "hs_sku": article_sku,
            "name": unique_article_name,
            "price": str(hs_price_eur) if hs_price_eur is not None else "",
            "quantity": str(num_cajas),
            "cardcode": article_cardcode,
            "tipo_articulo": "fabricante",
            "hs_product_type": "Articulo",
            "numero_container": container_name,
            "tipo_iva_repercutido": iva_rep,
            "tipo_iva_soportado": iva_sop,
            "hs_currency": "EUR"
        }

        for src, dst in [("peso_neto", "peso_neto"), ("peso_bruto", "peso_bruto"),
                         ("name_en", "name_en"), ("name_fr", "name_fr"),
                         ("description", "description"), ("formato", "formato"),
                         ("marca", "marca_certificado")]:
            val = article_product_info.get(src)
            if val is not None and str(val).strip() != "":
                article_line_item_properties[dst] = str(val)

        almacen_articulo = resolve_almacen_para_elemento(deal_almacen, "fabricante", tipo_contenedor)
        if almacen_articulo:
            article_line_item_properties["almacen"] = str(almacen_articulo)

        article_inputs.append({
            "properties": article_line_item_properties,
            "associations": [{
                "to": {"id": deal_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
            }]
        })

    return container_inputs, article_inputs

@router.post("/{deal_id}/containers", response_model=UpdateContainersResponse)
async def update_deal_containers(
    data: UpdateContainersRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Merge incremental de contenedores: edita en sitio los previos (preservando precios negociados), archiva los eliminados y crea los nuevos con precio de catálogo. NO toca los line items ni Elementos de contenedores previos no afectados. El IVA repercutido se deriva de residennum (NACIONAL→R21, EUROPA→EI21, EXTRANJERO→EX). Serializado por deal (ver edit_single_container)."""
    lock_resource = deal_lock.deal_resource(deal_id)
    lock_token = deal_lock.acquire(lock_resource)
    if lock_token is None:
        raise HTTPException(status_code=409, detail={
            "error_code": "edit_in_progress",
            "message": "Hay otra edición de este negocio en curso. Espera a que termine.",
        })
    try:
        client = hubspot_client.get_client()

        # Handle both Pydantic model and dict input (for AWS Lambda compatibility)
        if isinstance(data, dict):
            data = UpdateContainersRequest(**data)

        logger.warning(f"Processing {len(data.containers)} container configurations for deal {deal_id}")

        deal_almacen = None
        tipo_contenedor = ""
        try:
            deal_basic = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["almacen", "tipo_contenedor"]
            )
            deal_almacen = deal_basic.properties.get("almacen")
            tipo_contenedor = deal_basic.properties.get("tipo_contenedor") or ""
            if deal_almacen:
                logger.warning(f"[containers] Deal almacen: {deal_almacen}")
        except Exception as e:
            logger.warning(f"[containers] Could not fetch deal almacen: {e}")

        # Ensure deal has EUR as currency so all line items inherit it
        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": {"deal_currency_code": "EUR"}}
            )
        except Exception as e:
            logger.warning(f"[containers] Could not set deal_currency_code: {e}")

        tipo_to_product_id = {}
        unique_tipos = list(set([container.tipo for container in data.containers]))

        for tipo in unique_tipos:
            sku = CONTAINER_SKU_MAP.get(tipo)
            if not sku:
                logger.error(f"Unknown container tipo: {tipo}. Valid tipos: {list(CONTAINER_SKU_MAP.keys())}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid container tipo: {tipo}. Valid options: {', '.join(CONTAINER_SKU_MAP.keys())}"
                )

            try:
                logger.warning(f"Searching for container product with SKU: '{sku}'")
                search_response = client.crm.products.search_api.do_search(
                    public_object_search_request={
                        "filterGroups": [
                            {
                                "filters": [
                                    {
                                        "propertyName": "hs_sku",
                                        "operator": "EQ",
                                        "value": sku
                                    }
                                ]
                            }
                        ],
                        "properties": ["hs_sku", "name", "price"],
                        "limit": 1
                    }
                )

                if search_response.results and len(search_response.results) > 0:
                    product_id = search_response.results[0].id
                    raw_container_price = search_response.results[0].properties.get("price", "0")
                    tipo_to_product_id[tipo] = {
                        "id": str(product_id),
                        "sku": sku,
                        "name": search_response.results[0].properties.get("name", f"Contenedor {tipo}"),
                        "price": str(round_price(raw_container_price) or 0)
                    }
                    logger.warning(f"Found container product {product_id} for SKU {sku}")
                else:
                    logger.error(f"Container product with SKU '{sku}' not found in HubSpot")
                    raise HTTPException(
                        status_code=404,
                        detail=f"Container product with SKU '{sku}' not found. Please create the container product first."
                    )

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error searching for container product with SKU {sku}: {e}")
                raise HTTPException(status_code=500, detail=f"Error finding container product: {str(e)}")

        residennum = None
        tipo_cliente = ""
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=['residennum', 'tipo_cliente']
                )
                residennum = company.properties.get('residennum', '')
                tipo_cliente = company.properties.get('tipo_cliente') or ""
                logger.warning(f"Company residence is {residennum or 'NACIONAL'}")
            else:
                logger.warning("No company associated with deal, using default IVA")
        except Exception as e:
            logger.warning(f"Error getting company residence type, using default IVA: {e}")

        fabricante_cardcode = None
        try:
            CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
            provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)
            logger.warning(f"[fabricante] Found {len(provider_ids)} provider associations")

            if provider_ids:
                # Prepare headers for API requests
                access_token = client.access_token
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }

                # BATCH READ providers to find fabricante
                batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                batch_payload = {
                    "properties": ["tipo_proveedor", "cardcode"],
                    "inputs": [{"id": str(pid)} for pid in provider_ids]
                }

                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                if batch_response.ok:
                    providers = batch_response.json().get("results", [])
                    for provider in providers:
                        props = provider.get("properties", {})
                        tipo_proveedor = (props.get("tipo_proveedor") or "").strip().lower()
                        if tipo_proveedor == "fabricante":
                            fabricante_cardcode = props.get("cardcode", "")
                            logger.warning(f"[fabricante] Found fabricante provider with cardcode: {fabricante_cardcode}")
                            break  # Use first fabricante found
                else:
                    logger.error(f"[fabricante] Batch read providers failed: {batch_response.status_code}")
        except Exception as e:
            logger.warning(f"[fabricante] Error getting fabricante provider, will use product cardcode as fallback: {e}")

        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"Found {len(existing_line_item_ids)} existing line items")

        # Prepare headers for API requests (needed for batch operations)
        access_token = client.access_token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        batch_size = 100
        batch_read_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_archive_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"

        all_line_items = []
        if existing_line_item_ids:
            for i in range(0, len(existing_line_item_ids), batch_size):
                batch_ids = existing_line_item_ids[i:i + batch_size]
                batch_payload = {
                    "properties": ["hs_product_type", "tipo_articulo", "numero_container", "name", "hs_product_id"],
                    "inputs": [{"id": str(lid)} for lid in batch_ids]
                }
                try:
                    batch_response = requests.post(batch_read_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        all_line_items.extend(batch_response.json().get("results", []))
                    else:
                        logger.error(f"Batch read line items failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"Error in batch read line items: {e}")

        all_line_items = _sanear_prefijos_heredados(
            deal_id=deal_id,
            all_line_items=all_line_items,
            headers=headers,
            batch_size=batch_size,
        )

        existing_containers_by_lid = {}
        line_items_by_numero = {}
        for li in all_line_items:
            lid = li.get("id")
            props = li.get("properties", {}) or {}
            numero_container = (props.get("numero_container") or "").strip()
            if numero_container:
                line_items_by_numero.setdefault(numero_container, []).append({"id": lid, "properties": props})
            if _is_container_product_type(props.get("hs_product_type") or ""):
                name = (props.get("name") or numero_container or "").strip()
                existing_containers_by_lid[str(lid)] = {
                    "id": str(lid),
                    "numero_container": numero_container or name,
                    "name": name,
                    "numero": _parse_numero_contenedor(name or numero_container),
                }

        previos = []
        nuevos = []
        payload_lids = set()
        for entry in data.containers:
            lid = str(entry.line_item_id).strip() if entry.line_item_id else ""
            if lid and lid in existing_containers_by_lid:
                previos.append((lid, entry))
                payload_lids.add(lid)
            else:
                nuevos.append(entry)

        eliminados = [
            info for lid, info in existing_containers_by_lid.items()
            if lid not in payload_lids
        ]

        # Solo los supervivientes retienen su número; los eliminados lo liberan.
        numeros_ocupados = {
            existing_containers_by_lid[lid]["numero"]
            for lid, _ in previos
            if existing_containers_by_lid[lid].get("numero") is not None
        }
        numeros_nuevos = _asignar_numeros_contenedor(numeros_ocupados, len(nuevos))

        logger.warning(
            f"[containers] merge incremental deal={deal_id}: "
            f"{len(previos)} previos, {len(nuevos)} nuevos, {len(eliminados)} eliminados "
            f"(ocupados={sorted(numeros_ocupados)}, asignados={numeros_nuevos})"
        )

        ctx = _fetch_deal_container_context(deal_id, client)
        intermediario_ctx = _fetch_intermediario_context(deal_id, client, residennum)

        for info in eliminados:
            numero = info["numero_container"]
            to_archive = []
            for li in line_items_by_numero.get(numero, []):
                props = li.get("properties", {}) or {}
                if _is_container_product_type(props.get("hs_product_type") or ""):
                    to_archive.append(li["id"])
                    continue
                tipo_art = (props.get("tipo_articulo") or "").strip().lower()
                if tipo_art in ("fabricante", "comisiones", "servicio", "transitario", "seguro"):
                    to_archive.append(li["id"])
            if to_archive:
                payload = {"inputs": [{"id": str(x)} for x in to_archive]}
                try:
                    resp = requests.post(batch_archive_url, headers=headers, json=payload, timeout=30)
                    if not resp.ok:
                        logger.error(f"[containers] archive of removed container {numero} failed: {resp.status_code} - {resp.text}")
                    else:
                        logger.warning(f"[containers] archived removed container {numero} ({len(to_archive)} line items)")
                except Exception as e:
                    logger.error(f"[containers] error archiving removed container {numero}: {e}")

        # Solo el CRUD de line items es síncrono. El trabajo derivado (Elemento/
        # Contenedor CO, recalcular precios, touch_pagos SAP) lo hace el worker de
        # re-sync que se encola al final, reconstruyéndolo desde los line items.
        # cont_map es igual para todos los previos → se resuelve una vez y se comparte.
        previos_cont_map = _resolve_contenedor_co_by_numero(client, deal_id) if previos else {}
        # Total de contenedores del negocio tras el merge (para repartir la comisión
        # importe_por_negocio igual que el resto del endpoint).
        num_contenedores_total = len(previos) + len(nuevos)
        for lid, entry in previos:
            _edit_container_in_place(
                deal_id=deal_id,
                container_line_item_id=lid,
                new_config=entry.container_config,
                client=client,
                headers=headers,
                ctx=ctx,
                intermediario_ctx=intermediario_ctx,
                cont_map=previos_cont_map,
                num_contenedores_total=num_contenedores_total,
                defer_custom_objects=True,
            )

        unique_article_skus = set()
        for container_input in nuevos:
            for articulo in container_input.container_config.articulos:
                article_sku = str(articulo.articuloId) if articulo.articuloId is not None else None
                if article_sku and (articulo.numCajas or 0) > 0:
                    unique_article_skus.add(article_sku)

        logger.warning(f"Identified {len(unique_article_skus)} unique article SKUs to search (nuevos)")

        # articuloId contiene el hs_object_id del producto (hs_sku está vacío en productos nuevos)
        article_sku_to_product = {}

        if unique_article_skus:
            sku_list = list(unique_article_skus)
            try:
                batch_read_response = client.crm.products.batch_api.read(
                    batch_read_input_simple_public_object_id={
                        "properties": [
                            "name", "price", "hs_price_eur", "cardcode", "peso_neto", "peso_bruto",
                            "name_en", "name_fr", "description", "formato", "marca_certificado",
                            "tipo_iva_soportado", "tipo_iva_repercutido",
                            "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                            "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex"
                        ],
                        "inputs": [{"id": pid} for pid in sku_list]
                    }
                )

                for product in (batch_read_response.results or []):
                    pid = str(product.id)
                    article_sku_to_product[pid] = {
                        "id": pid,
                        "name": product.properties.get("name", f"Article {pid}"),
                        "price": product.properties.get("price", "0"),
                        "hs_price_eur": product.properties.get("hs_price_eur"),
                        "cardcode": product.properties.get("cardcode", ""),
                        "peso_neto": product.properties.get("peso_neto"),
                        "peso_bruto": product.properties.get("peso_bruto"),
                        "name_en": product.properties.get("name_en"),
                        "name_fr": product.properties.get("name_fr"),
                        "description": product.properties.get("description"),
                        "formato": product.properties.get("formato"),
                        "marca": product.properties.get("marca_certificado"),
                        "_product_properties": product.properties,  # Keep full props for IVA resolution
                    }

                logger.warning(f"Batch read found {len(article_sku_to_product)} article products")
                for _pid, _info in article_sku_to_product.items():
                    logger.warning(
                        f"[containers] product={_pid} "
                        f"peso_neto={_info.get('peso_neto')!r} peso_bruto={_info.get('peso_bruto')!r} "
                        f"name_en={_info.get('name_en')!r} name_fr={_info.get('name_fr')!r}"
                    )

            except Exception as e:
                # Sin este raise, un fallo transitorio creaba contenedores VACÍOS
                # (los artículos se saltan al faltar en article_sku_to_product) y
                # el endpoint respondía success — con pérdida de líneas de artículo.
                logger.error(f"Error in batch read for articles: {e}")
                raise HTTPException(status_code=502, detail=f"Error leyendo productos de artículos en HubSpot: {e}")

        container_line_items_to_create = []
        article_line_items_to_create = []
        nuevos_container_names = []

        for idx_nuevo, container_input in enumerate(nuevos):
            try:
                product_info = tipo_to_product_id[container_input.tipo]
                container_name = f"{deal_id}-{numeros_nuevos[idx_nuevo]:03d}"
                nuevos_container_names.append(container_name)

                cont_inputs, art_inputs = _create_container_with_articles(
                    deal_id=deal_id,
                    container_name=container_name,
                    container_input=container_input,
                    product_info=product_info,
                    article_sku_to_product=article_sku_to_product,
                    fabricante_cardcode=fabricante_cardcode,
                    residennum=residennum,
                    deal_almacen=deal_almacen,
                    tipo_contenedor=tipo_contenedor,
                    headers=headers,
                )
                container_line_items_to_create.extend(cont_inputs)
                article_line_items_to_create.extend(art_inputs)
                logger.warning(f"Prepared NUEVO container {container_name} ({container_input.tipo}) with {len(art_inputs)} articles")

            except Exception as e:
                logger.error(f"Error preparing line items for tipo {container_input.tipo}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error preparing line items: {str(e)}"
                )

        intermediario_line_items_to_create = []
        intermediario_updates = []

        try:
            deal = client.crm.deals.basic_api.get_by_id(
                deal_id,
                properties=["repercutir_a_cliente", "tipo_comision", "importe_comision", "codigo_intermediario", "tipo_iva_comision", "tipo_iva_comision_soportado"]
            )

            repercutir_a_cliente = (deal.properties.get("repercutir_a_cliente") or "").strip().lower()
            tipo_comision = (deal.properties.get("tipo_comision") or "").strip().lower()
            importe_comision = float(deal.properties.get("importe_comision") or 0)
            codigo_intermediario = (deal.properties.get("codigo_intermediario") or "").strip()

            logger.info(f"[intermediario] repercutir_a_cliente: {repercutir_a_cliente}, tipo_comision: {tipo_comision}, importe: {importe_comision}, codigo_intermediario: {codigo_intermediario}")

            if repercutir_a_cliente == "si" and importe_comision > 0:
                intermediario_product = _find_intermediario_product(client)

                if intermediario_product:
                    intermediario_product_id = intermediario_product["id"]
                    intermediario_name = intermediario_product["name"]

                    deal_iva_comision = (deal.properties.get("tipo_iva_comision") or "").strip() or None
                    deal_iva_comision_sop = (deal.properties.get("tipo_iva_comision_soportado") or "").strip() or None
                    intermediario_iva_rep = resolve_product_iva(intermediario_product["properties"], residennum, "repercutido", deal_iva_comision)
                    intermediario_iva_sop = resolve_product_iva(intermediario_product["properties"], residennum, "soportado", deal_iva_comision_sop)

                    # Conjunto final de contenedores tras el merge: previos (su numero_container)
                    # + nuevos (numero_container monótono recién asignado).
                    previo_names = [existing_containers_by_lid[lid]["numero_container"] for lid, _ in previos]
                    final_container_names = previo_names + nuevos_container_names
                    num_final = max(len(final_container_names), 1)

                    cajas_por_container = {}
                    for lid, entry in previos:
                        cajas_por_container[existing_containers_by_lid[lid]["numero_container"]] = sum(
                            a.numCajas or 0 for a in entry.container_config.articulos
                        )
                    for entry, cname in zip(nuevos, nuevos_container_names):
                        cajas_por_container[cname] = sum(a.numCajas or 0 for a in entry.container_config.articulos)

                    def _price_for(cname: str) -> Optional[float]:
                        if tipo_comision == "importe_por_negocio":
                            return round_price(importe_comision / num_final)
                        if tipo_comision == "importe_por_contenedor":
                            return round_price(importe_comision)
                        if tipo_comision == "importe_por_cajas":
                            return round_price(importe_comision * cajas_por_container.get(cname, 0))
                        logger.warning(f"[intermediario] Unknown tipo_comision: {tipo_comision}")
                        return None

                    almacen_intermediario = resolve_almacen_para_elemento(deal_almacen, "comisiones", tipo_contenedor)

                    # NUEVOS: crear su línea de comisión.
                    for cname in nuevos_container_names:
                        price = _price_for(cname)
                        if price is None:
                            continue
                        intermediario_properties = {
                            "hs_product_id": intermediario_product_id,
                            "hs_sku": intermediario_product_id,
                            "name": f"{cname} - {intermediario_name}",
                            "price": str(price),
                            "quantity": "1",
                            "cardcode": codigo_intermediario or "",
                            "numero_container": cname,
                            "tipo_iva_repercutido": intermediario_iva_rep,
                            "tipo_iva_soportado": intermediario_iva_sop,
                            "tipo_articulo": "comisiones",
                            "hs_product_type": "Servicio"
                        }
                        if almacen_intermediario:
                            intermediario_properties["almacen"] = str(almacen_intermediario)
                        intermediario_line_items_to_create.append({
                            "properties": intermediario_properties,
                            "associations": [{
                                "to": {"id": deal_id},
                                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                            }]
                        })

                    # PREVIOS: en modos dependientes del nº de contenedores el precio por
                    # contenedor cambia al añadir/eliminar. _edit_container_in_place solo
                    # recalcula importe_por_cajas, así que aquí actualizamos el resto.
                    if tipo_comision in ("importe_por_negocio", "importe_por_contenedor"):
                        for lid, _entry in previos:
                            cname = existing_containers_by_lid[lid]["numero_container"]
                            new_price = _price_for(cname)
                            if new_price is None:
                                continue
                            com_li = next(
                                (li for li in line_items_by_numero.get(cname, [])
                                 if (li.get("properties", {}).get("tipo_articulo") or "").strip().lower() == "comisiones"),
                                None
                            )
                            if com_li:
                                intermediario_updates.append({"id": com_li["id"], "properties": {"price": str(new_price)}})

                    logger.info(
                        f"[intermediario] {len(intermediario_line_items_to_create)} nuevas comisiones, "
                        f"{len(intermediario_updates)} comisiones de previos actualizadas (num_final={num_final})"
                    )
                else:
                    logger.warning("[intermediario] No product found with tipo_articulo='comisiones'. Please create one in HubSpot.")
            else:
                logger.info(f"[intermediario] Skipping - repercutir_a_cliente={repercutir_a_cliente}, importe={importe_comision}")

        except Exception as e:
            logger.error(f"[intermediario] Error preparing intermediario line items: {e}")

        if intermediario_updates:
            upd_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
            for i in range(0, len(intermediario_updates), batch_size):
                chunk = intermediario_updates[i:i + batch_size]
                for x in chunk:
                    enforce_line_item_price_rounding(x.get("properties", {}))
                try:
                    resp = requests.post(upd_url, headers=headers, json={"inputs": chunk}, timeout=60)
                    if not resp.ok:
                        logger.error(f"[intermediario] batch update of previos commissions failed: {resp.status_code} - {resp.text}")
                except Exception as e:
                    logger.error(f"[intermediario] error updating previos commissions: {e}")

        cloned_line_items_to_create = _clone_template_lines_for_new_containers(
            nuevos_container_names=nuevos_container_names,
            previos=previos,
            existing_containers_by_lid=existing_containers_by_lid,
            line_items_by_numero=line_items_by_numero,
            deal_id=deal_id,
            headers=headers,
            batch_size=batch_size,
        )

        all_line_items_to_create = (
            container_line_items_to_create
            + article_line_items_to_create
            + intermediario_line_items_to_create
            + cloned_line_items_to_create
        )
        logger.warning(
            f"Prepared {len(container_line_items_to_create)} container line items, "
            f"{len(article_line_items_to_create)} article line items, "
            f"{len(intermediario_line_items_to_create)} intermediario line items, and "
            f"{len(cloned_line_items_to_create)} cloned service/flete/seguro line items"
        )

        container_line_items_created = []
        article_line_items_created = []

        if all_line_items_to_create:
            batch_create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
            batch_size = 100

            for i in range(0, len(all_line_items_to_create), batch_size):
                batch_items = all_line_items_to_create[i:i + batch_size]

                for li in batch_items:
                    enforce_line_item_price_rounding(li.get("properties", {}))

                batch_payload = {
                    "inputs": batch_items
                }

                try:
                    batch_response = requests.post(batch_create_url, headers=headers, json=batch_payload, timeout=60)
                    if batch_response.ok:
                        batch_data = batch_response.json()
                        created_items = batch_data.get("results", [])

                        # Separate container and article line items by checking properties
                        for item in created_items:
                            props = item.get("properties", {})
                            if _is_container_product_type(props.get("hs_product_type") or ""):
                                container_line_items_created.append(item.get("id"))
                            elif props.get("tipo_articulo") == "fabricante":
                                article_line_items_created.append(item.get("id"))

                        logger.warning(f"Batch created {len(created_items)} line items")
                    else:
                        logger.error(f"Batch create failed: {batch_response.status_code} - {batch_response.text}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to create line items: {batch_response.text}"
                        )
                except Exception as e:
                    logger.error(f"Error in batch create line items: {e}")
                    raise HTTPException(status_code=500, detail=f"Error creating line items: {str(e)}")

        logger.warning(f"Created {len(container_line_items_created)} container line items and {len(article_line_items_created)} article line items")

        # Con contenedores en el deal, las líneas de catálogo (fabricante sin
        # numero_container, creadas por update_deal_articles) quedan obsoletas:
        # si no se archivan aparecen como línea extra en la cotización. Solo tras
        # completar el merge con éxito y si el conjunto final no queda vacío.
        if previos or nuevos_container_names:
            # numero_container Y name: en contenedores legacy numero_container es el
            # entero ("1") pero las líneas per-contenedor llevan el prefijo del name.
            known_container_names = {
                v
                for info in existing_containers_by_lid.values()
                for v in (info["numero_container"], info["name"])
                if v
            }
            known_container_names.update(nuevos_container_names)

            # Solo se archivan huérfanos cuyo producto quedó colocado en el conjunto
            # final: la línea de catálogo de un artículo sin colocar es su única
            # persistencia (GET /articles reconstruye la selección desde ella).
            placed_product_ids = set()
            for _lid, entry in previos:
                for articulo in entry.container_config.articulos:
                    if (articulo.numCajas or 0) > 0 and str(articulo.articuloId or "").strip():
                        placed_product_ids.add(str(articulo.articuloId))
            for li in article_line_items_to_create:
                pid = (li.get("properties") or {}).get("hs_product_id")
                if pid:
                    placed_product_ids.add(str(pid))

            hsid_by_lid = {
                str(li.get("id")): str((li.get("properties") or {}).get("hs_product_id") or "")
                for li in all_line_items
            }
            catalogo_huerfanos = [
                lid for lid in _catalogo_huerfano_ids(all_line_items, known_container_names)
                if hsid_by_lid.get(lid) in placed_product_ids
            ]
            for i in range(0, len(catalogo_huerfanos), batch_size):
                chunk = catalogo_huerfanos[i:i + batch_size]
                payload = {"inputs": [{"id": x} for x in chunk]}
                try:
                    resp = requests.post(batch_archive_url, headers=headers, json=payload, timeout=30)
                    if not resp.ok:
                        logger.error(f"[containers] archive de líneas de catálogo falló: {resp.status_code} - {resp.text}")
                    else:
                        logger.warning(f"[containers] archivadas {len(chunk)} líneas de catálogo sin contenedor")
                except Exception as e:
                    logger.error(f"[containers] error archivando líneas de catálogo: {e}")

        # Merge incremental: previos editados en sitio (ids estables), nuevos creados,
        # eliminados archivados. Si el deal ya estaba publicado, re-sincronizamos los
        # custom objects en background; el worker es incremental y no toca los Elemento
        # de previos no afectados mientras nombre/line_item_id se mantengan estables.
        resync_job_id = _enqueue_custom_objects_resync_if_published(client, deal_id)

        return UpdateContainersResponse(
            success=True,
            error=None,
            job_id=resync_job_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_containers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        deal_lock.release(lock_resource, lock_token)

@router.get("/{deal_id}/transit-options", response_model=DealTransitResponse)
async def get_deal_transit_options(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve las opciones de tránsito del deal (lugar_carga readonly, puerto_salida, puerto_destino, localidad_destino) con fallback a la company asociada cuando los campos del deal están vacíos."""
    try:
        client = hubspot_client.get_client()

        logger.warning(f"Fetching transit data for deal {deal_id}")

        deal_properties = [
            'lugar_carga',
            'puerto_salida',
            'puerto_destino',
            'localidad_destino'
        ]

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties,
            associations=['companies']
        )

        deal_lugar_carga = deal.properties.get('lugar_carga')
        deal_puerto_salida = deal.properties.get('puerto_salida')
        deal_puerto_destino = deal.properties.get('puerto_destino')
        deal_localidad_destino = deal.properties.get('localidad_destino')

        logger.info(f"Deal values - lugar_carga: {deal_lugar_carga}, puerto_salida: {deal_puerto_salida}, puerto_destino: {deal_puerto_destino}, localidad_destino: {deal_localidad_destino}")

        company_lugar_carga = None
        company_puerto_salida = None
        company_puerto_destino = None

        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=[
                        'lugar_de_la_carga_envio',
                        'puerto_salida',
                        'puerto_destino',
                    ]
                )
                company_lugar_carga = company.properties.get('lugar_de_la_carga_envio')
                company_puerto_salida = company.properties.get('puerto_salida')
                company_puerto_destino = company.properties.get('puerto_destino')
                logger.info(f"Company values - lugar_de_la_carga_envio: {company_lugar_carga}, puerto_salida: {company_puerto_salida}, puerto_destino: {company_puerto_destino}")
        except Exception as e:
            logger.warning(f"Error fetching company data: {e}")

        # lugar_carga: deal.lugar_carga, fallback to company.lugar_de_la_carga_envio
        final_lugar_carga = deal_lugar_carga or company_lugar_carga

        # puerto_salida: deal.puerto_salida, fallback to company.puerto_salida
        final_puerto_salida = deal_puerto_salida or company_puerto_salida

        # puerto_destino: deal.puerto_destino, fallback to company.puerto_destino
        final_puerto_destino = deal_puerto_destino or company_puerto_destino

        # localidad_destino: deal.localidad_destino
        final_localidad_destino = deal_localidad_destino

        logger.info(f"Final values - lugar_carga: {final_lugar_carga}, puerto_salida: {final_puerto_salida}, puerto_destino: {final_puerto_destino}, localidad_destino: {final_localidad_destino}")

        # Use the same approach as /articles endpoint: fetch from custom object property
        lugar_carga_label = None
        lugar_carga_options = {}

        try:
            # Fetch property definition from custom object (same as in /articles)
            access_token = client.access_token
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            headers = {"Authorization": f"Bearer {access_token}"}
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
                logger.info(f"[transit] Loaded {len(lugar_carga_options)} lugar_carga options from custom object")
        except Exception as e:
            logger.warning(f"[transit] Error fetching lugar_carga options: {e}")

        if final_lugar_carga:
            lugar_carga_label = lugar_carga_options.get(final_lugar_carga, final_lugar_carga)
            if lugar_carga_label != final_lugar_carga:
                logger.info(f"[transit] Mapped lugar_carga '{final_lugar_carga}' -> '{lugar_carga_label}'")
            else:
                logger.warning(f"[transit] No label found for lugar_carga value: '{final_lugar_carga}', using value as label")

        logger.info(f"[transit] Final lugar_carga_label: {lugar_carga_label}")

        puerto_salida_options = get_property_options('puerto_salida', 'deals')
        puerto_destino_options = get_property_options('puerto_destino', 'deals')

        localidad_destino_options_list = [
            PropertyOption(label=label, value=value)
            for value, label in lugar_carga_options.items()
        ]

        logger.info(f"[transit] Built {len(localidad_destino_options_list)} localidad_destino options")

        almacen_options = get_property_options('almacen', 'deals')
        logger.info(f"[transit] Loaded {len(almacen_options)} almacen options")

        return DealTransitResponse(
            success=True,
            error=None,
            lugar_carga=final_lugar_carga,
            lugar_carga_label=lugar_carga_label,
            puerto_salida=PropertyWithOptions(
                value=final_puerto_salida,
                options=puerto_salida_options
            ),
            puerto_destino=PropertyWithOptions(
                value=final_puerto_destino,
                options=puerto_destino_options
            ),
            localidad_destino=PropertyWithOptions(
                value=final_localidad_destino,
                options=localidad_destino_options_list
            ),
            almacen=PropertyWithOptions(
                value=None,  # Will be calculated on frontend based on tipo_cliente
                options=almacen_options
            )
        )

    except Exception as e:
        logger.error(f"Error in get_deal_transit_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/commission-providers/search", response_model=dict)
async def search_commission_providers(
    q: str = ""
):
    """Busca proveedores con tipo_proveedor=comisiones (custom object Proveedores `2-43108800`)."""
    try:
        client = hubspot_client.get_client()

        # Custom object ID for Proveedores
        CUSTOM_OBJECT_PROVEEDORES = "2-43108800"

        # We need to filter by tipo_proveedor = "comisiones" and optionally by name
        filters = []

        filters.append(Filter(
            property_name="tipo_proveedor",
            operator="EQ",
            value="comisiones"
        ))

        # If search query provided, filter by name with wildcards
        if q:
            filters.append(Filter(
                property_name="name",
                operator="CONTAINS_TOKEN",
                value=f"*{q}*"
            ))

        filter_group = FilterGroup(filters=filters)

        search_request = PublicObjectSearchRequest(
            filter_groups=[filter_group],
            properties=[
                "name",
                "cardcode",
                "tipo_proveedor",
                "repercutir_a_cliente",
                "tipo_comision",
                "importe_comision"
            ],
            limit=20,
            sorts=[{"propertyName": "name", "direction": "ASCENDING"}]
        )

        search_results = client.crm.objects.search_api.do_search(
            object_type=CUSTOM_OBJECT_PROVEEDORES,
            public_object_search_request=search_request
        )

        providers = []
        for result in search_results.results:
            props = result.properties
            providers.append({
                "id": result.id,
                "name": props.get("name", ""),
                "cardcode": props.get("cardcode", ""),
                "tipo_proveedor": props.get("tipo_proveedor", ""),
                "repercutir_a_cliente": props.get("repercutir_a_cliente", ""),
                "tipo_comision": props.get("tipo_comision", ""),
                "importe_comision": safe_convert(props.get("importe_comision"), float) or 0.0
            })

        return {
            "success": True,
            "providers": providers,
            "total": len(providers)
        }

    except Exception as e:
        logger.error(f"Error searching commission providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{deal_id}/transit", response_model=DealTransitDataResponse)
async def get_deal_transit(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Devuelve los datos de tránsito del deal (puertos, naviera, dias_libres, transitario, precios y caducidades de rutas, IVA fletes)."""
    try:
        client = hubspot_client.get_client()

        properties = [
            "puerto_salida",
            "puerto_destino",
            "localidad_destino",
            "naviera",
            "dias_libres",
            "transitario",
            "precio_ruta_hasta_puerto_de_salida",
            "precio_ruta_hasta_puerto_destino",
            "fecha_caducidad_flete_salida",
            "fecha_caducidad_flete_destino",
            "cotizacion_rapida",
            "almacen",
            "tipo_iva_fletes",
            "tipo_iva_fletes_soportado",
            "transitario_terrestre",
            "tipo_iva_fletes_terrestre",
            "tipo_iva_fletes_terrestre_soportado",
            "tipo_contenedor"
        ]

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id=deal_id,
            properties=properties
        )

        props = deal.properties

        transitario_cardcode = props.get("transitario") or None
        transitario_name = None
        transitario_id = None

        # If transitario exists, search for provider details by cardcode
        if transitario_cardcode:
            try:
                access_token = client.access_token
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

                # Search for transitario in proveedores using REST API by cardcode
                transitario_search_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/search"
                transitario_search_payload = {
                    "filterGroups": [{
                        "filters": [
                            {"propertyName": "cardcode", "operator": "EQ", "value": transitario_cardcode},
                            {"propertyName": "tipo_proveedor", "operator": "EQ", "value": "transitario"}
                        ]
                    }],
                    "properties": ["name", "cardcode"],
                    "limit": 1
                }

                trans_response = requests.post(transitario_search_url, headers=headers, json=transitario_search_payload, timeout=10)
                if trans_response.ok:
                    trans_data = trans_response.json()
                    if trans_data.get("results"):
                        transitario_result = trans_data["results"][0]
                        transitario_id = str(transitario_result["id"])
                        transitario_name = transitario_result["properties"].get("name", "")
                        logger.info(f"Found transitario: id={transitario_id}, cardcode={transitario_cardcode}, name={transitario_name}")
            except Exception as e:
                logger.warning(f"Error searching for transitario {transitario_cardcode}: {e}")

        # Transitario del tramo terrestre (doble flete): mismo lookup por cardcode
        transitario_terrestre_cardcode = props.get("transitario_terrestre") or None
        transitario_terrestre_name = None

        if transitario_terrestre_cardcode:
            try:
                access_token = client.access_token
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

                terrestre_search_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/search"
                terrestre_search_payload = {
                    "filterGroups": [{
                        "filters": [
                            {"propertyName": "cardcode", "operator": "EQ", "value": transitario_terrestre_cardcode},
                            {"propertyName": "tipo_proveedor", "operator": "EQ", "value": "transitario"}
                        ]
                    }],
                    "properties": ["name", "cardcode"],
                    "limit": 1
                }

                terrestre_response = requests.post(terrestre_search_url, headers=headers, json=terrestre_search_payload, timeout=10)
                if terrestre_response.ok:
                    terrestre_data = terrestre_response.json()
                    if terrestre_data.get("results"):
                        transitario_terrestre_name = terrestre_data["results"][0]["properties"].get("name", "")
            except Exception as e:
                logger.warning(f"Error searching for transitario terrestre {transitario_terrestre_cardcode}: {e}")

        flete_doble_disponible = es_camion(props.get("tipo_contenedor"))

        # Resolve IVA defaults from fletes product (Fase 4E)
        iva_fletes_defaults = None
        iva_fletes_terrestre_defaults = None
        try:
            residennum = None
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company = client.crm.companies.basic_api.get_by_id(
                    company_ids[0], properties=["residennum"]
                )
                residennum = company.properties.get("residennum")

            # Search for fletes product by cardcode
            fletes_search = client.crm.products.search_api.do_search(
                public_object_search_request={
                    "filterGroups": [{"filters": [{"propertyName": "cardcode", "operator": "EQ", "value": "VARIAB-TRANSP-00001"}]}],
                    "properties": [
                        "name", "tipo_iva_soportado", "tipo_iva_repercutido",
                        "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                        "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex"
                    ],
                    "limit": 1
                }
            )
            if fletes_search.results:
                fletes_props = fletes_search.results[0].properties
                iva_fletes_defaults = {
                    "repercutido": resolve_product_iva(fletes_props, residennum, "repercutido"),
                    "soportado": resolve_product_iva(fletes_props, residennum, "soportado")
                }
                logger.info(f"[transit] iva_fletes_defaults resolved: {iva_fletes_defaults}")

            # Tramo terrestre (doble flete): mismo producto que el marítimo, así
            # que reutiliza sus defaults de IVA.
            if flete_doble_disponible:
                iva_fletes_terrestre_defaults = iva_fletes_defaults
        except Exception as e:
            logger.warning(f"[transit] Error resolving iva_fletes_defaults: {e}")

        data = DealTransitUpdateRequest(
            puerto_salida=props.get("puerto_salida") or None,
            puerto_destino=props.get("puerto_destino") or None,
            localidad_destino=props.get("localidad_destino") or None,
            naviera=props.get("naviera") or None,
            dias_libres=int(props.get("dias_libres")) if props.get("dias_libres") else None,
            transitario=transitario_cardcode,
            transitario_name=transitario_name,
            precio_ruta_hasta_puerto_de_salida=float(props.get("precio_ruta_hasta_puerto_de_salida")) if props.get("precio_ruta_hasta_puerto_de_salida") else None,
            precio_ruta_hasta_puerto_destino=float(props.get("precio_ruta_hasta_puerto_destino")) if props.get("precio_ruta_hasta_puerto_destino") else None,
            fecha_caducidad_flete_salida=parse_fecha_caducidad(props.get("fecha_caducidad_flete_salida")),
            fecha_caducidad_flete_destino=parse_fecha_caducidad(props.get("fecha_caducidad_flete_destino")),
            cotizacion_rapida=props.get("cotizacion_rapida") or None,
            almacen=props.get("almacen") or None,
            tipo_iva_fletes=props.get("tipo_iva_fletes") or None,
            tipo_iva_fletes_soportado=props.get("tipo_iva_fletes_soportado") or None,
            iva_fletes_defaults=iva_fletes_defaults,
            transitario_terrestre=transitario_terrestre_cardcode,
            transitario_terrestre_name=transitario_terrestre_name,
            tipo_iva_fletes_terrestre=props.get("tipo_iva_fletes_terrestre") or None,
            tipo_iva_fletes_terrestre_soportado=props.get("tipo_iva_fletes_terrestre_soportado") or None,
            iva_fletes_terrestre_defaults=iva_fletes_terrestre_defaults,
            flete_doble_disponible=flete_doble_disponible
        )

        response_data = {
            "success": True,
            "error": None,
            "data": data.dict()
        }

        return response_data

    except Exception as e:
        logger.error(f"Error retrieving transit data for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _lookup_transitarios_por_cardcode(client, cardcodes) -> Dict[str, Dict[str, str]]:
    """Resuelve `{cardcode: {"id", "name"}}` contra Proveedores (2-43108800).

    Si el cardcode se repite entre proveedores gana el que tenga
    tipo_proveedor=transitario."""
    codes = sorted({(c or "").strip() for c in cardcodes if (c or "").strip()})
    if not codes:
        return {}

    CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
    headers = {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}
    search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/search"

    encontrados = {}
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        after = None
        while True:
            payload = {
                "filterGroups": [{"filters": [{"propertyName": "cardcode", "operator": "IN", "values": chunk}]}],
                "properties": ["name", "cardcode", "tipo_proveedor"],
                "limit": 100
            }
            if after:
                payload["after"] = after

            try:
                response = requests.post(search_url, headers=headers, json=payload, timeout=10)
            except Exception as e:
                logger.warning(f"[rutas] Error resolviendo transitarios {chunk}: {e}")
                break

            if not response.ok:
                logger.warning(f"[rutas] Búsqueda de transitarios falló: {response.status_code} - {response.text}")
                break

            data = response.json()
            for result in data.get("results", []):
                props = result.get("properties", {}) or {}
                cardcode = (props.get("cardcode") or "").strip()
                name = (props.get("name") or "").strip()
                if not cardcode or not name:
                    continue
                es_transitario = (props.get("tipo_proveedor") or "").strip().lower() == "transitario"
                previo = encontrados.get(cardcode)
                if previo and previo["es_transitario"] and not es_transitario:
                    continue
                encontrados[cardcode] = {
                    "id": str(result.get("id", "")),
                    "name": name,
                    "es_transitario": es_transitario
                }

            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break

    return {code: {"id": v["id"], "name": v["name"]} for code, v in encontrados.items()}

def _hidratar_transitario_rutas(client, rutas: list, backfill: bool = True) -> None:
    """Completa nombre e id del transitario en las rutas leídas de HubSpot.

    Las rutas creadas antes de que el upsert persistiera `transitario_name` solo
    guardan el cardcode: se resuelve contra Proveedores y se reescribe en el objeto
    para que la siguiente lectura no dependa del lookup."""
    if not rutas:
        return

    resueltos = _lookup_transitarios_por_cardcode(client, [r.transitario_cardcode for r in rutas])
    if not resueltos:
        return

    backfill_por_id = {}
    for ruta in rutas:
        datos = resueltos.get((ruta.transitario_cardcode or "").strip())
        if not datos:
            continue
        if not ruta.transitario_id:
            ruta.transitario_id = datos["id"] or None
        if not (ruta.transitario or "").strip():
            ruta.transitario = datos["name"]
            backfill_por_id[ruta.id] = {"id": ruta.id, "properties": {"transitario_name": datos["name"]}}

    if not backfill or not backfill_por_id:
        return

    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        headers = {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}
        update_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/batch/update"
        inputs = list(backfill_por_id.values())
        for i in range(0, len(inputs), 100):
            response = requests.post(update_url, headers=headers, json={"inputs": inputs[i:i + 100]}, timeout=10)
            if response.ok:
                logger.info(f"[rutas] transitario_name repuesto en {len(inputs[i:i + 100])} rutas")
            else:
                logger.warning(f"[rutas] Backfill de transitario_name falló: {response.status_code} - {response.text}")
    except Exception as e:
        logger.warning(f"[rutas] Error en backfill de transitario_name: {e}")

def _find_ruta(
    client,
    tipo_ruta: str,
    transitario: str,
    lugar_carga: str = None,
    puerto_salida: str = None,
    puerto_destino: str = None,
    naviera: str = None,
    tipo_contenedor: str = None
) -> Optional[str]:
    """Busca una ruta terrestre o marítima en HubSpot y devuelve su ID si existe.
    """
    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        naviera_normalizada = None
        if naviera and naviera.strip().upper() != "SIN ESPECIFICAR":
            naviera_normalizada = naviera

        filters = [
            {"propertyName": "transitario", "operator": "EQ", "value": transitario or ""}
        ]

        if tipo_contenedor:
            filters.append({"propertyName": "tipo_contenedor", "operator": "EQ", "value": tipo_contenedor})

        if tipo_ruta == "terrestre":
            if lugar_carga:
                filters.append({"propertyName": "lugar_carga", "operator": "EQ", "value": lugar_carga})
            if puerto_salida:
                filters.append({"propertyName": "puerto_salida", "operator": "EQ", "value": puerto_salida})
        else:
            if puerto_salida:
                filters.append({"propertyName": "puerto_salida", "operator": "EQ", "value": puerto_salida})
            if puerto_destino:
                filters.append({"propertyName": "puerto_destino", "operator": "EQ", "value": puerto_destino})
            if naviera_normalizada:
                filters.append({"propertyName": "naviera", "operator": "EQ", "value": naviera_normalizada})

        search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"
        search_payload = {
            "filterGroups": [{"filters": filters}],
            "properties": ["fecha_caducidad"],
            "limit": 1
        }

        response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

        if response.ok:
            results = response.json().get("results", [])
            if results:
                ruta_id = results[0].get("id")
                logger.info(f"[rutas] Ruta {tipo_ruta} encontrada: {ruta_id}")
                return ruta_id
            else:
                logger.info(f"[rutas] Ruta {tipo_ruta} NO existe")
                return None
        else:
            logger.error(f"[rutas] Error searching for ruta: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"[rutas] Error buscando ruta {tipo_ruta}: {e}")
        return None

def _update_ruta(client, ruta_id: str, importe: float, fecha_caducidad: int, transitario_name: str = None) -> bool:
    """Actualiza el importe y fecha_caducidad de una ruta existente en HubSpot.
    """
    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        update_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/{ruta_id}"
        update_payload = {
            "properties": {
                "importe": str(importe),
                "fecha_caducidad": str(fecha_caducidad)
            }
        }

        if (transitario_name or "").strip():
            update_payload["properties"]["transitario_name"] = transitario_name.strip()

        response = requests.patch(update_url, headers=headers, json=update_payload, timeout=10)

        if response.ok:
            logger.info(f"[rutas] Ruta {ruta_id} actualizada: importe={importe}, fecha_caducidad={fecha_caducidad}")
            return True
        else:
            logger.error(f"[rutas] Error actualizando ruta {ruta_id}: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"[rutas] Error actualizando ruta {ruta_id}: {e}")
        return False

def _verificar_ruta_existe(
    client,
    tipo_ruta: str,
    transitario: str,
    lugar_carga: str = None,
    puerto_salida: str = None,
    puerto_destino: str = None,
    naviera: str = None,
    tipo_contenedor: str = None
) -> bool:
    """Verifica si una ruta terrestre o marítima existe en HubSpot.
    """
    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Normalize naviera: "SIN ESPECIFICAR" -> None
        naviera_normalizada = None
        if naviera and naviera.strip().upper() != "SIN ESPECIFICAR":
            naviera_normalizada = naviera

        filters = [
            {"propertyName": "transitario", "operator": "EQ", "value": transitario or ""}
        ]

        if tipo_contenedor:
            filters.append({"propertyName": "tipo_contenedor", "operator": "EQ", "value": tipo_contenedor})

        if tipo_ruta == "terrestre":
            # Terrestre: transitario + lugar_carga + puerto_salida + tipo_contenedor
            if lugar_carga:
                filters.append({"propertyName": "lugar_carga", "operator": "EQ", "value": lugar_carga})
            if puerto_salida:
                filters.append({"propertyName": "puerto_salida", "operator": "EQ", "value": puerto_salida})
        else:  # maritima
            # Marítima: transitario + puerto_salida + puerto_destino + naviera (opcional) + tipo_contenedor
            if puerto_salida:
                filters.append({"propertyName": "puerto_salida", "operator": "EQ", "value": puerto_salida})
            if puerto_destino:
                filters.append({"propertyName": "puerto_destino", "operator": "EQ", "value": puerto_destino})
            # Only filter by naviera if it's not "SIN ESPECIFICAR"
            if naviera_normalizada:
                filters.append({"propertyName": "naviera", "operator": "EQ", "value": naviera_normalizada})

        search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"
        search_payload = {
            "filterGroups": [{"filters": filters}],
            "properties": ["fecha_caducidad"],
            "limit": 1
        }

        response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

        if response.ok:
            results = response.json().get("results", [])
            if results:
                # Check if route is expired
                ruta = results[0]
                fecha_caducidad_str = ruta.get("properties", {}).get("fecha_caducidad")

                if fecha_caducidad_str:
                    try:
                        from datetime import datetime
                        fecha_caducidad = int(fecha_caducidad_str)
                        now_timestamp = int(datetime.now().timestamp() * 1000)

                        if fecha_caducidad < now_timestamp:
                            logger.info(f"[rutas] Ruta {tipo_ruta} encontrada pero caducada")
                            return False
                    except (ValueError, TypeError):
                        logger.warning(f"[rutas] Error parsing fecha_caducidad: {fecha_caducidad_str}")

                logger.info(f"[rutas] Ruta {tipo_ruta} existe y está vigente")
                return True
            else:
                logger.info(f"[rutas] Ruta {tipo_ruta} NO existe")
                return False
        else:
            logger.error(f"[rutas] Error searching for ruta: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"[rutas] Error verificando ruta {tipo_ruta}: {e}")
        return False

def _crear_ruta(
    client,
    tipo_ruta: str,
    transitario: str,
    tipo_contenedor: str,
    importe: float,
    fecha_caducidad: int,
    lugar_carga: str = None,
    puerto_salida: str = None,
    puerto_destino: str = None,
    naviera: str = None,
    localidad_destino: str = None,
    transitario_name: str = None
) -> Optional[str]:
    """Crea una nueva ruta terrestre o marítima en HubSpot.
    """
    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Normalize naviera: "SIN ESPECIFICAR" -> None
        naviera_normalizada = None
        if naviera and naviera.strip().upper() != "SIN ESPECIFICAR":
            naviera_normalizada = naviera

        # Build referencia field: tipo_contenedor__cardcode__naviera__lugar_carga__puerto_salida__puerto_destino__localidad_destino
        # Use "NA" for empty values or "SIN ESPECIFICAR"
        referencia_parts = [
            tipo_contenedor or "NA",
            transitario or "NA",
            naviera_normalizada or "NA",  # Use normalized naviera (NA if "SIN ESPECIFICAR")
            lugar_carga or "NA",
            puerto_salida or "NA",
            puerto_destino or "NA",
            localidad_destino or "NA"
        ]
        referencia = "__".join(referencia_parts)

        nombre_transitario = (transitario_name or "").strip()
        if not nombre_transitario and transitario:
            resueltos = _lookup_transitarios_por_cardcode(client, [transitario])
            nombre_transitario = resueltos.get(transitario.strip(), {}).get("name", "")

        properties = {
            "transitario": transitario or "",
            "tipo_contenedor": tipo_contenedor or "",
            "importe": str(importe),
            "fecha_caducidad": str(fecha_caducidad),
            "referencia": referencia
        }

        if nombre_transitario:
            properties["transitario_name"] = nombre_transitario
        else:
            logger.warning(f"[rutas] Sin nombre de transitario para cardcode={transitario!r}; la ruta se crea solo con el cardcode")

        if tipo_ruta == "terrestre":
            if lugar_carga:
                properties["lugar_carga"] = lugar_carga
            if puerto_salida:
                properties["puerto_salida"] = puerto_salida
        else:  # maritima
            if puerto_salida:
                properties["puerto_salida"] = puerto_salida
            if puerto_destino:
                properties["puerto_destino"] = puerto_destino
            # Only set naviera if it's not "SIN ESPECIFICAR"
            if naviera_normalizada:
                properties["naviera"] = naviera_normalizada

        if localidad_destino:
            properties["localidad_destino"] = localidad_destino

        create_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}"
        create_payload = {"properties": properties}

        response = requests.post(create_url, headers=headers, json=create_payload, timeout=10)

        if response.ok:
            ruta_id = response.json().get("id")
            logger.info(f"[rutas] Ruta {tipo_ruta} creada exitosamente: ID={ruta_id}")
            return ruta_id
        else:
            logger.error(f"[rutas] Error creating ruta {tipo_ruta}: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f"[rutas] Error creando ruta {tipo_ruta}: {e}")
        return None

def _diff_flete_elemento(
    cur_props: dict,
    precio: float,
    cardcode: str,
    iva_repercutido: str,
    iva_soportado: str,
    line_item_id: str,
    almacen,
) -> dict:
    """Props a actualizar del Elemento espejo de una línea de flete.

    El flete es pass-through del transitario: `precio_de_coste` sigue al precio del
    tramo, o el pedido de compra al transitario sale por el importe viejo."""
    updates = {}
    if _safe_float(cur_props.get("precio_unitario")) != precio:
        updates["precio_unitario"] = precio
    if _safe_float(cur_props.get("precio_total")) != precio:
        updates["precio_total"] = precio
    if _safe_float(cur_props.get("precio_de_coste")) != precio:
        updates["precio_de_coste"] = precio
    if (cur_props.get("cardcode") or "") != cardcode:
        updates["cardcode"] = cardcode
    if iva_repercutido and cur_props.get("tipo_iva_repercutido") != iva_repercutido:
        updates["tipo_iva_repercutido"] = iva_repercutido
    if iva_soportado and cur_props.get("tipo_iva_soportado") != iva_soportado:
        updates["tipo_iva_soportado"] = iva_soportado
    if str(cur_props.get("line_item_id") or "") != line_item_id:
        updates["line_item_id"] = line_item_id
    if almacen and (cur_props.get("almacen") or "") != str(almacen):
        updates["almacen"] = str(almacen)
    return updates

def _indexar_fletes_existentes(line_items: list) -> dict:
    """Indexa todas las líneas de flete del deal por (numero_container, tramo).

    Entran legacy y líneas de tramo en ambos modos: las que queden fuera de los
    targets del plan se archivan al normalizar (la legacy al pasar a modo doble,
    las de tramo al volver a modo simple)."""
    index = {}
    for item in line_items:
        props = item.get("properties", {}) or {}
        tramo = clasificar_tramo_flete(props.get("name"))
        if not tramo:
            continue
        # Los sufijos de tramo coinciden con nombres reales de producto del
        # catálogo: una línea de SERVICIO homónima no debe entrar al índice
        # (acabaría archivada). Las legacy '- FLETES' se indexan solo por
        # nombre, como siempre.
        if tramo != TRAMO_LEGACY and (props.get("tipo_articulo") or "").strip().lower() != "transitario":
            continue
        key = (props.get("numero_container") or "").strip()
        index[(key, tramo)] = {"id": item.get("id"), "properties": props}
    return index

def _plan_fletes_upsert(
    fletes_existentes: dict,
    target_keys,
    tramo_specs: list,
    container_by_key: dict,
    flete_almacen,
    deal_id: str,
) -> tuple:
    """Plan de upsert de las líneas de flete por (numero_container, tramo).

    Cada spec de tramo aporta sufijo de nombre, precio, cardcode del transitario,
    IVAs y producto. Devuelve (to_update, to_create, to_delete_ids,
    persisted_by_key); las líneas indexadas fuera de los targets (contenedor
    eliminado o legacy al pasar a modo doble) se archivan."""
    to_update = []
    to_create = []
    to_delete_ids = []
    persisted_by_key = {}
    target_flete_keys = set()

    for spec in tramo_specs:
        for key in target_keys:
            fkey = (key, spec["tramo"])
            target_flete_keys.add(fkey)
            existing = fletes_existentes.get(fkey)
            if existing:
                cur = existing["properties"]
                update_props = {}
                if _safe_float(cur.get("price")) != spec["precio"]:
                    update_props["price"] = str(spec["precio"])
                if (cur.get("cardcode") or "") != spec["cardcode"]:
                    update_props["cardcode"] = spec["cardcode"]
                if spec["tipo_iva_repercutido"] and cur.get("tipo_iva_repercutido") != spec["tipo_iva_repercutido"]:
                    update_props["tipo_iva_repercutido"] = spec["tipo_iva_repercutido"]
                if spec["tipo_iva_soportado"] and cur.get("tipo_iva_soportado") != spec["tipo_iva_soportado"]:
                    update_props["tipo_iva_soportado"] = spec["tipo_iva_soportado"]
                if flete_almacen and (cur.get("almacen") or "") != str(flete_almacen):
                    update_props["almacen"] = str(flete_almacen)
                if update_props:
                    to_update.append({"id": existing["id"], "properties": update_props})
                persisted_by_key[fkey] = existing["id"]
            else:
                if not spec["product_id"]:
                    logger.warning(
                        f"[transit] cannot create flete {spec['tramo']} for key={key!r}: product_id missing"
                    )
                    continue
                container_name = container_by_key.get(key, {}).get("name") or ""
                nombre_flete = f"{container_name} - {spec['sufijo']}" if container_name else spec["sufijo"]
                new_props = {
                    "hs_product_id": spec["product_id"],
                    "hs_sku": spec["product_id"],
                    "name": nombre_flete,
                    "price": str(spec["precio"]),
                    "quantity": "1",
                    "cardcode": spec["cardcode"],
                    "numero_container": key,
                    "tipo_articulo": "transitario",
                    "hs_product_type": "Flete",
                    "hs_currency": "EUR",
                }
                if spec["tipo_iva_repercutido"]:
                    new_props["tipo_iva_repercutido"] = spec["tipo_iva_repercutido"]
                if spec["tipo_iva_soportado"]:
                    new_props["tipo_iva_soportado"] = spec["tipo_iva_soportado"]
                if flete_almacen:
                    new_props["almacen"] = str(flete_almacen)
                to_create.append({
                    "properties": new_props,
                    "associations": [{
                        "to": {"id": deal_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}],
                    }],
                    "_target_key": fkey,
                })

    for fkey, existing in fletes_existentes.items():
        if fkey not in target_flete_keys:
            to_delete_ids.append(existing["id"])

    return to_update, to_create, to_delete_ids, persisted_by_key

@router.post("/{deal_id}/transit", response_model=BaseResponse)
async def update_deal_transit(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    ),
    transit_data: DealTransitUpdateRequest = None
):
    """Actualiza los datos de tránsito del deal (puertos, naviera, transitario, precios y caducidades de rutas).

    Si los precios están informados, hace upsert incremental de los line items FLETES (uno por contenedor) con `precio = precioOrigen + precioDestino` (ABSOLUTO por contenedor, no se divide por nº de contenedores: cada contenedor lleva su flete; el reparto por caja es intra-contenedor) y el `tipo_iva_repercutido` derivado de la residencia de la empresa. No toca los fletes de contenedores que no cambian.
    """
    try:
        client = hubspot_client.get_client()

        logger.info(f"Updating transit data for deal {deal_id} with data: {transit_data}")

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=["tipo_contenedor", "lugar_carga", "almacen", "transitario"]
        )
        tipo_contenedor = deal.properties.get("tipo_contenedor", "")
        lugar_carga = deal.properties.get("lugar_carga", "")
        deal_almacen = deal.properties.get("almacen")
        deal_transitario_actual = deal.properties.get("transitario") or ""

        # Modo doble flete (camión + tramo marítimo): el card manda transitario_terrestre no vacío
        modo_doble = bool((transit_data.transitario_terrestre or "").strip())

        logger.info(f"[rutas] Deal tipo_contenedor: {tipo_contenedor}, lugar_carga: {lugar_carga}, modo_doble: {modo_doble}")

        # Ensure deal has EUR as currency so all line items inherit it
        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": {"deal_currency_code": "EUR"}}
            )
        except Exception as e:
            logger.warning(f"[transit] Could not set deal_currency_code: {e}")

        # Build properties dict with only non-None values
        properties = {}

        if transit_data.puerto_salida is not None:
            properties["puerto_salida"] = transit_data.puerto_salida

        if transit_data.puerto_destino is not None:
            properties["puerto_destino"] = transit_data.puerto_destino

        if transit_data.localidad_destino is not None:
            properties["localidad_destino"] = transit_data.localidad_destino

        if transit_data.naviera is not None:
            properties["naviera"] = transit_data.naviera

        if transit_data.dias_libres is not None:
            properties["dias_libres"] = str(transit_data.dias_libres)

        if transit_data.transitario is not None:
            properties["transitario"] = transit_data.transitario

        if transit_data.precio_ruta_hasta_puerto_de_salida is not None:
            properties["precio_ruta_hasta_puerto_de_salida"] = str(transit_data.precio_ruta_hasta_puerto_de_salida)

        if transit_data.precio_ruta_hasta_puerto_destino is not None:
            properties["precio_ruta_hasta_puerto_destino"] = str(transit_data.precio_ruta_hasta_puerto_destino)

        if transit_data.fecha_caducidad_flete_salida is not None:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(transit_data.fecha_caducidad_flete_salida / 1000, tz=timezone.utc)
            dt_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            properties["fecha_caducidad_flete_salida"] = str(int(dt_midnight.timestamp() * 1000))

        if transit_data.fecha_caducidad_flete_destino is not None:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(transit_data.fecha_caducidad_flete_destino / 1000, tz=timezone.utc)
            dt_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            properties["fecha_caducidad_flete_destino"] = str(int(dt_midnight.timestamp() * 1000))

        if transit_data.cotizacion_rapida is not None:
            properties["cotizacion_rapida"] = transit_data.cotizacion_rapida

        if transit_data.almacen is not None:
            properties["almacen"] = transit_data.almacen

        effective_almacen = (
            transit_data.almacen
            if transit_data.almacen is not None
            else deal_almacen
        )

        if transit_data.tipo_iva_fletes is not None:
            properties["tipo_iva_fletes"] = transit_data.tipo_iva_fletes

        if transit_data.tipo_iva_fletes_soportado is not None:
            properties["tipo_iva_fletes_soportado"] = transit_data.tipo_iva_fletes_soportado

        logger.info(f"Updating deal {deal_id} with properties: {properties}")

        if properties:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": properties}
            )
            logger.info(f"Successfully updated transit data for deal {deal_id}")
        else:
            logger.warning(f"No properties to update for deal {deal_id}")

        # Props del tramo terrestre: solo si el card las envía (los cards viejos no
        # las mandan y pueden no existir aún en el portal); "" limpia. Update aparte
        # para no tumbar el resto de props si aún no están creadas.
        props_terrestre = {}
        if transit_data.transitario_terrestre is not None:
            props_terrestre["transitario_terrestre"] = transit_data.transitario_terrestre
        if transit_data.tipo_iva_fletes_terrestre is not None:
            props_terrestre["tipo_iva_fletes_terrestre"] = transit_data.tipo_iva_fletes_terrestre
        if transit_data.tipo_iva_fletes_terrestre_soportado is not None:
            props_terrestre["tipo_iva_fletes_terrestre_soportado"] = transit_data.tipo_iva_fletes_terrestre_soportado
        if props_terrestre:
            try:
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={"properties": props_terrestre}
                )
            except Exception as e:
                logger.warning(f"[transit] no pude escribir props del flete terrestre (¿existen en el portal?): {e}")

        # Manage transitario provider association
        if transit_data.transitario is not None:
            transitario_cardcode = transit_data.transitario.strip()

            try:
                CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
                access_token = client.access_token
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                # Proveedor del tramo marítimo/principal: si el terrestre es el
                # mismo, el PUT v4 reemplaza etiquetas, así que hay que enviar las
                # dos juntas para no borrar la marítima (288).
                maritimo_provider_id = None

                if transitario_cardcode:
                    # Search for provider with tipo_proveedor="transitario" and matching cardcode
                    logger.info(f"[transit] Searching for transitario provider with cardcode: {transitario_cardcode}")

                    search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/search"
                    search_payload = {
                        "filterGroups": [{
                            "filters": [
                                {"propertyName": "cardcode", "operator": "EQ", "value": transitario_cardcode},
                                {"propertyName": "tipo_proveedor", "operator": "EQ", "value": "transitario"}
                            ]
                        }],
                        "properties": ["name", "cardcode", "tipo_proveedor"],
                        "limit": 1
                    }

                    search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

                    if search_response.ok:
                        search_data = search_response.json()
                        if search_data.get("results"):
                            provider = search_data["results"][0]
                            provider_id = provider["id"]
                            provider_name = provider["properties"].get("name", "")
                            maritimo_provider_id = provider_id

                            logger.info(f"[transit] Found transitario provider: id={provider_id}, name={provider_name}")

                            # Remove all existing transitario provider associations
                            existing_provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                            if existing_provider_ids:
                                batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                                batch_payload = {
                                    "properties": ["tipo_proveedor"],
                                    "inputs": [{"id": str(pid)} for pid in existing_provider_ids]
                                }

                                try:
                                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                                    if batch_response.ok:
                                        for prov in batch_response.json().get("results", []):
                                            if prov.get("properties", {}).get("tipo_proveedor") == "transitario":
                                                prov_id = prov.get("id")
                                                # Remove association
                                                delete_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{prov_id}/associations/deals/{deal_id}"
                                                requests.delete(delete_url, headers=headers, timeout=10)
                                                logger.info(f"[transit] Removed existing transitario provider association: {prov_id}")
                                except Exception as e:
                                    logger.error(f"[transit] Error removing existing transitario providers: {e}")

                            # Create new association (associationTypeId: 288 = "Transitario")
                            assoc_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{provider_id}/associations/deals/{deal_id}"
                            assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 288}]

                            assoc_response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                            if assoc_response.ok:
                                logger.info(f"[transit] Successfully associated transitario provider {provider_id} with deal {deal_id} (associationTypeId: 288 - Transitario)")
                            else:
                                logger.error(f"[transit] Failed to associate provider: {assoc_response.status_code} - {assoc_response.text}")

                        else:
                            logger.warning(f"[transit] No transitario provider found with cardcode={transitario_cardcode}")
                    else:
                        logger.error(f"[transit] Search for transitario provider failed: {search_response.status_code}")

                else:
                    # transitario is empty, remove all transitario provider associations
                    logger.info(f"[transit] transitario is empty, removing all transitario provider associations")

                    existing_provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                    if existing_provider_ids:
                        batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                        batch_payload = {
                            "properties": ["tipo_proveedor"],
                            "inputs": [{"id": str(pid)} for pid in existing_provider_ids]
                        }

                        try:
                            batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                            if batch_response.ok:
                                for prov in batch_response.json().get("results", []):
                                    if prov.get("properties", {}).get("tipo_proveedor") == "transitario":
                                        prov_id = prov.get("id")
                                        # Remove association
                                        delete_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{prov_id}/associations/deals/{deal_id}"
                                        requests.delete(delete_url, headers=headers, timeout=10)
                                        logger.info(f"[transit] Removed transitario provider association: {prov_id}")
                        except Exception as e:
                            logger.error(f"[transit] Error removing transitario providers: {e}")

                # Segundo transitario (modo doble): asociación con etiqueta propia.
                # El borrado de arriba ya limpió todas las de tipo transitario.
                if modo_doble:
                    if PROVEEDOR_ASSOC_TYPE_ID_TRANSITARIO_TERRESTRE is None:
                        logger.warning(
                            "[transit] etiqueta 'Transitario terrestre' sin associationTypeId configurado; "
                            "no se asocia el proveedor del tramo terrestre"
                        )
                    else:
                        terrestre_cardcode = transit_data.transitario_terrestre.strip()
                        terrestre_search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/search"
                        terrestre_search_payload = {
                            "filterGroups": [{
                                "filters": [
                                    {"propertyName": "cardcode", "operator": "EQ", "value": terrestre_cardcode},
                                    {"propertyName": "tipo_proveedor", "operator": "EQ", "value": "transitario"}
                                ]
                            }],
                            "properties": ["name", "cardcode", "tipo_proveedor"],
                            "limit": 1
                        }
                        terrestre_response = requests.post(terrestre_search_url, headers=headers, json=terrestre_search_payload, timeout=10)
                        if terrestre_response.ok and terrestre_response.json().get("results"):
                            terrestre_provider_id = terrestre_response.json()["results"][0]["id"]
                            assoc_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{terrestre_provider_id}/associations/deals/{deal_id}"
                            assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": PROVEEDOR_ASSOC_TYPE_ID_TRANSITARIO_TERRESTRE}]
                            # Mismo proveedor en ambos tramos: el PUT reemplaza, así
                            # que reincluimos la etiqueta marítima (288) para conservarla.
                            if terrestre_provider_id == maritimo_provider_id:
                                assoc_payload.append({"associationCategory": "USER_DEFINED", "associationTypeId": 288})
                            assoc_response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)
                            if assoc_response.ok:
                                logger.info(f"[transit] Associated transitario terrestre {terrestre_provider_id} with deal {deal_id}")
                            else:
                                logger.error(f"[transit] Failed to associate transitario terrestre: {assoc_response.status_code} - {assoc_response.text}")
                        else:
                            logger.warning(f"[transit] No transitario terrestre provider found with cardcode={terrestre_cardcode}")

            except Exception as e:
                logger.error(f"[transit] Error managing transitario provider association: {e}")

        if (transit_data.precio_ruta_hasta_puerto_de_salida is not None or
            transit_data.precio_ruta_hasta_puerto_destino is not None):

            precio_origen = transit_data.precio_ruta_hasta_puerto_de_salida or 0
            precio_destino = transit_data.precio_ruta_hasta_puerto_destino or 0
            precio_total_fletes = round_price(precio_origen + precio_destino)

            target_cardcode = (
                transit_data.transitario
                if transit_data.transitario is not None
                else deal_transitario_actual
            ) or ""

            existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
            access_token = client.access_token
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            batch_size = 100

            line_items_leidos = []
            container_line_items = []

            if existing_line_item_ids:
                for i in range(0, len(existing_line_item_ids), batch_size):
                    batch_ids = existing_line_item_ids[i:i + batch_size]
                    batch_payload = {
                        "properties": [
                            "name", "hs_product_type", "numero_container", "tipo_articulo",
                            "price", "precio_de_coste", "cardcode", "almacen",
                            "tipo_iva_repercutido", "tipo_iva_soportado",
                        ],
                        "inputs": [{"id": str(lid)} for lid in batch_ids],
                    }
                    try:
                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            for item in batch_response.json().get("results", []):
                                props = item.get("properties", {}) or {}
                                product_type = (props.get("hs_product_type") or "").strip().lower()
                                line_items_leidos.append({"id": item.get("id"), "properties": props})
                                if _is_container_product_type(product_type):
                                    container_line_items.append({
                                        "id": item.get("id"),
                                        "name": props.get("name", ""),
                                        "numero_container": props.get("numero_container", ""),
                                    })
                        else:
                            logger.error(f"[transit] batch read failed: {batch_response.status_code} - {batch_response.text}")
                    except Exception as e:
                        logger.error(f"[transit] error in batch read line items: {e}")

            fletes_existentes_by_tramo = _indexar_fletes_existentes(line_items_leidos)

            residennum = None
            equ = ""
            tipo_cliente = ""
            try:
                company_ids = get_associated_companies(deal_id)
                if company_ids:
                    company_id = company_ids[0]
                    company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                    company_response = requests.get(
                        company_url, headers=headers,
                        params={"properties": "residennum,equ,tipo_cliente"}, timeout=10,
                    )
                    if company_response.ok:
                        company_props_iva = company_response.json().get("properties", {}) or {}
                        residennum = company_props_iva.get("residennum") or ""
                        equ = company_props_iva.get("equ") or ""
                        tipo_cliente = company_props_iva.get("tipo_cliente") or ""
            except Exception as e:
                logger.error(f"[transit] error fetching company residence: {e}")

            # FLETES no son producto → se reubican fuera de San Roque (10/12)
            flete_almacen = resolve_almacen_para_elemento(effective_almacen, "flete", tipo_contenedor)

            tipo_iva_repercutido = transit_data.tipo_iva_fletes or None
            tipo_iva_soportado = transit_data.tipo_iva_fletes_soportado or None

            fletes_product_id = None
            try:
                search_response = client.crm.products.search_api.do_search(
                    public_object_search_request={
                        "filterGroups": [{"filters": [{"propertyName": "cardcode", "operator": "EQ", "value": "VARIAB-TRANSP-00001"}]}],
                        "properties": [
                            "name", "hs_sku", "price",
                            "tipo_iva_soportado", "tipo_iva_repercutido",
                            "tipo_iva_soportado_eu", "tipo_iva_repercutido_eu",
                            "tipo_iva_soportado_ex", "tipo_iva_repercutido_ex",
                        ],
                        "limit": 1,
                    }
                )
                if search_response.results and len(search_response.results) > 0:
                    fletes_product = search_response.results[0]
                    fletes_product_id = str(fletes_product.id)
                    tipo_iva_repercutido = resolve_product_iva(
                        fletes_product.properties, residennum, "repercutido", tipo_iva_repercutido,
                    )
                    tipo_iva_soportado = resolve_product_iva(
                        fletes_product.properties, residennum, "soportado", tipo_iva_soportado,
                    )
                else:
                    logger.warning("[transit] FLETES product not found in HubSpot (cardcode VARIAB-TRANSP-00001)")
            except Exception as e:
                logger.error(f"[transit] error searching FLETES product: {e}", exc_info=True)

            # Tramo terrestre (modo doble): mismo producto que el marítimo; IVA con
            # fallback al marítimo si viene vacío (nunca al default por residencia)
            flete_terrestre_product_id = None
            tipo_iva_terrestre_rep = None
            tipo_iva_terrestre_sop = None
            if modo_doble:
                flete_terrestre_product_id = fletes_product_id
                tipo_iva_terrestre_rep = transit_data.tipo_iva_fletes_terrestre or tipo_iva_repercutido
                tipo_iva_terrestre_sop = transit_data.tipo_iva_fletes_terrestre_soportado or tipo_iva_soportado

            container_by_key = {c["numero_container"]: c for c in container_line_items}
            target_keys = set(container_by_key.keys()) if container_line_items else {""}

            if modo_doble:
                tramo_specs = [
                    {
                        "tramo": TRAMO_MARITIMO,
                        "sufijo": FLETE_SUFIJO_MARITIMO,
                        "precio": round_price(precio_destino),
                        "cardcode": target_cardcode,
                        "tipo_iva_repercutido": tipo_iva_repercutido,
                        "tipo_iva_soportado": tipo_iva_soportado,
                        "product_id": fletes_product_id,
                    },
                    {
                        "tramo": TRAMO_TERRESTRE,
                        "sufijo": FLETE_SUFIJO_TERRESTRE,
                        "precio": round_price(precio_origen),
                        "cardcode": (transit_data.transitario_terrestre or "").strip(),
                        "tipo_iva_repercutido": tipo_iva_terrestre_rep,
                        "tipo_iva_soportado": tipo_iva_terrestre_sop,
                        "product_id": flete_terrestre_product_id,
                    },
                ]
            else:
                tramo_specs = [{
                    "tramo": TRAMO_LEGACY,
                    "sufijo": "FLETES",
                    "precio": precio_total_fletes,
                    "cardcode": target_cardcode,
                    "tipo_iva_repercutido": tipo_iva_repercutido,
                    "tipo_iva_soportado": tipo_iva_soportado,
                    "product_id": fletes_product_id,
                }]
            spec_by_tramo = {s["tramo"]: s for s in tramo_specs}

            to_update, to_create, to_delete_ids, persisted_by_key = _plan_fletes_upsert(
                fletes_existentes_by_tramo, target_keys, tramo_specs,
                container_by_key, flete_almacen, deal_id,
            )

            if to_update:
                upd_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/update"
                for i in range(0, len(to_update), batch_size):
                    chunk = to_update[i:i + batch_size]
                    for x in chunk:
                        enforce_line_item_price_rounding(x.get("properties", {}))
                    try:
                        resp = requests.post(upd_url, headers=headers, json={"inputs": chunk}, timeout=60)
                        if not resp.ok:
                            logger.error(f"[transit] batch update FLETES failed: {resp.status_code} - {resp.text}")
                    except Exception as e:
                        logger.error(f"[transit] error batch updating FLETES: {e}")

            created_ids_by_key = {}
            if to_create:
                cr_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
                for i in range(0, len(to_create), batch_size):
                    chunk = to_create[i:i + batch_size]
                    for x in chunk:
                        enforce_line_item_price_rounding(x.get("properties", {}))
                    payload = {"inputs": [{"properties": x["properties"], "associations": x["associations"]} for x in chunk]}
                    try:
                        resp = requests.post(cr_url, headers=headers, json=payload, timeout=60)
                        if resp.ok:
                            # HubSpot no garantiza el orden de los results: cada flete se
                            # correlaciona por (numero_container, tramo) —su _target_key—
                            # leído de la respuesta, no por posición.
                            key_by_ident = {
                                (
                                    (x["properties"].get("numero_container") or "").strip(),
                                    clasificar_tramo_flete(x["properties"].get("name")),
                                ): x["_target_key"]
                                for x in chunk
                            }
                            for created in resp.json().get("results", []):
                                new_id = created.get("id")
                                cprops = created.get("properties") or {}
                                ident = (
                                    (cprops.get("numero_container") or "").strip(),
                                    clasificar_tramo_flete(cprops.get("name")),
                                )
                                tk = key_by_ident.get(ident)
                                if new_id and tk is not None:
                                    created_ids_by_key[tk] = new_id
                                    persisted_by_key[tk] = new_id
                                elif new_id:
                                    logger.error(f"[transit] flete creado id={new_id} no casa con ningún input (numero_container, tramo)={ident!r}")
                        else:
                            logger.error(f"[transit] batch create FLETES failed: {resp.status_code} - {resp.text}")
                    except Exception as e:
                        logger.error(f"[transit] error batch creating FLETES: {e}")

            if to_delete_ids:
                del_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
                for i in range(0, len(to_delete_ids), batch_size):
                    chunk = to_delete_ids[i:i + batch_size]
                    try:
                        resp = requests.post(del_url, headers=headers, json={"inputs": [{"id": str(x)} for x in chunk]}, timeout=30)
                        if not resp.ok:
                            logger.error(f"[transit] batch delete FLETES failed: {resp.status_code} - {resp.text}")
                    except Exception as e:
                        logger.error(f"[transit] error batch deleting FLETES: {e}")

            try:
                elementos = get_elementos_by_deal(
                    client, deal_id,
                    properties=[
                        "nombre", "tipo_articulo", "cantidad", "precio_unitario",
                        "precio_de_coste", "precio_total", "cardcode",
                        "tipo_iva_repercutido", "tipo_iva_soportado", "line_item_id",
                        "nombre_contenedor",
                    ],
                )
            except Exception as e:
                logger.warning(f"[transit] could not load ELEMENTOS for deal {deal_id}: {e}")
                elementos = []

            elementos_flete_by_lid = {}
            elementos_flete_by_nombre = {}
            for el in elementos:
                props = el.get("properties", {}) or {}
                if (props.get("tipo_articulo") or "").lower() != "flete":
                    continue
                lid = props.get("line_item_id")
                nombre = (props.get("nombre") or "").strip()
                entry = {"id": el.get("id"), "properties": props}
                if lid:
                    elementos_flete_by_lid[str(lid).strip()] = entry
                if nombre:
                    elementos_flete_by_nombre[nombre] = entry

            affected_contenedor_co_ids = set()
            elementos_updated = 0
            elementos_orphan_created = 0
            new_lids_set = set(created_ids_by_key.values())
            cont_map_for_orphan = None
            for fkey, lid in persisted_by_key.items():
                key, tramo_flete = fkey
                spec = spec_by_tramo[tramo_flete]
                precio_flete_tramo = spec["precio"]
                cardcode_tramo = spec["cardcode"]
                iva_rep_tramo = spec["tipo_iva_repercutido"]
                iva_sop_tramo = spec["tipo_iva_soportado"]
                target_lid = str(lid).strip()
                if target_lid in {str(x) for x in new_lids_set}:
                    continue

                elem = elementos_flete_by_lid.get(target_lid)
                match_via = "line_item_id" if elem else None
                if not elem:
                    existing = fletes_existentes_by_tramo.get(fkey)
                    nombre_flete = (existing["properties"].get("name") or "").strip() if existing else ""
                    if nombre_flete:
                        elem = elementos_flete_by_nombre.get(nombre_flete)
                        if elem:
                            match_via = "nombre"
                if not elem:
                    if cont_map_for_orphan is None:
                        cont_map_for_orphan = _resolve_contenedor_co_by_numero(client, deal_id)
                    container_info = container_by_key.get(key, {})
                    container_name = container_info.get("name") or ""
                    container_lid = str(container_info.get("id") or "")
                    co = (
                        cont_map_for_orphan.get(key)
                        or cont_map_for_orphan.get(container_name)
                        or cont_map_for_orphan.get(f"lid:{container_lid}")
                    )
                    if not co:
                        logger.warning(
                            f"[transit] orphan flete: no CONTENEDOR CO for line_item_id={target_lid} key={fkey!r}"
                        )
                        continue
                    existing = fletes_existentes_by_tramo.get(fkey)
                    nombre_flete = (
                        (existing["properties"].get("name") or "").strip() if existing else ""
                    ) or (f"{container_name} - {spec['sufijo']}" if container_name else spec["sufijo"])
                    orphan_data = [{
                        "nombre": nombre_flete,
                        "nombre_contenedor": container_name,
                        "tipo_articulo": "flete",
                        "cantidad": 1,
                        "precio_unitario": precio_flete_tramo,
                        "precio_de_coste": precio_flete_tramo,
                        "precio_total": precio_flete_tramo,
                        "cardcode": cardcode_tramo,
                        "line_item_id": target_lid,
                    }]
                    if iva_rep_tramo:
                        orphan_data[0]["tipo_iva_repercutido"] = iva_rep_tramo
                    if iva_sop_tramo:
                        orphan_data[0]["tipo_iva_soportado"] = iva_sop_tramo
                    if flete_almacen:
                        orphan_data[0]["almacen"] = str(flete_almacen)
                    try:
                        contact_id_o = (get_associated_contacts(deal_id) or [None])[0]
                        company_id_o = (get_associated_companies(deal_id) or [None])[0]
                        created = batch_create_elementos_with_associations(
                            client=client,
                            elementos_data=orphan_data,
                            deal_id=deal_id,
                            contenedor_id=co.get("id"),
                            contact_id=contact_id_o,
                            company_id=company_id_o,
                        )
                        if created:
                            elementos_orphan_created += 1
                            affected_contenedor_co_ids.add(co.get("id"))
                    except Exception as e:
                        logger.warning(
                            f"[transit] orphan flete create failed for line_item_id={target_lid}: {e}"
                        )
                    continue

                cur_props = elem["properties"]
                elem_update_props = _diff_flete_elemento(
                    cur_props,
                    precio_flete_tramo,
                    cardcode_tramo,
                    iva_rep_tramo,
                    iva_sop_tramo,
                    target_lid,
                    flete_almacen,
                )

                if not elem_update_props:
                    continue

                if update_elemento(client, elem["id"], elem_update_props):
                    elementos_updated += 1
                    if cont_map_for_orphan is None:
                        cont_map_for_orphan = _resolve_contenedor_co_by_numero(client, deal_id)
                    co_upd = (
                        cont_map_for_orphan.get(key)
                        or cont_map_for_orphan.get(container_by_key.get(key, {}).get("name") or "")
                    )
                    if co_upd:
                        affected_contenedor_co_ids.add(co_upd.get("id"))

            elementos_created = 0
            if to_create and created_ids_by_key:
                contenedores_co_by_numero = _resolve_contenedor_co_by_numero(client, deal_id)
                contact_id = (get_associated_contacts(deal_id) or [None])[0]
                company_id = (get_associated_companies(deal_id) or [None])[0]

                for x in to_create:
                    fkey = x["_target_key"]
                    key, tramo_flete = fkey
                    spec = spec_by_tramo[tramo_flete]
                    new_lid = created_ids_by_key.get(fkey)
                    if not new_lid:
                        continue
                    container_info = container_by_key.get(key, {})
                    container_name = container_info.get("name") or ""
                    container_lid = str(container_info.get("id") or "")
                    co = (
                        contenedores_co_by_numero.get(key)
                        or contenedores_co_by_numero.get(container_name)
                        or contenedores_co_by_numero.get(f"lid:{container_lid}")
                    )
                    if not co:
                        logger.warning(
                            f"[transit] no CONTENEDOR CO matched for key={fkey!r} "
                            f"name={container_name!r}; available keys={list(contenedores_co_by_numero.keys())}"
                        )
                        continue
                    nombre_flete = x["properties"]["name"]
                    elementos_data = [{
                        "nombre": nombre_flete,
                        "nombre_contenedor": container_name,
                        "tipo_articulo": "flete",
                        "cantidad": 1,
                        "precio_unitario": spec["precio"],
                        "precio_de_coste": spec["precio"],
                        "precio_total": spec["precio"],
                        "cardcode": spec["cardcode"],
                        "line_item_id": str(new_lid),
                    }]
                    if spec["tipo_iva_repercutido"]:
                        elementos_data[0]["tipo_iva_repercutido"] = spec["tipo_iva_repercutido"]
                    if spec["tipo_iva_soportado"]:
                        elementos_data[0]["tipo_iva_soportado"] = spec["tipo_iva_soportado"]
                    if flete_almacen:
                        elementos_data[0]["almacen"] = str(flete_almacen)
                    created = batch_create_elementos_with_associations(
                        client=client,
                        elementos_data=elementos_data,
                        deal_id=deal_id,
                        contenedor_id=co.get("id"),
                        contact_id=contact_id,
                        company_id=company_id,
                    )
                    if created:
                        elementos_created += len(created)
                        affected_contenedor_co_ids.add(co.get("id"))

            elementos_deleted = 0
            for lid in to_delete_ids:
                elem = elementos_flete_by_lid.get(str(lid).strip())
                if elem and delete_elemento(client, elem["id"]):
                    elementos_deleted += 1
                    nc_del = ((elem.get("properties") or {}).get("nombre_contenedor") or "").strip()
                    if nc_del:
                        if cont_map_for_orphan is None:
                            cont_map_for_orphan = _resolve_contenedor_co_by_numero(client, deal_id)
                        co_del = cont_map_for_orphan.get(nc_del)
                        if co_del:
                            affected_contenedor_co_ids.add(co_del.get("id"))

            if to_update or to_create or to_delete_ids or elementos_updated or elementos_created or elementos_deleted or elementos_orphan_created:
                logger.info(
                    f"[transit] FLETES sync done: line_items updated={len(to_update)} "
                    f"created={len(to_create)} deleted={len(to_delete_ids)} | "
                    f"ELEMENTOS updated={elementos_updated} created={elementos_created} "
                    f"orphan_created={elementos_orphan_created} deleted={elementos_deleted}"
                )

        ruta_terrestre_creada = False
        ruta_maritima_creada = False

        # Ids de las rutas (custom object) a asociar al negocio. Autoritativos: los
        # que resuelve el upsert de abajo. Si el card mandó una selección la usamos
        # como fallback (p.ej. DAP terrestre, donde el upsert no aplica).
        assoc_ruta_terrestre_id = transit_data.ruta_terrestre_id
        assoc_ruta_maritima_id = transit_data.ruta_maritima_id

        # La ruta terrestre lleva el transitario de su tramo (fallback al principal = hoy)
        transitario_ruta_terrestre = (transit_data.transitario_terrestre or "").strip() or transit_data.transitario

        try:
            # Only process routes if we have the necessary data
            if transit_data.transitario:

                # El nombre viaja al objeto Ruta para que el listado no dependa de
                # resolver el Proveedor; el que manda el card es solo el respaldo.
                nombres_transitarios = _lookup_transitarios_por_cardcode(
                    client, [transit_data.transitario, transitario_ruta_terrestre]
                )

                def _nombre_transitario(cardcode: str, hint: Optional[str]) -> Optional[str]:
                    code = (cardcode or "").strip()
                    if not code:
                        return None
                    return nombres_transitarios.get(code, {}).get("name") or (hint or "").strip() or None

                nombre_transitario_maritimo = _nombre_transitario(
                    transit_data.transitario, transit_data.transitario_name
                )
                nombre_transitario_terrestre = _nombre_transitario(
                    transitario_ruta_terrestre,
                    transit_data.transitario_terrestre_name
                    if (transit_data.transitario_terrestre or "").strip()
                    else transit_data.transitario_name,
                )

                # RUTA TERRESTRE: lugar_carga + puerto_salida
                if lugar_carga and transit_data.puerto_salida and transit_data.precio_ruta_hasta_puerto_de_salida \
                        and transit_data.fecha_caducidad_flete_salida:
                    logger.info(f"[rutas] Procesando ruta terrestre: {lugar_carga} -> {transit_data.puerto_salida}")

                    ruta_id_terrestre = _find_ruta(
                        client=client,
                        tipo_ruta="terrestre",
                        transitario=transitario_ruta_terrestre,
                        lugar_carga=lugar_carga,
                        puerto_salida=transit_data.puerto_salida,
                        tipo_contenedor=tipo_contenedor
                    )

                    if ruta_id_terrestre:
                        logger.info(f"[rutas] Ruta terrestre existe, actualizando...")
                        _update_ruta(
                            client=client,
                            ruta_id=ruta_id_terrestre,
                            importe=transit_data.precio_ruta_hasta_puerto_de_salida,
                            fecha_caducidad=transit_data.fecha_caducidad_flete_salida,
                            transitario_name=nombre_transitario_terrestre
                        )
                        assoc_ruta_terrestre_id = ruta_id_terrestre
                    else:
                        logger.info(f"[rutas] Ruta terrestre NO existe, creando...")
                        ruta_id = _crear_ruta(
                            client=client,
                            tipo_ruta="terrestre",
                            transitario=transitario_ruta_terrestre,
                            tipo_contenedor=tipo_contenedor,
                            importe=transit_data.precio_ruta_hasta_puerto_de_salida,
                            fecha_caducidad=transit_data.fecha_caducidad_flete_salida,
                            lugar_carga=lugar_carga,
                            puerto_salida=transit_data.puerto_salida,
                            localidad_destino=transit_data.localidad_destino,
                            transitario_name=nombre_transitario_terrestre
                        )
                        if ruta_id:
                            ruta_terrestre_creada = True
                            assoc_ruta_terrestre_id = ruta_id
                            logger.info(f"[rutas] Ruta terrestre creada: {ruta_id}")

                # RUTA MARÍTIMA: puerto_salida + puerto_destino
                if transit_data.puerto_salida and transit_data.puerto_destino \
                        and transit_data.precio_ruta_hasta_puerto_destino \
                        and transit_data.fecha_caducidad_flete_destino:
                    logger.info(f"[rutas] Procesando ruta marítima: {transit_data.puerto_salida} -> {transit_data.puerto_destino}")

                    ruta_id_maritima = _find_ruta(
                        client=client,
                        tipo_ruta="maritima",
                        transitario=transit_data.transitario,
                        puerto_salida=transit_data.puerto_salida,
                        puerto_destino=transit_data.puerto_destino,
                        naviera=transit_data.naviera,
                        tipo_contenedor=tipo_contenedor
                    )

                    if ruta_id_maritima:
                        logger.info(f"[rutas] Ruta marítima existe, actualizando...")
                        _update_ruta(
                            client=client,
                            ruta_id=ruta_id_maritima,
                            importe=transit_data.precio_ruta_hasta_puerto_destino,
                            fecha_caducidad=transit_data.fecha_caducidad_flete_destino,
                            transitario_name=nombre_transitario_maritimo
                        )
                        assoc_ruta_maritima_id = ruta_id_maritima
                    else:
                        logger.info(f"[rutas] Ruta marítima NO existe, creando...")
                        ruta_id = _crear_ruta(
                            client=client,
                            tipo_ruta="maritima",
                            transitario=transit_data.transitario,
                            tipo_contenedor=tipo_contenedor,
                            importe=transit_data.precio_ruta_hasta_puerto_destino,
                            fecha_caducidad=transit_data.fecha_caducidad_flete_destino,
                            puerto_salida=transit_data.puerto_salida,
                            puerto_destino=transit_data.puerto_destino,
                            naviera=transit_data.naviera,
                            localidad_destino=transit_data.localidad_destino,
                            transitario_name=nombre_transitario_maritimo
                        )
                        if ruta_id:
                            ruta_maritima_creada = True
                            assoc_ruta_maritima_id = ruta_id
                            logger.info(f"[rutas] Ruta marítima creada: {ruta_id}")

        except Exception as e:
            logger.error(f"[rutas] Error processing routes: {e}", exc_info=True)
            # Don't fail the entire request if route creation fails

        # Asociar el negocio a las rutas (custom object) resueltas arriba.
        try:
            ruta_sync = sync_deal_ruta_associations(
                client,
                deal_id,
                ruta_terrestre_id=assoc_ruta_terrestre_id,
                ruta_maritima_id=assoc_ruta_maritima_id,
            )
            logger.info(f"[transit] rutas asociadas al deal {deal_id}: {ruta_sync}")
        except Exception as e:
            logger.error(f"[transit] Error asociando rutas al deal {deal_id}: {e}", exc_info=True)

        try:
            if affected_contenedor_co_ids:
                try:
                    await hubdb_client.preload_iva_table()
                    recalcular_contenedores(
                        client,
                        deal_id,
                        list(affected_contenedor_co_ids),
                        {"equ": equ, "residennum": residennum or ""},
                    )
                except Exception as e:
                    logger.warning(f"[transit] recalcular_contenedores falló: {e}")
                touch_pagos_for_contenedores(client, list(affected_contenedor_co_ids))
        except NameError:
            pass

        return {
            "success": True,
            "error": None,
            "rutas_creadas": {
                "ruta_terrestre_creada": ruta_terrestre_creada,
                "ruta_maritima_creada": ruta_maritima_creada
            }
        }

    except Exception as e:
        logger.error(f"Error updating transit data for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/transitarios/search", response_model=dict)
async def search_transitarios(
    q: str = ""
):
    """Busca transitarios (custom object Proveedores `2-43108800` con tipo_proveedor=transitario)."""
    try:
        client = hubspot_client.get_client()

        # Custom object ID for Proveedores
        CUSTOM_OBJECT_PROVEEDORES = "2-43108800"

        # Build search request
        filters = []

        filters.append(Filter(
            property_name="tipo_proveedor",
            operator="EQ",
            value="transitario"
        ))

        # If search query provided, filter by name with wildcards
        if q:
            filters.append(Filter(
                property_name="name",
                operator="CONTAINS_TOKEN",
                value=f"*{q}*"
            ))

        filter_group = FilterGroup(filters=filters)

        search_request = PublicObjectSearchRequest(
            filter_groups=[filter_group],
            properties=[
                "name",
                "cardcode",
                "tipo_proveedor"
            ],
            limit=20,
            sorts=[{"propertyName": "name", "direction": "ASCENDING"}]
        )

        search_results = client.crm.objects.search_api.do_search(
            object_type=CUSTOM_OBJECT_PROVEEDORES,
            public_object_search_request=search_request
        )

        # Build response
        transitarios = []
        for result in search_results.results:
            props = result.properties
            transitarios.append({
                "id": result.id,
                "name": props.get("name", ""),
                "cardcode": props.get("cardcode", ""),
                "tipo_proveedor": props.get("tipo_proveedor", "")
            })

        return {
            "success": True,
            "transitarios": transitarios,
            "total": len(transitarios)
        }

    except Exception as e:
        logger.error(f"Error searching transitarios: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{deal_id}/rutas", response_model=DealRutasResponse)
async def get_deal_rutas(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    ),
    include_expired: bool = False,
    puerto_salida: Optional[str] = None,
    puerto_destino: Optional[str] = None,
    localidad_destino: Optional[str] = None
):
    """Devuelve las rutas válidas (custom object `2-53295516`) para el deal, separadas en terrestres y marítimas.

    Filtros: tipo_contenedor, lugar_carga, puerto_salida, puerto_destino, naviera (todos del deal salvo overrides en query) y `fecha_caducidad > hoy`. Con `include_expired=true` se aceptan caducadas hace menos de 60 días. Si llega `localidad_destino` (DAP TERRESTRE) se buscan rutas directas (lugar_carga → localidad_destino) y se omite la búsqueda marítima.
    """
    try:
        from datetime import datetime, timedelta

        client = hubspot_client.get_client()
        access_token = client.access_token

        # Custom object ID for Rutas
        CUSTOM_OBJECT_RUTAS = "2-53295516"

        logger.warning(f"Fetching rutas for deal {deal_id}")

        deal_properties = [
            'tipo_contenedor',
            'lugar_carga',
            'puerto_salida',
            'puerto_destino',
            'naviera'
        ]

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties
        )

        deal_tipo_contenedor = deal.properties.get('tipo_contenedor')
        deal_lugar_carga = deal.properties.get('lugar_carga')
        deal_puerto_salida = deal.properties.get('puerto_salida')
        deal_puerto_destino = deal.properties.get('puerto_destino')
        deal_naviera = deal.properties.get('naviera')

        # Use query parameters if provided, otherwise use deal values
        effective_puerto_salida = puerto_salida if puerto_salida is not None else deal_puerto_salida
        effective_puerto_destino = puerto_destino if puerto_destino is not None else deal_puerto_destino

        logger.info(f"Deal filters - tipo_contenedor: {deal_tipo_contenedor}, lugar_carga: {deal_lugar_carga}, puerto_salida: {effective_puerto_salida} (from: {'query' if puerto_salida else 'deal'}), puerto_destino: {effective_puerto_destino} (from: {'query' if puerto_destino else 'deal'}), naviera: {deal_naviera}")

        # We'll need to do 2 separate searches: one for terrestrial routes, one for maritime routes

        rutas_terrestres = []
        rutas_maritimas = []

        # No port filtering, no maritime search
        if localidad_destino:
            if deal_tipo_contenedor and deal_lugar_carga:
                try:
                    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                    search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"

                    filters_directo = [
                        {"propertyName": "tipo_contenedor", "operator": "EQ", "value": deal_tipo_contenedor},
                        {"propertyName": "lugar_carga", "operator": "EQ", "value": deal_lugar_carga},
                        {"propertyName": "localidad_destino", "operator": "EQ", "value": localidad_destino},
                    ]

                    search_payload = {
                        "filterGroups": [{"filters": filters_directo}],
                        "properties": [
                            "referencia",
                            "tipo_contenedor",
                            "transitario",
                            "transitario_name",
                            "naviera",
                            "lugar_carga",
                            "puerto_salida",
                            "puerto_destino",
                            "localidad_destino",
                            "importe",
                            "fecha_caducidad",
                            "notas"
                        ],
                        "limit": 100,
                        "sorts": [{"propertyName": "importe", "direction": "ASCENDING"}]
                    }

                    logger.info(f"Searching DAP TERRESTRE direct routes with filters: {filters_directo}")

                    search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

                    if not search_response.ok:
                        logger.error(f"Direct routes search failed: {search_response.status_code} - {search_response.text}")
                        raise Exception(f"Search failed: {search_response.text}")

                    search_data = search_response.json()
                    search_results_directo = search_data.get("results", [])

                    logger.info(f"Found {len(search_results_directo)} direct routes (before date filtering)")

                    today = datetime.now().date()
                    threshold_date = today - timedelta(days=60) if include_expired else today

                    for result in search_results_directo:
                        props = result.get("properties", {})

                        fecha_cad_str = props.get("fecha_caducidad", "")
                        if fecha_cad_str:
                            try:
                                fecha_cad = datetime.strptime(fecha_cad_str, "%Y-%m-%d").date()
                                if fecha_cad < threshold_date:
                                    logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad})")
                                    continue
                            except ValueError:
                                try:
                                    fecha_cad_timestamp = int(fecha_cad_str)
                                    fecha_cad = datetime.fromtimestamp(fecha_cad_timestamp / 1000).date()
                                    if fecha_cad < threshold_date:
                                        logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad})")
                                        continue
                                except (ValueError, TypeError):
                                    logger.warning(f"Could not parse fecha_caducidad: {fecha_cad_str}")

                        transitario_cardcode = props.get("transitario", "")
                        transitario_name = props.get("transitario_name")

                        ruta_item = RutaItem(
                            id=str(result.get("id", "")),
                            referencia=props.get("referencia", ""),
                            tipo_contenedor=props.get("tipo_contenedor", ""),
                            transitario=transitario_name,
                            transitario_id=None,
                            transitario_cardcode=transitario_cardcode,
                            naviera=props.get("naviera"),
                            lugar_carga=props.get("lugar_carga"),
                            puerto_salida=props.get("puerto_salida"),
                            puerto_destino=props.get("puerto_destino"),
                            localidad_destino=props.get("localidad_destino"),
                            importe=safe_convert(props.get("importe"), float) or 0.0,
                            fecha_caducidad=props.get("fecha_caducidad", ""),
                            notas=props.get("notas"),
                            tipo_tramo="terrestre"
                        )
                        rutas_terrestres.append(ruta_item)

                    logger.info(f"Found {len(rutas_terrestres)} valid DAP TERRESTRE direct routes after date filtering")

                except Exception as e:
                    logger.error(f"Error searching DAP TERRESTRE direct routes: {e}", exc_info=True)

        # Using direct REST API for better compatibility with custom objects
        elif deal_tipo_contenedor and deal_lugar_carga and effective_puerto_salida:
            try:
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"

                # Only filter by: tipo_contenedor, lugar_carga, puerto_salida
                # Do NOT filter by puerto_destino (terrestrial routes don't have destination port)
                filters_terrestre = [
                    {"propertyName": "tipo_contenedor", "operator": "EQ", "value": deal_tipo_contenedor},
                    {"propertyName": "lugar_carga", "operator": "EQ", "value": deal_lugar_carga},
                    {"propertyName": "puerto_salida", "operator": "EQ", "value": effective_puerto_salida},
                ]

                search_payload = {
                    "filterGroups": [{"filters": filters_terrestre}],
                    "properties": [
                        "referencia",
                        "tipo_contenedor",
                        "transitario",
                        "transitario_name",
                        "naviera",
                        "lugar_carga",
                        "puerto_salida",
                        "puerto_destino",
                        "localidad_destino",
                        "importe",
                        "fecha_caducidad",
                        "notas"
                    ],
                    "limit": 100,
                    "sorts": [{"propertyName": "importe", "direction": "ASCENDING"}]
                }

                logger.info(f"Searching terrestrial routes with filters: {filters_terrestre}")

                search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

                if not search_response.ok:
                    logger.error(f"Terrestrial routes search failed: {search_response.status_code} - {search_response.text}")
                    raise Exception(f"Search failed: {search_response.text}")

                search_data = search_response.json()
                search_results_terrestre = search_data.get("results", [])

                logger.info(f"Found {len(search_results_terrestre)} terrestrial routes (before date filtering)")

                # Filter by date in code (HubSpot date filtering on custom objects is unreliable)
                today = datetime.now().date()
                # Calculate the threshold for expired routes (60 days ago)
                threshold_date = today - timedelta(days=60) if include_expired else today

                for result in search_results_terrestre:
                    props = result.get("properties", {})

                    # Check if route is still valid or recently expired (if include_expired=True)
                    fecha_cad_str = props.get("fecha_caducidad", "")
                    if fecha_cad_str:
                        try:
                            # Try parsing as YYYY-MM-DD format
                            fecha_cad = datetime.strptime(fecha_cad_str, "%Y-%m-%d").date()
                            if fecha_cad < threshold_date:
                                logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad}, threshold: {threshold_date})")
                                continue
                        except ValueError:
                            # If parsing fails, try as timestamp
                            try:
                                fecha_cad_timestamp = int(fecha_cad_str)
                                fecha_cad = datetime.fromtimestamp(fecha_cad_timestamp / 1000).date()
                                if fecha_cad < threshold_date:
                                    logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad}, threshold: {threshold_date})")
                                    continue
                            except (ValueError, TypeError):
                                logger.warning(f"Could not parse fecha_caducidad: {fecha_cad_str}")

                    # Get transitario data from ruta properties (no need to fetch from proveedores)
                    transitario_cardcode = props.get("transitario", "")
                    transitario_name = props.get("transitario_name")
                    transitario_id = None

                    ruta_item = RutaItem(
                        id=str(result.get("id", "")),
                        referencia=props.get("referencia", ""),
                        tipo_contenedor=props.get("tipo_contenedor", ""),
                        transitario=transitario_name,
                        transitario_id=transitario_id,
                        transitario_cardcode=transitario_cardcode,
                        naviera=props.get("naviera"),
                        lugar_carga=props.get("lugar_carga"),
                        puerto_salida=props.get("puerto_salida"),
                        puerto_destino=props.get("puerto_destino"),
                        localidad_destino=props.get("localidad_destino"),
                        importe=safe_convert(props.get("importe"), float) or 0.0,
                        fecha_caducidad=props.get("fecha_caducidad", ""),
                        notas=props.get("notas"),
                        tipo_tramo="terrestre"
                    )
                    rutas_terrestres.append(ruta_item)

                logger.info(f"Found {len(rutas_terrestres)} valid terrestrial routes after date filtering")

            except Exception as e:
                logger.error(f"Error searching terrestrial routes: {e}", exc_info=True)

        # Skip when localidad_destino is provided (DAP TERRESTRE — direct route, no ports)
        # Using direct REST API for better compatibility with custom objects
        if not localidad_destino and deal_tipo_contenedor and effective_puerto_salida and effective_puerto_destino:
            try:
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"

                # Only filter by: tipo_contenedor, puerto_salida, puerto_destino
                # Do NOT filter by lugar_carga (maritime routes don't have loading location)
                # Optionally filter by naviera if selected
                filters_maritima = [
                    {"propertyName": "tipo_contenedor", "operator": "EQ", "value": deal_tipo_contenedor},
                    {"propertyName": "puerto_salida", "operator": "EQ", "value": effective_puerto_salida},
                    {"propertyName": "puerto_destino", "operator": "EQ", "value": effective_puerto_destino},
                ]

                # Add naviera filter if selected (only if not empty/NA)
                if deal_naviera and deal_naviera != "NA" and deal_naviera != "SIN ESPECIFICAR" and deal_naviera.strip():
                    filters_maritima.append(
                        {"propertyName": "naviera", "operator": "EQ", "value": deal_naviera}
                    )

                search_payload = {
                    "filterGroups": [{"filters": filters_maritima}],
                    "properties": [
                        "referencia",
                        "tipo_contenedor",
                        "transitario",
                        "transitario_name",
                        "naviera",
                        "lugar_carga",
                        "puerto_salida",
                        "puerto_destino",
                        "localidad_destino",
                        "importe",
                        "fecha_caducidad",
                        "notas"
                    ],
                    "limit": 100,
                    "sorts": [{"propertyName": "importe", "direction": "ASCENDING"}]
                }

                logger.info(f"Searching maritime routes with filters: {filters_maritima}")

                search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)

                if not search_response.ok:
                    logger.error(f"Maritime routes search failed: {search_response.status_code} - {search_response.text}")
                    raise Exception(f"Search failed: {search_response.text}")

                search_data = search_response.json()
                search_results_maritima = search_data.get("results", [])

                logger.info(f"Found {len(search_results_maritima)} maritime routes (before date filtering)")

                today = datetime.now().date()
                # Calculate the threshold for expired routes (60 days ago)
                threshold_date = today - timedelta(days=60) if include_expired else today

                for result in search_results_maritima:
                    props = result.get("properties", {})

                    # Check if route is still valid or recently expired (if include_expired=True)
                    fecha_cad_str = props.get("fecha_caducidad", "")
                    if fecha_cad_str:
                        try:
                            # Try parsing as YYYY-MM-DD format
                            fecha_cad = datetime.strptime(fecha_cad_str, "%Y-%m-%d").date()
                            if fecha_cad < threshold_date:
                                logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad}, threshold: {threshold_date})")
                                continue
                        except ValueError:
                            # If parsing fails, try as timestamp
                            try:
                                fecha_cad_timestamp = int(fecha_cad_str)
                                fecha_cad = datetime.fromtimestamp(fecha_cad_timestamp / 1000).date()
                                if fecha_cad < threshold_date:
                                    logger.info(f"Skipping expired route: {props.get('referencia')} (expired: {fecha_cad}, threshold: {threshold_date})")
                                    continue
                            except (ValueError, TypeError):
                                logger.warning(f"Could not parse fecha_caducidad: {fecha_cad_str}")

                    # Get transitario data from ruta properties (no need to fetch from proveedores)
                    transitario_cardcode = props.get("transitario", "")
                    transitario_name = props.get("transitario_name")
                    transitario_id = None

                    ruta_item = RutaItem(
                        id=str(result.get("id", "")),
                        referencia=props.get("referencia", ""),
                        tipo_contenedor=props.get("tipo_contenedor", ""),
                        transitario=transitario_name,
                        transitario_id=transitario_id,
                        transitario_cardcode=transitario_cardcode,
                        naviera=props.get("naviera"),
                        lugar_carga=props.get("lugar_carga"),
                        puerto_salida=props.get("puerto_salida"),
                        puerto_destino=props.get("puerto_destino"),
                        localidad_destino=props.get("localidad_destino"),
                        importe=safe_convert(props.get("importe"), float) or 0.0,
                        fecha_caducidad=props.get("fecha_caducidad", ""),
                        notas=props.get("notas"),
                        tipo_tramo="maritima"
                    )
                    rutas_maritimas.append(ruta_item)

                logger.info(f"Found {len(rutas_maritimas)} valid maritime routes after date filtering")

            except Exception as e:
                logger.error(f"Error searching maritime routes: {e}", exc_info=True)

        _hidratar_transitario_rutas(client, rutas_terrestres + rutas_maritimas)

        return DealRutasResponse(
            success=True,
            error=None,
            rutas_terrestres=rutas_terrestres,
            rutas_maritimas=rutas_maritimas,
            total_rutas_terrestres=len(rutas_terrestres),
            total_rutas_maritimas=len(rutas_maritimas)
        )

    except Exception as e:
        logger.error(f"Error in get_deal_rutas: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{deal_id}/negociacion", response_model=BaseResponse)
async def update_deal_negociacion(
    data: UpdateNegociacionRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Actualiza margen y tipos de IVA negociados sobre los line items del deal (batch). `margen_unitario` se interpreta como fracción 0-1."""
    try:
        client = hubspot_client.get_client()

        logger.warning(f"[negociacion] Updating negotiation data for {len(data.articulos)} unique line items in deal {deal_id}")

        # Handle both Pydantic model and dict input (for AWS Lambda compatibility)
        if isinstance(data, dict):
            data = UpdateNegociacionRequest(**data)

        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        residennum = ""
        equ = ""
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company = client.crm.companies.basic_api.get_by_id(
                    company_ids[0], properties=["residennum", "equ"],
                )
                residennum = company.properties.get("residennum") or ""
                equ = company.properties.get("equ") or ""
        except Exception as e:
            logger.warning(f"[negociacion] no pude leer residennum/equ: {e}")

        line_item_ids = [articulo.line_item_id for articulo in data.articulos]
        logger.warning(f"[negociacion] Fetching {len(line_item_ids)} line items to get tipo_articulo")

        # BATCH READ line items
        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_size = 100
        line_items_data = {}  # line_item_id -> {tipo_articulo}

        for i in range(0, len(line_item_ids), batch_size):
            batch_ids = line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": ["tipo_articulo", "quantity", "name", "numero_container"],
                "inputs": [{"id": str(lid)} for lid in batch_ids]
            }

            try:
                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                if batch_response.ok:
                    for item in batch_response.json().get("results", []):
                        item_id = item.get("id")
                        props = item.get("properties", {})
                        line_items_data[item_id] = {
                            "tipo_articulo": (props.get("tipo_articulo") or "").strip().lower(),
                            "quantity": props.get("quantity"),
                            "name": props.get("name") or "",
                            "numero_container": props.get("numero_container") or "",
                        }
                else:
                    logger.error(f"[negociacion] Batch read failed: {batch_response.status_code}")
            except Exception as e:
                logger.error(f"[negociacion] Error in batch read line items: {e}")

        logger.warning(f"[negociacion] Fetched data for {len(line_items_data)} line items")

        # This avoids rate limiting by updating all line items in a single request
        batch_updates = []

        for articulo in data.articulos:
            line_item_id = articulo.line_item_id
            price = articulo.price
            margen_unitario = round_margin(articulo.margen_unitario)
            tipo_iva_repercutido = articulo.tipo_iva_repercutido
            tipo_iva_soportado = articulo.tipo_iva_soportado

            item_data = line_items_data.get(line_item_id, {})
            tipo_articulo = item_data.get("tipo_articulo", "")

            logger.warning(f"[negociacion] Preparing update for line item {line_item_id} with price={price}, margin={margen_unitario}, VAT rep={tipo_iva_repercutido}, VAT sop={tipo_iva_soportado}")

            properties_to_update = {
                "margen_unitario": str(margen_unitario) if margen_unitario is not None else "",
                "tipo_iva_repercutido": tipo_iva_repercutido
            }

            if tipo_iva_soportado is not None:
                properties_to_update["tipo_iva_soportado"] = tipo_iva_soportado

            # Add price only for fabricante articles (received from frontend)
            if tipo_articulo == "fabricante":
                price_rounded = round_price(price)
                properties_to_update["price"] = str(price_rounded) if price_rounded is not None else "0"
                logger.warning(f"[negociacion] Line item {line_item_id}: Setting price={price_rounded} (negotiated price from frontend)")

                # El ancla del tipo de venta viaja SIEMPRE con el price: o se fija
                # la negociada o se limpia (promo / payload sin ancla), para que un
                # ancla obsoleta no mande sobre un precio recién negociado.
                ancla = round_price(articulo.precio_negociado_tipo_venta)
                ancla_ic = _normalize_incoterm(articulo.incoterm_precio_negociado) if articulo.incoterm_precio_negociado else ""
                if ancla and ancla > 0 and ancla_ic:
                    properties_to_update["precio_negociado_tipo_venta"] = str(ancla)
                    properties_to_update["incoterm_precio_negociado"] = ancla_ic
                else:
                    if ancla and ancla > 0 and not ancla_ic:
                        logger.warning(
                            f"[negociacion] Line item {line_item_id}: ancla {ancla} "
                            f"sin incoterm_precio_negociado; se descarta"
                        )
                    properties_to_update["precio_negociado_tipo_venta"] = ""
                    properties_to_update["incoterm_precio_negociado"] = ""

            batch_updates.append({
                "id": line_item_id,
                "properties": properties_to_update
            })

        # Un id repetido en dos chunks distintos se pisaría a sí mismo.
        merged_updates: Dict[str, Dict[str, Any]] = {}
        for upd in batch_updates:
            lid = str(upd["id"])
            if lid in merged_updates:
                logger.warning(f"[negociacion] line item {lid} repetido en el payload; fusiono props")
                merged_updates[lid]["properties"].update(upd.get("properties") or {})
            else:
                merged_updates[lid] = {"id": lid, "properties": dict(upd.get("properties") or {})}
        batch_updates = list(merged_updates.values())

        updated_count = 0
        if batch_updates:
            logger.warning(f"[negociacion] Executing batch update for {len(batch_updates)} line items")

            for upd in batch_updates:
                enforce_line_item_price_rounding(upd.get("properties", {}))

            for i in range(0, len(batch_updates), batch_size):
                chunk = batch_updates[i:i + batch_size]
                try:
                    batch_result = client.crm.line_items.batch_api.update(
                        batch_input_simple_public_object_batch_input={"inputs": chunk}
                    )
                    updated_count += len(batch_result.results)
                except Exception as e:
                    error_msg = f"Error executing batch update [{i}:{i + len(chunk)}]: {e}"
                    logger.error(f"[negociacion] {error_msg}")
                    raise HTTPException(status_code=500, detail=error_msg)

            logger.warning(f"[negociacion] Successfully updated {updated_count} line items")
        else:
            logger.warning(f"[negociacion] No line items to update")

        try:
            elementos = get_elementos_by_deal(
                client, deal_id,
                properties=[
                    "nombre", "nombre_contenedor", "tipo_articulo", "cantidad", "precio_unitario",
                    "precio_de_coste", "precio_total", "margen_unitario",
                    "tipo_iva_repercutido", "tipo_iva_soportado", "line_item_id",
                ],
            )
        except Exception as e:
            logger.warning(f"[negociacion] could not load ELEMENTOS for deal {deal_id}: {e}")
            elementos = []

        # El line_item_id guardado en el Elemento puede venir cruzado de una creación
        # previa (rotación por orden de batch no garantizado). Por eso el match por lid
        # se verifica contra el contenido real del line item (numero_container + name);
        # si no coincide, se reempareja por (nombre_contenedor, nombre) cuando ese par es
        # único en el contenedor y el line_item_id se reescribe para autocorregir el dato.
        elementos_by_lid = {}
        elementos_by_key = {}
        key_counts = {}
        for el in elementos:
            props = el.get("properties", {}) or {}
            lid = props.get("line_item_id")
            nc = (props.get("nombre_contenedor") or "").strip()
            nombre = (props.get("nombre") or "").strip()
            entry = {"id": el.get("id"), "properties": props}
            if lid:
                elementos_by_lid[str(lid).strip()] = entry
            if nombre:
                key_counts[(nc, nombre)] = key_counts.get((nc, nombre), 0) + 1
                elementos_by_key[(nc, nombre)] = entry

        cont_map_neg = _resolve_contenedor_co_by_numero(client, deal_id)
        affected_contenedor_co_ids = set()
        elementos_synced = 0
        elemento_updates: Dict[str, Dict[str, Any]] = {}
        for articulo in data.articulos:
            target_lid = str(articulo.line_item_id).strip()
            cur_li = line_items_data.get(articulo.line_item_id, {})
            li_name = (cur_li.get("name") or "").strip()
            numero_cont = (cur_li.get("numero_container") or "").strip()
            cantidad = _safe_float(cur_li.get("quantity")) or 0

            elem = elementos_by_lid.get(target_lid)
            # lid cruzado: el Elemento resuelto por lid no es el de este line item.
            if elem and li_name and (elem["properties"].get("nombre") or "").strip() != li_name:
                elem = None
            # Reemparejar por contenido solo si (contenedor, nombre) es único: con
            # líneas duplicadas (promo 5+1) el nombre es ambiguo y el lid es el único
            # discriminador, así que no se fuerza el fallback.
            if not elem and li_name and key_counts.get((numero_cont, li_name), 0) == 1:
                elem = elementos_by_key.get((numero_cont, li_name))
            if not elem:
                continue

            precio_unitario_round = round_price(articulo.price)
            margen_round = round_margin(articulo.margen_unitario)

            # precio_total no se sincroniza aquí: lo fija recalcular_contenedores a
            # nivel de contenedor (cuadre con el precio negociado). El contenedor se
            # marca afectado siempre para que ese recálculo corra en cada guardado.
            target_props = {
                "precio_unitario": precio_unitario_round,
                "margen_unitario": margen_round,
                "tipo_iva_repercutido": articulo.tipo_iva_repercutido,
            }
            if articulo.tipo_iva_soportado is not None:
                target_props["tipo_iva_soportado"] = articulo.tipo_iva_soportado

            co = cont_map_neg.get(numero_cont) if numero_cont else None
            if co:
                affected_contenedor_co_ids.add(co.get("id"))

            cur_elem_props = elem["properties"]
            update_props = {}
            for field, target_val in target_props.items():
                if target_val is None:
                    continue
                if _normalize_for_diff(cur_elem_props.get(field)) != _normalize_for_diff(target_val):
                    update_props[field] = target_val

            if str(cur_elem_props.get("line_item_id") or "") != str(articulo.line_item_id):
                update_props["line_item_id"] = str(articulo.line_item_id)

            if not update_props:
                continue
            eid = str(elem["id"])
            props_str = {k: str(v) for k, v in update_props.items() if v is not None}
            if eid in elemento_updates:
                elemento_updates[eid]["properties"].update(props_str)
            else:
                elemento_updates[eid] = {"id": eid, "properties": props_str}

        if elemento_updates:
            try:
                elementos_synced = batch_update_custom_objects(
                    client, OBJECT_TYPE_ELEMENTO, list(elemento_updates.values())
                )
            except Exception as e:
                logger.warning(f"[negociacion] batch update ELEMENTOS falló: {e}")

        if elementos:
            logger.info(
                f"[negociacion] ELEMENTOS synced: {elementos_synced}/{len(data.articulos)}"
            )

        if affected_contenedor_co_ids:
            try:
                await hubdb_client.preload_iva_table()
                recalcular_contenedores(
                    client,
                    deal_id,
                    list(affected_contenedor_co_ids),
                    {"equ": equ, "residennum": residennum},
                )
            except Exception as e:
                logger.warning(f"[negociacion] recalcular_contenedores falló: {e}")
            touch_pagos_for_contenedores(client, list(affected_contenedor_co_ids))

        return BaseResponse(
            success=True,
            error=None
        )

    except Exception as e:
        logger.error(f"[negociacion] Error updating negotiation data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# HUBDB TEXTOS COTIZACION ENDPOINT

def _fetch_hubdb_textos_cotizaciones(
    idioma: str,
    claves: List[str],
    grupo: Optional[str] = None,
    subgrupo: Optional[str] = None,
    access_token: Optional[str] = None
) -> dict[str, TextoCotizacionItem]:
    """
    Lee plantillas de texto de la tabla HubDB `textos_cotizaciones` filtradas por idioma y claves (opcionalmente grupo/subgrupo). Devuelve `{clave: TextoCotizacionItem}`.
    """

    # HubDB table name (logical name)
    table_name = "textos_cotizaciones"

    # First, we need to get the table ID from the table name
    # HubDB API endpoint to list tables: GET /cms/v3/hubdb/tables
    list_tables_url = "https://api.hubapi.com/cms/v3/hubdb/tables"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        logger.info(f"[hubdb] Fetching table ID for table: {table_name}")
        tables_response = requests.get(list_tables_url, headers=headers, timeout=10)
        tables_response.raise_for_status()
        tables_data = tables_response.json()

        # Find the table with matching name
        table_id = None
        for table in tables_data.get("results", []):
            if table.get("name") == table_name:
                table_id = table.get("id")
                logger.info(f"[hubdb] Found table ID: {table_id} for table: {table_name}")
                break

        if not table_id:
            logger.error(f"[hubdb] Table '{table_name}' not found in HubDB")
            raise HTTPException(
                status_code=404,
                detail=f"HubDB table '{table_name}' not found"
            )

    except requests.exceptions.Timeout:
        logger.error("[hubdb] Timeout fetching table list from HubDB")
        raise HTTPException(status_code=504, detail="Timeout connecting to HubDB API")
    except requests.exceptions.RequestException as e:
        logger.error(f"[hubdb] Error fetching table list: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Error connecting to HubDB API: {str(e)}"
        )

    # HubDB API endpoint to get published rows: GET /cms/v3/hubdb/tables/{tableId}/rows
    rows_url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{table_id}/rows"

    # Note: HubDB API doesn't support complex filtering in query params,
    # so we'll fetch all rows and filter in Python
    try:
        logger.info(f"[hubdb] Fetching rows from table {table_id}")
        rows_response = requests.get(rows_url, headers=headers, timeout=15)
        rows_response.raise_for_status()
        rows_data = rows_response.json()

        all_rows = rows_data.get("results", [])
        logger.info(f"[hubdb] Fetched {len(all_rows)} total rows from table")

    except requests.exceptions.Timeout:
        logger.error("[hubdb] Timeout fetching rows from HubDB")
        raise HTTPException(status_code=504, detail="Timeout fetching data from HubDB")
    except requests.exceptions.RequestException as e:
        logger.error(f"[hubdb] Error fetching rows: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Error fetching data from HubDB: {str(e)}"
        )

    result = {}

    for row in all_rows:
        values = row.get("values", {})

        row_idioma = values.get("idioma", {}).get("name", "")
        row_clave = values.get("clave", "")
        row_grupo = values.get("grupo", "")
        row_subgrupo = values.get("subgrupo", "")
        row_texto = values.get("texto", "")

        # Apply filters:
        if row_idioma != idioma:
            continue

        if row_clave not in claves:
            continue

        if grupo is not None and row_grupo != grupo:
            continue

        if subgrupo is not None and row_subgrupo != subgrupo:
            continue

        # Row matches all criteria - add to result
        logger.info(f"[hubdb] Found matching row: clave={row_clave}, idioma={row_idioma}")

        result[row_clave] = TextoCotizacionItem(
            grupo=row_grupo,
            subgrupo=row_subgrupo,
            idioma=row_idioma,
            clave=row_clave,
            texto=row_texto
        )

    # Log summary
    found_claves = list(result.keys())
    missing_claves = [c for c in claves if c not in found_claves]

    logger.info(f"[hubdb] Found {len(found_claves)} matching texts: {found_claves}")
    if missing_claves:
        logger.warning(f"[hubdb] Missing texts for claves: {missing_claves}")

    return result

@router.post("/documentacion/textos-cotizacion", response_model=TextosCotizacionResponse)
async def get_textos_cotizacion(data: TextosCotizacionRequest):
    """Devuelve plantillas de texto de la tabla HubDB `textos_cotizaciones` filtradas por idioma + claves (con grupo/subgrupo opcionales). Idiomas válidos: es, en, fr."""
    try:
        # Validate idioma
        valid_idiomas = ["es", "en", "fr"]
        if data.idioma not in valid_idiomas:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid idioma. Must be one of: {', '.join(valid_idiomas)}"
            )

        # Validate claves not empty
        if not data.claves or len(data.claves) == 0:
            raise HTTPException(
                status_code=400,
                detail="claves list cannot be empty"
            )

        logger.info(
            f"[textos-cotizacion] Fetching texts for idioma={data.idioma}, "
            f"claves={data.claves}, grupo={data.grupo}, subgrupo={data.subgrupo}"
        )
        client = hubspot_client.get_client()
        access_token = client.access_token

        textos_dict = _fetch_hubdb_textos_cotizaciones(
            idioma=data.idioma,
            claves=data.claves,
            grupo=data.grupo,
            subgrupo=data.subgrupo,
            access_token=access_token
        )

        logger.info(f"[textos-cotizacion] Successfully fetched {len(textos_dict)} texts")

        return TextosCotizacionResponse(
            success=True,
            error=None,
            data=textos_dict
        )

    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as e:
        logger.error(f"[textos-cotizacion] Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def _fetch_hubdb_tipo_iva(filtros: List[TipoIVAFilter]) -> List[TipoIVAItem]:
    """Devuelve las filas del cache que matchean cualquiera de los filtros
    (categoria + tipo_residencia). Requiere que el cache esté precargado."""
    seen = set()
    result: List[TipoIVAItem] = []
    for f in filtros:
        entries = hubdb_client.list_entries(f.categoria, f.tipo_residencia)
        for e in entries:
            dedup_key = (e.code, e.categoria, e.tipo_residencia)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(TipoIVAItem(
                code=e.code,
                name=e.name,
                porcentaje_iva=e.porcentaje_iva,
                porcentaje_recargo=e.porcentaje_recargo,
                categoria=e.categoria,
                tipo_residencia=e.tipo_residencia,
            ))
    logger.info(f"[hubdb] Found {len(result)} IVA rows matching filters")
    return result

@router.post("/tipo-iva", response_model=TipoIVAResponse)
async def get_tipo_iva(data: TipoIVARequest):
    """Devuelve filas de `tipo_de_iva_soportado` matcheando cualquiera de los
    filtros (categoria + tipo_residencia). Consume el cache en memoria
    (`hubdb_client`); si está vacío, lo precarga on-demand."""
    try:
        if not data.filtros:
            raise HTTPException(400, "Debe incluir al menos un filtro")

        await hubdb_client.preload_iva_table()
        iva_rows = _fetch_hubdb_tipo_iva(filtros=data.filtros)

        return TipoIVAResponse(
            success=True,
            error=None,
            data=iva_rows
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[tipo-iva] Unexpected error: {e}", exc_info=True)
        raise HTTPException(500, str(e))

# ID fijo de la plantilla de cotización personalizada
QUOTE_TEMPLATE_ID = "530249630794"

# Tope de una propiedad de texto de HubSpot. Pasarse devuelve un 400 y deja el valor
# anterior intacto, que en `quote_data_payload` significa un PDF con datos de otra
# cotización.
HUBSPOT_PROPERTY_MAX_CHARS = 65536

@router.post("/{deal_id}/publish", response_model=PublishQuoteResponse)
async def publish_quote(
    deal_id: str = Path(..., description="HubSpot Deal ID"),
    data: PublishQuoteRequest = None
):
    """Publica la cotización en modo DRAFT de forma síncrona. Con batch tarda ~5s — cabe en el timeout de HubSpot UI Extensions (15s)."""
    if not data:
        raise HTTPException(400, "Payload de cotización requerido")
    if not data.lineItems:
        raise HTTPException(400, "No hay line items en la cotización")

    try:
        client = hubspot_client.get_client()
        access_token = client.access_token

        try:
            client.crm.deals.basic_api.get_by_id(deal_id=deal_id, properties=["dealname"])
        except Exception as e:
            logger.error(f"[publish] Deal {deal_id} not found: {e}")
            raise HTTPException(404, f"Deal {deal_id} no encontrado")

        logger.info(f"[publish] Starting quote publication for deal {deal_id}")

        deal_owner_id = None
        deal_puerto_destino = None
        owner_info = {}

        try:
            deal_response = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["hubspot_owner_id", "puerto_destino"]
            )
            deal_owner_id = deal_response.properties.get("hubspot_owner_id")
            deal_puerto_destino = deal_response.properties.get("puerto_destino")
            logger.info(f"[publish] Deal owner ID: {deal_owner_id}")

            if deal_owner_id:
                try:
                    owner_response = client.crm.owners.owners_api.get_by_id(
                        owner_id=deal_owner_id
                    )
                    owner_info = {
                        "id": owner_response.id,
                        "email": owner_response.email,
                        "first_name": owner_response.first_name,
                        "last_name": owner_response.last_name,
                    }
                    logger.info(f"[publish] Owner info: {owner_info}")
                except Exception as e:
                    logger.warning(f"[publish] Could not fetch owner details: {e}")
        except Exception as e:
            logger.warning(f"[publish] Could not fetch deal owner: {e}")

        existing_quote_count = 0
        try:
            existing_quote_ids = _get_associated_ids(deal_id, "quotes")
            existing_quote_count = len(existing_quote_ids)
            logger.info(f"[publish] Existing quotes for deal {deal_id}: {existing_quote_count}")
        except Exception as e:
            logger.warning(f"[publish] Could not count existing quotes: {e}")

        company_aseguradora = False
        company_tipo_cliente = None
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_resp = client.crm.companies.basic_api.get_by_id(
                    company_id=company_ids[0],
                    properties=["aseguradora", "tipo_cliente"]
                )
                raw_aseguradora = company_resp.properties.get("aseguradora")
                company_aseguradora = bool(raw_aseguradora and str(raw_aseguradora).strip())
                company_tipo_cliente = company_resp.properties.get("tipo_cliente")
                logger.info(
                    f"[publish] Company aseguradora={raw_aseguradora!r} "
                    f"tipo_cliente={company_tipo_cliente!r}"
                )
        except Exception as e:
            logger.warning(f"[publish] Could not fetch company aseguradora/tipo_cliente: {e}")

        # `lineItems` es el 82 % del JSON (unos 444 caracteres por línea) y la plantilla
        # de la cotización no lo usa: pinta la tabla desde los line items reales de la
        # Quote. Con 600 líneas el payload se iba a 266.000 caracteres, HubSpot rechazaba
        # la escritura por pasarse de 65.536 y el negocio se quedaba con el payload de la
        # cotización ANTERIOR: el PDF salía con las estadísticas de otro pedido
        # (08/09/2026, 50 contenedores imprimiendo el total de 10).
        _payload_json = json.dumps(
            {k: v for k, v in data.dict().items() if k != "lineItems"},
            ensure_ascii=False,
            default=str,
        )
        if len(_payload_json) > HUBSPOT_PROPERTY_MAX_CHARS:
            raise HTTPException(
                502,
                f"El payload de la cotización ocupa {len(_payload_json)} caracteres y "
                f"HubSpot admite {HUBSPOT_PROPERTY_MAX_CHARS}. No se publica: el PDF "
                f"saldría con los datos de la cotización anterior.",
            )
        deal_update_properties = {"quote_data_payload": _payload_json}

        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": deal_update_properties}
            )
            logger.info(f"[publish] Updated deal {deal_id} with quote payload")
        except HTTPException:
            raise
        except Exception as e:
            # Sin payload nuevo la plantilla pinta el del publish anterior, así que este
            # fallo no puede quedarse en un warning: se ve en el PDF, no en los logs.
            raise HTTPException(
                502, f"No se pudo guardar el payload de la cotización en el negocio: {e}"
            ) from e

        LANGUAGE_TOKENS = {
            "es": "es", "ES": "es", "ESPAÑOL": "es", "ESPANOL": "es", "SPANISH": "es",
            "en": "en", "EN": "en", "INGLÉS": "en", "INGLES": "en", "ENGLISH": "en",
            "fr": "fr", "FR": "fr", "FRANCÉS": "fr", "FRANCES": "fr", "FRENCH": "fr",
        }
        LOCALE_BY_LANGUAGE = {"es": "es_ES", "en": "en_US", "fr": "fr_FR"}

        raw_lang = (data.cliente.idioma or "es").strip()
        hs_language = LANGUAGE_TOKENS.get(raw_lang) or LANGUAGE_TOKENS.get(raw_lang.upper(), "es")
        hs_locale = LOCALE_BY_LANGUAGE[hs_language]

        hs_expiration_date = (
            data.cliente.fechaCaducidadCotizacion
            if getattr(data, "cliente", None) and getattr(data.cliente, "fechaCaducidadCotizacion", None)
            else None
        )

        quote_number = existing_quote_count + 1
        version_suffix = "" if quote_number == 1 else f"-v{quote_number}"
        tipo_cliente_titulo = (
            company_tipo_cliente
            or getattr(data.cliente, "tipoCliente", "")
            or ""
        ).strip().lower()
        fuera_territorio_aduanero = es_destino_fuera_territorio_aduanero_ue(deal_puerto_destino)
        nac_exp = (
            "NAC"
            if tipo_cliente_titulo == "nacional" and not fuera_territorio_aduanero
            else "EXP"
        )
        if fuera_territorio_aduanero:
            logger.info(
                f"[publish] puerto_destino={deal_puerto_destino!r} fuera del territorio "
                f"aduanero UE — cotización en formato exportación"
            )
        asegurada_segment = "ASG_" if company_aseguradora else ""
        lang = hs_language.upper()
        date_suffix = datetime.today().strftime("%d/%m/%y")
        hs_title = f"{deal_id}{version_suffix} {nac_exp}_{asegurada_segment}{lang} {date_suffix}"

        quote_properties = {
            "hs_title": hs_title,
            "hs_expiration_date": normalize_hs_date(hs_expiration_date),
            "hs_status": "DRAFT",
            "hs_language": hs_language,
            "hs_locale": hs_locale,
        }

        if deal_owner_id:
            quote_properties["hs_quote_owner_id"] = deal_owner_id

        if owner_info:
            if owner_info.get("first_name"):
                quote_properties["hs_sender_firstname"] = owner_info["first_name"]
            if owner_info.get("last_name"):
                quote_properties["hs_sender_lastname"] = owner_info["last_name"]
            if owner_info.get("email"):
                quote_properties["hs_sender_email"] = owner_info["email"]

        # La empresa remitente es siempre PROEX: no se toma de data.empresa
        # porque el front la mandaba hardcodeada y acababa en el asistente.
        quote_properties.update(SENDER_COMPANY_PROPERTIES)

        quote_properties = {k: v for k, v in quote_properties.items() if v is not None}

        associations = [
            build_association(deal_id, AssociationType.QUOTE_TO_DEAL),
            build_association(QUOTE_TEMPLATE_ID, AssociationType.QUOTE_TO_TEMPLATE),
        ]

        logger.info(
            f"[publish] Creating DRAFT quote for deal {deal_id} with template {QUOTE_TEMPLATE_ID}"
        )

        quote_id, error = create_quote_via_api(
            access_token=access_token,
            properties=quote_properties,
            associations=associations,
        )

        if not quote_id:
            logger.error(f"[publish] Failed to create quote via API: {error}")
            raise HTTPException(500, f"Error gestionando cotización: {error}")

        logger.info(
            f"[publish] Created new quote {quote_id} "
            f"associated with deal {deal_id} and template {QUOTE_TEMPLATE_ID}"
        )

        deal_line_items_raw = []
        try:
            li_ids = _get_associated_ids(deal_id, "line_items")
            if li_ids:
                _batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
                _headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                _batch_payload = {
                    "properties": [
                        "name", "hs_sku", "hs_product_id", "hs_product_type",
                        "tipo_articulo", "quantity", "price", "amount",
                        "container_config", "numero_container", "ocupacion",
                        "peso_neto", "peso_bruto", "colis",
                        "name_en", "name_fr",
                        "cardcode", "almacen", "precio_de_coste",
                        "margen_unitario", "tipo_iva_repercutido", "tipo_iva_soportado",
                        "precio_negociado_tipo_venta", "incoterm_precio_negociado",
                    ],
                }
                for _i in range(0, len(li_ids), 100):
                    _batch_payload["inputs"] = [{"id": str(lid)} for lid in li_ids[_i:_i + 100]]
                    _resp = requests.post(_batch_url, headers=_headers, json=_batch_payload, timeout=15)
                    if _resp.ok:
                        deal_line_items_raw.extend(_resp.json().get("results", []))
                    else:
                        raise HTTPException(
                            502,
                            f"Lectura incompleta de las líneas del negocio: el lote "
                            f"[{_i}:{_i + 100}] devolvió {_resp.status_code}. No se publica.",
                        )
                logger.info(f"[publish] Fetched {len(deal_line_items_raw)} deal line items for quote copy")
        except HTTPException:
            raise
        except Exception as _e:
            raise HTTPException(502, f"No se pudieron leer las líneas del negocio: {_e}") from _e

        # Una cotización a la que le faltan líneas no se nota a simple vista: la tabla
        # de productos cuadra consigo misma y solo descuadra contra el TOTAL, que sale
        # del payload. El 07/09/2026 se publicó una con 100 de 120 líneas (el truncado
        # a 100 de la batch v3) y 25.843,20 € de menos. Mejor no publicar.
        if len(deal_line_items_raw) != len(li_ids):
            raise HTTPException(
                502,
                f"Copia incompleta: se leyeron {len(deal_line_items_raw)} de "
                f"{len(li_ids)} líneas del negocio. No se publica la cotización.",
            )
        _payload_lineas = len(data.lineItems or [])
        if _payload_lineas and len(li_ids) < _payload_lineas:
            raise HTTPException(
                409,
                f"Asociaciones incompletas: el negocio devuelve {len(li_ids)} líneas y "
                f"la card envió {_payload_lineas}. No se publica la cotización.",
            )

        # Backstop para deals con huérfanos de catálogo previos al fix de
        # update_deal_containers: si hay contenedores, esas líneas no van al quote.
        nombres_contenedor = set()
        for _li in deal_line_items_raw:
            _props = _li.get("properties", {}) or {}
            if _is_container_product_type(_props.get("hs_product_type") or ""):
                # numero_container Y name: en legacy numero_container es el entero
                # ("1") y el prefijo de las líneas per-contenedor sale del name.
                for _nc in ((_props.get("numero_container") or "").strip(), (_props.get("name") or "").strip()):
                    if _nc:
                        nombres_contenedor.add(_nc)
        if nombres_contenedor:
            _huerfanos = set(_catalogo_huerfano_ids(deal_line_items_raw, nombres_contenedor))
            if _huerfanos:
                logger.warning(f"[publish] Excluyendo {len(_huerfanos)} líneas de catálogo sin contenedor de la copia al quote")
                deal_line_items_raw = [li for li in deal_line_items_raw if str(li.get("id")) not in _huerfanos]

        # El ancla (precio_negociado_tipo_venta + incoterm_precio_negociado) tiene que
        # viajar al line item del quote: `quote.html` la usa para imprimir el precio
        # negociado exacto en vez de recomponer EXW + delta, que arrastra el residuo
        # del EXW guardado a 3 decimales. Sin copiarla, esa rama del macro es código
        # muerto y el PDF depende de redondear el recompuesto.
        _copy_fields = [
            "name", "hs_sku", "hs_product_id", "hs_product_type",
            "tipo_articulo", "quantity", "price",
            "container_config", "numero_container", "ocupacion",
            "peso_neto", "peso_bruto", "colis",
            "name_en", "name_fr",
            "cardcode", "almacen", "precio_de_coste",
            "margen_unitario", "tipo_iva_repercutido", "tipo_iva_soportado",
            "precio_negociado_tipo_venta", "incoterm_precio_negociado",
        ]

        items_payload: List[dict] = []
        for li_data in deal_line_items_raw:
            props_src = li_data.get("properties", {})
            properties = {"hs_currency": "EUR"}
            for field in _copy_fields:
                val = props_src.get(field)
                if val is not None and val != "":
                    if field == "price":
                        rounded = round_price(val)
                        if rounded is not None:
                            properties[field] = str(rounded)
                    else:
                        properties[field] = val
            items_payload.append(properties)

        logger.warning(f"[publish] Creating {len(items_payload)} line items for quote {quote_id} via batch")

        # HubSpot no es de fiar en el recuento: con 600 líneas devuelve 100 ids por cada
        # uno de los 6 lotes y confirma las 600 asociaciones, pero en la Quote acaban
        # apareciendo 200 y los otros 400 line items no existen (08/09/2026, tres publish
        # seguidos con el mismo resultado; comprobado que la Quote admite más de 200 y que
        # no hay recorte diferido). Así que la copia se verifica contra lo que HubSpot
        # tiene de verdad y se reintenta lo que falte, identificando cada línea por `name`,
        # que es único (lleva el contenedor y el artículo).
        errors_list: List[str] = []
        pendientes = list(items_payload)
        created_ids: List[str] = []

        for intento in range(1, 4):
            nuevos_ids, create_errors = batch_create_line_items_via_api(
                access_token=access_token,
                items=pendientes,
            )
            errors_list += create_errors
            if nuevos_ids:
                _, assoc_errors = batch_associate_line_items_to_quote_v4(
                    access_token=access_token,
                    quote_id=quote_id,
                    line_item_ids=nuevos_ids,
                )
                errors_list += assoc_errors
                created_ids += nuevos_ids

            nombres_en_quote = _quote_line_item_names(access_token, quote_id)
            if nombres_en_quote is None:
                raise HTTPException(
                    502,
                    "No se pudo comprobar qué líneas quedaron en la cotización; "
                    "no se reintenta la copia para no duplicarlas.",
                )
            pendientes = [
                props for props in items_payload
                if (props.get("name") or "") not in nombres_en_quote
            ]
            logger.warning(
                f"[publish] Copia intento {intento}: la cotización tiene "
                f"{len(nombres_en_quote)} de {len(items_payload)} líneas "
                f"({len(pendientes)} pendientes)"
            )
            if not pendientes:
                break

        associated_count = len(items_payload) - len(pendientes)

        if pendientes:
            # La Quote se crea antes que sus líneas, así que a estas alturas ya existe en
            # HubSpot. Se archiva para no dejar una cotización a medias que alguien pueda
            # mandar al cliente.
            try:
                client.crm.quotes.basic_api.archive(quote_id=quote_id)
                logger.warning(f"[publish] Quote {quote_id} archivada por copia incompleta")
            except Exception as _arch_err:
                logger.error(
                    f"[publish] Quote {quote_id} incompleta y NO se pudo archivar: {_arch_err}"
                )
            raise HTTPException(
                502,
                f"Copia incompleta a la cotización tras 3 intentos: la cotización tiene "
                f"{associated_count} de {len(items_payload)} líneas. "
                f"{'; '.join(errors_list[:3]) if errors_list else 'sin error de HubSpot'}",
            )

        return PublishQuoteResponse(
            success=True,
            error=None,
            data={
                "deal_id": deal_id,
                "quote_id": quote_id,
                "quote_status": "DRAFT",
                "total_line_items": len(deal_line_items_raw),
                "created_line_items": len(created_ids),
                "associated_line_items": associated_count,
                "line_items_errors": errors_list,
                "total_euros": getattr(data.estadisticas, "totalEuros", None),
                "num_containers": getattr(data.estadisticas, "numContainers", None),
                "total_colis": getattr(data.estadisticas, "totalColis", None),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[publish] Unexpected error: {e}", exc_info=True)
        raise HTTPException(500, f"Error al publicar la cotización: {str(e)}")

@router.post("/{deal_id}/publish-custom-objects", response_model=BaseResponse)
async def publish_custom_objects(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Recrea los Contenedor/Elemento custom objects del deal a partir de sus line items. Se ejecuta en background (~20-30 s) y la respuesta es inmediata."""
    try:
        client = hubspot_client.get_client()

        try:
            client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["dealname"]
            )
        except Exception as e:
            logger.error(f"Deal {deal_id} not found: {e}")
            raise HTTPException(status_code=404, detail=f"Deal {deal_id} no encontrado")

        job_id = f"{CUSTOM_OBJECTS_JOB_TYPE}_{deal_id}_{int(time.time() * 1000)}"
        payload = {"deal_id": deal_id}
        create_job(deal_id=deal_id, job_type=CUSTOM_OBJECTS_JOB_TYPE, job_id=job_id, payload=payload)

        try:
            enqueue_job(job_id=job_id, job_type=CUSTOM_OBJECTS_JOB_TYPE, payload=payload)
        except Exception as enq_err:
            err = f"enqueue_failed: {enq_err}"
            logger.error(f"[publish_custom_objects] {err}", exc_info=True)
            update_job(job_id, status="failed", error=err, completed_at=int(time.time()))
            raise HTTPException(status_code=503, detail="No se pudo encolar el job de publicación")

        logger.info(f"[publish_custom_objects] enqueued job_id={job_id} deal={deal_id}")
        return BaseResponse(success=True, error=None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting publish custom objects task for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{deal_id}/custom-objects/status")
async def get_custom_objects_status(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Estado del job de publicación de custom objects: status (pending/processing/completed/error/not_started), número de contenedores y elementos asociados al deal y mensaje legible."""
    try:
        from app.services.custom_objects import get_contenedores_by_deal, get_elementos_by_deal

        job = get_latest_job_by_deal(deal_id, CUSTOM_OBJECTS_JOB_TYPE)

        if not job:
            client = hubspot_client.get_client()
            try:
                client.crm.deals.basic_api.get_by_id(deal_id=deal_id, properties=["dealname"])
            except Exception as e:
                logger.error(f"Deal {deal_id} not found: {e}")
                raise HTTPException(status_code=404, detail=f"Deal {deal_id} no encontrado")

            logger.info(f"[get_custom_objects_status] no job for deal {deal_id}")
            return {
                "success": True,
                "error": None,
                "data": {
                    "status": "not_started",
                    "contenedores": 0,
                    "elementos": 0,
                    "message": "No se ha iniciado la creación de objetos personalizados",
                    "job_id": None,
                    "progress_step": None,
                    "started_at": None,
                    "completed_at": None,
                    "created_at": None,
                }
            }

        client = hubspot_client.get_client()
        contenedores = get_contenedores_by_deal(client, deal_id, properties=["nombre"])
        num_contenedores = len(contenedores) if contenedores else 0
        elementos = get_elementos_by_deal(client, deal_id, properties=["descripcion"])
        num_elementos = len(elementos) if elementos else 0

        db_status = job.get("status") or "pending"
        if db_status == "pending":
            status = "pending"
            message = "Preparando creación de objetos personalizados..."
        elif db_status == "processing":
            status = "processing"
            message = "Procesando objetos personalizados..."
        elif db_status == "completed":
            status = "completed"
            message = "Objetos personalizados creados exitosamente"
        elif db_status == "failed":
            status = "error"
            message = f"Error al crear objetos personalizados: {job.get('error') or 'Error desconocido'}"
        else:
            status = "pending"
            message = "Estado desconocido"

        logger.info(
            f"[get_custom_objects_status] deal={deal_id} db_status={db_status} "
            f"step={job.get('progress_step')} contenedores={num_contenedores} elementos={num_elementos}"
        )

        return {
            "success": True,
            "error": None,
            "data": {
                "status": status,
                "contenedores": num_contenedores,
                "elementos": num_elementos,
                "message": message,
                "job_id": job.get("job_id"),
                "progress_step": job.get("progress_step"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "created_at": job.get("created_at"),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting custom objects status for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
