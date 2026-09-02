"""IDs y nombres internos del flujo Pedidos KIT en HubSpot.

Pipeline creado en el portal de producción (48336053) el 2026-08-04. Aislado en
su propio módulo a propósito: nada del flujo existente importa de aquí, así que
todo el bloque KIT se puede comentar de una pieza si hace falta.

Las dos etapas abiertas quedaron con probabilidad 10 % porque HubSpot no permite
bajarlas a 0. La salvaguarda es dejar `amount` VACÍO en los negocios KIT: un KIT
no factura (el cliente paga solo sus cajas), así que sin importe el forecast
multiplica por cero y esas cajas no se cuentan dos veces cuando se vendan.
"""

# PIPELINE Y ETAPAS

KIT_PIPELINE_ID = "922791345"

KIT_DEALSTAGE_PENDIENTE_VALIDACION = "1412294245"  # inicial
KIT_DEALSTAGE_ABIERTO = "1412294246"
KIT_DEALSTAGE_AGOTADO = "1412294250"  # closed won
KIT_DEALSTAGE_CANCELADO = "1412294251"  # closed lost

KIT_DEALSTAGE_LABELS = {
    KIT_DEALSTAGE_PENDIENTE_VALIDACION: "Pendiente de validación",
    KIT_DEALSTAGE_ABIERTO: "Abierto",
    KIT_DEALSTAGE_AGOTADO: "Agotado",
    KIT_DEALSTAGE_CANCELADO: "Cancelado",
}

# Etapas en las que el KIT ya no admite consumo nuevo.
KIT_DEALSTAGES_CERRADOS = (KIT_DEALSTAGE_AGOTADO, KIT_DEALSTAGE_CANCELADO)

# COMPANY

# Los KIT se asocian a PROEX, no al cliente: el sobrante es stock propio y
# ningún cliente debe verlo en su ficha.
KIT_COMPANY_ID = "31777315653"

# PROPIEDADES DEL PRODUCTO

PROP_PRODUCTO_TAMANO_KIT = "tamano_kit"
PROP_PRODUCTO_BLOQUE_MULTIPLO = "bloque_multiplo"

# PROPIEDADES DEL NEGOCIO KIT

PROP_KIT_NUMERO = "numero_de_kit"
PROP_KIT_SKU = "producto_kit_sku"
PROP_KIT_NOMBRE = "producto_kit_nombre"
PROP_KIT_CAJAS = "cajas_kit"
PROP_KIT_CAJAS_DISPONIBLES = "cajas_disponibles_kit"
PROP_KIT_VALIDACION_PRODUCCION = "validacion_produccion_kit_admin"
PROP_KIT_MOTIVO_CANCELACION = "motivo_cancelacion_kit"
PROP_KIT_COTIZACION_GENERADORA = "cotizacion_generadora_id"

# Relaciones por propiedad, no por asociación etiquetada: la poda de una marca es
# reescribir un string en vez de archivar asociaciones etiquetadas (que el SDK no
# soporta y el cotizador tuvo que resolver a mano con `requests`).
# Formato: "dealId:cajas;dealId:cajas"
PROP_KIT_MARCADO_POR_IDS = "kit_marcado_por_ids"
PROP_KIT_CONSUMIDO_POR_IDS = "kit_consumido_por_ids"

# Separadores del formato serializado.
KIT_SEP_ENTRADAS = ";"
KIT_SEP_CAMPOS = ":"
