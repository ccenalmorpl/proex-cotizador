import { useState, useEffect } from "react";

/**
 * Custom hook to debounce a value
 * @param {*} value - Value to debounce
 * @param {number} delay - Delay in milliseconds (default: 500)
 * @returns {*} - Debounced value
 */
export const useDebounce = (value, delay = 500) => {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
};
