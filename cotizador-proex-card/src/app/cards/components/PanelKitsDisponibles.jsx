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
  Checkbox,
  Alert,
  NumberInput,
} from "@hubspot/ui-extensions";

import { KITS_DISPONIBLES_STUB } from "../constants/kitsDisponibles.stub";
import { marcaEfectivaDeKit, marcasDeOtros } from "../utils/kits.utils";
import { guardarMarcasKit } from "../utils/api.utils";

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
  // Cotización abierta. Sin esto no se puede distinguir la marca propia de la de
  // otra oferta, y al volver no se vería lo ya marcado.
  negocioId = "",
  // Se llama tras guardar, para que la lista se relea de HubSpot.
  onGuardado,
}) => {
  const [marcadosLocal, setMarcadosLocal] = useState({});
  const marcados = kitsMarcados ?? marcadosLocal;

  // null = nada intentado todavía
  const [guardando, setGuardando] = useState(false);
  const [errorGuardado, setErrorGuardado] = useState(null);
  const [guardadoOk, setGuardadoOk] = useState(false);

  const cambiar = (kitId, cambios) => {
    if (typeof onCambiarMarcaKit === "function") return onCambiarMarcaKit(kitId, cambios);
    setMarcadosLocal((prev) => ({ ...prev, [kitId]: { ...prev[kitId], ...cambios } }));
  };

  // Marcar no reserva el máximo: sin cantidad explícita se asume todo lo
  // disponible, pero el input permite bajarlo — "puede ser que se termine el kit
  // en la totalidad o que se carguen 10 cajas". Nunca por encima del disponible.
  //
  // Si no hay edición local, cae a la marca que esta cotización ya tenga guardada
  // en HubSpot: así al volver sigue apareciendo marcada.
  const marcaDe = (k) => marcaEfectivaDeKit(k, marcados, negocioId);

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

  // Marcas listas para el backend, calculadas con la marca EFECTIVA: si el
  // comercial vuelve y no toca nada, sus marcas ya guardadas se reenvían tal
  // cual. Con sólo el estado local se mandaría una lista vacía y se borrarían.
  const marcasParaGuardar = () =>
    disponibles
      .map((k) => {
        const m = marcaDe(k);
        return m.marcado && m.cajas > 0
          ? { kit_id: String(k.id), cajas: m.cajas }
          : null;
      })
      .filter(Boolean);

  // Cuántos KIT tienen una edición sin guardar. Va en la etiqueta del botón para
  // que olvidarse de pulsarlo cueste: el riesgo de un guardado explícito es
  // precisamente que nadie lo pulse.
  const pendientes = disponibles.filter((k) => {
    const local = (marcados || {})[k.id];
    if (!local) return false;
    const guardada = (k.marcas || []).find(
      (m) => String(m?.negocioId || "") === String(negocioId || "")
    );
    const m = marcaDe(k);
    if (!m.marcado) return !!guardada;               // desmarcado algo guardado
    return !guardada || Number(guardada.cajas) !== m.cajas;
  }).length;

  const guardar = async () => {
    setGuardando(true);
    setErrorGuardado(null);
    setGuardadoOk(false);
    try {
      const res = await guardarMarcasKit(negocioId, marcasParaGuardar());
      if (!res) throw new Error("El servidor no confirmó el guardado");
      setGuardadoOk(true);
      if (typeof onGuardado === "function") onGuardado();
    } catch (e) {
      setErrorGuardado(e?.message || "No se pudieron guardar las cajas a consumir");
    } finally {
      setGuardando(false);
    }
  };

  return (
    <Flex direction="column" gap="small">
      <Text format={{ fontWeight: "bold" }}>KIT en curso de estos artículos</Text>
      <Text variant="microcopy">
        Cajas ya fabricadas que esta oferta puede consumir en vez de producir nuevas.
      </Text>
      <Text variant="microcopy" format={{ fontWeight: "bold" }}>
        Solicitar no reserva: se las lleva el primer pedido que pague.
      </Text>

      <Table bordered={true}>
        <TableHead>
          <TableRow>
            <TableHeader>Nº de KIT</TableHeader>
            <TableHeader>Artículo</TableHeader>
            <TableHeader>Cajas disponibles</TableHeader>
            <TableHeader>Cajas a consumir</TableHeader>
            <TableHeader>Estado</TableHeader>
            <TableHeader>Solicitado por</TableHeader>
            <TableHeader>Solicitar</TableHeader>
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
                  {marcasDeOtros(k, negocioId).length === 0 && !estaMarcado ? (
                    <Text variant="microcopy">Sin solicitudes</Text>
                  ) : (
                    <Flex direction="column" gap="extra-small">
                      {/* Sólo las de OTRAS ofertas: la propia se enseña abajo como
                          "Esta oferta", y repetirla confundiría. */}
                      {marcasDeOtros(k, negocioId).map((m, i) => (
                        <Text key={i} variant="microcopy">
                          {/* Si el negocio no se pudo resolver —borrado, o un id
                              que no es un negocio— se enseña el id en crudo. Peor
                              es un separador colgando sin nada delante. */}
                          {[
                            m.negocio || `Negocio ${m.negocioId}`,
                            m.propietario,
                            `${fmt(m.cajas)} cajas`,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
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
                  {/* Check y no botón, para que las dos tablas se manejen igual:
                      las dos se solicitan con un check. Un botón que
                      cambia de texto obliga a leerlo para saber el estado. */}
                  <Checkbox
                    name={"marcar-kit-" + k.id}
                    checked={estaMarcado}
                    onChange={(checked) => cambiar(k.id, { marcado: !!checked })}
                  >
                    {" "}
                  </Checkbox>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {/* El guardado va aquí y no en la cabecera: es donde acaba la vista después
          de marcar, así que es donde se espera la acción. Y el resultado puede ir
          al lado en vez de flotando arriba.

          Explícito a propósito: guardar en cada clic llenaría HubSpot de estados
          intermedios que nadie ha confirmado. El contador de pendientes está para
          que olvidarse de pulsarlo cueste. */}
      {!soloInformativo && (
        <Flex direction="row" align="center" gap="small">
          <Button
            variant={pendientes > 0 ? "primary" : "secondary"}
            disabled={guardando || pendientes === 0 || !negocioId}
            onClick={guardar}
          >
            {/* "Cajas a consumir" es el nombre de la columna que el comercial
                tiene delante: así queda claro que guarda eso y no el stock libre
                ni el check de Solicitar, que están en la tabla de arriba.
                Sin pendientes basta con "Guardado": ahí sólo confirma. */}
            {guardando
              ? "Guardando..."
              : pendientes > 0
              ? `Guardar cajas a consumir (${pendientes})`
              : "Guardado"}
          </Button>

          {pendientes > 0 && !guardando && (
            <Text variant="microcopy">
              {pendientes === 1
                ? "1 KIT con cambios sin guardar"
                : `${pendientes} KIT con cambios sin guardar`}
            </Text>
          )}
          {guardadoOk && pendientes === 0 && !errorGuardado && (
            <Text variant="microcopy">Guardado en HubSpot.</Text>
          )}
        </Flex>
      )}

      {errorGuardado && (
        <Alert title="No se pudieron guardar las cajas a consumir" variant="error">
          {errorGuardado}. Vuelve a intentarlo; los contenedores no se han visto
          afectados.
        </Alert>
      )}
    </Flex>
  );
};

export default PanelKitsDisponibles;
