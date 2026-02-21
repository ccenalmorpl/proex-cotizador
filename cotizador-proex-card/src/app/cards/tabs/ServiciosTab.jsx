// React
import React, { useState, useEffect, useCallback } from "react";

// HubSpot UI Components
import {
  Box,
  Heading,
  Flex,
  Divider,
  ButtonRow,
  Button,
  Select,
  Input,
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
  clienteData,
}) => {
  // Estado para los servicios disponibles (cargados desde API)
  const [serviciosDisponibles, setServiciosDisponibles] = useState([]);
  const [loadingServicios, setLoadingServicios] = useState(true);
  const [errorServicios, setErrorServicios] = useState(null);

  // Estado para los proveedores del servicio seleccionado
  const [proveedoresDisponibles, setProveedoresDisponibles] = useState([]);
  const [loadingProveedores, setLoadingProveedores] = useState(false);

  // Estado para el selector de artículo de servicio
  const [articuloSeleccionado, setArticuloSeleccionado] = useState(null);

  // Estado para el configurador del servicio
  const [proveedorSeleccionado, setProveedorSeleccionado] = useState(null);
  const [precioServicio, setPrecioServicio] = useState("");

  // Estado para la tabla de servicios agregados
  const [serviciosAgregados, setServiciosAgregados] = useState([]);

  // Estado para tipos de IVA por servicio (key: servicio.id, value: tipo_iva)
  const [tiposIVAServicios, setTiposIVAServicios] = useState({});

  // Estado para carga inicial de servicios guardados
  const [loadingGuardados, setLoadingGuardados] = useState(true);

  // Estado para el guardado
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

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
   */
  const loadProveedores = useCallback(async (tipoArticulo) => {
    if (!tipoArticulo) {
      setProveedoresDisponibles([]);
      return;
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
    } catch {
      setProveedoresDisponibles([]);
    } finally {
      setLoadingProveedores(false);
    }
  }, []);

  // Cargar servicios disponibles y servicios guardados al montar el componente
  useEffect(() => {
    loadServicios();
    loadServiciosGuardados();
  }, [loadServicios, loadServiciosGuardados]);

  /**
   * Handler cuando cambia el artículo seleccionado
   */
  const handleArticuloChange = useCallback(
    (value) => {
      const servicio = serviciosDisponibles.find((s) => s.value === value);

      setArticuloSeleccionado(servicio || null);
      setProveedorSeleccionado(null);
      setPrecioServicio(
        servicio?.precioDefault ? String(servicio.precioDefault) : ""
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
   * Get default IVA type based on tipoResidente
   */
  const getDefaultIVA = useCallback(() => {
    if (!clienteData?.tipoResidente || tipoIVAOptions.length === 0) {
      return "";
    }

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
    return ivaOption ? defaultIVA : "";
  }, [clienteData?.tipoResidente, tipoIVAOptions]);

  /**
   * Handler para agregar servicio a la tabla
   */
  const handleAgregarServicio = useCallback(() => {
    if (!articuloSeleccionado || !proveedorSeleccionado || !precioServicio) {
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
      importe: parseFloat(precioServicio),
    };

    const nuevosServicios = [...serviciosAgregados, nuevoServicio];
    setServiciosAgregados(nuevosServicios);

    // Auto-select default IVA for the new service
    const defaultIVA = getDefaultIVA();
    if (defaultIVA) {
      setTiposIVAServicios({
        ...tiposIVAServicios,
        [nuevoServicio.id]: defaultIVA,
      });
    }

    if (setServiciosData) {
      setServiciosData({ serviciosSeleccionados: nuevosServicios });
    }

    // Reset del configurador
    setArticuloSeleccionado(null);
    setProveedorSeleccionado(null);
    setPrecioServicio("");
    setProveedoresDisponibles([]);
  }, [
    articuloSeleccionado,
    proveedorSeleccionado,
    precioServicio,
    serviciosAgregados,
    setServiciosData,
    getDefaultIVA,
    tiposIVAServicios,
  ]);

  /**
   * Handler para eliminar servicio de la tabla
   */
  const handleEliminarServicio = useCallback(
    (id) => {
      const nuevosServicios = serviciosAgregados.filter(
        (servicio) => servicio.id !== id
      );
      setServiciosAgregados(nuevosServicios);

      if (setServiciosData) {
        setServiciosData({ serviciosSeleccionados: nuevosServicios });
      }
    },
    [serviciosAgregados, setServiciosData]
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

      const payload = {
        services: serviciosAgregados.map((servicio) => ({
          product_id: servicio.servicioId,
          product_name: servicio.nombreServicio,
          provider_id: servicio.proveedorId,
          provider_cardcode: servicio.proveedorCardcode,
          price: servicio.importe,
          tipo_iva: tiposIVAServicios[servicio.id] || "",
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
            placeholder="Seleccione un servicio..."
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
            {/* Select de proveedores */}
            {loadingProveedores ? (
              <Flex direction="row" gap="small" align="center">
                <LoadingSpinner size="xs" />
                <Text>{LOADING_MESSAGES.LOADING_PROVIDERS}</Text>
              </Flex>
            ) : (
              <Select
                label="Proveedor del Servicio"
                name="proveedor-servicio"
                placeholder="Seleccione un proveedor..."
                value={proveedorSeleccionado?.value || ""}
                onChange={handleProveedorChange}
                options={proveedoresDisponibles}
                description="Seleccione el proveedor que realizará el servicio"
              />
            )}

            {/* Input de precio */}
            <Input
              label="Precio del Servicio (€)"
              name="precio-servicio"
              placeholder="0.00"
              value={precioServicio}
              onChange={setPrecioServicio}
              description="Introduzca el importe del servicio en euros"
            />

            {/* Botón de añadir */}
            <ButtonRow>
              <Button
                variant="primary"
                onClick={handleAgregarServicio}
                disabled={!proveedorSeleccionado || !precioServicio}
              >
                Añadir Servicio
              </Button>
            </ButtonRow>
          </Flex>
        )}

        <Divider size="medium" />

        {/* TABLA DE SERVICIOS SELECCIONADOS */}
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
                  <TableHeader>Servicio</TableHeader>
                  <TableHeader>Proveedor</TableHeader>
                  <TableHeader>Importe (€)</TableHeader>
                  <TableHeader>Tipo IVA</TableHeader>
                  <TableHeader>Acciones</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {serviciosAgregados.map((servicio) => (
                  <TableRow key={servicio.id}>
                    <TableCell>{servicio.nombreServicio}</TableCell>
                    <TableCell>{servicio.nombreProveedor}</TableCell>
                    <TableCell>
                      {servicio.importe.toLocaleString("es-ES", {
                        minimumFractionDigits: 3,
                        maximumFractionDigits: 3,
                      })}€
                    </TableCell>
                    <TableCell>
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
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="destructive"
                        size="xs"
                        onClick={() => handleEliminarServicio(servicio.id)}
                      >
                        Eliminar
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
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
                    minimumFractionDigits: 3,
                    maximumFractionDigits: 3,
                  })}€
              </Text>
            </Box>
          )}
        </Box>

        {/* Error de guardado */}
        {saveError && (
          <Alert title="Error al guardar" variant="error">
            {saveError}
          </Alert>
        )}

        {/* Botones de navegación */}
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
