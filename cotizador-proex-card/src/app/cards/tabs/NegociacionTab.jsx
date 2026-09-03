import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Box,
  Heading,
  Flex,
  Divider,
  ButtonRow,
  Button,
  Text,
  Statistics,
  StatisticsItem,
  StatisticsTrend,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  TableFooter,
  StepperInput,
  NumberInput,
  Select,
  Dropdown,
  Tag,
  Alert,
  EmptyState,
  LoadingSpinner,
  Tile,
  Modal,
} from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";
import EditarContenedorModal from "../components/EditarContenedorModal";
import {
  LOADING_MESSAGES,
  INITIAL_RENDER_DELAY_MS,
} from "../constants/common.constants";
import { getContainerTypeLabel } from "../constants/containers.constants";
import {
  calcularPrecioPorCajaPorIncoterm,
  calcularPrecioEXWDesdeIncoterm,
  calcularTotalesPorTipoVenta,
  buildPricingParamsPorContenedor,
  comisionPorCajaIntra,
  normalizeIncoterm,
  bucketIncoterm,
  esModoDobleFlete,
} from "../utils/pricing.utils";
import { getIVAPorcentaje, getPorcentajesIVA, getDefaultIVAByResidencia } from "../utils/iva.utils";

const calcularMargenArticuloEnContenedor = (
  artContenedor,
  preciosEXW,
  comisionPorCaja,
  clienteData
) => {
  const lineItemId = artContenedor.line_item_id;
  const costePorCaja = artContenedor.precio_de_coste || 0;
  const margenInicial =
    artContenedor.margen_unitario != null &&
    artContenedor.margen_unitario > 0
      ? artContenedor.margen_unitario * 100
      : (clienteData?.margen ?? 12);

  const precioEXWFinal =
    preciosEXW[lineItemId] != null
      ? preciosEXW[lineItemId]
      : costePorCaja + (costePorCaja * margenInicial) / 100;
  const margenEnEuros = precioEXWFinal - costePorCaja;
  const margenActual =
    costePorCaja > 0 ? (margenEnEuros / costePorCaja) * 100 : 0;

  let margenEfectivo = margenActual;
  const repercutirAClienteValue =
    clienteData?.repercutirACliente?.value || clienteData?.repercutirACliente;
  if (comisionPorCaja > 0 && repercutirAClienteValue === "no") {
    const comisionComoMargen = (comisionPorCaja / costePorCaja) * 100;
    margenEfectivo = margenActual - comisionComoMargen;
  }

  return margenEfectivo;
};

// Tipos de venta (Incoterms)
const TIPOS_VENTA = {
  EXW: { label: "EXW", descripcion: "Ex Works" },
  DAP_TERRESTRE: { label: "DAP Terrestre", descripcion: "Destino terrestre" },
  FOB: { label: "FOB", descripcion: "Free On Board" },
  CFR: { label: "CFR", descripcion: "Cost & Freight" },
  DAP_MARITIMO: { label: "DAP Marítimo", descripcion: "Destino marítimo" },
  CIF: { label: "CIF", descripcion: "Cost, Insurance & Freight" },
};

const EDITAR_CONTENEDOR_MODAL_ID = "editar-contenedor-modal";

