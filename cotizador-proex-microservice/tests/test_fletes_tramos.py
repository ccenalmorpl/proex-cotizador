"""Tests del doble flete (camión + tramo marítimo): clasificación de líneas por
sufijo, índice de upsert por (numero_container, tramo) y plan de upsert con
normalización de la línea legacy '- FLETES'. Funciones puras."""

from app.routes.deals import _indexar_fletes_existentes, _plan_fletes_upsert
from app.services.custom_objects import _parse_line_items_to_custom_objects
from app.utils.fletes import (
    TRAMO_LEGACY,
    TRAMO_MARITIMO,
    TRAMO_TERRESTRE,
    clasificar_tramo_flete,
)


def test_clasificar_tramo_por_sufijo():
    assert clasificar_tramo_flete("61763928105-001 - FLETE MARITIMO") == TRAMO_MARITIMO
    assert clasificar_tramo_flete("61763928105-001 - FLETE TERRESTRE") == TRAMO_TERRESTRE
    assert clasificar_tramo_flete("61763928105-001 - FLETES") == TRAMO_LEGACY
    assert clasificar_tramo_flete("FLETES") == TRAMO_LEGACY
    # deal sin line items de contenedor (key ''): nombre = sufijo a secas
    assert clasificar_tramo_flete("FLETE MARITIMO") == TRAMO_MARITIMO
    assert clasificar_tramo_flete("FLETE TERRESTRE") == TRAMO_TERRESTRE


def test_clasificar_no_flete_devuelve_none():
    assert clasificar_tramo_flete("61763928105-001 - VINO TINTO") is None
    assert clasificar_tramo_flete("SERVICIO ADUANA") is None
    assert clasificar_tramo_flete("") is None
    assert clasificar_tramo_flete(None) is None


def _li(id, name, numero_container="", tipo_articulo="transitario", **props):
    base = {"name": name, "numero_container": numero_container, "tipo_articulo": tipo_articulo}
    base.update(props)
    return {"id": id, "properties": base}


def test_indice_incluye_legacy_y_tramos():
    items = [
        _li("1", "D-001 - FLETES", "D-001"),
        _li("2", "D-001 - FLETE MARITIMO", "D-001"),
        _li("3", "D-001 - FLETE TERRESTRE", "D-001"),
        _li("4", "D-001 - VINO TINTO", "D-001"),
        _li("5", "D-002 - FLETE MARITIMO", "D-002"),
    ]
    idx = _indexar_fletes_existentes(items)
    assert set(idx) == {
        ("D-001", TRAMO_LEGACY),
        ("D-001", TRAMO_MARITIMO),
        ("D-001", TRAMO_TERRESTRE),
        ("D-002", TRAMO_MARITIMO),
    }
    assert idx[("D-001", TRAMO_LEGACY)]["id"] == "1"


def test_indice_excluye_servicios_homonimos_del_catalogo():
    # Una línea de SERVICIO cuyo nombre acaba en un sufijo de tramo no debe
    # indexarse como flete (acabaría archivada); solo cuentan tipo_articulo=transitario.
    items = [
        _li("1", "D-001 - FLETE TERRESTRE", "D-001", tipo_articulo="servicio"),
        _li("2", "D-001 - FLETE MARITIMO", "D-001", tipo_articulo=""),
        _li("3", "D-001 - FLETES", "D-001", tipo_articulo=""),
    ]
    idx = _indexar_fletes_existentes(items)
    # la legacy se indexa solo por nombre, como el código anterior
    assert set(idx) == {("D-001", TRAMO_LEGACY)}


CONTAINER_BY_KEY = {"D-001": {"id": "li-cont", "name": "D-001", "numero_container": "D-001"}}


def _specs_doble(precio_mar=200.0, precio_terr=100.0):
    return [
        {
            "tramo": TRAMO_MARITIMO,
            "sufijo": "FLETE MARITIMO",
            "precio": precio_mar,
            "cardcode": "TRANS-MAR",
            "tipo_iva_repercutido": "R21",
            "tipo_iva_soportado": "S21",
            "product_id": "111",
        },
        {
            "tramo": TRAMO_TERRESTRE,
            "sufijo": "FLETE TERRESTRE",
            "precio": precio_terr,
            "cardcode": "TRANS-TERR",
            "tipo_iva_repercutido": "R10",
            "tipo_iva_soportado": "S10",
            "product_id": "222",
        },
    ]


