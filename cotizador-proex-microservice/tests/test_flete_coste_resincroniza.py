"""El Elemento de flete es pass-through del transitario: al cambiar el precio del
tramo, `precio_de_coste` tiene que seguirlo. Cuando no lo hacía, el pedido de
compra salía por el importe viejo (deal 63666631559: venta 1350, coste 1400)."""

from app.routes.deals import _diff_flete_elemento


def _cur(precio, coste=None, cardcode="P0690", line_item_id="57812192300", **props):
    base = {
        "precio_unitario": str(precio),
        "precio_total": str(precio),
        "precio_de_coste": str(coste if coste is not None else precio),
        "cardcode": cardcode,
        "tipo_iva_repercutido": "EX",
        "tipo_iva_soportado": "S21",
        "line_item_id": line_item_id,
        "almacen": "12",
    }
    base.update(props)
    return base


def test_bajada_de_precio_arrastra_el_coste():
    updates = _diff_flete_elemento(
        _cur(1400.0), 1350.0, "P0690", "EX", "S21", "57812192300", "12",
    )
    assert updates == {
        "precio_unitario": 1350.0,
        "precio_total": 1350.0,
        "precio_de_coste": 1350.0,
    }


def test_repara_el_coste_congelado_sin_tocar_lo_demas():
    # Estado real del Elemento 60004265743 tras el pase del 14/08: venta ya a 1350
    # y coste fósil en 1400 (margen_total −50,00 y OPOR al transitario por 1400).
    updates = _diff_flete_elemento(
        _cur(1350.0, coste=1400.0), 1350.0, "P0690", "EX", "S21", "57812192300", "12",
    )
    assert updates == {"precio_de_coste": 1350.0}


def test_sin_cambios_no_emite_nada():
    cur = _cur(849.0, cardcode="P0518", line_item_id="57812192297")
    updates = _diff_flete_elemento(
        cur, 849.0, "P0518", "EX", "S21", "57812192297", "12",
    )
    assert updates == {}


def test_subida_de_precio_tambien_arrastra_el_coste():
    cur = _cur(800.0, cardcode="P0518", line_item_id="57812192297")
    updates = _diff_flete_elemento(
        cur, 849.0, "P0518", "EX", "S21", "57812192297", "12",
    )
    assert updates["precio_de_coste"] == 849.0
    assert updates["precio_unitario"] == 849.0
    assert updates["precio_total"] == 849.0


def test_coste_vacio_se_rellena():
    updates = _diff_flete_elemento(
        _cur(1350.0, precio_de_coste=""), 1350.0, "P0690", "EX", "S21", "57812192300", "12",
    )
    assert updates == {"precio_de_coste": 1350.0}


def test_cambio_de_transitario_arrastra_cardcode_e_ivas():
    updates = _diff_flete_elemento(
        _cur(1400.0), 1350.0, "P0999", "R21", "S10", "57812192300", "12",
    )
    assert updates == {
        "precio_unitario": 1350.0,
        "precio_total": 1350.0,
        "precio_de_coste": 1350.0,
        "cardcode": "P0999",
        "tipo_iva_repercutido": "R21",
        "tipo_iva_soportado": "S10",
    }


def test_almacen_vacio_no_pisa_el_persistido():
    updates = _diff_flete_elemento(
        _cur(1350.0), 1350.0, "P0690", "EX", "S21", "57812192300", None,
    )
    assert "almacen" not in updates


def test_reemparejamiento_por_nombre_actualiza_line_item_id():
    updates = _diff_flete_elemento(
        _cur(1350.0, line_item_id="99999"), 1350.0, "P0690", "EX", "S21", "57812192300", "12",
    )
    assert updates == {"line_item_id": "57812192300"}
