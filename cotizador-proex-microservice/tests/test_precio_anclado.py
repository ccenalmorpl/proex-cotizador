"""Tests del ancla de precio del tipo de venta (`precio_negociado_tipo_venta`).

Modelo: con ancla vigente el precio final por caja del incoterm queda FIJO y el
EXW/margen flotan (suelo margen 0 = nunca bajo coste); sin ancla (histórico) el
EXW persistido manda y el final flota, como siempre. Si el deal cambia de
incoterm, el EXW manda y el final del nuevo incoterm se re-ancla.
"""

import logging
from types import SimpleNamespace

import pytest

from app.shared import hubdb_client
from app.utils import iva_helpers
from app.utils.iva_helpers import (
    _compute_price_from_fabricantes,
    _derivar_writebacks_lineas,
    _resolver_precios_linea,
)
from app.utils.pricing import (
    DealPricingContext,
    PricingParams,
    calcular_precio_exw_desde_incoterm,
    calcular_precio_por_caja_por_incoterm,
    derivar_exw_desde_final_anclado,
)

IVA_MAP = {
    ("R21", "NACIONAL"): (21.0, 5.2),
    ("EX", "NACIONAL"): (0.0, 0.0),
}


@pytest.fixture(autouse=True)
def mock_iva(monkeypatch):
    def fake(code, residencia):
        key = ((code or "").strip().upper(), (residencia or "").strip().upper())
        return IVA_MAP.get(key, (None, None))

    monkeypatch.setattr(hubdb_client, "get_porcentajes_iva_repercutido", fake)


GASTOS = dict(
    hasta_localidad_destino=0.4,
    hasta_puerto_salida=0.4,
    hasta_puerto_destino=0.25,
    servicios_por_caja=0.1,
    seguro_por_caja=0.05,
    comision_cliente=0.02,
)

INCOTERMS = ["EXW", "DAP_TERRESTRE", "FOB", "CFR", "CIF", "DAP_MARITIMO"]


def _params(incoterm, cajas=100.0, **overrides):
    base = dict(
        incoterm=incoterm,
        total_cajas=cajas,
        total_contenedores=1,
        tipo_iva_fletes="EX",
        tipo_iva_comision="EX",
        servicios_no_seguro_por_iva_por_caja={},
        seguro_por_iva_por_caja={},
        **GASTOS,
    )
    base.update(overrides)
    return PricingParams(**base)


@pytest.mark.parametrize("incoterm", INCOTERMS)
def test_inversa_es_roundtrip_de_la_directa(incoterm):
    exw = 7.23
    final = calcular_precio_por_caja_por_incoterm(exw, incoterm, **GASTOS)
    assert calcular_precio_exw_desde_incoterm(final, incoterm, **GASTOS) == pytest.approx(exw)


@pytest.mark.parametrize("incoterm", INCOTERMS)
def test_directa_es_roundtrip_de_la_inversa(incoterm):
    final = 8.0
    exw = calcular_precio_exw_desde_incoterm(final, incoterm, **GASTOS)
    assert calcular_precio_por_caja_por_incoterm(exw, incoterm, **GASTOS) == pytest.approx(final)


def test_derivar_sin_clamp_mantiene_el_final():
    pp = _params("CIF")
    res = derivar_exw_desde_final_anclado(8.0, 6.0, pp)
    assert res["clamped"] is False
    assert res["final_efectivo"] == pytest.approx(8.0)
    assert res["exw"] == pytest.approx(8.0 - 0.4 - 0.1 - 0.25 - 0.02)


def test_derivar_con_clamp_sube_el_final_a_coste_mas_addons():
    # add-ons CIF = 0.77; final 6.5 daría EXW 5.73 < coste 6.0 → clamp.
    pp = _params("CIF")
    res = derivar_exw_desde_final_anclado(6.5, 6.0, pp)
    assert res["clamped"] is True
    assert res["exw"] == pytest.approx(6.0)
    assert res["final_efectivo"] == pytest.approx(
        calcular_precio_por_caja_por_incoterm(6.0, "CIF", **GASTOS)
    )


