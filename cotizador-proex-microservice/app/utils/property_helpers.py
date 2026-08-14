import logging
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel

from app.utils.tipologia import es_camion

logger = logging.getLogger(__name__)

def round_price(value: Any):
    """Redondea precios unitarios (`precio_unitario`, `precio_de_coste`, `hs_price_eur`) a 3 decimales. Devuelve None si la entrada es None o no es numérica."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None

def round_margin(value: Any):
    """Redondea márgenes (`margen_unitario`) a 5 decimales. Devuelve None si la entrada es None o no es numérica."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return None

def enforce_line_item_price_rounding(properties: dict) -> dict:
    """Garantiza que `price` de un dict de propiedades de line item esté redondeado
    a 3 decimales. Si `price` no es numérico o es vacío, se deja como vino. Muta y
    devuelve el mismo dict para encadenar."""
    if not isinstance(properties, dict):
        return properties
    raw = properties.get("price")
    if raw is None or raw == "":
        return properties
    rounded = round_price(raw)
    if rounded is not None:
        properties["price"] = str(rounded)
    return properties

def safe_convert(value: Any, target_type: type) -> Any:
    """Convierte un valor de HubSpot al tipo Python destino. Soporta Optional[T]. Devuelve None si la conversión falla."""
    if value is None or value == "":
        return None

    import typing
    if hasattr(target_type, '__origin__') and target_type.__origin__ is typing.Union:
        actual_types = [t for t in target_type.__args__ if t is not type(None)]
        if actual_types:
            target_type = actual_types[0]

    if target_type is float:
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning(f"Failed to convert '{value}' to float")
            return None
    elif target_type is int:
        try:
            return int(float(value))
        except (ValueError, TypeError):
            logger.warning(f"Failed to convert '{value}' to int")
            return None
    elif target_type is str:
        return str(value) if value else None
    elif target_type is bool:
        # HubSpot devuelve booleanos como "true"/"false" string
        if isinstance(value, bool):
            return value
        return str(value).lower() == "true"

    return value

def get_model_property_names(model_class: Type[BaseModel], exclude: List[str] = None) -> List[str]:
    """Lista de nombres de campos del modelo Pydantic, para construir las propiedades a pedir a HubSpot."""
    exclude = exclude or []
    return [field_name for field_name in model_class.model_fields.keys()
            if field_name not in exclude]

def build_update_properties(data_model: BaseModel, model_class: Type[BaseModel]) -> Dict[str, str]:
    """Convierte un modelo Pydantic en el dict que espera HubSpot para PATCH (numéricos a string, null a "" para limpiar)."""
    properties = {}

    for field_name, field_info in model_class.model_fields.items():
        value = getattr(data_model, field_name, None)

        field_type = field_info.annotation

        import typing
        if hasattr(field_type, '__origin__') and field_type.__origin__ is typing.Union:
            actual_types = [t for t in field_type.__args__ if t is not type(None)]
            if actual_types:
                field_type = actual_types[0]

        if value is None:
            properties[field_name] = ""
        elif field_type is bool:
            properties[field_name] = str(value).lower()
        elif field_type in (float, int):
            properties[field_name] = str(value)
        else:
            properties[field_name] = value

    return properties

def parse_hubspot_properties(
    object_id: str,
    properties: Dict[str, Any],
    model_class: Type[BaseModel]
) -> Dict[str, Any]:
    """Parsea la respuesta de HubSpot a un dict listo para instanciar `model_class`, aplicando conversión de tipos."""
    result = {"id": object_id}

    for field_name, field_info in model_class.model_fields.items():
        if field_name == 'id':
            continue

        value = properties.get(field_name)

        field_type = field_info.annotation
        result[field_name] = safe_convert(value, field_type)

    return result

# HubSpot almacena residennum con código numérico: 1=Nacional, 2=Europa, 4=Extranjero
_RESIDENNUM_CODE_MAP: dict = {"1": "NACIONAL", "2": "EUROPA", "4": "EXTRANJERO"}

def _normalize_residennum(residennum: Any) -> str:
    if not residennum:
        return "NACIONAL"
    s = str(residennum).strip()
    return _RESIDENNUM_CODE_MAP.get(s, s).upper()

