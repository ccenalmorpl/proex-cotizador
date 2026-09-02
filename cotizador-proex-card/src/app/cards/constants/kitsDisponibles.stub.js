// FOTO DE DATOS REALES — KIT abiertos disponibles para consumir.
//
// Esto ya NO es inventado: son los KIT que hay ahora mismo en el pipeline
// `922791345` del portal 48336053. Generado con:
//
//   python scripts/kits_disponibles.py --json
//
// (en `cotizador-proex-microservice`). Para refrescarlo, vuelve a correrlo y pega
// la salida aquí. Es una foto, no datos en vivo: si Administración marca o agota
// un KIT, este fichero no se enterará hasta que se regenere.
//
// No hay endpoint todavía: hará falta uno que liste los negocios del pipeline en
// etapa `Abierto` filtrados por `producto_kit_sku`. Ese endpoint tiene que vivir
// en el API Gateway que ya está en `permittedUrls.fetch`, porque HubSpot valida
// esa lista en servidor y la card no puede llamar a ningún otro host.
//
// Para retirarlo: cambiar la fuente en ContenedoresTab por la llamada real y
// borrar este fichero. El componente no cambia.
//
// Los KIT son 1:1 con producto ("un pedido kit es de un producto y no de varios"),
// así que el filtro del panel es por SKU de los artículos que hay en la cotización.
//
// `marcas` viene vacío porque ninguno de los dos está marcado todavía. Cuando lo
// esté, el nombre del negocio y su propietario exigen leer cada negocio marcador
// y su owner: eso es trabajo del endpoint, no de un volcado.

export const KITS_DISPONIBLES_STUB = [
  {
    id: "64133402483",
    numeroDeKit: "KIT-96001-01",
    productoSku: "0303003002-00250",
    productoNombre: "CERVEZA-VITALSBERG-5.0%-LATA-4X6X33CL-3BNVBLBF",
    cajasDisponibles: 300,
    cajasKit: 300,
    marcas: [],
  },
  {
    id: "64133525876",
    numeroDeKit: "KIT-96001-02",
    productoSku: "0303003002-00250",
    productoNombre: "CERVEZA-VITALSBERG-5.0%-LATA-4X6X33CL-3BNVBLBF",
    cajasDisponibles: 224,
    cajasKit: 224,
    marcas: [],
  },
];
