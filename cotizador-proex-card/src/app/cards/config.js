/**
 * Configuración del microservicio backend Proex
 *
 * IMPORTANTE: Para cambiar entre entornos (desarrollo/producción),
 * solo modifica la variable baseURL
 *
 * Ejemplo de URLs por entorno:
 * - Desarrollo local: 'http://localhost:8000' o ngrok
 * - Producción: 'https://hvw14az2bf.execute-api.eu-south-2.amazonaws.com'
 */

export const API_CONFIG = {
  // URL del microservicio Lambda (PRODUCCIÓN - API Gateway HTTP API)
  baseURL: 'https://hvw14az2bf.execute-api.eu-south-2.amazonaws.com',

  // Endpoints disponibles (para referencia)
  endpoints: {
    root: '/',
    health: '/health',
    deals: '/api/v1/deals',
    articles: '/api/v1/articles',
    // Agregar aquí endpoints futuros cuando se implementen
    // quotes: '/api/v1/quotes',
    // etc.
  },

  // Timeout por defecto para las peticiones (30 segundos)
  timeout: 30000
};
