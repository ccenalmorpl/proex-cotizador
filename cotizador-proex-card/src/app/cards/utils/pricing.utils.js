// Normaliza el valor de tipo_de_venta a la clave canónica del incoterm.
// Quita acentos (los values de HubSpot son "DAP MARÍTIMO" / "DAP TERRESTRE")
// y espacios, para que matchee con "DAP_MARITIMO" / "DAP_TERRESTRE". Espejo de
// _normalize_incoterm en pricing.py.
export function normalizeIncoterm(value) {
  let v = value;
  if (v && typeof v === "object") v = v.value ?? v.internal ?? v.label;
  return (v || "EXW")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toUpperCase()
    .replace(/\s+/g, "_");
}

// Modo doble flete (transitario + flete por tramo): cliente nacional, cami\u00f3n
// (fleteDobleDisponible lo sirve el backend) e incoterm con tramo mar\u00edtimo y
// terrestre (CIF/CFR). \u00danico punto de verdad para que los tabs no diverjan.
export function esModoDobleFlete({ fleteDobleDisponible, tipoVenta, tipoCliente }) {
  const inc = normalizeIncoterm(tipoVenta);
  let cliente = tipoCliente;
  if (cliente && typeof cliente === "object") cliente = cliente.value;
  return (
    !!fleteDobleDisponible &&
    (inc === "CIF" || inc === "CFR") &&
    (cliente || "").toString().toLowerCase() === "nacional"
  );
}

// FCA se calcula/factura como EXW y NO tiene bucket propio en INCOTERMS. Para
// INDEXAR los objetos de totales (totals/totalsIVA de calcularTotalesPorTipoVenta,
// preciosPorTipoVenta) por el incoterm seleccionado, mapea FCA\u2192EXW. La l\u00f3gica que
// s\u00ed debe distinguir FCA (etiquetas de documento, validaciones) usa normalizeIncoterm.
export function bucketIncoterm(value) {
  const ic = normalizeIncoterm(value);
  return ic === "FCA" ? "EXW" : ic;
}

// Fuente única de las fórmulas de cada incoterm, compartida entre NegociacionTab y RevisionTab.
export function calcularPrecioPorCajaPorIncoterm(
  precioEXW,
  incoterm,
  { gastosPorTramo, serviciosPorCaja, seguroPorCaja, comisionCliente }
) {
  switch (normalizeIncoterm(incoterm)) {
    // FCA equivalente a EXW: sin flete/servicios/seguro en la base.
    case "EXW":
    case "FCA":
      return precioEXW + comisionCliente;
    case "DAP_TERRESTRE":
      return (
        precioEXW +
        gastosPorTramo.hastaLocalidadDestino +
        serviciosPorCaja +
        comisionCliente
      );
    case "FOB":
      return (
        precioEXW +
        gastosPorTramo.hastaPuertoSalida +
        serviciosPorCaja -
        seguroPorCaja +
        comisionCliente
      );
    case "CFR":
      return (
        precioEXW +
        gastosPorTramo.hastaPuertoSalida +
        serviciosPorCaja -
        seguroPorCaja +
        gastosPorTramo.hastaPuertoDestino +
        comisionCliente
      );
    case "CIF":
      return (
        precioEXW +
        gastosPorTramo.hastaPuertoSalida +
        serviciosPorCaja +
        gastosPorTramo.hastaPuertoDestino +
        comisionCliente
      );
    case "DAP_MARITIMO":
      return (
        precioEXW +
        gastosPorTramo.hastaPuertoSalida +
        serviciosPorCaja +
        gastosPorTramo.hastaPuertoDestino +
        gastosPorTramo.hastaPuertoDestinoLocalidad +
        comisionCliente
      );
    default:
      return precioEXW + comisionCliente;
  }
}

