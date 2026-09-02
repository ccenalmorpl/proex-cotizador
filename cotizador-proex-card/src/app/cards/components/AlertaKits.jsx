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
  Flex,
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
const explicacionBloques = (k, bloquesElegidos) => {
  if (!k.bloque || k.bloque <= 0 || k.aProducir <= 0) return "";

  const cociente = (k.aProducir / k.bloque).toFixed(2).replace(".", ",");
  const division = `${fmt(k.aProducir)} ÷ ${fmt(k.bloque)} = ${cociente}`;

  // Una sola línea: cada línea extra multiplica por el número de artículos, y
  // una fila de tres pisos por producto hace la tabla ilegible. Se quita "de
  // 1894" porque ese número ya está en la propia división.
  if (bloquesElegidos > k.bloquesMinimos) {
    return `${division} → ${k.bloquesMinimos} mín, se producen ${bloquesElegidos}`;
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
  soloLectura = false,
}) => {
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
    const extraCajas = bloquesExtra * k.bloque;
    return {
      solicitado: s?.solicitado ?? true,
      bloquesExtra,
      bloques: k.bloquesMinimos + bloquesExtra,
      cajas: k.cajasKit + extraCajas,
      tirada: k.tirada + extraCajas,
      // Stock parado tras esta oferta y cuánto se ahorra frente a no marcar.
      stockFinal: k.stockFinal + extraCajas,
      ahorro: Math.max(0, k.stockFinalSinMarcar - (k.stockFinal + extraCajas)),
    };
  };

  // Opciones de producción: el mínimo que obliga el déficit y tres bloques más.
  // Cada opción enseña las cajas que quedarían, para no tener que echar la cuenta.
  const opcionesBloques = (k) =>
    [0, 1, 2, 3].map((extra) => {
      const bloques = k.bloquesMinimos + extra;
      return {
        value: String(bloques),
        // Etiquetas cortas: la columna es estrecha y el Select las trunca con
        // puntos suspensivos, que es peor que no decir nada.
        label: extra === 0 ? `${bloques} (lo justo)` : `${bloques} (+${extra})`,
      };
    });

  const cambiar = (articuloId, cambios) => {
    if (typeof onChangeSolicitud === "function") onChangeSolicitud(articuloId, cambios);
  };

  const noSolicitados = kits.filter((k) => !solicitudDe(k).solicitado);

  return (
    <Flex direction="column" gap="small">
      {filas.length > 0 && (
        <>
          {kits.length > 0 ? (
            <Alert
              title={
                kits.length === 1
                  ? "1 artículo no completa un bloque de fabricación"
                  : `${kits.length} artículos no completan un bloque de fabricación`
              }
              variant="warning"
            >
              La fábrica produce en bloques enteros: lo que sobre se pide como pedido KIT y
              queda almacenado para ofertas posteriores.
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
                <TableHeader>Cliente pide</TableHeader>
                <TableHeader>KIT disponible</TableHeader>
                <TableHeader>Stock</TableHeader>
                <TableHeader>A fabricar</TableHeader>
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
                        <Text>{fmt(k.cajas)}</Text>
                        {/* De dónde se cubre el pedido: parte de cajas ya hechas
                            y parte de producción nueva. La duda nace al leer este
                            total, así que la respuesta va pegada a él. */}
                        {k.marcadas > 0 && (
                          <Text variant="microcopy">
                            {fmt(k.marcadas)} ya hechas + {fmt(k.aProducir)} nuevas
                          </Text>
                        )}
                      </Flex>
                    </TableCell>

                    {/* Dos orígenes distintos, dos columnas: un KIT es un negocio
                        con número y trazabilidad; el stock libre son cajas sueltas
                        que PROEX lleva en un documento fuera de HubSpot.
                        Descuentan igual, pero saber de dónde sale cada caja era
                        lo que faltaba. */}
                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        {k.kitDisponibles > 0 ? (
                          <Text>
                            {fmt(k.kitMarcadas)} / {fmt(k.kitDisponibles)}
                          </Text>
                        ) : (
                          <Text variant="microcopy">—</Text>
                        )}
                        {/* Lo accionable: consumir poco no evita el KIT —sólo
                            cambia de dónde salen las cajas—; llegar al umbral
                            baja un bloque entero y lo elimina. */}
                        {k.faltanPorMarcar > 0 && (
                          <Text variant="microcopy">
                            {k.umbralAlcanzable
                              ? "con " + fmt(k.faltanPorMarcar) + " más, sin KIT"
                              : "faltan " + fmt(k.faltanPorMarcar + k.marcadas)}
                          </Text>
                        )}
                      </Flex>
                    </TableCell>

                    <TableCell>
                      {soloLectura ? (
                        <Text>{k.stockUsado > 0 ? fmt(k.stockUsado) : "—"}</Text>
                      ) : (
                        /* Campo abierto y sin etiqueta: la cabecera ya la pone.
                           Sólo se pregunta cuántas cajas consume esta oferta,
                           porque las existencias no están en HubSpot. */
                        /* Envuelto en Stack width="auto": NumberInput no expone
                           ancho y por defecto ocupa toda la celda, lo que ensancha
                           la columna de Stock mas de lo que necesita un numero de
                           tres o cuatro cifras. */
                        <Stack width="auto">
                        <NumberInput
                          name={"stock-libre-" + k.articuloId}
                          label=""
                          value={k.stockUsado || 0}
                          min={0}
                          max={k.cajas}
                          onChange={(v) =>
                            onChangeStockUsado &&
                            onChangeStockUsado(
                              k.sku,
                              Math.min(Math.max(0, Number(v) || 0), k.cajas)
                            )
                          }
                        />
                        </Stack>
                      )}
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontWeight: "bold" }}>{fmt(s.tirada)}</Text>
                        <Text variant="microcopy">
                          {explicacionBloques(k, s.bloques)}
                        </Text>
                      </Flex>
                    </TableCell>

                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Flex direction="row" align="baseline" gap="small">
                          <Text format={{ fontWeight: "bold" }}>{fmt(s.cajas)}</Text>
                          <Text variant="microcopy">
                            ({fmt(s.tirada)} − {fmt(k.aProducir)})
                          </Text>
                        </Flex>
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
                        {/* El total parado en fábrica sólo se enseña cuando NO
                            coincide con el KIT nuevo, y entonces se enseña la
                            suma: si no, salen dos cifras sin decir por qué. */}
                        {k.marcadas > 0 && s.stockFinal !== s.cajas && (
                          <Text variant="microcopy">
                            + {fmt(s.stockFinal - s.cajas)} sin marcar ={" "}
                            {fmt(s.stockFinal)} en fábrica
                          </Text>
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
            <Alert title="Hay KIT sin solicitar" variant="error">
              Sin el pedido KIT no se completa el bloque y la fábrica no aceptará el
              pedido. Habrá que subir las cajas de la oferta o consumir KIT existentes.
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