def test_derivar_sin_coste_clampa_a_cero():
    pp = _params("CIF")
    res = derivar_exw_desde_final_anclado(0.5, None, pp)
    assert res["clamped"] is True
    assert res["exw"] == 0.0


def _linea(price, ancla=None, ancla_ic=None, coste=None, cajas="100", lid="li-1", **extra):
    li = {
        "hs_object_id": lid,
        "name": "Vino Tinto",
        "price": price,
        "quantity": cajas,
        "tipo_iva_repercutido": "EX",
    }
    if ancla is not None:
        li["precio_negociado_tipo_venta"] = str(ancla)
    if ancla_ic is not None:
        li["incoterm_precio_negociado"] = ancla_ic
    if coste is not None:
        li["precio_de_coste"] = str(coste)
    li.update(extra)
    return li


def test_resolver_con_ancla_vigente_fija_el_final():
    pp = _params("CIF")
    res = _resolver_precios_linea(_linea("7.23", ancla=8.0, ancla_ic="CIF", coste=6.0), pp)
    assert res["anclada"] is True
    assert res["final"] == pytest.approx(8.0)
    assert res["exw"] == pytest.approx(8.0 - 0.77)


def test_resolver_ancla_de_otro_incoterm_usa_exw_persistido():
    pp = _params("FOB")
    res = _resolver_precios_linea(_linea("7.23", ancla=8.0, ancla_ic="CIF", coste=6.0), pp)
    assert res["anclada"] is False
    assert res["exw"] == pytest.approx(7.23)
    assert res["final"] == pytest.approx(
        calcular_precio_por_caja_por_incoterm(7.23, "FOB", **GASTOS)
    )


def test_resolver_sin_ancla_es_el_modelo_historico():
    pp = _params("CIF")
    res = _resolver_precios_linea(_linea("7.23"), pp)
    assert res["anclada"] is False
    assert res["final"] == pytest.approx(calcular_precio_por_caja_por_incoterm(7.23, "CIF", **GASTOS))


def test_caso_real_cambio_de_cajas_mantiene_el_precio_cif():
    """2079 cajas a 8€ CIF; al bajar a 1500 el CIF sigue en 8 (antes: 8,297)."""
    flete_salida_cont = 831.6   # → 0.4/caja con 2079
    flete_destino_cont = 519.75  # → 0.25/caja con 2079
    servicios_cont = 207.9       # → 0.1/caja con 2079

    def pp_para(cajas):
        return _params(
            "CIF",
            cajas=cajas,
            hasta_puerto_salida=flete_salida_cont / cajas,
            hasta_puerto_destino=flete_destino_cont / cajas,
            servicios_por_caja=servicios_cont / cajas,
            comision_cliente=0.0,
        )

    linea = _linea("7.25", ancla=8.0, ancla_ic="CIF", coste=6.0, cajas="2079")
    total_2079 = _compute_price_from_fabricantes([linea], None, pp_para(2079))
    assert total_2079 == pytest.approx(8.0 * 2079, abs=0.01)

    linea_1500 = dict(linea, quantity="1500")
    total_1500 = _compute_price_from_fabricantes([linea_1500], None, pp_para(1500))
    assert total_1500 == pytest.approx(8.0 * 1500, abs=0.01)

    # El EXW absorbe la diferencia: con menos cajas los add-ons/caja suben.
    exw_2079 = _resolver_precios_linea(linea, pp_para(2079))["exw"]
    exw_1500 = _resolver_precios_linea(linea_1500, pp_para(1500))["exw"]
    assert exw_1500 < exw_2079


def test_linea_historica_sin_ancla_sigue_flotando():
    linea = _linea("7.25", cajas="2079")
    pp_2079 = _params("CIF", cajas=2079, comision_cliente=0.0)
    pp_1500 = _params(
        "CIF", cajas=1500, comision_cliente=0.0,
        hasta_puerto_salida=GASTOS["hasta_puerto_salida"] * 2079 / 1500,
        hasta_puerto_destino=GASTOS["hasta_puerto_destino"] * 2079 / 1500,
        servicios_por_caja=GASTOS["servicios_por_caja"] * 2079 / 1500,
    )
    por_caja_2079 = _compute_price_from_fabricantes([linea], None, pp_2079) / 2079
    linea_1500 = dict(linea, quantity="1500")
    por_caja_1500 = _compute_price_from_fabricantes([linea_1500], None, pp_1500) / 1500
    assert por_caja_1500 > por_caja_2079