// Inversa: dado el precio final y el incoterm, devuelve el precio EXW.
export function calcularPrecioEXWDesdeIncoterm(
  precioFinal,
  incoterm,
  { gastosPorTramo, serviciosPorCaja, seguroPorCaja, comisionCliente }
) {
  switch (normalizeIncoterm(incoterm)) {
    // FCA equivalente a EXW: sin flete/servicios/seguro en la base.
    case "EXW":
    case "FCA":
      return precioFinal - comisionCliente;
    case "DAP_TERRESTRE":
      return (
        precioFinal -
        gastosPorTramo.hastaLocalidadDestino -
        serviciosPorCaja -
        comisionCliente
      );
    case "FOB":
      return (
        precioFinal -
        gastosPorTramo.hastaPuertoSalida -
        (serviciosPorCaja - seguroPorCaja) -
        comisionCliente
      );
    case "CFR":
      return (
        precioFinal -
        gastosPorTramo.hastaPuertoSalida -
        (serviciosPorCaja - seguroPorCaja) -
        gastosPorTramo.hastaPuertoDestino -
        comisionCliente
      );
    case "CIF": {
      const cfr = precioFinal - comisionCliente;
      return (
        cfr -
        gastosPorTramo.hastaPuertoSalida -
        serviciosPorCaja -
        gastosPorTramo.hastaPuertoDestino
      );
    }
    case "DAP_MARITIMO": {
      const cfr =
        precioFinal - comisionCliente - gastosPorTramo.hastaPuertoDestinoLocalidad;
      return (
        cfr -
        gastosPorTramo.hastaPuertoSalida -
        serviciosPorCaja -
        gastosPorTramo.hastaPuertoDestino
      );
    }
    default:
      return precioFinal - comisionCliente;
  }
}

const INCOTERMS = ["EXW", "DAP_TERRESTRE", "FOB", "CFR", "DAP_MARITIMO", "CIF"];

// Componentes incluidos en el precio por incoterm (espejo de
// calcularPrecioPorCajaPorIncoterm e iva_helpers.py). Sirven de gate para
// repercutir el IVA de cada concepto solo cuando forma parte de la base.
// El seguro NO entra en FOB/CFR (la fórmula lo resta).
const INCLUYE_FLETE_ORIGEN = new Set(["DAP_TERRESTRE", "FOB", "CFR", "CIF", "DAP_MARITIMO"]);
const INCLUYE_FLETE_DESTINO = new Set(["CFR", "CIF", "DAP_MARITIMO"]);
const INCLUYE_FLETE_DESTINO_LOCALIDAD = new Set(["DAP_MARITIMO"]);
const INCLUYE_SERVICIOS = new Set(["DAP_TERRESTRE", "FOB", "CFR", "CIF", "DAP_MARITIMO"]);
const INCLUYE_SEGURO = new Set(["DAP_TERRESTRE", "CIF", "DAP_MARITIMO"]);

// Comisión por caja con reparto INTRA-contenedor (espejo de
// `_comision_por_caja_intra` en pricing.py). `importe_por_negocio` reparte el
// importe del negocio entre los contenedores (importe/numContenedores) y esa
// cuota entre las cajas del contenedor: es el único modo en que añadir un
// contenedor mueve a los previos.
export function comisionPorCajaIntra(tipoComision, importeComision, cajasContenedor, numContenedores) {
  const imp = Number(importeComision) || 0;
  const cajas = Number(cajasContenedor) || 0;
  if (!tipoComision || imp <= 0 || cajas <= 0) return 0;
  const tc = tipoComision.toString().trim().toLowerCase();
  if (tc === "importe_por_negocio") return numContenedores > 0 ? imp / numContenedores / cajas : 0;
  if (tc === "importe_por_contenedor") return imp / cajas;
  if (tc === "importe_por_cajas") return imp;
  return 0;
}

// Construye el pricingParams de UN contenedor: flete/servicios/seguro/comisión
// repartidos entre las cajas de ESE contenedor (no del negocio). Espejo de
// `DealPricingContext.for_container` en pricing.py. `base` lleva los importes
// (a nivel de contenedor) y los datos de comisión del deal:
//   { precioOrigen, precioDestino, serviciosImporte, seguroImporte,
//     serviciosBuckets, seguroBuckets, importeComision, tipoComision,
//     repercutir, tieneIntermediario, numContenedores }
export function buildPricingParamsPorContenedor(base, cajasContenedor) {
  const cajas = Number(cajasContenedor) || 0;
  const b = base || {};
  if (cajas <= 0) {
    return {
      gastosPorTramo: { hastaLocalidadDestino: 0, hastaPuertoSalida: 0, hastaPuertoDestino: 0, hastaPuertoDestinoLocalidad: 0 },
      serviciosPorCaja: 0,
      seguroPorCaja: 0,
      comisionCliente: 0,
      serviciosNoSeguroPorIva: {},
      seguroPorIva: {},
    };
  }
  const fleteOrigen = (Number(b.precioOrigen) || 0) / cajas;
  const fleteDestino = (Number(b.precioDestino) || 0) / cajas;
  const serviciosNoSeguroPorIva = {};
  const seguroPorIva = {};
  const sb = b.serviciosBuckets || {};
  const gb = b.seguroBuckets || {};
  for (const code in sb) serviciosNoSeguroPorIva[code] = sb[code] / cajas;
  for (const code in gb) seguroPorIva[code] = gb[code] / cajas;
  let comisionCliente = 0;
  if (b.repercutir && b.tieneIntermediario) {
    comisionCliente = comisionPorCajaIntra(b.tipoComision, b.importeComision, cajas, b.numContenedores);
  }
  return {
    gastosPorTramo: {
      hastaLocalidadDestino: fleteOrigen,
      hastaPuertoSalida: fleteOrigen,
      hastaPuertoDestino: fleteDestino,
      hastaPuertoDestinoLocalidad: fleteDestino,
    },
    serviciosPorCaja: (Number(b.serviciosImporte) || 0) / cajas,
    seguroPorCaja: (Number(b.seguroImporte) || 0) / cajas,
    comisionCliente,
    serviciosNoSeguroPorIva,
    seguroPorIva,
  };
}