def _spec_legacy(precio=300.0):
    return [{
        "tramo": TRAMO_LEGACY,
        "sufijo": "FLETES",
        "precio": precio,
        "cardcode": "TRANS-MAR",
        "tipo_iva_repercutido": "R21",
        "tipo_iva_soportado": None,
        "product_id": "111",
    }]


def test_normalizacion_archiva_legacy_y_crea_las_dos_lineas():
    existentes = {("D-001", TRAMO_LEGACY): {
        "id": "li-flete",
        "properties": {"name": "D-001 - FLETES", "numero_container": "D-001", "price": "300"},
    }}
    to_update, to_create, to_delete, persisted = _plan_fletes_upsert(
        existentes, {"D-001"}, _specs_doble(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert to_update == []
    assert to_delete == ["li-flete"]
    assert persisted == {}
    por_nombre = {x["properties"]["name"]: x for x in to_create}
    assert set(por_nombre) == {"D-001 - FLETE MARITIMO", "D-001 - FLETE TERRESTRE"}
    mar = por_nombre["D-001 - FLETE MARITIMO"]
    terr = por_nombre["D-001 - FLETE TERRESTRE"]
    assert mar["_target_key"] == ("D-001", TRAMO_MARITIMO)
    assert terr["_target_key"] == ("D-001", TRAMO_TERRESTRE)
    assert mar["properties"]["price"] == "200.0" and mar["properties"]["cardcode"] == "TRANS-MAR"
    assert terr["properties"]["price"] == "100.0" and terr["properties"]["cardcode"] == "TRANS-TERR"
    assert mar["properties"]["hs_product_id"] == "111"
    assert terr["properties"]["hs_product_id"] == "222"
    assert mar["properties"]["tipo_iva_repercutido"] == "R21"
    assert terr["properties"]["tipo_iva_repercutido"] == "R10"
    for x in (mar, terr):
        assert x["properties"]["tipo_articulo"] == "transitario"
        assert x["properties"]["hs_product_type"] == "Flete"
        assert x["properties"]["numero_container"] == "D-001"
        assert x["properties"]["almacen"] == "10"


def test_upsert_doble_idempotente_actualiza_solo_su_tramo():
    existentes = {
        ("D-001", TRAMO_MARITIMO): {"id": "li-m", "properties": {
            "name": "D-001 - FLETE MARITIMO", "numero_container": "D-001",
            "price": "200", "cardcode": "TRANS-MAR",
            "tipo_iva_repercutido": "R21", "tipo_iva_soportado": "S21", "almacen": "10",
        }},
        ("D-001", TRAMO_TERRESTRE): {"id": "li-t", "properties": {
            "name": "D-001 - FLETE TERRESTRE", "numero_container": "D-001",
            "price": "90", "cardcode": "TRANS-TERR",
            "tipo_iva_repercutido": "R10", "tipo_iva_soportado": "S10", "almacen": "10",
        }},
    }
    to_update, to_create, to_delete, persisted = _plan_fletes_upsert(
        existentes, {"D-001"}, _specs_doble(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert to_create == [] and to_delete == []
    assert to_update == [{"id": "li-t", "properties": {"price": "100.0"}}]
    assert persisted == {
        ("D-001", TRAMO_MARITIMO): "li-m",
        ("D-001", TRAMO_TERRESTRE): "li-t",
    }


def test_modo_simple_sin_cambios_no_emite_nada():
    existentes = {("D-001", TRAMO_LEGACY): {"id": "li-flete", "properties": {
        "name": "D-001 - FLETES", "numero_container": "D-001",
        "price": "300", "cardcode": "TRANS-MAR",
        "tipo_iva_repercutido": "R21", "almacen": "10",
    }}}
    to_update, to_create, to_delete, persisted = _plan_fletes_upsert(
        existentes, {"D-001"}, _spec_legacy(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert to_update == [] and to_create == [] and to_delete == []
    assert persisted == {("D-001", TRAMO_LEGACY): "li-flete"}


def test_modo_simple_crea_la_linea_combinada():
    to_update, to_create, to_delete, _ = _plan_fletes_upsert(
        {}, {"D-001"}, _spec_legacy(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert to_update == [] and to_delete == []
    assert len(to_create) == 1
    assert to_create[0]["properties"]["name"] == "D-001 - FLETES"
    assert to_create[0]["_target_key"] == ("D-001", TRAMO_LEGACY)


def test_contenedor_eliminado_archiva_sus_fletes():
    existentes = {
        ("D-001", TRAMO_MARITIMO): {"id": "li-m1", "properties": {
            "name": "D-001 - FLETE MARITIMO", "numero_container": "D-001",
            "price": "200", "cardcode": "TRANS-MAR",
            "tipo_iva_repercutido": "R21", "tipo_iva_soportado": "S21", "almacen": "10",
        }},
        ("D-002", TRAMO_MARITIMO): {"id": "li-m2", "properties": {
            "name": "D-002 - FLETE MARITIMO", "numero_container": "D-002",
            "price": "200", "cardcode": "TRANS-MAR",
        }},
        ("D-002", TRAMO_TERRESTRE): {"id": "li-t2", "properties": {
            "name": "D-002 - FLETE TERRESTRE", "numero_container": "D-002",
            "price": "100", "cardcode": "TRANS-TERR",
        }},
    }
    _, to_create, to_delete, _ = _plan_fletes_upsert(
        existentes, {"D-001"}, _specs_doble(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert sorted(to_delete) == ["li-m2", "li-t2"]
    # el terrestre de D-001 falta → se crea
    assert [x["properties"]["name"] for x in to_create] == ["D-001 - FLETE TERRESTRE"]


def test_sin_producto_del_tramo_no_crea_esa_linea():
    specs = _specs_doble()
    specs[1]["product_id"] = None
    _, to_create, _, _ = _plan_fletes_upsert(
        {}, {"D-001"}, specs, CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert [x["properties"]["name"] for x in to_create] == ["D-001 - FLETE MARITIMO"]


def test_toggle_doble_a_simple_archiva_tramos_y_deja_la_combinada():
    items = [
        _li("li-m", "D-001 - FLETE MARITIMO", "D-001"),
        _li("li-t", "D-001 - FLETE TERRESTRE", "D-001"),
    ]
    idx = _indexar_fletes_existentes(items)
    to_update, to_create, to_delete, persisted = _plan_fletes_upsert(
        idx, {"D-001"}, _spec_legacy(), CONTAINER_BY_KEY, "10", "deal-1",
    )
    assert sorted(to_delete) == ["li-m", "li-t"]
    assert to_update == [] and persisted == {}
    assert [x["properties"]["name"] for x in to_create] == ["D-001 - FLETES"]
    assert to_create[0]["_target_key"] == ("D-001", TRAMO_LEGACY)

    # Elementos espejo: el route borra por line_item_id los de las líneas archivadas
    elementos_by_lid = {
        "li-m": {"id": "el-m"},
        "li-t": {"id": "el-t"},
    }
    borrados = [elementos_by_lid[str(lid)]["id"] for lid in to_delete if str(lid) in elementos_by_lid]
    assert sorted(borrados) == ["el-m", "el-t"]


def _li_flete_parse(id, name, cardcode=None):
    props = {
        "name": name,
        "hs_product_type": "Flete",
        "price": "150",
        "quantity": "1",
        "numero_container": "",
    }
    if cardcode is not None:
        props["cardcode"] = cardcode
    return {"id": id, "properties": props}


def test_parse_elemento_flete_conserva_cardcode_del_line_item():
    _, standalone = _parse_line_items_to_custom_objects(
        [_li_flete_parse("li-1", "D-001 - FLETE TERRESTRE", cardcode="TERR")],
        deal_transitario="MAR",
    )
    assert standalone[0]["cardcode"] == "TERR"


def test_parse_elemento_flete_sin_cardcode_cae_al_transitario_del_deal():
    _, standalone = _parse_line_items_to_custom_objects(
        [_li_flete_parse("li-1", "D-001 - FLETES")],
        deal_transitario="MAR",
    )
    assert standalone[0]["cardcode"] == "MAR"
