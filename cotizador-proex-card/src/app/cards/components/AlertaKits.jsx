// Aviso de pedidos KIT que se generarán al ganar la cotización, con la solicitud
// por artículo.
//
// Va debajo del Resumen Total del Pedido (ContenedoresTab) y dentro del modal de
// edición per-contenedor. No va encima de cada artículo: el bloque se cuenta
// sobre el pedido entero, así que las cajas del mismo producto se suman en todos
// los contenedores.
//
// Tres cosas por artículo deficitario:
//   - Cajas KIT calculadas, EDITABLES: el cálculo es el mínimo necesario, pero
//     el comercial puede pedir más añadiendo bloques enteros.
//   - Solicitar pedido KIT: confirma que se pide esa producción adicional.
//   - Cajas disponibles en KIT abiertos de esa misma referencia, para que se vea
//     que quizá no haga falta producir: se puede consumir stock ya fabricado.
//
// El KIT no se crea aquí: esto es la solicitud, y se materializa al ganar.

import React from "react";
import {
  Alert,
  Button,
  Flex,
  Modal,
  Text,
  Select,
  Checkbox,
  NumberInput,
  Stack,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TableHeader,
} from "@hubspot/ui-extensions";

import { marcaEfectivaDeKit } from "../utils/kits.utils";
import { useNegociosGeneradores } from "../hooks/useNegociosGeneradores";
import ElegirNegociosKitModal from "./ElegirNegociosKitModal";

const fmt = (n) => Number(n || 0).toLocaleString("es-ES");

// Aviso de artículos sin `tamano_kit`. Apagado a propósito: hoy sólo 1 de los 326
// productos de fabricante tiene el dato, así que saldría en casi todas las
// cotizaciones y parecería un fallo en vez de información.
//
// Encender cuando PROEX haya rellenado el lote mínimo en el catálogo. Entonces sí
// interesa: distingue "este artículo no se fabrica en bloques" de "falta el dato y
// hay un KIT invisible", que se ven igual y significan lo contrario.
const MOSTRAR_ARTICULOS_SIN_DATO = false;

/**
 * La división y el redondeo, tal como los escribe Administración:
 * "14.508 / 1.894 = 7,66 → 8 bloques de 1.894".
 *
 * Es el paso donde un humano duda —¿por qué 8 y no 7?— y el que usan para
 * comprobar el número a mano. Sin él la fila da un total que hay que creerse.
 *
 * Si se piden bloques de más, se distingue el mínimo que sale de la división de
 * los que se van a producir de verdad: la división sólo justifica el mínimo.
 */
const explicacionBloques = (k, bloquesElegidos, aProducirTotal, bloquesMinimos) => {
  const aProducir = aProducirTotal ?? k.cajas;
  // El minimo se recibe recalculado, NO se lee de `k`: `k.bloquesMinimos` sale
  // del analisis de esta oferta sola, y al anotar otras ofertas se queda corto.
  // Comparar contra el viejo hacia decir "2 min, se producen 4" cuando 4 ERA el
  // minimo, dando a entender que alguien habia pedido bloques de mas.
  const minimo = bloquesMinimos ?? k.bloquesMinimos;
  if (!k.bloque || k.bloque <= 0 || aProducir <= 0) return "";

  const cociente = (aProducir / k.bloque).toFixed(2).replace(".", ",");
  const division = `${fmt(aProducir)} / ${fmt(k.bloque)} = ${cociente}`;

  // Una sola línea: cada línea extra multiplica por el número de artículos, y
  // una fila de tres pisos por producto hace la tabla ilegible. Se quita "de
  // 1894" porque ese número ya está en la propia división.
  if (bloquesElegidos > minimo) {
    return `${division} → ${minimo} mín, se producen ${bloquesElegidos}`;
  }

  const plural = bloquesElegidos === 1 ? "bloque" : "bloques";
  return `${division} → ${bloquesElegidos} ${plural}`;
};

/**
 * Por SKU: cuántas cajas hay disponibles en KIT abiertos de esa referencia y
 * cuántas ha marcado esta oferta. Alimenta el cálculo de producción: el stock
 * marcado se descuenta antes de redondear a bloques.
 */
