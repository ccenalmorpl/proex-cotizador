import React, { useState, useEffect, useCallback } from "react";
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

const TransitarioTab = ({
  context,
  onNavigate,
  transitarioData,
  setTransitarioData,
  navieraOptions,
  clienteData,
  tipoVentaOptions,
  tipoIVAOptions = [],
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

  // Rutas state
  const [rutasTerrestres, setRutasTerrestres] = useState([]);
  const [rutasMaritimas, setRutasMaritimas] = useState([]);
  const [loadingRutas, setLoadingRutas] = useState(false);
  const [rutasError, setRutasError] = useState(null);
  const [cotizacionRapida, setCotizacionRapida] = useState(false);

  // Use shared state from parent
  const {
    transitarioSeleccionado,
    puertoSalida,
    puertoDestino,
    localidadDestino,
    precioOrigen: precioOrigenShared,
    precioDestino: precioDestinoShared,
    fechaValidez: fechaValidezShared,
    diasLibres,
    naviera,
    almacen,
    rutaTerrestreSeleccionada,
    rutaMaritimaSeleccionada,
    tipoIVAFletes,
  } = transitarioData;

  // Determinar campos requeridos según tipo de venta
  const tipoVenta = clienteData?.tipoVenta || "";
  const requiresPuertoSalida = [
    "FOB",
    "CFR",
    "CIF",
    "DAP MARÍTIMO",
    "DAP_MARITIMO",
  ].includes(tipoVenta);
  const requiresPuertoDestino = [
    "CFR",
    "CIF",
    "DAP MARÍTIMO",
    "DAP_MARITIMO",
  ].includes(tipoVenta);
  const requiresLocalidadDestino = [
    "DAP TERRESTRE",
    "DAP_TERRESTRE",
    "DAP MARÍTIMO",
    "DAP_MARITIMO",
  ].includes(tipoVenta);

  // Determinar step names y current step para el StepIndicator
  const getRouteSteps = () => {
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

  // Local state for prices and date
  const [precioOrigen, setPrecioOrigen] = useState(precioOrigenShared);
  const [precioDestino, setPrecioDestino] = useState(precioDestinoShared);
  const [fechaValidez, setFechaValidez] = useState(fechaValidezShared);

  // Helper to update shared state
  const updateTransitarioField = (field, value) => {
    setTransitarioData((prevData) => ({
      ...prevData,
      [field]: value,
    }));
  };

  /**
   * Auto-select default IVA type based on tipoResidente
   */
  useEffect(() => {
    if (
      !tipoIVAFletes &&
      clienteData?.tipoResidente &&
      tipoIVAOptions.length > 0
    ) {
      let defaultIVA = "";
      const tipoResidente = clienteData.tipoResidente.toUpperCase();

      if (tipoResidente === "EXTRANJERO") {
        defaultIVA = "EX";
      } else if (tipoResidente === "EUROPA") {
        defaultIVA = "EI21";
      } else {
        defaultIVA = "R21";
      }

      // Verify the IVA option exists
      const ivaOption = tipoIVAOptions.find((opt) => opt.value === defaultIVA);
      if (ivaOption) {
        updateTransitarioField("tipoIVAFletes", defaultIVA);
      }
    }
  }, [clienteData?.tipoResidente, tipoIVAOptions, tipoIVAFletes]);

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

          // Fecha caducidad
          if (data.fecha_caducidad) {
            const fecha = new Date(data.fecha_caducidad);
            const dateObject = {
              year: fecha.getFullYear(),
              month: fecha.getMonth(),
              date: fecha.getDate(),
            };
            setFechaValidez(dateObject);
            updateTransitarioField("fechaValidez", dateObject);
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
        }

        // Load options separately (keeping old logic for now)
        const optionsUrl = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/transit-options`;
        const optionsResponse = await hubspot.fetch(optionsUrl);

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

  /**
   * Build rutas API URL with query parameters for puerto_salida and puerto_destino
   */
  const buildRutasUrl = useCallback(
    (dealId) => {
      const params = new URLSearchParams();

      // Add include_expired parameter if cotizacionRapida is true
      if (cotizacionRapida) {
        params.append("include_expired", "true");
      }

      // Add puerto_salida and puerto_destino from current select values
      // This ensures we get routes for the currently selected ports even if not saved to deal yet
      // Extract value from object structure {value, options} if present
      const puertoSalidaValue = puertoSalida?.value || puertoSalida;
      const puertoDestinoValue = puertoDestino?.value || puertoDestino;

      if (puertoSalidaValue) {
        params.append("puerto_salida", puertoSalidaValue);
      }
      if (puertoDestinoValue) {
        params.append("puerto_destino", puertoDestinoValue);
      }

      const queryString = params.toString();
      return `${API_CONFIG.baseURL}${
        API_CONFIG.endpoints.deals
      }/${dealId}/rutas${queryString ? `?${queryString}` : ""}`;
    },
    [cotizacionRapida, puertoSalida, puertoDestino]
  );

  /**
   * Fetch available rutas when transitario is not selected
   * CONSOLIDADO: Un solo useEffect para cargar rutas, evitando llamadas múltiples al endpoint
   */
  useEffect(() => {
    const fetchRutas = async () => {
      // Only fetch rutas if no transitario is selected
      if (transitarioSeleccionado) {
        return;
      }

      // Solo cargar rutas si tenemos los datos mínimos necesarios
      // Para evitar llamadas innecesarias al endpoint
      const dealId = context?.crm?.objectId;
      if (!dealId) {
        return;
      }

      try {
        setLoadingRutas(true);
        setRutasError(null);

        const url = buildRutasUrl(dealId);
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
        });

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (!data.success) {
          throw new Error(data.error || "Error al cargar rutas");
        }

        setRutasTerrestres(data.rutas_terrestres || []);
        setRutasMaritimas(data.rutas_maritimas || []);
      } catch (err) {
        console.error("Error loading rutas:", err);
        setRutasError(err.message || "Error al cargar rutas disponibles");
      } finally {
        setLoadingRutas(false);
      }
    };

    fetchRutas();
  }, [
    context,
    transitarioSeleccionado,
    buildRutasUrl,
    cotizacionRapida,
    puertoSalida,
    puertoDestino,
  ]);

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

  const transitariosFiltrados = transitariosBuscados;

  /**
   * Check if a route is expired
   */
  const isRutaExpired = (fechaCaducidad) => {
    if (!fechaCaducidad) return false;
    const now = new Date();
    const expiryDate = new Date(fechaCaducidad);
    return expiryDate < now;
  };

  const handleSeleccionarTransitario = (transitario) => {
    updateTransitarioField("transitarioSeleccionado", transitario);
    setSearchTransitario("");
    setTransitariosBuscados([]);
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
    updateTransitarioField("transitarioSeleccionado", transitario);

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

      // Auto-select maritime route if same transitario has one
      const rutaMaritimaDelMismoTransitario = rutasMaritimas.find(
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

      // Auto-select terrestrial route if same transitario has one
      const rutaTerrestreDelMismoTransitario = rutasTerrestres.find(
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

    // Set fecha validez from route
    // HubSpot DateInput expects: { year: number, month: number, date: number }
    if (ruta.fecha_caducidad) {
      try {
        let dateObject;

        // If it's a string date (YYYY-MM-DD), parse to object
        if (typeof ruta.fecha_caducidad === "string") {
          const [year, month, day] = ruta.fecha_caducidad
            .split("-")
            .map(Number);
          dateObject = {
            year: year,
            month: month - 1,
            date: day,
          };
        }
        // If it's a timestamp, convert to object
        else if (
          !isNaN(ruta.fecha_caducidad) &&
          ruta.fecha_caducidad > 1000000000
        ) {
          const fecha = new Date(parseInt(ruta.fecha_caducidad));
          dateObject = {
            year: fecha.getFullYear(),
            month: fecha.getMonth(), // Already 0-indexed
            date: fecha.getDate(),
          };
        }

        setFechaValidez(dateObject);
      } catch (e) {
        console.error("Error parsing fecha_caducidad:", e);
      }
    }
  };

  // Sync local prices and date to shared state
  useEffect(() => {
    updateTransitarioField("precioOrigen", precioOrigen);
    updateTransitarioField("precioDestino", precioDestino);
    updateTransitarioField("fechaValidez", fechaValidez);
  }, [precioOrigen, precioDestino, fechaValidez]);

  // Calculate default almacen value based on tipo_cliente and lugar_carga
  useEffect(() => {
    // Only set default if almacen is not already set
    const almacenValue = almacen?.value || almacen;
    if (almacenValue || !clienteData?.tipoCliente) {
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

      // Convert fechaValidez to timestamp at midnight UTC
      let fechaCaducidadTimestamp = null;
      if (fechaValidez) {
        // Create date in UTC to avoid timezone issues
        const fecha = new Date(
          Date.UTC(
            fechaValidez.year,
            fechaValidez.month,
            fechaValidez.date,
            0,
            0,
            0,
            0
          )
        );
        fechaCaducidadTimestamp = fecha.getTime();
      }

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
        fecha_caducidad: fechaCaducidadTimestamp,
        cotizacion_rapida: cotizacionRapida ? "true" : "false",
        almacen: almacen?.value || almacen || null,
        tipo_iva_fletes: tipoIVAFletes?.value || tipoIVAFletes || null,
      };

      const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/transit`;
      const response = await hubspot.fetch(url, {
        method: "POST",
        body: payload,
        timeout: API_CONFIG.timeout,
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
        {/* Error Alert */}
        {error && (
          <Alert title="Error" variant="error">
            {error}
          </Alert>
        )}

        {/* Sección: Ruta */}
        <Heading>Ruta de Transporte</Heading>

        {/* Alert informativo con el tipo de venta */}
        <Alert variant="info" title={`Tipo de Venta: ${tipoVentaLabel}`}>
          <Text>Campos obligatorios: {routeSteps.join(" → ")}</Text>
        </Alert>

        {/* Indicador visual de progreso de ruta */}
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

        {/* Campo 2: Puerto Salida (FOB, CFR, CIF, DAP marítimo) */}
        <Select
          label="2. Puerto de Salida"
          name="puerto-salida"
          description={
            requiresPuertoSalida
              ? "Puerto de origen para el envío"
              : "Puerto de origen para el envío (opcional)"
          }
          placeholder="Seleccione un puerto de salida..."
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

        {/* Campo 3: Puerto Destino (CFR, CIF, DAP marítimo) */}
        <Select
          label="3. Puerto de Destino"
          name="puerto-destino"
          description={
            requiresPuertoDestino
              ? "Puerto de destino para el envío"
              : "Puerto de destino para el envío (opcional)"
          }
          placeholder="Seleccione un puerto de destino..."
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

        {/* Campo 4: Localidad Destino (DAP terrestre, DAP marítimo) */}
        <Select
          label="4. Localidad de Destino"
          name="localidad-destino"
          description={
            requiresLocalidadDestino
              ? "Ciudad o localidad de entrega final"
              : "Ciudad o localidad de entrega final (opcional)"
          }
          placeholder="Seleccione una localidad de destino..."
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
          placeholder="Seleccione un almacén..."
          required={true}
          value={almacen?.value || almacen}
          onChange={(value) =>
            updateSelectField("almacen", value, almacenOptions)
          }
          options={almacenOptions}
          tooltip="Almacén asignado automáticamente según tipo de cliente y localidad de carga. Puede modificarse si es necesario."
        />

        <Divider size="small" />

        {/* Selector de Naviera */}
        <Select
          label="Naviera"
          name="naviera"
          description="Seleccione la naviera para este envío"
          placeholder="Seleccione una naviera..."
          value={naviera?.value || naviera}
          onChange={(value) =>
            updateSelectField("naviera", value, navieraOptions)
          }
          options={navieraOptions}
          tooltip="Compañía naviera que realizará el transporte marítimo"
        />

        {/* Días Libres en Puerto */}
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
            onChange={(checked) => setCotizacionRapida(checked)}
            description="Activa esta opción para incluir rutas que hayan caducado hace menos de 60 días. Esta configuración se guarda en el deal."
          >
            <Text format={{ fontWeight: "bold" }}>Cotización Rápida</Text>
          </Checkbox>
        </Box>

        {/* SI NO HAY TRANSITARIO SELECCIONADO: Mostrar rutas disponibles */}
        {!transitarioSeleccionado && (
          <>
            <Alert variant="info" title="Selección de Transitario">
              Seleccione una ruta para asignar transitario y precio
              automáticamente, o búsquelo manualmente más abajo.
            </Alert>

            {/* RUTAS TERRESTRES */}
            {/* Solo cargar y mostrar si la ruta actual tiene "lugar de carga > puerto salida" como campos obligatorios */}
            {requiresPuertoSalida ? (
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

                {/* Loading state for rutas */}
                {loadingRutas && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Cargando rutas disponibles...</Text>
                  </Flex>
                )}

                {/* Error state for rutas */}
                {rutasError && (
                  <Alert variant="warning" title="Error al cargar rutas">
                    {rutasError}
                  </Alert>
                )}

                {/* Rutas terrestres reales */}
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

                {/* Empty state for rutas terrestres */}
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
            {requiresPuertoDestino ? (
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

                {/* Loading state for rutas marítimas */}
                {loadingRutas && (
                  <Flex direction="row" align="center" gap="small">
                    <LoadingSpinner size="xs" />
                    <Text>Cargando rutas disponibles...</Text>
                  </Flex>
                )}

                {/* Error state for rutas marítimas */}
                {rutasError && (
                  <Alert variant="warning" title="Error al cargar rutas">
                    {rutasError}
                  </Alert>
                )}

                {/* Rutas marítimas reales */}
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

                {/* Empty state for rutas marítimas */}
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

            <SearchInput
              name="buscar-transitario"
              label="Buscar Transitario"
              placeholder="Ej: MERTRAMAR, NOATUM, Hillebrand..."
              value={searchTransitario}
              onChange={(value) => setSearchTransitario(value)}
              description="Busque y seleccione el transitario"
            />

            {/* Show loading spinner */}
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

        {/* SI HAY TRANSITARIO SELECCIONADO: Mostrar info y precios */}
        {transitarioSeleccionado && (
          <>
            <Flex
              direction="row"
              justify="space-between"
              align="center"
              gap="medium"
            >
              <Alert variant="success" title="Transitario Seleccionado">
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
                onClick={handleCambiarRutas}
              >
                Cambiar
              </Button>
            </Flex>

            {/* Mostrar rutas seleccionadas según restricciones del tipo de venta */}
            {(rutaTerrestreSeleccionada || rutaMaritimaSeleccionada) && (
              <Box>
                <Text format={{ fontWeight: "bold" }}>
                  Rutas Seleccionadas:
                </Text>

                {/* Ruta Terrestre - Solo mostrar si requiresPuertoSalida es true */}
                {requiresPuertoSalida && rutaTerrestreSeleccionada && (
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
                      onClick={handleCambiarRutas}
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
                      onClick={handleCambiarRutas}
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
        <Text>Configure los precios de transporte marítimo:</Text>

        <NumberInput
          label="Precio Ruta hasta Puerto Salida"
          name="precio-origen"
          description="Coste del transporte terrestre hasta el puerto de origen (EUR)"
          placeholder="0"
          value={precioOrigen}
          onChange={(value) => setPrecioOrigen(value)}
          min={0}
          formatStyle="currency"
          currencyCode="EUR"
          required={requiresPuertoSalida}
          tooltip="Coste del transporte desde la fábrica hasta el puerto de salida"
        />

        <NumberInput
          label="Precio Ruta hasta Puerto Destino"
          name="precio-destino"
          description="Coste del transporte marítimo hasta el puerto de destino (EUR)"
          placeholder="0"
          value={precioDestino}
          onChange={(value) => setPrecioDestino(value)}
          min={0}
          formatStyle="currency"
          currencyCode="EUR"
          required={requiresPuertoDestino}
          tooltip="Coste del transporte marítimo desde puerto salida hasta puerto destino"
        />

        <Select
          label="Tipo IVA Fletes"
          name="tipo-iva-fletes"
          description="Tipo de IVA a aplicar a los fletes (aplica a ambas rutas)"
          placeholder="Seleccione un tipo de IVA..."
          value={tipoIVAFletes?.value || tipoIVAFletes}
          onChange={(value) => updateTransitarioField("tipoIVAFletes", value)}
          options={tipoIVAOptions}
          tooltip="Define el tipo de IVA que se aplicará a los line items de FLETES"
        />

        <DateInput
          label="Fecha de Caducidad de la Cotización"
          name="fecha-caducidad"
          description="Fecha hasta la cual esta cotización es válida"
          value={fechaValidez}
          onChange={(value) => setFechaValidez(value)}
          format="ll"
          required={true}
          tooltip="Fecha límite de caducidad de los precios de esta cotización"
        />

        <Divider size="medium" />

        {/* Error al guardar */}
        {saveError && (
          <Alert variant="error" title="Error al guardar">
            {saveError}
          </Alert>
        )}

        {/* Botones de navegación */}
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
              !fechaValidez ||
              !(almacen?.value || almacen) ||
              (requiresPuertoSalida &&
                !(puertoSalida?.value || puertoSalida)) ||
              (requiresPuertoDestino &&
                !(puertoDestino?.value || puertoDestino)) ||
              (requiresLocalidadDestino &&
                !(localidadDestino?.value || localidadDestino)) ||
              (requiresPuertoSalida && !precioOrigen) ||
              (requiresPuertoDestino && !precioDestino)
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
