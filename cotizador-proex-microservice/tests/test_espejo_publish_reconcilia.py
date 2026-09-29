"""El espejo del publish deja el negocio a medias y la reconciliación lo rehace.

Publicar una cotización hace que el NEGOCIO pase a tener los line items de la
COTIZACIÓN: HubSpot los clona (CRM_UI) y archiva los propios, con un tope de 200 por
operación. El negocio 64731186508 (20 contenedores, 240 líneas, 11/09/2026) se quedó
con 200 clones más 40 líneas viejas que no llegaron a archivarse: 18 líneas de
contenedor en vez de 20, 40 nombres duplicados y 29 de las 162 filas de la rejilla de
Negociación sin line item, que es lo que devolvía un 422 al guardar.

Aquí se fija que la reconciliación repone lo que falta, archiva el duplicado viejo (no
el clon, que trae el estado publicado) y hereda el precio del contenedor al que entra.
"""

import json
from collections import Counter

import pytest

from app.services import espejo_publish
from app.services.espejo_publish import planificar

DEAL = "64731186508"
PRODUCTOS = [
    ("42078145099", "MORA SALVAJE", 600),
    ("42078145056", "FRESA SALVAJE", 600),
    ("42078145101", "SANDIA & UVA", 600),
    ("42078145068", "FRAMBUESA", 360),
    ("42078144943", "MANZANA & MELON", 240),
    ("42050417060", "MANGO", 240),
    ("42078145088", "PIÑA & COCO", 240),
    ("42050417012", "PLATANO", 120),
    ("42050417035", "CRAZY COCONUT", 120),
]


def _nombre_cont(n):
    return f"{DEAL}-{n:03d}"


def _config(nombre_cont):
    return json.dumps({
        "articulos": [
            {"articuloId": pid, "nombre": nombre, "numCajas": cajas, "line_item_id": None}
            for pid, nombre, cajas in PRODUCTOS
        ],
        "ocupacion": 100.0,
        "pesoNeto": 18720.0,
    }, ensure_ascii=False)


def _linea(lid, name, tipo, creada, **extra):
    props = {
        "name": name,
        "tipo_articulo": tipo,
        "numero_container": name.split(" - ")[0],
        "createdate": creada,
        "quantity": "1",
        "price": "6.415",
        "hs_product_type": "container" if tipo == "contenedor" else "inventory",
    }
    props.update(extra)
    if tipo == "contenedor":
        props["tipo_articulo"] = "transitario"
    return lid, props


def _negocio_entero(n_contenedores=20, creada="2026-09-11T14:05:21Z"):
    """Los 240 line items que un negocio de 20 contenedores tiene cuando está sano."""
    vivos = {}
    seq = iter(range(10000, 99999))
    for n in range(1, n_contenedores + 1):
        cont = _nombre_cont(n)
        lid, props = _linea(str(next(seq)), cont, "contenedor", creada,
                            container_config=_config(cont), price="0")
        vivos[lid] = props
        for pid, nombre, cajas in PRODUCTOS:
            lid, props = _linea(str(next(seq)), f"{cont} - {nombre}", "fabricante", creada,
                                quantity=str(cajas), hs_product_id=pid,
                                precio_negociado_tipo_venta="7.34",
                                incoterm_precio_negociado="CIF")
            vivos[lid] = props
        lid, props = _linea(str(next(seq)), f"{cont} - FLETES", "transitario", creada,
                            price="2825.0")
        vivos[lid] = props
        lid, props = _linea(str(next(seq)), f"{cont} - SEGURO MERCANCÍA", "seguro", creada,
                            price="60.0")
        vivos[lid] = props
    return vivos


