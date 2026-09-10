"""El documento de la cotización lo calcula el backend, no la plantilla.

Los números de estos tests salen de PDFs reales publicados, para que un cambio en el
cálculo se note aquí y no en un documento que ya está en manos del cliente:

- Exportación, 20 contenedores (deal 64836801849, cotización del 09/09/2026):
  EXW 395.102,40 / FOB 411.076,80 / CFR 448.704,00 / CIF 449.904,00, 62.400 bultos,
  9 filas agrupadas por artículo a 7,21 €.
- Nacional DAP TERRESTRE (deal 64134026617, PDF del 20/08/2026): 9 filas a 7,20 €
  más una promoción a 0,00, base 25.056,00 + IVA 21 % 5.261,76 = 30.317,76.
- Nacional EXW (deal 64338364037, PDF del 26/08/2026): el SKU 42078145060 sale DOS
  veces, 240 cajas a 6,40 y 120 a 0,00 de promoción. Por eso en nacional no se agrupa.
"""

from app.services.quote_documento import (
    construir_documento,
    es_nacional,
    precio_caja_final,
)

IVA_R21 = {("R21", "NACIONAL"): (21.0, 5.2)}


def _porcentajes(code, residencia):
    return IVA_R21.get((code, residencia), (None, None))


def _li(**props):
    base = {
        "hs_product_type": "Articulo",
        "tipo_articulo": "fabricante",
        "peso_bruto": "6.856",
        "peso_neto": "6.0",
        "tipo_iva_repercutido": "EX",
    }
    base.update(props)
    return {"id": props.get("hs_sku", "x"), "properties": base}


def _contenedor(nombre):
    return {"id": nombre, "properties": {
        "hs_product_type": "container", "tipo_articulo": "transitario",
        "name": nombre, "numero_container": nombre, "quantity": "1", "price": "0.0",
    }}


def _deal(**props):
    base = {"tipo_de_venta": "CIF", "puerto_destino": "Abidjan, CI"}
    base.update(props)
    return base


# El caso real: 20 contenedores de 3.120 cajas, 9 artículos, ancla 7,21 CIF.
# (cajas por contenedor, peso bruto por caja) — los pesos son los del PDF, que no son
# iguales entre artículos y por eso el bruto sale 425.491,20 y no 62.400 x 6,856.
CAJAS_POR_ARTICULO = {
    "42078145099": (600, "6.856"), "42050417012": (120, "6.65"),
    "42050417035": (120, "6.856"), "42078145101": (600, "6.856"),
    "42078144943": (240, "6.784"), "42050417060": (240, "6.856"),
    "42078145088": (240, "6.856"), "42050417004": (360, "6.65"),
    "42078145056": (600, "6.856"),
}


def _pedido_20_contenedores():
    items = []
    for n in range(1, 21):
        cont = f"64836801849-{n:03d}"
        items.append(_contenedor(cont))
        for sku, (cajas, peso) in CAJAS_POR_ARTICULO.items():
            items.append(_li(
                hs_sku=sku, name=f"{cont} - ART {sku}", description=f"ART {sku}",
                numero_container=cont, quantity=str(cajas), price="6.332",
                peso_bruto=peso,
                precio_negociado_tipo_venta="7.21", incoterm_precio_negociado="CIF",
            ))
        items.append({"id": f"f{n}", "properties": {
            "hs_product_type": "Flete", "tipo_articulo": "transitario",
            "name": f"{cont} - FLETES", "numero_container": cont,
            "quantity": "1", "price": "2680.0"}})
        items.append({"id": f"s{n}", "properties": {
            "hs_product_type": "service", "tipo_articulo": "seguro",
            "name": f"{cont} - SEGURO MERCANCÍA", "numero_container": cont,
            "quantity": "1", "price": "60.0"}})
    return items


DESGLOSE_20 = {"fobExpenses": 15974.4, "seafreightRate": 37627.2, "insurance": 1200.0}


def _doc_20(**extra):
    kwargs = dict(
        line_items=_pedido_20_contenedores(),
        deal=_deal(),
        tipo_cliente="exportacion", residennum="4", equ="No",
        desgloses=DESGLOSE_20, total_cajas=62400, num_contenedores=20,
        porcentajes_iva=_porcentajes,
    )
    kwargs.update(extra)
    return construir_documento(**kwargs)


