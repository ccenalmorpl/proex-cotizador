"""El guardado de la pestaña Cliente no puede vaciar el idioma de los documentos.

Negocio 65428281880 (01/10/2026): al guardar el intermediario desde Cliente,
`preprints_language` pasó de "ES" a "" en el mismo PATCH. El idioma se elige en
DocumentacionTab, pero estaba declarado en UpdateDealData y
`build_update_properties` escribe "" en todo campo que llegue a null.
"""

from app.models.schemas import UpdateDealData
from app.utils.property_helpers import build_update_properties

# Lo que ClienteTab.buildUpdatePayload mete en payload.deal.
PAYLOAD_DEL_TAB = {
    "tipo_de_venta": "FOB",
    "tipo_contenedor": "D20DR",
    "carga_por_palets": False,
    "margen_cliente": 0.12,
    "codigo_intermediario": "P0617",
    "repercutir_a_cliente": "no",
    "tipo_comision": "importe_por_cajas",
    "importe_comision": 0.12,
    "tipo_iva_comision": "EX",
    "tipo_iva_comision_soportado": "S21",
    "fecha_caducidad_cotizacion": 1793318400000,
}


def test_el_guardado_de_cliente_no_toca_el_idioma():
    props = build_update_properties(UpdateDealData(**PAYLOAD_DEL_TAB), UpdateDealData)
    assert "preprints_language" not in props


def test_los_campos_del_tab_si_se_siguen_escribiendo():
    props = build_update_properties(UpdateDealData(**PAYLOAD_DEL_TAB), UpdateDealData)
    assert props["codigo_intermediario"] == "P0617"
    assert props["importe_comision"] == "0.12"
    assert props["fecha_caducidad_cotizacion"] == "1793318400000"
