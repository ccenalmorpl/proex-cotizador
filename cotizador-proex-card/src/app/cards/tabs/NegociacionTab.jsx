import { useState, useCallback, useMemo, useEffect } from "react";
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
  Select,
  Tag,
  Alert,
  EmptyState,
  LoadingSpinner,
  Tile,
} from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";
import {
  ERROR_MESSAGES,
  LOADING_MESSAGES,
  INITIAL_RENDER_DELAY_MS,
} from "../constants/common.constants";
import { getContainerTypeLabel } from "../constants/containers.constants";

// Constantes
const SEGURO_PORCENTAJE = 2;

// Helper: Calcular margen efectivo de un artículo en un contenedor
const calcularMargenArticuloEnContenedor = (
  artContenedor,
  preciosEXW,
  comisionPorCaja,
  clienteData
) => {
  // Usar el line_item_id del artículo directamente
  const lineItemId = artContenedor.line_item_id;
  const costePorCaja =
    artContenedor.precio_de_coste || artContenedor.price || 0;
  const margenInicial =
    artContenedor.margen_unitario !== null &&
    artContenedor.margen_unitario !== undefined
      ? artContenedor.margen_unitario * 100
      : clienteData?.margen || 12;

  const precioEXWFinal =
    preciosEXW[lineItemId] ||
    costePorCaja + (costePorCaja * margenInicial) / 100;
  const margenEnEuros = precioEXWFinal - costePorCaja;
  const margenActual =
    costePorCaja > 0 ? (margenEnEuros / costePorCaja) * 100 : 0;

  let margenEfectivo = margenActual;
  // Extraer el valor si es un objeto {value, options}
  const repercutirAClienteValue =
    clienteData?.repercutirACliente?.value || clienteData?.repercutirACliente;
  if (comisionPorCaja > 0 && repercutirAClienteValue === "no") {
    const comisionComoMargen = (comisionPorCaja / costePorCaja) * 100;
    margenEfectivo = margenActual - comisionComoMargen;
  }

  return margenEfectivo;
};

