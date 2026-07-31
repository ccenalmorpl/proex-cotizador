"""FCA es un nuevo tipo de venta equivalente a EXW: mismo precio por caja y
sin flete/servicios/seguro repercutidos. Lock de esa equivalencia."""

import pytest

from app.utils import iva_helpers
from app.utils.pricing import (
    calcular_precio_exw_desde_incoterm,
    calcular_precio_por_caja_por_incoterm,
)

KWARGS = dict(
    hasta_localidad_destino=1.0,
    hasta_puerto_salida=2.0,
    hasta_puerto_destino=3.0,
    hasta_puerto_destino_localidad=4.0,
    servicios_por_caja=0.5,
    seguro_por_caja=0.25,
    comision_cliente=0.75,
)


@pytest.mark.parametrize("fca", ["FCA", "fca", " Fca "])
def test_fca_precio_por_caja_igual_que_exw(fca):
    exw = calcular_precio_por_caja_por_incoterm(10.0, "EXW", **KWARGS)
    got = calcular_precio_por_caja_por_incoterm(10.0, fca, **KWARGS)
    assert got == exw == 10.0 + KWARGS["comision_cliente"]


def test_fca_exw_desde_incoterm_igual_que_exw():
    exw = calcular_precio_exw_desde_incoterm(10.75, "EXW", **KWARGS)
    fca = calcular_precio_exw_desde_incoterm(10.75, "FCA", **KWARGS)
    assert fca == exw == 10.75 - KWARGS["comision_cliente"]


def test_fca_excluido_de_los_sets_de_iva_como_exw():
    # FCA no repercute flete/servicios/seguro (igual que EXW): debe quedar fuera
    # de todos los sets de gate de IVA por concepto.
    for nombre in (
        "_INCLUYE_FLETE_ORIGEN",
        "_INCLUYE_FLETE_DESTINO",
        "_INCLUYE_FLETE_DESTINO_LOCALIDAD",
        "_INCLUYE_SERVICIOS",
        "_INCLUYE_SEGURO",
    ):
        conjunto = getattr(iva_helpers, nombre)
        assert "FCA" not in conjunto
        assert "EXW" not in conjunto
