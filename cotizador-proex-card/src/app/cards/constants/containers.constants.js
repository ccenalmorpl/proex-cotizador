// Container type values (must match HubSpot property values - no spaces)
export const CONTAINER_TYPES = {
  D20DR: "D20DR",
  D40DR: "D40DR",
  TRAILER_80: "TRAILER80",
};

// Display labels for container types (user-friendly)
export const CONTAINER_TYPE_LABELS = {
  D20DR: "D20DR",
  D40DR: "D40DR",
  TRAILER80: "TRAILER 80",
};

export const getContainerTypeLabel = (containerType) => {
  return CONTAINER_TYPE_LABELS[containerType] || containerType;
};

// Mapping of container types to capacity fields in product data
export const CONTAINER_CAPACITY_FIELDS = {
  [CONTAINER_TYPES.D20DR]: "cajas20",
  [CONTAINER_TYPES.D40DR]: "cajas40",
  [CONTAINER_TYPES.TRAILER_80]: "cajasTrailer80",
};

// Maximum occupation percentage for containers
export const MAX_CONTAINER_OCCUPATION = 100;

// Default container occupation warning threshold
export const CONTAINER_WARNING_THRESHOLD = 95;