def _custom_objects(vivos, n_contenedores=20):
    contenedores = {
        f"co{n}": {"nombre": _nombre_cont(n), "line_item_id": "", "container_config": ""}
        for n in range(1, n_contenedores + 1)
    }
    elementos = {}
    i = 0
    for n in range(1, n_contenedores + 1):
        cont = _nombre_cont(n)
        for pid, nombre, cajas in PRODUCTOS:
            i += 1
            elementos[f"el{i}"] = {
                "nombre": f"{cont} - {nombre}", "nombre_contenedor": cont,
                "tipo_articulo": "fabricante", "cantidad": str(cajas),
                "product_sku": pid, "precio_de_coste": "5.25",
                "precio_unitario": "6.415", "margen_unitario": "0.22",
                "tipo_iva_repercutido": "EX", "tipo_iva_soportado": "NS",
            }
        i += 1
        # La Línea Contenedor dice `flete` donde el line item dice `transitario`.
        elementos[f"el{i}"] = {
            "nombre": f"{cont} - FLETES", "nombre_contenedor": cont,
            "tipo_articulo": "flete", "cantidad": "1", "product_sku": "",
            "precio_unitario": "2825.0",
        }
        i += 1
        elementos[f"el{i}"] = {
            "nombre": f"{cont} - SEGURO MERCANCÍA", "nombre_contenedor": cont,
            "tipo_articulo": "seguro", "cantidad": "1", "product_sku": "",
            "precio_unitario": "60.0",
        }
    return contenedores, elementos


def _espejar(vivos, tope=200):
    """Lo que HubSpot deja tras publicar: clona `tope` líneas y archiva otras `tope`.

    Los dos cortes caen sobre conjuntos distintos, que es lo que produce a la vez
    duplicados (la vieja que no se archivó más su clon) y ausencias.
    """
    ids = list(vivos)
    copiadas, archivadas = ids[:tope], ids[-tope:]
    resultado = {}
    for lid in ids:
        if lid not in archivadas:
            resultado[lid] = dict(vivos[lid], createdate="2026-09-08T13:40:15Z")
    for n, lid in enumerate(copiadas):
        resultado[f"clon{n}"] = dict(vivos[lid], createdate="2026-09-11T14:05:21Z")
    return resultado


@pytest.fixture
def hubspot(monkeypatch):
    """Sustituye las lecturas por diccionarios en memoria y recoge lo que se escribiría."""
    estado = {"vivos": {}, "contenedores": {}, "elementos": {}}

    def _fake_assoc(token, from_type, from_id, to_type):
        return {
            "line_items": list(estado["vivos"]),
            espejo_publish.OBJECT_TYPE_CONTENEDOR: list(estado["contenedores"]),
            espejo_publish.OBJECT_TYPE_ELEMENTO: list(estado["elementos"]),
        }[to_type]

    def _fake_read(token, obj, ids, props):
        fuente = {
            "line_items": estado["vivos"],
            espejo_publish.OBJECT_TYPE_CONTENEDOR: estado["contenedores"],
            espejo_publish.OBJECT_TYPE_ELEMENTO: estado["elementos"],
        }[obj]
        return [{"id": i, "properties": fuente[i]} for i in ids]

    monkeypatch.setattr(espejo_publish, "_assoc_ids", _fake_assoc)
    monkeypatch.setattr(espejo_publish, "_batch_read", _fake_read)
    monkeypatch.setattr(espejo_publish, "_line_item_props", lambda token: [])
    return estado


def _plan_de(estado, vivos, n_contenedores=20):
    estado["vivos"] = vivos
    estado["contenedores"], estado["elementos"] = _custom_objects(vivos, n_contenedores)
    return planificar("tok", DEAL)


def test_un_negocio_sano_no_se_toca(hubspot):
    plan = _plan_de(hubspot, _negocio_entero())
    assert plan["crear"] == []
    assert plan["archivar"] == []


