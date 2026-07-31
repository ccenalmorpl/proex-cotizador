export const API_CONFIG = {
  // URL del microservicio Lambda (PRODUCCIÓN - API Gateway HTTP API)
  baseURL: 'https://hvw14az2bf.execute-api.eu-south-2.amazonaws.com',

  // Secreto compartido con el microservicio (PROEX_CARD_SECRET). hubspot.fetch()
  // descarta cualquier header custom salvo Authorization, así que viaja como
  // "Bearer <cardSecret>". Vive en el bundle igual que baseURL: mismo modelo
  // de confianza (ver auditoría de seguridad del traspaso).
  cardSecret: '776248e4854ff020db1295083d34c858ce49b897e4bf25ba0171c882fc07eec5',

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
