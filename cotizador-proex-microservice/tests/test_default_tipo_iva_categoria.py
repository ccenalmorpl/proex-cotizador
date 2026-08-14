"""El default de IVA nunca debe cruzar categorías: un código repercutido grabado
en `tipo_iva_soportado` (o al revés) rompe la sincronización con SAP."""

import pytest

from app.shared import hubdb_client
from app.utils.property_helpers import compute_default_tipo_iva, resolve_product_iva

REPERCUTIDOS = {"R21", "EI21", "EX", "NSR", "REXN"}
SOPORTADOS = {"S21", "SEXN", "NS", "A21", "I21"}


@pytest.fixture(autouse=True)
def cache_iva(monkeypatch):
    """Cache mínimo con las dos categorías disjuntas en las tres residencias."""

    class Entry:
        def __init__(self, code):
            self.code = code

    def fake(categoria, tipo_residencia=None):
        codes = REPERCUTIDOS if categoria == "repercutido" else SOPORTADOS
        return [Entry(c) for c in codes]

    monkeypatch.setattr(hubdb_client, "list_entries", fake)


@pytest.mark.parametrize("residennum", ["1", "NACIONAL", None, ""])
def test_nacional_conserva_r21_y_s21(residennum):
    assert compute_default_tipo_iva(residennum, "repercutido") == "R21"
    assert compute_default_tipo_iva(residennum, "soportado") == "S21"


@pytest.mark.parametrize(
    "residennum,esperado",
    [("2", "EI21"), ("EUROPA", "EI21"), ("4", "EX"), ("EXTRANJERO", "EX")],
)
def test_repercutido_no_cambia_fuera_de_nacional(residennum, esperado):
    assert compute_default_tipo_iva(residennum, "repercutido") == esperado


@pytest.mark.parametrize("residennum", ["2", "EUROPA", "4", "EXTRANJERO"])
def test_soportado_sin_default_fuera_de_nacional(residennum):
    assert compute_default_tipo_iva(residennum, "soportado") == ""


@pytest.mark.parametrize("residennum", ["1", "2", "4", None])
@pytest.mark.parametrize("categoria", ["repercutido", "soportado"])
def test_el_default_nunca_es_de_la_otra_categoria(residennum, categoria):
    code = compute_default_tipo_iva(residennum, categoria)
    prohibidos = SOPORTADOS if categoria == "repercutido" else REPERCUTIDOS
    assert code not in prohibidos


def test_default_descartado_si_no_esta_en_la_tabla(monkeypatch):
    monkeypatch.setattr(hubdb_client, "list_entries", lambda *a, **k: [])
    assert compute_default_tipo_iva("1", "soportado") == "S21"

    class Entry:
        code = "S10"

    monkeypatch.setattr(hubdb_client, "list_entries", lambda *a, **k: [Entry()])
    assert compute_default_tipo_iva("1", "soportado") == ""


def test_resolve_respeta_el_valor_del_producto_en_europa():
    props = {"tipo_iva_soportado_eu": "A21"}
    assert resolve_product_iva(props, "2", "soportado") == "A21"


def test_resolve_no_inventa_soportado_en_europa_sin_producto():
    assert resolve_product_iva({}, "2", "soportado") == ""
    assert resolve_product_iva(None, "4", "soportado") == ""


def test_resolve_nunca_pisa_current_value():
    assert resolve_product_iva({}, "4", "soportado", current_value="SEXP") == "SEXP"
