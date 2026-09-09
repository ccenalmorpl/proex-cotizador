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

# Negocios que GENERAN el KIT, no que lo consumen. Son cosas distintas: PROEX
# suma las cajas de varias ofertas abiertas de la misma referencia y pide un
# unico KIT por la diferencia, asi que un KIT nace de varias cotizaciones.
#
# Existe porque `cotizacion_generadora_id` guarda UN id, y con eso el KIT solo
# podia salir en un pedido. Cristobal (2026-09-07): "si en el cotizador
# seleccionas 3 negocios asociados al kit, despues debe salir el kit como kit a
# generar en los 3 pedidos, no solo en el que realmente se uso para generarlo".
#
# `cotizacion_generadora_id` se queda, pero cambia de papel: ya no decide quien
# ve el KIT, solo dice quien lo creo — lo que usan la numeracion y la
# idempotencia. La cotizacion creadora va TAMBIEN aqui dentro, para que el
# filtro de pedidos sea uno solo y no dos.
PROP_KIT_GENERADO_POR_IDS = "kit_generado_por_ids"
PROP_KIT_CONSUMIDO_POR_IDS = "kit_consumido_por_ids"

# Separadores del formato serializado.
KIT_SEP_ENTRADAS = ";"
KIT_SEP_CAMPOS = ":"


# ─────────────────────────────────────────────────────────────────────────────
# NEGOCIOS GENERADORES DEL KIT
#
# Un pedido KIT no nace de una sola cotización: PROEX suma las cajas de varias
# ofertas abiertas de la misma referencia y pide un único KIT por la diferencia
# ("van a sumar los tres contenedores independientes… nos van a generar un único
# pedido kit", reunión del 2026-09-03). Para ofrecer esa lista hay que filtrar
# negocios del pipeline de cotización que no estén ni ganados ni perdidos.
# ─────────────────────────────────────────────────────────────────────────────

COTIZACION_PIPELINE_ID = "default"

# Se conservan por nombre porque son los que se citaron en la reunión, pero el
# filtro real usa `metadata.isClosed` de la etapa: así una etapa cerrada nueva
# queda fuera sola, sin tocar código.
COTIZACION_DEALSTAGE_GANADO = "986679651"
COTIZACION_DEALSTAGE_PERDIDO = "987141733"

# En los line items, `hs_sku` NO es la referencia: es el ID interno del producto
# en HubSpot. En los productos sí es la referencia. Verificado sobre datos reales
# (line item 57667553062: hs_sku="42078145076", name="63361158165-001 - CERVEZA…").
#
# Por eso el filtro va por ID de producto y es exacto, y no hace falta el
# "nombre contiene nombre" que temíamos: el nombre del line item lleva delante el
# ID del contenedor y nunca coincide con el del producto.
PROP_LINE_ITEM_PRODUCTO = "hs_sku"
PROP_LINE_ITEM_CANTIDAD = "quantity"


# ─────────────────────────────────────────────────────────────────────────────
# ASOCIACIÓN ETIQUETADA COTIZACIÓN ↔ PEDIDO KIT
#
# Cristóbal (2026-09-07): "yo crearía etiqueta de 'generador kit' y usaría esa".
# Creada en el portal el 2026-09-08 como PAR de etiquetas, siguiendo el estilo
# direccional que ya usaban las tres existentes (`Cotización > Pedido`).
#
# La asociación es ESPEJO de `kit_generado_por_ids`, no fuente de verdad: nada
# lee asociaciones para calcular. Si divergieran, se regeneran desde la
# propiedad. Duplicar el vínculo fue decisión suya —"si puedes mantener esto y
# tratar de hacer la asociación también" (reunión del 2026-09-03)— porque la
# propiedad no se ve en el registro de HubSpot y la asociación sí.
#
# OJO CON LA DIRECCIÓN: al ser un par, cada typeId vale para un sentido, y la
# API de etiquetas no dice cuál (las dos salen como deal→deal). Se usa la 724
# desde la cotización hacia el KIT porque es lo que dice su propio texto. Si al
# abrir el primer KIT real las etiquetas se leen al revés, se intercambian estas
# dos constantes y ya: el vínculo es correcto en ambos casos, solo cambia el
# rótulo.
ASOC_COTIZACION_A_KIT = 724      # "Cotización > Pedido KIT"
ASOC_KIT_A_COTIZACION = 723      # "Pedido KIT > Cotización"
