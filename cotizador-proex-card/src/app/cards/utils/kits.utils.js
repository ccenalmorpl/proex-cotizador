// Lógica de pedidos KIT — funciones puras, sin React ni HTTP.
//
// Un pedido KIT cubre lo que sobra cuando el pedido del cliente no completa un
// bloque de fabricación. Dos invariantes del diseño que gobiernan todo
// este fichero:
//
//   1. El KIT es POR PRODUCTO y a nivel de NEGOCIO, no de contenedor: las cajas
//      del mismo artículo se suman en todos los contenedores (y por su cantidad
//      de instancias) antes de decidir si falta producción.
//   2. Un KIT es de un solo producto. Un pedido puede generar varios KITs
//      independientes, uno por artículo que no llegue al mínimo.
//
// Aislado a propósito: nada de aquí se llama desde la lógica existente de
// contenedores, sólo desde los componentes de KIT.

const toNumber = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
};

/**
 * Cajas totales por artículo en todo el pedido.
 *
 * Cada entrada de `contenedoresDefinidos` es una DEFINICIÓN de contenedor que
 * puede repetirse N veces (`cantidadesContenedores[id]`), así que las cajas de
 * sus artículos se multiplican por esa cantidad: 3 contenedores × 7 cajas = 21.
 *
 * @returns {Map<string|number, number>} articuloId → cajas totales
 */
export function agregarCajasPorArticulo(contenedoresDefinidos, cantidadesContenedores) {
  const porArticulo = new Map();

  (contenedoresDefinidos || []).forEach((contenedor) => {
    if (!contenedor) return;
    const instancias = toNumber((cantidadesContenedores || {})[contenedor.id]);
    if (instancias <= 0) return;

    (contenedor.articulos || []).forEach((art) => {
      if (!art) return;
      const cajas = toNumber(art.numCajas) * instancias;
      if (cajas <= 0) return;
      porArticulo.set(art.articuloId, (porArticulo.get(art.articuloId) || 0) + cajas);
    });
  });

  return porArticulo;
}

/**
 * Cajas de KIT necesarias para completar la producción.
 *
 *   cajasKit = ceil(q / (T·B)) · (T·B) − q
 *
 * `T` (tamanoKit) es el bloque de fabricación y `B` (bloqueMultiplo) el
 * número de bloques que exige producir de golpe. Con B=1 se completa hasta el
 * siguiente múltiplo del mínimo; con B>1 hasta el siguiente múltiplo de B
 * bloques, que es el caso de los saborizantes (una vez abierta la bolsa hay que
 * consumirla, así que la fábrica no acepta fracciones).
 *
 * Devuelve 0 si el producto no tiene mínimo, si las cajas son 0 o si la
 * cantidad ya es múltiplo exacto.
 */
export function calcularKitsNecesarios(cajas, tamanoKit, bloqueMultiplo = 1) {
  const q = toNumber(cajas);
  const t = toNumber(tamanoKit);
  // Vacío o 0 se interpreta como 1: sólo los saborizantes rellenan el múltiplo.
  const b = toNumber(bloqueMultiplo) > 0 ? toNumber(bloqueMultiplo) : 1;

  if (q <= 0 || t <= 0) return 0;

  const bloque = t * b;
  return Math.ceil(q / bloque) * bloque - q;
}

/**
 * Cuentas de producción de un artículo, descontando el stock ya marcado.
 *
 * El stock existente se descuenta ANTES de redondear: PROEX solo pide a fábrica
 * lo que le falta. Y lo que pide se produce en bloques enteros —"no es que sea
 * el pedido mínimo, son los bloques en los que se produce y tiene que coincidir:
 * si produzco de 5 en 5, por mucho que me pidas 6, te tengo que hacer otros 4"—.
 *
 * `umbralParaEvitar` es cuántas cajas habría que marcar para que la producción
 * baje un bloque entero y no haga falta KIT. Es el dato accionable: marcar poco
 * no evita el KIT (solo cambia de dónde salen las cajas), marcar hasta el umbral
 * sí lo elimina.
 */
export function calcularProduccion(cajasPedidas, marcadas, tamanoKit, bloqueMultiplo = 1) {
  const q = Math.max(0, toNumber(cajasPedidas));
  const m = Math.min(Math.max(0, toNumber(marcadas)), q);
  const t = toNumber(tamanoKit);
  const b = toNumber(bloqueMultiplo) > 0 ? toNumber(bloqueMultiplo) : 1;

  const aProducir = q - m;
  if (t <= 0) {
    return { aProducir, bloque: 0, bloques: 0, tirada: aProducir, cajasKit: 0, umbralParaEvitar: 0 };
  }

  const bloque = t * b;
  const bloques = Math.ceil(aProducir / bloque);
  const tirada = bloques * bloque;

  return {
    aProducir,
    bloque,
    bloques,
    tirada,
    cajasKit: tirada - aProducir,
    // Marcando esto, `aProducir` cae justo a un múltiplo exacto de bloque.
    umbralParaEvitar: Math.max(0, q - Math.floor(q / bloque) * bloque),
  };
}

