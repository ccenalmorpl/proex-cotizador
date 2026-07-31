"""Empresas asociadas por la relación "Proveedor-Cliente" (empresa↔empresa).

Alimenta el selector de autorrelleno de la "Dirección de Entrega" en
DocumentacionTab. Se filtra por category USER_DEFINED para excluir la jerarquía
matriz/filial estándar de HubSpot; la dirección sale de company.address_entrega.
"""

import asyncio
from types import SimpleNamespace

from app.routes import deals
from app.core.custom_objects_constants import ASSOC_CATEGORY_USER_DEFINED


def _assoc(to_id, cats):
    return SimpleNamespace(
        to_object_id=to_id,
        association_types=[SimpleNamespace(category=c, type_id=t) for c, t in cats],
    )


def _client(pages, companies=None):
    """pages: lista de (results, next_after) que get_page va sirviendo en orden.
    companies: {id: props} que devuelve el batch read de empresas."""
    calls = {"i": 0}

    def get_page(**kwargs):
        results, next_after = pages[calls["i"]]
        calls["i"] += 1
        paging = (
            SimpleNamespace(next=SimpleNamespace(after=next_after))
            if next_after
            else None
        )
        return SimpleNamespace(results=results, paging=paging)

    def batch_read(batch_read_input_simple_public_object_id=None):
        pedidos = [i["id"] for i in batch_read_input_simple_public_object_id["inputs"]]
        return SimpleNamespace(results=[
            SimpleNamespace(id=cid, properties=(companies or {}).get(cid, {}))
            for cid in pedidos
        ])

    return SimpleNamespace(
        crm=SimpleNamespace(
            associations=SimpleNamespace(
                v4=SimpleNamespace(basic_api=SimpleNamespace(get_page=get_page))
            ),
            companies=SimpleNamespace(batch_api=SimpleNamespace(read=batch_read)),
        )
    )


def test_filtra_user_defined_y_excluye_hubspot_defined(monkeypatch):
    client = _client([
        (
            [
                _assoc("11", [(ASSOC_CATEGORY_USER_DEFINED, 711)]),
                _assoc("22", [("HUBSPOT_DEFINED", 1)]),   # matriz/filial → excluida
                _assoc("33", [("HUBSPOT_DEFINED", 1), (ASSOC_CATEGORY_USER_DEFINED, 712)]),  # inversa del par → incluida
            ],
            None,
        ),
    ])
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    assert deals._get_empresas_asociadas_ids("999") == ["11", "33"]


def test_pagina_y_deduplica(monkeypatch):
    client = _client([
        ([_assoc("11", [(ASSOC_CATEGORY_USER_DEFINED, 711)])], "cursor2"),
        ([
            _assoc("11", [(ASSOC_CATEGORY_USER_DEFINED, 711)]),  # duplicado
            _assoc("44", [(ASSOC_CATEGORY_USER_DEFINED, 711)]),
        ], None),
    ])
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    assert deals._get_empresas_asociadas_ids("999") == ["11", "44"]


def test_error_de_lectura_no_propaga(monkeypatch):
    def _boom(**k):
        raise RuntimeError("hubspot down")

    client = SimpleNamespace(
        crm=SimpleNamespace(
            associations=SimpleNamespace(
                v4=SimpleNamespace(basic_api=SimpleNamespace(get_page=_boom))
            )
        )
    )
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    assert deals._get_empresas_asociadas_ids("999") == []


def test_endpoint_mapea_nombre_y_direccion_ordenado(monkeypatch):
    client = _client(
        [([
            _assoc("11", [(ASSOC_CATEGORY_USER_DEFINED, 711)]),
            _assoc("22", [(ASSOC_CATEGORY_USER_DEFINED, 711)]),
        ], None)],
        companies={
            "11": {"name": "Beta SL", "address_entrega": "Calle B 2"},
            "22": {"name": "Alpha SA", "address_entrega": ""},
        },
    )
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    monkeypatch.setattr(deals, "get_associated_companies", lambda deal_id: ["999"])

    resp = asyncio.run(deals.get_deal_empresas_asociadas("46298478914"))

    assert resp.success is True
    # Ordenadas por nombre (case-insensitive): Alpha antes que Beta.
    assert [(e.id, e.name, e.address_entrega) for e in resp.empresas] == [
        ("22", "Alpha SA", None),   # address vacío → None
        ("11", "Beta SL", "Calle B 2"),
    ]


def test_endpoint_sin_empresa_en_el_deal_devuelve_vacio(monkeypatch):
    def _no_leer(*a, **k):
        raise AssertionError("no debe leer asociaciones sin empresa en el deal")

    monkeypatch.setattr(deals, "get_associated_companies", lambda deal_id: [])
    monkeypatch.setattr(deals, "_get_empresas_asociadas_ids", _no_leer)
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: SimpleNamespace())

    resp = asyncio.run(deals.get_deal_empresas_asociadas("46298478914"))
    assert resp.success is True and resp.empresas == []


def test_endpoint_sin_asociadas_devuelve_vacio(monkeypatch):
    client = _client([([], None)])
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    monkeypatch.setattr(deals, "get_associated_companies", lambda deal_id: ["999"])

    resp = asyncio.run(deals.get_deal_empresas_asociadas("46298478914"))
    assert resp.success is True and resp.empresas == []
