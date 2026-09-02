"""Tests del cálculo de pedidos KIT: fórmula de bloques, descuento del stock
marcado, relaciones serializadas por propiedad y etapa derivada. Funciones puras.

Los casos numéricos no son inventados: salen del ejemplo real que Administración
envió tras la reunión y de los escenarios validados en vivo con la card.
"""

import pytest

from app.core.kits_constants import (
    KIT_DEALSTAGE_ABIERTO,
    KIT_DEALSTAGE_AGOTADO,
    KIT_DEALSTAGE_CANCELADO,
    KIT_DEALSTAGE_PENDIENTE_VALIDACION,
    KIT_PIPELINE_ID,
)
from app.utils.kits import (
    aplicar_marca_kit,
    construir_payload_kit,
    formatear_numero_kit,
    calcular_cajas_disponibles,
    calcular_dealstage_kit,
    calcular_kits_necesarios,
    calcular_produccion,
    params_kit_de_producto,
    parsear_marcas_kit,
    podar_marcas_kit,
    serializar_marcas_kit,
    total_cajas_marcadas,
)

# FÓRMULA BASE


def test_caso_real_de_administracion():
    """El ejemplo que envió Administración: 14508 cajas con bloque de 1894.

    ceil(14508/1894) = 8 bloques -> 15152 producidas -> KIT = 644.
    Es el único caso con cifras reales confirmadas por negocio, así que si este
    test cae, la fórmula está mal por mucho que los demás pasen.
    """
    assert calcular_kits_necesarios(14508, 1894) == 644


def test_multiplo_exacto_no_genera_kit():
    assert calcular_kits_necesarios(6000, 3000) == 0
    assert calcular_kits_necesarios(1894, 1894) == 0


def test_completa_hasta_el_siguiente_bloque():
    assert calcular_kits_necesarios(3500, 3000) == 2500
    assert calcular_kits_necesarios(4836, 3000) == 1164
    # Menos de un bloque: se produce el bloque entero igual.
    assert calcular_kits_necesarios(1, 3000) == 2999


def test_bloque_multiplo_de_saborizantes():
    """Con B=2 la fábrica solo sirve pares de bloques: el múltiplo es 6000."""
    assert calcular_kits_necesarios(3500, 3000, 2) == 2500
    assert calcular_kits_necesarios(6500, 3000, 2) == 5500
    assert calcular_kits_necesarios(12000, 3000, 2) == 0


def test_sin_bloque_o_sin_cajas_no_hay_kit():
    assert calcular_kits_necesarios(4836, 0) == 0
    assert calcular_kits_necesarios(4836, None) == 0
    assert calcular_kits_necesarios(0, 3000) == 0
    assert calcular_kits_necesarios(-10, 3000) == 0


def test_bloque_multiplo_vacio_equivale_a_uno():
    """HubSpot devuelve '' cuando la propiedad no está puesta."""
    esperado = calcular_kits_necesarios(4836, 3000, 1)
    assert calcular_kits_necesarios(4836, 3000, "") == esperado
    assert calcular_kits_necesarios(4836, 3000, 0) == esperado
    assert calcular_kits_necesarios(4836, 3000, None) == esperado


def test_acepta_strings_de_hubspot():
    assert calcular_kits_necesarios("14508", "1894") == 644
    assert calcular_kits_necesarios("basura", "1894") == 0


# PRODUCCIÓN CON STOCK MARCADO


def test_produccion_sin_marcar_coincide_con_la_formula():
    p = calcular_produccion(4836, 0, 3000)
    assert p.a_producir == 4836
    assert p.bloque == 3000
    assert p.bloques == 2
    assert p.tirada == 6000
    assert p.cajas_kit == 1164
    assert p.cajas_kit == calcular_kits_necesarios(4836, 3000)


def test_produccion_descontando_stock_marcado():
    """Caso validado en la card: 4836 pedidas, 370 marcadas de KITs existentes.

    Se descuenta ANTES de redondear: 4466 a fabricar, siguen siendo 2 bloques,
    6000 - 4466 = 1534 de KIT nuevo.
    """
    p = calcular_produccion(4836, 370, 3000)
    assert p.a_producir == 4466
    assert p.bloques == 2
    assert p.tirada == 6000
    assert p.cajas_kit == 1534


def test_marcar_hasta_el_umbral_elimina_el_kit():
    """El umbral es el dato accionable: marcando eso, la producción cae a bloque
    exacto y no se genera KIT nuevo."""
    p = calcular_produccion(4836, 0, 3000)
    assert p.umbral_para_evitar == 1836  # 4836 - 3000

    exacto = calcular_produccion(4836, p.umbral_para_evitar, 3000)
    assert exacto.a_producir == 3000
    assert exacto.cajas_kit == 0


