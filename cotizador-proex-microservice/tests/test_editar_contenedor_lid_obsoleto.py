"""Editar un contenedor de un negocio publicado y cambiar su artículo dejaba vivo el
Elemento viejo: su `line_item_id` apuntaba a una línea que el espejo de HubSpot
ya había archivado y el borrado solo buscaba por lid (65571985983, 15/10)."""
from types import SimpleNamespace

import pytest

from app.routes import deals

CONT = "65571985983-002"
NOMBRE_VIEJO = f"{CONT} - ENERDRINK-4ERVTLBC "
NOMBRE_NUEVO = f"{CONT} - ENERDRINK-SELLO FISCAL-4ERVTLBG "


def _elemento(eid, nombre, lid, tipo="fabricante"):
    return {"id": eid, "properties": {"nombre": nombre, "line_item_id": lid, "tipo_articulo": tipo}}


def _articulo_nuevo():
    edit = SimpleNamespace(
        articuloId="42050417042", numCajas=3143, price=4.826, precio_de_coste=4.176,
        margen_unitario=0.1557, line_item_id=None, articuloName=NOMBRE_NUEVO,
        tipo_iva_repercutido="EX", tipo_iva_soportado="S21",
    )
    return {
        "edit": edit, "product_props": {}, "unique_name": NOMBRE_NUEVO, "precio_coste": 4.176,
        "cardcode": "P0671", "iva_rep": "EX", "iva_sop": "S21", "new_line_item_id": "li_nuevo",
    }


@pytest.fixture
def entorno(monkeypatch):
    estado = {
        "elementos": [],
        "vivos": set(),
        "borrados": [],
        "creados": [],
        "asociaciones": [],
        "deals_del_contenedor": ["65571985983", "65955896479"],
        "pipelines": {"65571985983": "default", "65955896479": deals.PIPELINE_PEDIDOS},
    }

    monkeypatch.setattr(deals, "get_elementos_by_contenedor", lambda client, cid, properties=None: estado["elementos"])
    monkeypatch.setattr(deals, "get_associated_contacts", lambda deal_id: ["c1"])
    monkeypatch.setattr(deals, "get_associated_companies", lambda deal_id: ["co1"])
    monkeypatch.setattr(deals, "update_elemento", lambda client, eid, props: True)
    monkeypatch.setattr(deals, "delete_elemento", lambda client, eid: estado["borrados"].append(eid) or True)
    monkeypatch.setattr(deals, "sync_contenedor_proveedor_associations", lambda *a, **k: None)

    def _crear(client, elementos_data, **k):
        ids = [f"nuevo{len(estado['creados']) + i}" for i in range(len(elementos_data))]
        estado["creados"].extend(ids)
        return ids

    monkeypatch.setattr(deals, "batch_create_elementos_with_associations", _crear)

    def _vivos(client, ids):
        if estado["vivos"] is None:
            return None
        return {i for i in ids if i in estado["vivos"]}

    monkeypatch.setattr(deals, "_line_items_vivos", _vivos)
    monkeypatch.setattr(deals, "fetch_associated_ids", lambda client, f, fid, t: estado["deals_del_contenedor"])

    def _post(url, headers=None, json=None, timeout=None):
        assert url.endswith("/crm/v3/objects/deals/batch/read"), url
        results = [{"id": i["id"], "properties": {"pipeline": estado["pipelines"][i["id"]]}} for i in json["inputs"]]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"results": results})

    monkeypatch.setattr(deals.requests, "post", _post)

    def _asociar(token, desde, hacia, tipo, pares, **k):
        estado["asociaciones"].append((desde, hacia, tipo, list(pares)))
        return len(pares), []

    monkeypatch.setattr(deals, "batch_create_associations_v4", _asociar)
    return estado


