"""Tests de `_derivar_precio_total_fabricantes`: el precio_total de los Elementos
fabricante se corrige para que Σ precio_total(fabricantes) + Σ precio_total(resto
de líneas del contenedor) = Σ final×cajas, el total real que SAP debe reconstruir.

Caso real que motivó el fix (deal SOKAF, 2026-07-30): 1020 cajas a 8,00 CIF +
640 a 7,00 CIF, seguro 60, fletes 1700 → EXW persistidos 6,94/5,94 y SAP sumaba
12.640,40 en vez de 12.640,00.
"""

import pytest

from app.utils.iva_helpers import _derivar_precio_total_fabricantes
from app.utils.pricing import PricingParams

CAJAS_TOTAL = 1660.0
FLETES = 1700.0  # 500 origen + 1200 destino
SEGURO = 60.0


def _pp(incoterm="CIF", cajas=CAJAS_TOTAL, origen=500.0, destino=1200.0, servicios=SEGURO, seguro=SEGURO):
    return PricingParams(
        incoterm=incoterm,
        hasta_localidad_destino=origen / cajas,
        hasta_puerto_salida=origen / cajas,
        hasta_puerto_destino=destino / cajas,
        hasta_puerto_destino_localidad=destino / cajas,
        servicios_por_caja=servicios / cajas,
        seguro_por_caja=seguro / cajas,
        comision_cliente=0.0,
        total_cajas=cajas,
        total_contenedores=1,
        servicios_no_seguro_por_iva_por_caja={},
        seguro_por_iva_por_caja={},
    )


def _linea(lid, price, cajas, ancla=None, nombre=None):
    li = {
        "hs_object_id": lid,
        "name": nombre or f"Producto {lid}",
        "price": str(price),
        "quantity": str(cajas),
        "tipo_iva_repercutido": "EX",
    }
    if ancla is not None:
        li["precio_negociado_tipo_venta"] = str(ancla)
        li["incoterm_precio_negociado"] = "CIF"
    return li


def _elem(eid, lid, precio_total, tipo="fabricante", nombre=None):
    return {"id": eid, "properties": {
        "line_item_id": lid,
        "nombre": nombre or f"Producto {lid}",
        "tipo_articulo": tipo,
        "precio_total": str(precio_total),
    }}


def _escenario_real():
    """Los datos exactos del caso real: EXW persistidos a 3 decimales (6,94/5,94)
    con ancla 8,00/7,00 CIF, más los Elementos de flete y seguro del contenedor."""
    fabricantes = [
        _linea("li-1", "6.94", 1020, ancla=8.0),
        _linea("li-2", "5.94", 640, ancla=7.0),
    ]
    elems = [
        _elem("el-1", "li-1", "7078.8"),   # crudo: 6,94×1020
        _elem("el-2", "li-2", "3801.6"),   # crudo: 5,94×640
        _elem("el-f", "li-f", str(FLETES), tipo="flete", nombre="FLETES"),
        _elem("el-s", "li-s", str(SEGURO), tipo="seguro", nombre="SEGURO"),
    ]
    elem_by_lid = {"li-1": elems[0], "li-2": elems[1]}
    return fabricantes, elems, elem_by_lid


def test_caso_real_suma_clava_el_total_negociado():
    fabricantes, elems, elem_by_lid = _escenario_real()
    upd = _derivar_precio_total_fabricantes(fabricantes, _pp(), elems, elem_by_lid)

    assert upd == {"el-1": "7078.55", "el-2": "3801.45"}
    assert float(upd["el-1"]) + float(upd["el-2"]) + FLETES + SEGURO == pytest.approx(12640.00)


def test_idempotente_si_ya_esta_corregido():
    fabricantes, elems, elem_by_lid = _escenario_real()
    elems[0]["properties"]["precio_total"] = "7078.55"
    elems[1]["properties"]["precio_total"] = "3801.45"
    assert _derivar_precio_total_fabricantes(fabricantes, _pp(), elems, elem_by_lid) == {}