export const resumenKitsPorSku = (
  kits = [],
  marcados = {},
  stockUsadoPorSku = {},
  negocioId = ""
) => {
  const mapa = new Map();

  const entrada = (sku) =>
    mapa.get(sku) || {
      disponibles: 0,
      marcadas: 0,
      kitDisponibles: 0,
      kitMarcadas: 0,
      stockUsado: 0,
    };

  kits.forEach((k) => {
    if (!k) return;
    const sku = (k.productoSku || "").trim();
    if (!sku) return;
    const disp = Number(k.cajasDisponibles) || 0;
    // Marca efectiva: cuenta también lo que esta cotización ya tenía guardado en
    // HubSpot, para que al volver el cálculo salga igual que al salir.
    const marca = marcaEfectivaDeKit(k, marcados, negocioId);
    const marcadas = marca.marcado ? marca.cajas : 0;
    const prev = entrada(sku);
    mapa.set(sku, {
      ...prev,
      kitDisponibles: prev.kitDisponibles + disp,
      kitMarcadas: prev.kitMarcadas + marcadas,
    });
  });

  // Del stock libre sólo se sabe lo que alguien teclea que va a consumir: la
  // existencia vive en un documento fuera de HubSpot, así que no hay un "de
  // cuántas" contra el que contrastar. Se cuenta como disponible y usado a la vez.
  Object.keys(stockUsadoPorSku || {}).forEach((raw) => {
    const sku = (raw || "").trim();
    if (!sku) return;
    const usado = Math.max(0, Number(stockUsadoPorSku[raw]) || 0);
    if (usado <= 0) return;
    const prev = entrada(sku);
    mapa.set(sku, { ...prev, stockUsado: prev.stockUsado + usado });
  });

  // `disponibles` y `marcadas` son la SUMA de las dos fuentes, que es lo único
  // que necesita el cálculo: a la fábrica le da igual de dónde salga una caja ya
  // fabricada. El desglose viaja aparte, sólo para poder pintarlo separado.
  mapa.forEach((v, sku) => {
    mapa.set(sku, {
      ...v,
      disponibles: v.kitDisponibles + v.stockUsado,
      marcadas: v.kitMarcadas + v.stockUsado,
    });
  });

  return mapa;
};

