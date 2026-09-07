"""Pydantic models for request/response validation.

This module defines all data models used by the API with comprehensive
validation, documentation, and type safety.
"""

from typing import Optional, List, Union
from datetime import datetime

from pydantic import BaseModel, Field

# Base response models
class BaseResponse(BaseModel):
    """Base response model for all API endpoints.

    Attributes:
        success: Whether the operation was successful.
        error: Error message if operation failed.
    """
    success: bool = Field(..., description="Whether the operation was successful")
    error: Optional[str] = Field(None, description="Error message if operation failed")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None
            }
        }

# Health check
# Client Data Models
class PropertyOption(BaseModel):
    """Property option model for select/dropdown fields.

    Attributes:
        label: Display label for the option.
        value: Internal value for the option.
    """
    label: str = Field(..., description="Display label for the option")
    value: str = Field(..., description="Internal value for the option")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "label": "EXW (Ex Works)",
                "value": "exw"
            }
        }

class PropertyWithOptions(BaseModel):
    """Property with available options and selected value.

    Attributes:
        value: Currently selected value.
        options: Available options for selection.
    """
    value: Optional[str] = Field(None, description="Currently selected value")
    options: list[PropertyOption] = Field(
        default_factory=list,
        description="Available options for selection"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "value": "exw",
                "options": [
                    {"label": "EXW (Ex Works)", "value": "exw"},
                    {"label": "FOB (Free On Board)", "value": "fob"}
                ]
            }
        }

class ContactData(BaseModel):
    """Contact information from HubSpot (internal use only, not returned in response).

    Attributes:
        hs_language: Contact language preference (used for preprints language determination).
        proyecto__canal_de_origen_: Project/origin channel (for analytics).
    """
    hs_language: Optional[str] = Field(None, description="Contact language preference")
    proyecto__canal_de_origen_: Optional[str] = Field(None, description="Project/origin channel (for beverage fair analytics)")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "hs_language": "es"
            }
        }

class CompanyData(BaseModel):
    """Company information from HubSpot.

    Attributes:
        name: Company name.
        pais_de_operaciones: Country where the company operates/invoices (priority field).
        country_entrega: Delivery country.
        country: Country (fallback if country_entrega is not available).
        langcode: Company language code.
        project: Company project/origin channel (fallback for beverage fair analytics).
        residennum: Company residence number.
        cif: Company tax identification number (CIF).
        address: Company fiscal address.
        tipo_cliente: Client type from HubSpot with options (NACIONAL or EXPORTACION).
    """
    name: Optional[str] = Field(None, description="Company name")
    pais_de_operaciones: Optional[str] = Field(None, description="Country of operations (billing country)")
    country_entrega: Optional[str] = Field(None, description="Delivery country")
    country: Optional[str] = Field(None, description="Country (fallback)")
    langcode: Optional[str] = Field(None, description="Company language code")
    project: Optional[str] = Field(None, description="Company project/origin channel (fallback for beverage fair analytics)")
    residennum: Optional[str] = Field(None, description="Company residence number")
    cif: Optional[str] = Field(None, description="Company tax identification number (CIF)")
    address: Optional[str] = Field(None, description="Company fiscal address")
    tipo_cliente: Optional[PropertyWithOptions] = Field(None, description="Client type with options: NACIONAL or EXPORTACION")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "name": "Acme Corp",
                "pais_de_operaciones": "Spain",
                "country_entrega": "Spain",
                "country": "Spain"
            }
        }

class DealClientData(BaseModel):
    """Deal-specific client information.

    Attributes:
        tipo_de_venta: Sale type with options (Incoterms).
        tipo_contenedor: Container type with options.
        margen_cliente: Client margin percentage.
        dias_libres: Free days in port (readonly, managed in TransitarioTab).
        naviera: Shipping line/carrier with options (managed in TransitarioTab).
        preprints_language: Language for document printing with options (ES, EN, FR).
        project: Project with options (fallback from deal.project -> company.project).
        codigo_intermediario: Commission intermediary code (editable).
        repercutir_a_cliente: Whether to pass commission cost to client with options (editable).
        tipo_comision: Commission type with options (editable).
        importe_comision: Commission amount in EUR (editable).
        commission_provider_info: Full commission provider information if found.
    """
    tipo_de_venta: PropertyWithOptions = Field(
        ...,
        description="Sale type (Incoterms) with available options"
    )
    tipo_contenedor: PropertyWithOptions = Field(
        ...,
        description="Container type with available options"
    )
    margen_cliente: Optional[float] = Field(
        None,
        description="Client margin percentage",
        ge=0,
        le=100
    )
    dias_libres: Optional[int] = Field(
        None,
        description="Free days in port (readonly in ClienteTab, managed in TransitarioTab)",
        ge=0
    )
    naviera: Optional[PropertyWithOptions] = Field(
        None,
        description="Shipping line/carrier with available options (managed in TransitarioTab)"
    )
    preprints_language: PropertyWithOptions = Field(
        ...,
        description="Language for document printing with available options (ES, EN, FR)"
    )
    project: Optional[PropertyWithOptions] = Field(
        None,
        description="Project with available options (fallback from deal.project -> company.project)"
    )
    codigo_intermediario: Optional[str] = Field(
        None,
        description="Commission intermediary code (editable, preloaded from provider if available)"
    )
    repercutir_a_cliente: Optional[PropertyWithOptions] = Field(
        None,
        description="Whether to pass commission cost to client with available options (editable)"
    )
    tipo_comision: Optional[PropertyWithOptions] = Field(
        None,
        description="Commission type with available options (editable)"
    )
    importe_comision: Optional[float] = Field(
        None,
        description="Commission amount in EUR (editable)",
        ge=0
    )
    carga_por_palets: Optional[bool] = Field(
        None,
        description="Whether the shipment is loaded by pallets"
    )
    fecha_caducidad_cotizacion: Optional[int] = Field(
        None,
        description="Quote general expiry date (timestamp ms)"
    )
    tipo_iva_comision: Optional[str] = Field(
        None,
        description="IVA repercutido type for commission (persisted on Deal)"
    )
    tipo_iva_comision_soportado: Optional[str] = Field(
        None,
        description="IVA soportado type for commission (persisted on Deal)"
    )
    commission_provider_info: Optional[dict] = Field(
        None,
        description="Full commission provider information (id, name, cardcode) if found"
    )
    iva_comision_defaults: Optional[dict] = Field(
        None,
        description="Product-resolved IVA defaults for commission: {repercutido, soportado}"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "tipo_de_venta": {
                    "value": "exw",
                    "options": [
                        {"label": "EXW (Ex Works)", "value": "exw"},
                        {"label": "FOB (Free On Board)", "value": "fob"}
                    ]
                },
                "tipo_contenedor": {
                    "value": "20dr",
                    "options": [
                        {"label": "20DR", "value": "20dr"},
                        {"label": "40DR", "value": "40dr"}
                    ]
                },
                "margen_cliente": 15.5,
                "dias_libres": 7,
                "naviera": {
                    "value": "maersk",
                    "options": [
                        {"label": "Maersk Line", "value": "maersk"},
                        {"label": "MSC", "value": "msc"}
                    ]
                }
            }
        }

class UpdateContactData(BaseModel):
    """Contact data to update in HubSpot.

    Note: Country is readonly and should not be updated from ClienteTab.
    This model is kept for backwards compatibility but contact updates
    are not used in ClienteTab anymore.

    Attributes:
        country: Contact country value to update (deprecated, readonly).
    """
    country: Optional[str] = Field(None, description="Contact country value to update (deprecated, readonly)")

class UpdateCompanyData(BaseModel):
    """Company data to update in HubSpot from ClienteTab.

    Attributes:
        tipo_cliente: Client type value to update (nacional or exportacion).
    """
    tipo_cliente: Optional[str] = Field(None, description="Client type value to update")

class UpdateDealData(BaseModel):
    """Deal data to update in HubSpot from ClienteTab.

    Note: dias_libres is managed in TransitarioTab, not ClienteTab.

    Attributes:
        tipo_de_venta: Sale type value to update.
        tipo_contenedor: Container type value to update.
        margen_cliente: Client margin percentage to update.
        preprints_language: Language for document printing to update (ES, EN, FR).
        project: Project value to update.
        codigo_intermediario: Commission intermediary code to update.
        repercutir_a_cliente: Whether to pass commission cost to client to update.
        tipo_comision: Commission type to update.
        importe_comision: Commission amount in EUR to update.
        tipo_iva_comision: IVA type for commission to update.
    """
    tipo_de_venta: Optional[str] = Field(None, description="Sale type value to update")
    tipo_contenedor: Optional[str] = Field(None, description="Container type value to update")
    carga_por_palets: Optional[bool] = Field(None, description="Whether the shipment is loaded by pallets")
    margen_cliente: Optional[float] = Field(None, description="Client margin percentage to update", ge=0, le=100)
    preprints_language: Optional[str] = Field(None, description="Language for document printing to update (ES, EN, FR)")
    project: Optional[str] = Field(None, description="Project value to update")
    codigo_intermediario: Optional[str] = Field(None, description="Commission intermediary code to update")
    repercutir_a_cliente: Optional[str] = Field(None, description="Whether to pass commission cost to client to update")
    tipo_comision: Optional[str] = Field(None, description="Commission type to update")
    importe_comision: Optional[float] = Field(None, description="Commission amount in EUR to update", ge=0)
    tipo_iva_comision: Optional[str] = Field(None, description="IVA type for commission to update")
    tipo_iva_comision_soportado: Optional[str] = Field(None, description="Input IVA type for commission to update")
    fecha_caducidad_cotizacion: Optional[int] = Field(None, description="Quote general expiry date to update (timestamp ms)")

class UpdateClientDataRequest(BaseModel):
    """Request model for updating client data from ClienteTab.

    Attributes:
        contact: Contact data to update (deprecated, not used).
        company: Company data to update (tipo_cliente).
        deal: Deal data to update (tipo_de_venta, tipo_contenedor, margen_cliente).
    """
    contact: Optional[UpdateContactData] = Field(None, description="Contact data to update (deprecated, not used)")
    company: Optional[UpdateCompanyData] = Field(None, description="Company data to update")
    deal: Optional[UpdateDealData] = Field(None, description="Deal data to update")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "company": {
                    "tipo_cliente": "nacional"
                },
                "deal": {
                    "tipo_de_venta": "exw",
                    "tipo_contenedor": "20dr",
                    "margen_cliente": 15.5
                }
            }
        }

