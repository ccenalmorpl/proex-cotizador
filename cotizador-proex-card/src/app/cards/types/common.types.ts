/**
 * Common TypeScript interfaces and types for the Cotizador application
 */

// ============================================================================
// SELECT OPTIONS
// ============================================================================

export interface SelectOption {
  label: string;
  value: string;
}

export interface PropertyWithOptions {
  value: string | null;
  options?: SelectOption[];
}

// ============================================================================
// CLIENTE (CLIENT) INTERFACES
// ============================================================================

export interface CommissionProvider {
  id: string | null;
  name: string;
  cardcode: string;
  tipo_proveedor?: string;
  repercutir_a_cliente: string;
  tipo_comision: string;
  importe_comision: number;
}

export interface ClienteData {
  nombreCliente: string;
  nombreEmpresa: string;
  country: string;
  tipoVenta: string;
  tipoContenedor: string;
  tipoCliente: string;
  margen: number;
  idioma: string;
  proyecto: string;
  lugarCarga: string;
  lugarCargaLabel: string;
  proveedorComision: CommissionProvider | null;
  codigoIntermediario: string;
  repercutirACliente: string;
  tipoComision: string;
  importeComision: number;
  tipoIVAComision: string;
  tipoResidente: string;
  cif: string;
  paisOperaciones: string;
}

// ============================================================================
// ARTÍCULOS (ARTICLES) INTERFACES
// ============================================================================

export interface ArticuloItem {
  id: string;
  hs_sku: string;
  name: string;
  cantidad: number;
  precio: number;
  margen_unitario?: number;
  tipo_iva_repercutido?: string;
  peso_neto?: number;
  proveedor_habitual?: {
    id: string;
    cardcode: string;
    name: string;
  };
}

// ============================================================================
// CONTENEDORES (CONTAINERS) INTERFACES
// ============================================================================

export interface ContenedorDefinido {
  tipo: string;
  cantidad: number;
  capacidad?: number;
  articulosAsignados?: any[];
}

export interface ContenedoresData {
  contenedoresDefinidos: ContenedorDefinido[];
  cantidadesContenedores: Record<string, number>;
  totales: {
    totalColis: number;
    totalPesoNeto: number;
    totalContenedores: number;
  };
}

export interface ContenedorUnicoDefinido {
  line_item_id: number;
  container_type: string;
  container_number: number;
  container_name: string;
  cantidad?: number;
  ocupacion: number;
  pesoNeto: number;
  container_config: {
    articulos: Array<{
      articuloId: string;
      articuloName?: string;
      nombre: string;
      numCajas: number;
      price?: number;
      margen_unitario?: number;
      tipo_iva_repercutido?: string;
      pesoNeto?: number;
      line_item_id?: string;
    }>;
  };
}
export interface ContenedoresUnicosData {
  contenedoresUnicos: ContenedorUnicoDefinido[];
}

// ============================================================================
// TRANSITARIO (FREIGHT FORWARDER) INTERFACES
// ============================================================================

export interface TransitarioSeleccionado {
  id: string;
  cardcode: string;
  name: string;
}

export interface TransitarioData {
  transitarioSeleccionado: TransitarioSeleccionado | null;
  puertoSalida: string;
  puertoDestino: string;
  localidadDestino: string;
  precioOrigen: number;
  precioDestino: number;
  fechaValidez: string | null;
  diasLibres: number;
  naviera: string;
  almacen: string;
  cotizacionRapida: boolean;
  tipoIVAFletes: string;
}

// ============================================================================
// SERVICIOS (SERVICES) INTERFACES
// ============================================================================

export interface ServicioItem {
  id: number;
  servicioId: string;
  nombreServicio: string;
  tipoArticulo: string;
  proveedorId: string;
  nombreProveedor: string;
  proveedorCardcode: string;
  importe: number;
}

export interface ServiciosData {
  serviciosSeleccionados: ServicioItem[];
}

// ============================================================================
// DOCUMENTACIÓN (DOCUMENTATION) INTERFACES
// ============================================================================

export interface DocumentacionData {
  // Idioma global
  idiomaCotizacion: PropertyWithOptions | null;

  // Documentación
  direccionFiscal: string;

