// React core
import React, { useState, useEffect, useCallback } from "react";

// HubSpot UI Extensions
import { Tabs, Tab, Box, Flex, Alert } from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";

// Utils and Constants
import { fetchFromAPI, cleanHTMLToText, fetchTipoIVA } from "./utils/api.utils";
import {
  ERROR_MESSAGES,
  PERCENTAGE_CONVERSION_FACTOR,
} from "./constants/common.constants";

// Types
import type {
  SelectOption,
  ClienteData,
  ArticuloItem,
  ContenedoresData,
  ContenedoresUnicosData,
  TransitarioData,
  ServiciosData,
  DocumentacionData,
  ClientAPIResponse,
  DocumentacionAPIResponse,
  ExtensionProps,
} from "./types/common.types";

// Tab Components
import ClienteTab from "./tabs/ClienteTab";
import ArticulosTab from "./tabs/ArticulosTab";
import ContenedoresTab from "./tabs/ContenedoresTab";
import TransitarioTab from "./tabs/TransitarioTab";
import DocumentacionTab from "./tabs/DocumentacionTab";
import RevisionTab from "./tabs/RevisionTab";
import ServiciosTab from "./tabs/ServiciosTab";
import NegociacionTab from "./tabs/NegociacionTab";

hubspot.extend(({ context, actions }) => (
  <Extension context={context} actions={actions} />
));