def test_el_espejo_deja_el_negocio_a_medias_y_se_repone_entero(hubspot):
    entero = _negocio_entero()
    espejado = _espejar(entero)
    plan = _plan_de(hubspot, espejado)

    nombres = Counter(p["name"] for p in espejado.values())
    assert [n for n, v in nombres.items() if v > 1], "el escenario no llegó a duplicar nada"
    assert len(nombres) < len(entero), "el escenario no llegó a perder nada"

    assert plan["crear"] and plan["archivar"]
    quedan = len(espejado) - len(plan["archivar"]) + len(plan["crear"])
    assert quedan == len(entero) == 240
    assert plan["sin_plantilla"] == []


def test_cada_contenedor_recupera_sus_nueve_articulos(hubspot):
    plan = _plan_de(hubspot, _espejar(_negocio_entero()))

    vivos_tras = {
        p["name"] for lid, p in hubspot["vivos"].items() if lid not in plan["archivar"]
    }
    vivos_tras |= {c["properties"]["name"] for c in plan["crear"]}
    for n in range(1, 21):
        cont = _nombre_cont(n)
        articulos = [f"{cont} - {nombre}" for _, nombre, _ in PRODUCTOS]
        assert all(a in vivos_tras for a in articulos), f"{cont} incompleto"
        assert cont in vivos_tras


def test_la_linea_de_contenedor_perdida_vuelve_con_su_configuracion(hubspot):
    """Sin ella la rejilla no sabe qué artículos lleva ese contenedor."""
    entero = _negocio_entero()
    mutilado = {lid: p for lid, p in entero.items()
                if p.get("name") != _nombre_cont(14)}
    plan = _plan_de(hubspot, mutilado)

    nuevas = [c for c in plan["crear"] if c["tipo"] == "contenedor"]
    assert len(nuevas) == 1
    config = json.loads(nuevas[0]["properties"]["container_config"])
    assert [a["nombre"] for a in config["articulos"]] == [n for _, n, _ in PRODUCTOS]
    assert nuevas[0]["properties"]["name"] == _nombre_cont(14)


def test_del_duplicado_se_archiva_la_vieja_y_se_conserva_el_clon(hubspot):
    entero = _negocio_entero()
    vieja, props = next(iter(entero.items()))
    espejado = dict(entero)
    espejado[vieja] = dict(props, createdate="2026-09-08T13:40:15Z")
    espejado["clon"] = dict(props, createdate="2026-09-11T14:05:21Z")

    plan = _plan_de(hubspot, espejado)
    assert plan["archivar"] == [vieja]
    assert plan["crear"] == []


def test_la_linea_repuesta_hereda_el_precio_de_su_contenedor(hubspot):
    """El precio por caja es del contenedor: los gastos se reparten entre sus cajas."""
    entero = _negocio_entero()
    for lid, p in entero.items():
        if p.get("numero_container") == _nombre_cont(7) and p["tipo_articulo"] == "fabricante":
            p["price"] = "6.171"
            p["precio_negociado_tipo_venta"] = "7.34"
    perdida = f"{_nombre_cont(7)} - MANGO"
    mutilado = {lid: p for lid, p in entero.items() if p["name"] != perdida}

    plan = _plan_de(hubspot, mutilado)
    nueva = next(c for c in plan["crear"] if c["properties"]["name"] == perdida)
    assert nueva["properties"]["price"] == "6.171"
    assert nueva["properties"]["precio_negociado_tipo_venta"] == "7.34"


def test_la_linea_repuesta_no_hereda_el_precio_de_una_que_se_va_a_archivar(hubspot):
    """La vecina de la que se copia precio y ancla tiene que ser una que sobreviva.

    Tomando cualquiera, en un contenedor con duplicados salía la vieja: 14 líneas del
    deal 64731186508 nacieron con el ancla 6,73 anterior a la última negociación
    mientras sus hermanas iban a 7,34.
    """
    entero = _negocio_entero()
    cont = _nombre_cont(9)
    vieja = next(lid for lid, p in entero.items()
                 if p["name"] == f"{cont} - MORA SALVAJE")
    entero[vieja] = dict(entero[vieja], price="5.805", createdate="2026-09-08T13:40:15Z",
                         precio_negociado_tipo_venta="6.73")
    entero["clon"] = dict(entero[vieja], price="6.415",
                          precio_negociado_tipo_venta="7.34",
                          createdate="2026-09-11T14:05:21Z")
    perdida = f"{cont} - MANGO"
    mutilado = {lid: p for lid, p in entero.items() if p["name"] != perdida}

    plan = _plan_de(hubspot, mutilado)
    nueva = next(c for c in plan["crear"] if c["properties"]["name"] == perdida)
    assert nueva["properties"]["precio_negociado_tipo_venta"] == "7.34"
    assert nueva["properties"]["price"] == "6.415"
    assert vieja in plan["archivar"]


