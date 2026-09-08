"""El guardado de documentación no puede pisar el check de cotización rápida.

Ticket de Elisa (07/09/2026): el comercial marca "Cotización Rápida" en
TransitarioTab, se guarda bien, y 13 minutos después la propiedad aparece vacía
en el historial del negocio, otra vez a nombre de la integración del cotizador.

La culpa era de `build_update_properties`: convierte a "" todo campo que llegue a
null, y `cotizacion_rapida` estaba declarado en UpdateDocumentacionData aunque
DocumentacionTab no lo edita ni lo manda. Cada "Siguiente" del tab de
documentación borraba el check.
"""

from app.models.schemas import UpdateDocumentacionData, DealDocumentacionData
from app.utils.property_helpers import build_update_properties

# Lo que DocumentacionTab.handleGuardarDocumentacion mete en payload.deal.
PAYLOAD_DEL_TAB = {
    "preprints_language": "es",
    "direccion_fiscal": "Avda. Américo Vespucio 5, Sevilla",
    "documentos_a_generar": "bl;packing_list",
    "metodo_pago_plantilla": "cotizacion_rapida",
    "banco_cobro": "cotizacion_rapida",
    "texto_base_oferta": "<p>Oferta</p>",
}


def test_el_guardado_de_documentacion_no_toca_cotizacion_rapida():
    props = build_update_properties(
        UpdateDocumentacionData(**PAYLOAD_DEL_TAB), UpdateDocumentacionData
    )
    assert "cotizacion_rapida" not in props


def test_los_campos_del_tab_si_se_siguen_escribiendo():
    props = build_update_properties(
        UpdateDocumentacionData(**PAYLOAD_DEL_TAB), UpdateDocumentacionData
    )
    for campo, valor in PAYLOAD_DEL_TAB.items():
        assert props[campo] == valor

    # Los que el tab manda a null sí limpian: es el borrado intencionado.
    assert props["texto_en_bl"] == ""


def test_el_get_sigue_devolviendo_el_flag_para_pintar_el_check():
    assert "cotizacion_rapida" in DealDocumentacionData.model_fields
