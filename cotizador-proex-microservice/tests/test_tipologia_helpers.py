"""Transporte camión vs contenedor: decide nacional/exportación en la plantilla y
el título de la cotización. Función pura, sin red."""

import pytest

from app.utils.tipologia import es_camion, normalize_tipo_contenedor


@pytest.mark.parametrize(
    "raw", ["trailer_80", "TRAILER 80", "TRAILER80", "trailer-80", " trailer_80 "],
)
def test_trailer_es_camion(raw):
    assert es_camion(raw) is True


@pytest.mark.parametrize(
    "raw", ["d40dr", "D20DR", "camion_16p", "palets", "r40rf", "", None],
)
def test_resto_es_contenedor(raw):
    assert es_camion(raw) is False


def test_normalize_quita_no_alfanumericos():
    assert normalize_tipo_contenedor("trailer_80") == "TRAILER80"
    assert normalize_tipo_contenedor("TRAILER 80") == "TRAILER80"
    assert normalize_tipo_contenedor("trailer-80") == "TRAILER80"
    assert normalize_tipo_contenedor("D40DR") == "D40DR"
    assert normalize_tipo_contenedor(None) == ""
