"""
Service layer for managing HubSpot custom objects: CONTENEDOR and ELEMENTO.

This module provides CRUD operations for custom objects with dual tracking
alongside the existing line items system.
"""

import logging
import json
import time
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from hubspot import HubSpot
from hubspot.crm.objects import SimplePublicObjectInputForCreate, SimplePublicObjectInput
from hubspot.crm.associations import BatchInputPublicObjectId, PublicObjectId

from app.core.custom_objects_constants import (
    OBJECT_TYPE_CONTENEDOR,
    OBJECT_TYPE_ELEMENTO,
    OBJECT_NAME_CONTENEDOR,
    OBJECT_NAME_ELEMENTO,
    ASSOCIATION_TYPE_ID_DEAL_TO_CONTENEDOR,
    ASSOCIATION_TYPE_ID_DEAL_TO_ELEMENTO,
    ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTENEDOR,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_CONTACT,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_COMPANY,
    ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTACT,
    ASSOCIATION_TYPE_ID_ELEMENTO_TO_COMPANY
)

logger = logging.getLogger(__name__)


# ============================================================================
# CONTENEDOR CRUD OPERATIONS
# ============================================================================

def create_contenedor(
    client: HubSpot,
    deal_id: str,
    nombre: str,
    tipo_contenedor: str,
    numero_container: int,
    ocupacion: Optional[float] = None,
    peso_neto: Optional[float] = None,
    peso_bruto: Optional[float] = None,
    container_config: Optional[str] = None,
    total_cajas: Optional[int] = None,
    price: Optional[float] = None,
    line_item_id: Optional[str] = None,
    numero_precinto: Optional[str] = None
) -> Optional[str]:
    """
    Create a Contenedor custom object and associate it with a deal.

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID to associate with
        nombre: Unique container name (e.g., "46298478914-001")
        tipo_contenedor: Container type (e.g., "d40dr")
        numero_container: Sequential container number
        ocupacion: Occupancy percentage (97-103%)
        peso_neto: Total net weight in kg
        peso_bruto: Total gross weight in kg
        container_config: JSON configuration string
        total_cajas: Total boxes/units
        price: Container price
        line_item_id: Original line item ID for dual tracking
        numero_precinto: Security seal number

    Returns:
        Created contenedor object ID if successful, None otherwise
    """
    try:
        # Prepare properties (all values as strings for HubSpot)
        properties = {
            "nombre": nombre,
            "tipo_contenedor": tipo_contenedor.lower(),  # HubSpot expects lowercase
            "numero_container": str(numero_container),
            "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))  # Unix timestamp in milliseconds
        }

        # Add optional properties
        if ocupacion is not None:
            properties["ocupacion"] = str(ocupacion)
        if peso_neto is not None:
            properties["peso_neto"] = str(peso_neto)
        if peso_bruto is not None:
            properties["peso_bruto"] = str(peso_bruto)
        if container_config:
            properties["container_config"] = container_config
        if total_cajas is not None:
            properties["total_cajas"] = str(total_cajas)
        if price is not None:
            properties["price"] = str(price)
        if line_item_id:
            properties["line_item_id"] = line_item_id
        if numero_precinto:
            properties["numero_precinto"] = numero_precinto

        # Create the object first without associations
        object_input = SimplePublicObjectInputForCreate(
            properties=properties
        )

        result = client.crm.objects.basic_api.create(
            object_type=OBJECT_TYPE_CONTENEDOR,
            simple_public_object_input_for_create=object_input
        )

        contenedor_id = result.id
        logger.info(f"✅ Created Contenedor {contenedor_id} ({nombre})")

        # Associate the contenedor with the deal using v4 API
        try:
            from hubspot.crm.associations.v4 import AssociationSpec

            association_spec = [AssociationSpec(
                association_category="USER_DEFINED",
                association_type_id=ASSOCIATION_TYPE_ID_DEAL_TO_CONTENEDOR
            )]

            client.crm.associations.v4.basic_api.create(
                object_type=OBJECT_TYPE_CONTENEDOR,
                object_id=contenedor_id,
                to_object_type="deals",
                to_object_id=deal_id,
                association_spec=association_spec
            )
            logger.info(f"✅ Associated Contenedor {contenedor_id} with deal {deal_id}")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not associate Contenedor {contenedor_id} with deal {deal_id}: {assoc_error}")

        return contenedor_id

    except Exception as e:
        logger.error(f"❌ Error creating Contenedor for deal {deal_id}: {e}")
        return None


