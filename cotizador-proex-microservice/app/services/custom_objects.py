import logging
import json
import time
import re
import requests
from typing import Optional, List, Dict, Any, Callable
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
    ASSOCIATION_TYPE_ID_ELEMENTO_TO_COMPANY,
    OBJECT_TYPE_PAGO,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PAGO,
    ASSOCIATION_TYPE_IDS_CONTENEDOR_TO_PAGO,
    PAGO_PROP_ACTUALIZACION_PEDIDO,
    PAGO_PROP_DOCTYPE,
    PAGO_DOCTYPE_CONTENEDOR,
    CONTENEDOR_PROP_ACTUALIZACION_PEDIDO,
    DEAL_PROP_ACTUALIZACION_PEDIDO,
)
from app.utils.property_helpers import round_margin, round_price

logger = logging.getLogger(__name__)

# CONTENEDOR CRUD OPERATIONS

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
        ocupacion: Occupancy percentage (90-103%)
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

            for key in ["container_config", "line_item_id", "numero_precinto", "documentos_a_generar"]:
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

        assoc_start = time.time()
        access_token = client.access_token
        contenedor_ids = list(contenedor_map.values())
        total_associations = 0

        deal_pairs = [(cid, deal_id) for cid in contenedor_ids]
        deal_ok, deal_errs = batch_create_associations_v4(
            access_token, OBJECT_TYPE_CONTENEDOR, "deals",
            ASSOCIATION_TYPE_ID_DEAL_TO_CONTENEDOR, deal_pairs,
        )
        total_associations += deal_ok
        if deal_errs:
            logger.warning(f"⚠️ Errors associating Contenedores to deal: {deal_errs}")

        if contact_id:
            contact_pairs = [(cid, contact_id) for cid in contenedor_ids]
            contact_ok, contact_errs = batch_create_associations_v4(
                access_token, OBJECT_TYPE_CONTENEDOR, "contacts",
                ASSOCIATION_TYPE_ID_CONTENEDOR_TO_CONTACT, contact_pairs,
            )
            total_associations += contact_ok
            if contact_errs:
                logger.warning(f"⚠️ Errors associating Contenedores to contact {contact_id}: {contact_errs}")

        if company_id:
            company_pairs = [(cid, company_id) for cid in contenedor_ids]
            company_ok, company_errs = batch_create_associations_v4(
                access_token, OBJECT_TYPE_CONTENEDOR, "companies",
                ASSOCIATION_TYPE_ID_CONTENEDOR_TO_COMPANY, company_pairs,
            )
            total_associations += company_ok
            if company_errs:
                logger.warning(f"⚠️ Errors associating Contenedores to company {company_id}: {company_errs}")

        assoc_time = time.time() - assoc_start
        logger.info(
            f"✅ Created {total_associations} associations for {len(contenedor_map)} Contenedores "
            f"(Deal + Contact + Company) in {assoc_time:.2f}s via batch"
        )

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

# ELEMENTO CRUD OPERATIONS

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
    tipo_iva_soportado: Optional[str] = None,
    codigo_proveedor: Optional[str] = None,
    line_item_id: Optional[str] = None,
    almacen: Optional[str] = None,
    nombre_es: Optional[str] = None,
    nombre_en: Optional[str] = None,
    nombre_fr: Optional[str] = None,
    formato: Optional[str] = None,
    marca: Optional[str] = None
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
            margen_round = round_margin(margen_unitario)
            if margen_round is not None:
                properties["margen_unitario"] = str(margen_round)
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
            properties["tipo_iva_repercutido"] = tipo_iva_repercutido.upper()  # HubSpot expects uppercase (R21, EI21, EX…)
        if tipo_iva_soportado:
            properties["tipo_iva_soportado"] = tipo_iva_soportado
        if codigo_proveedor:
            properties["codigo_proveedor"] = codigo_proveedor
        if line_item_id:
            properties["line_item_id"] = line_item_id
        if almacen:
            properties["almacen"] = almacen
        if nombre_es:
            properties["nombre_es"] = nombre_es
        if nombre_en:
            properties["nombre_en"] = nombre_en
        if nombre_fr:
            properties["nombre_fr"] = nombre_fr
        if formato:
            properties["formato"] = formato
        if marca:
            properties["marca"] = marca

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
                       "peso_neto_unitario", "peso_bruto_unitario"]:
                if key in data and data[key] is not None:
                    properties[key] = str(data[key])
                elif key in ("precio_unitario", "precio_de_coste"):
                    logger.warning(
                        f"[batch_create_elementos] {key} es None para "
                        f"tipo_articulo={data.get('tipo_articulo')!r} "
                        f"nombre={data.get('nombre')!r} — propiedad omitida"
                    )

            if data.get("margen_unitario") is not None:
                margen_round = round_margin(data["margen_unitario"])
                if margen_round is not None:
                    properties["margen_unitario"] = str(margen_round)

            for key in ["product_sku", "cardcode", "nombre_proveedor", "codigo_proveedor", "line_item_id", "almacen",
                        "nombre_es", "nombre_en", "nombre_fr", "formato", "marca"]:
                if key in data and data[key]:
                    properties[key] = data[key]

            if "tipo_iva_repercutido" in data and data["tipo_iva_repercutido"]:
                properties["tipo_iva_repercutido"] = data["tipo_iva_repercutido"].upper()
            if "tipo_iva_soportado" in data and data["tipo_iva_soportado"]:
                properties["tipo_iva_soportado"] = data["tipo_iva_soportado"]

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

        assoc_start = time.time()
        access_token = client.access_token
        total_associations = 0

        deal_pairs = [(eid, deal_id) for eid in elemento_ids]
        deal_ok, deal_errs = batch_create_associations_v4(
            access_token, OBJECT_TYPE_ELEMENTO, "deals",
            ASSOCIATION_TYPE_ID_DEAL_TO_ELEMENTO, deal_pairs,
        )
        total_associations += deal_ok
        if deal_errs:
            logger.warning(f"⚠️ Errors associating Elementos to deal: {deal_errs}")

        contenedor_pairs = [(contenedor_id, eid) for eid in elemento_ids]
        cont_ok, cont_errs = batch_create_associations_v4(
            access_token, OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO,
            ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTENEDOR, contenedor_pairs,
        )
        total_associations += cont_ok
        if cont_errs:
            logger.warning(f"⚠️ Errors associating Contenedor {contenedor_id} to Elementos: {cont_errs}")

        if contact_id:
            contact_pairs = [(eid, contact_id) for eid in elemento_ids]
            contact_ok, contact_errs = batch_create_associations_v4(
                access_token, OBJECT_TYPE_ELEMENTO, "contacts",
                ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTACT, contact_pairs,
            )
            total_associations += contact_ok
            if contact_errs:
                logger.warning(f"⚠️ Errors associating Elementos to contact {contact_id}: {contact_errs}")

        if company_id:
            company_pairs = [(eid, company_id) for eid in elemento_ids]
            company_ok, company_errs = batch_create_associations_v4(
                access_token, OBJECT_TYPE_ELEMENTO, "companies",
                ASSOCIATION_TYPE_ID_ELEMENTO_TO_COMPANY, company_pairs,
            )
            total_associations += company_ok
            if company_errs:
                logger.warning(f"⚠️ Errors associating Elementos to company {company_id}: {company_errs}")

        assoc_time = time.time() - assoc_start
        logger.info(
            f"✅ Created {total_associations} associations for {len(elemento_ids)} Elementos "
            f"(Deal + Contenedor + Contact + Company) in {assoc_time:.2f}s via batch"
        )

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] Total batch_create_elementos_with_associations: {total_time:.2f}s")
        return elemento_ids

    except Exception as e:
        logger.error(f"❌ Error batch creating Elementos: {e}")
        return []

