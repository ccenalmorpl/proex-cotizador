import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  Heading,
  Flex,
  NumberInput,
  DateInput,
  Divider,
  Button,
  ButtonRow,
  Text,
  SearchInput,
  Select,
  Input,
  LoadingSpinner,
  Alert,
  StepIndicator,
  EmptyState,
  Checkbox,
  Tag,
} from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";
import { API_CONFIG } from "../config";
import { resolveIVA } from "../utils/iva.utils";
import { normalizeIncoterm, esModoDobleFlete } from "../utils/pricing.utils";
import { timestampUTCaDateInput } from "../utils/date.utils";

const TransitarioTab = ({
  context,
  onNavigate,
  transitarioData,
  setTransitarioData,
  navieraOptions,
  clienteData,
  tipoVentaOptions,
  tipoIVAOptions = [],
  tipoIVASoportadoOptions = [],
  ivaFletesDefaults = null,
  setIvaFletesDefaults,
  selectedTab,
}) => {
  // Loading and error states
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Get readable label for tipo de venta
  const tipoVentaLabel =
    tipoVentaOptions.find((option) => option.value === clienteData?.tipoVenta)
      ?.label ||
    clienteData?.tipoVenta ||
    "No especificado";

  // Get lugar carga label from shared state
  const lugarCargaLabel = clienteData?.lugarCargaLabel || "No especificado";

  const [puertoSalidaOptions, setPuertoSalidaOptions] = useState([]);
  const [puertoDestinoOptions, setPuertoDestinoOptions] = useState([]);
  const [localidadDestinoOptions, setLocalidadDestinoOptions] = useState([]);
  const [almacenOptions, setAlmacenOptions] = useState([]);

  // Transitario search state
  const [searchTransitario, setSearchTransitario] = useState("");
  const [transitariosBuscados, setTransitariosBuscados] = useState([]);
  const [loadingTransitarios, setLoadingTransitarios] = useState(false);

  // Transitario terrestre search state (modo doble flete)
  const [searchTransitarioTerrestre, setSearchTransitarioTerrestre] = useState("");
  const [transitariosTerrestresBuscados, setTransitariosTerrestresBuscados] = useState([]);
  const [loadingTransitariosTerrestres, setLoadingTransitariosTerrestres] = useState(false);

  // Rutas state
  const [rutasTerrestres, setRutasTerrestres] = useState([]);
  const [rutasMaritimas, setRutasMaritimas] = useState([]);
  const [loadingRutas, setLoadingRutas] = useState(false);
  const [rutasError, setRutasError] = useState(null);
  const [cotizacionRapida, setCotizacionRapida] = useState(false);
  const rutasRequestIdRef = useRef(0);
  const [tabEntryTick, setTabEntryTick] = useState(0);

  // Use shared state from parent
  const {
    transitarioSeleccionado,
    transitarioTerrestreSeleccionado,
    puertoSalida,
    puertoDestino,
    localidadDestino,
    precioOrigen: precioOrigenShared,
    precioDestino: precioDestinoShared,
    diasLibres,
    naviera,
    almacen,
    rutaTerrestreSeleccionada,
    rutaMaritimaSeleccionada,
    tipoIVAFletes,
    tipoIVAFletesSoportado,
    tipoIVAFleteTerrestre,
    tipoIVAFleteTerrestreSoportado,
    fleteDobleDisponible,
    ivaFletesTerrestreDefaults,
  } = transitarioData;

  // Determinar campos requeridos según tipo de venta (clave canónica del incoterm)
  const tipoVenta = normalizeIncoterm(clienteData?.tipoVenta);
  // FCA se trata como equivalente a EXW: el comprador gestiona el transporte,
  // el transitario es opcional.
  const isEXW = tipoVenta === "EXW" || tipoVenta === "FCA";
  const isDAPTerrestre = tipoVenta === "DAP_TERRESTRE";
  const requiresPuertoSalida = ["FOB", "CFR", "CIF", "DAP_MARITIMO"].includes(
    tipoVenta
  );
  const requiresPuertoDestino = ["CFR", "CIF", "DAP_MARITIMO"].includes(
    tipoVenta
  );
  const requiresLocalidadDestino = [
    "DAP_TERRESTRE",
    "DAP_MARITIMO",
  ].includes(tipoVenta);
  // Modo doble flete: cliente nacional + camión + incoterm CIF/CFR.
  const modoDoble = esModoDobleFlete({
    fleteDobleDisponible,
    tipoVenta: clienteData?.tipoVenta,
    tipoCliente: clienteData?.tipoCliente,
  });
  const seleccionCompleta = modoDoble
    ? !!(transitarioSeleccionado && transitarioTerrestreSeleccionado)
    : !!transitarioSeleccionado;

  // Determinar step names y current step para el StepIndicator
  const getRouteSteps = () => {
    if (isDAPTerrestre) {
      return ["Localidad de Carga", "Localidad Destino"];
    }

    const steps = ["Localidad de Carga"];

    if (requiresPuertoSalida) {
      steps.push("Puerto Salida");
    }

    if (requiresPuertoDestino) {
      steps.push("Puerto Destino");
    }

    if (requiresLocalidadDestino) {
      steps.push("Localidad Destino");
    }

    return steps;
  };

  const routeSteps = getRouteSteps();

  // Calcular el step actual basado en campos completados
  const getCurrentStep = () => {
    if (!lugarCargaLabel || lugarCargaLabel === "No especificado") return 0;

    let stepIndex = 0; // Current step index (0-based)

    // Step 1: Localidad de Carga (always required, already validated above)
    stepIndex = 1;

    // Step 2: Puerto Salida (if required)
    if (requiresPuertoSalida) {
      const puertoSalidaValue = puertoSalida?.value || puertoSalida;
      if (!puertoSalidaValue) return stepIndex;
      stepIndex++;
    }

    // Step 3: Puerto Destino (if required)
    if (requiresPuertoDestino) {
      const puertoDestinoValue = puertoDestino?.value || puertoDestino;
      if (!puertoDestinoValue) return stepIndex;
      stepIndex++;
    }

    // Step 4: Localidad Destino (if required)
    if (requiresLocalidadDestino) {
      const localidadDestinoValue = localidadDestino?.value || localidadDestino;
      if (!localidadDestinoValue) return stepIndex;
      stepIndex++;
    }

    // All required steps are complete
    return stepIndex;
  };

  const currentStep = getCurrentStep();

  // Local state for prices and dates
  const [precioOrigen, setPrecioOrigen] = useState(precioOrigenShared);
  const [precioDestino, setPrecioDestino] = useState(precioDestinoShared);
  const [fechaCaducidadSalida, setFechaCaducidadSalida] = useState(transitarioData.fechaCaducidadFleteSalida);
  const [fechaCaducidadDestino, setFechaCaducidadDestino] = useState(transitarioData.fechaCaducidadFleteDestino);

  // Helper to update shared state
  const updateTransitarioField = (field, value) => {
    setTransitarioData((prevData) => ({
      ...prevData,
      [field]: value,
    }));
  };

  /**
   * Auto-select default IVA fletes from product-resolved defaults (Fase 4E).
   * Priority: persisted value > product-resolved > residency fallback.
   * Only runs when ivaFletesDefaults becomes available from backend.
   */
  useEffect(() => {
    if (!ivaFletesDefaults || !clienteData?.tipoResidente) return;

    if (!tipoIVAFletes && tipoIVAOptions.length > 0) {
      const resolved = resolveIVA({
        currentValue: tipoIVAFletes,
        productResolvedValue: ivaFletesDefaults.repercutido,
        product: null,
        tipoResidente: clienteData.tipoResidente,
        categoria: "repercutido",
        options: tipoIVAOptions,
      });
      if (resolved) updateTransitarioField("tipoIVAFletes", resolved);
    }

    if (!tipoIVAFletesSoportado && tipoIVASoportadoOptions.length > 0) {
      const resolved = resolveIVA({
        currentValue: tipoIVAFletesSoportado,
        productResolvedValue: ivaFletesDefaults.soportado,
        product: null,
        tipoResidente: clienteData.tipoResidente,
        categoria: "soportado",
        options: tipoIVASoportadoOptions,
      });
      if (resolved) updateTransitarioField("tipoIVAFletesSoportado", resolved);
    }
  }, [ivaFletesDefaults, clienteData?.tipoResidente, tipoIVAOptions, tipoIVASoportadoOptions, tipoIVAFletes, tipoIVAFletesSoportado]);

  // Auto-default del IVA del flete terrestre (solo en modo doble; nunca pisa lo persistido)
  useEffect(() => {
    if (!modoDoble || !ivaFletesTerrestreDefaults || !clienteData?.tipoResidente) return;

    if (!tipoIVAFleteTerrestre && tipoIVAOptions.length > 0) {
      const resolved = resolveIVA({
        currentValue: tipoIVAFleteTerrestre,
        productResolvedValue: ivaFletesTerrestreDefaults.repercutido,
        product: null,
        tipoResidente: clienteData.tipoResidente,
        categoria: "repercutido",
        options: tipoIVAOptions,
      });
      if (resolved) updateTransitarioField("tipoIVAFleteTerrestre", resolved);
    }

    if (!tipoIVAFleteTerrestreSoportado && tipoIVASoportadoOptions.length > 0) {
      const resolved = resolveIVA({
        currentValue: tipoIVAFleteTerrestreSoportado,
        productResolvedValue: ivaFletesTerrestreDefaults.soportado,
        product: null,
        tipoResidente: clienteData.tipoResidente,
        categoria: "soportado",
        options: tipoIVASoportadoOptions,
      });
      if (resolved) updateTransitarioField("tipoIVAFleteTerrestreSoportado", resolved);
    }
  }, [modoDoble, ivaFletesTerrestreDefaults, clienteData?.tipoResidente, tipoIVAOptions, tipoIVASoportadoOptions, tipoIVAFleteTerrestre, tipoIVAFleteTerrestreSoportado]);

  // Helper para actualizar campos select con estructura {value, options}
  const updateSelectField = useCallback(
    (field, value, options) => {
      setTransitarioData((prevData) => ({
        ...prevData,
        [field]: { value, options },
      }));
    },
    [setTransitarioData]
  );

  const readSelectValue = (v) =>
    v && typeof v === "object" ? v.value || "" : v || "";

  // Load transit data on component mount
  useEffect(() => {
    const fetchTransitData = async () => {
      try {
        setLoading(true);
        setError(null);

        const dealId = context?.crm?.objectId;
        if (!dealId) {
          setError("No se pudo obtener el ID del Deal desde el contexto.");
          setLoading(false);
          return;
        }

        const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/transit`;
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();

        if (!result.success) {
          throw new Error(result.error || "Error al cargar datos de ruta");
        }

        const data = result.data;

        // Set options (if they exist in the response - currently they don't, so we'll load them separately)
        // For now, we'll keep the existing options loading logic from the old endpoint
        // TODO: Update backend to return options in the same endpoint
        // Preload saved values if they exist
        if (data) {
          // NOTA: Puertos, almacen y naviera se actualizarán después de cargar las options
          // para poder usar la estructura {value, options}

          // Días libres
          if (data.dias_libres !== null && data.dias_libres !== undefined) {
            updateTransitarioField("diasLibres", data.dias_libres);
          }

          // Transitario
          if (data.transitario) {
            updateTransitarioField("transitarioSeleccionado", {
              id: data.transitario,
              cardcode: data.transitario,
              name: data.transitario_name || data.transitario_cardcode,
            });
          } else {
            updateTransitarioField("transitarioSeleccionado", null);
          }

          // Doble flete (camión): flag y transitario terrestre
          updateTransitarioField(
            "fleteDobleDisponible",
            !!data.flete_doble_disponible
          );
          if (data.transitario_terrestre) {
            updateTransitarioField("transitarioTerrestreSeleccionado", {
              id: data.transitario_terrestre,
              cardcode: data.transitario_terrestre,
              name: data.transitario_terrestre_name || data.transitario_terrestre,
            });
          } else {
            updateTransitarioField("transitarioTerrestreSeleccionado", null);
          }

          // Precios
          if (
            data.precio_ruta_hasta_puerto_de_salida !== null &&
            data.precio_ruta_hasta_puerto_de_salida !== undefined
          ) {
            setPrecioOrigen(data.precio_ruta_hasta_puerto_de_salida);
            updateTransitarioField(
              "precioOrigen",
              data.precio_ruta_hasta_puerto_de_salida
            );
          }

          if (
            data.precio_ruta_hasta_puerto_destino !== null &&
            data.precio_ruta_hasta_puerto_destino !== undefined
          ) {
            setPrecioDestino(data.precio_ruta_hasta_puerto_destino);
            updateTransitarioField(
              "precioDestino",
              data.precio_ruta_hasta_puerto_destino
            );
          }

          // Fechas caducidad flete
          if (data.fecha_caducidad_flete_salida) {
            const dateObject = timestampUTCaDateInput(data.fecha_caducidad_flete_salida);
            setFechaCaducidadSalida(dateObject);
            updateTransitarioField("fechaCaducidadFleteSalida", dateObject);
          }

          if (data.fecha_caducidad_flete_destino) {
            const dateObject = timestampUTCaDateInput(data.fecha_caducidad_flete_destino);
            setFechaCaducidadDestino(dateObject);
            updateTransitarioField("fechaCaducidadFleteDestino", dateObject);
          }

          // Cotización Rápida
          if (
            data.cotizacion_rapida !== null &&
            data.cotizacion_rapida !== undefined
          ) {
            const isRapida =
              data.cotizacion_rapida === "si" ||
              data.cotizacion_rapida === "true" ||
              data.cotizacion_rapida === true;

            setCotizacionRapida(isRapida);
            // Actualizar también en el estado compartido
            updateTransitarioField("cotizacionRapida", isRapida);
          }

          // IVA Fletes (persisted values from Deal)
          if (data.tipo_iva_fletes) {
            updateTransitarioField("tipoIVAFletes", data.tipo_iva_fletes);
          }
          if (data.tipo_iva_fletes_soportado) {
            updateTransitarioField("tipoIVAFletesSoportado", data.tipo_iva_fletes_soportado);
          }
          if (data.tipo_iva_fletes_terrestre) {
            updateTransitarioField("tipoIVAFleteTerrestre", data.tipo_iva_fletes_terrestre);
          }
          if (data.tipo_iva_fletes_terrestre_soportado) {
            updateTransitarioField("tipoIVAFleteTerrestreSoportado", data.tipo_iva_fletes_terrestre_soportado);
          }

          // Store product-resolved IVA defaults (Fase 4E)
          if (data.iva_fletes_defaults && setIvaFletesDefaults) {
            setIvaFletesDefaults(data.iva_fletes_defaults);
          }
          if (data.iva_fletes_terrestre_defaults) {
            updateTransitarioField("ivaFletesTerrestreDefaults", data.iva_fletes_terrestre_defaults);
          }
        }

        // Load options separately (keeping old logic for now)
        const optionsUrl = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/transit-options`;
        const optionsResponse = await hubspot.fetch(optionsUrl, {
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });

        if (optionsResponse.ok) {
          const optionsData = await optionsResponse.json();
          if (optionsData.success) {
            const puertoSalidaOpts = optionsData.puerto_salida?.options || [];
            const puertoDestinoOpts = optionsData.puerto_destino?.options || [];
            const localidadDestinoOpts =
              optionsData.localidad_destino?.options || [];
            const almacenOpts = optionsData.almacen?.options || [];
            const navieraOpts = navieraOptions; // Ya vienen como prop

            setPuertoSalidaOptions(puertoSalidaOpts);
            setPuertoDestinoOptions(puertoDestinoOpts);
            setLocalidadDestinoOptions(localidadDestinoOpts);
            setAlmacenOptions(almacenOpts);

            // Ahora que tenemos las options, convertir los valores cargados a estructura {value, options}
            if (data) {
              if (data.puerto_salida) {
                updateSelectField(
                  "puertoSalida",
                  data.puerto_salida,
                  puertoSalidaOpts
                );
              }
              if (data.puerto_destino) {
                updateSelectField(
                  "puertoDestino",
                  data.puerto_destino,
                  puertoDestinoOpts
                );
              }
              if (data.localidad_destino) {
                updateSelectField(
                  "localidadDestino",
                  data.localidad_destino,
                  localidadDestinoOpts
                );
              }
              if (data.almacen) {
                updateSelectField("almacen", data.almacen, almacenOpts);
              }
              if (data.naviera) {
                updateSelectField("naviera", data.naviera, navieraOpts);
              }
            }
          }
        }
      } catch (err) {
        console.error("Error loading transit data:", err);
        setError(err.message || "Error al cargar los datos de ruta");
      } finally {
        setLoading(false);
      }
    };

    fetchTransitData();
  }, [context]);

  useEffect(() => {
    const fetchRutas = async () => {
      if (seleccionCompleta) return;

      const dealId = context?.crm?.objectId;
      if (!dealId) return;

      if (!clienteData?.tipoVenta) return;

      if (isDAPTerrestre) {
        const localidadDestinoValue = readSelectValue(localidadDestino);
        if (!localidadDestinoValue) {
          setRutasTerrestres([]);
          setRutasMaritimas([]);
          return;
        }
      } else if (requiresPuertoSalida) {
        const puertoSalidaValue = readSelectValue(puertoSalida);
        if (!puertoSalidaValue) return;
      }

      const params = new URLSearchParams();
      if (cotizacionRapida) params.append("include_expired", "true");

      if (isDAPTerrestre) {
        params.append("localidad_destino", readSelectValue(localidadDestino));
      } else {
        const ps = readSelectValue(puertoSalida);
        const pd = readSelectValue(puertoDestino);
        if (ps) params.append("puerto_salida", ps);
        if (pd) params.append("puerto_destino", pd);
      }

      const queryString = params.toString();
      const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/rutas${queryString ? `?${queryString}` : ""}`;

      const requestId = ++rutasRequestIdRef.current;

      try {
        setLoadingRutas(true);
        setRutasError(null);

        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });
        if (requestId !== rutasRequestIdRef.current) return;

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        if (requestId !== rutasRequestIdRef.current) return;

        if (!data.success) {
          throw new Error(data.error || "Error al cargar rutas");
        }

        setRutasTerrestres(data.rutas_terrestres || []);
        setRutasMaritimas(data.rutas_maritimas || []);
      } catch (err) {
        if (requestId !== rutasRequestIdRef.current) return;
        console.error("Error loading rutas:", err);
        setRutasError(err.message || "Error al cargar rutas disponibles");
      } finally {
        if (requestId === rutasRequestIdRef.current) setLoadingRutas(false);
      }
    };

    fetchRutas();
  }, [
    context,
    transitarioSeleccionado,
    transitarioTerrestreSeleccionado,
    seleccionCompleta,
    cotizacionRapida,
    puertoSalida,
    puertoDestino,
    localidadDestino,
    isDAPTerrestre,
    tipoVenta,
    requiresPuertoSalida,
    tabEntryTick,
  ]);

  useEffect(() => {
    if (selectedTab === "transitario") {
      setTabEntryTick((t) => t + 1);
    }
  }, [selectedTab]);

  /**
   * Search for transitarios with debounce
   */
  useEffect(() => {
    const searchTransitarios = async () => {
      if (!searchTransitario) {
        setTransitariosBuscados([]);
        return;
      }

      try {
        setLoadingTransitarios(true);
        const url = `${
          API_CONFIG.baseURL
        }/api/v1/deals/transitarios/search?q=${encodeURIComponent(
          searchTransitario
        )}`;
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
          setTransitariosBuscados(data.transitarios || []);
        }
      } catch (err) {
        console.error("Error searching transitarios:", err);
        setTransitariosBuscados([]);
      } finally {
        setLoadingTransitarios(false);
      }
    };

    const timeoutId = setTimeout(() => {
      searchTransitarios();
    }, 300); // Debounce 300ms

    return () => clearTimeout(timeoutId);
  }, [searchTransitario]);

  /**
   * Search for transitarios terrestres with debounce (modo doble flete)
   */
  useEffect(() => {
    const searchTransitariosTerrestres = async () => {
      if (!searchTransitarioTerrestre) {
        setTransitariosTerrestresBuscados([]);
        return;
      }

      try {
        setLoadingTransitariosTerrestres(true);
        const url = `${
          API_CONFIG.baseURL
        }/api/v1/deals/transitarios/search?q=${encodeURIComponent(
          searchTransitarioTerrestre
        )}`;
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
          setTransitariosTerrestresBuscados(data.transitarios || []);
        }
      } catch (err) {
        console.error("Error searching transitarios terrestres:", err);
        setTransitariosTerrestresBuscados([]);
      } finally {
        setLoadingTransitariosTerrestres(false);
      }
    };

    const timeoutId = setTimeout(() => {
      searchTransitariosTerrestres();
    }, 300); // Debounce 300ms

    return () => clearTimeout(timeoutId);
  }, [searchTransitarioTerrestre]);

  const transitariosFiltrados = transitariosBuscados;

  const isRutaExpired = (fechaCaducidad) => {
    if (!fechaCaducidad) return false;

    let expiryDate;
    if (typeof fechaCaducidad === "string" && /^\d{4}-\d{2}-\d{2}/.test(fechaCaducidad)) {
      const [year, month, day] = fechaCaducidad.split("-").map(Number);
      expiryDate = new Date(year, month - 1, day);
    } else {
      const ts = typeof fechaCaducidad === "string" ? parseInt(fechaCaducidad, 10) : fechaCaducidad;
      if (isNaN(ts)) return false;
      expiryDate = new Date(ts);
      expiryDate.setHours(0, 0, 0, 0);
    }

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return expiryDate < today;
  };

  const handleSeleccionarTransitario = (transitario) => {
    updateTransitarioField("transitarioSeleccionado", transitario);
    setSearchTransitario("");
    setTransitariosBuscados([]);
  };

  const handleSeleccionarTransitarioTerrestre = (transitario) => {
    updateTransitarioField("transitarioTerrestreSeleccionado", transitario);
    setSearchTransitarioTerrestre("");
    setTransitariosTerrestresBuscados([]);
  };

  /**
   * Handle selecting a route (terrestrial or maritime)
   * This automatically selects the transitario and sets the price
   */
  /**
   * Handle changing/deselecting routes
   * This clears both routes and reloads available routes
   */
  const handleCambiarRutas = () => {
    // Clear transitario and both routes
    updateTransitarioField("transitarioSeleccionado", null);
    updateTransitarioField("rutaTerrestreSeleccionada", null);
    updateTransitarioField("rutaMaritimaSeleccionada", null);

    // Clear prices
    setPrecioOrigen(null);
    setPrecioDestino(null);

    // The useEffect will automatically reload routes when transitarioSeleccionado becomes null
  };

  // Modo doble: "Cambiar" limpia solo el tramo indicado
  const handleCambiarTramo = (tramo) => {
    if (tramo === "terrestre") {
      updateTransitarioField("transitarioTerrestreSeleccionado", null);
      updateTransitarioField("rutaTerrestreSeleccionada", null);
      setPrecioOrigen(null);
    } else {
      updateTransitarioField("transitarioSeleccionado", null);
      updateTransitarioField("rutaMaritimaSeleccionada", null);
      setPrecioDestino(null);
    }
  };

  /**
   * Parse a ruta's fecha_caducidad (YYYY-MM-DD string or timestamp) to DateInput object
   */
  const parseRutaFecha = (fechaCaducidad) => {
    if (!fechaCaducidad) return null;
    if (typeof fechaCaducidad === "string") {
      const [year, month, day] = fechaCaducidad.split("-").map(Number);
      return { year, month: month - 1, date: day };
    }
    if (!isNaN(fechaCaducidad) && fechaCaducidad > 1000000000) {
      return timestampUTCaDateInput(fechaCaducidad);
    }
    return null;
  };

  /**
   * Handle selecting a route (terrestrial or maritime)
   * This automatically selects the transitario and sets the price
   */
  const handleSeleccionarRuta = (ruta) => {
    // Set transitario from route
    const transitario = {
      id: ruta.transitario_id,
      cardcode: ruta.transitario_cardcode,
      name: ruta.transitario || ruta.transitario_cardcode, // Fallback to cardcode if name not found
    };
    // Modo doble: la ruta terrestre fija el transitario terrestre, la marítima el principal
    if (modoDoble && ruta.tipo_tramo === "terrestre") {
      updateTransitarioField("transitarioTerrestreSeleccionado", transitario);
    } else {
      updateTransitarioField("transitarioSeleccionado", transitario);
    }

    // Enrich route with human-readable labels
    const enrichedRuta = {
      ...ruta,
      lugar_carga_label: lugarCargaLabel,
      puerto_salida_label:
        puertoSalidaOptions.find((p) => p.value === ruta.puerto_salida)
          ?.label || ruta.puerto_salida,
      puerto_destino_label:
        puertoDestinoOptions.find((p) => p.value === ruta.puerto_destino)
          ?.label || ruta.puerto_destino,
    };

    // Set price based on route type
    if (ruta.tipo_tramo === "terrestre") {
      setPrecioOrigen(ruta.importe);
      updateTransitarioField("rutaTerrestreSeleccionada", enrichedRuta);

      // No auto-pair para DAP TERRESTRE (ruta directa, sin tramo marítimo)
      if (isDAPTerrestre) {
        return;
      }

      // Auto-select maritime route if same transitario has one (no en modo doble)
      const rutaMaritimaDelMismoTransitario = modoDoble
        ? null
        : rutasMaritimas.find(
            (r) => r.transitario_cardcode === ruta.transitario_cardcode
          );
      if (rutaMaritimaDelMismoTransitario) {
        setPrecioDestino(rutaMaritimaDelMismoTransitario.importe);

        const enrichedRutaMaritima = {
          ...rutaMaritimaDelMismoTransitario,
          lugar_carga_label: lugarCargaLabel,
          puerto_salida_label:
            puertoSalidaOptions.find(
              (p) => p.value === rutaMaritimaDelMismoTransitario.puerto_salida
            )?.label || rutaMaritimaDelMismoTransitario.puerto_salida,
          puerto_destino_label:
            puertoDestinoOptions.find(
              (p) => p.value === rutaMaritimaDelMismoTransitario.puerto_destino
            )?.label || rutaMaritimaDelMismoTransitario.puerto_destino,
        };
        updateTransitarioField(
          "rutaMaritimaSeleccionada",
          enrichedRutaMaritima
        );

        // If maritime route has naviera, update it
        if (
          rutaMaritimaDelMismoTransitario.naviera &&
          rutaMaritimaDelMismoTransitario.naviera !== "NA"
        ) {
          updateTransitarioField(
            "naviera",
            rutaMaritimaDelMismoTransitario.naviera
          );
        }
      }
    } else if (ruta.tipo_tramo === "maritima") {
      setPrecioDestino(ruta.importe);
      updateTransitarioField("rutaMaritimaSeleccionada", enrichedRuta);

      // If route has naviera, update it
      if (ruta.naviera && ruta.naviera !== "NA") {
        updateTransitarioField("naviera", ruta.naviera);
      }

      // Auto-select terrestrial route if same transitario has one (no en modo doble)
      const rutaTerrestreDelMismoTransitario = modoDoble
        ? null
        : rutasTerrestres.find(
            (r) => r.transitario_cardcode === ruta.transitario_cardcode
          );
      if (rutaTerrestreDelMismoTransitario) {
        setPrecioOrigen(rutaTerrestreDelMismoTransitario.importe);

        const enrichedRutaTerrestre = {
          ...rutaTerrestreDelMismoTransitario,
          lugar_carga_label: lugarCargaLabel,
          puerto_salida_label:
            puertoSalidaOptions.find(
              (p) => p.value === rutaTerrestreDelMismoTransitario.puerto_salida
            )?.label || rutaTerrestreDelMismoTransitario.puerto_salida,
          puerto_destino_label:
            puertoDestinoOptions.find(
              (p) => p.value === rutaTerrestreDelMismoTransitario.puerto_destino
            )?.label || rutaTerrestreDelMismoTransitario.puerto_destino,
        };
        updateTransitarioField(
          "rutaTerrestreSeleccionada",
          enrichedRutaTerrestre
        );
      }
    }

    // Auto-fill fechas caducidad según tipo de ruta seleccionada
    if (ruta.tipo_tramo === "terrestre") {
      const fechaSalida = parseRutaFecha(ruta.fecha_caducidad);
      if (fechaSalida) {
        setFechaCaducidadSalida(fechaSalida);
        updateTransitarioField("fechaCaducidadFleteSalida", fechaSalida);
      }
      if (!isDAPTerrestre && !modoDoble) {
        const maritima = rutasMaritimas.find(
          (r) => r.transitario_cardcode === ruta.transitario_cardcode
        );
        if (maritima) {
          const fechaDestino = parseRutaFecha(maritima.fecha_caducidad);
          if (fechaDestino) {
            setFechaCaducidadDestino(fechaDestino);
            updateTransitarioField("fechaCaducidadFleteDestino", fechaDestino);
          }
        }
      }
    } else if (ruta.tipo_tramo === "maritima") {
      const fechaDestino = parseRutaFecha(ruta.fecha_caducidad);
      if (fechaDestino) {
        setFechaCaducidadDestino(fechaDestino);
        updateTransitarioField("fechaCaducidadFleteDestino", fechaDestino);
      }
      const terrestre = modoDoble
        ? null
        : rutasTerrestres.find(
            (r) => r.transitario_cardcode === ruta.transitario_cardcode
          );
      if (terrestre) {
        const fechaSalida = parseRutaFecha(terrestre.fecha_caducidad);
        if (fechaSalida) {
          setFechaCaducidadSalida(fechaSalida);
          updateTransitarioField("fechaCaducidadFleteSalida", fechaSalida);
        }
      }
    }
  };

  // Sync local prices to shared state
  useEffect(() => {
    updateTransitarioField("precioOrigen", precioOrigen);
    updateTransitarioField("precioDestino", precioDestino);
  }, [precioOrigen, precioDestino]);

  // Calculate default almacen value based on tipo_cliente and lugar_carga
  useEffect(() => {
    // Only set default if almacen is not already set
    const almacenValue = almacen?.value || almacen;
    if (almacenValue || !clienteData?.tipoCliente) {
      return;
    }

    // Wait for options to load so the saved value resolves to a readable label
    if (!almacenOptions || almacenOptions.length === 0) {
      return;
    }

    const tipoClienteValue =
      clienteData.tipoCliente?.value || clienteData.tipoCliente;
    const tipoCliente = tipoClienteValue?.toUpperCase() || "";
    const lugarCarga = clienteData.lugarCarga || "";

    let defaultAlmacen = "";

    // Priority 1: If localidad de carga is "ALMACEN PROEX" (any tipo_cliente)
    if (
      lugarCarga === "almacen_proex" ||
      lugarCargaLabel?.toUpperCase().includes("ALMACEN PROEX")
    ) {
      defaultAlmacen = "01";
    }
    // Priority 2: Based on tipo_cliente
    else if (tipoCliente === "EXPORTACION" || tipoCliente === "EXPORTACIÓN") {
      defaultAlmacen = "10";
    } else if (tipoCliente === "NACIONAL") {
      defaultAlmacen = "12";
    }

    if (defaultAlmacen) {
      updateSelectField("almacen", defaultAlmacen, almacenOptions);
    }
  }, [
    clienteData?.tipoCliente,
    clienteData?.lugarCarga,
    lugarCargaLabel,
    almacen,
    almacenOptions,
    updateSelectField,
  ]);

  // Save transit data to backend
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const handleSaveAndContinue = async () => {
    try {
      setSaving(true);
      setSaveError(null);

      const dealId = context?.crm?.objectId;
      if (!dealId) {
        throw new Error("No se pudo obtener el ID del Deal");
      }

      // Convert dates to UTC midnight timestamps
      const toUTCTimestamp = (dateObj) => {
        if (!dateObj) return null;
        return new Date(Date.UTC(dateObj.year, dateObj.month, dateObj.date, 0, 0, 0, 0)).getTime();
      };

      // Build request payload
      const payload = {
        puerto_salida: puertoSalida?.value || puertoSalida || null,
        puerto_destino: puertoDestino?.value || puertoDestino || null,
        localidad_destino: localidadDestino?.value || localidadDestino || null,
        naviera: naviera?.value || naviera || null,
        dias_libres: diasLibres || null,
        transitario: transitarioSeleccionado?.cardcode || null,
        precio_ruta_hasta_puerto_de_salida: precioOrigen || null,
        precio_ruta_hasta_puerto_destino: precioDestino || null,
        fecha_caducidad_flete_salida: toUTCTimestamp(fechaCaducidadSalida),
        fecha_caducidad_flete_destino: toUTCTimestamp(fechaCaducidadDestino),
        cotizacion_rapida: cotizacionRapida ? "true" : "false",
        almacen: almacen?.value || almacen || null,
        tipo_iva_fletes: tipoIVAFletes?.value || tipoIVAFletes || null,
        tipo_iva_fletes_soportado: tipoIVAFletesSoportado?.value || tipoIVAFletesSoportado || null,
        ruta_terrestre_id: rutaTerrestreSeleccionada?.id || null,
        ruta_maritima_id: rutaMaritimaSeleccionada?.id || null,
        // Solo con flag camión: "" limpia el modo doble; ausente = no escribir props
        ...(fleteDobleDisponible
          ? {
              transitario_terrestre: modoDoble
                ? transitarioTerrestreSeleccionado?.cardcode || ""
                : "",
              tipo_iva_fletes_terrestre: modoDoble
                ? tipoIVAFleteTerrestre?.value || tipoIVAFleteTerrestre || ""
                : "",
              tipo_iva_fletes_terrestre_soportado: modoDoble
                ? tipoIVAFleteTerrestreSoportado?.value || tipoIVAFleteTerrestreSoportado || ""
                : "",
            }
          : {}),
      };

      const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/transit`;
      const response = await hubspot.fetch(url, {
        method: "POST",
        body: payload,
        timeout: API_CONFIG.timeout,
        headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
      });

      if (!response.ok) {
        throw new Error(`Error ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error(data.error || "Error al guardar datos de transitario");
      }

      // Actualizar el estado compartido con la estructura completa antes de navegar
      setTransitarioData({
        ...transitarioData,
        cotizacion_rapida: cotizacionRapida ? "true" : "false",
        cotizacionRapida,
        // Espejo del POST: fuera de modo doble se limpian las props terrestres
        ...(fleteDobleDisponible && !modoDoble
          ? {
              transitarioTerrestreSeleccionado: null,
              tipoIVAFleteTerrestre: "",
              tipoIVAFleteTerrestreSoportado: "",
            }
          : {}),
      });

      // Navigate to next tab
      onNavigate("servicios");
    } catch (err) {
      console.error("Error saving transit data:", err);
      setSaveError(err.message || "Error al guardar los datos");
    } finally {
      setSaving(false);
    }
  };

  // Show loading state - wait for transit data AND required clientData fields
  const isDataReady =
    !loading && clienteData?.tipoVenta && clienteData?.lugarCargaLabel;

  if (!isDataReady) {
    return (
      <Box>
        <Heading>Ruta de Transporte</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>Cargando datos de ruta...</Text>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Flex direction="column" gap="medium">
        {error && (
          <Alert title="Error" variant="error">
            {error}
          </Alert>
        )}

        {/* Sección: Ruta */}
        <Heading>Ruta de Transporte</Heading>

        <Alert variant="info" title={`Tipo de Venta: ${tipoVentaLabel}`}>
          <Text>Campos obligatorios: {routeSteps.join(" → ")}</Text>
        </Alert>

        <StepIndicator
          currentStep={currentStep}
          stepNames={routeSteps}
          variant="default"
        />

        {/* Campo 1: Localidad de Carga (siempre obligatorio y readonly) */}
        <Input
          label="1. Localidad de Carga"
          name="lugar-carga"
          value={lugarCargaLabel}
          readOnly={true}
          required={true}
          description="Origen de la mercancía (configurado en Artículos)"
          tooltip="Este campo se establece en la pestaña de Artículos"
        />

        {/* Campo 2: Puerto Salida (FOB, CFR, CIF, DAP marítimo) — oculto para DAP TERRESTRE */}
        {!isDAPTerrestre && (
          <Select
            label="2. Puerto de Salida"
            name="puerto-salida"
            description={
              requiresPuertoSalida
                ? "Puerto de origen para el envío"
                : "Puerto de origen para el envío (opcional)"
            }
            placeholder="Seleccionar..."
            required={requiresPuertoSalida}
            value={puertoSalida?.value || puertoSalida}
            onChange={(value) =>
              updateSelectField("puertoSalida", value, puertoSalidaOptions)
            }
            options={puertoSalidaOptions}
            tooltip={
              requiresPuertoSalida
                ? "Puerto desde donde saldrá la mercancía"
                : "Puerto desde donde saldrá la mercancía (opcional para este tipo de venta)"
            }
          />
        )}

        {/* Campo 3: Puerto Destino (CFR, CIF, DAP marítimo) — oculto para DAP TERRESTRE */}
        {!isDAPTerrestre && (
          <Select
            label="3. Puerto de Destino"
            name="puerto-destino"
            description={
              requiresPuertoDestino
                ? "Puerto de destino para el envío"
                : "Puerto de destino para el envío (opcional)"
            }
            placeholder="Seleccionar..."
            required={requiresPuertoDestino}
            value={puertoDestino?.value || puertoDestino}
            onChange={(value) =>
              updateSelectField("puertoDestino", value, puertoDestinoOptions)
            }
            options={puertoDestinoOptions}
            tooltip={
              requiresPuertoDestino
                ? "Puerto donde llegará la mercancía"
                : "Puerto donde llegará la mercancía (opcional para este tipo de venta)"
            }
          />
        )}

        {/* Campo 4: Localidad Destino (DAP terrestre, DAP marítimo) */}
        <Select
          label="4. Localidad de Destino"
          name="localidad-destino"
          description={
            requiresLocalidadDestino
              ? "Ciudad o localidad de entrega final"
              : "Ciudad o localidad de entrega final (opcional)"
          }
          placeholder="Seleccionar..."
          required={requiresLocalidadDestino}
          value={localidadDestino?.value || localidadDestino}
          onChange={(value) =>
            updateSelectField(
              "localidadDestino",
              value,
              localidadDestinoOptions
            )
          }
          options={localidadDestinoOptions}
          tooltip={
            requiresLocalidadDestino
              ? "Localidad donde se entregará la mercancía al cliente final"
              : "Localidad donde se entregará la mercancía al cliente final (opcional para este tipo de venta)"
          }
        />

        {/* Campo 5: Almacén */}
        <Select
          label="5. Almacén"
          name="almacen"
          description="Código de almacén para los elementos del pedido"
          placeholder="Seleccionar..."
          required={true}
          value={almacen?.value || almacen}
          onChange={(value) =>
            updateSelectField("almacen", value, almacenOptions)
          }
          options={almacenOptions}
          tooltip="Almacén asignado automáticamente según tipo de cliente y localidad de carga. Puede modificarse si es necesario."
        />

        <Divider size="small" />

        <Select
          label="Naviera"
          name="naviera"
          description="Seleccione la naviera para este envío"
          placeholder="Seleccionar..."
          value={naviera?.value || naviera}
          onChange={(value) =>
            updateSelectField("naviera", value, navieraOptions)
          }
          options={navieraOptions}
          tooltip="Compañía naviera que realizará el transporte marítimo"
        />

        <NumberInput
          label="Días Libres en Puerto Destino"
          name="dias-libres"
          value={diasLibres}
          onChange={(value) => updateTransitarioField("diasLibres", value)}
          min={0}
          max={365}
          description="Número de días libres solicitados en puerto para retirada de mercancía"
          tooltip="Días antes de aplicar gastos de almacenaje en puerto"
        />

        <Divider size="medium" />

        {/* Sección: Selección de Transitario */}
        <Heading>Transitario</Heading>

        {/* Checkbox de Cotización Rápida - Siempre visible */}
        <Box>
          <Checkbox
            name="cotizacion-rapida"
            checked={cotizacionRapida}
            onChange={(checked) => {
              setCotizacionRapida(checked);
              // DocumentacionTab lee este flag para preseleccionar los textos de
              // cotización rápida, así que tiene que verlo ya, no tras guardar.
              updateTransitarioField("cotizacionRapida", checked);
            }}
            description="Activa esta opción para incluir rutas que hayan caducado hace menos de 60 días. Esta configuración se guarda en el deal."
          >
            <Text format={{ fontWeight: "bold" }}>Cotización Rápida</Text>
          </Checkbox>
        </Box>

        {/* SI NO HAY TRANSITARIO SELECCIONADO: Mostrar rutas disponibles */}
        {!seleccionCompleta && (
          <>
            <Alert variant="info" title="Selección de Transitario">
              {isEXW
                ? "En ventas EXW/FCA el transitario es opcional (el comprador gestiona el transporte)."
                : modoDoble
                ? "Este envío requiere DOS transitarios: uno para el flete terrestre y otro para el flete marítimo. Seleccione una ruta de cada tramo o búsquelos manualmente más abajo."
                : "Seleccione una ruta para asignar transitario y precio automáticamente, o búsquelo manualmente más abajo."}
            </Alert>

            {/* RUTAS DIRECTAS (DAP TERRESTRE: Localidad Carga → Localidad Destino) */}
            {isDAPTerrestre && (
              <>
                <Box>
                  <Text format={{ fontWeight: "bold" }}>
                    🚛 Rutas Directas Disponibles
                  </Text>
                  <Text variant="microcopy">
                    {lugarCargaLabel} →{" "}
                    {localidadDestinoOptions.find(
                      (l) => l.value === (localidadDestino?.value || localidadDestino)
                    )?.label ||
                      localidadDestino?.value ||
                      localidadDestino ||
                      "Localidad Destino"}
                  </Text>
                </Box>

                {loadingRutas && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Cargando rutas disponibles...</Text>
                  </Flex>
                )}

                {rutasError && (
                  <Alert variant="warning" title="Error al cargar rutas">
                    {rutasError}
                  </Alert>
                )}

                {!loadingRutas && !rutasError && rutasTerrestres.length > 0 && (
                  <Flex direction="column" gap="small">
                    {rutasTerrestres.map((ruta) => {
                      const isExpired = isRutaExpired(ruta.fecha_caducidad);
                      return (
                        <Flex
                          key={ruta.id}
                          direction="column"
                          gap="extra-small"
                        >
                          <Flex direction="row" gap="small" align="center">
                            <Text format={{ fontWeight: "bold" }}>
                              {ruta.tipo_contenedor} •{" "}
                              {ruta.transitario_cardcode}
                              {ruta.transitario && ` - ${ruta.transitario}`}
                            </Text>
                            <Tag variant={isExpired ? "error" : "success"}>
                              {isExpired ? "Caducada" : "Vigente"}
                            </Tag>
                            <Button
                              size="xs"
                              variant="secondary"
                              onClick={() => handleSeleccionarRuta(ruta)}
                            >
                              Seleccionar
                            </Button>
                          </Flex>
                          <Text>
                            {ruta.importe}€ • Válida hasta:{" "}
                            {new Date(ruta.fecha_caducidad).toLocaleDateString(
                              "es-ES"
                            )}
                          </Text>
                          {ruta.notas && (
                            <Text variant="microcopy">{ruta.notas}</Text>
                          )}
                        </Flex>
                      );
                    })}
                  </Flex>
                )}

                {!loadingRutas && !rutasError && rutasTerrestres.length === 0 && (
                  <Text variant="microcopy">
                    No hay rutas directas disponibles para esta configuración.
                    Seleccione primero la localidad de destino.
                  </Text>
                )}
              </>
            )}

            {/* Solo cargar y mostrar si la ruta actual tiene "lugar de carga > puerto salida" como campos obligatorios */}
            {requiresPuertoSalida &&
            !isDAPTerrestre &&
            !(modoDoble && transitarioTerrestreSeleccionado) ? (
              <>
                <Box>
                  <Text format={{ fontWeight: "bold" }}>
                    🚛 Rutas Terrestres Disponibles
                  </Text>
                  <Text variant="microcopy">
                    {lugarCargaLabel} →{" "}
                    {puertoSalidaOptions.find(
                      (p) => p.value === (puertoSalida?.value || puertoSalida)
                    )?.label ||
                      puertoSalida?.value ||
                      puertoSalida ||
                      "Puerto Salida"}
                  </Text>
                </Box>

                {loadingRutas && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Cargando rutas disponibles...</Text>
                  </Flex>
                )}

                {rutasError && (
                  <Alert variant="warning" title="Error al cargar rutas">
                    {rutasError}
                  </Alert>
                )}

                {!loadingRutas && !rutasError && rutasTerrestres.length > 0 && (
                  <Flex direction="column" gap="small">
                    {rutasTerrestres.map((ruta) => {
                      const isExpired = isRutaExpired(ruta.fecha_caducidad);
                      return (
                        <Flex
                          key={ruta.id}
                          direction="column"
                          gap="extra-small"
                        >
                          <Flex direction="row" gap="small" align="center">
                            <Text format={{ fontWeight: "bold" }}>
                              {ruta.tipo_contenedor} •{" "}
                              {ruta.transitario_cardcode}
                              {ruta.transitario && ` - ${ruta.transitario}`}
                              {ruta.naviera &&
                                ruta.naviera !== "NA" &&
                                ` • ${ruta.naviera}`}
                            </Text>
                            <Tag variant={isExpired ? "error" : "success"}>
                              {isExpired ? "Caducada" : "Vigente"}
                            </Tag>
                            <Button
                              size="xs"
                              variant="secondary"
                              onClick={() => handleSeleccionarRuta(ruta)}
                            >
                              Seleccionar
                            </Button>
                          </Flex>
                          <Text>
                            {ruta.importe}€ • Válida hasta:{" "}
                            {new Date(ruta.fecha_caducidad).toLocaleDateString(
                              "es-ES"
                            )}
                          </Text>
                          {ruta.notas && (
                            <Text variant="microcopy">{ruta.notas}</Text>
                          )}
                        </Flex>
                      );
                    })}
                  </Flex>
                )}

                {!loadingRutas &&
                  !rutasError &&
                  rutasTerrestres.length === 0 && (
                    <Text variant="microcopy">
                      No hay rutas terrestres disponibles para esta
                      configuración.
                    </Text>
                  )}
              </>
            ) : null}

            {/* Divider solo si ambas secciones son visibles */}
            {requiresPuertoSalida && requiresPuertoDestino && (
              <Divider size="small" />
            )}

            {/* RUTAS MARÍTIMAS */}
            {/* Solo cargar y mostrar si la ruta actual tiene "puerto salida > puerto destino" como campos obligatorios */}
            {requiresPuertoDestino &&
            !isDAPTerrestre &&
            !(modoDoble && transitarioSeleccionado) ? (
              <>
                <Box>
                  <Text format={{ fontWeight: "bold" }}>
                    🚢 Rutas Marítimas Disponibles
                  </Text>
                  <Text variant="microcopy">
                    {puertoSalidaOptions.find(
                      (p) => p.value === (puertoSalida?.value || puertoSalida)
                    )?.label ||
                      puertoSalida?.value ||
                      puertoSalida ||
                      "Puerto Salida"}{" "}
                    →{" "}
                    {puertoDestinoOptions.find(
                      (p) => p.value === (puertoDestino?.value || puertoDestino)
                    )?.label ||
                      puertoDestino?.value ||
                      puertoDestino ||
                      "Puerto Destino"}
                  </Text>
                </Box>

                {loadingRutas && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Cargando rutas disponibles...</Text>
                  </Flex>
                )}

                {rutasError && (
                  <Alert variant="warning" title="Error al cargar rutas">
                    {rutasError}
                  </Alert>
                )}

                {!loadingRutas && !rutasError && rutasMaritimas.length > 0 && (
                  <Flex direction="column" gap="small">
                    {rutasMaritimas.map((ruta) => {
                      const isExpired = isRutaExpired(ruta.fecha_caducidad);
                      return (
                        <Flex
                          key={ruta.id}
                          direction="column"
                          gap="extra-small"
                        >
                          <Flex direction="row" gap="small" align="center">
                            <Text format={{ fontWeight: "bold" }}>
                              {ruta.tipo_contenedor} •{" "}
                              {ruta.transitario_cardcode}
                              {ruta.transitario && ` - ${ruta.transitario}`}
                              {ruta.naviera &&
                                ruta.naviera !== "NA" &&
                                ` • Naviera: ${ruta.naviera}`}
                            </Text>
                            <Tag variant={isExpired ? "error" : "success"}>
                              {isExpired ? "Caducada" : "Vigente"}
                            </Tag>
                            <Button
                              size="xs"
                              variant="secondary"
                              onClick={() => handleSeleccionarRuta(ruta)}
                            >
                              Seleccionar
                            </Button>
                          </Flex>
                          <Text>
                            {ruta.importe}€ • Válida hasta:{" "}
                            {new Date(ruta.fecha_caducidad).toLocaleDateString(
                              "es-ES"
                            )}
                          </Text>
                          {ruta.notas && (
                            <Text variant="microcopy">{ruta.notas}</Text>
                          )}
                        </Flex>
                      );
                    })}
                  </Flex>
                )}

                {!loadingRutas &&
                  !rutasError &&
                  rutasMaritimas.length === 0 && (
                    <Text variant="microcopy">
                      No hay rutas marítimas disponibles para esta
                      configuración.
                    </Text>
                  )}
              </>
            ) : null}

            {/* Empty state general cuando no hay ninguna ruta */}
            {!loadingRutas &&
              !rutasError &&
              (!requiresPuertoSalida || rutasTerrestres.length === 0) &&
              (!requiresPuertoDestino || rutasMaritimas.length === 0) &&
              (requiresPuertoSalida || requiresPuertoDestino) && (
                <EmptyState
                  title="No hay rutas guardadas para esta configuración"
                  layout="vertical"
                >
                  <Text>
                    No se encontraron rutas que coincidan con su configuración
                    actual. Puede buscar un transitario manualmente abajo.
                  </Text>
                </EmptyState>
              )}

            <Divider size="medium" />

            <Text>O buscar transitario manualmente:</Text>

            {!(modoDoble && transitarioSeleccionado) && (
              <>
                <SearchInput
                  name="buscar-transitario"
                  label={modoDoble ? "Buscar Transitario Marítimo" : "Buscar Transitario"}
                  placeholder=""
                  value={searchTransitario}
                  onChange={(value) => setSearchTransitario(value)}
                  description={
                    modoDoble
                      ? "Busque y seleccione el transitario del flete marítimo"
                      : "Busque y seleccione el transitario"
                  }
                />

                {loadingTransitarios && searchTransitario && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Buscando transitarios...</Text>
                  </Flex>
                )}

                {/* Mostrar resultados de búsqueda de transitarios */}
                {!loadingTransitarios &&
                  searchTransitario &&
                  transitariosFiltrados.length > 0 && (
                    <Box>
                      <Text format={{ fontWeight: "bold" }}>
                        Resultados de búsqueda:
                      </Text>
                      {transitariosFiltrados.map((transitario) => (
                        <Flex
                          key={transitario.id}
                          direction="row"
                          justify="space-between"
                          align="center"
                          gap="small"
                        >
                          <Text>
                            {transitario.cardcode}
                            {transitario.name && ` - ${transitario.name}`}
                          </Text>
                          <Button
                            size="xs"
                            variant="secondary"
                            onClick={() =>
                              handleSeleccionarTransitario(transitario)
                            }
                          >
                            Seleccionar
                          </Button>
                        </Flex>
                      ))}
                    </Box>
                  )}

                {!loadingTransitarios &&
                  searchTransitario &&
                  transitariosFiltrados.length === 0 && (
                    <Text>
                      No se encontraron transitarios que coincidan con "
                      {searchTransitario}"
                    </Text>
                  )}
              </>
            )}

            {/* Buscador del transitario terrestre (solo modo doble) */}
            {modoDoble && !transitarioTerrestreSeleccionado && (
              <>
                <SearchInput
                  name="buscar-transitario-terrestre"
                  label="Buscar Transitario Terrestre"
                  placeholder=""
                  value={searchTransitarioTerrestre}
                  onChange={(value) => setSearchTransitarioTerrestre(value)}
                  description="Busque y seleccione el transitario del flete terrestre"
                />

                {loadingTransitariosTerrestres && searchTransitarioTerrestre && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Buscando transitarios...</Text>
                  </Flex>
                )}

                {!loadingTransitariosTerrestres &&
                  searchTransitarioTerrestre &&
                  transitariosTerrestresBuscados.length > 0 && (
                    <Box>
                      <Text format={{ fontWeight: "bold" }}>
                        Resultados de búsqueda:
                      </Text>
                      {transitariosTerrestresBuscados.map((transitario) => (
                        <Flex
                          key={transitario.id}
                          direction="row"
                          justify="space-between"
                          align="center"
                          gap="small"
                        >
                          <Text>
                            {transitario.cardcode}
                            {transitario.name && ` - ${transitario.name}`}
                          </Text>
                          <Button
                            size="xs"
                            variant="secondary"
                            onClick={() =>
                              handleSeleccionarTransitarioTerrestre(transitario)
                            }
                          >
                            Seleccionar
                          </Button>
                        </Flex>
                      ))}
                    </Box>
                  )}

                {!loadingTransitariosTerrestres &&
                  searchTransitarioTerrestre &&
                  transitariosTerrestresBuscados.length === 0 && (
                    <Text>
                      No se encontraron transitarios que coincidan con "
                      {searchTransitarioTerrestre}"
                    </Text>
                  )}
              </>
            )}
          </>
        )}

        {/* SI HAY TRANSITARIO SELECCIONADO: Mostrar info y precios */}
        {(transitarioSeleccionado ||
          (modoDoble && transitarioTerrestreSeleccionado)) && (
          <>
            {transitarioSeleccionado && (
              <Flex
                direction="row"
                justify="space-between"
                align="center"
                gap="medium"
              >
                <Alert
                  variant="success"
                  title={
                    modoDoble
                      ? "Transitario Marítimo Seleccionado"
                      : "Transitario Seleccionado"
                  }
                >
                  <Text format={{ fontWeight: "bold" }}>
                    {transitarioSeleccionado.cardcode}
                    {transitarioSeleccionado.name &&
                      transitarioSeleccionado.cardcode !==
                        transitarioSeleccionado.name &&
                      ` - ${transitarioSeleccionado.name}`}
                  </Text>
                </Alert>
                <Button
                  size="xs"
                  variant="secondary"
                  onClick={
                    modoDoble
                      ? () => handleCambiarTramo("maritima")
                      : handleCambiarRutas
                  }
                >
                  Cambiar
                </Button>
              </Flex>
            )}

            {modoDoble && transitarioTerrestreSeleccionado && (
              <Flex
                direction="row"
                justify="space-between"
                align="center"
                gap="medium"
              >
                <Alert
                  variant="success"
                  title="Transitario Terrestre Seleccionado"
                >
                  <Text format={{ fontWeight: "bold" }}>
                    {transitarioTerrestreSeleccionado.cardcode}
                    {transitarioTerrestreSeleccionado.name &&
                      transitarioTerrestreSeleccionado.cardcode !==
                        transitarioTerrestreSeleccionado.name &&
                      ` - ${transitarioTerrestreSeleccionado.name}`}
                  </Text>
                </Alert>
                <Button
                  size="xs"
                  variant="secondary"
                  onClick={() => handleCambiarTramo("terrestre")}
                >
                  Cambiar
                </Button>
              </Flex>
            )}

            {/* Mostrar rutas seleccionadas según restricciones del tipo de venta */}
            {(rutaTerrestreSeleccionada || rutaMaritimaSeleccionada) && (
              <Box>
                <Text format={{ fontWeight: "bold" }}>
                  Rutas Seleccionadas:
                </Text>

                {/* Ruta Directa - Solo para DAP TERRESTRE */}
                {isDAPTerrestre && rutaTerrestreSeleccionada && (
                  <Flex
                    direction="row"
                    justify="space-between"
                    align="center"
                    gap="medium"
                  >
                    <Box>
                      <Text>
                        🚛 Ruta directa:{" "}
                        {rutaTerrestreSeleccionada.lugar_carga_label ||
                          rutaTerrestreSeleccionada.lugar_carga}
                        {" → "}
                        {localidadDestinoOptions.find(
                          (l) => l.value === (localidadDestino?.value || localidadDestino)
                        )?.label ||
                          localidadDestino?.value ||
                          localidadDestino}{" "}
                        • {rutaTerrestreSeleccionada.tipo_contenedor}
                      </Text>
                      <Text variant="microcopy">
                        Precio: {rutaTerrestreSeleccionada.importe}€ • Válida
                        hasta:{" "}
                        {new Date(
                          rutaTerrestreSeleccionada.fecha_caducidad
                        ).toLocaleDateString("es-ES")}
                      </Text>
                    </Box>
                    <Button
                      size="xs"
                      variant="secondary"
                      onClick={handleCambiarRutas}
                    >
                      Cambiar
                    </Button>
                  </Flex>
                )}

                {/* Ruta Terrestre - Solo mostrar si requiresPuertoSalida es true (y no DAP TERRESTRE) */}
                {requiresPuertoSalida && !isDAPTerrestre && rutaTerrestreSeleccionada && (
                  <Flex
                    direction="row"
                    justify="space-between"
                    align="center"
                    gap="medium"
                  >
                    <Box>
                      <Text>
                        🚛 Ruta terrestre:{" "}
                        {rutaTerrestreSeleccionada.lugar_carga_label ||
                          rutaTerrestreSeleccionada.lugar_carga}
                        →
                        {rutaTerrestreSeleccionada.puerto_salida_label ||
                          rutaTerrestreSeleccionada.puerto_salida}{" "}
                        • {rutaTerrestreSeleccionada.tipo_contenedor}
                      </Text>
                      <Text variant="microcopy">
                        Precio: {rutaTerrestreSeleccionada.importe}€ • Válida
                        hasta:{" "}
                        {new Date(
                          rutaTerrestreSeleccionada.fecha_caducidad
                        ).toLocaleDateString("es-ES")}
                      </Text>
                    </Box>
                    <Button
                      size="xs"
                      variant="secondary"
                      onClick={
                        modoDoble
                          ? () => handleCambiarTramo("terrestre")
                          : handleCambiarRutas
                      }
                    >
                      Cambiar
                    </Button>
                  </Flex>
                )}

                {/* Ruta Marítima - Solo mostrar si requiresPuertoDestino es true */}
                {requiresPuertoDestino && rutaMaritimaSeleccionada && (
                  <Flex
                    direction="row"
                    justify="space-between"
                    align="center"
                    gap="medium"
                  >
                    <Box>
                      <Text>
                        🚢 Ruta marítima:{" "}
                        {rutaMaritimaSeleccionada.puerto_salida_label ||
                          rutaMaritimaSeleccionada.puerto_salida}
                        →
                        {rutaMaritimaSeleccionada.puerto_destino_label ||
                          rutaMaritimaSeleccionada.puerto_destino}{" "}
                        • {rutaMaritimaSeleccionada.tipo_contenedor}
                        {rutaMaritimaSeleccionada.naviera &&
                          rutaMaritimaSeleccionada.naviera !== "NA" &&
                          ` • ${rutaMaritimaSeleccionada.naviera}`}
                      </Text>
                      <Text variant="microcopy">
                        Precio: {rutaMaritimaSeleccionada.importe}€ • Válida
                        hasta:{" "}
                        {new Date(
                          rutaMaritimaSeleccionada.fecha_caducidad
                        ).toLocaleDateString("es-ES")}
                      </Text>
                    </Box>
                    <Button
                      size="xs"
                      variant="secondary"
                      onClick={
                        modoDoble
                          ? () => handleCambiarTramo("maritima")
                          : handleCambiarRutas
                      }
                    >
                      Cambiar
                    </Button>
                  </Flex>
                )}
              </Box>
            )}
          </>
        )}

        <Divider size="medium" />

        {/* Sección: Precios de Transporte */}
        <Heading>Precios de Transporte</Heading>
        <Text>
          {modoDoble
            ? "Configure los precios de flete por tramo (terrestre y marítimo):"
            : isDAPTerrestre
            ? "Configure el precio del transporte directo hasta la localidad de destino:"
            : "Configure los precios de transporte marítimo:"}
        </Text>

        {modoDoble ? (
          <>
            <Text format={{ fontWeight: "bold" }}>Flete terrestre</Text>
            <Flex direction="row" gap="medium">
              <NumberInput
                label="Precio Flete Terrestre"
                name="precio-origen"
                description="Coste del transporte terrestre hasta el puerto de salida (EUR)"
                placeholder=""
                value={precioOrigen}
                onChange={(value) => setPrecioOrigen(value)}
                min={0}
                formatStyle="currency"
                currencyCode="EUR"
                required={requiresPuertoSalida}
                tooltip="Coste del transporte desde la fábrica hasta el puerto de salida"
              />
              <DateInput
                label="Fecha Caducidad Flete Terrestre"
                name="fecha-caducidad-flete-salida"
                description="Fecha hasta la cual es válido el precio del tramo terrestre"
                value={fechaCaducidadSalida}
                onChange={(value) => { setFechaCaducidadSalida(value); updateTransitarioField("fechaCaducidadFleteSalida", value); }}
                format="ll"
                required={requiresPuertoSalida}
                tooltip="Fecha límite de validez del precio de flete hasta el puerto de salida"
              />
              <Select
                label="IVA Repercutido Terrestre"
                name="tipo-iva-flete-terrestre"
                description="Tipo de IVA repercutido a aplicar al flete terrestre"
                placeholder="Seleccionar..."
                value={tipoIVAFleteTerrestre?.value || tipoIVAFleteTerrestre}
                onChange={(value) => updateTransitarioField("tipoIVAFleteTerrestre", value)}
                options={tipoIVAOptions}
                tooltip="Define el tipo de IVA repercutido que se aplicará al line item de FLETE TERRESTRE"
              />
              <Select
                label="IVA Soportado Terrestre"
                name="tipo-iva-flete-terrestre-soportado"
                description="Tipo de IVA soportado a aplicar al flete terrestre"
                placeholder="Seleccionar..."
                value={tipoIVAFleteTerrestreSoportado?.value || tipoIVAFleteTerrestreSoportado}
                onChange={(value) => updateTransitarioField("tipoIVAFleteTerrestreSoportado", value)}
                options={tipoIVASoportadoOptions}
                tooltip="Define el tipo de IVA soportado que se aplicará al line item de FLETE TERRESTRE"
              />
            </Flex>

            <Divider size="medium" />

            <Text format={{ fontWeight: "bold" }}>Flete marítimo</Text>
            <Flex direction="row" gap="medium">
              <NumberInput
                label="Precio Flete Marítimo"
                name="precio-destino"
                description="Coste del transporte marítimo hasta el puerto de destino (EUR)"
                placeholder=""
                value={precioDestino}
                onChange={(value) => setPrecioDestino(value)}
                min={0}
                formatStyle="currency"
                currencyCode="EUR"
                required={requiresPuertoDestino}
                tooltip="Coste del transporte marítimo desde puerto salida hasta puerto destino"
              />
              <DateInput
                label="Fecha Caducidad Flete Marítimo"
                name="fecha-caducidad-flete-destino"
                description="Fecha hasta la cual es válido el precio del tramo marítimo"
                value={fechaCaducidadDestino}
                onChange={(value) => { setFechaCaducidadDestino(value); updateTransitarioField("fechaCaducidadFleteDestino", value); }}
                format="ll"
                required={requiresPuertoDestino}
                tooltip="Fecha límite de validez del precio de flete hasta el puerto de destino"
              />
              <Select
                label="IVA Repercutido Marítimo"
                name="tipo-iva-fletes"
                description="Tipo de IVA repercutido a aplicar al flete marítimo"
                placeholder="Seleccionar..."
                value={tipoIVAFletes?.value || tipoIVAFletes}
                onChange={(value) => updateTransitarioField("tipoIVAFletes", value)}
                options={tipoIVAOptions}
                tooltip="Define el tipo de IVA repercutido que se aplicará al line item de FLETE MARITIMO"
              />
              <Select
                label="IVA Soportado Marítimo"
                name="tipo-iva-fletes-soportado"
                description="Tipo de IVA soportado a aplicar al flete marítimo"
                placeholder="Seleccionar..."
                value={tipoIVAFletesSoportado?.value || tipoIVAFletesSoportado}
                onChange={(value) => updateTransitarioField("tipoIVAFletesSoportado", value)}
                options={tipoIVASoportadoOptions}
                tooltip="Define el tipo de IVA soportado que se aplicará al line item de FLETE MARITIMO"
              />
            </Flex>
          </>
        ) : isDAPTerrestre ? (
          <>
            <Flex direction="row" gap="medium">
              <NumberInput
                label="Precio Ruta hasta Localidad Destino"
                name="precio-origen"
                description="Coste del transporte directo hasta la localidad de destino (EUR)"
                placeholder=""
                value={precioOrigen}
                onChange={(value) => setPrecioOrigen(value)}
                min={0}
                formatStyle="currency"
                currencyCode="EUR"
                required={true}
                tooltip="Coste del transporte desde la fábrica hasta la localidad de destino del cliente"
              />
              <DateInput
                label="Fecha caducidad flete"
                name="fecha-caducidad-flete-salida"
                description="Fecha hasta la cual es válido el precio del flete"
                value={fechaCaducidadSalida}
                onChange={(value) => { setFechaCaducidadSalida(value); updateTransitarioField("fechaCaducidadFleteSalida", value); }}
                format="ll"
                required={true}
                tooltip="Fecha límite de validez del precio de flete negociado con el transitario"
              />
            </Flex>
            <Select
              label="Tipo IVA Fletes (Repercutido)"
              name="tipo-iva-fletes"
              description="Tipo de IVA repercutido a aplicar a los fletes"
              placeholder="Seleccionar..."
              value={tipoIVAFletes?.value || tipoIVAFletes}
              onChange={(value) => updateTransitarioField("tipoIVAFletes", value)}
              options={tipoIVAOptions}
              tooltip="Define el tipo de IVA repercutido que se aplicará a los line items de FLETES"
            />
            <Select
              label="Tipo IVA Fletes (Soportado)"
              name="tipo-iva-fletes-soportado"
              description="Tipo de IVA soportado a aplicar a los fletes"
              placeholder="Seleccionar..."
              value={tipoIVAFletesSoportado?.value || tipoIVAFletesSoportado}
              onChange={(value) => updateTransitarioField("tipoIVAFletesSoportado", value)}
              options={tipoIVASoportadoOptions}
              tooltip="Define el tipo de IVA soportado que se aplicará a los line items de FLETES"
            />
          </>
        ) : (
          <>
            <Flex direction="row" gap="medium">
              <NumberInput
                label="Precio Ruta hasta Puerto Salida"
                name="precio-origen"
                description="Coste del transporte terrestre hasta el puerto de origen (EUR)"
                placeholder=""
                value={precioOrigen}
                onChange={(value) => setPrecioOrigen(value)}
                min={0}
                formatStyle="currency"
                currencyCode="EUR"
                required={requiresPuertoSalida}
                tooltip="Coste del transporte desde la fábrica hasta el puerto de salida"
              />
              <DateInput
                label="Fecha caducidad flete salida"
                name="fecha-caducidad-flete-salida"
                description="Fecha hasta la cual es válido el precio del tramo terrestre"
                value={fechaCaducidadSalida}
                onChange={(value) => { setFechaCaducidadSalida(value); updateTransitarioField("fechaCaducidadFleteSalida", value); }}
                format="ll"
                required={requiresPuertoSalida}
                tooltip="Fecha límite de validez del precio de flete hasta el puerto de salida"
              />
            </Flex>

            <Flex direction="row" gap="medium">
              <NumberInput
                label="Precio Ruta hasta Puerto Destino"
                name="precio-destino"
                description="Coste del transporte marítimo hasta el puerto de destino (EUR)"
                placeholder=""
                value={precioDestino}
                onChange={(value) => setPrecioDestino(value)}
                min={0}
                formatStyle="currency"
                currencyCode="EUR"
                required={requiresPuertoDestino}
                tooltip="Coste del transporte marítimo desde puerto salida hasta puerto destino"
              />
              <DateInput
                label="Fecha caducidad flete destino"
                name="fecha-caducidad-flete-destino"
                description="Fecha hasta la cual es válido el precio del tramo marítimo"
                value={fechaCaducidadDestino}
                onChange={(value) => { setFechaCaducidadDestino(value); updateTransitarioField("fechaCaducidadFleteDestino", value); }}
                format="ll"
                required={requiresPuertoDestino}
                tooltip="Fecha límite de validez del precio de flete hasta el puerto de destino"
              />
            </Flex>

            <Select
              label="Tipo IVA Fletes (Repercutido)"
              name="tipo-iva-fletes"
              description="Tipo de IVA repercutido a aplicar a los fletes"
              placeholder="Seleccionar..."
              value={tipoIVAFletes?.value || tipoIVAFletes}
              onChange={(value) => updateTransitarioField("tipoIVAFletes", value)}
              options={tipoIVAOptions}
              tooltip="Define el tipo de IVA repercutido que se aplicará a los line items de FLETES"
            />
            <Select
              label="Tipo IVA Fletes (Soportado)"
              name="tipo-iva-fletes-soportado"
              description="Tipo de IVA soportado a aplicar a los fletes"
              placeholder="Seleccionar..."
              value={tipoIVAFletesSoportado?.value || tipoIVAFletesSoportado}
              onChange={(value) => updateTransitarioField("tipoIVAFletesSoportado", value)}
              options={tipoIVASoportadoOptions}
              tooltip="Define el tipo de IVA soportado que se aplicará a los line items de FLETES"
            />
          </>
        )}

        {saveError && (
          <Alert variant="error" title="Error al guardar">
            {saveError}
          </Alert>
        )}

        <ButtonRow>
          <Button
            variant="secondary"
            onClick={() => onNavigate("contenedores")}
            disabled={saving}
          >
            Anterior
          </Button>
          <Button
            variant="primary"
            onClick={handleSaveAndContinue}
            disabled={
              saving ||
              !fechaCaducidadSalida ||
              (!isDAPTerrestre && requiresPuertoDestino && !fechaCaducidadDestino) ||
              !(almacen?.value || almacen) ||
              (requiresPuertoSalida &&
                !isDAPTerrestre &&
                !(puertoSalida?.value || puertoSalida)) ||
              (requiresPuertoDestino &&
                !(puertoDestino?.value || puertoDestino)) ||
              (requiresLocalidadDestino &&
                !(localidadDestino?.value || localidadDestino)) ||
              (isDAPTerrestre && !precioOrigen) ||
              (!isDAPTerrestre && requiresPuertoSalida && !precioOrigen) ||
              (!isDAPTerrestre && requiresPuertoDestino && !precioDestino) ||
              (modoDoble &&
                (!transitarioSeleccionado || !transitarioTerrestreSeleccionado))
            }
          >
            {saving ? "Guardando..." : "Siguiente"}
          </Button>
        </ButtonRow>
      </Flex>
    </Box>
  );
};

export default TransitarioTab;
