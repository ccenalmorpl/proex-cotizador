"""Lógica de pedidos KIT — funciones puras, sin HubSpot ni HTTP.

Un pedido KIT cubre lo que sobra cuando el pedido del cliente no completa un
bloque de fabricación. Dos invariantes del diseño que gobiernan el fichero:

  1. El KIT es POR PRODUCTO y a nivel de NEGOCIO, no de contenedor: las cajas del
     mismo artículo se suman en todos los contenedores antes de decidir si falta
     producción.
  2. Un KIT es de un solo producto. Un pedido puede generar varios KITs
     independientes, uno por artículo que no llegue al bloque.

Espejo en Python de `cotizador-proex-card/src/app/cards/utils/kits.utils.js`. Las
dos implementaciones deben dar el mismo número para las mismas entradas: la card
calcula para mostrar, el backend recalcula para persistir, y si divergen el
comercial vería un KIT distinto del que se crea.

Aislado a propósito: nada de la lógica existente llama aquí.
"""

import math
from dataclasses import dataclass
from typing import Dict, Optional

from app.core.kits_constants import (
    KIT_DEALSTAGE_ABIERTO,
    KIT_DEALSTAGE_AGOTADO,
    KIT_DEALSTAGE_CANCELADO,
    KIT_DEALSTAGE_PENDIENTE_VALIDACION,
    KIT_PIPELINE_ID,
    KIT_SEP_CAMPOS,
    KIT_SEP_ENTRADAS,
    PROP_KIT_CAJAS,
    PROP_KIT_CAJAS_DISPONIBLES,
    PROP_KIT_GENERADO_POR_IDS,
    PROP_KIT_COTIZACION_GENERADORA,
    PROP_KIT_NOMBRE,
    PROP_KIT_NUMERO,
    PROP_KIT_SKU,
    PROP_KIT_VALIDACION_PRODUCCION,
)


