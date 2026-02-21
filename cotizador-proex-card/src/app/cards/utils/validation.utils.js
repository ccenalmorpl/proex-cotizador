/**
 * Validation utilities for form fields
 */

/**
 * Check if a value is present (not undefined, null, or empty string)
 * @param {*} value - Value to check
 * @returns {boolean}
 */
export const isValuePresent = (value) => {
  return value !== undefined && value !== null && value !== "";
};

/**
 * Validate required field
 * @param {*} value - Value to validate
 * @param {string} fieldName - Name of the field for error message
 * @returns {string|null} - Error message or null if valid
 */
export const validateRequired = (value, fieldName) => {
  if (!isValuePresent(value)) {
    return `${fieldName} es requerido`;
  }
  return null;
};

/**
 * Validate number field
 * @param {*} value - Value to validate
 * @param {string} fieldName - Name of the field for error message
 * @returns {string|null} - Error message or null if valid
 */
export const validateNumber = (value, fieldName) => {
  if (typeof value !== "number" || isNaN(value)) {
    return `${fieldName} debe ser un número válido`;
  }
  return null;
};

/**
 * Validate number range
 * @param {number} value - Value to validate
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @param {string} fieldName - Name of the field for error message
 * @returns {string|null} - Error message or null if valid
 */
export const validateRange = (value, min, max, fieldName) => {
  if (value < min || value > max) {
    return `${fieldName} debe estar entre ${min} y ${max}`;
  }
  return null;
};

/**
 * Validate margen field (percentage 0-100)
 * @param {*} margen - Margen value to validate
 * @returns {string|null} - Error message or null if valid
 */
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

/**
 * Get validation messages for cliente data
 * @param {Object} clienteData - Cliente data object
 * @returns {string[]} - Array of validation error messages
 */
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

  return messages;
};

/**
 * Validate form data against rules
 * @param {Object} data - Form data to validate
 * @param {Object} rules - Validation rules { fieldName: [validatorFn, ...] }
 * @returns {{ isValid: boolean, errors: Object }} - Validation result
 */
export const validateForm = (data, rules) => {
  const errors = {};

  Object.entries(rules).forEach(([field, validations]) => {
    for (const validation of validations) {
      const error = validation(data[field]);
      if (error) {
        errors[field] = error;
        break; // Stop at first error per field
      }
    }
  });

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
};
