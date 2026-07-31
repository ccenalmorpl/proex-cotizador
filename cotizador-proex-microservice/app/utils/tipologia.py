"""Transporte camión vs contenedor a partir de `tipo_contenedor`.

Espejo del helper de los microservicios de pedidos/documentos: camión = valores de
CAMION_TIPO_CONTENEDOR (normalizado); el resto, contenedor. Gobierna apariencia y
routing de almacén, NO el eje Nacional/Exportación de la plantilla ni el título de la
cotización (eso lo decide `tipo_cliente` de la Company).
"""

import re

CAMION_TIPO_CONTENEDOR = frozenset({"TRAILER80"})


def normalize_tipo_contenedor(raw) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").strip().upper())


def es_camion(tipo_contenedor_raw) -> bool:
    return normalize_tipo_contenedor(tipo_contenedor_raw) in CAMION_TIPO_CONTENEDOR