class UpdateDocumentacionData(BaseModel):
    """Deal documentacion data to update in HubSpot from DocumentacionTab.

    All fields are optional and only non-null values will be updated.

    Attributes:
        # Idioma global
        preprints_language: Language for document printing (ES, EN, FR, IT).

        # Documentación Logística
        documentos_a_generar: Documents to generate (multi-select semicolon-separated).
        texto_en_bl: Bill of Lading text/description.
        comentarios_logistica: Internal logistics comments.
        comentarios_transitario: Freight forwarder instructions.

        # Certificado de Origen
        consignee: Importer/consignee data.
        shipper: Exporter/shipper data.
        notify: Notify party data.
        certificado_origen_mercancia: Certificate of origin merchandise description.

        # Método de Pago & Textos
        metodo_pago_plantilla: Payment method template selection.
        banco_cobro: Collection bank selection.
        cotizacion_rapida: Quick quotation flag.
        texto_adicional_metodo_pago: Additional payment method text.

        # Textos Generados
        texto_banco: Bank information text.
        texto_forma_pago: Payment terms text.
        texto_base_oferta: Base offer text.
    """
    # Idioma global
    preprints_language: Optional[str] = Field(None, description="Language for document printing")

    # Documentación
    direccion_fiscal: Optional[str] = Field(None, description="Fiscal address for documentation")
    direccion_de_entrega: Optional[str] = Field(None, description="Delivery address (optional)")

    # Documentación Logística
    desagregar_cotizacion: Optional[bool] = Field(None, description="Whether to disaggregate containers in the quote")
    documentos_a_generar: Optional[str] = Field(None, description="Documents to generate (semicolon-separated)")
    texto_en_bl: Optional[str] = Field(None, description="Bill of Lading text")
    comentarios_logistica: Optional[str] = Field(None, description="Internal logistics comments")
    comentarios_transitario: Optional[str] = Field(None, description="Freight forwarder instructions")

    # Certificado de Origen
    consignee: Optional[str] = Field(None, description="Importer/consignee data")
    shipper: Optional[str] = Field(None, description="Exporter/shipper data")
    notify: Optional[str] = Field(None, description="Notify party data")
    certificado_origen_mercancia: Optional[str] = Field(None, description="Certificate of origin merchandise description")
    observaciones: Optional[str] = Field(None, description="Observations for certificate of origin")

    # Método de Pago & Textos
    metodo_pago_plantilla: Optional[str] = Field(None, description="Payment method template")
    banco_cobro: Optional[str] = Field(None, description="Collection bank")
    cotizacion_rapida: Optional[bool] = Field(None, description="Quick quotation flag")
    texto_adicional_metodo_pago: Optional[str] = Field(None, description="Additional payment method text")

    # Textos Generados
    texto_banco: Optional[str] = Field(None, description="Bank information text")
    texto_forma_pago: Optional[str] = Field(None, description="Payment terms text")
    texto_base_oferta: Optional[str] = Field(None, description="Base offer text")

    # Identificación y Referencia
    cif: Optional[str] = Field(None, description="NIF/CIF del cliente para la cotización (sobreescribe company.cif en el deal)")
    referencia_cotizacion: Optional[str] = Field(None, description="Referencia de la cotización")

class UpdateDocumentacionRequest(BaseModel):
    """Request model for updating documentacion data from DocumentacionTab.

    Attributes:
        deal: Deal documentacion data to update.
    """
    deal: Optional[UpdateDocumentacionData] = Field(None, description="Deal documentacion data to update")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "deal": {
                    "preprints_language": "es",
                    "documentos_a_generar": "bl;packing_list;commercial_invoice",
                    "texto_en_bl": "Description of goods for BL",
                    "consignee": "ABC Import Company\n123 Main St\nParis, France",
                    "shipper": "PROEX International Trading S.L.\nSpain"
                }
            }
        }

class DealDocumentacionData(BaseModel):
    """Deal documentacion data response model.

    Contains all documentation-related fields from a deal.

    Attributes:
        preprints_language: Language for document printing with options.
        direccion_fiscal: Fiscal address for documentation.
        documentos_a_generar: Documents to generate with options.
        texto_en_bl: Bill of Lading text.
        comentarios_logistica: Internal logistics comments.
        comentarios_transitario: Freight forwarder instructions.
        consignee: Importer/consignee data.
        shipper: Exporter/shipper data.
        notify: Notify party data.
        certificado_origen_mercancia: Certificate of origin merchandise description.
        metodo_pago_plantilla: Payment method template with options.
        banco_cobro: Collection bank with options.
        cotizacion_rapida: Quick quotation flag.
        texto_adicional_metodo_pago: Additional payment method text.
        texto_banco: Bank information text.
        texto_forma_pago: Payment terms text.
        texto_base_oferta: Base offer text.
    """
    # Idioma global
    preprints_language: Optional[PropertyWithOptions] = Field(None, description="Language for document printing")

    # Documentación
    direccion_fiscal: Optional[str] = Field(None, description="Fiscal address for documentation")
    direccion_de_entrega: Optional[str] = Field(None, description="Delivery address (optional)")

    # Documentación Logística
    desagregar_cotizacion: Optional[bool] = Field(None, description="Whether to disaggregate containers in the quote")
    documentos_a_generar: Optional[PropertyWithOptions] = Field(None, description="Documents to generate")
    texto_en_bl: Optional[str] = Field(None, description="Bill of Lading text")
    comentarios_logistica: Optional[str] = Field(None, description="Internal logistics comments")
    comentarios_transitario: Optional[str] = Field(None, description="Freight forwarder instructions")

    # Certificado de Origen
    consignee: Optional[str] = Field(None, description="Importer/consignee data")
    shipper: Optional[str] = Field(None, description="Exporter/shipper data")
    notify: Optional[str] = Field(None, description="Notify party data")
    certificado_origen_mercancia: Optional[str] = Field(None, description="Certificate of origin merchandise description")
    observaciones: Optional[str] = Field(None, description="Observations for certificate of origin")

    # Método de Pago & Textos
    metodo_pago_plantilla: Optional[PropertyWithOptions] = Field(None, description="Payment method template")
    banco_cobro: Optional[PropertyWithOptions] = Field(None, description="Collection bank")
    cotizacion_rapida: Optional[bool] = Field(None, description="Quick quotation flag")
    texto_adicional_metodo_pago: Optional[str] = Field(None, description="Additional payment method text")

    # Textos Generados
    texto_banco: Optional[str] = Field(None, description="Bank information text")
    texto_forma_pago: Optional[str] = Field(None, description="Payment terms text")
    texto_base_oferta: Optional[str] = Field(None, description="Base offer text")

    # Identificación y Referencia
    cif: Optional[str] = Field(None, description="NIF/CIF del cliente para la cotización")
    referencia_cotizacion: Optional[str] = Field(None, description="Referencia de la cotización")

class EmpresaAsociadaItem(BaseModel):
    """Empresa asociada a la del negocio por la relación "Proveedor-Cliente"."""
    id: str
    name: Optional[str] = None
    address_entrega: Optional[str] = None


class EmpresasAsociadasResponse(BaseResponse):
    """Empresas asociadas para el selector de autorrelleno de dirección de entrega."""
    empresas: List[EmpresaAsociadaItem] = Field(default_factory=list, description="Empresas asociadas ordenadas por nombre")


class DealDocumentacionResponse(BaseResponse):
    """Response model for deal documentacion data endpoint.

    Attributes:
        deal: Deal documentacion data.
    """
    deal: Optional[DealDocumentacionData] = Field(None, description="Deal documentacion data")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "deal": {
                    "preprints_language": {
                        "value": "es",
                        "options": [
                            {"label": "Español", "value": "es"},
                            {"label": "English", "value": "en"}
                        ]
                    },
                    "documentos_a_generar": {
                        "value": "bl;packing_list",
                        "options": [
                            {"label": "BL", "value": "bl"},
                            {"label": "Packing List", "value": "packing_list"}
                        ]
                    },
                    "texto_banco": "<p>Bank information text</p>",
                    "texto_base_oferta": "<p>Base offer text</p>"
                }
            }
        }

class ClientDataResponse(BaseResponse):
    """Response model for client data endpoint.

    Attributes:
        company: Company information from HubSpot.
        deal: Deal-specific client information.
        clasificacion: Client classification based on country (NACIONAL or EXPORTACION).
        proyecto: Project/origin channel from contact or company (for beverage fair analytics).
    """
    company: Optional[CompanyData] = Field(
        None,
        description="Company information from HubSpot"
    )
    deal: Optional[DealClientData] = Field(
        None,
        description="Deal-specific client information"
    )
    clasificacion: Optional[str] = Field(
        None,
        description="Client classification: NACIONAL or EXPORTACION"
    )
    proyecto: Optional[str] = Field(
        None,
        description="Project/origin channel from contact or company (for beverage fair analytics)"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "company": {
                    "name": "Acme Corp"
                },
                "deal": {
                    "tipo_de_venta": {
                        "value": "exw",
                        "options": []
                    },
                    "tipo_contenedor": {
                        "value": "20dr",
                        "options": []
                    },
                    "margen_cliente": 15.5,
                    "dias_libres": 7
                }
            }
        }

class HealthResponse(BaseModel):
    """Health check response model.

    Attributes:
        status: Service health status.
        version: Application version.
        timestamp: Response timestamp.
        hubspot_status: HubSpot API connectivity status.
    """
    status: str = Field(
        "healthy",
        description="Service health status"
    )
    version: str = Field(
        ...,
        description="Application version"
    )
    timestamp: datetime = Field(
        ...,
        description="Response timestamp"
    )
    hubspot_status: Optional[str] = Field(
        None,
        description="HubSpot API connectivity status"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "version": "1.0.0",
                "timestamp": "2025-11-04T09:00:00Z",
                "hubspot_status": "connected"
            }
        }

