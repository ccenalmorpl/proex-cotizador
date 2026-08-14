"""Tests del nombre de transitario en las Rutas: resolución por cardcode contra
Proveedores e hidratación/backfill de las rutas que solo guardaron el cardcode."""

import pytest

from app.models.schemas import RutaItem
from app.routes import deals


class _Client:
    access_token = "token"


class _Response:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeRequests:
    """Sustituye a `requests` en el módulo: devuelve respuestas encoladas por URL."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.llamadas.append({"url": url, "json": json})
        for fragmento, cola in self.respuestas.items():
            if fragmento in url:
                return cola.pop(0) if len(cola) > 1 else cola[0]
        return _Response({"results": []})


def _proveedor(id, cardcode, name, tipo_proveedor="transitario"):
    return {
        "id": id,
        "properties": {"cardcode": cardcode, "name": name, "tipo_proveedor": tipo_proveedor},
    }


def _ruta(id, cardcode, transitario=None, tipo_tramo="maritima"):
    return RutaItem(
        id=id,
        referencia=f"D20DR__{cardcode}__NA__NA__NA__NA__NA",
        tipo_contenedor="D20DR",
        transitario=transitario,
        transitario_id=None,
        transitario_cardcode=cardcode,
        importe=2525.0,
        fecha_caducidad="2026-08-31",
        tipo_tramo=tipo_tramo,
    )


@pytest.fixture
def fake_requests(monkeypatch):
    def _instalar(respuestas):
        fake = _FakeRequests(respuestas)
        monkeypatch.setattr(deals, "requests", fake)
        return fake

    return _instalar


def test_lookup_devuelve_nombre_por_cardcode(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": [_proveedor("1", "P0557", "TRANSCOMA")]})]
    })

    assert deals._lookup_transitarios_por_cardcode(_Client(), ["P0557"]) == {
        "P0557": {"id": "1", "name": "TRANSCOMA"}
    }
    assert fake.llamadas[0]["json"]["filterGroups"][0]["filters"][0]["values"] == ["P0557"]


def test_lookup_sin_cardcodes_no_llama_a_hubspot(fake_requests):
    fake = fake_requests({})

    assert deals._lookup_transitarios_por_cardcode(_Client(), [None, "", "   "]) == {}
    assert fake.llamadas == []


def test_lookup_prefiere_el_proveedor_marcado_como_transitario(fake_requests):
    fake_requests({
        "2-43108800/search": [_Response({"results": [
            _proveedor("1", "P0409", "OTRO PROVEEDOR", tipo_proveedor="fabricante"),
            _proveedor("2", "P0409", "RHENUS LOGISTICS"),
        ]})]
    })

    resuelto = deals._lookup_transitarios_por_cardcode(_Client(), ["P0409"])
    assert resuelto["P0409"] == {"id": "2", "name": "RHENUS LOGISTICS"}


def test_lookup_no_pisa_el_transitario_con_un_proveedor_posterior(fake_requests):
    fake_requests({
        "2-43108800/search": [_Response({"results": [
            _proveedor("2", "P0409", "RHENUS LOGISTICS"),
            _proveedor("1", "P0409", "OTRO PROVEEDOR", tipo_proveedor="fabricante"),
        ]})]
    })

    resuelto = deals._lookup_transitarios_por_cardcode(_Client(), ["P0409"])
    assert resuelto["P0409"]["name"] == "RHENUS LOGISTICS"


def test_lookup_pagina(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [
            _Response({"results": [_proveedor("1", "P0557", "TRANSCOMA")],
                       "paging": {"next": {"after": "50"}}}),
            _Response({"results": [_proveedor("2", "P0761", "DSV")]}),
        ]
    })

    resuelto = deals._lookup_transitarios_por_cardcode(_Client(), ["P0557", "P0761"])
    assert set(resuelto) == {"P0557", "P0761"}
    assert fake.llamadas[1]["json"]["after"] == "50"


def test_lookup_ignora_error_http(fake_requests):
    fake_requests({"2-43108800/search": [_Response({}, ok=False, status_code=500)]})

    assert deals._lookup_transitarios_por_cardcode(_Client(), ["P0557"]) == {}


def test_hidratar_rellena_nombre_e_id_y_hace_backfill(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": [_proveedor("77", "P0557", "TRANSCOMA")]})],
        "2-53295516/batch/update": [_Response({"results": []})],
    })
    rutas = [_ruta("100", "P0557"), _ruta("101", "P0557", tipo_tramo="terrestre")]

    deals._hidratar_transitario_rutas(_Client(), rutas)

    assert [r.transitario for r in rutas] == ["TRANSCOMA", "TRANSCOMA"]
    assert [r.transitario_id for r in rutas] == ["77", "77"]
    backfill = [c for c in fake.llamadas if "batch/update" in c["url"]]
    assert backfill[0]["json"]["inputs"] == [
        {"id": "100", "properties": {"transitario_name": "TRANSCOMA"}},
        {"id": "101", "properties": {"transitario_name": "TRANSCOMA"}},
    ]


def test_hidratar_respeta_el_nombre_persistido(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": [_proveedor("77", "P0557", "TRANSCOMA")]})],
    })
    rutas = [_ruta("100", "P0557", transitario="NOMBRE EN LA RUTA")]

    deals._hidratar_transitario_rutas(_Client(), rutas)

    assert rutas[0].transitario == "NOMBRE EN LA RUTA"
    assert rutas[0].transitario_id == "77"
    assert [c for c in fake.llamadas if "batch/update" in c["url"]] == []


def test_hidratar_sin_rutas_no_llama_a_hubspot(fake_requests):
    fake = fake_requests({})

    deals._hidratar_transitario_rutas(_Client(), [])

    assert fake.llamadas == []


def test_hidratar_cardcode_sin_proveedor_deja_la_ruta_intacta(fake_requests):
    fake = fake_requests({"2-43108800/search": [_Response({"results": []})]})
    rutas = [_ruta("100", "P9999")]

    deals._hidratar_transitario_rutas(_Client(), rutas)

    assert rutas[0].transitario is None
    assert [c for c in fake.llamadas if "batch/update" in c["url"]] == []


def test_hidratar_backfill_desactivado(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": [_proveedor("77", "P0557", "TRANSCOMA")]})],
    })
    rutas = [_ruta("100", "P0557")]

    deals._hidratar_transitario_rutas(_Client(), rutas, backfill=False)

    assert rutas[0].transitario == "TRANSCOMA"
    assert [c for c in fake.llamadas if "batch/update" in c["url"]] == []


def test_crear_ruta_persiste_el_nombre_recibido(fake_requests):
    fake = fake_requests({"objects/2-53295516": [_Response({"id": "500"})]})

    ruta_id = deals._crear_ruta(
        client=_Client(),
        tipo_ruta="maritima",
        transitario="P0557",
        tipo_contenedor="D20DR",
        importe=2525.0,
        fecha_caducidad=1798675200000,
        puerto_salida="VLC",
        puerto_destino="DLA",
        transitario_name="TRANSCOMA",
    )

    assert ruta_id == "500"
    props = fake.llamadas[0]["json"]["properties"]
    assert props["transitario_name"] == "TRANSCOMA"
    assert props["transitario"] == "P0557"


def test_crear_ruta_resuelve_el_nombre_si_no_llega(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": [_proveedor("77", "P0557", "TRANSCOMA")]})],
        "objects/2-53295516": [_Response({"id": "500"})],
    })

    deals._crear_ruta(
        client=_Client(),
        tipo_ruta="terrestre",
        transitario="P0557",
        tipo_contenedor="D20DR",
        importe=525.0,
        fecha_caducidad=1798675200000,
        lugar_carga="SALEM",
        puerto_salida="VLC",
    )

    creacion = [c for c in fake.llamadas if c["url"].endswith("objects/2-53295516")][0]
    assert creacion["json"]["properties"]["transitario_name"] == "TRANSCOMA"


def test_crear_ruta_sin_nombre_resoluble_no_manda_la_propiedad(fake_requests):
    fake = fake_requests({
        "2-43108800/search": [_Response({"results": []})],
        "objects/2-53295516": [_Response({"id": "500"})],
    })

    deals._crear_ruta(
        client=_Client(),
        tipo_ruta="terrestre",
        transitario="P9999",
        tipo_contenedor="D20DR",
        importe=525.0,
        fecha_caducidad=1798675200000,
        lugar_carga="SALEM",
        puerto_salida="VLC",
    )

    creacion = [c for c in fake.llamadas if c["url"].endswith("objects/2-53295516")][0]
    assert "transitario_name" not in creacion["json"]["properties"]


def test_update_ruta_repone_el_nombre(monkeypatch):
    llamadas = []

    class _FakePatch:
        def patch(self, url, headers=None, json=None, timeout=None):
            llamadas.append(json)
            return _Response({})

    monkeypatch.setattr(deals, "requests", _FakePatch())

    assert deals._update_ruta(_Client(), "100", 525.0, 1798675200000, transitario_name="TRANSCOMA")
    assert llamadas[0]["properties"]["transitario_name"] == "TRANSCOMA"


def test_update_ruta_sin_nombre_no_toca_la_propiedad(monkeypatch):
    llamadas = []

    class _FakePatch:
        def patch(self, url, headers=None, json=None, timeout=None):
            llamadas.append(json)
            return _Response({})

    monkeypatch.setattr(deals, "requests", _FakePatch())

    deals._update_ruta(_Client(), "100", 525.0, 1798675200000, transitario_name="   ")

    assert "transitario_name" not in llamadas[0]["properties"]
