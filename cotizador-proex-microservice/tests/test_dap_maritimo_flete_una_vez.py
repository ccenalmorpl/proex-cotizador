"""Ticket de Elisa (05/11/2026), negocio 67562859370: con 525 € de flete a puerto,
2.955 € de marítimo y 60 € de seguro sobre 2.592 cajas a 5,112 € de coste, el
cotizador no dejaba bajar de 7,618 €/caja porque contaba el marítimo dos veces."""

import pytest

from app.utils.pricing import (
    DealPricingContext,
    _pricing_kwargs,
    calcular_precio_por_caja_por_incoterm,
    derivar_exw_desde_final_anclado,
)

CAJAS = 2592.0
COSTE = 5.112


def _pp():
    ctx = DealPricingContext(
        incoterm="DAP_MARITIMO",
        precio_origen=525.0,
        precio_destino=2955.0,
        repercutir=False,
        tiene_intermediario=False,
        tipo_comision="",
        importe_comision=0.0,
        num_contenedores=1,
        tipo_iva_fletes="EX",
        tipo_iva_comision="",
        servicios_por_contenedor={"67562859370-001": 60.0},
        seguro_por_contenedor={"67562859370-001": 60.0},
        servicios_buckets_por_contenedor={},
        seguro_buckets_por_contenedor={"67562859370-001": {"EX": 60.0}},
    )
    return ctx.for_container("67562859370-001", CAJAS)


def test_precio_minimo_es_coste_mas_gastos_reales():
    minimo = calcular_precio_por_caja_por_incoterm(COSTE, "DAP_MARITIMO", **_pricing_kwargs(_pp()))
    assert round(minimo, 3) == 6.478


def test_negociado_a_6_95_deja_1224_de_margen():
    res = derivar_exw_desde_final_anclado(6.95, COSTE, _pp())

    assert res["clamped"] is False
    assert res["final_efectivo"] == 6.95
    assert (res["exw"] - COSTE) * CAJAS == pytest.approx(1224.10, abs=0.01)
