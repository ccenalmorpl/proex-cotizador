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

// Default por residencia: repercutido → NACIONAL R21 / EUROPA EI21 / EXTRANJERO EX;
// soportado → NACIONAL S21 / resto igual.
export function getDefaultIVAByResidencia(tipoResidente, tipoIVAOptions, categoria = "repercutido") {
  if (!tipoResidente || !tipoIVAOptions || tipoIVAOptions.length === 0) {
    return "";
  }

  const residencia = tipoResidente.toUpperCase();
  let defaultIVA;

  if (residencia === "EXTRANJERO") {
    defaultIVA = "EX";
  } else if (residencia === "EUROPA") {
    defaultIVA = "EI21";
  } else {
    defaultIVA = categoria === "soportado" ? "S21" : "R21";
  }

  const ivaOption = tipoIVAOptions.find((opt) => opt.value === defaultIVA);
  return ivaOption ? defaultIVA : "";
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
