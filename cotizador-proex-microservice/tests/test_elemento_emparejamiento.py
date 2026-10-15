"""Tests de `_emparejar_elementos_fabricante`: reemparejar Elemento↔line item
cuando el `line_item_id` del Elemento apunta a una línea ya archivada.

Caso real (deal 63433865390, 2026-08-11): los 4 Elementos fabricante apuntaban a
line items archivados, así que `recalcular_contenedores` no los encontraba y ni el
EXW ni el `precio_total` se sincronizaban. Al subir el flete de 855 a 900 los line
items bajaron su EXW a 6,933 y los Elementos se quedaron en 6,946: SAP leyó 45,00 €
de más.
"""

import logging

from app.utils.iva_helpers import _emparejar_elementos_fabricante

CONT = "63433865390-001"


def _linea(lid, nombre, cajas=1200):
    return {
        "hs_object_id": lid,
        "name": nombre,
        "price": "6.933",
        "quantity": str(cajas),
        "numero_container": CONT,
    }


def _elem(eid, lid, nombre, tipo="fabricante", nombre_contenedor=CONT, cajas=None):
    return {"id": eid, "properties": {
        "line_item_id": lid,
        "nombre": nombre,
        "tipo_articulo": tipo,
        "nombre_contenedor": nombre_contenedor,
        "precio_total": "8335.0",
        "cantidad": None if cajas is None else f"{cajas}.0",
    }}


def _by_lid(elems):
    idx = {}
    for el in elems:
        lid = str((el.get("properties") or {}).get("line_item_id") or "").strip()
        if lid:
            idx[lid] = el
    return idx


def test_lid_stale_reempareja_por_nombre_y_repara():
    fabricantes = [
        _linea("57706881562", "PINA COCO"),
        _linea("57706881560", "FRESA"),
        _linea("57706881556", "SANDIA UVA", 600),
        _linea("57706881557", "MORA", 600),
    ]
    elems = [
        _elem("el-1", "57712297398", "PINA COCO"),
        _elem("el-2", "57712297395", "FRESA"),
        _elem("el-3", "57712297394", "SANDIA UVA"),
        _elem("el-4", "57712297396", "MORA"),
    ]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems), "63433865390")

    assert emp.resueltos == 4
    assert emp.sin_resolver == []
    assert emp.by_lid["57706881562"]["id"] == "el-1"
    assert emp.by_lid["57706881556"]["id"] == "el-3"
    assert {r["id"]: r["properties"]["line_item_id"] for r in emp.lid_repairs} == {
        "el-1": "57706881562",
        "el-2": "57706881560",
        "el-3": "57706881556",
        "el-4": "57706881557",
    }