# Product Models
class ProductItem(BaseModel):
    """Product item model.

    Attributes:
        id: HubSpot product ID.
        name: Product name.
        hs_sku: Product SKU/reference code.
        description: Product description.
        barcode: Product barcode.
        cardcode: Usual supplier code.
        n_articulo_proveedor: Supplier article number.
        codigo_proveedor: Supplier product code.
        tipo_iva_soportado: Supported VAT type.
        tipo_iva_repercutido: Transferred VAT type.
        peso_neto: Net weight per box in kilos.
        cuentas_mayor: General ledger accounts.
        gestion_stock_almacen: Warehouse stock management.
        formato: Format.
        sap_updated: SAP updated flag.
        hs_price_eur: EX-WORKS FABRICA PROVEEDOR price.
        ultimo_precio_compra: Last purchase price.
        ultimo_precio_evaluado: Last evaluated price.
        name_en: Name in English.
        name_fr: Name in French.
        tipo_articulo: Article type (fabricante, transitario, servicio, etc.).
        hs_product_type: Product type for filtering (inventory, service, container).
    """
    id: str = Field(..., description="HubSpot product ID")
    name: str = Field(..., description="Product name")
    hs_sku: Optional[str] = Field(None, description="Product SKU/reference code")
    description: Optional[str] = Field(None, description="Product description")
    barcode: Optional[str] = Field(None, description="Product barcode")
    cardcode: Optional[str] = Field(None, description="Usual supplier code (Proveedor habitual)")
    n_articulo_proveedor: Optional[str] = Field(None, description="Supplier article number")
    codigo_proveedor: Optional[str] = Field(None, description="Supplier product code")
    tipo_iva_soportado: Optional[str] = Field(None, description="Supported VAT type")
    tipo_iva_repercutido: Optional[str] = Field(None, description="Transferred VAT type")
    tipo_iva_soportado_eu: Optional[str] = Field(None, description="Supported VAT type for EU companies")
    tipo_iva_repercutido_eu: Optional[str] = Field(None, description="Transferred VAT type for EU companies")
    tipo_iva_soportado_ex: Optional[str] = Field(None, description="Supported VAT type for foreign companies")
    tipo_iva_repercutido_ex: Optional[str] = Field(None, description="Transferred VAT type for foreign companies")
    peso_neto: Optional[float] = Field(None, description="Net weight per box in kilos")
    cuentas_mayor: Optional[str] = Field(None, description="General ledger accounts")
    gestion_stock_almacen: Optional[str] = Field(None, description="Warehouse stock management")
    formato: Optional[str] = Field(None, description="Format")
    marca_certificado: Optional[str] = Field(None, description="Brand")
    sap_updated: Optional[str] = Field(None, description="SAP updated flag")
    hs_price_eur: Optional[float] = Field(None, description="EX-WORKS FABRICA PROVEEDOR price")
    ultimo_precio_compra: Optional[float] = Field(None, description="Last purchase price")
    ultimo_precio_evaluado: Optional[float] = Field(None, description="Last evaluated price")
    name_es: Optional[str] = Field(None, description="Clean name in Spanish")
    name_en: Optional[str] = Field(None, description="Name in English")
    name_fr: Optional[str] = Field(None, description="Name in French")
    tipo_articulo: Optional[str] = Field(None, description="Article type (fabricante, transitario, servicio, etc.)")
    hs_product_type: Optional[str] = Field(None, description="Product type (inventory, service, container)")
    activo: Optional[str] = Field(None, description="Product active status (Activo/Inactivo)")
    # Container capacity fields
    unidades_cam16: Optional[int] = Field(None, description="Maximum units for CAMION 16P container")
    unidades_d20dr: Optional[int] = Field(None, description="Maximum units for D20DR container")
    unidades_d40dr: Optional[int] = Field(None, description="Maximum units for D40DR container")
    unidades_palet: Optional[int] = Field(None, description="Maximum units for PALETS container")
    unidades_tra80: Optional[int] = Field(None, description="Maximum units for TRAILER 80 container")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "id": "123456789",
                "name": "Vino Tinto Reserva",
                "hs_sku": "VT-RES-001",
                "description": "Vino tinto de gran calidad",
                "barcode": "1234567890123",
                "cardcode": "PROV001",
                "hs_price_eur": 15.50,
                "ultimo_precio_compra": 14.80,
                "name_en": "Red Wine Reserve",
                "name_fr": "Vin Rouge Réserve",
                "tipo_articulo": "fabricante",
                "unidades_d20dr": 1200,
                "unidades_d40dr": 2400
            }
        }

class ProductListResponse(BaseResponse):
    """Response model for product list endpoint.

    Attributes:
        products: List of products matching the search query.
        total: Total number of products found.
    """
    products: list[ProductItem] = Field(
        default_factory=list,
        description="List of products matching the search query"
    )
    total: int = Field(
        0,
        description="Total number of products found"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "products": [
                    {
                        "id": "123456789",
                        "name": "Vino Tinto Reserva",
                        "sku": "VT-RES-001",
                        "description": "Vino tinto de gran calidad"
                    }
                ],
                "total": 1
            }
        }

class ProviderData(BaseModel):
    """Provider/supplier data model.

    Attributes:
        id: HubSpot company ID of the provider.
        name: Provider company name.
        cardcode: Provider card code.
        lugar_de_la_carga_envio: Loading/shipping location.
    """
    id: str = Field(..., description="HubSpot company ID")
    name: str = Field(..., description="Provider company name")
    cardcode: Optional[str] = Field(None, description="Provider card code")
    lugar_de_la_carga_envio: Optional[str] = Field(None, description="Loading/shipping location")

class ProductProviderResponse(BaseResponse):
    """Response model for product provider endpoint.

    Attributes:
        provider: Provider data for the product.
    """
    provider: Optional[ProviderData] = Field(None, description="Provider data")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "provider": {
                    "id": "987654321",
                    "name": "Bodegas Ejemplo S.L.",
                    "cardcode": "PROV-001"
                }
            }
        }

class ProviderListResponse(BaseResponse):
    """Response model for provider list endpoint.

    Attributes:
        providers: List of providers matching the search query.
        total: Total number of providers found.
    """
    providers: list[ProviderData] = Field(
        default_factory=list,
        description="List of providers matching the search query"
    )
    total: int = Field(
        0,
        description="Total number of providers found"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "providers": [
                    {
                        "id": "987654321",
                        "name": "Bodegas Ejemplo S.L.",
                        "cardcode": "PROV-001"
                    }
                ],
                "total": 1
            }
        }

# Articles Models
class LineItemInput(BaseModel):
    """Line item input for creating line items.

    Attributes:
        id: HubSpot product object ID.
        margen_unitario: Unit margin percentage for the line item.
    """
    id: str = Field(..., description="HubSpot product object ID")
    margen_unitario: Optional[float] = Field(None, description="Unit margin percentage for the line item")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "hs_sku": "0303005008-00205",
                "margen_unitario": 12.0
            }
        }

class ProviderInput(BaseModel):
    """Provider input for associating providers with deals.

    Attributes:
        cardcode: Provider cardcode.
    """
    cardcode: str = Field(..., description="Provider cardcode")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "cardcode": "P0490"
            }
        }

class UpdateArticlesRequest(BaseModel):
    """Request model for updating deal articles from ArticulosTab.

    Attributes:
        line_items: List of line items to create (products with quantities).
        providers: List of providers to associate with the deal.
        lugar_carga: Lugar de carga (loading location) to save to deal.
    """
    line_items: list[LineItemInput] = Field(
        default_factory=list,
        description="List of line items to create with SKU IDs and quantities"
    )
    providers: list[ProviderInput] = Field(
        default_factory=list,
        description="List of providers to associate with the deal"
    )
    lugar_carga: Optional[str] = Field(
        None,
        description="Lugar de carga (loading location) for the shipment"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "line_items": [
                    {"hs_sku": "0303005008-00205" },
                    {"hs_sku": "0303005009-00106" }
                ],
                "providers": [
                    {"cardcode": "P0490"},
                    {"cardcode": "P0543"}
                ]
            }
        }

class ServiceItemInput(BaseModel):
    """Service item input for creating service line items.

    Attributes:
        product_id: HubSpot product ID for the service.
        product_name: Service/product name.
        provider_id: HubSpot provider ID for this service.
        provider_cardcode: Provider cardcode for this service.
        price: Price for the service.
        tipo_iva: IVA type for this service.
    """
    product_id: str = Field(..., description="HubSpot product ID for the service")
    product_name: str = Field(..., description="Service/product name")
    provider_id: str = Field(..., description="HubSpot provider ID for this service")
    provider_cardcode: str = Field(..., description="Provider cardcode for this service")
    price: float = Field(..., description="Price for the service", gt=0)
    tipo_iva: Optional[str] = Field(None, description="IVA repercutido type for this service")
    tipo_iva_soportado: Optional[str] = Field(None, description="IVA soportado type for this service")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "product_id": "21912688938",
                "product_name": "Certificado Sanitario",
                "provider_id": "26148270263",
                "provider_cardcode": "P0159",
                "price": 450.00
            }
        }

class UpdateServicesRequest(BaseModel):
    """Request model for updating deal services from ServiciosTab.

    Attributes:
        services: List of service items to create as line items.
    """
    services: list[ServiceItemInput] = Field(
        default_factory=list,
        description="List of service items to create as line items"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "services": [
                    {
                        "product_id": "21912688938",
                        "product_name": "Certificado Sanitario",
                        "provider_id": "26148270263",
                        "provider_cardcode": "P0159",
                        "price": 450.00
                    },
                    {
                        "product_id": "21912688939",
                        "product_name": "Seguro de Mercancía",
                        "provider_id": "40691227933",
                        "provider_cardcode": "P0234",
                        "price": 1250.00
                    }
                ]
            }
        }

class ServiceItem(BaseModel):
    """Service item for GET response."""
    product_id: str = Field(..., description="HubSpot product ID")
    product_name: str = Field(..., description="Service/product name")
    tipo_articulo: Optional[str] = Field(None, description="Product type")
    provider_id: Optional[str] = Field(None, description="HubSpot provider ID")
    provider_name: Optional[str] = Field(None, description="Provider name")
    provider_cardcode: Optional[str] = Field(None, description="Provider cardcode")
    price: float = Field(..., description="Price for the service")
    tipo_iva_repercutido: Optional[str] = Field(None, description="VAT type for the service line item")
    tipo_iva_soportado: Optional[str] = Field(None, description="Input VAT type for the service line item")

class DealServicesResponse(BaseModel):
    """Response model for GET deal services."""
    success: bool = Field(..., description="Whether the request was successful")
    error: Optional[str] = Field(None, description="Error message if any")
    services: list[ServiceItem] = Field(default_factory=list, description="List of service items")

# Legacy model for backwards compatibility
class ArticleAssociation(BaseModel):
    """Article association with provider (DEPRECATED - use UpdateArticlesRequest instead).

    Attributes:
        hs_sku: Product SKU ID from HubSpot.
        cardcode: Provider cardcode.
    """
    hs_sku: str = Field(..., description="Product SKU ID from HubSpot")
    cardcode: Optional[str] = Field(None, description="Provider cardcode")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "hs_sku": "0303005008-00205",
                "cardcode": "P0490"
            }
        }

