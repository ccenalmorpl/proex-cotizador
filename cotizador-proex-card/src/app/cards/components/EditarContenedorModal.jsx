import React, { useState, useEffect, useMemo, useRef } from "react";
import {
  Flex,
  Text,
  Button,
  ButtonRow,
  Divider,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  SearchInput,
  NumberInput,
  Alert,
  LoadingSpinner,
  Statistics,
  StatisticsItem,
  Heading,
  ModalBody,
  ModalFooter,
  Box,
  Tag
} from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";
// Pedidos KIT — aislado: la alerta y la línea informativa no tocan la lógica del modal
import { analizarKitsDesdeCajas } from "../utils/kits.utils";
import AlertaKits, { resumenKitsPorSku } from "./AlertaKits";
import PanelKitsDisponibles from "./PanelKitsDisponibles";
import { useKitsDisponibles } from "../hooks/useKitsDisponibles";
import { useKitParams } from "../hooks/useKitParams";
import { conParamsKit } from "../utils/kits.utils";
import { API_CONFIG } from "../config";
import { DEBOUNCE_DELAY_MS } from "../constants/common.constants";

const isOcupacionWarning = (value) => {
  if (value == null || isNaN(value)) return false;
  return value < 90 || value > 103;
};

const formatPercent = (value) => {
  if (value == null || isNaN(value)) return "-";
  return `${Number(value).toFixed(1)}%`;
};

