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
  Box
} from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";
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

const EditarContenedorModal = ({
  dealId,
  contenedor,
  contenedoresUnicos = [],
  clienteData,
  articulosSeleccionados = [],
  onClose,
  onUpdated,
}) => {
  const containerName = contenedor?.container_name || "";
  const containerType = contenedor?.container_type || "";
  const initialOcupacion = contenedor?.container_config?.ocupacion ?? 0;
  const initialPesoNeto = contenedor?.container_config?.pesoNeto ?? 0;

  const [articulos, setArticulos] = useState(() =>
    (contenedor?.container_config?.articulos || []).map((art) => ({
      line_item_id: art.line_item_id || null,
      articuloId: art.articuloId,
      articuloName: art.articuloName || `${containerName} - ${art.nombre}`,
      nombre: art.nombre || art.articuloName || "",
      numCajas: Number(art.numCajas) || 0,
      precio_de_coste:
        art.precio_de_coste != null ? Number(art.precio_de_coste) : null,
    })),
  );

  const [searchValue, setSearchValue] = useState("");
  const [productosFiltrados, setProductosFiltrados] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const debounceRef = useRef(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const [datosFisicos, setDatosFisicos] = useState(() => {
    const map = {};
    (articulosSeleccionados || []).forEach((art) => {
      if (art && art.id != null) {
        map[String(art.id)] = buildPhysicalEntry(art, contenedor?.container_type || "");
      }
    });
    return map;
  });

  const articuloIdsActuales = useMemo(
    () => new Set(articulos.map((a) => String(a.articuloId))),
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
        });
        if (!response.ok) {
          throw new Error(`Error ${response.status}`);
        }
        const data = await response.json();
        if (!data.success) {
          throw new Error(data.error || "Error al buscar productos");
        }
        const filtrados = (data.products || []).filter(
          (p) => !articuloIdsActuales.has(String(p.id)),
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

  const handleAddArticulo = (producto) => {
    const articuloId = String(producto.id);
    const costeRaw =
      producto.hs_price_eur != null && producto.hs_price_eur !== ""
        ? producto.hs_price_eur
        : producto.price != null && producto.price !== ""
          ? producto.price
          : null;
    const nuevo = {
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
    setProductosFiltrados((prev) =>
      prev.filter((p) => String(p.id) !== articuloId),
    );
  };

  const handleRemoveArticulo = (articuloId) => {
    setArticulos((prev) =>
      prev.filter((a) => String(a.articuloId) !== String(articuloId)),
    );
  };

  const handleNumCajasChange = (articuloId, value) => {
    const safe = Math.max(0, Number(value) || 0);
    setArticulos((prev) =>
      prev.map((a) =>
        String(a.articuloId) === String(articuloId)
          ? { ...a, numCajas: safe }
          : a,
      ),
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
    setSubmitting(true);
    setSubmitError(null);

    try {
      const payload = {
        container_config: {
          articulos: articulos.map((a) => ({
            line_item_id: a.line_item_id || null,
            articuloId: a.articuloId,
            articuloName: a.articuloName,
            nombre: a.nombre,
            numCajas: Number(a.numCajas) || 0,
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
      setSubmitError(err.message || "Error al guardar los cambios.");
    } finally {
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
                        <Button
                          size="xs"
                          variant="secondary"
                          onClick={() => handleAddArticulo(p)}
                        >
                          Añadir
                        </Button>
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
                  <TableRow key={a.articuloId}>
                    <TableCell width={300}>
                      <Flex direction="column" gap="extra-small">
                        
                        <Text format={{ fontWeight: "demibold" }}>{a.nombre}</Text>
                        {!a.line_item_id && (
                          <Text format={{ fontSize: "small" }}>(nuevo)</Text>
                        )}
                        <Box>
                          <Button
                            size="xs"
                            variant="destructive"
                            onClick={() => handleRemoveArticulo(a.articuloId)}
                            disabled={submitting}
                          >
                            Eliminar
                          </Button>
                        </Box>
                      </Flex>
                    </TableCell>
                    <TableCell align="center" width={150}>
                      <NumberInput
                        name={`cajas-${a.articuloId}`}
                        value={a.numCajas}
                        onChange={(value) =>
                          handleNumCajasChange(a.articuloId, value)
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
