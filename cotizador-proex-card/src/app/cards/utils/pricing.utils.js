// Fuente única de las fórmulas de cada incoterm, compartida entre NegociacionTab y RevisionTab.
export function calcularPrecioPorCajaPorIncoterm(
  precioEXW,
  incoterm,
  { gastosPorTramo, serviciosPorCaja, seguroPorCaja, comisionCliente }
) {
  switch (incoterm) {
    case "EXW":
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
  switch (incoterm) {
    case "EXW":
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

// Redondeo: si llega `options.incotermRef` los demás incoterms se derivan
// como `total_ref + (gastosX - gastosRef) × cajas` para mantener consistencia visual.
// Sin incotermRef se redondea precio/caja a 3 decimales antes de multiplicar por cajas.
export function calcularTotalesPorTipoVenta(
  contenedoresUnicos,
  preciosEXW,
  params,
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

  const incRef = options.incotermRef;

  contenedoresUnicos.forEach((contenedor) => {
    contenedor.container_config?.articulos?.forEach((art) => {
      const lineItemId = art.line_item_id;
      const costePorCaja = art.precio_de_coste || 0;
      const cajas = art.numCajas || 0;

      const margenInicial =
        art.margen_unitario !== null && art.margen_unitario !== undefined
          ? art.margen_unitario * 100
          : options.clienteData?.margen || 12;

      const precioEXW =
        preciosEXW[lineItemId] ||
        costePorCaja + (costePorCaja * margenInicial) / 100;

      // IVA efectivo = % IVA + % recargo equivalencia si aplica al cliente.
      // Usa options.getPorcentajesIVA → {iva, recargo}; fallback al getIVAPorcentaje legacy.
      let ivaPorcentaje = 0;
      if (options.tiposIVA && options.ivaData) {
        const tipoIVA =
          options.tiposIVA[lineItemId] || art.tipo_iva_repercutido;
        if (options.getPorcentajesIVA) {
          const { iva = 0, recargo = 0 } = options.getPorcentajesIVA(tipoIVA, options.ivaData) || {};
          const aplicaRE = (options.clienteData?.equ || "").toString().toLowerCase() === "si";
          ivaPorcentaje = iva + (aplicaRE ? recargo : 0);
        } else if (options.getIVAPorcentaje) {
          ivaPorcentaje = options.getIVAPorcentaje(tipoIVA, options.ivaData);
        }
      }

      if (incRef) {
        // Precio de referencia redondeado a 3 decimales (precio que el usuario ve)
        const precioRefPorCaja = calcularPrecioPorCajaPorIncoterm(precioEXW, incRef, params);
        const precioRefRedondeado = Math.round(precioRefPorCaja * 1000) / 1000;
        // Gastos del incoterm de referencia con precioEXW=0 para aislar la parte de costes
        const gastosRef = calcularPrecioPorCajaPorIncoterm(0, incRef, params);

        INCOTERMS.forEach((inc) => {
          let subtotal;
          if (inc === incRef) {
            subtotal = precioRefRedondeado * cajas;
          } else {
            const gastosX = calcularPrecioPorCajaPorIncoterm(0, inc, params);
            subtotal = (precioRefRedondeado + gastosX - gastosRef) * cajas;
          }
          totals[inc] += subtotal;
          totalsIVA[`${inc}_IVA`] += subtotal * (1 + ivaPorcentaje / 100);
        });
      } else {
        INCOTERMS.forEach((inc) => {
          const precioPorCaja = calcularPrecioPorCajaPorIncoterm(precioEXW, inc, params);
          const subtotal = Math.round(precioPorCaja * 1000) / 1000 * cajas;
          totals[inc] += subtotal;
          totalsIVA[`${inc}_IVA`] += subtotal * (1 + ivaPorcentaje / 100);
        });
      }

      totalCajasGlobal += cajas;
    });
  });

  // Calcular precio/caja global (total / totalCajas)
  if (totalCajasGlobal > 0) {
    INCOTERMS.forEach((inc) => {
      porCajaTotals[inc] = totals[inc] / totalCajasGlobal;
    });
  }

  return { ...totals, ...totalsIVA, porCaja: porCajaTotals };
}
