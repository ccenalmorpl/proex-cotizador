"""Tests de `_sanear_prefijos_heredados`: los line items que un negocio clonado
arrastra con el numero_container del negocio origen se reasignan al actual (o se
archivan si no hay contenedor destino). Ver incidencia EL SYRIANO'S 63837047422,
donde 3 líneas de SEGURO MERCANCÍA quedaron en '63756493976-00X' y nunca llegaron
ni a Elemento ni a SAP."""

import pytest

from app.routes import deals
from app.routes.deals import _sanear_prefijos_heredados

DEAL = "63837047422"
ORIGEN = "63756493976"


class _FakeResp:
    ok = True
    status_code = 200
    text = ""


class _SpyRequests:
    """Captura los batch update/archive que la función lanza contra HubSpot."""

    def __init__(self):
        self.updates = []
        self.archives = []

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/batch/update"):
            self.updates.extend(json["inputs"])
        elif url.endswith("/batch/archive"):
            self.archives.extend(x["id"] for x in json["inputs"])
        else:
            raise AssertionError(f"llamada inesperada a {url}")
        return _FakeResp()


@pytest.fixture
def spy(monkeypatch):
    s = _SpyRequests()
    monkeypatch.setattr(deals, "requests", s)
    return s


def _li(id, numero_container, name, hs_product_type="Servicio", tipo_articulo="seguro"):
    return {"id": id, "properties": {
        "numero_container": numero_container,
        "name": name,
        "hs_product_type": hs_product_type,
        "tipo_articulo": tipo_articulo,
    }}


def _contenedor(id, numero_container):
    return _li(id, numero_container, numero_container,
               hs_product_type="container", tipo_articulo="transitario")


def _sanear(items, spy):
    return _sanear_prefijos_heredados(
        deal_id=DEAL, all_line_items=items, headers={}, batch_size=100,
    )


def test_sin_lineas_ajenas_no_escribe_nada(spy):
    items = [
        _contenedor("1", f"{DEAL}-001"),
        _li("2", f"{DEAL}-001", f"{DEAL}-001 - SEGURO MERCANCÍA"),
    ]
    assert _sanear(items, spy) == items
    assert spy.updates == []
    assert spy.archives == []


def test_seguro_heredado_se_reasigna_al_negocio_actual(spy):
    items = [
        _contenedor("1", f"{DEAL}-001"),
        _contenedor("2", f"{DEAL}-002"),
        _li("3", f"{ORIGEN}-001", f"{ORIGEN}-001 - SEGURO MERCANCÍA"),
        _li("4", f"{ORIGEN}-002", f"{ORIGEN}-002 - SEGURO MERCANCÍA"),
    ]
    vivos = _sanear(items, spy)

    assert spy.archives == []
    assert {u["id"] for u in spy.updates} == {"3", "4"}
    assert {u["properties"]["numero_container"] for u in spy.updates} == {
        f"{DEAL}-001", f"{DEAL}-002"}
    assert {u["properties"]["name"] for u in spy.updates} == {
        f"{DEAL}-001 - SEGURO MERCANCÍA", f"{DEAL}-002 - SEGURO MERCANCÍA"}
    assert len(vivos) == 4


def test_clon_integro_sin_contenedores_propios_conserva_la_numeracion(spy):
    # Recién clonado: hasta el contenedor lleva el prefijo del origen.
    items = [
        _contenedor("1", f"{ORIGEN}-001"),
        _li("2", f"{ORIGEN}-001", f"{ORIGEN}-001 - SEGURO MERCANCÍA"),
        _li("3", f"{ORIGEN}-007", f"{ORIGEN}-007 - FLETES",
            hs_product_type="flete", tipo_articulo="transitario"),
    ]
    _sanear(items, spy)

    assert spy.archives == []
    numeros = {u["id"]: u["properties"]["numero_container"] for u in spy.updates}
    assert numeros == {"1": f"{DEAL}-001", "2": f"{DEAL}-001", "3": f"{DEAL}-007"}


def test_sufijo_sin_contenedor_destino_se_archiva(spy):
    items = [
        _contenedor("1", f"{DEAL}-001"),
        _li("2", f"{ORIGEN}-004", f"{ORIGEN}-004 - SEGURO MERCANCÍA"),
    ]
    vivos = _sanear(items, spy)

    assert spy.updates == []
    assert spy.archives == ["2"]
    assert [li["id"] for li in vivos] == ["1"]


def test_props_quedan_saneadas_en_memoria_para_el_merge(spy):
    heredado = _li("3", f"{ORIGEN}-002", f"{ORIGEN}-002 - SEGURO MERCANCÍA")
    _sanear([_contenedor("1", f"{DEAL}-002"), heredado], spy)

    assert heredado["properties"]["numero_container"] == f"{DEAL}-002"
    assert heredado["properties"]["name"] == f"{DEAL}-002 - SEGURO MERCANCÍA"


def test_numeracion_legacy_sin_prefijo_se_ignora(spy):
    items = [
        _contenedor("1", "1"),
        _li("2", "1", "1 - SEGURO MERCANCÍA"),
    ]
    assert _sanear(items, spy) == items
    assert spy.updates == []
    assert spy.archives == []


def test_name_sin_sufijo_recibe_el_prefijo_nuevo(spy):
    items = [
        _contenedor("1", f"{DEAL}-001"),
        _li("2", f"{ORIGEN}-001", "SEGURO MERCANCÍA"),
    ]
    _sanear(items, spy)

    assert spy.updates[0]["properties"]["name"] == f"{DEAL}-001 - SEGURO MERCANCÍA"


def test_el_propio_deal_no_alimenta_los_sufijos_desde_una_linea_no_contenedor(spy):
    # Solo los contenedores definen qué sufijos existen: un servicio propio en -009
    # no legitima un heredado en -009.
    items = [
        _contenedor("1", f"{DEAL}-001"),
        _li("2", f"{DEAL}-009", f"{DEAL}-009 - ADUANA", tipo_articulo="servicio"),
        _li("3", f"{ORIGEN}-009", f"{ORIGEN}-009 - SEGURO MERCANCÍA"),
    ]
    _sanear(items, spy)

    assert spy.archives == ["3"]
