"""Clasificación de líneas de flete por tramo a partir del sufijo del nombre.

Modo doble (camión con tramo marítimo): dos líneas por contenedor, una por
tramo, cada una con su transitario e IVA. La línea legada '- FLETES'
(combinada) se trata como marítima/actual.
"""

from typing import Optional

TRAMO_MARITIMO = "maritimo"
TRAMO_TERRESTRE = "terrestre"
TRAMO_LEGACY = "legacy"

FLETE_SUFIJO_MARITIMO = "FLETE MARITIMO"
FLETE_SUFIJO_TERRESTRE = "FLETE TERRESTRE"

# Ambos tramos usan el mismo producto de catálogo; se distinguen por el sufijo
# del nombre del line item / Elemento, no por el producto.
FLETE_PRODUCT_CARDCODE = "VARIAB-TRANSP-00001"


def clasificar_tramo_flete(nombre) -> Optional[str]:
    """Tramo de una línea/Elemento de flete según el sufijo de su nombre.
    Devuelve None si el nombre no corresponde a un flete."""
    n = (nombre or "").strip()
    if n.endswith(f"- {FLETE_SUFIJO_TERRESTRE}") or n == FLETE_SUFIJO_TERRESTRE:
        return TRAMO_TERRESTRE
    if n.endswith(f"- {FLETE_SUFIJO_MARITIMO}") or n == FLETE_SUFIJO_MARITIMO:
        return TRAMO_MARITIMO
    if n.endswith("- FLETES") or n == "FLETES":
        return TRAMO_LEGACY
    return None