def test_marcar_poco_no_evita_el_kit_solo_lo_desplaza():
    """Marcar por debajo del umbral no ahorra bloque: el KIT nuevo hasta sube,
    porque cambias cajas viejas por nuevas. Es la confusión que motivó mostrar el
    stock final en la card en vez del KIT nuevo aislado."""
    sin_marcar = calcular_produccion(4836, 0, 3000)
    marcando_algo = calcular_produccion(4836, 370, 3000)

    assert marcando_algo.bloques == sin_marcar.bloques
    assert marcando_algo.cajas_kit > sin_marcar.cajas_kit

    # Pero el total parado en fábrica no empeora: KIT nuevo + stock no consumido.
    disponibles = 370
    stock_final = marcando_algo.cajas_kit + (disponibles - 370)
    stock_final_sin_marcar = sin_marcar.cajas_kit + disponibles
    assert stock_final <= stock_final_sin_marcar


def test_marcar_todo_lo_pedido_no_produce_nada():
    p = calcular_produccion(4836, 4836, 3000)
    assert p.a_producir == 0
    assert p.bloques == 0
    assert p.tirada == 0
    assert p.cajas_kit == 0


def test_marcadas_nunca_superan_lo_pedido():
    """Aunque llegue un valor absurdo, no puede salir producción negativa."""
    p = calcular_produccion(100, 9999, 3000)
    assert p.a_producir == 0
    assert p.cajas_kit == 0


def test_producto_sin_bloque_no_genera_kit_pero_informa_produccion():
    p = calcular_produccion(4836, 370, 0)
    assert p.a_producir == 4466
    assert p.tirada == 4466
    assert p.cajas_kit == 0
    assert p.bloque == 0


# PARÁMETROS DESDE EL PRODUCTO


def test_params_desde_producto():
    assert params_kit_de_producto({"tamano_kit": "1894", "bloque_multiplo": "2"}) == {
        "tamano_kit": 1894,
        "bloque_multiplo": 2,
    }


def test_params_bloque_multiplo_ausente_es_uno():
    assert params_kit_de_producto({"tamano_kit": "3000"}) == {
        "tamano_kit": 3000,
        "bloque_multiplo": 1,
    }
    assert params_kit_de_producto({"tamano_kit": "3000", "bloque_multiplo": ""}) == {
        "tamano_kit": 3000,
        "bloque_multiplo": 1,
    }


@pytest.mark.parametrize(
    "props",
    [None, {}, {"tamano_kit": ""}, {"tamano_kit": "0"}, {"bloque_multiplo": "2"}],
)
def test_producto_sin_tamano_kit_devuelve_none(props):
    """Sin bloque declarado no se inventa uno: quien llama decide si avisar."""
    assert params_kit_de_producto(props) is None


# RELACIONES SERIALIZADAS


def test_parsear_marcas():
    assert parsear_marcas_kit("111:250;222:120") == {"111": 250, "222": 120}


def test_parsear_tolera_basura_y_vacios():
    assert parsear_marcas_kit("") == {}
    assert parsear_marcas_kit(None) == {}
    assert parsear_marcas_kit(";;") == {}
    assert parsear_marcas_kit("111:250;;222:120;") == {"111": 250, "222": 120}
    # Sin cajas, cajas a 0 o negativas, y entradas sin dealId: se ignoran.
    assert parsear_marcas_kit("111;222:120") == {"222": 120}
    assert parsear_marcas_kit("111:0;222:120") == {"222": 120}
    assert parsear_marcas_kit("111:-5;222:120") == {"222": 120}
    assert parsear_marcas_kit(":250;222:120") == {"222": 120}
    assert parsear_marcas_kit("111:abc;222:120") == {"222": 120}
    # Espacios de un humano editando la propiedad a mano.
    assert parsear_marcas_kit(" 111 : 250 ; 222 : 120 ") == {"111": 250, "222": 120}


def test_dealid_repetido_suma_en_vez_de_perder_cajas():
    assert parsear_marcas_kit("111:250;111:100") == {"111": 350}


def test_serializar_es_el_inverso_de_parsear():
    raw = "111:250;222:120"
    assert serializar_marcas_kit(parsear_marcas_kit(raw)) == raw


def test_serializar_sin_marcas_borra_la_propiedad():
    assert serializar_marcas_kit({}) == ""
    assert serializar_marcas_kit(None) == ""
    assert serializar_marcas_kit({"111": 0}) == ""


def test_aplicar_marca_reemplaza_no_suma():
    """Cada edición de "cajas a consumir" es el valor definitivo de esa oferta."""
    raw = aplicar_marca_kit("", "111", 250)
    assert raw == "111:250"

    raw = aplicar_marca_kit(raw, "111", 300)
    assert parsear_marcas_kit(raw) == {"111": 300}

    raw = aplicar_marca_kit(raw, "222", 120)
    assert parsear_marcas_kit(raw) == {"111": 300, "222": 120}


def test_aplicar_marca_con_cero_desmarca():
    raw = "111:250;222:120"
    assert parsear_marcas_kit(aplicar_marca_kit(raw, "111", 0)) == {"222": 120}
    # Desmarcar algo que no estaba no rompe ni altera el resto.
    assert parsear_marcas_kit(aplicar_marca_kit(raw, "999", 0)) == {"111": 250, "222": 120}