def test_exportacion_veinte_contenedores_reproduce_el_pdf_publicado():
    doc = _doc_20()

    assert doc["eje"] == "exportacion"
    assert doc["incoterm"] == "CIF"
    assert doc["stats"]["bultos"] == 62400
    assert doc["stats"]["contenedores"] == 20
    assert doc["stats"]["pesoNeto"] == 374400.00
    assert doc["stats"]["pesoBruto"] == 425491.20
    assert doc["totales"] == {
        "tabla": 449904.00, "exw": 395102.40, "fob": 411076.80,
        "cfr": 448704.00, "cif": 449904.00,
    }


def test_exportacion_multicontenedor_agrupa_por_articulo():
    doc = _doc_20()

    assert len(doc["filas"]) == 9
    assert {f["cod"] for f in doc["filas"]} == set(CAJAS_POR_ARTICULO)
    # Las cajas de los 20 contenedores se suman en una sola fila por artículo.
    por_cod = {f["cod"]: f for f in doc["filas"]}
    assert por_cod["42078145099"]["cajas"] == 600 * 20
    assert por_cod["42078145099"]["precio"] == 7.21
    assert por_cod["42078145099"]["importe"] == 86520.00
    assert sum(f["importe"] for f in doc["filas"]) == 449904.00


def test_el_orden_de_las_filas_es_estable():
    """El de las asociaciones de HubSpot cambia entre publicaciones."""
    doc = _doc_20()
    otro = _doc_20(line_items=list(reversed(_pedido_20_contenedores())))

    assert [f["cod"] for f in doc["filas"]] == [f["cod"] for f in otro["filas"]]
    assert [f["cod"] for f in doc["filas"]] == sorted(CAJAS_POR_ARTICULO)


def test_nacional_dap_terrestre_reproduce_el_pdf():
    cont = "64134026617-001"
    cajas = {"42078145088": 600, "42078145068": 120, "42050417004": 120,
             "42078145101": 240, "42078144943": 240, "42078145099": 840,
             "42050417060": 120, "42078145056": 1200}
    items = [_contenedor(cont)]
    for sku, n in cajas.items():
        items.append(_li(hs_sku=sku, name=f"{cont} - ART {sku}", numero_container=cont,
                         quantity=str(n), price="6.808", tipo_iva_repercutido="R21",
                         precio_negociado_tipo_venta="7.2",
                         incoterm_precio_negociado="DAP_TERRESTRE"))
    items.append(_li(hs_sku="42078145060", name=f"{cont} - ART PROMO", numero_container=cont,
                     quantity="120", price="0.0", tipo_iva_repercutido="R21"))

    doc = construir_documento(
        line_items=items, deal=_deal(tipo_de_venta="DAP TERRESTRE", puerto_destino="ALMERIA, ES"),
        tipo_cliente="nacional", residennum="1", equ="No",
        desgloses={"fobExpenses": 1353.33, "seafreightRate": 0, "insurance": 60.0},
        total_cajas=3600, num_contenedores=1, porcentajes_iva=_porcentajes,
    )

    assert doc["eje"] == "nacional"
    assert len(doc["filas"]) == 9
    assert doc["totales"]["tabla"] == 25056.00
    assert doc["totales"]["base"] == 25056.00
    assert doc["totales"]["impuestos"] == 5261.76
    assert doc["totales"]["total"] == 30317.76
    assert doc["totales"]["ivaBuckets"] == [
        {"iva": 21.0, "rec": 0.0, "base": 25056.00, "imp": 5261.76}
    ]


def test_nacional_no_agrupa_para_no_fundir_la_promocion_con_su_articulo():
    """PDF del deal 64338364037: 42078145060 sale a 6,40 y a 0,00 en dos filas."""
    cont = "64338364037-001"
    items = [
        _contenedor(cont),
        _li(hs_sku="42078145060", name=f"{cont} - ORIGINAL", numero_container=cont,
            quantity="240", price="6.4", tipo_iva_repercutido="R21",
            precio_negociado_tipo_venta="6.4", incoterm_precio_negociado="EXW"),
        _li(hs_sku="42078145060", name=f"{cont} - ORIGINAL", numero_container=cont,
            quantity="120", price="0.0", tipo_iva_repercutido="R21"),
    ]

    doc = construir_documento(
        line_items=items, deal=_deal(tipo_de_venta="EXW", puerto_destino="MURCIA, ES"),
        tipo_cliente="nacional", residennum="1", equ="No",
        desgloses={"fobExpenses": 0, "seafreightRate": 0, "insurance": 0},
        total_cajas=360, num_contenedores=1, porcentajes_iva=_porcentajes,
    )

    assert len(doc["filas"]) == 2
    assert [f["cajas"] for f in doc["filas"]] == [240, 120]
    assert doc["filas"][1]["promo"] is True
    assert doc["totales"]["base"] == 1536.00