/**
 * Añade a cada artículo el `tamano_kit` que venga de HubSpot.
 *
 * El catálogo no sirve esa propiedad, así que llega por su propio endpoint y se
 * mezcla aquí. Lo que ya traiga el artículo manda: el día que el catálogo la
 * incluya, esto deja de hacer nada y se puede borrar sin tocar el cálculo.
 *
 * @param {Array} articulos  con `sku`
 * @param {object} params    { [sku]: { tamano_kit, bloque_multiplo } }
 */
export function conParamsKit(articulos = [], params = {}) {
  return (articulos || []).map((a) => {
    if (!a) return a;
    const p = params[(a.sku || "").trim()];
    if (!p || toNumber(a.tamano_kit) > 0) return a;
    return { ...a, tamano_kit: p.tamano_kit, bloque_multiplo: p.bloque_multiplo };
  });
}

/**
 * Parámetros de KIT de un artículo, ya mezclados por `conParamsKit` con lo que
 * devuelve el endpoint. Sin `tamano_kit` devuelve null: quien llama decide si
 * avisar de la falta de dato, pero nadie se inventa un bloque.
 */
export function paramsKitDeArticulo(articulo) {
  if (!articulo) return null;

  const delArticulo = toNumber(articulo.tamano_kit);
  if (delArticulo > 0) {
    return {
      tamanoKit: delArticulo,
      bloqueMultiplo: toNumber(articulo.bloque_multiplo) || 1,
      origen: "producto",
    };
  }

  // Sin `tamano_kit` no se calcula KIT. Antes había aquí un fichero de respaldo
  // con un SKU escrito a mano, y era peor que nada: si HubSpot decía que ese
  // producto no tiene bloque, el respaldo lo contradecía con un número inventado.
  return null;
}

/**
 * Análisis completo de KITs del pedido actual.
 *
 * @returns {{kits: Array, sinDato: Array, totalCajasKit: number}}
 *   kits    — artículos que generan KIT, con las cajas que faltan
 *   sinDato — artículos con cajas pero sin mínimo definido. NO son un error:
 *             puede que la fábrica no produzca ese artículo en bloques. Se dan para
 *             poder avisar de la falta de dato en vez de esconderla (el
 *             "informe de cobertura" del diseño: ningún producto invisible).
 */
export function analizarKits(
  contenedoresDefinidos,
  cantidadesContenedores,
  articulos,
  marcadasPorSku
) {
  const porArticulo = agregarCajasPorArticulo(contenedoresDefinidos, cantidadesContenedores);
  return analizarKitsDesdeCajas(porArticulo, articulos, marcadasPorSku);
}

/**
 * Igual que `analizarKits` pero partiendo de un mapa ya agregado
 * `articuloId → cajas`. Lo usa el modal de edición per-contenedor, donde las
 * cajas se agregan desde otra forma de los datos (`contenedoresUnicos`) y con el
 * borrador en edición incluido.
 */
