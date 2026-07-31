"""Cache en memoria de la HubDB `tipo_de_iva_soportado`.

Indexa dos sub-caches independientes (`repercutido` y `soportado`) por
`(code, tipo_residencia)`. El recargo de equivalencia sólo aplica a la
categoría repercutido; en soportado el segundo elemento es siempre 0.0.

La tabla es estática: se carga una vez al startup (`preload_iva_table`)
y vive lo que dure el proceso.
"""

import asyncio
import logging
import threading
from typing import Dict, List, Optional, Tuple

from app.services.hubspot_client import hubspot_client

logger = logging.getLogger(__name__)

HUBDB_TABLE_TIPO_IVA = "tipo_de_iva_soportado"
CATEGORIAS_VALIDAS = ("repercutido", "soportado")
RESIDENCIAS_CONOCIDAS = ("NACIONAL", "EUROPA", "EXTRANJERO")

# Códigos exentos que deben ofrecerse en todos los flujos aunque la tabla HubDB
# no tenga fila para esa residencia (la tabla solo los define para algunas).
# Se sintetizan a 0% de IVA sin recargo; el name de respaldo solo se usa si el
# código no existe en NINGUNA residencia de su categoría.
SIEMPRE_DISPONIBLES = {
    "repercutido": {"EX": "Exportaciones Bebidas"},
    "soportado": {"SEXN": "Sujeto Exento Motivo Exento Art.22 Navieras"},
}


class IvaEntry:
    """Una fila normalizada de la tabla de IVA."""

    __slots__ = ("code", "name", "categoria", "tipo_residencia", "porcentaje_iva", "porcentaje_recargo")

    def __init__(
        self,
        code: str,
        name: str,
        categoria: str,
        tipo_residencia: str,
        porcentaje_iva: float,
        porcentaje_recargo: float,
    ):
        self.code = code
        self.name = name
        self.categoria = categoria
        self.tipo_residencia = tipo_residencia
        self.porcentaje_iva = porcentaje_iva
        self.porcentaje_recargo = porcentaje_recargo


CacheBucket = Dict[Tuple[str, str], IvaEntry]

_iva_cache: Optional[Dict[str, CacheBucket]] = None
_iva_cache_lock = asyncio.Lock()
_iva_cache_sync_lock = threading.Lock()


def _row_value(row, column: str) -> Optional[str]:
    """Lee `row.values[column]` tolerando dict o atributo."""
    values = getattr(row, "values", None) or {}
    raw = values.get(column) if isinstance(values, dict) else None
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw.get("name") or raw.get("label") or raw.get("value")
    return str(raw)


def _to_float(raw) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _ensure_siempre_disponibles(cache: Dict[str, CacheBucket]) -> None:
    """Garantiza los códigos de SIEMPRE_DISPONIBLES en toda residencia conocida.

    Las filas reales de HubDB tienen prioridad; solo se sintetiza la entrada
    (0% IVA, 0% recargo) para las residencias donde falta, reutilizando el name
    de una fila existente del mismo código para que la etiqueta sea idéntica."""
    for categoria, codes in SIEMPRE_DISPONIBLES.items():
        bucket = cache[categoria]
        for code, default_name in codes.items():
            name = next((e.name for e in bucket.values() if e.code == code), default_name)
            for residencia in RESIDENCIAS_CONOCIDAS:
                if (code, residencia) not in bucket:
                    bucket[(code, residencia)] = IvaEntry(
                        code=code,
                        name=name,
                        categoria=categoria,
                        tipo_residencia=residencia,
                        porcentaje_iva=0.0,
                        porcentaje_recargo=0.0,
                    )


def _build_cache_from_rows(rows) -> Tuple[Dict[str, CacheBucket], int]:
    cache: Dict[str, CacheBucket] = {cat: {} for cat in CATEGORIAS_VALIDAS}
    descartadas = 0
    for row in rows or []:
        categoria = (_row_value(row, "categoria") or "").strip().lower()
        if categoria not in CATEGORIAS_VALIDAS:
            descartadas += 1
            continue
        code = _row_value(row, "code")
        tipo_residencia = _row_value(row, "tipo_residencia")
        porcentaje_iva = _to_float(_row_value(row, "porcentaje_iva"))
        if not code or not tipo_residencia or porcentaje_iva is None:
            logger.warning(
                "[hubdb_iva] fila incompleta categoria=%r code=%r residencia=%r iva=%r — saltada",
                categoria, code, tipo_residencia, _row_value(row, "porcentaje_iva"),
            )
            continue
        porcentaje_recargo = _to_float(_row_value(row, "porcentaje_recargo")) or 0.0
        if categoria == "soportado":
            porcentaje_recargo = 0.0
        name = _row_value(row, "name") or code
        entry = IvaEntry(
            code=code.strip().upper(),
            name=name,
            categoria=categoria,
            tipo_residencia=tipo_residencia.strip().upper(),
            porcentaje_iva=porcentaje_iva,
            porcentaje_recargo=porcentaje_recargo,
        )
        cache[categoria][(entry.code, entry.tipo_residencia)] = entry
    _ensure_siempre_disponibles(cache)
    return cache, descartadas


