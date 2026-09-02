// MOCKUP — panel de KIT en curso disponibles para consumir.
//
// Ubicación según la reunión: en la misma pantalla de contenedores, debajo del
// resumen —"me aparecerán aquí, por ejemplo, aquí debajo, en una tabla de pedidos
// kit disponibles o posibles pedidos kit a consumir o panel de kits en curso"—.
//
// Filtro: KIT en etapa Abierto Y del artículo que hay en la cotización
// ("tendré que filtrar por pedido kit abierto... pero también por artículo").
//
// Marcar es OPCIONAL y no reserva nada: no marcar significa que la oferta va a
// producción nueva. El consumo real se valida después, en el asistente de pedidos.

import React, { useState } from "react";
import {
  Flex,
  Text,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TableHeader,
  Tag,
  Button,
  Alert,
  NumberInput,
} from "@hubspot/ui-extensions";

import { KITS_DISPONIBLES_STUB } from "../constants/kitsDisponibles.stub";

const fmt = (n) => Number(n || 0).toLocaleString("es-ES");

const PanelKitsDisponibles = ({
  skusEnCotizacion = [],
  kits = KITS_DISPONIBLES_STUB,
  // Variante para el modal de edición per-contenedor: solo informa, sin acción.
  // Marcar vincula el KIT a la COTIZACIÓN, no a un contenedor, así que el botón
  // no puede vivir dentro de un modal que edita un contenedor concreto.
  soloInformativo = false,
  // Marcas de ESTA cotización, gobernadas por Cotizador.tsx para que marcar
  // desde Contenedores o desde el modal de Negociación sea lo mismo y el resumen
  // de Vista previa lo vea. Si no se pasan, cae a estado local (uso aislado).
  kitsMarcados,
  onCambiarMarcaKit,
}) => {
  const [marcadosLocal, setMarcadosLocal] = useState({});
  const marcados = kitsMarcados ?? marcadosLocal;

  const cambiar = (kitId, cambios) => {
    if (typeof onCambiarMarcaKit === "function") return onCambiarMarcaKit(kitId, cambios);
    setMarcadosLocal((prev) => ({ ...prev, [kitId]: { ...prev[kitId], ...cambios } }));
  };

  // Marcar no reserva el máximo: sin cantidad explícita se asume todo lo
  // disponible, pero el input permite bajarlo — "puede ser que se termine el kit
  // en la totalidad o que se carguen 10 cajas". Nunca por encima del disponible.
  const marcaDe = (k) => {
    const m = marcados[k.id] || {};
    const cajas = m.cajas ?? k.cajasDisponibles;
    return {
      marcado: !!m.marcado,
      cajas: Math.min(Number(cajas) || 0, Number(k.cajasDisponibles) || 0),
    };
  };

  const skus = new Set(
    (skusEnCotizacion || []).map((s) => (s || "").trim()).filter(Boolean)
  );
  const disponibles = kits.filter((k) => skus.has((k.productoSku || "").trim()));

  // Sin artículos no hay nada que decir: el bloque entero sobra.
  if (skus.size === 0) return null;

  // Con artículos pero sin KIT sí hay que decirlo. Desaparecer sin más es
  // ambiguo: el comercial no sabe si no hay stock o si la sección no ha cargado,
  // y de eso depende que dé por buena la cifra de producción de arriba.
  if (disponibles.length === 0) {
    if (soloInformativo) return null;
    return (
      <Flex direction="column" gap="small">
        <Text format={{ fontWeight: "bold" }}>KIT en curso de estos artículos</Text>
        <Alert title="No hay KIT disponibles" variant="info">
          Ninguno de los artículos de esta cotización tiene cajas ya fabricadas
          que consumir, así que todo lo que se pida sale de producción nueva.
        </Alert>
      </Flex>
    );
  }

  if (soloInformativo) {
    const totalCajas = disponibles.reduce(
      (sum, k) => sum + Number(k.cajasDisponibles || 0),
      0
    );
    return (
      <Alert title="Hay KIT disponibles de estos artículos" variant="info">
        {disponibles.length === 1
          ? `1 KIT abierto con ${fmt(totalCajas)} cajas disponibles`
          : `${disponibles.length} KIT abiertos con ${fmt(totalCajas)} cajas disponibles en total`}
        . Puedes marcarlos en la pestaña Contenedores para que esta oferta los consuma.
      </Alert>
    );
  }

  return (
    <Flex direction="column" gap="small">
      <Text format={{ fontWeight: "bold" }}>KIT en curso de estos artículos</Text>
      <Text variant="microcopy">
        Cajas ya fabricadas que esta oferta puede consumir en vez de producir nuevas.
      </Text>
      <Text variant="microcopy" format={{ fontWeight: "bold" }}>
        Marcar no reserva: se las lleva el primer pedido que pague.
      </Text>

      <Table bordered={true}>
        <TableHead>
          <TableRow>
            <TableHeader>Nº de KIT</TableHeader>
            <TableHeader>Artículo</TableHeader>
            <TableHeader>Cajas disponibles</TableHeader>
            <TableHeader>Cajas a consumir</TableHeader>
            <TableHeader>Estado</TableHeader>
            <TableHeader>Marcado por</TableHeader>
            <TableHeader>Acciones</TableHeader>
          </TableRow>
        </TableHead>
        <TableBody>
          {disponibles.map((k) => {
            const { marcado: estaMarcado, cajas: cajasAConsumir } = marcaDe(k);
            return (
              <TableRow key={k.id}>
                <TableCell>
                  <Text format={{ fontWeight: "bold" }}>{k.numeroDeKit}</Text>
                </TableCell>
                <TableCell>
                  <Text>{k.productoNombre}</Text>
                </TableCell>
                <TableCell>
                  <Text format={{ fontWeight: "bold" }}>
                    {fmt(k.cajasDisponibles)} / {fmt(k.cajasKit)}
                  </Text>
                </TableCell>
                <TableCell>
                  {/* Editable siempre: primero se pone la cantidad y luego se
                      confirma con Marcar, no al revés. Escribir sin marcar no
                      afecta al cálculo — solo deja preparada la cifra. */}
                  <NumberInput
                    name={`cajas-consumir-${k.id}`}
                    label=""
                    value={cajasAConsumir}
                    min={0}
                    max={k.cajasDisponibles}
                    onChange={(v) =>
                      cambiar(k.id, {
                        cajas: Math.min(Number(v) || 0, k.cajasDisponibles),
                      })
                    }
                  />
                </TableCell>
                <TableCell>
                  <Tag variant="success">Abierto</Tag>
                </TableCell>
                <TableCell>
                  {(k.marcas || []).length === 0 && !estaMarcado ? (
                    <Text variant="microcopy">Sin marcas</Text>
                  ) : (
                    <Flex direction="column" gap="extra-small">
                      {(k.marcas || []).map((m, i) => (
                        <Text key={i} variant="microcopy">
                          {m.negocio} · {m.propietario} · {m.fecha}
                        </Text>
                      ))}
                      {estaMarcado && (
                        <Text variant="microcopy">
                          <Text format={{ fontWeight: "bold" }} inline>
                            Esta oferta
                          </Text>
                          {` · ${fmt(cajasAConsumir)} cajas`}
                        </Text>
                      )}
                    </Flex>
                  )}
                </TableCell>
                <TableCell>
                  <Button
                    variant={estaMarcado ? "primary" : "secondary"}
                    size="extra-small"
                    onClick={() => cambiar(k.id, { marcado: !estaMarcado })}
                  >
                    {estaMarcado ? "Marcado" : "Marcar"}
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Flex>
  );
};

export default PanelKitsDisponibles;
