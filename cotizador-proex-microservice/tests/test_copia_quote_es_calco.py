"""La copia de líneas a la cotización es un calco 1:1 de las del negocio.

Publicar la cotización desde el editor de HubSpot hace que el negocio pase a tener los
line items de la COTIZACIÓN: HubSpot los clona sobre el negocio (hs_object_source=CRM_UI,
en la sesión del comercial que pulsa Publicar) y archiva los propios. Así que la copia no
es un documento aparte, es lo que va a quedar dentro del negocio.

Mientras la copia se fundía por artículo y precio, el negocio 64836801849 (20 contenedores,
09/09/2026) acabó con 9 líneas agregadas sin `numero_container` y con el prefijo
"{contenedor} - " recortado del nombre, en lugar de sus 180 líneas per-contenedor.
GET /containers busca el line item de cada artículo por ese nombre exacto, así que 240 de
las 270 filas de la rejilla de Negociación salieron sin coste ni IVA.

Estos tests fijan que la copia conserva una línea por línea, con su nombre y su
`numero_container`, y que la verificación de la copia cuenta las líneas repetidas en vez
de mirar si el nombre aparece.
"""

from collections import Counter

from app.routes.deals import _catalogo_huerfano_ids

PESO = {"peso_neto": "6.0", "peso_bruto": "6.82"}

# Las mismas que copia el publish (_copy_fields), reducidas a lo que estos tests miran.
_COPY_FIELDS = [
    "name", "hs_product_id", "hs_product_type", "tipo_articulo",
    "quantity", "price", "numero_container", "peso_neto", "peso_bruto",
]


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


def _pedido_de(n_contenedores, productos=3, deal="64836801849"):
    items = []
    for c in range(1, n_contenedores + 1):
        nombre = f"{deal}-{c:03d}"
        items.append(_contenedor(nombre))
        items.append(_flete(nombre))
        for p in range(productos):
            items.append(_articulo(nombre, f"prod{p}", "6.332", 600))
    return items


def _copiar(items):
    """Lo que el publish vuelca a la cotización: los campos de _copy_fields, sin más."""
    salida = []
    for props in items:
        copia = {"hs_currency": "EUR"}
        for campo in _COPY_FIELDS:
            val = props.get(campo)
            if val is not None and val != "":
                copia[campo] = val
        salida.append(copia)
    return salida


def _cajas(items):
    return sum(float(i["quantity"]) for i in items if i.get("tipo_articulo") == "fabricante")


def _importe(items):
    return sum(float(i["price"]) * float(i["quantity"]) for i in items)


def test_veinte_contenedores_se_copian_linea_a_linea():
    items = _pedido_de(20, productos=9)
    copia = _copiar(items)

    assert len(items) == 220
    assert len(copia) == len(items)
    assert _cajas(copia) == _cajas(items)
    assert _importe(copia) == _importe(items)


def test_cada_linea_de_articulo_conserva_su_contenedor_y_su_nombre_compuesto():
    copia = _copiar(_pedido_de(20, productos=9))
    articulos = [i for i in copia if i["tipo_articulo"] == "fabricante"]

    assert len(articulos) == 180
    # Es el nombre por el que GET /containers empareja la fila del container_config.
    for art in articulos:
        assert art["numero_container"]
        assert art["name"].startswith(f"{art['numero_container']} - ")
    assert len({i["numero_container"] for i in articulos}) == 20


def test_las_lineas_de_contenedor_siguen_siendo_una_por_contenedor():
    copia = _copiar(_pedido_de(20, productos=9))
    contenedores = [i for i in copia if i["hs_product_type"] == "container"]

    assert len(contenedores) == 20
    assert sorted(i["name"] for i in contenedores) == [
        f"64836801849-{n:03d}" for n in range(1, 21)
    ]


def test_un_pedido_grande_no_se_funde_por_articulo():
    """50 contenedores x 9 artículos: las 450 líneas de mercancía siguen siendo 450.

    Con la agrupación, un pedido de este tamaño se quedaba en 61 líneas: 9 artículos,
    50 contenedores, flete y seguro."""
    items = _pedido_de(50, productos=9)
    copia = _copiar(items)

    assert len(copia) == 550  # 50 x (1 contenedor + 1 flete + 9 artículos)
    assert len([i for i in copia if i["tipo_articulo"] == "fabricante"]) == 450
    assert len({i["name"] for i in copia if i["tipo_articulo"] == "fabricante"}) == 450


def test_la_verificacion_cuenta_las_lineas_repetidas_de_un_mismo_articulo():
    """Promo 5+1: dos líneas del mismo artículo en un contenedor comparten `name`.

    Con un conjunto de nombres, copiar una de las dos daba la copia por completa y la
    segunda no se reintentaba nunca."""
    contenedor = "64836801849-001"
    items = _copiar([
        _contenedor(contenedor),
        _articulo(contenedor, "prod0", "6.332", 600),
        _articulo(contenedor, "prod0", "0.0", 120),
    ])

    por_copiar = Counter(i["name"] for i in items)
    # HubSpot solo se quedó con una de las dos líneas de prod0.
    en_quote = Counter({contenedor: 1, f"{contenedor} - ENERDRINK prod0": 1})

    faltan = por_copiar - en_quote
    pendientes = []
    for props in items:
        nombre = props["name"]
        if faltan[nombre] > 0:
            pendientes.append(props)
            faltan[nombre] -= 1

    assert len(pendientes) == 1
    assert pendientes[0]["name"] == f"{contenedor} - ENERDRINK prod0"


def test_las_lineas_de_catalogo_sin_contenedor_siguen_fuera_de_la_copia():
    """Lo único que se excluye de la copia son los huérfanos de catálogo."""
    items = _pedido_de(2, productos=2)
    catalogo = {
        "id": "999",
        "properties": {
            "name": "ENERDRINK prod0",
            "hs_product_type": "Articulo",
            "tipo_articulo": "fabricante",
            "quantity": "1200",
            "price": "6.332",
        },
    }
    con_ids = [{"id": str(n), "properties": p} for n, p in enumerate(items)] + [catalogo]
    nombres_contenedor = {i["numero_container"] for i in items if i["hs_product_type"] == "container"}

    huerfanos = _catalogo_huerfano_ids(con_ids, nombres_contenedor)

    assert huerfanos == ["999"]
