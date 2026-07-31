// React
import React, { useState, useEffect, useCallback, useRef } from "react";

// HubSpot UI Components
import {
  Box,
  Heading,
  Flex,
  Divider,
  ButtonRow,
  Button,
  Select,
  NumberInput,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  Text,
  Alert,
  LoadingSpinner,
} from "@hubspot/ui-extensions";

// Utils & Config
import { fetchFromAPI } from "../utils/api.utils";
import { API_CONFIG } from "../config";
import { resolveIVA } from "../utils/iva.utils";
import { normalizeIncoterm } from "../utils/pricing.utils";
import {
  ERROR_MESSAGES,
  LOADING_MESSAGES,
} from "../constants/common.constants";

const ServiciosTab = ({
  context,
  onNavigate,
  serviciosData,
  setServiciosData,
  tipoIVAOptions = [],
  tipoIVASoportadoOptions = [],
  clienteData,
}) => {
  // Estado para los servicios disponibles (cargados desde API)
  const [serviciosDisponibles, setServiciosDisponibles] = useState([]);
  const [loadingServicios, setLoadingServicios] = useState(true);
  const [errorServicios, setErrorServicios] = useState(null);

  // Estado para los proveedores del servicio seleccionado
  const [proveedoresDisponibles, setProveedoresDisponibles] = useState([]);
  const [loadingProveedores, setLoadingProveedores] = useState(false);

  // Caché de proveedores por tipoArticulo, alimenta los Select editables de cada fila
  const [proveedoresPorTipo, setProveedoresPorTipo] = useState({});

  // Estado para el selector de artículo de servicio
  const [articuloSeleccionado, setArticuloSeleccionado] = useState(null);

  // Estado para el configurador del servicio
  const [proveedorSeleccionado, setProveedorSeleccionado] = useState(null);
  const [precioServicio, setPrecioServicio] = useState(undefined);

  // Estado para la tabla de servicios agregados
  const [serviciosAgregados, setServiciosAgregados] = useState([]);

  // Estado para tipos de IVA por servicio (key: servicio.id, value: tipo_iva)
  const [tiposIVAServicios, setTiposIVAServicios] = useState({});
  const [tiposIVAServiciosSoportado, setTiposIVAServiciosSoportado] = useState({});

  // Estado para carga inicial de servicios guardados
  const [loadingGuardados, setLoadingGuardados] = useState(true);

  // Estado para el guardado
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // Auto-precarga de seguro: aplica a CIF, DAP marítimo y DAP terrestre
  const tipoVentaNorm = normalizeIncoterm(clienteData?.tipoVenta);
  const isCIF = tipoVentaNorm === "CIF";
  const isDAPMaritimo = tipoVentaNorm === "DAP_MARITIMO";
  const isDAPTerrestre = tipoVentaNorm === "DAP_TERRESTRE";
  const requiereSeguroAuto = isCIF || isDAPMaritimo || isDAPTerrestre;
  // Negocio de exportación según el "Tipo de cliente" de ClienteTab. Determina el
  // IVA del seguro auto-añadido (exportación → repercutido EX / soportado NS).
  const tipoClienteNorm = String(
    clienteData?.tipoCliente?.value ?? clienteData?.tipoCliente ?? ""
  ).toUpperCase();
  const esExportacion =
    tipoClienteNorm === "EXPORTACION" || tipoClienteNorm === "EXPORTACIÓN";
  const [seguroEliminadoAviso, setSeguroEliminadoAviso] = useState(false);
  const seguroAutoAddedRef = useRef(false);

  /**
   * Carga los servicios guardados del deal desde la API
   */
  const loadServiciosGuardados = useCallback(async () => {
    setLoadingGuardados(true);
    try {
      const dealId = context?.crm?.objectId;
      console.log("[ServiciosTab] loadServiciosGuardados: dealId =", dealId);
      if (!dealId) return;

      const data = await fetchFromAPI(
        `${API_CONFIG.endpoints.deals}/${dealId}/services`
      );

      console.log("[ServiciosTab] Data services: success =", data?.success, "services length =", data?.services?.length, "raw response keys =", data ? Object.keys(data) : []);

      if (data.services && data.services.length > 0) {
        const serviciosCargados = data.services.map((service, index) => ({
          id: Date.now() + index,
          servicioId: service.product_id,
          nombreServicio: service.product_name,
          tipoArticulo: service.tipo_articulo,
          proveedorId: service.provider_id,
          nombreProveedor: service.provider_name,
          proveedorCardcode: service.provider_cardcode,
          importe: service.price,
        }));

        // Restaurar tipo de IVA guardado por cada servicio
        const ivasGuardados = {};
        const ivasSopGuardados = {};
        serviciosCargados.forEach((s, index) => {
          const tipoIva = data.services[index]?.tipo_iva_repercutido;
          if (tipoIva) ivasGuardados[s.id] = tipoIva;
          const tipoIvaSop = data.services[index]?.tipo_iva_soportado;
          if (tipoIvaSop) ivasSopGuardados[s.id] = tipoIvaSop;
        });
        setTiposIVAServicios(ivasGuardados);
        setTiposIVAServiciosSoportado(ivasSopGuardados);

        console.log("[ServiciosTab] Servicios cargados:", serviciosCargados.length, "items");
        setServiciosAgregados(serviciosCargados);

        if (setServiciosData) {
          setServiciosData({ serviciosSeleccionados: serviciosCargados });
        }
      } else {
        console.log("[ServiciosTab] No hay services en la respuesta o array vacío");
      }
    } catch (err) {
      console.log("[ServiciosTab] Error loadServiciosGuardados:", err?.message, err);
      // Error silencioso - los servicios simplemente no se cargan
    } finally {
      setLoadingGuardados(false);
    }
  }, [context?.crm?.objectId, setServiciosData]);

  /**
   * Carga los servicios disponibles desde la API
   */
  const loadServicios = useCallback(async () => {
    setLoadingServicios(true);
    setErrorServicios(null);

    try {
      const data = await fetchFromAPI(
        `${API_CONFIG.endpoints.articles}/services/list`
      );

      const options = data.products.map((product) => ({
        value: product.id,
        label: product.name,
        tipoArticulo: product.tipo_articulo,
        precioDefault: product.hs_price_eur,
        // IVA properties from product (6 fields)
        tipo_iva_repercutido: product.tipo_iva_repercutido || null,
        tipo_iva_repercutido_eu: product.tipo_iva_repercutido_eu || null,
        tipo_iva_repercutido_ex: product.tipo_iva_repercutido_ex || null,
        tipo_iva_soportado: product.tipo_iva_soportado || null,
        tipo_iva_soportado_eu: product.tipo_iva_soportado_eu || null,
        tipo_iva_soportado_ex: product.tipo_iva_soportado_ex || null,
      }));
      setServiciosDisponibles(options);
    } catch (error) {
      setErrorServicios(error.message || ERROR_MESSAGES.LOAD_ERROR);
    } finally {
      setLoadingServicios(false);
    }
  }, []);

  /**
   * Carga los proveedores disponibles para un tipo de artículo
   * y cachea las opciones en proveedoresPorTipo para los Selects en la tabla.
   */
  const loadProveedores = useCallback(async (tipoArticulo) => {
    if (!tipoArticulo) {
      setProveedoresDisponibles([]);
      return [];
    }

    setLoadingProveedores(true);

    try {
      const data = await fetchFromAPI(
        `${
          API_CONFIG.endpoints.articles
        }/services/providers/list?tipo_proveedor=${encodeURIComponent(
          tipoArticulo
        )}`
      );

      const options = data.providers.map((provider) => ({
        value: provider.id,
        label: provider.name,
        cardcode: provider.cardcode,
      }));

      setProveedoresDisponibles(options);
      setProveedoresPorTipo((prev) => ({ ...prev, [tipoArticulo]: options }));
      return options;
    } catch {
      setProveedoresDisponibles([]);
      return [];
    } finally {
      setLoadingProveedores(false);
    }
  }, []);

  const ensureProveedoresPorTipo = useCallback(
    async (tipoArticulo) => {
      if (!tipoArticulo || proveedoresPorTipo[tipoArticulo]) return;
      try {
        const data = await fetchFromAPI(
          `${
            API_CONFIG.endpoints.articles
          }/services/providers/list?tipo_proveedor=${encodeURIComponent(
            tipoArticulo
          )}`
        );
        const options = data.providers.map((provider) => ({
          value: provider.id,
          label: provider.name,
          cardcode: provider.cardcode,
        }));
        setProveedoresPorTipo((prev) => ({ ...prev, [tipoArticulo]: options }));
      } catch {
        setProveedoresPorTipo((prev) => ({ ...prev, [tipoArticulo]: [] }));
      }
    },
    [proveedoresPorTipo]
  );

  // Cargar servicios disponibles y servicios guardados al montar el componente
  useEffect(() => {
    loadServicios();
    loadServiciosGuardados();
  }, [loadServicios, loadServiciosGuardados]);

  // Precargar el caché de proveedores para los tipos de los servicios ya agregados
  useEffect(() => {
    const tipos = Array.from(
      new Set(serviciosAgregados.map((s) => s.tipoArticulo).filter(Boolean))
    );
    tipos.forEach((tipo) => {
      ensureProveedoresPorTipo(tipo);
    });
  }, [serviciosAgregados, ensureProveedoresPorTipo]);

  // Auto-precargar seguro cuando tipoVenta requiere seguro (CIF, DAP marítimo, DAP terrestre)
  useEffect(() => {
    if (loadingServicios || loadingGuardados) return;
    if (!requiereSeguroAuto) return;
    if (seguroAutoAddedRef.current) return;
    seguroAutoAddedRef.current = true;

    const seguroProducto = serviciosDisponibles.find(
      (s) => s.tipoArticulo === "seguro"
    );
    if (!seguroProducto) return;

    const precargarSeguro = async () => {
      try {
        const data = await fetchFromAPI(
          `${API_CONFIG.endpoints.articles}/services/providers/list?tipo_proveedor=seguro`
        );
        const proveedor = data.providers?.[0];
        if (!proveedor) return;

        const nuevoSeguro = {
          id: Date.now(),
          servicioId: seguroProducto.value,
          nombreServicio: seguroProducto.label,
          tipoArticulo: "seguro",
          proveedorId: proveedor.id,
          nombreProveedor: proveedor.name,
          proveedorCardcode: proveedor.cardcode,
          importe: parseFloat(seguroProducto.precioDefault || 0),
        };

        setServiciosAgregados((prev) => {
          if (prev.some((s) => s.tipoArticulo === "seguro")) return prev;
          const nuevos = [...prev, nuevoSeguro];
          if (setServiciosData) setServiciosData({ serviciosSeleccionados: nuevos });
          return nuevos;
        });

        // IVA del seguro. En exportación el seguro de mercancía va exento:
        // repercutido EX / soportado NS. En nacional se resuelve como hasta ahora
        // (por producto/residencia, con fallback 21%).
        const defaultIVA = esExportacion
          ? "EX"
          : resolveIVA({
              currentValue: undefined,
              product: seguroProducto,
              tipoResidente: clienteData?.tipoResidente,
              categoria: "repercutido",
              options: tipoIVAOptions,
            }) || "R21";
        setTiposIVAServicios((prev) => ({ ...prev, [nuevoSeguro.id]: defaultIVA }));
        const defaultIVASop = esExportacion
          ? "NS"
          : resolveIVA({
              currentValue: undefined,
              product: seguroProducto,
              tipoResidente: clienteData?.tipoResidente,
              categoria: "soportado",
              options: tipoIVASoportadoOptions,
            }) || "S21";
        setTiposIVAServiciosSoportado((prev) => ({ ...prev, [nuevoSeguro.id]: defaultIVASop }));
      } catch (e) {
        console.error("[ServiciosTab] Error precargando seguro:", e);
      }
    };

    precargarSeguro();
  }, [
    loadingServicios,
    loadingGuardados,
    requiereSeguroAuto,
    serviciosDisponibles,
    clienteData?.tipoResidente,
    clienteData?.tipoCliente,
    esExportacion,
    setServiciosData,
  ]);

  // El estado compartido lleva el tipo_iva de cada servicio (vive aparte en
  // tiposIVAServicios) para que Negociación/Revisión calculen el IVA por
  // servicio. Reescribe enriquecido tras cualquier cambio de servicios o IVA.
  useEffect(() => {
    if (!setServiciosData) return;
    setServiciosData({
      serviciosSeleccionados: serviciosAgregados.map((s) => ({
        ...s,
        tipo_iva: tiposIVAServicios[s.id] || s.tipo_iva || null,
      })),
    });
  }, [serviciosAgregados, tiposIVAServicios, setServiciosData]);

  /**
   * Handler cuando cambia el artículo seleccionado
   */
  const handleArticuloChange = useCallback(
    (value) => {
      const servicio = serviciosDisponibles.find((s) => s.value === value);

      setArticuloSeleccionado(servicio || null);
      setProveedorSeleccionado(null);
      setPrecioServicio(
        servicio?.precioDefault != null ? Number(servicio.precioDefault) : undefined
      );
      setProveedoresDisponibles([]);

      if (servicio?.tipoArticulo) {
        loadProveedores(servicio.tipoArticulo);
      }
    },
    [serviciosDisponibles, loadProveedores]
  );

  /**
   * Handler cuando cambia el proveedor seleccionado
   */
  const handleProveedorChange = useCallback(
    (value) => {
      const proveedor = proveedoresDisponibles.find((p) => p.value === value);
      setProveedorSeleccionado(proveedor || null);
    },
    [proveedoresDisponibles]
  );

  /**
   * Handler para agregar servicio a la tabla
   */
  const handleAgregarServicio = useCallback(() => {
    if (!articuloSeleccionado || !proveedorSeleccionado || precioServicio == null) {
      return;
    }

    const nuevoServicio = {
      id: Date.now(),
      servicioId: articuloSeleccionado.value,
      nombreServicio: articuloSeleccionado.label,
      tipoArticulo: articuloSeleccionado.tipoArticulo,
      proveedorId: proveedorSeleccionado.value,
      nombreProveedor: proveedorSeleccionado.label,
      proveedorCardcode: proveedorSeleccionado.cardcode,
      importe: precioServicio,
    };
    if (requiereSeguroAuto && nuevoServicio?.tipoArticulo === "seguro") {
      setSeguroEliminadoAviso(false);
    }
    const nuevosServicios = [...serviciosAgregados, nuevoServicio];
    setServiciosAgregados(nuevosServicios);

    // Resolve IVA repercutido from service product (Fase 4E)
    const defaultIVA = resolveIVA({
      currentValue: undefined,
      product: articuloSeleccionado,
      tipoResidente: clienteData?.tipoResidente,
      categoria: "repercutido",
      options: tipoIVAOptions,
    });
    if (defaultIVA) {
      setTiposIVAServicios({
        ...tiposIVAServicios,
        [nuevoServicio.id]: defaultIVA,
      });
    }

    // Resolve IVA soportado from service product (Fase 4E)
    const defaultIVASop = resolveIVA({
      currentValue: undefined,
      product: articuloSeleccionado,
      tipoResidente: clienteData?.tipoResidente,
      categoria: "soportado",
      options: tipoIVASoportadoOptions,
    });
    if (defaultIVASop) {
      setTiposIVAServiciosSoportado((prev) => ({
        ...prev,
        [nuevoServicio.id]: defaultIVASop,
      }));
    }

    if (setServiciosData) {
      setServiciosData({ serviciosSeleccionados: nuevosServicios });
    }

    // Reset del configurador
    setArticuloSeleccionado(null);
    setProveedorSeleccionado(null);
    setPrecioServicio(undefined);
    setProveedoresDisponibles([]);
  }, [
    articuloSeleccionado,
    proveedorSeleccionado,
    precioServicio,
    serviciosAgregados,
    setServiciosData,
    tiposIVAServicios,
    clienteData?.tipoResidente,
    tipoIVAOptions,
    tipoIVASoportadoOptions,
  ]);

  const handleProveedorRowChange = useCallback(
    (rowId, providerId) => {
      setServiciosAgregados((prev) => {
        const nuevos = prev.map((s) => {
          if (s.id !== rowId) return s;
          const opt = (proveedoresPorTipo[s.tipoArticulo] || []).find(
            (o) => o.value === providerId
          );
          return opt
            ? {
                ...s,
                proveedorId: opt.value,
                nombreProveedor: opt.label,
                proveedorCardcode: opt.cardcode,
              }
            : s;
        });
        if (setServiciosData) {
          setServiciosData({ serviciosSeleccionados: nuevos });
        }
        return nuevos;
      });
    },
    [proveedoresPorTipo, setServiciosData]
  );

  const handleImporteRowChange = useCallback(
    (rowId, value) => {
      const num = Math.round((parseFloat(value) || 0) * 100) / 100;
      setServiciosAgregados((prev) => {
        const nuevos = prev.map((s) =>
          s.id === rowId ? { ...s, importe: num } : s
        );
        if (setServiciosData) {
          setServiciosData({ serviciosSeleccionados: nuevos });
        }
        return nuevos;
      });
    },
    [setServiciosData]
  );

  /**
   * Handler para eliminar servicio de la tabla
   */
  const handleEliminarServicio = useCallback(
    (id) => {
      const servicio = serviciosAgregados.find((s) => s.id === id);
      if (requiereSeguroAuto && servicio?.tipoArticulo === "seguro") {
        setSeguroEliminadoAviso(true);
      }

      const nuevosServicios = serviciosAgregados.filter((s) => s.id !== id);
      setServiciosAgregados(nuevosServicios);

      if (setServiciosData) {
        setServiciosData({ serviciosSeleccionados: nuevosServicios });
      }
    },
    [serviciosAgregados, setServiciosData, requiereSeguroAuto]
  );

  /**
   * Handler para guardar y navegar al siguiente tab
   */
  const handleSaveAndNext = useCallback(async () => {
    setSaving(true);
    setSaveError(null);

    try {
      const dealId = context?.crm?.objectId;
      if (!dealId) {
        throw new Error(ERROR_MESSAGES.NO_DEAL_ID);
      }

      const servicioInvalido = serviciosAgregados.find(
        (s) => !(Number(s.importe) > 0)
      );
      if (servicioInvalido) {
        throw new Error(
          `El precio del servicio "${servicioInvalido.nombreServicio}" debe ser mayor que 0 €.`
        );
      }

      const payload = {
        services: serviciosAgregados.map((servicio) => ({
          product_id: servicio.servicioId,
          product_name: servicio.nombreServicio,
          provider_id: servicio.proveedorId,
          provider_cardcode: servicio.proveedorCardcode,
          price: Math.round(servicio.importe * 100) / 100,
          tipo_iva: tiposIVAServicios[servicio.id] || "",
          tipo_iva_soportado: tiposIVAServiciosSoportado[servicio.id] || "",
        })),
      };

      await fetchFromAPI(`${API_CONFIG.endpoints.deals}/${dealId}/services`, {
        method: "POST",
        body: payload,
      });

      onNavigate("negociacion");
    } catch (error) {
      setSaveError(error.message || ERROR_MESSAGES.SAVE_ERROR);
    } finally {
      setSaving(false);
    }
  }, [
    context?.crm?.objectId,
    serviciosAgregados,
    tiposIVAServicios,
    tiposIVAServiciosSoportado,
    onNavigate,
  ]);

  // Loading state
  if (loadingGuardados) {
    return (
      <Box>
        <Heading>Selección de Servicios</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_SERVICES_SAVED}</Text>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Heading>Selección de Servicios</Heading>

      <Flex direction="column" gap="medium">
        <Text>Configure los servicios adicionales para esta cotización:</Text>

        {/* SELECTOR DE ARTÍCULO DE SERVICIO */}
        {loadingServicios ? (
          <Flex direction="row" gap="small" align="center">
            <LoadingSpinner size="xs" />
            <Text>{LOADING_MESSAGES.LOADING_SERVICES}</Text>
          </Flex>
        ) : errorServicios ? (
          <Alert title="Error" variant="error">
            {errorServicios}
          </Alert>
        ) : (
          <Select
            label="Seleccionar Servicio"
            name="articulo-servicio"
            placeholder="Seleccionar..."
            value={articuloSeleccionado?.value || ""}
            onChange={handleArticuloChange}
            options={serviciosDisponibles.filter(
              (s) =>
                !serviciosAgregados.some(
                  (agregado) => agregado.servicioId === s.value
                )
            )}
            description="Elija el tipo de servicio que desea agregar"
          />
        )}

        {/* CONFIGURADOR DEL SERVICIO (se muestra solo si hay artículo seleccionado) */}
        {articuloSeleccionado && (
          <Flex direction="column" gap="medium">
            {loadingProveedores ? (
              <Flex direction="row" gap="small" align="center">
                <LoadingSpinner size="xs" />
                <Text>{LOADING_MESSAGES.LOADING_PROVIDERS}</Text>
              </Flex>
            ) : (
              <Select
                label="Proveedor del Servicio"
                name="proveedor-servicio"
                placeholder="Seleccionar..."
                value={proveedorSeleccionado?.value || ""}
                onChange={handleProveedorChange}
                options={proveedoresDisponibles}
                description="Seleccione el proveedor que realizará el servicio"
              />
            )}

            <NumberInput
              label="Precio del Servicio (€)"
              name="precio-servicio"
              placeholder=""
              value={precioServicio}
              onChange={setPrecioServicio}
              description="Introduzca el importe del servicio en euros"
              min={0}
              precision={2}
              formatStyle="decimal"
            />

            <ButtonRow>
              <Button
                variant="primary"
                onClick={handleAgregarServicio}
                disabled={!proveedorSeleccionado || precioServicio == null}
              >
                Añadir Servicio
              </Button>
            </ButtonRow>
          </Flex>
        )}

        <Divider size="medium" />

        <Box>
          <Heading>Servicios Seleccionados</Heading>

          {serviciosAgregados.length === 0 ? (
            <Alert title="Sin servicios" variant="warning">
              No hay servicios agregados a la cotización
            </Alert>
          ) : (
            <Table bordered={true}>
              <TableHead>
                <TableRow>
                  <TableHeader width="min">Servicio</TableHeader>
                  <TableHeader width={220}>Proveedor</TableHeader>
                  <TableHeader width="min">Importe (€)</TableHeader>
                  <TableHeader width={200}>IVA (Rep./Sop.)</TableHeader>
                  <TableHeader width="min">Acciones</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {serviciosAgregados.map((servicio) => {
                  const proveedoresOpts =
                    proveedoresPorTipo[servicio.tipoArticulo] || [];
                  const proveedorEnCache = proveedoresOpts.some(
                    (o) => o.value === servicio.proveedorId
                  );
                  const rawProveedorOptions = proveedorEnCache
                    ? proveedoresOpts
                    : [
                        ...proveedoresOpts,
                        ...(servicio.proveedorId
                          ? [
                              {
                                value: servicio.proveedorId,
                                label: servicio.nombreProveedor || "(actual)",
                                cardcode: servicio.proveedorCardcode || "",
                              },
                            ]
                          : []),
                      ];
                  const proveedorOptions = rawProveedorOptions.map((o) => ({
                    ...o,
                    label: o.label
                  }));

                  return (
                    <TableRow key={servicio.id}>
                      <TableCell width="min">{servicio.nombreServicio}</TableCell>
                      <TableCell width={220}>
                        <Select
                          name={`proveedor-${servicio.id}`}
                          value={servicio.proveedorId || ""}
                          onChange={(value) =>
                            handleProveedorRowChange(servicio.id, value)
                          }
                          options={proveedorOptions}
                          placeholder="Seleccionar..."
                        />
                      </TableCell>
                      <TableCell width="min">
                        <NumberInput
                          name={`importe-${servicio.id}`}
                          value={servicio.importe}
                          onChange={(value) =>
                            handleImporteRowChange(servicio.id, value)
                          }
                          min={0}
                          precision={2}
                          formatStyle="decimal"
                        />
                      </TableCell>
                      <TableCell width={200}>
                        <Flex direction="column" gap="small">
                          <Flex direction="column" gap="extra-small">
                            <Text
                              variant="microcopy"
                              format={{ fontWeight: "demibold" }}
                            >
                              Repercutido
                            </Text>
                            <Select
                              name={`tipo-iva-${servicio.id}`}
                              value={tiposIVAServicios[servicio.id] || ""}
                              onChange={(value) =>
                                setTiposIVAServicios({
                                  ...tiposIVAServicios,
                                  [servicio.id]: value,
                                })
                              }
                              options={tipoIVAOptions}
                              placeholder="Seleccionar..."
                            />
                          </Flex>
                          <Flex direction="column" gap="extra-small">
                            <Text
                              variant="microcopy"
                              format={{ fontWeight: "demibold" }}
                            >
                              Soportado
                            </Text>
                            <Select
                              name={`tipo-iva-sop-${servicio.id}`}
                              value={
                                tiposIVAServiciosSoportado[servicio.id] || ""
                              }
                              onChange={(value) =>
                                setTiposIVAServiciosSoportado({
                                  ...tiposIVAServiciosSoportado,
                                  [servicio.id]: value,
                                })
                              }
                              options={tipoIVASoportadoOptions}
                              placeholder="Seleccionar..."
                            />
                          </Flex>
                        </Flex>
                      </TableCell>
                      <TableCell width="min">
                        <Button
                          variant="destructive"
                          size="xs"
                          onClick={() => handleEliminarServicio(servicio.id)}
                        >
                          Eliminar
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}

          {serviciosAgregados.length > 0 && (
            <Box>
              <Text format={{ fontWeight: "bold" }}>
                Total Servicios: €
                {serviciosAgregados
                  .reduce((total, servicio) => total + servicio.importe, 0)
                  .toLocaleString("es-ES", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}€
              </Text>
            </Box>
          )}
        </Box>

        {seguroEliminadoAviso && isCIF && (
          <Alert title="Cotización sin seguro" variant="warning">
            Al no incluir el seguro, esta cotización se generará como tipo CFR
            (sin seguro incluido), no como CIF. Si desea mantener el incoterm
            CIF, añada de nuevo el servicio de seguro.
          </Alert>
        )}
        {seguroEliminadoAviso && !isCIF && requiereSeguroAuto && (
          <Alert title="Cotización sin seguro" variant="warning">
            Al no incluir el seguro, esta cotización se generará sin el coste
            del seguro incluido en el precio. Si desea mantenerlo, añada de
            nuevo el servicio.
          </Alert>
        )}

        {saveError && (
          <Alert title="Error al guardar" variant="error">
            {saveError}
          </Alert>
        )}

        <ButtonRow>
          <Button variant="secondary" onClick={() => onNavigate("transitario")}>
            Anterior
          </Button>
          <Button
            variant="primary"
            onClick={handleSaveAndNext}
            disabled={saving}
          >
            {saving ? LOADING_MESSAGES.SAVING : "Siguiente"}
          </Button>
        </ButtonRow>
      </Flex>
    </Box>
  );
};

export default ServiciosTab;
