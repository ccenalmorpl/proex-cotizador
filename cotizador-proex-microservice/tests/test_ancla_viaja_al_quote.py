"""El ancla del tipo de venta tiene que llegar al line item del quote.

`publish_quote` no asocia al quote los line items del negocio: crea copias nuevas
con las propiedades de `_copy_fields`, leídas del negocio con una lista explícita.
Si el ancla falta en cualquiera de las dos listas, `quote.html` no puede imprimir
el precio negociado y recompone EXW + delta, que arrastra el residuo del EXW
guardado a 3 decimales (+1,60 € en 6.240 cajas).
"""

import inspect

from app.routes.deals import publish_quote

ANCLA = ("precio_negociado_tipo_venta", "incoterm_precio_negociado")


def _fuente_publish():
    return inspect.getsource(publish_quote)


def test_el_ancla_se_lee_del_negocio_y_se_copia_al_quote():
    fuente = _fuente_publish()
    for prop in ANCLA:
        # Una vez en el batch read del negocio y otra en _copy_fields.
        assert fuente.count(f'"{prop}"') >= 2, f"{prop} falta en el read o en _copy_fields"


def test_el_precio_del_quote_sigue_redondeado_a_3():
    # El EXW copiado mantiene round_price; el ancla es el precio final, no el EXW.
    assert "rounded = round_price(val)" in _fuente_publish()