const AlertaKits = ({
  analisis,
  // { [articuloId]: { solicitado: boolean, cajas: number } } — gobernado por
  // Cotizador.tsx para que la solicitud sobreviva a cambiar de tab y la vea el
  // resumen de Vista previa.
  solicitudes = {},
  onChangeSolicitud,
  // Stock libre consumido por SKU. Campo abierto: no se guarda en ninguna
  // propiedad, PROEX lo lleva en un documento aparte.
  stockUsado = {},
  onChangeStockUsado,
  // Necesario para cerrar el diálogo de negocios: los overlays los abre y cierra
  // HubSpot, no el componente.
  actions,
  // ID de la cotización actual. Hace falta para pedir al backend las OTRAS
  // ofertas abiertas: sin él se ofrecería a sí misma, que en la tabla va
  // siempre dentro y no se puede quitar.
  negocioId = "",
  soloLectura = false,
}) => {
  // Ofertas abiertas de estas referencias, del backend. Antes de la respuesta
  // el mapa esta vacio y la columna dice "solo esta oferta", que es tambien la
  // respuesta correcta cuando no hay ninguna.
  const negociosPorSku = useNegociosGeneradores(
    ((analisis?.kits || []).concat(analisis?.cubiertos || []))
      .map((k) => k.sku)
      .filter(Boolean),
    negocioId
  );

  if (!analisis) return null;

  const { kits = [], cubiertos = [], sinDato = [] } = analisis;
  if (kits.length === 0 && cubiertos.length === 0 && sinDato.length === 0) return null;

  // La tabla lleva las dos listas juntas. Un artículo que se cubre al consumir
  // stock NO debe salirse de la tabla: si la fila salta a otro sitio, el campo
  // donde acabas de escribir desaparece y ya no puedes corregir el número.
  // Cubierto es un estado de la fila, no otra sección.
  const filas = [...kits, ...cubiertos];

  // Sin solicitud registrada todavía, se asume solicitado con las cajas
  // calculadas: el KIT es lo que hace falta para cumplir el mínimo.
  // La cantidad del KIT no es libre: la fábrica produce en bloques enteros, así
  // que pedir más es añadir bloques. `bloquesExtra` es lo que el comercial suma
  // sobre el mínimo que ya obliga el déficit.
  const solicitudDe = (k) => {
    const s = solicitudes[k.articuloId];
    const bloquesExtra = Math.max(0, Number(s?.bloquesExtra) || 0);

    // Negocios abiertos anotados: su demanda pendiente entra en la produccion.
    // Es la idea de la reunion — consolidar en vez de dejar cajas paradas y que
    // otra oferta arranque su propia tirada dentro de un mes.
    const elegidos = negociosElegidos(k, s);
    const demandaExtra = elegidos.reduce((n, x) => n + (Number(x.cajas) || 0), 0);
    // Total de cajas de la referencia entre todas las ofertas anotadas: "ese total
    // se van a ir sumando las cajas de los negocios que seleccionemos" (13:51).
    const cajasTotales = k.cajas + demandaExtra;
    // La produccion NO descuenta las cajas ya marcadas en KIT abiertos: "los kits
    // que genero y los kits que consumo no interactuan entre ellos nunca" (04:18).
    // Son dos circuitos separados y mezclarlos era el error que habia que quitar.
    const aProducirTotal = cajasTotales;

    const bloque = k.bloque || 0;
    const bloquesMinimos =
      bloque > 0 && aProducirTotal > 0 ? Math.ceil(aProducirTotal / bloque) : 0;
    const bloques = bloquesMinimos + bloquesExtra;
    const tirada = bloques * bloque;
    const cajasCalculadas = Math.max(0, tirada - aProducirTotal);

    return {
      // Sin marcar por defecto: pedir el KIT es una decision, no algo que pase
      // por no tocar nada. Tres ofertas de la misma referencia pueden generar el
      // mismo KIT y solo una debe pedirlo —"el kit lo voy a generar solo en una"
      // (Cristobal, 2026-09-09)—; con la casilla marcada salian tres.
      solicitado: s?.solicitado ?? false,
      bloquesExtra,
      bloquesMinimos,
      bloques,
      elegidos,
      demandaExtra,
      aProducirTotal,
      cajasTotales,
      cajasCalculadas,
      // El calculo propone; la usuaria puede escribir otra cifra encima.
      cajas: s?.cajasManual ?? cajasCalculadas,
      tirada,
    };
  };

  // ── Ofertas abiertas de la referencia, ya filtradas por el backend
  const negociosDe = (k) => negociosPorSku[(k.sku || "").trim()] || [];

  function negociosElegidos(k, s) {
    const ids = new Set(s?.negocios || []);
    return negociosDe(k).filter((n) => ids.has(n.id));
  }

  // Opciones de producción: el mínimo que obliga el déficit y tres bloques más.
  // Cada opción enseña las cajas que quedarían, para no tener que echar la cuenta.
  const opcionesBloques = (k) =>
    [0, 1, 2, 3].map((extra) => {
      const bloques = solicitudDe(k).bloquesMinimos + extra;
      return {
        value: String(bloques),
        // Etiquetas cortas: la columna es estrecha y el Select las trunca con
        // puntos suspensivos, que es peor que no decir nada.
        label: extra === 0 ? `${bloques} (lo justo)` : `${bloques} (+${extra})`,
      };
    });

  // HubSpot gestiona los overlays: cerrar es pedírselo por id, no un estado.
  const cerrar = (articuloId) =>
    actions && actions.closeOverlay(`negocios-kit-${articuloId}`);

  const cambiar = (articuloId, cambios) => {
    if (typeof onChangeSolicitud === "function") onChangeSolicitud(articuloId, cambios);
  };

  const noSolicitados = kits.filter((k) => !solicitudDe(k).solicitado);

  return (
    <Flex direction="column" gap="small">
      {filas.length > 0 && (
        <>
          {/* Titulo y contexto, en paralelo al panel de "KIT en curso" de abajo:
              son dos circuitos distintos —aqui se generan cajas nuevas, abajo se
              consumen las ya fabricadas— y sin un titulo la primera tabla parecia
              una alerta suelta. El nombre es el que uso Cristobal en la reunion
              del 2026-09-03 (04:18): "kit a generar". */}
          <Text format={{ fontWeight: "bold" }}>KIT a generar en esta oferta</Text>
          <Text variant="microcopy">
            Cajas nuevas que hay que pedir a fábrica porque la producción no cae en
            bloques exactos.
          </Text>
          <Text variant="microcopy" format={{ fontWeight: "bold" }}>
            El número del pedido KIT es editable: el cálculo es solo una propuesta.
          </Text>

          {kits.length > 0 ? (
            <Alert
              title={
                kits.length === 1
                  ? "1 artículo no completa un bloque de fabricación"
                  : `${kits.length} artículos no completan un bloque de fabricación`
              }
              variant="warning"
            >
              La fábrica produce en bloques enteros: lo que sobre se puede pedir como
              pedido KIT y queda almacenado para ofertas posteriores. Pedirlo es
              opcional y la cotización se publica con KIT o sin él.
            </Alert>
          ) : (
            /* Todo cuadra: se consumen cajas ya fabricadas y la producción cae en
               bloques exactos, así que no hace falta pedir ningún KIT. */
            <Alert title="No hace falta ningún pedido KIT" variant="success">
              Con las cajas ya fabricadas que consume esta oferta, la producción
              queda en bloques exactos y no sobra nada.
            </Alert>
          )}

          <Table bordered={true}>
            <TableHead>
              <TableRow>
                <TableHeader>Artículo</TableHeader>
                <TableHeader>Cajas totales</TableHeader>
                <TableHeader>A fabricar</TableHeader>
                <TableHeader>Negocios generadores</TableHeader>
                <TableHeader>Pedido KIT</TableHeader>
                <TableHeader>Solicitar</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {filas.map((k) => {
                const s = solicitudDe(k);
                return (
                  <TableRow key={k.articuloId}>
                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Text>{k.nombre}</Text>
                        {k.sku && <Text variant="microcopy">{k.sku}</Text>}
                      </Flex>
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontWeight: "bold" }}>{fmt(s.cajasTotales)}</Text>
                        {/* El total suma las cajas de las otras ofertas anotadas:
                            el KIT se calcula sobre la demanda conjunta, no sobre
                            esta cotización sola. */}
                        {s.demandaExtra > 0 && (
                          <Text variant="microcopy">
                            {fmt(k.cajas)} esta oferta + {fmt(s.demandaExtra)} otras
                          </Text>
                        )}
                      </Flex>
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontWeight: "bold" }}>{fmt(s.tirada)}</Text>
                        <Text variant="microcopy">
                          {explicacionBloques(k, s.bloques, s.aProducirTotal, s.bloquesMinimos)}
                        </Text>
                      </Flex>
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        {/* Columna propia: anotar ofertas es una decisión aparte
                            de mirar cuánto se fabrica, y metida en "A fabricar"
                            estiraba esa celda. Se eligen en un diálogo porque
                            decidir necesita ver la etapa y las cajas de cada
                            oferta, y eso no cabe en una etiqueta truncada. */}
                        {!soloLectura && (
                          <Flex direction="column" gap="extra-small">
                            <Text variant="microcopy">
                              {/* Esta oferta cuenta siempre: es la que genera
                                  el KIT y no se puede quitar. */}
                              {s.elegidos.length > 0
                                ? `${s.elegidos.length + 1} negocios · ${fmt(
                                    s.cajasTotales
                                  )} cajas`
                                : "solo esta oferta"}
                            </Text>
                            {/* El Modal va como `overlay` del botón: es el patrón
                                que usa el resto de la card y el que HubSpot
                                gestiona. Con un prop `open` y estado propio no se
                                abre. */}
                            <Button
                              variant="secondary"
                              size="extra-small"
                              overlay={
                                <Modal
                                  id={`negocios-kit-${k.articuloId}`}
                                  title="Negocios que consumirán este KIT"
                                  width="lg"
                                >
                                  <ElegirNegociosKitModal
                                    articulo={k}
                                    negocios={negociosDe(k)}
                                    seleccionados={
                                      solicitudes[k.articuloId]?.negocios || []
                                    }
                                    onConfirmar={(ids) => {
                                      cambiar(k.articuloId, { negocios: ids });
                                      cerrar(k.articuloId);
                                    }}
                                    onCerrar={() => cerrar(k.articuloId)}
                                  />
                                </Modal>
                              }
                            >
                              Elegir negocios
                            </Button>
                          </Flex>
                        )}
                        {s.demandaExtra > 0 && (
                          <Text variant="microcopy">
                            suman {fmt(s.demandaExtra)} cajas a la producción
                          </Text>
                        )}
                      </Flex>
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        {soloLectura ? (
                          <Text format={{ fontWeight: "bold" }}>{fmt(s.cajas)}</Text>
                        ) : (
                          /* Editable por decision de la reunion: el sistema propone
                             el calculo y la usuaria puede cambiarlo. El desplegable
                             de bloques sigue debajo como ayuda. */
                          <NumberInput
                            name={`cajas-kit-${k.articuloId}`}
                            label=""
                            value={s.cajas}
                            min={0}
                            onChange={(v) =>
                              cambiar(k.articuloId, {
                                cajasManual: Math.max(0, Number(v) || 0),
                              })
                            }
                          />
                        )}
                        <Text variant="microcopy">
                          {fmt(s.tirada)} − {fmt(s.aProducirTotal)} ={" "}
                          {fmt(s.cajasCalculadas)} calculadas
                        </Text>
                        {!soloLectura && s.solicitado && k.cajasKit > 0 && (
                          <Select
                            name={"bloques-kit-" + k.articuloId}
                            label=""
                            value={String(s.bloques)}
                            options={opcionesBloques(k)}
                            onChange={(v) =>
                              cambiar(k.articuloId, {
                                bloquesExtra: Math.max(
                                  0,
                                  (Number(v) || k.bloquesMinimos) - k.bloquesMinimos
                                ),
                              })
                            }
                          />
                        )}
                      </Flex>
                    </TableCell>

                    <TableCell>
                      {k.cajasKit <= 0 ? (
                        /* Cubierto: no hay nada que solicitar. Se dice, en vez de
                           dejar un check vacío que invita a pulsarlo. */
                        <Text variant="microcopy">no hace falta</Text>
                      ) : soloLectura ? (
                        <Text>{s.solicitado ? "Sí" : "No"}</Text>
                      ) : (
                        <Checkbox
                          name={"solicitar-kit-" + k.articuloId}
                          checked={s.solicitado}
                          onChange={(checked) =>
                            cambiar(k.articuloId, { solicitado: !!checked })
                          }
                        >
                          {" "}
                        </Checkbox>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {noSolicitados.length > 0 && (
            <Alert title="Esta oferta no pide pedido KIT" variant="info">
              La cotización se puede publicar igual. Sólo tenlo en cuenta: si nadie
              pide el KIT, la producción no cae en bloques exactos y habrá que
              cuadrarlo por otro lado —subiendo las cajas, consumiendo KIT ya
              fabricados, o pidiendo el KIT desde otra de las ofertas que comparten
              esta referencia.
            </Alert>
          )}
        </>
      )}

      {MOSTRAR_ARTICULOS_SIN_DATO && sinDato.length > 0 && (
        <Alert title="Artículos sin tamaño de bloque definido" variant="info">
          <Flex direction="column" gap="extra-small">
            <Text>
              No se puede comprobar si generan pedido KIT. Si la fábrica los produce en
              bloques, hay que informar su tamaño de kit en el catálogo.
            </Text>
            {sinDato.map((a) => (
              <Text key={a.articuloId} variant="microcopy">
                {a.nombre}
                {a.sku ? ` (${a.sku})` : ""} — {fmt(a.cajas)} cajas
              </Text>
            ))}
          </Flex>
        </Alert>
      )}
    </Flex>
  );
};

export default AlertaKits;
