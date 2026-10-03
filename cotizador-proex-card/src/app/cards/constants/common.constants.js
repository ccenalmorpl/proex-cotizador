// API and timing constants
export const API_TIMEOUT_MS = 30000;
export const DEBOUNCE_DELAY_MS = 500;
export const INITIAL_RENDER_DELAY_MS = 300;

// Percentage conversion
export const PERCENTAGE_CONVERSION_FACTOR = 100;

// La opción "Importe por cajas" se leía como el total de las cajas: el usuario
// metía 290,16 € (0,12 × 2.418) y el cotizador cobraba 290,16 € por caja.
export const IMPORTE_COMISION_POR_TIPO = {
  importe_por_cajas: {
    label: "Importe Comisión por caja (EUR/caja)",
    description: "Por cada caja, no el total: se multiplica por las cajas de cada contenedor",
  },
  importe_por_contenedor: {
    label: "Importe Comisión por contenedor (EUR)",
    description: "Por cada contenedor: se reparte entre sus cajas",
  },
  importe_por_negocio: {
    label: "Importe Comisión del negocio (EUR)",
    description: "Total del negocio: se reparte a partes iguales entre los contenedores",
  },
};
export const IMPORTE_COMISION_SIN_TIPO = {
  label: "Importe Comisión (EUR)",
  description: "Importe de la comisión en euros",
};

// Error messages
export const ERROR_MESSAGES = {
  NO_DEAL_ID: "No se pudo obtener el ID del Deal desde el contexto.",
  NO_CONTACT: "Este Negocio no tiene un Contacto asociado.",
  NO_CONTACT_ASSOCIATED:
    "Este negocio no tiene un contacto asociado. Por favor, asocia un contacto desde la página principal del Deal.",
  NO_COMPANY_ASSOCIATED:
    "Este negocio no tiene una empresa asociada. Por favor, asocia una empresa desde la página principal del Deal.",
  NO_CONTACT_AND_COMPANY:
    "Este negocio no tiene un contacto ni una empresa asociados. Por favor, asócialos desde la página principal del Deal.",
  NETWORK_ERROR: "Error de conexión. Por favor, verifica tu conexión a internet.",
  UNKNOWN_ERROR: "Error desconocido. Por favor, intenta de nuevo.",
  LOAD_ERROR: "Error al cargar los datos del negocio.",
  SAVE_ERROR: "Error al guardar los datos",
};

// Success messages
export const SUCCESS_MESSAGES = {
  DATA_SAVED: "Datos guardados correctamente",
  CONTAINER_ADDED: "Contenedor agregado correctamente",
  PRODUCT_ADDED: "Producto agregado correctamente",
};

// Loading messages
export const LOADING_MESSAGES = {
  LOADING_DATA: "Cargando datos...",
  LOADING_CLIENT: "Cargando datos del cliente...",
  LOADING_PRODUCTS: "Cargando artículos...",
  LOADING_CONTAINERS: "Cargando contenedores guardados...",
  LOADING_SERVICES: "Cargando servicios disponibles...",
  LOADING_SERVICES_SAVED: "Cargando servicios guardados...",
  LOADING_PROVIDERS: "Cargando proveedores...",
  LOADING_NEGOCIACION: "Cargando datos de la negociación...",
  LOADING_DOCUMENTACION: "Cargando documentación...",
  SAVING: "Guardando...",
  SYNCING_PRICES: "Sincronizando precios de contenedor...",
};
