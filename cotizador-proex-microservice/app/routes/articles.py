"""Article-related API routes.

This module defines all endpoints related to HubSpot products/articles.
"""

import logging
import requests
from fastapi import APIRouter, HTTPException, Query, Path

from app.models.schemas import (
    ProductListResponse,
    ProductItem,
    ProductProviderResponse,
    ProviderData,
    ProviderListResponse,
)
from app.services.hubspot_client import hubspot_client
from app.utils.property_helpers import get_model_property_names, parse_hubspot_properties

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/product/list", response_model=ProductListResponse)
async def search_products(
    query: str = Query(
        ...,
        description="Search query to filter products by name",
        example="vino",
        min_length=1
    ),
    limit: int = Query(
        100,
        description="Maximum number of results to return",
        ge=1,
        le=100
    )
):
    """Busca productos por nombre (CONTAINS_TOKEN, case-insensitive) limitados a hs_product_type=inventory + tipo_articulo=fabricante + activo=Activo."""
    try:
        client = hubspot_client.get_client()

        logger.info(f"Searching products with query: '{query}', limit: {limit}")

        properties_to_request = get_model_property_names(ProductItem, exclude=['id'])

        # Regla unificada: artículos = hs_product_type = inventory y tipo_articulo = fabricante.
        # Devolvemos productos con hs_product_type y tipo_articulo para que el front los envíe al guardar.
        search_request = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": f"*{query}*"},
                        {"propertyName": "hs_product_type", "operator": "EQ", "value": "inventory"},
                        {"propertyName": "tipo_articulo", "operator": "EQ", "value": "fabricante"},
                        {"propertyName": "activo", "operator": "EQ", "value": "Activo"},
                    ]
                }
            ],
            "properties": properties_to_request,
            "limit": limit
        }

        search_response = client.crm.products.search_api.do_search(
            public_object_search_request=search_request
        )

        # Parse results
        products = []
        if search_response.results:
            for result in search_response.results:
                product_data = parse_hubspot_properties(result.id, result.properties, ProductItem)
                products.append(ProductItem(**product_data))

        total = len(products)
        logger.info(f"Found {total} products matching query '{query}'")

        return ProductListResponse(
            success=True,
            error=None,
            products=products,
            total=total
        )

    except Exception as e:
        logger.error(f"Error searching products: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/services/list", response_model=ProductListResponse)
async def list_services(
    query: str = Query(
        "",
        description="Optional search query to filter services by name",
        example="transporte",
    ),
    limit: int = Query(
        100,
        description="Maximum number of results to return",
        ge=1,
        le=100
    )
):
    """List available services: productos con hs_product_type = service.

    Regla unificada: servicios = hs_product_type = service. Devolvemos hs_product_type y tipo_articulo
    para que el front los envíe al guardar.
    """
    try:
        client = hubspot_client.get_client()

        logger.info(f"Listing services with query: '{query}', limit: {limit}")

        properties_to_request = get_model_property_names(ProductItem, exclude=['id'])

        # Regla unificada: servicios = hs_product_type = service (solo).
        filters = [{"propertyName": "hs_product_type", "operator": "EQ", "value": "service"}]
        if query:
            filters.append({"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": f"*{query}*"})

        search_request = {
            "filterGroups": [{"filters": filters}],
            "properties": properties_to_request,
            "limit": limit
        }

        search_response = client.crm.products.search_api.do_search(
            public_object_search_request=search_request
        )

        products = []
        if search_response.results:
            for result in search_response.results:
                product_data = parse_hubspot_properties(result.id, result.properties, ProductItem)
                item = ProductItem(**product_data)
                tipo_articulo = (item.tipo_articulo or "").strip().lower()
                if tipo_articulo in {"transitario", "comisiones"}:
                    continue
                products.append(item)

        total = len(products)
        logger.info(f"Found {total} services")

        return ProductListResponse(
            success=True,
            error=None,
            products=products,
            total=total
        )

    except Exception as e:
        logger.error(f"Error listing services: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/services/providers/list", response_model=ProviderListResponse)
