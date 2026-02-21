"""
Constantes para objetos personalizados de HubSpot.

Este archivo contiene los Object Type IDs de los objetos personalizados
CONTENEDOR y ELEMENTO. Estos IDs se obtienen después de ejecutar el script
de creación de objetos.

IMPORTANTE: Actualizar estos valores después de ejecutar:
    python scripts/create_custom_objects.py
"""

# ============================================================================
# OBJECT TYPE IDS
# ============================================================================
# NOTA: Estos valores deben actualizarse con los IDs reales después de
# crear los objetos personalizados en HubSpot.

# Object Type ID del objeto personalizado CONTENEDOR
# Formato: "2-XXXXXXXX" donde XXXXXXXX es el ID único
# Actualizar después de ejecutar create_custom_objects.py
OBJECT_TYPE_CONTENEDOR = "2-54989406"

# Object Type ID del objeto personalizado ELEMENTO
# Formato: "2-YYYYYYYY" donde YYYYYYYY es el ID único
# Actualizar después de ejecutar create_custom_objects.py
OBJECT_TYPE_ELEMENTO = "2-54989407"


# ============================================================================
# NOMBRES INTERNOS DE OBJETOS
# ============================================================================

# Nombres internos de los objetos (para búsquedas y operaciones)
OBJECT_NAME_CONTENEDOR = "contenedor"
OBJECT_NAME_ELEMENTO = "elemento"


# ============================================================================
# TIPOS DE CONTENEDOR
# ============================================================================

TIPO_CONTENEDOR_OPTIONS = [
    "camion_16p",
    "d20dr",
    "d40dr",
    "d20w",
    "d40hd",
    "d45hd",
    "d45w",
    "palets",
    "r20rf",
    "r40rf",
    "trailer_80"
]

# Mapeo de valores a labels para display
TIPO_CONTENEDOR_LABELS = {
    "camion_16p": "CAMION 16P",
    "d20dr": "D20DR",
    "d40dr": "D40DR",
    "d20w": "D20W",
    "d40hd": "D40HD",
    "d45hd": "D45HD",
    "d45w": "D45W",
    "palets": "PALETS",
    "r20rf": "R20RF",
    "r40rf": "R40RF",
    "trailer_80": "TRAILER 80"
}


# ============================================================================
# TIPOS DE ARTÍCULO
# ============================================================================

TIPO_ARTICULO_OPTIONS = [
    "fabricante",
    "servicio",
    "flete",
    "intermediario",
    "seguro"
]

# Mapeo de valores a labels para display
TIPO_ARTICULO_LABELS = {
    "fabricante": "Fabricante (Producto)",
    "servicio": "Servicio",
    "flete": "Flete",
    "intermediario": "Intermediario/Comisión",
    "seguro": "Seguro"
}


# ============================================================================
# TIPOS DE IVA
# ============================================================================

TIPO_IVA_OPTIONS = [
    "r21",
    "ei21",
    "ex"
]

# Mapeo de valores a labels para display
TIPO_IVA_LABELS = {
    "r21": "R21 - NACIONAL",
    "ei21": "EI21 - EUROPA",
    "ex": "EX - EXPORTACIONES"
}


# ============================================================================
# ASOCIACIONES
# ============================================================================

# Nombres de asociaciones entre objetos
ASSOCIATION_CONTENEDOR_TO_DEAL = "contenedor"
ASSOCIATION_ELEMENTO_TO_CONTENEDOR = "elementos"
ASSOCIATION_ELEMENTO_TO_DEAL = "elementos_negocio"

# IDs de asociaciones personalizadas (USER_DEFINED)
# Estos IDs se obtienen después de crear las etiquetas de asociación en HubSpot
ASSOCIATION_TYPE_ID_DEAL_TO_CONTENEDOR = 489  # Deal → Contenedor
ASSOCIATION_TYPE_ID_DEAL_TO_ELEMENTO = 493     # Deal → Elemento
ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTENEDOR = 491  # Elemento → Contenedor

# IDs de asociaciones de Contenedor con Contacto y Empresa
# NOTA: Estos IDs deben actualizarse con los valores reales de HubSpot
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_CONTACT = 511  # Contenedor → Contact
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_COMPANY = 513  # Contenedor → Company

# IDs de asociaciones de Elemento con Contacto y Empresa
# NOTA: Estos IDs deben actualizarse con los valores reales de HubSpot
ASSOCIATION_TYPE_ID_ELEMENTO_TO_CONTACT = 521  # Elemento → Contact
ASSOCIATION_TYPE_ID_ELEMENTO_TO_COMPANY = 519  # Elemento → Company


# ============================================================================
# PROPIEDADES REQUERIDAS
# ============================================================================

# Propiedades obligatorias para crear un CONTENEDOR
CONTENEDOR_REQUIRED_PROPERTIES = [
    "nombre",
    "tipo_contenedor",
    "numero_container"
]

# Propiedades obligatorias para crear un ELEMENTO
ELEMENTO_REQUIRED_PROPERTIES = [
    "nombre",
    "tipo_articulo"
]


# ============================================================================
# VALIDACIONES
# ============================================================================

# Rango permitido de ocupación del contenedor (%)
OCUPACION_MIN = 97.0
OCUPACION_MAX = 103.0

# Formato del nombre de contenedor
# Ejemplo: "46298478914-001"
NOMBRE_CONTENEDOR_PATTERN = r"^\d+-\d{3}$"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def validate_object_type_ids():
    """
    Valida que los Object Type IDs hayan sido actualizados.

    Raises:
        ValueError: Si los IDs no han sido actualizados
    """
    if "2-54989406" in OBJECT_TYPE_CONTENEDOR:
        raise ValueError(
            "OBJECT_TYPE_CONTENEDOR no ha sido actualizado. "
            "Ejecuta: python scripts/create_custom_objects.py"
        )
    if "2-54989407" in OBJECT_TYPE_ELEMENTO:
        raise ValueError(
            "OBJECT_TYPE_ELEMENTO no ha sido actualizado. "
            "Ejecuta: python scripts/create_custom_objects.py"
        )


def get_tipo_contenedor_label(value: str) -> str:
    """
    Obtiene el label de un tipo de contenedor.

    Args:
        value: Valor del tipo de contenedor

    Returns:
        Label legible o el valor original si no se encuentra
    """
    return TIPO_CONTENEDOR_LABELS.get(value, value)


def get_tipo_articulo_label(value: str) -> str:
    """
    Obtiene el label de un tipo de artículo.

    Args:
        value: Valor del tipo de artículo

    Returns:
        Label legible o el valor original si no se encuentra
    """
    return TIPO_ARTICULO_LABELS.get(value, value)


def get_tipo_iva_label(value: str) -> str:
    """
    Obtiene el label de un tipo de IVA.

    Args:
        value: Valor del tipo de IVA

    Returns:
        Label legible o el valor original si no se encuentra
    """
    return TIPO_IVA_LABELS.get(value, value)