def test_promo_sin_ancla_no_aporta_y_ancla_stale_cero_tampoco():
    pp = _params("CIF")
    normal = _linea("10", lid="li-1")
    promo = _linea("0", lid="li-2", cajas="50")
    promo_ancla_cero = _linea("0", ancla=0, ancla_ic="CIF", lid="li-3", cajas="50")
    solo = _compute_price_from_fabricantes([normal], None, pp)
    con_promos = _compute_price_from_fabricantes([normal, promo, promo_ancla_cero], None, pp)
    assert con_promos == pytest.approx(solo)


def test_price_stale_cero_con_ancla_positiva_si_aporta():
    # El write-back aún no corrió: price quedó 0 pero hay ancla → la línea cuenta.
    pp = _params("CIF")
    linea = _linea("0", ancla=8.0, ancla_ic="CIF", coste=6.0)
    res = _compute_price_from_fabricantes([linea], None, pp)
    assert res == pytest.approx(8.0 * 100, abs=0.01)


def test_writeback_deriva_price_y_margen():
    pp = _params("CIF")
    # price persistido desfasado (quedó del reparto con otras cajas)
    linea = _linea("7.0", ancla=8.0, ancla_ic="CIF", coste=6.0)
    li_upd, elem_upd = _derivar_writebacks_lineas([linea], pp, {})
    assert len(li_upd) == 1
    props = li_upd[0]["properties"]
    exw = 8.0 - 0.77
    assert float(props["price"]) == pytest.approx(exw, abs=0.001)
    assert float(props["margen_unitario"]) == pytest.approx((exw - 6.0) / 6.0, abs=0.0001)
    assert elem_upd == []


def test_writeback_sin_cambios_no_emite_update():
    pp = _params("CIF")
    exw = round(8.0 - 0.77, 3)
    margen = round((exw - 6.0) / 6.0, 5)
    linea = _linea(str(exw), ancla=8.0, ancla_ic="CIF", coste=6.0,
                   margen_unitario=str(margen))
    li_upd, _ = _derivar_writebacks_lineas([linea], pp, {})
    assert li_upd == []


def test_writeback_clamp_deja_margen_cero():
    pp = _params("CIF")
    linea = _linea("7.23", ancla=6.5, ancla_ic="CIF", coste=6.0)
    li_upd, _ = _derivar_writebacks_lineas([linea], pp, {})
    props = li_upd[0]["properties"]
    assert float(props["price"]) == pytest.approx(6.0)
    assert float(props["margen_unitario"]) == 0.0


def test_writeback_cambio_incoterm_reancla_sin_tocar_price():
    pp = _params("FOB")
    linea = _linea("7.23", ancla=8.0, ancla_ic="CIF", coste=6.0)
    li_upd, _ = _derivar_writebacks_lineas([linea], pp, {})
    assert len(li_upd) == 1
    props = li_upd[0]["properties"]
    assert "price" not in props and "margen_unitario" not in props
    esperado = calcular_precio_por_caja_por_incoterm(7.23, "FOB", **GASTOS)
    assert float(props["precio_negociado_tipo_venta"]) == pytest.approx(esperado, abs=0.001)
    assert props["incoterm_precio_negociado"] == "FOB"


def test_writeback_linea_sin_ancla_no_se_toca():
    pp = _params("CIF")
    li_upd, elem_upd = _derivar_writebacks_lineas([_linea("7.23", coste=6.0)], pp, {})
    assert li_upd == [] and elem_upd == []


def test_writeback_sincroniza_elemento_por_lid():
    pp = _params("CIF")
    linea = _linea("7.0", ancla=8.0, ancla_ic="CIF", coste=6.0)
    elem = {"id": "el-1", "properties": {
        "line_item_id": "li-1", "nombre": "Vino Tinto",
        "precio_unitario": "7.0", "precio_total": "700", "margen_unitario": "0.16667",
    }}
    li_upd, elem_upd = _derivar_writebacks_lineas([linea], pp, {"li-1": elem})
    assert len(elem_upd) == 1
    eprops = elem_upd[0]["properties"]
    exw = round(8.0 - 0.77, 3)
    assert float(eprops["precio_unitario"]) == pytest.approx(exw)
    # precio_total ya no sale del writeback per-línea: lo fija
    # _derivar_precio_total_fabricantes a nivel de contenedor.
    assert "precio_total" not in eprops


