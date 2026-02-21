"""Deal-related API routes.

This module defines all endpoints related to HubSpot deals.
"""

import json
import logging
import requests
import time
import threading
from typing import Any, Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Path, Query
from hubspot import HubSpot
from hubspot.crm.associations import BatchInputPublicObjectId
from hubspot.crm.associations.models import PublicObjectId
from hubspot.crm.objects import PublicObjectSearchRequest, Filter, FilterGroup, BatchInputSimplePublicObjectId, SimplePublicObjectId, BatchReadInputSimplePublicObjectId
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
    BaseResponse,
    UpdateArticlesRequest,
    UpdateServicesRequest,
    DealServicesResponse,
    ServiceItem,
    ProviderSummary,
    DealArticlesResponse,
    ArticleWithProvider,
    UpdateContainersRequest,
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
from app.services.quote_helpers import (
    build_association,
    create_quote_via_api,
    create_line_item_via_api,
    associate_line_item_to_quote_v4,
    normalize_hs_date
)
from app.services.custom_objects import process_deal_custom_objects_from_line_items
from app.core.constants import AssociationType
from app.utils.property_helpers import safe_convert, build_update_properties, parse_hubspot_properties, compute_tipo_iva_repercutido

logger = logging.getLogger(__name__)
router = APIRouter()

# Global dictionary to track background job status
# Structure: {deal_id: {"status": "pending"|"completed"|"error", "started_at": timestamp, "completed_at": timestamp, "error": str}}
_background_jobs_status = {}


def _background_job_wrapper(deal_id: str):
    """
    Wrapper function that tracks the status of background job execution.
    """
    try:
        # Mark job as started
        _background_jobs_status[deal_id] = {
            "status": "processing",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "error": None
        }
        logger.info(f"🚀 [JOB] Background job started for deal {deal_id}")

        # Execute the actual job
        process_deal_custom_objects_from_line_items(deal_id)

        # Mark job as completed
        _background_jobs_status[deal_id].update({
            "status": "completed",
            "completed_at": datetime.now().isoformat()
        })
        logger.info(f"✅ [JOB] Background job completed successfully for deal {deal_id}")

    except Exception as e:
        # Mark job as failed
        _background_jobs_status[deal_id].update({
            "status": "error",
            "completed_at": datetime.now().isoformat(),
            "error": str(e)
        })
        logger.error(f"❌ [JOB] Background job failed for deal {deal_id}: {e}", exc_info=True)


def _get_associated_ids(deal_id: str, to_object_type: str) -> List[str]:
    """
    Devuelve IDs asociados a un deal para el tipo indicado.
    Intenta Batch API SDK -> HTTP v4 fallback.
    """
    client = hubspot_client.get_client()
    ids = []

    # 1) Batch API SDK (método preferido)
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
            logger.warning(f"[Batch API] {len(ids)} {to_object_type} asociados al deal {deal_id}")
            return ids

    except Exception as batch_error:
        logger.warning(f"[Batch API] asociaciones {to_object_type} falló: {batch_error}")

        # 2) Try alternative batch input format
        try:
            batch_ids = BatchInputPublicObjectId([{'id': deal_id}])
            associations_response = client.crm.associations.batch_api.read(
                from_object_type="deals",
                to_object_type=to_object_type,
                batch_input_public_object_id=batch_ids
            )

            if hasattr(associations_response, 'results') and associations_response.results:
                for result in associations_response.results:
                    if hasattr(result, 'to') and result.to:
                        for assoc in result.to:
                            assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                            if assoc_id:
                                ids.append(str(assoc_id))

            if ids:
                logger.warning(f"[Alt Batch API] {len(ids)} {to_object_type} asociados al deal {deal_id}")
                return ids

        except Exception as alt_batch_error:
            logger.warning(f"[Alt Batch API] asociaciones {to_object_type} falló: {alt_batch_error}")

    # 3) HTTP v4 fallback
    try:
        access_token = client.access_token

        url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/{to_object_type}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        r = requests.get(url, headers=headers, timeout=10)

        if r.ok:
            data = r.json() or {}
            for item in data.get("results", []):
                val = item.get("toObjectId") or item.get("id")
                if val is not None:
                    ids.append(str(val))

            if ids:
                logger.warning(f"[HTTP v4] {len(ids)} {to_object_type} asociados al deal {deal_id}")
                return ids
            else:
                logger.warning(f"[HTTP v4] sin resultados para {to_object_type} en deal {deal_id}")
        else:
            logger.error(f"[HTTP v4] fallo {r.status_code}: {r.text}")

    except Exception as e:
        logger.error(f"[HTTP v4] asociaciones {to_object_type} excepción: {e}")

    return ids


def get_associated_contacts(deal_id: str) -> List[str]:
    return _get_associated_ids(deal_id, "contacts")


def get_associated_companies(deal_id: str) -> List[str]:
    return _get_associated_ids(deal_id, "companies")


def _get_line_item_to_product_ids(line_item_ids: List[str], access_token: str) -> dict:
    """
    Get the associated product ID for each line item (when hs_product_id property is empty).
    Uses HubSpot v4 batch associations: line_items -> products.

    Returns:
        dict: line_item_id (str) -> product_id (str), only entries that have an associated product.
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

    Args:
        deal_id: HubSpot deal ID
        tipo_articulo: Value to filter by (e.g., "fabricante")

    Returns:
        List of line item IDs that match the tipo_articulo filter
    """
    client = hubspot_client.get_client()

    # Get all line item IDs
    all_line_item_ids = _get_associated_ids(deal_id, "line_items")
    logger.warning(f"[Filter] Found {len(all_line_item_ids)} total line items for deal {deal_id}")

    # Filter by tipo_articulo
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

    # 1) Get all provider IDs associated to the deal
    all_provider_ids = _get_associated_ids(deal_id, "2-43108800")
    logger.warning(f"[providers-batch-filter] Found {len(all_provider_ids)} providers for deal {deal_id}")

    if not all_provider_ids:
        return []

    # 2) Prepare batch read (HubSpot safe batch size = 100)
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
    "CAMION16P": "CONTAINER-CAMION16P",
    "PALETS": "CONTAINER-PALETS",
    "TRAILER80": "CONTAINER-TRAILER80",
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

    Args:
        value: String to sanitize.

    Returns:
        Sanitized string safe for JSON serialization.
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
    tipo_articulo: Optional[str] = None,
    hs_product_type: Optional[str] = None
) -> Optional[str]:
    """
    Create a HubSpot line item and associate it with a deal

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID
        product_id: HubSpot product ID
        sku: Product SKU
        product_name: Product name
        product_price: Product price
        quantity: Line item quantity (default: "1")
        margen_unitario: Unit margin percentage (optional)
        cardcode: Provider cardcode for the line item (optional, used for FLETES)
        almacen: Warehouse code from deal (optional)
        tipo_iva_repercutido: IVA type (R21, EX, EI21) (optional)
        tipo_articulo: Article type e.g. fabricante, transitario, comisiones (optional)
        hs_product_type: Product type e.g. container, service, articulo, flete (optional)

    Returns:
        Created line item ID if successful, None otherwise
    """
    try:
        logger.warning(f"Creating line item for product {product_id} (SKU: {sku})")

        # Create line item properties
        line_item_properties = {
            "hs_product_id": str(product_id),
            "quantity": quantity,
            "price": product_price,
            "name": product_name
        }

        # Add margen_unitario if provided (HubSpot stores as percentage: 12 for 12%)
        if margen_unitario is not None:
            line_item_properties["margen_unitario"] = str(margen_unitario)

        # Add cardcode if provided (used for FLETES line items with transitario cardcode)
        if cardcode is not None:
            line_item_properties["cardcode"] = str(cardcode)

        # Add almacen if provided (warehouse code from deal)
        if almacen is not None:
            line_item_properties["almacen"] = str(almacen)

        # Add tipo_iva_repercutido if provided
        if tipo_iva_repercutido is not None:
            line_item_properties["tipo_iva_repercutido"] = str(tipo_iva_repercutido)

        # Add tipo_articulo and hs_product_type so line items are correctly classified in HubSpot
        if tipo_articulo is not None and str(tipo_articulo).strip():
            line_item_properties["tipo_articulo"] = str(tipo_articulo).strip().lower()
        if hs_product_type is not None and str(hs_product_type).strip():
            line_item_properties["hs_product_type"] = str(hs_product_type).strip()

        # Create line item using SDK
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

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID
        product_id: HubSpot product ID
        sku: Product SKU
        product_price: Product price
        quantity: Quantity for this line item (always 1 for individual containers)
        container_number: Sequential container number
        container_config: Container configuration dict (articulos, ocupacion, pesoNeto)

    Returns:
        Created line item ID if successful, None otherwise
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
            "container_config": json.dumps(sanitized_config, ensure_ascii=False)  # JSON string of sanitized config
        }

        # Create line item using SDK
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

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID
        product_id: HubSpot product ID
        sku: Product SKU
        product_name: Product name
        product_price: Product price
        quantity: Quantity of boxes/cases for this article
        container_number: Container number this article belongs to

    Returns:
        Created line item ID if successful, None otherwise
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
            "numero_container": str(container_number)
        }

        # Create line item using SDK
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


def determine_preprints_language(
    deal_language: Optional[str],
    contact_data: Optional[ContactData],
    company_data: Optional[CompanyData]
) -> str:
    """Determine the language for document printing.

    Priority:
    1. deal.preprints_language (if set, this takes precedence)
    2. contact.hs_language (if deal language is not set)
    3. company.langcode (if contact language is not set)
    4. Default to "ES" if none of the above are set

    Only ES, EN, FR are valid values. Any other value defaults to ES.

    Args:
        deal_language: Current value of deal.preprints_language.
        contact_data: Contact data with hs_language field.
        company_data: Company data with langcode field.

    Returns:
        Language code (ES, EN, or FR).
    """
    valid_languages = ["es", "en", "fr"]

    # Priority 1: Deal language (if already set)
    if deal_language:
        normalized = deal_language.strip().upper()
        if normalized in [lang.upper() for lang in valid_languages]:
            return normalized

    # Priority 2: Contact language
    if contact_data and contact_data.hs_language:
        normalized = contact_data.hs_language.strip().upper()
        if normalized in [lang.upper() for lang in valid_languages]:
            return normalized

    # Priority 3: Company language
    if company_data and company_data.langcode:
        normalized = company_data.langcode.strip().upper()
        if normalized in [lang.upper() for lang in valid_languages]:
            return normalized

    # Default: ES
    return "ES"


def determine_proyecto(
    contact_data: Optional[ContactData],
    company_data: Optional[CompanyData]
) -> Optional[str]:
    """Determine the project/origin channel for beverage fair analytics.

    Priority:
    1. contact.proyecto__canal_de_origen_ (if set)
    2. company.project (fallback if contact project is not set)

    Args:
        contact_data: Contact data with proyecto__canal_de_origen_ field.
        company_data: Company data with project field.

    Returns:
        Project name or None if not available.
    """
    # Priority 1: Contact project
    if contact_data and contact_data.proyecto__canal_de_origen_:
        return contact_data.proyecto__canal_de_origen_

    # Priority 2: Company project
    if company_data and company_data.project:
        return company_data.project

    return None


def get_property_options(property_name: str, object_type: str = "deals") -> List[PropertyOption]:
    """Get property options from HubSpot API using the SDK.

    Args:
        property_name: The name of the property to get options for.
        object_type: The object type (deals, contacts, companies, etc.).

    Returns:
        List of PropertyOption objects with label and value.
    """
    try:
        client = hubspot_client.get_client()

        # Get property info using the HubSpot SDK
        property_info = client.crm.properties.core_api.get_by_name(
            object_type=object_type,
            property_name=property_name
        )

        # Extract options from the property info
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
    """Get client data for a deal including contact, company, and deal properties.

    This endpoint retrieves:
    - Contact: firstname, lastname, country (readonly)
    - Company: name, tipo_cliente, residennum, cif, pais_de_operaciones
    - Deal: tipo_de_venta, tipo_contenedor, margen_cliente, dias_libres, naviera,
            preprints_language, project, commission fields

    Note: Documentation-related fields are now available via GET /{deal_id}/documentacion endpoint.

    Args:
        deal_id: HubSpot deal ID.

    Returns:
        ClientDataResponse with contact, company, and deal information.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:

        client = hubspot_client.get_client()

        # Step 1: Get deal properties
        deal_properties = [
            'tipo_de_venta',
            'tipo_contenedor',
            'margen_cliente',
            'dias_libres',
            'naviera',
            'preprints_language',
            'project',
            'codigo_intermediario',
            'repercutir_a_cliente',
            'tipo_comision',
            'importe_comision'
        ]

        logger.warning(f"Fetching deal {deal_id} with properties: {deal_properties}")
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties,
            associations=['contacts', 'companies']
        )

        # Step 1.5: Fetch ALL property options in parallel (OPTIMIZATION)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Define all properties to fetch upfront
        all_properties_to_fetch = [
            ('country', 'contacts'),
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

        # Fetch all property options in parallel
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


        # Step 2: Get associated contact
        contact_data = None

        try:
            # Get contact associations using the SDK
            contact_ids = get_associated_contacts(deal_id)

            if contact_ids:
                contact_id = contact_ids[0]  # Get primary contact
                logger.warning(f"Found associated contact: {contact_id}")

                # Get contact details
                contact = client.crm.contacts.basic_api.get_by_id(
                    contact_id,
                    properties=['firstname', 'lastname', 'country', 'hs_language', 'proyecto__canal_de_origen_']
                )

                # Get country label from options (using cache)
                country_value = contact.properties.get('country')
                country_label = country_value
                if country_value:
                    country_options = property_options_cache.get('country', [])
                    for option in country_options:
                        if option.value == country_value:
                            country_label = option.label
                            break

                contact_data = ContactData(
                    firstname=contact.properties.get('firstname'),
                    lastname=contact.properties.get('lastname'),
                    country=country_label,  # Country label for display
                    hs_language=contact.properties.get('hs_language'),
                    proyecto__canal_de_origen_=contact.properties.get('proyecto__canal_de_origen_')
                )
            else:
                logger.warning(f"No contacts associated with deal {deal_id}")

        except Exception as contact_error:
            logger.warning(f"Error fetching contact data: {contact_error}")

        # Step 3: Get associated company
        company_data = None

        try:
            # Get company associations using the SDK
            company_ids = get_associated_companies(deal_id)

            if company_ids:
                company_id = company_ids[0]  # Get primary company
                logger.warning(f"Found associated company: {company_id}")

                # Get company details
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

                company_data = CompanyData(
                    name=company.properties.get('name'),
                    pais_de_operaciones=pais_de_operaciones_label,  # Use label instead of value
                    country_entrega=company.properties.get('country_entrega'),
                    country=company.properties.get('country'),
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

        # Step 4: Build deal client data with options
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


        # Step 4.5: Get intermediary fields with fallback logic
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
            commission_provider_info=commission_provider_info
        )

        # Step 5: Get client classification from company tipo_cliente field
        clasificacion = None
        if company_data and company_data.tipo_cliente:
            # Get the label for clasificacion (to match frontend expectations)
            tipo_cliente_value = company_data.tipo_cliente.value
            if tipo_cliente_value:
                for option in company_data.tipo_cliente.options:
                    if option.value == tipo_cliente_value:
                        clasificacion = option.label
                        break


        # Step 6: Determine project/origin channel
        proyecto = determine_proyecto(contact_data, company_data)

        # Step 7: Build response
        return ClientDataResponse(
            success=True,
            contact=contact_data,
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
    """Update client data for a deal from ClienteTab.

    This endpoint updates:
    - Company: tipo_cliente
    - Deal: tipo_de_venta, tipo_contenedor, margen_cliente

    Note:
    - Contact fields (country) are readonly and not updated
    - dias_libres is managed in TransitarioTab, not here

    All fields are optional and only non-null values will be updated.

    Args:
        deal_id: HubSpot deal ID.
        data: UpdateClientDataRequest with company and deal data to update.

    Returns:
        BaseResponse indicating success or failure.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Step 1: Update contact if contact data is provided
        if data.contact is not None:
            try:
                # Get contact associations using the SDK
                contact_ids = get_associated_contacts(deal_id)

                if contact_ids:
                    contact_id = contact_ids[0]  # Get primary contact
                    logger.warning(f"Updating contact {contact_id} for deal {deal_id}")

                    # Build properties dict dynamically from model
                    contact_properties = build_update_properties(data.contact, UpdateContactData)

                    # Update contact if there are properties to update
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

        # Step 2: Update company if company data is provided
        if data.company is not None:
            try:
                # Get company associations using the SDK
                company_ids = get_associated_companies(deal_id)

                if company_ids:
                    company_id = company_ids[0]  # Get primary company
                    logger.warning(f"Updating company {company_id} for deal {deal_id}")

                    # Build properties dict dynamically from model
                    company_properties = build_update_properties(data.company, UpdateCompanyData)

                    # Update company if there are properties to update
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

        # Step 3: Update deal if deal data is provided
        if data.deal is not None:
            # Build properties dict dynamically from model
            deal_properties = build_update_properties(data.deal, UpdateDealData)

            # Update deal if there are properties to update
            if deal_properties:
                logger.warning(f"Updating deal {deal_id} with properties: {deal_properties}")
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={
                        "properties": deal_properties
                    }
                )
                logger.warning(f"Deal {deal_id} updated successfully")

            # Step 3.5: Manage intermediario (commission provider) association
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

                                # Check if already associated
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


