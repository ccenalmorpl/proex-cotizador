// TEMPORAL — sustituir por las propiedades reales del Producto en la fase 2.
//
// `tamano_kit` y `bloque_multiplo` ya existen en HubSpot sobre el objeto Producto,
// pero el backend todavía no las devuelve: el catálogo se sirve desde
// `ProductItem` (schemas.py), y hasta que se añadan ahí no llegan a la card.
// Mientras tanto este fixture las suple, keyed por SKU.
//
// Para retirarlo: añadir los dos campos a `ProductItem`, propagarlos en
// ARTICULOS_DISPONIBLES (ContenedoresTab) y borrar este fichero — la lectura ya
// prioriza el valor del artículo sobre el fixture, así que no hay que tocar la
// lógica de kits.utils.js.

export const KIT_PARAMS_POR_SKU = {
  // CERVEZA-VITALSBERG-5.0%-LATA-4X6X33CL-3BNVBLBF (product id 42078145076)
  //
  // 1894 es el valor REAL leído de HubSpot, no una estimación: coincide con el
  // ejemplo que envió Administración (14508 cajas -> 8 bloques -> 15152 -> KIT de
  // 644). Antes aquí había un 3000 inventado que daba números que no cuadraban
  // con nada.
  //
  // Con 2418 cajas por D20DR y 2 contenedores: 4836 cajas ->
  // ceil(4836/1894) = 3 bloques -> 5682 -> KIT de 846.
  "0303003002-00250": { tamanoKit: 1894, bloqueMultiplo: 1 },
};