def _fetch_all_rows_sync() -> list:
    client = hubspot_client.get_client()
    rows: list = []
    after = None
    while True:
        kwargs = {"limit": 1000}
        if after:
            kwargs["after"] = after
        page = client.cms.hubdb.rows_api.get_table_rows(
            HUBDB_TABLE_TIPO_IVA, **kwargs,
        )
        rows.extend(page.results or [])
        paging = getattr(page, "paging", None)
        nxt = getattr(paging, "next", None) if paging else None
        if not nxt or not getattr(nxt, "after", None):
            break
        after = nxt.after
    return rows


async def preload_iva_table() -> None:
    """Carga toda la tabla en cache si aún no está. Idempotente y thread-safe."""
    global _iva_cache
    if _iva_cache is not None:
        return
    async with _iva_cache_lock:
        if _iva_cache is not None:
            return
        try:
            rows = await asyncio.to_thread(_fetch_all_rows_sync)
        except Exception as e:
            # Se deja None a propósito: un cache vacío no-None cortocircuitaría
            # los guards y esta instancia nunca reintentaría la carga.
            logger.error(
                "[hubdb_iva] fallo cargando tabla %r: %s",
                HUBDB_TABLE_TIPO_IVA, e, exc_info=True,
            )
            return
        cache, descartadas = _build_cache_from_rows(rows)
        _iva_cache = cache
        logger.info(
            "[hubdb_iva] tabla %r cargada: repercutido=%d, soportado=%d (descartadas=%d)",
            HUBDB_TABLE_TIPO_IVA,
            len(cache["repercutido"]),
            len(cache["soportado"]),
            descartadas,
        )


def _get_porcentajes(
    categoria: str,
    code: Optional[str],
    tipo_residencia: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    global _iva_cache
    if _iva_cache is None:
        with _iva_cache_sync_lock:
            if _iva_cache is None:
                try:
                    rows = _fetch_all_rows_sync()
                    cache, descartadas = _build_cache_from_rows(rows)
                    _iva_cache = cache
                    logger.info(
                        "[hubdb_iva] lazy-load sync tabla %r: repercutido=%d soportado=%d (descartadas=%d)",
                        HUBDB_TABLE_TIPO_IVA,
                        len(cache["repercutido"]),
                        len(cache["soportado"]),
                        descartadas,
                    )
                except Exception as e:
                    # Sin asignar cache vacío: el siguiente uso reintenta la carga.
                    logger.error(
                        "[hubdb_iva] fallo lazy-load sync tabla %r: %s",
                        HUBDB_TABLE_TIPO_IVA, e, exc_info=True,
                    )
    cache = _iva_cache
    if cache is None or not code or not tipo_residencia:
        return (None, None)
    bucket = cache.get(categoria, {})
    entry = bucket.get((code.strip().upper(), tipo_residencia.strip().upper()))
    if entry is None:
        return (None, None)
    return (entry.porcentaje_iva, entry.porcentaje_recargo)


def get_porcentajes_iva_repercutido(
    code: Optional[str],
    tipo_residencia: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    """`(porcentaje_iva, porcentaje_recargo)` o `(None, None)` si no hay match."""
    return _get_porcentajes("repercutido", code, tipo_residencia)


def get_porcentajes_iva_soportado(
    code: Optional[str],
    tipo_residencia: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    """`(porcentaje_iva, 0.0)` o `(None, None)`. El recargo nunca aplica a soportado."""
    return _get_porcentajes("soportado", code, tipo_residencia)


def list_entries(categoria: str, tipo_residencia: Optional[str] = None) -> List[IvaEntry]:
    """Lista entries del cache, opcionalmente filtradas por residencia."""
    if _iva_cache is None:
        return []
    bucket = _iva_cache.get(categoria.strip().lower(), {})
    if tipo_residencia is None:
        return list(bucket.values())
    target = tipo_residencia.strip().upper()
    return [e for e in bucket.values() if e.tipo_residencia == target]


def reset_cache() -> None:
    """Solo para tests."""
    global _iva_cache
    _iva_cache = None