async def list_service_providers(
    tipo_proveedor: str = Query(
        ...,
        description="Provider type to filter by (e.g., 'transitario', 'comisiones')",
        example="transitario",
        min_length=1
    ),
    query: str = Query(
        "",
        description="Optional search query to filter providers by name or cardcode",
        example="DHL",
    ),
    limit: int = Query(
        100,
        description="Maximum number of results to return",
        ge=1,
        le=100
    )
):
    """Lista los proveedores con `tipo_proveedor` indicado, opcionalmente filtrados por nombre/cardcode con `query`. Usado para poblar el select de proveedores en el panel de servicios."""
    try:
        client = hubspot_client.get_client()

        logger.info(f"Listing service providers with tipo_proveedor: '{tipo_proveedor}', query: '{query}', limit: {limit}")

        # Properties to request based on ProviderData model
        properties_to_request = get_model_property_names(ProviderData, exclude=['id'])

        filter_groups = []

        if query:
            # Two filter groups for OR between name and cardcode, both with tipo_proveedor
            filter_groups = [
                {
                    "filters": [
                        {"propertyName": "tipo_proveedor", "operator": "EQ", "value": tipo_proveedor},
                        {"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": f"*{query}*"},
                    ]
                },
                {
                    "filters": [
                        {"propertyName": "tipo_proveedor", "operator": "EQ", "value": tipo_proveedor},
                        {"propertyName": "cardcode", "operator": "CONTAINS_TOKEN", "value": f"*{query}*"},
                    ]
                },
            ]
        else:
            # Just filter by tipo_proveedor
            filter_groups = [
                {
                    "filters": [
                        {"propertyName": "tipo_proveedor", "operator": "EQ", "value": tipo_proveedor},
                    ]
                }
            ]

        search_request = {
            "filterGroups": filter_groups,
            "properties": properties_to_request,
            "limit": limit,
        }

        search_response = client.crm.objects.search_api.do_search(
            object_type="2-43108800",
            public_object_search_request=search_request
        )

        lugar_carga_options = {}
        try:
            access_token = client.access_token
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            headers = {"Authorization": f"Bearer {access_token}"}
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
        except Exception as e:
            logger.warning(f"Error fetching lugar_de_la_carga_envio options: {e}")

        # Parse results
        providers = []
        if getattr(search_response, "results", None):
            for result in search_response.results:
                provider_data = parse_hubspot_properties(result.id, result.properties, ProviderData)

                lugar_value = provider_data.get("lugar_de_la_carga_envio")
                if lugar_value and lugar_carga_options:
                    provider_data["lugar_de_la_carga_envio"] = lugar_carga_options.get(lugar_value, lugar_value)

                providers.append(ProviderData(**provider_data))

        total = len(providers)
        logger.info(f"Found {total} service providers for tipo_proveedor '{tipo_proveedor}'")

        return ProviderListResponse(
            success=True,
            error=None,
            providers=providers,
            total=total
        )

    except Exception as e:
        logger.error(f"Error listing service providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/provider/{provider_id}", response_model=ProductProviderResponse)
async def get_provider(
    provider_id: str = Path(
        ...,
        description="Provider ID (cardcode) to search for",
        example="P0159",
        min_length=1
    )
):
    """Devuelve los datos del proveedor cuyo cardcode coincide con `provider_id` (custom object Proveedores `2-43108800`)."""
    try:
        client = hubspot_client.get_client()

        logger.info(f"Searching for provider with cardcode: {provider_id}")

        properties_to_request = get_model_property_names(ProviderData, exclude=['id'])

        # Search for custom object "Proveedores" with matching cardcode
        search_request = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "cardcode",
                            "operator": "EQ",
                            "value": provider_id
                        }
                    ]
                }
            ],
            "properties": properties_to_request,
            "limit": 1
        }

        search_response = client.crm.objects.search_api.do_search(
            object_type="2-43108800",
            public_object_search_request=search_request
        )

        # Check if provider was found
        if not search_response.results or len(search_response.results) == 0:
            logger.warning(f"Provider not found with cardcode: {provider_id}")
            return ProductProviderResponse(
                success=True,
                error=None,
                provider=None
            )

        lugar_carga_options = {}
        try:
            # Fetch property definition for the custom object
            access_token = client.access_token
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            headers = {"Authorization": f"Bearer {access_token}"}
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
                logger.info(f"Loaded {len(lugar_carga_options)} lugar_de_la_carga_envio options")
        except Exception as e:
            logger.warning(f"Error fetching lugar_de_la_carga_envio options: {e}")

        result = search_response.results[0]
        provider_data_dict = parse_hubspot_properties(result.id, result.properties, ProviderData)

        lugar_value = provider_data_dict.get("lugar_de_la_carga_envio")
        if lugar_value and lugar_carga_options:
            lugar_label = lugar_carga_options.get(lugar_value, lugar_value)
            provider_data_dict["lugar_de_la_carga_envio"] = lugar_label

        provider_data = ProviderData(**provider_data_dict)

        logger.info(f"Provider found: {provider_data.name} (ID: {provider_data.id})")

        return ProductProviderResponse(
            success=True,
            error=None,
            provider=provider_data
        )

    except Exception as e:
        logger.error(f"Error getting provider information: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/providers/list", response_model=ProviderListResponse)
