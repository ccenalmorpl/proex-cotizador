"""Transporte camión vs contenedor a partir de `tipo_contenedor`.

Espejo del helper de los microservicios de pedidos/documentos: camión = valores de
CAMION_TIPO_CONTENEDOR (normalizado); el resto, contenedor. Gobierna apariencia y
routing de almacén, NO el eje Nacional/Exportación de la plantilla ni el título de la
cotización (eso lo decide `tipo_cliente` de la Company, salvo en los destinos fuera
de la unión aduanera — ver `es_destino_fuera_territorio_aduanero_ue`).
"""

import re

CAMION_TIPO_CONTENEDOR = frozenset({"TRAILER80"})

# Ceuta y Melilla: clientes nacionales fuera del territorio aduanero de la UE. La
# aduana exige documentación de exportación (Incoterm, peso neto y peso bruto), así
# que el puerto de destino manda sobre el `tipo_cliente` de la Company. Se admiten
# el valor interno y el label por si el puerto llega ya resuelto; Melbourne (MEB /
# "Melbourne, (VI), AU") no colisiona con ninguna forma.
PUERTOS_FUERA_TERRITORIO_ADUANERO_UE = frozenset({"CEU", "MEL", "CEUTA", "MELILLA"})


def normalize_tipo_contenedor(raw) -> str:
    return re.sub(r"[^A-Z0-9]", "", (raw or "").strip().upper())


def es_camion(tipo_contenedor_raw) -> bool:
    return normalize_tipo_contenedor(tipo_contenedor_raw) in CAMION_TIPO_CONTENEDOR


def es_destino_fuera_territorio_aduanero_ue(puerto_destino_raw) -> bool:
    puerto = str(puerto_destino_raw or "").split(",")[0].strip().upper()
    return puerto in PUERTOS_FUERA_TERRITORIO_ADUANERO_UE