def test_podar_marcas_de_negocios_muertos():
    """Sin poda, una oferta perdida dejaría sus cajas reservadas para siempre."""
    raw = "111:250;222:120;333:50"
    assert parsear_marcas_kit(podar_marcas_kit(raw, ["111", "333"])) == {"111": 250, "333": 50}
    assert podar_marcas_kit(raw, []) == ""
    assert podar_marcas_kit(raw, None) == ""


def test_total_cajas_marcadas():
    assert total_cajas_marcadas("111:250;222:120") == 370
    assert total_cajas_marcadas("") == 0


# CAJAS DISPONIBLES


def test_disponibles_descuenta_consumido():
    assert calcular_cajas_disponibles(1534, "111:250;222:120") == 1164


def test_disponibles_ignora_lo_solo_marcado():
    """Una marca es una reserva pendiente de validar: no descuenta stock. Si lo
    hiciera, una oferta que nunca cierra bloquearía el KIT."""
    assert calcular_cajas_disponibles(1534, "") == 1534


def test_disponibles_nunca_es_negativo():
    assert calcular_cajas_disponibles(100, "111:250") == 0


# PAYLOAD DE CREACIÓN


def test_numero_de_kit_a_dos_digitos():
    """Dos dígitos para que ordenen bien alfabéticamente en los listados."""
    assert formatear_numero_kit("98311", 1) == "KIT-98311-01"
    assert formatear_numero_kit("98311", 2) == "KIT-98311-02"
    assert formatear_numero_kit("98311", 12) == "KIT-98311-12"


def test_payload_de_creacion():
    payload = construir_payload_kit(
        cotizacion_id="98311",
        secuencia=1,
        sku="0303003002-00250",
        nombre_producto="NECTAR MELOCOTON 250ML",
        cajas_kit=1534,
    )

    assert payload["pipeline"] == KIT_PIPELINE_ID
    assert payload["dealstage"] == KIT_DEALSTAGE_PENDIENTE_VALIDACION
    assert payload["numero_de_kit"] == "KIT-98311-01"
    assert payload["dealname"] == "KIT-98311-01 - NECTAR MELOCOTON 250ML"
    assert payload["producto_kit_sku"] == "0303003002-00250"
    assert payload["cajas_kit"] == "1534"
    assert payload["cotizacion_generadora_id"] == "98311"
    assert payload["validacion_produccion_kit_admin"] == "false"


def test_payload_nace_con_todo_disponible():
    payload = construir_payload_kit(
        cotizacion_id="98311",
        secuencia=1,
        sku="0303003002-00250",
        nombre_producto="NECTAR",
        cajas_kit=1534,
    )
    assert payload["cajas_disponibles_kit"] == payload["cajas_kit"]


def test_payload_sin_amount():
    """Un KIT no factura: con importe entraría en el forecast y duplicaría el
    ingreso cuando ese sobrante se venda. La ausencia es deliberada."""
    payload = construir_payload_kit(
        cotizacion_id="98311",
        secuencia=1,
        sku="X",
        nombre_producto="X",
        cajas_kit=100,
    )
    assert "amount" not in payload


# ETAPA DERIVADA


def test_sin_validar_queda_pendiente():
    assert (
        calcular_dealstage_kit(validado_produccion=False, cajas_disponibles=1534)
        == KIT_DEALSTAGE_PENDIENTE_VALIDACION
    )


def test_validado_con_stock_esta_abierto():
    assert (
        calcular_dealstage_kit(validado_produccion=True, cajas_disponibles=1534)
        == KIT_DEALSTAGE_ABIERTO
    )


def test_validado_sin_stock_esta_agotado():
    assert (
        calcular_dealstage_kit(validado_produccion=True, cajas_disponibles=0)
        == KIT_DEALSTAGE_AGOTADO
    )


def test_agotado_se_reabre_al_liberar_cajas():
    """El recálculo no mira de dónde venía, así que soltar una marca reabre el KIT
    sin necesidad de una transición explícita."""
    assert (
        calcular_dealstage_kit(
            validado_produccion=True,
            cajas_disponibles=250,
            dealstage_actual=KIT_DEALSTAGE_AGOTADO,
        )
        == KIT_DEALSTAGE_ABIERTO
    )


def test_cancelado_es_pegajoso():
    """Cancelar es una decisión manual de Administración: ningún recálculo la
    deshace, ni siquiera con stock disponible y producción validada."""
    assert (
        calcular_dealstage_kit(
            validado_produccion=True,
            cajas_disponibles=1534,
            dealstage_actual=KIT_DEALSTAGE_CANCELADO,
        )
        == KIT_DEALSTAGE_CANCELADO
    )


def test_pendiente_no_es_pegajoso():
    """Lo contrario de cancelado: validar producción saca al KIT de pendiente."""
    assert (
        calcular_dealstage_kit(
            validado_produccion=True,
            cajas_disponibles=1534,
            dealstage_actual=KIT_DEALSTAGE_PENDIENTE_VALIDACION,
        )
        == KIT_DEALSTAGE_ABIERTO
    )