class ArticleWithProvider(BaseModel):
    """Article with provider information.

    Attributes:
        id: HubSpot product ID.
        hs_sku: Product SKU ID.
        codigo_proveedor: Supplier product code.
        name: Product name.
        provider_cardcode: Provider cardcode (if associated).
        provider_name: Provider name (if associated).
        provider_id: HubSpot company ID of the provider (if associated).
        peso_neto: Net weight per box in kilograms.
        hs_price_eur: EX-WORKS FABRICA PROVEEDOR price (purchase price from supplier).
        ultimo_precio_compra: Last purchase price.
        ultimo_precio_evaluado: Last evaluated price.
        tipo_iva_repercutido: Transferred VAT type.
        unidades_cam16: Maximum units for CAMION 16P container.
        unidades_d20dr: Maximum units for D20DR container.
        unidades_d40dr: Maximum units for D40DR container.
        unidades_palet: Maximum units for PALETS container.
        unidades_tra80: Maximum units for TRAILER 80 container.
    """
    id: str = Field(..., description="HubSpot product ID")
    hs_sku: Optional[str] = Field(None, description="Product SKU ID")
    codigo_proveedor: Optional[str] = Field(None, description="Supplier product code")
    name: str = Field(..., description="Product name")
    name_es: Optional[str] = Field(None, description="Clean name in Spanish")
    name_en: Optional[str] = Field(None, description="Name in English")
    name_fr: Optional[str] = Field(None, description="Name in French")
    description: Optional[str] = Field(None, description="Product description (nombre limpio ES para documentos de cliente)")
    provider_cardcode: Optional[str] = Field(None, description="Provider cardcode")
    provider_name: Optional[str] = Field(None, description="Provider name")
    provider_id: Optional[str] = Field(None, description="HubSpot company ID of the provider")
    peso_neto: Optional[float] = Field(None, description="Net weight per box in kilograms")
    # Price fields for negotiation
    hs_price_eur: Optional[float] = Field(None, description="EX-WORKS FABRICA PROVEEDOR price (purchase price)")
    ultimo_precio_compra: Optional[float] = Field(None, description="Last purchase price")
    ultimo_precio_evaluado: Optional[float] = Field(None, description="Last evaluated price")
    margen_unitario: Optional[float] = Field(None, description="Unit margin percentage for negotiation")
    tipo_iva_repercutido: Optional[str] = Field(None, description="Transferred VAT type")
    tipo_iva_soportado: Optional[str] = Field(None, description="Input VAT type")
    # Container capacity fields
    unidades_cam16: Optional[int] = Field(None, description="Maximum units for CAMION 16P container")
    unidades_d20dr: Optional[int] = Field(None, description="Maximum units for D20DR container")
    unidades_d40dr: Optional[int] = Field(None, description="Maximum units for D40DR container")
    unidades_palet: Optional[int] = Field(None, description="Maximum units for PALETS container")
    unidades_tra80: Optional[int] = Field(None, description="Maximum units for TRAILER 80 container")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "id": "123456789",
                "hs_sku": "0303005008-00205",
                "name": "2022 VINO TINTO SECO SHANPELINO",
                "provider_cardcode": "P0490",
                "provider_name": "REAL EXPORT ESPUMANTES DE PORTUGAL LTD",
                "provider_id": "987654321",
                "peso_neto": 8.4,
                "unidades_d20dr": 1200,
                "unidades_d40dr": 2400
            }
        }

class ProviderSummary(BaseModel):
    """Proveedor asociado al deal."""
    id: str = Field(..., description="ID del objeto proveedor (custom object) o cardcode si no hay ID")
    cardcode: str = Field(..., description="Código de proveedor (cardcode)")
    name: Optional[str] = Field(None, description="Nombre del proveedor")
    lugar_de_la_carga_envio: Optional[str] = Field(None, description="Lugar de carga/envío (si está disponible)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "26160148103",
                "cardcode": "P0671",
                "name": "DAMM GLOBAL BARCELONA, S.L. (Salem)",
                "lugar_de_la_carga_envio": "Barcelona"
            }
        }

class DealArticlesResponse(BaseResponse):
    """Respuesta del endpoint de artículos del deal (NO contenedores)."""
    articles: List[ArticleWithProvider] = Field(
        default_factory=list,
        description="Lista de artículos (productos) excluyendo contenedores"
    )
    providers: List[ProviderSummary] = Field(
        default_factory=list,
        description="Lista de proveedores asociados al deal (únicos por cardcode)"
    )
    lugar_carga: Optional[str] = Field(
        None,
        description="Lugar de carga seleccionado (del deal)"
    )
    lugar_carga_options: List[PropertyOption] = Field(
        default_factory=list,
        description="Opciones disponibles para lugar de carga"
    )
    total_articles: int = Field(0, description="Total de artículos")
    total_providers: int = Field(0, description="Total de proveedores únicos")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "articles": [
                    {
                        "hs_sku": "0303003003-00167",
                        "name": "4VEVTLB0 2024 BEBIDA ENERGETICA MIX VODKA \"ENERDRINK\"...",
                        "provider_cardcode": "P0671",
                        "provider_name": "DAMM GLOBAL BARCELONA, S.L. (Salem)",
                        "provider_id": "26160148103"
                    },
                    {
                        "hs_sku": "0303003003-00173",
                        "name": "1004009 BEBIDA ENERGETICA \"ENERDRINK EVOLUTION\"...",
                        "provider_cardcode": "P0543",
                        "provider_name": None,
                        "provider_id": None
                    }
                ],
                "providers": [
                    {
                        "id": "26160148103",
                        "cardcode": "P0671",
                        "name": "DAMM GLOBAL BARCELONA, S.L. (Salem)",
                        "lugar_de_la_carga_envio": "Barcelona"
                    }
                ],
                "total_articles": 2,
                "total_providers": 1
            }
        }

# Container Models
class ContainerArticle(BaseModel):
    """Article within a container configuration.

    Attributes:
        articuloId: HubSpot product object ID.
        nombre: Product name.
        numCajas: Number of boxes/cases of this product in the container.
    """
    articuloId: str = Field(..., description="HubSpot product object ID", alias="articuloId")
    nombre: str = Field(..., description="Product name")
    numCajas: int = Field(..., description="Number of boxes/cases", ge=0, alias="numCajas")

    class Config:
        """Pydantic configuration."""
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "articuloId": "0303005008-00205",
                "nombre": "Vino Tinto Reserva",
                "numCajas": 200
            }
        }

class UniqueContainerArticle(BaseModel):
    """Article within a unique container configuration with unique name.

    Attributes:
        articuloId: Product SKU ID.
        nombre: Product name.
        articuloName: Unique article name combining container_name and nombre.
        numCajas: Number of boxes/cases of this product in the container.
        line_item_id: HubSpot line item ID for this article (hs_product_id of the line item).
        price: Price per unit (precio base por caja).
        precio_de_coste: Negotiated cost price per unit (price * (1 + margen_unitario)).
        margen_unitario: Unit margin (decimal 0-1).
        tipo_iva_repercutido: Type of VAT to charge to customer.
    """
    articuloId: str = Field(..., description="Product SKU ID", alias="articuloId")
    nombre: str = Field(..., description="Product name")
    articuloName: str = Field(..., description="Unique article name (container_name - nombre)", alias="articuloName")
    numCajas: int = Field(..., description="Number of boxes/cases", ge=0, alias="numCajas")
    line_item_id: Optional[str] = Field(None, description="HubSpot line item ID (hs_product_id)")
    price: Optional[float] = Field(None, description="Base price per unit")
    precio_de_coste: Optional[float] = Field(None, description="Negotiated cost price per unit")
    margen_unitario: Optional[float] = Field(None, description="Unit margin (decimal 0-1)")
    tipo_iva_repercutido: Optional[str] = Field(None, description="Type of VAT to charge")
    tipo_iva_soportado: Optional[str] = Field(None, description="Type of input VAT")
    precio_negociado_tipo_venta: Optional[float] = Field(None, description="Anchored per-box price of the selected incoterm")
    incoterm_precio_negociado: Optional[str] = Field(None, description="tipo_de_venta the anchored price was set under")

    class Config:
        """Pydantic configuration."""
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "articuloId": "0303005008-00205",
                "nombre": "Vino Tinto Reserva",
                "articuloName": "D20DR-1 - Vino Tinto Reserva",
                "numCajas": 200,
                "price": 12.50,
                "margen_unitario": 0.15,
                "tipo_iva_repercutido": "A21"
            }
        }

class ContainerConfig(BaseModel):
    """Container configuration stored as JSON.

    Attributes:
        articulos: List of articles with quantities in the container.
        ocupacion: Occupancy percentage of the container (0-103, allowing ±3% margin).
        pesoNeto: Net weight of the container in kilograms.
    """
    articulos: list[ContainerArticle] = Field(
        default_factory=list,
        description="List of articles with quantities"
    )
    ocupacion: float = Field(..., description="Occupancy percentage", ge=0, le=200)
    pesoNeto: float = Field(..., description="Net weight in kg", ge=0, alias="pesoNeto")

    class Config:
        """Pydantic configuration."""
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "articulos": [
                    {
                        "articuloId": "0303005008-00205",
                        "nombre": "Vino Blanco",
                        "numCajas": 200
                    },
                    {
                        "articuloId": "0303005009-00106",
                        "nombre": "Vino Tinto",
                        "numCajas": 400
                    }
                ],
                "ocupacion": 85.5,
                "pesoNeto": 5040
            }
        }

class UniqueContainerConfig(BaseModel):
    """Unique container configuration with articles containing articuloName.

    Attributes:
        articulos: List of unique articles with articuloName field.
        ocupacion: Occupancy percentage of the container (0-103, allowing ±3% margin).
        pesoNeto: Net weight of the container in kilograms.
    """
    articulos: list[UniqueContainerArticle] = Field(
        default_factory=list,
        description="List of articles with unique names"
    )
    ocupacion: float = Field(..., description="Occupancy percentage", ge=0, le=200)
    pesoNeto: float = Field(..., description="Net weight in kg", ge=0, alias="pesoNeto")

    class Config:
        """Pydantic configuration."""
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "articulos": [
                    {
                        "articuloId": "0303005008-00205",
                        "nombre": "Vino Blanco",
                        "articuloName": "D20DR-1 - Vino Blanco",
                        "numCajas": 200
                    },
                    {
                        "articuloId": "0303005009-00106",
                        "nombre": "Vino Tinto",
                        "articuloName": "D20DR-1 - Vino Tinto",
                        "numCajas": 400
                    }
                ],
                "ocupacion": 99.5,
                "pesoNeto": 5040
            }
        }

class ContainerArticleEdit(BaseModel):
    """Article in a container edit payload.

    Existing articles carry line_item_id; new articles omit it so the backend
    creates the line item (and the matching ELEMENTO custom object if any).
    """
    line_item_id: Optional[str] = Field(None, description="Existing HubSpot line item ID (omit for new article)")
    articuloId: str = Field(..., description="HubSpot product object ID", alias="articuloId")
    articuloName: Optional[str] = Field(None, description="Article name (without container prefix)", alias="articuloName")
    nombre: Optional[str] = Field(None, description="Article name (alias for articuloName)")
    numCajas: int = Field(..., description="Number of boxes/cases", ge=0, alias="numCajas")
    precio_de_coste: Optional[float] = Field(None, description="Provider cost price")
    price: Optional[float] = Field(None, description="Sale (EXW) price per unit")
    margen_unitario: Optional[float] = Field(None, description="Unit margin (decimal 0-1)")
    tipo_iva_repercutido: Optional[str] = Field(None, description="VAT charged to customer")
    tipo_iva_soportado: Optional[str] = Field(None, description="Input VAT")
    precio_negociado_tipo_venta: Optional[float] = Field(None, description="Anchored per-box price of the selected incoterm", ge=0)
    incoterm_precio_negociado: Optional[str] = Field(None, description="tipo_de_venta the anchored price was set under")

    class Config:
        populate_by_name = True

class ContainerConfigEdit(BaseModel):
    """Container configuration for edit-in-place operations.

    Does not enforce 97-103% occupancy: the modal lets the user save freely
    and surfaces an inline warning instead of blocking.
    """
    articulos: list[ContainerArticleEdit] = Field(default_factory=list)
    ocupacion: float = Field(..., description="Occupancy percentage", ge=0, le=200)
    pesoNeto: float = Field(..., description="Net weight in kg", ge=0, alias="pesoNeto")
    pesoBruto: Optional[float] = Field(None, description="Gross weight in kg", alias="pesoBruto")
    totalCajas: Optional[int] = Field(None, description="Total boxes in the container", alias="totalCajas")

    class Config:
        populate_by_name = True
        extra = "allow"

