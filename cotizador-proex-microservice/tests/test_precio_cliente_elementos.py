"""`precio_unitario_cliente` y `total_linea_cliente` de los Elementos: el precio
por caja que ve el cliente (mercancía + gastos repartidos) y su importe de línea.
SAP los lee como UDF de línea para mandar pedido y factura por EDI (petición de
Gonzalo, 14/10/2026), así que tienen que cuadrar con los precio_total que suma SAP.
"""

import logging
from types import SimpleNamespace

import pytest

from app.routes import deals as deals_routes
from app.shared import hubdb_client
from app.utils import iva_helpers
from app.utils.iva_helpers import (
    PROPS_PRECIO_CLIENTE,
    _derivar_precio_cliente_elementos,
    recalcular_contenedores,
)
from app.utils.pricing import PricingParams


def _pp(incoterm="CIF", cajas=1660.0, origen=500.0, destino=1200.0, servicios=60.0, seguro=60.0):
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
    )


def _linea(lid, price, cajas, ancla=None, incoterm_ancla="CIF", nombre=None):
    li = {
        "hs_object_id": lid,
        "name": nombre or f"Producto {lid}",
        "price": str(price),
        "quantity": str(cajas),
    }
    if ancla is not None:
        li["precio_negociado_tipo_venta"] = str(ancla)
        li["incoterm_precio_negociado"] = incoterm_ancla
    return li


def _elem(eid, lid, cajas, tipo="fabricante", nombre=None, **props):
    return {"id": eid, "properties": {
        "line_item_id": lid,
        "nombre": nombre or f"Producto {lid}",
        "tipo_articulo": tipo,
        "cantidad": str(cajas),
        **props,
    }}


def _sokaf():
    """Caso real SOKAF (30/07): 1.020 cajas a 8,00 y 640 a 7,00 CIF con 1.700 de
    fletes y 60 de seguro. SAP suma 12.640,00 entre las cuatro líneas."""
    fabricantes = [_linea("li-1", "6.94", 1020, ancla=8.0), _linea("li-2", "5.94", 640, ancla=7.0)]
    elems = [
        _elem("el-1", "li-1", 1020),
        _elem("el-2", "li-2", 640),
        _elem("el-f", "li-f", 1, tipo="flete", nombre="FLETES"),
        _elem("el-s", "li-s", 1, tipo="seguro", nombre="SEGURO"),
    ]
    return fabricantes, elems, {"li-1": elems[0], "li-2": elems[1]}


def test_lineas_ancladas_llevan_el_precio_negociado():
    fabricantes, elems, by_lid = _sokaf()

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, by_lid)

    assert upd == {
        "el-1": {"precio_unitario_cliente": "8.0", "total_linea_cliente": "8160.0"},
        "el-2": {"precio_unitario_cliente": "7.0", "total_linea_cliente": "4480.0"},
    }


def test_promo_escribe_cero_aunque_la_prop_este_vacia():
    fabricantes, elems, by_lid = _sokaf()
    fabricantes.append(_linea("li-p", "0", 50))
    elems.append(_elem("el-p", "li-p", 50))
    by_lid["li-p"] = elems[-1]

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, by_lid)

    assert upd["el-p"] == {"precio_unitario_cliente": "0.0", "total_linea_cliente": "0.0"}


def test_price_cero_con_ancla_vigente_no_es_promo():
    fabricantes = [_linea("li-1", "0", 1020, ancla=8.0)]
    elems = [_elem("el-1", "li-1", 1020)]

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, {"li-1": elems[0]})

    assert upd["el-1"]["precio_unitario_cliente"] == "8.0"


def test_linea_sin_ancla_es_exw_mas_gastos_por_las_cajas_del_elemento():
    # Gastos CIF por caja: 0,40 + 0,25 + 0,10 = 0,75. El total usa las cajas del
    # Elemento, que son las que lleva la línea de SAP.
    pp = _pp(cajas=100.0, origen=40.0, destino=25.0, servicios=10.0, seguro=5.0)
    fabricantes = [_linea("li-1", "7.23", 100)]
    elems = [_elem("el-1", "li-1", 90)]

    upd = _derivar_precio_cliente_elementos(fabricantes, pp, elems, {"li-1": elems[0]})

    assert upd["el-1"] == {"precio_unitario_cliente": "7.98", "total_linea_cliente": "718.2"}


