// Las props date de HubSpot llegan como medianoche UTC. Leerlas con los getters
// locales en un navegador al oeste de UTC da el día anterior, y como los tabs
// guardan con Date.UTC cada guardado restaba un día a la fecha.
export function timestampUTCaDateInput(ts) {
  const esMilisegundos = typeof ts === "number" || /^\d+$/.test(String(ts));
  const fecha = new Date(esMilisegundos ? Number(ts) : ts);
  if (Number.isNaN(fecha.getTime())) return null;
  return {
    year: fecha.getUTCFullYear(),
    month: fecha.getUTCMonth(),
    date: fecha.getUTCDate(),
  };
}