# EX y EI21 son códigos REPERCUTIDOS: no valen como default de soportado. Fuera de
# NACIONAL la categoría soportado no tiene un equivalente unívoco (compra
# intracomunitaria vs importación vs inversión del sujeto pasivo), así que se deja
# sin default y lo elige el usuario en el select.
_DEFAULT_TIPO_IVA: dict = {
    ("repercutido", "NACIONAL"): "R21",
    ("repercutido", "EUROPA"): "EI21",
    ("repercutido", "EXTRANJERO"): "EX",
    ("soportado", "NACIONAL"): "S21",
}


def _existe_en_categoria(code: str, categoria: str, residencia: str) -> bool:
    """False solo si la tabla de IVA está cargada y no tiene ese code en la
    categoría/residencia. Con el cache vacío no se invalida nada."""
    from app.shared import hubdb_client

    entries = hubdb_client.list_entries(categoria, residencia)
    return not entries or any(e.code == code for e in entries)


def compute_default_tipo_iva(residennum: Any, categoria: str = "repercutido") -> str:
    """Calcula el tipo de IVA por defecto cuando el producto no trae un valor específico.

    `residennum` admite código numérico (1/2/4) o etiqueta ya resuelta (NACIONAL/EUROPA/EXTRANJERO).
    Devuelve "" cuando no hay default seguro para esa categoría y residencia: grabar
    un código de la categoría contraria rompe la sincronización con SAP.
    """
    cat = (categoria or "").strip().lower()
    residencia = _normalize_residennum(residennum)

    code = _DEFAULT_TIPO_IVA.get((cat, residencia))
    if not code:
        return ""

    if not _existe_en_categoria(code, cat, residencia):
        logger.warning(
            "[compute_default_tipo_iva] %r no está en categoria=%s residencia=%s — sin default",
            code, cat, residencia,
        )
        return ""

    return code

compute_tipo_iva_repercutido = compute_default_tipo_iva

def resolve_product_iva(
    product_properties: Dict[str, Any],
    residennum: Any,
    categoria: str,
    current_value: str = None
) -> str:
    """Resuelve el IVA del producto.

    Reglas: (1) si `current_value` está informado se respeta — nunca se sobrescribe;
    (2) se busca en el producto la propiedad que corresponde a la residencia
    (`tipo_iva_{categoria}` / `_eu` / `_ex`); (3) fallback a `compute_default_tipo_iva`.
    """
    if current_value is not None and current_value != "":
        return current_value

    if product_properties:
        residencia = _normalize_residennum(residennum)

        if residencia == "EUROPA":
            prop_name = f"tipo_iva_{categoria}_eu"
        elif residencia == "EXTRANJERO":
            prop_name = f"tipo_iva_{categoria}_ex"
        else:
            prop_name = f"tipo_iva_{categoria}"

        product_value = product_properties.get(prop_name)
        if product_value and str(product_value).strip():
            return str(product_value).strip()

    return compute_default_tipo_iva(residennum, categoria)

# Almacenes SAP (valor interno de la propiedad `almacen`)
ALMACEN_SAN_ROQUE = "01"
ALMACEN_EXPORTACION = "10"
ALMACEN_NACIONAL = "12"

def resolve_almacen_para_elemento(
    deal_almacen: Any,
    tipo_articulo: Optional[str],
    tipo_contenedor: Optional[str],
) -> Any:
    """Almacén efectivo de un line item / elemento de pedido o de contenedor.

    Solo los productos (`tipo_articulo == "fabricante"`) se quedan en San Roque (01).
    El resto (servicio, flete, transitario, comisiones/intermediario, seguro y la
    propia línea de contenedor) se reubica en el almacén interno por transporte: 12
    si es camión (trailer), 10 si es contenedor. Para cualquier almacén distinto de
    San Roque, o para productos, se devuelve `deal_almacen` sin cambios.
    """
    if deal_almacen is None or str(deal_almacen).strip() != ALMACEN_SAN_ROQUE:
        return deal_almacen
    if (tipo_articulo or "").strip().lower() == "fabricante":
        return deal_almacen
    return ALMACEN_NACIONAL if es_camion(tipo_contenedor) else ALMACEN_EXPORTACION
