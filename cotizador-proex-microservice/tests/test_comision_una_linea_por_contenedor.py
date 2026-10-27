"""Cada contenedor lleva una sola línea de comisión, con el importe vigente del negocio.

Ticket de María Bernal (27/10/2026), cliente AJ VIERCI:
- 66502772767 (cotización rápida): guardar Artículos archiva contenedor y artículos
  pero no la comisión; el contenedor renacía como nuevo con el mismo número y cada
  vuelta le añadía otra línea. El -001 acabó con tres (936, 936 y 954).
- 66730502410: el importe pasó de 0,394 a 0,303 €/caja en Cliente y la línea se quedó
  en 1.229,28 (0,394 × 3.120), porque ese guardado no tocaba los line items.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.models.schemas import UpdateClientDataRequest, UpdateDealData
from app.routes import deals

DEAL = "66502772767"
CONT = f"{DEAL}-001"


def _ctx(tipo="importe_por_negocio", importe=954.0, codigo="P0694"):
    return {
        "tipo_comision": tipo,
        "importe_comision": importe,
        "codigo_intermediario": codigo,
        "product_id": "prod_comision",
        "product_name": "COMISIONES INTERMEDIACION DE VENTAS",
        "product_sku": "prod_comision",
        "iva_repercutido": "EX",
        "iva_soportado": "SEXN",
    }


def _contenedor(lid, nombre):
    return lid, {"hs_product_type": "container", "tipo_articulo": "transitario", "numero_container": nombre, "name": nombre}


def _articulo(lid, nombre, cajas):
    return lid, {"hs_product_type": "inventory", "tipo_articulo": "fabricante", "numero_container": nombre, "quantity": str(cajas)}


def _comision(lid, nombre, precio, cardcode="P0694"):
    return lid, {
        "hs_product_type": "Servicio", "tipo_articulo": "comisiones",
        "numero_container": nombre, "price": str(precio), "cardcode": cardcode,
    }


@pytest.fixture
def hubspot(monkeypatch):
    estado = {"lineas": {}, "llamadas": [], "falla_lectura": False}

    monkeypatch.setattr(deals, "_get_associated_ids", lambda deal_id, tipo: list(estado["lineas"]))

    def _post(url, headers=None, json=None, timeout=None):
        accion = url.rsplit("/", 1)[-1]
        estado["llamadas"].append((accion, json["inputs"]))
        if accion == "read":
            if estado["falla_lectura"]:
                return SimpleNamespace(ok=False, status_code=502, text="bad gateway", json=lambda: {})
            results = [
                {"id": i["id"], "properties": dict(estado["lineas"][i["id"]])}
                for i in json["inputs"] if i["id"] in estado["lineas"]
            ]
            return SimpleNamespace(ok=True, status_code=200, text="", json=lambda: {"results": results})
        if accion == "archive":
            for i in json["inputs"]:
                estado["lineas"].pop(i["id"])
        elif accion == "update":
            for i in json["inputs"]:
                estado["lineas"][i["id"]].update(i["properties"])
        elif accion == "create":
            for n, i in enumerate(json["inputs"]):
                estado["lineas"][f"9000{len(estado['lineas'])}{n}"] = dict(i["properties"])
        return SimpleNamespace(ok=True, status_code=200, text="", json=lambda: {"results": []})

    monkeypatch.setattr(deals.requests, "post", _post)

    def cargar(*lineas):
        estado["lineas"].update({lid: props for lid, props in lineas})

    estado["cargar"] = cargar
    return estado


def _comisiones(estado, nombre=CONT):
    return {
        lid: props for lid, props in estado["lineas"].items()
        if props.get("tipo_articulo") == "comisiones" and props.get("numero_container") == nombre
    }


def _sincronizar(ctx, **kwargs):
    return deals._sincronizar_lineas_comision(DEAL, headers={}, intermediario_ctx=ctx, **kwargs)


def test_el_contenedor_que_renace_conserva_una_sola_comision_con_el_ultimo_importe(hubspot):
    hubspot["cargar"](
        _comision("60396386193", CONT, 936.0),
        _comision("60416275469", CONT, 936.0),
        _contenedor("60618299044", CONT),
        _articulo("60618299041", CONT, 3120),
    )

    resumen = _sincronizar(_ctx(), cajas_por_contenedor={CONT: 3120})

    assert resumen == {"creadas": 0, "actualizadas": 1, "archivadas": 1}
    assert list(_comisiones(hubspot)) == ["60396386193"]
    assert hubspot["lineas"]["60396386193"]["price"] == "954.0"


def test_cambiar_el_importe_en_cliente_actualiza_la_linea_existente(hubspot):
    hubspot["cargar"](
        _contenedor("60624698444", CONT),
        _articulo("60624698455", CONT, 840),
        _articulo("60624698453", CONT, 2280),
        _comision("60624698446", CONT, 1229.28),
    )

    resumen = _sincronizar(_ctx(tipo="importe_por_cajas", importe=0.303))

    assert resumen == {"creadas": 0, "actualizadas": 1, "archivadas": 0}
    assert hubspot["lineas"]["60624698446"]["price"] == "945.36"


def test_sin_cambios_no_escribe_nada(hubspot):
    hubspot["cargar"](
        _contenedor("c1", CONT),
        _articulo("a1", CONT, 3120),
        _comision("10", CONT, 945.36),
    )

    assert _sincronizar(_ctx(tipo="importe_por_cajas", importe=0.303)) == {"creadas": 0, "actualizadas": 0, "archivadas": 0}
    assert [accion for accion, _ in hubspot["llamadas"]] == ["read"]


def test_el_contenedor_sin_comision_la_recibe(hubspot):
    hubspot["cargar"](_contenedor("c1", CONT), _articulo("a1", CONT, 3120))

    _sincronizar(_ctx(), cajas_por_contenedor={CONT: 3120}, deal_almacen="10", tipo_contenedor="D20DR")

    creadas = [i for accion, inputs in hubspot["llamadas"] if accion == "create" for i in inputs]
    assert len(creadas) == 1
    props = creadas[0]["properties"]
    assert props["numero_container"] == CONT
    assert props["name"] == f"{CONT} - COMISIONES INTERMEDIACION DE VENTAS"
    assert props["price"] == "954.0"
    assert props["cardcode"] == "P0694"
    assert props["tipo_articulo"] == "comisiones"
    assert props["tipo_iva_repercutido"] == "EX"
    assert creadas[0]["associations"][0]["to"] == {"id": DEAL}


def test_por_negocio_se_reparte_entre_todos_los_contenedores(hubspot):
    otro = f"{DEAL}-002"
    hubspot["cargar"](_comision("10", CONT, 954.0))

    _sincronizar(_ctx(importe=954.0), cajas_por_contenedor={CONT: 3120, otro: 1000})

    assert hubspot["lineas"]["10"]["price"] == "477.0"
    assert [p["price"] for p in _comisiones(hubspot, otro).values()] == ["477.0"]


def test_sin_comision_repercutida_se_archivan_todas(hubspot):
    hubspot["cargar"](
        _contenedor("c1", CONT),
        _comision("10", CONT, 936.0),
        _comision("11", CONT, 936.0),
    )

    assert _sincronizar(None)["archivadas"] == 2
    assert _comisiones(hubspot) == {}


def test_la_comision_de_un_contenedor_que_ya_no_existe_se_archiva(hubspot):
    hubspot["cargar"](
        _contenedor("c1", CONT),
        _comision("10", CONT, 477.0),
        _comision("11", f"{DEAL}-002", 477.0),
    )

    _sincronizar(_ctx(importe=954.0))

    assert "11" not in hubspot["lineas"]
    assert hubspot["lineas"]["10"]["price"] == "954.0"


def test_cambiar_de_intermediario_cambia_el_cardcode(hubspot):
    hubspot["cargar"](_contenedor("c1", CONT), _comision("10", CONT, 954.0, cardcode="P0694"))

    _sincronizar(_ctx(codigo="P0617"))

    assert hubspot["lineas"]["10"]["cardcode"] == "P0617"


def test_si_no_puede_leer_las_lineas_no_toca_nada(hubspot):
    hubspot["cargar"](_contenedor("c1", CONT), _comision("10", CONT, 936.0), _comision("11", CONT, 936.0))
    hubspot["falla_lectura"] = True

    assert _sincronizar(None) is None
    assert [accion for accion, _ in hubspot["llamadas"]] == ["read"]
    assert set(hubspot["lineas"]) == {"c1", "10", "11"}


def test_el_contexto_estricto_lanza_si_hubspot_falla():
    class _Caido:
        def get_by_id(self, *a, **k):
            raise RuntimeError("HubSpot 502")

    client = SimpleNamespace(crm=SimpleNamespace(deals=SimpleNamespace(basic_api=_Caido())))

    assert deals._fetch_intermediario_context(DEAL, client, None) is None
    with pytest.raises(RuntimeError):
        deals._fetch_intermediario_context(DEAL, client, None, estricto=True)


PAYLOAD_CLIENTE = {
    "tipo_de_venta": "CIF",
    "tipo_contenedor": "D20DR",
    "carga_por_palets": False,
    "margen_cliente": 0.12,
    "codigo_intermediario": "P0694",
    "repercutir_a_cliente": "si",
    "tipo_comision": "importe_por_cajas",
    "importe_comision": 0.303,
    "tipo_iva_comision": "EX",
    "tipo_iva_comision_soportado": "SEXN",
}


@pytest.fixture
def guardar_cliente(monkeypatch):
    sincronizados = []
    guardado = {}

    class _Deals:
        def get_by_id(self, deal_id, properties=None, **k):
            return SimpleNamespace(properties=dict(guardado))

        def update(self, deal_id, simple_public_object_input):
            guardado.update(simple_public_object_input["properties"])

    client = SimpleNamespace(access_token="t", crm=SimpleNamespace(deals=SimpleNamespace(basic_api=_Deals())))
    monkeypatch.setattr(deals.hubspot_client, "get_client", lambda: client)
    monkeypatch.setattr(deals.requests, "post", lambda *a, **k: SimpleNamespace(ok=True, json=lambda: {"results": []}))
    monkeypatch.setattr(deals, "_sincronizar_comision_tras_cliente", lambda deal_id, client: sincronizados.append(deal_id))

    def guardar(previo, **cambios):
        guardado.clear()
        guardado.update(previo)
        payload = UpdateClientDataRequest(deal=UpdateDealData(**{**PAYLOAD_CLIENTE, **cambios}))
        asyncio.run(deals.update_deal_client_data(data=payload, deal_id="66730502410"))
        return sincronizados

    return guardar


def test_guardar_cliente_con_otro_importe_cuadra_la_comision(guardar_cliente):
    previo = {"repercutir_a_cliente": "si", "tipo_comision": "importe_por_cajas", "importe_comision": "0.394", "codigo_intermediario": "P0694"}

    assert guardar_cliente(previo) == ["66730502410"]


def test_guardar_cliente_con_la_misma_comision_no_toca_las_lineas(guardar_cliente):
    previo = {"repercutir_a_cliente": "si", "tipo_comision": "importe_por_cajas", "importe_comision": "0.303", "codigo_intermediario": "P0694"}

    assert guardar_cliente(previo) == []
