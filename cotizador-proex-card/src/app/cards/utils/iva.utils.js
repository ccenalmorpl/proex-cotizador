// Resuelve el IVA del producto según residencia + categoría.
// NACIONAL → tipo_iva_{categoria}, EUROPA → ..._eu, EXTRANJERO → ..._ex
export function getIVAFromProduct(product, tipoResidente, categoria) {
  if (!product || !tipoResidente || !categoria) return null;

  const residencia = tipoResidente.toUpperCase();
  let propertyName;

  if (residencia === "EUROPA") {
    propertyName = `tipo_iva_${categoria}_eu`;
  } else if (residencia === "EXTRANJERO") {
    propertyName = `tipo_iva_${categoria}_ex`;
  } else {
    propertyName = `tipo_iva_${categoria}`;
  }

  const value = product[propertyName];
  return value && value !== "" ? value : null;
}

// currentValue NUNCA se sobrescribe; si está vacío se prefiere el valor resuelto por backend,
// luego se intenta desde `product`, y como último recurso el default por residencia+categoría.
export function resolveIVA({
  currentValue,
  productResolvedValue,
  product,
  tipoResidente,
  categoria,
  options,
}) {
  if (currentValue !== undefined && currentValue !== null && currentValue !== "") {
    return currentValue;
  }

  if (productResolvedValue) {
    const exists = options.some((opt) => opt.value === productResolvedValue);
    if (exists) return productResolvedValue;
  }

  if (product) {
    const fromProduct = getIVAFromProduct(product, tipoResidente, categoria);
    if (fromProduct) {
      const exists = options.some((opt) => opt.value === fromProduct);
      if (exists) return fromProduct;
    }
  }

  return getDefaultIVAByResidencia(tipoResidente, options, categoria);
}

// EX y EI21 son códigos REPERCUTIDOS: no valen como default de soportado. Fuera de
// NACIONAL la categoría soportado no tiene equivalente unívoco (compra
// intracomunitaria vs importación vs inversión del sujeto pasivo) y se deja sin
// default en vez de arriesgar un código que rompa la sync con SAP.
// Espejo de _DEFAULT_TIPO_IVA en el microservicio (app/utils/property_helpers.py).
const DEFAULT_IVA_POR_RESIDENCIA = {
  repercutido: { NACIONAL: "R21", EUROPA: "EI21", EXTRANJERO: "EX" },
  soportado: { NACIONAL: "S21" },
};

// Devuelve "" si no hay default para esa categoría+residencia o si el código no
// está entre las opciones recibidas (que ya vienen filtradas por categoría).
export function getDefaultIVAByResidencia(tipoResidente, tipoIVAOptions, categoria = "repercutido") {
  if (!tipoResidente || !tipoIVAOptions || tipoIVAOptions.length === 0) {
    return "";
  }

  const porResidencia = DEFAULT_IVA_POR_RESIDENCIA[categoria] || {};
  const defaultIVA = porResidencia[tipoResidente.toUpperCase()];
  if (!defaultIVA) {
    return "";
  }

  return tipoIVAOptions.some((opt) => opt.value === defaultIVA) ? defaultIVA : "";
}

export function getIVAPorcentaje(tipoIVA, ivaDataMap) {
  if (!tipoIVA || tipoIVA === "") {
    return 21;
  }
  if (ivaDataMap && tipoIVA in ivaDataMap) {
    return ivaDataMap[tipoIVA].porcentaje_iva ?? ivaDataMap[tipoIVA].porcentaje_total ?? 21;
  }
  return 21;
}

// Devuelve {iva, recargo} (recargo equivalencia sólo aplica si la company lo tiene).
// Si el tipo de IVA no está en el mapa, asume 21 / 0 (default nacional sin RE).
export function getPorcentajesIVA(tipoIVA, ivaDataMap) {
  if (!tipoIVA || tipoIVA === "") {
    return { iva: 21, recargo: 0 };
  }
  const hit = ivaDataMap?.[tipoIVA];
  if (!hit) return { iva: 21, recargo: 0 };
  return {
    iva: hit.porcentaje_iva ?? 0,
    recargo: hit.porcentaje_recargo ?? 0,
  };
}

// % efectivo = IVA + recargo si aplicaRE === true.
export function getIVAEfectivo(tipoIVA, ivaDataMap, aplicaRE) {
  const { iva, recargo } = getPorcentajesIVA(tipoIVA, ivaDataMap);
  return iva + (aplicaRE ? recargo : 0);
}

// Resuelve si la company tiene recargo de equivalencia.
// company.equ guarda "si"/"no" (case-insensitive).
export function aplicaRecargoEquivalencia(clienteData) {
  return (clienteData?.equ || "").toString().toLowerCase() === "si";
}