def test_el_recargo_de_equivalencia_solo_entra_si_la_empresa_lo_tiene():
    cont = "1-001"
    items = [_contenedor(cont), _li(hs_sku="A", name=f"{cont} - A", numero_container=cont,
                                    quantity="100", price="10.0", tipo_iva_repercutido="R21")]
    comun = dict(
        line_items=items, deal=_deal(tipo_de_venta="EXW", puerto_destino="ALMERIA, ES"),
        tipo_cliente="nacional", residennum="1",
        desgloses={"fobExpenses": 0, "seafreightRate": 0, "insurance": 0},
        total_cajas=100, num_contenedores=1, porcentajes_iva=_porcentajes,
    )

    sin_re = construir_documento(equ="No", **comun)
    con_re = construir_documento(equ="si", **comun)

    assert sin_re["filas"][0]["recPct"] == 0.0
    assert sin_re["totales"]["impuestos"] == 210.00
    assert con_re["filas"][0]["recPct"] == 5.2
    assert con_re["totales"]["impuestos"] == 262.00


def test_ceuta_y_melilla_salen_en_formato_exportacion():
    """Clientes nacionales fuera del territorio aduanero de la UE: la aduana exige
    Incoterm y peso neto/bruto."""
    assert es_nacional("nacional", "ALMERIA, ES") is True
    assert es_nacional("nacional", "CEU") is False
    assert es_nacional("nacional", "Melilla, ES") is False
    assert es_nacional("exportacion", "Abidjan, CI") is False
    assert es_nacional("", "Abidjan, CI") is False


def test_el_ancla_gana_al_recompuesto_pero_solo_en_su_tipo_de_venta():
    linea = {"price": "6.332", "precio_negociado_tipo_venta": "7.21",
             "incoterm_precio_negociado": "CIF"}

    assert precio_caja_final(linea, 0.877795, "CIF") == 7.21
    # Con otro incoterm el ancla no aplica y manda el recompuesto.
    assert precio_caja_final(linea, 0.5, "FOB") == 6.832
    # Si el recompuesto supera el ancla, tampoco: el precio ha subido de verdad.
    assert precio_caja_final(linea, 2.0, "CIF") == 8.332
    # Precio 0 es una promoción y no recibe reparto de gastos.
    assert precio_caja_final({"price": "0.0"}, 5.0, "CIF") == 0.0


def test_el_desagregado_reparte_los_gastos_entre_los_contenedores_del_pedido():
    doc = _doc_20(deal=_deal(desagregar_cotizacion="true"))

    assert len(doc["paginas"]) == 20
    assert len(doc["catalogo"]) == 9
    primera = doc["paginas"][0]
    assert primera["nombre"] == "64836801849-001"
    assert primera["stats"]["bultos"] == 3120
    assert primera["totales"]["tabla"] == 22495.20
    # Las 20 páginas suman lo mismo que la tabla agregada.
    assert round(sum(p["totales"]["tabla"] for p in doc["paginas"]), 2) == 449904.00
    assert sum(p["stats"]["bultos"] for p in doc["paginas"]) == 62400
    # Las filas apuntan al catálogo por índice, no repiten los nombres.
    assert all("a" in f and "es" not in f for f in primera["filas"])


def test_sin_desagregar_no_se_emiten_paginas():
    """Con 50 contenedores las páginas ocupan 52.000 caracteres y el payload no
    cabría en los 65.536 de la propiedad."""
    doc = _doc_20()

    assert "paginas" not in doc
    assert "catalogo" not in doc


def test_sin_estadisticas_de_la_card_las_cajas_salen_de_las_lineas():
    doc = _doc_20(total_cajas=0, num_contenedores=0)

    assert doc["stats"]["bultos"] == 62400
    assert doc["stats"]["contenedores"] == 20


# El documento viaja al PDF como registros delimitados: `fromjson` no responde en el
# contexto de la plantilla (10/09/2026, payload de 12.675 caracteres entero y parseo
# nulo), y partir cadenas es lo que ya sostiene el resto del documento.

def _registros(txt):
    return [r.split("|") for r in txt.split("~")]


