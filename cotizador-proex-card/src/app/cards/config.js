export const API_CONFIG = {
  // URL del microservicio Lambda (PRODUCCIÓN - API Gateway HTTP API)
  baseURL: 'https://hvw14az2bf.execute-api.eu-south-2.amazonaws.com',

  // Secreto compartido con el microservicio (PROEX_CARD_SECRET). hubspot.fetch()
  // descarta cualquier header custom salvo Authorization, así que viaja como
  // "Bearer <cardSecret>". Vive en el bundle igual que baseURL: mismo modelo
  // de confianza (ver auditoría de seguridad del traspaso).
  cardSecret: '776248e4854ff020db1295083d34c858ce49b897e4bf25ba0171c882fc07eec5',

  // SOLO DESARROLLO — backend local de pedidos KIT (kits_local.py) expuesto con
  // ngrok. Vacío significa "usa baseURL", que es lo que debe quedar en cuanto el
  // endpoint se despliegue en el API Gateway.
  //
  // La URL cambia cada vez que se reinicia ngrok, y hay que añadirla TAMBIÉN a
  // `permittedUrls.fetch` en `src/app/app-hsmeta.json`: HubSpot valida esa lista
  // en servidor, así que sin eso la llamada falla sin salir del navegador.
  kitsBaseURL: '',

  // Endpoints disponibles (para referencia)
  endpoints: {
    root: '/',
    health: '/health',
    deals: '/api/v1/deals',
    articles: '/api/v1/articles',
    kitsDisponibles: '/api/v1/kits/disponibles',
    kitsMarcas: '/api/v1/kits/marcas',
    // Agregar aquí endpoints futuros cuando se implementen
    // quotes: '/api/v1/quotes',
    // etc.
  },

  // Timeout por defecto para las peticiones (30 segundos)
  timeout: 30000
};
