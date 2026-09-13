"""Ticket de mbernal (07/09/2026, deal 63434201662 NEQ INVESTMENTS): el flete subió
70 € y el margen del panel comercial no bajó, se quedó clavado en 1.387,48 €.

La subida no se repercute al cliente: el CIF del contenedor sigue en 15.112,50 € y
el cotizador lo absorbe bajando el EXW de la mercancía en esos mismos 70 €. Lo que
debe bajar es el margen. Con el `precio_de_coste` del flete congelado en el importe
del alta, el Elemento de flete aparentaba +70 € de margen que compensaban exactamente
la rebaja del EXW, y el total no se movía."""

import pytest

from app.routes.deals import _diff_flete_elemento

CAJAS = 2418
COSTE_CAJA = 4.512
FLETE_ALTA = 2625.0
FLETE_NUEVO = 2695.0
CIF_CONTENEDOR = 15112.5
# seguro 60 + mensajería 80 + certificado de origen 50, todos a margen 0
SERVICIOS = 190.0


def _margen(precio_total, precio_de_coste, cantidad=1):
    """Réplica de la propiedad calculada `margen_total` del Elemento en HubSpot."""
    return round(precio_total - precio_de_coste * cantidad, 3)


def _margen_contenedor(flete_venta, flete_coste):
    # El EXW de la mercancía es el resto: el precio al cliente está negociado y no
    # cambia, así que lo que sube el flete lo pierde la mercancía.
    exw_total = CIF_CONTENEDOR - flete_venta - SERVICIOS
    return round(
        _margen(exw_total, COSTE_CAJA, CAJAS) + _margen(flete_venta, flete_coste), 3
    )


def test_margen_de_partida_es_el_del_alta():
    assert _margen_contenedor(FLETE_ALTA, FLETE_ALTA) == 1387.484


def test_subida_de_flete_no_repercutida_baja_el_margen():
    coste = _diff_flete_elemento(
        {"precio_unitario": str(FLETE_ALTA), "precio_total": str(FLETE_ALTA),
         "precio_de_coste": str(FLETE_ALTA), "cardcode": "P0557",
         "tipo_iva_repercutido": "EX", "tipo_iva_soportado": "SEXN",
         "line_item_id": "57748780345", "almacen": "10"},
        FLETE_NUEVO, "P0404", "EX", "SEXN", "57748780345", "10",
    )["precio_de_coste"]

    assert _margen_contenedor(FLETE_NUEVO, coste) == pytest.approx(1317.484)
    assert _margen_contenedor(FLETE_ALTA, FLETE_ALTA) - _margen_contenedor(
        FLETE_NUEVO, coste
    ) == pytest.approx(70.0)


def test_el_coste_fosil_borraba_la_bajada_del_margen():
    # Lo que veía Administración: el margen del flete (+70) tapaba el de la mercancía (−70).
    assert _margen(FLETE_NUEVO, FLETE_ALTA) == 70.0
    assert _margen_contenedor(FLETE_NUEVO, FLETE_ALTA) == _margen_contenedor(
        FLETE_ALTA, FLETE_ALTA
    )


def test_el_cliente_paga_lo_mismo_antes_y_despues():
    exw_antes = CIF_CONTENEDOR - FLETE_ALTA - SERVICIOS
    exw_despues = CIF_CONTENEDOR - FLETE_NUEVO - SERVICIOS
    assert exw_antes + FLETE_ALTA + SERVICIOS == exw_despues + FLETE_NUEVO + SERVICIOS
    assert round(exw_antes - exw_despues, 2) == 70.0