const Extension: React.FC<ExtensionProps> = ({ context, actions }) => {
  // UI state
  const [selectedTab, setSelectedTab] = useState<string>("cliente");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [noContactAssociated, setNoContactAssociated] =
    useState<boolean>(false);

  // Data state - Cliente
  const [clienteData, setClienteData] = useState<ClienteData>({
    nombreCliente: "",
    nombreEmpresa: "",
    country: "",
    tipoVenta: "",
    tipoContenedor: "",
    tipoCliente: "",
    margen: 12,
    idioma: "ES",
    proyecto: "",
    lugarCarga: "",
    lugarCargaLabel: "",
    proveedorComision: null,
    codigoIntermediario: "",
    repercutirACliente: "",
    tipoComision: "",
    importeComision: 0,
    tipoIVAComision: "",
    tipoResidente: "",
    cif: "",
    paisOperaciones: "",
  });

  // Data state - Dynamic options
  const [tipoVentaOptions, setTipoVentaOptions] = useState<SelectOption[]>([]);
  const [tipoContenedorOptions, setTipoContenedorOptions] = useState<
    SelectOption[]
  >([]);
  const [tipoClienteOptions, setTipoClienteOptions] = useState<SelectOption[]>(
    []
  );
  const [navieraOptions, setNavieraOptions] = useState<SelectOption[]>([]);
  const [idiomaOptions, setIdiomaOptions] = useState<SelectOption[]>([]);
  const [proyectoOptions, setProyectoOptions] = useState<SelectOption[]>([]);
  const [repercutirAClienteOptions, setRepercutirAClienteOptions] = useState<
    SelectOption[]
  >([]);
  const [tipoComisionOptions, setTipoComisionOptions] = useState<
    SelectOption[]
  >([]);

  // Data state - Artículos
  const [articulosSeleccionados, setArticulosSeleccionados] = useState<
    ArticuloItem[]
  >([]);
  const [tipoIVAOptions, setTipoIVAOptions] = useState<SelectOption[]>([]);
  const [ivaData, setIvaData] = useState<Record<string, any>>({});

  // Data state - Contenedores
  const [contenedoresData, setContenedoresData] = useState<ContenedoresData>({
    contenedoresDefinidos: [],
    cantidadesContenedores: {},
    totales: { totalColis: 0, totalPesoNeto: 0, totalContenedores: 0 },
  });

  const [contenedoresUnicos, setContenedoresUnicos] =
    useState<ContenedoresUnicosData>({
      contenedoresUnicos: [],
    });

  // Data state - Negociación (shared pricing state)
  const [preciosEXW, setPreciosEXW] = useState<Record<string, number>>({});
  const [tiposIVA, setTiposIVA] = useState<Record<string, string>>({});

  // Data state - Transitario
  const [transitarioData, setTransitarioData] = useState<TransitarioData>({
    transitarioSeleccionado: null,
    puertoSalida: "",
    puertoDestino: "",
    localidadDestino: "",
    precioOrigen: 0,
    precioDestino: 0,
    fechaValidez: null,
    diasLibres: 0,
    naviera: "",
    almacen: "",
    cotizacionRapida: false,
    tipoIVAFletes: "",
  });

  // Data state - Servicios
  const [serviciosData, setServiciosData] = useState<ServiciosData>({
    serviciosSeleccionados: [],
  });

  // Data state - Documentación
  const [documentacionData, setDocumentacionData] = useState<DocumentacionData>(
    {
      // Idioma global
      idiomaCotizacion: null,

      // Documentación
      direccionFiscal: "",

      // Documentación Logística
      tramitarCarga: false,
      documentosAGenerar: null,
      textoEnBl: "",
      comentariosLogistica: "",
      comentariosTransitario: "",

      // Certificado de Origen
      consignee: "",
      shipper: "",
      notify: "",
      certificadoOrigenMercancia: "",

      // Método de Pago & Textos
      metodoPagoPlantilla: null,
      bancoCobro: null,
      cotizacionRapida: false,
      textoAdicionalMetodoPago: "",
      textoFirmaAceptacionCliente: "",

      // Textos Generados
      textoBanco: "",
      textoFormaPago: "",
      textoInfoExpediente: "",
      textoBaseOferta: "",
    }
  );

  /**
   * Check if error message indicates missing contact association
   *
   * @param errorMsg - Error message to check
   * @returns true if error is related to missing contact
   */
  const isContactError = useCallback((errorMsg: string): boolean => {
    const lowerMsg = errorMsg.toLowerCase();
    return (
      lowerMsg.includes("contacto") ||
      lowerMsg.includes("contact") ||
      lowerMsg.includes("asociad")
    );
  }, []);

  /**
   * Fetch client data on component mount
   *
   * This function:
   * 1. Retrieves deal, contact, and company data from the API
   * 2. Builds commission provider object if intermediary data exists
   * 3. Populates all form options (tipo de venta, contenedor, idioma, etc.)
   * 4. Handles contact association errors gracefully
   * 5. Converts percentage formats from HubSpot (0.12) to display (12)
   */
  useEffect(() => {
    const dealIdOnMount = context?.crm?.objectId;
    console.log("[Cotizador] useEffect fetchClientData: dealId =", dealIdOnMount);

    const fetchClientData = async () => {
      try {
        setIsLoading(true);
        setError(null);

        const dealId = context?.crm?.objectId;
        if (!dealId) {
          setError(ERROR_MESSAGES.NO_DEAL_ID);
          setIsLoading(false);
          return;
        }

        const data = (await fetchFromAPI(
          `/api/v1/deals/${dealId}/client`
        )) as ClientAPIResponse;

        if (!data.success) {
          const errorMsg = data.error || ERROR_MESSAGES.LOAD_ERROR;

          if (isContactError(errorMsg)) {
            setNoContactAssociated(true);
            setError(ERROR_MESSAGES.NO_CONTACT_ASSOCIATED);
          } else {
            throw new Error(errorMsg);
          }
          setIsLoading(false);
          return;
        }

        // Check if contact or company are missing
        const missingContact = !data.contact;
        const missingCompany = !data.company;

        if (missingContact || missingCompany) {
          setNoContactAssociated(true);

          if (missingContact && missingCompany) {
            setError(ERROR_MESSAGES.NO_CONTACT_AND_COMPANY);
          } else if (missingContact) {
            setError(ERROR_MESSAGES.NO_CONTACT_ASSOCIATED);
          } else {
            setError(ERROR_MESSAGES.NO_COMPANY_ASSOCIATED);
          }

          setIsLoading(false);
          return;
        }

        // Build proveedorComision object if intermediary data exists
        let proveedorComisionObj = null;
        if (data.deal?.codigo_intermediario) {
          // Use commission_provider_info if available (includes real provider name)
          if (data.deal.commission_provider_info) {
            proveedorComisionObj = {
              id: data.deal.commission_provider_info.id || null,
              name:
                data.deal.commission_provider_info.name ||
                `Intermediario - ${data.deal.codigo_intermediario}`,
              cardcode:
                data.deal.commission_provider_info.cardcode ||
                data.deal.codigo_intermediario,
              repercutir_a_cliente:
                data.deal.commission_provider_info.repercutir_a_cliente ||
                data.deal.repercutir_a_cliente?.value ||
                "",
              tipo_comision:
                data.deal.commission_provider_info.tipo_comision ||
                data.deal.tipo_comision?.value ||
                "",
              importe_comision:
                data.deal.commission_provider_info.importe_comision ||
                data.deal.importe_comision ||
                0,
            };
          } else {
            // Fallback: Create a basic provider object with just the code
            proveedorComisionObj = {
              id: null,
              name: `Intermediario - ${data.deal.codigo_intermediario}`,
              cardcode: data.deal.codigo_intermediario,
              repercutir_a_cliente: data.deal.repercutir_a_cliente?.value || "",
              tipo_comision: data.deal.tipo_comision?.value || "",
              importe_comision: data.deal.importe_comision || 0,
            };
          }
        }

        // Update clienteData with API response
        setClienteData({
          nombreCliente:
            `${data.contact?.firstname || ""} ${
              data.contact?.lastname || ""
            }`.trim() || "No especificado",
          nombreEmpresa: data.company?.name || "No especificado",
          country: data.contact?.country || "",
          // Extraer valores de objetos {value, options} para campos que deben ser strings
          tipoVenta: data.deal?.tipo_de_venta?.value || "",
          tipoContenedor: data.deal?.tipo_contenedor?.value || "",
          tipoCliente: data.company?.tipo_cliente?.value || "",
          // Convert from HubSpot format (0.12) to display format (12)
          margen: data.deal?.margen_cliente
            ? data.deal.margen_cliente * PERCENTAGE_CONVERSION_FACTOR
            : 12,
          idioma: data.deal?.preprints_language?.value || "ES",
          proyecto: data.deal?.project?.value || data.proyecto || "",
          lugarCarga: data.deal?.lugar_carga || "",
          lugarCargaLabel: "", // Will be loaded when needed
          proveedorComision: proveedorComisionObj,
          codigoIntermediario: data.deal?.codigo_intermediario || "",
          repercutirACliente: data.deal?.repercutir_a_cliente?.value || "",
          tipoComision: data.deal?.tipo_comision?.value || "",
          importeComision: data.deal?.importe_comision || 0,
          tipoIVAComision: "",
          tipoResidente: data.company?.residennum || "",
          cif: data.company?.cif || "",
          paisOperaciones: data.company?.pais_de_operaciones || "",
        });

        // Update transitarioData with diasLibres and naviera from deal
        setTransitarioData((prev) => ({
          ...prev,
          diasLibres: data.deal?.dias_libres || 0,
          naviera: data.deal?.naviera?.value || "",
        }));

        // Fetch documentacion data from separate endpoint
        const docData = (await fetchFromAPI(
          `/api/v1/deals/${dealId}/documentacion`
        )) as DocumentacionAPIResponse;

        if (docData.success && docData.deal) {
          setDocumentacionData({
            // Idioma global
            idiomaCotizacion: docData.deal.preprints_language || null,

            // Documentación
            direccionFiscal: docData.deal.direccion_fiscal || "",

            // Documentación Logística
            tramitarCarga: docData.deal.tramitar_carga || false,
            documentosAGenerar: docData.deal.documentos_a_generar || null,
            textoEnBl: docData.deal.texto_en_bl || "",
            comentariosLogistica: docData.deal.comentarios_logistica || "",
            comentariosTransitario: docData.deal.comentarios_transitario || "",

            // Certificado de Origen
            consignee: docData.deal.consignee || "",
            shipper: docData.deal.shipper || "",
            notify: docData.deal.notify || "",
            certificadoOrigenMercancia:
              docData.deal.certificado_origen_mercancia || "",

            // Método de Pago & Textos
            metodoPagoPlantilla: docData.deal.metodo_pago_plantilla || null,
            bancoCobro: docData.deal.banco_cobro || null,
            cotizacionRapida: docData.deal.cotizacion_rapida || false,
            textoAdicionalMetodoPago:
              docData.deal.texto_adicional_metodo_pago || "",
            textoFirmaAceptacionCliente:
              docData.deal.texto_firma_aceptacion_cliente || "",

            // Textos Generados - Limpiar HTML para mostrar como texto plano
            textoBanco: cleanHTMLToText(docData.deal.texto_banco || ""),
            textoFormaPago: cleanHTMLToText(
              docData.deal.texto_forma_pago || ""
            ),
            textoInfoExpediente: cleanHTMLToText(
              docData.deal.texto_informacion_expediente || ""
            ),
            textoBaseOferta: cleanHTMLToText(
              docData.deal.texto_base_oferta || ""
            ),
          });
        }

        // Update dynamic options
        if (data.deal?.tipo_de_venta?.options) {
          setTipoVentaOptions(data.deal.tipo_de_venta.options);
        }
        if (data.deal?.tipo_contenedor?.options) {
          setTipoContenedorOptions(data.deal.tipo_contenedor.options);
        }
        if (data.company?.tipo_cliente?.options) {
          setTipoClienteOptions(data.company.tipo_cliente.options);
        }
        if (data.deal?.naviera?.options) {
          setNavieraOptions(data.deal.naviera.options);
        }
        if (data.deal?.preprints_language?.options) {
          setIdiomaOptions(data.deal.preprints_language.options);
        }
        if (data.deal?.project?.options) {
          setProyectoOptions(data.deal.project.options);
        }
        if (data.deal?.repercutir_a_cliente?.options) {
          setRepercutirAClienteOptions(data.deal.repercutir_a_cliente.options);
        }
        if (data.deal?.tipo_comision?.options) {
          setTipoComisionOptions(data.deal.tipo_comision.options);
        }

        // Clear errors if successful
        setError(null);
        setNoContactAssociated(false);
      } catch (err: any) {
        const errorMsg = err.message || ERROR_MESSAGES.LOAD_ERROR;

        if (isContactError(errorMsg)) {
          setNoContactAssociated(true);
          setError(ERROR_MESSAGES.NO_CONTACT_ASSOCIATED);
        } else {
          setError(errorMsg);
        }
      } finally {
        setIsLoading(false);
      }
    };

    fetchClientData();
  }, [context, isContactError]);

  /**
   * Load IVA options when tipoResidente changes
   */
  useEffect(() => {
    const loadIVAOptions = async () => {
      if (!clienteData.tipoResidente) {
        setTipoIVAOptions([]);
        setIvaData({});
        return;
      }

      const result = (await fetchTipoIVA(clienteData.tipoResidente)) as {
        options: SelectOption[];
        ivaData: Record<string, any>;
      };
      setTipoIVAOptions(result.options);
      setIvaData(result.ivaData);
    };

    loadIVAOptions();
  }, [clienteData.tipoResidente]);

  /**
   * Handle tab navigation with contact validation
   *
   * Prevents navigation away from "cliente" tab if no contact is associated.
   * This ensures users associate a contact before proceeding with other tabs.
   *
   * @param tabId - ID of the tab to navigate to
   */
  const handleNavigate = useCallback(
    (tabId: string) => {
      if (noContactAssociated && tabId !== "cliente") {
        return;
      }
      setSelectedTab(tabId);
    },
    [noContactAssociated]
  );

  return (
    <Flex direction="column" gap="medium">
      {/* Error Alert */}
      {error && (
        <Alert
          title={noContactAssociated ? "Contacto Requerido" : "Error"}
          variant="error"
        >
          {error}
        </Alert>
      )}

      {/* Tab Content */}
      <Box>
        <Tabs
          variant="enclosed"
          selected={selectedTab}
          onSelectedChange={handleNavigate}
        >
          <Tab tabId="cliente" title="Cliente">
            <ClienteTab
              context={context}
              onNavigate={handleNavigate}
              clienteData={clienteData}
              setClienteData={setClienteData}
              tipoVentaOptions={tipoVentaOptions}
              tipoContenedorOptions={tipoContenedorOptions}
              tipoClienteOptions={tipoClienteOptions}
              idiomaOptions={idiomaOptions}
              proyectoOptions={proyectoOptions}
              repercutirAClienteOptions={repercutirAClienteOptions}
              tipoComisionOptions={tipoComisionOptions}
              tipoIVAOptions={tipoIVAOptions}
              loading={isLoading}
            />
          </Tab>

          <Tab
            tabId="articulos"
            title="Artículos"
            disabled={noContactAssociated}
          >
            <ArticulosTab
              context={context}
              onNavigate={setSelectedTab}
              articulosSeleccionados={articulosSeleccionados}
              setArticulosSeleccionados={setArticulosSeleccionados}
              tipoContenedorOptions={tipoContenedorOptions}
              tipoContenedorSeleccionado={clienteData.tipoContenedor}
              clienteData={clienteData}
              setClienteData={setClienteData}
              actions={actions}
              contenedoresData={contenedoresData}
              setContenedoresData={setContenedoresData}
              setContenedoresUnicos={setContenedoresUnicos}
            />
          </Tab>

          <Tab
            tabId="contenedores"
            title="Contenedores"
            disabled={noContactAssociated}
          >
            <ContenedoresTab
              context={context}
              onNavigate={setSelectedTab}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresData={contenedoresData}
              setContenedoresData={setContenedoresData}
              tipoContenedorOptions={tipoContenedorOptions}
              tipoContenedorSeleccionado={clienteData.tipoContenedor}
              setContenedoresUnicos={setContenedoresUnicos}
            />
          </Tab>

          <Tab tabId="transitario" title="Rutas" disabled={noContactAssociated}>
            <TransitarioTab
              context={context}
              onNavigate={setSelectedTab}
              transitarioData={transitarioData}
              setTransitarioData={setTransitarioData}
              navieraOptions={navieraOptions}
              clienteData={clienteData}
              tipoVentaOptions={tipoVentaOptions}
              tipoIVAOptions={tipoIVAOptions}
            />
          </Tab>

          <Tab
            tabId="servicios"
            title="Servicios"
            disabled={noContactAssociated}
          >
            <ServiciosTab
              context={context}
              onNavigate={setSelectedTab}
              serviciosData={serviciosData}
              setServiciosData={setServiciosData}
              tipoIVAOptions={tipoIVAOptions}
              clienteData={clienteData}
            />
          </Tab>

          <Tab
            tabId="negociacion"
            title="Negociación"
            disabled={noContactAssociated}
          >
            <NegociacionTab
              context={context}
              onNavigate={setSelectedTab}
              transitarioData={transitarioData}
              serviciosData={serviciosData}
              clienteData={clienteData}
              contenedoresUnicos={contenedoresUnicos.contenedoresUnicos}
              tipoIVAOptions={tipoIVAOptions}
              ivaData={ivaData}
              preciosEXW={preciosEXW}
              setPreciosEXW={setPreciosEXW}
              tiposIVA={tiposIVA}
              setTiposIVA={setTiposIVA}
            />
          </Tab>

          <Tab
            tabId="documentacion"
            title="Documentación"
            disabled={noContactAssociated}
          >
            <DocumentacionTab
              context={context}
              onNavigate={setSelectedTab}
              documentacionData={documentacionData}
              setDocumentacionData={setDocumentacionData}
              articulosSeleccionados={articulosSeleccionados}
              transitarioData={transitarioData}
            />
          </Tab>

          <Tab
            tabId="revision"
            title="Vista previa"
            disabled={noContactAssociated}
          >
            <RevisionTab
              context={context}
              onNavigate={setSelectedTab}
              clienteData={clienteData}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresData={contenedoresData}
              transitarioData={transitarioData}
              serviciosData={serviciosData}
              documentacionData={documentacionData}
              contenedoresUnicos={contenedoresUnicos.contenedoresUnicos}
              tipoIVAOptions={tipoIVAOptions}
              ivaData={ivaData}
              preciosEXW={preciosEXW}
              tiposIVA={tiposIVA}
            />
          </Tab>
        </Tabs>
      </Box>
    </Flex>
  );
};