def _sync(**k):
    defaults = dict(
        client=SimpleNamespace(access_token="t"), deal_id="65571985983", contenedor_co_id="62949770647",
        container_name=CONT, payload_with_id=[], new_articles_resolved=[], to_delete_line_item_ids=[],
        current_by_id={}, intermediario_ctx=None, current_intermediario=None,
        intermediario_recalculated=False, total_cajas_new=3143,
    )
    defaults.update(k)
    return deals._sync_elementos_for_container(**defaults)


def test_cambiar_articulo_borra_el_elemento_viejo_con_lid_de_linea_archivada(entorno):
    entorno["elementos"] = [
        _elemento("e_viejo", NOMBRE_VIEJO, "59467213318"),
        _elemento("e_flete", f"{CONT} - FLETES", "59469525312", tipo="flete"),
    ]
    _sync(
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={"59423467999": {"properties": {"name": NOMBRE_VIEJO}}},
    )
    assert entorno["borrados"] == ["e_viejo"]


def test_el_elemento_nuevo_se_asocia_al_negocio_pedido(entorno):
    entorno["elementos"] = [_elemento("e_viejo", NOMBRE_VIEJO, "59467213318")]
    _sync(
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={"59423467999": {"properties": {"name": NOMBRE_VIEJO}}},
    )
    assert entorno["asociaciones"] == [
        ("deals", deals.OBJECT_TYPE_ELEMENTO, deals.ASSOCIATION_TYPE_ID_PEDIDO_TO_ELEMENTO, [("65955896479", "nuevo0")])
    ]


def test_sin_negocio_pedido_no_asocia_nada(entorno):
    entorno["deals_del_contenedor"] = ["65571985983"]
    _sync(new_articles_resolved=[_articulo_nuevo()])
    assert entorno["asociaciones"] == [("deals", deals.OBJECT_TYPE_ELEMENTO, deals.ASSOCIATION_TYPE_ID_PEDIDO_TO_ELEMENTO, [])]


def test_no_borra_si_el_line_item_del_elemento_sigue_vivo(entorno):
    entorno["elementos"] = [_elemento("e_viejo", NOMBRE_VIEJO, "li_vivo")]
    entorno["vivos"] = {"li_vivo"}
    _sync(
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={"59423467999": {"properties": {"name": NOMBRE_VIEJO}}},
    )
    assert entorno["borrados"] == []


def test_no_borra_si_hubspot_no_confirma_que_la_linea_esta_archivada(entorno):
    entorno["elementos"] = [_elemento("e_viejo", NOMBRE_VIEJO, "59467213318")]
    entorno["vivos"] = None
    _sync(
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={"59423467999": {"properties": {"name": NOMBRE_VIEJO}}},
    )
    assert entorno["borrados"] == []


def test_lid_al_dia_sigue_borrando_por_lid_y_una_sola_vez(entorno):
    entorno["elementos"] = [_elemento("e_viejo", NOMBRE_VIEJO, "59423467999")]
    _sync(
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={"59423467999": {"properties": {"name": NOMBRE_VIEJO}}},
    )
    assert entorno["borrados"] == ["e_viejo"]


def test_articulo_que_se_queda_no_se_toca_aunque_su_lid_este_obsoleto(entorno):
    otro = f"{CONT} - OTRO ARTICULO "
    entorno["elementos"] = [
        _elemento("e_viejo", NOMBRE_VIEJO, "59467213318"),
        _elemento("e_otro", otro, "59467213399"),
    ]
    sigue = SimpleNamespace(
        line_item_id="li_otro", articuloName=otro, price=4.826, precio_de_coste=4.176,
        numCajas=100, margen_unitario=None, tipo_iva_repercutido=None, tipo_iva_soportado=None,
    )
    _sync(
        payload_with_id=[sigue],
        new_articles_resolved=[_articulo_nuevo()],
        to_delete_line_item_ids=["59423467999"],
        current_by_id={
            "59423467999": {"properties": {"name": NOMBRE_VIEJO}},
            "li_otro": {"properties": {"name": otro}},
        },
    )
    assert entorno["borrados"] == ["e_viejo"]