def test_empate_de_redondeo_guarda_el_ancla_y_no_el_recompuesto():
    # Gastos por caja de 4 decimales acabados en 5: el EXW persistido pierde medio
    # milésimo y recomponer EXW + gastos (lo que hace hoy la factura) da 10,198.
    pp = _pp(cajas=800.0, origen=1400.0, destino=1200.0, servicios=50.0, seguro=0.0)
    gastos = 2650.0 / 800.0
    assert round(round(10.199 - gastos, 3) + gastos, 3) == 10.198

    fabricantes = [_linea("li-1", "6.886", 800, ancla=10.199)]
    elems = [_elem("el-1", "li-1", 800)]

    upd = _derivar_precio_cliente_elementos(fabricantes, pp, elems, {"li-1": elems[0]})

    assert upd["el-1"] == {"precio_unitario_cliente": "10.199", "total_linea_cliente": "8159.2"}


def test_ancla_de_otro_incoterm_usa_exw_mas_gastos_del_nuevo():
    fabricantes = [_linea("li-1", "7.10", 1020, ancla=8.0, incoterm_ancla="EXW")]
    elems = [_elem("el-1", "li-1", 1020)]

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, {"li-1": elems[0]})

    assert upd["el-1"] == {"precio_unitario_cliente": "8.16", "total_linea_cliente": "8323.2"}


def test_dap_maritimo_cuenta_el_destino_dos_veces_como_sap_y_la_proforma():
    pp = _pp(incoterm="DAP_MARITIMO", cajas=1000.0, origen=900.0, destino=1500.0, servicios=0.0, seguro=0.0)
    fabricantes = [_linea("li-1", "5.0", 1000)]
    elems = [_elem("el-1", "li-1", 1000)]

    upd = _derivar_precio_cliente_elementos(fabricantes, pp, elems, {"li-1": elems[0]})

    assert upd["el-1"]["precio_unitario_cliente"] == "8.9"


def test_ancla_que_no_cubre_coste_mas_gastos_sigue_al_cotizador_y_a_sap():
    # La proforma imprime el ancla (6,95); cotizador y SAP suben a coste + gastos.
    pp = _pp(cajas=1000.0, origen=300.0, destino=150.0, servicios=50.0, seguro=50.0)
    li = _linea("li-1", "6.5", 1000, ancla=6.95)
    li["precio_de_coste"] = "6.5"
    elems = [_elem("el-1", "li-1", 1000)]

    upd = _derivar_precio_cliente_elementos([li], pp, elems, {"li-1": elems[0]})

    assert upd["el-1"]["precio_unitario_cliente"] == "7.0"


def test_idempotente_con_el_formato_que_devuelve_hubspot():
    fabricantes, elems, by_lid = _sokaf()
    elems[0]["properties"].update({"precio_unitario_cliente": "8", "total_linea_cliente": "8160"})
    elems[1]["properties"].update({"precio_unitario_cliente": "7.000", "total_linea_cliente": "4480.00"})

    assert _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, by_lid) == {}


def test_los_gastos_quedan_en_blanco():
    fabricantes, elems, by_lid = _sokaf()
    elems[2]["properties"].update({"precio_unitario_cliente": "1.0", "total_linea_cliente": "1.0"})

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, by_lid)

    assert upd["el-f"] == {"precio_unitario_cliente": "", "total_linea_cliente": ""}
    assert "el-s" not in upd


def test_lid_cruzado_no_se_toca():
    fabricantes, elems, by_lid = _sokaf()
    elems[0]["properties"]["nombre"] = "OTRO ARTÍCULO"
    elems[0]["properties"]["precio_unitario_cliente"] = "5.0"

    upd = _derivar_precio_cliente_elementos(fabricantes, _pp(), elems, by_lid)

    assert "el-1" not in upd
    assert "el-2" in upd


FLETE = 2825.0
SEGURO = 60.0
ANCLA_CIF = 6.73


def _li(lid, cont, producto, cajas):
    return {
        "hs_object_id": lid, "name": f"{cont} - {producto}", "quantity": str(cajas),
        "price": "5.805", "precio_de_coste": "5.0",
        "precio_negociado_tipo_venta": str(ANCLA_CIF), "incoterm_precio_negociado": "CIF",
        "numero_container": cont, "hs_product_type": "inventory", "tipo_articulo": "fabricante",
        "tipo_iva_repercutido": "EX", "margen_unitario": "0.161",
    }


def _li_gasto(lid, cont, nombre, precio, tipo):
    return {"hs_object_id": lid, "name": f"{cont} - {nombre}", "quantity": "1", "price": str(precio),
            "numero_container": cont, "hs_product_type": tipo, "tipo_articulo": "transitario"}


