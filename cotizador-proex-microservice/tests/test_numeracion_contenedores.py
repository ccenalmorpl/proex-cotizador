"""Numeración de contenedores nuevos en el merge incremental de /containers."""
import pytest

from app.routes.deals import _asignar_numeros_contenedor, _parse_numero_contenedor


class TestParseNumeroContenedor:
    def test_extrae_el_numero_del_nombre(self):
        assert _parse_numero_contenedor("64288826462-007") == 7
        assert _parse_numero_contenedor("64288826462-046") == 46

    def test_devuelve_none_si_no_es_un_nombre_de_contenedor(self):
        assert _parse_numero_contenedor("") is None
        assert _parse_numero_contenedor(None) is None
        assert _parse_numero_contenedor("sin-numero") is None


class TestAsignarNumerosContenedor:
    def test_cotizacion_nueva_empieza_en_uno(self):
        assert _asignar_numeros_contenedor(set(), 12) == list(range(1, 13))

    def test_rehacer_todos_los_contenedores_vuelve_a_empezar_en_uno(self):
        # El caso de la incidencia: los 12 previos se archivan en esta misma
        # llamada, así que ninguno sobrevive y sus números quedan libres.
        assert _asignar_numeros_contenedor(set(), 12) == list(range(1, 13))

    def test_anadir_a_los_existentes_continua_la_serie(self):
        assert _asignar_numeros_contenedor({1, 2, 3}, 2) == [4, 5]

    def test_rellena_el_hueco_que_deja_un_contenedor_eliminado(self):
        assert _asignar_numeros_contenedor({1, 3}, 1) == [2]

    def test_rellena_varios_huecos_antes_de_seguir_la_serie(self):
        assert _asignar_numeros_contenedor({2, 5}, 4) == [1, 3, 4, 6]

    def test_sin_contenedores_nuevos_no_asigna_nada(self):
        assert _asignar_numeros_contenedor({1, 2}, 0) == []

    def test_los_numeros_asignados_nunca_chocan_con_los_supervivientes(self):
        ocupados = {1, 4, 7}
        asignados = _asignar_numeros_contenedor(ocupados, 5)
        assert not ocupados.intersection(asignados)
        assert len(set(asignados)) == len(asignados)


@pytest.mark.parametrize("cantidad", [1, 3, 12, 40])
def test_numeracion_correlativa_desde_cero(cantidad):
    assert _asignar_numeros_contenedor(set(), cantidad) == list(range(1, cantidad + 1))
