"""El reconciliador del espejo corre tras el publish y en ningún otro job.

Negocio 65489559709 (02/10/2026): bajó de 2x20 a 1x20. POST /containers archivó las
cuatro líneas del -002 y encoló el re-sync, pero el worker reconcilió antes: el
Contenedor -002 seguía vivo, lo tomó por perdido en el espejo y rehízo sus líneas.
El re-sync ya no tuvo nada que borrar y el negocio siguió con dos contenedores.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import worker_handler
from app.routes import deals
from app.services import espejo_publish, hubspot_client, pedidos_sync
from app.workers import custom_objects_worker

DEAL = "65489559709"


@pytest.fixture
def worker(monkeypatch):
    llamadas = []
    monkeypatch.setattr(worker_handler, "get_job", lambda job_id: None)
    monkeypatch.setattr(worker_handler, "update_job", lambda *a, **k: None)
    monkeypatch.setattr(custom_objects_worker, "update_job", lambda *a, **k: None)
    monkeypatch.setattr(custom_objects_worker, "process_deal_custom_objects_from_line_items",
                        lambda deal_id, progress_callback=None: llamadas.append("resync") or {})
    monkeypatch.setattr(espejo_publish, "reconciliar_lineas_tras_espejo",
                        lambda token, deal_id: llamadas.append("reconciliar"))
    monkeypatch.setattr(pedidos_sync, "reconciliar_pedidos_si_ganada", lambda *a: None)
    monkeypatch.setattr(hubspot_client.hubspot_client, "get_client",
                        lambda: SimpleNamespace(access_token="token"))
    return llamadas


@pytest.fixture
def encolados(monkeypatch):
    payloads = []
    monkeypatch.setattr(deals, "create_job", lambda **k: None)
    monkeypatch.setattr(deals, "enqueue_job", lambda **k: payloads.append(k["payload"]))
    return payloads


def _procesar(payload):
    body = {"job_id": "job-1", "job_type": deals.CUSTOM_OBJECTS_JOB_TYPE, "payload": payload}
    worker_handler._process_record({"body": json.dumps(body)})


def test_el_resync_de_contenedores_no_reconcilia(monkeypatch, worker, encolados):
    monkeypatch.setattr(deals, "get_contenedores_by_deal",
                        lambda client, deal_id, properties=None: [{"id": "62998731286"}])

    assert deals._enqueue_custom_objects_resync_if_published(object(), DEAL)
    _procesar(encolados[0])

    assert worker == ["resync"]


def test_el_publish_reconcilia_antes_del_resync(monkeypatch, worker, encolados):
    basic_api = SimpleNamespace(get_by_id=lambda **k: None)
    cliente = SimpleNamespace(crm=SimpleNamespace(deals=SimpleNamespace(basic_api=basic_api)))
    monkeypatch.setattr(deals, "hubspot_client", SimpleNamespace(get_client=lambda: cliente))

    asyncio.run(deals.publish_custom_objects(deal_id=DEAL))
    _procesar(encolados[0])

    assert worker == ["reconciliar", "resync"]