def _num(value) -> float:
    """Número tolerante: vacío, None y basura valen 0 (HubSpot sirve strings)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def _bloque_multiplo(value) -> int:
    """Vacío o 0 se interpreta como 1: solo los saborizantes rellenan múltiplo."""
    b = int(_num(value))
    return b if b > 0 else 1


# CÁLCULO


def calcular_kits_necesarios(cajas, tamano_kit, bloque_multiplo=1) -> int:
    """Cajas de KIT necesarias para completar la producción.

        cajas_kit = ceil(q / (T*B)) * (T*B) - q

    `T` (tamano_kit) es el bloque de fabricación y `B` (bloque_multiplo) el
    número de bloques que la fábrica exige producir de golpe. Con B=1 se completa
    hasta el siguiente múltiplo del mínimo; con B>1 hasta el siguiente múltiplo
    de B bloques, que es el caso de los saborizantes (una vez abierta la bolsa
    hay que consumirla, así que no se aceptan fracciones).

    Devuelve 0 si el producto no tiene bloque, si las cajas son 0 o si la
    cantidad ya es múltiplo exacto.
    """
    q = _num(cajas)
    t = _num(tamano_kit)
    b = _bloque_multiplo(bloque_multiplo)

    if q <= 0 or t <= 0:
        return 0

    bloque = t * b
    return int(math.ceil(q / bloque) * bloque - q)


@dataclass(frozen=True)
class ProduccionKit:
    """Cuentas de producción de un artículo tras descontar el stock marcado."""

    a_producir: int
    bloque: int
    bloques: int
    tirada: int
    cajas_kit: int
    umbral_para_evitar: int


def calcular_produccion(cajas_pedidas, marcadas, tamano_kit, bloque_multiplo=1) -> ProduccionKit:
    """Producción de un artículo descontando el stock de KIT ya marcado.

    El stock existente se descuenta ANTES de redondear: PROEX solo pide a fábrica
    lo que le falta. Y lo que pide se produce en bloques enteros, tal como lo
    explicó Cristóbal en el traspaso: si la fábrica produce de 5 en 5, por mucho
    que le pidas 6 te tiene que hacer otros 4.

    `umbral_para_evitar` es cuántas cajas habría que marcar para que la producción
    baje un bloque entero y no haga falta KIT. Es el dato accionable: marcar poco
    no evita el KIT (solo cambia de dónde salen las cajas), marcar hasta el umbral
    sí lo elimina.
    """
    q = max(0.0, _num(cajas_pedidas))
    m = min(max(0.0, _num(marcadas)), q)
    t = _num(tamano_kit)
    b = _bloque_multiplo(bloque_multiplo)

    a_producir = int(q - m)
    if t <= 0:
        return ProduccionKit(
            a_producir=a_producir,
            bloque=0,
            bloques=0,
            tirada=a_producir,
            cajas_kit=0,
            umbral_para_evitar=0,
        )

    bloque = int(t * b)
    bloques = int(math.ceil(a_producir / bloque)) if a_producir > 0 else 0
    tirada = bloques * bloque

    return ProduccionKit(
        a_producir=a_producir,
        bloque=bloque,
        bloques=bloques,
        tirada=tirada,
        cajas_kit=tirada - a_producir,
        # Marcando esto, `a_producir` cae justo a un múltiplo exacto de bloque.
        umbral_para_evitar=max(0, int(q) - (int(q) // bloque) * bloque),
    )


def params_kit_de_producto(props: Optional[Dict]) -> Optional[Dict]:
    """Parámetros de KIT desde las propiedades de un Producto de HubSpot.

    Devuelve None cuando el producto no declara bloque. Eso NO es un error: puede
    que la fábrica no produzca ese artículo en bloques. Quien llama decide si
    avisar de la falta de dato, pero no debe inventarse un bloque.
    """
    if not props:
        return None

    tamano = _num(props.get("tamano_kit"))
    if tamano <= 0:
        return None

    return {
        "tamano_kit": int(tamano),
        "bloque_multiplo": _bloque_multiplo(props.get("bloque_multiplo")),
    }


# RELACIONES SERIALIZADAS  (formato "dealId:cajas;dealId:cajas")


def parsear_marcas_kit(raw) -> Dict[str, int]:
    """Deserializa `kit_marcado_por_ids` / `kit_consumido_por_ids`.

    Tolerante por diseño: la propiedad la puede haber tocado un humano desde la
    ficha del negocio, así que entradas vacías, sin cajas o con basura se ignoran
    en vez de romper. Un dealId repetido suma sus cajas: dos escrituras
    concurrentes que hayan añadido la misma marca no deben perder cantidad.
    """
    marcas: Dict[str, int] = {}
    if not raw:
        return marcas

    for entrada in str(raw).split(KIT_SEP_ENTRADAS):
        entrada = entrada.strip()
        if not entrada:
            continue

        deal_id, _, cajas_raw = entrada.partition(KIT_SEP_CAMPOS)
        deal_id = deal_id.strip()
        cajas = int(_num(cajas_raw))
        if not deal_id or cajas <= 0:
            continue

        marcas[deal_id] = marcas.get(deal_id, 0) + cajas

    return marcas


def serializar_marcas_kit(marcas: Optional[Dict[str, int]]) -> str:
    """Vuelve al string de HubSpot. Las marcas a 0 desaparecen.

    Devuelve cadena vacía cuando no queda ninguna, que es lo que HubSpot entiende
    como borrar la propiedad.
    """
    if not marcas:
        return ""

    return KIT_SEP_ENTRADAS.join(
        f"{deal_id}{KIT_SEP_CAMPOS}{int(cajas)}"
        for deal_id, cajas in marcas.items()
        if str(deal_id).strip() and int(_num(cajas)) > 0
    )


def aplicar_marca_kit(raw, deal_id: str, cajas) -> str:
    """Añade o reemplaza la marca de un negocio sobre un KIT.

    Reemplaza en vez de sumar: el comercial edita "cajas a consumir" varias veces
    sobre la misma oferta, y cada edición es el valor definitivo de ESA oferta, no
    un incremento. Con `cajas <= 0` la marca se elimina (desmarcar).
    """
    marcas = parsear_marcas_kit(raw)
    deal_id = str(deal_id or "").strip()
    if not deal_id:
        return serializar_marcas_kit(marcas)

    n = int(_num(cajas))
    if n > 0:
        marcas[deal_id] = n
    else:
        marcas.pop(deal_id, None)

    return serializar_marcas_kit(marcas)


def podar_marcas_kit(raw, deal_ids_vivos) -> str:
    """Quita las marcas de negocios que ya no existen o han dejado de marcar.

    Necesario porque una oferta puede perderse, borrarse o rehacerse sin avisar al
    KIT: sin poda, sus cajas quedarían reservadas para siempre y el KIT parecería
    agotado teniendo stock.
    """
    vivos = {str(d).strip() for d in (deal_ids_vivos or []) if str(d).strip()}
    marcas = parsear_marcas_kit(raw)
    return serializar_marcas_kit({d: c for d, c in marcas.items() if d in vivos})


def total_cajas_marcadas(raw) -> int:
    """Suma de cajas comprometidas en una propiedad de relación."""
    return sum(parsear_marcas_kit(raw).values())


def calcular_cajas_disponibles(cajas_kit, raw_consumido) -> int:
    """Cajas que un KIT aún puede servir.

    Solo descuenta lo CONSUMIDO (consumo ya validado por Administración), nunca lo
    marcado: una marca es una reserva pendiente de validar y puede caerse. Si
    restara marcas, un KIT quedaría bloqueado por ofertas que nunca cerraron.
    """
    return max(0, int(_num(cajas_kit)) - total_cajas_marcadas(raw_consumido))


# PAYLOAD DE CREACIÓN


def formatear_numero_kit(cotizacion_id, secuencia: int) -> str:
    """Número legible del KIT: `KIT-<id cotización>-<secuencia>`.

    La secuencia distingue los varios KIT que puede generar una misma cotización
    (uno por artículo que no llegue al bloque), y va a dos dígitos para que
    ordenen bien alfabéticamente en los listados de HubSpot.
    """
    return f"KIT-{str(cotizacion_id or '').strip()}-{max(1, int(_num(secuencia))):02d}"


def construir_payload_kit(
    *,
    cotizacion_id,
    secuencia: int,
    sku: str,
    nombre_producto: str,
    cajas_kit,
    negocios_generadores: Optional[Dict[str, int]] = None,
) -> Dict[str, str]:
    """Propiedades para crear el Negocio KIT en HubSpot.

    Nace siempre en `Pendiente de validación`: las cajas las calcula el cotizador,
    pero solo Administración confirma que la fábrica las va a producir de verdad.

    `amount` se deja FUERA a propósito, no vacío por descuido: un KIT no factura
    —el cliente paga solo sus cajas— y con importe entraría en el forecast y
    duplicaría el ingreso cuando ese sobrante se venda más adelante.

    `negocios_generadores` son las ofertas abiertas que se anotaron al pedir el
    KIT, con las cajas que aporta cada una: `{deal_id: cajas}`. La cotización que
    lo crea entra siempre, aunque no venga en el diccionario, porque es la fila
    fija de la tabla y sin ella el KIT no saldría en su propio pedido.

    Devuelve solo propiedades. La asociación con la Company de PROEX
    (`KIT_COMPANY_ID`) y con la cotización las hace quien llama, porque son
    llamadas a la API y aquí no se toca HubSpot.
    """
    numero = formatear_numero_kit(cotizacion_id, secuencia)
    cajas = int(_num(cajas_kit))
    creadora = str(cotizacion_id or "").strip()

    # La creadora primero y con las cajas que ya tuviera anotadas: si viene en el
    # diccionario se respeta su cifra, y si no, se le pone el total del KIT para
    # que la entrada nunca quede a 0 —serializar_marcas_kit descarta los ceros y
    # la creadora desapareceria de su propio KIT.
    generadores: Dict[str, int] = {}
    if creadora:
        generadores[creadora] = int(_num((negocios_generadores or {}).get(creadora))) or cajas
    for deal_id, aporta in (negocios_generadores or {}).items():
        if str(deal_id).strip() and str(deal_id).strip() != creadora:
            generadores[str(deal_id).strip()] = int(_num(aporta))

    return {
        "dealname": f"{numero} - {nombre_producto}".strip(" -"),
        "pipeline": KIT_PIPELINE_ID,
        "dealstage": KIT_DEALSTAGE_PENDIENTE_VALIDACION,
        PROP_KIT_NUMERO: numero,
        PROP_KIT_SKU: str(sku or "").strip(),
        PROP_KIT_NOMBRE: str(nombre_producto or "").strip(),
        PROP_KIT_CAJAS: str(cajas),
        # Nace con todo disponible: nadie ha consumido nada todavía.
        PROP_KIT_CAJAS_DISPONIBLES: str(cajas),
        PROP_KIT_COTIZACION_GENERADORA: creadora,
        # Quien lo genera, la creadora incluida. Es lo que permite que el KIT
        # salga en los pedidos de las tres ofertas y no solo en el suyo.
        PROP_KIT_GENERADO_POR_IDS: serializar_marcas_kit(generadores),
        PROP_KIT_VALIDACION_PRODUCCION: "false",
    }


# ETAPA DERIVADA


def calcular_dealstage_kit(
    *,
    validado_produccion: bool,
    cajas_disponibles,
    dealstage_actual: str = "",
) -> str:
    """Etapa del Negocio KIT a partir de sus propiedades.

    Se recalcula desde cero en cada pasada, sin mirar de qué etapa venía: así un
    KIT Agotado vuelve solo a Abierto cuando se libera una marca, sin necesidad de
    una transición explícita. `Cancelado` es la única etapa pegajosa, porque es una
    decisión manual de Administración que ningún recálculo debe deshacer.

    Sigue el patrón de `calcular_dealstage_documento` en proex-documentos.
    """
    if dealstage_actual == KIT_DEALSTAGE_CANCELADO:
        return KIT_DEALSTAGE_CANCELADO

    if not validado_produccion:
        return KIT_DEALSTAGE_PENDIENTE_VALIDACION

    if int(_num(cajas_disponibles)) <= 0:
        return KIT_DEALSTAGE_AGOTADO

    return KIT_DEALSTAGE_ABIERTO
