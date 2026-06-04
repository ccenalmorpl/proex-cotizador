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

// Mapeo de código numérico HubSpot → etiqueta textual de tipo de residente
const RESIDENNUM_MAP: Record<string, string> = {
  "1": "NACIONAL",
  "2": "EUROPA",
  "4": "EXTRANJERO",
};
const mapResidennum = (value: string | undefined | null): string => {
  if (!value) return "";
  return RESIDENNUM_MAP[value] ?? value;
};

hubspot.extend(({ context, actions }) => (
  <Extension context={context} actions={actions} />
));

const Extension: React.FC<ExtensionProps> = ({ context, actions }) => {
  // UI state
  const [selectedTab, setSelectedTab] = useState<string>("cliente");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [noCompanyAssociated, setNoCompanyAssociated] =
    useState<boolean>(false);

  // Data state - Cliente
  const [clienteData, setClienteData] = useState<ClienteData>({
    nombreEmpresa: "",
    country: "",
    tipoVenta: "",
    tipoContenedor: "",
    tipoCliente: "",
    margen: 12,
    proyecto: "",
    lugarCarga: "",
    lugarCargaLabel: "",
    proveedorComision: null,
    codigoIntermediario: "",
    repercutirACliente: "",
    tipoComision: "",
    importeComision: 0,
    tipoIVAComision: "",
    tipoIVAComisionSoportado: "",
    tipoResidente: "",
    cif: "",
    paisOperaciones: "",
    cargaPorPalets: false,
    fechaCaducidadCotizacion: (() => {
      const d = new Date();
      d.setMonth(d.getMonth() + 1);
      return { year: d.getFullYear(), month: d.getMonth(), date: d.getDate() };
    })(),
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
  const [tipoIVARepercutidoOptions, setTipoIVARepercutidoOptions] = useState<SelectOption[]>([]);
  const [ivaRepercutidoData, setIvaRepercutidoData] = useState<Record<string, any>>({});
  const [tipoIVASoportadoOptions, setTipoIVASoportadoOptions] = useState<SelectOption[]>([]);
  const [ivaSoportadoData, setIvaSoportadoData] = useState<Record<string, any>>({});

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
  const [tiposIVASoportado, setTiposIVASoportado] = useState<Record<string, string>>({});

  // IVA defaults resolved from product (Fase 4E)
  const [ivaComisionDefaults, setIvaComisionDefaults] = useState<{ repercutido?: string; soportado?: string } | null>(null);
  const [ivaFletesDefaults, setIvaFletesDefaults] = useState<{ repercutido?: string; soportado?: string } | null>(null);

  // Data state - Transitario
  const [transitarioData, setTransitarioData] = useState<TransitarioData>({
    transitarioSeleccionado: null,
    puertoSalida: "",
    puertoDestino: "",
    localidadDestino: "",
    precioOrigen: 0,
    precioDestino: 0,
    fechaCaducidadFleteSalida: null,
    fechaCaducidadFleteDestino: null,
    diasLibres: 0,
    naviera: "",
    almacen: "",
    cotizacionRapida: false,
    tipoIVAFletes: "",
    tipoIVAFletesSoportado: "",
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
      direccionDeEntrega: "",
      cif: "",
      referenciaCotizacion: "",

      // Documentación Logística
      desagregarCotizacion: false,
      documentosAGenerar: null,
      textoEnBl: "",
      comentariosLogistica: "",
      comentariosTransitario: "",

      // Certificado de Origen
      consignee: "",
      shipper: "",
      notify: "",
      certificadoOrigenMercancia: "",
      observaciones: "",

      // Método de Pago & Textos
      metodoPagoPlantilla: null,
      bancoCobro: null,
      cotizacionRapida: false,
      textoAdicionalMetodoPago: "",

      // Textos Generados
      textoBanco: "",
      textoFormaPago: "",
      textoBaseOferta: "",
    }
  );

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
          throw new Error(data.error || ERROR_MESSAGES.LOAD_ERROR);
        }

        // Block only if company is missing — contact is optional
        if (!data.company) {
          setNoCompanyAssociated(true);
          setError(ERROR_MESSAGES.NO_COMPANY_ASSOCIATED);
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
        const margenCalculado = data.deal?.margen_cliente
          ? data.deal.margen_cliente * PERCENTAGE_CONVERSION_FACTOR
          : 12;

        setClienteData({
          nombreEmpresa: data.company?.name || "No especificado",
          country: data.company?.country || "",
          // Extraer valores de objetos {value, options} para campos que deben ser strings
          tipoVenta: data.deal?.tipo_de_venta?.value || "",
          tipoContenedor: data.deal?.tipo_contenedor?.value || "",
          tipoCliente: data.company?.tipo_cliente?.value || "",
          // Convert from HubSpot format (0.12) to display format (12)
          margen: margenCalculado,
          proyecto: data.deal?.project?.value || data.proyecto || "",
          lugarCarga: data.deal?.lugar_carga || "",
          lugarCargaLabel: "", // Will be loaded when needed
          proveedorComision: proveedorComisionObj,
          codigoIntermediario: data.deal?.codigo_intermediario || "",
          repercutirACliente: data.deal?.repercutir_a_cliente?.value || "",
          tipoComision: data.deal?.tipo_comision?.value || "",
          importeComision: data.deal?.importe_comision || 0,
          tipoIVAComision: data.deal?.tipo_iva_comision || "",
          tipoIVAComisionSoportado: data.deal?.tipo_iva_comision_soportado || "",
          tipoResidente: mapResidennum(data.company?.residennum),
          cif: data.company?.cif || "",
          paisOperaciones: data.company?.pais_de_operaciones || "",
          cargaPorPalets: data.deal?.carga_por_palets || false,
          fechaCaducidadCotizacion: (() => {
            const ts = data.deal?.fecha_caducidad_cotizacion;
            if (ts) {
              const d = new Date(ts);
              return { year: d.getFullYear(), month: d.getMonth(), date: d.getDate() };
            }
            const d = new Date();
            d.setMonth(d.getMonth() + 1);
            return { year: d.getFullYear(), month: d.getMonth(), date: d.getDate() };
          })(),
        });

       // Store IVA defaults resolved from product (Fase 4E)
        if (data.deal?.iva_comision_defaults) {
          setIvaComisionDefaults(data.deal.iva_comision_defaults);
        }

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
            direccionDeEntrega: docData.deal.direccion_de_entrega || "",
            cif: docData.deal.cif || "",
            referenciaCotizacion: docData.deal.referencia_cotizacion || "",

            // Documentación Logística
            desagregarCotizacion: docData.deal.desagregar_cotizacion || false,
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
            observaciones: docData.deal.observaciones || "",

            // Método de Pago & Textos
            metodoPagoPlantilla: docData.deal.metodo_pago_plantilla || null,
            bancoCobro: docData.deal.banco_cobro || null,
            cotizacionRapida: docData.deal.cotizacion_rapida || false,
            textoAdicionalMetodoPago:
              docData.deal.texto_adicional_metodo_pago || "",

            // Textos Generados - Limpiar HTML para mostrar como texto plano
            textoBanco: cleanHTMLToText(docData.deal.texto_banco || ""),
            textoFormaPago: cleanHTMLToText(
              docData.deal.texto_forma_pago || ""
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
        setNoCompanyAssociated(false);
      } catch (err: any) {
        setError(err.message || ERROR_MESSAGES.LOAD_ERROR);
      } finally {
        setIsLoading(false);
      }
    };

    fetchClientData();

  }, [context]);

  /**
   * Load IVA options when tipoResidente changes
   */
  useEffect(() => {
    const loadIVAOptions = async () => {
      if (!clienteData.tipoResidente) {
        setTipoIVARepercutidoOptions([]);
        setIvaRepercutidoData({});
        setTipoIVASoportadoOptions([]);
        setIvaSoportadoData({});
        return;
      }

      const result = (await fetchTipoIVA(clienteData.tipoResidente)) as {
        repercutidoOptions: SelectOption[];
        repercutidoData: Record<string, any>;
        soportadoOptions: SelectOption[];
        soportadoData: Record<string, any>;
      };
      setTipoIVARepercutidoOptions(result.repercutidoOptions);
      setIvaRepercutidoData(result.repercutidoData);
      setTipoIVASoportadoOptions(result.soportadoOptions);
      setIvaSoportadoData(result.soportadoData);
    };

    loadIVAOptions();
  }, [clienteData.tipoResidente]);

  // Bloquea la navegación a otras tabs hasta que el deal tenga company asociada
  const handleNavigate = useCallback(
    (tabId: string) => {
      if (noCompanyAssociated && tabId !== "cliente") {
        return;
      }
      setSelectedTab(tabId);
    },
    [noCompanyAssociated]
  );

  return (
    <Flex direction="column" gap="medium">
      {error && (
        <Alert
          title={noCompanyAssociated ? "Empresa Requerida" : "Error"}
          variant="error"
        >
          {error}
        </Alert>
      )}

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
              proyectoOptions={proyectoOptions}
              repercutirAClienteOptions={repercutirAClienteOptions}
              tipoComisionOptions={tipoComisionOptions}
              tipoIVAOptions={tipoIVARepercutidoOptions}
              tipoIVASoportadoOptions={tipoIVASoportadoOptions}
              ivaComisionDefaults={ivaComisionDefaults}
              loading={isLoading}
            />
          </Tab>

          <Tab
            tabId="articulos"
            title="Artículos"
            disabled={noCompanyAssociated}
          >
            <ArticulosTab
              context={context}
              onNavigate={handleNavigate}
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
            disabled={noCompanyAssociated}
          >
            <ContenedoresTab
              context={context}
              onNavigate={handleNavigate}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresData={contenedoresData}
              setContenedoresData={setContenedoresData}
              tipoContenedorOptions={tipoContenedorOptions}
              tipoContenedorSeleccionado={clienteData.tipoContenedor}
              setContenedoresUnicos={setContenedoresUnicos}
              cargaPorPalets={clienteData.cargaPorPalets}
            />
          </Tab>

          <Tab tabId="transitario" title="Rutas" disabled={noCompanyAssociated}>
            <TransitarioTab
              context={context}
              onNavigate={handleNavigate}
              transitarioData={transitarioData}
              setTransitarioData={setTransitarioData}
              navieraOptions={navieraOptions}
              clienteData={clienteData}
              tipoVentaOptions={tipoVentaOptions}
              tipoIVAOptions={tipoIVARepercutidoOptions}
              tipoIVASoportadoOptions={tipoIVASoportadoOptions}
              ivaFletesDefaults={ivaFletesDefaults}
              setIvaFletesDefaults={setIvaFletesDefaults}
              selectedTab={selectedTab}
            />
          </Tab>

          <Tab
            tabId="servicios"
            title="Servicios"
            disabled={noCompanyAssociated}
          >
            <ServiciosTab
              context={context}
              onNavigate={handleNavigate}
              serviciosData={serviciosData}
              setServiciosData={setServiciosData}
              tipoIVAOptions={tipoIVARepercutidoOptions}
              tipoIVASoportadoOptions={tipoIVASoportadoOptions}
              clienteData={clienteData}
            />
          </Tab>

          <Tab
            tabId="negociacion"
            title="Negociación"
            disabled={noCompanyAssociated}
          >
            <NegociacionTab
              context={context}
              actions={actions}
              onNavigate={handleNavigate}
              transitarioData={transitarioData}
              serviciosData={serviciosData}
              clienteData={clienteData}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresUnicos={contenedoresUnicos.contenedoresUnicos}
              setContenedoresUnicos={setContenedoresUnicos}
              tipoIVAOptions={tipoIVARepercutidoOptions}
              tipoIVASoportadoOptions={tipoIVASoportadoOptions}
              ivaData={ivaRepercutidoData}
              preciosEXW={preciosEXW}
              setPreciosEXW={setPreciosEXW}
              tiposIVA={tiposIVA}
              setTiposIVA={setTiposIVA}
              tiposIVASoportado={tiposIVASoportado}
              setTiposIVASoportado={setTiposIVASoportado}
            />
          </Tab>

          <Tab
            tabId="documentacion"
            title="Documentación"
            disabled={noCompanyAssociated}
          >
            <DocumentacionTab
              context={context}
              onNavigate={handleNavigate}
              documentacionData={documentacionData}
              setDocumentacionData={setDocumentacionData}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresData={contenedoresData}
              transitarioData={transitarioData}
            />
          </Tab>

          <Tab
            tabId="revision"
            title="Vista previa"
            disabled={noCompanyAssociated}
          >
            <RevisionTab
              context={context}
              onNavigate={handleNavigate}
              clienteData={clienteData}
              articulosSeleccionados={articulosSeleccionados}
              contenedoresData={contenedoresData}
              transitarioData={transitarioData}
              serviciosData={serviciosData}
              documentacionData={documentacionData}
              contenedoresUnicos={contenedoresUnicos.contenedoresUnicos}
              tipoIVAOptions={tipoIVARepercutidoOptions}
              ivaData={ivaRepercutidoData}
              preciosEXW={preciosEXW}
              tiposIVA={tiposIVA}
              tipoIVASoportadoOptions={tipoIVASoportadoOptions}
              tiposIVASoportado={tiposIVASoportado}
            />
          </Tab>
        </Tabs>
      </Box>
    </Flex>
  );
};