// IVA por caja de los conceptos compartidos (flete/servicios/seguro/comisión),
// por incoterm, a partir de un pricingParams ya por-contenedor. Los tramos de
// origen (salen de precioOrigen) gravan con pctFleteTerrestre y los de destino
// con pctFletes (espejo de iva_helpers.py).
function _ivaConceptosPorCajaPorIncoterm(params, pctFletes, pctFleteTerrestre, pctComision, pctConcepto) {
  const gp = params.gastosPorTramo || {};
  const serviciosNoSeguroPorIva = params.serviciosNoSeguroPorIva || {};
  const seguroPorIva = params.seguroPorIva || {};
  const out = {};
  INCOTERMS.forEach((inc) => {
    const fleteOrigen = inc === "DAP_TERRESTRE" ? (gp.hastaLocalidadDestino || 0) : (gp.hastaPuertoSalida || 0);
    let v = (params.comisionCliente || 0) * pctComision / 100;
    if (INCLUYE_FLETE_ORIGEN.has(inc)) v += fleteOrigen * pctFleteTerrestre / 100;
    if (INCLUYE_FLETE_DESTINO.has(inc)) v += (gp.hastaPuertoDestino || 0) * pctFletes / 100;
    if (INCLUYE_FLETE_DESTINO_LOCALIDAD.has(inc)) v += (gp.hastaPuertoDestinoLocalidad || 0) * pctFletes / 100;
    if (INCLUYE_SERVICIOS.has(inc)) {
      for (const code in serviciosNoSeguroPorIva) v += serviciosNoSeguroPorIva[code] * pctConcepto(code) / 100;
    }
    if (INCLUYE_SEGURO.has(inc)) {
      for (const code in seguroPorIva) v += seguroPorIva[code] * pctConcepto(code) / 100;
    }
    out[inc] = v;
  });
  return out;
}

