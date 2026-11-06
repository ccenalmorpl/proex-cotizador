"""Tests del cálculo del precio del contenedor con IVA por concepto
(`_compute_price_from_fabricantes`). Función pura: se mockea solo la resolución
de porcentajes de IVA de HubDB."""

import pytest

from app.shared import hubdb_client
from app.utils import iva_helpers
from app.utils.iva_helpers import _compute_price_from_fabricantes
from app.utils.pricing import PricingParams

IVA_MAP = {
    ("R21", "NACIONAL"): (21.0, 5.2),
    ("R10", "NACIONAL"): (10.0, 1.4),
    ("EX", "NACIONAL"): (0.0, 0.0),
    ("EI21", "EUROPA"): (0.0, 0.0),
}


@pytest.fixture(autouse=True)
def mock_iva(monkeypatch):
    def fake(code, residencia):
        key = ((code or "").strip().upper(), (residencia or "").strip().upper())
        return IVA_MAP.get(key, (None, None))

    monkeypatch.setattr(hubdb_client, "get_porcentajes_iva_repercutido", fake)


def _params(incoterm, **overrides):
    base = dict(
        incoterm=incoterm,
        hasta_localidad_destino=0.5,
        hasta_puerto_salida=0.5,
        hasta_puerto_destino=0.5,
        servicios_por_caja=2.0,
        seguro_por_caja=1.0,
        comision_cliente=0.0,
        total_cajas=100.0,
        total_contenedores=1,
        tipo_iva_fletes="R21",
        tipo_iva_comision="R21",
        servicios_no_seguro_por_iva_por_caja={"R21": 1.0},
        seguro_por_iva_por_caja={"EX": 1.0},
    )
    base.update(overrides)
    return PricingParams(**base)


PRODUCTO = {"price": "10", "quantity": "100", "tipo_iva_repercutido": "R21"}


def test_cif_seguro_exento_no_tributa_al_21():
    # Base CIF = 10 + 0.5 + 2 + 0.5 = 13 → 1300.
    # IVA: prod 210 + flete origen 10.5 + flete destino 10.5 + serv 21 + seguro 0.
    res = _compute_price_from_fabricantes([PRODUCTO], None, _params("CIF"))
    assert res == pytest.approx(1552.0)


def test_cif_bug_anterior_delta_es_iva_del_seguro():
    # El bug aplicaba 21% a toda la base (1300 * 1.21 = 1573). El delta debe ser
    # exactamente el IVA que el seguro NO debe tributar: base_seguro(100) * 21% = 21.
    res = _compute_price_from_fabricantes([PRODUCTO], None, _params("CIF"))
    assert 1573.0 - res == pytest.approx(21.0)


def test_fob_seguro_excluido_de_base_y_de_iva():
    # Base FOB = 10 + 0.5 + 2 - 1 = 11.5 → 1150.
    # IVA: prod 210 + flete origen 10.5 + serv 21 (sin flete destino, sin seguro).
    res = _compute_price_from_fabricantes([PRODUCTO], None, _params("FOB"))
    assert res == pytest.approx(1391.5)


def test_exw_solo_producto_y_comision():
    # Base EXW = 10 + 0.3 (comisión) = 10.3 → 1030. IVA: prod 210 + comisión 6.3.
    params = _params("EXW", comision_cliente=0.3)
    res = _compute_price_from_fabricantes([PRODUCTO], None, params)
    assert res == pytest.approx(1246.3)


def test_linea_promocional_no_aporta_nada():
    promo = {"price": "0", "quantity": "50", "tipo_iva_repercutido": "R21"}
    solo_normal = _compute_price_from_fabricantes([PRODUCTO], None, _params("CIF"))
    con_promo = _compute_price_from_fabricantes([PRODUCTO, promo], None, _params("CIF"))
    assert con_promo == pytest.approx(solo_normal)


def test_recargo_equivalencia_suma_a_cada_concepto():
    # equ=si → R21 efectivo 26.2%; EX sigue 0%.
    # IVA: prod 262 + flete origen 13.1 + flete destino 13.1 + serv 26.2 + seguro 0.
    company = {"equ": "si"}
    res = _compute_price_from_fabricantes([PRODUCTO], company, _params("CIF"))
    assert res == pytest.approx(1300.0 + 314.4)


def test_residencia_europea_usa_su_porcentaje():
    # Producto EI21 exento en EUROPA → IVA producto 0. EXW sin otros conceptos.
    company = {"residennum": "EUROPA"}
    producto_eu = {"price": "10", "quantity": "100", "tipo_iva_repercutido": "EI21"}
    params = _params("EXW")
    res = _compute_price_from_fabricantes([producto_eu], company, params)
    assert res == pytest.approx(1000.0)


def test_cajas_cero_no_cuenta():
    sin_cajas = {"price": "10", "quantity": "0", "tipo_iva_repercutido": "R21"}
    res = _compute_price_from_fabricantes([sin_cajas], None, _params("CIF"))
    assert res == pytest.approx(0.0)


def test_flete_terrestre_con_iva_propio_por_tramo():
    # CIF: tramo origen (0.5/caja → 50) al 10%, destino (50) al 21%.
    # Base 1300; IVA: prod 210 + origen 5 + destino 10.5 + serv 21 + seguro 0.
    res = _compute_price_from_fabricantes(
        [PRODUCTO], None, _params("CIF", tipo_iva_fletes_terrestre="R10")
    )
    assert res == pytest.approx(1546.5)


def test_flete_terrestre_vacio_cae_al_iva_maritimo():
    # INVARIANTE: con la prop nueva vacía el resultado es EXACTAMENTE el actual
    # (fallback a tipo_iva_fletes, nunca al default por residencia).
    res = _compute_price_from_fabricantes(
        [PRODUCTO], None, _params("CIF", tipo_iva_fletes_terrestre="")
    )
    assert res == pytest.approx(1552.0)


def test_dap_terrestre_grava_localidad_con_iva_terrestre():
    # DAP_TERRESTRE: base 10 + 0.5 (hasta_localidad_destino, sale de precioOrigen)
    # + 2 = 12.5 → 1250. IVA: prod 210 + origen 50*10% = 5 + serv 21 + seguro 0.
    res = _compute_price_from_fabricantes(
        [PRODUCTO], None, _params("DAP_TERRESTRE", tipo_iva_fletes_terrestre="R10")
    )
    assert res == pytest.approx(1250.0 + 236.0)


def test_dap_maritimo_grava_el_destino_una_vez_con_iva_maritimo():
    # DAP_MARITIMO: base 10 + 0.5 + 2 + 0.5 = 13 → 1300. IVA: prod 210 +
    # origen 50*10% = 5 + destino 10.5 + serv 21.
    res = _compute_price_from_fabricantes(
        [PRODUCTO], None, _params("DAP_MARITIMO", tipo_iva_fletes_terrestre="R10")
    )
    assert res == pytest.approx(1300.0 + 246.5)