def test_la_linea_repuesta_recupera_el_coste_de_su_linea_contenedor(hubspot):
    """El espejo no copia precio_de_coste, y sin él la rejilla sale a margen 0."""
    entero = _negocio_entero()
    perdida = f"{_nombre_cont(3)} - PLATANO"
    mutilado = {lid: p for lid, p in entero.items() if p["name"] != perdida}

    plan = _plan_de(hubspot, mutilado)
    nueva = next(c for c in plan["crear"] if c["properties"]["name"] == perdida)
    assert nueva["properties"]["precio_de_coste"] == "5.25"
    assert nueva["properties"]["quantity"] == "120"
    assert nueva["properties"]["tipo_iva_repercutido"] == "EX"


def test_el_flete_perdido_vuelve_y_ningun_flete_vivo_se_archiva(hubspot):
    """El line item marca el flete como `transitario` y su Línea Contenedor como `flete`.

    Al leer el objetivo por el tipo del line item, los 20 fletes del negocio quedaban
    fuera y el plan los daba por sobrantes.
    """
    entero = _negocio_entero()
    perdido = f"{_nombre_cont(5)} - FLETES"
    mutilado = {lid: p for lid, p in entero.items() if p["name"] != perdido}

    plan = _plan_de(hubspot, mutilado)
    assert [c["properties"]["name"] for c in plan["crear"]] == [perdido]
    assert plan["crear"][0]["properties"]["price"] == "2825.0"
    assert plan["archivar"] == []


def test_las_agregadas_sin_contenedor_se_archivan(hubspot):
    entero = _negocio_entero()
    entero["agregada"] = {"name": "MORA SALVAJE", "tipo_articulo": "fabricante",
                          "numero_container": "", "createdate": "2026-09-11T14:05:21Z",
                          "quantity": "600", "price": "6.415", "hs_product_type": "inventory"}
    plan = _plan_de(hubspot, entero)
    assert plan["archivar"] == ["agregada"]


def test_no_se_duplican_los_articulos_que_se_repiten_en_un_contenedor(hubspot):
    """En nacional un contenedor lleva dos líneas del mismo SKU (promo 5+1)."""
    entero = _negocio_entero(n_contenedores=1)
    cont = _nombre_cont(1)
    config = json.loads(_config(cont))
    config["articulos"].append({"articuloId": "42078145099", "nombre": "MORA SALVAJE",
                                "numCajas": 120, "line_item_id": None})
    for p in entero.values():
        if p["name"] == cont:
            p["container_config"] = json.dumps(config, ensure_ascii=False)
    promo = dict(next(p for p in entero.values() if p["name"] == f"{cont} - MORA SALVAJE"),
                 quantity="120", price="0")
    entero["promo"] = promo

    plan = _plan_de(hubspot, entero, n_contenedores=1)
    assert plan["crear"] == []
    assert plan["archivar"] == []


def _sin_custom_objects(estado, vivos):
    estado["vivos"] = vivos
    estado["contenedores"], estado["elementos"] = {}, {}
    return planificar("tok", DEAL)