def test_la_serializacion_lleva_la_tabla_y_los_totales():
    from app.services.quote_documento import serializar_documento

    txt = serializar_documento(_doc_20())
    por_tipo = {}
    for campos in _registros(txt):
        por_tipo.setdefault(campos[0], []).append(campos)

    assert por_tipo["E"][0][1] == "exportacion"
    assert por_tipo["I"][0][1] == "CIF"
    assert por_tipo["S"][0][1:] == ["62400", "20", "374400", "425491.2"]
    assert por_tipo["T"][0][1:] == ["449904", "395102.4", "411076.8", "448704", "449904"]
    assert por_tipo["D"][0][1:] == ["15974.4", "37627.2", "1200"]
    assert len(por_tipo["F"]) == 9
    assert "N" not in por_tipo and "B" not in por_tipo


def test_la_serializacion_de_una_fila_conserva_los_once_campos():
    from app.services.quote_documento import serializar_documento

    fila = [c for c in _registros(serializar_documento(_doc_20())) if c[0] == "F"][0]

    assert fila == ["F", "42050417004", "ART 42050417004", "", "",
                    "7200", "6.65", "7.21", "51912", "0", "0", "0"]


def test_nacional_serializa_los_totales_y_los_buckets():
    from app.services.quote_documento import serializar_documento

    cont = "1-001"
    items = [_contenedor(cont), _li(hs_sku="A", name=f"{cont} - A", numero_container=cont,
                                    quantity="100", price="10.0", tipo_iva_repercutido="R21")]
    doc = construir_documento(
        line_items=items, deal=_deal(tipo_de_venta="EXW", puerto_destino="ALMERIA, ES"),
        tipo_cliente="nacional", residennum="1", equ="si",
        desgloses={"fobExpenses": 0, "seafreightRate": 0, "insurance": 0},
        total_cajas=100, num_contenedores=1, porcentajes_iva=_porcentajes,
    )
    por_tipo = {c[0]: c for c in _registros(serializar_documento(doc))}

    assert por_tipo["E"][1] == "nacional"
    assert por_tipo["N"][1:] == ["1000", "262", "1262"]
    assert por_tipo["B"][1:] == ["21", "5.2", "1000", "262"]
    assert por_tipo["F"][10:] == ["21", "5.2"]


def test_el_desagregado_serializa_catalogo_y_una_pagina_por_contenedor():
    from app.services.quote_documento import serializar_documento

    txt = serializar_documento(_doc_20(deal=_deal(desagregar_cotizacion="true")))
    tipos = [c[0] for c in _registros(txt)]

    assert tipos.count("C") == 9
    assert tipos.count("P") == 20
    assert tipos.count("Q") == 180
    # Cada Q va detrás de su P: la plantilla cierra la página al abrir la siguiente.
    assert tipos.index("P") < tipos.index("Q")
    primera = [c for c in _registros(txt) if c[0] == "P"][0]
    assert primera[1] == "64836801849-001"
    assert primera[2] == "3120"
    assert primera[5] == "22495.2"


def test_los_separadores_y_las_comillas_no_sobreviven_al_texto_libre():
    from app.services.quote_documento import serializar_documento

    cont = "1-001"
    items = [_contenedor(cont), _li(
        hs_sku="A|B", name=f"{cont} - raro", numero_container=cont,
        description='ART "PIPE|TILDE~QUOTE"', quantity="1", price="1.0")]
    doc = construir_documento(
        line_items=items, deal=_deal(tipo_de_venta="EXW"),
        tipo_cliente="exportacion", residennum="4", equ="No",
        desgloses={"fobExpenses": 0, "seafreightRate": 0, "insurance": 0},
        total_cajas=1, num_contenedores=1, porcentajes_iva=_porcentajes,
    )
    fila = [c for c in _registros(serializar_documento(doc)) if c[0] == "F"][0]

    assert len(fila) == 12
    assert fila[1] == "A B"
    assert fila[2] == "ART  PIPE TILDE QUOTE"


def test_la_serializacion_es_mas_compacta_que_el_json():
    import json as _json
    from app.services.quote_documento import serializar_documento

    doc = _doc_20(deal=_deal(desagregar_cotizacion="true"))

    txt = serializar_documento(doc)
    assert len(txt) < len(_json.dumps(doc, ensure_ascii=False))
    # 20 contenedores desagregados tienen que caber junto al resto del payload.
    assert len(txt) < 20000