def test_writeback_elemento_nombre_cruzado_se_salta():
    pp = _params("CIF")
    linea = _linea("7.23", ancla=8.0, ancla_ic="CIF", coste=6.0)
    elem = {"id": "el-9", "properties": {
        "line_item_id": "li-1", "nombre": "OTRO ARTÍCULO", "precio_unitario": "1",
    }}
    li_upd, elem_upd = _derivar_writebacks_lineas([linea], pp, {"li-1": elem})
    assert len(li_upd) == 1
    assert elem_upd == []


class _FakeLineItemsBatchApi:
    def __init__(self):
        self.updates = []

    def update(self, batch_input_simple_public_object_batch_input):
        self.updates.append(batch_input_simple_public_object_batch_input["inputs"])
        return SimpleNamespace(results=[])


def test_recalcular_integra_ancla_y_writebacks(monkeypatch):
    """Al recalcular tras un cambio de cajas, contenedor.price honra el ancla y
    los line items reciben el EXW/margen derivados."""
    li_api = _FakeLineItemsBatchApi()
    client = SimpleNamespace(crm=SimpleNamespace(
        line_items=SimpleNamespace(batch_api=li_api),
    ))

    fabricante = _linea(
        "7.23", ancla=8.0, ancla_ic="CIF", coste=6.0, cajas="1500",
        hs_product_type="inventory", tipo_articulo="fabricante",
        numero_container="D-001",
    )
    contexto = DealPricingContext(
        incoterm="CIF",
        precio_origen=600.0,
        precio_destino=375.0,
        repercutir=False,
        tiene_intermediario=False,
        tipo_comision="",
        importe_comision=0.0,
        num_contenedores=1,
        tipo_iva_fletes="EX",
        tipo_iva_comision="EX",
        servicios_por_contenedor={"D-001": 150.0},
        seguro_por_contenedor={},
        servicios_buckets_por_contenedor={"D-001": {"EX": 150.0}},
        seguro_buckets_por_contenedor={},
    )

    import app.routes.deals as deals_mod
    monkeypatch.setattr(deals_mod, "_get_associated_ids", lambda _d, _t: ["li-1"])
    monkeypatch.setattr(iva_helpers, "_batch_read_line_items_superset", lambda _c, _ids: [fabricante])
    monkeypatch.setattr(iva_helpers, "build_deal_pricing_context", lambda _c, _d, line_items_precached=None: contexto)
    monkeypatch.setattr(iva_helpers, "_batch_read_contenedores_props", lambda _c, _ids: {
        "co-1": {"nombre": "D-001", "importe_total_cobrado": ""},
    })
    monkeypatch.setattr(iva_helpers, "get_elementos_by_deal", lambda _c, _d, properties=None: [
        {"id": "el-1", "properties": {
            "line_item_id": "li-1", "nombre": "Vino Tinto", "tipo_articulo": "fabricante",
            "cantidad": "1500", "peso_neto_unitario": "1", "peso_bruto_unitario": "1.1",
            "nombre_contenedor": "D-001",
            "precio_unitario": "7.23", "precio_total": "10845", "margen_unitario": "0.205",
        }},
    ])
    co_updates = []
    monkeypatch.setattr(
        iva_helpers, "batch_update_custom_objects",
        lambda _c, otype, updates: co_updates.append((otype, updates)),
    )

    resultados = iva_helpers.recalcular_contenedores(client, "deal-1", ["co-1"])

    # add-ons/caja con 1500 cajas: flete 0.4+0.25, servicios 0.1 → EXW = 8 − 0.75...
    exw = 8.0 - (600.0 / 1500 + 375.0 / 1500 + 150.0 / 1500)
    assert resultados["co-1"]["price"] == pytest.approx(8.0 * 1500, abs=0.01)

    line_updates = [u for chunk in li_api.updates for u in chunk]
    assert len(line_updates) == 1
    assert float(line_updates[0]["properties"]["price"]) == pytest.approx(exw, abs=0.001)

    tipos = [otype for otype, _ in co_updates]
    from app.core.custom_objects_constants import OBJECT_TYPE_CONTENEDOR, OBJECT_TYPE_ELEMENTO
    assert OBJECT_TYPE_CONTENEDOR in tipos and OBJECT_TYPE_ELEMENTO in tipos
    elem_updates = next(u for otype, u in co_updates if otype == OBJECT_TYPE_ELEMENTO)
    assert float(elem_updates[0]["properties"]["precio_unitario"]) == pytest.approx(exw, abs=0.001)
    # precio_total corregido: sin Elementos de servicio/flete en el contenedor, la
    # línea fabricante absorbe todos los gastos para que Σ precio_total = 8×1500.
    assert float(elem_updates[0]["properties"]["precio_total"]) == pytest.approx(12000.0)


