import { useState, useCallback, useEffect } from "react";
import {
  Box,
  Heading,
  Form,
  Select,
  NumberInput,
  Divider,
  Flex,
  Button,
  LoadingSpinner,
  Text,
  Alert,
  SearchInput,
  Tile,
  Tag,
} from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";
import { API_CONFIG } from "../config";
import {
  isValuePresent,
  getClienteValidationMessages,
} from "../utils/validation.utils";
import {
  ERROR_MESSAGES,
  LOADING_MESSAGES,
  PERCENTAGE_CONVERSION_FACTOR,
} from "../constants/common.constants";

const ClienteTab = ({
  context,
  onNavigate,
  clienteData,
  setClienteData,
  tipoVentaOptions,
  tipoContenedorOptions,
  tipoClienteOptions,
  idiomaOptions,
  proyectoOptions,
  repercutirAClienteOptions,
  tipoComisionOptions,
  tipoIVAOptions = [],
  loading,
}) => {
  // Destructure clienteData
  const {
    nombreCliente,
    nombreEmpresa,
    country,
    tipoVenta,
    tipoContenedor,
    tipoCliente,
    margen,
    idioma,
    proyecto,
    proveedorComision,
    codigoIntermediario,
    repercutirACliente,
    tipoComision,
    importeComision,
    tipoIVAComision,
  } = clienteData;

  // Helper para actualizar campos select con estructura {value, options}
  const updateSelectField = useCallback(
    (field, value, options) => {
      setClienteData({
        ...clienteData,
        [field]: { value, options },
      });
    },
    [clienteData, setClienteData]
  );

  // UI state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Commission provider search state
  const [searchCommissionProvider, setSearchCommissionProvider] = useState("");
  const [commissionProviders, setCommissionProviders] = useState([]);
  const [loadingProviders, setLoadingProviders] = useState(false);

  /**
   * Update a single field in clienteData
   */
  const updateField = useCallback(
    (field, value) => {
      setClienteData({ ...clienteData, [field]: value });
    },
    [clienteData, setClienteData]
  );

  /**
   * Search for commission providers
   */
  useEffect(() => {
    const searchProviders = async () => {
      if (!searchCommissionProvider) {
        setCommissionProviders([]);
        return;
      }

      try {
        setLoadingProviders(true);
        const url = `${
          API_CONFIG.baseURL
        }/api/v1/deals/commission-providers/search?q=${encodeURIComponent(
          searchCommissionProvider
        )}`;
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
        });

        if (!response.ok) {
          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
          setCommissionProviders(data.providers || []);
        }
      } catch (err) {
        console.error("Error searching commission providers:", err);
        setCommissionProviders([]);
      } finally {
        setLoadingProviders(false);
      }
    };

    const timeoutId = setTimeout(() => {
      searchProviders();
    }, 300); // Debounce 300ms

    return () => clearTimeout(timeoutId);
  }, [searchCommissionProvider]);

  /**
   * Handle commission provider selection - preload all fields
   */
  const handleSelectCommissionProvider = useCallback(
    (provider) => {
      setClienteData({
        ...clienteData,
        proveedorComision: provider, // Store full provider object to show name
        codigoIntermediario: provider.cardcode || "",
        repercutirACliente: provider.repercutir_a_cliente || "",
        tipoComision: provider.tipo_comision || "",
        importeComision: provider.importe_comision || 0,
      });
      setSearchCommissionProvider("");
      setCommissionProviders([]);
    },
    [clienteData, setClienteData]
  );

  /**
   * Handle deselecting commission provider
   */
  const handleDeselectCommissionProvider = useCallback(() => {
    setClienteData({
      ...clienteData,
      proveedorComision: null,
      codigoIntermediario: "",
      repercutirACliente: "",
      tipoComision: "",
      importeComision: 0,
    });
  }, [clienteData, setClienteData]);

  /**
   * Auto-select default IVA type based on tipoResidente
   */
  useEffect(() => {
    if (!tipoIVAComision && clienteData?.tipoResidente && tipoIVAOptions.length > 0) {
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
      const ivaOption = tipoIVAOptions.find(opt => opt.value === defaultIVA);
      if (ivaOption) {
        setClienteData({
          ...clienteData,
          tipoIVAComision: defaultIVA,
        });
      }
    }
  }, [clienteData?.tipoResidente, tipoIVAOptions, tipoIVAComision, clienteData, setClienteData]);

  /**
   * Build the payload for updating client and deal data
   * @returns {Object} - Payload with contact and deal fields
   */
  const buildUpdatePayload = useCallback(() => {
    const payload = {};
    const contact = {};
    const company = {};
    const deal = {};

    // Company fields
    const tipoClienteValue = tipoCliente?.value || tipoCliente;
    if (isValuePresent(tipoClienteValue)) {
      company.tipo_cliente = tipoClienteValue;
    }

    // Deal fields
    const tipoVentaValue = tipoVenta?.value || tipoVenta;
    if (isValuePresent(tipoVentaValue)) {
      deal.tipo_de_venta = tipoVentaValue;
    }

    const tipoContenedorValue = tipoContenedor?.value || tipoContenedor;
    if (isValuePresent(tipoContenedorValue)) {
      deal.tipo_contenedor = tipoContenedorValue;
    }

    if (isValuePresent(margen)) {
      // Convert percentage to HubSpot format (divide by 100)
      // e.g., 12% is sent as 0.12
      deal.margen_cliente = Number(margen) / PERCENTAGE_CONVERSION_FACTOR;
    }

    const idiomaValue = idioma?.value || idioma;
    if (isValuePresent(idiomaValue)) {
      deal.preprints_language = idiomaValue;
    }

    const proyectoValue = proyecto?.value || proyecto;
    if (isValuePresent(proyectoValue)) {
      deal.project = proyectoValue;
    }

    // New editable intermediary fields
    if (isValuePresent(codigoIntermediario)) {
      deal.codigo_intermediario = codigoIntermediario;
    }

    const repercutirAClienteValue = repercutirACliente?.value || repercutirACliente;
    if (isValuePresent(repercutirAClienteValue)) {
      deal.repercutir_a_cliente = repercutirAClienteValue;
    }

    const tipoComisionValue = tipoComision?.value || tipoComision;
    if (isValuePresent(tipoComisionValue)) {
      deal.tipo_comision = tipoComisionValue;
    }

    if (isValuePresent(importeComision)) {
      deal.importe_comision = Number(importeComision);
    }

    const tipoIVAComisionValue = tipoIVAComision?.value || tipoIVAComision;
    if (isValuePresent(tipoIVAComisionValue)) {
      deal.tipo_iva_comision = tipoIVAComisionValue;
    }

    if (Object.keys(contact).length) payload.contact = contact;
    if (Object.keys(company).length) payload.company = company;
    if (Object.keys(deal).length) payload.deal = deal;

    return payload;
  }, [
    tipoVenta,
    tipoContenedor,
    tipoCliente,
    margen,
    idioma,
    proyecto,
    codigoIntermediario,
    repercutirACliente,
    tipoComision,
    importeComision,
    tipoIVAComision,
  ]);

  /**
   * Handle form submission and navigation to next tab
   */
  const handleNext = useCallback(async () => {
    try {
      setError(null);

      const dealId = context?.crm?.objectId;
      if (!dealId) {
        setError(ERROR_MESSAGES.NO_DEAL_ID);
        return;
      }

      const payload = buildUpdatePayload();

      // If nothing to update, just navigate
      if (!payload.contact && !payload.company && !payload.deal) {
        onNavigate("articulos");
        return;
      }

      setIsSubmitting(true);

      const response = await fetchFromAPI(`/api/v1/deals/${dealId}/client`, {
        method: "POST",
        body: payload,
      });

      if (!response.success) {
        throw new Error(`Error ${response.status}: ${response.statusText}`);
      }

      // Success: navigate to Articles
      onNavigate("articulos");
    } catch (err) {
      setError(err.message || ERROR_MESSAGES.SAVE_ERROR);
    } finally {
      setIsSubmitting(false);
    }
  }, [context, buildUpdatePayload, onNavigate]);

  // Get validation messages
  const validationMessages = getClienteValidationMessages(clienteData);
  const hasValidationErrors = validationMessages.length > 0;

  // Loading state
  if (loading) {
    return (
      <Box>
        <Heading>Información general del cliente</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_CLIENT}</Text>
        </Flex>
      </Box>
    );
  }
  
  return (
    <Box>
      {/* Error Alert */}
      {error && (
        <Box marginBottom="small">
          <Alert title="No se pudo guardar" variant="error">
            {error}
          </Alert>
        </Box>
      )}

      <Form>
        {/* Client Data Section - Wrapped separately for width control */}
        <Flex direction="row" justify="start" gap="medium">
          <Tile>
            <Flex direction="column" gap="small">
              <Heading>Información general del cliente</Heading>
              <Divider size="small" />

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>
                  Nombre del Cliente:
                </Text>
                <Text>{nombreCliente}</Text>
              </Flex>

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>
                  País de Facturación:
                </Text>
                <Text>{country || "No especificado"}</Text>
              </Flex>

              <Flex direction="row" justify="start" align="center" gap="large">
                <Text format={{ fontWeight: "demibold" }}>
                  Nombre de la Empresa:
                </Text>
                <Text>{nombreEmpresa}</Text>
              </Flex>
            </Flex>
          </Tile>
        </Flex>

        {/* Form Inputs Section - Full width */}
        <Flex direction="column" gap="medium">
          {/* Sale Configuration */}
          <Select
            label="Tipo de Venta"
            name="tipo-venta"
            description="Seleccione el Incoterm aplicable a esta cotización"
            placeholder="Seleccione un tipo de venta..."
            required={true}
            value={tipoVenta?.value || tipoVenta}
            onChange={(value) => updateSelectField("tipoVenta", value, tipoVentaOptions)}
            options={tipoVentaOptions}
            tooltip="EXW: Cliente recoge en origen | FOB/CFR/CIF: Envío marítimo | DAP: Entrega en destino"
          />

          <Select
            label="Tipo de Contenedor"
            name="tipo-contenedor"
            description="Seleccione el tipo de contenedor para el envío"
            placeholder="Seleccione un tipo de contenedor..."
            required={true}
            value={tipoContenedor?.value || tipoContenedor}
            onChange={(value) => updateSelectField("tipoContenedor", value, tipoContenedorOptions)}
            options={tipoContenedorOptions}
            tooltip="Tipo de contenedor o vehículo utilizado para el transporte"
          />

          <Select
            label="Tipo de Cliente"
            name="tipo-cliente"
            description="Clasificación del cliente (nacional o exportación)"
            placeholder="Seleccione un tipo de cliente..."
            required={true}
            value={tipoCliente?.value || tipoCliente}
            onChange={(value) => updateSelectField("tipoCliente", value, tipoClienteOptions)}
            options={tipoClienteOptions}
            tooltip="Determina si el cliente es nacional o de exportación"
          />

          {/* Commercial Conditions */}
          <NumberInput
            label="% Margen"
            name="margen"
            description="Porcentaje de margen comercial a aplicar"
            placeholder="0"
            value={margen}
            onChange={(value) => updateField("margen", value)}
            min={0}
            max={100}
            precision={2}
            formatStyle="percentage"
            tooltip="Margen de beneficio sobre el coste total"
          />

          {/* Document Language */}
          <Select
            label="Idioma"
            name="idioma"
            description="Idioma para la impresión de documentos"
            placeholder="Seleccione un idioma..."
            required={true}
            value={idioma?.value || idioma}
            onChange={(value) => updateSelectField("idioma", value, idiomaOptions)}
            options={idiomaOptions}
            tooltip="Idioma en el que se imprimirán los documentos de la cotización"
          />

          {/* Project Selection */}
          <Select
            label="Proyecto"
            name="proyecto"
            description="Proyecto o canal de origen"
            placeholder="Seleccione un proyecto..."
            value={proyecto?.value || proyecto}
            onChange={(value) => updateSelectField("proyecto", value, proyectoOptions)}
            options={proyectoOptions}
            tooltip="Proyecto o canal de origen asociado a esta cotización"
          />

          <Divider size="small" />

          {/* Intermediary and Commission Section */}
          <Heading>Intermediario y Comisiones</Heading>

          {/* Show selected provider or search */}
          {proveedorComision ? (
            <Tile>
              <Flex direction="column" gap="small">
                <Flex direction="row" justify="space-between" align="center">
                  <Text format={{ fontWeight: "bold" }}>
                    Intermediario seleccionado:
                  </Text>
                  <Button
                    size="xs"
                    variant="secondary"
                    onClick={handleDeselectCommissionProvider}
                  >
                    Cambiar
                  </Button>
                </Flex>

                <Flex direction="row" align="center" gap="medium">
                  <Text format={{ fontWeight: "demibold" }}>
                    {proveedorComision.name}
                  </Text>
                  <Tag variant="default">
                    {proveedorComision.cardcode || "Sin código"}
                  </Tag>
                </Flex>
              </Flex>
            </Tile>
          ) : (
            <>
              {/* Search for intermediary */}
              <SearchInput
                label="Buscar Intermediario"
                name="buscar-intermediario"
                placeholder="Buscar intermediario por nombre..."
                value={searchCommissionProvider}
                onChange={(value) => setSearchCommissionProvider(value)}
                description="Busque un intermediario para precargar los campos de comisión"
                tooltip="El código, tipo y valores se cargarán automáticamente al seleccionar"
              />

              {/* Show search results */}
              {loadingProviders && (
                <Flex direction="row" align="center" gap="small">
                  <LoadingSpinner size="xs" />
                  <Text>Buscando intermediarios...</Text>
                </Flex>
              )}

              {!loadingProviders && commissionProviders.length > 0 && (
                <Flex direction="column" gap="small">
                  <Text format={{ fontWeight: "bold" }}>Resultados:</Text>
                  {commissionProviders.map((provider) => (
                    <Flex
                      key={provider.id}
                      direction="column"
                      gap="extra-small"
                    >
                      <Flex direction="row" gap="small" align="center">
                        <Text format={{ fontWeight: "demibold" }}>
                          {provider.name}
                        </Text>
                        <Button
                          size="xs"
                          variant="secondary"
                          onClick={() => handleSelectCommissionProvider(provider)}
                        >
                          Seleccionar
                        </Button>
                      </Flex>
                      <Text>Código: {provider.cardcode || "N/A"}</Text>
                    </Flex>
                  ))}
                </Flex>
              )}

              {!loadingProviders &&
                searchCommissionProvider &&
                commissionProviders.length === 0 && (
                  <Text>
                    No se encontraron intermediarios que coincidan con "
                    {searchCommissionProvider}"
                  </Text>
                )}
            </>
          )}

          <Divider size="small" />

          {/* Editable Commission Fields - Solo mostrar si hay intermediario seleccionado */}
          {proveedorComision && (
            <>
              <Select
                label="Repercutir a Cliente"
                name="repercutir-a-cliente"
                description="¿Se repercute la comisión al cliente?"
                placeholder="Seleccione una opción..."
                value={repercutirACliente?.value || repercutirACliente}
                onChange={(value) => updateSelectField("repercutirACliente", value, repercutirAClienteOptions)}
                options={repercutirAClienteOptions}
                tooltip="SÍ = Aumenta EXW | NO = Reduce margen Proex"
              />

              <Select
                label="Tipo de Comisión"
                name="tipo-comision"
                description="Tipo de comisión a aplicar"
                placeholder="Seleccione un tipo..."
                value={tipoComision?.value || tipoComision}
                onChange={(value) => updateSelectField("tipoComision", value, tipoComisionOptions)}
                options={tipoComisionOptions}
                tooltip="Define cómo se calcula la comisión"
              />

              <NumberInput
                label="Importe Comisión (EUR)"
                name="importe-comision"
                description="Importe de la comisión en euros"
                placeholder="0.000"
                value={importeComision}
                onChange={(value) => updateField("importeComision", value)}
                min={0}
                precision={3}
                tooltip="Importe de la comisión en EUR"
              />

              <Select
                label="Tipo IVA Comisión"
                name="tipo-iva-comision"
                description="Tipo de IVA a aplicar a la comisión"
                placeholder="Seleccione un tipo de IVA..."
                value={tipoIVAComision?.value || tipoIVAComision}
                onChange={(value) => updateSelectField("tipoIVAComision", value, tipoIVAOptions)}
                options={tipoIVAOptions}
                tooltip="Define el tipo de IVA que se aplicará a la comisión del intermediario"
              />

              <Divider size="small" />
            </>
          )}

          {/* Validation Warning */}
          {hasValidationErrors && (
            <Alert title="Información Requerida" variant="warning">
              <Flex direction="column" gap="small">
                {validationMessages.map((message, index) => (
                  <Text key={index}>• {message}</Text>
                ))}
              </Flex>
            </Alert>
          )}

          {/* Navigation Button */}
          <Flex direction="row" justify="start">
            <Button
              variant="primary"
              onClick={handleNext}
              disabled={hasValidationErrors || isSubmitting}
              loading={isSubmitting}
            >
              {isSubmitting ? "Guardando..." : "Siguiente"}
            </Button>
          </Flex>
        </Flex>
      </Form>
    </Box>
  );
};

export default ClienteTab;
