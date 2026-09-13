"""Tests de `_derivar_descuento_elementos`: el % que SAP necesita para reconstruir
el `precio_total` desde `precio_unitario × cantidad`, y el retoque de céntimos
cuando ese importe no es expresable con 3 decimales de precio y 4 de descuento.

Datos del expediente 64837047300 (ticket de decimales de mlechuga, 09-2026): el
pedido nace correcto (24.144,00), Logística sube el flete 1.200 → 1.250, el
cotizador reprecia la mercancía a 6,586 y SAP se queda con el descuento del alta
(0,4079 % sobre 6,60) → factura 24.145,37, 1,37 € de más.
"""

import pytest

from app.utils.iva_helpers import (
    _derivar_descuento_elementos,
    _importe_expresable,
)


def _elem(eid, cantidad, unitario, total, descuento=None, tipo="fabricante"):
    props = {
        "cantidad": str(cantidad),
        "precio_unitario": str(unitario),
        "precio_total": str(total),
        "tipo_articulo": tipo,
    }
    if descuento is not None:
        props["descuento"] = str(descuento)
    return {"id": eid, "properties": props}


def _reconstruido(elems, cambios):
    """Lo que SAP sumaría con el descuento y el precio_total resultantes."""
    total = 0.0
    for el in elems:
        props = {**el["properties"], **cambios.get(el["id"], {})}
        pct = float(props.get("descuento") or 0)
        total += round(
            float(props["precio_unitario"]) * float(props["cantidad"]) * (1 - pct / 100), 2
        )
    return round(total, 2)


class TestImporteExpresable:
    def test_por_debajo_de_10000_cualquier_centimo_es_alcanzable(self):
        pct, importe = _importe_expresable(6.586, 240, 1580.67)
        assert importe == 1580.67
        assert round(6.586 * 240 * (1 - pct / 100), 2) == 1580.67

    def test_la_linea_que_absorbe_el_residuo_del_contenedor(self):
        pct, importe = _importe_expresable(6.586, 1560, 10230.67)
        assert (pct, importe) == pytest.approx((0.4233, 10230.67), abs=1e-4)

    def test_sobre_10000_hay_centimos_que_no_se_pueden_expresar(self):
        # 2.418 × 5,603 = 13.548,05: el paso del descuento vale 1,35 céntimos
        _, importe = _importe_expresable(5.603, 2418, 13549.20)
        assert abs(importe - 13549.20) == pytest.approx(0.01, abs=1e-6)


class TestDescuento:
    def test_linea_sin_residuo_sale_a_cero(self):
        elems = [_elem("1", 240, 6.586, 1580.64)]
        assert _derivar_descuento_elementos(elems, {}) == {"1": {"descuento": "0.0"}}

    def test_residuo_positivo_da_descuento_negativo(self):
        # 240 × 6,586 = 1.580,64 pero la línea vale 1.580,67
        elems = [_elem("1", 240, 6.586, 1580.67)]
        pct = float(_derivar_descuento_elementos(elems, {})["1"]["descuento"])
        assert pct == pytest.approx(-0.0019, abs=1e-4)

    def test_el_descuento_fosil_del_middleware_se_corrige(self):
        elems = [_elem("1", 1560, 6.586, 10230.67, descuento=0.4079)]
        assert _derivar_descuento_elementos(elems, {}) == {"1": {"descuento": "0.4233"}}

    def test_usa_los_valores_del_mismo_lote_no_los_persistidos(self):
        elems = [_elem("1", 1560, 6.6, 10254.0, descuento=0.4079)]
        updates = {"1": {"id": "1", "properties": {
            "precio_unitario": "6.586", "precio_total": "10230.67",
        }}}
        assert _derivar_descuento_elementos(elems, updates) == {"1": {"descuento": "0.4233"}}

    def test_promo_y_lineas_sin_importe_van_a_cero(self):
        elems = [_elem("promo", 120, 0, 0), _elem("sin_cantidad", 0, 6.586, 0)]
        assert _derivar_descuento_elementos(elems, {}) == {
            "promo": {"descuento": "0.0"}, "sin_cantidad": {"descuento": "0.0"},
        }

    def test_flete_y_seguro_no_llevan_descuento(self):
        elems = [
            _elem("flete", 1, 1250.0, 1250.0, tipo="flete"),
            _elem("seguro", 1, 60.0, 60.0, tipo="seguro"),
        ]
        assert _derivar_descuento_elementos(elems, {}) == {
            "flete": {"descuento": "0.0"}, "seguro": {"descuento": "0.0"},
        }

    def test_no_reescribe_lo_que_ya_esta_al_dia(self):
        elems = [
            _elem("1", 240, 6.586, 1580.64, descuento=0),
            _elem("2", 1560, 6.586, 10230.67, descuento=0.4233),
        ]
        assert _derivar_descuento_elementos(elems, {}) == {}

    def test_un_descuento_vacio_se_puebla_aunque_sea_cero(self):
        elems = [_elem("1", 240, 6.586, 1580.64, descuento="")]
        assert _derivar_descuento_elementos(elems, {}) == {"1": {"descuento": "0.0"}}