@router.get("/{deal_id}/documentacion", response_model=DealDocumentacionResponse)
async def get_deal_documentacion_data(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Get documentacion data for a deal from DocumentacionTab.

    This endpoint retrieves all documentation-related fields:
    - Language settings (preprints_language)
    - Fiscal address (direccion_fiscal, inherited from company if empty)
    - Logistics documentation (tramitar_carga, documentos_a_generar, texto_en_bl, etc.)
    - Certificate of origin (consignee, shipper, notify, certificado_origen_mercancia)
    - Payment method and texts (metodo_pago_plantilla, banco_cobro, etc.)
    - Generated texts (texto_banco, texto_forma_pago, texto_informacion_expediente, texto_base_oferta)

    Args:
        deal_id: HubSpot deal ID.

    Returns:
        DealDocumentacionResponse with deal documentacion data.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Properties to fetch
        deal_properties = [
            # Idioma global
            'preprints_language',
            # Documentación
            'direccion_fiscal',
            # Documentación Logística
            'tramitar_carga',
            'documentos_a_generar',
            'texto_en_bl',
            'comentarios_logistica',
            'comentarios_transitario',
            # Certificado de Origen
            'consignee',
            'shipper',
            'notify',
            'certificado_origen_mercancia',
            # Método de Pago & Textos
            'metodo_pago_plantilla',
            'banco_cobro',
            'cotizacion_rapida',
            'texto_adicional_metodo_pago',
            'texto_firma_aceptacion_cliente',
            # Textos Generados
            'texto_banco',
            'texto_forma_pago',
            'texto_informacion_expediente',
            'texto_base_oferta'
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

        # Get company for direccion_fiscal fallback
        company_address = None
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=['address']
                )
                company_address = company.properties.get('address')
        except Exception as e:
            logger.warning(f"Error fetching company for direccion_fiscal fallback: {e}")

        # Extract values
        direccion_fiscal_value = deal.properties.get('direccion_fiscal')
        if not direccion_fiscal_value and company_address:
            direccion_fiscal_value = company_address

        # Build response
        deal_documentacion_data = DealDocumentacionData(
            # Idioma global
            preprints_language=PropertyWithOptions(
                value=deal.properties.get('preprints_language'),
                options=property_options_cache.get('preprints_language', [])
            ) if property_options_cache.get('preprints_language') else None,
            # Documentación
            direccion_fiscal=direccion_fiscal_value,
            # Documentación Logística
            tramitar_carga=safe_convert(deal.properties.get('tramitar_carga'), bool),
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
            texto_firma_aceptacion_cliente=deal.properties.get('texto_firma_aceptacion_cliente'),
            # Textos Generados
            texto_banco=deal.properties.get('texto_banco'),
            texto_forma_pago=deal.properties.get('texto_forma_pago'),
            texto_informacion_expediente=deal.properties.get('texto_informacion_expediente'),
            texto_base_oferta=deal.properties.get('texto_base_oferta')
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
    """Update documentacion data for a deal from DocumentacionTab.

    This endpoint updates all documentation-related fields:
    - Language settings (preprints_language)
    - Logistics documentation (tramitar_carga, documentos_a_generar, texto_en_bl, etc.)
    - Certificate of origin (consignee, shipper, notify, certificado_origen_mercancia)
    - Payment method and texts (metodo_pago_plantilla, banco_cobro, etc.)
    - Generated texts (texto_banco, texto_forma_pago, texto_informacion_expediente)

    All fields are optional and only non-null values will be updated.

    Args:
        deal_id: HubSpot deal ID.
        data: UpdateDocumentacionRequest with deal documentacion data to update.

    Returns:
        BaseResponse indicating success or failure.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Update deal if deal data is provided
        if data.deal is not None:
            # Build properties dict dynamically from model
            deal_properties = build_update_properties(data.deal, UpdateDocumentacionData)

            # Update deal if there are properties to update
            if deal_properties:
                logger.warning(f"Updating deal {deal_id} with documentacion properties: {deal_properties}")
                client.crm.deals.basic_api.update(
                    deal_id=deal_id,
                    simple_public_object_input={
                        "properties": deal_properties
                    }
                )
                logger.warning(f"Deal {deal_id} documentacion updated successfully")

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
    """Get articles with tipo_articulo=fabricante and providers with tipo_proveedor=fabricante for a deal (separate arrays).

    Flujo:
      1) Asociaciones de line items del deal
      2) Filtrar solo line items con tipo_articulo == "fabricante"
      3) Para cada line item filtrado: obtener product_id y datos del producto
      4) Obtener asociaciones de proveedores (custom object 2-43108800) del deal
      5) Filtrar proveedores por tipo_proveedor == "fabricante"
      6) Devolver:
         - articles: artículos de fabricante con cardcode del proveedor habitual del producto
         - providers: proveedores de tipo fabricante (únicos)
    """
    try:
        client = hubspot_client.get_client()

        logger.warning(f"[articles] Fetching articles with tipo_articulo=fabricante for deal {deal_id}")

        # 0) Fetch deal's lugar_carga value
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=["lugar_carga"]
        )
        lugar_carga_value = deal.properties.get("lugar_carga")
        logger.info(f"[articles] Deal lugar_carga value: {lugar_carga_value}")

        # 1) Line items asociados al deal
        line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[articles] READ deal_id={deal_id} -> line_item_ids count={len(line_item_ids)} ids={line_item_ids[:20]}{'...' if len(line_item_ids) > 20 else ''}")

        # 2) BATCH READ: Filtrar solo fabricante y extraer product_ids
        # También guardamos el tipo_iva_repercutido de cada line item
        product_ids: list[str] = []
        line_item_tipo_iva: dict[str, str] = {}  # product_id -> tipo_iva_repercutido del line item

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
                    "properties": ["hs_product_id", "hs_product_type", "tipo_articulo", "tipo_iva_repercutido"],
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

            product_ids = list(dict.fromkeys(product_ids))

        logger.warning(f"[articles] After fabricante filter: product_ids count={len(product_ids)} product_ids={product_ids[:15]}{'...' if len(product_ids) > 15 else ''}")

        # 3) BATCH READ: Detalles de productos (non-container)
        articles: list[ArticleWithProvider] = []

        if product_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
            all_products = []

            for i in range(0, len(product_ids), batch_size):
                batch_ids = product_ids[i:i + batch_size]

                batch_payload = {
                    "properties": [
                        "name", "hs_sku", "cardcode", "codigo_proveedor", "peso_neto",
                        # Price fields for negotiation
                        "hs_price_eur", "ultimo_precio_compra", "ultimo_precio_evaluado", "margen_unitario",
                        # VAT type
                        "tipo_iva_repercutido",
                        # Container capacity fields
                        "unidades_cam16", "unidades_d20dr", "unidades_d40dr",
                        "unidades_d20w", "unidades_d40hd", "unidades_d45hd",
                        "unidades_d45w", "unidades_palet", "unidades_r20rf",
                        "unidades_r40rf", "unidades_tra80"
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

            # Process all products
            for product in all_products:
                try:
                    product_id = product.get("id")
                    props = product.get("properties", {})

                    # Parse all product properties using helper
                    product_data = parse_hubspot_properties(product_id, props, ArticleWithProvider)

                    # HubSpot stores margen_unitario as percentage already (12 for 12%), no conversion needed

                    # Override provider fields (set to None initially, will be matched later)
                    product_data["provider_name"] = None
                    product_data["provider_id"] = None

                    # Override tipo_iva_repercutido with the value from the line item (if exists)
                    if product_id in line_item_tipo_iva:
                        product_data["tipo_iva_repercutido"] = line_item_tipo_iva[product_id]
                        logger.info(f"[articles] Using line item tipo_iva_repercutido for product {product_id}: {line_item_tipo_iva[product_id]}")

                    # Create ArticleWithProvider instance
                    articles.append(ArticleWithProvider(**product_data))

                except Exception as e:
                    logger.error(f"[articles] Error processing product {product.get('id')}: {e}")

        # 4) BATCH READ: Proveedores asociados al deal (custom object 2-43108800) filtrados por tipo_proveedor = "fabricante"
        provider_ids = _get_associated_ids(deal_id, "2-43108800")
        logger.warning(f"[articles] Found {len(provider_ids)} provider associations (pre-filter)")

        providers: list[ProviderSummary] = []
        providers_by_cardcode: dict[str, ProviderSummary] = {}

        # Get lugar_de_la_carga_envio options for mapping value to label
        lugar_carga_options = {}
        try:
            # Fetch property definition for the custom object
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                # Create a mapping of value -> label
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

            # Process all providers
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

                    # Map value to label if options are available
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

        # 5) Build lugar_carga options from the lugar_carga_options dictionary
        lugar_carga_property_options: List[PropertyOption] = []

        # Add options from provider field
        for value, label in lugar_carga_options.items():
            lugar_carga_property_options.append(
                PropertyOption(value=value, label=label)
            )

        # Always add "Almacen PROEX" option
        almacen_proex_option = PropertyOption(
            value="almacen_proex",
            label="Almacen PROEX"
        )
        # Check if it's not already in the list
        if not any(opt.value == "almacen_proex" for opt in lugar_carga_property_options):
            lugar_carga_property_options.append(almacen_proex_option)

        logger.info(f"[articles] Built {len(lugar_carga_property_options)} lugar_carga options")

        # 6) Respuesta separada
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
    """
    Fully optimized ArticlesTab update endpoint:
    - Batch search SKUs
    - Batch search providers
    - Batch delete line items (fabricante)
    - Batch delete containers
    - Correct delete for providers (NO batch API)
    - Batch get products
    - Create line items
    - Associate providers
    """

    try:
        client = hubspot_client.get_client()
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # ---------------------------------------------------
        # 0. NORMALIZAR PAYLOAD
        # ---------------------------------------------------
        if not isinstance(data, UpdateArticlesRequest):

            if isinstance(data, dict) and "line_items" in data:
                data = UpdateArticlesRequest(**data)

            elif isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                data = UpdateArticlesRequest(**data["data"])

            else:
                raise HTTPException(status_code=400, detail="Invalid payload structure")

        logger.warning(f"[articles] Processing {len(data.line_items)} SKUs and {len(data.providers)} providers")

        # ---------------------------------------------------
        # 0.5. GET DEAL ALMACEN AND DETERMINE IVA TYPE
        # ---------------------------------------------------
        deal_almacen = None
        tipo_iva_repercutido = "R21"  # Default: NACIONAL

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
                tipo_iva_repercutido = compute_tipo_iva_repercutido(residennum)
                logger.warning(f"[articles] Company residence: {residennum or 'NACIONAL'} -> IVA: {tipo_iva_repercutido}")
            else:
                logger.warning(f"[articles] No company found, using default IVA: R21")
        except Exception as e:
            logger.warning(f"[articles] Error getting company residence, using default IVA: {e}")

        # ---------------------------------------------------
        # 1. SKUs + PROVIDERS (UNIQUE)
        # ---------------------------------------------------
        hs_skus = list({item.hs_sku for item in data.line_items})
        cardcodes = list({p.cardcode for p in data.providers})

        logger.warning(f"[articles] Unique SKUs={len(hs_skus)}, unique providers={len(cardcodes)}")


        # ---------------------------------------------------
        # 2. BATCH OR-SEARCH — PRODUCTS
        # ---------------------------------------------------
        from itertools import islice

        def chunk(arr, size):
            it = iter(arr)
            return iter(lambda: list(islice(it, size)), [])

        sku_to_product_id = {}
        search_url = "https://api.hubapi.com/crm/v3/objects/products/search"

        logger.warning(f"[products] Searching SKUs in OR batches")

        for batch_skus in chunk(hs_skus, 3):
            filter_groups = [
                {"filters": [{"propertyName": "hs_sku", "operator": "EQ", "value": sku}]}
                for sku in batch_skus
            ]

            payload = {
                "filterGroups": filter_groups,
                "properties": ["hs_sku", "name"],
                "limit": 100
            }

            resp = requests.post(search_url, headers=headers, json=payload, timeout=15)

            if not resp.ok:
                logger.error(f"[products] Search batch failed: {resp.status_code} - {resp.text}")
                continue

            for item in resp.json().get("results", []):
                props = item.get("properties", {})
                sku = props.get("hs_sku")
                if not sku:
                    continue

                sku_to_product_id[sku] = item.get("id")
                logger.warning(f"[products] SKU={sku} -> Product {item.get('id')}")


        # ---------------------------------------------------
        # 3. BATCH OR-SEARCH — PROVIDERS (custom object)
        # ---------------------------------------------------
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


        # ---------------------------------------------------
        # 4+5. SINGLE BATCH READ → DELETE fabricante + containers
        # ---------------------------------------------------

        # 1. Get IDs once
        all_line_items = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[cleanup] Total line items associated: {len(all_line_items)}")

        fabricantes_to_delete = []
        containers_to_delete = []

        if all_line_items:
            # 2. Batch read BOTH tipo_articulo and hs_product_type in one go
            batch_read = {
                "properties": ["tipo_articulo", "hs_product_type"],
                "inputs": [{"id": str(li)} for li in all_line_items]
            }

            resp = client.crm.line_items.batch_api.read(
                batch_read_input_simple_public_object_id=batch_read
            )

            # 3. Single pass → classify items
            for item in resp.results or []:
                props = item.properties or {}

                tipo = (props.get("tipo_articulo") or "").strip().lower()
                ptype = (props.get("hs_product_type") or "").strip().lower()

                if tipo == "fabricante":
                    fabricantes_to_delete.append(item.id)

                if _is_container_product_type(ptype):
                    containers_to_delete.append(item.id)

        # 4. Delete fabricantes batch
        if fabricantes_to_delete:
            try:
                batch_input = {"inputs": [{"id": fid} for fid in fabricantes_to_delete]}
                client.crm.line_items.batch_api.archive(
                    batch_input_simple_public_object_id=batch_input
                )
                logger.warning(f"[cleanup] Deleted {len(fabricantes_to_delete)} fabricante line items")
            except Exception as e:
                logger.error(f"[cleanup] fabricante batch delete failed: {e}")

        # 5. Delete containers batch
        if containers_to_delete:
            try:
                batch_input = {"inputs": [{"id": cid} for cid in containers_to_delete]}
                client.crm.line_items.batch_api.archive(
                    batch_input_simple_public_object_id=batch_input
                )
                logger.warning(f"[cleanup] Deleted {len(containers_to_delete)} containers")
            except Exception as e:
                logger.error(f"[cleanup] container batch delete failed: {e}")


        # ---------------------------------------------------
        # 6. DELETE PROVIDERS tipo_proveedor=fabricante (INDIVIDUAL — REQUIRED)
        # ---------------------------------------------------
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


        # ---------------------------------------------------
        # 7. BATCH GET PRODUCT DETAILS (FAST)
        # ---------------------------------------------------
        product_ids = list(sku_to_product_id.values())

        batch_read = {
            "properties": [
                "name", "hs_sku", "hs_price_eur", "ultimo_precio_compra",
                "ultimo_precio_evaluado"
            ],
            "inputs": [{"id": pid} for pid in product_ids]
        }

        resp = client.crm.products.batch_api.read(
            batch_read_input_simple_public_object_id=batch_read
        )

        product_details = {
            item.id: item.properties for item in (resp.results or [])
        }


        # ---------------------------------------------------
        # 8. CREATE LINE ITEMS (ONE BY ONE)
        # ---------------------------------------------------
        sku_to_margin = {item.hs_sku: item.margen_unitario for item in data.line_items}
        line_items_created = []

        for sku, product_id in sku_to_product_id.items():

            props = product_details.get(product_id, {})
            if not props:
                logger.error(f"[products] Missing product details for {product_id}")
                continue

            price = props.get("hs_price_eur") or props.get("ultimo_precio_compra") or props.get("ultimo_precio_evaluado") or "0"
            margin = sku_to_margin.get(sku)

            line_item_id = create_line_item_with_association(
                client=client,
                deal_id=deal_id,
                product_id=product_id,
                sku=sku,
                product_name=props.get("name", f"Product {sku}"),
                product_price=str(price),
                margen_unitario=margin,
                almacen=deal_almacen,
                tipo_iva_repercutido=tipo_iva_repercutido,
                tipo_articulo="fabricante",
                hs_product_type="Articulo"
            )

            if line_item_id:
                line_items_created.append(line_item_id)


        # ---------------------------------------------------
        # 9. ASSOCIATE PROVIDERS (INDIVIDUAL — REQUIRED)
        # ---------------------------------------------------
        for provider_id in cardcode_to_provider_id.values():
            assoc_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
            payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 286}]

            resp = requests.put(assoc_url, headers=headers, json=payload, timeout=10)
            if not resp.ok:
                logger.error(f"[providers-assoc] Failed provider {provider_id}: {resp.status_code} - {resp.text}")


        # ---------------------------------------------------
        # 10. DONE
        # ---------------------------------------------------
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

        # Get all line items associated with the deal
        line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[services] READ line_item_ids count={len(line_item_ids)} ids={line_item_ids[:20]}{'...' if len(line_item_ids) > 20 else ''}")

        services = []

        # STEP 1: BATCH READ all line items
        if not line_item_ids:
            logger.warning(f"[services] No line items for deal {deal_id} -> returning empty services")
            return DealServicesResponse(success=True, error=None, services=[])

        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_size = 100
        all_line_items = []

        for i in range(0, len(line_item_ids), batch_size):
            batch_ids = line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": ["hs_product_id", "hs_product_type", "price", "name", "cardcode", "tipo_articulo", "tipo_iva_repercutido"],
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

        # Regla unificada: servicios = hs_product_type = service, excluyendo tipo_articulo = transitario
        service_line_items = []
        unique_cardcodes = set()
        product_ids_to_fetch = []

        for line_item in all_line_items:
            props = line_item.get("properties", {})
            if not _line_item_is_service(props):
                continue
            tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()
            if tipo_articulo == "transitario":
                continue  # Los de tipo_articulo=transitario quedan fuera del read de servicios
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
                if not s["product_name"] and s["product_id"] in product_map:
                    s["product_name"] = product_map[s["product_id"]].get("name") or ""

        logger.warning(f"[services] Filtered to {len(service_line_items)} service line items (hs_product_type=service)")

        # STEP 4: BATCH SEARCH all providers by cardcodes
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

        # STEP 5: Group service line items by (product_id, cardcode) to get unique services
        # Since each service is now replicated per container, we need to deduplicate
        unique_services_map = {}  # key: (product_id, cardcode) -> service data

        for service_data in service_line_items:
            props = service_data["line_item"].get("properties", {})
            cardcode = props.get("cardcode") or ""
            product_id = str(service_data["product_id"])

            # Create unique key
            unique_key = (product_id, cardcode)

            # Only add if not already in map (first occurrence)
            if unique_key not in unique_services_map:
                provider_info = cardcode_to_provider.get(cardcode, {}) if cardcode else {}
                price = float(props.get("price") or 0)
                tipo_iva_repercutido = props.get("tipo_iva_repercutido") or None

                unique_services_map[unique_key] = {
                    "product_id": product_id,
                    "product_name": service_data["product_name"] or props.get("name") or "",
                    "tipo_articulo": service_data["tipo_articulo"],
                    "provider_id": provider_info.get("id"),
                    "provider_name": provider_info.get("name"),
                    "provider_cardcode": cardcode if cardcode else None,
                    "price": price,
                    "tipo_iva_repercutido": tipo_iva_repercutido
                }

        # STEP 6: Build unique service items
        for service_data in unique_services_map.values():
            services.append(ServiceItem(
                product_id=service_data["product_id"],
                product_name=service_data["product_name"],
                tipo_articulo=service_data["tipo_articulo"],
                provider_id=service_data["provider_id"],
                provider_name=service_data["provider_name"],
                provider_cardcode=service_data["provider_cardcode"],
                price=service_data["price"],
                tipo_iva_repercutido=service_data.get("tipo_iva_repercutido")
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
    """Update service line items for a deal from ServiciosTab.

    This endpoint:
    1. Receives a list of services with product_id, product_name, provider_id, provider_cardcode, and price
    2. Removes existing service line items (hs_product_type = service)
    3. Removes existing service provider associations (tipo_proveedor NOT in fabricante, transitario, comisiones)
    4. Fetches all containers associated with the deal
    5. Determines IVA type (tipo_iva_repercutido) based on company residence (R21, EX, EI21)
    6. For each service × container, creates a line item with:
       - name: "{container_name} - {service_name}"
       - quantity: 1
       - price: service price (per container)
       - numero_container: links to container
       - tipo_iva_repercutido: IVA type
    7. Associates the service providers with the deal

    Args:
        deal_id: HubSpot deal ID.
        data: UpdateServicesRequest with services array.

    Returns:
        BaseResponse indicating success or failure.
    """
    try:
        client = hubspot_client.get_client()
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        batch_size = 100

        # Handle both Pydantic model and dict input
        if isinstance(data, dict):
            data = UpdateServicesRequest(**data)

        logger.warning(f"[services] Processing {len(data.services)} services for deal {deal_id}")

        # Step 0.5: Get deal almacen
        deal_almacen = None
        try:
            deal_basic = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["almacen"]
            )
            deal_almacen = deal_basic.properties.get("almacen")
            if deal_almacen:
                logger.warning(f"[services] Deal almacen: {deal_almacen}")
        except Exception as e:
            logger.warning(f"[services] Could not fetch deal almacen: {e}")

        # Step 1: BATCH READ line items and products, then BATCH DELETE
        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[services] Found {len(existing_line_item_ids)} total existing line items")

        service_line_item_ids_to_delete = []

        if existing_line_item_ids:
            # BATCH READ all line items
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
            batch_size = 100
            all_line_items = []

            for i in range(0, len(existing_line_item_ids), batch_size):
                batch_ids = existing_line_item_ids[i:i + batch_size]
                batch_payload = {
                    "properties": ["hs_product_id", "hs_product_type"],
                    "inputs": [{"id": str(lid)} for lid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        all_line_items.extend(batch_response.json().get("results", []))
                except Exception as e:
                    logger.error(f"[services] Error in batch read line items: {e}")

            logger.warning(f"[services] Batch fetched {len(all_line_items)} line items")

            # Regla unificada: borrar line items que son servicios (hs_product_type = service)
            for line_item in all_line_items:
                props = line_item.get("properties", {})
                if _line_item_is_service(props):
                    service_line_item_ids_to_delete.append(line_item.get("id"))
                    logger.warning(f"[services] Will delete line item {line_item.get('id')} (hs_product_type=service)")

            logger.warning(f"[services] Found {len(service_line_item_ids_to_delete)} service line items to delete")

            # BATCH DELETE service line items
            if service_line_item_ids_to_delete:
                batch_delete_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"

                for i in range(0, len(service_line_item_ids_to_delete), batch_size):
                    batch_ids = service_line_item_ids_to_delete[i:i + batch_size]
                    batch_payload = {
                        "inputs": [{"id": str(lid)} for lid in batch_ids]
                    }

                    try:
                        batch_response = requests.post(batch_delete_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            logger.warning(f"[services] Batch deleted {len(batch_ids)} line items")
                        else:
                            logger.error(f"[services] Batch delete failed: {batch_response.status_code}")
                    except Exception as e:
                        logger.error(f"[services] Error in batch delete: {e}")

        # Step 2: BATCH READ providers and remove service provider associations
        existing_provider_ids = _get_associated_ids(deal_id, "2-43108800")
        logger.warning(f"[services] Found {len(existing_provider_ids)} total provider associations")

        service_provider_ids_to_remove = []

        if existing_provider_ids:
            # BATCH READ all providers
            batch_url = "https://api.hubapi.com/crm/v3/objects/2-43108800/batch/read"

            for i in range(0, len(existing_provider_ids), batch_size):
                batch_ids = existing_provider_ids[i:i + batch_size]
                batch_payload = {
                    "properties": ["tipo_proveedor"],
                    "inputs": [{"id": str(pid)} for pid in batch_ids]
                }

                try:
                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        for provider in batch_response.json().get("results", []):
                            provider_id = provider.get("id")
                            tipo_proveedor = (provider.get("properties", {}).get("tipo_proveedor") or "").strip().lower()

                            # Quitar asociaciones de proveedores que no son fabricante/transitario/comisiones/seguro (son proveedores de servicio)
                            if tipo_proveedor not in ("fabricante", "transitario", "comisiones", "seguro"):
                                service_provider_ids_to_remove.append(provider_id)
                                logger.warning(f"[services] Will remove provider {provider_id} (tipo_proveedor: {tipo_proveedor})")
                except Exception as e:
                    logger.error(f"[services] Error in batch read providers: {e}")

            logger.warning(f"[services] Found {len(service_provider_ids_to_remove)} service provider associations to remove")

            # Remove service provider associations (individual DELETE requests - no batch API available)
            for provider_id in service_provider_ids_to_remove:
                try:
                    delete_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
                    delete_response = requests.delete(delete_url, headers=headers, timeout=10)

                    if delete_response.ok:
                        logger.warning(f"[services] Removed provider {provider_id} association")
                    else:
                        logger.error(f"[services] Failed to remove provider {provider_id}: {delete_response.status_code}")
                except Exception as e:
                    logger.error(f"[services] Error removing provider {provider_id} association: {e}")

        # Step 3: GET CONTAINERS and determine IVA type
        container_line_items = []
        tipo_iva_repercutido = "R21"  # Default

        if data.services:
            # Fetch all line items to get containers
            if not existing_line_item_ids:
                existing_line_item_ids = _get_associated_ids(deal_id, "line_items")

            if existing_line_item_ids:
                # BATCH READ all line items to find containers
                batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
                batch_size = 100

                for i in range(0, len(existing_line_item_ids), batch_size):
                    batch_ids = existing_line_item_ids[i:i + batch_size]
                    batch_payload = {
                        "properties": ["name", "hs_product_type", "numero_container"],
                        "inputs": [{"id": str(lid)} for lid in batch_ids]
                    }

                    try:
                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            for line_item in batch_response.json().get("results", []):
                                props = line_item.get("properties", {})
                                product_type = (props.get("hs_product_type") or "").strip().lower()

                                if _is_container_product_type(product_type):
                                    container_line_items.append({
                                        "id": line_item.get("id"),
                                        "name": props.get("name", ""),
                                        "numero_container": props.get("numero_container", "")
                                    })
                    except Exception as e:
                        logger.error(f"[services] Error fetching containers: {e}")

                logger.warning(f"[services] Found {len(container_line_items)} containers")

            # Get company residence to determine IVA type
            try:
                company_ids = get_associated_companies(deal_id)
                if company_ids:
                    company_id = company_ids[0]
                    company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                    company_response = requests.get(
                        company_url,
                        headers=headers,
                        params={"properties": "residennum"},
                        timeout=10
                    )

                    if company_response.ok:
                        residennum = (company_response.json()
                                     .get("properties", {})
                                     .get("residennum", ""))

                        tipo_iva_repercutido = compute_tipo_iva_repercutido(residennum)
                        logger.warning(f"[services] Company residence: {residennum or 'NACIONAL'}, IVA type: {tipo_iva_repercutido}")
            except Exception as e:
                logger.error(f"[services] Error fetching company residence: {e}")

        # Step 4: BATCH CREATE new service line items (one per service per container)
        line_items_created = []
        provider_ids_to_associate = set()

        if data.services:
            # Get tipo_articulo and hs_product_type from products so we set them on each service line item (do not override with hardcoded values)
            service_product_ids = list({str(s.product_id) for s in data.services})
            service_product_tipo_map = {}  # product_id -> tipo_articulo (lowercase)
            service_product_hs_type_map = {}  # product_id -> hs_product_type (as in HubSpot: Servicio, Flete, etc.)
            if service_product_ids:
                try:
                    prod_batch_url = "https://api.hubapi.com/crm/v3/objects/products/batch/read"
                    for i in range(0, len(service_product_ids), batch_size):
                        batch_ids = service_product_ids[i:i + batch_size]
                        batch_payload = {"properties": ["tipo_articulo", "hs_product_type"], "inputs": [{"id": pid} for pid in batch_ids]}
                        batch_response = requests.post(prod_batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            for product in batch_response.json().get("results", []):
                                pid = product.get("id")
                                if not pid:
                                    continue
                                pid = str(pid)
                                props = product.get("properties", {})
                                t = (props.get("tipo_articulo") or "").strip().lower()
                                service_product_tipo_map[pid] = t or "servicio"
                                # Use product's hs_product_type as-is (e.g. Servicio, Flete) so we don't overwrite with wrong value
                                hs_type = (props.get("hs_product_type") or "").strip()
                                service_product_hs_type_map[pid] = hs_type if hs_type else "Servicio"
                except Exception as e:
                    logger.warning(f"[services] Error fetching product tipo_articulo/hs_product_type: {e}")

            # Prepare all line items for batch creation
            line_items_to_create = []

            if not container_line_items:
                logger.warning(f"[services] No containers found - creating services without container association")
                # Fallback: create services without container association
                for service in data.services:
                    service_tipo_iva = (service.tipo_iva if service.tipo_iva else tipo_iva_repercutido)
                    service_tipo_iva_normalized = _tipo_iva_repercutido_for_hubspot(service_tipo_iva)  # EX, R21, etc. en mayúsculas
                    service_tipo = service_product_tipo_map.get(str(service.product_id), "servicio")
                    service_hs_type = _hs_product_type_for_hubspot(
                        service_product_hs_type_map.get(str(service.product_id)) or "Servicio"
                    )  # Valor exacto: service, Flete, etc.
                    service_properties = {
                        "hs_product_id": service.product_id,
                        "name": service.product_name,
                        "price": str(service.price),
                        "precio_de_coste": str(service.price),
                        "quantity": "1",
                        "cardcode": service.provider_cardcode or "",
                        "tipo_iva_repercutido": service_tipo_iva_normalized,
                        "tipo_articulo": service_tipo,
                        "hs_product_type": service_hs_type
                    }

                    # Add almacen if available
                    if deal_almacen:
                        service_properties["almacen"] = str(deal_almacen)

                    line_item_data = {
                        "properties": service_properties,
                        "associations": [{
                            "to": {"id": deal_id},
                            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                        }]
                    }
                    line_items_to_create.append(line_item_data)
                    provider_ids_to_associate.add(service.provider_id)
            else:
                # Create one line item per service per container
                for service in data.services:
                    service_tipo_iva = (service.tipo_iva if service.tipo_iva else tipo_iva_repercutido)
                    service_tipo_iva_normalized = _tipo_iva_repercutido_for_hubspot(service_tipo_iva)
                    service_tipo = service_product_tipo_map.get(str(service.product_id), "servicio")
                    service_hs_type = _hs_product_type_for_hubspot(
                        service_product_hs_type_map.get(str(service.product_id)) or "Servicio"
                    )

                    for container in container_line_items:
                        service_with_container_properties = {
                            "hs_product_id": service.product_id,
                            "name": f"{container['name']} - {service.product_name}",
                            "price": str(service.price),
                            "precio_de_coste": str(service.price),
                            "quantity": "1",
                            "cardcode": service.provider_cardcode or "",
                            "numero_container": container["numero_container"],
                            "tipo_iva_repercutido": service_tipo_iva_normalized,
                            "tipo_articulo": service_tipo,
                            "hs_product_type": service_hs_type
                        }

                        # Add almacen if available
                        if deal_almacen:
                            service_with_container_properties["almacen"] = str(deal_almacen)

                        line_item_data = {
                            "properties": service_with_container_properties,
                            "associations": [{
                                "to": {"id": deal_id},
                                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                            }]
                        }
                        line_items_to_create.append(line_item_data)

                    provider_ids_to_associate.add(service.provider_id)

            logger.warning(f"[services] Prepared {len(line_items_to_create)} line items for batch creation")

            # BATCH CREATE line items
            batch_create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"

            for i in range(0, len(line_items_to_create), batch_size):
                batch_items = line_items_to_create[i:i + batch_size]
                batch_payload = {"inputs": batch_items}

                try:
                    batch_response = requests.post(batch_create_url, headers=headers, json=batch_payload, timeout=60)
                    if batch_response.ok:
                        created_items = batch_response.json().get("results", [])
                        for item in created_items:
                            line_items_created.append(item.get("id"))
                        logger.warning(f"[services] Batch created {len(created_items)} line items")
                    else:
                        logger.error(f"[services] Batch create failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"[services] Error in batch create line items: {e}")

            logger.warning(f"[services] Created {len(line_items_created)} service line items")

        # Step 6: Create new service provider associations (using provider_id directly)
        if provider_ids_to_associate:
            logger.warning(f"[services] Associating {len(provider_ids_to_associate)} providers with deal")

            for provider_id in provider_ids_to_associate:
                try:
                    # Verify provider exists
                    verify_url = f"https://api.hubapi.com/crm/v3/objects/2-43108800/{provider_id}"
                    verify_response = requests.get(verify_url, headers=headers, timeout=10)

                    if not verify_response.ok:
                        logger.error(f"[services] Provider {provider_id} does not exist")
                        continue

                    # Create association (associationTypeId: 448 = "Servicios")
                    assoc_url = f"https://api.hubapi.com/crm/v4/objects/2-43108800/{provider_id}/associations/deals/{deal_id}"
                    assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 448}]

                    response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                    if response.ok:
                        logger.warning(f"[services] Associated provider {provider_id} (associationTypeId: 448 - Servicios)")
                    else:
                        logger.error(f"[services] Failed to associate provider {provider_id}: {response.status_code}")

                except Exception as e:
                    logger.error(f"[services] Error associating provider {provider_id}: {e}")

        return BaseResponse(success=True, error=None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_services: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/containers", response_model=DealContainersResponse)
async def get_deal_containers(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Get container line items for a deal from ContenedoresTab.

    This endpoint retrieves all container line items associated with a deal:
    1. Fetches all line items associated with the deal
    2. Filters for container line items (hs_product_type = container, tipo_articulo = transitario)
    3. Groups containers by type and configuration (same config = same group)
    4. For each group, aggregates quantity and returns single ContainerItem
    5. Calculates totals: total_containers, total_units, total_colis, total_peso_neto

    Note: Individual containers are stored as separate line items (quantity=1 each),
    but are returned grouped by configuration for UI display.

    Args:
        deal_id: HubSpot deal ID.

    Returns:
        DealContainersResponse with grouped containers and calculated totals.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        import json
        client = hubspot_client.get_client()

        logger.warning(f"[containers] READ deal_id={deal_id} fetching container line items")

        # Step 1: Get all line items associated with the deal
        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"[containers] READ line_item_ids count={len(existing_line_item_ids)} ids={existing_line_item_ids[:20]}{'...' if len(existing_line_item_ids) > 20 else ''}")

        # Step 2: Filter for container line items and parse their data
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
                        "tipo_iva_repercutido"
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

            # Process container line items
            for line_item in container_line_items:
                try:
                    line_item_id = line_item.get("id")
                    props = line_item.get("properties", {})

                    # Parse container_config JSON
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

                    # Get container type from product SKU map
                    product_id = props.get("hs_product_id", "")
                    container_type = "UNKNOWN"
                    if product_id:
                        sku = product_sku_map.get(product_id, "")
                        # Reverse lookup from SKU to tipo
                        for tipo, container_sku in CONTAINER_SKU_MAP.items():
                            if container_sku == sku:
                                container_type = tipo
                                break

                    # Parse quantity and container_number
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

        # Step 3: Group containers by type and configuration
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

        # Step 4: Create aggregated ContainerItems
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

            # Get the lowest container number in the group
            min_container_number = first_container["container_number"]

            # Create aggregated ContainerItem
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

            # Calculate peso neto
            total_peso_neto += first_container["container_config"].pesoNeto * total_cantidad

            logger.warning(f"Grouped containers: {first_container['container_type']} x{total_cantidad} (containers #{min_container_number}-{container_list[-1]['container_number']})")

        # Sort containers by container_number
        containers.sort(key=lambda c: c.container_number)

        # Step 5: Build article data map from batch results
        # We already have article line items from the batch request above
        article_data_map = {}
        for line_item in article_line_items:
            try:
                line_item_id = line_item.get("id")
                props = line_item.get("properties", {})

                # Use the name property as unique key (contains container name + article name)
                unique_name = props.get("name", "")
                if unique_name:
                    article_data_map[unique_name] = {
                        "line_item_id": line_item_id,
                        "hs_sku": props.get("hs_sku", ""),
                        "price": float(props.get("price", 0)) if props.get("price") else None,
                        "precio_de_coste": float(props.get("precio_de_coste", 0)) if props.get("precio_de_coste") else None,
                        "margen_unitario": float(props.get("margen_unitario", 0)) if props.get("margen_unitario") else None,
                        "tipo_iva_repercutido": props.get("tipo_iva_repercutido", None)
                    }
                    logger.warning(f"Mapped article line item {line_item_id}: name='{unique_name}'")
            except Exception as e:
                logger.error(f"Error mapping article line item: {e}")

        logger.warning(f"Mapped data for {len(article_data_map)} article line items (by unique name)")

        # Step 6: Create unique_containers array (ungrouped)
        unique_containers = []
        for container_data in individual_containers:
            container_name = container_data["container_name"]

            # Create unique articles with articuloName field and additional data
            unique_articles = []
            for articulo in container_data["container_config"].articulos:
                articulo_name = f"{container_name} - {articulo.nombre}"

                # Get additional article data from map using unique name
                # This ensures each article in each container gets its own line_item_id
                article_extra_data = article_data_map.get(articulo_name, {})

                # Fallback: if not found by exact name, try normalizing quotes
                if not article_extra_data:
                    # Normalize quotes: replace '' with "" to match HubSpot's storage
                    normalized_name = articulo_name.replace("''", '""')
                    article_extra_data = article_data_map.get(normalized_name, {})

                    if article_extra_data:
                        logger.warning(f"Article found after normalizing quotes: '{normalized_name}'")

                # Second fallback: if still not found, try by SKU (backwards compatibility)
                if not article_extra_data:
                    logger.warning(f"Article not found by unique name '{articulo_name}', trying SKU '{articulo.articuloId}'")
                    # Look for any article with matching SKU and same container
                    for name, data in article_data_map.items():
                        if data.get("hs_sku") == articulo.articuloId and name.startswith(container_name):
                            article_extra_data = data
                            logger.warning(f"Found article by SKU in same container: {name}")
                            break

                unique_article = UniqueContainerArticle(
                    articuloId=articulo.articuloId,
                    nombre=articulo.nombre,
                    articuloName=articulo_name,
                    numCajas=articulo.numCajas,
                    line_item_id=article_extra_data.get("line_item_id"),
                    price=article_extra_data.get("price"),
                    precio_de_coste=article_extra_data.get("precio_de_coste"),
                    margen_unitario=article_extra_data.get("margen_unitario"),
                    tipo_iva_repercutido=article_extra_data.get("tipo_iva_repercutido")
                )
                unique_articles.append(unique_article)

            # Create unique container config with unique articles
            unique_config = UniqueContainerConfig(
                articulos=unique_articles,
                ocupacion=container_data["container_config"].ocupacion,
                pesoNeto=container_data["container_config"].pesoNeto
            )

            # Create unique container item
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


@router.post("/{deal_id}/containers", response_model=BaseResponse)
async def update_deal_containers(
    data: UpdateContainersRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Update container line items for a deal from ContenedoresTab.

    This endpoint implements a clear-and-replace pattern:
    1. Receives container configurations from the frontend
    2. Gets the company's residence type (residennum) to determine IVA type for articles:
       - EXTRANJERO -> tipo_iva_repercutido = "EX" (Exportaciones Bebidas)
       - EUROPA -> tipo_iva_repercutido = "EI21"
       - NACIONAL (or empty) -> tipo_iva_repercutido = "R21" (default)
    3. Removes all existing container and fabricante article line items
    4. For each container config, maps the tipo to the HubSpot container product SKU
    5. Creates new container line items with custom properties:
       - hs_product_type: "container"
       - numero_container: Sequential container number (1, 2, 3...)
       - container_config: JSON with articulos, ocupacion, pesoNeto
       - quantity: Number of containers of this configuration
    6. Creates article line items with the determined tipo_iva_repercutido

    Args:
        deal_id: HubSpot deal ID.
        data: UpdateContainersRequest with containers array.

    Returns:
        BaseResponse indicating success or failure.

    Raises:
        HTTPException: If deal not found, container products not found, or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Handle both Pydantic model and dict input (for AWS Lambda compatibility)
        if isinstance(data, dict):
            data = UpdateContainersRequest(**data)

        logger.warning(f"Processing {len(data.containers)} container configurations for deal {deal_id}")

        # Step 0.5: Get deal almacen
        deal_almacen = None
        try:
            deal_basic = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["almacen"]
            )
            deal_almacen = deal_basic.properties.get("almacen")
            if deal_almacen:
                logger.warning(f"[containers] Deal almacen: {deal_almacen}")
        except Exception as e:
            logger.warning(f"[containers] Could not fetch deal almacen: {e}")

        # Step 1: Map container tipos to HubSpot Product IDs via SKU
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
                    tipo_to_product_id[tipo] = {
                        "id": str(product_id),
                        "sku": sku,
                        "name": search_response.results[0].properties.get("name", f"Contenedor {tipo}"),
                        "price": search_response.results[0].properties.get("price", "0")
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

        # Step 1.5: Get company residence type to determine IVA type for articles
        tipo_iva_repercutido = "R21"  # Default: NACIONAL
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company = client.crm.companies.basic_api.get_by_id(
                    company_id,
                    properties=['residennum']
                )
                residennum = company.properties.get('residennum', '')
                tipo_iva_repercutido = compute_tipo_iva_repercutido(residennum)
                logger.warning(f"Company residence is {residennum or 'NACIONAL'} -> IVA type: {tipo_iva_repercutido}")
            else:
                logger.warning("No company associated with deal, using default IVA type: R21")
        except Exception as e:
            logger.warning(f"Error getting company residence type, using default IVA type R21: {e}")

        # Step 1.6: Get fabricante provider associated with the deal to use as cardcode for articles
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

        # Step 2: Get all existing line items associated with the deal
        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.warning(f"Found {len(existing_line_item_ids)} existing line items")

        # Prepare headers for API requests (needed for batch operations)
        access_token = client.access_token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Step 3: BATCH READ line items to identify which ones to delete
        containers_to_delete = []
        articles_to_delete = []
        intermediarios_to_delete = []

        if existing_line_item_ids:
            batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"

            # Split into batches of 100 (HubSpot limit)
            batch_size = 100
            all_line_items = []

            for i in range(0, len(existing_line_item_ids), batch_size):
                batch_ids = existing_line_item_ids[i:i + batch_size]

                batch_payload = {
                    "properties": ["hs_product_type", "tipo_articulo"],
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

            logger.warning(f"Batch fetched {len(all_line_items)} line items for deletion check")

            # Identify which line items to delete
            for line_item in all_line_items:
                line_item_id = line_item.get("id")
                props = line_item.get("properties", {})
                product_type = props.get("hs_product_type", "")
                tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()

                # Delete if it's a container OR if it's a fabricante article OR if it's a comisiones (intermediario)
                if _is_container_product_type(product_type):
                    containers_to_delete.append(line_item_id)
                    logger.warning(f"Marked container line item {line_item_id} for deletion")
                elif tipo_articulo == "fabricante":
                    articles_to_delete.append(line_item_id)
                    logger.warning(f"Marked fabricante article line item {line_item_id} for deletion")
                elif tipo_articulo == "comisiones":
                    intermediarios_to_delete.append(line_item_id)
                    logger.warning(f"Marked intermediario line item {line_item_id} for deletion")

            logger.warning(f"Identified {len(containers_to_delete)} containers, {len(articles_to_delete)} articles, and {len(intermediarios_to_delete)} intermediarios to delete")

            # Step 4: BATCH DELETE line items
            all_to_delete = containers_to_delete + articles_to_delete + intermediarios_to_delete

            if all_to_delete:
                batch_delete_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"

                for i in range(0, len(all_to_delete), batch_size):
                    batch_ids = all_to_delete[i:i + batch_size]

                    batch_payload = {
                        "inputs": [{"id": str(lid)} for lid in batch_ids]
                    }

                    try:
                        batch_response = requests.post(batch_delete_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            logger.warning(f"Batch deleted {len(batch_ids)} line items")
                        else:
                            logger.error(f"Batch delete failed: {batch_response.status_code} - {batch_response.text}")
                    except Exception as e:
                        logger.error(f"Error in batch delete: {e}")

            logger.warning(f"Deleted {len(containers_to_delete)} container line items, {len(articles_to_delete)} article line items, and {len(intermediarios_to_delete)} intermediario line items")

        # Step 5: Collect all unique article SKUs needed for batch search
        unique_article_skus = set()
        for container_input in data.containers:
            for articulo in container_input.container_config.articulos:
                article_sku = str(articulo.articuloId) if articulo.articuloId is not None else None
                if article_sku and (articulo.numCajas or 0) > 0:
                    unique_article_skus.add(article_sku)

        logger.warning(f"Identified {len(unique_article_skus)} unique article SKUs to search")

        # Step 6: BATCH SEARCH all article products by SKU
        article_sku_to_product = {}

        if unique_article_skus:
            # Build batch search request (search by multiple SKUs using OR filters)
            filter_groups = []
            sku_list = list(unique_article_skus)

            # HubSpot search API doesn't support OR within a filter group directly for batch SKUs
            # We need to use multiple searches or a different approach
            # Best approach: Use batch read by SKU mapping through search

            # Alternative: Make individual searches but batch them smartly
            # For now, let's use a single search with multiple filter groups (each SKU is a filter group)
            # This creates an OR condition between filter groups

            for article_sku in sku_list[:100]:  # Limit to 100 SKUs per search
                filter_groups.append({
                    "filters": [{
                        "propertyName": "hs_sku",
                        "operator": "EQ",
                        "value": article_sku
                    }]
                })

            if filter_groups:
                try:
                    search_response = client.crm.products.search_api.do_search(
                        public_object_search_request={
                            "filterGroups": filter_groups,
                            "properties": ["hs_sku", "name", "price", "cardcode"],
                            "limit": 100
                        }
                    )

                    for product in search_response.results:
                        sku = product.properties.get("hs_sku")
                        if sku:
                            article_sku_to_product[sku] = {
                                "id": str(product.id),
                                "name": product.properties.get("name", f"Article {sku}"),
                                "price": product.properties.get("price", "0"),
                                "cardcode": product.properties.get("cardcode", "")
                            }

                    logger.warning(f"Batch search found {len(article_sku_to_product)} article products")

                except Exception as e:
                    logger.error(f"Error in batch search for articles: {e}")

            # If we have more than 100 SKUs, handle remaining ones
            if len(sku_list) > 100:
                remaining_skus = sku_list[100:]
                logger.warning(f"Searching remaining {len(remaining_skus)} article SKUs...")

                for article_sku in remaining_skus:
                    try:
                        search_response = client.crm.products.search_api.do_search(
                            public_object_search_request={
                                "filterGroups": [{
                                    "filters": [{
                                        "propertyName": "hs_sku",
                                        "operator": "EQ",
                                        "value": article_sku
                                    }]
                                }],
                                "properties": ["hs_sku", "name", "price", "cardcode"],
                                "limit": 1
                            }
                        )

                        if search_response.results:
                            product = search_response.results[0]
                            article_sku_to_product[article_sku] = {
                                "id": str(product.id),
                                "name": product.properties.get("name", f"Article {article_sku}"),
                                "price": product.properties.get("price", "0"),
                                "cardcode": product.properties.get("cardcode", "")
                            }
                    except Exception as e:
                        logger.error(f"Error searching for article SKU {article_sku}: {e}")

        # Step 7: Build line items in memory (to be created in batch)
        container_line_items_to_create = []
        article_line_items_to_create = []
        container_number = 1  # Sequential container numbering

        for container_input in data.containers:
            try:
                product_info = tipo_to_product_id[container_input.tipo]

                # Convert container_config to dict for JSON serialization
                container_config_dict = container_input.container_config.model_dump(by_alias=True)

                # Create individual line items for each container (quantity=1 each)
                for _ in range(container_input.cantidad):
                    # Build container line item data
                    container_name = f"{deal_id}-{container_number:03d}"

                    container_properties = {
                        "hs_product_id": product_info["id"],
                        "hs_sku": product_info["sku"],
                        "name": container_name,
                        "price": product_info["price"],
                        "quantity": "1",
                        "hs_product_type": "container",
                        "numero_container": container_name,
                        "container_config": json.dumps(container_config_dict)
                    }

                    # Add almacen if available
                    if deal_almacen:
                        container_properties["almacen"] = str(deal_almacen)

                    container_line_item_data = {
                        "properties": container_properties,
                        "associations": [{
                            "to": {"id": deal_id},
                            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                        }]
                    }

                    container_line_items_to_create.append(container_line_item_data)
                    logger.warning(f"Prepared container #{container_number}: {container_name} ({container_input.tipo})")

                    # Build article line items for this container
                    for articulo in container_input.container_config.articulos:
                        article_sku = str(articulo.articuloId) if articulo.articuloId is not None else None

                        if not article_sku:
                            logger.warning(f"Skipping article with no SKU in container #{container_number}")
                            continue

                        num_cajas = articulo.numCajas or 0
                        if num_cajas <= 0:
                            logger.warning(f"Skipping article {article_sku} with 0 boxes in container #{container_number}")
                            continue

                        # Get product info from our batch search results
                        article_product_info = article_sku_to_product.get(article_sku)

                        if article_product_info:
                            article_name = article_product_info["name"]
                            unique_article_name = f"{container_name} - {article_name}"

                            # Determine cardcode: First try fabricante from deal, fallback to product's cardcode
                            article_cardcode = fabricante_cardcode or article_product_info.get("cardcode", "")
                            cardcode_source = "fabricante del deal" if fabricante_cardcode else "proveedor habitual del producto"

                            # Store original price as precio_de_coste, leave price empty (will be set in negotiation)
                            article_line_item_properties = {
                                "hs_product_id": article_product_info["id"],
                                "hs_sku": article_sku,
                                "name": unique_article_name,
                                "precio_de_coste": article_product_info["price"],
                                "price": "",
                                "quantity": str(num_cajas),
                                "cardcode": article_cardcode,
                                "tipo_articulo": "fabricante",
                                "hs_product_type": "Articulo",
                                "numero_container": container_name,
                                "tipo_iva_repercutido": tipo_iva_repercutido
                            }

                            # Add almacen if available
                            if deal_almacen:
                                article_line_item_properties["almacen"] = str(deal_almacen)

                            article_line_item_data = {
                                "properties": article_line_item_properties,
                                "associations": [{
                                    "to": {"id": deal_id},
                                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                                }]
                            }

                            article_line_items_to_create.append(article_line_item_data)
                            logger.warning(f"Prepared article line item for {article_name} (x{num_cajas}) in container #{container_number} with IVA type: {tipo_iva_repercutido}, cardcode: {article_cardcode} (from {cardcode_source})")
                        else:
                            logger.warning(f"Article product with SKU '{article_sku}' not found in batch search, skipping")

                    container_number += 1

            except Exception as e:
                logger.error(f"Error preparing line items for tipo {container_input.tipo}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error preparing line items: {str(e)}"
                )

        # Step 7.5: Create INTERMEDIARIO line items if applicable
        intermediario_line_items_to_create = []

        try:
            # Fetch deal properties for intermediario
            deal = client.crm.deals.basic_api.get_by_id(
                deal_id,
                properties=["repercutir_a_cliente", "tipo_comision", "importe_comision", "codigo_intermediario"]
            )

            repercutir_a_cliente = (deal.properties.get("repercutir_a_cliente") or "").strip().lower()
            tipo_comision = (deal.properties.get("tipo_comision") or "").strip().lower()
            importe_comision = float(deal.properties.get("importe_comision") or 0)
            codigo_intermediario = (deal.properties.get("codigo_intermediario") or "").strip()

            logger.info(f"[intermediario] repercutir_a_cliente: {repercutir_a_cliente}, tipo_comision: {tipo_comision}, importe: {importe_comision}, codigo_intermediario: {codigo_intermediario}")

            # Only create intermediario line items if repercutir_a_cliente is "si" and importe > 0
            if repercutir_a_cliente == "si" and importe_comision > 0:
                logger.info(f"[intermediario] Creating intermediario line items")

                # Search for INTERMEDIARIO product (tipo_articulo = "comisiones")
                try:
                    search_response = client.crm.products.search_api.do_search(
                        public_object_search_request={
                            "filterGroups": [{
                                "filters": [{
                                    "propertyName": "tipo_articulo",
                                    "operator": "EQ",
                                    "value": "comisiones"
                                }]
                            }],
                            "properties": ["name", "hs_sku", "price"],
                            "limit": 1
                        }
                    )

                    if search_response.results and len(search_response.results) > 0:
                        intermediario_product = search_response.results[0]
                        intermediario_product_id = str(intermediario_product.id)
                        intermediario_sku = intermediario_product.properties.get("hs_sku", "INTERMEDIARIO")
                        intermediario_name = intermediario_product.properties.get("name", "INTERMEDIARIO")

                        logger.info(f"[intermediario] Found product: ID={intermediario_product_id}, name={intermediario_name}")

                        num_contenedores = len(container_line_items_to_create)

                        # Calculate commission per container based on tipo_comision
                        if tipo_comision == "importe_por_negocio":
                            # Divide total commission by number of containers
                            comision_por_contenedor = importe_comision / num_contenedores
                            logger.info(f"[intermediario] Importe por negocio: {importe_comision} / {num_contenedores} = {comision_por_contenedor}€/container")

                            # Create line items uniformly
                            container_idx = 0
                            for container_input in data.containers:
                                for _ in range(container_input.cantidad):
                                    container_name = f"{deal_id}-{container_idx + 1:03d}"

                                    intermediario_properties = {
                                        "hs_product_id": intermediario_product_id,
                                        "hs_sku": intermediario_sku,
                                        "name": f"{container_name} - {intermediario_name}",
                                        "price": str(comision_por_contenedor),
                                        "precio_de_coste": str(comision_por_contenedor),
                                        "quantity": "1",
                                        "cardcode": codigo_intermediario or "",
                                        "numero_container": container_name,
                                        "tipo_iva_repercutido": tipo_iva_repercutido,
                                        "tipo_articulo": "comisiones",
                                        "hs_product_type": "Servicio"
                                    }

                                    # Add almacen if available
                                    if deal_almacen:
                                        intermediario_properties["almacen"] = str(deal_almacen)

                                    intermediario_line_item_data = {
                                        "properties": intermediario_properties,
                                        "associations": [{
                                            "to": {"id": deal_id},
                                            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                                        }]
                                    }

                                    intermediario_line_items_to_create.append(intermediario_line_item_data)
                                    container_idx += 1

                        elif tipo_comision == "importe_por_contenedor":
                            # Direct amount per container
                            comision_por_contenedor = importe_comision
                            logger.info(f"[intermediario] Importe por contenedor: {comision_por_contenedor}€/container")

                            container_idx = 0
                            for container_input in data.containers:
                                for _ in range(container_input.cantidad):
                                    container_name = f"{deal_id}-{container_idx + 1:03d}"

                                    intermediario_properties = {
                                        "hs_product_id": intermediario_product_id,
                                        "hs_sku": intermediario_sku,
                                        "name": f"{container_name} - {intermediario_name}",
                                        "price": str(comision_por_contenedor),
                                        "precio_de_coste": str(comision_por_contenedor),
                                        "quantity": "1",
                                        "cardcode": codigo_intermediario or "",
                                        "numero_container": container_name,
                                        "tipo_iva_repercutido": tipo_iva_repercutido,
                                        "tipo_articulo": "comisiones",
                                        "hs_product_type": "Servicio"
                                    }

                                    # Add almacen if available
                                    if deal_almacen:
                                        intermediario_properties["almacen"] = str(deal_almacen)

                                    intermediario_line_item_data = {
                                        "properties": intermediario_properties,
                                        "associations": [{
                                            "to": {"id": deal_id},
                                            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                                        }]
                                    }

                                    intermediario_line_items_to_create.append(intermediario_line_item_data)
                                    container_idx += 1

                        elif tipo_comision == "importe_por_cajas":
                            # Calculate based on total boxes per container (from container_config)
                            logger.info(f"[intermediario] Importe por cajas: {importe_comision}€/caja")

                            container_idx = 0
                            for container_input in data.containers:
                                for _ in range(container_input.cantidad):
                                    # Calculate total boxes in this container
                                    total_cajas = sum(
                                        articulo.numCajas or 0
                                        for articulo in container_input.container_config.articulos
                                    )

                                    comision_por_contenedor = importe_comision * total_cajas
                                    container_name = f"{deal_id}-{container_idx + 1:03d}"

                                    intermediario_properties = {
                                        "hs_product_id": intermediario_product_id,
                                        "hs_sku": intermediario_sku,
                                        "name": f"{container_name} - {intermediario_name}",
                                        "price": str(comision_por_contenedor),
                                        "precio_de_coste": str(comision_por_contenedor),
                                        "quantity": "1",
                                        "cardcode": codigo_intermediario or "",
                                        "numero_container": container_name,
                                        "tipo_iva_repercutido": tipo_iva_repercutido,
                                        "tipo_articulo": "comisiones",
                                        "hs_product_type": "Servicio"
                                    }

                                    # Add almacen if available
                                    if deal_almacen:
                                        intermediario_properties["almacen"] = str(deal_almacen)

                                    intermediario_line_item_data = {
                                        "properties": intermediario_properties,
                                        "associations": [{
                                            "to": {"id": deal_id},
                                            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                                        }]
                                    }

                                    intermediario_line_items_to_create.append(intermediario_line_item_data)
                                    logger.info(f"[intermediario] Container {container_name}: {total_cajas} cajas × {importe_comision}€ = {comision_por_contenedor}€")

                                    container_idx += 1

                        else:
                            logger.warning(f"[intermediario] Unknown tipo_comision: {tipo_comision}")

                        logger.info(f"[intermediario] Prepared {len(intermediario_line_items_to_create)} intermediario line items")

                    else:
                        logger.warning("[intermediario] No product found with tipo_articulo='comisiones'. Please create one in HubSpot.")

                except Exception as e:
                    logger.error(f"[intermediario] Error searching for intermediario product: {e}")

            else:
                logger.info(f"[intermediario] Skipping - repercutir_a_cliente={repercutir_a_cliente}, importe={importe_comision}")

        except Exception as e:
            logger.error(f"[intermediario] Error creating intermediario line items: {e}")

        # Step 8: BATCH CREATE all line items (containers + articles + intermediario)
        all_line_items_to_create = container_line_items_to_create + article_line_items_to_create + intermediario_line_items_to_create
        logger.warning(f"Prepared {len(container_line_items_to_create)} container line items, {len(article_line_items_to_create)} article line items, and {len(intermediario_line_items_to_create)} intermediario line items")

        container_line_items_created = []
        article_line_items_created = []

        if all_line_items_to_create:
            batch_create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"
            batch_size = 100

            for i in range(0, len(all_line_items_to_create), batch_size):
                batch_items = all_line_items_to_create[i:i + batch_size]

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

        return BaseResponse(
            success=True,
            error=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_deal_containers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/transit-options", response_model=DealTransitResponse)
async def get_deal_transit_options(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Get transit/route options for a deal.

    This endpoint retrieves:
    - lugar_carga: Loading location from deal (readonly, set in ArticulosTab)
      Initial value: deal.lugar_carga, fallback to company.lugar_de_la_carga_envio
    - lugar_carga_label: Human-readable label for lugar_carga
    - puerto_salida: Departure port with options
      Initial value: deal.puerto_salida, fallback to company.puerto_salida
    - puerto_destino: Destination port with options
      Initial value: deal.puerto_destino, fallback to company.puerto_destino
    - localidad_destino: Destination locality/city
      Initial value: deal.lugar_carga_envio, fallback to company.lugar_carga_envio_entrega

    Args:
        deal_id: HubSpot deal ID.

    Returns:
        DealTransitResponse with transit/route information.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        logger.warning(f"Fetching transit data for deal {deal_id}")

        # Step 1: Get deal properties
        deal_properties = [
            'lugar_carga',
            'puerto_salida',
            'puerto_destino',
            'lugar_carga_envio'  # For localidad_destino
        ]

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=deal_properties,
            associations=['companies']
        )

        deal_lugar_carga = deal.properties.get('lugar_carga')
        deal_puerto_salida = deal.properties.get('puerto_salida')
        deal_puerto_destino = deal.properties.get('puerto_destino')
        deal_localidad_destino = deal.properties.get('lugar_carga_envio')

        logger.info(f"Deal values - lugar_carga: {deal_lugar_carga}, puerto_salida: {deal_puerto_salida}, puerto_destino: {deal_puerto_destino}, lugar_carga_envio: {deal_localidad_destino}")

        # Step 2: Get associated company for fallback values
        company_lugar_carga = None
        company_puerto_salida = None
        company_puerto_destino = None
        company_localidad_destino = None

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
                        'lugar_carga_envio_entrega'
                    ]
                )
                company_lugar_carga = company.properties.get('lugar_de_la_carga_envio')
                company_puerto_salida = company.properties.get('puerto_salida')
                company_puerto_destino = company.properties.get('puerto_destino')
                company_localidad_destino = company.properties.get('lugar_carga_envio_entrega')
                logger.info(f"Company values - lugar_de_la_carga_envio: {company_lugar_carga}, puerto_salida: {company_puerto_salida}, puerto_destino: {company_puerto_destino}, lugar_carga_envio_entrega: {company_localidad_destino}")
        except Exception as e:
            logger.warning(f"Error fetching company data: {e}")

        # Step 3: Determine final values with fallbacks
        # lugar_carga: deal.lugar_carga, fallback to company.lugar_de_la_carga_envio
        final_lugar_carga = deal_lugar_carga or company_lugar_carga

        # puerto_salida: deal.puerto_salida, fallback to company.puerto_salida
        final_puerto_salida = deal_puerto_salida or company_puerto_salida

        # puerto_destino: deal.puerto_destino, fallback to company.puerto_destino
        final_puerto_destino = deal_puerto_destino or company_puerto_destino

        # localidad_destino: deal.lugar_carga_envio, fallback to company.lugar_carga_envio_entrega
        final_localidad_destino = deal_localidad_destino or company_localidad_destino

        logger.info(f"Final values - lugar_carga: {final_lugar_carga}, puerto_salida: {final_puerto_salida}, puerto_destino: {final_puerto_destino}, localidad_destino: {final_localidad_destino}")

        # Step 4: Get lugar_carga options to map value to label
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
                # Create a mapping of value -> label
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
                logger.info(f"[transit] Loaded {len(lugar_carga_options)} lugar_carga options from custom object")
        except Exception as e:
            logger.warning(f"[transit] Error fetching lugar_carga options: {e}")

        # Map lugar_carga value to label
        if final_lugar_carga:
            # Special case for almacen_proex (dynamically added option)
            if final_lugar_carga == "almacen_proex":
                lugar_carga_label = "Almacen PROEX"
                logger.info(f"[transit] Special case: lugar_carga is almacen_proex")
            else:
                # Look up in options dictionary
                lugar_carga_label = lugar_carga_options.get(final_lugar_carga, final_lugar_carga)
                if lugar_carga_label != final_lugar_carga:
                    logger.info(f"[transit] Mapped lugar_carga '{final_lugar_carga}' -> '{lugar_carga_label}'")
                else:
                    logger.warning(f"[transit] No label found for lugar_carga value: '{final_lugar_carga}', using value as label")

        logger.info(f"[transit] Final lugar_carga_label: {lugar_carga_label}")

        # Step 5: Get puerto options
        puerto_salida_options = get_property_options('puerto_salida', 'deals')
        puerto_destino_options = get_property_options('puerto_destino', 'deals')

        # Step 6: Get localidad_destino options (same as lugar_carga from custom object)
        # Convert lugar_carga_options dict to list of PropertyOption objects
        localidad_destino_options_list = []
        for value, label in lugar_carga_options.items():
            localidad_destino_options_list.append(PropertyOption(label=label, value=value))

        # Add special almacen_proex option if not already in list
        if not any(opt.value == "almacen_proex" for opt in localidad_destino_options_list):
            localidad_destino_options_list.insert(0, PropertyOption(label="Almacen PROEX", value="almacen_proex"))

        logger.info(f"[transit] Built {len(localidad_destino_options_list)} localidad_destino options")

        # Step 7: Get almacen options from deal property definition
        almacen_options = get_property_options('almacen', 'deals')
        logger.info(f"[transit] Loaded {len(almacen_options)} almacen options")

        # Step 8: Build response
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
    """Search for commission providers (tipo_proveedor = COMISIONES).

    This endpoint searches the custom object Proveedores (ID: 2-43108800)
    for providers with tipo_proveedor = "COMISIONES".

    Args:
        q: Search query string (searches in provider name)

    Returns:
        List of commission providers with their properties

    Raises:
        HTTPException: If API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Custom object ID for Proveedores
        CUSTOM_OBJECT_PROVEEDORES = "2-43108800"

        # Step 1: Build search request
        # We need to filter by tipo_proveedor = "comisiones" and optionally by name
        filters = []

        # Filter by tipo_proveedor = "comisiones"
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

        # Step 2: Execute search
        search_results = client.crm.objects.search_api.do_search(
            object_type=CUSTOM_OBJECT_PROVEEDORES,
            public_object_search_request=search_request
        )

        # Step 3: Build response
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
    """Get transit data for a deal.

    This endpoint retrieves the following deal properties:
    - puerto_salida: Departure port
    - puerto_destino: Destination port
    - lugar_carga_envio: Destination locality/city (maps to localidad_destino)
    - naviera: Shipping line/carrier
    - dias_libres: Free days at destination port
    - transitario: Freight forwarder name
    - precio_ruta_hasta_puerto_de_salida: Price for route to departure port (EUR)
    - precio_ruta_hasta_puerto_destino: Price for route to destination port (EUR)
    - fecha_caducidad: Quote expiration date (timestamp milliseconds)

    Args:
        deal_id: HubSpot deal ID.

    Returns:
        DealTransitDataResponse with transit data.

    Raises:
        HTTPException: If deal not found or retrieval fails.
    """
    try:
        client = hubspot_client.get_client()

        # Fetch deal with transit properties
        properties = [
            "puerto_salida",
            "puerto_destino",
            "lugar_carga_envio",
            "naviera",
            "dias_libres",
            "transitario",
            "precio_ruta_hasta_puerto_de_salida",
            "precio_ruta_hasta_puerto_destino",
            "fecha_caducidad",
            "cotizacion_rapida",
            "almacen"
        ]

        deal = client.crm.deals.basic_api.get_by_id(
            deal_id=deal_id,
            properties=properties
        )

        # Parse properties
        props = deal.properties

        # Helper to parse fecha_caducidad (can be timestamp or date string)
        def parse_fecha_caducidad(value):
            if not value:
                return None
            try:
                # Try to parse as int (timestamp)
                return int(value)
            except (ValueError, TypeError):
                # Try to parse as date string (YYYY-MM-DD)
                try:
                    from datetime import datetime, timezone
                    dt = datetime.strptime(str(value), "%Y-%m-%d")
                    # Convert to UTC midnight timestamp in milliseconds
                    dt_utc = dt.replace(tzinfo=timezone.utc)
                    return int(dt_utc.timestamp() * 1000)
                except Exception as e:
                    logger.warning(f"Could not parse fecha_caducidad value '{value}': {e}")
                    return None

        # Get transitario cardcode from deal
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

        # Build response data
        data = DealTransitUpdateRequest(
            puerto_salida=props.get("puerto_salida") or None,
            puerto_destino=props.get("puerto_destino") or None,
            localidad_destino=props.get("lugar_carga_envio") or None,
            naviera=props.get("naviera") or None,
            dias_libres=int(props.get("dias_libres")) if props.get("dias_libres") else None,
            transitario=transitario_cardcode,
            transitario_name=transitario_name,
            precio_ruta_hasta_puerto_de_salida=float(props.get("precio_ruta_hasta_puerto_de_salida")) if props.get("precio_ruta_hasta_puerto_de_salida") else None,
            precio_ruta_hasta_puerto_destino=float(props.get("precio_ruta_hasta_puerto_destino")) if props.get("precio_ruta_hasta_puerto_destino") else None,
            fecha_caducidad=parse_fecha_caducidad(props.get("fecha_caducidad")),
            cotizacion_rapida=props.get("cotizacion_rapida") or None,
            almacen=props.get("almacen") or None
        )

        # Add transitario details to response
        response_data = {
            "success": True,
            "error": None,
            "data": data.dict()
        }

        return response_data

    except Exception as e:
        logger.error(f"Error retrieving transit data for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

    Args:
        client: HubSpot client instance
        tipo_ruta: "terrestre" o "maritima"
        transitario: Cardcode del transitario
        lugar_carga: Lugar de carga (solo terrestre)
        puerto_salida: Puerto de salida
        puerto_destino: Puerto de destino (solo marítima)
        naviera: Naviera (opcional, solo marítima)
        tipo_contenedor: Tipo de contenedor

    Returns:
        True si la ruta existe y NO está caducada, False en caso contrario
    """
    try:
        CUSTOM_OBJECT_RUTAS = "2-53295516"
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Normalize naviera: "SIN ESPECIFICAR" -> None
        naviera_normalizada = None
        if naviera and naviera.strip().upper() != "SIN ESPECIFICAR":
            naviera_normalizada = naviera

        # Build filters based on route type
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
    localidad_destino: str = None
) -> Optional[str]:
    """Crea una nueva ruta terrestre o marítima en HubSpot.

    Args:
        client: HubSpot client instance
        tipo_ruta: "terrestre" o "maritima"
        transitario: Cardcode del transitario
        tipo_contenedor: Tipo de contenedor
        importe: Precio de la ruta
        fecha_caducidad: Fecha de caducidad (timestamp milliseconds)
        lugar_carga: Lugar de carga (solo terrestre)
        puerto_salida: Puerto de salida
        puerto_destino: Puerto de destino (solo marítima)
        naviera: Naviera (opcional, solo marítima)
        localidad_destino: Localidad de destino (opcional)

    Returns:
        ID de la ruta creada o None si falla
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

        # Build properties
        properties = {
            "transitario": transitario or "",
            "tipo_contenedor": tipo_contenedor or "",
            "importe": str(importe),
            "fecha_caducidad": str(fecha_caducidad),
            "referencia": referencia
        }

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


@router.post("/{deal_id}/transit", response_model=BaseResponse)
async def update_deal_transit(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    ),
    transit_data: DealTransitUpdateRequest = None
):
    """Update transit data for a deal and manage FLETES line items per container.

    This endpoint updates the following deal properties:
    - puerto_salida: Departure port
    - puerto_destino: Destination port
    - localidad_destino: Destination locality/city
    - naviera: Shipping line/carrier
    - dias_libres: Free days at destination port
    - transitario: Freight forwarder (HubSpot ID)
    - precio_ruta_hasta_puerto_de_salida: Price for route to departure port (EUR)
    - precio_ruta_hasta_puerto_destino: Price for route to destination port (EUR)
    - fecha_caducidad: Quote expiration date (timestamp milliseconds)

    Additionally, if prices are provided, this endpoint:
    1. Fetches all containers associated with the deal
    2. Searches for existing FLETES line items and deletes them
    3. Searches for the FLETES product (name="FLETES", tipo_articulo="transitario")
    4. Determines IVA type (tipo_iva_repercutido) based on company residence
    5. Creates FLETES line items (one per container) with:
       - name: "{container_name} - FLETES"
       - price: (precioOrigen + precioDestino) / num_containers
       - numero_container: links to container
       - tipo_iva_repercutido: IVA type

    Args:
        deal_id: HubSpot deal ID.
        transit_data: Transit data to update.

    Returns:
        BaseResponse with success status.

    Raises:
        HTTPException: If deal not found or update fails.
    """
    try:
        client = hubspot_client.get_client()

        logger.info(f"Updating transit data for deal {deal_id} with data: {transit_data}")

        # Step 0: Get deal properties needed for route creation
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=["tipo_contenedor", "lugar_carga", "almacen"]
        )
        tipo_contenedor = deal.properties.get("tipo_contenedor", "")
        lugar_carga = deal.properties.get("lugar_carga", "")
        deal_almacen = deal.properties.get("almacen")

        logger.info(f"[rutas] Deal tipo_contenedor: {tipo_contenedor}, lugar_carga: {lugar_carga}")

        # Build properties dict with only non-None values
        properties = {}

        if transit_data.puerto_salida is not None:
            properties["puerto_salida"] = transit_data.puerto_salida

        if transit_data.puerto_destino is not None:
            properties["puerto_destino"] = transit_data.puerto_destino

        if transit_data.localidad_destino is not None:
            properties["lugar_carga_envio"] = transit_data.localidad_destino

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

        if transit_data.fecha_caducidad is not None:
            # HubSpot requires date properties to be at midnight UTC
            # Convert timestamp to midnight UTC
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(transit_data.fecha_caducidad / 1000, tz=timezone.utc)
            # Set to midnight UTC
            dt_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            # Convert back to milliseconds timestamp
            properties["fecha_caducidad"] = str(int(dt_midnight.timestamp() * 1000))

        if transit_data.cotizacion_rapida is not None:
            properties["cotizacion_rapida"] = transit_data.cotizacion_rapida

        if transit_data.almacen is not None:
            properties["almacen"] = transit_data.almacen

        logger.info(f"Updating deal {deal_id} with properties: {properties}")

        # Update deal with properties
        if properties:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": properties}
            )
            logger.info(f"Successfully updated transit data for deal {deal_id}")
        else:
            logger.warning(f"No properties to update for deal {deal_id}")

        # Manage transitario provider association
        if transit_data.transitario is not None:
            transitario_cardcode = transit_data.transitario.strip()

            try:
                CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
                access_token = client.access_token
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

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

            except Exception as e:
                logger.error(f"[transit] Error managing transitario provider association: {e}")

        # Manage FLETES line items (one per container) if prices are provided
        if (transit_data.precio_ruta_hasta_puerto_de_salida is not None or
            transit_data.precio_ruta_hasta_puerto_destino is not None):

            logger.info(f"Managing FLETES line items for deal {deal_id}")

            # Calculate total price (precioOrigen + precioDestino)
            precio_origen = transit_data.precio_ruta_hasta_puerto_de_salida or 0
            precio_destino = transit_data.precio_ruta_hasta_puerto_destino or 0
            precio_total_fletes = precio_origen + precio_destino

            logger.info(f"FLETES total price: {precio_origen} + {precio_destino} = {precio_total_fletes}")

            # Step 1: Find existing FLETES line items and containers
            existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
            logger.info(f"Found {len(existing_line_item_ids)} existing line items")

            fletes_line_item_ids_to_delete = []
            container_line_items = []
            tipo_iva_repercutido = "R21"  # Default

            if existing_line_item_ids:
                # BATCH READ all line items to find FLETES items and containers
                access_token = client.access_token
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
                batch_size = 100

                for i in range(0, len(existing_line_item_ids), batch_size):
                    batch_ids = existing_line_item_ids[i:i + batch_size]
                    batch_payload = {
                        "properties": ["name", "hs_product_type", "numero_container"],
                        "inputs": [{"id": str(lid)} for lid in batch_ids]
                    }

                    try:
                        batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                        if batch_response.ok:
                            batch_data = batch_response.json()
                            for item in batch_data.get("results", []):
                                props = item.get("properties", {})
                                line_item_name = (props.get("name") or "").strip()
                                product_type = (props.get("hs_product_type") or "").strip().lower()

                                # Collect FLETES items for deletion
                                if line_item_name.endswith("- FLETES") or line_item_name == "FLETES":
                                    fletes_line_item_ids_to_delete.append(item.get("id"))

                                # Collect containers
                                if _is_container_product_type(product_type):
                                    container_line_items.append({
                                        "id": item.get("id"),
                                        "name": props.get("name", ""),
                                        "numero_container": props.get("numero_container", "")
                                    })

                            logger.info(f"Batch read {len(batch_data.get('results', []))} line items")
                        else:
                            logger.error(f"Batch read failed: {batch_response.status_code} - {batch_response.text}")
                    except Exception as e:
                        logger.error(f"Error in batch read line items: {e}")

                logger.info(f"Found {len(fletes_line_item_ids_to_delete)} FLETES line items to delete")
                logger.info(f"Found {len(container_line_items)} containers")

                # BATCH DELETE FLETES line items
                if fletes_line_item_ids_to_delete:
                    batch_delete_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
                    for i in range(0, len(fletes_line_item_ids_to_delete), batch_size):
                        batch_ids = fletes_line_item_ids_to_delete[i:i + batch_size]
                        batch_payload = {
                            "inputs": [{"id": str(lid)} for lid in batch_ids]
                        }

                        try:
                            batch_response = requests.post(batch_delete_url, headers=headers, json=batch_payload, timeout=30)
                            if batch_response.ok:
                                logger.info(f"Batch deleted {len(batch_ids)} FLETES line items")
                            else:
                                logger.error(f"Batch delete failed: {batch_response.status_code} - {batch_response.text}")
                        except Exception as e:
                            logger.error(f"Error in batch delete line items: {e}")

            # Determine IVA type: use from frontend if provided, otherwise calculate from company residence
            if transit_data.tipo_iva_fletes:
                tipo_iva_repercutido = transit_data.tipo_iva_fletes
                logger.info(f"[transit] Using IVA type from frontend: {tipo_iva_repercutido}")
            else:
                # Get company residence to determine IVA type
                try:
                    company_ids = get_associated_companies(deal_id)
                    if company_ids:
                        company_id = company_ids[0]
                        company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                        company_response = requests.get(
                            company_url,
                            headers=headers,
                            params={"properties": "residennum"},
                            timeout=10
                        )

                        if company_response.ok:
                            residennum = (company_response.json()
                                         .get("properties", {})
                                         .get("residennum", ""))

                            tipo_iva_repercutido = compute_tipo_iva_repercutido(residennum)
                            logger.info(f"[transit] Company residence: {residennum or 'NACIONAL'}, IVA type: {tipo_iva_repercutido}")
                except Exception as e:
                    logger.error(f"[transit] Error fetching company residence: {e}")

            # Step 2: Search for FLETES product (tipo_articulo = "transitario", name = "FLETES")
            try:
                logger.info("Searching for FLETES product")
                search_response = client.crm.products.search_api.do_search(
                    public_object_search_request={
                        "filterGroups": [
                            {
                                "filters": [
                                    {
                                        "propertyName": "name",
                                        "operator": "EQ",
                                        "value": "FLETES"
                                    },
                                    {
                                        "propertyName": "tipo_articulo",
                                        "operator": "EQ",
                                        "value": "transitario"
                                    }
                                ]
                            }
                        ],
                        "properties": ["name", "hs_sku", "price"],
                        "limit": 1
                    }
                )

                if search_response.results and len(search_response.results) > 0:
                    fletes_product = search_response.results[0]
                    fletes_product_id = str(fletes_product.id)
                    fletes_sku = fletes_product.properties.get("hs_sku", "FLETES")

                    logger.info(f"Found FLETES product: ID={fletes_product_id}, SKU={fletes_sku}")

                    # Step 3: Create FLETES line items (one per container)
                    if not container_line_items:
                        logger.warning("No containers found - creating single FLETES line item")
                        # Fallback: create single FLETES line item
                        fletes_line_item_id = create_line_item_with_association(
                            client=client,
                            deal_id=deal_id,
                            product_id=fletes_product_id,
                            sku=fletes_sku,
                            product_name="FLETES",
                            product_price=str(precio_total_fletes),
                            quantity="1",
                            cardcode=transit_data.transitario,
                            almacen=deal_almacen,
                            tipo_iva_repercutido=tipo_iva_repercutido,
                            tipo_articulo="transitario",
                            hs_product_type="Flete"
                        )

                        if fletes_line_item_id:
                            logger.info(f"Successfully created FLETES line item {fletes_line_item_id} with price {precio_total_fletes}")
                    else:
                        # Use total price per container (precio_origen + precio_destino)
                        num_containers = len(container_line_items)
                        precio_por_contenedor = precio_total_fletes

                        logger.info(f"Creating {num_containers} FLETES line items at {precio_por_contenedor}€ each (total per container)")

                        # Prepare line items for batch creation
                        fletes_line_items_to_create = []

                        for container in container_line_items:
                            fletes_properties = {
                                "hs_product_id": fletes_product_id,
                                "hs_sku": fletes_sku,
                                "name": f"{container['name']} - FLETES",
                                "price": str(precio_por_contenedor),
                                "precio_de_coste": str(precio_por_contenedor),
                                "quantity": "1",
                                "cardcode": transit_data.transitario or "",
                                "numero_container": container["numero_container"],
                                "tipo_iva_repercutido": tipo_iva_repercutido,
                                "tipo_articulo": "transitario",
                                "hs_product_type": "Flete"
                            }

                            # Add almacen if available
                            if deal_almacen:
                                fletes_properties["almacen"] = str(deal_almacen)

                            line_item_data = {
                                "properties": fletes_properties,
                                "associations": [{
                                    "to": {"id": deal_id},
                                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                                }]
                            }
                            fletes_line_items_to_create.append(line_item_data)

                        # BATCH CREATE FLETES line items
                        batch_create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"

                        for i in range(0, len(fletes_line_items_to_create), batch_size):
                            batch_items = fletes_line_items_to_create[i:i + batch_size]
                            batch_payload = {"inputs": batch_items}

                            try:
                                batch_response = requests.post(batch_create_url, headers=headers, json=batch_payload, timeout=60)
                                if batch_response.ok:
                                    created_items = batch_response.json().get("results", [])
                                    logger.info(f"Batch created {len(created_items)} FLETES line items")
                                else:
                                    logger.error(f"Batch create failed: {batch_response.status_code} - {batch_response.text}")
                            except Exception as e:
                                logger.error(f"Error in batch create FLETES line items: {e}")

                else:
                    logger.warning("FLETES product not found in HubSpot. Please create a product with name='FLETES' and tipo_articulo='transitario'")

            except Exception as e:
                logger.error(f"Error managing FLETES product: {e}", exc_info=True)

        # Step 4: Verify and create routes if they don't exist
        ruta_terrestre_creada = False
        ruta_maritima_creada = False

        try:
            # Only process routes if we have the necessary data
            if transit_data.transitario and transit_data.fecha_caducidad:

                # RUTA TERRESTRE: lugar_carga + puerto_salida
                if lugar_carga and transit_data.puerto_salida and transit_data.precio_ruta_hasta_puerto_de_salida:
                    logger.info(f"[rutas] Verificando ruta terrestre: {lugar_carga} -> {transit_data.puerto_salida}")

                    ruta_terrestre_existe = _verificar_ruta_existe(
                        client=client,
                        tipo_ruta="terrestre",
                        transitario=transit_data.transitario,
                        lugar_carga=lugar_carga,
                        puerto_salida=transit_data.puerto_salida,
                        tipo_contenedor=tipo_contenedor
                    )

                    if not ruta_terrestre_existe:
                        logger.info(f"[rutas] Ruta terrestre NO existe, creando...")
                        ruta_id = _crear_ruta(
                            client=client,
                            tipo_ruta="terrestre",
                            transitario=transit_data.transitario,
                            tipo_contenedor=tipo_contenedor,
                            importe=transit_data.precio_ruta_hasta_puerto_de_salida,
                            fecha_caducidad=transit_data.fecha_caducidad,
                            lugar_carga=lugar_carga,
                            puerto_salida=transit_data.puerto_salida,
                            localidad_destino=transit_data.localidad_destino
                        )
                        if ruta_id:
                            ruta_terrestre_creada = True
                            logger.info(f"[rutas] Ruta terrestre creada: {ruta_id}")
                    else:
                        logger.info(f"[rutas] Ruta terrestre ya existe")

                # RUTA MARÍTIMA: puerto_salida + puerto_destino
                if transit_data.puerto_salida and transit_data.puerto_destino and transit_data.precio_ruta_hasta_puerto_destino:
                    logger.info(f"[rutas] Verificando ruta marítima: {transit_data.puerto_salida} -> {transit_data.puerto_destino}")

                    ruta_maritima_existe = _verificar_ruta_existe(
                        client=client,
                        tipo_ruta="maritima",
                        transitario=transit_data.transitario,
                        puerto_salida=transit_data.puerto_salida,
                        puerto_destino=transit_data.puerto_destino,
                        naviera=transit_data.naviera,
                        tipo_contenedor=tipo_contenedor
                    )

                    if not ruta_maritima_existe:
                        logger.info(f"[rutas] Ruta marítima NO existe, creando...")
                        ruta_id = _crear_ruta(
                            client=client,
                            tipo_ruta="maritima",
                            transitario=transit_data.transitario,
                            tipo_contenedor=tipo_contenedor,
                            importe=transit_data.precio_ruta_hasta_puerto_destino,
                            fecha_caducidad=transit_data.fecha_caducidad,
                            puerto_salida=transit_data.puerto_salida,
                            puerto_destino=transit_data.puerto_destino,
                            naviera=transit_data.naviera,
                            localidad_destino=transit_data.localidad_destino
                        )
                        if ruta_id:
                            ruta_maritima_creada = True
                            logger.info(f"[rutas] Ruta marítima creada: {ruta_id}")
                    else:
                        logger.info(f"[rutas] Ruta marítima ya existe")

        except Exception as e:
            logger.error(f"[rutas] Error processing routes: {e}", exc_info=True)
            # Don't fail the entire request if route creation fails

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
    """Search for transitarios (tipo_proveedor = transitario).

    This endpoint searches the custom object Proveedores (ID: 2-43108800)
    for providers with tipo_proveedor = "transitario".

    Args:
        q: Search query string (searches in provider name)

    Returns:
        List of transitarios with their properties

    Raises:
        HTTPException: If API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        # Custom object ID for Proveedores
        CUSTOM_OBJECT_PROVEEDORES = "2-43108800"

        # Build search request
        filters = []

        # Filter by tipo_proveedor = "transitario"
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

        # Execute search
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
    puerto_destino: Optional[str] = None
):
    """Get available routes for a deal from HubSpot custom object 'rutas'.

    This endpoint retrieves routes that match the deal's configuration:
    - Filters by tipo_contenedor (from deal)
    - Filters by lugar_carga, puerto_salida, puerto_destino (from deal or query params)
    - Filters by naviera if selected (from deal)
    - Filters by fecha_caducidad > today (only active routes)
    - If include_expired=true, also includes routes expired less than 60 days ago
    - Separates routes into terrestrial (lugar_carga → puerto_salida) and maritime (puerto_salida → puerto_destino)

    Args:
        deal_id: HubSpot deal ID.
        include_expired: If True, include routes expired less than 60 days ago (for quick quotes).
        puerto_salida: Optional. Override puerto_salida from deal (used when frontend select changes haven't been saved yet).
        puerto_destino: Optional. Override puerto_destino from deal (used when frontend select changes haven't been saved yet).

    Returns:
        DealRutasResponse with terrestrial and maritime routes lists.

    Raises:
        HTTPException: If deal not found or API error occurs.
    """
    try:
        from datetime import datetime, timedelta

        client = hubspot_client.get_client()
        access_token = client.access_token

        # Custom object ID for Rutas
        CUSTOM_OBJECT_RUTAS = "2-53295516"

        logger.warning(f"Fetching rutas for deal {deal_id}")

        # Step 1: Get deal properties needed for filtering
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

        # Step 2: Build filters for rutas search
        # We'll need to do 2 separate searches: one for terrestrial routes, one for maritime routes

        rutas_terrestres = []
        rutas_maritimas = []

        # Step 3: Search for terrestrial routes (lugar_carga → puerto_salida)
        # Using direct REST API for better compatibility with custom objects
        if deal_tipo_contenedor and deal_lugar_carga and effective_puerto_salida:
            try:
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"

                # Build filter payload for terrestrial routes
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

                # Parse results and get transitario IDs
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

        # Step 4: Search for maritime routes (puerto_salida → puerto_destino)
        # Using direct REST API for better compatibility with custom objects
        if deal_tipo_contenedor and effective_puerto_salida and effective_puerto_destino:
            try:
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_RUTAS}/search"

                # Build filter payload for maritime routes
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

                # Filter by date in code
                today = datetime.now().date()
                # Calculate the threshold for expired routes (60 days ago)
                threshold_date = today - timedelta(days=60) if include_expired else today

                # Parse results and get transitario IDs
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

        # Step 5: Build response
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


async def _create_seguro_line_items(client, deal_id: str, articulos_updated: list):
    """Helper function to create SEGURO (2%) line items per container.

    This function:
    1. Checks if tipo_de_venta is "CIF" or "DAP MARÍTIMO"
    2. Calculates total value per container (articles + services + intermediario + flete)
    3. Creates SEGURO line items (2% of container value)
    4. Associates seguro provider with deal

    Args:
        client: HubSpot client instance
        deal_id: Deal ID
        articulos_updated: List of updated articles with their margins
    """
    try:
        # Fetch deal properties to check tipo_de_venta
        deal = client.crm.deals.basic_api.get_by_id(
            deal_id,
            properties=["tipo_de_venta"]
        )

        tipo_de_venta = (deal.properties.get("tipo_de_venta") or "").strip().upper()
        logger.info(f"[seguro] Deal tipo_de_venta: {tipo_de_venta}")

        # Only create seguro if tipo_de_venta is "CIF" or "DAP MARÍTIMO"
        if tipo_de_venta not in ["CIF", "DAP MARÍTIMO", "DAP MARITIMO"]:
            logger.info(f"[seguro] tipo_de_venta '{tipo_de_venta}' does not require insurance, skipping")
            return

        logger.info(f"[seguro] tipo_de_venta requires insurance, proceeding to create SEGURO line items")

        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Get all line items to calculate insurance per container
        existing_line_item_ids = _get_associated_ids(deal_id, "line_items")
        logger.info(f"[seguro] Found {len(existing_line_item_ids)} existing line items")

        if not existing_line_item_ids:
            logger.warning(f"[seguro] No line items found, skipping insurance creation")
            return

        # BATCH READ all line items
        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_size = 100
        all_line_items = []

        for i in range(0, len(existing_line_item_ids), batch_size):
            batch_ids = existing_line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": ["name", "hs_product_type", "numero_container", "price", "precio_de_coste", "quantity", "margen_unitario", "tipo_articulo"],
                "inputs": [{"id": str(lid)} for lid in batch_ids]
            }

            try:
                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                if batch_response.ok:
                    all_line_items.extend(batch_response.json().get("results", []))
            except Exception as e:
                logger.error(f"[seguro] Error in batch read line items: {e}")

        logger.info(f"[seguro] Batch fetched {len(all_line_items)} line items")

        # Group line items by container and calculate total value per container
        container_values = {}  # numero_container -> {name, total_value}
        seguro_items_to_delete = []

        for line_item in all_line_items:
            props = line_item.get("properties", {})
            product_type = (props.get("hs_product_type") or "").strip().lower()
            tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()
            numero_container = props.get("numero_container")
            name = props.get("name", "")

            # Collect existing seguro items for deletion
            if tipo_articulo == "seguro":
                seguro_items_to_delete.append(line_item.get("id"))
                continue

            # Skip if no container number
            if not numero_container:
                continue

            # Initialize container if not exists
            if numero_container not in container_values:
                container_values[numero_container] = {
                    "name": "",
                    "total_value": 0
                }

            # Get container name from container line items
            if _is_container_product_type(product_type):
                container_values[numero_container]["name"] = name
                continue

            # Calculate value based on line item type
            price = float(props.get("price") or 0)
            precio_de_coste = float(props.get("precio_de_coste") or 0)
            quantity = float(props.get("quantity") or 0)

            if tipo_articulo == "fabricante":
                # Articles: Use price (negotiated price) if available, otherwise use precio_de_coste (base cost)
                if price > 0:
                    article_value = price * quantity
                    logger.info(f"[seguro] Container {numero_container} - Article: price={price} × quantity={quantity} = {article_value}")
                elif precio_de_coste > 0:
                    # Fallback for articles not yet negotiated
                    article_value = precio_de_coste * quantity
                    logger.info(f"[seguro] Container {numero_container} - Article (fallback): precio_de_coste={precio_de_coste} × quantity={quantity} = {article_value}")
                else:
                    article_value = 0
                    logger.warning(f"[seguro] Container {numero_container} - Article has no price or precio_de_coste")

                container_values[numero_container]["total_value"] += article_value

            elif tipo_articulo in ["comisiones", "transitario"]:
                # Intermediario, Flete: use price
                container_values[numero_container]["total_value"] += price
                logger.info(f"[seguro] Container {numero_container} - {tipo_articulo}: {price}")

            elif tipo_articulo not in ["seguro"]:
                # Services and other items: use price
                container_values[numero_container]["total_value"] += price
                logger.info(f"[seguro] Container {numero_container} - {tipo_articulo or 'other'}: {price}")

        logger.info(f"[seguro] Calculated values for {len(container_values)} containers")

        # Delete existing seguro line items
        if seguro_items_to_delete:
            batch_delete_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/archive"
            for i in range(0, len(seguro_items_to_delete), batch_size):
                batch_ids = seguro_items_to_delete[i:i + batch_size]
                batch_payload = {"inputs": [{"id": str(lid)} for lid in batch_ids]}

                try:
                    batch_response = requests.post(batch_delete_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        logger.info(f"[seguro] Deleted {len(batch_ids)} existing seguro line items")
                except Exception as e:
                    logger.error(f"[seguro] Error deleting seguro line items: {e}")

        # Search for SEGURO product
        search_response = client.crm.products.search_api.do_search(
            public_object_search_request={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "tipo_articulo",
                        "operator": "EQ",
                        "value": "seguro"
                    }]
                }],
                "properties": ["name", "hs_sku", "price"],
                "limit": 1
            }
        )

        if not search_response.results or len(search_response.results) == 0:
            logger.warning("[seguro] No product found with tipo_articulo='seguro'. Please create one in HubSpot.")
            return

        seguro_product = search_response.results[0]
        seguro_product_id = str(seguro_product.id)
        seguro_sku = seguro_product.properties.get("hs_sku", "SEGURO")
        seguro_name = seguro_product.properties.get("name", "SEGURO")

        logger.info(f"[seguro] Found SEGURO product: ID={seguro_product_id}, name={seguro_name}")

        # Get company residence to determine IVA type
        tipo_iva_repercutido = "R21"  # Default
        try:
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                company_url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
                company_response = requests.get(
                    company_url,
                    headers=headers,
                    params={"properties": "residennum"},
                    timeout=10
                )

                if company_response.ok:
                    residennum = (company_response.json()
                                 .get("properties", {})
                                 .get("residennum", ""))

                    tipo_iva_repercutido = compute_tipo_iva_repercutido(residennum)
                    logger.info(f"[seguro] Company residence: {residennum or 'NACIONAL'}, IVA type: {tipo_iva_repercutido}")
        except Exception as e:
            logger.error(f"[seguro] Error fetching company residence: {e}")

        # Search for seguro provider
        seguro_provider_id = None
        seguro_provider_cardcode = None
        try:
            CUSTOM_OBJECT_PROVEEDORES = "2-43108800"
            search_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/search"
            search_payload = {
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "tipo_proveedor",
                        "operator": "EQ",
                        "value": "seguro"
                    }]
                }],
                "properties": ["name", "cardcode"],
                "limit": 1
            }

            search_response = requests.post(search_url, headers=headers, json=search_payload, timeout=10)
            if search_response.ok:
                search_data = search_response.json()
                if search_data.get("results"):
                    provider = search_data["results"][0]
                    seguro_provider_id = provider["id"]
                    seguro_provider_cardcode = provider.get("properties", {}).get("cardcode", "")
                    logger.info(f"[seguro] Found seguro provider: id={seguro_provider_id}, cardcode={seguro_provider_cardcode}")
        except Exception as e:
            logger.error(f"[seguro] Error searching for seguro provider: {e}")

        # Create seguro line items (one per container)
        seguro_line_items_to_create = []

        for numero_container, container_data in container_values.items():
            container_name = container_data["name"]
            total_value = container_data["total_value"]
            seguro_value = total_value * 0.02

            logger.info(f"[seguro] Container {container_name}: total={total_value}, seguro (2%)={seguro_value}")

            line_item_data = {
                "properties": {
                    "hs_product_id": seguro_product_id,
                    "hs_sku": seguro_sku,
                    "name": f"{container_name} - {seguro_name}",
                    "price": str(seguro_value),
                    "precio_de_coste": str(seguro_value),
                    "quantity": "1",
                    "cardcode": seguro_provider_cardcode or "",
                    "numero_container": container_name,
                    "tipo_iva_repercutido": tipo_iva_repercutido
                },
                "associations": [{
                    "to": {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 20}]
                }]
            }

            seguro_line_items_to_create.append(line_item_data)

        # BATCH CREATE seguro line items
        if seguro_line_items_to_create:
            batch_create_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/create"

            for i in range(0, len(seguro_line_items_to_create), batch_size):
                batch_items = seguro_line_items_to_create[i:i + batch_size]
                batch_payload = {"inputs": batch_items}

                try:
                    batch_response = requests.post(batch_create_url, headers=headers, json=batch_payload, timeout=60)
                    if batch_response.ok:
                        created_items = batch_response.json().get("results", [])
                        logger.info(f"[seguro] Batch created {len(created_items)} seguro line items")
                    else:
                        logger.error(f"[seguro] Batch create failed: {batch_response.status_code} - {batch_response.text}")
                except Exception as e:
                    logger.error(f"[seguro] Error in batch create seguro line items: {e}")

        # Associate seguro provider with deal if found
        if seguro_provider_id:
            try:
                # Remove existing seguro provider associations
                existing_provider_ids = _get_associated_ids(deal_id, CUSTOM_OBJECT_PROVEEDORES)

                if existing_provider_ids:
                    batch_url = f"https://api.hubapi.com/crm/v3/objects/{CUSTOM_OBJECT_PROVEEDORES}/batch/read"
                    batch_payload = {
                        "properties": ["tipo_proveedor"],
                        "inputs": [{"id": str(pid)} for pid in existing_provider_ids]
                    }

                    batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                    if batch_response.ok:
                        for prov in batch_response.json().get("results", []):
                            if prov.get("properties", {}).get("tipo_proveedor") == "seguro":
                                prov_id = prov.get("id")
                                delete_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{prov_id}/associations/deals/{deal_id}"
                                requests.delete(delete_url, headers=headers, timeout=10)
                                logger.info(f"[seguro] Removed existing seguro provider association: {prov_id}")

                # Create new association (associationTypeId: 450 = "Seguro")
                assoc_url = f"https://api.hubapi.com/crm/v4/objects/{CUSTOM_OBJECT_PROVEEDORES}/{seguro_provider_id}/associations/deals/{deal_id}"
                assoc_payload = [{"associationCategory": "USER_DEFINED", "associationTypeId": 450}]

                assoc_response = requests.put(assoc_url, headers=headers, json=assoc_payload, timeout=10)

                if assoc_response.ok:
                    logger.info(f"[seguro] Successfully associated seguro provider {seguro_provider_id} with deal {deal_id} (associationTypeId: 450 - Seguro)")
                else:
                    logger.error(f"[seguro] Failed to associate provider: {assoc_response.status_code} - {assoc_response.text}")

            except Exception as e:
                logger.error(f"[seguro] Error managing seguro provider association: {e}")

    except Exception as e:
        logger.error(f"[seguro] Error in _create_seguro_line_items: {e}", exc_info=True)


@router.post("/{deal_id}/negociacion", response_model=BaseResponse)
async def update_deal_negociacion(
    data: UpdateNegociacionRequest,
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """Update negotiation data (margins and VAT types) for deal unique line items.

    This endpoint:
    1. Receives a list of unique line items with their negotiated margins and VAT types
    2. Using line item ID, updates the associated line item directly
    3. Updates the margen_unitario and tipo_iva_repercutido fields on each unique line item

    Args:
        deal_id: HubSpot deal ID.
        data: UpdateNegociacionRequest with articulos array containing:
            - line_item_id: Line Item ID (unique identifier for article in container)
            - margen_unitario: Effective margin (0-1 range, where 1 = 100%)
            - tipo_iva_repercutido: VAT type code

    Returns:
        BaseResponse indicating success or failure.

    Raises:
        HTTPException: If deal not found, line items not found, or API error occurs.
    """
    try:
        client = hubspot_client.get_client()

        logger.warning(f"[negociacion] Updating negotiation data for {len(data.articulos)} unique line items in deal {deal_id}")

        # Handle both Pydantic model and dict input (for AWS Lambda compatibility)
        if isinstance(data, dict):
            data = UpdateNegociacionRequest(**data)

        # STEP 1: Fetch all line items to get tipo_articulo for filtering
        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        line_item_ids = [articulo.line_item_id for articulo in data.articulos]
        logger.warning(f"[negociacion] Fetching {len(line_item_ids)} line items to get tipo_articulo")

        # BATCH READ line items
        batch_url = "https://api.hubapi.com/crm/v3/objects/line_items/batch/read"
        batch_size = 100
        line_items_data = {}  # line_item_id -> {tipo_articulo}

        for i in range(0, len(line_item_ids), batch_size):
            batch_ids = line_item_ids[i:i + batch_size]
            batch_payload = {
                "properties": ["tipo_articulo"],
                "inputs": [{"id": str(lid)} for lid in batch_ids]
            }

            try:
                batch_response = requests.post(batch_url, headers=headers, json=batch_payload, timeout=30)
                if batch_response.ok:
                    for item in batch_response.json().get("results", []):
                        item_id = item.get("id")
                        props = item.get("properties", {})
                        line_items_data[item_id] = {
                            "tipo_articulo": (props.get("tipo_articulo") or "").strip().lower()
                        }
                else:
                    logger.error(f"[negociacion] Batch read failed: {batch_response.status_code}")
            except Exception as e:
                logger.error(f"[negociacion] Error in batch read line items: {e}")

        logger.warning(f"[negociacion] Fetched data for {len(line_items_data)} line items")

        # STEP 2: Prepare batch update for all line items
        # This avoids rate limiting by updating all line items in a single request
        batch_updates = []

        for articulo in data.articulos:
            line_item_id = articulo.line_item_id
            price = articulo.price
            margen_unitario = articulo.margen_unitario
            tipo_iva_repercutido = articulo.tipo_iva_repercutido

            # Get line item data
            item_data = line_items_data.get(line_item_id, {})
            tipo_articulo = item_data.get("tipo_articulo", "")

            logger.warning(f"[negociacion] Preparing update for line item {line_item_id} with price={price}, margin={margen_unitario}, VAT={tipo_iva_repercutido}")

            # Build properties to update
            properties_to_update = {
                "margen_unitario": str(margen_unitario),
                "tipo_iva_repercutido": tipo_iva_repercutido
            }

            # Add price only for fabricante articles (received from frontend)
            if tipo_articulo == "fabricante":
                properties_to_update["price"] = str(price)
                logger.warning(f"[negociacion] Line item {line_item_id}: Setting price={price} (negotiated price from frontend)")

            # Add line item to batch update
            batch_updates.append({
                "id": line_item_id,
                "properties": properties_to_update
            })

        # Execute batch update if there are items to update
        updated_count = 0
        if batch_updates:
            try:
                logger.warning(f"[negociacion] Executing batch update for {len(batch_updates)} line items")

                batch_update_input = {
                    "inputs": batch_updates
                }

                batch_result = client.crm.line_items.batch_api.update(
                    batch_input_simple_public_object_batch_input=batch_update_input
                )

                updated_count = len(batch_result.results)
                logger.warning(f"[negociacion] Successfully updated {updated_count} line items")

            except Exception as e:
                error_msg = f"Error executing batch update: {e}"
                logger.error(f"[negociacion] {error_msg}")
                raise HTTPException(status_code=500, detail=error_msg)
        else:
            logger.warning(f"[negociacion] No line items to update")

        # STEP 2: Create SEGURO (2%) line items if tipo_de_venta requires it
        await _create_seguro_line_items(client, deal_id, data.articulos)

        return BaseResponse(
            success=True,
            error=None
        )

    except Exception as e:
        logger.error(f"[negociacion] Error updating negotiation data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# HUBDB TEXTOS COTIZACION ENDPOINT
# ============================================================================

def _fetch_hubdb_textos_cotizaciones(
    idioma: str,
    claves: List[str],
    grupo: Optional[str] = None,
    subgrupo: Optional[str] = None,
    access_token: Optional[str] = None
) -> dict[str, TextoCotizacionItem]:
    """
    Fetch text templates from HubDB textos_cotizaciones table.

    This function queries the HubDB v3 API to retrieve text templates based on:
    - Language (idioma)
    - Template keys (claves)
    - Optional group and subgroup filters

    Args:
        idioma: Language code (es, en, fr).
        claves: List of template keys to retrieve.
        grupo: Optional group filter.
        subgrupo: Optional subgroup filter.
        access_token: HubSpot Private App Token.

    Returns:
        Dictionary mapping template keys to TextoCotizacionItem objects.

    Raises:
        HTTPException: If HubDB API call fails.
    """

    # HubDB table name (logical name)
    table_name = "textos_cotizaciones"

    # Step 1: Get table ID by name
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

    # Step 2: Fetch rows from the table
    # HubDB API endpoint to get published rows: GET /cms/v3/hubdb/tables/{tableId}/rows
    rows_url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{table_id}/rows"

    # Build query parameters for filtering
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

    # Step 3: Filter rows by criteria and build result dictionary
    result = {}

    for row in all_rows:
        values = row.get("values", {})

        # Extract column values
        row_idioma = values.get("idioma", {}).get("name", "")
        row_clave = values.get("clave", "")
        row_grupo = values.get("grupo", "")
        row_subgrupo = values.get("subgrupo", "")
        row_texto = values.get("texto", "")

        # Apply filters:
        # 1. Must match idioma
        if row_idioma != idioma:
            continue

        # 2. Must be in the requested claves list
        if row_clave not in claves:
            continue

        # 3. Optional: filter by grupo
        if grupo is not None and row_grupo != grupo:
            continue

        # 4. Optional: filter by subgrupo
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
    """
    Get text templates from HubDB textos_cotizaciones table.

    This endpoint queries the HubDB table to retrieve text templates for:
    - Offer texts (oferta_base)
    - Payment method texts (pago_100, pago_30_70, pago_50_50)
    - Bank texts (banco_la_caixa, banco_sabadell)
    - Quick quote texts (cotizacion_rapida)

    The texts are filtered by:
    - Language (idioma): es, en, fr
    - Template keys (claves): List of specific template identifiers
    - Optional group/subgroup filters

    Args:
        data: Request with idioma, claves, and optional filters.

    Returns:
        TextosCotizacionResponse with matching text templates.

    Raises:
        HTTPException: If validation fails or HubDB API error occurs.

    Example:
        Request:
        ```json
        {
            "idioma": "es",
            "claves": ["oferta_base", "pago_30_70", "banco_la_caixa"],
            "grupo": null,
            "subgrupo": null
        }
        ```

        Response:
        ```json
        {
            "success": true,
            "error": null,
            "data": {
                "oferta_base": {
                    "grupo": "oferta",
                    "subgrupo": "condiciones",
                    "idioma": "es",
                    "clave": "oferta_base",
                    "texto": "Estimado cliente, ..."
                },
                ...
            }
        }
        ```
    """
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

        # Fetch texts from HubDB
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


def _fetch_hubdb_tipo_iva(
    filtros: List[TipoIVAFilter],
    access_token: Optional[str] = None
) -> List[TipoIVAItem]:
    """
    Fetch IVA/recargo rows from HubDB table 'tipo_de_iva_soportado'
    filtered by categoria + tipo_residencia.

    Each filter is an AND:
    - categoria == filter.categoria
    - tipo_residencia == filter.tipo_residencia

    Returns rows that match ANY of the provided filters (OR between filters).
    """

    # Get access token
    if not access_token:
        client = hubspot_client.get_client()
        access_token = client.access_token

    table_name = "tipo_de_iva_soportado"
    list_tables_url = "https://api.hubapi.com/cms/v3/hubdb/tables"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # 1. Fetch HubDB table ID
    try:
        logger.info(f"[hubdb] Fetching table ID for: {table_name}")
        tables_response = requests.get(list_tables_url, headers=headers, timeout=10)
        tables_response.raise_for_status()
        tables = tables_response.json().get("results", [])

        table_id = next((t["id"] for t in tables if t["name"] == table_name), None)

        if not table_id:
            raise HTTPException(404, f"HubDB table '{table_name}' not found")

    except requests.exceptions.RequestException as e:
        logger.error(f"[hubdb] Error fetching table list: {e}")
        raise HTTPException(502, "Error connecting to HubDB API")

    # 2. Fetch ALL rows from table
    try:
        rows_url = f"https://api.hubapi.com/cms/v3/hubdb/tables/{table_id}/rows"
        logger.info(f"[hubdb] Fetching rows for table {table_name} (ID {table_id})")

        rows_response = requests.get(rows_url, headers=headers, timeout=10)
        rows_response.raise_for_status()
        all_rows = rows_response.json().get("results", [])

    except requests.exceptions.RequestException as e:
        logger.error(f"[hubdb] Error fetching rows: {e}")
        raise HTTPException(502, "Error fetching rows from HubDB")

    # 3. FILTER rows
    result: List[TipoIVAItem] = []

    for row in all_rows:
        values = row.get("values", {})

        row_categoria = values.get("categoria", {}).get("name", "")
        row_tipo_res = values.get("tipo_residencia", {}).get("name", "")

        # Apply OR logic across filter entries
        match = any(
            f.categoria == row_categoria and f.tipo_residencia == row_tipo_res
            for f in filtros
        )

        if not match:
            continue

        # Build response item
        item = TipoIVAItem(
            code=values.get("code", ""),
            name=values.get("name", ""),
            porcentaje_iva=float(values.get("porcentaje_iva", 0)),
            porcentaje_recargo=float(values.get("porcentaje_recargo", 0)),
            categoria=row_categoria,
            tipo_residencia=row_tipo_res
        )

        result.append(item)

    logger.info(f"[hubdb] Found {len(result)} IVA rows matching filters")
    return result

@router.post("/tipo-iva", response_model=TipoIVAResponse)
async def get_tipo_iva(data: TipoIVARequest):
    """
    Query HubDB table 'tipo_de_iva_soportado' and return IVA rows matching
    any of the filter entries (categoria + tipo_residencia).

    Example request:
    {
      "filtros": [
        { "categoria": "soportado", "tipo_residencia": "NACIONAL" },
        { "categoria": "repercutido", "tipo_residencia": "EUROPA" }
      ]
    }
    """
    try:
        if not data.filtros:
            raise HTTPException(400, "Debe incluir al menos un filtro")

        client = hubspot_client.get_client()
        access_token = client.access_token

        iva_rows = _fetch_hubdb_tipo_iva(
            filtros=data.filtros,
            access_token=access_token
        )

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

@router.post("/{deal_id}/publish", response_model=PublishQuoteResponse)
async def publish_quote(
    deal_id: str = Path(..., description="HubSpot Deal ID"),
    data: PublishQuoteRequest = None
):
    """
    Publish a quote in draft mode.

    This endpoint:
    1. Stores the complete quote payload in the Deal property 'quote_data_payload'
    2. Creates a NEW Quote object in HubSpot with status DRAFT (always creates new)
    3. Associates the Quote with:
       - The Deal
       - The custom quote template (QUOTE_TEMPLATE_ID)
    4. Returns success confirmation with quote ID

    The complete payload structure includes:
    - header: Quote header information
    - empresa: Company details
    - cliente: Client information
    - ruta: Route and logistics
    - lineItems: All line items (servicios, contenedores, articulos, fletes)
    - precios: Pricing breakdown by Incoterm
    - estadisticas: Quote statistics
    - textos: Legal and commercial texts
    - intermediario: Broker information (optional)
    - documentacion: Documentation settings

    The Quote template can access all data from DEAL.quote_data_payload.

    Note: Line items are expected to be already updated in HubSpot before calling this endpoint.
    """
    try:
        client = hubspot_client.get_client()
        access_token = client.access_token

        if not data:
            raise HTTPException(400, "Payload de cotización requerido")

        if not data.lineItems:
            raise HTTPException(400, "No hay line items en la cotización")

        logger.info(f"[publish] Starting quote publication for deal {deal_id}")

        # =====================================================================
        # STEP 0: Get deal owner ID and owner details
        # =====================================================================

        deal_owner_id = None
        owner_info = {}

        try:
            deal_response = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["hubspot_owner_id"]
            )
            deal_owner_id = deal_response.properties.get("hubspot_owner_id")
            logger.info(f"[publish] Deal owner ID: {deal_owner_id}")

            # Get owner details
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

        # =====================================================================
        # STEP 1: Store all quote data in Deal properties
        # =====================================================================

        deal_update_properties = {
            "quote_data_payload": json.dumps(
                data.dict(),
                ensure_ascii=False,
                default=str
            )
        }

        try:
            client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input={"properties": deal_update_properties}
            )
            logger.info(f"[publish] Updated deal {deal_id} with quote payload")
        except Exception as e:
            logger.warning(f"[publish] Could not update deal with quote payload: {e}")

        # =====================================================================
        # STEP 2: Build quote properties (solo propiedades estándar)
        # =====================================================================

        # Idioma: usamos el del cliente si viene, si no por defecto 'en'
        language_map = {"ES": "es", "EN": "en", "FR": "fr"}
        raw_lang = (data.cliente.idioma or "ES").upper()
        hs_language = language_map.get(raw_lang, "en")

        hs_expiration_date = (
            data.ruta.fechaValidez
            if getattr(data, "ruta", None) and getattr(data.ruta, "fechaValidez", None)
            else None
        )

        quote_properties = {
            "hs_title": f"Cotización #{data.header.offerNumber}",
            "hs_expiration_date": normalize_hs_date(hs_expiration_date),
            "hs_status": "DRAFT",
            "hs_language": hs_language,
        }

        # Agregar owner y sender properties si existe
        if deal_owner_id:
            quote_properties["hs_quote_owner_id"] = deal_owner_id

        if owner_info:
            if owner_info.get("first_name"):
                quote_properties["hs_sender_firstname"] = owner_info["first_name"]
            if owner_info.get("last_name"):
                quote_properties["hs_sender_lastname"] = owner_info["last_name"]
            if owner_info.get("email"):
                quote_properties["hs_sender_email"] = owner_info["email"]

            # Company name from data.empresa if available
            if data.empresa and data.empresa.nombre:
                quote_properties["hs_sender_company_name"] = data.empresa.nombre

        # Elimina valores None
        quote_properties = {k: v for k, v in quote_properties.items() if v is not None}

        # =====================================================================
        # STEP 3: Build associations (igual estilo que create_quote)
        # =====================================================================

        associations = []

        # Asociación a Deal
        associations.append(
            build_association(deal_id, AssociationType.QUOTE_TO_DEAL)
        )

        # Asociación a plantilla fija
        associations.append(
            build_association(QUOTE_TEMPLATE_ID, AssociationType.QUOTE_TO_TEMPLATE)
        )

        logger.info(
            f"[publish] Creating DRAFT quote for deal {deal_id} with template {QUOTE_TEMPLATE_ID}"
        )
        logger.info(
            f"[publish] Associations: {associations}"
        )

        # =====================================================================
        # STEP 4: Create quote via generic helper (create_quote_via_api)
        # =====================================================================

        quote_id, error = create_quote_via_api(
            access_token=access_token,
            properties=quote_properties,
            associations=associations,
        )

        if not quote_id:
            logger.error(f"[publish] Failed to create quote via API: {error}")
            raise HTTPException(
                status_code=500,
                detail=f"Error gestionando cotización: {error}",
            )

        logger.info(
            f"[publish] Created new quote {quote_id} "
            f"associated with deal {deal_id} and template {QUOTE_TEMPLATE_ID}"
        )

        # =====================================================================
        # STEP 4.5: Create and associate Line Items
        # =====================================================================

        def safe_parse_float(value, fallback=None):
            """Parse float from value, handling empty strings and None."""
            if value is None or value == "":
                return fallback
            try:
                return float(value)
            except (ValueError, TypeError):
                return fallback

        def calculate_price(li):
            """
            Calculate price with priority:
            1. Use price from payload if exists and not empty
            2. If price empty but total and qty exist -> total / qty
            3. Otherwise -> None (don't set property)
            """
            price = safe_parse_float(li.price)
            if price is not None:
                return price

            total = safe_parse_float(li.total)
            qty = safe_parse_float(li.qty, fallback=1.0)

            if total is not None and qty > 0:
                return total / qty

            return None

        # Sort line items: articulos first, then others
        articulos = [li for li in data.lineItems if li.type == "articulo"]
        otros = [li for li in data.lineItems if li.type != "articulo"]
        lineItems_sorted = articulos + otros

        # Tracking counters
        created_count = 0
        associated_count = 0
        errors_list = []

        logger.info(f"[publish] Creating {len(lineItems_sorted)} line items for quote {quote_id}")

        # Mapping de tipos: lowercase del payload -> HubSpot (primera letra mayúscula)
        type_mapping = {
            "articulo": "Articulo",
            "servicio": "Servicio",
            "contenedor": "Contenedor",
            "flete": "Flete"
        }

        for idx, li in enumerate(lineItems_sorted):
            try:
                # Build line item properties
                properties = {}

                # hs_product_type and tipo_articulo: use product-origin values when provided so quote matches product
                if li.hs_product_type and str(li.hs_product_type).strip():
                    _raw = str(li.hs_product_type).strip()
                    properties["hs_product_type"] = (_raw[0].upper() + _raw[1:].lower()) if _raw else type_mapping.get(li.type, li.type)
                else:
                    properties["hs_product_type"] = type_mapping.get(li.type, li.type)
                if li.tipo_articulo and str(li.tipo_articulo).strip():
                    properties["tipo_articulo"] = str(li.tipo_articulo).strip().lower()
                properties["name"] = li.item

                # Optional string fields
                if li.code and li.code.strip():
                    properties["hs_sku"] = li.code

                # Optional numeric fields with robust parsing
                colis_val = safe_parse_float(li.colis)
                if colis_val is not None:
                    properties["colis"] = colis_val

                weight_val = safe_parse_float(li.weight)
                if weight_val is not None:
                    properties["peso_bruto"] = weight_val

                # quantity: default to 1.0 if not provided
                qty_val = safe_parse_float(li.qty, fallback=1.0)
                properties["quantity"] = qty_val

                # price: calculate with priority logic
                price_val = calculate_price(li)
                if price_val is not None:
                    properties["price"] = price_val

                # margen_unitario
                margen_val = safe_parse_float(li.margen_unitario)
                if margen_val is not None:
                    properties["margen_unitario"] = margen_val

                # tipo_iva_repercutido (string)
                if li.tipo_iva_repercutido and li.tipo_iva_repercutido.strip():
                    properties["tipo_iva_repercutido"] = li.tipo_iva_repercutido

                # numero_container (string) - container identifier
                if li.numero_container and li.numero_container.strip():
                    properties["numero_container"] = li.numero_container.strip()

                # Create line item via API
                line_item_id, create_error = create_line_item_via_api(
                    access_token=access_token,
                    properties=properties
                )

                if not line_item_id:
                    error_msg = f"Item {idx} ({li.type}): Error creating line item - {create_error}"
                    logger.error(f"[publish] {error_msg}")
                    errors_list.append(error_msg)
                    continue

                # Line item created successfully
                created_count += 1
                logger.info(f"[publish] Created line item {idx} ({li.type}): {line_item_id}")

                # Associate line item to quote
                assoc_success, assoc_error = associate_line_item_to_quote_v4(
                    access_token=access_token,
                    quote_id=quote_id,
                    line_item_id=line_item_id
                )

                if assoc_success:
                    associated_count += 1
                    logger.info(f"[publish] Associated line item {idx} to quote {quote_id}")
                else:
                    error_msg = f"Item {idx} ({li.type}): Created but association failed - {assoc_error}"
                    logger.warning(f"[publish] {error_msg}")
                    errors_list.append(error_msg)

            except Exception as e:
                error_msg = f"Item {idx} ({li.type}): Unexpected error - {str(e)}"
                logger.error(f"[publish] {error_msg}", exc_info=True)
                errors_list.append(error_msg)

        logger.info(
            f"[publish] Line items summary: {created_count} created, "
            f"{associated_count} associated, {len(errors_list)} errors"
        )

        # =====================================================================
        # STEP 5: Return success response
        # =====================================================================

        return PublishQuoteResponse(
            success=True,
            error=None,
            data={
                "deal_id": deal_id,
                "quote_id": quote_id,
                "quote_status": "DRAFT",
                "total_line_items": len(data.lineItems),
                "created_line_items": created_count,
                "associated_line_items": associated_count,
                "line_items_errors": errors_list if errors_list else [],
                "total_euros": data.estadisticas.totalEuros,
                "num_containers": data.estadisticas.numContainers,
                "total_colis": data.estadisticas.totalColis,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[publish] Unexpected error publishing quote: {e}", exc_info=True)
        raise HTTPException(500, f"Error al publicar la cotización: {str(e)}")


@router.post("/{deal_id}/publish-custom-objects", response_model=BaseResponse)
async def publish_custom_objects(
    deal_id: str = Path(
        ...,
        description="HubSpot deal ID",
        example="46298478914"
    )
):
    """
    Publica los objetos personalizados (Contenedor y Elemento) basándose en los line items del deal.

    Este endpoint procesa de forma ASÍNCRONA:
    1. Lee todos los line items del deal
    2. Elimina objetos personalizados existentes
    3. Crea objetos Contenedor para cada line item de tipo contenedor
    4. Crea objetos Elemento para cada line item de artículo/servicio/flete/etc.
    5. Establece las asociaciones correctas entre objetos

    **Nota**: Este proceso se ejecuta en background usando threading y puede tardar 20-30 segundos.
    El endpoint devuelve respuesta inmediata y el procesamiento continúa en segundo plano.

    Args:
        deal_id: HubSpot deal ID

    Returns:
        BaseResponse con success=True si se inició el proceso correctamente
    """
    try:
        # Validar que el deal existe
        client = hubspot_client.get_client()

        try:
            client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["dealname"]
            )
        except Exception as e:
            logger.error(f"Deal {deal_id} not found: {e}")
            raise HTTPException(status_code=404, detail=f"Deal {deal_id} no encontrado")

        # Lanzar tarea en background usando threading (para Lambda)
        logger.info(f"📦 Starting background thread to publish custom objects for deal {deal_id}")

        # Initialize job status as pending
        _background_jobs_status[deal_id] = {
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "error": None
        }

        thread = threading.Thread(
            target=_background_job_wrapper,
            args=(deal_id,),
            daemon=True
        )
        thread.start()

        logger.info(f"✅ Background thread started for deal {deal_id}")

        return BaseResponse(
            success=True,
            error=None
        )

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
    """
    Verifica el estado de los objetos personalizados (Contenedor y Elemento) para un deal.

    Este endpoint consulta cuántos contenedores y elementos existen actualmente
    asociados al deal para determinar si la tarea de publicación ha terminado.

    Args:
        deal_id: HubSpot deal ID

    Returns:
        {
            "status": "pending" | "completed" | "error",
            "contenedores": 0,
            "elementos": 0,
            "message": "..."
        }
    """
    try:
        from app.services.custom_objects import get_contenedores_by_deal, get_elementos_by_deal

        client = hubspot_client.get_client()

        # Verificar que el deal existe
        try:
            client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["dealname"]
            )
        except Exception as e:
            logger.error(f"Deal {deal_id} not found: {e}")
            raise HTTPException(status_code=404, detail=f"Deal {deal_id} no encontrado")

        # Check job status from tracking dictionary
        job_status = _background_jobs_status.get(deal_id)

        if not job_status:
            # No job found for this deal
            logger.info(f"📊 No background job found for deal {deal_id}")
            return {
                "success": True,
                "error": None,
                "data": {
                    "status": "not_started",
                    "contenedores": 0,
                    "elementos": 0,
                    "message": "No se ha iniciado la creación de objetos personalizados"
                }
            }

        # Get current status from tracking
        current_status = job_status["status"]

        # Obtener contenedores asociados al deal
        contenedores = get_contenedores_by_deal(client, deal_id, properties=["nombre"])
        num_contenedores = len(contenedores) if contenedores else 0

        # Obtener elementos asociados al deal
        elementos = get_elementos_by_deal(client, deal_id, properties=["descripcion"])
        num_elementos = len(elementos) if elementos else 0

        # Determinar mensaje según el estado
        if current_status == "pending":
            status = "pending"
            message = "Preparando creación de objetos personalizados..."
        elif current_status == "processing":
            status = "processing"
            message = "Procesando objetos personalizados..."
        elif current_status == "completed":
            status = "completed"
            message = f"Objetos personalizados creados exitosamente"
        elif current_status == "error":
            status = "error"
            message = f"Error al crear objetos personalizados: {job_status.get('error', 'Error desconocido')}"
        else:
            status = "pending"
            message = "Estado desconocido"

        logger.info(f"📊 Custom objects status for deal {deal_id}: {current_status} - {num_contenedores} contenedores, {num_elementos} elementos")

        return {
            "success": True,
            "error": None,
            "data": {
                "status": status,
                "contenedores": num_contenedores,
                "elementos": num_elementos,
                "message": message
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting custom objects status for deal {deal_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))