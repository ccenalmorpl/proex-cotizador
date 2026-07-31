"""Tests de la garantía EX/SEXN en el cache de IVA (`_ensure_siempre_disponibles`):
EX (repercutido) y SEXN (soportado) deben existir para toda residencia aunque la
HubDB no tenga fila, con 0% de IVA y la misma etiqueta que la fila real."""

from types import SimpleNamespace

import pytest

from app.shared import hubdb_client
from app.shared.hubdb_client import (
    _build_cache_from_rows,
    get_porcentajes_iva_repercutido,
    get_porcentajes_iva_soportado,
    list_entries,
    reset_cache,
)


def _row(categoria, code, residencia, iva, recargo=0, name=None):
    return SimpleNamespace(values={
        "categoria": categoria,
        "code": code,
        "tipo_residencia": residencia,
        "porcentaje_iva": iva,
        "porcentaje_recargo": recargo,
        "name": name or code,
    })


# Names distintos de los defaults de SIEMPRE_DISPONIBLES a propósito: así los
# asserts distinguen "copiado de la fila real" de "usado el fallback hardcodeado".
ROWS_HUBDB = [
    _row("repercutido", "R21", "NACIONAL", 21, 5.2, "IVA repercutido al 21%"),
    _row("repercutido", "EI21", "EUROPA", 21, 0, "Entregas intracomunitarias de bienes 21%"),
    _row("repercutido", "EX", "EXTRANJERO", 0, 0, "Exportaciones Bebidas (fila HubDB)"),
    _row("soportado", "S21", "NACIONAL", 21, 0, "IVA soportado al 21%"),
    _row("soportado", "SEXN", "NACIONAL", 0, 0, "Sujeto Exento Art.22 Navieras (fila HubDB)"),
]


@pytest.fixture(autouse=True)
def cache_desde_rows(monkeypatch):
    monkeypatch.setattr(hubdb_client, "_fetch_all_rows_sync", lambda: ROWS_HUBDB)
    reset_cache()
    yield
    reset_cache()


def test_ex_repercutido_se_sintetiza_en_toda_residencia():
    cache, _ = _build_cache_from_rows(ROWS_HUBDB)
    for residencia in ("NACIONAL", "EUROPA", "EXTRANJERO"):
        assert ("EX", residencia) in cache["repercutido"]


def test_sexn_soportado_se_sintetiza_en_toda_residencia():
    cache, _ = _build_cache_from_rows(ROWS_HUBDB)
    for residencia in ("NACIONAL", "EUROPA", "EXTRANJERO"):
        assert ("SEXN", residencia) in cache["soportado"]


def test_sintetico_copia_name_de_la_fila_real():
    cache, _ = _build_cache_from_rows(ROWS_HUBDB)
    assert cache["repercutido"][("EX", "NACIONAL")].name == "Exportaciones Bebidas (fila HubDB)"
    assert cache["soportado"][("SEXN", "EUROPA")].name == "Sujeto Exento Art.22 Navieras (fila HubDB)"


def test_fila_real_no_se_sobreescribe():
    # Porcentajes no-cero (irreales para EX) para que una sobrescritura por el
    # sintético (0%/0%) no pase desapercibida.
    rows = ROWS_HUBDB + [_row("repercutido", "EX", "NACIONAL", 5, 1.4, "EX manual")]
    cache, _ = _build_cache_from_rows(rows)
    ex = cache["repercutido"][("EX", "NACIONAL")]
    assert (ex.porcentaje_iva, ex.porcentaje_recargo) == (5.0, 1.4)
    assert ex.name == "EX manual"
    assert cache["soportado"][("SEXN", "NACIONAL")].name == "Sujeto Exento Art.22 Navieras (fila HubDB)"


def test_sintetico_a_cero_por_ciento():
    assert get_porcentajes_iva_repercutido("EX", "NACIONAL") == (0.0, 0.0)
    assert get_porcentajes_iva_repercutido("EX", "EUROPA") == (0.0, 0.0)
    assert get_porcentajes_iva_soportado("SEXN", "EXTRANJERO") == (0.0, 0.0)


def test_name_de_respaldo_si_el_codigo_no_existe_en_hubdb():
    rows = [_row("repercutido", "R21", "NACIONAL", 21, 5.2)]
    cache, _ = _build_cache_from_rows(rows)
    assert cache["soportado"][("SEXN", "NACIONAL")].name == "Sujeto Exento Motivo Exento Art.22 Navieras"
    assert cache["repercutido"][("EX", "NACIONAL")].name == "Exportaciones Bebidas"


def test_no_se_cruzan_categorias():
    cache, _ = _build_cache_from_rows(ROWS_HUBDB)
    assert ("SEXN", "NACIONAL") not in cache["repercutido"]
    assert ("EX", "NACIONAL") not in cache["soportado"]


def test_list_entries_expone_los_sintetizados():
    hubdb_client._get_porcentajes("repercutido", "R21", "NACIONAL")
    codes_nacional = {e.code for e in list_entries("repercutido", "NACIONAL")}
    assert {"R21", "EX"} <= codes_nacional
    codes_sop_europa = {e.code for e in list_entries("soportado", "EUROPA")}
    assert "SEXN" in codes_sop_europa


def test_resto_de_codigos_no_se_propaga_entre_residencias():
    cache, _ = _build_cache_from_rows(ROWS_HUBDB)
    assert ("R21", "EUROPA") not in cache["repercutido"]
    assert ("EI21", "NACIONAL") not in cache["repercutido"]


def test_fallo_de_carga_no_envenena_el_cache(monkeypatch):
    # Un fallo transitorio no debe dejar cache vacío no-None: el siguiente
    # uso tiene que reintentar la carga y recuperarse.
    def boom():
        raise RuntimeError("HubSpot caído")

    monkeypatch.setattr(hubdb_client, "_fetch_all_rows_sync", boom)
    reset_cache()
    assert get_porcentajes_iva_repercutido("R21", "NACIONAL") == (None, None)
    assert hubdb_client._iva_cache is None

    monkeypatch.setattr(hubdb_client, "_fetch_all_rows_sync", lambda: ROWS_HUBDB)
    assert get_porcentajes_iva_repercutido("R21", "NACIONAL") == (21.0, 5.2)
    assert get_porcentajes_iva_repercutido("EX", "NACIONAL") == (0.0, 0.0)