export function analizarKitsDesdeCajas(porArticulo, articulos, marcadasPorSku = new Map()) {
  const porId = new Map(
    (articulos || []).filter(Boolean).map((a) => [a.id, a])
  );

  const kits = [];
  const cubiertos = [];
  const sinDato = [];

  porArticulo.forEach((cajas, articuloId) => {
    const articulo = porId.get(articuloId);
    if (!articulo) return;

    const params = paramsKitDeArticulo(articulo);
    if (!params) {
      sinDato.push({ articuloId, nombre: articulo.nombre, sku: articulo.sku || "", cajas });
      return;
    }

    const sku = (articulo.sku || "").trim();
    const marcado = marcadasPorSku instanceof Map ? marcadasPorSku.get(sku) : null;
    // `marcadas` y `disponibles` son la suma de KIT + stock libre: al cálculo le
    // da igual de dónde salga una caja ya fabricada. El desglose de abajo existe
    // sólo para poder enseñarlo separado, como pidió PROEX.
    const marcadas = Number(marcado?.marcadas) || 0;
    const disponibles = Number(marcado?.disponibles) || 0;
    const kitDisponibles = Number(marcado?.kitDisponibles) || 0;
    const kitMarcadas = Number(marcado?.kitMarcadas) || 0;
    const stockUsado = Number(marcado?.stockUsado) || 0;

    const p = calcularProduccion(cajas, marcadas, params.tamanoKit, params.bloqueMultiplo);

    const fila = {
      articuloId,
      nombre: articulo.nombre,
      sku,
      cajas,
      tamanoKit: params.tamanoKit,
      bloqueMultiplo: params.bloqueMultiplo,
      marcadas,
      disponibles,
      kitDisponibles,
      kitMarcadas,
      stockUsado,
      aProducir: p.aProducir,
      bloque: p.bloque,
      bloques: p.bloques,
      tirada: p.tirada,
      cajasKit: p.cajasKit,
      cajasProduccion: p.tirada,
      // Cuántas cajas MÁS habría que marcar para librarse del KIT, y si el stock
      // abierto de esa referencia da para ello.
      faltanPorMarcar: Math.max(0, p.umbralParaEvitar - marcadas),
      umbralAlcanzable: p.umbralParaEvitar <= disponibles,
      // Bloques mínimos que obliga el déficit. Pedir más KIT = añadir bloques
      // enteros: la fábrica no sirve fracciones, así que las únicas cantidades
      // posibles son `cajasKit + n · bloque`.
      bloquesMinimos: p.bloques,
      // Lo que de verdad queda parado en fábrica tras esta oferta: el KIT nuevo
      // MÁS el stock viejo que no se ha consumido. Es el número que nunca sube
      // al marcar; el KIT nuevo aislado sí puede subir (cambias cajas viejas por
      // nuevas sin ahorrar bloque), y visto solo parece que marcar empeora.
      stockFinal: p.cajasKit + Math.max(0, disponibles - marcadas),
      // Referencia: lo que quedaría sin marcar nada, para poder mostrar el ahorro.
      stockFinalSinMarcar:
        calcularProduccion(cajas, 0, params.tamanoKit, params.bloqueMultiplo).cajasKit +
        disponibles,
    };

    // Cubierto: el stock marcado deja la producción en bloques exactos (o en 0).
    if (p.cajasKit <= 0) {
      if (marcadas > 0) cubiertos.push(fila);
      return;
    }
    kits.push(fila);
  });

  return {
    kits,
    cubiertos,
    sinDato,
    totalCajasKit: kits.reduce((sum, k) => sum + k.cajasKit, 0),
  };
}

/**
 * Marca efectiva de esta oferta sobre un KIT.
 *
 * Prioriza la edición local (lo que el comercial acaba de tocar) y, si no hay,
 * lee la marca YA GUARDADA en HubSpot para este negocio. Esa segunda parte es la
 * que hace que al volver a la cotización siga apareciendo marcada.
 *
 * Es lectura, no copia: no se vuelca nada al estado local. Si se volcara, habría
 * dos fuentes de verdad y un efecto que las sincronice — y el primer render con
 * los KIT aún sin llegar borraría las marcas.
 *
 * @param {object} kit         del endpoint, con `marcas: [{negocioId, cajas}]`
 * @param {object} marcados    estado local por id de KIT
 * @param {string} negocioId   la cotización abierta (context.crm.objectId)
 */
export function marcaEfectivaDeKit(kit, marcados = {}, negocioId = "") {
  const disp = toNumber(kit?.cajasDisponibles);
  const local = (marcados || {})[kit?.id];

  const propia = negocioId
    ? (kit?.marcas || []).find(
        (m) => String(m?.negocioId || "") === String(negocioId || "")
      )
    : null;

  // Cualquier edición local manda, y se mezcla campo a campo con lo guardado.
  // Ojo: escribir una cantidad sin pulsar Marcar deja `marcado` sin definir, y
  // ahí hay que conservar la cifra tecleada — si se descartara, el campo volvería
  // al máximo y el comercial vería desaparecer lo que acaba de escribir.
  if (local && (local.marcado !== undefined || local.cajas !== undefined)) {
    const marcado =
      local.marcado !== undefined ? !!local.marcado : !!propia;
    const cajas = local.cajas ?? propia?.cajas ?? disp;
    return { marcado, cajas: Math.min(toNumber(cajas), disp), origen: "local" };
  }

  // Sin edición local: la marca que esta cotización ya tenga guardada.
  if (propia) {
    return {
      marcado: true,
      cajas: Math.min(toNumber(propia.cajas), disp),
      origen: "guardada",
    };
  }

  return { marcado: false, cajas: disp, origen: "ninguna" };
}

/** Marcas de OTRAS ofertas: las que no son de este negocio. */
export function marcasDeOtros(kit, negocioId = "") {
  return (kit?.marcas || []).filter(
    (m) => String(m?.negocioId || "") !== String(negocioId || "")
  );
}
