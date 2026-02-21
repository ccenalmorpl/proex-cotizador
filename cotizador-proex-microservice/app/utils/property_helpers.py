"""Helper functions for dynamic property handling with HubSpot API.

This module provides reusable utilities for working with HubSpot properties
and Pydantic models, making endpoints more maintainable and scalable.
"""

import logging
from typing import Any, Dict, List, Type
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def safe_convert(value: Any, target_type: type) -> Any:
    """Safely convert a value to the target type.

    Handles conversion of HubSpot property values (usually strings) to
    Python types (float, int, str). Supports Optional types.

    Args:
        value: Value to convert (can be string, number, or None).
        target_type: Target type (float, int, str, or Optional[T]).

    Returns:
        Converted value or None if conversion fails.

    Examples:
        >>> safe_convert("15.5", float)
        15.5
        >>> safe_convert("10", int)
        10
        >>> safe_convert("invalid", float)
        None
        >>> safe_convert(None, float)
        None
    """
    if value is None or value == "":
        return None

    # Handle Optional types - extract the actual type
    import typing
    if hasattr(target_type, '__origin__') and target_type.__origin__ is typing.Union:
        actual_types = [t for t in target_type.__args__ if t is not type(None)]
        if actual_types:
            target_type = actual_types[0]

    # Convert based on target type
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

    return value


def get_model_property_names(model_class: Type[BaseModel], exclude: List[str] = None) -> List[str]:
    """Extract property names from a Pydantic model.

    Useful for dynamically building HubSpot API property lists from models.

    Args:
        model_class: Pydantic model class to extract fields from.
        exclude: List of field names to exclude (e.g., ['id']).

    Returns:
        List of field names from the model.

    Examples:
        >>> get_model_property_names(ProductItem, exclude=['id'])
        ['name', 'hs_sku', 'description', 'barcode', ...]
    """
    exclude = exclude or []
    return [field_name for field_name in model_class.model_fields.keys()
            if field_name not in exclude]


def build_update_properties(data_model: BaseModel, model_class: Type[BaseModel]) -> Dict[str, str]:
    """Build HubSpot properties dict from Pydantic model instance.

    Includes all values explicitly set in the model, converting null to empty string
    to clear fields in HubSpot. Numeric types are converted to strings as required by HubSpot API.

    Args:
        data_model: Instance of Pydantic model with data to update.
        model_class: Pydantic model class for field type inspection.

    Returns:
        Dict of properties ready for HubSpot API (with numeric values as strings, null as empty string).

    Examples:
        >>> update_data = UpdateDealData(margen_cliente=15.5, dias_libres=7, comentarios=None)
        >>> build_update_properties(update_data, UpdateDealData)
        {'margen_cliente': '15.5', 'dias_libres': '7', 'comentarios': ''}
    """
    properties = {}

    for field_name, field_info in model_class.model_fields.items():
        value = getattr(data_model, field_name, None)

        # Get field type
        field_type = field_info.annotation

        # Handle Optional types - extract the actual type
        import typing
        if hasattr(field_type, '__origin__') and field_type.__origin__ is typing.Union:
            actual_types = [t for t in field_type.__args__ if t is not type(None)]
            if actual_types:
                field_type = actual_types[0]

        # Handle null values: convert to empty string to clear fields in HubSpot
        if value is None:
            properties[field_name] = ""
        # Convert numeric types to string (HubSpot requirement)
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
    """Parse HubSpot properties into a dict compatible with a Pydantic model.

    Handles type conversion automatically based on the model's field types.

    Args:
        object_id: HubSpot object ID (e.g., product ID, deal ID).
        properties: Dict of properties from HubSpot API response.
        model_class: Pydantic model class to parse properties for.

    Returns:
        Dict with parsed values ready for model instantiation.

    Examples:
        >>> props = {'name': 'Product', 'price_eur': '15.5', 'hs_sku': 'SKU001'}
        >>> parse_hubspot_properties('12345', props, ProductItem)
        {'id': '12345', 'name': 'Product', 'price_eur': 15.5, 'hs_sku': 'SKU001', ...}
    """
    # Start with the ID
    result = {"id": object_id}

    # Process each field from the model
    for field_name, field_info in model_class.model_fields.items():
        if field_name == 'id':
            continue

        # Get the value from HubSpot properties
        value = properties.get(field_name)

        # Convert to the appropriate type using safe_convert
        field_type = field_info.annotation
        result[field_name] = safe_convert(value, field_type)

    return result


def compute_tipo_iva_repercutido(residennum: Any) -> str:
    """Calculate IVA type based on company fiscal residence.

    Centralizes the business logic for determining the IVA (VAT) type
    to apply to line items based on the company's residence type.

    Args:
        residennum: Company residence type from HubSpot.
                   Can be None, empty string, or one of:
                   - "EXTRANJERO" (Foreign)
                   - "EUROPA" (European Union)
                   - "NACIONAL" (National/Spain)

    Returns:
        IVA type code:
        - "EX": Exportaciones (Foreign companies)
        - "EI21": Intracomunitario 21% (EU companies)
        - "R21": Nacional 21% (Spanish companies or default)

    Examples:
        >>> compute_tipo_iva_repercutido("EXTRANJERO")
        "EX"
        >>> compute_tipo_iva_repercutido("europa")  # Case insensitive
        "EI21"
        >>> compute_tipo_iva_repercutido(None)
        "R21"
        >>> compute_tipo_iva_repercutido("")
        "R21"
    """
    # Handle None, empty string, or non-string values
    if not residennum:
        return "R21"

    # Normalize to uppercase and strip whitespace
    residennum_normalized = str(residennum).strip().upper()

    # Determine IVA type based on residence
    if residennum_normalized == "EXTRANJERO":
        return "EX"
    elif residennum_normalized == "EUROPA":
        return "EI21"
    else:
        # Default: NACIONAL or any other value
        return "R21"
