// MOCKUP — elegir qué negocios abiertos consumirán el KIT que se va a crear.
//
// Reunión del 2026-09-03: se anotan otras ofertas abiertas de la misma referencia
// y su demanda pendiente entra en la producción. La idea es consolidar: en vez de
// dejar cajas paradas y que otra oferta arranque su propia tirada dentro de un mes,
// se fabrica todo junto.
//
// Modal y no desplegable porque elegir necesita contexto: la etapa del negocio y
// las cajas pendientes deciden si merece la pena comprometer producción, y eso no
// cabe en una etiqueta truncada.
//
// La previsualización de abajo es la razón de ser de esta pantalla: enseña si la
// selección ahorra sobrante o si obliga a un bloque más. Sin ella, anotar negocios
// sería a ciegas.

import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Flex,
  ModalBody,
  ModalFooter,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Text,
} from "@hubspot/ui-extensions";

const fmt = (n) => Number(n || 0).toLocaleString("es-ES");

const ElegirNegociosKitModal = ({
  articulo,
  // Esta oferta genera el KIT, así que va siempre dentro y no se puede quitar:
  // "es el negocio actual por defecto seleccionado y que no se pueda eliminar"
  // (reunión 2026-09-03, 12:29). Por eso es una fila fija y no un checkbox.
  negocios = [],
  seleccionados = [],
  onConfirmar,
  onCerrar,
}) => {
  const [marcados, setMarcados] = useState(() => new Set(seleccionados));

  const alternar = (id) => {
    setMarcados((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  };

  const elegidos = negocios.filter((n) => marcados.has(n.id));
  const demanda = elegidos.reduce((t, n) => t + (Number(n.cajas) || 0), 0);

  // Mismo cálculo que la tabla, para que la previsualización no mienta: la
  // producción sube en bloques enteros, así que sumar demanda puede no cambiar
  // nada o puede obligar a una tirada más.
  const bloque = Number(articulo?.bloque) || 0;
  // Se parte de las cajas totales, NO de `aProducir`: eso lleva descontadas las
  // cajas ya marcadas en KIT abiertos, y generar y consumir no se cruzan
  // ("los kits que genero y los kits que consumo no interactúan entre ellos
  // nunca", reunión del 2026-09-03). Si aquí se usara otra base, la
  // previsualización diría un número y la tabla otro.
  const cuentas = (extra) => {
    const aProducir = Number(articulo?.cajas || 0) + extra;
    if (bloque <= 0 || aProducir <= 0) {
      return { aProducir, bloques: 0, tirada: 0, sobra: 0 };
    }
    const bloques = Math.ceil(aProducir / bloque);
    const tirada = bloques * bloque;
    return { aProducir, bloques, tirada, sobra: tirada - aProducir };
  };

  const ahora = cuentas(0);
  const despues = cuentas(demanda);
  const mejora = despues.sobra < ahora.sobra;
  const empeora = despues.sobra > ahora.sobra;

  return (
    <>
      <ModalBody>
        <Flex direction="column" gap="medium">
          <Flex direction="column" gap="extra-small">
            <Text format={{ fontWeight: "bold" }}>{articulo?.nombre}</Text>
            <Text variant="microcopy">
              Ofertas abiertas de esta referencia. Las que marques entran en la
              producción de este KIT, así que sus cajas se fabrican ahora en vez de
              en una tirada aparte.
            </Text>
          </Flex>

          {negocios.length === 0 ? (
            <Alert title="No hay otras ofertas abiertas" variant="info">
              Ningún negocio abierto del pipeline de cotización lleva esta
              referencia, así que este KIT sólo cubre lo que sobra de esta oferta.
            </Alert>
          ) : (
            <Table bordered={true}>
              <TableHead>
                <TableRow>
                  <TableHeader>Añadir</TableHeader>
                  <TableHeader>Negocio</TableHeader>
                  <TableHeader>Etapa</TableHeader>
                  <TableHeader>Cajas pendientes</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow>
                  <TableCell>
                    <Text variant="microcopy">Siempre</Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>Esta oferta</Text>
                  </TableCell>
                  <TableCell>
                    <Text variant="microcopy">Genera el KIT</Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>
                      {fmt(articulo?.cajas)}
                    </Text>
                  </TableCell>
                </TableRow>
                {negocios.map((n) => (
                  <TableRow key={n.id}>
                    <TableCell>
                      <Checkbox
                        name={`negocio-${n.id}`}
                        checked={marcados.has(n.id)}
                        onChange={() => alternar(n.id)}
                      >
                        {" "}
                      </Checkbox>
                    </TableCell>
                    <TableCell>
                      <Text>{n.nombre}</Text>
                    </TableCell>
                    <TableCell>
                      <Text variant="microcopy">{n.etapa || "—"}</Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>{fmt(n.cajas)}</Text>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {/* El efecto antes de confirmar. Es lo que evita anotar a ciegas: con
              1.200 cajas más el sobrante SUBE, porque hace falta otro bloque. */}
          <Box>
            <Flex direction="column" gap="extra-small">
              <Text format={{ fontWeight: "bold" }}>
                {`${elegidos.length + 1} ${
                  elegidos.length === 0 ? "negocio" : "negocios"
                } · ${fmt(Number(articulo?.cajas || 0) + demanda)} cajas`}
              </Text>
              <Text variant="microcopy">
                Cajas totales {fmt(ahora.aProducir)} → {fmt(despues.aProducir)} ·{" "}
                {despues.bloques} {despues.bloques === 1 ? "bloque" : "bloques"} de{" "}
                {fmt(bloque)} = {fmt(despues.tirada)}
              </Text>
              <Text
                format={{ fontWeight: "bold" }}
              >{`Pedido KIT: ${fmt(ahora.sobra)} → ${fmt(despues.sobra)}`}</Text>
            </Flex>
          </Box>

          {mejora && (
            <Alert title="Se aprovecha la misma producción" variant="success">
              Quedan {fmt(ahora.sobra - despues.sobra)} cajas menos paradas en
              fábrica, sin cambiar el número de tiradas.
            </Alert>
          )}
          {empeora && (
            <Alert title="Hace falta un bloque más" variant="warning">
              Con esta selección la fábrica arranca{" "}
              {despues.bloques - ahora.bloques} bloque(s) adicional(es) y el
              sobrante sube {fmt(despues.sobra - ahora.sobra)} cajas. Quizá conviene
              dejar fuera la oferta más grande.
            </Alert>
          )}
        </Flex>
      </ModalBody>

      <ModalFooter>
        <Button variant="secondary" onClick={onCerrar}>
          Cancelar
        </Button>
        <Button variant="primary" onClick={() => onConfirmar([...marcados])}>
          {marcados.size === 0
            ? "Solo esta oferta"
            : `Añadir ${marcados.size} ${marcados.size === 1 ? "negocio" : "negocios"}`}
        </Button>
      </ModalFooter>
    </>
  );
};

export default ElegirNegociosKitModal;