def _el(eid, lid, cont, producto, cajas, tipo="fabricante", precio_total=None):
    return {"id": eid, "properties": {
        "line_item_id": lid, "nombre": f"{cont} - {producto}", "nombre_contenedor": cont,
        "tipo_articulo": tipo, "cantidad": str(cajas),
        "precio_unitario": "5.805" if tipo == "fabricante" else str(precio_total),
        "precio_total": str(precio_total if precio_total is not None else round(5.805 * cajas, 2)),
        "descuento": "0", "margen_unitario": "0.161",
    }}


def _montar(monkeypatch, line_items, elementos, contenedores, for_container=None):
    """Doble de recalcular_contenedores: lecturas fijas y escrituras capturadas."""
    monkeypatch.setattr(hubdb_client, "get_porcentajes_iva_repercutido", lambda code, res: (0.0, 0.0))
    monkeypatch.setattr(deals_routes, "_get_associated_ids", lambda deal_id, to: [l["hs_object_id"] for l in line_items])
    monkeypatch.setattr(iva_helpers, "_batch_read_line_items_superset", lambda client, ids: line_items)
    for_container = for_container or (
        lambda nombre, cajas: _pp(cajas=cajas, origen=FLETE, destino=0.0, servicios=SEGURO, seguro=SEGURO)
    )
    monkeypatch.setattr(
        iva_helpers, "build_deal_pricing_context",
        lambda client, deal_id, line_items_precached=None: SimpleNamespace(for_container=for_container),
    )
    monkeypatch.setattr(iva_helpers, "_batch_read_contenedores_props", lambda client, ids: contenedores)
    monkeypatch.setattr(iva_helpers, "get_elementos_by_deal", lambda client, deal_id, properties=None: elementos)
    escrituras = []
    monkeypatch.setattr(
        iva_helpers, "batch_update_custom_objects",
        lambda client, object_type, updates: escrituras.append(updates),
    )
    client = SimpleNamespace(crm=SimpleNamespace(line_items=SimpleNamespace(batch_api=SimpleNamespace(
        update=lambda batch_input_simple_public_object_batch_input: None,
    ))))
    return client, escrituras


def _gastos(cont, sufijo):
    return (
        [_li_gasto(f"l{sufijo}f", cont, "FLETES", FLETE, "Flete"), _li_gasto(f"l{sufijo}s", cont, "SEGURO", SEGURO, "service")],
        [_el(f"e{sufijo}f", f"l{sufijo}f", cont, "FLETES", 1, tipo="flete", precio_total=FLETE),
         _el(f"e{sufijo}s", f"l{sufijo}s", cont, "SEGURO", 1, tipo="seguro", precio_total=SEGURO)],
    )


def _escrito(escrituras):
    final = {}
    for updates in escrituras:
        for u in updates:
            final.setdefault(str(u["id"]), {}).update(u["properties"])
    return final


def _precio_cliente_escrito(escrituras):
    return {
        eid: {k: v for k, v in props.items() if k in PROPS_PRECIO_CLIENTE}
        for eid, props in _escrito(escrituras).items()
        if "precio_unitario_cliente" in props
    }


def _assert_pedidos_lo_acepta(elementos, escrituras, cont):
    """La comprobación de `precios_cliente_guardados` de Pedidos contra los
    precio_total que deja el recálculo: si falla, la factura recalcula y el PDF
    deja de ser lo que SAP manda por EDI."""
    escrito = _escrito(escrituras)
    del_cont = [e for e in elementos if e["properties"]["nombre_contenedor"] == cont]
    fab = [e for e in del_cont if e["properties"]["tipo_articulo"] == "fabricante"]
    final = {e["id"]: {**e["properties"], **escrito.get(e["id"], {})} for e in del_cont}
    suma_cliente = sum(float(final[e["id"]]["total_linea_cliente"]) for e in fab)
    suma_sap = sum(float(final[e["id"]]["precio_total"]) for e in del_cont)
    cajas = sum(float(e["properties"]["cantidad"]) for e in fab)
    assert abs(suma_cliente - suma_sap) <= 0.01 + 0.0005 * cajas + 0.005 * len(fab), (suma_cliente, suma_sap)
    for e in fab:
        precio, total = float(final[e["id"]]["precio_unitario_cliente"]), float(final[e["id"]]["total_linea_cliente"])
        assert abs(total - round(precio * float(e["properties"]["cantidad"]), 2)) <= 0.011