def batch_create_contenedores_with_associations(
    client: HubSpot,
    contenedores_data: List[Dict[str, Any]],
    deal_id: str,
    contact_id: str = None,
    company_id: str = None
) -> Dict[str, str]:
    """
    Batch create multiple Contenedor objects and their associations to a deal.

    This is much faster than creating them one by one.
    Reduces API calls from N creates + N associations to 1 batch create + N associations.

    Args:
        client: HubSpot client instance
        contenedores_data: List of dicts with Contenedor properties
        deal_id: Deal ID to associate with
        contact_id: Contact ID to associate with (optional)
        company_id: Company ID to associate with (optional)

    Returns:
        Dict mapping contenedor nombre to contenedor ID
    """
    if not contenedores_data:
        return {}

    start_time = time.time()

    try:
        from hubspot.crm.objects import BatchInputSimplePublicObjectInputForCreate

        # Prepare batch input
        prep_start = time.time()
        batch_inputs = []
        for data in contenedores_data:
            properties = {
                "nombre": data["nombre"],
                "tipo_contenedor": data["tipo_contenedor"].lower(),
                "numero_container": str(data["numero_container"]),
                "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
            }

            # Add optional properties
            for key in ["ocupacion", "peso_neto", "peso_bruto", "total_cajas", "price", "importe_pendiente_cobro"]:
                if key in data and data[key] is not None:
                    properties[key] = str(data[key])

            for key in ["container_config", "line_item_id", "numero_precinto"]:
                if key in data and data[key]:
                    properties[key] = data[key]

            batch_inputs.append(SimplePublicObjectInputForCreate(properties=properties))

        prep_time = time.time() - prep_start
        logger.info(f"⏱️  [TIMER] Prepared {len(batch_inputs)} contenedores data in {prep_time:.2f}s")

        # Batch create contenedores
        create_start = time.time()
        batch_input = BatchInputSimplePublicObjectInputForCreate(inputs=batch_inputs)
        result = client.crm.objects.batch_api.create(
            object_type=OBJECT_TYPE_CONTENEDOR,
            batch_input_simple_public_object_input_for_create=batch_input
        )

        # Create mapping of nombre -> id
        contenedor_map = {}
        for obj in result.results:
            nombre = obj.properties.get("nombre")
            if nombre:
                contenedor_map[nombre] = obj.id

        create_time = time.time() - create_start
        logger.info(f"✅ Batch created {len(contenedor_map)} Contenedores in 1 API call ({create_time:.2f}s)")

        # Create associations to deal, contact, and company (one by one for now - HubSpot v4 associations API doesn't support batch well)
        # This is still much faster than before because object creation is batched
        try:
            from hubspot.crm.associations.v4 import AssociationSpec

            assoc_start = time.time()
            total_associations = 0

            for contenedor_id in contenedor_map.values():
                # Associate with deal
                association_spec = [AssociationSpec(
                    association_category="USER_DEFINED",
                    association_type_id=ASSOCIATION_TYPE_ID_DEAL_TO_CONTENEDOR
                )]

                client.crm.associations.v4.basic_api.create(
                    object_type=OBJECT_TYPE_CONTENEDOR,
                    object_id=contenedor_id,
                    to_object_type="deals",
                    to_object_id=deal_id,
                    association_spec=association_spec
                )
                total_associations += 1

                # Associate with contact (if provided)
                if contact_id:
                    try:
                        association_spec_contact = [AssociationSpec(
                            association_category="USER_DEFINED",
                            association_type_id=ASSOCIATION_TYPE_ID_CONTENEDOR_TO_CONTACT
                        )]
                        client.crm.associations.v4.basic_api.create(
                            object_type=OBJECT_TYPE_CONTENEDOR,
                            object_id=contenedor_id,
                            to_object_type="contacts",
                            to_object_id=contact_id,
                            association_spec=association_spec_contact
                        )
                        total_associations += 1
                    except Exception as contact_error:
                        logger.warning(f"⚠️ Could not associate Contenedor {contenedor_id} with contact {contact_id}: {contact_error}")

                # Associate with company (if provided)
                if company_id:
                    try:
                        association_spec_company = [AssociationSpec(
                            association_category="USER_DEFINED",
                            association_type_id=ASSOCIATION_TYPE_ID_CONTENEDOR_TO_COMPANY
                        )]
                        client.crm.associations.v4.basic_api.create(
                            object_type=OBJECT_TYPE_CONTENEDOR,
                            object_id=contenedor_id,
                            to_object_type="companies",
                            to_object_id=company_id,
                            association_spec=association_spec_company
                        )
                        total_associations += 1
                    except Exception as company_error:
                        logger.warning(f"⚠️ Could not associate Contenedor {contenedor_id} with company {company_id}: {company_error}")

            assoc_time = time.time() - assoc_start
            logger.info(f"✅ Created {total_associations} associations for {len(contenedor_map)} Contenedores (Deal + Contact + Company) ({assoc_time:.2f}s, {assoc_time/total_associations:.2f}s per assoc)")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not associate Contenedores: {assoc_error}")

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] Total batch_create_contenedores_with_associations: {total_time:.2f}s")
        return contenedor_map

    except Exception as e:
        logger.error(f"❌ Error batch creating Contenedores: {e}")
        return {}


def update_contenedor(
    client: HubSpot,
    contenedor_id: str,
    properties: Dict[str, Any]
) -> bool:
    """
    Update a Contenedor custom object.

    Args:
        client: HubSpot client instance
        contenedor_id: Contenedor object ID
        properties: Properties to update (values will be converted to strings)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Convert all values to strings (HubSpot requirement)
        string_properties = {k: str(v) for k, v in properties.items() if v is not None}

        object_input = SimplePublicObjectInput(properties=string_properties)

        client.crm.objects.basic_api.update(
            object_type=OBJECT_TYPE_CONTENEDOR,
            object_id=contenedor_id,
            simple_public_object_input=object_input
        )

        logger.info(f"✅ Updated Contenedor {contenedor_id} with {len(string_properties)} properties")
        return True

    except Exception as e:
        logger.error(f"❌ Error updating Contenedor {contenedor_id}: {e}")
        return False


def delete_contenedor(
    client: HubSpot,
    contenedor_id: str
) -> bool:
    """
    Delete (archive) a Contenedor custom object.

    Args:
        client: HubSpot client instance
        contenedor_id: Contenedor object ID

    Returns:
        True if successful, False otherwise
    """
    try:
        client.crm.objects.basic_api.archive(
            object_type=OBJECT_TYPE_CONTENEDOR,
            object_id=contenedor_id
        )

        logger.info(f"✅ Deleted Contenedor {contenedor_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Error deleting Contenedor {contenedor_id}: {e}")
        return False


def get_contenedores_by_deal(
    client: HubSpot,
    deal_id: str,
    properties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Get all Contenedores associated with a deal.

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID
        properties: List of properties to retrieve (None = default set)

    Returns:
        List of contenedor objects with their properties
    """
    try:
        # Default properties if none specified
        if properties is None:
            properties = [
                "nombre", "tipo_contenedor", "numero_container",
                "ocupacion", "peso_neto", "peso_bruto", "total_cajas",
                "line_item_id"
            ]

        contenedor_ids = []

        # Try first: Use associations API to get contenedor IDs
        try:
            deal_object_id = PublicObjectId(id=deal_id)
            batch_input = BatchInputPublicObjectId(inputs=[deal_object_id])

            associations_response = client.crm.associations.batch_api.read(
                from_object_type="deals",
                to_object_type=OBJECT_TYPE_CONTENEDOR,
                batch_input_public_object_id=batch_input
            )

            if hasattr(associations_response, 'results') and associations_response.results:
                for result in associations_response.results:
                    if hasattr(result, 'to') and result.to:
                        for assoc in result.to:
                            assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                            if assoc_id:
                                contenedor_ids.append(str(assoc_id))

            logger.info(f"🔗 [ASSOCIATIONS] Found {len(contenedor_ids)} Contenedores via associations for deal {deal_id}")
        except Exception as e:
            logger.warning(f"⚠️ [ASSOCIATIONS] Failed to get Contenedores via associations: {e}")

        # Fallback: Search by nombre property pattern if no associations found
        if not contenedor_ids:
            logger.info(f"🔍 [SEARCH] Searching Contenedores by nombre pattern for deal {deal_id}")
            try:
                from hubspot.crm.objects import PublicObjectSearchRequest

                # Search for contenedores with nombre matching pattern: {deal_id}-XXX
                search_request = PublicObjectSearchRequest(
                    filter_groups=[{
                        "filters": [{
                            "propertyName": "nombre",
                            "operator": "CONTAINS_TOKEN",
                            "value": str(deal_id)
                        }]
                    }],
                    properties=properties,
                    limit=100
                )

                search_response = client.crm.objects.search_api.do_search(
                    object_type=OBJECT_TYPE_CONTENEDOR,
                    public_object_search_request=search_request
                )

                if hasattr(search_response, 'results') and search_response.results:
                    # Filter results to only include exact matches (nombre starts with deal_id-)
                    for obj in search_response.results:
                        nombre = obj.properties.get("nombre", "")
                        # Only include if nombre matches pattern: {deal_id}-XXX
                        if nombre.startswith(f"{deal_id}-"):
                            contenedor_ids.append(obj.id)

                    logger.info(f"🔍 [SEARCH] Found {len(contenedor_ids)} Contenedores via search for deal {deal_id}")
                else:
                    logger.info(f"🔍 [SEARCH] No Contenedores found via search for deal {deal_id}")
            except Exception as e:
                logger.warning(f"⚠️ [SEARCH] Failed to search Contenedores: {e}")

        if not contenedor_ids:
            logger.info(f"❌ No Contenedores found for deal {deal_id} (tried both associations and search)")
            return []

        # Batch read contenedores
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId

        batch_read_input = BatchReadInputSimplePublicObjectId(
            properties=properties,
            inputs=[{"id": cid} for cid in contenedor_ids]
        )

        batch_response = client.crm.objects.batch_api.read(
            object_type=OBJECT_TYPE_CONTENEDOR,
            batch_read_input_simple_public_object_id=batch_read_input
        )

        contenedores = []
        if hasattr(batch_response, 'results'):
            for obj in batch_response.results:
                contenedores.append({
                    "id": obj.id,
                    "properties": obj.properties
                })

        logger.info(f"✅ Retrieved {len(contenedores)} Contenedores for deal {deal_id}")
        return contenedores

    except Exception as e:
        logger.error(f"❌ Error getting Contenedores for deal {deal_id}: {e}")
        return []