def test_la_primera_publicacion_no_archiva_las_lineas_recien_creadas(hubspot):
    """El negocio aún no tiene Contenedor: el re-sync que los crea corre DESPUÉS.

    Con el objetivo vacío, todo line item que no fuera de contenedor se daba por
    duplicado del espejo. El 14/09/2026 se llevó por delante las líneas de cinco
    negocios recién cotizados (deal 64986199472: archivó artículo, flete y seguro y
    dejó la línea de contenedor sola).
    """
    entero = _negocio_entero(n_contenedores=1)
    plan = _sin_custom_objects(hubspot, entero)

    assert plan["archivar"] == []
    assert plan["crear"] == []
    assert plan["con_contenedores"] is False


def test_sin_contenedores_la_reconciliacion_no_toca_el_negocio(hubspot):
    entero = _negocio_entero(n_contenedores=1)
    hubspot["vivos"] = entero
    hubspot["contenedores"], hubspot["elementos"] = {}, {}

    llamadas = []
    monkeypatched = espejo_publish.aplicar
    espejo_publish.aplicar = lambda *a, **kw: llamadas.append(a) or {}
    try:
        stats = espejo_publish.reconciliar_lineas_tras_espejo("tok", DEAL)
    finally:
        espejo_publish.aplicar = monkeypatched

    assert llamadas == []
    assert stats == {"creadas": 0, "archivadas": 0, "repuntados": 0}


def test_un_contenedor_sin_lineas_contenedor_conserva_su_flete_y_su_seguro(hubspot):
    """El flete y el seguro del objetivo se deducen de los Elementos del contenedor.

    Los negocios que el bug del 14/09 dejó sin Líneas Contenedor tienen el Contenedor
    creado y cero Elementos: el objetivo sale con los artículos (los trae el
    container_config) pero sin flete ni seguro, y relanzar el job los archivaba.
    """
    entero = _negocio_entero(n_contenedores=1)
    hubspot["vivos"] = entero
    hubspot["contenedores"], _ = _custom_objects(entero, 1)
    hubspot["elementos"] = {}

    plan = planificar("tok", DEAL)
    cont = _nombre_cont(1)
    assert plan["archivar"] == []
    assert plan["ajenos"] == [f"{cont} - FLETES", f"{cont} - SEGURO MERCANCÍA"]


def test_el_guardarrail_aborta_si_vacia_medio_negocio_sin_reponer_nada(hubspot):
    """Archivar mucho y no crear nada no es la firma del espejo, es un objetivo roto."""
    entero = _negocio_entero(n_contenedores=1)
    hubspot["vivos"] = entero
    hubspot["contenedores"], hubspot["elementos"] = _custom_objects(entero, 1)

    plan = planificar("tok", DEAL)
    plan["archivar"] = [lid for lid, p in entero.items() if p["tipo_articulo"] == "fabricante"]
    monkeypatched = espejo_publish.planificar
    espejo_publish.planificar = lambda *a, **kw: plan
    try:
        stats = espejo_publish.reconciliar_lineas_tras_espejo("tok", DEAL)
    finally:
        espejo_publish.planificar = monkeypatched

    assert stats["abortado"] is True
    assert stats["archivadas"] == 0


SKU_FLETE = "42084189861"
SKU_SEGURO = "42399229255"