def test_recalcular_escribe_el_precio_cliente_con_el_precio_total_y_respeta_el_bloqueo(monkeypatch):
    # B lleva en line items 600 cajas que sus Elementos no tienen: recalcular lo bloquea.
    gla, gea = _gastos("T-001", "a")
    glb, geb = _gastos("T-002", "b")
    line_items = [
        _li("la1", "T-001", "Mora", 1560), _li("la2", "T-001", "Fresa", 1560), *gla,
        _li("lb1", "T-002", "Mora", 1560), _li("lb2", "T-002", "Fresa", 1560), _li("lb3", "T-002", "Sandia", 600), *glb,
    ]
    elementos = [
        _el("ea1", "la1", "T-001", "Mora", 1560), _el("ea2", "la2", "T-001", "Fresa", 1560), *gea,
        _el("eb1", "lb1", "T-002", "Mora", 1560), _el("eb2", "lb2", "T-002", "Fresa", 1560), *geb,
    ]
    contenedores = {
        "A": {"nombre": "T-001", "importe_total_cobrado": "0"},
        "B": {"nombre": "T-002", "importe_total_cobrado": "0"},
    }
    client, escrituras = _montar(monkeypatch, line_items, elementos, contenedores)

    resultados = recalcular_contenedores(client, "deal", ["A", "B"])

    assert "bloqueado" in resultados["B"]
    total = str(round(ANCLA_CIF * 1560, 2))
    assert _precio_cliente_escrito(escrituras) == {
        "ea1": {"precio_unitario_cliente": str(ANCLA_CIF), "total_linea_cliente": total},
        "ea2": {"precio_unitario_cliente": str(ANCLA_CIF), "total_linea_cliente": total},
    }
    # En el mismo update que precio_total: si HubSpot rechaza el lote, no llega ninguno.
    for updates in escrituras:
        for u in updates:
            if "precio_unitario_cliente" in u["properties"]:
                assert "precio_total" in u["properties"]
    assert not {"eb1", "eb2", "ebf", "ebs"} & set(_escrito(escrituras))
    _assert_pedidos_lo_acepta(elementos, escrituras, "T-001")