# ============================================================================
# ELEMENTO CRUD OPERATIONS
# ============================================================================

def create_elemento(
    client: HubSpot,
    deal_id: str,
    contenedor_id: str,
    nombre: str,
    tipo_articulo: str,
    nombre_contenedor: Optional[str] = None,
    cantidad: Optional[float] = None,
    product_sku: Optional[str] = None,
    precio_unitario: Optional[float] = None,
    precio_de_coste: Optional[float] = None,
    precio_total: Optional[float] = None,
    margen_unitario: Optional[float] = None,
    peso_neto_unitario: Optional[float] = None,
    peso_neto_total: Optional[float] = None,
    peso_bruto_unitario: Optional[float] = None,
    peso_bruto_total: Optional[float] = None,
    cardcode: Optional[str] = None,
    nombre_proveedor: Optional[str] = None,
    tipo_iva_repercutido: Optional[str] = None,
    codigo_proveedor: Optional[str] = None,
    line_item_id: Optional[str] = None
) -> Optional[str]:
    """
    Create an Elemento custom object and associate it with a deal and contenedor.

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID to associate with
        contenedor_id: Contenedor object ID to associate with
        nombre: Element name (e.g., "46298478914-001 - Vino Tinto")
        tipo_articulo: Article type (fabricante, servicio, flete, etc.)
        nombre_contenedor: Container name for quick reference
        cantidad: Quantity
        product_sku: Product SKU
        precio_unitario: Unit price
        precio_de_coste: Cost price
        precio_total: Total price
        margen_unitario: Unit margin percentage
        peso_neto_unitario: Unit net weight in kg
        peso_neto_total: Total net weight in kg
        peso_bruto_unitario: Unit gross weight in kg
        peso_bruto_total: Total gross weight in kg
        cardcode: Provider cardcode
        nombre_proveedor: Provider name
        tipo_iva_repercutido: VAT type
        codigo_proveedor: Provider's product code
        line_item_id: Original line item ID for dual tracking

    Returns:
        Created elemento object ID if successful, None otherwise
    """
    try:
        # Prepare properties (all values as strings for HubSpot)
        properties = {
            "nombre": nombre,
            "tipo_articulo": tipo_articulo.lower(),  # HubSpot expects lowercase
            "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))  # Unix timestamp in milliseconds
        }

        # Add optional properties
        if nombre_contenedor:
            properties["nombre_contenedor"] = nombre_contenedor
        if cantidad is not None:
            properties["cantidad"] = str(cantidad)
        if product_sku:
            properties["product_sku"] = product_sku
        if precio_unitario is not None:
            properties["precio_unitario"] = str(precio_unitario)
        if precio_de_coste is not None:
            properties["precio_de_coste"] = str(precio_de_coste)
        if precio_total is not None:
            properties["precio_total"] = str(precio_total)
        if margen_unitario is not None:
            properties["margen_unitario"] = str(margen_unitario)
        if peso_neto_unitario is not None:
            properties["peso_neto_unitario"] = str(peso_neto_unitario)
        if peso_neto_total is not None:
            properties["peso_neto_total"] = str(peso_neto_total)
        if peso_bruto_unitario is not None:
            properties["peso_bruto_unitario"] = str(peso_bruto_unitario)
        if peso_bruto_total is not None:
            properties["peso_bruto_total"] = str(peso_bruto_total)
        if cardcode:
            properties["cardcode"] = cardcode
        if nombre_proveedor:
            properties["nombre_proveedor"] = nombre_proveedor
        if tipo_iva_repercutido:
            properties["tipo_iva_repercutido"] = tipo_iva_repercutido.lower()  # HubSpot expects lowercase
        if codigo_proveedor:
            properties["codigo_proveedor"] = codigo_proveedor
        if line_item_id:
            properties["line_item_id"] = line_item_id

        # Create the object first without associations
        object_input = SimplePublicObjectInputForCreate(
            properties=properties
        )

        result = client.crm.objects.basic_api.create(
            object_type=OBJECT_TYPE_ELEMENTO,
            simple_public_object_input_for_create=object_input
        )

        elemento_id = result.id
        logger.info(f"✅ Created Elemento {elemento_id} ({nombre})")

        # Associate the elemento with the deal using v4 API
        try:
            from hubspot.crm.associations.v4 import AssociationSpec

            association_spec_deal = [AssociationSpec(
                association_category="USER_DEFINED",
                association_type_id=ASSOCIATION_TYPE_ID_DEAL_TO_ELEMENTO
            )]

            client.crm.associations.v4.basic_api.create(
                object_type=OBJECT_TYPE_ELEMENTO,
                object_id=elemento_id,
                to_object_type="deals",
                to_object_id=deal_id,
                association_spec=association_spec_deal
            )
            logger.info(f"✅ Associated Elemento {elemento_id} with deal {deal_id}")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not associate Elemento {elemento_id} with deal {deal_id}: {assoc_error}")

        # Associate the elemento with the contenedor using v4 API
        try:
            from hubspot.crm.associations.v4 import AssociationSpec

            association_spec_contenedor = [AssociationSpec(
                association_category="USER_DEFINED",
                association_type_id=ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTENEDOR
            )]

            # Note: Association 491 might be defined as Contenedor -> Elemento
            # So we need to create it from Contenedor to Elemento
            client.crm.associations.v4.basic_api.create(
                object_type=OBJECT_TYPE_CONTENEDOR,
                object_id=contenedor_id,
                to_object_type=OBJECT_TYPE_ELEMENTO,
                to_object_id=elemento_id,
                association_spec=association_spec_contenedor
            )
            logger.info(f"✅ Associated Elemento {elemento_id} with Contenedor {contenedor_id}")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not associate Elemento {elemento_id} with Contenedor {contenedor_id}: {assoc_error}")

        return elemento_id

    except Exception as e:
        logger.error(f"❌ Error creating Elemento for contenedor {contenedor_id}: {e}")
        return None


