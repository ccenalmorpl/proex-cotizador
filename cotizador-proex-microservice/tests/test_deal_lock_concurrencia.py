"""Tests del lock de escritura por deal y del guard de relectura previo a crear
líneas, las dos barreras contra la duplicación por peticiones concurrentes
(incidencia 2026-08-28: dos PATCH simultáneos dejaron 22 líneas donde iban 11)."""

import time

import pytest
from botocore.exceptions import ClientError

from app.routes import deals
from app.routes.deals import _lineas_cambiaron
from app.services import deal_lock


class FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok
        self.status_code = 200 if ok else 500

    def json(self):
        return self._payload


class FakeTable:
    """Emula lo justo de DynamoDB: PutItem condicional sobre expires_at y
    UpdateItem condicional sobre lock_token."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeValues=None):
        key = Item["job_id"]
        actual = self.items.get(key)
        if actual is not None and ConditionExpression:
            ahora = ExpressionAttributeValues[":now"]
            if actual.get("expires_at", 0) >= ahora:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "ocupado"}},
                    "PutItem",
                )
        self.items[key] = dict(Item)

    def update_item(self, Key, UpdateExpression, ConditionExpression=None,
                    ExpressionAttributeValues=None):
        actual = self.items.get(Key["job_id"])
        if actual is None or actual.get("lock_token") != ExpressionAttributeValues[":token"]:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "token distinto"}},
                "UpdateItem",
            )
        actual["expires_at"] = ExpressionAttributeValues[":zero"]


@pytest.fixture
def tabla(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(deal_lock, "_get_table", lambda: fake)
    return fake


def test_segunda_peticion_no_adquiere_el_lock(tabla):
    primera = deal_lock.acquire("deal#1", ttl_seconds=60)
    segunda = deal_lock.acquire("deal#1", ttl_seconds=60)

    assert primera and primera != deal_lock.DEGRADED
    assert segunda is None


def test_tras_liberar_el_lock_vuelve_a_estar_disponible(tabla):
    token = deal_lock.acquire("deal#1", ttl_seconds=60)
    deal_lock.release("deal#1", token)

    assert deal_lock.acquire("deal#1", ttl_seconds=60) not in (None, deal_lock.DEGRADED)


def test_release_con_token_ajeno_no_libera(tabla):
    deal_lock.acquire("deal#1", ttl_seconds=60)
    deal_lock.release("deal#1", "token-de-otro")

    assert deal_lock.acquire("deal#1", ttl_seconds=60) is None


def test_lock_caducado_se_puede_reclamar(tabla):
    deal_lock.acquire("deal#1", ttl_seconds=60)
    tabla.items["lock#deal#1"]["expires_at"] = int(time.time()) - 1

    assert deal_lock.acquire("deal#1", ttl_seconds=60) not in (None, deal_lock.DEGRADED)


def test_deals_distintos_no_se_bloquean(tabla):
    assert deal_lock.acquire("deal#1", ttl_seconds=60) not in (None, deal_lock.DEGRADED)
    assert deal_lock.acquire("deal#2", ttl_seconds=60) not in (None, deal_lock.DEGRADED)


def test_dynamodb_caido_degrada_en_vez_de_bloquear(monkeypatch):
    def boom():
        raise RuntimeError("sin credenciales")

    monkeypatch.setattr(deal_lock, "_get_table", boom)

    assert deal_lock.acquire("deal#1") == deal_lock.DEGRADED


def test_release_degradado_no_explota(monkeypatch):
    monkeypatch.setattr(deal_lock, "_get_table", lambda: (_ for _ in ()).throw(RuntimeError("x")))

    deal_lock.release("deal#1", deal_lock.DEGRADED)
    deal_lock.release("deal#1", None)


def test_guard_detecta_lineas_nuevas_de_otra_peticion():
    assert _lineas_cambiaron({"1", "2", "99"}, {"1": {}, "2": {}}) is True


def test_guard_detecta_lineas_archivadas_por_otra_peticion():
    assert _lineas_cambiaron({"1"}, {"1": {}, "2": {}}) is True


def test_guard_no_aborta_si_nada_cambio():
    assert _lineas_cambiaron({"1", "2"}, {"1": {}, "2": {}}) is False


def test_guard_no_aborta_si_no_pudo_releer():
    """Un fallo de lectura devuelve None y NO debe interpretarse como cambio: si no,
    una caída puntual de HubSpot bloquearía cualquier edición."""
    assert _lineas_cambiaron(None, {"1": {}, "2": {}}) is False


def test_current_article_ids_solo_devuelve_fabricantes_del_contenedor(monkeypatch):
    monkeypatch.setattr(deals, "_get_associated_ids", lambda *a, **k: ["1", "2", "3", "4"])
    monkeypatch.setattr(deals.requests, "post", lambda *a, **k: FakeResponse({"results": [
        {"id": "1", "properties": {"numero_container": "D-001", "tipo_articulo": "fabricante",
                                   "hs_product_type": "Articulo"}},
        {"id": "2", "properties": {"numero_container": "D-002", "tipo_articulo": "fabricante",
                                   "hs_product_type": "Articulo"}},
        {"id": "3", "properties": {"numero_container": "D-001", "tipo_articulo": "transitario",
                                   "hs_product_type": "Flete"}},
        {"id": "4", "properties": {"numero_container": "D-001", "tipo_articulo": "fabricante",
                                   "hs_product_type": "Articulo"}},
    ]}))

    assert deals._current_article_ids("d", "D-001", {}) == {"1", "4"}


def test_current_article_ids_devuelve_none_si_falla_la_lectura(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api caída")

    monkeypatch.setattr(deals, "_get_associated_ids", boom)

    assert deals._current_article_ids("d", "D-001", {}) is None


def test_current_article_ids_devuelve_none_si_el_batch_read_falla(monkeypatch):
    monkeypatch.setattr(deals, "_get_associated_ids", lambda *a, **k: ["1"])
    monkeypatch.setattr(deals.requests, "post", lambda *a, **k: FakeResponse({}, ok=False))

    assert deals._current_article_ids("d", "D-001", {}) is None
