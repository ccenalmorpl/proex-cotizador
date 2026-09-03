// Ofertas abiertas que podrían generar el KIT junto con esta cotización.
//
// Sustituye al fichero de ejemplo que llevaba negocios reales pero cajas
// inventadas y un único SKU escrito a mano.
//
// Devuelve un mapa por SKU. Que un SKU no esté, o esté con lista vacía, es una
// respuesta normal: significa que esta oferta es la única abierta con esa
// referencia, y entonces la columna dice "solo esta oferta".

import { useEffect, useState } from "react";

import { fetchNegociosGeneradores } from "../utils/api.utils";

export const useNegociosGeneradores = (skus = [], excluirNegocioId = "") => {
  const [negocios, setNegocios] = useState({});

  // Los SKU se serializan para no relanzar el efecto cada vez que llega un array
  // nuevo con el mismo contenido, que es en cada render de la tabla.
  const clave = (skus || [])
    .map((s) => (s || "").trim())
    .filter(Boolean)
    .sort()
    .join(",");

  useEffect(() => {
    if (!clave) return;
    let cancelado = false;

    (async () => {
      const r = await fetchNegociosGeneradores(clave.split(","), excluirNegocioId);
      if (!cancelado) setNegocios(r || {});
    })();

    return () => {
      cancelado = true;
    };
  }, [clave, excluirNegocioId]);

  return negocios;
};

export default useNegociosGeneradores;