def batch_create_elementos_with_associations(
    client: HubSpot,
    elementos_data: List[Dict[str, Any]],
    deal_id: str,
    contenedor_id: str,
    contact_id: str = None,
    company_id: str = None
) -> List[str]:
    """
    Batch create multiple Elemento objects and their associations to a deal and contenedor.

    This is much faster than creating them one by one.
    Reduces API calls from N creates + 2N associations to 1 batch create + 2N associations.

    Args:
        client: HubSpot client instance
        elementos_data: List of dicts with Elemento properties
        deal_id: Deal ID to associate with
        contenedor_id: Contenedor ID to associate with
        contact_id: Contact ID to associate with (optional)
        company_id: Company ID to associate with (optional)

    Returns:
        List of created elemento IDs
    """
    if not elementos_data:
        return []

    start_time = time.time()

    try:
        from hubspot.crm.objects import BatchInputSimplePublicObjectInputForCreate

        # Prepare batch input
        prep_start = time.time()
        batch_inputs = []
        for data in elementos_data:
            properties = {
                "nombre": data["nombre"],
                "tipo_articulo": data["tipo_articulo"].lower(),
                "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
            }

            # Add optional properties
            if "nombre_contenedor" in data and data["nombre_contenedor"]:
                properties["nombre_contenedor"] = data["nombre_contenedor"]

            for key in ["cantidad", "precio_unitario", "precio_de_coste", "precio_total",
                       "margen_unitario", "peso_neto_unitario", "peso_neto_total",
                       "peso_bruto_unitario", "peso_bruto_total"]:
                if key in data and data[key] is not None:
                    properties[key] = str(data[key])

            for key in ["product_sku", "cardcode", "nombre_proveedor", "codigo_proveedor", "line_item_id"]:
                if key in data and data[key]:
                    properties[key] = data[key]

            if "tipo_iva_repercutido" in data and data["tipo_iva_repercutido"]:
                properties["tipo_iva_repercutido"] = data["tipo_iva_repercutido"].lower()

            batch_inputs.append(SimplePublicObjectInputForCreate(properties=properties))

        prep_time = time.time() - prep_start
        logger.info(f"⏱️  [TIMER] Prepared {len(batch_inputs)} elementos data in {prep_time:.2f}s")

        # Batch create elementos
        create_start = time.time()
        batch_input = BatchInputSimplePublicObjectInputForCreate(inputs=batch_inputs)
        result = client.crm.objects.batch_api.create(
            object_type=OBJECT_TYPE_ELEMENTO,
            batch_input_simple_public_object_input_for_create=batch_input
        )

        elemento_ids = [obj.id for obj in result.results]
        create_time = time.time() - create_start
        logger.info(f"✅ Batch created {len(elemento_ids)} Elementos in 1 API call ({create_time:.2f}s)")

        # Create associations to deal, contenedor, contact, and company
        try:
            from hubspot.crm.associations.v4 import AssociationSpec

            assoc_start = time.time()
            total_associations = 0

            for elemento_id in elemento_ids:
                # Associate with deal
                association_spec_deal = [AssociationSpec(
                    association_category="USER_DEFINED",
                    association_type_id=ASSOCIATION_TYPE_ID_DEAL_TO_ELEMENTO
                )]

                client.crm.associations.v4.basic_api.create(
                    object_type=OBJECT_TYPE_ELEMENTO,
                    object_id=elemento_id,
                    to_object_type="deals",
                    to_object_id=deal_id,
                    association_spec=association_spec_deal
                )
                total_associations += 1

                # Associate with contenedor (Contenedor -> Elemento direction)
                association_spec_contenedor = [AssociationSpec(
                    association_category="USER_DEFINED",
                    association_type_id=ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTENEDOR
                )]

                client.crm.associations.v4.basic_api.create(
                    object_type=OBJECT_TYPE_CONTENEDOR,
                    object_id=contenedor_id,
                    to_object_type=OBJECT_TYPE_ELEMENTO,
                    to_object_id=elemento_id,
                    association_spec=association_spec_contenedor
                )
                total_associations += 1

                # Associate with contact (if provided)
                if contact_id:
                    try:
                        association_spec_contact = [AssociationSpec(
                            association_category="USER_DEFINED",
                            association_type_id=ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTACT
                        )]
                        client.crm.associations.v4.basic_api.create(
                            object_type=OBJECT_TYPE_ELEMENTO,
                            object_id=elemento_id,
                            to_object_type="contacts",
                            to_object_id=contact_id,
                            association_spec=association_spec_contact
                        )
                        total_associations += 1
                    except Exception as contact_error:
                        logger.warning(f"⚠️ Could not associate Elemento {elemento_id} with contact {contact_id}: {contact_error}")

                # Associate with company (if provided)
                if company_id:
                    try:
                        association_spec_company = [AssociationSpec(
                            association_category="USER_DEFINED",
                            association_type_id=ASSOCIATION_TYPE_ID_ELEMENTO_TO_COMPANY
                        )]
                        client.crm.associations.v4.basic_api.create(
                            object_type=OBJECT_TYPE_ELEMENTO,
                            object_id=elemento_id,
                            to_object_type="companies",
                            to_object_id=company_id,
                            association_spec=association_spec_company
                        )
                        total_associations += 1
                    except Exception as company_error:
                        logger.warning(f"⚠️ Could not associate Elemento {elemento_id} with company {company_id}: {company_error}")

            assoc_time = time.time() - assoc_start
            logger.info(f"✅ Created {total_associations} associations for {len(elemento_ids)} Elementos (Deal + Contenedor + Contact + Company) ({assoc_time:.2f}s, {assoc_time/total_associations:.2f}s per assoc)")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not associate Elementos: {assoc_error}")

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] Total batch_create_elementos_with_associations: {total_time:.2f}s")
        return elemento_ids

    except Exception as e:
        logger.error(f"❌ Error batch creating Elementos: {e}")
        return []