async def search_providers(
    query: str = Query(
        ...,
        description="Search query to filter providers by name or cardcode",
        example="P0159",
        min_length=1
    ),
    limit: int = Query(
        100,
        description="Maximum number of results to return",
        ge=1,
        le=100
    )
):
    """Busca proveedores con tipo_proveedor=fabricante en `proveedores` (`2-43108800`) por coincidencia parcial en `name` o `cardcode` (case-insensitive)."""
    try:
        client = hubspot_client.get_client()

        logger.info(f"Searching providers with query: '{query}', limit: {limit}")

        # Properties to request based on ProviderData model (excluding 'id', supplied separately)
        properties_to_request = get_model_property_names(ProviderData, exclude=['id'])

        # Build HubSpot CRM search for custom object 'proveedores'
        # Two filter groups to achieve OR between 'name' and 'cardcode'
        # Each filter group also includes tipo_proveedor = "fabricante"
        search_request = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": f"*{query}*",
                        },
                        {
                            "propertyName": "tipo_proveedor",
                            "operator": "EQ",
                            "value": "fabricante",
                        }
                    ]
                },
                {
                    "filters": [
                        {
                            "propertyName": "cardcode",
                            "operator": "CONTAINS_TOKEN",
                            "value": f"*{query}*",
                        },
                        {
                            "propertyName": "tipo_proveedor",
                            "operator": "EQ",
                            "value": "fabricante",
                        }
                    ]
                },
            ],
            "properties": properties_to_request,
            "limit": limit,
        }

        search_response = client.crm.objects.search_api.do_search(
            object_type="2-43108800",
            public_object_search_request=search_request
        )

        lugar_carga_options = {}
        try:
            # Fetch property definition for the custom object
            access_token = client.access_token
            property_url = "https://api.hubapi.com/crm/v3/properties/2-43108800/lugar_de_la_carga_envio"
            headers = {"Authorization": f"Bearer {access_token}"}
            prop_response = requests.get(property_url, headers=headers, timeout=10)

            if prop_response.ok:
                prop_data = prop_response.json()
                options = prop_data.get("options", [])
                lugar_carga_options = {opt["value"]: opt["label"] for opt in options if "value" in opt and "label" in opt}
                logger.info(f"Loaded {len(lugar_carga_options)} lugar_de_la_carga_envio options for provider search")
        except Exception as e:
            logger.warning(f"Error fetching lugar_de_la_carga_envio options: {e}")

        # Parse results into ProviderData
        providers = []
        if getattr(search_response, "results", None):
            for result in search_response.results:
                provider_data = parse_hubspot_properties(
                    result.id, result.properties, ProviderData
                )

                lugar_value = provider_data.get("lugar_de_la_carga_envio")
                if lugar_value and lugar_carga_options:
                    lugar_label = lugar_carga_options.get(lugar_value, lugar_value)
                    provider_data["lugar_de_la_carga_envio"] = lugar_label

                providers.append(ProviderData(**provider_data))

        total = len(providers)
        logger.info(f"Found {total} providers matching query '{query}'")

        return ProviderListResponse(
            success=True,
            error=None,
            providers=providers,
            total=total
        )

    except Exception as e:
        logger.error(f"Error searching providers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
