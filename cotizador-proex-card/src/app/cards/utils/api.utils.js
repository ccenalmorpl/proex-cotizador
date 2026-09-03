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
  // `options.baseURL` permite apuntar a otro host sin duplicar este helper. Lo
  // usa el backend local de pedidos KIT; el resto de llamadas no lo pasan y
  // siguen yendo al microservicio de producción.
  const url = `${options.baseURL || API_CONFIG.baseURL}${endpoint}`;

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

// Pedidos KIT abiertos con cajas libres, filtrados por los SKU de la cotización.
//
// Mientras dure el desarrollo apunta al backend local (`API_CONFIG.kitsBaseURL`);
// en cuanto el endpoint se despliegue, basta con dejar ese campo vacío y la
// llamada va sola al microservicio, sin tocar nada más.
//
// Devuelve `null` si la llamada falla y `[]` si simplemente no hay KIT para esos
// SKU. La distinción importa: un array vacío es una respuesta legítima, y si el
// fallo también devolviera [] el panel desaparecería sin decir por qué. Quien
// llama usa `null` para caer al fixture.
export const fetchKitsDisponibles = async (skus = []) => {
  const limpios = (skus || []).map((s) => (s || "").trim()).filter(Boolean);

  try {
    const query = limpios.length ? `?skus=${encodeURIComponent(limpios.join(","))}` : "";

    const response = await fetchFromAPI(
      `${API_CONFIG.endpoints.kitsDisponibles}${query}`,
      { baseURL: API_CONFIG.kitsBaseURL }
    );

    return response.kits || [];
  } catch (error) {
    console.error("Error al obtener pedidos KIT disponibles:", error);
    return null;
  }
};

// Persiste las marcas de KIT de esta cotización. Se llama al pulsar Siguiente en
// Contenedores, no en cada clic: el comercial ajusta cantidades varias veces
// antes de decidir, y escribir en cada pulsación llenaría HubSpot de estados
// intermedios que nadie ha confirmado.
//
// Manda el conjunto COMPLETO de marcas, no un incremento: los KIT que no van en
// la lista se desmarcan en el servidor. Así desmarcar en la card desmarca de
// verdad, sin llevar la cuenta de lo que se ha quitado.
//
// @param {string} cotizacionId  dealId de la cotización (context.crm.objectId)
// @param {Array<{kit_id: string, cajas: number}>} marcas
export const guardarMarcasKit = async (cotizacionId, marcas = []) => {
  if (!cotizacionId) return null;

  try {
    return await fetchFromAPI(API_CONFIG.endpoints.kitsMarcas, {
      method: "POST",
      baseURL: API_CONFIG.kitsBaseURL,
      body: { cotizacion_id: String(cotizacionId), marcas },
    });
  } catch (error) {
    console.error("Error al guardar las marcas de pedidos KIT:", error);
    return null;
  }
};

// `tamano_kit` y `bloque_multiplo` de los productos de la cotización, leídos de
// HubSpot. Sustituye al fichero de parámetros escrito a mano: con esto, cada
// producto empieza a generar KIT en cuanto PROEX le ponga el lote mínimo, sin
// tocar código.
//
// Devuelve {} si falla: sin parámetros no se calcula ningún KIT, que es más
// seguro que calcularlo con un bloque inventado.
export const fetchKitParams = async (skus = []) => {
  const limpios = (skus || []).map((s) => (s || "").trim()).filter(Boolean);
  if (limpios.length === 0) return {};

  try {
    const response = await fetchFromAPI(
      `${API_CONFIG.endpoints.kitsParams}?skus=${encodeURIComponent(limpios.join(","))}`,
      { baseURL: API_CONFIG.kitsBaseURL }
    );
    return response.params || {};
  } catch (error) {
    console.error("Error al obtener los parámetros de KIT:", error);
    return {};
  }
};

// Ofertas abiertas que podrían generar el KIT junto con esta cotización.
//
// Un pedido KIT no nace de una sola oferta: PROEX suma las cajas de varias
// cotizaciones abiertas de la misma referencia y pide un único KIT por la
// diferencia. El backend filtra por pipeline de cotización sin ganar ni perder,
// y devuelve las cajas ya sumadas de todos los contenedores de cada negocio.
//
// La cotización actual se excluye: en la tabla va siempre dentro y no se puede
// quitar, así que ofrecerla sería ofrecer desmarcar lo que no se desmarca.
//
// Un mapa vacío es una respuesta normal y frecuente: significa que esta oferta es
// la única abierta con esa referencia.
//
// @param {string[]} skus
// @param {string} excluirNegocioId
// @returns {Promise<Object<string, Array<{id,nombre,etapa,cajas}>>>}
export const fetchNegociosGeneradores = async (skus = [], excluirNegocioId = "") => {
  const limpios = (skus || []).map((s) => (s || "").trim()).filter(Boolean);
  if (limpios.length === 0) return {};

  try {
    const params = new URLSearchParams({ skus: limpios.join(",") });
    if (excluirNegocioId) params.set("excluir", excluirNegocioId);

    const response = await fetchFromAPI(
      `${API_CONFIG.endpoints.kitsNegocios}?${params.toString()}`,
      { baseURL: API_CONFIG.kitsBaseURL }
    );
    return response.negocios || {};
  } catch (error) {
    // Sin lista no se puede anotar otras ofertas, pero el KIT de esta sí se puede
    // pedir: se devuelve vacío en vez de romper la tabla entera.
    console.error("Error al obtener los negocios generadores:", error);
    return {};
  }
};

// Crea los pedidos KIT que genera esta cotización. Se llama al publicar, cuando
// la publicación ya ha salido bien: si esto falla, la cotización sigue publicada.
//
// El backend es idempotente por (cotización, SKU), así que republicar no duplica.
// Devuelve `creados` y `omitidos` — los omitidos son los que ya existían.
//
// @param {string} cotizacionId
// @param {Array<{sku: string, nombre: string, cajas: number}>} kits
export const crearKitsDeCotizacion = async (cotizacionId, kits = []) => {
  if (!cotizacionId || kits.length === 0) return null;

  try {
    return await fetchFromAPI(API_CONFIG.endpoints.kitsCrear, {
      method: "POST",
      baseURL: API_CONFIG.kitsBaseURL,
      body: { cotizacion_id: String(cotizacionId), kits },
    });
  } catch (error) {
    console.error("Error al crear los pedidos KIT:", error);
    return null;
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
