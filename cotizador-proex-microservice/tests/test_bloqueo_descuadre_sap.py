"""Un contenedor cuyos line items y Elementos ya no cuadran no se escribe ni avisa a SAP.

Caso real (64731186508, DISTRIBUTION PLUS 20x20, 02/10/2026): los line items
llevaban desde el 14/09 entre 2.160 y 3.720 cajas por contenedor y los Elementos
las 3.120 buenas. Un cambio de transitario recalculó, el cuadre volcó la
diferencia en una sola línea (−41,70 %, +259 %, importes negativos), se selló y
SAP llegó a grabar cinco pedidos con esos importes.
"""

import logging
from types import SimpleNamespace

import pytest

from app.routes import deals as deals_routes
from app.shared import hubdb_client
from app.utils import iva_helpers
from app.utils.iva_helpers import (
    _motivo_bloqueo_sap,
    contenedores_a_sellar,
    recalcular_contenedores,
)
from app.utils.pricing import PricingParams

FLETE = 2825.0
SEGURO = 60.0
ANCLA_CIF = 6.73


@pytest.fixture(autouse=True)
def mock_iva(monkeypatch):
    monkeypatch.setattr(hubdb_client, "get_porcentajes_iva_repercutido", lambda code, res: (0.0, 0.0))


def _li_fab(producto, cajas, cont="C-001"):
    return {"name": f"{cont} - {producto}", "quantity": str(cajas), "tipo_articulo": "fabricante"}


def _li_gasto(nombre, tipo="Flete", cont="C-001"):
    return {"name": f"{cont} - {nombre}", "quantity": "1", "hs_product_type": tipo, "numero_container": cont}


def _el(eid, nombre, cajas=1, tipo="fabricante", precio_total=100.0, cont="C-001"):
    return {"id": eid, "properties": {
        "nombre": f"{cont} - {nombre}",
        "tipo_articulo": tipo,
        "cantidad": str(cajas),
        "precio_total": str(precio_total),
    }}


GASTOS_LI = [_li_gasto("FLETES"), _li_gasto("SEGURO MERCANCÍA", tipo="service")]
GASTOS_EL = [_el("ef", "FLETES", tipo="flete", precio_total=FLETE),
             _el("es", "SEGURO MERCANCÍA", tipo="seguro", precio_total=SEGURO)]


def test_contenedor_coherente_pasa():
    fab = [_li_fab("Mora", 600), _li_fab("Fresa", 600)]
    els = [_el("1", "Mora", 600), _el("2", "Fresa", 600), *GASTOS_EL]
    assert _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], {}) is None


def test_promo_con_descuento_alto_pero_coherente_pasa():
    # 65076637078 (DAP terrestre, 5+1 de 120 cajas): el EXW reparte los gastos entre
    # las 720 cajas y el importe solo entre las 600 de pago; los 98,65 € caen en una
    # línea al 11,78 %. Total correcto y el middleware lo admite: no se bloquea.
    fab = [_li_fab(f"P{i}", 120) for i in range(6)]
    els = [_el(str(i), f"P{i}", 120, precio_total=837.33) for i in range(6)]
    els[5]["properties"].update({"precio_total": "738.68", "descuento": "11.7847"})
    assert _motivo_bloqueo_sap(fab, GASTOS_LI, [*els, *GASTOS_EL], [], {}) is None


def test_cajas_distintas_bloquea():
    fab = [_li_fab("Mora", 600), _li_fab("Fresa", 600), _li_fab("Platano", 120)]
    els = [_el("1", "Mora", 600), _el("2", "Fresa", 600), *GASTOS_EL]
    motivo = _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], {})
    assert motivo == "cajas de artículo: 1320 en line items y 1200 en Elementos"


def test_linea_de_articulo_sin_elemento_bloquea():
    sin_resolver = [{"lid": "9", "nombre": "C-001 - Mora", "motivo": "nombre_ambiguo"}]
    motivo = _motivo_bloqueo_sap([], [], [], sin_resolver, {})
    assert motivo == "1 líneas de artículo sin Elemento (nombre_ambiguo)"


