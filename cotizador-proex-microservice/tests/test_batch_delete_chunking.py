"""`batch_delete_custom_objects` trocea a 100.

El re-sync borra de golpe todos los Elementos del deal (108-350 en cotizaciones
de 12 contenedores). Sin trocear, HubSpot rechaza el batch entero y el except
del helper se lo tragaba: no se borraba nada y el flujo seguía como si sí.
"""

from unittest.mock import MagicMock

from app.core.custom_objects_constants import OBJECT_TYPE_ELEMENTO
from app.services.custom_objects import batch_delete_custom_objects


def _client_con_limite():
    client = MagicMock()

    def _archive(object_type=None, batch_input_simple_public_object_id=None):
        inputs = batch_input_simple_public_object_id.inputs
        if len(inputs) > 100:
            raise RuntimeError("(400) maximum number of inputs supported in a batch is 100")
        llamadas.append(len(inputs))

    llamadas = []
    client.crm.objects.batch_api.archive.side_effect = _archive
    return client, llamadas


def test_250_elementos_se_borran_en_lotes_de_100():
    client, llamadas = _client_con_limite()
    ids = [str(9000 + n) for n in range(250)]

    borrados = batch_delete_custom_objects(client, OBJECT_TYPE_ELEMENTO, ids)

    assert llamadas == [100, 100, 50]
    assert borrados == 250


def test_un_chunk_fallido_no_arrastra_a_los_demas():
    client, llamadas = _client_con_limite()

    def _archive(object_type=None, batch_input_simple_public_object_id=None):
        inputs = batch_input_simple_public_object_id.inputs
        llamadas.append(len(inputs))
        if len(llamadas) == 1:
            raise RuntimeError("boom")

    client.crm.objects.batch_api.archive.side_effect = _archive

    borrados = batch_delete_custom_objects(client, OBJECT_TYPE_ELEMENTO, [str(n) for n in range(150)])

    assert llamadas == [100, 50]
    assert borrados == 50
