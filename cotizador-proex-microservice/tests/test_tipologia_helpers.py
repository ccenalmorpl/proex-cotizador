"""Transporte camión vs contenedor (apariencia y almacén) y destinos fuera de la
unión aduanera, que sí deciden nacional/exportación en la plantilla y el título de
la cotización. Funciones puras, sin red."""

import pytest

from app.utils.tipologia import (
    es_camion,
    es_destino_fuera_territorio_aduanero_ue,
    normalize_tipo_contenedor,
)


@pytest.mark.parametrize(
    "raw", ["trailer_80", "TRAILER 80", "TRAILER80", "trailer-80", " trailer_80 "],
)
def test_trailer_es_camion(raw):
    assert es_camion(raw) is True


@pytest.mark.parametrize(
    "raw", ["d40dr", "D20DR", "", None],
)
def test_resto_es_contenedor(raw):
    assert es_camion(raw) is False


def test_normalize_quita_no_alfanumericos():
    assert normalize_tipo_contenedor("trailer_80") == "TRAILER80"
    assert normalize_tipo_contenedor("TRAILER 80") == "TRAILER80"
    assert normalize_tipo_contenedor("trailer-80") == "TRAILER80"
    assert normalize_tipo_contenedor("D40DR") == "D40DR"
    assert normalize_tipo_contenedor(None) == ""


@pytest.mark.parametrize(
    "puerto", ["CEU", "MEL", "ceu", " mel ", "Ceuta, ES", "Melilla, ES"],
)
def test_ceuta_y_melilla_estan_fuera_del_territorio_aduanero(puerto):
    assert es_destino_fuera_territorio_aduanero_ue(puerto) is True


@pytest.mark.parametrize(
    "puerto",
    ["MEB", "Melbourne, (VI), AU", "ALG", "Algeciras, ES", "CAS", "TNG", "", None],
)
def test_resto_de_puertos_dentro(puerto):
    # MEB es Melbourne: no debe colarse por parecido con MEL.
    assert es_destino_fuera_territorio_aduanero_ue(puerto) is False
