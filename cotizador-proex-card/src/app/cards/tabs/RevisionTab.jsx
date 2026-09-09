import { useState, useEffect, useMemo, useRef } from "react";
import {
  Box,
  Heading,
  Flex,
  Text,
  Divider,
  Button,
  ButtonRow,
  Alert,
  Statistics,
  StatisticsItem,
  StatisticsTrend,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  Tile,
  Select,
  StepIndicator,
  LoadingSpinner,
  Modal,
  hubspot,
} from "@hubspot/ui-extensions";
import {
  LOADING_MESSAGES,
  INITIAL_RENDER_DELAY_MS,
} from "../constants/common.constants";
import { crearKitsDeCotizacion, fetchFromAPI } from "../utils/api.utils";
import { API_CONFIG } from "../config";
import {
  calcularPrecioPorCajaPorIncoterm,
  calcularTotalesPorTipoVenta,
  buildPricingParamsPorContenedor,
  normalizeIncoterm,
  bucketIncoterm,
  esModoDobleFlete,
} from "../utils/pricing.utils";
import { getPorcentajesIVA } from "../utils/iva.utils";
// Pedidos KIT — aislado: solo alimenta el modal de resumen previo a publicar
import { analizarKits } from "../utils/kits.utils";
import { resumenKitsPorSku } from "../components/AlertaKits";
import { useKitsDisponibles } from "../hooks/useKitsDisponibles";
import { conParamsKit, marcaEfectivaDeKit } from "../utils/kits.utils";
import { useKitParams } from "../hooks/useKitParams";
import { useNegociosGeneradores } from "../hooks/useNegociosGeneradores";
import ResumenKitsModal from "../components/ResumenKitsModal";

// Resuelve el label legible de un campo: acepta string o {value, options}
const getSelectLabel = (field) => {
  if (!field) return "";

  if (typeof field === "object" && field.value !== undefined && field.options) {
    const option = field.options.find((opt) => opt.value === field.value);
    return option?.label || field.value || "";
  }

  if (typeof field === "string") {
    return field;
  }

  if (typeof field === "object") {
    return field.label || field.value || field.name || "";
  }

  return String(field);
};

const RevisionField = ({ label, value, required = false }) => {
  const displayValue = value || "No especificado";
  const hasError = required && !value;

  return (
    <Flex direction="column" gap="extra-small">
      <Text format={{ fontWeight: "demibold" }}>{label}</Text>
      {hasError ? (
        <Alert title="Campo requerido" variant="warning">
          Es necesario especificar este valor.
        </Alert>
      ) : (
        <Text>{displayValue}</Text>
      )}
    </Flex>
  );
};

const PublishingStatusPanel = ({
  publishStatus,
  publishData,
  customObjectsStatus,
  customObjectsData,
  publicacionError,
  stalled,
}) => {
  let overall = "idle";
  if (publishStatus === "error") overall = "error_publish";
  else if (customObjectsStatus === "error") overall = "error_custom";
  else if (publishStatus === "completed" && customObjectsStatus === "completed") overall = "done";
  else if (
    publishStatus === "completed" &&
    (customObjectsStatus === "pending" || customObjectsStatus === "processing")
  ) overall = "customizing";
  else if (publishStatus === "completed" && !customObjectsStatus) overall = "publish_done_waiting_co";
  else if (publishStatus === "pending" || publishStatus === "processing") overall = "publishing";

  if (overall === "idle") return null;

  const headers = {
    publishing: { title: "Publicando cotización...", variant: "info" },
    publish_done_waiting_co: { title: "Preparando objetos...", variant: "info" },
    customizing: { title: "Sincronizando objetos...", variant: "info" },
    done: { title: "¡Cotización publicada!", variant: "success" },
    error_publish: { title: "Error al publicar la cotización", variant: "danger" },
    error_custom: { title: "Falló la sincronización", variant: "warning" },
  };
  const { title, variant } = headers[overall];

  const publishStepState =
    publishStatus === "completed" ? "completed"
    : publishStatus === "error" ? "error"
    : publishStatus === "processing" || (publishStatus === "pending" && publishData.created > 0) ? "processing"
    : "pending";

  const customStepState =
    customObjectsStatus === "completed" ? "completed"
    : customObjectsStatus === "error" ? "error"
    : customObjectsStatus === "pending" || customObjectsStatus === "processing" ? "processing"
    : "pending";

  const stepIndex =
    customStepState === "completed" ? 2
    : publishStepState === "completed" ? 1
    : 0;

  const renderStep = (label, state, processingText, completedText, errorText) => {
    const icons = {
      pending: <Text>⚪</Text>,
      processing: <LoadingSpinner size="xs" />,
      completed: <Text>✅</Text>,
      error: <Text>❌</Text>,
    };
    const texts = {
      pending: `${label}: en espera`,
      processing: `${label}: ${processingText}`,
      completed: `${label}: ${completedText}`,
      error: `${label}: ${errorText}`,
    };
    return (
      <Flex direction="row" gap="small" align="center">
        {icons[state]}
        <Text>{texts[state]}</Text>
      </Flex>
    );
  };

  const publishProcessingText = publishData.total > 0
    ? `creando ${publishData.created} de ${publishData.total} líneas`
    : "preparando líneas de cotización";
  const publishCompletedText = publishData.total > 0
    ? `${publishData.associated} de ${publishData.total} líneas asociadas a la cotización`
    : "cotización creada en HubSpot";
  const publishErrorText = publicacionError || "fallo desconocido al publicar";

  const customProcessingText = customObjectsData.contenedores > 0 || customObjectsData.elementos > 0
    ? `${customObjectsData.contenedores} contenedores · ${customObjectsData.elementos} líneas sincronizados`
    : "creando contenedores y líneas en HubSpot";
  const customCompletedText = `${customObjectsData.contenedores} contenedores · ${customObjectsData.elementos} líneas`;
  const customErrorText = "no se pudo sincronizar — la cotización ya está creada en HubSpot, puedes reintentar";

  const showStalled = stalled && overall !== "done" && overall !== "error_publish" && overall !== "error_custom";

  return (
    <Alert title={title} variant={variant}>
      <Flex direction="column" gap="medium">
        <Flex direction="row">
          <Box flex={"auto"}>
            <StepIndicator
              currentStep={stepIndex}
              stepNames={["Cotización", "Contenedores y líneas"]}
            />
          </Box>
        </Flex>
        <Flex direction="row">
          <Box>
            <Flex direction="column" gap="extra-small">
              {renderStep("Cotización", publishStepState, publishProcessingText, publishCompletedText, publishErrorText)}
              {renderStep("Contenedores y líneas", customStepState, customProcessingText, customCompletedText, customErrorText)}
            </Flex>
          </Box>
        </Flex>
        {showStalled && (
          <Flex direction="row">
            <Box>
              <Text format={{ fontStyle: "italic", color: "medium" }} variant="microcopy">
                El proceso está tardando más de lo habitual. Seguiremos comprobando cada 3 segundos hasta el límite de seguridad.
              </Text>
            </Box>
          </Flex>
        )}
      </Flex>
    </Alert>
  );
};