// Reparto INTRA-contenedor: cada contenedor reparte sus importes entre SUS
// propias cajas (no sobre el total del negocio). El 3er argumento es `base`
// (importes a nivel de contenedor + datos de comisión); el pricingParams se
// construye por contenedor con sus cajas. El total del negocio coincide con el
// del reparto global; lo que cambia es el precio por contenedor.
// Redondeo: con `options.incotermRef` los demás incoterms se derivan como
// `total_ref + (gastosX - gastosRef) × cajas`; sin él se redondea precio/caja a
// 3 decimales antes de multiplicar por cajas.
export function calcularTotalesPorTipoVenta(
  contenedoresUnicos,
  preciosEXW,
  base,
  options = {}
) {
  const totals = {};
  const totalsIVA = {};
  const porCajaTotals = {};
  let totalCajasGlobal = 0;

  INCOTERMS.forEach((inc) => {
    totals[inc] = 0;
    totalsIVA[`${inc}_IVA`] = 0;
    porCajaTotals[inc] = 0;
  });

  if (!contenedoresUnicos || !Array.isArray(contenedoresUnicos)) {
    return { ...totals, ...totalsIVA, porCaja: porCajaTotals };
  }

  const incRef = options.incotermRef ? normalizeIncoterm(options.incotermRef) : options.incotermRef;

  // % efectivo de un concepto (flete/comisión/servicio): IVA + recargo si el
  // cliente lo tiene. Code vacío o desconocido → 0 (espejo del backend).
  const aplicaRE = (options.clienteData?.equ || "").toString().toLowerCase() === "si";
  const pctConcepto = (code) => {
    const c = (code || "").toString().trim().toUpperCase();
    if (!c) return 0;
    const hit = options.ivaData?.[c];
    if (!hit) return 0;
    return (hit.porcentaje_iva ?? 0) + (aplicaRE ? (hit.porcentaje_recargo ?? 0) : 0);
  };
  const pctFletes = pctConcepto(options.tipoIVAFletes);
  // Flete terrestre vacío → hereda el IVA de fletes marítimo (NUNCA el default por residencia).
  const pctFleteTerrestre = pctConcepto(options.tipoIVAFleteTerrestre || options.tipoIVAFletes);
  const pctComision = pctConcepto(options.tipoIVAComision);

  contenedoresUnicos.forEach((contenedor) => {
    const arts = contenedor.container_config?.articulos || [];
    const cajasContenedor = arts.reduce((s, a) => s + (a.numCajas || 0), 0);
    // pricingParams de ESTE contenedor (reparto sobre SUS cajas).
    const params = buildPricingParamsPorContenedor(base, cajasContenedor);
    const ivaConceptosPorCaja = _ivaConceptosPorCajaPorIncoterm(params, pctFletes, pctFleteTerrestre, pctComision, pctConcepto);

    arts.forEach((art) => {
      const lineItemId = art.line_item_id;
      const costePorCaja = art.precio_de_coste || 0;
      const cajas = art.numCajas || 0;

      const margenInicial =
        art.margen_unitario !== null && art.margen_unitario !== undefined
          ? art.margen_unitario * 100
          : options.clienteData?.margen || 12;

      const precioEXW =
        preciosEXW[lineItemId] != null
          ? preciosEXW[lineItemId]
          : costePorCaja + (costePorCaja * margenInicial) / 100;

      // Línea promocional (EXW 0): no se repercute NADA (flete/servicios/comisión),
      // aporta 0 a todos los incoterms. Las cajas siguen contando para el precio/caja medio.
      if (precioEXW === 0) {
        totalCajasGlobal += cajas;
        return;
      }

      // IVA efectivo = % IVA + % recargo equivalencia si aplica al cliente.
      let ivaPorcentaje = 0;
      if (options.tiposIVA && options.ivaData) {
        const tipoIVA =
          options.tiposIVA[lineItemId] || art.tipo_iva_repercutido;
        if (options.getPorcentajesIVA) {
          const { iva = 0, recargo = 0 } = options.getPorcentajesIVA(tipoIVA, options.ivaData) || {};
          ivaPorcentaje = iva + (aplicaRE ? recargo : 0);
        } else if (options.getIVAPorcentaje) {
          ivaPorcentaje = options.getIVAPorcentaje(tipoIVA, options.ivaData);
        }
      }

      if (incRef) {
        const precioRefPorCaja = calcularPrecioPorCajaPorIncoterm(precioEXW, incRef, params);
        const gastosRef = calcularPrecioPorCajaPorIncoterm(0, incRef, params);

        INCOTERMS.forEach((inc) => {
          let subtotal;
          if (inc === incRef) {
            subtotal = precioRefPorCaja * cajas;
          } else {
            const gastosX = calcularPrecioPorCajaPorIncoterm(0, inc, params);
            subtotal = (precioRefPorCaja + gastosX - gastosRef) * cajas;
          }
          totals[inc] += subtotal;
          totalsIVA[`${inc}_IVA`] +=
            subtotal + precioEXW * cajas * (ivaPorcentaje / 100) + ivaConceptosPorCaja[inc] * cajas;
        });
      } else {
        INCOTERMS.forEach((inc) => {
          const precioPorCaja = calcularPrecioPorCajaPorIncoterm(precioEXW, inc, params);
          const subtotal = precioPorCaja * cajas;
          totals[inc] += subtotal;
          totalsIVA[`${inc}_IVA`] +=
            subtotal + precioEXW * cajas * (ivaPorcentaje / 100) + ivaConceptosPorCaja[inc] * cajas;
        });
      }

      totalCajasGlobal += cajas;
    });
  });

  // precio/caja medio del negocio (total / totalCajas) — solo informativo.
  if (totalCajasGlobal > 0) {
    INCOTERMS.forEach((inc) => {
      porCajaTotals[inc] = totals[inc] / totalCajasGlobal;
    });
  }

  return { ...totals, ...totalsIVA, porCaja: porCajaTotals };
}