class ContainerLineItemInput(BaseModel):
    """One container instance in an incremental merge.

    Each entry is a single container. `line_item_id` present means it already
    exists (PREVIO, edited in place); absent means it's new (NUEVO). Containers
    not present in the payload are deleted. `cantidad`/`container_number` are
    deprecated (kept optional for compatibility) and ignored by the backend.
    """
    tipo: str = Field(..., description="Container type SKU value")
    container_config: ContainerConfigEdit = Field(..., description="Container configuration with per-article ids/prices")
    line_item_id: Optional[str] = Field(None, description="Existing container line item ID (omit for a new container)")
    cantidad: Optional[int] = Field(1, description="DEPRECATED — ignored; each entry is one container")
    container_number: Optional[int] = Field(None, description="DEPRECATED — ignored; numbering is server-assigned")

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "tipo": "D20DR",
                "line_item_id": "12345",
                "container_config": {
                    "articulos": [
                        {
                            "line_item_id": "67890",
                            "articuloId": "0303005008-00205",
                            "nombre": "Vino Blanco",
                            "numCajas": 200,
                            "price": 12.5,
                            "precio_de_coste": 10.0,
                            "margen_unitario": 0.25
                        }
                    ],
                    "ocupacion": 85.5,
                    "pesoNeto": 5040
                }
            }
        }

class UpdateContainersRequest(BaseModel):
    """Request model for updating deal containers from ContenedoresTab.

    Attributes:
        containers: List of containers to create as line items.
    """
    containers: list[ContainerLineItemInput] = Field(
        default_factory=list,
        description="List of containers to create with quantities and configurations"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "containers": [
                    {
                        "tipo": "D20DR",
                        "line_item_id": "12345",
                        "container_config": {
                            "articulos": [
                                {
                                    "line_item_id": "67890",
                                    "articuloId": "0303005008-00205",
                                    "nombre": "Vino Blanco",
                                    "numCajas": 200,
                                    "price": 12.5,
                                    "precio_de_coste": 10.0,
                                    "margen_unitario": 0.25
                                }
                            ],
                            "ocupacion": 75.0,
                            "pesoNeto": 1680
                        }
                    },
                    {
                        "tipo": "D40DR",
                        "container_config": {
                            "articulos": [
                                {
                                    "articuloId": "0303005009-00106",
                                    "nombre": "Vino Tinto",
                                    "numCajas": 1200
                                }
                            ],
                            "ocupacion": 90.0,
                            "pesoNeto": 10080
                        }
                    }
                ]
            }
        }

class UpdateContainersResponse(BaseResponse):
    """Response for POST /{deal_id}/containers.

    `job_id` es el id del job de re-sync de custom objects encolado cuando el
    deal ya está publicado (el worker recalcula precios/elementos en background).
    Es None si no había nada que sincronizar (deal sin publicar) — en ese caso
    el frontend no necesita pollear.
    """
    job_id: Optional[str] = Field(
        None,
        description="Job id del re-sync async encolado, o None si no aplica"
    )


class UpdateSingleContainerRequest(BaseModel):
    """Request body for PATCH /{deal_id}/containers/{container_line_item_id}.

    Carries the new desired state of a single container. The backend diffs it
    against the current state and edits line items + ELEMENTO custom objects in place.
    """
    container_config: ContainerConfigEdit = Field(..., description="Updated container configuration")
    numero_container: Optional[str] = Field(
        None,
        description="numero_container/nombre del contenedor ({deal_id}-NNN). Fallback para resolver el contenedor si el line_item_id de la URL quedó obsoleto (stale).",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "container_config": {
                    "articulos": [
                        {
                            "line_item_id": "11111",
                            "articuloId": "0303005008-00205",
                            "articuloName": "Vino Tinto",
                            "numCajas": 250,
                            "price": 13.5,
                            "precio_de_coste": 11.0,
                            "margen_unitario": 0.22,
                            "tipo_iva_repercutido": "R21",
                            "tipo_iva_soportado": "S21"
                        },
                        {
                            "articuloId": "0303005009-00106",
                            "articuloName": "Vino Blanco",
                            "numCajas": 100
                        }
                    ],
                    "ocupacion": 99.0,
                    "pesoNeto": 5500
                }
            }
        }

class ContainerItem(BaseModel):
    """Container item with full details.

    Attributes:
        line_item_id: HubSpot line item ID.
        container_type: Container type (D20DR, D40DR, etc.).
        container_number: Sequential container number.
        cantidad: Quantity of containers of this type.
        container_config: Detailed configuration (articles, occupancy, weight).
    """
    line_item_id: str = Field(..., description="HubSpot line item ID")
    container_type: str = Field(..., description="Container type")
    container_number: int = Field(..., description="Sequential container number")
    cantidad: int = Field(..., description="Quantity of containers")
    container_config: ContainerConfig = Field(..., description="Container configuration")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "line_item_id": "123456789",
                "container_type": "D20DR",
                "container_number": 1,
                "cantidad": 2,
                "container_config": {
                    "articulos": [
                        {
                            "articuloId": "0303005008-00205",
                            "nombre": "Vino Blanco",
                            "numCajas": 200
                        }
                    ],
                    "ocupacion": 75.0,
                    "pesoNeto": 1680
                }
            }
        }

class UniqueContainerItem(BaseModel):
    """Individual container item with unique name.

    Attributes:
        line_item_id: HubSpot line item ID.
        container_type: Container type (D20DR, D40DR, etc.).
        container_number: Sequential container number.
        container_name: Unique container name (e.g., "D20DR-1", "D40DR-2").
        cantidad: Quantity of this specific container (always 1).
        container_config: Detailed configuration with unique article names.
    """
    line_item_id: str = Field(..., description="HubSpot line item ID")
    container_type: str = Field(..., description="Container type")
    container_number: int = Field(..., description="Sequential container number")
    container_name: str = Field(..., description="Unique container name")
    cantidad: int = Field(..., description="Quantity of this container")
    container_config: UniqueContainerConfig = Field(..., description="Container configuration with unique article names")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "line_item_id": "123456789",
                "container_type": "D20DR",
                "container_number": 1,
                "container_name": "D20DR-1",
                "cantidad": 1,
                "container_config": {
                    "articulos": [
                        {
                            "articuloId": "0303005008-00205",
                            "nombre": "Vino Blanco",
                            "articuloName": "D20DR-1 - Vino Blanco",
                            "numCajas": 200
                        }
                    ],
                    "ocupacion": 75.0,
                    "pesoNeto": 1680
                }
            }
        }

class UpdateSingleContainerResponse(BaseResponse):
    """Response model for PATCH /{deal_id}/containers/{container_line_item_id}.

    Returns the updated container in the same shape the frontend uses for
    `unique_containers`, plus optional info about commission recalc and elements sync.
    """
    container: Optional[UniqueContainerItem] = Field(None, description="Updated unique container item")
    elementos_synced: bool = Field(False, description="Whether ELEMENTO custom objects were synced")
    intermediario_recalculated: bool = Field(False, description="Whether the intermediario commission was recomputed")

class DealContainersResponse(BaseResponse):
    """Response model for deal containers endpoint.

    Attributes:
        containers: List of containers with configurations (grouped by type).
        unique_containers: List of all individual containers without grouping.
        total_containers: Total number of container types defined.
        total_units: Total number of physical container units.
        total_colis: Total number of boxes/cases across all containers.
        total_peso_neto: Total net weight across all containers in kg.
    """
    containers: list[ContainerItem] = Field(
        default_factory=list,
        description="List of containers with configurations (grouped by type)"
    )
    unique_containers: list[UniqueContainerItem] = Field(
        default_factory=list,
        description="List of all individual containers without grouping, each with unique container_name"
    )
    total_containers: int = Field(
        0,
        description="Total number of container types defined"
    )
    total_units: int = Field(
        0,
        description="Total number of physical container units"
    )
    total_colis: int = Field(
        0,
        description="Total number of boxes/cases"
    )
    total_peso_neto: float = Field(
        0.0,
        description="Total net weight in kg"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "containers": [
                    {
                        "line_item_id": "123456789",
                        "container_type": "D20DR",
                        "container_number": 1,
                        "cantidad": 2,
                        "container_config": {
                            "articulos": [
                                {
                                    "articuloId": "0303005008-00205",
                                    "nombre": "Vino Blanco",
                                    "numCajas": 200
                                }
                            ],
                            "ocupacion": 75.0,
                            "pesoNeto": 1680
                        }
                    }
                ],
                "total_containers": 1,
                "total_units": 2,
                "total_colis": 400,
                "total_peso_neto": 3360.0
            }
        }

# Transit/Route Models
class DealTransitResponse(BaseResponse):
    """Response model for deal transit/route data endpoint.

    Attributes:
        lugar_carga: Loading location value from deal (readonly).
        lugar_carga_label: Loading location label (human-readable).
        puerto_salida: Departure port with available options.
        puerto_destino: Destination port with available options.
        localidad_destino: Destination locality/city value.
        almacen: Warehouse code with available options.
    """
    lugar_carga: Optional[str] = Field(
        None,
        description="Loading location value from deal (readonly)"
    )
    lugar_carga_label: Optional[str] = Field(
        None,
        description="Loading location label (human-readable)"
    )
    puerto_salida: Optional[PropertyWithOptions] = Field(
        None,
        description="Departure port with available options"
    )
    puerto_destino: Optional[PropertyWithOptions] = Field(
        None,
        description="Destination port with available options"
    )
    localidad_destino: Optional[PropertyWithOptions] = Field(
        None,
        description="Destination locality/city with available options"
    )
    almacen: Optional[PropertyWithOptions] = Field(
        None,
        description="Warehouse code with available options"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "lugar_carga": "almacen_proex",
                "lugar_carga_label": "Almacen PROEX",
                "puerto_salida": {
                    "value": "ALG        Algeciras, ES",
                    "options": [
                        {"label": "ALG        Algeciras, ES", "value": "ALG        Algeciras, ES"},
                        {"label": "BCN        Barcelona, ES", "value": "BCN        Barcelona, ES"}
                    ]
                },
                "puerto_destino": {
                    "value": "NYC        New York, US",
                    "options": [
                        {"label": "NYC        New York, US", "value": "NYC        New York, US"},
                        {"label": "LAX        Los Angeles, US", "value": "LAX        Los Angeles, US"}
                    ]
                }
            }
        }