def test_un_fallo_del_precio_cliente_no_bloquea_el_resto(monkeypatch, caplog):
    gli, gel = _gastos("T-001", "a")
    line_items = [_li("la1", "T-001", "Mora", 1560), _li("la2", "T-001", "Fresa", 1560), *gli]
    elementos = [_el("ea1", "la1", "T-001", "Mora", 1560), _el("ea2", "la2", "T-001", "Fresa", 1560), *gel]
    client, escrituras = _montar(monkeypatch, line_items, elementos, {"A": {"nombre": "T-001", "importe_total_cobrado": "0"}})

    def _revienta(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(iva_helpers, "_derivar_precio_cliente_elementos", _revienta)
    with caplog.at_level(logging.ERROR, logger="app.utils.iva_helpers"):
        resultados = recalcular_contenedores(client, "deal", ["A"])

    assert resultados["A"]["price"] == pytest.approx(ANCLA_CIF * 3120, abs=0.01)
    assert "bloqueado" not in resultados["A"]
    assert "precio_total" in _escrito(escrituras)["ea1"]
    assert _precio_cliente_escrito(escrituras) == {}
    assert any("precio cliente" in r.message and "T-001" in r.message for r in caplog.records)


def test_elemento_reemparejado_por_nombre_recibe_el_precio_cliente(monkeypatch):
    """El Elemento apunta a un line item archivado y se recupera por nombre: el
    precio cliente le llega en el mismo update que el line_item_id corregido."""
    gli, gel = _gastos("T-001", "a")
    line_items = [_li("la1", "T-001", "Mora", 1560), _li("la2", "T-001", "Fresa", 1560), *gli]
    elementos = [_el("ea1", "la1-archivado", "T-001", "Mora", 1560), _el("ea2", "la2", "T-001", "Fresa", 1560), *gel]
    client, escrituras = _montar(monkeypatch, line_items, elementos, {"A": {"nombre": "T-001", "importe_total_cobrado": "0"}})

    resultados = recalcular_contenedores(client, "deal", ["A"])

    assert "bloqueado" not in resultados["A"]
    escrito = _escrito(escrituras)["ea1"]
    assert escrito["line_item_id"] == "la1"
    assert (escrito["precio_unitario_cliente"], escrito["total_linea_cliente"]) == (str(ANCLA_CIF), str(round(ANCLA_CIF * 1560, 2)))
    _assert_pedidos_lo_acepta(elementos, escrituras, "T-001")


def test_promo_5_mas_1_con_nombre_repetido_no_cruza_precios(monkeypatch):
    """Mismo artículo de pago y de regalo con lids archivados (65027387977-001):
    se empareja por (nombre, cajas) y la promo lleva 0."""
    gli, gel = _gastos("T-001", "a")
    promo = _li("lap", "T-001", "Mora", 260)
    promo.update(price="0", precio_negociado_tipo_venta="", incoterm_precio_negociado="")
    line_items = [_li("la1", "T-001", "Mora", 1300), promo, _li("la2", "T-001", "Fresa", 1560), *gli]
    el_promo = _el("eap", "viejo-2", "T-001", "Mora", 260)
    el_promo["properties"].update(precio_unitario="0", precio_total="0")
    elementos = [_el("ea1", "viejo-1", "T-001", "Mora", 1300), el_promo, _el("ea2", "la2", "T-001", "Fresa", 1560), *gel]
    client, escrituras = _montar(monkeypatch, line_items, elementos, {"A": {"nombre": "T-001", "importe_total_cobrado": "0"}})

    resultados = recalcular_contenedores(client, "deal", ["A"])

    assert "bloqueado" not in resultados["A"]
    escritos = _precio_cliente_escrito(escrituras)
    assert escritos["ea1"] == {"precio_unitario_cliente": "6.73", "total_linea_cliente": str(round(6.73 * 1300, 2))}
    assert escritos["eap"] == {"precio_unitario_cliente": "0.0", "total_linea_cliente": "0.0"}
    _assert_pedidos_lo_acepta(elementos, escrituras, "T-001")


def test_dos_contenedores_cada_uno_con_sus_gastos_por_caja(monkeypatch):
    """B no tiene ancla y reparte los mismos gastos entre 2.400 cajas: 5,805 +
    2.885/2.400 = 7,007, no el 6,73 de A."""
    gla, gea = _gastos("T-001", "a")
    glb, geb = _gastos("T-002", "b")
    sin_ancla = lambda li: {**li, "precio_negociado_tipo_venta": "", "incoterm_precio_negociado": ""}
    line_items = [
        _li("la1", "T-001", "Mora", 1560), _li("la2", "T-001", "Fresa", 1560), *gla,
        sin_ancla(_li("lb1", "T-002", "Mora", 1500)), sin_ancla(_li("lb2", "T-002", "Fresa", 900)), *glb,
    ]
    elementos = [
        _el("ea1", "la1", "T-001", "Mora", 1560), _el("ea2", "la2", "T-001", "Fresa", 1560), *gea,
        _el("eb1", "lb1", "T-002", "Mora", 1500), _el("eb2", "lb2", "T-002", "Fresa", 900), *geb,
    ]
    contenedores = {
        "A": {"nombre": "T-001", "importe_total_cobrado": "0"},
        "B": {"nombre": "T-002", "importe_total_cobrado": "0"},
    }
    client, escrituras = _montar(monkeypatch, line_items, elementos, contenedores)

    recalcular_contenedores(client, "deal", ["A", "B"])

    escritos = _precio_cliente_escrito(escrituras)
    assert escritos["ea1"]["precio_unitario_cliente"] == "6.73"
    assert escritos["eb1"] == {"precio_unitario_cliente": "7.007", "total_linea_cliente": "10510.5"}
    assert escritos["eb2"] == {"precio_unitario_cliente": "7.007", "total_linea_cliente": "6306.3"}
    _assert_pedidos_lo_acepta(elementos, escrituras, "T-001")
    _assert_pedidos_lo_acepta(elementos, escrituras, "T-002")


def test_contenedor_exw_sin_gastos_lleva_el_exw(monkeypatch):
    li = _li("lc1", "T-003", "Mora", 1000)
    li.update(price="6.123", precio_negociado_tipo_venta="", incoterm_precio_negociado="")
    el = _el("ec1", "lc1", "T-003", "Mora", 1000)
    el["properties"].update(precio_unitario="6.123", precio_total="6123.0")
    client, escrituras = _montar(
        monkeypatch, [li], [el], {"C": {"nombre": "T-003", "importe_total_cobrado": "0"}},
        for_container=lambda nombre, cajas: _pp(incoterm="EXW", cajas=cajas, origen=0.0, destino=0.0, servicios=0.0, seguro=0.0),
    )

    recalcular_contenedores(client, "deal", ["C"])

    assert _precio_cliente_escrito(escrituras) == {
        "ec1": {"precio_unitario_cliente": "6.123", "total_linea_cliente": "6123.0"},
    }
