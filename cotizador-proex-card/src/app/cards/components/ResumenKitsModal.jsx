// MOCKUP — resumen de pedidos KIT antes de publicar la cotización.
//
// Se abre desde la pestaña Vista previa. Muestra las dos caras del KIT en esta
// oferta: los que se van a GENERAR (por no completar un bloque de fabricación) y
// los que la oferta ha MARCADO para consumir.
//
// De momento cuelga de un botón propio, sin tocar "Publicar Cotización": ese
// botón dispara la creación real de la Quote y sus line items, y no conviene
// meterle un paso intermedio con datos ficticios. Cuando el flujo sea real, se
// pasa el `overlay` al botón de publicar y el confirmar llama a `handlePublicar`.
//
// Los KIT marcados vienen del stub. En real saldrían de `kit_marcado_por_ids`.

import React from "react";
import {
  Flex,
  Text,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TableHeader,
  Alert,
  Divider,
  Button,
  ButtonRow,
  ModalBody,
  ModalFooter,
} from "@hubspot/ui-extensions";

const fmt = (n) => Number(n || 0).toLocaleString("es-ES");

const ResumenKitsModal = ({ kitsAGenerar = [], kitsMarcados = [], onClose }) => {
  const sinNada = kitsAGenerar.length === 0 && kitsMarcados.length === 0;

  return (
    <>
      <ModalBody>
        <Flex direction="column" gap="medium">
      {sinNada && (
        <Alert title="Sin pedidos KIT" variant="info">
          Esta oferta no genera ningún pedido KIT ni consume KIT existentes. Todos los
          artículos completan bloque o no tienen tamaño de bloque definido.
        </Alert>
      )}

      {kitsAGenerar.length > 0 && (
        <Flex direction="column" gap="small">
          <Text format={{ fontWeight: "bold" }}>
            {kitsAGenerar.length === 1
              ? "Se generará 1 pedido KIT"
              : `Se generarán ${kitsAGenerar.length} pedidos KIT`}
          </Text>
          <Text variant="microcopy">
            Al pasar la cotización a ganada. Requieren validación de Administración antes
            de poder mandar el pedido a fábrica.
          </Text>
          <Table bordered={true}>
            <TableHead>
              <TableRow>
                <TableHeader>Artículo</TableHeader>
                <TableHeader>Cajas pedidas</TableHeader>
                <TableHeader>Bloque</TableHeader>
                <TableHeader>Cajas KIT</TableHeader>
                <TableHeader>Producción total</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {kitsAGenerar.map((k) => (
                <TableRow key={k.articuloId}>
                  <TableCell>
                    <Text>{k.nombre}</Text>
                  </TableCell>
                  <TableCell>{fmt(k.cajas)}</TableCell>
                  <TableCell>
                    {fmt(k.tamanoKit)}
                    {k.bloqueMultiplo > 1 ? ` × ${k.bloqueMultiplo}` : ""}
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>{fmt(k.cajasKit)}</Text>
                  </TableCell>
                  <TableCell>{fmt(k.cajasProduccion)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Flex>
      )}

      {kitsAGenerar.length > 0 && kitsMarcados.length > 0 && <Divider />}

      {kitsMarcados.length > 0 && (
        <Flex direction="column" gap="small">
          <Text format={{ fontWeight: "bold" }}>
            KIT marcados para consumir ({kitsMarcados.length})
          </Text>
          <Text variant="microcopy">
            Marcar no reserva: el KIT lo consume el primer pedido que paga, y el consumo
            lo valida Administración en el asistente de pedidos.
          </Text>
          <Table bordered={true}>
            <TableHead>
              <TableRow>
                <TableHeader>Nº de KIT</TableHeader>
                <TableHeader>Artículo</TableHeader>
                <TableHeader>Cajas a consumir</TableHeader>
                <TableHeader>Disponibles</TableHeader>
                <TableHeader>Otras ofertas que lo han marcado</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {kitsMarcados.map((k) => (
                <TableRow key={k.id}>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>{k.numeroDeKit}</Text>
                  </TableCell>
                  <TableCell>
                    <Text>{k.productoNombre}</Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>
                      {fmt(k.cajasAConsumir ?? k.cajasDisponibles)}
                    </Text>
                  </TableCell>
                  <TableCell>
                    {fmt(k.cajasDisponibles)} / {fmt(k.cajasKit)}
                  </TableCell>
                  <TableCell>
                    {(k.marcas || []).length === 0 ? (
                      <Text variant="microcopy">Ninguna</Text>
                    ) : (
                      <Flex direction="column" gap="extra-small">
                        {k.marcas.map((m, i) => (
                          <Text key={i} variant="microcopy">
                            {m.negocio} · {m.propietario} · {m.fecha}
                          </Text>
                        ))}
                      </Flex>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {kitsMarcados.some((k) => (k.marcas || []).length > 0) && (
            <Alert title="Hay KIT marcados por otras ofertas" variant="warning">
              Si otra cotización paga antes, consumirá esas cajas y esta oferta perderá la
              marca. En ese caso se generaría producción nueva.
            </Alert>
          )}
          </Flex>
        )}
        </Flex>
      </ModalBody>

      <ModalFooter>
        <ButtonRow>
          <Button variant="secondary" onClick={onClose}>
            Cerrar
          </Button>
        </ButtonRow>
      </ModalFooter>
    </>
  );
};

export default ResumenKitsModal;