def _negocio_doble_flete():
    """Camión CIF nacional con los dos tramos, como el 65027508906 el 22/09/2026.

    Flete y seguro llevan product_sku en sus Líneas Contenedor, igual que en el portal,
    y los dos tramos comparten producto.
    """
    cont = _nombre_cont(1)
    creada = "2026-09-22T11:15:15Z"
    vivos = dict([
        _linea("c1", cont, "contenedor", creada, container_config=_config(cont), price="0"),
        _linea("mar", f"{cont} - FLETE MARITIMO", "transitario", creada, price="849.0",
               cardcode="P0518", hs_product_id=SKU_FLETE, tipo_iva_soportado="SEXN"),
        _linea("ter", f"{cont} - FLETE TERRESTRE", "transitario", creada, price="1200.0",
               cardcode="P0690", hs_product_id=SKU_FLETE, tipo_iva_soportado="S21"),
        _linea("seg", f"{cont} - SEGURO MERCANCÍA", "seguro", creada, price="60.0",
               cardcode="P0082", hs_product_id=SKU_SEGURO),
    ])
    for pid, nombre, cajas in PRODUCTOS:
        lid, props = _linea(f"a{pid}", f"{cont} - {nombre}", "fabricante", creada,
                            quantity=str(cajas), hs_product_id=pid)
        vivos[lid] = props

    contenedores, elementos = _custom_objects(vivos, 1)
    elementos = {k: v for k, v in elementos.items() if v["tipo_articulo"] == "fabricante"}
    elementos.update({
        "el_mar": {"nombre": f"{cont} - FLETE MARITIMO", "nombre_contenedor": cont,
                   "tipo_articulo": "flete", "cantidad": "1", "product_sku": SKU_FLETE,
                   "precio_unitario": "849.0", "cardcode": "P0518",
                   "tipo_iva_repercutido": "EX", "tipo_iva_soportado": "SEXN"},
        "el_ter": {"nombre": f"{cont} - FLETE TERRESTRE", "nombre_contenedor": cont,
                   "tipo_articulo": "flete", "cantidad": "1", "product_sku": SKU_FLETE,
                   "precio_unitario": "1200.0", "cardcode": "P0690",
                   "tipo_iva_repercutido": "EX", "tipo_iva_soportado": "S21"},
        "el_seg": {"nombre": f"{cont} - SEGURO MERCANCÍA", "nombre_contenedor": cont,
                   "tipo_articulo": "seguro", "cantidad": "1", "product_sku": SKU_SEGURO,
                   "precio_unitario": "60.0", "cardcode": "P0082"},
    })
    return vivos, contenedores, elementos


def test_el_doble_flete_sano_no_repone_un_flete_legado(hubspot):
    """El objetivo pedía un FLETES único y daba los dos tramos por ajenos: publicar el
    65027508906 creó un tercer flete de 1.300 € y SAP sacó un tercer pedido de compra.
    """
    hubspot["vivos"], hubspot["contenedores"], hubspot["elementos"] = _negocio_doble_flete()

    plan = planificar("tok", DEAL)
    assert plan["crear"] == []
    assert plan["archivar"] == []
    assert plan["ajenos"] == []


def test_un_fletes_legado_junto_a_los_tramos_no_se_repone(hubspot):
    """La Línea Contenedor FLETES que dejó el incidente no puede resucitar su line item."""
    vivos, contenedores, elementos = _negocio_doble_flete()
    cont = _nombre_cont(1)
    elementos["el_fletes"] = {"nombre": f"{cont} - FLETES", "nombre_contenedor": cont,
                              "tipo_articulo": "flete", "cantidad": "1",
                              "product_sku": SKU_FLETE, "precio_unitario": "1300.0",
                              "cardcode": "P0780"}
    hubspot["vivos"], hubspot["contenedores"], hubspot["elementos"] = vivos, contenedores, elementos

    plan = planificar("tok", DEAL)
    assert plan["crear"] == []
    assert plan["archivar"] == []


def test_el_tramo_perdido_vuelve_con_su_precio_su_transitario_y_su_iva(hubspot):
    """La plantilla de flete es la línea del otro tramo; lo propio del tramo sale de su
    Línea Contenedor."""
    vivos, contenedores, elementos = _negocio_doble_flete()
    del vivos["ter"]
    hubspot["vivos"], hubspot["contenedores"], hubspot["elementos"] = vivos, contenedores, elementos

    plan = planificar("tok", DEAL)
    assert [c["properties"]["name"] for c in plan["crear"]] == [f"{_nombre_cont(1)} - FLETE TERRESTRE"]
    nueva = plan["crear"][0]["properties"]
    assert nueva["price"] == "1200.0"
    assert nueva["cardcode"] == "P0690"
    assert nueva["tipo_iva_soportado"] == "S21"
    assert plan["archivar"] == []
