export const isValuePresent = (value) => {
  return value !== undefined && value !== null && value !== "";
};

export const validateRequired = (value, fieldName) => {
  if (!isValuePresent(value)) {
    return `${fieldName} es requerido`;
  }
  return null;
};

export const validateNumber = (value, fieldName) => {
  if (typeof value !== "number" || isNaN(value)) {
    return `${fieldName} debe ser un número válido`;
  }
  return null;
};

export const validateRange = (value, min, max, fieldName) => {
  if (value < min || value > max) {
    return `${fieldName} debe estar entre ${min} y ${max}`;
  }
  return null;
};

export const validateMargen = (margen) => {
  if (!isValuePresent(margen)) {
    return "El margen es requerido";
  }

  const number = Number(margen);
  if (isNaN(number)) {
    return "El margen debe ser un número válido";
  }

  if (number < 0 || number > 100) {
    return "El margen debe estar entre 0 y 100";
  }

  return null;
};

export const getClienteValidationMessages = (clienteData) => {
  const messages = [];

  if (!clienteData.tipoVenta) {
    messages.push("Debe seleccionar un Tipo de Venta");
  }

  if (!clienteData.tipoContenedor) {
    messages.push("Debe seleccionar un Tipo de Contenedor");
  }

  const margenError = validateMargen(clienteData.margen);
  if (margenError) {
    messages.push("Debe especificar el % Margen");
  }

  if (!clienteData.fechaCaducidadCotizacion) {
    messages.push("Debe especificar la fecha de caducidad de la cotización");
  }

  return messages;
};

export const validateForm = (data, rules) => {
  const errors = {};

  Object.entries(rules).forEach(([field, validations]) => {
    for (const validation of validations) {
      const error = validation(data[field]);
      if (error) {
        errors[field] = error;
        break;
      }
    }
  });

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
};