class TestCuadreDelContenedor:
    def test_contenedor_completo_reconstruye_el_total_de_hubspot(self):
        # Las 11 líneas del 64837047300-001 tal y como quedaron tras el reprecio.
        lineas = [
            (240, 6.586, 1580.67), (120, 6.586, 790.33), (120, 6.586, 790.33),
            (960, 6.586, 6322.67), (120, 0.0, 0.0), (120, 6.586, 790.33),
            (1, 1250.0, 1250.0), (1560, 6.586, 10230.67), (240, 6.586, 1580.67),
            (120, 6.236, 748.33), (1, 60.0, 60.0),
        ]
        elems = [_elem(str(i), q, pu, pt) for i, (q, pu, pt) in enumerate(lineas)]
        cambios = _derivar_descuento_elementos(elems, {})
        assert _reconstruido(elems, cambios) == 24144.00

    def test_el_centimo_inexpresable_pasa_a_una_linea_de_grano_fino(self):
        # La línea grande (13.548,05 de bruto) no puede valer 13.549,20 exactos.
        elems = [
            _elem("grande", 2418, 5.603, 13549.20),
            _elem("chica", 120, 6.586, 790.33),
        ]
        cambios = _derivar_descuento_elementos(elems, {})
        assert "precio_total" in cambios["grande"]
        assert abs(float(cambios["grande"]["precio_total"]) - 13549.20) == pytest.approx(0.01, abs=1e-6)
        assert _reconstruido(elems, cambios) == round(13549.20 + 790.33, 2)

    def test_el_reparto_no_toca_el_precio_total_de_servicios(self):
        elems = [
            _elem("grande", 2418, 5.603, 13549.20),
            _elem("chica", 120, 6.586, 790.33),
            _elem("flete", 1, 1250.0, 1250.0, tipo="flete"),
        ]
        cambios = _derivar_descuento_elementos(elems, {})
        assert "precio_total" not in cambios.get("flete", {})

    def test_monoproducto_grande_no_baja_el_importe_para_cuadrar(self):
        # 63433830062-001: un solo fabricante de 1.573 cajas. Sin destino para el
        # céntimo, el precio negociado manda y SAP factura 0,01 de menos.
        elems = [
            _elem("unico", 1573, 8.253, 12982.74),
            _elem("flete", 1, 1250.0, 1250.0, tipo="flete"),
            _elem("seguro", 1, 60.0, 60.0, tipo="seguro"),
        ]
        cambios = _derivar_descuento_elementos(elems, {})
        assert "precio_total" not in cambios["unico"]
        assert abs(_reconstruido(elems, cambios) - (12982.74 + 1310)) == pytest.approx(0.01, abs=1e-6)
