#!/usr/bin/env python3
"""
Script para listar todos los objetos personalizados existentes en HubSpot.

Útil para:
- Verificar si los objetos ya existen antes de crearlos
- Obtener los Object Type IDs de objetos existentes
- Revisar la estructura de objetos personalizados

Uso:
    python scripts/list_custom_objects.py
"""

import os
import sys
import logging
from pathlib import Path

# Añadir el directorio raíz al path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from hubspot import HubSpot
from dotenv import load_dotenv

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
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


def list_custom_objects(client: HubSpot):
    """Listar todos los objetos personalizados."""
    logger.info("=" * 80)
    logger.info("OBJETOS PERSONALIZADOS EN HUBSPOT")
    logger.info("=" * 80)
    logger.info("")

    try:
        # Obtener todos los schemas
        schemas = client.crm.schemas.core_api.get_all()

        if not schemas.results:
            logger.info("❌ No se encontraron objetos personalizados")
            return

        logger.info(f"✅ Encontrados {len(schemas.results)} objetos personalizados:")
        logger.info("")

        for schema in schemas.results:
            # Acceder a labels como atributos, no como diccionario
            singular = getattr(schema.labels, 'singular', schema.name)
            plural = getattr(schema.labels, 'plural', schema.name + 's')

            logger.info(f"📦 {singular} ({plural})")
            logger.info(f"   Nombre interno: {schema.name}")
            logger.info(f"   Object Type ID: {schema.object_type_id}")
            logger.info(f"   Descripción: {schema.description or 'N/A'}")
            logger.info(f"   Propiedades: {len(schema.properties) if schema.properties else 0}")
            logger.info(f"   Propiedad principal: {schema.primary_display_property}")

            if hasattr(schema, 'associated_objects') and schema.associated_objects:
                logger.info(f"   Asociado a: {', '.join(schema.associated_objects)}")

            logger.info("")

    except Exception as e:
        logger.error(f"❌ Error listando objetos: {e}")
        raise


def check_specific_objects(client: HubSpot):
    """Verificar si los objetos contenedor y elemento ya existen."""
    logger.info("=" * 80)
    logger.info("VERIFICACIÓN DE OBJETOS CONTENEDOR Y ELEMENTO")
    logger.info("=" * 80)
    logger.info("")

    try:
        schemas = client.crm.schemas.core_api.get_all()

        contenedor_exists = False
        elemento_exists = False

        for schema in schemas.results:
            if schema.name == "contenedor":
                contenedor_exists = True
                logger.info(f"✅ Objeto CONTENEDOR encontrado")
                logger.info(f"   Object Type ID: {schema.object_type_id}")
                logger.info("")

            if schema.name == "elemento":
                elemento_exists = True
                logger.info(f"✅ Objeto ELEMENTO encontrado")
                logger.info(f"   Object Type ID: {schema.object_type_id}")
                logger.info("")

        if not contenedor_exists:
            logger.info("❌ Objeto CONTENEDOR NO encontrado")
            logger.info("")

        if not elemento_exists:
            logger.info("❌ Objeto ELEMENTO NO encontrado")
            logger.info("")

        if not contenedor_exists or not elemento_exists:
            logger.info("💡 Ejecuta: python scripts/create_custom_objects.py")
            logger.info("")

    except Exception as e:
        logger.error(f"❌ Error verificando objetos: {e}")
        raise


def main():
    """Función principal."""
    try:
        client = get_hubspot_client()

        # Listar todos los objetos personalizados
        list_custom_objects(client)

        # Verificar objetos específicos
        check_specific_objects(client)

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