def _recalcular_con_elemento(monkeypatch, elem_props, li_price="7.23", li_margen=None):
    """Monta el mismo escenario de un contenedor con una línea fabricante anclada y
    devuelve (resultados, line_updates, elem_updates)."""
    li_api = _FakeLineItemsBatchApi()
    client = SimpleNamespace(crm=SimpleNamespace(line_items=SimpleNamespace(batch_api=li_api)))

    extra = {
        "hs_product_type": "inventory",
        "tipo_articulo": "fabricante",
        "numero_container": "D-001",
    }
    if li_margen is not None:
        extra["margen_unitario"] = li_margen
    fabricante = _linea(
        li_price, ancla=8.0, ancla_ic="CIF", coste=6.0, cajas="1500", **extra
    )
    contexto = DealPricingContext(
        incoterm="CIF", precio_origen=600.0, precio_destino=375.0,
        repercutir=False, tiene_intermediario=False, tipo_comision="",
        importe_comision=0.0, num_contenedores=1,
        tipo_iva_fletes="EX", tipo_iva_comision="EX",
        servicios_por_contenedor={"D-001": 150.0}, seguro_por_contenedor={},
        servicios_buckets_por_contenedor={"D-001": {"EX": 150.0}},
        seguro_buckets_por_contenedor={},
    )

    import app.routes.deals as deals_mod
    monkeypatch.setattr(deals_mod, "_get_associated_ids", lambda _d, _t: ["li-1"])
    monkeypatch.setattr(iva_helpers, "_batch_read_line_items_superset", lambda _c, _ids: [fabricante])
    monkeypatch.setattr(iva_helpers, "build_deal_pricing_context", lambda _c, _d, line_items_precached=None: contexto)
    monkeypatch.setattr(iva_helpers, "_batch_read_contenedores_props", lambda _c, _ids: {
        "co-1": {"nombre": "D-001", "importe_total_cobrado": ""},
    })
    monkeypatch.setattr(iva_helpers, "get_elementos_by_deal", lambda _c, _d, properties=None: [
        {"id": "el-1", "properties": elem_props},
    ])
    co_updates = []
    monkeypatch.setattr(
        iva_helpers, "batch_update_custom_objects",
        lambda _c, otype, updates: co_updates.append((otype, updates)),
    )

    resultados = iva_helpers.recalcular_contenedores(client, "deal-1", ["co-1"])
    from app.core.custom_objects_constants import OBJECT_TYPE_ELEMENTO
    elem_updates = next(
        (u for otype, u in co_updates if otype == OBJECT_TYPE_ELEMENTO), [],
    )
    return resultados, [u for chunk in li_api.updates for u in chunk], elem_updates


def _elem_props(lid="li-1", precio_unitario="7.23", precio_total="10845"):
    return {
        "line_item_id": lid, "nombre": "Vino Tinto", "tipo_articulo": "fabricante",
        "cantidad": "1500", "peso_neto_unitario": "1", "peso_bruto_unitario": "1.1",
        "nombre_contenedor": "D-001",
        "precio_unitario": precio_unitario, "precio_total": precio_total,
        "margen_unitario": "0.205",
    }


