// `tamano_kit` y `bloque_multiplo` por SKU, leídos de HubSpot.
//
// Existe para que la card deje de depender de `kits.fixture.js`, que tenía un
// único SKU escrito a mano: aunque PROEX rellenara el lote mínimo en el resto del
// catálogo, la card no se enteraba.
//
// Un producto sin `tamano_kit` no aparece en el mapa, y eso es correcto: sin
// bloque no se calcula KIT, en vez de inventarse uno.

import { useEffect, useState } from "react";

import { fetchKitParams } from "../utils/api.utils";

export const useKitParams = (skus = []) => {
  const [params, setParams] = useState({});

  // Se serializan los SKU para no relanzar el efecto por recibir un array nuevo
  // con el mismo contenido en cada render.
  const clave = (skus || [])
    .map((s) => (s || "").trim())
    .filter(Boolean)
    .sort()
    .join(",");

  useEffect(() => {
    if (!clave) return;
    let cancelado = false;

    (async () => {
      const p = await fetchKitParams(clave.split(","));
      if (!cancelado) setParams(p || {});
    })();

    return () => {
      cancelado = true;
    };
  }, [clave]);

  return params;
};

export default useKitParams;
