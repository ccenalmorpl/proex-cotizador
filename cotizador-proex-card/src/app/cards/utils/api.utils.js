import { hubspot } from "@hubspot/ui-extensions";
import { API_CONFIG } from "../config";

export class APIError extends Error {
  constructor(message, statusCode = null) {
    super(message);
    this.name = "APIError";
    this.statusCode = statusCode;
  }
}

export const fetchFromAPI = async (endpoint, options = {}) => {
  const url = `${API_CONFIG.baseURL}${endpoint}`;

  try {
    const response = await hubspot.fetch(url, {
      method: options.method || "GET",
      timeout: API_CONFIG.timeout,
      body: options.body,
      headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
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
    throw new APIError(error.message || "Error de red o parsing", null);
  }
};

export const cleanHTMLToText = (htmlString) => {
  if (!htmlString) return "";

  let text = htmlString;

  text = text.replace(/<span[^>]*data-sheets-root[^>]*>/gi, "");
  text = text.replace(/<\/span>/gi, "");

  text = text.replace(/<br\s*\/?>/gi, "\n");
  text = text.replace(/<\/(p|div|li)>/gi, "\n");
  text = text.replace(/<(p|div)[^>]*>/gi, "\n");

  text = text.replace(/<[^>]+>/g, "");

  text = text.replace(/&nbsp;/g, " ");
  text = text.replace(/&amp;/g, "&");
  text = text.replace(/&lt;/g, "<");
  text = text.replace(/&gt;/g, ">");
  text = text.replace(/&quot;/g, '"');
  text = text.replace(/&#39;/g, "'");

  text = text.replace(/\n{3,}/g, "\n\n");
  text = text.split("\n").map(line => line.trim()).join("\n");
  text = text.replace(/ {2,}/g, " ");
  text = text.trim();

  return text;
};

export const convertTextToHTML = (plainText) => {
  if (!plainText) return "";

  let html = plainText;

  html = html.replace(/&/g, "&amp;");
  html = html.replace(/</g, "&lt;");
  html = html.replace(/>/g, "&gt;");

  html = html.replace(/\n\n/g, "<br><br>");
  html = html.replace(/\n/g, "<br>");

  html = `<span data-sheets-root="1">${html}</span>`;

  return html;
};

export const fetchTextosCotizacion = async (idioma, clave, options = {}) => {
  if (!idioma || !clave) {
    return "";
  }

  try {
    const body = {
      idioma: idioma.toLowerCase(),
      claves: [clave]
    };

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

const buildIVAResult = (items) => {
  const options = items.map(item => ({
    label: item.name,
    value: item.code
  }));

  const data = {};
  items.forEach(item => {
    data[item.code] = {
      name: item.name,
      porcentaje_iva: item.porcentaje_iva,
      porcentaje_recargo: item.porcentaje_recargo,
      porcentaje_total: item.porcentaje_iva + (item.porcentaje_recargo || 0)
    };
  });

  return { options, data };
};

// Pide repercutido + soportado en una sola llamada al endpoint /tipo-iva
export const fetchTipoIVA = async (tipoResidencia) => {
  const empty = {
    repercutidoOptions: [], repercutidoData: {},
    soportadoOptions: [], soportadoData: {},
  };

  if (!tipoResidencia) {
    return empty;
  }

  try {
    const response = await fetchFromAPI(`/api/v1/deals/tipo-iva`, {
      method: "POST",
      body: {
        filtros: [
          { categoria: "repercutido", tipo_residencia: tipoResidencia },
          { categoria: "soportado", tipo_residencia: tipoResidencia }
        ]
      }
    });

    if (response.data && Array.isArray(response.data)) {
      const repercutidoItems = response.data.filter(item => item.categoria === "repercutido");
      const soportadoItems = response.data.filter(item => item.categoria === "soportado");

      const rep = buildIVAResult(repercutidoItems);
      const sop = buildIVAResult(soportadoItems);

      return {
        repercutidoOptions: rep.options,
        repercutidoData: rep.data,
        soportadoOptions: sop.options,
        soportadoData: sop.data,
      };
    }

    return empty;
  } catch (error) {
    console.error("Error al obtener tipos de IVA:", error);
    return empty;
  }
};

// Empresas asociadas a la del negocio por la relación "Proveedor-Cliente", con
// su dirección de entrega. Alimenta el selector de autorrelleno de la
// "Dirección de Entrega" en DocumentacionTab.
export const fetchEmpresasAsociadas = async (dealId) => {
  if (!dealId) return [];

  try {
    const response = await fetchFromAPI(
      `/api/v1/deals/${dealId}/empresas-asociadas`
    );

    return (response.empresas || []).map((e) => ({
      id: e.id,
      nombre: e.name || "",
      direccionEntrega: e.address_entrega || null,
    }));
  } catch (error) {
    console.error("Error al obtener empresas asociadas:", error);
    return [];
  }
};
