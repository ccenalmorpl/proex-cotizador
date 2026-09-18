#!/usr/bin/env python3
"""
Script para crear objetos personalizados CONTENEDOR y ELEMENTO en HubSpot.

Este script:
1. Crea el objeto personalizado CONTENEDOR con todas sus propiedades
2. Crea el objeto personalizado ELEMENTO con todas sus propiedades
3. Crea las asociaciones necesarias entre objetos

Uso:
    python scripts/create_custom_objects.py

Nota: Requiere el token de acceso configurado en .env (HUBSPOT_ACCESS_TOKEN)
"""

import os
import sys
import logging
from pathlib import Path

# Añadir el directorio raíz al path para importar módulos
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from hubspot import HubSpot
from hubspot.crm.schemas.models import ObjectSchemaEgg, ObjectTypeDefinition, ObjectTypeDefinitionLabels
from dotenv import load_dotenv

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

def get_hubspot_client() -> HubSpot:
    """Obtener cliente de HubSpot autenticado."""
    access_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    if not access_token:
        raise ValueError("HUBSPOT_ACCESS_TOKEN no encontrado en variables de entorno")
    return HubSpot(access_token=access_token)

def create_contenedor_schema(client: HubSpot) -> str:
    """
    Crear el esquema del objeto personalizado CONTENEDOR.

    Returns:
        Object type ID del objeto creado (e.g., "2-12345678")
    """
    logger.info("=" * 80)
    logger.info("Creando objeto personalizado: CONTENEDOR")
    logger.info("=" * 80)

    # Definir propiedades del objeto
    properties = [
        {
            "name": "nombre",
            "label": "Nombre del Contenedor",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre único del contenedor (formato: dealId-numeroContainer, ej: 46298478914-001)",
            "hasUniqueValue": True
        },
        {
            "name": "tipo_contenedor",
            "label": "Tipo de Contenedor",
            "type": "enumeration",
            "fieldType": "select",
            "description": "Tipo de contenedor o medio de transporte",
            "options": [
                {"label": "D20DR", "value": "d20dr"},
                {"label": "D40DR", "value": "d40dr"},
                {"label": "TRAILER 80", "value": "trailer_80"}
            ]
        },
        {
            "name": "numero_container",
            "label": "Número de Contenedor",
            "type": "number",
            "fieldType": "number",
            "description": "Número secuencial del contenedor dentro del negocio (1, 2, 3...)"
        },
        {
            "name": "ocupacion",
            "label": "Ocupación (%)",
            "type": "number",
            "fieldType": "number",
            "description": "Porcentaje de ocupación del contenedor calculado (rango permitido: 97-103%)"
        },
        {
            "name": "peso_neto",
            "label": "Peso Neto (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso neto total del contenedor en kilogramos"
        },
        {
            "name": "peso_bruto",
            "label": "Peso Bruto (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso bruto total del contenedor en kg"
        },
        {
            "name": "container_config",
            "label": "Configuración JSON",
            "type": "string",
            "fieldType": "textarea",
            "description": "Configuración completa del contenedor en formato JSON (artículos, ocupación, peso)"
        },
        {
            "name": "total_cajas",
            "label": "Total de Cajas",
            "type": "number",
            "fieldType": "number",
            "description": "Suma total de cajas/unidades contenidas (calculado de container_config.articulos)"
        },
        {
            "name": "price",
            "label": "Precio del Contenedor",
            "type": "number",
            "fieldType": "number",
            "description": "Precio del contenedor ya configurado"
        },
        {
            "name": "line_item_id",
            "label": "ID del Line Item",
            "type": "string",
            "fieldType": "text",
            "description": "ID del line item original (para dual tracking con sistema actual)"
        },
        {
            "name": "fecha_creacion",
            "label": "Fecha de Creación",
            "type": "datetime",
            "fieldType": "date",
            "description": "Fecha y hora de creación del contenedor"
        },
        {
            "name": "numero_precinto",
            "label": "Número de Precinto",
            "type": "string",
            "fieldType": "text",
            "description": "Número del precinto de seguridad del container"
        },
        {
            "name": "documentos_a_generar",
            "label": "Documentos a Generar",
            "type": "string",
            "fieldType": "text",
            "description": "Documentos a generar para este contenedor (separados por punto y coma, ej: bl;packing_list)"
        }
    ]

    # Crear el objeto
    labels = ObjectTypeDefinitionLabels(
        singular="Contenedor",
        plural="Contenedores"
    )

    schema = ObjectSchemaEgg(
        name="contenedor",
        labels=labels,
        description="Representa los contenedores y camiones que componen un negocio. Permite análisis detallado y reporting sobre el centro del negocio.",
        primary_display_property="nombre",
        secondary_display_properties=["tipo_contenedor", "numero_container"],
        searchable_properties=["nombre", "numero_container", "tipo_contenedor"],
        required_properties=["nombre", "tipo_contenedor", "numero_container"],
        properties=properties,
        associated_objects=["DEAL"]
    )

    try:
        result = client.crm.schemas.core_api.create(object_schema_egg=schema)
        object_type_id = result.object_type_id
        logger.info(f"✅ Objeto CONTENEDOR creado exitosamente")
        logger.info(f"   Object Type ID: {object_type_id}")
        logger.info(f"   Nombre: {result.name}")
        logger.info(f"   Propiedades: {len(result.properties)}")
        return object_type_id
    except Exception as e:
        logger.error(f"❌ Error creando objeto CONTENEDOR: {e}")
        raise

