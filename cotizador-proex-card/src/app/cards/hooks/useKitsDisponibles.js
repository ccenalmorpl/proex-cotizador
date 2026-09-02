import { useEffect, useState } from "react";

import { KITS_DISPONIBLES_STUB } from "../constants/kitsDisponibles.stub";
import { fetchKitsDisponibles } from "../utils/api.utils";

export const ORIGEN_VIVO = "vivo";
export const ORIGEN_FIXTURE = "fixture";

/**
 * Pedidos KIT abiertos consumibles para los artículos de la cotización.
 *
 * Aislado en un hook para que el origen de los datos sea invisible a quien los
 * pinta: salen del microservicio, y ni `PanelKitsDisponibles` ni
 * `ContenedoresTab` saben de dónde vienen.
 *
 * Degrada al fixture a propósito: si la llamada falla, se sigue viendo la
 * pantalla con la última foto conocida en vez de quedarse en blanco. Pero eso es
 * la excepción — el camino normal es leer de HubSpot.
 *
 * @param {string[]} skus SKUs de los artículos de la cotización
 * @returns {{
 *   kits: Array,
 *   origen: ('vivo'|'fixture'),
 *   cargando: boolean
 * }}
 */
export const useKitsDisponibles = (skus = []) => {
  const [estado, setEstado] = useState({
    kits: KITS_DISPONIBLES_STUB,
    origen: ORIGEN_FIXTURE,
    cargando: false,
  });

  // Se serializan los SKU para que el efecto no se relance al recibir un array
  // nuevo con el mismo contenido en cada render.
  const clave = (skus || [])
    .map((s) => (s || "").trim())
    .filter(Boolean)
    .sort()
    .join(",");

  useEffect(() => {
    let cancelado = false;
    setEstado((prev) => ({ ...prev, cargando: true }));

    (async () => {
      const kits = await fetchKitsDisponibles(clave ? clave.split(",") : []);
      if (cancelado) return;

      // `null` es fallo de la llamada -> fixture. Un array vacío es una respuesta
      // legítima (no hay KIT para esos SKU) y cuenta como dato en vivo.
      setEstado(
        kits === null
          ? { kits: KITS_DISPONIBLES_STUB, origen: ORIGEN_FIXTURE, cargando: false }
          : { kits, origen: ORIGEN_VIVO, cargando: false }
      );
    })();

    return () => {
      cancelado = true;
    };
  }, [clave]);

  return estado;
};

export default useKitsDisponibles;