/** @typedef {import('../types/common.types').ContenedorUnicoDefinido} ContenedorUnicoDefinido */
const RevisionTab = ({
  context,
  actions,
  kitsMarcados,
  kitsSolicitados,
  onNavigate,
  clienteData,
  articulosSeleccionados,
  contenedoresData,
  transitarioData,
  serviciosData,
  documentacionData,
  contenedoresUnicos = [],
  tipoIVAOptions = [],
  ivaData = {},
  preciosEXW = {},
  tiposIVA = {},
  tipoIVASoportadoOptions = [],
  tiposIVASoportado = {},
}) => {
  // Extraer dealId del contexto al inicio
  const dealId = context?.crm?.objectId;

  // Pedidos KIT — resumen previo a publicar. Aislado: solo alimenta el modal.
  // KIT reales de HubSpot, los mismos que ve Contenedores. Con el fixture, esta
  // pantalla daba otro número para el mismo pedido.
  const { kits: kitsDisponibles } = useKitsDisponibles(
    (articulosSeleccionados || [])
      .filter(Boolean)
      .map((a) => (a.hs_sku || "").trim())
  );

  // `tamano_kit` real por SKU, igual que en Contenedores.
  const paramsKit = useKitParams(
    (articulosSeleccionados || []).filter(Boolean).map((a) => (a.hs_sku || "").trim())
  );

  // Las ofertas abiertas de cada referencia, para poder resolver los negocios
  // anotados a sus cajas: la alerta guarda solo los ids, y al crear el KIT hay
  // que mandar tambien lo que aporta cada uno.
  const negociosPorSku = useNegociosGeneradores(
    (articulosSeleccionados || []).filter(Boolean).map((a) => (a.hs_sku || "").trim()),
    dealId ? String(dealId) : ""
  );

  const analisisKits = useMemo(
    () =>
      analizarKits(
        contenedoresData?.contenedoresDefinidos || [],
        contenedoresData?.cantidadesContenedores || {},
        conParamsKit(
          (articulosSeleccionados || []).filter(Boolean).map((a) => ({
            id: a.id,
            nombre: a.nombre,
            sku: a.hs_sku || "",
            tamano_kit: a.tamano_kit,
            bloque_multiplo: a.bloque_multiplo,
          })),
          paramsKit
        ),
        resumenKitsPorSku(kitsDisponibles, kitsMarcados, {}, dealId)
      ),
    [contenedoresData, articulosSeleccionados, kitsMarcados, kitsDisponibles, dealId, paramsKit]
  );

  // Los KIT que esta cotización ha marcado. El estado lo gobierna Cotizador.tsx,
  // así que aquí se ve lo marcado tanto en Contenedores como en el modal de
  // Negociación. MOCKUP: en real se filtraría por `kit_marcado_por_ids`.
  const kitsMarcadosDetalle = useMemo(() => {
    const skus = new Set(
      (articulosSeleccionados || [])
        .filter(Boolean)
        .map((a) => (a.hs_sku || "").trim())
        .filter(Boolean)
    );
    // Marca EFECTIVA: cuenta lo editado en esta sesión y lo que ya estaba guardado
    // en HubSpot para esta cotización. Sin lo segundo, entrando directo a Vista
    // previa no aparecería ningún KIT consumido.
    return kitsDisponibles
      .filter((k) => skus.has((k.productoSku || "").trim()))
      .map((k) => ({ kit: k, marca: marcaEfectivaDeKit(k, kitsMarcados, dealId) }))
      .filter(({ marca }) => marca.marcado && marca.cajas > 0)
      .map(({ kit, marca }) => ({ ...kit, cajasAConsumir: marca.cajas }));
  }, [articulosSeleccionados, kitsMarcados, kitsDisponibles, dealId]);

  // Lo que se va a pedir de verdad: solo los solicitados, con las cajas que el
  // comercial haya ajustado en la alerta. Sin entrada explícita se asume
  // solicitado con las cajas calculadas (mismo criterio que AlertaKits).
  const kitsSolicitadosDetalle = useMemo(
    () =>
      analisisKits.kits
        .filter((k) => kitsSolicitados?.[k.articuloId]?.solicitado ?? true)
        .map((k) => {
          const s = kitsSolicitados?.[k.articuloId];
          // Los bloques extra que haya pedido el comercial sobre el mínimo.
          const extra = Math.max(0, Number(s?.bloquesExtra) || 0);

          // Mismo cálculo que la alerta, y por la misma razón: generar y
          // consumir no se cruzan, así que se parte del total de cajas y no de
          // `aProducir`, que lleva descontado lo marcado en KIT abiertos.
          const disponibles = negociosPorSku[(k.sku || "").trim()] || [];
          const elegidos = disponibles.filter((n) =>
            (s?.negocios || []).includes(n.id)
          );
          const demandaExtra = elegidos.reduce(
            (t, n) => t + (Number(n.cajas) || 0),
            0
          );
          const aProducirTotal = k.cajas + demandaExtra;

          const bloquesMinimos =
            k.bloque > 0 && aProducirTotal > 0
              ? Math.ceil(aProducirTotal / k.bloque)
              : 0;
          const bloques = bloquesMinimos + extra;
          const tirada = bloques * k.bloque;
          const cajasCalculadas = Math.max(0, tirada - aProducirTotal);

          return {
            ...k,
            bloques,
            // La cifra escrita a mano gana al cálculo: el input del pedido KIT
            // es editable a propósito y publicar tiene que crear lo que la
            // usuaria vio, no lo que salía antes de que lo tocara.
            cajasKit: s?.cajasManual ?? cajasCalculadas,
            cajasProduccion: tirada,
            // Ofertas que generan el KIT, con las cajas que aporta cada una a la
            // demanda. Esta oferta va explícita y PRIMERA: el backend la añade
            // sola si falta, pero entonces le pone las cajas del KIT en vez de
            // las suyas, y el campo dice cuánto aportó cada uno, no el total.
            negocios: [
              { id: String(dealId), cajas: Number(k.cajas) || 0 },
              ...elegidos.map((n) => ({
                id: n.id,
                cajas: Number(n.cajas) || 0,
              })),
            ],
          };
        }),
    [analisisKits, kitsSolicitados, negociosPorSku]
  );

  const resumenKitsTotal = kitsSolicitadosDetalle.length + kitsMarcadosDetalle.length;

  // Estado de carga - empieza en true para esperar los datos de otros paneles
  const [isLoadingInitial, setIsLoadingInitial] = useState(true);

  // Estados para el proceso de publicación
  const [publicando, setPublicando] = useState(false);
  const [publicacionError, setPublicacionError] = useState(null);
  // Resultado de crear los KIT al publicar: {creados, omitidos}. null = no se
  // ha intentado todavía.
  const [resultadoKits, setResultadoKits] = useState(null);

  // Statuses canónicos en ambos endpoints: not_started | pending | processing | completed | error
  const POLLING_MAX_ATTEMPTS = 20;
  // El estado del job se persiste en DynamoDB tras el POST, así que `not_started` solo aparece
  // de forma transitoria por la consistencia eventual del GSI deal_id-job_type-index (~1-2s).
  const POLLING_MAX_NOT_STARTED = 2;

  // Estados para el polling de objetos personalizados
  const [customObjectsStatus, setCustomObjectsStatus] = useState(null);
  const [customObjectsData, setCustomObjectsData] = useState({ contenedores: 0, elementos: 0 });
  const pollingTimeoutRef = useRef(null);
  const pollingAttemptsRef = useRef(0);
  const customObjectsNotStartedAttemptsRef = useRef(0);
  // Cada `startPolling()` incrementa este contador. Cada tick/fetch en vuelo guarda el id y
  // sólo continúa si sigue siendo el activo — cualquier cadena huérfana (remonte de componente,
  // doble invocación) se autoinvalida en el siguiente tick.
  const pollingSessionIdRef = useRef(0);

  // Estado de la publicación del Quote (proceso síncrono — no requiere polling)
  const [publishStatus, setPublishStatus] = useState(null);
  const [publishData, setPublishData] = useState({ created: 0, associated: 0, total: 0 });

  // Detección de estancamiento del proceso conjunto (publish + custom-objects)
  const STALLED_TICKS_THRESHOLD = 8;
  const [stalled, setStalled] = useState(false);
  const lastProgressSignatureRef = useRef(null);
  const stalledTicksRef = useRef(0);

  // Invalida cualquier sesión de polling en vuelo. Incrementa el contador para que cadenas
  // huérfanas (remonte del card, doble invocación) se autoinvaliden en su próximo tick.
  const stopPolling = () => {
    pollingSessionIdRef.current += 1;
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
      pollingTimeoutRef.current = null;
    }
  };

  const isCurrentSession = (sessionId) => pollingSessionIdRef.current === sessionId;

  const checkCustomObjectsStatus = async (sessionId) => {
    if (!isCurrentSession(sessionId)) return;

    let response;
    try {
      response = await fetchFromAPI(`/api/v1/deals/${dealId}/custom-objects/status`, {
        method: "GET",
      });
    } catch (error) {
      if (!isCurrentSession(sessionId)) return;
      console.error("❌ Error verificando estado de objetos personalizados:", error);
      setCustomObjectsStatus("error");
      stopPolling();
      return;
    }

    if (!isCurrentSession(sessionId)) return;

    if (!response.success || !response.data) {
      setCustomObjectsStatus("error");
      stopPolling();
      console.error("❌ Respuesta inválida del servidor:", response);
      return;
    }

    const { status, contenedores, elementos, message } = response.data;

    if (status === "completed") {
      setCustomObjectsStatus("completed");
      setCustomObjectsData({
        contenedores: contenedores || 0,
        elementos: elementos || 0,
      });
      stopPolling();
      return;
    }

    if (status === "error") {
      setCustomObjectsStatus("error");
      stopPolling();
      console.error("❌ Error creando objetos personalizados:", message);
      return;
    }

    // `not_started` solo es válido como ventana corta tras el POST (consistencia eventual del GSI).
    // Reintentamos un par de veces; si persiste, asumimos que el job nunca llegó a crearse.
    if (status === "not_started") {
      customObjectsNotStartedAttemptsRef.current += 1;
      if (customObjectsNotStartedAttemptsRef.current >= POLLING_MAX_NOT_STARTED) {
        setCustomObjectsStatus("error");
        stopPolling();
        console.warn("⚠️ El job de objetos personalizados no se inició tras varios intentos");
        return;
      }
      schedulePollingTick(sessionId);
      return;
    }

    customObjectsNotStartedAttemptsRef.current = 0;

    if (status === "pending" || status === "processing") {
      setCustomObjectsStatus(status);
      setCustomObjectsData({
        contenedores: contenedores || 0,
        elementos: elementos || 0,
      });
      pollingAttemptsRef.current += 1;
      if (pollingAttemptsRef.current >= POLLING_MAX_ATTEMPTS) {
        setCustomObjectsStatus("error");
        stopPolling();
        console.warn("⚠️ Timeout: Los objetos personalizados están tardando demasiado");
        return;
      }
      schedulePollingTick(sessionId);
      return;
    }

    console.warn("⚠️ Status no canónico en /custom-objects/status:", status, message);
    setCustomObjectsStatus("error");
    stopPolling();
  };

  const schedulePollingTick = (sessionId) => {
    if (!isCurrentSession(sessionId)) return;
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
    }
    pollingTimeoutRef.current = setTimeout(() => {
      pollingTimeoutRef.current = null;
      if (!isCurrentSession(sessionId)) return;
      checkCustomObjectsStatus(sessionId);
    }, 3000);
  };

  const startPolling = () => {
    stopPolling();
    pollingSessionIdRef.current += 1;
    const sessionId = pollingSessionIdRef.current;
    setCustomObjectsStatus("pending");
    pollingAttemptsRef.current = 0;
    customObjectsNotStartedAttemptsRef.current = 0;
    checkCustomObjectsStatus(sessionId);
  };

  const triggerCustomObjects = async () => {
    try {
      const customObjectsResponse = await fetchFromAPI(`/api/v1/deals/${dealId}/publish-custom-objects`, {
        method: "POST",
      });
      if (customObjectsResponse.success) {
        startPolling();
      } else {
        console.warn("⚠️ Error al iniciar tarea de objetos personalizados:", customObjectsResponse.error);
        setCustomObjectsStatus("error");
      }
    } catch (customObjError) {
      console.error("⚠️ Error al lanzar creación de objetos personalizados:", customObjError);
      setCustomObjectsStatus("error");
    }
  };

  // Cleanup al desmontar el componente
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, []);

  useEffect(() => {
    const active = customObjectsStatus === "pending" || customObjectsStatus === "processing";
    if (!active) {
      stalledTicksRef.current = 0;
      lastProgressSignatureRef.current = null;
      if (stalled) setStalled(false);
      return;
    }
    const sig = `${customObjectsStatus}|${customObjectsData.contenedores}|${customObjectsData.elementos}`;
    if (sig === lastProgressSignatureRef.current) {
      stalledTicksRef.current += 1;
      if (stalledTicksRef.current >= STALLED_TICKS_THRESHOLD && !stalled) {
        setStalled(true);
      }
    } else {
      lastProgressSignatureRef.current = sig;
      stalledTicksRef.current = 0;
      if (stalled) setStalled(false);
    }
  }, [customObjectsStatus, customObjectsData]);

  // Verificar si todos los datos necesarios están disponibles
  useEffect(() => {
    // Verificar que todos los datos críticos existan y no estén vacíos
    const hasClienteData = clienteData && Object.keys(clienteData).length > 0;
    const hasArticulos =
      articulosSeleccionados && articulosSeleccionados.length > 0;
    const hasContenedores = contenedoresUnicos && contenedoresUnicos.length > 0;
    const hasTransitario =
      transitarioData && Object.keys(transitarioData).length > 0;
    const hasServicios = serviciosData && Object.keys(serviciosData).length > 0;
    const hasDocumentacion =
      documentacionData && Object.keys(documentacionData).length > 0;

    // Todos los datos deben estar disponibles
    const allDataReady =
      hasClienteData &&
      hasArticulos &&
      hasContenedores &&
      hasTransitario &&
      hasServicios &&
      hasDocumentacion;

    if (allDataReady && isLoadingInitial) {
      // Añadir un pequeño delay para asegurar que todo esté renderizado
      const timer = setTimeout(() => {
        setIsLoadingInitial(false);
      }, INITIAL_RENDER_DELAY_MS);

      return () => clearTimeout(timer);
    }
  }, [
    clienteData,
    articulosSeleccionados,
    contenedoresUnicos,
    transitarioData,
    serviciosData,
    documentacionData,
    isLoadingInitial,
  ]);

  // Estado para el contenedor seleccionado en el selector
  const [contenedorSeleccionadoId, setContenedorSeleccionadoId] = useState(
    contenedoresUnicos.length > 0 ? contenedoresUnicos[0].line_item_id : null
  );

  // Actualizar el contenedor seleccionado cuando cambien los contenedores únicos
  useEffect(() => {
    if (contenedoresUnicos.length > 0 && !contenedorSeleccionadoId) {
      setContenedorSeleccionadoId(contenedoresUnicos[0].line_item_id);
    }
  }, [contenedoresUnicos, contenedorSeleccionadoId]);

  // Obtener el contenedor seleccionado
  const contenedorSeleccionado = contenedoresUnicos.find(
    (c) => c.line_item_id === contenedorSeleccionadoId
  );

  // Calcular total de cajas para gastos por tramo
  const totalCajas = useMemo(() => {
    return contenedoresUnicos.reduce((total, cont) => {
      const cajas =
        cont.container_config?.articulos?.reduce(
          (sum, art) => sum + (art.numCajas || 0),
          0
        ) || 0;
      return total + cajas;
    }, 0);
  }, [contenedoresUnicos]);

  // Calcular servicios totales
  const totalServicios = useMemo(() => {
    if (
      serviciosData?.serviciosSeleccionados &&
      serviciosData.serviciosSeleccionados.length > 0
    ) {
      return serviciosData.serviciosSeleccionados.reduce(
        (total, servicio) => total + servicio.importe,
        0
      );
    }
    return 0;
  }, [serviciosData]);

  // Coste de servicios por caja (el servicio es por contenedor, se multiplica por nº de contenedores)
  const serviciosPorCaja = useMemo(
    () => (totalCajas > 0 ? (totalServicios * contenedoresUnicos.length) / totalCajas : 0),
    [totalCajas, totalServicios, contenedoresUnicos]
  );

  // Coste del seguro por caja (filtrado de serviciosPorCaja para CFR)
  const seguroPorCaja = useMemo(() => {
    if (!serviciosData?.serviciosSeleccionados || totalCajas === 0) return 0;
    const totalSeguro = serviciosData.serviciosSeleccionados
      .filter((s) => s.tipoArticulo === "seguro")
      .reduce((sum, s) => sum + (Number(s.importe) || 0), 0);
    return (totalSeguro * contenedoresUnicos.length) / totalCajas;
  }, [serviciosData, totalCajas, contenedoresUnicos]);

  // Gastos por tramo por caja (el flete es por contenedor, se multiplica por nº de contenedores)
  const gastosPorTramo = useMemo(
    () => ({
      hastaLocalidadDestino:
        (transitarioData?.precioOrigen || 0) * contenedoresUnicos.length / totalCajas || 0,
      hastaPuertoSalida: (transitarioData?.precioOrigen || 0) * contenedoresUnicos.length / totalCajas || 0,
      hastaPuertoDestino:
        (transitarioData?.precioDestino || 0) * contenedoresUnicos.length / totalCajas || 0,
      hastaPuertoDestinoLocalidad:
        (transitarioData?.precioDestino || 0) * contenedoresUnicos.length / totalCajas || 0,
    }),
    [transitarioData, totalCajas, contenedoresUnicos]
  );

  const comisionPorCaja = useMemo(() => {
    if (!clienteData?.codigoIntermediario || !clienteData?.tipoComision || !clienteData?.importeComision) return 0;
    const importe = clienteData.importeComision?.value || clienteData.importeComision;
    const tipo = clienteData.tipoComision?.value || clienteData.tipoComision;
    const numContenedores = contenedoresUnicos.length;
    if (tipo === "importe_por_negocio") return totalCajas > 0 ? importe / totalCajas : 0;
    if (tipo === "importe_por_contenedor") return totalCajas > 0 ? (importe * numContenedores) / totalCajas : 0;
    if (tipo === "importe_por_cajas") return importe;
    return 0;
  }, [clienteData, totalCajas, contenedoresUnicos]);

  const comisionCliente = useMemo(() => {
    const repercutir = clienteData?.repercutirACliente?.value || clienteData?.repercutirACliente;
    return repercutir === "si" ? comisionPorCaja : 0;
  }, [clienteData, comisionPorCaja]);

  const pricingParams = useMemo(
    () => ({ gastosPorTramo, serviciosPorCaja, seguroPorCaja, comisionCliente }),
    [gastosPorTramo, serviciosPorCaja, seguroPorCaja, comisionCliente]
  );

  // Importes a nivel de contenedor (sin repartir). Cada contenedor los reparte
  // entre SUS propias cajas vía buildPricingParamsPorContenedor.
  const pricingBase = useMemo(() => {
    const servicios = serviciosData?.serviciosSeleccionados || [];
    let serviciosImporte = 0;
    let seguroImporte = 0;
    const serviciosBuckets = {};
    const seguroBuckets = {};
    servicios.forEach((s) => {
      const importe = Number(s.importe) || 0;
      const code = (s.tipo_iva || "").toString().trim().toUpperCase();
      serviciosImporte += importe;
      if (s.tipoArticulo === "seguro") {
        seguroImporte += importe;
        seguroBuckets[code] = (seguroBuckets[code] || 0) + importe;
      } else {
        serviciosBuckets[code] = (serviciosBuckets[code] || 0) + importe;
      }
    });
    return {
      precioOrigen: Number(transitarioData?.precioOrigen) || 0,
      precioDestino: Number(transitarioData?.precioDestino) || 0,
      serviciosImporte,
      seguroImporte,
      serviciosBuckets,
      seguroBuckets,
      importeComision: clienteData?.importeComision?.value ?? clienteData?.importeComision,
      tipoComision: clienteData?.tipoComision?.value ?? clienteData?.tipoComision,
      repercutir: (clienteData?.repercutirACliente?.value ?? clienteData?.repercutirACliente) === "si",
      tieneIntermediario: !!clienteData?.codigoIntermediario,
      numContenedores: contenedoresUnicos.length,
    };
  }, [serviciosData, transitarioData, clienteData, contenedoresUnicos]);

  // Buckets {code_iva: importe/caja} de servicios y seguro, para gravar cada
  // servicio con su propio tipo. Mismo factor que serviciosPorCaja/seguroPorCaja.
  const { serviciosNoSeguroPorIva, seguroPorIva } = useMemo(() => {
    const noSeguro = {};
    const seguro = {};
    if (serviciosData?.serviciosSeleccionados && totalCajas > 0) {
      const factor = contenedoresUnicos.length / totalCajas;
      serviciosData.serviciosSeleccionados.forEach((s) => {
        const code = (s.tipo_iva || "").toString().trim().toUpperCase();
        const importePorCaja = (Number(s.importe) || 0) * factor;
        const target = s.tipoArticulo === "seguro" ? seguro : noSeguro;
        target[code] = (target[code] || 0) + importePorCaja;
      });
    }
    return { serviciosNoSeguroPorIva: noSeguro, seguroPorIva: seguro };
  }, [serviciosData, totalCajas, contenedoresUnicos]);

  // Modo doble flete (cliente nacional + camión + CIF/CFR): dos transitarios/IVAs por tramo
  const modoDoble = esModoDobleFlete({
    fleteDobleDisponible: transitarioData?.fleteDobleDisponible,
    tipoVenta: clienteData?.tipoVenta,
    tipoCliente: clienteData?.tipoCliente,
  });

  // Total del pedido (sin IVA y con IVA por concepto) para el tipo de venta
  // elegido. Reusa la utilidad compartida en vez de recalcular inline.
  const totalesRevision = useMemo(() => {
    // bucketIncoterm mapea FCA→EXW: FCA no tiene bucket propio en los totales.
    const key = bucketIncoterm(clienteData?.tipoVenta?.value || clienteData?.tipoVenta);
    const t = calcularTotalesPorTipoVenta(contenedoresUnicos, preciosEXW, pricingBase, {
      clienteData,
      tiposIVA,
      ivaData,
      getPorcentajesIVA,
      tipoIVAFletes: transitarioData?.tipoIVAFletes || "",
      tipoIVAFleteTerrestre: transitarioData?.tipoIVAFleteTerrestre || "",
      tipoIVAComision: clienteData?.tipoIVAComision || "",
    });
    return { totalSinIVA: t[key] || 0, totalConIVA: t[`${key}_IVA`] || 0 };
  }, [
    contenedoresUnicos,
    preciosEXW,
    pricingBase,
    clienteData,
    tiposIVA,
    ivaData,
    transitarioData,
  ]);

  // Validar campos requeridos
  const validarCamposRequeridos = () => {
    const errores = [];

    if (!clienteData.country) {
      errores.push({
        seccion: "Cliente",
        campo: "País de Facturación",
        mensaje: "Debe especificar el país de facturación",
      });
    }

    if (!clienteData.nombreEmpresa) {
      errores.push({
        seccion: "Cliente",
        campo: "Nombre de la Empresa",
        mensaje: "Debe especificar el nombre de la empresa",
      });
    }

    if (!clienteData.tipoVenta) {
      errores.push({
        seccion: "Cliente",
        campo: "Tipo de Venta",
        mensaje: "Debe seleccionar un tipo de venta (Incoterm)",
      });
    }

    if (!clienteData.tipoContenedor) {
      errores.push({
        seccion: "Cliente",
        campo: "Tipo de Contenedor",
        mensaje: "Debe seleccionar un tipo de contenedor",
      });
    }

    const tipoClienteValue =
      clienteData.tipoCliente?.value || clienteData.tipoCliente;
    if (!tipoClienteValue) {
      errores.push({
        seccion: "Cliente",
        campo: "Tipo de Cliente",
        mensaje: "Debe seleccionar el tipo de cliente",
      });
    }

    if (clienteData.margen === null || clienteData.margen === undefined) {
      errores.push({
        seccion: "Cliente",
        campo: "Margen",
        mensaje: "Debe especificar el margen",
      });
    }

    if (!clienteData.fechaCaducidadCotizacion) {
      errores.push({
        seccion: "Cliente",
        campo: "Fecha de caducidad de la cotización",
        mensaje: "Debe especificar la fecha de caducidad de la cotización",
      });
    }

    // Validaciones condicionales si hay intermediario
    if (clienteData.codigoIntermediario && clienteData.proveedorComision) {
      const repercutirValue =
        clienteData.repercutirACliente?.value || clienteData.repercutirACliente;
      if (!repercutirValue) {
        errores.push({
          seccion: "Cliente - Intermediario",
          campo: "Repercutir a Cliente",
          mensaje: "Debe especificar si se repercute la comisión al cliente",
        });
      }

      const tipoComisionValue =
        clienteData.tipoComision?.value || clienteData.tipoComision;
      if (!tipoComisionValue) {
        errores.push({
          seccion: "Cliente - Intermediario",
          campo: "Tipo de Comisión",
          mensaje: "Debe seleccionar el tipo de comisión",
        });
      }

      if (
        clienteData.importeComision === null ||
        clienteData.importeComision === undefined
      ) {
        errores.push({
          seccion: "Cliente - Intermediario",
          campo: "Importe Comisión",
          mensaje: "Debe especificar el importe de la comisión",
        });
      }
    }

    if (articulosSeleccionados.length === 0) {
      errores.push({
        seccion: "Artículos",
        campo: "Artículos Seleccionados",
        mensaje: "Debe seleccionar al menos un artículo",
      });
    }

    if (contenedoresUnicos.length === 0) {
      errores.push({
        seccion: "Contenedores",
        campo: "Contenedores",
        mensaje: "Debe configurar al menos un contenedor",
      });
    }

    const tipoVenta = normalizeIncoterm(
      clienteData.tipoVenta?.value || clienteData.tipoVenta
    );

    const almacenValue =
      transitarioData.almacen?.value || transitarioData.almacen;
    if (!almacenValue) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Almacén",
        mensaje: "Debe especificar el almacén",
      });
    }

    if (
      transitarioData.diasLibres === null ||
      transitarioData.diasLibres === undefined
    ) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Días Libres",
        mensaje: "Debe especificar los días libres en puerto destino",
      });
    }
    
    if (
      !["EXW", "FCA"].includes(tipoVenta.trim()) &&
      !transitarioData.transitarioSeleccionado
    ) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Transitario",
        mensaje: "Debe seleccionar un transitario",
      });
    }

    // Modo doble flete: transitario e IVA propios del tramo terrestre
    if (modoDoble) {
      if (!transitarioData.transitarioTerrestreSeleccionado) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Transitario terrestre",
          mensaje: "Debe seleccionar el transitario del flete terrestre",
        });
      }

      if (!transitarioData.tipoIVAFleteTerrestre) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "IVA Flete Terrestre",
          mensaje: "Debe especificar el tipo de IVA del flete terrestre",
        });
      }

      if (!transitarioData.tipoIVAFletes) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "IVA Flete Marítimo",
          mensaje: "Debe especificar el tipo de IVA del flete marítimo",
        });
      }
    }

    if (
      ["FOB", "CFR", "CIF", "DAP_MARITIMO", "DAP_TERRESTRE"].includes(tipoVenta) &&
      !transitarioData.fechaCaducidadFleteSalida
    ) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Fecha caducidad flete salida",
        mensaje: "Debe especificar la fecha de caducidad del flete hasta puerto salida",
      });
    }

    if (
      ["CFR", "CIF", "DAP_MARITIMO"].includes(tipoVenta) &&
      !transitarioData.fechaCaducidadFleteDestino
    ) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Fecha caducidad flete destino",
        mensaje: "Debe especificar la fecha de caducidad del flete hasta puerto destino",
      });
    }

    // Validar localidad de carga (siempre requerida)
    if (!clienteData.lugarCarga && !clienteData.lugarCargaLabel) {
      errores.push({
        seccion: "Ruta y Transporte",
        campo: "Localidad de Carga",
        mensaje: "Debe especificar la localidad de carga",
      });
    }

    // Validar puerto de salida si es requerido
    if (
      ["FOB", "CFR", "CIF", "DAP_MARITIMO"].includes(tipoVenta)
    ) {
      const puertoSalidaValue =
        transitarioData.puertoSalida?.value || transitarioData.puertoSalida;
      if (!puertoSalidaValue) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Puerto de Salida",
          mensaje: "El puerto de salida es obligatorio para este tipo de venta",
        });
      }

      if (
        transitarioData.precioOrigen === null ||
        transitarioData.precioOrigen === undefined
      ) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Precio Ruta hasta Puerto Salida",
          mensaje:
            "Debe especificar el precio de la ruta hasta el puerto de salida",
        });
      }
    }

    // Validar puerto de destino si es requerido
    if (["CFR", "CIF", "DAP_MARITIMO"].includes(tipoVenta)) {
      const puertoDestinoValue =
        transitarioData.puertoDestino?.value || transitarioData.puertoDestino;
      if (!puertoDestinoValue) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Puerto de Destino",
          mensaje:
            "El puerto de destino es obligatorio para este tipo de venta",
        });
      }

      if (
        transitarioData.precioDestino === null ||
        transitarioData.precioDestino === undefined
      ) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Precio Ruta hasta Puerto Destino",
          mensaje:
            "Debe especificar el precio de la ruta hasta el puerto de destino",
        });
      }
    }

    // Validar localidad de destino si es requerida
    if (["DAP_TERRESTRE", "DAP_MARITIMO"].includes(tipoVenta)) {
      const localidadDestinoValue =
        transitarioData.localidadDestino?.value ||
        transitarioData.localidadDestino;
      if (!localidadDestinoValue) {
        errores.push({
          seccion: "Ruta y Transporte",
          campo: "Localidad de Destino",
          mensaje:
            "La localidad de destino es obligatoria para este tipo de venta",
        });
      }
    }

    // Los campos de negociación (Tipo de residente, CIF, País de operaciones)
    // se validan en el panel de Negociación, aquí solo verificamos que existan precios

    if (!documentacionData.direccionFiscal) {
      errores.push({
        seccion: "Documentación",
        campo: "Dirección Fiscal",
        mensaje: "La dirección fiscal es obligatoria",
      });
    }

    if (!documentacionData.idiomaCotizacion?.value) {
      errores.push({
        seccion: "Documentación",
        campo: "Idioma de Cotización",
        mensaje: "Debe seleccionar el idioma de la cotización",
      });
    }

    if (!documentacionData.textoBaseOferta) {
      errores.push({
        seccion: "Documentación",
        campo: "Texto Base de Oferta",
        mensaje: "El texto base de la oferta es obligatorio",
      });
    }

    if (!documentacionData.metodoPagoPlantilla?.value) {
      errores.push({
        seccion: "Documentación",
        campo: "Método de Pago",
        mensaje: "Debe seleccionar el método de pago",
      });
    }

    if (!documentacionData.textoFormaPago) {
      errores.push({
        seccion: "Documentación",
        campo: "Texto Forma de Pago",
        mensaje: "El texto de forma de pago es obligatorio",
      });
    }

    if (!documentacionData.bancoCobro?.value) {
      errores.push({
        seccion: "Documentación",
        campo: "Banco de Cobro",
        mensaje: "Debe seleccionar el banco de cobro",
      });
    }

    if (!documentacionData.textoBanco) {
      errores.push({
        seccion: "Documentación",
        campo: "Texto Banco",
        mensaje: "El texto del banco es obligatorio",
      });
    }

    return errores;
  };

  const erroresValidacion = validarCamposRequeridos();
  const hayErrores = erroresValidacion.length > 0;

  // Función para publicar la cotización
  const handlePublicar = async () => {
    if (hayErrores) return;

    try {
      const dealId = context?.crm?.objectId;

      if (!dealId) {
        throw new Error("ID del negocio no disponible");
      }

      // Total de cajas
      const totalCajas = contenedoresUnicos.reduce((total, cont) => {
        const cajas = cont.container_config?.articulos?.reduce(
          (sum, art) => sum + (art.numCajas || 0),
          0
        ) || 0;
        return total + cajas;
      }, 0);

      // Peso neto total
      const netWeightKg = contenedoresUnicos.reduce((total, cont) => {
        const peso = cont.container_config?.articulos?.reduce((sum, art) => {
          const articuloCompleto = articulosSeleccionados.find(
            (a) => a.id === art.articuloId
          );
          const pesoNeto = articuloCompleto?.pesoNeto || art.pesoNeto || 0;
          return sum + (art.numCajas || 0) * pesoNeto;
        }, 0) || 0;
        return total + peso;
      }, 0);

      // Calcular servicios y gastos por tramo
      const totalServicios = serviciosData?.serviciosSeleccionados?.reduce(
        (total, servicio) => total + (servicio.importe || 0),
        0
      ) || 0;

      // Los servicios y fletes son por contenedor: multiplicar por nº de contenedores
      const numContenedores = contenedoresUnicos.length;

      // Importes a nivel de contenedor; cada contenedor los reparte entre SUS cajas.
      const serviciosBucketsFn = {};
      const seguroBucketsFn = {};
      let seguroImporteFn = 0;
      (serviciosData?.serviciosSeleccionados || []).forEach((s) => {
        const importe = Number(s.importe) || 0;
        const code = (s.tipo_iva || "").toString().trim().toUpperCase();
        if (s.tipoArticulo === "seguro") {
          seguroImporteFn += importe;
          seguroBucketsFn[code] = (seguroBucketsFn[code] || 0) + importe;
        } else {
          serviciosBucketsFn[code] = (serviciosBucketsFn[code] || 0) + importe;
        }
      });
      const baseFn = {
        precioOrigen: Number(transitarioData?.precioOrigen) || 0,
        precioDestino: Number(transitarioData?.precioDestino) || 0,
        serviciosImporte: totalServicios,
        seguroImporte: seguroImporteFn,
        serviciosBuckets: serviciosBucketsFn,
        seguroBuckets: seguroBucketsFn,
        importeComision: clienteData?.importeComision?.value ?? clienteData?.importeComision,
        tipoComision: clienteData?.tipoComision?.value ?? clienteData?.tipoComision,
        repercutir: (clienteData?.repercutirACliente?.value ?? clienteData?.repercutirACliente) === "si",
        tieneIntermediario: !!clienteData?.codigoIntermediario,
        numContenedores,
      };


      const lineItems = [];

      // 2.1. SERVICIOS
      if (serviciosData?.serviciosSeleccionados && serviciosData.serviciosSeleccionados.length > 0) {
        serviciosData.serviciosSeleccionados.forEach((servicio) => {
          lineItems.push({
            type: "servicio",
            code: "",
            item: servicio.nombreServicio,
            colis: "",
            weight: "",
            qty: 1,
            unit: "",
            price: "",
            total: servicio.importe * numContenedores,
          });
        });
      }

      // 2.2. CONTENEDORES Y ARTÍCULOS
      contenedoresUnicos.forEach((cont, contIndex) => {
        const containerName = cont.container_name || `${dealId}-${String(contIndex + 1).padStart(3, '0')}`;

        const cajasContenedor = cont.container_config?.articulos?.reduce(
          (sum, a) => sum + (a.numCajas || 0),
          0
        ) || 0;
        const pricingParamsContenedor = buildPricingParamsPorContenedor(baseFn, cajasContenedor);

        // Línea de contenedor (agrupador)
        lineItems.push({
          type: "contenedor",
          code: cont.line_item_id || containerName,
          item: containerName,
          colis: "",
          weight: "",
          qty: 1,
          unit: "",
          price: "",
          total: 0,
          numero_container: containerName,
        });

        // Artículos dentro del contenedor
        cont.container_config?.articulos?.forEach((art) => {
          const lineItemId = art.line_item_id;
          const articuloCompleto = articulosSeleccionados.find(
            (a) => a.id === art.articuloId
          );
          const precioCoste = articuloCompleto?.hs_price_eur || art.price || 0;
          const numCajas = art.numCajas || 0;

          // Obtener precio EXW del estado compartido
          const margenInicial = art.margen_unitario !== null && art.margen_unitario !== undefined
            ? art.margen_unitario * 100
            : clienteData.margen || 12;

          const precioEXW = preciosEXW[lineItemId] != null ? preciosEXW[lineItemId] :
                            (precioCoste + (precioCoste * margenInicial / 100));

          // Calcular precio según tipo de venta (utilidad compartida con NegociacionTab)
          const tipoVenta = normalizeIncoterm(clienteData.tipoVenta?.value || clienteData.tipoVenta);
          const precioFinalRaw = precioEXW === 0 ? 0 : calcularPrecioPorCajaPorIncoterm(precioEXW, tipoVenta, pricingParamsContenedor);
          const precioFinal = Math.round(precioFinalRaw * 1000) / 1000;

          lineItems.push({
            type: "articulo",
            code: lineItemId,
            item: art.nombre,
            colis: "",
            weight: "",
            qty: numCajas,
            unit: "",
            price: precioFinal,
            total: Math.round(precioFinal * numCajas * 1000) / 1000,
            // Datos adicionales para el backend
            margen_unitario: (precioEXW - precioCoste) / precioCoste,
            tipo_iva_repercutido: tiposIVA[lineItemId] || art.tipo_iva_repercutido || "",
            tipo_iva_soportado: tiposIVASoportado[lineItemId] || art.tipo_iva_soportado || "",
            numero_container: containerName,
          });
        });
      });

      // 2.3. FLETES (si aplica según Incoterm)
      const tipoVenta = normalizeIncoterm(clienteData.tipoVenta?.value || clienteData.tipoVenta);

      if (["FOB", "CFR", "CIF", "DAP_MARITIMO"].includes(tipoVenta)) {
        const totalFletes = ((transitarioData?.precioOrigen || 0) + (transitarioData?.precioDestino || 0)) * numContenedores;
        lineItems.push({
          type: "flete",
          code: "",
          item: "FLETES",
          colis: "",
          weight: "",
          qty: 1,
          unit: "",
          price: "",
          total: totalFletes,
        });
      }

      // Utilidad compartida con NegociacionTab.

      const totalesPorVenta = calcularTotalesPorTipoVenta(
        contenedoresUnicos, preciosEXW, baseFn, { clienteData }
      );
      const totalEXW = totalesPorVenta.EXW;
      const totalDAP_TERRESTRE = totalesPorVenta.DAP_TERRESTRE;
      const totalFOB = totalesPorVenta.FOB;
      const totalCFR = totalesPorVenta.CFR;
      const totalDAP_MARITIMO = totalesPorVenta.DAP_MARITIMO;
      const totalCIF = totalesPorVenta.CIF;


      const quotePayload = {
        // HEADER
        header: {
          offerNumber: dealId.toString(),
          date: new Date().toISOString(),
          destinationPort: getSelectLabel(transitarioData.puertoDestino) ||
                          getSelectLabel(transitarioData.localidadDestino) ||
                          "No especificado",
          vessel: getSelectLabel(transitarioData.naviera) || "SIN ESPECIFICAR",
          terms: getSelectLabel(clienteData.tipoVenta) || "EXW",
          currency: "€",
          container: getSelectLabel(clienteData.tipoContenedor),
          sealNumber: null, // No disponible
          etd: null, // No disponible
          eta: null, // No disponible
        },

        // INFORMACIÓN DE LA EMPRESA
        empresa: {
          nombre: "Proex Drinks Group S.A.",
          direccion: documentacionData.direccionFiscal || "",
          iniciales: "AD",
        },

        // INFORMACIÓN DEL CLIENTE
        cliente: {
          nombreEmpresa: clienteData.nombreEmpresa || "",
          pais: clienteData.country || "",
          tipoCliente: getSelectLabel(clienteData.tipoCliente),
          tipoVenta: getSelectLabel(clienteData.tipoVenta),
          tipoContenedor: getSelectLabel(clienteData.tipoContenedor),
          proyecto: getSelectLabel(clienteData.proyecto),
          idioma: getSelectLabel(documentacionData.idiomaCotizacion),
          margen: clienteData.margen || 0,
          // Datos fiscales
          tipoResidente: clienteData.tipoResidente || "",
          cif: clienteData.cif || "",
          paisOperaciones: clienteData.paisOperaciones || "",
          fechaCaducidadCotizacion: clienteData.fechaCaducidadCotizacion ? new Date(Date.UTC(
            clienteData.fechaCaducidadCotizacion.year,
            clienteData.fechaCaducidadCotizacion.month,
            clienteData.fechaCaducidadCotizacion.date
          )).toISOString() : null,
        },

        // RUTA Y LOGÍSTICA
        ruta: {
          lugarCarga: clienteData.lugarCargaLabel || clienteData.lugarCarga || "",
          puertoSalida: getSelectLabel(transitarioData.puertoSalida),
          puertoDestino: getSelectLabel(transitarioData.puertoDestino),
          localidadDestino: getSelectLabel(transitarioData.localidadDestino),
          transitario: transitarioData.transitarioSeleccionado?.name || "",
          transitarioCardcode: transitarioData.transitarioSeleccionado?.cardcode || "",
          naviera: getSelectLabel(transitarioData.naviera),
          almacen: getSelectLabel(transitarioData.almacen),
          diasLibres: transitarioData.diasLibres || 0,
          precioOrigen: transitarioData.precioOrigen || 0,
          precioDestino: transitarioData.precioDestino || 0,
          tipoIVAFletes: transitarioData.tipoIVAFletes || "",
          tipoIVAFletesSoportado: transitarioData.tipoIVAFletesSoportado || "",
          // Modo doble flete: tramo terrestre con transitario/IVA propios (aditivo)
          transitarioTerrestre: modoDoble
            ? transitarioData.transitarioTerrestreSeleccionado?.name || ""
            : "",
          transitarioTerrestreCardcode: modoDoble
            ? transitarioData.transitarioTerrestreSeleccionado?.cardcode || ""
            : "",
          tipoIVAFleteTerrestre: modoDoble
            ? transitarioData.tipoIVAFleteTerrestre || ""
            : "",
          tipoIVAFleteTerrestreSoportado: modoDoble
            ? transitarioData.tipoIVAFleteTerrestreSoportado || ""
            : "",
        },

        // LINE ITEMS (Servicios, Contenedores, Artículos, Fletes)
        lineItems: lineItems,

        // PRECIOS POR TIPO DE VENTA
        precios: {
          fobExpenses: totalFOB - totalEXW,
          totalFOB: totalFOB,
          seafreightRate: totalCFR - totalFOB,
          totalCFR: totalCFR,
          insurance: (serviciosData?.serviciosSeleccionados
            ? serviciosData.serviciosSeleccionados
                .filter((s) => s.tipoArticulo === "seguro")
                .reduce((sum, s) => sum + (Number(s.importe) || 0), 0)
            : 0) * numContenedores,
          totalCIF: totalCIF,
          // Otros Incoterms
          totalEXW: totalEXW,
          totalDAP_TERRESTRE: totalDAP_TERRESTRE,
          totalDAP_MARITIMO: totalDAP_MARITIMO,
        },

        // ESTADÍSTICAS Y TOTALES
        estadisticas: {
          totalColis: totalCajas,
          netWeightKg: netWeightKg,
          grossWeightKg: netWeightKg, // TODO: Agregar tara del contenedor
          containerType: getSelectLabel(clienteData.tipoContenedor),
          filledUpTo: Math.round(contenedoresData.totales?.mediaOcupacion || 0),
          numContainers: contenedoresUnicos.length,
          totalEuros: (() => {
            switch (tipoVenta) {
              case "EXW": return totalEXW;
              case "FCA": return totalEXW;
              case "DAP_TERRESTRE": return totalDAP_TERRESTRE;
              case "FOB": return totalFOB;
              case "CFR": return totalCFR;
              case "DAP_MARITIMO": return totalDAP_MARITIMO;
              case "CIF": return totalCIF;
              default: return totalEXW;
            }
          })(),
        },

        // TEXTOS LEGALES Y COMERCIALES
        textos: {
          textoBaseOferta: documentacionData.textoBaseOferta || "",
          textoBanco: documentacionData.textoBanco || "",
          textoFormaPago: documentacionData.textoFormaPago || "",
          // Opcionales
          textoAdicionalMetodoPago: documentacionData.textoAdicionalMetodoPago || null,
        },

        // INFORMACIÓN ADICIONAL
        intermediario: clienteData.codigoIntermediario && clienteData.proveedorComision ? {
          nombre: clienteData.proveedorComision.name,
          codigo: clienteData.codigoIntermediario,
          tipoComision: getSelectLabel(clienteData.tipoComision),
          importeComision: clienteData.importeComision,
          repercutirACliente: getSelectLabel(clienteData.repercutirACliente),
          tipoIVAComision: clienteData.tipoIVAComision || "",
          tipoIVAComisionSoportado: clienteData.tipoIVAComisionSoportado || "",
        } : null,

        documentacion: {
          idiomaCotizacion: getSelectLabel(documentacionData.idiomaCotizacion),
          metodoPago: getSelectLabel(documentacionData.metodoPagoPlantilla),
          bancoCobro: getSelectLabel(documentacionData.bancoCobro),
          documentosAGenerar: documentacionData.documentosAGenerar?.value?.split(";").map(val => {
            const doc = documentacionData.documentosAGenerar?.options?.find(opt => opt.value === val);
            return doc?.label || val;
          }) || [],
          direccionFiscal: documentacionData.direccionFiscal || "",
          // Certificado de origen
          consignee: documentacionData.consignee || null,
          shipper: documentacionData.shipper || null,
          notify: documentacionData.notify || null,
          certificadoOrigenMercancia: documentacionData.certificadoOrigenMercancia || null,
          // Comentarios
          textoEnBl: documentacionData.textoEnBl || null,
          comentariosLogistica: documentacionData.comentariosLogistica || null,
          comentariosTransitario: documentacionData.comentariosTransitario || null,
        },
      };

      setPublicando(true);
      setPublicacionError(null);
      setPublishStatus("processing");
      setPublishData({ created: 0, associated: 0, total: 0 });
      setCustomObjectsStatus(null);
      setCustomObjectsData({ contenedores: 0, elementos: 0 });
      setStalled(false);
      lastProgressSignatureRef.current = null;
      stalledTicksRef.current = 0;

      const response = await fetchFromAPI(`/api/v1/deals/${dealId}/publish`, {
        method: "POST",
        body: quotePayload,
      });

      if (response.success) {
        const d = response.data || {};
        setPublishData({
          created: d.created_line_items || 0,
          associated: d.associated_line_items || 0,
          total: d.total_line_items || 0,
        });
        setPublishStatus("completed");
        triggerCustomObjects();

        // Los pedidos KIT se crean AQUÍ, después de publicar y no dentro de la
        // publicación: si esto falla, la cotización ya está publicada y no se
        // pierde lo importante. El backend es idempotente por (cotización, SKU),
        // así que republicar no duplica.
        if (kitsSolicitadosDetalle.length > 0) {
          const kits = kitsSolicitadosDetalle.map((k) => ({
            sku: k.sku,
            nombre: k.nombre,
            cajas: k.cajasKit,
            negocios: k.negocios || [],
          }));
          const res = await crearKitsDeCotizacion(dealId, kits);
          setResultadoKits(
            res || { creados: [], omitidos: [], error: true }
          );
        }
      } else {
        console.error("❌ Error al publicar la cotización:", response.error);
        setPublishStatus("error");
        setPublicacionError(response.error || "Error desconocido al publicar");
      }
    } catch (error) {
      console.error("❌ Error al publicar la cotización:", error);
      setPublishStatus("error");
      setPublicacionError(error.message || "Error desconocido al publicar");
    } finally {
      setPublicando(false);
    }
  };

  // Helper para formatear moneda: 3 dec por defecto (por caja), pasar decimals=2 para totales
  const formatCurrency = (value, decimals = 3) => {
    if (value === null || value === undefined || isNaN(value)) {
      return decimals === 2 ? "0,00€" : "0,000€";
    }
    return `${value.toLocaleString("es-ES", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}€`;
  };

  // Mostrar estado de carga mientras se cargan los datos
  if (isLoadingInitial) {
    return (
      <Box>
        <Heading>Vista previa y revisión de la Cotización</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_DATA}</Text>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Heading>Vista previa y revisión de la Cotización</Heading>

      <Flex direction="column" gap="medium">
        <Text>
          Revise todos los datos de la cotización antes de publicarla. Los
          campos marcados en rojo son obligatorios.
        </Text>

        {/* ERRORES DE VALIDACIÓN */}
        {hayErrores && (
          <Alert title="Faltan campos requeridos" variant="error">
            <Flex direction="column" gap="small">
              <Text>Complete los siguientes campos antes de publicar:</Text>
              {erroresValidacion.map((error, idx) => (
                <Text key={idx}>
                  • {error.seccion} - {error.campo}: {error.mensaje}
                </Text>
              ))}
            </Flex>
          </Alert>
        )}

        {/* ========== 1. Información del Cliente ========== */}
        <Box>
          <Heading as="h3">1. Información del Cliente</Heading>
          <Tile>
            <Flex direction="column" gap="medium">
              {/* --- Fila 0: Fecha caducidad cotización --- */}
              <Flex direction="row" gap="large" justify="start">
                <Box flex={1}>
                  <RevisionField
                    label="Fecha de caducidad de la cotización *"
                    value={
                      clienteData.fechaCaducidadCotizacion
                        ? new Date(Date.UTC(
                            clienteData.fechaCaducidadCotizacion.year,
                            clienteData.fechaCaducidadCotizacion.month,
                            clienteData.fechaCaducidadCotizacion.date
                          )).toLocaleDateString("es-ES")
                        : "No especificada"
                    }
                    required={true}
                  />
                </Box>
                <Box flex={1} />
                <Box flex={1} />
              </Flex>

              {/* --- Fila 1: Cliente, Venta y Contenedor --- */}
              <Flex direction="row" gap="large" justify="start">
                <Box flex={1}>
                  <RevisionField
                    label="Nombre del Cliente *"
                    value={clienteData.nombreEmpresa}
                    required={true}
                  />
                </Box>
                <Box flex={1}>
                  <RevisionField
                    label="Tipo de Venta *"
                    value={getSelectLabel(clienteData.tipoVenta)}
                    required={true}
                  />
                </Box>
                <Box flex={1}>
                  <RevisionField
                    label="Tipo de Contenedor *"
                    value={getSelectLabel(clienteData.tipoContenedor)}
                    required={true}
                  />
                </Box>
              </Flex>

              {/* --- Fila 2: País y Tipo Cliente --- */}
              <Flex direction="row" gap="large" justify="start">
                <Box flex={1}>
                  <RevisionField
                    label="País de Facturación *"
                    value={clienteData.country}
                    required={true}
                  />
                </Box>
                <Box flex={1}>
                  <RevisionField
                    label="Tipo de Cliente *"
                    value={getSelectLabel(clienteData.tipoCliente)}
                    required={true}
                  />
                </Box>
              </Flex>

              {/* --- Fila 3: Margen y Proyecto --- */}
              <Flex direction="row" gap="large" justify="start">
                <Box flex={1}>
                  <RevisionField
                    label="% Margen *"
                    value={clienteData.margen ? `${clienteData.margen}%` : "0%"}
                    required={true}
                  />
                </Box>
                <Box flex={1}>
                  <RevisionField
                    label="Proyecto"
                    value={getSelectLabel(clienteData.proyecto)}
                  />
                </Box>
              </Flex>

              {/* --- Sección de Intermediario --- */}
              {clienteData.codigoIntermediario &&
                clienteData.proveedorComision && (
                  <>
                    <Divider />
                    <Heading as="h4">Intermediario y Comisiones</Heading>
                    <Flex direction="row" gap="large" justify="start">
                      <Box flex={1}>
                        <RevisionField
                          label="Intermediario"
                          value={clienteData.proveedorComision.name}
                        />
                      </Box>
                      <Box flex={1}>
                        <RevisionField
                          label="Repercutir a Cliente *"
                          value={getSelectLabel(clienteData.repercutirACliente)}
                          required={true}
                        />
                      </Box>
                    </Flex>
                    <Flex direction="row" gap="large" justify="start">
                      <Box flex={1}>
                        <RevisionField
                          label="Tipo de Comisión *"
                          value={getSelectLabel(clienteData.tipoComision)}
                          required={true}
                        />
                      </Box>
                      <Box flex={1}>
                        <RevisionField
                          label="Importe Comisión *"
                          value={formatCurrency(clienteData.importeComision, 2)}
                          required={true}
                        />
                      </Box>
                    </Flex>
                    <Flex direction="row" gap="large" justify="start">
                      <Box flex={1}>
                        <RevisionField
                          label="IVA Repercutido Comisión"
                          value={(() => {
                            const code = clienteData.tipoIVAComision;
                            const opt = tipoIVAOptions.find(o => o.value === code);
                            return opt?.label || code || "No especificado";
                          })()}
                        />
                      </Box>
                      <Box flex={1}>
                        <RevisionField
                          label="IVA Soportado Comisión"
                          value={(() => {
                            const code = clienteData.tipoIVAComisionSoportado;
                            const opt = tipoIVASoportadoOptions.find(o => o.value === code);
                            return opt?.label || code || "No especificado";
                          })()}
                        />
                      </Box>
                    </Flex>
                  </>
                )}
            </Flex>
          </Tile>
        </Box>

        <Divider size="medium" />

        {/* ========== 2. Artículos, Contenedores y Negociación ========== */}
        <Box>
          <Heading as="h3">2. Artículos, Contenedores y Negociación</Heading>

          {contenedoresUnicos.length === 0 ? (
            <Alert title="Sin contenedores configurados" variant="warning">
              No se han configurado contenedores. Vaya a la pestaña
              "Contenedores" para configurar el pedido.
            </Alert>
          ) : (
            <Tile>
              <Flex direction="column" gap="medium">
                <Box>
                  <Text format={{ fontWeight: "demibold" }}>
                    Seleccione un contenedor para ver su composición:
                  </Text>
                  <Select
                    name="contenedor-selector-revision"
                    label="Contenedor"
                    value={contenedorSeleccionadoId || ""}
                    onChange={(value) => setContenedorSeleccionadoId(value)}
                    options={contenedoresUnicos.map((c) => ({
                      label: c.container_name,
                      value: c.line_item_id,
                    }))}
                  />
                </Box>

                {contenedorSeleccionado && (
                  <>
                    <Flex direction="row" gap="large" justify="start">
                      <Box>
                        <RevisionField
                          label="Tipo de Contenedor"
                          value={contenedorSeleccionado.container_type || "N/A"}
                        />
                      </Box>
                    </Flex>

                    <Heading as="h4">Artículos en este contenedor</Heading>
                    {(() => {
                      const tipoVentaLabel = normalizeIncoterm(
                        clienteData.tipoVenta?.value || clienteData.tipoVenta
                      );

                      const cajasContenedor =
                        contenedorSeleccionado.container_config?.articulos?.reduce(
                          (sum, a) => sum + (a.numCajas || 0),
                          0
                        ) || 0;
                      const pricingParamsContenedor = buildPricingParamsPorContenedor(
                        pricingBase,
                        cajasContenedor
                      );

                      return contenedorSeleccionado.container_config
                        ?.articulos &&
                        contenedorSeleccionado.container_config.articulos
                          .length > 0 ? (
                        <Table bordered={true}>
                          <TableHead>
                            <TableRow>
                              <TableHeader>Artículo</TableHeader>
                              <TableHeader align="right">
                                Cantidad (Cajas)
                              </TableHeader>
                              <TableHeader align="right">
                                Peso Neto/Caja (kg)
                              </TableHeader>
                              <TableHeader align="right">
                                Peso Total (kg)
                              </TableHeader>
                              <TableHeader align="right">
                                Precio Coste/Caja (€)
                              </TableHeader>
                              <TableHeader align="right">
                                Precio {tipoVentaLabel}/Caja (€)
                              </TableHeader>
                              <TableHeader align="right">
                                Subtotal {tipoVentaLabel} (€)
                              </TableHeader>
                              <TableHeader align="right">Margen (%)</TableHeader>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {contenedorSeleccionado.container_config.articulos.map(
                              (art) => {
                                // Buscar el artículo completo en articulosSeleccionados para obtener datos completos
                                const articuloCompleto =
                                  articulosSeleccionados.find(
                                    (a) => a.id === art.articuloId
                                  );

                                const numCajas = art.numCajas || 0;
                                const pesoNetoPorCaja =
                                  articuloCompleto?.pesoNeto ||
                                  art.pesoNeto ||
                                  0;
                                const pesoTotal = numCajas * pesoNetoPorCaja;

                                // Precio coste (del backend)
                                const precioCoste =
                                  articuloCompleto?.hs_price_eur ||
                                  art.price ||
                                  0;

                                // Usar el precio EXW editado del estado compartido o calcular el inicial
                                const lineItemId = art.line_item_id;
                                const margenInicial =
                                  art.margen_unitario !== null &&
                                  art.margen_unitario !== undefined
                                    ? art.margen_unitario * 100
                                    : clienteData.margen || 12;
                                const precioEXW =
                                  preciosEXW[lineItemId] != null ? preciosEXW[lineItemId] :
                                  precioCoste +
                                    (precioCoste * margenInicial) / 100;

                                // Calcular margen basado en el precio EXW (margen de negociación del producto)
                                const margenEnEuros = precioEXW - precioCoste;
                                const margenPorcentaje =
                                  precioCoste > 0
                                    ? (
                                        (margenEnEuros / precioCoste) *
                                        100
                                      ).toFixed(1)
                                    : "0.0";

                                const precioFinalTipoVenta = precioEXW === 0 ? 0 : calcularPrecioPorCajaPorIncoterm(precioEXW, tipoVentaLabel, pricingParamsContenedor);

                                return (
                                  <TableRow
                                    key={art.line_item_id || art.articuloId}
                                  >
                                    <TableCell>{art.nombre}</TableCell>
                                    <TableCell align="right">
                                      {numCajas}
                                    </TableCell>
                                    <TableCell align="right">
                                      {pesoNetoPorCaja.toFixed(2)}
                                    </TableCell>
                                    <TableCell align="right">
                                      {pesoTotal.toFixed(2)}
                                    </TableCell>
                                    <TableCell align="right">
                                      {formatCurrency(precioCoste)}
                                    </TableCell>
                                    <TableCell align="right">
                                      {formatCurrency(precioFinalTipoVenta)}
                                    </TableCell>
                                    <TableCell align="right">
                                      {formatCurrency(
                                        numCajas * precioFinalTipoVenta,
                                        2
                                      )}
                                    </TableCell>
                                    <TableCell align="right">
                                      {margenPorcentaje}%
                                    </TableCell>
                                  </TableRow>
                                );
                              }
                            )}
                          </TableBody>
                        </Table>
                    ) : (
                      <Alert title="Sin artículos" variant="info">
                        Este contenedor no tiene artículos configurados.
                      </Alert>
                      );
                    })()}
                  </>
                )}
              </Flex>
            </Tile>
          )}
        </Box>

        <Divider size="medium" />

        {/* ========== 3. Ruta y Transporte ========== */}
        <Heading as="h3">3. Ruta y Transporte</Heading>
        <Tile>
          <Flex direction="column" gap="medium">
            <Flex direction="column" gap="small">
              <Flex direction="row" justify="space-between">
                <RevisionField
                  label={modoDoble ? "Transitario marítimo *" : "Transitario *"}
                  value={
                    transitarioData.transitarioSeleccionado?.name ||
                    "No seleccionado"
                  }
                  required={true}
                />
                <RevisionField
                  label="Naviera"
                  value={
                    getSelectLabel(transitarioData.naviera) || "No especificada"
                  }
                />
              </Flex>
              {modoDoble && (
                <Flex direction="row" justify="space-between">
                  <RevisionField
                    label="Transitario terrestre *"
                    value={
                      transitarioData.transitarioTerrestreSeleccionado?.name ||
                      "No seleccionado"
                    }
                    required={true}
                  />
                </Flex>
              )}
              <Flex direction="row" justify="space-between">
                <RevisionField
                  label="Días Libres en Puerto Destino *"
                  value={transitarioData.diasLibres || "No especificados"}
                  required={true}
                />
                <RevisionField
                  label="Almacén *"
                  value={
                    getSelectLabel(transitarioData.almacen) || "No especificado"
                  }
                  required={true}
                />
              </Flex>
            </Flex>

            <Divider size="small" />

            <Heading as="h4">Trayecto Logístico</Heading>
            {(() => {
              const tipoVenta = normalizeIncoterm(
                clienteData.tipoVenta?.value || clienteData.tipoVenta
              );

              // Determinar qué pasos son necesarios según el tipo de venta
              const steps = [
                {
                  label: "Localidad de Carga",
                  value:
                    clienteData.lugarCargaLabel ||
                    clienteData.lugarCarga ||
                    "No especificada",
                  required: true,
                  completed: !!(
                    clienteData.lugarCargaLabel || clienteData.lugarCarga
                  ),
                },
                {
                  label: "Puerto de Salida",
                  value:
                    getSelectLabel(transitarioData.puertoSalida) ||
                    "No especificado",
                  required: [
                    "FOB",
                    "CFR",
                    "CIF",
                    "DAP_MARITIMO",
                  ].includes(tipoVenta),
                  completed: !!(
                    transitarioData.puertoSalida?.value ||
                    transitarioData.puertoSalida
                  ),
                },
                {
                  label: "Puerto de Destino",
                  value:
                    getSelectLabel(transitarioData.puertoDestino) ||
                    "No especificado",
                  required: [
                    "CFR",
                    "CIF",
                    "DAP_MARITIMO",
                  ].includes(tipoVenta),
                  completed: !!(
                    transitarioData.puertoDestino?.value ||
                    transitarioData.puertoDestino
                  ),
                },
                {
                  label: "Localidad de Destino",
                  value:
                    getSelectLabel(transitarioData.localidadDestino) ||
                    "No especificada",
                  required: [
                    "DAP_TERRESTRE",
                    "DAP_MARITIMO",
                  ].includes(tipoVenta),
                  completed: !!(
                    transitarioData.localidadDestino?.value ||
                    transitarioData.localidadDestino
                  ),
                },
              ];

              // Filtrar solo los pasos obligatorios para el StepIndicator
              const requiredSteps = steps.filter((step) => step.required);
              const stepNames = requiredSteps.map((step) => step.label);

              // Todos los pasos obligatorios están activos hasta el último
              const currentStep = requiredSteps.length;

              return (
                <Flex direction="column" gap="medium">
                  {/* StepIndicator solo con pasos obligatorios */}
                  {requiredSteps.length > 0 && (
                    <StepIndicator
                      currentStep={currentStep}
                      stepNames={stepNames}
                    />
                  )}

                  {/* Grid 2x2 con todos los puntos de ruta */}
                  <Flex direction="column" gap="small">
                    {/* Fila 1: Localidad de Carga y Puerto de Salida */}
                    <Flex direction="row" gap="medium">
                      <Box flex={1}>
                        <RevisionField
                          label={`${steps[0].label}${
                            steps[0].required ? " *" : ""
                          }`}
                          value={steps[0].value}
                          required={steps[0].required}
                        />
                      </Box>
                      <Box flex={1}>
                        <RevisionField
                          label={`${steps[1].label}${
                            steps[1].required ? " *" : ""
                          }`}
                          value={steps[1].value}
                          required={steps[1].required}
                        />
                      </Box>
                    </Flex>

                    {/* Fila 2: Puerto de Destino y Localidad de Destino */}
                    <Flex direction="row" gap="medium">
                      <Box flex={1}>
                        <RevisionField
                          label={`${steps[2].label}${
                            steps[2].required ? " *" : ""
                          }`}
                          value={steps[2].value}
                          required={steps[2].required}
                        />
                      </Box>
                      <Box flex={1}>
                        <RevisionField
                          label={`${steps[3].label}${
                            steps[3].required ? " *" : ""
                          }`}
                          value={steps[3].value}
                          required={steps[3].required}
                        />
                      </Box>
                    </Flex>
                  </Flex>
                </Flex>
              );
            })()}

            <Divider size="small" />

            <Flex direction="column" gap="small">
              <Heading as="h4">Costes de Transporte</Heading>
              <Flex direction="row" justify="space-between">
                <RevisionField
                  label={
                    modoDoble
                      ? "Flete terrestre — Precio Ruta hasta Puerto Salida"
                      : "Precio Ruta hasta Puerto Salida"
                  }
                  value={
                    formatCurrency(transitarioData.precioOrigen, 2) ||
                    "No especificados"
                  }
                />
                <RevisionField
                  label={
                    modoDoble
                      ? "Flete terrestre — Fecha caducidad *"
                      : "Fecha caducidad flete salida *"
                  }
                  value={
                    transitarioData.fechaCaducidadFleteSalida
                      ? new Date(Date.UTC(
                          transitarioData.fechaCaducidadFleteSalida.year,
                          transitarioData.fechaCaducidadFleteSalida.month,
                          transitarioData.fechaCaducidadFleteSalida.date
                        )).toLocaleDateString("es-ES")
                      : "No especificada"
                  }
                />
              </Flex>
              {!["EXW", "FCA", "DAP_TERRESTRE"].includes(
                normalizeIncoterm(clienteData.tipoVenta?.value || clienteData.tipoVenta)
              ) && (
                <Flex direction="row" justify="space-between">
                  <RevisionField
                    label={
                      modoDoble
                        ? "Flete marítimo — Precio Ruta hasta Puerto Destino"
                        : "Precio Ruta hasta Puerto Destino"
                    }
                    value={
                      formatCurrency(transitarioData.precioDestino, 2) ||
                      "No especificados"
                    }
                  />
                  <RevisionField
                    label={
                      modoDoble
                        ? "Flete marítimo — Fecha caducidad *"
                        : "Fecha caducidad flete destino *"
                    }
                    value={
                      transitarioData.fechaCaducidadFleteDestino
                        ? new Date(Date.UTC(
                            transitarioData.fechaCaducidadFleteDestino.year,
                            transitarioData.fechaCaducidadFleteDestino.month,
                            transitarioData.fechaCaducidadFleteDestino.date
                          )).toLocaleDateString("es-ES")
                        : "No especificada"
                    }
                  />
                </Flex>
              )}
              {modoDoble && (
                <Flex direction="row" justify="space-between">
                  <RevisionField
                    label="IVA Repercutido Flete Terrestre"
                    value={(() => {
                      const code = transitarioData.tipoIVAFleteTerrestre;
                      const opt = tipoIVAOptions.find(o => o.value === code);
                      return opt?.label || code || "No especificado";
                    })()}
                  />
                  <RevisionField
                    label="IVA Soportado Flete Terrestre"
                    value={(() => {
                      const code = transitarioData.tipoIVAFleteTerrestreSoportado;
                      const opt = tipoIVASoportadoOptions.find(o => o.value === code);
                      return opt?.label || code || "No especificado";
                    })()}
                  />
                </Flex>
              )}
              <Flex direction="row" justify="space-between">
                <RevisionField
                  label={
                    modoDoble
                      ? "IVA Repercutido Flete Marítimo"
                      : "IVA Repercutido Fletes"
                  }
                  value={(() => {
                    const code = transitarioData.tipoIVAFletes;
                    const opt = tipoIVAOptions.find(o => o.value === code);
                    return opt?.label || code || "No especificado";
                  })()}
                />
                <RevisionField
                  label={
                    modoDoble
                      ? "IVA Soportado Flete Marítimo"
                      : "IVA Soportado Fletes"
                  }
                  value={(() => {
                    const code = transitarioData.tipoIVAFletesSoportado;
                    const opt = tipoIVASoportadoOptions.find(o => o.value === code);
                    return opt?.label || code || "No especificado";
                  })()}
                />
              </Flex>
            </Flex>
          </Flex>
        </Tile>

        <Divider size="medium" />

        {/* ========== 4. Servicios del Pedido ========== */}
        <Heading as="h3">4. Servicios del Pedido</Heading>
        <Tile>
          {serviciosData?.serviciosSeleccionados &&
          serviciosData.serviciosSeleccionados.length > 0 ? (
            <Flex direction="column" gap="medium">
              <Table bordered={true}>
                <TableHead>
                  <TableRow>
                    <TableHeader>Servicio</TableHeader>
                    <TableHeader>Proveedor</TableHeader>
                    <TableHeader align="right">Importe (€)</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {serviciosData.serviciosSeleccionados.map((servicio) => (
                    <TableRow key={servicio.id}>
                      <TableCell>{servicio.nombreServicio}</TableCell>
                      <TableCell>
                        {servicio.nombreProveedor ||
                          servicio.proveedorCardcode ||
                          "No especificado"}
                      </TableCell>
                      <TableCell align="right">
                        {formatCurrency(servicio.importe, 2)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <Flex direction="row" justify="end" gap="medium">
                <Text format={{ fontWeight: "bold" }}>Total Servicios:</Text>
                <Text format={{ fontWeight: "bold" }}>
                  {formatCurrency(
                    serviciosData.serviciosSeleccionados.reduce(
                      (total, servicio) => total + (servicio.importe || 0),
                      0
                    ),
                    2
                  )}
                </Text>
              </Flex>
            </Flex>
          ) : (
            <Alert title="Sin servicios" variant="info">
              No se han seleccionado servicios para este pedido.
            </Alert>
          )}
        </Tile>

        <Divider size="medium" />

        {contenedoresUnicos.length > 0 && (
          <>
            <Heading as="h4">Resumen Global</Heading>
            <Statistics>
              <StatisticsItem
                label="Total Contenedores"
                number={contenedoresUnicos.length.toString()}
              />
              <StatisticsItem
                label="Total Cajas"
                number={contenedoresUnicos
                  .reduce((total, cont) => {
                    const cajas =
                      cont.container_config?.articulos?.reduce(
                        (sum, art) => sum + (art.numCajas || 0),
                        0
                      ) || 0;
                    return total + cajas;
                  }, 0)
                  .toLocaleString("es-ES")}
              />
              <StatisticsItem
                label="Peso Neto Total"
                number={`${contenedoresUnicos
                  .reduce((total, cont) => {
                    const peso =
                      cont.container_config?.articulos?.reduce((sum, art) => {
                        // Buscar el artículo completo para obtener el peso neto correcto
                        const articuloCompleto = articulosSeleccionados.find(
                          (a) => a.id === art.articuloId
                        );
                        const pesoNeto =
                          articuloCompleto?.pesoNeto || art.pesoNeto || 0;
                        return sum + (art.numCajas || 0) * pesoNeto;
                      }, 0) || 0;
                    return total + peso;
                  }, 0)
                  .toLocaleString("es-ES", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })} kg`}
              />
              <StatisticsItem
                label={`Total ${clienteData.tipoVenta || "EXW"}`}
                number={formatCurrency(totalesRevision.totalSinIVA, 2)}
              >
                <StatisticsTrend
                  direction="increase"
                  value={formatCurrency(totalesRevision.totalConIVA, 2) + " c/IVA"}
                />
                <Text variant="microcopy">
                  {(() => {
                    const precioPorCajaSinIVA =
                      totalCajas > 0 ? totalesRevision.totalSinIVA / totalCajas : 0;
                    const precioPorCajaConIVA =
                      totalCajas > 0 ? totalesRevision.totalConIVA / totalCajas : 0;

                    return (
                      formatCurrency(precioPorCajaSinIVA) +
                      "/caja - " +
                      formatCurrency(precioPorCajaConIVA) +
                      "/caja c/IVA"
                    );
                  })()}
                </Text>
              </StatisticsItem>
              <StatisticsItem
                label="Margen negocio"
                number={(() => {
                  // Calcular margen global ponderado del pedido completo
                  let totalMargenPonderado = 0;
                  let totalCajasNegocio = 0;
                  let totalMargenEnEuros = 0;

                  contenedoresUnicos.forEach((cont) => {
                    cont.container_config?.articulos?.forEach((art) => {
                      const lineItemId = art.line_item_id;
                      const articuloCompleto = articulosSeleccionados.find(
                        (a) => a.id === art.articuloId
                      );
                      const precioCoste =
                        articuloCompleto?.hs_price_eur || art.price || 0;
                      const margenInicial =
                        art.margen_unitario !== null &&
                        art.margen_unitario !== undefined
                          ? art.margen_unitario * 100
                          : clienteData.margen || 12;
                      const precioEXW =
                        preciosEXW[lineItemId] != null ? preciosEXW[lineItemId] :
                        precioCoste + (precioCoste * margenInicial) / 100;
                      const numCajas = art.numCajas || 0;

                      // Calcular margen en euros y porcentaje
                      const margenEnEuros = precioEXW - precioCoste;
                      const margenPorcentaje =
                        precioCoste > 0
                          ? (margenEnEuros / precioCoste) * 100
                          : 0;

                      // Acumular margen ponderado por número de cajas
                      totalMargenPonderado += margenPorcentaje * numCajas;
                      totalMargenEnEuros += margenEnEuros * numCajas;
                      totalCajasNegocio += numCajas;
                    });
                  });

                  const margenGlobal =
                    totalCajasNegocio > 0
                      ? totalMargenPonderado / totalCajasNegocio
                      : 0;
                  const margenPorCajaEnEuros =
                    totalCajasNegocio > 0
                      ? totalMargenEnEuros / totalCajasNegocio
                      : 0;

                  return `${margenGlobal.toLocaleString("es-ES", {
                    minimumFractionDigits: 1,
                    maximumFractionDigits: 1,
                  })}%`;
                })()}
              >
                <Text format={{ fontWeight: "demibold" }}>
                  {(() => {
                    // Calcular margen por caja en euros
                    let totalMargenEnEuros = 0;
                    let totalCajasNegocio = 0;

                    contenedoresUnicos.forEach((cont) => {
                      cont.container_config?.articulos?.forEach((art) => {
                        const lineItemId = art.line_item_id;
                        const articuloCompleto = articulosSeleccionados.find(
                          (a) => a.id === art.articuloId
                        );
                        const precioCoste =
                          articuloCompleto?.hs_price_eur || art.price || 0;
                        const margenInicial =
                          art.margen_unitario !== null &&
                          art.margen_unitario !== undefined
                            ? art.margen_unitario * 100
                            : clienteData.margen || 12;
                        const precioEXW =
                          preciosEXW[lineItemId] != null ? preciosEXW[lineItemId] :
                          precioCoste + (precioCoste * margenInicial) / 100;
                        const numCajas = art.numCajas || 0;

                        const margenEnEuros = precioEXW - precioCoste;
                        totalMargenEnEuros += margenEnEuros * numCajas;
                        totalCajasNegocio += numCajas;
                      });
                    });

                    const margenPorCajaEnEuros =
                      totalCajasNegocio > 0
                        ? totalMargenEnEuros / totalCajasNegocio
                        : 0;

                    return `${margenPorCajaEnEuros.toLocaleString("es-ES", {
                      minimumFractionDigits: 3,
                      maximumFractionDigits: 3,
                    })}€/caja`;
                  })()}
                </Text>
              </StatisticsItem>
            </Statistics>
          </>
        )}

        <Divider size="medium" />

        {/* ========== 5. Documentación ========== */}
        <Heading as="h3">5. Documentación</Heading>
        <Tile>
          <Flex direction="column" gap="medium">
            <Flex direction="column" gap="small">
              <Flex direction="row" justify="space-between">
                <RevisionField
                  label="Idioma de la Cotización *"
                  value={
                    documentacionData.idiomaCotizacion?.options?.find(
                      (opt) =>
                        opt.value === documentacionData.idiomaCotizacion?.value
                    )?.label || "No especificado"
                  }
                  required={true}
                />
                <RevisionField
                  label="Plantilla de Método de Pago *"
                  value={
                    documentacionData.metodoPagoPlantilla?.options?.find(
                      (opt) =>
                        opt.value ===
                        documentacionData.metodoPagoPlantilla?.value
                    )?.label || "No especificado"
                  }
                  required={true}
                />
              </Flex>

              <RevisionField
                label="Banco para Recepción de Pagos *"
                value={
                  documentacionData.bancoCobro?.options?.find(
                    (opt) => opt.value === documentacionData.bancoCobro?.value
                  )?.label || "No especificado"
                }
                required={true}
              />
            </Flex>

            <RevisionField
              label="Dirección Fiscal *"
              value={documentacionData.direccionFiscal}
              required={true}
            />

            <Divider size="small" />

            <RevisionField
              label="Documentos a Generar"
              value={
                documentacionData.documentosAGenerar?.value
                  ? documentacionData.documentosAGenerar.value
                      .split(";")
                      .map((docValue) => {
                        const doc =
                          documentacionData.documentosAGenerar.options?.find(
                            (opt) => opt.value === docValue
                          );
                        return doc?.label || docValue;
                      })
                      .join(", ")
                  : "Ninguno seleccionado"
              }
            />

            {documentacionData.textoEnBl && (
              <>
                <Divider size="small" />
                <RevisionField
                  label="Texto para Bill of Lading (BL)"
                  value={documentacionData.textoEnBl}
                />
              </>
            )}

            {documentacionData.comentariosLogistica && (
              <>
                <Divider size="small" />
                <RevisionField
                  label="Comentarios de Logística"
                  value={documentacionData.comentariosLogistica}
                />
              </>
            )}

            {documentacionData.comentariosTransitario && (
              <>
                <Divider size="small" />
                <RevisionField
                  label="Instrucciones para el Transitario"
                  value={documentacionData.comentariosTransitario}
                />
              </>
            )}

            {/* Datos de Envío - Certificado de Origen */}
            {(documentacionData.consignee ||
              documentacionData.shipper ||
              documentacionData.notify) && (
              <>
                <Divider size="small" />
                <Heading as="h4">Certificado de Origen</Heading>
                <Flex direction="column" gap="small">
                  {documentacionData.consignee && (
                    <RevisionField
                      label="Consignee (Importador)"
                      value={documentacionData.consignee}
                    />
                  )}
                  {documentacionData.shipper && (
                    <RevisionField
                      label="Shipper (Exportador)"
                      value={documentacionData.shipper}
                    />
                  )}
                  {documentacionData.notify && (
                    <RevisionField
                      label="Notify (Notificar a)"
                      value={documentacionData.notify}
                    />
                  )}
                </Flex>
              </>
            )}

            {/* Descripción de Mercancía para Certificado */}
            {documentacionData.certificadoOrigenMercancia && (
              <>
                <Divider size="small" />
                <RevisionField
                  label="Descripción de Mercancía"
                  value={documentacionData.certificadoOrigenMercancia}
                />
              </>
            )}

            {documentacionData.textoAdicionalMetodoPago && (
              <>
                <Divider size="small" />
                <RevisionField
                  label="Información Adicional de Pago"
                  value={
                    documentacionData.textoAdicionalMetodoPago.substring(
                      0,
                      150
                    ) +
                    (documentacionData.textoAdicionalMetodoPago.length > 150
                      ? "..."
                      : "")
                  }
                />
              </>
            )}

            {/* Textos Principales (Colapsados) */}
            <Divider size="small" />
            <Heading as="h4">Textos de la Cotización</Heading>
            <Flex direction="column" gap="small">
              {documentacionData.textoBaseOferta && (
                <RevisionField
                  label="Texto Base de la Oferta Comercial *"
                  value={
                    documentacionData.textoBaseOferta.substring(0, 150) + "..."
                  }
                  required={true}
                />
              )}

              {documentacionData.textoBanco && (
                <RevisionField
                  label="Datos Bancarios *"
                  value={documentacionData.textoBanco.substring(0, 150) + "..."}
                  required={true}
                />
              )}

              {documentacionData.textoFormaPago && (
                <RevisionField
                  label="Condiciones de Pago *"
                  value={
                    documentacionData.textoFormaPago.substring(0, 150) + "..."
                  }
                  required={true}
                />
              )}
            </Flex>

          </Flex>
        </Tile>

        {/* PANEL UNIFICADO DE ESTADO DE PUBLICACIÓN */}
        <PublishingStatusPanel
          publishStatus={publishStatus}
          publishData={publishData}
          customObjectsStatus={customObjectsStatus}
          customObjectsData={customObjectsData}
          publicacionError={publicacionError}
          stalled={stalled}
        />

        {/* BOTONES DE ACCIÓN */}
        <ButtonRow>
          <Button
            variant="secondary"
            onClick={() => onNavigate("documentacion")}
          >
            Anterior
          </Button>
          {/* MOCKUP pedidos KIT — botón propio a propósito: "Publicar Cotización"
              crea la Quote y sus line items de verdad, así que no le metemos un
              paso intermedio con datos ficticios. Cuando el flujo sea real, este
              `overlay` pasa al botón de publicar y el confirmar llama a
              handlePublicar. */}
          <Button
            variant="secondary"
            overlay={
              <Modal
                id="resumen-kits-modal"
                title="Resumen de pedidos KIT"
                width="md"
              >
                <ResumenKitsModal
                  kitsAGenerar={kitsSolicitadosDetalle}
                  kitsMarcados={kitsMarcadosDetalle}
                  onClose={() =>
                    actions && actions.closeOverlay("resumen-kits-modal")
                  }
                />
              </Modal>
            }
          >
            {resumenKitsTotal > 0
              ? `Resumen de KIT (${resumenKitsTotal})`
              : "Resumen de KIT"}
          </Button>
          <Button
            variant="primary"
            onClick={handlePublicar}
            disabled={
              hayErrores ||
              publicando ||
              publishStatus === "pending" ||
              publishStatus === "processing" ||
              publishStatus === "completed"
            }
          >
            {publicando || publishStatus === "pending" || publishStatus === "processing"
              ? "Publicando..."
              : "Publicar Cotización"}
          </Button>
        </ButtonRow>

        {hayErrores && publishStatus !== "completed" && (
          <Alert title="Revise los errores antes de publicar" variant="warning">
            Complete todos los campos requeridos marcados arriba para poder
            publicar la cotización.
          </Alert>
        )}

        {/* Qué pasó con los pedidos KIT al publicar. Se dice explícitamente:
            crear un KIT compromete producción en fábrica, así que no puede
            ocurrir en silencio ni fallar en silencio. */}
        {resultadoKits && resultadoKits.error && (
          <Alert title="La cotización se publicó, pero los pedidos KIT no" variant="warning">
            Vuelve a publicar para reintentarlo, o avisa a Administración para que
            los cree a mano. La cotización no se ve afectada.
          </Alert>
        )}

        {resultadoKits && !resultadoKits.error && resultadoKits.creados?.length > 0 && (
          <Alert
            title={
              resultadoKits.creados.length === 1
                ? "1 pedido KIT creado"
                : `${resultadoKits.creados.length} pedidos KIT creados`
            }
            variant="success"
          >
            <Flex direction="column" gap="extra-small">
              <Text>
                Quedan en Pendiente de validación hasta que Administración
                confirme la producción.
              </Text>
              {resultadoKits.creados.map((k) => (
                <Text key={k.kit_id} variant="microcopy">
                  {k.numero} — {Number(k.cajas || 0).toLocaleString("es-ES")} cajas
                </Text>
              ))}
            </Flex>
          </Alert>
        )}

        {resultadoKits && !resultadoKits.error && resultadoKits.omitidos?.length > 0 && (
          <Alert title="Pedidos KIT que ya existían" variant="info">
            <Flex direction="column" gap="extra-small">
              <Text>
                Esta cotización ya los había generado, así que no se han
                duplicado.
              </Text>
              {resultadoKits.omitidos.map((k, i) => (
                <Text key={i} variant="microcopy">
                  {k.numero || k.sku}{" "}
                  {k.cajas
                    ? `— ${Number(k.cajas).toLocaleString("es-ES")} cajas`
                    : ""}
                </Text>
              ))}
            </Flex>
          </Alert>
        )}
      </Flex>
    </Box>
  );
};

export default RevisionTab;