def create_elemento_schema(client: HubSpot, contenedor_object_type_id: str) -> str:
    """
    Crear el esquema del objeto personalizado ELEMENTO.

    Args:
        contenedor_object_type_id: ID del objeto Contenedor para asociación

    Returns:
        Object type ID del objeto creado (e.g., "2-12345679")
    """
    logger.info("=" * 80)
    logger.info("Creando objeto personalizado: ELEMENTO")
    logger.info("=" * 80)

    # Definir propiedades del objeto
    properties = [
        {
            "name": "nombre",
            "label": "Nombre del Elemento",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre del producto, servicio o elemento (formato: contenedorName - articuloName)"
        },
        {
            "name": "nombre_contenedor",
            "label": "Nombre del Contenedor",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre del contenedor asociado (ej: 46298478914-001)"
        },
        {
            "name": "tipo_articulo",
            "label": "Tipo de Artículo",
            "type": "enumeration",
            "fieldType": "select",
            "description": "Tipo de elemento (fabricante, servicio, flete, intermediario, seguro)",
            "options": [
                {"label": "Fabricante (Producto)", "value": "fabricante"},
                {"label": "Servicio", "value": "servicio"},
                {"label": "Flete", "value": "flete"},
                {"label": "Intermediario/Comisión", "value": "intermediario"},
                {"label": "Seguro", "value": "seguro"}
            ]
        },
        {
            "name": "cantidad",
            "label": "Cantidad",
            "type": "number",
            "fieldType": "number",
            "description": "Cantidad de unidades (cajas para productos, 1 para servicios)"
        },
        {
            "name": "product_sku",
            "label": "SKU del Producto",
            "type": "string",
            "fieldType": "text",
            "description": "SKU del producto en HubSpot"
        },
        {
            "name": "precio_unitario",
            "label": "Precio Unitario (€)",
            "type": "number",
            "fieldType": "number",
            "description": "Precio unitario final de venta del elemento (price)"
        },
        {
            "name": "precio_de_coste",
            "label": "Precio de Coste (€)",
            "type": "number",
            "fieldType": "number",
            "description": "Precio de coste del proveedor"
        },
        {
            "name": "precio_total",
            "label": "Precio Total (€)",
            "type": "number",
            "fieldType": "number",
            "description": "Precio total calculado (cantidad × precio_unitario)"
        },
        {
            "name": "margen_unitario",
            "label": "Margen Unitario (%)",
            "type": "number",
            "fieldType": "number",
            "description": "Margen de beneficio en porcentaje (ej: 12 para 12%)"
        },
        {
            "name": "peso_neto_unitario",
            "label": "Peso Neto Unitario (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso neto por unidad en kilogramos"
        },
        {
            "name": "peso_neto_total",
            "label": "Peso Neto Total (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso neto total (cantidad × peso_neto_unitario)"
        },
        {
            "name": "peso_bruto_unitario",
            "label": "Peso Bruto Unitario (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso bruto por unidad en kilogramos"
        },
        {
            "name": "peso_bruto_total",
            "label": "Peso Bruto Total (kg)",
            "type": "number",
            "fieldType": "number",
            "description": "Peso bruto total (cantidad × peso_bruto_unitario)"
        },
        {
            "name": "cardcode",
            "label": "Cardcode Proveedor",
            "type": "string",
            "fieldType": "text",
            "description": "Código del proveedor (cardcode de SAP/ERP)"
        },
        {
            "name": "nombre_proveedor",
            "label": "Nombre del Proveedor",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre del proveedor asociado"
        },
        {
            "name": "tipo_iva_repercutido",
            "label": "Tipo IVA Repercutido",
            "type": "enumeration",
            "fieldType": "select",
            "description": "Tipo de IVA aplicado al elemento",
            "options": [
                {"label": "R21 - NACIONAL", "value": "r21"},
                {"label": "EI21 - EUROPA", "value": "ei21"},
                {"label": "EX - EXPORTACIONES", "value": "ex"}
            ]
        },
        {
            "name": "codigo_proveedor",
            "label": "Código Proveedor Producto",
            "type": "string",
            "fieldType": "text",
            "description": "Código del producto según el proveedor"
        },
        {
            "name": "line_item_id",
            "label": "ID del Line Item",
            "type": "string",
            "fieldType": "text",
            "description": "ID del line item original (para dual tracking con sistema actual)"
        },
        {
            "name": "nombre_en",
            "label": "Nombre (EN)",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre del artículo en inglés"
        },
        {
            "name": "nombre_fr",
            "label": "Nombre (FR)",
            "type": "string",
            "fieldType": "text",
            "description": "Nombre del artículo en francés"
        },
        {
            "name": "formato",
            "label": "Formato",
            "type": "string",
            "fieldType": "text",
            "description": "Formato del artículo"
        },
        {
            "name": "marca",
            "label": "Marca",
            "type": "string",
            "fieldType": "text",
            "description": "Marca del artículo"
        },
        {
            "name": "fecha_creacion",
            "label": "Fecha de Creación",
            "type": "datetime",
            "fieldType": "date",
            "description": "Fecha y hora de creación del elemento"
        }
    ]

    # Crear el objeto con asociaciones a Deal y Contenedor
    labels = ObjectTypeDefinitionLabels(
        singular="Elemento",
        plural="Elementos"
    )

    schema = ObjectSchemaEgg(
        name="elemento",
        labels=labels,
        description="Representa productos, servicios, fletes, comisiones y otros elementos que componen los contenedores de un negocio. Permite análisis detallado del contenido vendido.",
        primary_display_property="nombre",
        secondary_display_properties=["tipo_articulo", "cantidad"],
        searchable_properties=["nombre", "product_sku", "tipo_articulo", "cardcode", "nombre_contenedor"],
        required_properties=["nombre", "tipo_articulo"],
        properties=properties,
        associated_objects=["DEAL", contenedor_object_type_id]
    )

    try:
        result = client.crm.schemas.core_api.create(object_schema_egg=schema)
        object_type_id = result.object_type_id
        logger.info(f"✅ Objeto ELEMENTO creado exitosamente")
        logger.info(f"   Object Type ID: {object_type_id}")
        logger.info(f"   Nombre: {result.name}")
        logger.info(f"   Propiedades: {len(result.properties)}")
        logger.info(f"   Asociado a DEAL y CONTENEDOR ({contenedor_object_type_id})")
        return object_type_id
    except Exception as e:
        logger.error(f"❌ Error creando objeto ELEMENTO: {e}")
        raise

