# OBJECT TYPE IDS
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

# NOMBRES INTERNOS DE OBJETOS

# Nombres internos de los objetos (para búsquedas y operaciones)
OBJECT_NAME_CONTENEDOR = "contenedor"
OBJECT_NAME_ELEMENTO = "elemento"

# TIPOS DE CONTENEDOR

TIPO_CONTENEDOR_OPTIONS = [
    "d20dr",
    "d40dr",
    "trailer_80"
]

# Mapeo de valores a labels para display
TIPO_CONTENEDOR_LABELS = {
    "d20dr": "D20DR",
    "d40dr": "D40DR",
    "trailer_80": "TRAILER 80"
}

# TIPOS DE ARTÍCULO

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

# TIPOS DE IVA

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

# ASOCIACIONES

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

# Asociación Empresa↔Empresa con etiqueta "Proveedor-Cliente" (USER_DEFINED).
# 711 es una de las dos caras del par: al leer las asociadas filtramos por
# category USER_DEFINED, que captura el par en cualquier sentido y excluye la
# jerarquía matriz/filial estándar de HubSpot (HUBSPOT_DEFINED).
ASSOC_TYPE_ID_COMPANY_TO_COMPANY_PROVEEDOR_CLIENTE = 711
ASSOC_CATEGORY_USER_DEFINED = "USER_DEFINED"
# Dirección de entrega de la Empresa (Company).
COMPANY_PROP_ADDRESS_ENTREGA = "address_entrega"

# Objeto personalizado Proveedores
OBJECT_TYPE_PROVEEDOR = "2-43108800"

# Etiqueta "Transitario terrestre" (par USER_DEFINED análogo a 287/288 del
# transitario principal). Sentido Proveedor→Deal, el que usa el PUT de
# asociación de POST /transit (Deal→Proveedor es el 718, lo usa Pedidos).
PROVEEDOR_ASSOC_TYPE_ID_TRANSITARIO_TERRESTRE = 719  # type: int | None

# Asociaciones Contenedor → Proveedor (réplica de Negocio → Proveedor).
# Dirección contenedor→proveedor: el contenedor es el "from", igual que el resto
# de asociaciones del contenedor (deal 489, contacto 511, empresa 513).
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_FABRICA = 669
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_TRANSITARIO = 671
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_INTERMEDIARIO = 673
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SERVICIO = 675
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SEGURO = 677

# tipo_proveedor del objeto Proveedor → typeId de la asociación Contenedor→Proveedor.
# Mismo criterio que las asociaciones Negocio→Proveedor del cotizador: cualquier
# proveedor que no sea fabricante/transitario/comisiones/seguro se trata como servicio.
def contenedor_assoc_type_for_tipo_proveedor(tipo_proveedor: str) -> int:
    tipo = (tipo_proveedor or "").strip().lower()
    if tipo == "fabricante":
        return ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_FABRICA
    if tipo == "transitario":
        return ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_TRANSITARIO
    if tipo == "comisiones":
        return ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_INTERMEDIARIO
    if tipo == "seguro":
        return ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SEGURO
    return ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SERVICIO

CONTENEDOR_PROVEEDOR_ASSOC_TYPE_IDS = {
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_FABRICA,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_TRANSITARIO,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_INTERMEDIARIO,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SERVICIO,
    ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PROV_SEGURO,
}

# Movimiento de tesorería (Pagos) gestionado en pagos-proex-microservice.
OBJECT_TYPE_PAGO = "2-44408688"
# Aceptamos ambas direcciones de la etiqueta cobros_to_contenedor: HubSpot
# expone 499 y 500 como dos caras de la misma asociación inversa.
ASSOCIATION_TYPE_ID_CONTENEDOR_TO_PAGO = 499  # mantener por compatibilidad
ASSOCIATION_TYPE_IDS_CONTENEDOR_TO_PAGO = {499, 500}
PAGO_PROP_ACTUALIZACION_PEDIDO = "actualizacion_pedido"
PAGO_PROP_DOCTYPE = "doctype"
PAGO_DOCTYPE_CONTENEDOR = "contenedor"

CONTENEDOR_PROP_ACTUALIZACION_PEDIDO = "actualizacion_pedido"
DEAL_PROP_ACTUALIZACION_PEDIDO = "actualizacion_pedido"

# Objeto personalizado Rutas (fletes cotizados por transitario).
OBJECT_TYPE_RUTA = "2-53295516"
# Etiquetas de asociación Negocio → Ruta creadas en HubSpot (dirección deal→ruta).
ASSOCIATION_TYPE_ID_DEAL_TO_RUTA_TERRESTRE = 714
ASSOCIATION_TYPE_ID_DEAL_TO_RUTA_MARITIMA = 716

# PROPIEDADES REQUERIDAS

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

# VALIDACIONES

# Rango permitido de ocupación del contenedor (%)
OCUPACION_MIN = 90.0
OCUPACION_MAX = 103.0

# Formato del nombre de contenedor
# Ejemplo: "46298478914-001"
NOMBRE_CONTENEDOR_PATTERN = r"^\d+-\d{3}$"

# HELPER FUNCTIONS

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
    """
    return TIPO_CONTENEDOR_LABELS.get(value, value)

def get_tipo_articulo_label(value: str) -> str:
    """
    Obtiene el label de un tipo de artículo.
    """
    return TIPO_ARTICULO_LABELS.get(value, value)

def get_tipo_iva_label(value: str) -> str:
    """
    Obtiene el label de un tipo de IVA.
    """
    return TIPO_IVA_LABELS.get(value, value)