# Route Models (Rutas Hubspot Custom Object)
class RutaItem(BaseModel):
    """Single route item from HubSpot custom object.

    Attributes:
        id: HubSpot route object ID.
        referencia: Route reference (normalized identifier).
        tipo_contenedor: Container type (D20DR, D40DR, etc.).
        transitario: Freight forwarder name.
        transitario_id: Freight forwarder HubSpot ID.
        transitario_cardcode: Freight forwarder cardcode (SAP code).
        naviera: Shipping line/carrier (NA if not applicable).
        lugar_carga: Loading location code.
        puerto_salida: Departure port code.
        puerto_destino: Destination port code.
        localidad_destino: Destination locality code.
        importe: Route price in EUR.
        fecha_caducidad: Expiration date (YYYY-MM-DD or timestamp).
        notas: Additional notes.
        tipo_tramo: Route segment type (terrestre or maritima).
    """
    id: str = Field(..., description="HubSpot route object ID")
    referencia: str = Field(..., description="Route reference (normalized identifier)")
    tipo_contenedor: str = Field(..., description="Container type")
    transitario: Optional[str] = Field(None, description="Freight forwarder name")
    transitario_id: Optional[str] = Field(None, description="Freight forwarder HubSpot ID")
    transitario_cardcode: str = Field(..., description="Freight forwarder cardcode (SAP code)")
    naviera: Optional[str] = Field(None, description="Shipping line/carrier")
    lugar_carga: Optional[str] = Field(None, description="Loading location code")
    puerto_salida: Optional[str] = Field(None, description="Departure port code")
    puerto_destino: Optional[str] = Field(None, description="Destination port code")
    localidad_destino: Optional[str] = Field(None, description="Destination locality code")
    importe: float = Field(..., description="Route price in EUR", ge=0)
    fecha_caducidad: str = Field(..., description="Expiration date")
    notas: Optional[str] = Field(None, description="Additional notes")
    tipo_tramo: str = Field(..., description="Route segment type (terrestre or maritima)")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "id": "123456789",
                "referencia": "D20DR__TIBA__NA__694__VAL__NA__NA",
                "tipo_contenedor": "D20DR",
                "transitario": "TIBA",
                "transitario_id": "987654321",
                "naviera": "NA",
                "lugar_carga": "694",
                "puerto_salida": "VAL",
                "puerto_destino": "NA",
                "localidad_destino": "NA",
                "importe": 850.00,
                "fecha_caducidad": "2025-12-31",
                "notas": "Ruta terrestre preferente",
                "tipo_tramo": "terrestre"
            }
        }

class DealRutasResponse(BaseResponse):
    """Response model for deal routes endpoint.

    Attributes:
        rutas_terrestres: List of available terrestrial routes.
        rutas_maritimas: List of available maritime routes.
        total_rutas_terrestres: Total number of terrestrial routes.
        total_rutas_maritimas: Total number of maritime routes.
    """
    rutas_terrestres: List[RutaItem] = Field(
        default_factory=list,
        description="List of available terrestrial routes"
    )
    rutas_maritimas: List[RutaItem] = Field(
        default_factory=list,
        description="List of available maritime routes"
    )
    total_rutas_terrestres: int = Field(
        0,
        description="Total number of terrestrial routes"
    )
    total_rutas_maritimas: int = Field(
        0,
        description="Total number of maritime routes"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "rutas_terrestres": [
                    {
                        "id": "123456789",
                        "referencia": "D20DR__TIBA__NA__694__VAL__NA__NA",
                        "tipo_contenedor": "D20DR",
                        "transitario": "TIBA",
                        "transitario_id": "987654321",
                        "naviera": "NA",
                        "lugar_carga": "694",
                        "puerto_salida": "VAL",
                        "puerto_destino": "NA",
                        "localidad_destino": "NA",
                        "importe": 850.00,
                        "fecha_caducidad": "2025-12-31",
                        "notas": "Ruta terrestre preferente",
                        "tipo_tramo": "terrestre"
                    }
                ],
                "rutas_maritimas": [
                    {
                        "id": "987654321",
                        "referencia": "D20DR__MERTRAMAR__MAERSK__NA__VAL__HAMBURG__NA",
                        "tipo_contenedor": "D20DR",
                        "transitario": "MERTRAMAR",
                        "transitario_id": "111222333",
                        "naviera": "MAERSK",
                        "lugar_carga": "NA",
                        "puerto_salida": "VAL",
                        "puerto_destino": "HAMBURG",
                        "localidad_destino": "NA",
                        "importe": 2500.00,
                        "fecha_caducidad": "2025-12-31",
                        "notas": None,
                        "tipo_tramo": "maritima"
                    }
                ],
                "total_rutas_terrestres": 1,
                "total_rutas_maritimas": 1
            }
        }

class DealTransitUpdateRequest(BaseModel):
    """Request model for updating transit data in a deal.

    Attributes:
        puerto_salida: Departure port code.
        puerto_destino: Destination port code.
        localidad_destino: Destination locality code.
        naviera: Shipping line/carrier.
        dias_libres: Free days at destination port.
        transitario: Freight forwarder HubSpot ID.
        precio_ruta_hasta_puerto_de_salida: Price for route to departure port (EUR).
        precio_ruta_hasta_puerto_destino: Price for route to destination port (EUR).
        fecha_caducidad_flete_salida: Expiry date for terrestrial route (timestamp milliseconds).
        fecha_caducidad_flete_destino: Expiry date for maritime route (timestamp milliseconds).
        almacen: Warehouse code for order items.
        tipo_iva_fletes: IVA type for freight line items.
    """
    puerto_salida: Optional[str] = Field(None, description="Departure port code")
    puerto_destino: Optional[str] = Field(None, description="Destination port code")
    localidad_destino: Optional[str] = Field(None, description="Destination locality code")
    naviera: Optional[str] = Field(None, description="Shipping line/carrier")
    dias_libres: Optional[int] = Field(None, description="Free days at destination port", ge=0)
    transitario: Optional[str] = Field(None, description="Freight forwarder HubSpot ID")
    precio_ruta_hasta_puerto_de_salida: Optional[float] = Field(None, description="Price for route to departure port (EUR)", ge=0)
    precio_ruta_hasta_puerto_destino: Optional[float] = Field(None, description="Price for route to destination port (EUR)", ge=0)
    fecha_caducidad_flete_salida: Optional[int] = Field(None, description="Expiry date for terrestrial route (timestamp milliseconds)")
    fecha_caducidad_flete_destino: Optional[int] = Field(None, description="Expiry date for maritime route (timestamp milliseconds)")
    cotizacion_rapida: Optional[str] = Field(None, description="Quick quote flag (true/false as string)")
    transitario_name: Optional[str] = Field(None, description="Freight forwarder name (for response purposes only)")
    almacen: Optional[str] = Field(None, description="Warehouse code for order items")
    tipo_iva_fletes: Optional[str] = Field(None, description="IVA repercutido type for freight line items")
    tipo_iva_fletes_soportado: Optional[str] = Field(None, description="IVA soportado type for freight line items")
    iva_fletes_defaults: Optional[dict] = Field(None, description="Product-resolved IVA defaults for fletes: {repercutido, soportado}")
    transitario_terrestre: Optional[str] = Field(None, description="Terrestrial freight forwarder cardcode (dual freight mode; empty string clears)")
    transitario_terrestre_name: Optional[str] = Field(None, description="Terrestrial freight forwarder name (for response purposes only)")
    tipo_iva_fletes_terrestre: Optional[str] = Field(None, description="IVA repercutido type for terrestrial freight (empty falls back to tipo_iva_fletes)")
    tipo_iva_fletes_terrestre_soportado: Optional[str] = Field(None, description="IVA soportado type for terrestrial freight")
    iva_fletes_terrestre_defaults: Optional[dict] = Field(None, description="Product-resolved IVA defaults for terrestrial freight: {repercutido, soportado}")
    flete_doble_disponible: Optional[bool] = Field(None, description="Whether dual freight (terrestre + marítimo) is available for this deal (camión)")
    ruta_terrestre_id: Optional[str] = Field(None, description="Selected terrestrial Ruta custom object id to associate with the deal")
    ruta_maritima_id: Optional[str] = Field(None, description="Selected maritime Ruta custom object id to associate with the deal")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "puerto_salida": "VAL",
                "puerto_destino": "HAMBURG",
                "localidad_destino": "Hamburg",
                "naviera": "MAERSK",
                "dias_libres": 7,
                "transitario": "123456789",
                "transitario_name": "Transitarios S.L.",
                "precio_ruta_hasta_puerto_de_salida": 850.00,
                "precio_ruta_hasta_puerto_destino": 2500.00,
                "fecha_caducidad": 1735689600000
            }
        }

class DealTransitDataResponse(BaseModel):
    """Response model for deal transit data.

    Attributes:
        success: Whether the request was successful.
        error: Error message if any.
        data: Transit data.
    """
    success: bool = Field(..., description="Whether the request was successful")
    error: Optional[str] = Field(None, description="Error message if any")
    data: Optional[DealTransitUpdateRequest] = Field(None, description="Transit data")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "data": {
                    "puerto_salida": "VAL",
                    "puerto_destino": "HAMBURG",
                    "localidad_destino": "Hamburg",
                    "naviera": "MAERSK",
                    "dias_libres": 7,
                    "transitario": "Transitarios S.L.",
                    "precio_ruta_hasta_puerto_de_salida": 850.00,
                    "precio_ruta_hasta_puerto_destino": 2500.00,
                    "fecha_caducidad": 1735689600000
                }
            }
        }

class NegociacionArticleItem(BaseModel):
    """Individual article negotiation data for a unique line item.

    Attributes:
        line_item_id: HubSpot line item ID (unique identifier for article in container).
        price: Negotiated price per unit (price charged to client per box).
        margen_unitario: Effective unit margin (0-1 range, where 1 = 100%).
        tipo_iva_repercutido: VAT type code to be applied.
    """
    line_item_id: str = Field(..., description="HubSpot line item ID")
    # price 0 y margen negativo permitidos: línea promocional / regalo (descuento 100%).
    price: float = Field(..., description="Negotiated price per unit (0 = línea promocional)", ge=0)
    margen_unitario: float = Field(..., description="Effective unit margin (negativo permitido para descuento/promoción; 1 = 100%)", le=1)
    tipo_iva_repercutido: str = Field(..., description="VAT type code")
    tipo_iva_soportado: Optional[str] = Field(None, description="Input VAT type code")
    # Ancla del precio del tipo de venta: el FINAL por caja tecleado por el comercial
    # para el incoterm vigente. Al recalcular se mantiene fijo y se derivan price(EXW)
    # y margen. incoterm_precio_negociado guarda con qué tipo de venta se fijó, para
    # re-derivar si el deal cambia de incoterm. Opcionales por compat con históricos.
    precio_negociado_tipo_venta: Optional[float] = Field(None, description="Anchored per-box price of the selected incoterm (0 = promo)", ge=0)
    incoterm_precio_negociado: Optional[str] = Field(None, description="tipo_de_venta the anchored price was set under")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "line_item_id": "987654321",
                "price": 12.50,
                "margen_unitario": 0.15,
                "tipo_iva_repercutido": "A21"
            }
        }

class UpdateNegociacionRequest(BaseModel):
    """Request model for updating negotiation data for unique line items.

    Attributes:
        articulos: List of unique line items with their negotiated margins and VAT types.
    """
    articulos: List[NegociacionArticleItem] = Field(..., description="List of unique line items with negotiation data")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "articulos": [
                    {
                        "line_item_id": "987654321",
                        "price": 12.50,
                        "margen_unitario": 0.15,
                        "tipo_iva_repercutido": "A21"
                    },
                    {
                        "line_item_id": "123456789",
                        "price": 10.80,
                        "margen_unitario": 0.12,
                        "tipo_iva_repercutido": "A10"
                    }
                ]
            }
        }