const NegociacionTab = ({
  context,
  actions,
  kitsMarcados,
  onCambiarMarcaKit,
  kitsSolicitados,
  onChangeSolicitudKit,
  onNavigate,
  transitarioData,
  serviciosData,
  clienteData, // Necesitamos el margen del cliente como fallback
  tipoIVAOptions = [], // Opciones de tipo de IVA repercutido
  tipoIVASoportadoOptions = [], // Opciones de tipo de IVA soportado
  ivaData = {}, // Datos completos de IVA indexados por code
  articulosSeleccionados = [], // Catálogo de artículos del deal (incluye peso_neto y unidades_*)
  contenedoresUnicos, // Contenedores únicos (nueva estructura que reemplaza articulosData y contenedoresData)
  setContenedoresUnicos, // Setter del estado global de contenedores únicos
  preciosEXW, // Estado compartido de precios EXW
  setPreciosEXW, // Setter para precios EXW
  preciosAnclados, // Precio final anclado por line item (tipo de venta vigente)
  setPreciosAnclados, // Setter para precios anclados
  tiposIVA, // Estado compartido de tipos de IVA
  setTiposIVA, // Setter para tipos de IVA
  tiposIVASoportado, // Estado compartido de tipos de IVA soportado
  setTiposIVASoportado, // Setter para tipos de IVA soportado
}) => {
  // LOGS INICIALES
  console.log("[NegociacionTab] === COMPONENTE MONTADO/ACTUALIZADO ===");
  console.log("[NegociacionTab] clienteData.margen:", clienteData?.margen);
  console.log("[NegociacionTab] contenedoresUnicos:", contenedoresUnicos);

  // DATOS DE ARTÍCULOS DESDE CONTENEDORES ÚNICOS

  // Transformar artículos desde contenedoresUnicos a formato necesario para negociación
  // Se extraen artículos únicos de todos los contenedores para la vista agregada
  const articulosParaNegociacion = useMemo(() => {
    console.log("[NegociacionTab] Recalculando articulosParaNegociacion...");
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return [];
    }

    // Calcular el total de cajas por artículo sumando de todos los contenedores únicos
    const cajasPorArticulo = {};

    contenedoresUnicos.forEach((contenedor) => {
      if (
        contenedor.container_config?.articulos &&
        Array.isArray(contenedor.container_config.articulos)
      ) {
        contenedor.container_config.articulos.forEach((art) => {
          const id = art.articuloId;
          if (!cajasPorArticulo[id]) {
            cajasPorArticulo[id] = 0;
          }
          cajasPorArticulo[id] += art.numCajas || 0;
        });
      }
    });

    // Crear un mapa de artículos únicos agregando datos de todos los contenedores
    const articulosMap = new Map();

    contenedoresUnicos.forEach((contenedor) => {
      if (
        contenedor.container_config?.articulos &&
        Array.isArray(contenedor.container_config.articulos)
      ) {
        contenedor.container_config.articulos.forEach((art) => {
          const articuloId = art.articuloId;

          if (!articulosMap.has(articuloId)) {
            const costePorCaja = art.precio_de_coste || 0;
            const margenInicial =
              art.margen_unitario != null && art.margen_unitario > 0
                ? art.margen_unitario * 100
                : (clienteData?.margen ?? 12);

            articulosMap.set(articuloId, {
              id: articuloId,
              nombre: art.nombre,
              cajas: cajasPorArticulo[articuloId] || 0,
              costePorCaja: costePorCaja,
              margen: margenInicial,
              tipo_iva_repercutido: art.tipo_iva_repercutido || null,
              tipo_iva_soportado: art.tipo_iva_soportado || null,
              priceEXW: (art.price != null && art.price !== "") ? Number(art.price) : null,
              hasMargenUnitario: art.margen_unitario != null, // Flag: ¿tiene margen_unitario guardado?
              // Promoción = línea creada como regalo (precio persistido 0). Se mira el
              // dato guardado, no el precio editable, para no convertir en promo una
              // línea cuyo precio se borra momentáneamente para reescribirlo.
              esPromo: art.price != null && art.price !== "" && Number(art.price) === 0,
            });
          }
        });
      }
    });

    const result = Array.from(articulosMap.values());
    console.log("[NegociacionTab] articulosParaNegociacion:", result);
    return result;
  }, [contenedoresUnicos, clienteData]);

  // Total de contenedores del pedido (cada contenedor único ya cuenta como 1)
  const totalContenedores = useMemo(() => {
    if (!contenedoresUnicos || !Array.isArray(contenedoresUnicos)) {
      return 0;
    }
    return contenedoresUnicos.length;
  }, [contenedoresUnicos]);

  // Total de cajas
  const totalCajas = useMemo(
    () => articulosParaNegociacion.reduce((sum, art) => sum + art.cajas, 0),
    [articulosParaNegociacion]
  );

  // Servicios totales
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

  // Importes a nivel de contenedor (sin repartir entre cajas). Es el `base`
  // que consumen buildPricingParamsPorContenedor y calcularTotalesPorTipoVenta:
  // cada contenedor reparte estos importes entre SUS propias cajas.
  const pricingBase = useMemo(() => {
    const servicios = serviciosData?.serviciosSeleccionados || [];
    const serviciosImporte = servicios.reduce(
      (total, s) => total + (Number(s.importe) || 0),
      0
    );
    const seguroImporte = servicios
      .filter((s) => s.tipoArticulo === "seguro")
      .reduce((sum, s) => sum + (Number(s.importe) || 0), 0);

    const serviciosBuckets = {};
    const seguroBuckets = {};
    servicios.forEach((s) => {
      const code = (s.tipo_iva || "").toString().trim().toUpperCase();
      const importe = Number(s.importe) || 0;
      const target = s.tipoArticulo === "seguro" ? seguroBuckets : serviciosBuckets;
      target[code] = (target[code] || 0) + importe;
    });

    const importeComision =
      clienteData?.importeComision?.value ?? clienteData?.importeComision;
    const tipoComision =
      clienteData?.tipoComision?.value ?? clienteData?.tipoComision;
    const repercutir =
      (clienteData?.repercutirACliente?.value ??
        clienteData?.repercutirACliente) === "si";

    return {
      precioOrigen: Number(transitarioData?.precioOrigen) || 0,
      precioDestino: Number(transitarioData?.precioDestino) || 0,
      serviciosImporte,
      seguroImporte,
      serviciosBuckets,
      seguroBuckets,
      importeComision,
      tipoComision,
      repercutir,
      tieneIntermediario: !!clienteData?.codigoIntermediario,
      numContenedores: Array.isArray(contenedoresUnicos)
        ? contenedoresUnicos.length
        : 0,
    };
  }, [transitarioData, serviciosData, clienteData, contenedoresUnicos]);

  // Modo doble flete (cliente nacional + camión + CIF/CFR): flete terrestre y marítimo con transitario/IVA propios
  const modoDoble = esModoDobleFlete({
    fleteDobleDisponible: transitarioData?.fleteDobleDisponible,
    tipoVenta: clienteData?.tipoVenta,
    tipoCliente: clienteData?.tipoCliente,
  });

  // ESTADO

  // NOTA: preciosEXW, setPreciosEXW, tiposIVA, setTiposIVA ahora vienen como props compartidos

  // Estado de contenedor único seleccionado
  const [contenedorUnicoSeleccionado, setContenedorUnicoSeleccionado] =
    useState(null);

  const [batchSelectedArticulos, setBatchSelectedArticulos] = useState([]);   // line_item_id[] seleccionados para batch
  const [batchNuevoPrecio, setBatchNuevoPrecio] = useState(null);             // null = no cambiar
  const [batchNuevoIVA, setBatchNuevoIVA] = useState("");                      // "" = no cambiar
  const [batchNuevoIVASoportado, setBatchNuevoIVASoportado] = useState("");    // "" = no cambiar

  // Estado de carga y errores
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [isLoadingInitial, setIsLoadingInitial] = useState(true);

  // Estado del job publish-custom-objects (para bloquear el botón Editar contenedor)
  const [customObjectsJobStatus, setCustomObjectsJobStatus] = useState(null);
  const isCustomObjectsJobBusy =
    customObjectsJobStatus === "pending" ||
    customObjectsJobStatus === "processing";

  // Obtener el contenedor seleccionado
  const contenedorSeleccionado = useMemo(() => {
    if (
      !contenedorUnicoSeleccionado ||
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos)
    ) {
      return null;
    }
    return contenedoresUnicos.find(
      (c) => c.line_item_id === contenedorUnicoSeleccionado
    );
  }, [contenedorUnicoSeleccionado, contenedoresUnicos]);

  // Cajas del contenedor seleccionado (denominador del reparto intra-contenedor)
  const cajasContenedorSeleccionado = useMemo(() => {
    const arts = contenedorSeleccionado?.container_config?.articulos || [];
    return arts.reduce((sum, art) => sum + (art.numCajas || 0), 0);
  }, [contenedorSeleccionado]);

  // Artículos únicos del contenedor seleccionado para la tabla
  const articulosUnicosParaNegociacion = useMemo(() => {
    console.log("[NegociacionTab] Recalculando articulosUnicosParaNegociacion...");
    if (!contenedorSeleccionado || !contenedorSeleccionado.container_config) {
      return [];
    }

    const articulos = contenedorSeleccionado.container_config.articulos || [];

    return articulos.map((art) => {
      // precio_de_coste es el coste del proveedor (inmutable). Sin fallback a price.
      const costePorCaja = art.precio_de_coste || 0;

      // Usar margen_unitario del artículo (si es > 0), o el margen del cliente como fallback
      // Si margen_unitario es 0 o null/undefined, usar clienteData.margen
      const margenInicial =
        art.margen_unitario != null && art.margen_unitario > 0
          ? art.margen_unitario * 100
          : (clienteData?.margen ?? 12);

      // Un margen previo existe si: margen_unitario > 0 (no contamos 0 como un margen previo)
      const hasMargenUnitario = art.margen_unitario != null && art.margen_unitario > 0;
      const priceEXW = (art.price != null && art.price !== "") ? Number(art.price) : null;

      console.log(`[NegociacionTab] Artículo: ${art.articuloName}`, {
        margen_unitario: art.margen_unitario,
        hasMargenUnitario,
        priceEXW,
        costePorCaja,
        margenInicial,
      });

      return {
        id: art.line_item_id, // HubSpot line item ID del artículo
        articuloId: art.articuloId, // ID original del artículo (SKU)
        nombre: art.articuloName, // Usar el nombre único
        cajas: art.numCajas,
        costePorCaja: costePorCaja,
        margen: margenInicial,
        tipo_iva_repercutido: art.tipo_iva_repercutido || null,
        tipo_iva_soportado: art.tipo_iva_soportado || null,
        priceEXW,
        hasMargenUnitario, // Flag: ¿tiene margen_unitario guardado?
        // Promoción = línea creada como regalo (precio persistido 0). Se mira el
        // dato guardado, no el precio editable, para no convertir en promo una
        // línea cuyo precio se borra momentáneamente para reescribirlo.
        esPromo: art.price != null && art.price !== "" && Number(art.price) === 0,
      };
    });
  }, [contenedorSeleccionado, clienteData]);

  // Verificar si todos los datos necesarios están disponibles
  useEffect(() => {
    // Verificar que todos los datos críticos existan y no estén vacíos
    const hasClienteData = clienteData && Object.keys(clienteData).length > 0;
    const hasContenedores =
      contenedoresUnicos &&
      Array.isArray(contenedoresUnicos) &&
      contenedoresUnicos.length > 0;
    const hasTransitario =
      transitarioData && Object.keys(transitarioData).length > 0;
    const hasServicios = serviciosData && Object.keys(serviciosData).length > 0;

    // Todos los datos deben estar disponibles
    const allDataReady =
      hasClienteData && hasContenedores && hasTransitario && hasServicios;

    if (allDataReady && isLoadingInitial) {
      // Añadir un pequeño delay para asegurar que todo esté renderizado
      const timer = setTimeout(() => {
        setIsLoadingInitial(false);
      }, INITIAL_RENDER_DELAY_MS);

      return () => clearTimeout(timer);
    }
  }, [
    clienteData,
    contenedoresUnicos,
    transitarioData,
    serviciosData,
    isLoadingInitial,
  ]);

  // Seleccionar automáticamente el primer contenedor único al cargar
  // o resetear la selección si el contenedor seleccionado ya no existe
  useEffect(() => {
    if (
      contenedoresUnicos &&
      Array.isArray(contenedoresUnicos) &&
      contenedoresUnicos.length > 0
    ) {
      // Check if currently selected container still exists in the new list
      const selectedExists = contenedoresUnicos.find(
        (c) => c.line_item_id === contenedorUnicoSeleccionado
      );

      // If no selection or selected container doesn't exist anymore, select the first one
      if (!contenedorUnicoSeleccionado || !selectedExists) {
        setContenedorUnicoSeleccionado(contenedoresUnicos[0].line_item_id);
      }
    } else if (
      contenedoresUnicos &&
      Array.isArray(contenedoresUnicos) &&
      contenedoresUnicos.length === 0
    ) {
      // If containers list is empty, clear selection
      setContenedorUnicoSeleccionado(null);
    }
  }, [contenedoresUnicos, contenedorUnicoSeleccionado]);

  useEffect(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return;
    }

    const margenCliente = clienteData?.margen ?? 12;

    setPreciosEXW((prevPrecios) => {
      const newPrecios = { ...prevPrecios };
      contenedoresUnicos.forEach((contenedor) => {
        contenedor.container_config?.articulos?.forEach((art) => {
          const lineItemId = art.line_item_id;
          if (newPrecios[lineItemId] !== undefined) return;

          const costePorCaja = art.precio_de_coste || 0;
          const tieneMargenUnitario =
            art.margen_unitario != null && art.margen_unitario > 0;
          // Un margen negativo (descuento / línea promocional a 0€) también es
          // un precio negociado: hay que respetar el precio guardado, no recalcularlo.
          const tienePrecioNegociado =
            art.margen_unitario != null && art.margen_unitario !== 0;
          const priceEXW =
            art.price != null && art.price !== "" ? Number(art.price) : null;

          if (priceEXW != null && (tienePrecioNegociado || priceEXW === 0)) {
            newPrecios[lineItemId] = priceEXW;
          } else {
            const margenInicial = tieneMargenUnitario
              ? art.margen_unitario * 100
              : margenCliente;
            newPrecios[lineItemId] =
              costePorCaja + (costePorCaja * margenInicial) / 100;
          }
        });
      });
      return newPrecios;
    });

    setTiposIVA((prevTipos) => {
      const newTipos = { ...prevTipos };
      contenedoresUnicos.forEach((contenedor) => {
        contenedor.container_config?.articulos?.forEach((art) => {
          const lineItemId = art.line_item_id;
          if (newTipos[lineItemId] !== undefined) return;
          newTipos[lineItemId] =
            art.tipo_iva_repercutido ||
            getDefaultIVAByResidencia(clienteData?.tipoResidente, tipoIVAOptions);
        });
      });
      return newTipos;
    });

    setTiposIVASoportado((prevTipos) => {
      const newTipos = { ...prevTipos };
      contenedoresUnicos.forEach((contenedor) => {
        contenedor.container_config?.articulos?.forEach((art) => {
          const lineItemId = art.line_item_id;
          if (newTipos[lineItemId] !== undefined) return;
          newTipos[lineItemId] =
            art.tipo_iva_soportado ||
            getDefaultIVAByResidencia(
              clienteData?.tipoResidente,
              tipoIVASoportadoOptions,
              "soportado"
            );
        });
      });
      return newTipos;
    });
  }, [contenedoresUnicos, clienteData, tipoIVAOptions, tipoIVASoportadoOptions]);

  // CÁLCULOS

  // Parámetros de pricing del contenedor seleccionado: flete/servicios/seguro/
  // comisión repartidos entre SUS cajas (reparto intra-contenedor).
  const pricingParams = useMemo(
    () => buildPricingParamsPorContenedor(pricingBase, cajasContenedorSeleccionado),
    [pricingBase, cajasContenedorSeleccionado]
  );

  // Derivados del pricingParams del contenedor seleccionado.
  const gastosPorTramo = pricingParams.gastosPorTramo;
  const serviciosPorCaja = pricingParams.serviciosPorCaja;
  const seguroPorCaja = pricingParams.seguroPorCaja;
  const serviciosNoSeguroPorIva = pricingParams.serviciosNoSeguroPorIva;
  const seguroPorIva = pricingParams.seguroPorIva;

  // Comisión por caja del intermediario para el contenedor seleccionado
  // (reparto intra-contenedor). El gate de repercutir se aplica en los usos.
  const comisionPorCaja = useMemo(() => {
    const importeComision =
      clienteData?.importeComision?.value ?? clienteData?.importeComision;
    const tipoComision =
      clienteData?.tipoComision?.value ?? clienteData?.tipoComision;
    if (!clienteData?.codigoIntermediario) return 0;
    return comisionPorCajaIntra(
      tipoComision,
      importeComision,
      cajasContenedorSeleccionado,
      totalContenedores
    );
  }, [clienteData, cajasContenedorSeleccionado, totalContenedores]);

  // CÁLCULOS DE PRECIO POR TIPO DE VENTA

  // Tipo de venta seleccionado normalizado a la clave canónica del incoterm
  const tipoVentaSeleccionado = useMemo(
    () => normalizeIncoterm(clienteData?.tipoVenta?.value || clienteData?.tipoVenta),
    [clienteData]
  );

  // Valor de repercutirACliente normalizado
  const repercutirAClienteValue = useMemo(() => {
    return (
      clienteData?.repercutirACliente?.value || clienteData?.repercutirACliente
    );
  }, [clienteData]);

  // Convierte EXW → Precio final visible al cliente para el tipo de venta seleccionado.
  const calcularPrecioFinalPorTipoVenta = useCallback(
    (precioEXW) =>
      calcularPrecioPorCajaPorIncoterm(precioEXW, tipoVentaSeleccionado, pricingParams),
    [tipoVentaSeleccionado, pricingParams]
  );

  // Back-calculation: Precio final → EXW. Función inversa exacta de calcularPrecioFinalPorTipoVenta.
  const calcularPrecioEXWDesdeFinal = useCallback(
    (precioFinal) =>
      calcularPrecioEXWDesdeIncoterm(precioFinal, tipoVentaSeleccionado, pricingParams),
    [tipoVentaSeleccionado, pricingParams]
  );

  // ANCLA DEL PRECIO DEL TIPO DE VENTA
  // El precio final tecleado queda fijo; el EXW/margen se derivan de él.
  // Las anclas locales valen solo para el incoterm vigente: al cambiar el tipo
  // de venta se vacían (el EXW manda y el final se re-deriva y re-ancla al guardar).
  const prevTipoVentaRef = useRef(tipoVentaSeleccionado);
  useEffect(() => {
    if (prevTipoVentaRef.current === tipoVentaSeleccionado) return;
    prevTipoVentaRef.current = tipoVentaSeleccionado;
    setPreciosAnclados({});
  }, [tipoVentaSeleccionado, setPreciosAnclados]);

  // Hidratación de anclas persistidas (solo las fijadas bajo el incoterm vigente;
  // las de otro incoterm las re-ancla el backend en el siguiente recálculo).
  useEffect(() => {
    if (!contenedoresUnicos || !Array.isArray(contenedoresUnicos)) return;
    setPreciosAnclados((prev) => {
      let changed = false;
      const next = { ...prev };
      contenedoresUnicos.forEach((contenedor) => {
        contenedor.container_config?.articulos?.forEach((art) => {
          const lid = art.line_item_id;
          if (next[lid] !== undefined) return;
          const ancla = Number(art.precio_negociado_tipo_venta);
          if (!(ancla > 0)) return;
          if (normalizeIncoterm(art.incoterm_precio_negociado) !== tipoVentaSeleccionado) return;
          next[lid] = ancla;
          changed = true;
        });
      });
      return changed ? next : prev;
    });
  }, [contenedoresUnicos, tipoVentaSeleccionado, setPreciosAnclados]);

  // Reconciliador: mantiene preciosEXW = inversa(ancla, gastos del momento) con
  // suelo en el coste (margen 0, nunca bajo coste). Así todos los consumidores
  // (márgenes, totales, RevisionTab, publish) ven el EXW que absorbe los cambios
  // de cajas/gastos mientras el precio final anclado permanece fijo.
  useEffect(() => {
    if (!contenedoresUnicos || !Array.isArray(contenedoresUnicos)) return;
    setPreciosEXW((prev) => {
      let changed = false;
      const next = { ...prev };
      contenedoresUnicos.forEach((contenedor) => {
        const arts = contenedor.container_config?.articulos || [];
        const cajasCont = arts.reduce((sum, a) => sum + (a.numCajas || 0), 0);
        const paramsCont = buildPricingParamsPorContenedor(pricingBase, cajasCont);
        arts.forEach((art) => {
          const lid = art.line_item_id;
          const ancla = preciosAnclados[lid];
          if (ancla == null || ancla <= 0) return;
          const coste = art.precio_de_coste || 0;
          const exw = Math.max(
            coste,
            calcularPrecioEXWDesdeIncoterm(ancla, tipoVentaSeleccionado, paramsCont)
          );
          if (next[lid] !== exw) {
            next[lid] = exw;
            changed = true;
          }
        });
      });
      return changed ? next : prev;
    });
  }, [
    preciosAnclados,
    pricingBase,
    contenedoresUnicos,
    tipoVentaSeleccionado,
    setPreciosEXW,
  ]);

  // Calcular precio EXW por caja de cada artículo
  const articulosConPrecios = useMemo(() => {
    const articulosBase = contenedorUnicoSeleccionado
      ? articulosUnicosParaNegociacion
      : articulosParaNegociacion;

    return articulosBase.map((art) => {
      // Precio unitario de la caja (coste)
      const precioCaja = art.costePorCaja;

      // Precio EXW establecido por el comercial - ESTE ES SIEMPRE EL PRECIO FINAL AL CLIENTE
      const precioEXWFinal =
        preciosEXW[art.id] != null ? preciosEXW[art.id] : precioCaja;

      // Calcular el margen bruto en euros (precio - coste)
      const margenEnEuros = precioEXWFinal - precioCaja;

      // Calcular el porcentaje del margen bruto
      const margenActual =
        precioCaja > 0 ? (margenEnEuros / precioCaja) * 100 : 0;

      // Calcular margen efectivo según si repercute o no la comisión
      let margenEfectivo = margenActual;
      let margenEfectivoEnEuros = margenEnEuros;

      // Aplicar comisión de intermediario
      if (comisionPorCaja > 0) {
        // Extraer el valor si es un objeto {value, options}
        const repercutirAClienteValue =
          clienteData?.repercutirACliente?.value ||
          clienteData?.repercutirACliente;
        if (repercutirAClienteValue === "si") {
          // Si repercute al cliente: el margen efectivo es el mismo (cliente paga la comisión)
          margenEfectivo = margenActual;
          margenEfectivoEnEuros = margenEnEuros;
        } else if (repercutirAClienteValue === "no") {
          // Si NO repercute: la comisión se resta del margen (empresa la asume)
          const comisionComoMargen = (comisionPorCaja / precioCaja) * 100;
          margenEfectivo = margenActual - comisionComoMargen;
          margenEfectivoEnEuros = margenEnEuros - comisionPorCaja;
        }
      }

      // % IVA efectivo = % IVA + % recargo equivalencia si la company lo tiene (equ === "si")
      const tipoIVAArticulo = tiposIVA[art.id] || art.tipo_iva_repercutido;
      const { iva: ivaBase = 0, recargo: recargoBase = 0 } =
        getPorcentajesIVA(tipoIVAArticulo, ivaData) || {};
      const aplicaRE = (clienteData?.equ || "").toString().toLowerCase() === "si";
      const ivaPorcentaje = ivaBase + (aplicaRE ? recargoBase : 0);

      // La condición de promoción es persistida (viene del artículo guardado), no
      // del precio editable: borrar el precio para reescribirlo deja precioEXWFinal
      // en 0 transitoriamente y NO debe marcar la línea como promoción.
      const esPromo = art.esPromo === true;

      // Con ancla vigente, el precio mostrado es el negociado (fijo), salvo que
      // no cubra coste + gastos: entonces sube al mínimo (margen 0) y se avisa.
      const anclaFinal = preciosAnclados[art.id];
      const anclada = !esPromo && anclaFinal != null && anclaFinal > 0;
      let anclaClamped = false;
      let precioFinalEditable = esPromo
        ? 0
        : Math.round(calcularPrecioFinalPorTipoVenta(precioEXWFinal) * 1000) / 1000;
      if (anclada) {
        anclaClamped = calcularPrecioEXWDesdeFinal(anclaFinal) < precioCaja;
        precioFinalEditable = anclaClamped
          ? Math.round(calcularPrecioFinalPorTipoVenta(precioCaja) * 1000) / 1000
          : anclaFinal;
      }

      return {
        ...art,
        esPromo,
        anclaClamped,
        margenActual,
        margenEnEuros,
        margenEfectivo,
        margenEfectivoEnEuros,
        precioPorCaja: esPromo
          ? 0
          : calcularPrecioPorCajaPorIncoterm(precioEXWFinal, "EXW", pricingParams),
        precioFinalEditable,
        ivaPorcentaje,
      };
    });
  }, [
    contenedorUnicoSeleccionado,
    articulosUnicosParaNegociacion,
    articulosParaNegociacion,
    preciosEXW,
    preciosAnclados,
    comisionPorCaja,
    clienteData,
    tiposIVA,
    ivaData,
    calcularPrecioFinalPorTipoVenta,
    calcularPrecioEXWDesdeFinal,
    pricingParams,
  ]);

  // Calcular totales para el footer de la tabla
  const totalesContenedorSeleccionado = useMemo(() => {
    if (!articulosConPrecios || articulosConPrecios.length === 0) {
      return {
        totalCajas: 0,
        totalCoste: 0,
        totalComision: 0,
        totalMargenEfectivoEnEuros: 0,
        totalPrecioFinal: 0,
      };
    }

    const totalCajas = articulosConPrecios.reduce(
      (sum, art) => sum + art.cajas,
      0
    );
    const totalCoste = articulosConPrecios.reduce(
      (sum, art) => sum + art.costePorCaja * art.cajas,
      0
    );
    const totalComision = comisionPorCaja * totalCajas;
    const totalMargenEfectivoEnEuros = articulosConPrecios.reduce(
      (sum, art) => sum + art.margenEfectivoEnEuros * art.cajas,
      0
    );
    const totalPrecioFinal = articulosConPrecios.reduce(
      (sum, art) => sum + art.precioFinalEditable * art.cajas,
      0
    );

    return {
      totalCajas,
      totalCoste,
      totalComision,
      totalMargenEfectivoEnEuros,
      totalPrecioFinal,
    };
  }, [articulosConPrecios, comisionPorCaja]);

  // Calcular total con IVA para el contenedor seleccionado reutilizando calcularTotalesPorTipoVenta
  const totalesConIVAContenedorSeleccionado = useMemo(() => {
    if (!contenedorSeleccionado) return {};
    return calcularTotalesPorTipoVenta([contenedorSeleccionado], preciosEXW, pricingBase, {
      clienteData,
      tiposIVA,
      ivaData,
      getPorcentajesIVA,
      incotermRef: tipoVentaSeleccionado,
      tipoIVAFletes: transitarioData?.tipoIVAFletes || "",
      tipoIVAFleteTerrestre: transitarioData?.tipoIVAFleteTerrestre || "",
      tipoIVAComision: clienteData?.tipoIVAComision || "",
    });
  }, [contenedorSeleccionado, preciosEXW, pricingBase, clienteData, tiposIVA, ivaData, tipoVentaSeleccionado, transitarioData]);

  // Calcular precios por tipo de venta GLOBALES (sin IVA y con IVA ponderado)
  // IMPORTANTE: Este cálculo SIEMPRE usa todos los artículos de todos los contenedores.
  // Usa calcularTotalesPorTipoVenta (utilidad compartida con RevisionTab).
  const preciosPorTipoVenta = useMemo(
    () =>
      calcularTotalesPorTipoVenta(contenedoresUnicos, preciosEXW, pricingBase, {
        clienteData,
        tiposIVA,
        ivaData,
        getPorcentajesIVA,
        incotermRef: tipoVentaSeleccionado,
        tipoIVAFletes: transitarioData?.tipoIVAFletes || "",
        tipoIVAFleteTerrestre: transitarioData?.tipoIVAFleteTerrestre || "",
        tipoIVAComision: clienteData?.tipoIVAComision || "",
      }),
    [contenedoresUnicos, preciosEXW, pricingBase, clienteData, tiposIVA, ivaData, tipoVentaSeleccionado, transitarioData]
  );

  // Calcular márgenes por cada contenedor único
  const margenesPorContenedor = useMemo(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return [];
    }

    const importeComision =
      clienteData?.importeComision?.value ?? clienteData?.importeComision;
    const tipoComision =
      clienteData?.tipoComision?.value ?? clienteData?.tipoComision;

    return contenedoresUnicos.map((contenedor) => {
      let totalMargenPonderado = 0;
      let totalMargenEnEuros = 0;
      let totalCajasContenedor = 0;

      const artsContenedor = Array.isArray(
        contenedor.container_config?.articulos
      )
        ? contenedor.container_config.articulos
        : [];
      const cajasDeEseContenedor = artsContenedor.reduce(
        (s, a) => s + (a.numCajas || 0),
        0
      );
      const comisionPorCajaContenedor = clienteData?.codigoIntermediario
        ? comisionPorCajaIntra(
            tipoComision,
            importeComision,
            cajasDeEseContenedor,
            totalContenedores
          )
        : 0;

      artsContenedor.forEach((artContenedor) => {
        const margenEfectivo = calcularMargenArticuloEnContenedor(
          artContenedor,
          preciosEXW,
          comisionPorCajaContenedor,
          clienteData
        );

        const cajasEnContenedor = artContenedor.numCajas || 0;
        totalMargenPonderado += margenEfectivo * cajasEnContenedor;
        totalCajasContenedor += cajasEnContenedor;

        const lineItemId = artContenedor.line_item_id;
        const costePorCaja = artContenedor.precio_de_coste || 0;
        const margenInicial =
          artContenedor.margen_unitario != null &&
          artContenedor.margen_unitario > 0
            ? artContenedor.margen_unitario * 100
            : (clienteData?.margen ?? 12);

        const precioEXWFinal =
          preciosEXW[lineItemId] != null
            ? preciosEXW[lineItemId]
            : costePorCaja + (costePorCaja * margenInicial) / 100;
        const margenEnEuros = precioEXWFinal - costePorCaja;

        let margenEfectivoEnEuros = margenEnEuros;
        const repercutirAClienteValue =
          clienteData?.repercutirACliente?.value ||
          clienteData?.repercutirACliente;
        if (comisionPorCajaContenedor > 0 && repercutirAClienteValue === "no") {
          margenEfectivoEnEuros = margenEnEuros - comisionPorCajaContenedor;
        }

        totalMargenEnEuros += margenEfectivoEnEuros * cajasEnContenedor;
      });

      const margenContenedor =
        totalCajasContenedor > 0
          ? totalMargenPonderado / totalCajasContenedor
          : 0;
      const margenPorCajaEnEuros =
        totalCajasContenedor > 0
          ? totalMargenEnEuros / totalCajasContenedor
          : 0;
      const margenTotalEnEuros = totalMargenEnEuros; // Margen total del contenedor en euros

      return {
        id: contenedor.line_item_id,
        label: contenedor.container_name,
        tipo: contenedor.container_type || "N/A",
        margen: margenContenedor,
        margenPorCajaEnEuros: margenPorCajaEnEuros,
        margenTotalEnEuros: margenTotalEnEuros,
        cantidad: contenedor.cantidad || 1,
        cajas: totalCajasContenedor,
      };
    });
  }, [contenedoresUnicos, preciosEXW, clienteData, totalContenedores]);

  // Margen del negocio completo (promedio ponderado de todos los contenedores únicos)
  const margenNegocio = useMemo(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return 0;
    }

    let totalMargenPonderado = 0;
    let totalCajasNegocio = 0;

    const importeComision =
      clienteData?.importeComision?.value ?? clienteData?.importeComision;
    const tipoComision =
      clienteData?.tipoComision?.value ?? clienteData?.tipoComision;

    contenedoresUnicos.forEach((contenedor) => {
      const artsContenedor = Array.isArray(
        contenedor.container_config?.articulos
      )
        ? contenedor.container_config.articulos
        : [];
      const cajasDeEseContenedor = artsContenedor.reduce(
        (s, a) => s + (a.numCajas || 0),
        0
      );
      const comisionPorCajaContenedor = clienteData?.codigoIntermediario
        ? comisionPorCajaIntra(
            tipoComision,
            importeComision,
            cajasDeEseContenedor,
            totalContenedores
          )
        : 0;

      artsContenedor.forEach((artContenedor) => {
        const margenEfectivo = calcularMargenArticuloEnContenedor(
          artContenedor,
          preciosEXW,
          comisionPorCajaContenedor,
          clienteData
        );

        const cajasEnContenedor = artContenedor.numCajas || 0;
        totalMargenPonderado += margenEfectivo * cajasEnContenedor;
        totalCajasNegocio += cajasEnContenedor;
      });
    });

    return totalCajasNegocio > 0 ? totalMargenPonderado / totalCajasNegocio : 0;
  }, [contenedoresUnicos, preciosEXW, clienteData, totalContenedores]);

  // Margen del negocio en euros (total y por caja)
  const margenNegocioEnEuros = useMemo(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return { total: 0, porCaja: 0 };
    }

    let totalMargenEnEuros = 0;
    let totalCajasNegocio = 0;

    const importeComision =
      clienteData?.importeComision?.value ?? clienteData?.importeComision;
    const tipoComision =
      clienteData?.tipoComision?.value ?? clienteData?.tipoComision;

    contenedoresUnicos.forEach((contenedor) => {
      const artsContenedor = Array.isArray(
        contenedor.container_config?.articulos
      )
        ? contenedor.container_config.articulos
        : [];
      const cajasDeEseContenedor = artsContenedor.reduce(
        (s, a) => s + (a.numCajas || 0),
        0
      );
      const comisionPorCajaContenedor = clienteData?.codigoIntermediario
        ? comisionPorCajaIntra(
            tipoComision,
            importeComision,
            cajasDeEseContenedor,
            totalContenedores
          )
        : 0;

      artsContenedor.forEach((artContenedor) => {
        const lineItemId = artContenedor.line_item_id;
        const costePorCaja = artContenedor.precio_de_coste || 0;
        const margenInicial =
          artContenedor.margen_unitario != null &&
          artContenedor.margen_unitario > 0
            ? artContenedor.margen_unitario * 100
            : (clienteData?.margen ?? 12);

        const precioEXWFinal =
          preciosEXW[lineItemId] != null
            ? preciosEXW[lineItemId]
            : costePorCaja + (costePorCaja * margenInicial) / 100;
        const margenEnEuros = precioEXWFinal - costePorCaja;

        let margenEfectivoEnEuros = margenEnEuros;
        if (
          comisionPorCajaContenedor > 0 &&
          clienteData?.repercutirACliente === "no"
        ) {
          margenEfectivoEnEuros = margenEnEuros - comisionPorCajaContenedor;
        }

        const cajasEnContenedor = artContenedor.numCajas || 0;
        totalMargenEnEuros += margenEfectivoEnEuros * cajasEnContenedor;
        totalCajasNegocio += cajasEnContenedor;
      });
    });

    return {
      total: totalMargenEnEuros,
      porCaja:
        totalCajasNegocio > 0 ? totalMargenEnEuros / totalCajasNegocio : 0,
    };
  }, [contenedoresUnicos, preciosEXW, clienteData, totalContenedores]);

  // Deshabilitar el botón "Aplicar" si no hay artículos seleccionados o no hay ningún valor nuevo
  const isBatchApplyDisabled = useMemo(() => {
    if (batchSelectedArticulos.length === 0) return true;
    const hasPrice = batchNuevoPrecio !== null && batchNuevoPrecio !== undefined;
    return !hasPrice && batchNuevoIVA === "" && batchNuevoIVASoportado === "";
  }, [batchSelectedArticulos, batchNuevoPrecio, batchNuevoIVA, batchNuevoIVASoportado]);

  // Opciones para el select de contenedores únicos
  const contenedoresUnicosOptions = useMemo(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return [];
    }
    return contenedoresUnicos.map((contenedor) => ({
      label: contenedor.container_name,
      value: contenedor.line_item_id,
    }));
  }, [contenedoresUnicos]);

  // HANDLERS

  const handlePrecioChange = useCallback(
    (articuloId, nuevoPrecioFinal) => {
      const valor = parseFloat(nuevoPrecioFinal) || 0;
      const precioEXW = calcularPrecioEXWDesdeFinal(valor);
      // El final tecleado es el ancla; el reconciliador re-deriva el EXW con
      // suelo en el coste cuando cambian cajas/gastos.
      setPreciosAnclados((prev) => ({
        ...prev,
        [articuloId]: valor,
      }));
      setPreciosEXW((prev) => ({
        ...prev,
        [articuloId]: Math.max(0, precioEXW),
      }));
    },
    [calcularPrecioEXWDesdeFinal, setPreciosEXW, setPreciosAnclados]
  );

  const handleTipoIVAChange = useCallback((articuloId, nuevoTipoIVA) => {
    setTiposIVA((prev) => ({
      ...prev,
      [articuloId]: nuevoTipoIVA,
    }));
  }, []);

  const handleTipoIVASoportadoChange = useCallback((articuloId, nuevoTipoIVA) => {
    setTiposIVASoportado((prev) => ({
      ...prev,
      [articuloId]: nuevoTipoIVA,
    }));
  }, []);

  // Colectar todos los line_item_id que comparten articuloId en todos los contenedores
  const getLineItemIdsParaArticulo = useCallback((articuloId) => {
    const ids = [];
    contenedoresUnicos?.forEach((contenedor) => {
      contenedor.container_config?.articulos?.forEach((art) => {
        if (art.articuloId === articuloId) ids.push(art.line_item_id);
      });
    });
    return ids;
  }, [contenedoresUnicos]);

  // Seleccionar solo el line_item_id de esta fila (este contenedor)
  const handleSelectSolo = useCallback((lineItemId) => {
    setBatchSelectedArticulos((prev) =>
      prev.includes(lineItemId) ? prev : [...prev, lineItemId]
    );
  }, []);

  // Seleccionar el mismo artículo en todos los contenedores
  const handleSelectEnTodos = useCallback((articuloId) => {
    const ids = getLineItemIdsParaArticulo(articuloId);
    setBatchSelectedArticulos((prev) => {
      const merged = new Set(prev);
      ids.forEach((id) => merged.add(id));
      return Array.from(merged);
    });
  }, [getLineItemIdsParaArticulo]);

  // Deseleccionar el artículo (todas sus instancias)
  const handleDeseleccionarArticulo = useCallback((articuloId) => {
    const toRemove = new Set(getLineItemIdsParaArticulo(articuloId));
    setBatchSelectedArticulos((prev) => prev.filter((id) => !toRemove.has(id)));
  }, [getLineItemIdsParaArticulo]);

  // Seleccionar todos los artículos solo en el contenedor actual
  const handleSelectAllSolo = useCallback(() => {
    const currentIds = articulosConPrecios.map((art) => art.id);
    setBatchSelectedArticulos((prev) => {
      const merged = new Set(prev);
      currentIds.forEach((id) => merged.add(id));
      return Array.from(merged);
    });
  }, [articulosConPrecios]);

  // Seleccionar todos los artículos del contenedor actual en todos los contenedores
  const handleSelectAllTodos = useCallback(() => {
    const articuloIdsActuales = new Set(articulosConPrecios.map((art) => art.articuloId));
    const allIds = [];
    contenedoresUnicos?.forEach((contenedor) => {
      contenedor.container_config?.articulos?.forEach((art) => {
        if (articuloIdsActuales.has(art.articuloId)) allIds.push(art.line_item_id);
      });
    });
    setBatchSelectedArticulos((prev) => {
      const merged = new Set(prev);
      allIds.forEach((id) => merged.add(id));
      return Array.from(merged);
    });
  }, [articulosConPrecios, contenedoresUnicos]);

  // Limpiar toda la selección
  const handleDeseleccionarTodos = useCallback(() => {
    setBatchSelectedArticulos([]);
  }, []);

  const handleBatchApply = useCallback(() => {
    if (isBatchApplyDisabled) return;

    const lineItemIds = batchSelectedArticulos;
    if (lineItemIds.length === 0) return;

    if (batchNuevoPrecio !== null && batchNuevoPrecio !== undefined) {
      const precioEXW = Math.max(0, calcularPrecioEXWDesdeFinal(batchNuevoPrecio));
      setPreciosAnclados((prev) => {
        const next = { ...prev };
        lineItemIds.forEach((id) => { next[id] = batchNuevoPrecio; });
        return next;
      });
      setPreciosEXW((prev) => {
        const next = { ...prev };
        lineItemIds.forEach((id) => { next[id] = precioEXW; });
        return next;
      });
    }

    if (batchNuevoIVA !== "") {
      setTiposIVA((prev) => {
        const next = { ...prev };
        lineItemIds.forEach((id) => { next[id] = batchNuevoIVA; });
        return next;
      });
    }

    if (batchNuevoIVASoportado !== "") {
      setTiposIVASoportado((prev) => {
        const next = { ...prev };
        lineItemIds.forEach((id) => { next[id] = batchNuevoIVASoportado; });
        return next;
      });
    }

    setBatchSelectedArticulos([]);
    setBatchNuevoPrecio(null);
    setBatchNuevoIVA("");
    setBatchNuevoIVASoportado("");
  }, [
    isBatchApplyDisabled,
    batchSelectedArticulos,
    batchNuevoPrecio,
    batchNuevoIVA,
    batchNuevoIVASoportado,
    calcularPrecioEXWDesdeFinal,
    setPreciosEXW,
    setPreciosAnclados,
    setTiposIVA,
    setTiposIVASoportado,
  ]);

  // Polling ligero del estado del job publish-custom-objects para bloquear el botón Editar.
  // Ritmo adaptativo: 3s cuando el job está en vuelo (pending/processing), 15s en idle.
  useEffect(() => {
    const dealId = context?.crm?.objectId;
    if (!dealId) return;

    let cancelled = false;
    let timeoutId = null;

    const poll = async () => {
      if (cancelled) return;
      let status = null;
      try {
        const data = await fetchFromAPI(
          `/api/v1/deals/${dealId}/custom-objects/status`,
        );
        status = data?.data?.status || null;
      } catch (err) {
        status = null;
      }
      if (cancelled) return;
      setCustomObjectsJobStatus(status);
      const nextDelay =
        status === "pending" || status === "processing" ? 3000 : 15000;
      timeoutId = setTimeout(poll, nextDelay);
    };

    poll();
    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [context?.crm?.objectId]);

  // Handler invocado por la modal de edición tras el PATCH exitoso
  const handleContenedorActualizado = useCallback(
    (updatedContainer) => {
      if (!updatedContainer || !setContenedoresUnicos) return;
      setContenedoresUnicos((prev) => {
        const list = Array.isArray(prev?.contenedoresUnicos)
          ? prev.contenedoresUnicos
          : [];
        const next = list.map((c) =>
          c.line_item_id === updatedContainer.line_item_id
            ? updatedContainer
            : c,
        );
        return { ...prev, contenedoresUnicos: next };
      });
    },
    [setContenedoresUnicos],
  );

  // Handler para guardar la negociación
  const handleGuardarNegociacion = useCallback(async () => {
    try {
      setIsSubmitting(true);
      setSaveError(null);

      const dealId = context.crm.objectId;

      // Recolectar TODOS los artículos de TODOS los contenedores únicos
      const todosLosArticulos = [];

      if (contenedoresUnicos && Array.isArray(contenedoresUnicos)) {
        contenedoresUnicos.forEach((contenedor) => {
          if (
            contenedor.container_config?.articulos &&
            Array.isArray(contenedor.container_config.articulos)
          ) {
            const cajasCont = contenedor.container_config.articulos.reduce(
              (sum, a) => sum + (a.numCajas || 0),
              0
            );
            const paramsCont = buildPricingParamsPorContenedor(pricingBase, cajasCont);
            contenedor.container_config.articulos.forEach((art) => {
              const lineItemId = art.line_item_id;
              const costePorCaja = art.precio_de_coste || 0;

              // Obtener el precio EXW editado o calcular el inicial
              const margenInicial =
                art.margen_unitario != null && art.margen_unitario > 0
                  ? art.margen_unitario * 100
                  : (clienteData?.margen ?? 12);

              // Precio por line_item_id (cada línea es independiente; una línea
              // a 0€ debe persistir como 0, no recalcularse ni heredar de otra línea).
              const precioEXWFinal =
                preciosEXW[lineItemId] != null
                  ? preciosEXW[lineItemId]
                  : costePorCaja + (costePorCaja * margenInicial) / 100;
              const margenEnEuros = precioEXWFinal - costePorCaja;
              const margenActual =
                costePorCaja > 0 ? (margenEnEuros / costePorCaja) * 100 : 0;

              // Calcular margen efectivo considerando comisión
              let margenEfectivo = margenActual;
              // Extraer el valor si es un objeto {value, options}
              const repercutirAClienteValue =
                clienteData?.repercutirACliente?.value ||
                clienteData?.repercutirACliente;
              if (comisionPorCaja > 0 && repercutirAClienteValue === "no") {
                const comisionComoMargen =
                  (comisionPorCaja / costePorCaja) * 100;
                margenEfectivo = margenActual - comisionComoMargen;
              }

              // Ancla del tipo de venta: la tecleada, o el final mostrado (que es
              // lo que el comercial valida al guardar). Promo o precio 0 → sin
              // ancla (el backend la limpia).
              const esPromoLinea =
                art.price != null && art.price !== "" && Number(art.price) === 0;
              let anclaPayload = null;
              if (!esPromoLinea && precioEXWFinal > 0) {
                const anclaLocal = preciosAnclados[lineItemId];
                anclaPayload =
                  anclaLocal != null && anclaLocal > 0
                    ? anclaLocal
                    : Math.round(
                        calcularPrecioPorCajaPorIncoterm(
                          precioEXWFinal,
                          tipoVentaSeleccionado,
                          paramsCont
                        ) * 1000
                      ) / 1000;
              }

              // Siempre agregar el artículo (el backend validará si es necesario)
              todosLosArticulos.push({
                line_item_id: lineItemId,
                price: Math.round(precioEXWFinal * 1000000) / 1000000,
                margen_unitario: margenEfectivo / 100,
                tipo_iva_repercutido:
                  tiposIVA[lineItemId] ||
                  art.tipo_iva_repercutido ||
                  "",
                tipo_iva_soportado:
                  tiposIVASoportado[lineItemId] ||
                  art.tipo_iva_soportado ||
                  "",
                precio_negociado_tipo_venta: anclaPayload,
                incoterm_precio_negociado:
                  anclaPayload != null ? tipoVentaSeleccionado : null,
              });
            });
          }
        });
      }

      // Llamar al endpoint con TODOS los artículos
      const response = await fetchFromAPI(
        `/api/v1/deals/${dealId}/negociacion`,
        {
          method: "POST",
          body: { articulos: todosLosArticulos },
        }
      );

      if (response.success) {
        onNavigate("documentacion");
      } else {
        setSaveError(response.error || "Error al guardar la negociación");
      }
    } catch (error) {
      console.error("Error al guardar negociación:", error);
      setSaveError(
        "Error al guardar la negociación. Por favor, intente nuevamente."
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [
    context,
    contenedoresUnicos,
    preciosEXW,
    preciosAnclados,
    pricingBase,
    tipoVentaSeleccionado,
    tiposIVA,
    tiposIVASoportado,
    comisionPorCaja,
    clienteData,
    onNavigate,
    ivaData,
    calcularPrecioFinalPorTipoVenta,
  ]);

  // Formateo de moneda: 3 dec por defecto (por caja), pasar decimals=2 para totales
  const formatCurrency = useCallback((value, decimals = 3) => {
    return `${value.toLocaleString("es-ES", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}€`;
  }, []);

  // Formateo de porcentaje
  const formatPercent = useCallback((value) => {
    return `${value.toLocaleString("es-ES", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    })}%`;
  }, []);

  // Determinar qué tipos de venta deben mostrarse según el tipo seleccionado
  const tiposVentaVisibles = useMemo(() => {
    if (!clienteData || !clienteData.tipoVenta) {
      // Sin tipo de venta seleccionado: fallback a EXW (igual que el cálculo)
      return ["EXW"];
    }

    // Clave canónica del incoterm (sin acentos ni espacios)
    const tipoVentaNormalizado = normalizeIncoterm(
      clienteData.tipoVenta?.value || clienteData.tipoVenta
    );

    // DAP TERRESTRE: Solo mostrar DAP_TERRESTRE
    if (tipoVentaNormalizado === "DAP_TERRESTRE") {
      return ["DAP_TERRESTRE"];
    }

    // DAP Marítimo: Solo mostrar DAP_MARITIMO
    if (tipoVentaNormalizado === "DAP_MARITIMO") {
      return ["DAP_MARITIMO"];
    }

    // Jerarquía: EXW < FOB < CFR < CIF
    // Si se selecciona uno, mostrar ese y todos los inferiores
    const jerarquia = ["EXW", "FOB", "CFR", "CIF"];
    const indiceSeleccionado = jerarquia.findIndex(
      (tipo) => tipo === tipoVentaNormalizado
    );

    if (indiceSeleccionado !== -1) {
      // Mostrar desde EXW hasta el tipo seleccionado (inclusive)
      return jerarquia.slice(0, indiceSeleccionado + 1);
    }

    // Tipo fuera de los 6 incoterms conocidos: fallback a EXW
    return ["EXW"];
  }, [clienteData]);

  // RENDER

  // Wait only for initial delay, not for containers
  // This prevents permanent loading state when containers are recreated
  const hasContenedores =
    contenedoresUnicos &&
    Array.isArray(contenedoresUnicos) &&
    contenedoresUnicos.length > 0;

  // Show loading spinner only during initial delay
  if (isLoadingInitial) {
    return (
      <Box>
        <Heading>Negociación</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_NEGOCIACION}</Text>
        </Flex>
      </Box>
    );
  }

  // After initial delay, show alert if no containers configured
  if (!hasContenedores) {
    return (
      <Box>
        <Heading>Negociación</Heading>
        <Flex direction="column" gap="medium">
          <Alert title="Contenedores Requeridos" variant="warning">
            <Text>
              No hay contenedores configurados. Debe configurar al menos un
              contenedor en el paso anterior para poder realizar la negociación
              sobre los productos.
            </Text>
          </Alert>
          <ButtonRow>
            <Button
              variant="secondary"
              onClick={() => onNavigate("contenedores")}
            >
              Ir a Contenedores
            </Button>
          </ButtonRow>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Heading>Negociación</Heading>

      <Flex direction="column" gap="medium">
        <Text>
          Ajuste los márgenes por artículo para negociar con el cliente. Los
          precios se recalculan automáticamente.
        </Text>

        {/* ========== INFORMACIÓN FISCAL DEL CLIENTE ========== */}
        <Flex direction="row" justify="start" gap="medium">
          <Tile>
            <Flex direction="column" gap="small">
              <Heading>Información fiscal del cliente</Heading>
              <Divider size="small" />

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>
                  Tipo de Residente:
                </Text>
                <Text>{clienteData?.tipoResidente || "No especificado"}</Text>
              </Flex>

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>CIF:</Text>
                <Text>{clienteData?.cif || "No especificado"}</Text>
              </Flex>

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>
                  País de Operaciones:
                </Text>
                <Text>{clienteData?.paisOperaciones || "No especificado"}</Text>
              </Flex>
            </Flex>
          </Tile>
        </Flex>

        {articulosParaNegociacion.length > 0 &&
          articulosParaNegociacion.some((art) => art.costePorCaja === 0) && (
            <Alert title="Datos de costes pendientes" variant="warning">
              <Text>
                Algunos artículos no tienen coste por caja definido. Los
                cálculos de precio y margen requieren esta información del
                backend para funcionar correctamente.
              </Text>
            </Alert>
          )}

        {/* ========== AJUSTE DE MÁRGENES POR ARTÍCULO ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>
            Ajuste de Márgenes por Artículo
          </Text>

          {contenedoresUnicosOptions.length > 0 && (
            <Flex direction="column" gap="small">
              <Text variant="microcopy">
                Seleccione un contenedor para ajustar los precios de sus
                artículos:
              </Text>
              <Flex direction="row" gap="small" align="end">
                <Box flex={1}>
                  <Select
                    name="contenedor-unico-selector"
                    label="Contenedor"
                    placeholder="Seleccionar..."
                    value={contenedorUnicoSeleccionado || ""}
                    onChange={(value) => setContenedorUnicoSeleccionado(value)}
                    options={contenedoresUnicosOptions}
                  />
                </Box>
                {contenedorSeleccionado && (
                  <Button
                    variant="primary"
                    disabled={isCustomObjectsJobBusy}
                    overlay={
                      <Modal
                        id={EDITAR_CONTENEDOR_MODAL_ID}
                        title={`Editar contenedor ${contenedorSeleccionado.container_name || ""}`}
                        width="lg"
                      >
                        <EditarContenedorModal
                          actions={actions}
                          dealId={context?.crm?.objectId}
                          contenedor={contenedorSeleccionado}
                          contenedoresUnicos={contenedoresUnicos}
                          clienteData={clienteData}
                          articulosSeleccionados={articulosSeleccionados}
                          kitsMarcados={kitsMarcados}
                          onCambiarMarcaKit={onCambiarMarcaKit}
                          kitsSolicitados={kitsSolicitados}
                          onChangeSolicitudKit={onChangeSolicitudKit}
                          onClose={() =>
                            actions &&
                            actions.closeOverlay(EDITAR_CONTENEDOR_MODAL_ID)
                          }
                          onUpdated={(updated) => {
                            handleContenedorActualizado(updated);
                          }}
                        />
                      </Modal>
                    }
                  >
                    Editar contenedor
                  </Button>
                )}
              </Flex>
              {isCustomObjectsJobBusy && (
                <Text variant="microcopy">
                  Publicación de objetos en curso. La edición se habilitará al
                  finalizar.
                </Text>
              )}
            </Flex>
          )}

          {articulosConPrecios.length === 0 ? (
            <EmptyState
              title="No hay artículos para negociar"
              layout="vertical"
            >
              <Text>
                Debe completar los pasos anteriores (Artículos y Contenedores)
                para poder ajustar márgenes y negociar precios.
              </Text>
            </EmptyState>
          ) : (
            <>
              <Table bordered={true}>
                <TableHead>
                  <TableRow>
                    <TableHeader width="auto">
                      <Dropdown
                        buttonText="+"
                        variant="transparent"
                        buttonSize="xs"
                      >
                        <Dropdown.ButtonItem onClick={handleSelectAllSolo}>
                          Seleccionar todos (este contenedor)
                        </Dropdown.ButtonItem>
                        {contenedoresUnicos.length > 1 && (
                          <Dropdown.ButtonItem onClick={handleSelectAllTodos}>
                            Seleccionar todos (todos los contenedores)
                          </Dropdown.ButtonItem>
                        )}
                        {batchSelectedArticulos.length > 0 && (
                          <Dropdown.ButtonItem onClick={handleDeseleccionarTodos}>
                            Deseleccionar todos
                          </Dropdown.ButtonItem>
                        )}
                      </Dropdown>
                    </TableHeader>
                    <TableHeader width="auto">Artículo</TableHeader>
                    <TableHeader width="auto">Cajas</TableHeader>
                    <TableHeader width="auto">Coste/Caja</TableHeader>
                    <TableHeader width="auto">Comisión/Caja</TableHeader>
                    <TableHeader width="auto">Margen Efectivo</TableHeader>
                    <TableHeader width="auto">
                      Precio/Caja (
                      {TIPOS_VENTA[tipoVentaSeleccionado]?.label ||
                        tipoVentaSeleccionado}
                      )
                    </TableHeader>
                    <TableHeader width="auto">IVA</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {articulosConPrecios.map((art) => (
                    <TableRow key={art.id}>
                      <TableCell width="auto">
                        <Dropdown
                          buttonText={batchSelectedArticulos.includes(art.id) ? "✓" : "+"}
                          variant="transparent"
                          buttonSize="xs"
                        >
                          {batchSelectedArticulos.includes(art.id) ? (
                            <Dropdown.ButtonItem onClick={() => handleDeseleccionarArticulo(art.articuloId)}>
                              Deseleccionar
                            </Dropdown.ButtonItem>
                          ) : (
                            <>
                              <Dropdown.ButtonItem onClick={() => handleSelectSolo(art.id)}>
                                Seleccionar solo en este contenedor
                              </Dropdown.ButtonItem>
                              {contenedoresUnicos.length > 1 && (
                                <Dropdown.ButtonItem onClick={() => handleSelectEnTodos(art.articuloId)}>
                                  Seleccionar en todos los contenedores
                                </Dropdown.ButtonItem>
                              )}
                            </>
                          )}
                        </Dropdown>
                      </TableCell>
                      <TableCell width="auto">
                        <Flex direction="column" gap="extra-small">
                          <Text>{art.nombre}</Text>
                          {art.esPromo && <Tag variant="success">Promoción</Tag>}
                        </Flex>
                      </TableCell>
                      <TableCell width="auto">{art.cajas}</TableCell>
                      <TableCell width="auto">{formatCurrency(art.costePorCaja)}</TableCell>
                      <TableCell width="auto">
                        {comisionPorCaja > 0 ? (
                          <Flex direction="column" gap="extra-small">
                            <Text format={{ fontWeight: "bold" }}>
                              {formatCurrency(comisionPorCaja)}
                            </Text>
                            <Text>
                              {(() => {
                                const repercutirAClienteValue =
                                  clienteData?.repercutirACliente?.value ||
                                  clienteData?.repercutirACliente;
                                return repercutirAClienteValue === "si" ? (
                                  <Tag variant="success">+ Cliente</Tag>
                                ) : (
                                  <Tag variant="warning">- Margen</Tag>
                                );
                              })()}
                            </Text>
                          </Flex>
                        ) : (
                          <Text>-</Text>
                        )}
                      </TableCell>
                      <TableCell width="auto">
                        <Flex direction="column" gap="extra-small">
                          <Text format={{ fontWeight: "bold" }}>
                            {formatPercent(art.margenEfectivo)}
                          </Text>
                          <Text>{formatCurrency(art.margenEfectivoEnEuros)}</Text>
                        </Flex>
                      </TableCell>
                      <TableCell width="auto">
                        {art.esPromo ? (
                          <Flex direction="column" gap="extra-small">
                            <Text format={{ fontWeight: "bold" }}>
                              {formatCurrency(0)}
                            </Text>
                            <Text variant="microcopy">Promoción · no editable</Text>
                          </Flex>
                        ) : (
                          <Flex direction="column" gap="extra-small">
                            <StepperInput
                              name={`precio-${art.id}`}
                              value={art.precioFinalEditable || 0}
                              onChange={(value) => handlePrecioChange(art.id, value)}
                              stepSize={0.001}
                              min={0}
                              precision={3}
                              formatStyle="decimal"
                              minValueReachedTooltip="El precio no puede ser negativo"
                            />
                            {!["EXW", "FCA"].includes(tipoVentaSeleccionado) && (
                              <Text variant="microcopy">
                                EXW: {formatCurrency(art.precioPorCaja)}
                              </Text>
                            )}
                            {art.anclaClamped && (
                              <Text variant="microcopy" format={{ fontWeight: "bold" }}>
                                ⚠️ Precio mínimo aplicado (margen 0): el negociado no
                                cubre coste + gastos
                              </Text>
                            )}
                          </Flex>
                        )}
                      </TableCell>
                      <TableCell width="auto">
                        <Flex direction="column" gap="small">
                          <Flex direction="column" gap="extra-small">
                            <Text variant="microcopy" format={{ fontWeight: "demibold" }}>Repercutido</Text>
                            <Select
                              name={`tipo-iva-${art.id}`}
                              value={tiposIVA[art.id] || ""}
                              onChange={(value) => handleTipoIVAChange(art.id, value)}
                              options={tipoIVAOptions}
                            />
                          </Flex>
                          <Flex direction="column" gap="extra-small">
                            <Text variant="microcopy" format={{ fontWeight: "demibold" }}>Soportado</Text>
                            <Select
                              name={`tipo-iva-sop-${art.id}`}
                              value={tiposIVASoportado[art.id] || ""}
                              onChange={(value) => handleTipoIVASoportadoChange(art.id, value)}
                              options={tipoIVASoportadoOptions}
                            />
                          </Flex>
                        </Flex>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow>
                    <TableCell />
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>TOTALES</Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {totalesContenedorSeleccionado.totalCajas}
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {formatCurrency(totalesContenedorSeleccionado.totalCoste, 2)}
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {totalesContenedorSeleccionado.totalComision > 0
                          ? formatCurrency(totalesContenedorSeleccionado.totalComision, 2)
                          : "-"}
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {formatCurrency(totalesContenedorSeleccionado.totalMargenEfectivoEnEuros, 2)}
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {formatCurrency(totalesContenedorSeleccionado.totalPrecioFinal, 2)}
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text format={{ fontWeight: "bold" }}>
                        {formatCurrency(totalesConIVAContenedorSeleccionado[`${bucketIncoterm(tipoVentaSeleccionado)}_IVA`] ?? 0, 2)}
                      </Text>
                    </TableCell>
                  </TableRow>
                </TableFooter>
              </Table>

              {/* ========== EDICIÓN MASIVA ========== */}
              {batchSelectedArticulos.length > 0 && (
                <Tile>
                  <Flex direction="column" gap="medium">
                    <Text format={{ fontWeight: "bold" }}>
                      Edición Masiva ({batchSelectedArticulos.length} artículo{batchSelectedArticulos.length > 1 ? "s" : ""} seleccionado{batchSelectedArticulos.length > 1 ? "s" : ""})
                    </Text>
                    <Divider size="small" />

                    <Flex direction="row" gap="medium">
                      <Flex direction="column" gap="extra-small">
                        <Text variant="microcopy" format={{ fontWeight: "demibold" }}>
                          Precio/Caja ({TIPOS_VENTA[tipoVentaSeleccionado]?.label || tipoVentaSeleccionado})
                        </Text>
                        <NumberInput
                          name="batch-precio"
                          label="Nuevo precio/caja"
                          value={batchNuevoPrecio ?? undefined}
                          onChange={(value) => setBatchNuevoPrecio(value)}
                          min={0}
                          precision={3}
                          formatStyle="decimal"
                          placeholder=""
                        />
                      </Flex>

                      <Flex direction="column" gap="extra-small">
                        <Text variant="microcopy" format={{ fontWeight: "demibold" }}>IVA Repercutido</Text>
                        <Select
                          name="batch-iva-rep"
                          label="IVA Repercutido"
                          placeholder="Seleccionar..."
                          value={batchNuevoIVA}
                          onChange={(value) => setBatchNuevoIVA(value)}
                          options={tipoIVAOptions}
                        />
                      </Flex>

                      <Flex direction="column" gap="extra-small">
                        <Text variant="microcopy" format={{ fontWeight: "demibold" }}>IVA Soportado</Text>
                        <Select
                          name="batch-iva-sop"
                          label="IVA Soportado"
                          placeholder="Seleccionar..."
                          value={batchNuevoIVASoportado}
                          onChange={(value) => setBatchNuevoIVASoportado(value)}
                          options={tipoIVASoportadoOptions}
                        />
                      </Flex>
                    </Flex>

                    {isBatchApplyDisabled && (
                      <Text variant="microcopy">
                        Introduce al menos un valor (precio o IVA) para poder aplicar.
                      </Text>
                    )}

                    <ButtonRow>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setBatchSelectedArticulos([]);
                          setBatchNuevoPrecio(null);
                          setBatchNuevoIVA("");
                          setBatchNuevoIVASoportado("");
                        }}
                      >
                        Cancelar
                      </Button>
                      <Button
                        variant="primary"
                        onClick={handleBatchApply}
                        disabled={isBatchApplyDisabled}
                      >
                        Aplicar a seleccionados
                      </Button>
                    </ButtonRow>
                  </Flex>
                </Tile>
              )}
            </>
          )}
        </Box>
        {/* ========== DESGLOSE DE COSTES ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>
            Desglose de Costes Incluidos del Contenedor
          </Text>
          <Flex direction="row" gap="large">
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Cajas (contenedor)</Text>
              <Text>{cajasContenedorSeleccionado}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Servicios (contenedor)</Text>
              <Text>{formatCurrency(totalServicios, 2)}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Servicios/caja</Text>
              <Text>{formatCurrency(serviciosPorCaja)}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">
                {modoDoble
                  ? "Flete terrestre (hasta puerto salida)"
                  : "Hasta puerto salida"}
              </Text>
              <Text>
                {formatCurrency(gastosPorTramo.hastaPuertoSalida)}/caja
              </Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">
                {modoDoble
                  ? "Flete marítimo (hasta puerto destino)"
                  : "Hasta puerto destino"}
              </Text>
              <Text>
                {formatCurrency(gastosPorTramo.hastaPuertoDestino)}/caja
              </Text>
            </Flex>
          </Flex>
        </Box>

        <Divider size="medium" />

        {/* ========== RESUMEN DE MÁRGENES (NO EDITABLE) ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>Resumen de Márgenes (%)</Text>
          <Statistics>
            {margenesPorContenedor.map((contenedor) => (
              <StatisticsItem
                key={contenedor.id}
                label={"% MARGEN " + contenedor.label}
                number={formatPercent(contenedor.margen)}
              >
                <Text format={{ fontWeight: "demibold" }}>
                  {formatCurrency(contenedor.margenPorCajaEnEuros)}/caja
                </Text>
                <Text variant="microcopy">{getContainerTypeLabel(contenedor.tipo)}</Text>
              </StatisticsItem>
            ))}
            <StatisticsItem
              label="% Margen Negocio"
              number={formatPercent(margenNegocio)}
            >
              <Text format={{ fontWeight: "demibold" }}>
                {formatCurrency(margenNegocioEnEuros.porCaja)}/caja
              </Text>
            </StatisticsItem>
          </Statistics>
        </Box>

        {/* ========== RESUMEN DE MÁRGENES EN EUROS (NO EDITABLE) ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>Márgenes de Contribución (€)</Text>
          <Statistics>
            {margenesPorContenedor.map((contenedor) => (
              <StatisticsItem
                key={contenedor.id}
                label={"MARGEN CONTRIBUCIÓN " + contenedor.label}
                number={formatCurrency(contenedor.margenTotalEnEuros, 2)}
              ></StatisticsItem>
            ))}
            <StatisticsItem
              label="Margen de Contribución Total"
              number={formatCurrency(margenNegocioEnEuros.total, 2)}
            >
              <Text format={{ fontWeight: "demibold" }}>
                {formatPercent(margenNegocio)}
              </Text>
            </StatisticsItem>
          </Statistics>
        </Box>

        <Divider size="medium" />

        {/* ========== PRECIOS POR TIPO DE VENTA ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>
            Precios Globales por Tipo de Venta
          </Text>

          {(() => {
            // Filtrar solo los tipos de venta visibles según la selección
            const keysVisibles = tiposVentaVisibles.filter(
              (key) => TIPOS_VENTA[key]
            );
            const dapKeys = keysVisibles.filter((k) =>
              ["DAP_TERRESTRE", "DAP_MARITIMO"].includes(k)
            );
            const otherKeys = keysVisibles.filter(
              (k) => !["DAP_TERRESTRE", "DAP_MARITIMO"].includes(k)
            );

            const renderItem = (key) => {
              const tipo = TIPOS_VENTA[key];
              const precioGlobal = preciosPorTipoVenta[key] || 0;
              // Usar el precio con IVA ponderado pre-calculado
              const totalConIva =
                preciosPorTipoVenta[`${key}_IVA`] || precioGlobal;
              const precioPorCajaSinIVA =
                totalCajas > 0 ? precioGlobal / totalCajas : 0;
              const precioPorCajaConIVA =
                totalCajas > 0 ? totalConIva / totalCajas : 0;
              
              return (
                <StatisticsItem
                  key={key}
                  label={tipo.label}
                  number={formatCurrency(precioGlobal, 2)}
                >
                  <StatisticsTrend
                    direction="increase"
                    value={`${formatCurrency(totalConIva, 2)} c/IVA`}
                  />
                  <Text variant="microcopy">
                    {formatCurrency(precioPorCajaSinIVA)}/caja -{" "}
                    {formatCurrency(precioPorCajaConIVA)}/caja c/IVA
                  </Text>
                </StatisticsItem>
              );
            };

            return (
              <Flex direction="column" gap="small">
                {/* Fila superior: NO-DAP (jerarquía EXW > FOB > CFR > CIF) */}
                {otherKeys.length > 0 && (
                  <Flex direction="row" gap="medium" wrap="wrap">
                    <Statistics>{otherKeys.map(renderItem)}</Statistics>
                  </Flex>
                )}

                {/* Fila inferior: DAPs (independientes) */}
                {dapKeys.length > 0 && (
                  <Flex
                    direction="row"
                    gap="medium"
                    wrap="wrap"
                    style={{ marginTop: otherKeys.length > 0 ? 8 : 0 }}
                  >
                    <Statistics>{dapKeys.map(renderItem)}</Statistics>
                  </Flex>
                )}
              </Flex>
            );
          })()}
        </Box>

        {/* ========== MENSAJE DE ERROR ========== */}
        {saveError && (
          <Alert title="Error al guardar" variant="error">
            <Text>{saveError}</Text>
          </Alert>
        )}

        {/* ========== BOTONES DE NAVEGACIÓN ========== */}
        <ButtonRow>
          <Button
            variant="secondary"
            onClick={() => onNavigate("servicios")}
            disabled={isSubmitting}
          >
            Anterior
          </Button>
          <Button
            variant="primary"
            onClick={handleGuardarNegociacion}
            disabled={isSubmitting || articulosConPrecios.length === 0}
            loading={isSubmitting}
          >
            {isSubmitting ? LOADING_MESSAGES.SAVING : "Siguiente"}
          </Button>
        </ButtonRow>
      </Flex>
    </Box>
  );
};

export default NegociacionTab;
