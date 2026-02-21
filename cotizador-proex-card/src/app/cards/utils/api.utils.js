import { hubspot } from "@hubspot/ui-extensions";
import { API_CONFIG } from "../config";

/**
 * Custom error class for API-related errors
 */
export class APIError extends Error {
  constructor(message, statusCode = null) {
    super(message);
    this.name = "APIError";
    this.statusCode = statusCode;
  }
}

/**
 * Centralized API fetch utility with consistent error handling
 * @param {string} endpoint - API endpoint (will be appended to base URL)
 * @param {Object} options - Fetch options
 * @param {string} options.method - HTTP method (GET, POST, PUT, DELETE)
 * @param {Object} options.body - Request body (will be stringified if needed)
 * @param {Object} options.headers - Additional headers
 * @returns {Promise<Object>} - Parsed JSON response
 * @throws {APIError} - On network or API errors
 */
export const fetchFromAPI = async (endpoint, options = {}) => {
  const url = `${API_CONFIG.baseURL}${endpoint}`;

  try {
    const response = await hubspot.fetch(url, {
      method: options.method || "GET",
      timeout: API_CONFIG.timeout,
      body: options.body
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => "");
      throw new APIError(
        `Error ${response.status}: ${response.statusText}${errorText ? ` - ${errorText}` : ""}`,
        response.status
      );
    }

    const data = await response.json();

    if (!data.success) {
      throw new APIError(data.error || "Error en la respuesta de la API");
    }

    return data;
  } catch (error) {
    if (error instanceof APIError) {
      throw error;
    }
    // Network or parsing error
    throw new APIError(error.message || "Error de red o parsing", null);
  }
};

/**
 * Limpia el HTML y lo convierte a texto plano manteniendo saltos de línea
 * @param {string} htmlString - String con HTML
 * @returns {string} - Texto plano con saltos de línea
 */
export const cleanHTMLToText = (htmlString) => {
  if (!htmlString) return "";

  let text = htmlString;

  // Remover span con data-sheets-root y mantener el contenido
  text = text.replace(/<span[^>]*data-sheets-root[^>]*>/gi, "");
  text = text.replace(/<\/span>/gi, "");

  // Convertir <br>, <br/>, <br /> a saltos de línea
  text = text.replace(/<br\s*\/?>/gi, "\n");

  // Convertir </p>, </div>, </li> a saltos de línea
  text = text.replace(/<\/(p|div|li)>/gi, "\n");

  // Convertir <p>, <div> a saltos de línea al inicio
  text = text.replace(/<(p|div)[^>]*>/gi, "\n");

  // Remover cualquier otra etiqueta HTML restante
  text = text.replace(/<[^>]+>/g, "");

  // Decodificar entidades HTML comunes
  text = text.replace(/&nbsp;/g, " ");
  text = text.replace(/&amp;/g, "&");
  text = text.replace(/&lt;/g, "<");
  text = text.replace(/&gt;/g, ">");
  text = text.replace(/&quot;/g, '"');
  text = text.replace(/&#39;/g, "'");

  // Limpiar múltiples saltos de línea consecutivos (más de 2)
  text = text.replace(/\n{3,}/g, "\n\n");

  // Limpiar espacios al inicio y final de cada línea
  text = text.split("\n").map(line => line.trim()).join("\n");

  // Limpiar espacios múltiples
  text = text.replace(/ {2,}/g, " ");

  // Trim general
  text = text.trim();

  return text;
};

/**
 * Convierte texto plano a HTML con formato de Google Sheets
 * @param {string} plainText - Texto plano con saltos de línea
 * @returns {string} - HTML formateado con span y br
 */
export const convertTextToHTML = (plainText) => {
  if (!plainText) return "";

  let html = plainText;

  // Codificar entidades HTML especiales primero
  html = html.replace(/&/g, "&amp;");
  html = html.replace(/</g, "&lt;");
  html = html.replace(/>/g, "&gt;");

  // Convertir saltos de línea dobles a <br><br>
  html = html.replace(/\n\n/g, "<br><br>");

  // Convertir saltos de línea simples a <br>
  html = html.replace(/\n/g, "<br>");

  // Envolver en span con data-sheets-root
  html = `<span data-sheets-root="1">${html}</span>`;

  return html;
};

/**
 * Obtiene textos de cotización desde HubDB
 * @param {string} idioma - Código de idioma (es, en, fr)
 * @param {string} clave - Clave del texto a obtener (ej: banco_la_caixa, pago_100)
 * @param {object} options - Opciones adicionales (grupo, subgrupo)
 * @returns {Promise<string>} - Texto obtenido de HubDB o string vacío si hay error
 */
export const fetchTextosCotizacion = async (idioma, clave, options = {}) => {
  if (!idioma || !clave) {
    return "";
  }

  try {
    const body = {
      idioma: idioma.toLowerCase(),
      claves: [clave]
    };

    // Añadir grupo y subgrupo si están presentes
    if (options.grupo) {
      body.grupo = options.grupo;
    }
    if (options.subgrupo) {
      body.subgrupo = options.subgrupo;
    }

    const response = await fetchFromAPI(`/api/v1/deals/documentacion/textos-cotizacion`, {
      method: "POST",
      body
    });

    // Retornar el texto correspondiente a la clave, limpiando el HTML
    if (response.data) {
      const rawText = response.data[clave]?.texto || "";
      return cleanHTMLToText(rawText);
    }

    return "";
  } catch (error) {
    console.error("Error al obtener textos de cotización:", error);
    return "";
  }
};

/**
 * Obtiene tipos de IVA desde HubDB filtrados por categoría y tipo de residencia
 * @param {string} tipoResidencia - Tipo de residencia del cliente (NACIONAL, EUROPA, EXTRANJERO)
 * @returns {Promise<Object>} - Objeto con options (para selects) y ivaData (datos completos indexados por code)
 */
export const fetchTipoIVA = async (tipoResidencia) => {
  if (!tipoResidencia) {
    return { options: [], ivaData: {} };
  }

  try {
    const response = await fetchFromAPI(`/api/v1/deals/tipo-iva`, {
      method: "POST",
      body: {
        filtros: [
          { categoria: "repercutido", tipo_residencia: tipoResidencia }
        ]
      }
    });

    if (response.data && Array.isArray(response.data)) {
      // Crear opciones para los selects
      const options = response.data.map(item => ({
        label: item.name,
        value: item.code
      }));

      // Crear índice por code para búsqueda rápida
      const ivaData = {};
      response.data.forEach(item => {
        ivaData[item.code] = {
          name: item.name,
          porcentaje_iva: item.porcentaje_iva,
          porcentaje_recargo: item.porcentaje_recargo,
          porcentaje_total: item.porcentaje_iva + (item.porcentaje_recargo || 0)
        };
      });

      return { options, ivaData };
    }

    return { options: [], ivaData: {} };
  } catch (error) {
    console.error("Error al obtener tipos de IVA:", error);
    return { options: [], ivaData: {} };
  }
};