# Textos Cotizacion Models (HubDB)
class TextosCotizacionRequest(BaseModel):
    """Request model for querying HubDB textos_cotizaciones table.

    Attributes:
        idioma: Language code (es, en, fr).
        claves: List of text template keys to retrieve.
        grupo: Optional group filter.
        subgrupo: Optional subgroup filter.
    """
    idioma: str = Field(..., description="Language code (es, en, fr)")
    claves: List[str] = Field(..., description="List of text template keys", min_length=1)
    grupo: Optional[str] = Field(None, description="Optional group filter")
    subgrupo: Optional[str] = Field(None, description="Optional subgroup filter")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "idioma": "es",
                "claves": ["oferta_base", "pago_30_70", "banco_la_caixa", "cotizacion_rapida"],
                "grupo": None,
                "subgrupo": None
            }
        }

class TextoCotizacionItem(BaseModel):
    """Individual text item from HubDB textos_cotizaciones table.

    Attributes:
        grupo: Group category (oferta, pagos, bancos, cotizacion_rapida).
        subgrupo: Subgroup category.
        idioma: Language code.
        clave: Text template key.
        texto: Rich text content.
    """
    grupo: str = Field(..., description="Group category")
    subgrupo: str = Field(..., description="Subgroup category")
    idioma: str = Field(..., description="Language code")
    clave: str = Field(..., description="Text template key")
    texto: str = Field(..., description="Rich text content")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "grupo": "pagos",
                "subgrupo": "30_70",
                "idioma": "es",
                "clave": "pago_30_70",
                "texto": "TÉRMINOS DE PAGO.- 30% al confirmar el pedido, 70% antes del envío."
            }
        }

class TextosCotizacionResponse(BaseResponse):
    """Response model for textos cotizacion endpoint.

    Attributes:
        data: Dictionary mapping text keys to their full data.
    """
    data: Optional[dict[str, TextoCotizacionItem]] = Field(
        None,
        description="Dictionary of text items keyed by clave"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "data": {
                    "oferta_base": {
                        "grupo": "oferta",
                        "subgrupo": "condiciones",
                        "idioma": "es",
                        "clave": "oferta_base",
                        "texto": "Estimado cliente, ..."
                    },
                    "pago_30_70": {
                        "grupo": "pagos",
                        "subgrupo": "30_70",
                        "idioma": "es",
                        "clave": "pago_30_70",
                        "texto": "TÉRMINOS DE PAGO.- 30% ..."
                    }
                }
            }
        }

# Tipo IVA Models (HubDB)
class TipoIVAFilter(BaseModel):
    """Single filter entry for querying HubDB table tipo_de_iva_soportado.

    Each filter represents one combination of category and residency type
    that must match a row in the HubDB table.

    Attributes:
        categoria: IVA category type (repercutido, soportado).
        tipo_residencia: Residency type (NACIONAL, EXTRANJERO, EUROPA).
    """
    categoria: str = Field(
        ...,
        description="IVA category type ('repercutido' or 'soportado')"
    )
    tipo_residencia: str = Field(
        ...,
        description="Residency type ('NACIONAL', 'EXTRANJERO', 'EUROPA')"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "categoria": "soportado",
                "tipo_residencia": "NACIONAL"
            }
        }

class TipoIVARequest(BaseModel):
    """Request model for querying HubDB tipo_de_iva_soportado table.

    This request allows querying multiple IVA configurations at once.
    The endpoint will return all HubDB rows that match ANY of the provided filters.

    Attributes:
        filtros: List of filter objects containing categoria + tipo_residencia.
    """
    filtros: List[TipoIVAFilter] = Field(
        ...,
        description="List of filters combining categoria and tipo_residencia",
        min_length=1
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "filtros": [
                    {"categoria": "soportado", "tipo_residencia": "NACIONAL"}                
                ]
            }
        }

class TipoIVAItem(BaseModel):
    """Represents one row of the HubDB table tipo_de_iva_soportado.

    Attributes:
        code: Internal code of the IVA configuration.
        name: Human-friendly name of the IVA type.
        porcentaje_iva: IVA tax percentage.
        porcentaje_recargo: Additional recargo equivalencia percentage.
        categoria: IVA category (taken from the select-value name).
        tipo_residencia: Residency type (taken from the select-value name).
    """
    code: str = Field(..., description="Internal code for IVA configuration")
    name: str = Field(..., description="Name of the IVA/recargo type")
    porcentaje_iva: float = Field(..., description="IVA percentage applied")
    porcentaje_recargo: float = Field(..., description="Recargo de equivalencia percentage")
    categoria: str = Field(..., description="IVA category (select value name)")
    tipo_residencia: str = Field(..., description="Residency type (select value name)")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "code": "IVA21_REDEQ5.2",
                "name": "IVA 21% + Recargo 5.2%",
                "porcentaje_iva": 21.0,
                "porcentaje_recargo": 5.2,
                "categoria": "soportado",
                "tipo_residencia": "NACIONAL"
            }
        }

class TipoIVAResponse(BaseResponse):
    """Response model for tipo-iva HubDB endpoint.

    Attributes:
        data: List of IVA configuration rows that matched the input filters.
    """
    data: Optional[List[TipoIVAItem]] = Field(
        None,
        description="List of IVA/recargo matching configurations from HubDB"
    )

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "data": [
                    {
                        "code": "IVA21_REDEQ5.2",
                        "name": "IVA 21% + Recargo 5.2%",
                        "porcentaje_iva": 21.0,
                        "porcentaje_recargo": 5.2,
                        "categoria": "soportado",
                        "tipo_residencia": "NACIONAL"
                    },
                    {
                        "code": "IVA0",
                        "name": "IVA Exento",
                        "porcentaje_iva": 0.0,
                        "porcentaje_recargo": 0.0,
                        "categoria": "repercutido",
                        "tipo_residencia": "EUROPA"
                    }
                ]
            }
        }

# Publish Quote Models

class QuoteHeader(BaseModel):
    """Header information for the quote."""
    offerNumber: str
    date: str
    destinationPort: str
    vessel: str
    terms: str
    currency: str
    container: str
    sealNumber: Optional[str] = None
    etd: Optional[str] = None
    eta: Optional[str] = None

class QuoteEmpresa(BaseModel):
    """Company information."""
    nombre: str
    direccion: str
    iniciales: str

class QuoteCliente(BaseModel):
    """Client information."""
    nombreEmpresa: str
    pais: str
    tipoCliente: str
    tipoVenta: str
    tipoContenedor: str
    proyecto: str
    idioma: str
    margen: float
    tipoResidente: Optional[str] = None
    cif: Optional[str] = None
    paisOperaciones: Optional[str] = None
    fechaCaducidadCotizacion: Optional[str] = None

class QuoteRuta(BaseModel):
    """Route and logistics information."""
    lugarCarga: str
    puertoSalida: str
    puertoDestino: str
    localidadDestino: str
    transitario: str
    transitarioCardcode: str
    naviera: str
    almacen: str
    diasLibres: int
    precioOrigen: float
    precioDestino: float
    tipoIVAFletes: Optional[str] = None
    tipoIVAFletesSoportado: Optional[str] = None
    transitarioTerrestre: Optional[str] = None
    transitarioTerrestreCardcode: Optional[str] = None
    tipoIVAFleteTerrestre: Optional[str] = None
    tipoIVAFleteTerrestreSoportado: Optional[str] = None

class QuoteLineItem(BaseModel):
    """Line item in the quote."""
    type: str  # servicio, contenedor, articulo, flete
    code: str
    item: str
    colis: str
    weight: str
    qty: float
    unit: str
    price: Union[float, str]
    total: float
    margen_unitario: Optional[float] = None
    tipo_iva_repercutido: Optional[str] = None
    tipo_iva_soportado: Optional[str] = None
    # From product: use these so quote line items match the associated product (do not override with inferred values)
    tipo_articulo: Optional[str] = None
    hs_product_type: Optional[str] = None
    numero_container: Optional[str] = None
    peso_neto: Optional[str] = None
    name_en: Optional[str] = None
    name_fr: Optional[str] = None

class QuotePrecios(BaseModel):
    """Pricing breakdown by Incoterm."""
    fobExpenses: float
    totalFOB: float
    seafreightRate: float
    totalCFR: float
    insurance: float
    totalCIF: float
    totalEXW: float
    totalDAP_TERRESTRE: float
    totalDAP_MARITIMO: float

class QuoteEstadisticas(BaseModel):
    """Quote statistics and totals."""
    totalColis: int
    netWeightKg: float
    grossWeightKg: float
    containerType: str
    filledUpTo: int
    numContainers: int
    totalEuros: float

class QuoteTextos(BaseModel):
    """Legal and commercial texts."""
    textoBaseOferta: str
    textoBanco: str
    textoFormaPago: str
    textoAdicionalMetodoPago: Optional[str] = None

class QuoteIntermediario(BaseModel):
    """Intermediary/broker information."""
    nombre: str
    codigo: str
    tipoComision: str
    importeComision: float
    repercutirACliente: str
    tipoIVAComision: Optional[str] = None
    tipoIVAComisionSoportado: Optional[str] = None

class QuoteDocumentacion(BaseModel):
    """Documentation settings."""
    idiomaCotizacion: str
    metodoPago: str
    bancoCobro: str
    documentosAGenerar: List[str]
    direccionFiscal: str
    consignee: Optional[str] = None
    shipper: Optional[str] = None
    notify: Optional[str] = None
    certificadoOrigenMercancia: Optional[str] = None
    textoEnBl: Optional[str] = None
    comentariosLogistica: Optional[str] = None
    comentariosTransitario: Optional[str] = None

