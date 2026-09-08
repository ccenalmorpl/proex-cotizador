"""La copia de líneas a la cotización se agrupa para que quepa en HubSpot.

HubSpot recorta a 200 los line items de una cotización, y lo hace minutos DESPUÉS de
publicar, cuando ya ha confirmado todas las asociaciones (deal 64696789273, 08/09/2026:
600 copiadas y confirmadas, 200 al rato). Con 12 líneas por contenedor, a partir de unos
16 contenedores el documento sale con la mercancía a medias y el precio por caja
disparado, porque los gastos se reparten entre las cajas que hayan sobrevivido.

La copia se funde por artículo y precio: 50 contenedores pasan de 600 líneas a 61 sin
que cambien las cajas, el importe ni los pesos. Los line items del negocio no se tocan.
"""

from app.routes.deals import _agrupar_items_para_quote

PESO = {"peso_neto": "6.0", "peso_bruto": "6.82"}


def _articulo(contenedor, producto, precio, cajas):
    return {
        "name": f"{contenedor} - ENERDRINK {producto}",
        "numero_container": contenedor,
        "hs_product_id": producto,
        "hs_product_type": "Articulo",
        "tipo_articulo": "fabricante",
        "quantity": str(cajas),
        "price": precio,
        **PESO,
    }


def _contenedor(nombre):
    return {
        "name": nombre,
        "numero_container": nombre,
        "hs_product_id": "34240664372",
        "hs_product_type": "container",
        "tipo_articulo": "transitario",
        "quantity": "1",
        "price": "0.0",
    }


def _flete(contenedor):
    return {
        "name": f"{contenedor} - FLETES",
        "numero_container": contenedor,
        "hs_product_id": "42084189861",
        "hs_product_type": "Flete",
        "tipo_articulo": "transitario",
        "quantity": "1",
        "price": "2680.0",
    }


def _pedido_de(n_contenedores, productos=3):
    items = []
    for c in range(1, n_contenedores + 1):
        nombre = f"64696789273-{c:03d}"
        items.append(_contenedor(nombre))
        items.append(_flete(nombre))
        for p in range(productos):
            items.append(_articulo(nombre, f"prod{p}", "5.04", 600))
    return items


def _cajas(items):
    return sum(float(i["quantity"]) for i in items if i["tipo_articulo"] == "fabricante")


def _importe(items):
    return sum(float(i["price"]) * float(i["quantity"]) for i in items)


def _peso_neto(items):
    return sum(float(i["quantity"]) * float(i.get("peso_neto") or 0) for i in items)


def test_un_pedido_de_50_contenedores_cabe_en_la_cotizacion():
    items = _pedido_de(50, productos=9)
    agrupado = _agrupar_items_para_quote(items)

    assert len(items) == 550
    # 9 artículos + 50 contenedores + 1 flete
    assert len(agrupado) == 60
    assert len(agrupado) < 200


def test_no_cambian_las_cajas_ni_el_importe_ni_los_pesos():
    items = _pedido_de(50, productos=9)
    agrupado = _agrupar_items_para_quote(items)

    assert _cajas(agrupado) == _cajas(items)
    assert round(_importe(agrupado), 2) == round(_importe(items), 2)
    assert round(_peso_neto(agrupado), 2) == round(_peso_neto(items), 2)


def test_las_lineas_de_contenedor_se_respetan_una_a_una():
    """Dan el número de contenedores y las páginas del modo desagregado."""
    agrupado = _agrupar_items_para_quote(_pedido_de(50, productos=9))
    contenedores = [i for i in agrupado if i["hs_product_type"] == "container"]

    assert len(contenedores) == 50
    assert {i["numero_container"] for i in contenedores} == {
        f"64696789273-{c:03d}" for c in range(1, 51)
    }


def test_dos_contenedores_cotizados_a_precios_distintos_no_se_funden():
    items = [
        _articulo("64696789273-001", "prod0", "5.04", 600),
        _articulo("64696789273-002", "prod0", "5.31", 600),
    ]
    agrupado = _agrupar_items_para_quote(items)

    assert len(agrupado) == 2
    assert {i["price"] for i in agrupado} == {"5.04", "5.31"}


def test_la_linea_agrupada_pierde_el_prefijo_del_contenedor():
    agrupado = _agrupar_items_para_quote(
        [_articulo("64696789273-001", "prod0", "5.04", 600)]
    )

    assert agrupado[0]["name"] == "ENERDRINK prod0"
    assert "numero_container" not in agrupado[0]