def update_elemento(
    client: HubSpot,
    elemento_id: str,
    properties: Dict[str, Any]
) -> bool:
    """
    Update an Elemento custom object.

    Args:
        client: HubSpot client instance
        elemento_id: Elemento object ID
        properties: Properties to update (values will be converted to strings)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Convert all values to strings (HubSpot requirement)
        string_properties = {k: str(v) for k, v in properties.items() if v is not None}

        object_input = SimplePublicObjectInput(properties=string_properties)

        client.crm.objects.basic_api.update(
            object_type=OBJECT_TYPE_ELEMENTO,
            object_id=elemento_id,
            simple_public_object_input=object_input
        )

        logger.info(f"✅ Updated Elemento {elemento_id} with {len(string_properties)} properties")
        return True

    except Exception as e:
        logger.error(f"❌ Error updating Elemento {elemento_id}: {e}")
        return False


def delete_elemento(
    client: HubSpot,
    elemento_id: str
) -> bool:
    """
    Delete (archive) an Elemento custom object.

    Args:
        client: HubSpot client instance
        elemento_id: Elemento object ID

    Returns:
        True if successful, False otherwise
    """
    try:
        client.crm.objects.basic_api.archive(
            object_type=OBJECT_TYPE_ELEMENTO,
            object_id=elemento_id
        )

        logger.info(f"✅ Deleted Elemento {elemento_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Error deleting Elemento {elemento_id}: {e}")
        return False


def get_elementos_by_contenedor(
    client: HubSpot,
    contenedor_id: str,
    properties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Get all Elementos associated with a contenedor.

    Args:
        client: HubSpot client instance
        contenedor_id: Contenedor object ID
        properties: List of properties to retrieve (None = default set)

    Returns:
        List of elemento objects with their properties
    """
    try:
        # Default properties if none specified
        if properties is None:
            properties = [
                "nombre", "tipo_articulo", "cantidad", "precio_total",
                "product_sku", "cardcode", "line_item_id"
            ]

        # Use associations API to get elemento IDs
        contenedor_object_id = PublicObjectId(id=contenedor_id)
        batch_input = BatchInputPublicObjectId(inputs=[contenedor_object_id])

        associations_response = client.crm.associations.batch_api.read(
            from_object_type=OBJECT_TYPE_CONTENEDOR,
            to_object_type=OBJECT_TYPE_ELEMENTO,
            batch_input_public_object_id=batch_input
        )

        elemento_ids = []
        if hasattr(associations_response, 'results') and associations_response.results:
            for result in associations_response.results:
                if hasattr(result, 'to') and result.to:
                    for assoc in result.to:
                        assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                        if assoc_id:
                            elemento_ids.append(str(assoc_id))

        if not elemento_ids:
            logger.info(f"No Elementos found for contenedor {contenedor_id}")
            return []

        # Batch read elementos
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId

        batch_read_input = BatchReadInputSimplePublicObjectId(
            properties=properties,
            inputs=[{"id": eid} for eid in elemento_ids]
        )

        batch_response = client.crm.objects.batch_api.read(
            object_type=OBJECT_TYPE_ELEMENTO,
            batch_read_input_simple_public_object_id=batch_read_input
        )

        elementos = []
        if hasattr(batch_response, 'results'):
            for obj in batch_response.results:
                elementos.append({
                    "id": obj.id,
                    "properties": obj.properties
                })

        logger.info(f"✅ Retrieved {len(elementos)} Elementos for contenedor {contenedor_id}")
        return elementos

    except Exception as e:
        logger.error(f"❌ Error getting Elementos for contenedor {contenedor_id}: {e}")
        return []


