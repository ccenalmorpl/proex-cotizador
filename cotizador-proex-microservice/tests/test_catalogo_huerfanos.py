"""Tests de `_catalogo_huerfano_ids`: detección de line items de catálogo
huérfanos (fabricante sin numero_container) que deben archivarse al crear
contenedores y excluirse de la copia al quote. Función pura."""

from app.routes.deals import _catalogo_huerfano_ids

CONTAINERS = {"61763928105-001", "61763928105-002"}


def _li(id, name, tipo_articulo="fabricante", hs_product_type="Articulo", numero_container=None):
    props = {"name": name, "tipo_articulo": tipo_articulo, "hs_product_type": hs_product_type}
    if numero_container is not None:
        props["numero_container"] = numero_container
    return {"id": id, "properties": props}


def test_catalogo_sin_contenedor_es_huerfano():
    items = [_li("1", "BEBIDA GASIFICADA-PRIMO-UVA TINTA-BOTELLA-6")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["1"]


def test_articulo_per_contenedor_no_se_toca():
    items = [_li("2", "61763928105-001 - VINO TINTO", numero_container="61763928105-001")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == []


def test_sync_back_con_prefijo_de_contenedor_se_protege():
    # Copia regenerada por HubSpot: perdió numero_container pero conserva el name.
    items = [_li("3", "61763928105-002 - VINO TINTO")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == []


def test_prefijo_de_contenedor_desconocido_no_protege():
    items = [_li("4", "99999999999-001 - VINO TINTO")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["4"]


def test_contenedor_y_servicios_no_son_candidatos():
    items = [
        _li("5", "61763928105-001", tipo_articulo="transitario", hs_product_type="container"),
        _li("6", "SERVICIO ADUANA", tipo_articulo="servicio", hs_product_type="Servicio"),
        _li("7", "COMISION", tipo_articulo="comisiones", hs_product_type="Servicio"),
        _li("8", "FLETES", tipo_articulo="transitario", hs_product_type="flete"),
    ]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == []


def test_promo_precio_cero_per_contenedor_no_se_toca():
    items = [_li("9", "61763928105-001 - VINO TINTO", numero_container="61763928105-001")]
    items[0]["properties"]["price"] = "0"
    assert _catalogo_huerfano_ids(items, CONTAINERS) == []


def test_numero_container_con_espacios_cuenta_como_vacio():
    items = [_li("10", "VINO BLANCO", numero_container="   ")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["10"]


def test_hs_product_type_variantes_de_articulo():
    items = [
        _li("11", "VINO A", hs_product_type="inventory"),
        _li("12", "VINO B", hs_product_type="non_inventory"),
        _li("13", "VINO C", hs_product_type="ARTICULO"),
        _li("14", "VINO D", hs_product_type="Servicio"),
    ]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["11", "12", "13"]


def test_tipo_articulo_distinto_de_fabricante_no_es_candidato():
    items = [_li("15", "VINO A", tipo_articulo="seguro")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == []


def test_sin_nombres_de_contenedor_detecta_igual():
    # El guard de "hay contenedores" es del callsite; la función solo clasifica.
    items = [_li("16", "VINO A"), _li("17", "61763928105-001 - VINO B")]
    assert _catalogo_huerfano_ids(items, set()) == ["16", "17"]


def test_ids_devueltos_como_str():
    items = [_li(18, "VINO A")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["18"]


def test_properties_none_no_rompe():
    items = [{"id": "19", "properties": None}, _li("20", "VINO A")]
    assert _catalogo_huerfano_ids(items, CONTAINERS) == ["20"]


def test_contenedor_legacy_protege_por_name_y_numero():
    # Contenedores legacy: numero_container="1" pero las líneas per-contenedor
    # llevan el prefijo del name — los callsites deben pasar AMBOS valores.
    legacy = {"1", "61763928105-001"}
    items = [
        _li("21", "61763928105-001 - VINO TINTO"),
        _li("22", "1 - VINO BLANCO"),
        _li("23", "VINO CATALOGO"),
    ]
    assert _catalogo_huerfano_ids(items, legacy) == ["23"]