def test_flete_huerfano_bloquea():
    # 65027508906 (29/09): un FLETES archivado dejó vivo su Elemento y el cuadre
    # restó 1.300 € de más a la FRESA (descuento del 17,93 %).
    fab = [_li_fab("Fresa", 1080)]
    els = [_el("1", "Fresa", 1080), *GASTOS_EL, _el("eh", "FLETES", tipo="flete", precio_total=1300)]
    motivo = _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], {})
    assert motivo.startswith("gastos sin pareja: Elementos de más ['C-001 - FLETES']")


def test_linea_de_gasto_sin_elemento_bloquea():
    fab = [_li_fab("Fresa", 1080)]
    els = [_el("1", "Fresa", 1080), GASTOS_EL[0]]
    motivo = _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], {})
    assert "líneas sin Elemento ['C-001 - SEGURO MERCANCÍA']" in motivo


def test_importe_negativo_bloquea_salvo_que_el_lote_lo_corrija():
    fab = [_li_fab("Fresa", 600)]
    els = [_el("1", "Fresa", 600, precio_total=-1597.69), *GASTOS_EL]
    assert "precio_total -1597.69" in _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], {})

    arreglo = {"1": {"id": "1", "properties": {"precio_total": "3483.19"}}}
    assert _motivo_bloqueo_sap(fab, GASTOS_LI, els, [], arreglo) is None


def test_contenedores_a_sellar_quita_los_bloqueados():
    resultados = {"A": {"price": 1.0}, "B": {"bloqueado": "x"}, "C": None}
    assert contenedores_a_sellar(["A", "B", "C"], resultados) == ["A", "C"]
    assert contenedores_a_sellar(["A", "B"], {}) == ["A", "B"]
    assert contenedores_a_sellar(["A"], None) == ["A"]


def _pp(cajas):
    return PricingParams(
        incoterm="CIF",
        hasta_localidad_destino=FLETE / cajas,
        hasta_puerto_salida=FLETE / cajas,
        hasta_puerto_destino=0.0,
        hasta_puerto_destino_localidad=0.0,
        servicios_por_caja=SEGURO / cajas,
        seguro_por_caja=SEGURO / cajas,
        comision_cliente=0.0,
        total_cajas=cajas,
        total_contenedores=1,
        servicios_no_seguro_por_iva_por_caja={},
        seguro_por_iva_por_caja={},
    )


def _linea(lid, cont, producto, cajas):
    return {
        "hs_object_id": lid,
        "name": f"{cont} - {producto}",
        "quantity": str(cajas),
        "price": "5.805",
        "precio_de_coste": "5.0",
        "precio_negociado_tipo_venta": str(ANCLA_CIF),
        "incoterm_precio_negociado": "CIF",
        "numero_container": cont,
        "hs_product_type": "inventory",
        "tipo_articulo": "fabricante",
        "tipo_iva_repercutido": "EX",
        "margen_unitario": "0.161",
    }


def _linea_gasto(lid, cont, nombre, precio, tipo):
    return {"hs_object_id": lid, "name": f"{cont} - {nombre}", "quantity": "1", "price": str(precio),
            "numero_container": cont, "hs_product_type": tipo, "tipo_articulo": "transitario"}


def _elemento(eid, lid, cont, producto, cajas, tipo="fabricante", precio_total=None):
    return {"id": eid, "properties": {
        "line_item_id": lid,
        "nombre": f"{cont} - {producto}",
        "nombre_contenedor": cont,
        "tipo_articulo": tipo,
        "cantidad": str(cajas),
        "precio_unitario": "5.805" if tipo == "fabricante" else str(precio_total),
        "precio_total": str(precio_total if precio_total is not None else round(5.805 * cajas, 2)),
        "descuento": "0",
        "margen_unitario": "0.161",
    }}