def get_elementos_by_deal(
    client: HubSpot,
    deal_id: str,
    properties: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Get all Elementos associated with a deal (across all contenedores).

    Args:
        client: HubSpot client instance
        deal_id: HubSpot deal ID
        properties: List of properties to retrieve (None = default set)

    Returns:
        List of elemento objects with their properties
    """
    try:
        # Default properties if none specified
        if properties is None:
            properties = [
                "nombre", "nombre_contenedor", "tipo_articulo", "cantidad",
                "precio_total", "product_sku", "cardcode", "line_item_id"
            ]

        # Use associations API to get elemento IDs
        deal_object_id = PublicObjectId(id=deal_id)
        batch_input = BatchInputPublicObjectId(inputs=[deal_object_id])

        associations_response = client.crm.associations.batch_api.read(
            from_object_type="deals",
            to_object_type=OBJECT_TYPE_ELEMENTO,
            batch_input_public_object_id=batch_input
        )

        elemento_ids = []
        if hasattr(associations_response, 'results') and associations_response.results:
            for result in associations_response.results:
                if hasattr(result, 'to') and result.to:
                    for assoc in result.to:
                        assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                        if assoc_id:
                            elemento_ids.append(str(assoc_id))

        if not elemento_ids:
            logger.info(f"No Elementos found for deal {deal_id}")
            return []

        # Batch read elementos
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId

        batch_read_input = BatchReadInputSimplePublicObjectId(
            properties=properties,
            inputs=[{"id": eid} for eid in elemento_ids]
        )

        batch_response = client.crm.objects.batch_api.read(
            object_type=OBJECT_TYPE_ELEMENTO,
            batch_read_input_simple_public_object_id=batch_read_input
        )

        elementos = []
        if hasattr(batch_response, 'results'):
            for obj in batch_response.results:
                elementos.append({
                    "id": obj.id,
                    "properties": obj.properties
                })

        logger.info(f"✅ Retrieved {len(elementos)} Elementos for deal {deal_id}")
        return elementos

    except Exception as e:
        logger.error(f"❌ Error getting Elementos for deal {deal_id}: {e}")
        return []


# ============================================================================
# HELPER FUNCTIONS FOR DUAL TRACKING INTEGRATION
# ============================================================================

def find_contenedor_by_nombre(
    client: HubSpot,
    nombre: str
) -> Optional[str]:
    """
    Find Contenedor ID by its unique nombre property.

    Args:
        client: HubSpot client instance
        nombre: Unique container name (e.g., "46298478914-001")

    Returns:
        Contenedor ID if found, None otherwise
    """
    try:
        from hubspot.crm.objects import PublicObjectSearchRequest

        search_request = PublicObjectSearchRequest(
            filter_groups=[
                {
                    "filters": [
                        {
                            "propertyName": "nombre",
                            "operator": "EQ",
                            "value": nombre
                        }
                    ]
                }
            ],
            properties=["nombre"],
            limit=1
        )

        search_result = client.crm.objects.search_api.do_search(
            object_type=OBJECT_TYPE_CONTENEDOR,
            public_object_search_request=search_request
        )

        if hasattr(search_result, 'results') and search_result.results:
            contenedor_id = search_result.results[0].id
            logger.info(f"✅ Found Contenedor {contenedor_id} with nombre '{nombre}'")
            return contenedor_id

        logger.info(f"No Contenedor found with nombre '{nombre}'")
        return None

    except Exception as e:
        logger.error(f"❌ Error searching Contenedor by nombre '{nombre}': {e}")
        return None


def find_elemento_by_nombre(
    client: HubSpot,
    nombre: str
) -> Optional[str]:
    """
    Find Elemento ID by its unique nombre property.

    Args:
        client: HubSpot client instance
        nombre: Elemento name (e.g., "46298478914-001 - Vino Tinto")

    Returns:
        Elemento ID if found, None otherwise
    """
    try:
        from hubspot.crm.objects import PublicObjectSearchRequest

        search_request = PublicObjectSearchRequest(
            filter_groups=[
                {
                    "filters": [
                        {
                            "propertyName": "nombre",
                            "operator": "EQ",
                            "value": nombre
                        }
                    ]
                }
            ],
            properties=["nombre"],
            limit=1
        )

        search_result = client.crm.objects.search_api.do_search(
            object_type=OBJECT_TYPE_ELEMENTO,
            public_object_search_request=search_request
        )

        if hasattr(search_result, 'results') and search_result.results:
            elemento_id = search_result.results[0].id
            logger.info(f"✅ Found Elemento {elemento_id} with nombre '{nombre}'")
            return elemento_id

        logger.info(f"No Elemento found with nombre '{nombre}'")
        return None

    except Exception as e:
        logger.error(f"❌ Error searching Elemento by nombre '{nombre}': {e}")
        return None


def batch_delete_custom_objects(
    client: HubSpot,
    object_type: str,
    object_ids: List[str]
) -> int:
    """
    Batch delete (archive) multiple custom objects.

    This is much faster than deleting them one by one.

    Args:
        client: HubSpot client instance
        object_type: Object type (OBJECT_TYPE_CONTENEDOR or OBJECT_TYPE_ELEMENTO)
        object_ids: List of object IDs to delete

    Returns:
        Number of objects deleted
    """
    if not object_ids:
        return 0

    try:
        from hubspot.crm.objects import BatchInputSimplePublicObjectId

        batch_input = BatchInputSimplePublicObjectId(
            inputs=[{"id": obj_id} for obj_id in object_ids]
        )

        client.crm.objects.batch_api.archive(
            object_type=object_type,
            batch_input_simple_public_object_id=batch_input
        )

        logger.info(f"✅ Batch deleted {len(object_ids)} {object_type} objects in 1 API call")
        return len(object_ids)

    except Exception as e:
        logger.error(f"❌ Error batch deleting {object_type} objects: {e}")
        return 0


def delete_contenedores_by_nombres(
    client: HubSpot,
    nombres: List[str]
) -> int:
    """
    Delete Contenedores by their unique nombre properties.

    When a Contenedor is deleted, all its associated Elementos are also deleted.
    This maintains referential integrity.

    OPTIMIZED: Uses batch deletion for better performance.

    Args:
        client: HubSpot client instance
        nombres: List of container names to delete (e.g., ["46298478914-001", "46298478914-002"])

    Returns:
        Number of objects deleted (contenedores + elementos)
    """
    if not nombres:
        return 0

    deleted_count = 0
    start_time = time.time()

    try:
        logger.info(f"🗑️  Deleting {len(nombres)} Contenedores by nombre...")

        # Step 1: Find all contenedor IDs
        find_start = time.time()
        contenedor_ids = []
        for nombre in nombres:
            contenedor_id = find_contenedor_by_nombre(client, nombre)
            if contenedor_id:
                contenedor_ids.append(contenedor_id)

        find_time = time.time() - find_start
        logger.info(f"⏱️  [TIMER] Found {len(contenedor_ids)} contenedores by nombre in {find_time:.2f}s ({find_time/len(nombres):.2f}s per search)")

        if not contenedor_ids:
            logger.info("No Contenedores found to delete")
            return 0

        # Step 2: Get all elementos associated with these contenedores
        fetch_start = time.time()
        all_elemento_ids = []
        for contenedor_id in contenedor_ids:
            elementos = get_elementos_by_contenedor(client, contenedor_id, properties=["nombre"])
            all_elemento_ids.extend([elem["id"] for elem in elementos])

        fetch_time = time.time() - fetch_start
        logger.info(f"⏱️  [TIMER] Fetched {len(all_elemento_ids)} elementos in {fetch_time:.2f}s ({fetch_time/len(contenedor_ids):.2f}s per contenedor)")

        # Step 3: Batch delete all elementos
        if all_elemento_ids:
            delete_elem_start = time.time()
            elemento_count = batch_delete_custom_objects(client, OBJECT_TYPE_ELEMENTO, all_elemento_ids)
            delete_elem_time = time.time() - delete_elem_start
            deleted_count += elemento_count
            logger.info(f"   ✅ Deleted {elemento_count} Elementos ({delete_elem_time:.2f}s)")

        # Step 4: Batch delete all contenedores
        delete_cont_start = time.time()
        contenedor_count = batch_delete_custom_objects(client, OBJECT_TYPE_CONTENEDOR, contenedor_ids)
        delete_cont_time = time.time() - delete_cont_start
        deleted_count += contenedor_count
        logger.info(f"   ✅ Deleted {contenedor_count} Contenedores ({delete_cont_time:.2f}s)")

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] Total delete_contenedores_by_nombres: {total_time:.2f}s")
        logger.info(f"✅ Total deleted: {deleted_count} custom objects")
        return deleted_count

    except Exception as e:
        logger.error(f"❌ Error deleting Contenedores by nombres: {e}")
        return deleted_count


def delete_elementos_by_nombres(
    client: HubSpot,
    nombres: List[str]
) -> int:
    """
    Delete Elementos by their unique nombre properties.

    Use this for deleting individual elementos (e.g., services not tied to containers).

    Args:
        client: HubSpot client instance
        nombres: List of elemento names to delete

    Returns:
        Number of elementos deleted
    """
    if not nombres:
        return 0

    deleted_count = 0

    try:
        logger.info(f"🗑️  Deleting {len(nombres)} Elementos by nombre...")

        for nombre in nombres:
            # Find the elemento
            elemento_id = find_elemento_by_nombre(client, nombre)
            if not elemento_id:
                continue

            # Delete the elemento
            if delete_elemento(client, elemento_id):
                deleted_count += 1
                logger.info(f"   ✅ Deleted Elemento {elemento_id} ('{nombre}')")

        logger.info(f"✅ Total deleted: {deleted_count} Elementos")
        return deleted_count

    except Exception as e:
        logger.error(f"❌ Error deleting Elementos by nombres: {e}")
        return deleted_count


# ============================================================================
# PUBLISH CUSTOM OBJECTS FROM LINE ITEMS (ASYNC BACKGROUND TASK)
# ============================================================================

def process_deal_custom_objects_from_line_items(deal_id: str) -> Dict[str, Any]:
    """
    Procesa todos los line items de un deal y crea los objetos personalizados correspondientes.

    Esta función:
    1. Lee todos los line items del deal
    2. Elimina objetos personalizados existentes
    3. Crea objetos Contenedor para line items de tipo contenedor
    4. Crea objetos Elemento para el resto de line items
    5. Establece las asociaciones correctas

    **Formato del nombre de line item**: "{deal_id}-{numero_container:03d} - {nombre}"
    - Ejemplo contenedor: "46298478914-001 - D20DR"
    - Ejemplo elemento: "46298478914-001 - VINO TINTO TEMPRANILLO"

    Args:
        deal_id: ID del deal en HubSpot

    Returns:
        Dict con estadísticas del proceso (contenedores creados, elementos creados, etc.)
    """
    start_time = time.time()
    stats = {
        "deal_id": deal_id,
        "contenedores_created": 0,
        "elementos_created": 0,
        "contenedores_deleted": 0,
        "elementos_deleted": 0,
        "errors": []
    }

    try:
        from app.services.hubspot_client import hubspot_client
        client = hubspot_client.get_client()

        logger.info(f"📦 [PUBLISH] Starting custom objects publish for deal {deal_id}")

        # Step 0: Get associated contact and company from the deal
        contact_id = None
        company_id = None

        try:
            from app.routes.deals import get_associated_contacts, get_associated_companies

            # Get contact ID
            contact_ids = get_associated_contacts(deal_id)
            if contact_ids:
                contact_id = contact_ids[0]
                logger.info(f"📞 Found contact {contact_id} associated with deal {deal_id}")
            else:
                logger.warning(f"⚠️ No contact found associated with deal {deal_id}")

            # Get company ID
            company_ids = get_associated_companies(deal_id)
            if company_ids:
                company_id = company_ids[0]
                logger.info(f"🏢 Found company {company_id} associated with deal {deal_id}")
            else:
                logger.warning(f"⚠️ No company found associated with deal {deal_id}")
        except Exception as assoc_error:
            logger.warning(f"⚠️ Could not fetch contact/company associations: {assoc_error}")

        # Step 1: Fetch all line items del deal
        fetch_start = time.time()
        line_items = _fetch_all_line_items_for_deal(client, deal_id)
        fetch_time = time.time() - fetch_start
        logger.info(f"⏱️  [TIMER] Fetched {len(line_items)} line items in {fetch_time:.2f}s")

        if not line_items:
            logger.warning(f"No line items found for deal {deal_id}")
            return stats

        # Step 2: Delete existing custom objects
        delete_start = time.time()
        existing_contenedores = []
        try:
            existing_objs = get_contenedores_by_deal(client, deal_id, properties=["nombre"])
            existing_contenedores = [obj["properties"].get("nombre") for obj in existing_objs if obj.get("properties", {}).get("nombre")]
        except Exception as e:
            logger.warning(f"Error fetching existing contenedores: {e}")

        if existing_contenedores:
            deleted_count = delete_contenedores_by_nombres(client, existing_contenedores)
            stats["contenedores_deleted"] = len(existing_contenedores)
            stats["elementos_deleted"] = deleted_count - len(existing_contenedores)
            logger.info(f"🗑️  Deleted {deleted_count} existing custom objects")

        delete_time = time.time() - delete_start
        logger.info(f"⏱️  [TIMER] Delete phase: {delete_time:.2f}s")

        # Step 3: Parse line items and group by container
        parse_start = time.time()
        containers_map, standalone_elements = _parse_line_items_to_custom_objects(line_items)
        parse_time = time.time() - parse_start
        logger.info(f"⏱️  [TIMER] Parsed {len(containers_map)} containers and {len(standalone_elements)} standalone elements in {parse_time:.2f}s")

        # Step 4: Batch create Contenedores
        if containers_map:
            create_cont_start = time.time()
            contenedores_data = []
            for container_name, container_info in containers_map.items():
                contenedores_data.append(container_info["contenedor_data"])

            contenedor_map = batch_create_contenedores_with_associations(
                client=client,
                contenedores_data=contenedores_data,
                deal_id=deal_id,
                contact_id=contact_id,
                company_id=company_id
            )
            stats["contenedores_created"] = len(contenedor_map)
            create_cont_time = time.time() - create_cont_start
            logger.info(f"⏱️  [TIMER] Created {len(contenedor_map)} contenedores in {create_cont_time:.2f}s")

            # Log the mapping for debugging
            logger.info(f"📦 [MAPPING] Created contenedor map: {contenedor_map}")

            # Step 5: Batch create Elementos for each container
            create_elem_start = time.time()
            for container_name, container_info in containers_map.items():
                # Get the correct contenedor_id from the map using container name
                contenedor_id = contenedor_map.get(container_name)

                if not contenedor_id:
                    logger.warning(f"⚠️ No contenedor ID found for container '{container_name}', skipping elementos")
                    continue

                elementos_data = container_info["elementos_data"]

                if elementos_data:
                    elemento_ids = batch_create_elementos_with_associations(
                        client=client,
                        elementos_data=elementos_data,
                        deal_id=deal_id,
                        contenedor_id=contenedor_id,
                        contact_id=contact_id,
                        company_id=company_id
                    )
                    stats["elementos_created"] += len(elemento_ids)
                    logger.info(f"✅ Created {len(elemento_ids)} elementos for {container_name} (contenedor_id: {contenedor_id})")

            create_elem_time = time.time() - create_elem_start
            logger.info(f"⏱️  [TIMER] Created {stats['elementos_created']} elementos in {create_elem_time:.2f}s")

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] ========== TOTAL PUBLISH CUSTOM OBJECTS: {total_time:.2f}s ==========")
        logger.info(f"✅ [PUBLISH] Completed: {stats['contenedores_created']} contenedores, {stats['elementos_created']} elementos")

        return stats

    except Exception as e:
        error_msg = f"Error processing custom objects for deal {deal_id}: {e}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        stats["errors"].append(error_msg)
        return stats


def _fetch_all_line_items_for_deal(client: HubSpot, deal_id: str) -> List[Dict[str, Any]]:
    """Fetch all line items associated with a deal."""
    try:
        from hubspot.crm.associations import BatchInputPublicObjectId, PublicObjectId

        # Get line item IDs associated with deal
        deal_object_id = PublicObjectId(id=deal_id)
        batch_input = BatchInputPublicObjectId(inputs=[deal_object_id])

        associations_response = client.crm.associations.batch_api.read(
            from_object_type="deals",
            to_object_type="line_items",
            batch_input_public_object_id=batch_input
        )

        line_item_ids = []
        if hasattr(associations_response, 'results') and associations_response.results:
            for result in associations_response.results:
                if hasattr(result, 'to') and result.to:
                    for assoc in result.to:
                        assoc_id = getattr(assoc, 'id', None) or getattr(assoc, 'toObjectId', None)
                        if assoc_id:
                            line_item_ids.append(str(assoc_id))

        if not line_item_ids:
            return []

        # Batch read line items with all properties
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId

        properties = [
            "name", "hs_sku", "quantity", "price", "amount",
            "hs_cost_of_goods_sold", "hs_margin", "hs_product_id"
        ]

        batch_read_input = BatchReadInputSimplePublicObjectId(
            properties=properties,
            inputs=[{"id": lid} for lid in line_item_ids]
        )

        batch_response = client.crm.objects.batch_api.read(
            object_type="line_items",
            batch_read_input_simple_public_object_id=batch_read_input
        )

        line_items = []
        if hasattr(batch_response, 'results'):
            for obj in batch_response.results:
                line_items.append({
                    "id": obj.id,
                    "properties": obj.properties
                })

        return line_items

    except Exception as e:
        logger.error(f"Error fetching line items for deal {deal_id}: {e}")
        return []


def _parse_line_items_to_custom_objects(line_items: List[Dict[str, Any]]) -> tuple:
    """
    Parsea line items y los agrupa por contenedor.

    Formato esperado del nombre: "{deal_id}-{numero:03d} - {descripcion}"
    - "46298478914-001 - D20DR" -> Contenedor
    - "46298478914-001 - VINO TINTO" -> Elemento del contenedor 001

    Returns:
        (containers_map, standalone_elements) donde:
        - containers_map: Dict[container_name, {"contenedor_data": {...}, "elementos_data": [...]}]
        - standalone_elements: List[elemento_data] para elementos sin contenedor
    """
    containers_map = {}
    standalone_elements = []

    logger.info(f"🔍 [PARSE] Processing {len(line_items)} line items")

    # Primero identificar contenedores
    # Los contenedores tienen nombres como "46298478914-001", "46298478914-002", etc.
    # mientras que los elementos tienen nombres como "46298478914-001 - PRODUCTO XYZ"
    container_skus = {"CONTAINER-D20DR", "CONTAINER-D40DR", "CONTAINER-D20W", "CONTAINER-D40HD",
                     "CONTAINER-D45HD", "CONTAINER-D45W", "CONTAINER-PALETS", "CONTAINER-R20RF",
                     "CONTAINER-R40RF", "CONTAINER-TRAILER_80", "CONTAINER-CAMION_16P"}

    for line_item in line_items:
        props = line_item.get("properties", {})
        name = props.get("name", "")
        sku = props.get("hs_sku", "")

        # Check if it's a container by SKU OR by name pattern
        is_container_by_sku = sku in container_skus
        is_container_by_name = bool(re.match(r'^\d+-\d+$', name.strip()))  # Matches "46298478914-001"

        logger.info(f"  📄 Line item: name='{name}', sku='{sku}', is_container_by_sku={is_container_by_sku}, is_container_by_name={is_container_by_name}")

        if is_container_by_sku or is_container_by_name:
            # Extract container name (format: "dealId-XXX" or "dealId-XXX - SKU")
            parts = name.split(" - ")
            container_name = parts[0].strip()  # "46298478914-001"

            # Get tipo_contenedor from SKU if available, otherwise try to infer
            if sku and sku.startswith("CONTAINER-"):
                tipo_contenedor = sku.replace("CONTAINER-", "")  # "D20DR"
            elif len(parts) > 1:
                tipo_contenedor = parts[1].strip()  # Get from name if split by " - "
            else:
                tipo_contenedor = "D20DR"  # Default

            try:
                numero_container = int(container_name.split("-")[-1])  # 1
            except (ValueError, IndexError):
                numero_container = 1

            logger.info(f"📦 Detected container: {container_name} (tipo: {tipo_contenedor})")

            containers_map[container_name] = {
                "contenedor_data": {
                    "nombre": container_name,
                    "tipo_contenedor": tipo_contenedor,
                    "numero_container": numero_container,
                    "line_item_id": line_item["id"],
                    "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
                },
                "elementos_data": []
            }

    # Luego asignar elementos a contenedores
    for line_item in line_items:
        props = line_item.get("properties", {})
        name = props.get("name", "")
        sku = props.get("hs_sku", "")

        # Skip containers (using same logic as above)
        is_container_by_sku = sku in container_skus
        is_container_by_name = bool(re.match(r'^\d+-\d+$', name.strip()))
        if is_container_by_sku or is_container_by_name:
            continue

        # Extract container name from element name (format: "dealId-XXX - PRODUCT NAME")
        parts = name.split(" - ")
        if len(parts) >= 2:
            container_name = parts[0].strip()  # "46298478914-001"
            element_name = name  # Full name including container prefix

            # Determine tipo_articulo based on SKU or name
            tipo_articulo = "fabricante"  # Default
            if "FLETE" in name.upper() or "FREIGHT" in name.upper():
                tipo_articulo = "flete"
            elif "SERVICIO" in name.upper() or "SERVICE" in name.upper():
                tipo_articulo = "servicio"
            elif "COMISION" in name.upper() or "INTERMEDIARIO" in name.upper():
                tipo_articulo = "intermediario"
            elif "SEGURO" in name.upper() or "INSURANCE" in name.upper():
                tipo_articulo = "seguro"

            elemento_data = {
                "nombre": element_name,
                "nombre_contenedor": container_name,
                "tipo_articulo": tipo_articulo,
                "cantidad": float(props.get("quantity", 1)) if props.get("quantity") else 1.0,
                "product_sku": sku if sku else None,
                "precio_unitario": float(props.get("price", 0)) if props.get("price") else None,
                "precio_de_coste": float(props.get("hs_cost_of_goods_sold", 0)) if props.get("hs_cost_of_goods_sold") else None,
                "precio_total": float(props.get("amount", 0)) if props.get("amount") else None,
                "margen_unitario": float(props.get("hs_margin", 0)) if props.get("hs_margin") else None,
                "line_item_id": line_item["id"],
                "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
            }

            # Add to container if exists, otherwise standalone
            if container_name in containers_map:
                containers_map[container_name]["elementos_data"].append(elemento_data)
            else:
                standalone_elements.append(elemento_data)
        else:
            # No container prefix, standalone element
            standalone_elements.append({
                "nombre": name,
                "tipo_articulo": "servicio",
                "cantidad": float(props.get("quantity", 1)) if props.get("quantity") else 1.0,
                "product_sku": sku if sku else None,
                "precio_unitario": float(props.get("price", 0)) if props.get("price") else None,
                "precio_total": float(props.get("amount", 0)) if props.get("amount") else None,
                "line_item_id": line_item["id"],
                "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
            })

    # Calculate importe_pendiente_cobro and price for each container
    for container_name, container_info in containers_map.items():
        # Sum up precio_total from all elementos in this container
        importe_total = 0.0
        for elemento in container_info["elementos_data"]:
            precio_total = elemento.get("precio_total")
            if precio_total is not None:
                importe_total += precio_total

        # Set both importe_pendiente_cobro and price in contenedor_data
        container_info["contenedor_data"]["importe_pendiente_cobro"] = importe_total
        container_info["contenedor_data"]["price"] = importe_total
        logger.info(f"💰 Container {container_name}: price = {importe_total}, importe_pendiente_cobro = {importe_total}")

    # Log summary
    total_elementos_in_containers = sum(len(c["elementos_data"]) for c in containers_map.values())
    logger.info(f"✅ [PARSE] Summary: {len(containers_map)} containers with {total_elementos_in_containers} elements, {len(standalone_elements)} standalone elements")
    for container_name, container_info in containers_map.items():
        logger.info(f"   📦 {container_name}: {len(container_info['elementos_data'])} elements, importe: {container_info['contenedor_data'].get('importe_pendiente_cobro', 0)}")

    return containers_map, standalone_elements