def test_recalcular_repara_lid_stale_y_cuadra(monkeypatch):
    """Elemento apuntando a un line item archivado: se reempareja por nombre, se
    corrige el line_item_id y el precio_total vuelve a cuadrar."""
    _res, _li, elem_updates = _recalcular_con_elemento(
        monkeypatch, _elem_props(lid="li-archivado"),
    )

    assert len(elem_updates) == 1
    props = elem_updates[0]["properties"]
    assert props["line_item_id"] == "li-1"
    assert float(props["precio_total"]) == pytest.approx(12000.0)
    assert len({u["id"] for u in elem_updates}) == len(elem_updates)


def test_recalcular_sincroniza_elemento_aunque_el_line_item_este_al_dia(monkeypatch):
    """El line item ya tiene EXW y margen derivados (no genera update propio), pero el
    Elemento espejo sigue con el valor viejo y debe actualizarse igual."""
    exw = 8.0 - (600.0 + 375.0 + 150.0) / 1500
    _res, line_updates, elem_updates = _recalcular_con_elemento(
        monkeypatch, _elem_props(precio_unitario="7.0"),
        li_price=str(round(exw, 3)), li_margen="0.20833",
    )

    assert line_updates == []
    assert float(elem_updates[0]["properties"]["precio_unitario"]) == pytest.approx(exw, abs=0.001)


def test_recalcular_avisa_invariante_si_el_elemento_no_resuelve(monkeypatch, caplog):
    """Elemento con otro nombre: no empareja, no se corrige nada y el invariante
    canta el descuadre que leerá SAP."""
    props = _elem_props(lid="li-x", precio_total="10845")
    props["nombre"] = "Otro Articulo"
    with caplog.at_level(logging.WARNING):
        _recalcular_con_elemento(monkeypatch, props)

    assert "0/1 Elementos fabricante emparejados" in caplog.text
    assert "INVARIANTE" in caplog.text


def test_build_pricing_context_seguro_entra_en_servicios(monkeypatch):
    """El builder por-contenedor suma el seguro a servicios_por_contenedor (la
    invariante que asumen las fórmulas por incoterm y la plantilla) y a la vez lo
    mantiene aparte en seguro_por_contenedor y en su propio bucket de IVA.

    Regresión del bug del seguro contado dos veces: al excluirlo del escalar, el
    EXW derivado del ancla salía inflado y el contenedor/plantilla sumaban el
    seguro de más. Ningún test cubría _aggregate con una línea de seguro."""
    from app.utils.pricing import build_deal_pricing_context

    deal = SimpleNamespace(properties={
        "tipo_de_venta": "CIF",
        "precio_ruta_hasta_puerto_de_salida": "300",
        "precio_ruta_hasta_puerto_destino": "400",
    })
    client = SimpleNamespace(crm=SimpleNamespace(
        deals=SimpleNamespace(basic_api=SimpleNamespace(get_by_id=lambda **_: deal)),
    ))

    line_items = [
        {"hs_product_type": "container", "tipo_articulo": "transitario",
         "numero_container": "D-001"},
        {"hs_product_type": "service", "tipo_articulo": "seguro",
         "numero_container": "D-001", "price": "60", "tipo_iva_repercutido": "R21"},
        {"hs_product_type": "service", "tipo_articulo": "servicio",
         "numero_container": "D-001", "price": "40", "tipo_iva_repercutido": "R21"},
    ]

    ctx = build_deal_pricing_context(client, "deal-1", line_items_precached=line_items)

    # servicios_por_contenedor INCLUYE el seguro: 40 (servicio) + 60 (seguro) = 100.
    assert ctx.servicios_por_contenedor["D-001"] == pytest.approx(100.0)
    # ...pero el seguro sigue disponible por separado (para la resta de FOB/CFR).
    assert ctx.seguro_por_contenedor["D-001"] == pytest.approx(60.0)
    # Buckets de IVA separados: el seguro NO contamina el bucket de servicios.
    assert ctx.servicios_buckets_por_contenedor["D-001"] == {"R21": 40.0}
    assert ctx.seguro_buckets_por_contenedor["D-001"] == {"R21": 60.0}