class PublishQuoteRequest(BaseModel):
    """Request model for publishing a quote.

    Contains all data needed to generate and publish a quote in draft mode.
    This is the complete payload from the frontend.
    """
    header: QuoteHeader
    empresa: QuoteEmpresa
    cliente: QuoteCliente
    ruta: QuoteRuta
    lineItems: List[QuoteLineItem]
    precios: QuotePrecios
    estadisticas: QuoteEstadisticas
    textos: QuoteTextos
    intermediario: Optional[QuoteIntermediario] = None
    documentacion: QuoteDocumentacion

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "header": {
                    "offerNumber": "123456",
                    "date": "2025-12-02T10:16:30.212Z",
                    "destinationPort": "Abidjan, CI",
                    "vessel": "SIN ESPECIFICAR",
                    "terms": "CIF",
                    "currency": "€",
                    "container": "D20DR",
                    "sealNumber": None,
                    "etd": None,
                    "eta": None
                },
                "empresa": {
                    "nombre": "Proex Drinks Group S.A.",
                    "direccion": "Avda. Americo Vespucio 5, Edif. Cartuja, 41092 Sevilla, España",
                    "iniciales": "AD"
                },
                "cliente": {
                    "nombreEmpresa": "CLIENTE EJEMPLO S.A.",
                    "pais": "Andorra",
                    "tipoCliente": "exportacion",
                    "tipoVenta": "CIF",
                    "tipoContenedor": "D20DR",
                    "proyecto": "ANG GABELA",
                    "idioma": "ES",
                    "margen": 12,
                    "tipoResidente": "EXTRANJERO",
                    "cif": "VC16003832",
                    "paisOperaciones": "ESPAÑA"
                },
                "ruta": {
                    "lugarCarga": "Villatuerta",
                    "puertoSalida": "Valencia, ES",
                    "puertoDestino": "Abidjan, CI",
                    "localidadDestino": "Villatuerta",
                    "transitario": "CEVA FREIGHT",
                    "transitarioCardcode": "P0416",
                    "naviera": "SIN ESPECIFICAR",
                    "almacen": "FURGONETA 2",
                    "diasLibres": 10,
                    "precioOrigen": 525,
                    "precioDestino": 1200
                },
                "lineItems": [],
                "precios": {
                    "fobExpenses": 525.0,
                    "totalFOB": 49752.67,
                    "seafreightRate": 1200,
                    "totalCFR": 50952.67,
                    "insurance": 1019.05,
                    "totalCIF": 51971.73,
                    "totalEXW": 49227.67,
                    "totalDAP_TERRESTRE": 49752.67,
                    "totalDAP_MARITIMO": 53171.73
                },
                "estadisticas": {
                    "totalColis": 9224,
                    "netWeightKg": 76416,
                    "grossWeightKg": 76416,
                    "containerType": "D20DR",
                    "filledUpTo": 100,
                    "numContainers": 4,
                    "totalEuros": 51971.73
                },
                "textos": {
                    "textoBaseOferta": "Estimado cliente...",
                    "textoBanco": "Nombre del Banco...",
                    "textoFormaPago": "100% anticipado"
                },
                "documentacion": {
                    "idiomaCotizacion": "Español",
                    "metodoPago": "100% anticipado",
                    "bancoCobro": "La Caixa",
                    "documentosAGenerar": [],
                    "direccionFiscal": "Calle Tuétaro 10"
                }
            }
        }

class PublishQuoteResponse(BaseResponse):
    """Response model for quote publication.

    Returns success status and quote details if successful.
    """
    data: Optional[dict] = Field(None, description="Quote data including deal stage and line items")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None,
                "data": {
                    "deal_id": "123456",
                    "stage": "draft",
                    "line_items_updated": 2
                }
            }
        }

# CUSTOM OBJECTS: CONTENEDOR Y ELEMENTO

class ContenedorCreate(BaseModel):
    """Model for creating a Contenedor custom object.

    Attributes:
        nombre: Unique container name (format: dealId-numeroContainer, e.g., "46298478914-001")
        tipo_contenedor: Container type (camion_16p, d20dr, d40dr, etc.)
        numero_container: Sequential container number within the deal (1, 2, 3...)
        ocupacion: Container occupancy percentage (allowed range: 90-103%)
        peso_neto: Total net weight in kilograms
        peso_bruto: Total gross weight in kilograms
        container_config: Complete container configuration in JSON format
        total_cajas: Total boxes/units contained
        price: Configured container price
        line_item_id: Original line item ID (for dual tracking)
        numero_precinto: Security seal number
    """
    nombre: str = Field(..., description="Unique container name (format: dealId-numeroContainer)")
    tipo_contenedor: str = Field(..., description="Container type")
    numero_container: int = Field(..., description="Sequential container number")
    ocupacion: Optional[float] = Field(None, description="Occupancy percentage (90-103%)")
    peso_neto: Optional[float] = Field(None, description="Total net weight in kg")
    peso_bruto: Optional[float] = Field(None, description="Total gross weight in kg")
    container_config: Optional[str] = Field(None, description="Container configuration JSON")
    total_cajas: Optional[int] = Field(None, description="Total boxes/units")
    price: Optional[float] = Field(None, description="Container price")
    line_item_id: Optional[str] = Field(None, description="Line item ID for dual tracking")
    numero_precinto: Optional[str] = Field(None, description="Security seal number")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "nombre": "46298478914-001",
                "tipo_contenedor": "d40dr",
                "numero_container": 1,
                "ocupacion": 98.5,
                "peso_neto": 24000.0,
                "peso_bruto": 26000.0,
                "container_config": '{"articulos": [], "ocupacion": 98.5, "pesoNeto": 24000}',
                "total_cajas": 1200,
                "price": 0.0,
                "line_item_id": "123456789",
                "numero_precinto": "SEAL-001-2024"
            }
        }

class ContenedorUpdate(BaseModel):
    """Model for updating a Contenedor custom object.

    All fields are optional for partial updates.
    """
    nombre: Optional[str] = Field(None, description="Container name")
    tipo_contenedor: Optional[str] = Field(None, description="Container type")
    numero_container: Optional[int] = Field(None, description="Container number")
    ocupacion: Optional[float] = Field(None, description="Occupancy percentage")
    peso_neto: Optional[float] = Field(None, description="Net weight in kg")
    peso_bruto: Optional[float] = Field(None, description="Gross weight in kg")
    container_config: Optional[str] = Field(None, description="Container configuration JSON")
    total_cajas: Optional[int] = Field(None, description="Total boxes")
    price: Optional[float] = Field(None, description="Container price")
    numero_precinto: Optional[str] = Field(None, description="Security seal number")

class ContenedorResponse(BaseModel):
    """Response model for Contenedor operations.

    Attributes:
        id: HubSpot object ID
        properties: Container properties
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """
    id: str = Field(..., description="HubSpot object ID")
    properties: ContenedorCreate = Field(..., description="Container properties")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")

class ElementoCreate(BaseModel):
    """Model for creating an Elemento custom object.

    Attributes:
        nombre: Element name (format: "containerName - articleName")
        nombre_contenedor: Associated container name (e.g., "46298478914-001")
        tipo_articulo: Article type (fabricante, servicio, flete, intermediario, seguro)
        cantidad: Quantity (boxes for products, 1 for services)
        product_sku: Product SKU
        precio_unitario: Unit sale price
        precio_de_coste: Provider cost price
        precio_total: Total price (cantidad × precio_unitario)
        margen_unitario: Profit margin percentage (e.g., 12 for 12%)
        peso_neto_unitario: Unit net weight in kg
        peso_neto_total: Total net weight (cantidad × peso_neto_unitario)
        peso_bruto_unitario: Unit gross weight in kg
        peso_bruto_total: Total gross weight (cantidad × peso_bruto_unitario)
        cardcode: Provider code (SAP/ERP cardcode)
        nombre_proveedor: Provider name
        tipo_iva_repercutido: Applied VAT type (r21, ei21, ex)
        codigo_proveedor: Product code according to provider
        line_item_id: Original line item ID (for dual tracking)
    """
    nombre: str = Field(..., description="Element name")
    nombre_contenedor: Optional[str] = Field(None, description="Container name")
    tipo_articulo: str = Field(..., description="Article type")
    cantidad: Optional[float] = Field(None, description="Quantity")
    product_sku: Optional[str] = Field(None, description="Product SKU")
    precio_unitario: Optional[float] = Field(None, description="Unit price")
    precio_de_coste: Optional[float] = Field(None, description="Cost price")
    precio_total: Optional[float] = Field(None, description="Total price")
    margen_unitario: Optional[float] = Field(None, description="Unit margin %")
    peso_neto_unitario: Optional[float] = Field(None, description="Unit net weight kg")
    peso_neto_total: Optional[float] = Field(None, description="Total net weight kg")
    peso_bruto_unitario: Optional[float] = Field(None, description="Unit gross weight kg")
    peso_bruto_total: Optional[float] = Field(None, description="Total gross weight kg")
    cardcode: Optional[str] = Field(None, description="Provider cardcode")
    nombre_proveedor: Optional[str] = Field(None, description="Provider name")
    tipo_iva_repercutido: Optional[str] = Field(None, description="VAT type")
    codigo_proveedor: Optional[str] = Field(None, description="Provider product code")
    line_item_id: Optional[str] = Field(None, description="Line item ID for dual tracking")
    nombre_es: Optional[str] = Field(None, description="Spanish name (clean, from product description)")
    nombre_en: Optional[str] = Field(None, description="Name in English")
    nombre_fr: Optional[str] = Field(None, description="Name in French")
    formato: Optional[str] = Field(None, description="Format")
    marca: Optional[str] = Field(None, description="Brand")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "nombre": "46298478914-001 - Vino Tinto Reserva",
                "nombre_contenedor": "46298478914-001",
                "tipo_articulo": "fabricante",
                "cantidad": 1200,
                "product_sku": "VTR-001",
                "precio_unitario": 8.50,
                "precio_de_coste": 6.50,
                "precio_total": 10200.00,
                "margen_unitario": 30.77,
                "peso_neto_unitario": 8.4,
                "peso_neto_total": 10080.0,
                "peso_bruto_unitario": 9.2,
                "peso_bruto_total": 11040.0,
                "cardcode": "PROV001",
                "nombre_proveedor": "Bodegas Example S.L.",
                "tipo_iva_repercutido": "r21",
                "codigo_proveedor": "VTR-PROV-001",
                "line_item_id": "987654321"
            }
        }

class ElementoUpdate(BaseModel):
    """Model for updating an Elemento custom object.

    All fields are optional for partial updates.
    """
    nombre: Optional[str] = Field(None, description="Element name")
    nombre_contenedor: Optional[str] = Field(None, description="Container name")
    tipo_articulo: Optional[str] = Field(None, description="Article type")
    cantidad: Optional[float] = Field(None, description="Quantity")
    product_sku: Optional[str] = Field(None, description="Product SKU")
    precio_unitario: Optional[float] = Field(None, description="Unit price")
    precio_de_coste: Optional[float] = Field(None, description="Cost price")
    precio_total: Optional[float] = Field(None, description="Total price")
    margen_unitario: Optional[float] = Field(None, description="Unit margin %")
    peso_neto_unitario: Optional[float] = Field(None, description="Unit net weight kg")
    peso_neto_total: Optional[float] = Field(None, description="Total net weight kg")
    peso_bruto_unitario: Optional[float] = Field(None, description="Unit gross weight kg")
    peso_bruto_total: Optional[float] = Field(None, description="Total gross weight kg")
    cardcode: Optional[str] = Field(None, description="Provider cardcode")
    nombre_proveedor: Optional[str] = Field(None, description="Provider name")
    tipo_iva_repercutido: Optional[str] = Field(None, description="VAT type")
    codigo_proveedor: Optional[str] = Field(None, description="Provider product code")
    nombre_es: Optional[str] = Field(None, description="Spanish name (clean, from product description)")
    nombre_en: Optional[str] = Field(None, description="Name in English")
    nombre_fr: Optional[str] = Field(None, description="Name in French")
    formato: Optional[str] = Field(None, description="Format")
    marca: Optional[str] = Field(None, description="Brand")

class ElementoResponse(BaseModel):
    """Response model for Elemento operations.

    Attributes:
        id: HubSpot object ID
        properties: Element properties
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """
    id: str = Field(..., description="HubSpot object ID")
    properties: ElementoCreate = Field(..., description="Element properties")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")
