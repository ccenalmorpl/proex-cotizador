"""Dependency de auth para los endpoints de negocio (deals/articles/templates),
llamados por la card via hubspot.fetch() con Authorization: Bearer <secreto>."""

import asyncio

import pytest
from fastapi import HTTPException

from app.core.card_auth import verify_card_secret
from app.core.config import settings


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host="127.0.0.1"):
        self.client = _FakeClient(host)


@pytest.fixture(autouse=True)
def restore_settings():
    orig_debug = settings.debug
    orig_secret = settings.proex_card_secret
    yield
    settings.debug = orig_debug
    settings.proex_card_secret = orig_secret


def test_sin_header_en_produccion_devuelve_401():
    settings.debug = False
    settings.proex_card_secret = "el-secreto"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_card_secret(_FakeRequest(), authorization=None))
    assert exc_info.value.status_code == 401


def test_secreto_incorrecto_en_produccion_devuelve_401():
    settings.debug = False
    settings.proex_card_secret = "el-secreto"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_card_secret(_FakeRequest(), authorization="Bearer otro-secreto"))
    assert exc_info.value.status_code == 401


def test_secreto_correcto_en_produccion_no_lanza():
    settings.debug = False
    settings.proex_card_secret = "el-secreto"
    asyncio.run(verify_card_secret(_FakeRequest(), authorization="Bearer el-secreto"))


def test_secreto_no_configurado_en_produccion_devuelve_503():
    settings.debug = False
    settings.proex_card_secret = None
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_card_secret(_FakeRequest(), authorization="Bearer lo-que-sea"))
    assert exc_info.value.status_code == 503


def test_debug_activo_no_exige_header():
    settings.debug = True
    settings.proex_card_secret = "el-secreto"
    asyncio.run(verify_card_secret(_FakeRequest(), authorization=None))