def test_residuo_de_centimos_lo_absorbe_la_linea_con_mas_cajas():
    # 3 líneas a 7,00 CIF con 3120 cajas y gastos 2060: los ideales a céntimos
    # suman 19.779,99 y el target es 19.780,00 → +0,01 a la línea de 1080 cajas.
    pp = _pp(cajas=3120.0, origen=800.0, destino=1200.0)
    fabricantes = [
        _linea("li-1", "6.34", 1000, ancla=7.0),
        _linea("li-2", "6.34", 1080, ancla=7.0),
        _linea("li-3", "6.34", 1040, ancla=7.0),
    ]
    elems = [
        _elem("el-1", "li-1", "6340"),
        _elem("el-2", "li-2", "6847.2"),
        _elem("el-3", "li-3", "6593.6"),
        _elem("el-f", "li-f", "2000", tipo="flete", nombre="FLETES"),
        _elem("el-s", "li-s", "60", tipo="seguro", nombre="SEGURO"),
    ]
    elem_by_lid = {"li-1": elems[0], "li-2": elems[1], "li-3": elems[2]}

    upd = _derivar_precio_total_fabricantes(fabricantes, pp, elems, elem_by_lid)
    total = sum(float(v) for v in upd.values())
    assert total == pytest.approx(21840.00 - 2060.0)
    # la de más cajas (li-2 → el-2) carga con el céntimo
    assert sum(float(v) for k, v in upd.items() if k != "el-2") == pytest.approx(
        round(total - float(upd["el-2"]), 2)
    )


def test_promo_va_a_cero_y_no_absorbe_residuo():
    fabricantes, elems, elem_by_lid = _escenario_real()
    fabricantes.append(_linea("li-p", "0", 50))
    elems.append(_elem("el-p", "li-p", "123.45"))  # crudo desfasado de la promo
    elem_by_lid["li-p"] = elems[-1]

    upd = _derivar_precio_total_fabricantes(fabricantes, _pp(), elems, elem_by_lid)
    assert upd["el-p"] == "0.0"
    assert float(upd["el-1"]) + float(upd["el-2"]) + FLETES + SEGURO == pytest.approx(12640.00)


def test_linea_sin_ancla_mantiene_exw_por_cajas():
    # Sin ancla el final = EXW + gastos, así que el ideal es EXW×cajas: el
    # comportamiento histórico, sin residuo que repartir.
    pp = _pp(cajas=100.0, origen=40.0, destino=25.0, servicios=10.0, seguro=5.0)
    fabricantes = [_linea("li-1", "7.23", 100)]
    elems = [
        _elem("el-1", "li-1", "0"),
        _elem("el-f", "li-f", "65", tipo="flete", nombre="FLETES"),
        _elem("el-s", "li-s", "10", tipo="servicio", nombre="SERVICIO"),
    ]
    upd = _derivar_precio_total_fabricantes(fabricantes, pp, elems, {"li-1": elems[0]})
    assert upd == {"el-1": "723.0"}


def test_lid_cruzado_no_se_toca():
    fabricantes, elems, elem_by_lid = _escenario_real()
    elems[0]["properties"]["nombre"] = "OTRO ARTÍCULO"
    upd = _derivar_precio_total_fabricantes(fabricantes, _pp(), elems, elem_by_lid)
    assert "el-1" not in upd


def test_sin_linea_de_gastos_el_fabricante_absorbe_todo():
    # Contenedor sin Elementos de servicio/flete (anomalía legacy): para que SAP
    # cuadre, el total negociado completo tiene que vivir en las líneas fabricante.
    fabricantes = [_linea("li-1", "6.94", 1020, ancla=8.0)]
    elems = [_elem("el-1", "li-1", "7078.8")]
    pp = _pp(cajas=1020.0, origen=500.0, destino=1200.0)
    upd = _derivar_precio_total_fabricantes(fabricantes, pp, elems, {"li-1": elems[0]})
    assert upd == {"el-1": "8160.0"}