// Función para obtener el porcentaje de IVA de un artículo usando datos dinámicos de HubDB
const getIVAPorcentaje = (tipoIVA, ivaDataMap) => {
  // Si no hay tipo de IVA o es vacío, devolver 21%
  if (!tipoIVA || tipoIVA === "") {
    return 21;
  }
  // Si el tipo existe en los datos dinámicos, devolver el porcentaje total
  if (ivaDataMap && tipoIVA in ivaDataMap) {
    return ivaDataMap[tipoIVA].porcentaje_total;
  }
  // Si no se encuentra el tipo, por defecto 21%
  return 21;
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

const NegociacionTab = ({
  context,
  onNavigate,
  transitarioData,
  serviciosData,
  clienteData, // Necesitamos el margen del cliente como fallback
  tipoIVAOptions = [], // Opciones de tipo de IVA repercutido
  ivaData = {}, // Datos completos de IVA indexados por code
  contenedoresUnicos, // Contenedores únicos (nueva estructura que reemplaza articulosData y contenedoresData)
  preciosEXW, // Estado compartido de precios EXW
  setPreciosEXW, // Setter para precios EXW
  tiposIVA, // Estado compartido de tipos de IVA
  setTiposIVA, // Setter para tipos de IVA
}) => {
  // ============================================
  // DATOS DE ARTÍCULOS DESDE CONTENEDORES ÚNICOS
  // ============================================

  // Transformar artículos desde contenedoresUnicos a formato necesario para negociación
  // Se extraen artículos únicos de todos los contenedores para la vista agregada
  const articulosParaNegociacion = useMemo(() => {
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
            const costePorCaja = art.precio_de_coste || art.price || 0;
            const margenInicial =
              art.margen_unitario !== null && art.margen_unitario !== undefined
                ? art.margen_unitario * 100
                : clienteData?.margen || 12;

            articulosMap.set(articuloId, {
              id: articuloId,
              nombre: art.nombre,
              cajas: cajasPorArticulo[articuloId] || 0,
              costePorCaja: costePorCaja,
              margen: margenInicial,
              tipo_iva_repercutido: art.tipo_iva_repercutido || null,
            });
          }
        });
      }
    });

    return Array.from(articulosMap.values());
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

  // Gastos por tramo por caja
  const gastosPorTramo = useMemo(
    () => ({
      hastaLocalidadDestino: transitarioData?.precioOrigen / totalCajas || 0,
      hastaPuertoSalida: transitarioData?.precioOrigen / totalCajas || 0,
      hastaPuertoDestino: transitarioData?.precioDestino / totalCajas || 0,
      hastaPuertoDestinoLocalidad:
        transitarioData?.precioDestino / totalCajas || 0,
    }),
    [transitarioData, totalCajas]
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

  // Calcular comisión por caja del intermediario
  const comisionPorCaja = useMemo(() => {
    // Verificar si hay intermediario configurado
    if (
      !clienteData?.codigoIntermediario ||
      !clienteData?.tipoComision ||
      !clienteData?.importeComision
    ) {
      return 0;
    }

    // Extraer valores si son objetos {value, options}
    const importeComision =
      clienteData.importeComision?.value || clienteData.importeComision;
    const tipoComision =
      clienteData.tipoComision?.value || clienteData.tipoComision;

    if (tipoComision === "importe_por_negocio") {
      // Comisión total / número total de cajas
      return totalCajas > 0 ? importeComision / totalCajas : 0;
    } else if (tipoComision === "importe_por_contenedor") {
      // (Comisión * número de contenedores) / número total de cajas
      return totalCajas > 0
        ? (importeComision * totalContenedores) / totalCajas
        : 0;
    } else if (tipoComision === "importe_por_cajas") {
      // Comisión directa por caja
      return importeComision;
    }
    return 0;
  }, [clienteData, totalCajas, totalContenedores]);

  // ============================================
  // ESTADO
  // ============================================

  // NOTA: preciosEXW, setPreciosEXW, tiposIVA, setTiposIVA ahora vienen como props compartidos

  // Estado de contenedor único seleccionado
  const [contenedorUnicoSeleccionado, setContenedorUnicoSeleccionado] =
    useState(null);

  // Estado de carga y errores
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [isLoadingInitial, setIsLoadingInitial] = useState(true);

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

  // Artículos únicos del contenedor seleccionado para la tabla
  const articulosUnicosParaNegociacion = useMemo(() => {
    if (!contenedorSeleccionado || !contenedorSeleccionado.container_config) {
      return [];
    }

    const articulos = contenedorSeleccionado.container_config.articulos || [];

    return articulos.map((art) => {
      // Usar precio_de_coste como coste base (costo del fabricante), fallback a price
      const costePorCaja = art.precio_de_coste || art.price || 0;

      // Usar margen_unitario del artículo, o el margen del cliente como fallback
      const margenInicial =
        art.margen_unitario !== null && art.margen_unitario !== undefined
          ? art.margen_unitario * 100
          : clienteData?.margen || 12;

      return {
        id: art.line_item_id, // HubSpot line item ID del artículo
        articuloId: art.articuloId, // ID original del artículo (SKU)
        nombre: art.articuloName, // Usar el nombre único
        cajas: art.numCajas,
        costePorCaja: costePorCaja,
        margen: margenInicial,
        tipo_iva_repercutido: art.tipo_iva_repercutido || null,
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

  // Sincronizar precios y tipos de IVA cuando cambien los artículos
  useEffect(() => {
    // Determinar qué lista de artículos usar
    const articulos = contenedorUnicoSeleccionado
      ? articulosUnicosParaNegociacion
      : articulosParaNegociacion;

    if (articulos.length > 0) {
      setPreciosEXW((prevPrecios) => {
        const newPrecios = { ...prevPrecios }; // Mantener todos los precios anteriores
        articulos.forEach((art) => {
          // SOLO inicializar si NO existe (nunca sobrescribir valores editados)
          if (newPrecios[art.id] === undefined) {
            // Calcular precio inicial: coste + margen por defecto
            const margenInicial = art.costePorCaja * (art.margen / 100);
            newPrecios[art.id] = art.costePorCaja + margenInicial;
          }
        });
        return newPrecios;
      });

      // Sincronizar tipos de IVA
      setTiposIVA((prevTipos) => {
        const newTipos = { ...prevTipos }; // Mantener todos los tipos anteriores
        articulos.forEach((art) => {
          // SOLO inicializar si NO existe (nunca sobrescribir valores editados)
          if (newTipos[art.id] === undefined) {
            newTipos[art.id] =
              art.tipo_iva_repercutido ||
              (tipoIVAOptions.length > 0 ? tipoIVAOptions[0].value : "");
          }
        });
        return newTipos;
      });
    }
  }, [
    contenedorUnicoSeleccionado,
    articulosUnicosParaNegociacion,
    articulosParaNegociacion,
    tipoIVAOptions,
  ]);

  // ============================================
  // CÁLCULOS
  // ============================================

  // Coste de servicios por caja (diluido equitativamente)
  const serviciosPorCaja = useMemo(
    () => (totalCajas > 0 ? totalServicios / totalCajas : 0),
    [totalCajas, totalServicios]
  );

  // Calcular precio EXW por caja de cada artículo
  const articulosConPrecios = useMemo(() => {
    const articulosBase = contenedorUnicoSeleccionado
      ? articulosUnicosParaNegociacion
      : articulosParaNegociacion;

    return articulosBase.map((art) => {
      // Precio unitario de la caja (coste)
      const precioCaja = art.costePorCaja;

      // Precio EXW establecido por el comercial - ESTE ES SIEMPRE EL PRECIO FINAL AL CLIENTE
      const precioEXWFinal = preciosEXW[art.id] || precioCaja;

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

      // Obtener el porcentaje de IVA de este artículo
      const tipoIVAArticulo = tiposIVA[art.id] || art.tipo_iva_repercutido;
      const ivaPorcentaje = getIVAPorcentaje(tipoIVAArticulo, ivaData);

      return {
        ...art,
        margenActual,
        margenEnEuros,
        margenEfectivo,
        margenEfectivoEnEuros,
        precioPorCaja: precioEXWFinal,
        precioEXWEditable: precioEXWFinal,
        subtotal: precioEXWFinal * art.cajas,
        ivaPorcentaje,
      };
    });
  }, [
    contenedorUnicoSeleccionado,
    articulosUnicosParaNegociacion,
    articulosParaNegociacion,
    preciosEXW,
    comisionPorCaja,
    clienteData,
    tiposIVA,
    ivaData,
  ]);

  // Calcular totales para el footer de la tabla
  const totalesContenedorSeleccionado = useMemo(() => {
    if (!articulosConPrecios || articulosConPrecios.length === 0) {
      return {
        totalCajas: 0,
        totalCoste: 0,
        totalComision: 0,
        totalMargenEfectivoEnEuros: 0,
        totalPrecioEXW: 0,
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
    const totalPrecioEXW = articulosConPrecios.reduce(
      (sum, art) => sum + art.precioPorCaja * art.cajas,
      0
    );

    return {
      totalCajas,
      totalCoste,
      totalComision,
      totalMargenEfectivoEnEuros,
      totalPrecioEXW,
    };
  }, [articulosConPrecios, comisionPorCaja]);

  // Calcular precios por tipo de venta GLOBALES (sin IVA y con IVA ponderado)
  // IMPORTANTE: Este cálculo SIEMPRE usa todos los artículos de todos los contenedores
  const preciosPorTipoVenta = useMemo(() => {
    // Calcular precios totales por tipo de venta (sumando por artículo)
    let totalEXW = 0;
    let totalDAP_TERRESTRE = 0;
    let totalFOB = 0;
    let totalCFR = 0;
    let totalDAP_MARITIMO = 0;
    let totalCIF = 0;

    // Calcular precios con IVA (aplicando el IVA específico de cada artículo)
    let totalEXW_IVA = 0;
    let totalDAP_TERRESTRE_IVA = 0;
    let totalFOB_IVA = 0;
    let totalCFR_IVA = 0;
    let totalDAP_MARITIMO_IVA = 0;
    let totalCIF_IVA = 0;

    // Iterar por TODOS los contenedores y TODOS los artículos
    if (contenedoresUnicos && Array.isArray(contenedoresUnicos)) {
      contenedoresUnicos.forEach((contenedor) => {
        if (
          contenedor.container_config?.articulos &&
          Array.isArray(contenedor.container_config.articulos)
        ) {
          contenedor.container_config.articulos.forEach((artContenedor) => {
            const lineItemId = artContenedor.line_item_id;
            const costePorCaja =
              artContenedor.precio_de_coste || artContenedor.price || 0;
            const cajas = artContenedor.numCajas || 0;

            // Obtener el precio EXW editado o calcular el inicial
            const margenInicial =
              artContenedor.margen_unitario !== null &&
              artContenedor.margen_unitario !== undefined
                ? artContenedor.margen_unitario * 100
                : clienteData?.margen || 12;

            const precioEXWFinal =
              preciosEXW[lineItemId] ||
              costePorCaja + (costePorCaja * margenInicial) / 100;

            // Obtener el tipo de IVA del artículo
            const tipoIVAArticulo =
              tiposIVA[lineItemId] || artContenedor.tipo_iva_repercutido;
            const ivaPorcentaje = getIVAPorcentaje(tipoIVAArticulo, ivaData);

            // Calcular precios por tipo de venta
            const exw = precioEXWFinal * cajas;
            const dapTerrestre =
              (precioEXWFinal +
                gastosPorTramo.hastaLocalidadDestino +
                serviciosPorCaja) *
              cajas;
            const fob =
              (precioEXWFinal +
                gastosPorTramo.hastaPuertoSalida +
                serviciosPorCaja) *
              cajas;
            const cfr = fob + gastosPorTramo.hastaPuertoDestino * cajas;
            const dapMaritimo =
              cfr +
              gastosPorTramo.hastaPuertoDestinoLocalidad * cajas +
              (cfr * SEGURO_PORCENTAJE) / 100;
            const cif = cfr + (cfr * SEGURO_PORCENTAJE) / 100;

            // Sumar precios sin IVA
            totalEXW += exw;
            totalDAP_TERRESTRE += dapTerrestre;
            totalFOB += fob;
            totalCFR += cfr;
            totalDAP_MARITIMO += dapMaritimo;
            totalCIF += cif;

            // Sumar precios con IVA (aplicando el porcentaje específico de cada artículo)
            totalEXW_IVA += exw * (1 + ivaPorcentaje / 100);
            totalDAP_TERRESTRE_IVA += dapTerrestre * (1 + ivaPorcentaje / 100);
            totalFOB_IVA += fob * (1 + ivaPorcentaje / 100);
            totalCFR_IVA += cfr * (1 + ivaPorcentaje / 100);
            totalDAP_MARITIMO_IVA += dapMaritimo * (1 + ivaPorcentaje / 100);
            totalCIF_IVA += cif * (1 + ivaPorcentaje / 100);
          });
        }
      });
    }

    return {
      EXW: totalEXW,
      DAP_TERRESTRE: totalDAP_TERRESTRE,
      FOB: totalFOB,
      CFR: totalCFR,
      DAP_MARITIMO: totalDAP_MARITIMO,
      CIF: totalCIF,
      // Precios con IVA ponderado
      EXW_IVA: totalEXW_IVA,
      DAP_TERRESTRE_IVA: totalDAP_TERRESTRE_IVA,
      FOB_IVA: totalFOB_IVA,
      CFR_IVA: totalCFR_IVA,
      DAP_MARITIMO_IVA: totalDAP_MARITIMO_IVA,
      CIF_IVA: totalCIF_IVA,
    };
  }, [
    contenedoresUnicos,
    preciosEXW,
    tiposIVA,
    gastosPorTramo,
    serviciosPorCaja,
    clienteData,
    ivaData,
  ]);

  // Calcular márgenes por cada contenedor único
  const margenesPorContenedor = useMemo(() => {
    if (
      !contenedoresUnicos ||
      !Array.isArray(contenedoresUnicos) ||
      contenedoresUnicos.length === 0
    ) {
      return [];
    }

    return contenedoresUnicos.map((contenedor) => {
      let totalMargenPonderado = 0;
      let totalMargenEnEuros = 0;
      let totalCajasContenedor = 0;

      if (
        contenedor.container_config?.articulos &&
        Array.isArray(contenedor.container_config.articulos)
      ) {
        contenedor.container_config.articulos.forEach((artContenedor) => {
          const margenEfectivo = calcularMargenArticuloEnContenedor(
            artContenedor,
            preciosEXW,
            comisionPorCaja,
            clienteData
          );

          const cajasEnContenedor = artContenedor.numCajas || 0;
          totalMargenPonderado += margenEfectivo * cajasEnContenedor;
          totalCajasContenedor += cajasEnContenedor;

          // Calcular margen en euros por artículo
          const lineItemId = artContenedor.line_item_id;
          const costePorCaja =
            artContenedor.precio_de_coste || artContenedor.price || 0;
          const margenInicial =
            artContenedor.margen_unitario !== null &&
            artContenedor.margen_unitario !== undefined
              ? artContenedor.margen_unitario * 100
              : clienteData?.margen || 12;

          const precioEXWFinal =
            preciosEXW[lineItemId] ||
            costePorCaja + (costePorCaja * margenInicial) / 100;
          const margenEnEuros = precioEXWFinal - costePorCaja;

          // Calcular margen efectivo en euros considerando comisión
          let margenEfectivoEnEuros = margenEnEuros;
          // Extraer el valor si es un objeto {value, options}
          const repercutirAClienteValue =
            clienteData?.repercutirACliente?.value ||
            clienteData?.repercutirACliente;
          if (comisionPorCaja > 0 && repercutirAClienteValue === "no") {
            margenEfectivoEnEuros = margenEnEuros - comisionPorCaja;
          }

          totalMargenEnEuros += margenEfectivoEnEuros * cajasEnContenedor;
        });
      }

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
  }, [contenedoresUnicos, preciosEXW, comisionPorCaja, clienteData]);

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

    contenedoresUnicos.forEach((contenedor) => {
      if (
        contenedor.container_config?.articulos &&
        Array.isArray(contenedor.container_config.articulos)
      ) {
        contenedor.container_config.articulos.forEach((artContenedor) => {
          const margenEfectivo = calcularMargenArticuloEnContenedor(
            artContenedor,
            preciosEXW,
            comisionPorCaja,
            clienteData
          );

          const cajasEnContenedor = artContenedor.numCajas || 0;
          totalMargenPonderado += margenEfectivo * cajasEnContenedor;
          totalCajasNegocio += cajasEnContenedor;
        });
      }
    });

    return totalCajasNegocio > 0 ? totalMargenPonderado / totalCajasNegocio : 0;
  }, [contenedoresUnicos, preciosEXW, comisionPorCaja, clienteData]);

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

    contenedoresUnicos.forEach((contenedor) => {
      if (
        contenedor.container_config?.articulos &&
        Array.isArray(contenedor.container_config.articulos)
      ) {
        contenedor.container_config.articulos.forEach((artContenedor) => {
          const lineItemId = artContenedor.line_item_id;
          const costePorCaja =
            artContenedor.precio_de_coste || artContenedor.price || 0;
          const margenInicial =
            artContenedor.margen_unitario !== null &&
            artContenedor.margen_unitario !== undefined
              ? artContenedor.margen_unitario * 100
              : clienteData?.margen || 12;

          const precioEXWFinal =
            preciosEXW[lineItemId] ||
            costePorCaja + (costePorCaja * margenInicial) / 100;
          const margenEnEuros = precioEXWFinal - costePorCaja;

          // Calcular margen efectivo en euros considerando comisión
          let margenEfectivoEnEuros = margenEnEuros;
          if (comisionPorCaja > 0 && clienteData?.repercutirACliente === "no") {
            margenEfectivoEnEuros = margenEnEuros - comisionPorCaja;
          }

          const cajasEnContenedor = artContenedor.numCajas || 0;
          totalMargenEnEuros += margenEfectivoEnEuros * cajasEnContenedor;
          totalCajasNegocio += cajasEnContenedor;
        });
      }
    });

    return {
      total: totalMargenEnEuros,
      porCaja:
        totalCajasNegocio > 0 ? totalMargenEnEuros / totalCajasNegocio : 0,
    };
  }, [contenedoresUnicos, preciosEXW, comisionPorCaja, clienteData]);

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

  // ============================================
  // HANDLERS
  // ============================================

  const handlePrecioChange = useCallback((articuloId, nuevoPrecio) => {
    const valor = parseFloat(nuevoPrecio) || 0;
    setPreciosEXW((prev) => ({
      ...prev,
      [articuloId]: valor,
    }));
  }, []);

  const handleTipoIVAChange = useCallback((articuloId, nuevoTipoIVA) => {
    setTiposIVA((prev) => ({
      ...prev,
      [articuloId]: nuevoTipoIVA,
    }));
  }, []);

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
            contenedor.container_config.articulos.forEach((art) => {
              const lineItemId = art.line_item_id;
              const articuloId = art.articuloId;
              // Usar precio_de_coste, fallback a price para datos antiguos
              const costePorCaja = art.precio_de_coste || art.price || 0;

              // Obtener el precio EXW editado o calcular el inicial
              const margenInicial =
                art.margen_unitario !== null &&
                art.margen_unitario !== undefined
                  ? art.margen_unitario * 100
                  : clienteData?.margen || 12;

              // Buscar precio por line_item_id primero, luego por articuloId (vista agregada)
              const precioEXWFinal =
                preciosEXW[lineItemId] ||
                preciosEXW[articuloId] ||
                costePorCaja + (costePorCaja * margenInicial) / 100;
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

              // Siempre agregar el artículo (el backend validará si es necesario)
              todosLosArticulos.push({
                line_item_id: lineItemId,
                price: precioEXWFinal,
                margen_unitario: margenEfectivo / 100,
                tipo_iva_repercutido:
                  tiposIVA[lineItemId] ||
                  tiposIVA[articuloId] ||
                  art.tipo_iva_repercutido ||
                  "",
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
    tiposIVA,
    comisionPorCaja,
    clienteData,
    onNavigate,
    ivaData,
  ]);

  // Formateo de moneda (3 decimales para precisión en negociación)
  const formatCurrency = useCallback((value) => {
    return `${value.toLocaleString("es-ES", {
      minimumFractionDigits: 3,
      maximumFractionDigits: 3,
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
      // Si no hay tipo de venta seleccionado, mostrar todos
      return Object.keys(TIPOS_VENTA);
    }

    // Extraer el valor si es un objeto {value, options}
    const tipoVentaValue =
      clienteData.tipoVenta?.value || clienteData.tipoVenta;
    const tipoVentaNormalizado = tipoVentaValue
      .toUpperCase()
      .replace(/\s+/g, "_");

    // DAP TERRESTRE: Solo mostrar DAP_TERRESTRE
    if (tipoVentaNormalizado === "DAP_TERRESTRE") {
      return ["DAP_TERRESTRE"];
    }

    // DAP Marítimo: Solo mostrar DAP_MARITIMO
    if (
      tipoVentaNormalizado === "DAP_MARITIMO" ||
      tipoVentaNormalizado === "DAP_MARÍTIMO"
    ) {
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

    // Si no se reconoce el tipo, mostrar todos
    return Object.keys(TIPOS_VENTA);
  }, [clienteData]);

  // ============================================
  // RENDER
  // ============================================

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

        {/* Alerta informativa sobre datos faltantes */}
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

          {/* Selector de contenedor único */}
          {contenedoresUnicosOptions.length > 0 && (
            <Flex direction="column" gap="small">
              <Text variant="microcopy">
                Seleccione un contenedor para ajustar los precios de sus
                artículos:
              </Text>
              <Box>
                <Select
                  name="contenedor-unico-selector"
                  label="Contenedor"
                  placeholder="Seleccione un contenedor"
                  value={contenedorUnicoSeleccionado || ""}
                  onChange={(value) => setContenedorUnicoSeleccionado(value)}
                  options={contenedoresUnicosOptions}
                />
              </Box>
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
            <Table bordered={true}>
              <TableHead>
                <TableRow>
                  <TableHeader>Artículo</TableHeader>
                  <TableHeader>Cajas</TableHeader>
                  <TableHeader>Coste/Caja</TableHeader>
                  <TableHeader>Comisión/Caja</TableHeader>
                  <TableHeader>Margen Efectivo</TableHeader>
                  <TableHeader>Precio/Caja (EXW)</TableHeader>
                  <TableHeader>Tipo IVA</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {articulosConPrecios.map((art) => (
                  <TableRow key={art.id}>
                    <TableCell>{art.nombre}</TableCell>
                    <TableCell>{art.cajas}</TableCell>
                    <TableCell>{formatCurrency(art.costePorCaja)}</TableCell>
                    <TableCell>
                      {comisionPorCaja > 0 ? (
                        <Flex direction="column" gap="extra-small">
                          <Text format={{ fontWeight: "bold" }}>
                            {formatCurrency(comisionPorCaja)}
                          </Text>
                          <Text>
                            {/* Extraer el valor si es un objeto {value, options} */}
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
                    <TableCell>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontWeight: "bold" }}>
                          {formatPercent(art.margenEfectivo)}
                        </Text>
                        <Text>{formatCurrency(art.margenEfectivoEnEuros)}</Text>
                      </Flex>
                    </TableCell>
                    <TableCell>
                      <StepperInput
                        name={`precio-${art.id}`}
                        value={art.precioEXWEditable || 0}
                        onChange={(value) => handlePrecioChange(art.id, value)}
                        stepSize={0.001}
                        min={0}
                        precision={3}
                        formatStyle="decimal"
                        minValueReachedTooltip="El precio no puede ser negativo"
                      />
                    </TableCell>
                    <TableCell>
                      <Select
                        name={`tipo-iva-${art.id}`}
                        value={tiposIVA[art.id] || ""}
                        onChange={(value) => handleTipoIVAChange(art.id, value)}
                        options={tipoIVAOptions}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
              <TableFooter>
                <TableRow>
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
                      {formatCurrency(totalesContenedorSeleccionado.totalCoste)}
                    </Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>
                      {totalesContenedorSeleccionado.totalComision > 0
                        ? formatCurrency(
                            totalesContenedorSeleccionado.totalComision
                          )
                        : "-"}
                    </Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>
                      {formatCurrency(
                        totalesContenedorSeleccionado.totalMargenEfectivoEnEuros
                      )}
                    </Text>
                  </TableCell>
                  <TableCell>
                    <Text format={{ fontWeight: "bold" }}>
                      {formatCurrency(
                        totalesContenedorSeleccionado.totalPrecioEXW
                      )}
                    </Text>
                  </TableCell>
                  <TableCell>
                    <Text>-</Text>
                  </TableCell>
                </TableRow>
              </TableFooter>
            </Table>
          )}
        </Box>

        <Divider size="medium" />

        {/* ========== DESGLOSE DE COSTES ========== */}
        <Box>
          <Text format={{ fontWeight: "bold" }}>
            Desglose de Costes Incluidos
          </Text>
          <Flex direction="row" gap="large">
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Cajas totales</Text>
              <Text>{totalCajas}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Servicios totales</Text>
              <Text>{formatCurrency(totalServicios)}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Servicios/caja</Text>
              <Text>{formatCurrency(serviciosPorCaja)}</Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Hasta puerto salida</Text>
              <Text>
                {formatCurrency(gastosPorTramo.hastaPuertoSalida)}/caja
              </Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Hasta puerto destino</Text>
              <Text>
                {formatCurrency(gastosPorTramo.hastaPuertoDestino)}/caja
              </Text>
            </Flex>
            <Flex direction="column" gap="extra-small">
              <Text variant="microcopy">Seguro</Text>
              <Text>{SEGURO_PORCENTAJE}%</Text>
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
                number={formatCurrency(contenedor.margenTotalEnEuros)}
              ></StatisticsItem>
            ))}
            <StatisticsItem
              label="Margen de Contribución Total"
              number={formatCurrency(margenNegocioEnEuros.total)}
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
                  number={formatCurrency(precioGlobal)}
                >
                  <StatisticsTrend
                    direction="increase"
                    value={`${formatCurrency(totalConIva)} c/IVA`}
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