  // Documentación Logística
  tramitarCarga: boolean;
  documentosAGenerar: PropertyWithOptions | null;
  textoEnBl: string;
  comentariosLogistica: string;
  comentariosTransitario: string;

  // Certificado de Origen
  consignee: string;
  shipper: string;
  notify: string;
  certificadoOrigenMercancia: string;

  // Método de Pago & Textos
  metodoPagoPlantilla: PropertyWithOptions | null;
  bancoCobro: PropertyWithOptions | null;
  cotizacionRapida: boolean;
  textoAdicionalMetodoPago: string;
  textoFirmaAceptacionCliente: string;

  // Textos Generados
  textoBanco: string;
  textoFormaPago: string;
  textoInfoExpediente: string;
  textoBaseOferta: string;
}

// ============================================================================
// API RESPONSE INTERFACES
// ============================================================================

export interface ClientAPIResponse {
  success: boolean;
  error?: string;
  contact: {
    firstname?: string;
    lastname?: string;
    country?: string;
  };
  company: {
    name?: string;
    residennum?: string;
    cif?: string;
    pais_de_operaciones?: string;
    tipo_cliente: {
      value: string;
      options?: SelectOption[];
    };

  };
  deal: {
    tipo_de_venta?: {
      value: string;
      options?: SelectOption[];
    };
    tipo_contenedor?: {
      value: string;
      options?: SelectOption[];
    };
    naviera?: {
      value: string;
      options?: SelectOption[];
    };
    preprints_language?: {
      value: string;
      options?: SelectOption[];
    };
    project?: {
      value: string;
      options?: SelectOption[];
    };
    margen_cliente?: number;
    dias_libres?: number;
    lugar_carga?: string;
    codigo_intermediario?: string;
    repercutir_a_cliente?: {
      value: string;
      options?: SelectOption[];
    };
    tipo_comision?: {
      value: string;
      options?: SelectOption[];
    };
    importe_comision?: number;
    commission_provider_info?: {
      id: string | null;
      name: string;
      cardcode: string;
      repercutir_a_cliente: string;
      tipo_comision: string;
      importe_comision: number;
    };
    // Documentación
    direccion_fiscal?: string;
    // Documentación Logística
    tramitar_carga?: boolean;
    documentos_a_generar?: {
      value: string | null;
      options?: SelectOption[];
    };
    texto_en_bl?: string;
    comentarios_logistica?: string;
    comentarios_transitario?: string;
    // Certificado de Origen
    consignee?: string;
    shipper?: string;
    notify?: string;
    certificado_origen_mercancia?: string;
    // Método de Pago & Textos
    metodo_pago_plantilla?: {
      value: string | null;
      options?: SelectOption[];
    };
    banco_cobro?: {
      value: string | null;
      options?: SelectOption[];
    };
    cotizacion_rapida?: boolean;
    texto_adicional_metodo_pago?: string;
    texto_firma_aceptacion_cliente?: string;
    // Textos Generados
    texto_banco?: string;
    texto_forma_pago?: string;
    texto_informacion_expediente?: string;
    texto_base_oferta?: string;
  };
  proyecto?: string;
}

// ============================================================================
// DOCUMENTACIÓN API RESPONSE
// ============================================================================

export interface DocumentacionAPIResponse {
  success: boolean;
  error?: string;
  deal?: {
    preprints_language?: PropertyWithOptions;
    direccion_fiscal?: string;
    tramitar_carga?: boolean;
    documentos_a_generar?: PropertyWithOptions;
    texto_en_bl?: string;
    comentarios_logistica?: string;
    comentarios_transitario?: string;
    consignee?: string;
    shipper?: string;
    notify?: string;
    certificado_origen_mercancia?: string;
    metodo_pago_plantilla?: PropertyWithOptions;
    banco_cobro?: PropertyWithOptions;
    cotizacion_rapida?: boolean;
    texto_adicional_metodo_pago?: string;
    texto_firma_aceptacion_cliente?: string;
    texto_banco?: string;
    texto_forma_pago?: string;
    texto_informacion_expediente?: string;
    texto_base_oferta?: string;
  };
}

// ============================================================================
// COMPONENT PROPS INTERFACES
// ============================================================================

export interface ExtensionProps {
  context: any;
  actions: any;
}