def main():
    """Función principal."""
    try:
        logger.info("🚀 Iniciando creación de objetos personalizados en HubSpot")
        logger.info("")

        # Obtener cliente de HubSpot
        client = get_hubspot_client()
        logger.info("✅ Cliente de HubSpot autenticado")
        logger.info("")

        # Crear objeto CONTENEDOR
        contenedor_id = create_contenedor_schema(client)
        logger.info("")

        # Crear objeto ELEMENTO (con asociación a Contenedor)
        elemento_id = create_elemento_schema(client, contenedor_id)
        logger.info("")

        # Resumen final
        logger.info("=" * 80)
        logger.info("🎉 OBJETOS PERSONALIZADOS CREADOS EXITOSAMENTE")
        logger.info("=" * 80)
        logger.info(f"")
        logger.info(f"📦 CONTENEDOR:")
        logger.info(f"   - Object Type ID: {contenedor_id}")
        logger.info(f"   - Nombre interno: contenedor")
        logger.info(f"   - URL: https://app.hubspot.com/contacts/{os.getenv('HUBSPOT_ACCOUNT_ID')}/objects/{contenedor_id}")
        logger.info(f"")
        logger.info(f"📋 ELEMENTO:")
        logger.info(f"   - Object Type ID: {elemento_id}")
        logger.info(f"   - Nombre interno: elemento")
        logger.info(f"   - URL: https://app.hubspot.com/contacts/{os.getenv('HUBSPOT_ACCOUNT_ID')}/objects/{elemento_id}")
        logger.info(f"")
        logger.info(f"🔗 Asociaciones creadas:")
        logger.info(f"   - Contenedor → Deal")
        logger.info(f"   - Elemento → Deal")
        logger.info(f"   - Elemento → Contenedor")
        logger.info("")
        logger.info("📝 PRÓXIMOS PASOS:")
        logger.info("   1. Actualizar constantes en app/services/custom_objects.py:")
        logger.info(f"      OBJECT_TYPE_CONTENEDOR = '{contenedor_id}'")
        logger.info(f"      OBJECT_TYPE_ELEMENTO = '{elemento_id}'")
        logger.info("   2. Implementar funciones CRUD en custom_objects.py")
        logger.info("   3. Integrar en endpoints de deals.py")
        logger.info("")

    except Exception as e:
        logger.error(f"❌ Error en el proceso: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