def _touch_contenedores_y_deals(contenedor_ids: List[str], ts_ms: int, headers: Dict[str, str]) -> None:
    """Best-effort: escribe actualizacion_pedido=ts_ms en cada contenedor y en
    cada deal asociado a esos contenedores. Reusa el mismo timestamp."""
    if not contenedor_ids:
        return
    ts_str = str(ts_ms)

    upd_cont = f"https://api.hubapi.com/crm/v3/objects/{OBJECT_TYPE_CONTENEDOR}/batch/update"
    for i in range(0, len(contenedor_ids), 100):
        chunk = contenedor_ids[i:i + 100]
        payload = {"inputs": [
            {"id": cid, "properties": {CONTENEDOR_PROP_ACTUALIZACION_PEDIDO: ts_str}}
            for cid in chunk
        ]}
        try:
            resp = requests.post(upd_cont, headers=headers, json=payload, timeout=30)
            if not resp.ok:
                logger.warning(f"[touch_pedido] contenedor batch update failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.warning(f"[touch_pedido] contenedor batch update exception: {e}")

    deal_ids = set()
    read_assoc = f"https://api.hubapi.com/crm/v4/associations/{OBJECT_TYPE_CONTENEDOR}/deals/batch/read"
    for i in range(0, len(contenedor_ids), 100):
        chunk = contenedor_ids[i:i + 100]
        payload = {"inputs": [{"id": cid} for cid in chunk]}
        try:
            resp = requests.post(read_assoc, headers=headers, json=payload, timeout=30)
            if not resp.ok:
                logger.warning(f"[touch_pedido] read contenedor->deal failed: {resp.status_code} - {resp.text}")
                continue
            for row in resp.json().get("results", []):
                for assoc in row.get("to", []) or []:
                    did = assoc.get("toObjectId") or assoc.get("id")
                    if did is not None:
                        deal_ids.add(str(did))
        except Exception as e:
            logger.warning(f"[touch_pedido] read contenedor->deal exception: {e}")

    if not deal_ids:
        logger.info(f"[touch_pedido] contenedores={len(contenedor_ids)} sin deals asociados")
        return

    upd_deal = "https://api.hubapi.com/crm/v3/objects/deals/batch/update"
    deal_list = list(deal_ids)
    for i in range(0, len(deal_list), 100):
        chunk = deal_list[i:i + 100]
        payload = {"inputs": [
            {"id": did, "properties": {DEAL_PROP_ACTUALIZACION_PEDIDO: ts_str}}
            for did in chunk
        ]}
        try:
            resp = requests.post(upd_deal, headers=headers, json=payload, timeout=30)
            if not resp.ok:
                logger.warning(f"[touch_pedido] deal batch update failed: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.warning(f"[touch_pedido] deal batch update exception: {e}")

    logger.info(f"[touch_pedido] contenedores={len(contenedor_ids)} deals={len(deal_ids)} ts={ts_ms}")


def touch_pagos_for_contenedores(client: HubSpot, contenedor_co_ids) -> int:
    """Marca actualizacion_pedido=now (ms UTC) en los pagos (2-44408688) con
    doctype='contenedor' asociados a los contenedores indicados via typeId
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PAGO (499). Propaga el mismo timestamp
    al Contenedor y a los Deals asociados. Best-effort: nunca propaga
    excepciones.

    Devuelve cuántos pagos quedaron efectivamente actualizados.
    """
    try:
        ids = []
        seen = set()
        for cid in contenedor_co_ids or []:
            cid_str = str(cid).strip() if cid is not None else ""
            if cid_str and cid_str not in seen:
                seen.add(cid_str)
                ids.append(cid_str)
        if not ids:
            return 0

        access_token = client.access_token
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        logger.info(f"[touch_pagos] reading assoc {OBJECT_TYPE_CONTENEDOR} -> {OBJECT_TYPE_PAGO} for {len(ids)} contenedor(es) ids={ids}")

        pago_ids = set()
        url = f"https://api.hubapi.com/crm/v4/associations/{OBJECT_TYPE_CONTENEDOR}/{OBJECT_TYPE_PAGO}/batch/read"
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            payload = {"inputs": [{"id": cid} for cid in chunk]}
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if not resp.ok:
                    logger.warning(f"[touch_pagos] read associations failed: {resp.status_code} - {resp.text}")
                    continue
                body = resp.json()
                rows = body.get("results", [])
                logger.info(f"[touch_pagos] v4 batch response rows={len(rows)} body_keys={list(body.keys())}")
                if not rows:
                    logger.info(f"[touch_pagos] empty results; raw body={body}")
                for row in rows:
                    from_id = (row.get("from") or {}).get("id") or row.get("id")
                    to_list = row.get("to", []) or []
                    logger.info(f"[touch_pagos] from={from_id} to_count={len(to_list)} sample={to_list[:2] if to_list else None}")
                    for assoc in to_list:
                        assoc_types = assoc.get("associationTypes", []) or []
                        type_ids_found = [t.get("typeId") for t in assoc_types]
                        if any(tid in ASSOCIATION_TYPE_IDS_CONTENEDOR_TO_PAGO for tid in type_ids_found):
                            pass
                        elif not assoc_types:
                            logger.info(f"[touch_pagos] assoc has NO associationTypes block; assuming match. assoc keys={list(assoc.keys())}")
                        else:
                            logger.info(f"[touch_pagos] assoc typeIds={type_ids_found} doesn't match expected {sorted(ASSOCIATION_TYPE_IDS_CONTENEDOR_TO_PAGO)}; skipping")
                            continue
                        target_id = assoc.get("toObjectId") or assoc.get("id")
                        if target_id is not None:
                            pago_ids.add(str(target_id))
            except Exception as e:
                logger.warning(f"[touch_pagos] read associations exception: {e}")

        candidatos = len(pago_ids)
        if not pago_ids:
            logger.info(f"[touch_pagos] cont={len(ids)} pagos_candidatos=0 pagos_actualizados=0")
            return 0

        contenedor_pago_ids = []
        pago_ids_list = list(pago_ids)
        for i in range(0, len(pago_ids_list), 100):
            chunk = pago_ids_list[i:i + 100]
            payload = {"properties": [PAGO_PROP_DOCTYPE], "inputs": [{"id": pid} for pid in chunk]}
            read_url = f"https://api.hubapi.com/crm/v3/objects/{OBJECT_TYPE_PAGO}/batch/read"
            try:
                resp = requests.post(read_url, headers=headers, json=payload, timeout=30)
                if not resp.ok:
                    logger.warning(f"[touch_pagos] batch read doctype failed: {resp.status_code} - {resp.text}")
                    continue
                for item in resp.json().get("results", []):
                    doctype = (item.get("properties", {}) or {}).get(PAGO_PROP_DOCTYPE)
                    if (doctype or "").strip().lower() == PAGO_DOCTYPE_CONTENEDOR:
                        contenedor_pago_ids.append(str(item.get("id")))
            except Exception as e:
                logger.warning(f"[touch_pagos] batch read doctype exception: {e}")

        if not contenedor_pago_ids:
            logger.info(f"[touch_pagos] cont={len(ids)} pagos_candidatos={candidatos} pagos_actualizados=0")
            return 0

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        actualizados = 0
        update_url = f"https://api.hubapi.com/crm/v3/objects/{OBJECT_TYPE_PAGO}/batch/update"
        for i in range(0, len(contenedor_pago_ids), 100):
            chunk = contenedor_pago_ids[i:i + 100]
            payload = {"inputs": [{"id": pid, "properties": {PAGO_PROP_ACTUALIZACION_PEDIDO: str(now_ms)}} for pid in chunk]}
            try:
                resp = requests.post(update_url, headers=headers, json=payload, timeout=30)
                if resp.ok:
                    actualizados += len(chunk)
                else:
                    logger.warning(f"[touch_pagos] batch update failed: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.warning(f"[touch_pagos] batch update exception: {e}")

        logger.info(
            f"[touch_pagos] cont={len(ids)} pagos_candidatos={candidatos} pagos_actualizados={actualizados}"
        )

        if contenedor_pago_ids:
            _touch_contenedores_y_deals(ids, now_ms, headers)

        return actualizados
    except Exception as e:
        logger.warning(f"[touch_pagos] unexpected error: {e}")
        return 0

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
        string_properties = {k: str(v) for k, v in properties.items() if v is not None}
        object_input = SimplePublicObjectInput(properties=string_properties)

        client.crm.objects.basic_api.update(
            object_type=OBJECT_TYPE_ELEMENTO,
            object_id=elemento_id,
            simple_public_object_input=object_input
        )

        logger.info(f"Updated Elemento {elemento_id} with {len(string_properties)} properties")
        return True

    except Exception as e:
        logger.error(f"[update_elemento] error id={elemento_id}: {e}")
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
            logger.info(f"No Elementos associated to contenedor {contenedor_id}")
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

# HELPER FUNCTIONS FOR DUAL TRACKING INTEGRATION

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

def batch_create_associations_v4(
    access_token: str,
    from_object_type: str,
    to_object_type: str,
    association_type_id: int,
    pairs: List[tuple],
    association_category: str = "USER_DEFINED",
    chunk_size: int = 100,
) -> tuple:
    """Crea N asociaciones del mismo `(from_object_type, to_object_type)` en una sola llamada batch v4.

    `pairs`: lista de `(from_id, to_id)`. Chunkea de 100 en 100.
    Devuelve `(successful_count, errors)`.
    """
    if not pairs:
        return 0, []

    url = f"https://api.hubapi.com/crm/v4/associations/{from_object_type}/{to_object_type}/batch/create"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    successful = 0
    errors: List[str] = []

    for chunk_start in range(0, len(pairs), chunk_size):
        chunk = pairs[chunk_start:chunk_start + chunk_size]
        payload = {
            "inputs": [
                {
                    "from": {"id": str(from_id)},
                    "to": {"id": str(to_id)},
                    "types": [
                        {
                            "associationCategory": association_category,
                            "associationTypeId": association_type_id,
                        }
                    ],
                }
                for from_id, to_id in chunk
            ]
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            error_msg = f"{from_object_type}->{to_object_type} chunk [{chunk_start}:{chunk_start + len(chunk)}] exception: {e}"
            logger.error(f"[batch_create_associations_v4] {error_msg}", exc_info=True)
            errors.append(error_msg)
            continue

        if response.status_code >= 300:
            error_msg = (
                f"{from_object_type}->{to_object_type} chunk [{chunk_start}:{chunk_start + len(chunk)}] "
                f"HTTP {response.status_code}: {response.text}"
            )
            logger.error(f"[batch_create_associations_v4] {error_msg}")
            errors.append(error_msg)
            continue

        try:
            results = response.json().get("results", [])
            successful += len(results)
            logger.info(
                f"[batch_create_associations_v4] {from_object_type}->{to_object_type} "
                f"chunk [{chunk_start}:{chunk_start + len(chunk)}] created {len(results)} associations "
                f"(typeId={association_type_id})"
            )
        except Exception as e:
            error_msg = (
                f"{from_object_type}->{to_object_type} chunk [{chunk_start}:{chunk_start + len(chunk)}] "
                f"could not parse response: {e}"
            )
            logger.error(f"[batch_create_associations_v4] {error_msg}")
            errors.append(error_msg)

    return successful, errors


def batch_update_custom_objects(
    client: HubSpot,
    object_type: str,
    items: List[Dict[str, Any]],
    chunk_size: int = 100,
) -> int:
    """Batch update de custom objects vía `client.crm.objects.batch_api.update`.

    `items`: [{"id": "...", "properties": {...}}]. Chunkea de 100 en 100.
    Devuelve el total de objetos actualizados con éxito.
    """
    if not items:
        return 0

    try:
        from hubspot.crm.objects import BatchInputSimplePublicObjectBatchInput
    except ImportError:
        logger.error("[batch_update_custom_objects] SDK missing BatchInputSimplePublicObjectBatchInput")
        return 0

    updated = 0
    for chunk_start in range(0, len(items), chunk_size):
        chunk = items[chunk_start:chunk_start + chunk_size]
        try:
            batch_input = BatchInputSimplePublicObjectBatchInput(
                inputs=[{"id": str(x["id"]), "properties": x["properties"]} for x in chunk]
            )
            client.crm.objects.batch_api.update(
                object_type=object_type,
                batch_input_simple_public_object_batch_input=batch_input,
            )
            updated += len(chunk)
            logger.info(
                f"✅ Batch updated {len(chunk)} {object_type} objects "
                f"(chunk [{chunk_start}:{chunk_start + len(chunk)}])"
            )
        except Exception as e:
            logger.error(
                f"❌ Error batch updating {object_type} chunk [{chunk_start}:{chunk_start + len(chunk)}]: {e}",
                exc_info=True,
            )
    return updated


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

        fetch_start = time.time()
        all_elemento_ids = []
        for contenedor_id in contenedor_ids:
            elementos = get_elementos_by_contenedor(client, contenedor_id, properties=["nombre"])
            all_elemento_ids.extend([elem["id"] for elem in elementos])

        fetch_time = time.time() - fetch_start
        logger.info(f"⏱️  [TIMER] Fetched {len(all_elemento_ids)} elementos in {fetch_time:.2f}s ({fetch_time/len(contenedor_ids):.2f}s per contenedor)")

        if all_elemento_ids:
            delete_elem_start = time.time()
            elemento_count = batch_delete_custom_objects(client, OBJECT_TYPE_ELEMENTO, all_elemento_ids)
            delete_elem_time = time.time() - delete_elem_start
            deleted_count += elemento_count
            logger.info(f"   ✅ Deleted {elemento_count} Elementos ({delete_elem_time:.2f}s)")

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

# PUBLISH CUSTOM OBJECTS FROM LINE ITEMS (ASYNC BACKGROUND TASK)

# Campos del CONTENEDOR gestionados por SAP/operaciones que NO deben pisarse en update
PRESERVED_CONT_FIELDS = {"importe_total_cobrado", "numero_precinto"}

# Campos a sincronizar en update de ELEMENTO
ELEM_DIFF_FIELDS = (
    "nombre", "nombre_contenedor", "tipo_articulo", "cantidad",
    "precio_unitario", "precio_de_coste", "precio_total", "margen_unitario",
    "tipo_iva_repercutido", "tipo_iva_soportado",
    "product_sku", "cardcode", "almacen",
    "nombre_es", "nombre_en", "nombre_fr", "formato", "marca",
    "peso_neto_unitario", "peso_bruto_unitario",
)

def _normalize_for_diff(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

def _build_elem_update_props(
    target_data: Dict[str, Any],
    current_props: Dict[str, Any],
    nombre_contenedor: str,
    preserved_fields: set,
) -> Dict[str, Any]:
    update_props = {}
    for field in ELEM_DIFF_FIELDS:
        if field in preserved_fields:
            continue
        if field == "nombre_contenedor":
            target_val = nombre_contenedor or target_data.get("nombre_contenedor") or ""
        else:
            target_val = target_data.get(field)
        if target_val is None:
            continue
        if _normalize_for_diff(current_props.get(field)) != _normalize_for_diff(target_val):
            update_props[field] = target_val
    return update_props

def process_deal_custom_objects_from_line_items(
    deal_id: str,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Recrea los Contenedor/Elemento custom objects del deal a partir de sus line items.

    Convención de naming: `{deal_id}-{numero_container:03d} - {nombre}` (p. ej. `46298478914-001 - D20DR`).
    """
    def _emit(step: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(step)
        except Exception as cb_err:
            logger.warning(f"[publish] progress_callback raised on step={step}: {cb_err}")

    start_time = time.time()
    stats = {
        "deal_id": deal_id,
        "contenedores_created": 0,
        "contenedores_updated": 0,
        "contenedores_deleted": 0,
        "elementos_created": 0,
        "elementos_updated": 0,
        "elementos_deleted": 0,
        "errors": [],
    }

    try:
        from app.services.hubspot_client import hubspot_client
        client = hubspot_client.get_client()

        logger.info(f"📦 [PUBLISH] Starting custom objects publish for deal {deal_id}")

        # Fetch documentos_a_generar and almacen from deal
        documentos_a_generar = None
        deal_almacen = None
        deal_transitario = None
        try:
            deal_obj = client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=["documentos_a_generar", "almacen", "transitario"]
            )
            documentos_a_generar = deal_obj.properties.get("documentos_a_generar")
            deal_almacen = deal_obj.properties.get("almacen")
            deal_transitario = deal_obj.properties.get("transitario")
            logger.info(f"📄 documentos_a_generar for deal {deal_id}: {documentos_a_generar}")
            logger.info(f"🏭 almacen for deal {deal_id}: {deal_almacen}")
            logger.info(f"🚚 transitario for deal {deal_id}: {deal_transitario}")
        except Exception as e:
            logger.warning(f"⚠️ Could not fetch documentos_a_generar for deal {deal_id}: {e}")

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

        _emit("fetching_line_items")
        fetch_start = time.time()
        line_items = _fetch_all_line_items_for_deal(client, deal_id)
        fetch_time = time.time() - fetch_start
        logger.info(f"⏱️  [TIMER] Fetched {len(line_items)} line items in {fetch_time:.2f}s")

        if not line_items:
            logger.warning(f"No line items found for deal {deal_id}")
            return stats

        _emit("reading_state")
        read_start = time.time()
        existing_cont_props = [
            "nombre", "tipo_contenedor", "numero_container", "ocupacion",
            "peso_neto", "peso_bruto", "total_cajas", "price",
            "importe_pendiente_cobro", "importe_total_cobrado",
            "container_config", "line_item_id", "numero_precinto",
            "documentos_a_generar",
        ]
        try:
            existing_contenedores = get_contenedores_by_deal(client, deal_id, properties=existing_cont_props)
        except Exception as e:
            logger.warning(f"[publish] Error fetching existing contenedores: {e}")
            existing_contenedores = []
        contenedores_existentes_by_nombre = {
            (co.get("properties", {}) or {}).get("nombre"): {
                "id": co.get("id"),
                "properties": co.get("properties", {}) or {},
            }
            for co in existing_contenedores
            if (co.get("properties", {}) or {}).get("nombre")
        }

        existing_elem_props = [
            "nombre", "nombre_contenedor", "tipo_articulo", "cantidad",
            "precio_unitario", "precio_de_coste", "precio_total", "margen_unitario",
            "tipo_iva_repercutido", "tipo_iva_soportado",
            "line_item_id", "product_sku", "cardcode", "almacen",
            "nombre_es", "nombre_en", "nombre_fr", "formato", "marca",
            "peso_neto_unitario", "peso_bruto_unitario",
        ]
        try:
            existing_elementos = get_elementos_by_deal(client, deal_id, properties=existing_elem_props)
        except Exception as e:
            logger.warning(f"[publish] Error fetching existing elementos: {e}")
            existing_elementos = []

        elementos_by_lid = {}
        elementos_by_key = {}
        for el in existing_elementos:
            props = el.get("properties", {}) or {}
            lid = (props.get("line_item_id") or "").strip()
            nc = (props.get("nombre_contenedor") or "").strip()
            nombre = (props.get("nombre") or "").strip()
            entry = {"id": el.get("id"), "properties": props}
            if lid:
                elementos_by_lid[lid] = entry
            if nombre:
                elementos_by_key[(nc, nombre)] = entry
        read_time = time.time() - read_start
        logger.info(
            f"⏱️  [TIMER] Read state: {len(contenedores_existentes_by_nombre)} contenedores, "
            f"{len(existing_elementos)} elementos in {read_time:.2f}s"
        )

        product_price_map = _fetch_product_prices_for_line_items(client, line_items)

        company_props: Optional[Dict[str, Any]] = None
        if company_id:
            try:
                company_resp = client.crm.companies.basic_api.get_by_id(
                    company_id=company_id,
                    properties=["equ", "residennum"],
                )
                company_props = getattr(company_resp, "properties", None) or {}
            except Exception as e:
                logger.warning(f"[publish] No pude leer equ/residennum de company {company_id}: {e}")

        _emit("parsing_containers")
        parse_start = time.time()
        containers_map, standalone_elements = _parse_line_items_to_custom_objects(
            line_items,
            deal_almacen=deal_almacen,
            product_price_map=product_price_map,
            company_props=company_props,
            deal_transitario=deal_transitario,
        )
        parse_time = time.time() - parse_start
        logger.info(f"⏱️  [TIMER] Parsed {len(containers_map)} containers and {len(standalone_elements)} standalone elements in {parse_time:.2f}s")

        target_cont_keys = set(containers_map.keys())
        cont_to_create = []
        cont_to_update = []
        cont_to_delete_ids = []
        contenedor_id_by_nombre = {}

        for nombre, container_info in containers_map.items():
            target = container_info["contenedor_data"]
            if documentos_a_generar:
                target["documentos_a_generar"] = documentos_a_generar
            existing = contenedores_existentes_by_nombre.get(nombre)
            if existing:
                contenedor_id_by_nombre[nombre] = existing["id"]
                update_props = {}
                for prop, target_val in target.items():
                    if prop in PRESERVED_CONT_FIELDS or prop == "fecha_creacion":
                        continue
                    if target_val is None:
                        continue
                    if _normalize_for_diff(existing["properties"].get(prop)) != _normalize_for_diff(target_val):
                        update_props[prop] = target_val
                if update_props:
                    cont_to_update.append({"id": existing["id"], "properties": update_props})
            else:
                cont_to_create.append(target)

        for nombre, existing in contenedores_existentes_by_nombre.items():
            if nombre not in target_cont_keys:
                cont_to_delete_ids.append(existing["id"])

        _emit("applying_contenedores")
        apply_cont_start = time.time()

        if cont_to_update:
            stats["contenedores_updated"] += batch_update_custom_objects(
                client, OBJECT_TYPE_CONTENEDOR, cont_to_update,
            )

        if cont_to_create:
            nuevos = batch_create_contenedores_with_associations(
                client=client,
                contenedores_data=cont_to_create,
                deal_id=deal_id,
                contact_id=contact_id,
                company_id=company_id,
            )
            stats["contenedores_created"] = len(nuevos)
            contenedor_id_by_nombre.update(nuevos)

        if cont_to_delete_ids:
            stats["contenedores_deleted"] += batch_delete_custom_objects(
                client, OBJECT_TYPE_CONTENEDOR, cont_to_delete_ids,
            )

        apply_cont_time = time.time() - apply_cont_start
        logger.info(
            f"⏱️  [TIMER] CONTENEDORES diff applied: updated={stats['contenedores_updated']} "
            f"created={stats['contenedores_created']} deleted={stats['contenedores_deleted']} "
            f"in {apply_cont_time:.2f}s"
        )

        target_elems = []
        for nombre, container_info in containers_map.items():
            for el in container_info["elementos_data"]:
                target_elems.append((nombre, el))
        for el in standalone_elements:
            target_elems.append(("", el))

        elem_to_update = []
        elem_to_create_by_contenedor = {}
        matched_existing_ids = set()
        affected_contenedor_co_ids = set()
        for x in cont_to_update:
            if x.get("id"):
                affected_contenedor_co_ids.add(x["id"])
        for cid_del in cont_to_delete_ids:
            if cid_del:
                affected_contenedor_co_ids.add(cid_del)
        PRESERVED_ELEM_FIELDS = set()

        for nc, el in target_elems:
            target_lid = str(el.get("line_item_id") or "").strip()
            target_nombre = (el.get("nombre") or "").strip()

            existing_entry = elementos_by_lid.get(target_lid) if target_lid else None
            if not existing_entry and target_nombre:
                existing_entry = elementos_by_key.get((nc, target_nombre))

            cid_for_elem = contenedor_id_by_nombre.get(nc) if nc else None
            if existing_entry:
                matched_existing_ids.add(existing_entry["id"])
                update_props = _build_elem_update_props(el, existing_entry["properties"], nc, PRESERVED_ELEM_FIELDS)
                if target_lid and (existing_entry["properties"].get("line_item_id") or "") != target_lid:
                    update_props["line_item_id"] = target_lid
                if update_props:
                    elem_to_update.append({"id": existing_entry["id"], "properties": update_props, "cid": cid_for_elem})
            else:
                if cid_for_elem:
                    el["nombre_contenedor"] = nc
                    elem_to_create_by_contenedor.setdefault(cid_for_elem, []).append(el)
                else:
                    logger.warning(f"[publish] standalone elemento '{target_nombre}' sin contenedor padre — skipping")

        elem_to_delete = []
        for el in existing_elementos:
            eid = el.get("id")
            if not eid or eid in matched_existing_ids:
                continue
            nc_existing = ((el.get("properties") or {}).get("nombre_contenedor") or "").strip()
            cid_existing = contenedor_id_by_nombre.get(nc_existing) if nc_existing else None
            elem_to_delete.append((eid, cid_existing))

        _emit("applying_elementos")
        apply_elem_start = time.time()

        if elem_to_update:
            for x in elem_to_update:
                if x.get("cid"):
                    affected_contenedor_co_ids.add(x["cid"])
            stats["elementos_updated"] += batch_update_custom_objects(
                client, OBJECT_TYPE_ELEMENTO,
                [{"id": x["id"], "properties": x["properties"]} for x in elem_to_update],
            )

        for cid, elems in elem_to_create_by_contenedor.items():
            nuevos = batch_create_elementos_with_associations(
                client=client,
                elementos_data=elems,
                deal_id=deal_id,
                contenedor_id=cid,
                contact_id=contact_id,
                company_id=company_id,
            )
            if nuevos:
                stats["elementos_created"] += len(nuevos)
                if cid:
                    affected_contenedor_co_ids.add(cid)

        if elem_to_delete:
            for _eid, cid_del in elem_to_delete:
                if cid_del:
                    affected_contenedor_co_ids.add(cid_del)
            stats["elementos_deleted"] += batch_delete_custom_objects(
                client, OBJECT_TYPE_ELEMENTO,
                [eid for eid, _ in elem_to_delete],
            )

        apply_elem_time = time.time() - apply_elem_start
        logger.info(
            f"⏱️  [TIMER] ELEMENTOS diff applied: updated={stats['elementos_updated']} "
            f"created={stats['elementos_created']} deleted={stats['elementos_deleted']} "
            f"in {apply_elem_time:.2f}s"
        )

        if affected_contenedor_co_ids:
            _emit("post_processing")
            post_start = time.time()
            try:
                from app.utils.iva_helpers import recalcular_contenedores
                recalcular_contenedores(
                    client,
                    deal_id,
                    list(affected_contenedor_co_ids),
                    {
                        "equ": (company_props or {}).get("equ") or "",
                        "residennum": (company_props or {}).get("residennum") or "",
                    },
                )
            except Exception as e:
                logger.warning(f"[publish] recalcular_contenedores falló: {e}")

            touch_start = time.time()
            try:
                touch_pagos_for_contenedores(client, list(affected_contenedor_co_ids))
            except Exception as e:
                logger.warning(f"[publish] touch_pagos_for_contenedores falló: {e}")
            logger.info(f"⏱️  [TIMER] touch_pagos_for_contenedores: {time.time() - touch_start:.2f}s")

            logger.info(
                f"⏱️  [TIMER] post-batch (recalcular + touch_pagos): {time.time() - post_start:.2f}s "
                f"(N={len(affected_contenedor_co_ids)} contenedores)"
            )

        total_time = time.time() - start_time
        logger.info(f"⏱️  [TIMER] ========== TOTAL PUBLISH CUSTOM OBJECTS: {total_time:.2f}s ==========")
        logger.info(
            f"📦 [publish] sync done deal={deal_id}: "
            f"CONTENEDORES updated={stats['contenedores_updated']} created={stats['contenedores_created']} deleted={stats['contenedores_deleted']} | "
            f"ELEMENTOS updated={stats['elementos_updated']} created={stats['elementos_created']} deleted={stats['elementos_deleted']}"
        )

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

        # Fallback: v4 HTTP API if v1 SDK returned no results
        if not line_item_ids:
            try:
                access_token = client.access_token
                url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/line_items"
                headers = {"Authorization": f"Bearer {access_token}"}
                r = requests.get(url, headers=headers, timeout=10)
                if r.ok:
                    for item in r.json().get("results", []):
                        val = item.get("toObjectId") or item.get("id")
                        if val is not None:
                            line_item_ids.append(str(val))
                    if line_item_ids:
                        logger.info(f"[v4 fallback] Found {len(line_item_ids)} line items for deal {deal_id}")
                else:
                    logger.warning(f"[v4 fallback] HTTP {r.status_code} fetching line items for deal {deal_id}")
            except Exception as fallback_error:
                logger.warning(f"[v4 fallback] Failed fetching line items for deal {deal_id}: {fallback_error}")

        if not line_item_ids:
            return []

        # Batch read line items with all properties
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId

        properties = [
            "name", "hs_sku", "quantity", "price", "amount",
            "hs_margin", "hs_product_id",
            "hs_product_type", "tipo_articulo", "tipo_iva_repercutido", "tipo_iva_soportado",
            "precio_de_coste", "margen_unitario", "numero_container", "ocupacion",
            "peso_neto", "peso_bruto", "codigo_proveedor",
            "name_en", "name_fr", "description", "formato", "marca_certificado",
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

def _fetch_product_prices_for_line_items(client: HubSpot, line_items: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Devuelve `{hs_product_id: round_price(hs_price_eur)}` para los productos asociados
    a los line items que NO son contenedores. Se usa como fuente única de `precio_de_coste`."""
    product_ids = {
        (li.get("properties", {}) or {}).get("hs_product_id")
        for li in line_items
        if ((li.get("properties", {}) or {}).get("hs_product_type") or "").strip().lower() not in {"container", "contenedor"}
    }
    product_ids.discard(None)
    product_ids.discard("")

    if not product_ids:
        return {}

    product_price_map: Dict[str, Optional[float]] = {}
    try:
        from hubspot.crm.objects import BatchReadInputSimplePublicObjectId
        batch_input = BatchReadInputSimplePublicObjectId(
            properties=["hs_price_eur"],
            inputs=[{"id": str(pid)} for pid in product_ids],
        )
        resp = client.crm.products.batch_api.read(
            batch_read_input_simple_public_object_id=batch_input,
        )
        for result in (resp.results or []):
            props = result.properties or {}
            product_price_map[str(result.id)] = round_price(props.get("hs_price_eur"))
    except Exception as e:
        logger.warning(f"[parse] could not batch-read product hs_price_eur: {e}")

    return product_price_map

def _parse_line_items_to_custom_objects(
    line_items: List[Dict[str, Any]],
    deal_almacen: Optional[str] = None,
    product_price_map: Optional[Dict[str, Optional[float]]] = None,
    company_props: Optional[Dict[str, Any]] = None,
    deal_transitario: Optional[str] = None,
) -> tuple:
    """
    Parsea line items y los agrupa por contenedor usando propiedades HubSpot.

    Clasificación:
    - hs_product_type in {container, contenedor} → CONTENEDOR
    - hs_product_type in {flete, service, servicio} → ELEMENTO
    - tipo_articulo in {comisiones, fabricante} → ELEMENTO
    - tipo_articulo = transitario → ignorado

    Asociación elemento-contenedor: propiedad numero_container del line item
    (formato "{deal_id}-{num:03d}") coincide con el nombre del contenedor.

    Returns:
        (containers_map, standalone_elements) donde:
        - containers_map: Dict[container_name, {"contenedor_data": {...}, "elementos_data": [...]}]
        - standalone_elements: List[elemento_data] para elementos sin contenedor
    """
    containers_map = {}
    standalone_elements = []
    product_price_map = product_price_map or {}

    logger.info(f"🔍 [PARSE] Processing {len(line_items)} line items")

    # Primera pasada: identificar contenedores por hs_product_type
    for line_item in line_items:
        props = line_item.get("properties", {})
        name = props.get("name", "")
        sku = (props.get("hs_sku") or "").strip()
        hs_product_type = (props.get("hs_product_type") or "").strip().lower()

        logger.info(f"  📄 Line item: name='{name}', hs_product_type='{hs_product_type}', sku='{sku}'")

        if hs_product_type not in {"container", "contenedor"}:
            continue

        container_name = name.strip()  # e.g., "46298478914-001"

        # Extraer tipo_contenedor del SKU y normalizar a los valores permitidos en HubSpot
        _sku_to_tipo = {
            "d20dr": "d20dr", "d40dr": "d40dr", "d20w": "d20w",
            "d40hd": "d40hd", "d45hd": "d45hd", "d45w": "d45w",
            "palets": "palets", "r20rf": "r20rf", "r40rf": "r40rf",
            "trailer_80": "trailer_80", "trailer80": "trailer_80",
            "camion_16p": "camion_16p", "camion16p": "camion_16p",
        }
        sku_upper = sku.upper()
        if sku_upper.startswith("CONTAINER-"):
            raw_tipo = sku_upper.replace("CONTAINER-", "").lower()
            tipo_contenedor = _sku_to_tipo.get(raw_tipo, raw_tipo)
        else:
            tipo_contenedor = "d20dr"  # fallback

        try:
            numero_container = int(container_name.split("-")[-1])
        except (ValueError, IndexError):
            numero_container = 1

        logger.info(f"📦 Detected container: {container_name} (tipo: {tipo_contenedor})")

        raw_ocupacion = props.get("ocupacion")
        ocupacion = float(raw_ocupacion) if raw_ocupacion else None

        containers_map[container_name] = {
            "contenedor_data": {
                "nombre": container_name,
                "tipo_contenedor": tipo_contenedor,
                "numero_container": numero_container,
                "ocupacion": ocupacion,
                "line_item_id": line_item["id"],
                "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000))
            },
            "elementos_data": []
        }

    # Segunda pasada: clasificar elementos y asociarlos a contenedores
    for line_item in line_items:
        props = line_item.get("properties", {})
        name = props.get("name", "")
        sku = (props.get("hs_sku") or "").strip()
        hs_product_type = (props.get("hs_product_type") or "").strip().lower()
        tipo_articulo = (props.get("tipo_articulo") or "").strip().lower()

        # Saltar contenedores
        if hs_product_type in {"container", "contenedor"}:
            continue

        # Clasificar como elemento según reglas
        is_flete = hs_product_type == "flete"
        is_service = hs_product_type in {"service", "servicio"}
        is_comision = tipo_articulo == "comisiones"
        is_fabricante = tipo_articulo == "fabricante"

        if not (is_flete or is_service or is_comision or is_fabricante):
            logger.info(f"  ⏭️ Skipping line item '{name}' (hs_product_type='{hs_product_type}', tipo_articulo='{tipo_articulo}')")
            continue

        # Fabricante: coste viene del catálogo del producto, precio_unitario aplica margen.
        # Flete/intermediario: coste calculado a partir de propiedades del deal en
        # POST /transit y POST /containers, ya persistido en line_item.price. Estos line
        # items llevan hs_product_type="Servicio" + tipo_articulo="comisiones" (o "Flete"
        # con tipo_articulo libre), por lo que esta rama debe evaluarse antes que `is_service`.
        # Servicio genérico (transitarios u otros): coste del catálogo del producto.
        hs_product_id = (props.get("hs_product_id") or "").strip()
        raw_price = props.get("price")
        raw_margen = props.get("margen_unitario")

        precio_de_coste = None
        precio_unitario = None

        if is_fabricante:
            precio_de_coste = product_price_map.get(hs_product_id) if hs_product_id else None
            if precio_de_coste is not None:
                margen = float(raw_margen) if raw_margen else 0.0
                precio_unitario = round(precio_de_coste * (1 + margen), 3)
                logger.info(f"  💶 fabricante '{name}': precio_de_coste={precio_de_coste}, margen={margen} ({margen*100:.2f}%), precio_unitario={precio_unitario}")
            else:
                logger.warning(f"  ⚠️ '{name}' sin hs_price_eur en producto {hs_product_id or 'N/A'} — precio_de_coste queda vacío")
        elif is_flete or is_comision:
            try:
                precio_de_coste = round(float(raw_price), 3) if raw_price else None
            except (ValueError, TypeError):
                precio_de_coste = None
            precio_unitario = precio_de_coste
        elif is_service:
            try:
                precio_de_coste = round(float(raw_price), 3) if raw_price else None
            except (ValueError, TypeError):
                precio_de_coste = None
            if precio_de_coste is None and hs_product_id:
                precio_de_coste = product_price_map.get(hs_product_id)
            if precio_de_coste is not None:
                precio_unitario = precio_de_coste
            else:
                logger.warning(f"  ⚠️ servicio '{name}' sin price ni hs_price_eur (producto {hs_product_id or 'N/A'}) — precio_de_coste queda vacío")

        # tipo_iva_repercutido — HubSpot espera uppercase (R21, EI21, EX…)
        tipo_iva = (props.get("tipo_iva_repercutido") or "").strip().upper() or None
        tipo_iva_sop = (props.get("tipo_iva_soportado") or "").strip() or None

        # tipo_articulo normalizado a los valores permitidos en el objeto personalizado Elemento
        _VALID_TIPO_ARTICULO = {"fabricante", "servicio", "flete", "intermediario", "seguro"}
        if tipo_articulo in _VALID_TIPO_ARTICULO:
            tipo_articulo_elem = tipo_articulo
        elif is_flete:
            tipo_articulo_elem = "flete"
        elif is_comision:
            tipo_articulo_elem = "intermediario"
        elif is_service:
            tipo_articulo_elem = "servicio"
        else:
            tipo_articulo_elem = "servicio"  # fallback seguro

        cantidad_elem = float(props.get("quantity", 1)) if props.get("quantity") else 1.0
        # precio_total = precio_unitario × cantidad, siempre EXW independientemente del tipo de venta.
        # precio_unitario para fabricante = precio_de_coste × (1 + margen); para el resto = price del line item.
        # Ambos son precios EXW: excluyen flete/servicios añadidos por tipoVenta en NegociacionTab.
        precio_total_elem = (
            round(precio_unitario * cantidad_elem, 3)
            if precio_unitario is not None
            else None
        )
        nombre_es = (props.get("description") or "").strip() or None
        nombre_en = (props.get("name_en") or "").strip() or None
        nombre_fr = (props.get("name_fr") or "").strip() or None
        formato   = (props.get("formato") or "").strip() or None
        marca     = (props.get("marca_certificado") or "").strip() or None

        if tipo_articulo_elem == "flete":
            cardcode_val = deal_transitario or props.get("cardcode") or None
        else:
            cardcode_val = props.get("cardcode") or props.get("codigo_proveedor") or None

        elemento_data = {
            "nombre": name,
            "tipo_articulo": tipo_articulo_elem,
            "cantidad": cantidad_elem,
            "product_sku": sku or None,
            "precio_unitario": precio_unitario,
            "precio_de_coste": precio_de_coste,
            "precio_total": precio_total_elem,
            "margen_unitario": round_margin(raw_margen),
            "tipo_iva_repercutido": tipo_iva,
            "tipo_iva_soportado": tipo_iva_sop,
            "line_item_id": line_item["id"],
            "fecha_creacion": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            "peso_neto_unitario": float(props["peso_neto"]) if props.get("peso_neto") else None,
            "peso_bruto_unitario": float(props["peso_bruto"]) if props.get("peso_bruto") else None,
            "cardcode": cardcode_val,
            "almacen": deal_almacen,
            "nombre_es": nombre_es,
            "nombre_en": nombre_en,
            "nombre_fr": nombre_fr,
            "formato": formato,
            "marca": marca,
        }

        # Asociar al contenedor usando la propiedad numero_container
        num_cont = (props.get("numero_container") or "").strip()  # "46298478914-001"
        if num_cont and num_cont in containers_map:
            elemento_data["nombre_contenedor"] = num_cont
            containers_map[num_cont]["elementos_data"].append(elemento_data)
            logger.info(f"  🔗 Element '{name}' → container '{num_cont}'")
        else:
            standalone_elements.append(elemento_data)
            logger.info(f"  📎 Standalone element '{name}' (numero_container='{num_cont}')")

    # `price` e `importe_pendiente_cobro` los recalcula el caller con
    # `recalcular_contenedores(client, deal_id, [co_ids], company_props)` tras
    # crear los CONTENEDOR custom objects — usando la misma fórmula que
    # NegociacionTab (pricing.utils.js) para evitar desajustes de redondeo.
    for container_name, container_info in containers_map.items():
        container_info["contenedor_data"]["importe_pendiente_cobro"] = 0
        container_info["contenedor_data"]["price"] = 0
        container_info["contenedor_data"]["importe_total_cobrado"] = 0

        # total_cajas = suma de quantity de los elementos fabricante de este contenedor
        total_cajas = sum(
            int(e.get("cantidad") or 0)
            for e in container_info["elementos_data"]
            if e.get("tipo_articulo") == "fabricante"
        )
        container_info["contenedor_data"]["total_cajas"] = total_cajas

        peso_neto_total = sum(
            float(e.get("peso_neto_unitario") or 0) * float(e.get("cantidad") or 0)
            for e in container_info["elementos_data"]
            if e.get("tipo_articulo") == "fabricante"
        )
        peso_bruto_total = sum(
            float(e.get("peso_bruto_unitario") or 0) * float(e.get("cantidad") or 0)
            for e in container_info["elementos_data"]
            if e.get("tipo_articulo") == "fabricante"
        )
        container_info["contenedor_data"]["peso_neto"] = round(peso_neto_total, 3)
        container_info["contenedor_data"]["peso_bruto"] = round(peso_bruto_total, 3)

    # Log resumen
    total_elementos_in_containers = sum(len(c["elementos_data"]) for c in containers_map.values())
    logger.info(f"✅ [PARSE] Summary: {len(containers_map)} containers with {total_elementos_in_containers} elements, {len(standalone_elements)} standalone elements")
    for container_name, container_info in containers_map.items():
        logger.info(f"   📦 {container_name}: {len(container_info['elementos_data'])} elements, importe: {container_info['contenedor_data'].get('importe_pendiente_cobro', 0)}")

    return containers_map, standalone_elements
