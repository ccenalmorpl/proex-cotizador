"""Salvaguardas de la carga inicial de precio cliente: escribe solo las dos props
y solo en los contenedores al día, nunca en los bloqueados, en los que esperan un
recálculo ni en los facturados."""

import sys
from types import SimpleNamespace

import pytest

from scripts import backfill_precio_cliente as bf
from test_precio_cliente_elementos import ANCLA_CIF, FLETE, SEGURO, _el, _li, _li_gasto, _pp


def _contenedor(cont, sufijo, flete_el=FLETE, extra_li=None):
    """Dos artículos anclados a 6,73 CIF con los precio_total de un recálculo
    hecho con un flete de `flete_el`."""
    lis = [_li(f"l{sufijo}1", cont, "Mora", 1560), _li(f"l{sufijo}2", cont, "Fresa", 1560),
           _li_gasto(f"l{sufijo}f", cont, "FLETES", FLETE, "Flete"),
           _li_gasto(f"l{sufijo}s", cont, "SEGURO", SEGURO, "service")]
    if extra_li:
        lis.append(_li(f"l{sufijo}3", cont, extra_li, 600))
    gastos_pc = (flete_el + SEGURO) / 3120
    pt = round((ANCLA_CIF - gastos_pc) * 1560, 2)
    els = [_el(f"e{sufijo}1", f"l{sufijo}1", cont, "Mora", 1560, precio_total=pt),
           _el(f"e{sufijo}2", f"l{sufijo}2", cont, "Fresa", 1560, precio_total=pt),
           _el(f"e{sufijo}f", f"l{sufijo}f", cont, "FLETES", 1, tipo="flete", precio_total=FLETE),
           _el(f"e{sufijo}s", f"l{sufijo}s", cont, "SEGURO", 1, tipo="seguro", precio_total=SEGURO)]
    return lis, els


@pytest.fixture
def expediente(monkeypatch):
    """-001 al día; -002 con los precio_total de un flete de 2.900 (falta un
    recálculo); -003 facturado; -004 con 600 cajas en line items sin Elemento."""
    partes = [
        _contenedor("65000000000-001", "a"),
        _contenedor("65000000000-002", "b", flete_el=2900.0),
        _contenedor("65000000000-003", "c"),
        _contenedor("65000000000-004", "d", extra_li="Sandia"),
    ]
    line_items = [li for lis, _ in partes for li in lis]
    elementos = [el for _, els in partes for el in els]
    contenedores = [{"id": f"c{i}", "properties": {"nombre": f"65000000000-00{i}"}} for i in range(1, 5)]
    escrituras = []
    monkeypatch.setattr(bf, "_get_associated_ids", lambda deal_id, to: [li["hs_object_id"] for li in line_items])
    monkeypatch.setattr(bf, "_batch_read_line_items_superset", lambda client, ids: line_items)
    monkeypatch.setattr(bf, "build_deal_pricing_context", lambda client, deal_id, line_items_precached=None: SimpleNamespace(
        for_container=lambda nombre, cajas: _pp(cajas=cajas, origen=FLETE, destino=0.0, servicios=SEGURO, seguro=SEGURO)))
    monkeypatch.setattr(bf, "get_elementos_by_deal", lambda client, deal_id, properties=None: elementos)
    monkeypatch.setattr(bf, "get_contenedores_by_deal", lambda client, deal_id, properties=None: contenedores)
    monkeypatch.setattr(bf, "_contenedores_facturados", lambda client, ids: {"c3"})
    monkeypatch.setattr(bf, "batch_update_custom_objects",
                        lambda client, object_type, updates: escrituras.append((object_type, updates)) or len(updates))
    monkeypatch.setattr(bf.hubspot_client, "get_client", lambda: SimpleNamespace())
    return escrituras


def test_clasifica_y_solo_escribe_los_contenedores_al_dia(expediente):
    filas = {f["nombre"]: f for f in bf._analizar_deal(SimpleNamespace(), "65000000000", incluir_facturados=False)}

    assert {n: f["estado"] for n, f in filas.items()} == {
        "65000000000-001": "OK",
        "65000000000-002": "PENDIENTE",
        "65000000000-003": "FACTURADO",
        "65000000000-004": "BLOQUEADO",
    }
    assert set(filas["65000000000-001"]["updates"]) == {"ea1", "ea2"}
    assert all(not filas[n]["updates"] for n in filas if n != "65000000000-001")


def test_apply_escribe_solo_las_dos_props(expediente, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["backfill", "--deal", "65000000000", "--apply"])

    assert bf.main() == 0

    escritos = [u for _, updates in expediente for u in updates]
    assert {u["id"] for u in escritos} == {"ea1", "ea2"}
    assert all(set(u["properties"]) == set(bf.PROPS_PRECIO_CLIENTE) for u in escritos)
    assert {u["properties"]["total_linea_cliente"] for u in escritos} == {str(round(ANCLA_CIF * 1560, 2))}


@pytest.mark.parametrize("numero, facturado", [
    ("2026300377", True), ("", False), (None, False), ("ERROR", False), (" error ", False),
])
def test_error_del_workflow_no_cuenta_como_factura(numero, facturado):
    assert bf._tiene_factura(numero) is facturado
