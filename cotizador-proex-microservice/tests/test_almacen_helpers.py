"""Tests de `resolve_almacen_para_elemento`: reubicación del almacén de los
elementos no-producto cuando el deal está en San Roque (01). El destino lo decide
el transporte (camión → 12, contenedor → 10). Función pura."""

import pytest

from app.utils.property_helpers import (
    ALMACEN_SAN_ROQUE,
    ALMACEN_EXPORTACION,
    ALMACEN_NACIONAL,
    resolve_almacen_para_elemento,
)


def test_fabricante_conserva_san_roque():
    # El producto (fabricante) nunca sale de San Roque, sea cual sea el transporte.
    assert resolve_almacen_para_elemento("01", "fabricante", "trailer_80") == "01"
    assert resolve_almacen_para_elemento("01", "fabricante", "D40DR") == "01"
    assert resolve_almacen_para_elemento("01", "FABRICANTE", None) == "01"


def test_no_producto_camion_va_a_12():
    for tipo in ("servicio", "flete", "transitario", "comisiones", "intermediario", "seguro"):
        assert resolve_almacen_para_elemento("01", tipo, "trailer_80") == ALMACEN_NACIONAL


def test_no_producto_contenedor_va_a_10():
    for tipo in ("servicio", "flete", "comisiones", "seguro"):
        assert resolve_almacen_para_elemento("01", tipo, "D40DR") == ALMACEN_EXPORTACION


def test_linea_de_contenedor_sin_tipo_articulo_se_reubica():
    # La línea de contenedor (hs_product_type=container) llega sin tipo_articulo.
    assert resolve_almacen_para_elemento("01", None, "trailer_80") == ALMACEN_NACIONAL
    assert resolve_almacen_para_elemento("01", "", "D40DR") == ALMACEN_EXPORTACION


def test_tipo_contenedor_vacio_o_desconocido_defaultea_a_exportacion():
    # Cualquier cosa que no sea camión (incl. vacío o tipo desconocido) → export.
    assert resolve_almacen_para_elemento("01", "servicio", "") == ALMACEN_EXPORTACION
    assert resolve_almacen_para_elemento("01", "servicio", None) == ALMACEN_EXPORTACION
    assert resolve_almacen_para_elemento("01", "servicio", "d20dr") == ALMACEN_EXPORTACION


def test_tipo_contenedor_normaliza():
    for raw in ("TRAILER 80", "trailer_80", " trailer-80 ", "TRAILER80"):
        assert resolve_almacen_para_elemento("01", "servicio", raw) == ALMACEN_NACIONAL


def test_almacen_distinto_de_san_roque_no_se_toca():
    # Solo San Roque dispara la regla; cualquier otro almacén se respeta tal cual.
    for almacen in ("10", "12", "05", "FURGONETA 2"):
        assert resolve_almacen_para_elemento(almacen, "servicio", "trailer_80") == almacen
        assert resolve_almacen_para_elemento(almacen, "fabricante", "D40DR") == almacen


def test_codigo_san_roque_requiere_cero_a_la_izquierda():
    # El valor interno es exactamente "01"; "1" sin cero NO es San Roque.
    assert resolve_almacen_para_elemento("1", "servicio", "trailer_80") == "1"
    assert ALMACEN_SAN_ROQUE == "01"


def test_deal_almacen_vacio_devuelve_tal_cual():
    assert resolve_almacen_para_elemento(None, "servicio", "trailer_80") is None
    assert resolve_almacen_para_elemento("", "servicio", "trailer_80") == ""


def test_san_roque_con_espacios_se_normaliza():
    assert resolve_almacen_para_elemento(" 01 ", "servicio", "trailer_80") == ALMACEN_NACIONAL