def test_recalcular_no_escribe_ni_sella_el_contenedor_descuadrado(monkeypatch, caplog):
    # A es sano: 3.120 cajas en line items y en Elementos. B arrastra una línea
    # de 600 cajas que no existe en los Elementos, como los contenedores de DP.
    line_items = [
        _linea("la1", "T-001", "Mora", 1560),
        _linea("la2", "T-001", "Fresa", 1560),
        _linea_gasto("laf", "T-001", "FLETES", FLETE, "Flete"),
        _linea_gasto("las", "T-001", "SEGURO", SEGURO, "service"),
        _linea("lb1", "T-002", "Mora", 1560),
        _linea("lb2", "T-002", "Fresa", 1560),
        _linea("lb3", "T-002", "Sandia", 600),
        _linea_gasto("lbf", "T-002", "FLETES", FLETE, "Flete"),
        _linea_gasto("lbs", "T-002", "SEGURO", SEGURO, "service"),
    ]
    elementos = [
        _elemento("ea1", "la1", "T-001", "Mora", 1560),
        _elemento("ea2", "la2", "T-001", "Fresa", 1560),
        _elemento("eaf", "laf", "T-001", "FLETES", 1, tipo="flete", precio_total=FLETE),
        _elemento("eas", "las", "T-001", "SEGURO", 1, tipo="seguro", precio_total=SEGURO),
        _elemento("eb1", "lb1", "T-002", "Mora", 1560),
        _elemento("eb2", "lb2", "T-002", "Fresa", 1560),
        _elemento("ebf", "lbf", "T-002", "FLETES", 1, tipo="flete", precio_total=FLETE),
        _elemento("ebs", "lbs", "T-002", "SEGURO", 1, tipo="seguro", precio_total=SEGURO),
    ]
    contenedores = {
        "A": {"nombre": "T-001", "importe_total_cobrado": "0"},
        "B": {"nombre": "T-002", "importe_total_cobrado": "0"},
    }

    escrituras = []
    lineas_escritas = []
    monkeypatch.setattr(deals_routes, "_get_associated_ids", lambda deal_id, to: [l["hs_object_id"] for l in line_items])
    monkeypatch.setattr(iva_helpers, "_batch_read_line_items_superset", lambda client, ids: line_items)
    monkeypatch.setattr(
        iva_helpers, "build_deal_pricing_context",
        lambda client, deal_id, line_items_precached=None: SimpleNamespace(for_container=lambda nombre, cajas: _pp(cajas)),
    )
    monkeypatch.setattr(iva_helpers, "_batch_read_contenedores_props", lambda client, ids: contenedores)
    monkeypatch.setattr(iva_helpers, "get_elementos_by_deal", lambda client, deal_id, properties=None: elementos)
    monkeypatch.setattr(
        iva_helpers, "batch_update_custom_objects",
        lambda client, object_type, updates: escrituras.append((object_type, updates)),
    )
    client = SimpleNamespace(crm=SimpleNamespace(line_items=SimpleNamespace(batch_api=SimpleNamespace(
        update=lambda batch_input_simple_public_object_batch_input: lineas_escritas.extend(
            batch_input_simple_public_object_batch_input["inputs"]
        ),
    ))))

    with caplog.at_level(logging.ERROR, logger="app.utils.iva_helpers"):
        resultados = recalcular_contenedores(client, "deal", ["A", "B"])

    assert "bloqueado" in resultados["B"]
    assert resultados["A"]["price"] == pytest.approx(ANCLA_CIF * 3120, abs=0.01)
    assert any("BLOQUEADO" in r.message and "T-002" in r.message for r in caplog.records)

    ids_escritos = {str(u["id"]) for _, updates in escrituras for u in updates}
    assert {"ea1", "ea2"} & ids_escritos
    assert not {"eb1", "eb2", "ebf", "ebs", "B"} & ids_escritos
    assert not {"lb1", "lb2", "lb3"} & {str(u["id"]) for u in lineas_escritas}

    assert contenedores_a_sellar(["A", "B"], resultados) == ["A"]