const formatCurrency = (value, decimals = 3) => {
  if (value == null || isNaN(value)) return "-";
  return Number(value).toLocaleString("es-ES", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
};

const CAPACIDAD_FIELD_BY_TIPO = {
  CAMION16P: "unidades_cam16",
  D20DR: "unidades_d20dr",
  D40DR: "unidades_d40dr",
  D20W: "unidades_d20w",
  D40HD: "unidades_d40hd",
  D45HD: "unidades_d45hd",
  D45W: "unidades_d45w",
  PALETS: "unidades_palet",
  R20RF: "unidades_r20rf",
  R40RF: "unidades_r40rf",
  TRAILER80: "unidades_tra80",
  "TRAILER 80": "unidades_tra80",
};

const buildPhysicalEntry = (producto, tipoContenedor) => {
  const capField = CAPACIDAD_FIELD_BY_TIPO[tipoContenedor];
  const capacidad =
    capField && producto[capField] != null
      ? Number(producto[capField]) || 0
      : 0;
  const pesoRaw =
    producto.pesoNeto != null
      ? producto.pesoNeto
      : producto.peso_neto != null
        ? producto.peso_neto
        : 0;
  return {
    pesoNeto: Number(pesoRaw) || 0,
    capacidadMaxima: capacidad,
  };
};

const mapArticulosDelContenedor = (contenedor, containerName) =>
  (contenedor?.container_config?.articulos || []).map((art, idx) => ({
    _uid: art.line_item_id ? String(art.line_item_id) : `init-${idx}`,
    // Promoción = precio EXW guardado a 0 (regalo, no se repercute nada).
    _esPromo: art.price != null && art.price !== "" && Number(art.price) === 0,
    line_item_id: art.line_item_id || null,
    articuloId: art.articuloId,
    articuloName: art.articuloName || `${containerName} - ${art.nombre}`,
    nombre: art.nombre || art.articuloName || "",
    numCajas: Number(art.numCajas) || 0,
    precio_de_coste:
      art.precio_de_coste != null ? Number(art.precio_de_coste) : null,
  }));

const EditarContenedorModal = ({
  actions,
  dealId,
  contenedor,
  contenedoresUnicos = [],
  clienteData,
  articulosSeleccionados = [],
  onClose,
  onUpdated,
  kitsMarcados,
  onCambiarMarcaKit,
  kitsSolicitados,
  onChangeSolicitudKit,
}) => {
  const containerName = contenedor?.container_name || "";
  const containerType = contenedor?.container_type || "";
  const initialOcupacion = contenedor?.container_config?.ocupacion ?? 0;
  const initialPesoNeto = contenedor?.container_config?.pesoNeto ?? 0;

  // En flujo nacional se permite repetir el mismo artículo en un contenedor
  // (promociones tipo 5+1). La identidad de cada fila es _uid, no articuloId.
  const tipoClienteValue =
    clienteData?.tipoCliente?.value ?? clienteData?.tipoCliente ?? "";
  const esNacional = String(tipoClienteValue).toLowerCase() === "nacional";

  const uidRef = useRef(0);
  const nextUid = () => `new-${++uidRef.current}`;

  const [articulos, setArticulos] = useState(() =>
    mapArticulosDelContenedor(contenedor, containerName),
  );

  const [searchValue, setSearchValue] = useState("");
  const [productosFiltrados, setProductosFiltrados] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const debounceRef = useRef(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const enviandoRef = useRef(false);

  // El modal vive en el overlay del botón de NegociacionTab, que no siempre se
  // remonta al cambiar de contenedor en el Select. Sin esto, `articulos` se
  // quedaría con las filas del contenedor anterior y sus line_item_id: el backend
  // no reconocería esos ids, los trataría como artículos nuevos y RECREARÍA las
  // líneas del contenedor destino (incidencia 2026-08-28).
  const contenedorIdRef = useRef(contenedor?.line_item_id);
  useEffect(() => {
    if (contenedorIdRef.current === contenedor?.line_item_id) return;
    contenedorIdRef.current = contenedor?.line_item_id;
    uidRef.current = 0;
    setArticulos(mapArticulosDelContenedor(contenedor, containerName));
    setSearchValue("");
    setProductosFiltrados([]);
    setSubmitError(null);
  }, [contenedor, containerName]);

  const [datosFisicos, setDatosFisicos] = useState(() => {
    const map = {};
    (articulosSeleccionados || []).forEach((art) => {
      if (art && art.id != null) {
        map[String(art.id)] = buildPhysicalEntry(art, contenedor?.container_type || "");
      }
    });
    return map;
  });

  // En nacional no excluimos del buscador los productos ya presentes,
  // para poder añadir el mismo artículo otra vez (línea promocional).
  const articuloIdsActuales = useMemo(
    () =>
      esNacional
        ? new Set()
        : new Set(articulos.map((a) => String(a.articuloId))),
    [articulos, esNacional],
  );

  // articuloIds que ya tienen una línea promocional en este contenedor:
  // se usa para bloquear el botón de promoción y no duplicar la promo del mismo artículo.
  const promoArticuloIds = useMemo(
    () =>
      new Set(
        articulos.filter((a) => a._esPromo).map((a) => String(a.articuloId)),
      ),
    [articulos],
  );

  useEffect(() => {
    setDatosFisicos((prev) => {
      const next = { ...prev };
      (articulosSeleccionados || []).forEach((art) => {
        if (art && art.id != null) {
          next[String(art.id)] = buildPhysicalEntry(art, containerType);
        }
      });
      return next;
    });
  }, [articulosSeleccionados, containerType]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (!searchValue || !searchValue.trim()) {
      setProductosFiltrados([]);
      setSearchError(null);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      setSearchError(null);
      try {
        const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.articles}/product/list?query=${encodeURIComponent(searchValue)}&limit=50`;
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` },
        });
        if (!response.ok) {
          throw new Error(`Error ${response.status}`);
        }
        const data = await response.json();
        if (!data.success) {
          throw new Error(data.error || "Error al buscar productos");
        }
        const filtrados = (data.products || []).filter(
          (p) => p.activo === "Activo" && !articuloIdsActuales.has(String(p.id)),
        );
        setProductosFiltrados(filtrados);
      } catch (err) {
        setSearchError(err.message || "Error al buscar productos");
        setProductosFiltrados([]);
      } finally {
        setSearchLoading(false);
      }
    }, DEBOUNCE_DELAY_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchValue, articuloIdsActuales]);

  const totalCajasContenedor = useMemo(
    () => articulos.reduce((sum, a) => sum + (Number(a.numCajas) || 0), 0),
    [articulos],
  );

  const pesoNetoCalculado = useMemo(() => {
    return articulos.reduce((sum, a) => {
      const fisico = datosFisicos[String(a.articuloId)];
      const peso = fisico?.pesoNeto || 0;
      return sum + peso * (Number(a.numCajas) || 0);
    }, 0);
  }, [articulos, datosFisicos]);

  const ocupacionCalculada = useMemo(() => {
    if (!containerType) return null;
    let total = 0;
    let conDatos = false;
    for (const a of articulos) {
      const fisico = datosFisicos[String(a.articuloId)];
      if (!fisico || !fisico.capacidadMaxima) continue;
      conDatos = true;
      total += ((Number(a.numCajas) || 0) / fisico.capacidadMaxima) * 100;
    }
    return conDatos ? total : null;
  }, [articulos, datosFisicos, containerType]);

  const ocupacionParaMostrar =
    ocupacionCalculada != null ? ocupacionCalculada : Number(initialOcupacion) || 0;
  const pesoNetoParaMostrar =
    pesoNetoCalculado > 0 ? pesoNetoCalculado : Number(initialPesoNeto) || 0;

  const totalContenedoresGlobal = useMemo(
    () => (Array.isArray(contenedoresUnicos) ? contenedoresUnicos.length : 0),
    [contenedoresUnicos],
  );

  const totalCajasGlobal = useMemo(() => {
    if (!Array.isArray(contenedoresUnicos)) return totalCajasContenedor;
    let cajasOtros = 0;
    contenedoresUnicos.forEach((c) => {
      if (c?.line_item_id === contenedor?.line_item_id) return;
      (c?.container_config?.articulos || []).forEach((art) => {
        cajasOtros += Number(art.numCajas) || 0;
      });
    });
    return cajasOtros + totalCajasContenedor;
  }, [contenedoresUnicos, contenedor, totalCajasContenedor]);

  // Pedidos KIT — cajas por ARTÍCULO en todo el pedido, con el borrador en edición
  // incluido, para que la alerta se recalcule al teclear. Mismo criterio que
  // `totalCajasGlobal` (los demás contenedores + este), pero desglosado por
  // artículo: el bloque de fabricación es por producto y sobre el pedido entero.
  const skusEnCotizacion = useMemo(
    () => (articulosSeleccionados || []).map((a) => a.hs_sku || ""),
    [articulosSeleccionados]
  );

  // KIT reales de HubSpot, los mismos que ven Contenedores y Vista previa. Con el
  // fixture, este modal daba otro pedido KIT para el mismo pedido.
  const { kits: kitsDisponibles } = useKitsDisponibles(skusEnCotizacion);
  const paramsKit = useKitParams(skusEnCotizacion);

  const analisisKits = useMemo(() => {
    const porArticulo = new Map();
    const acumular = (articuloId, cajas) => {
      const n = Number(cajas) || 0;
      if (!articuloId || n <= 0) return;
      porArticulo.set(articuloId, (porArticulo.get(articuloId) || 0) + n);
    };

    (Array.isArray(contenedoresUnicos) ? contenedoresUnicos : []).forEach((c) => {
      if (c?.line_item_id === contenedor?.line_item_id) return;
      (c?.container_config?.articulos || []).forEach((art) =>
        acumular(art.articuloId, art.numCajas)
      );
    });
    articulos.forEach((art) => acumular(art.articuloId, art.numCajas));

    const catalogo = (articulosSeleccionados || []).map((a) => ({
      id: a.id,
      nombre: a.nombre,
      sku: a.hs_sku || "",
      tamano_kit: a.tamano_kit,
      bloque_multiplo: a.bloque_multiplo,
    }));

    return analizarKitsDesdeCajas(
      porArticulo,
      conParamsKit(catalogo, paramsKit),
      resumenKitsPorSku(kitsDisponibles, kitsMarcados, {}, dealId)
    );
  }, [contenedoresUnicos, contenedor, articulos, articulosSeleccionados, kitsMarcados, kitsDisponibles, dealId, paramsKit]);


  const comisionPorCaja = useMemo(() => {
    if (
      !clienteData?.codigoIntermediario ||
      !clienteData?.tipoComision ||
      !clienteData?.importeComision
    ) {
      return 0;
    }
    const importeComision =
      clienteData.importeComision?.value ?? clienteData.importeComision;
    const tipoComision =
      clienteData.tipoComision?.value ?? clienteData.tipoComision;
    const importe = Number(importeComision) || 0;

    if (tipoComision === "importe_por_negocio") {
      return totalCajasGlobal > 0 ? importe / totalCajasGlobal : 0;
    }
    if (tipoComision === "importe_por_contenedor") {
      return totalCajasGlobal > 0
        ? (importe * totalContenedoresGlobal) / totalCajasGlobal
        : 0;
    }
    if (tipoComision === "importe_por_cajas") {
      return importe;
    }
    return 0;
  }, [clienteData, totalCajasGlobal, totalContenedoresGlobal]);

  const handleAddArticulo = (producto, esPromo = false) => {
    const articuloId = String(producto.id);
    const costeRaw =
      producto.hs_price_eur != null && producto.hs_price_eur !== ""
        ? producto.hs_price_eur
        : producto.price != null && producto.price !== ""
          ? producto.price
          : null;
    const nuevo = {
      _uid: nextUid(),
      _esPromo: esPromo,
      line_item_id: null,
      articuloId,
      articuloName: `${containerName} - ${producto.name}`,
      nombre: producto.name,
      numCajas: 0,
      precio_de_coste: costeRaw != null ? Number(costeRaw) : null,
    };
    setArticulos((prev) => [...prev, nuevo]);
    setDatosFisicos((prev) => ({
      ...prev,
      [articuloId]: buildPhysicalEntry(producto, containerType),
    }));
    if (!esNacional) {
      setProductosFiltrados((prev) =>
        prev.filter((p) => String(p.id) !== articuloId),
      );
    }
  };

  // Añade una línea promocional (regalo) del mismo artículo: se guardará a precio 0.
  const handleAddPromo = (base) => {
    setArticulos((prev) => [
      ...prev,
      {
        _uid: nextUid(),
        _esPromo: true,
        line_item_id: null,
        articuloId: base.articuloId,
        articuloName: base.articuloName,
        nombre: base.nombre,
        numCajas: 0,
        precio_de_coste: base.precio_de_coste,
      },
    ]);
  };

  const handleRemoveArticulo = (uid) => {
    setArticulos((prev) => prev.filter((a) => a._uid !== uid));
  };

  const handleNumCajasChange = (uid, value) => {
    const safe = Math.max(0, Number(value) || 0);
    setArticulos((prev) =>
      prev.map((a) => (a._uid === uid ? { ...a, numCajas: safe } : a)),
    );
  };

  const isValid = useMemo(() => {
    if (articulos.length === 0) return false;
    return articulos.every((a) => Number(a.numCajas) > 0);
  }, [articulos]);

  const handleSubmit = async () => {
    if (!isValid) {
      setSubmitError(
        "Cada artículo debe tener un número de cajas mayor que 0. Elimine los que no quiera incluir.",
      );
      return;
    }
    // `submitting` no sirve de guard: setState no es síncrono, así que dos
    // ejecuciones del handler en el mismo tick pasarían las dos. El ref sí corta.
    if (enviandoRef.current) return;
    enviandoRef.current = true;
    setSubmitting(true);
    setSubmitError(null);

    try {
      const payload = {
        // Fallback para que el backend resuelva el contenedor por numero_container
        // si el line_item_id de la URL quedó obsoleto (stale).
        numero_container: containerName || null,
        container_config: {
          articulos: articulos.map((a) => ({
            line_item_id: a.line_item_id || null,
            articuloId: a.articuloId,
            articuloName: a.articuloName,
            nombre: a.nombre,
            numCajas: Number(a.numCajas) || 0,
            // Línea promocional: se persiste a precio 0 (regalo, EXW 0 / margen -100%).
            ...(a._esPromo ? { price: 0, margen_unitario: -1 } : {}),
          })),
          ocupacion: Number(ocupacionParaMostrar) || 0,
          pesoNeto: Number(pesoNetoParaMostrar) || 0,
        },
      };

      const response = await fetchFromAPI(
        `/api/v1/deals/${dealId}/containers/${contenedor.line_item_id}`,
        {
          method: "PATCH",
          body: payload,
        },
      );

      if (response.container && typeof onUpdated === "function") {
        onUpdated(response.container);
      }
      onClose();
    } catch (err) {
      // El backend serializa las ediciones por negocio: si otra petición tiene el
      // lock, esta no ha hecho nada y la otra sí está guardando. Cerrar en vez de
      // mostrar un error rojo por un guardado que sí se está aplicando.
      if (err?.code === "edit_in_progress") {
        onClose();
        return;
      }
      setSubmitError(err.message || "Error al guardar los cambios.");
    } finally {
      enviandoRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <>
      <ModalBody>
        <Flex direction="column" gap="small">
          <Text>
            Añade, elimina o ajusta el número de cajas de los artículos del
            contenedor. Los precios e IVA se editan en la pestaña de
            Negociación.
          </Text>

          <Statistics>
            <StatisticsItem label="Total cajas" number={totalCajasContenedor} />
            <StatisticsItem
              label="Ocupación"
              number={formatPercent(ocupacionParaMostrar)}
            />
            <StatisticsItem
              label="Peso neto (kg)"
              number={Number(pesoNetoParaMostrar || 0).toFixed(2)}
            />
          </Statistics>

          {isOcupacionWarning(ocupacionParaMostrar) && (
            <Alert title="Ocupación fuera de rango" variant="warning">
              La ocupación está fuera del rango operativo (90–103%). Puedes
              guardar igualmente los cambios en el contenedor.
            </Alert>
          )}

          {/* Pedidos KIT: se recalcula al teclear, sobre el total del pedido.
              El panel de marcado va aquí a petición de Cristóbal: para marcar en
              una cotización ya creada, este camino es más seguro que reabrir la
              pestaña Contenedores, que rehace contenedores y line items. Marcar
              sigue siendo de la COTIZACIÓN, no de este contenedor: el estado lo
              gobierna Cotizador.tsx y es el mismo en las tres pantallas. */}
          <AlertaKits
            analisis={analisisKits}
            solicitudes={kitsSolicitados}
            onChangeSolicitud={onChangeSolicitudKit}
            actions={actions}
            negocioId={dealId}
          />
          <PanelKitsDisponibles
            skusEnCotizacion={skusEnCotizacion}
            kits={kitsDisponibles}
            kitsMarcados={kitsMarcados}
            onCambiarMarcaKit={onCambiarMarcaKit}
            negocioId={dealId}
          />

          <SearchInput
            name="buscar-articulo-modal"
            label="Buscar artículo"
            placeholder=""
            value={searchValue}
            onChange={(value) => setSearchValue(value)}
            description="Buscar productos para añadir al contenedor"
          />

          {searchError && (
            <Alert title="Error de búsqueda" variant="error">
              {searchError}
            </Alert>
          )}

          {searchLoading && (
            <Flex direction="row" align="center" gap="small">
              <LoadingSpinner size="xs" />
              <Text>Buscando productos...</Text>
            </Flex>
          )}

          {!searchLoading &&
            searchValue &&
            productosFiltrados.length === 0 &&
            !searchError && (
              <Text>
                No se encontraron productos disponibles para "{searchValue}".
              </Text>
            )}

          {searchValue.trim() && productosFiltrados.length > 0 && (
            <Table bordered={true}>
              <TableHead>
                <TableRow>
                  <TableHeader>Producto</TableHeader>
                  <TableHeader>Código proveedor</TableHeader>
                  <TableHeader align="right">Peso neto (kg)</TableHeader>
                  <TableHeader align="right">Coste/Caja</TableHeader>
                  <TableHeader width="min">Acción</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {productosFiltrados.map((p) => {
                  const coste =
                    p.hs_price_eur != null && p.hs_price_eur !== ""
                      ? Number(p.hs_price_eur)
                      : p.price != null && p.price !== ""
                        ? Number(p.price)
                        : null;
                  return (
                    <TableRow key={p.id}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell>{p.codigo_proveedor || p.cardcode || "-"}</TableCell>
                      <TableCell align="right">
                        {p.peso_neto ? Number(p.peso_neto).toFixed(2) : "0.00"}
                      </TableCell>
                      <TableCell align="right">
                        {coste != null ? formatCurrency(coste, 3) : "-"}
                      </TableCell>
                      <TableCell width="min">
                        <Flex direction="column" gap="extra-small">
                          <Button
                            size="xs"
                            variant="secondary"
                            onClick={() => handleAddArticulo(p)}
                          >
                            Añadir
                          </Button>
                          {esNacional && (
                            <Button
                              size="xs"
                              variant="secondary"
                              onClick={() => handleAddArticulo(p, true)}
                              disabled={promoArticuloIds.has(String(p.id))}
                            >
                              Promoción
                            </Button>
                          )}
                        </Flex>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}

          <Divider />

          <Heading level={3}>Artículos del contenedor ({articulos.length})</Heading>

          {articulos.length === 0 ? (
            <Alert title="Sin artículos" variant="warning">
              Añade al menos un artículo desde el buscador antes de guardar.
            </Alert>
          ) : (
            <Table bordered={true}>
              <TableHead>
                <TableRow>
                  <TableHeader width={300}>Artículo</TableHeader>
                  <TableHeader align="center" width={150}>Cajas</TableHeader>
                  <TableHeader align="center" width={150}>Info por caja</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {articulos.map((a) => {
                  const fisico = datosFisicos[String(a.articuloId)];
                  return (
                  <TableRow key={a._uid}>
                    <TableCell width={300}>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontWeight: "demibold" }}>{a.nombre}</Text>
                        {a._esPromo && <Tag variant="success">Promoción</Tag>}
                        {!a.line_item_id && !a._esPromo && (
                          <Text format={{ fontSize: "small" }}>(nuevo)</Text>
                        )}
                        {esNacional && !a._esPromo && (
                          <Box>
                            <Button
                              size="xs"
                              variant="secondary"
                              onClick={() => handleAddPromo(a)}
                              disabled={
                                submitting ||
                                promoArticuloIds.has(String(a.articuloId))
                              }
                            >
                              + Promoción
                            </Button>
                          </Box>
                        )}
                        <Box>
                          <Button
                            size="xs"
                            variant="destructive"
                            onClick={() => handleRemoveArticulo(a._uid)}
                            disabled={submitting}
                          >
                            Eliminar
                          </Button>
                        </Box>
                      </Flex>
                    </TableCell>
                    <TableCell align="center" width={150}>
                      <NumberInput
                        name={`cajas-${a._uid}`}
                        value={a.numCajas}
                        onChange={(value) =>
                          handleNumCajasChange(a._uid, value)
                        }
                        min={0}
                        precision={0}
                      />
                    </TableCell>
                    <TableCell align="center" width={150}>
                      <Flex direction="column" gap="extra-small">
                        <Text format={{ fontSize: "small" }}>
                          Peso neto:{" "}
                          {fisico?.pesoNeto
                            ? `${Number(fisico.pesoNeto).toFixed(2)} kg`
                            : "-"}
                        </Text>
                        <Text format={{ fontSize: "small" }}>
                          Coste:{" "}
                          {a.precio_de_coste != null
                            ? formatCurrency(a.precio_de_coste, 3)
                            : "-"}
                        </Text>
                        <Text format={{ fontSize: "small" }}>
                          Comisión:{" "}
                          {comisionPorCaja > 0
                            ? formatCurrency(comisionPorCaja, 3)
                            : "-"}
                        </Text>
                      </Flex>
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}

          {submitError && (
            <Alert title="Error al guardar" variant="error">
              {submitError}
            </Alert>
          )}
        </Flex>
      </ModalBody>

      <ModalFooter>
        <ButtonRow>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={submitting || !isValid}
            loading={submitting}
          >
            {submitting ? "Guardando..." : "Guardar cambios"}
          </Button>
        </ButtonRow>
      </ModalFooter>
    </>
  );
};

export default EditarContenedorModal;
