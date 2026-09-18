// React core
import React, { useState, useEffect, useRef } from "react";

// HubSpot UI Extensions
import {
  Box,
  Heading,
  Flex,
  Text,
  Select,
  NumberInput,
  Button,
  ButtonRow,
  Divider,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  Statistics,
  StatisticsItem,
  EmptyState,
  Alert,
  LoadingSpinner,
} from "@hubspot/ui-extensions";

// Utils and Constants
import { fetchFromAPI } from "../utils/api.utils";
import { useCustomObjectsSync } from "../hooks/useCustomObjectsSync";

// Pedidos KIT (aislado: sólo se consume desde el aviso, no toca la lógica de contenedores)
import { analizarKits, conParamsKit } from "../utils/kits.utils";
import AlertaKits, { resumenKitsPorSku } from "../components/AlertaKits";
import PanelKitsDisponibles from "../components/PanelKitsDisponibles";
import { useKitsDisponibles } from "../hooks/useKitsDisponibles";
import { useKitParams } from "../hooks/useKitParams";
import {
  ERROR_MESSAGES,
  LOADING_MESSAGES,
  INITIAL_RENDER_DELAY_MS,
} from "../constants/common.constants";

const ContenedoresTab = ({
  // Para cerrar el diálogo de negocios del aviso de KIT: los overlays los
  // gestiona HubSpot, no el componente.
  actions,
  context,
  onNavigate,
  articulosSeleccionados,
  contenedoresData,
  setContenedoresData,
  setContenedoresUnicos,
  tipoContenedorOptions,
  tipoContenedorSeleccionado,
  cargaPorPalets,
  kitsMarcados,
  onCambiarMarcaKit,
  kitsSolicitados,
  onChangeSolicitudKit,
  stockUsado = {},
  onChangeStockUsado,
}) => {
  // Usar artículos seleccionados del tab previo
  // Helper to extract numeric value from field (handles both direct values and {value, options} objects)
  const getNumericValue = (field) => {
    if (field === null || field === undefined) return 0;
    if (typeof field === 'object' && field.value !== undefined) return Number(field.value) || 0;
    return Number(field) || 0;
  };

  const ARTICULOS_DISPONIBLES = articulosSeleccionados.map((articulo) => ({
    id: articulo.id,
    nombre: articulo.nombre,
    pesoCaja: articulo.pesoNeto || 8.4, // kg por caja (default)
    precio: articulo.hs_price_eur || 0, // Precio por caja
    margen_unitario: articulo.margen_unitario, // Margen unitario del artículo
    // Pedidos KIT: `sku` resuelve los parámetros de kit contra el fixture; los dos
    // campos siguientes llegarán del backend en la fase 2 y tienen prioridad.
    sku: articulo.hs_sku || "",
    tamano_kit: articulo.tamano_kit,
    bloque_multiplo: articulo.bloque_multiplo,
    // Capacidades por tipo de contenedor (extract numeric value)
    unidades_d20dr: getNumericValue(articulo.unidades_d20dr),
    unidades_d40dr: getNumericValue(articulo.unidades_d40dr),
    unidades_tra80: getNumericValue(articulo.unidades_tra80),
  }));

  // Pedidos KIT disponibles. El hook decide la fuente: backend local si está
  // configurado, y si no el fixture. Nada más de esta pestaña depende de ello.
  const { kits: kitsDisponibles, recargar: recargarKits } = useKitsDisponibles(
    ARTICULOS_DISPONIBLES.map((a) => a.sku)
  );

  // `tamano_kit` real de cada producto. Sin esto sólo calculaba KIT el único SKU
  // que estaba en el fichero de parámetros.
  const paramsKit = useKitParams(ARTICULOS_DISPONIBLES.map((a) => a.sku));
  const articulosConKit = conParamsKit(ARTICULOS_DISPONIBLES, paramsKit);

  // La cotización abierta: identifica cuáles de las marcas de un KIT son suyas.
  const negocioId = context?.crm?.objectId ? String(context.crm.objectId) : "";

  // Estado para el contenedor que se está configurando
  const [tipoContenedor, setTipoContenedor] = useState(
    tipoContenedorSeleccionado || tipoContenedorOptions?.[0]?.value || ""
  );
  const [
    articulosSeleccionadosContenedor,
    setArticulosSeleccionadosContenedor,
  ] = useState([]);
  const [cajasConfig, setCajasConfig] = useState({}); // { articuloId: numCajas }

  // Estados locales desde el "shared state" del padre, con fallback
  const [contenedoresDefinidos, setContenedoresDefinidos] = useState(
    contenedoresData?.contenedoresDefinidos || []
  );
  const [cantidadesContenedores, setCantidadesContenedores] = useState(
    contenedoresData?.cantidadesContenedores || {}
  );
  // Instancias reales por grupo (fuente de identidad para preservar line_item_id
  // de contenedor y de cada artículo al expandir cantidad → instancias al guardar)
  const [instanciasPorGrupo, setInstanciasPorGrupo] = useState(
    contenedoresData?.instanciasPorGrupo || {}
  );

  // Análisis de pedidos KIT del pedido completo: las cajas del mismo artículo se
  // suman en todos los contenedores antes de decidir si falta producción.
  const analisisKits = analizarKits(
    contenedoresDefinidos,
    cantidadesContenedores,
    articulosConKit,
    resumenKitsPorSku(kitsDisponibles, kitsMarcados, stockUsado, negocioId)
  );

  // Estados de carga / error inicial (UX igual a ClienteTab)
  const [isLoadingInitial, setIsLoadingInitial] = useState(false);
  const [errorInitialLoad, setErrorInitialLoad] = useState(null);
  const [initialRenderComplete, setInitialRenderComplete] = useState(false);

  // Estadísticas del contenedor actual
  const [ocupacionActual, setOcupacionActual] = useState(0);
  const [pesoNetoActual, setPesoNetoActual] = useState(0);
  const [errorOcupacion, setErrorOcupacion] = useState(false);

  // Gestión de guardado
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorSave, setErrorSave] = useState(null);

  // Sincronización async de precios de contenedor (worker en background)
  const { status: syncStatus, data: syncData, waitForCompletion: waitForSync } =
    useCustomObjectsSync(context?.crm?.objectId);

  // Track previous parent state to detect when ArticulosTab clears containers
  const prevParentLengthRef = useRef(null);

  // Log cuando articulosSeleccionados está vacío pero hay contenedores (depurar paneles vacíos)
  useEffect(() => {
    const artCount = articulosSeleccionados?.length ?? 0;
    const contCount = contenedoresDefinidos?.length ?? 0;
    const parentContCount = contenedoresData?.contenedoresDefinidos?.length ?? 0;
    if (artCount === 0 && (contCount > 0 || parentContCount > 0)) {
      console.log("[ContenedoresTab] articulosSeleccionados está vacío pero hay contenedores definidos - posible orden de carga");
    }
  }, [articulosSeleccionados?.length, contenedoresDefinidos?.length, contenedoresData?.contenedoresDefinidos?.length]);

  // Sync tipoContenedor when tipoContenedorSeleccionado changes
  useEffect(() => {
    if (tipoContenedorSeleccionado) {
      setTipoContenedor(tipoContenedorSeleccionado);
    }
  }, [tipoContenedorSeleccionado]);

  // Sync local state when contenedoresData changes from parent
  // This handles the case when ArticulosTab clears containers after updating articles
  // Only sync when the parent explicitly clears containers (both arrays empty)
  useEffect(() => {
    const currentParentLength = contenedoresData?.contenedoresDefinidos?.length || 0;
    const prevParentLength = prevParentLengthRef.current;

    if (
      currentParentLength === 0 &&
      prevParentLength !== null &&
      prevParentLength > 0 &&
      contenedoresDefinidos.length > 0
    ) {
      // Parent has cleared containers (ArticulosTab action), sync local state
      setContenedoresDefinidos([]);
      setCantidadesContenedores({});
      setInstanciasPorGrupo({});
      setContenedoresUnicos({ contenedoresUnicos: [] });
    }

    // Update ref for next comparison
    prevParentLengthRef.current = currentParentLength;
  }, [contenedoresData?.contenedoresDefinidos?.length]);

  // Marcar que el render inicial está completo después de un breve delay
  // Esto da tiempo a que los artículos del tab anterior se carguen antes de mostrar alertas
  useEffect(() => {
    const timer = setTimeout(() => {
      setInitialRenderComplete(true);
    }, INITIAL_RENDER_DELAY_MS);

    return () => clearTimeout(timer);
  }, []);

  // Carga inicial de contenedores guardados en backend
  useEffect(() => {
    const dealId = context?.crm?.objectId;
    console.log("[ContenedoresTab] cargarContenedores: dealId =", dealId);
    if (!dealId) return;

    let cancelado = false;

    const cargarContenedores = async () => {
      try {
        setIsLoadingInitial(true);
        setErrorInitialLoad(null);

        const data = await fetchFromAPI(
          `/api/v1/deals/${dealId}/containers`,
          { method: "GET" }
        );

        console.log("[ContenedoresTab] Data containers: success =", data?.success, "containers length =", data?.containers?.length, "unique_containers length =", data?.unique_containers?.length, "raw data keys =", data ? Object.keys(data) : []);

        if (!data?.success) {
          throw new Error(
            data?.error || ERROR_MESSAGES.LOAD_ERROR
          );
        }
        const lista = Array.isArray(data.containers) ? data.containers : [];
        console.log("[ContenedoresTab] lista.length =", lista.length, "cancelado =", cancelado);

        if (!cancelado && lista.length > 0) {
          const unicos = Array.isArray(data.unique_containers)
            ? data.unique_containers
            : [];

          // Indexar las instancias reales por clave de composición para
          // asociarlas a su grupo/definición y conservar identidad
          // (line_item_id de contenedor y de cada artículo).
          const instanciasPorClave = {};
          unicos.forEach((u) => {
            const articulosUnico = u.container_config?.articulos || [];
            const clave = grupoKey(u.container_type, articulosUnico);
            if (!instanciasPorClave[clave]) instanciasPorClave[clave] = [];
            instanciasPorClave[clave].push({
              line_item_id: u.line_item_id,
              numero_container: u.container_number,
              articulos: articulosUnico.map((a) => ({
                line_item_id: a.line_item_id,
                articuloId: a.articuloId,
                nombre: a.nombre,
                numCajas: a.numCajas,
              })),
            });
          });
          Object.values(instanciasPorClave).forEach((arr) =>
            arr.sort((x, y) => (x.numero_container || 0) - (y.numero_container || 0))
          );

          // Mapear a tu modelo local, conservando line_item_id de contenedor y
          // de cada artículo (de unique_containers, o del propio grouped si lo trae).
          const defin = lista.map((c) => {
            const articulos = (c.container_config?.articulos || []).map((a) => ({
              articuloId: a.articuloId,
              nombre: a.nombre,
              numCajas: a.numCajas,
              line_item_id: a.line_item_id, // suele venir solo en unique_containers
            }));
            const clave = grupoKey(c.container_type, articulos);
            const instancias = instanciasPorClave[clave] || [];
            // Completar line_item_id de artículo desde la 1ª instancia (mismo orden por composición)
            const primera = instancias[0];
            if (primera) {
              articulos.forEach((art) => {
                if (art.line_item_id) return;
                const match = primera.articulos.find(
                  (ia) => ia.articuloId === art.articuloId && ia.numCajas === art.numCajas
                );
                if (match) art.line_item_id = match.line_item_id;
              });
            }
            return {
              id: c.line_item_id || `loaded-${c.container_number}`,
              tipo: c.container_type,
              numero_container: c.container_number,
              line_item_id: c.line_item_id || null,
              clave,
              articulos,
              ocupacion: c.container_config?.ocupacion ?? 0,
              pesoNeto: c.container_config?.pesoNeto ?? 0,
            };
          });

          const cantidades = {};
          const instanciasGrupo = {};
          defin.forEach((d, idx) => {
            const c = lista[idx];
            cantidades[d.id] = c.cantidad ?? 1;
            instanciasGrupo[d.id] = instanciasPorClave[d.clave] || [];
          });

          console.log("[ContenedoresTab] Estado actualizado: defin count =", defin.length, "unique_containers =", unicos.length);
          setContenedoresDefinidos(defin);
          setCantidadesContenedores(cantidades);
          setInstanciasPorGrupo(instanciasGrupo);
          setContenedoresUnicos({ contenedoresUnicos: unicos });
        } else {
          console.log("[ContenedoresTab] No se actualiza estado: lista vacía o cancelado");
        }
      } catch (e) {
        console.log("[ContenedoresTab] Error cargando contenedores:", e?.message, e);
        if (!cancelado)
          setErrorInitialLoad(
            e?.message || ERROR_MESSAGES.LOAD_ERROR
          );
      } finally {
        if (!cancelado) setIsLoadingInitial(false);
      }
    };

    cargarContenedores();
    return () => {
      cancelado = true;
    };
  }, [context?.crm?.objectId]);

  // Sync local → parent
  useEffect(() => {
    const totales = calcularTotales();
    setContenedoresData({
      contenedoresDefinidos,
      cantidadesContenedores,
      instanciasPorGrupo,
      totales,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contenedoresDefinidos, cantidadesContenedores, instanciasPorGrupo]);

  // Clave de composición que replica el agrupamiento del backend
  // (container_type + artículos por articuloId/numCajas). Sirve para asociar
  // un grupo/definición de contenedoresDefinidos con sus instancias reales en
  // unique_containers.
  const grupoKey = (tipo, articulos) => {
    const arts = (articulos || [])
      .map((a) => `${a.articuloId}:${a.numCajas}`)
      .sort()
      .join("|");
    return `${tipo}::${arts}`;
  };

  const getCapacidadMaxima = (articulo, tipoContenedor) => {
    const containerFieldMap = {
      D20DR: "unidades_d20dr",
      D40DR: "unidades_d40dr",
      TRAILER80: "unidades_tra80",
      "TRAILER 80": "unidades_tra80"
    };

    const field = containerFieldMap[tipoContenedor];
    if (field && articulo[field] !== undefined) {
      return articulo[field];
    }

    return 0;
  };

  const calcularTotales = () => {
    let totalColis = 0;
    let totalPesoNeto = 0;
    let totalContenedores = 0;
    let sumaOcupacion = 0;
    let contadorOcupacion = 0;

    contenedoresDefinidos.forEach((contenedor) => {
      const cantidad = cantidadesContenedores[contenedor.id] || 0;
      totalContenedores += cantidad;

      const colisContenedor = contenedor.articulos.reduce(
        (sum, art) => sum + art.numCajas,
        0
      );
      totalColis += colisContenedor * cantidad;
      totalPesoNeto += contenedor.pesoNeto * cantidad;

      sumaOcupacion += contenedor.ocupacion * cantidad;
      contadorOcupacion += cantidad;
    });

    const mediaOcupacion =
      contadorOcupacion > 0 ? sumaOcupacion / contadorOcupacion : 0;

    return { totalColis, totalPesoNeto, totalContenedores, mediaOcupacion };
  };

  // Calcular ocupación y peso del contenedor en edición
  useEffect(() => {
    if (!tipoContenedor || articulosSeleccionadosContenedor.length === 0) {
      setOcupacionActual(0);
      setPesoNetoActual(0);
      setErrorOcupacion(false);
      return;
    }

    let totalCajas = 0;
    let pesoTotal = 0;
    let ocupacionTotal = 0;

    articulosSeleccionadosContenedor.forEach((articuloId) => {
      const articulo = ARTICULOS_DISPONIBLES.find((a) => a.id === articuloId);
      if (!articulo) return;

      const numCajas = cajasConfig[articuloId] || 0;
      const capacidadMaxima = getCapacidadMaxima(articulo, tipoContenedor);

      if (capacidadMaxima > 0 && numCajas > 0) {
        // Calcular ocupación individual de este artículo
        const ocupacionArticulo = (numCajas / capacidadMaxima) * 100;
        ocupacionTotal += ocupacionArticulo;
      }

      totalCajas += numCajas;
      pesoTotal += numCajas * articulo.pesoCaja;
    });

    setOcupacionActual(ocupacionTotal);
    setPesoNetoActual(pesoTotal);
    // Si es carga por palets, no validar ocupación mínima
    if (cargaPorPalets) {
      setErrorOcupacion(false);
    } else {
      // Rango permitido (90% - 103%)
      setErrorOcupacion(ocupacionTotal < 90 || ocupacionTotal > 103);
    }
  }, [
    tipoContenedor,
    articulosSeleccionadosContenedor,
    cajasConfig,
    ARTICULOS_DISPONIBLES,
    cargaPorPalets,
  ]);

  // Handlers
  const handleTipoContenedorChange = (value) => {
    setTipoContenedor(value);
    setCajasConfig({});
  };

  const handleToggleArticulo = (articuloId) => {
    if (articulosSeleccionadosContenedor.includes(articuloId)) {
      setArticulosSeleccionadosContenedor(
        articulosSeleccionadosContenedor.filter((id) => id !== articuloId)
      );
      const newConfig = { ...cajasConfig };
      delete newConfig[articuloId];
      setCajasConfig(newConfig);
    } else {
      setArticulosSeleccionadosContenedor([
        ...articulosSeleccionadosContenedor,
        articuloId,
      ]);
    }
  };

  const handleCajasChange = (articuloId, value) => {
    setCajasConfig({
      ...cajasConfig,
      [articuloId]: value,
    });
  };

  const handleAgregarContenedor = () => {
    if (articulosSeleccionadosContenedor.length === 0) return;
    if (errorOcupacion) return;

    const nuevoContenedor = {
      id: Date.now(), // temporal
      tipo: tipoContenedor,
      articulos: articulosSeleccionadosContenedor.map((articuloId) => {
        const articulo = ARTICULOS_DISPONIBLES.find((a) => a.id === articuloId);
        return {
          articuloId,
          nombre: articulo.nombre,
          numCajas: cajasConfig[articuloId] || 0,
        };
      }),
      ocupacion: ocupacionActual,
      pesoNeto: pesoNetoActual,
    };

    setContenedoresDefinidos([...contenedoresDefinidos, nuevoContenedor]);
    setCantidadesContenedores({
      ...cantidadesContenedores,
      [nuevoContenedor.id]: 1,
    });

    // Reset form (manteniendo default del selector)
    setTipoContenedor(
      tipoContenedorSeleccionado || tipoContenedorOptions?.[0]?.value || ""
    );
    setArticulosSeleccionadosContenedor([]);
    setCajasConfig({});
  };

  const handleEliminarContenedor = (contenedorId) => {
    setContenedoresDefinidos(
      contenedoresDefinidos.filter((c) => c.id !== contenedorId)
    );
    const newCantidades = { ...cantidadesContenedores };
    delete newCantidades[contenedorId];
    setCantidadesContenedores(newCantidades);
    const newInstancias = { ...instanciasPorGrupo };
    delete newInstancias[contenedorId];
    setInstanciasPorGrupo(newInstancias);
  };

  /**
   * Save containers configuration to backend and navigate to next tab
   *
   * Validates that at least one container with quantity > 0 exists,
   * then sends the configuration to the API endpoint.
   */
  const guardarContenedores = async () => {
    try {
      setErrorSave(null);
      setIsSubmitting(true);

      const dealId = context?.crm?.objectId;
      if (!dealId) {
        setErrorSave(ERROR_MESSAGES.NO_DEAL_ID);
        return;
      }

      if (contenedoresDefinidos.length === 0) {
        setErrorSave(
          "Debe agregar al menos un contenedor antes de continuar."
        );
        return;
      }

      // Expandir cantidad → instancias: una entrada de payload = UN contenedor.
      // Con line_item_id = instancia existente a preservar; sin él = contenedor nuevo.
      // Reducir cantidad NO envía las instancias de mayor container_number → "se
      // elimina el último". El backend hace MERGE INCREMENTAL por instancia.
      const containers = [];
      contenedoresDefinidos.forEach((contenedor) => {
        const cantidad = cantidadesContenedores[contenedor.id] || 0;
        if (cantidad <= 0) return;

        // Instancias reales del grupo, ordenadas por container_number ASC.
        const instancias = [...(instanciasPorGrupo[contenedor.id] || [])].sort(
          (a, b) => (a.numero_container || 0) - (b.numero_container || 0)
        );

        // Articulos de la instancia preservada: line_item_id de la propia
        // instancia para que el backend preserve precios negociados (_edit_container_in_place).
        const buildArticulos = (instancia) =>
          contenedor.articulos.map((art) => {
            const articuloCompleto = ARTICULOS_DISPONIBLES.find((a) => a.id === art.articuloId);
            const articuloInstancia = instancia
              ? (instancia.articulos || []).find(
                  (ia) => ia.articuloId === art.articuloId && ia.numCajas === art.numCajas
                )
              : null;
            const lineItemArticulo =
              articuloInstancia?.line_item_id || art.line_item_id || undefined;
            const entrada = {
              articuloId: art.articuloId,
              nombre: art.nombre,
              numCajas: art.numCajas,
              pesoNeto: articuloCompleto?.pesoCaja || 0,
              price: articuloCompleto?.precio || 0,
              margen_unitario: articuloCompleto?.margen_unitario,
            };
            if (lineItemArticulo) entrada.line_item_id = lineItemArticulo;
            return entrada;
          });

        for (let i = 0; i < cantidad; i++) {
          // Las primeras `instancias.length` entradas preservan instancias
          // existentes (menor container_number primero); el resto son nuevas.
          const instancia = i < instancias.length ? instancias[i] : null;
          const entrada = {
            tipo: contenedor.tipo,
            container_config: {
              articulos: buildArticulos(instancia),
              ocupacion: cargaPorPalets ? 100 : contenedor.ocupacion,
              pesoNeto: contenedor.pesoNeto,
            },
          };
          if (instancia?.line_item_id) entrada.line_item_id = instancia.line_item_id;
          containers.push(entrada);
        }
      });

      if (containers.length === 0) {
        setErrorSave(
          "Debe especificar una cantidad mayor a 0 para al menos un contenedor."
        );
        return;
      }

      const payload = { containers };
      const data = await fetchFromAPI(
        `/api/v1/deals/${dealId}/containers`,
        {
          method: "POST",
          body: payload,
        }
      );

      if (!data?.success) {
        throw new Error(
          data?.error || ERROR_MESSAGES.SAVE_ERROR
        );
      }

      // Si el deal ya está publicado, el backend encola un worker que recalcula
      // precios y sincroniza Contenedor/Elemento en background (igual que publicar
      // cotización). Esperamos a que termine antes de pasar a transitario para que
      // el siguiente paso vea los precios ya actualizados.
      if (data.job_id) {
        try {
          await waitForSync(data.job_id);
        } catch (syncError) {
          console.error("La sincronización de precios no completó:", syncError);
          setErrorSave(
            "Los contenedores se guardaron, pero la sincronización de precios no se completó. Espera unos segundos y vuelve a pulsar Siguiente."
          );
          return;
        }
      }

      // Backend doesn't return unique_containers in POST response
      // We need to fetch them separately with a GET request
      try {
        const containersData = await fetchFromAPI(
          `/api/v1/deals/${dealId}/containers`,
          { method: "GET" }
        );

        if (containersData?.success && containersData.unique_containers && setContenedoresUnicos) {
          setContenedoresUnicos({ contenedoresUnicos: containersData.unique_containers });
        }
      } catch (error) {
        console.error("Error al cargar unique_containers después de guardar:", error);
        // Don't block navigation if this fails
      }

      onNavigate("transitario");
    } catch (error) {
      setErrorSave(
        error?.message || ERROR_MESSAGES.SAVE_ERROR
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const totales = calcularTotales();

  // UX de carga inicial: solo mostrar loading mientras se cargan los contenedores
  if (isLoadingInitial) {
    return (
      <Box>
        <Heading>Configuración de Contenedores</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_CONTAINERS}</Text>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Heading>Configuración de Contenedores</Heading>

      {/* Errores con el mismo patrón visual que ClienteTab */}
      {errorInitialLoad && (
        <Box marginBottom="small">
          <Alert
            title="No se pudieron cargar los contenedores"
            variant="warning"
          >
            {errorInitialLoad}
          </Alert>
        </Box>
      )}

      {errorSave && (
        <Box marginBottom="small">
          <Alert title="Error al guardar contenedores" variant="error">
            {errorSave}
          </Alert>
        </Box>
      )}

      <Flex direction="column" gap="medium">
        {/* Mensaje si no hay artículos seleccionados (solo después de la carga inicial Y del render inicial) */}
        {!isLoadingInitial &&
          initialRenderComplete &&
          ARTICULOS_DISPONIBLES.length === 0 && (
            <>
              <Alert title="No hay artículos seleccionados" variant="warning">
                Debe seleccionar al menos un artículo en el panel de "Artículos"
                antes de configurar contenedores.
              </Alert>
              <ButtonRow>
                <Button
                  variant="secondary"
                  onClick={() => onNavigate("articulos")}
                >
                  Ir a Artículos
                </Button>
              </ButtonRow>
            </>
          )}

        {/* Contenido principal: mostrar solo si no está cargando Y hay artículos */}
        {!isLoadingInitial &&
          initialRenderComplete &&
          ARTICULOS_DISPONIBLES.length > 0 && (
            <>
              {/* SECCIÓN 1: CONFIGURAR NUEVO CONTENEDOR */}
              <Text>Configure la composición de cada tipo de contenedor:</Text>

              <Select
                label="Tipo de Contenedor"
                name="tipo-contenedor"
                description="Seleccione el tamaño del contenedor"
                placeholder="Seleccionar..."
                value={tipoContenedor}
                onChange={handleTipoContenedorChange}
                options={tipoContenedorOptions}
                tooltip="La capacidad máxima varía según el tipo"
              />

              <Box>
                <Text format={{ fontWeight: "bold" }}>
                  Artículos a Incluir:
                </Text>
                <Flex direction="column" gap="small">
                  {ARTICULOS_DISPONIBLES.map((articulo) => (
                    <Flex
                      key={articulo.id}
                      direction="row"
                      justify="space-between"
                      align="center"
                      gap="small"
                    >
                      <Text>{articulo.nombre}</Text>
                      <Button
                        size="xs"
                        variant={
                          articulosSeleccionadosContenedor.includes(articulo.id)
                            ? "primary"
                            : "secondary"
                        }
                        onClick={() => handleToggleArticulo(articulo.id)}
                      >
                        {articulosSeleccionadosContenedor.includes(articulo.id)
                          ? "Seleccionado"
                          : "Añadir"}
                      </Button>
                    </Flex>
                  ))}
                </Flex>
              </Box>

              {articulosSeleccionadosContenedor.length > 0 && (
                <>
                  <Divider size="small" />
                  <Text format={{ fontWeight: "bold" }}>
                    Número de Cajas por Artículo:
                  </Text>
                  {articulosSeleccionadosContenedor.map((articuloId) => {
                    const articulo = ARTICULOS_DISPONIBLES.find(
                      (a) => a.id === articuloId
                    );
                    const maxCajas = getCapacidadMaxima(
                      articulo,
                      tipoContenedor
                    );

                    return (
                      <NumberInput
                        key={articuloId}
                        label={articulo.nombre}
                        name={`cajas-${articuloId}`}
                        description={`Máximo ${maxCajas} cajas en contenedor de tipo ${tipoContenedor}`}
                        value={cajasConfig[articuloId] || 0}
                        onChange={(value) =>
                          handleCajasChange(articuloId, value)
                        }
                        min={0}
                        placeholder=""
                        tooltip={`Peso por caja: ${articulo.pesoCaja} kg`}
                      />
                    );
                  })}
                </>
              )}

              {articulosSeleccionadosContenedor.length > 0 && (
                <>
                  <Divider size="small" />
                  <Statistics>
                    <StatisticsItem
                      label="Ocupación del Contenedor"
                      number={cargaPorPalets ? "100.00%" : `${ocupacionActual.toFixed(2)}%`}
                    />
                    <StatisticsItem
                      label="Peso Neto Total"
                      number={`${pesoNetoActual.toFixed(2)} kg`}
                    />
                  </Statistics>

                  {errorOcupacion && !cargaPorPalets && (
                    <Alert title="Capacidad fuera de rango" variant="error">
                      El contenedor está fuera del rango permitido de ocupación (90%-103%). Reduzca o aumente el número de cajas.
                    </Alert>
                  )}

                  {cargaPorPalets && (
                    <Alert title="Carga por Palets activa" variant="info">
                      La validación de ocupación mínima está desactivada para carga por palets.
                    </Alert>
                  )}

                  <Button
                    variant="primary"
                    onClick={handleAgregarContenedor}
                    disabled={
                      errorOcupacion ||
                      articulosSeleccionadosContenedor.length === 0
                    }
                  >
                    Agregar Contenedor
                  </Button>
                </>
              )}

              <Divider size="medium" />

              {/* SECCIÓN 2: CONTENEDORES DEFINIDOS */}
              <Heading>Contenedores Definidos</Heading>

              {contenedoresDefinidos.length === 0 ? (
                <EmptyState
                  title="No hay contenedores definidos"
                  layout="vertical"
                >
                  <Text>Configure y agregue contenedores arriba.</Text>
                </EmptyState>
              ) : (
                <Table bordered={true}>
                  <TableHead>
                    <TableRow>
                      <TableHeader width="min">#</TableHeader>
                      <TableHeader>Tipo</TableHeader>
                      <TableHeader>Artículos</TableHeader>
                      <TableHeader align="right">Ocupación</TableHeader>
                      <TableHeader align="right">Peso Neto</TableHeader>
                      <TableHeader width="min">Acciones</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {contenedoresDefinidos.map((contenedor, index) => (
                      <TableRow key={contenedor.id}>
                        <TableCell width="min">{index + 1}</TableCell>
                        <TableCell>{contenedor.tipo}</TableCell>
                        <TableCell>
                          {contenedor.articulos.map((art, idx) => (
                            <Text key={idx}>
                              {art.nombre}: {art.numCajas} cajas
                              {idx < contenedor.articulos.length - 1 && " | "}
                            </Text>
                          ))}
                        </TableCell>
                        <TableCell align="right">
                          {cargaPorPalets ? "100.00" : contenedor.ocupacion.toFixed(2)}%
                        </TableCell>
                        <TableCell align="right">
                          {contenedor.pesoNeto.toFixed(2)} kg
                        </TableCell>
                        <TableCell width="min">
                          <Button
                            size="xs"
                            variant="destructive"
                            onClick={() =>
                              handleEliminarContenedor(contenedor.id)
                            }
                          >
                            Eliminar
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}

              {/* SECCIÓN 3: CANTIDAD DE CADA TIPO DE CONTENEDOR */}
              {contenedoresDefinidos.length > 0 && (
                <>
                  <Divider size="medium" />
                  <Heading>Cantidad de Contenedores</Heading>
                  <Text>
                    Especifique cuántos contenedores de cada tipo necesita:
                  </Text>

                  {contenedoresDefinidos.map((contenedor, index) => (
                    <NumberInput
                      key={contenedor.id}
                      label={`Contenedor ${index + 1} (${contenedor.tipo})`}
                      name={`cantidad-${contenedor.id}`}
                      description={`Ocupación: ${cargaPorPalets ? "100.00" : contenedor.ocupacion.toFixed(2)}% | Peso: ${contenedor.pesoNeto.toFixed(2)} kg`}
                      value={cantidadesContenedores[contenedor.id] || 0}
                      onChange={(value) =>
                        setCantidadesContenedores({
                          ...cantidadesContenedores,
                          [contenedor.id]: value,
                        })
                      }
                      min={0}
                      placeholder=""
                    />
                  ))}
                </>
              )}

              {/* SECCIÓN 4: RESUMEN TOTAL */}
              {contenedoresDefinidos.length > 0 && (
                <>
                  <Divider size="medium" />
                  <Heading>Resumen Total del Pedido</Heading>

                  <Statistics>
                    <StatisticsItem
                      label="Total Cajas"
                      number={totales.totalColis.toLocaleString("es-ES")}
                    />
                    <StatisticsItem
                      label="Peso Neto Total"
                      number={`${totales.totalPesoNeto.toLocaleString("es-ES", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })} kg`}
                    />
                  </Statistics>

                  <Statistics>
                    <StatisticsItem
                      label="Número de Contenedores"
                      number={totales.totalContenedores.toString()}
                    />
                    <StatisticsItem
                      label="Ocupación media"
                      number={cargaPorPalets ? "100.00%" : `${totales.mediaOcupacion.toFixed(2)}%`}
                    />
                  </Statistics>

                  {/* Pedidos KIT: se calcula sobre el total del pedido, no por contenedor */}
                  <AlertaKits
                    analisis={analisisKits}
                    solicitudes={kitsSolicitados}
                    onChangeSolicitud={onChangeSolicitudKit}
                    actions={actions}
                    stockUsado={stockUsado}
                    onChangeStockUsado={onChangeStockUsado}
                    negocioId={negocioId}
                  />

                  {/* KIT abiertos consumibles, filtrados por los artículos de esta
                      cotización. La fuente la decide el hook: en vivo si hay backend
                      local levantado, y si no el fixture. */}
                  <PanelKitsDisponibles
                    skusEnCotizacion={ARTICULOS_DISPONIBLES.map((a) => a.sku)}
                    kits={kitsDisponibles}
                    kitsMarcados={kitsMarcados}
                    onCambiarMarcaKit={onCambiarMarcaKit}
                    negocioId={negocioId}
                    onGuardado={recargarKits}
                  />

                </>
              )}

              {(syncStatus === "pending" || syncStatus === "processing") && (
                <Flex direction="row" align="center" gap="small">
                  <LoadingSpinner size="xs" />
                  <Text>
                    {LOADING_MESSAGES.SYNCING_PRICES}
                    {syncData.contenedores || syncData.elementos
                      ? ` (${syncData.contenedores} contenedores · ${syncData.elementos} líneas)`
                      : ""}
                  </Text>
                </Flex>
              )}

              <ButtonRow>
                <Button
                  variant="secondary"
                  onClick={() => onNavigate("articulos")}
                  disabled={isSubmitting}
                >
                  Anterior
                </Button>
                <Button
                  variant="primary"
                  onClick={guardarContenedores}
                  disabled={
                    contenedoresDefinidos.length === 0 ||
                    isSubmitting ||
                    isLoadingInitial
                  }
                  loading={isSubmitting}
                >
                  {isSubmitting
                    ? syncStatus === "pending" || syncStatus === "processing"
                      ? LOADING_MESSAGES.SYNCING_PRICES
                      : LOADING_MESSAGES.SAVING
                    : "Siguiente"}
                </Button>
              </ButtonRow>
            </>
          )}
      </Flex>
    </Box>
  );
};

export default ContenedoresTab;