def test_lid_correcto_no_genera_reparacion():
    fabricantes = [_linea("li-1", "PINA COCO")]
    elems = [_elem("el-1", "li-1", "PINA COCO")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 1
    assert emp.lid_repairs == []


def test_cruce_intra_contenedor_repara_los_dos_sin_duplicar():
    fabricantes = [_linea("li-1", "PINA COCO"), _linea("li-2", "FRESA")]
    elems = [_elem("el-1", "li-2", "PINA COCO"), _elem("el-2", "li-1", "FRESA")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 2
    assert emp.by_lid["li-1"]["id"] == "el-1"
    assert emp.by_lid["li-2"]["id"] == "el-2"
    assert len({r["id"] for r in emp.lid_repairs}) == 2


def test_promo_nombre_duplicado_mismas_cajas_no_se_toca():
    """Dos líneas del mismo artículo con las mismas cajas: ni el nombre ni las cajas
    discriminan, así que la que tiene el lid roto se queda sin emparejar en vez de
    robar el Elemento de la otra."""
    fabricantes = [_linea("li-1", "PINA COCO"), _linea("li-2", "PINA COCO")]
    elems = [_elem("el-1", "li-1", "PINA COCO"), _elem("el-2", "li-viejo", "PINA COCO")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 1
    assert emp.by_lid["li-1"]["id"] == "el-1"
    assert emp.lid_repairs == []
    assert [s["motivo"] for s in emp.sin_resolver] == ["nombre_ambiguo"]


def test_promo_lids_stale_se_reemparejan_por_cajas():
    """Contenedor 65027387977-001 (29/09): la publicación del 18/09 archivó los line
    items y los dos Elementos del producto (promo 120 a 0 + 3.480 de pago) se
    quedaron apuntando a los viejos. Con el nombre repetido no emparejaban, y al
    bajar el flete de 900 a 840 el producto no absorbió los 60 €: SAP pasó a
    30.245,17 con un anticipo de 30.317,76."""
    nombre = "65027387977-001 - ENERGETICA-VITALIS ENERDRINK EVOLUTION-FRESA"
    fabricantes = [_linea("59009411368", nombre, 3480), _linea("59009411372", nombre, 120)]
    elems = [
        _elem("62131710482", "59029967131", nombre, cajas=120),
        _elem("62131710483", "59017748307", nombre, cajas=3480),
    ]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems), "65027387977")

    assert emp.resueltos == 2
    assert emp.sin_resolver == []
    assert emp.by_lid["59009411368"]["id"] == "62131710483"
    assert emp.by_lid["59009411372"]["id"] == "62131710482"
    assert {r["id"]: r["properties"]["line_item_id"] for r in emp.lid_repairs} == {
        "62131710483": "59009411368",
        "62131710482": "59009411372",
    }


def test_promo_mismas_cajas_desempata_el_precio_cero():
    """Contenedor 65949742192-001 (14/10): ORIGINAL 120 de regalo + 120 de pago. La
    publicación de las 08:17 archivó los line items y los dos Elementos no
    emparejaban ni por nombre ni por cajas, así que recalcular bloqueó el
    contenedor: el flete bajó de 625 a 600 y la mercancía no absorbió los 25 €."""
    nombre = "65949742192-001 - ENERGETICA-VITALIS ENERDRINK EVOLUTION-ORIGINAL"
    pago = _linea("59857662987", nombre, 120)
    pago["price"] = "6.219"
    regalo = _linea("59857662988", nombre, 120)
    regalo["price"] = "0.0"
    el_regalo = _elem("63724939699", "59862205843", nombre, cajas=120)
    el_regalo["properties"].update(precio_unitario="0.0", precio_total="0.0")
    el_pago = _elem("63724939700", "59862061993", nombre, cajas=120)
    el_pago["properties"].update(precio_unitario="6.219", precio_total="746.33")
    elems = [el_regalo, el_pago]

    emp = _emparejar_elementos_fabricante(CONT, [pago, regalo], elems, _by_lid(elems), "65949742192")

    assert emp.resueltos == 2
    assert emp.sin_resolver == []
    assert {r["id"]: r["properties"]["line_item_id"] for r in emp.lid_repairs} == {
        "63724939700": "59857662987",
        "63724939699": "59857662988",
    }


def test_promo_mismas_cajas_sin_precio_en_el_elemento_no_se_empareja():
    fabricantes = [_linea("li-1", "PINA COCO", 120), _linea("li-2", "PINA COCO", 120)]
    fabricantes[1]["price"] = "0"
    elems = [
        _elem("el-1", "viejo-1", "PINA COCO", cajas=120),
        _elem("el-2", "viejo-2", "PINA COCO", cajas=120),
    ]
    for el in elems:
        el["properties"]["precio_total"] = None

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 0
    assert emp.lid_repairs == []


def test_promo_stale_por_cajas_no_roba_el_elemento_con_lid_vivo():
    fabricantes = [_linea("li-2", "PINA COCO", 100), _linea("li-1", "PINA COCO", 500)]
    elems = [
        _elem("el-1", "li-1", "PINA COCO", cajas=100),
        _elem("el-2", "li-viejo", "PINA COCO", cajas=100),
    ]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.by_lid["li-1"]["id"] == "el-1"
    assert emp.by_lid["li-2"]["id"] == "el-2"
    assert emp.lid_repairs == [{"id": "el-2", "properties": {"line_item_id": "li-2"}}]


def test_promo_stale_sin_cajas_en_el_elemento_no_se_empareja():
    fabricantes = [_linea("li-1", "PINA COCO", 500), _linea("li-2", "PINA COCO", 100)]
    elems = [_elem("el-1", "viejo-1", "PINA COCO"), _elem("el-2", "viejo-2", "PINA COCO")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 0
    assert [s["motivo"] for s in emp.sin_resolver] == ["nombre_ambiguo", "nombre_ambiguo"]


def test_un_solo_elemento_para_dos_lineas_iguales_no_se_reclama_dos_veces():
    fabricantes = [_linea("li-1", "PINA COCO"), _linea("li-2", "PINA COCO")]
    elems = [_elem("el-1", "li-1", "PINA COCO")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 1
    assert len(emp.sin_resolver) == 1


def test_elemento_de_otro_contenedor_no_se_empareja():
    fabricantes = [_linea("li-1", "PINA COCO")]
    elems = [_elem("el-1", "li-1", "PINA COCO", nombre_contenedor="63433865390-002")]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, [], _by_lid(elems))

    assert emp.resueltos == 0
    assert emp.sin_resolver[0]["motivo"] == "otro_contenedor"


def test_elemento_renombrado_no_resuelve_y_avisa(caplog):
    fabricantes = [_linea("li-1", "PINA COCO")]
    elems = [_elem("el-1", "li-1", "OTRO ARTICULO")]

    with caplog.at_level(logging.WARNING):
        emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems), "deal-9")

    assert emp.resueltos == 0
    assert emp.sin_resolver[0]["motivo"] == "nombre_cruzado"
    assert "line_item=li-1" in caplog.text
    assert "'PINA COCO'" in caplog.text


def test_duplicado_stale_y_vivo_se_deja_intacto():
    """Contenedor 63436741971-001: la misma línea tiene dos Elementos (uno con lid
    vivo, otro stale). El nombre es ambiguo, así que no se reempareja nada."""
    fabricantes = [_linea("57806751756", "MALTA PURE JOY", 2418)]
    elems = [
        _elem("59999315640", "57806751756", "MALTA PURE JOY"),
        _elem("59876143426", "57753142832", "MALTA PURE JOY"),
    ]

    emp = _emparejar_elementos_fabricante(CONT, fabricantes, elems, _by_lid(elems))

    assert emp.resueltos == 1
    assert emp.by_lid["57806751756"]["id"] == "59999315640"
    assert emp.lid_repairs == []
