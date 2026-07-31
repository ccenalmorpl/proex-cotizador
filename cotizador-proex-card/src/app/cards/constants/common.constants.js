// API and timing constants
export const API_TIMEOUT_MS = 30000;
export const DEBOUNCE_DELAY_MS = 500;
export const INITIAL_RENDER_DELAY_MS = 300;

// Percentage conversion
export const PERCENTAGE_CONVERSION_FACTOR = 100;

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
