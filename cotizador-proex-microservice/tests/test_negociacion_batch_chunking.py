"""Tests del troceo del batch en POST /deals/{id}/negociacion.

Caso real (ticket Elena, deal 64484736696, 2026-08-31): una proforma de 12
contenedores x 9 productos manda 108 line items en un solo payload y HubSpot
rechaza el batch entero con "The maximum number of inputs supported in a batch
is 100", así que la pestaña de Negociación no dejaba avanzar.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.schemas import UpdateNegociacionRequest
from app.routes import deals as deals_module


def _articulo(lid):
    return {
        "line_item_id": lid,
        "price": 5.37376,
        "margen_unitario": 0.12,
        "tipo_iva_repercutido": "EX",
        "tipo_iva_soportado": "S21",
        "precio_negociado_tipo_venta": 6.252,
        "incoterm_precio_negociado": "CIF",
    }


@pytest.fixture
def entorno(monkeypatch):
    client = MagicMock()
    client.access_token = "tok"
    client.crm.companies.basic_api.get_by_id.return_value = SimpleNamespace(
        properties={"residennum": "", "equ": ""}
    )

    chunks = []

    def _batch_update(batch_input_simple_public_object_batch_input=None):
        inputs = batch_input_simple_public_object_batch_input["inputs"]
        if len(inputs) > 100:
            raise RuntimeError("(400) maximum number of inputs supported in a batch is 100")
        chunks.append(inputs)
        return SimpleNamespace(results=inputs)

    client.crm.line_items.batch_api.update.side_effect = _batch_update

    monkeypatch.setattr(deals_module.hubspot_client, "get_client", lambda: client)
    monkeypatch.setattr(deals_module, "get_associated_companies", lambda _d: [])
    monkeypatch.setattr(deals_module, "get_elementos_by_deal", lambda *a, **k: [])
    monkeypatch.setattr(deals_module, "_resolve_contenedor_co_by_numero", lambda *a, **k: {})

    def _fake_post(url, headers=None, json=None, timeout=None):
        ids = [i["id"] for i in json["inputs"]]
        assert len(ids) <= 100, "el batch read tampoco puede pasar de 100"
        return SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"results": [
                {"id": i, "properties": {
                    "tipo_articulo": "fabricante",
                    "quantity": "1200",
                    "name": f"PROD {i}",
                    "numero_container": "64484736696-001",
                }} for i in ids
            ]},
        )

    monkeypatch.setattr(deals_module.requests, "post", _fake_post)
    return client, chunks


def test_108_line_items_se_trocean_en_lotes_de_100(entorno):
    _client, chunks = entorno
    ids = [str(58476615300 + n) for n in range(108)]
    data = UpdateNegociacionRequest(articulos=[_articulo(i) for i in ids])

    res = asyncio.run(deals_module.update_deal_negociacion(data, deal_id="64484736696"))

    assert res.success is True
    assert [len(c) for c in chunks] == [100, 8]
    assert [i["id"] for c in chunks for i in c] == ids


def test_line_item_repetido_no_se_duplica_entre_chunks(entorno):
    _client, chunks = entorno
    ids = [str(58476615300 + n) for n in range(100)] + ["58476615300"]
    data = UpdateNegociacionRequest(articulos=[_articulo(i) for i in ids])

    asyncio.run(deals_module.update_deal_negociacion(data, deal_id="64484736696"))

    enviados = [i["id"] for c in chunks for i in c]
    assert len(enviados) == len(set(enviados)) == 100
    assert len(chunks) == 1
