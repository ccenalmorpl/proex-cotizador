import React, { useState, useEffect, useRef } from "react";
import {
  Box,
  Heading,
  Flex,
  SearchInput,
  Button,
  ButtonRow,
  Table,
  TableHead,
  TableRow,
  TableHeader,
  TableBody,
  TableCell,
  Divider,
  Text,
  EmptyState,
  LoadingSpinner,
  Alert,
  Select,
  Modal,
  ModalBody,
  ModalFooter,
} from "@hubspot/ui-extensions";
import { hubspot } from "@hubspot/ui-extensions";
import { API_CONFIG } from "../config";

const ArticulosTab = ({
  context,
  onNavigate,
  articulosSeleccionados,
  setArticulosSeleccionados,
  tipoContenedorOptions,
  tipoContenedorSeleccionado,
  clienteData,
  setClienteData,
  actions,
  contenedoresData, // Estado global de contenedores desde ContenedoresTab
  setContenedoresData, // Para limpiar contenedores cuando se actualizan artículos
  setContenedoresUnicos, // Para limpiar contenedores únicos cuando se actualizan artículos
}) => {
  const [searchValue, setSearchValue] = useState("");
  const [productosFiltrados, setProductosFiltrados] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingArticulos, setLoadingArticulos] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [tipoContenedorVista, setTipoContenedorVista] = useState(
    tipoContenedorSeleccionado || ""
  );
  const debounceTimeout = useRef(null);

  // Estados para proveedores (sección separada)
  const [proveedoresSeleccionados, setProveedoresSeleccionados] = useState([]);
  const [proveedorSearchValue, setProveedorSearchValue] = useState("");
  const [proveedoresFiltrados, setProveedoresFiltrados] = useState([]);
  const [proveedorLoading, setProveedorLoading] = useState(false);
  const proveedorDebounceTimeout = useRef(null);
  const autoAddProviderEnabledRef = useRef(false);

  // Estados para localidad de carga
  const [lugarCarga, setLugarCarga] = useState("");
  const [lugarCargaOptions, setLugarCargaOptions] = useState([]);
  const [lugarCargaDisabled, setLugarCargaDisabled] = useState(false);

  // Verificar si hay contenedores configurados
  const tieneContenedores = contenedoresData?.contenedoresDefinidos?.length > 0;

  // Actualizar el tipo de contenedor de vista cuando cambie el seleccionado en ClienteTab
  useEffect(() => {
    if (tipoContenedorSeleccionado) {
      setTipoContenedorVista(tipoContenedorSeleccionado);
    }
  }, [tipoContenedorSeleccionado]);

  // Sincronizar lugar_carga local con clienteData global
  useEffect(() => {
    // Only update if we have a valid local lugarCarga value and options are loaded
    if (lugarCarga && lugarCargaOptions.length > 0) {
      // Find the label for the current value
      const option = lugarCargaOptions.find((opt) => opt.value === lugarCarga);
      const label = option ? option.label : lugarCarga;

      // Update global state ONLY if our local value is different
      // This prevents overwriting values set by other tabs
      if (clienteData.lugarCarga !== lugarCarga) {
        setClienteData({
          ...clienteData,
          lugarCarga: lugarCarga,
          lugarCargaLabel: label,
        });
      }
    } else if (
      !lugarCarga &&
      lugarCargaOptions.length > 0 &&
      clienteData.lugarCarga
    ) {
      // If we don't have a local value but clienteData has one, sync it to local state
      // This prevents losing the value when the component re-renders
      setLugarCarga(clienteData.lugarCarga);
    }
  }, [lugarCarga, lugarCargaOptions, clienteData.lugarCarga]);

  // Actualizar lugar_carga dinámicamente según proveedores seleccionados
  useEffect(() => {
    // Solo aplicar lógica después de la carga inicial
    if (!autoAddProviderEnabledRef.current || lugarCargaOptions.length === 0) {
      return;
    }

    const numProveedores = proveedoresSeleccionados.length;

    if (numProveedores === 0) {
      // Sin proveedores: limpiar y deshabilitar
      setLugarCarga("");
      setLugarCargaDisabled(true);
    } else if (numProveedores === 1) {
      // Un solo proveedor: usar su localidad de carga y habilitar selector
      const proveedor = proveedoresSeleccionados[0];
      const providerLugarCarga = proveedor.lugarCarga;

      // Buscar la opción que coincida con el label del proveedor
      const matchingOption = lugarCargaOptions.find(
        (opt) => opt.label === providerLugarCarga
      );

      if (matchingOption) {
        setLugarCarga(matchingOption.value);
      } else {
        // Si no encuentra coincidencia, usar almacen_proex como fallback
        setLugarCarga("almacen_proex");
      }

      setLugarCargaDisabled(false);
    } else {
      // Múltiples proveedores: forzar "Almacen PROEX" y deshabilitar
      setLugarCarga("almacen_proex");
      setLugarCargaDisabled(true);
    }
  }, [proveedoresSeleccionados, lugarCargaOptions]);

  // Cargar artículos asociados al deal al montar el componente
  useEffect(() => {
    const cargarArticulosIniciales = async () => {
      const dealId = context?.crm?.objectId;
      console.log("[ArticulosTab] cargarArticulosIniciales: dealId =", dealId);
      if (!dealId) {
        console.log("[ArticulosTab] Abortando: no hay dealId");
        return;
      }

      setLoadingArticulos(true);
      setError(null);
      autoAddProviderEnabledRef.current = false;

      try {
        const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/articles`;
        console.log("[ArticulosTab] GET articles URL:", url);
        const response = await hubspot.fetch(url, {
          timeout: API_CONFIG.timeout,
          headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
        });

        console.log("[ArticulosTab] Response articles: ok =", response.ok, "status =", response.status);

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          console.log("[ArticulosTab] Error response body:", errorData);

          // Manejar errores del backend Lambda con formato HubSpot
          if (errorData.responseBody?.detail) {
            throw new Error(`Error del servidor: ${errorData.responseBody.detail}`);
          }

          throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        console.log("[ArticulosTab] Data recibida: success =", data?.success, "articles length =", data?.articles?.length, "providers length =", data?.providers?.length);

        if (!data.success) {
          throw new Error(
            data.error || "Error al cargar los artículos asociados"
          );
        }

        // Since articles may now be individual line items per container,
        // we need to deduplicate them for display in ArticulosTab
        const articulosMap = new Map();

        (data.articles || []).forEach((art) => {
          // Usar HubSpot product id como clave (siempre único).
          // Antes se usaba hs_sku, que puede ser null y colapsaba todos los productos
          // sin SKU en una única entrada del Map.
          const productId = art.id;

          // Si no hemos visto este producto aún, añadirlo
          if (!articulosMap.has(productId)) {
            articulosMap.set(productId, {
              id: productId,
              hs_sku: art.hs_sku || "", // SKU separado para el payload al guardar
              codigoProveedor: art.codigo_proveedor || "",
              proveedor: art.codigo_proveedor || "", // Para consistencia con search results
              nombre: art.name || "",
              name_es: art.name_es || "",
              name_en: art.name_en || "",
              name_fr: art.name_fr || "",
              description: art.description || "",
              pesoNeto: art.peso_neto || 0,
              cajas20: 0,
              cajas40: 0,
              cajasTrailer80: 0,
              // Container capacity fields from backend
              unidades_d20dr: art.unidades_d20dr || 0,
              unidades_d40dr: art.unidades_d40dr || 0,
              unidades_tra80: art.unidades_tra80 || 0,
              hs_price_eur: art.hs_price_eur || 0,
              margen_unitario: art.margen_unitario || null, // Puede ser null si no está definido
              tipo_iva_repercutido: art.tipo_iva_repercutido || null, // Tipo de IVA repercutido
            });
          }
          // If we've seen it before, skip it (already have the unique product)
        });

        const articulosTransformados = Array.from(articulosMap.values());
        console.log("[ArticulosTab] Artículos transformados (únicos por SKU):", articulosTransformados.length, "items:", articulosTransformados);
        setArticulosSeleccionados(articulosTransformados);
        const proveedoresIniciales = (data.providers || []).map((prov) => ({
          id: prov.id || prov.cardcode,
          cardcode: prov.cardcode,
          nombre: prov.name || prov.cardcode,
          lugarCarga: prov.lugar_de_la_carga_envio || "",
        }));
        setProveedoresSeleccionados(proveedoresIniciales);

        if (data.lugar_carga_options) {
          setLugarCargaOptions(data.lugar_carga_options);
        }

        // Determine default value for lugar_carga
        let defaultLugarCarga = data.lugar_carga || "";

        // If no value is set in the deal, apply default logic
        if (!defaultLugarCarga && proveedoresIniciales.length > 0) {
          if (proveedoresIniciales.length === 1) {
            // Single provider: use provider's localidad de carga
            const providerLugarCarga = proveedoresIniciales[0].lugarCarga;
            // Find the matching option value (we have the label, need the value)
            const matchingOption = (data.lugar_carga_options || []).find(
              (opt) => opt.label === providerLugarCarga
            );
            defaultLugarCarga = matchingOption
              ? matchingOption.value
              : "almacen_proex";
          } else {
            // Multiple providers: default to "Almacen PROEX"
            defaultLugarCarga = "almacen_proex";
          }
        }

        setLugarCarga(defaultLugarCarga);
      } catch (err) {
        console.error("Error cargando artículos iniciales:", err);
        setError(
          err.message || "Error al cargar los artículos asociados al negocio"
        );
      } finally {
        setLoadingArticulos(false);
        autoAddProviderEnabledRef.current = true;
      }
    };

    cargarArticulosIniciales();
  }, [context]);

  // Función para obtener el máximo de cajas según el tipo de contenedor
  const getMaxCajas = (articulo, tipoContenedor) => {
    // Mapeo de tipos de contenedor a sus campos de unidades correspondientes
    const containerFieldMap = {
      D20DR: "unidades_d20dr",
      D40DR: "unidades_d40dr",
      TRAILER80: "unidades_tra80",
      "TRAILER 80": "unidades_tra80",
    };
        
    const field = containerFieldMap[tipoContenedor];
    if (field && articulo[field] !== undefined) {
      const value = articulo[field];
      // Handle both direct values and {value, options} objects
      if (typeof value === 'object' && value !== null && value.value !== undefined) {
        return Number(value.value) || 0;
      }
      return Number(value) || 0;
    }

    // Fallback: return 0 if no match found
    return 0;
  };

  // Función para buscar productos en el API
  const buscarProductos = async (query) => {
    if (!query || query.trim().length === 0) {
      setProductosFiltrados([]);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const url = `${API_CONFIG.baseURL}${
        API_CONFIG.endpoints.articles
      }/product/list?query=${encodeURIComponent(query)}&limit=100`;
      const response = await hubspot.fetch(url, {
        timeout: API_CONFIG.timeout,
        headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
      });

      if (!response.ok) {
        throw new Error(`Error ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();

      if (!data.success) {
        throw new Error(data.error || "Error al buscar productos");
      }
      
      // Mapear la respuesta del API a la estructura esperada y filtrar ya seleccionados
      const productosFormateados = data.products
        .filter((producto) => producto.activo === "Activo")
        .filter(
          // Usar HubSpot product id (siempre único) para excluir ya seleccionados.
          // Antes se usaba hs_sku, lo que causaba que null === null filtrara todo.
          (producto) =>
            !articulosSeleccionados.find((art) => art.id === producto.id)
        )
        .map((producto) => ({
          id: producto.id,        // HubSpot product ID (siempre único)
          hs_sku: producto.hs_sku || "", // SKU separado para el payload al guardar
          nombre: producto.name,
          name_es: producto.name_es || "",
          name_en: producto.name_en || "",
          name_fr: producto.name_fr || "",
          codigoProveedor: producto.codigo_proveedor || "",
          pesoNeto: producto.peso_neto || 0,
          cajas20: 0,
          cajas40: 0,
          proveedor: producto.cardcode || "",
          lugarCarga: "",
          puertoSalida: "",
          // Guardamos datos adicionales del producto
          description: producto.description,
          barcode: producto.barcode,
          // Container capacity fields
          unidades_d20dr: producto.unidades_d20dr || 0,
          unidades_d40dr: producto.unidades_d40dr || 0,
          unidades_tra80: producto.unidades_tra80 || 0,
        }));
      console.log("[buscarProductos] Cardcodes recibidos:", productosFormateados);

      setProductosFiltrados(productosFormateados);
    } catch (err) {
      console.error("Error al buscar productos:", err);
      setError(err.message || "Error al buscar productos");
      setProductosFiltrados([]);
    } finally {
      setLoading(false);
    }
  };

  // Efecto para el debounce de 500ms
  useEffect(() => {
    // Limpiar timeout anterior
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current);
    }

    // Si no hay valor de búsqueda, limpiar resultados
    if (!searchValue || searchValue.trim().length === 0) {
      setProductosFiltrados([]);
      setError(null);
      return;
    }

    // Configurar nuevo timeout de 500ms
    debounceTimeout.current = setTimeout(() => {
      buscarProductos(searchValue);
    }, 500);

    // Cleanup
    return () => {
      if (debounceTimeout.current) {
        clearTimeout(debounceTimeout.current);
      }
    };
  }, [searchValue]); // Solo depende de searchValue, no de articulosSeleccionados

  const handleSeleccionarArticulo = (producto) => {
    // Añadir el producto a la lista
    const nuevoArticulo = {
      ...producto,
    };
    setArticulosSeleccionados([...articulosSeleccionados, nuevoArticulo]);

    // Filtrar el producto seleccionado de los resultados mostrados
    setProductosFiltrados(
      productosFiltrados.filter((p) => p.id !== producto.id)
    );

    // Auto-añadir proveedor SOLO si NO estamos en carga inicial
    // codigoProveedor = cardcode del proveedor habitual (ej. "P0670")
    // proveedor/cardcode = código interno del artículo (ej. "0303003002-00147")
    const proveedorCode = producto.codigoProveedor || producto.proveedor;
    console.log("[AutoSelect] producto seleccionado:", {
      id: producto.id,
      nombre: producto.nombre,
      cardcode: producto.proveedor,
      codigoProveedor: producto.codigoProveedor,
      proveedorCode,
      autoAddEnabled: autoAddProviderEnabledRef.current,
    });

    if (autoAddProviderEnabledRef.current && proveedorCode) {
      const fetchProviderData = async () => {
        try {
          const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.articles}/provider/${proveedorCode}`;
          console.log("[AutoSelect] Buscando proveedor en:", url);
          const response = await hubspot.fetch(url, {
            timeout: API_CONFIG.timeout,
            headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
          });

          console.log("[AutoSelect] Respuesta HTTP:", response.status, response.ok);

          if (response.ok) {
            const data = await response.json();
            console.log("[AutoSelect] Datos proveedor recibidos:", data);

            if (data.success && data.provider) {
              // Validar que el proveedor tenga cardcode
              if (
                !data.provider.cardcode ||
                data.provider.cardcode.trim() === ""
              ) {
                console.warn(
                  "[AutoSelect] Proveedor obtenido no tiene cardcode válido, no se agregará automáticamente"
                );
                return;
              }

              // Añadir proveedor a la lista si no existe
              setProveedoresSeleccionados((prevProveedores) => {
                const existe = prevProveedores.find(
                  (p) => p.cardcode === data.provider.cardcode
                );
                console.log("[AutoSelect] ¿Proveedor ya existe en lista?", !!existe, "| cardcode:", data.provider.cardcode);
                if (!existe) {
                  console.log("[AutoSelect] Añadiendo proveedor a la lista:", data.provider.cardcode, data.provider.name);
                  return [
                    ...prevProveedores,
                    {
                      id: data.provider.id || data.provider.cardcode,
                      cardcode: data.provider.cardcode,
                      nombre: data.provider.name || data.provider.cardcode,
                      lugarCarga: data.provider.lugar_de_la_carga_envio || "",
                    },
                  ];
                }
                return prevProveedores;
              });
            } else {
              console.warn("[AutoSelect] Respuesta sin proveedor válido:", data);
            }
          } else {
            console.error(
              "[AutoSelect] Error al obtener proveedor:",
              response.status,
              response.statusText
            );
          }
        } catch (err) {
          console.error("[AutoSelect] Excepción al obtener datos del proveedor:", err);
        }
      };

      // Ejecutar en segundo plano
      fetchProviderData();
    } else {
      console.warn("[AutoSelect] Condición no cumplida — autoAddEnabled:", autoAddProviderEnabledRef.current, "| proveedorCode:", proveedorCode);
    }
  };

  const handleEliminarArticulo = (idArticulo) => {
    const nuevosArticulos = articulosSeleccionados.filter(
      (art) => art.id !== idArticulo
    );
    setArticulosSeleccionados(nuevosArticulos);

    // Limpiar búsqueda de artículos
    setSearchValue("");
    setProductosFiltrados([]);
  };

  // Función para buscar proveedores en la sección de proveedores
  const buscarProveedores = async (query) => {
    if (!query || query.trim().length === 0) {
      setProveedoresFiltrados([]);
      return;
    }

    setProveedorLoading(true);

    try {
      const url = `${API_CONFIG.baseURL}${
        API_CONFIG.endpoints.articles
      }/providers/list?query=${encodeURIComponent(query)}`;
      const response = await hubspot.fetch(url, {
        timeout: API_CONFIG.timeout,
        headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
      });
      const data = await response.json();

      if (data.success) {
        // Filtrar proveedores ya seleccionados y proveedores sin cardcode
        const proveedoresDisponibles = (data.providers || []).filter(
          (prov) =>
            prov.cardcode &&
            prov.cardcode.trim() !== "" &&
            !proveedoresSeleccionados.find((p) => p.cardcode === prov.cardcode)
        );
        setProveedoresFiltrados(proveedoresDisponibles);
      } else {
        console.error("Error al buscar proveedores:", data.error);
        setProveedoresFiltrados([]);
      }
    } catch (err) {
      console.error("Error al buscar proveedores:", err);
      setProveedoresFiltrados([]);
    } finally {
      setProveedorLoading(false);
    }
  };

  // Efecto para debounce de búsqueda de proveedores
  useEffect(() => {
    if (proveedorDebounceTimeout.current) {
      clearTimeout(proveedorDebounceTimeout.current);
    }

    if (!proveedorSearchValue || proveedorSearchValue.trim().length === 0) {
      setProveedoresFiltrados([]);
      return;
    }

    proveedorDebounceTimeout.current = setTimeout(() => {
      buscarProveedores(proveedorSearchValue);
    }, 500);

    return () => {
      if (proveedorDebounceTimeout.current) {
        clearTimeout(proveedorDebounceTimeout.current);
      }
    };
  }, [proveedorSearchValue]);

  // Handler para seleccionar un proveedor de los resultados
  const handleSeleccionarProveedor = (proveedor) => {
    // Validar que el proveedor tenga cardcode
    if (!proveedor.cardcode || proveedor.cardcode.trim() === "") {
      setError("No se puede agregar un proveedor sin código (cardcode)");
      return;
    }

    const nuevoProveedor = {
      id: proveedor.id || proveedor.cardcode,
      cardcode: proveedor.cardcode,
      nombre: proveedor.name || proveedor.cardcode,
      lugarCarga: proveedor.lugar_de_la_carga_envio || "",
    };
    setProveedoresSeleccionados([...proveedoresSeleccionados, nuevoProveedor]);
    setProveedoresFiltrados(
      proveedoresFiltrados.filter((p) => p.cardcode !== proveedor.cardcode)
    );

    // Limpiar cualquier error previo
    setError(null);
  };

  // Handler para eliminar un proveedor
  const handleEliminarProveedor = (cardcode) => {
    setProveedoresSeleccionados(
      proveedoresSeleccionados.filter((prov) => prov.cardcode !== cardcode)
    );

    // Limpiar búsqueda de proveedores
    setProveedorSearchValue("");
    setProveedoresFiltrados([]);
  };

  // Función para verificar si hay múltiples proveedores
  const tieneMultiplesProveedores = () => {
    return proveedoresSeleccionados.length > 1;
  };

  // Función para guardar las asociaciones de artículos y proveedores
  const guardarAsociacionesArticulos = async () => {
    try {
      setSubmitting(true);
      setError(null);

      const dealId = context?.crm?.objectId;
      if (!dealId) {
        setError("No se pudo obtener el ID del Deal desde el contexto.");
        return false;
      }

      // Validar que todos los proveedores tengan cardcode
      const proveedoresSinCardcode = proveedoresSeleccionados.filter(
        (prov) => !prov.cardcode || prov.cardcode.trim() === ""
      );

      if (proveedoresSinCardcode.length > 0) {
        setError(
          "Error: Hay proveedores sin código (cardcode) en la lista. Por favor, elimine los proveedores sin código antes de continuar."
        );
        return false;
      }

      // Construir el payload con el nuevo formato
      const payload = {
        line_items: articulosSeleccionados.map((art) => ({
          id: art.id, // HubSpot product object ID
          // Si el artículo tiene margen_unitario, usarlo; sino usar el margen del cliente
          margen_unitario: art.margen_unitario !== null && art.margen_unitario !== undefined
            ? art.margen_unitario / 100 // Convertir a decimal
            : clienteData?.margen / 100, // Convertir a decimal
        })),
        providers: proveedoresSeleccionados.map((prov) => ({
          cardcode: prov.cardcode,
        })),
        lugar_carga: lugarCarga,
      };

      const url = `${API_CONFIG.baseURL}${API_CONFIG.endpoints.deals}/${dealId}/articles`;
      const response = await hubspot.fetch(url, {
        method: "POST",
        body: payload,
        timeout: API_CONFIG.timeout,
        headers: { Authorization: `Bearer ${API_CONFIG.cardSecret}` }
      });

      if (!response.ok) {
        throw new Error(`Error ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      if (!data?.success) {
        throw new Error(
          data?.error || "La API devolvió un error al guardar las asociaciones."
        );
      }

      return true;
    } catch (err) {
      console.error("Error guardando asociaciones de artículos:", err);
      setError(
        err?.message ||
          "Error desconocido al guardar las asociaciones de artículos."
      );
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  // Handler para continuar (sin múltiples proveedores)
  const handleContinuar = async () => {
    const success = await guardarAsociacionesArticulos();
    if (success) {
      // Limpiar contenedores del estado global si había alguno
      // (el backend ya los eliminó)
      if (tieneContenedores && setContenedoresData) {
        setContenedoresData({
          contenedoresDefinidos: [],
          cantidadesContenedores: {},
          totales: {
            totalColis: 0,
            totalPesoNeto: 0,
            totalContenedores: 0,
            mediaOcupacion: 0
          }
        });
      }
      // Limpiar contenedores únicos también
      if (tieneContenedores && setContenedoresUnicos) {
        setContenedoresUnicos({ contenedoresUnicos: [] });
      }
      onNavigate("contenedores");
    }
  };

  // Handler para continuar desde la modal (con múltiples proveedores)
  const handleContinuarConMultiplesProveedores = async () => {
    if (actions) {
      actions.closeOverlay("modal-multiples-proveedores");
    }
    const success = await guardarAsociacionesArticulos();
    if (success) {
      // Limpiar contenedores del estado global si había alguno
      if (tieneContenedores && setContenedoresData) {
        setContenedoresData({
          contenedoresDefinidos: [],
          cantidadesContenedores: {},
          totales: {
            totalColis: 0,
            totalPesoNeto: 0,
            totalContenedores: 0,
            mediaOcupacion: 0
          }
        });
      }
      // Limpiar contenedores únicos también
      if (tieneContenedores && setContenedoresUnicos) {
        setContenedoresUnicos({ contenedoresUnicos: [] });
      }
      onNavigate("contenedores");
    }
  };

  // Handler para continuar desde la modal (con contenedores configurados)
  const handleContinuarConContenedoresExistentes = async () => {
    if (actions) {
      actions.closeOverlay("modal-contenedores-existentes");
    }
    const success = await guardarAsociacionesArticulos();
    if (success) {
      // Limpiar contenedores del estado global (el backend ya los eliminó)
      if (setContenedoresData) {
        setContenedoresData({
          contenedoresDefinidos: [],
          cantidadesContenedores: {},
          totales: {
            totalColis: 0,
            totalPesoNeto: 0,
            totalContenedores: 0,
            mediaOcupacion: 0
          }
        });
      }
      // Limpiar contenedores únicos también
      if (setContenedoresUnicos) {
        setContenedoresUnicos({ contenedoresUnicos: [] });
      }
      onNavigate("contenedores");
    }
  };

  // Mostrar loading similar a ClienteTab
  if (loadingArticulos) {
    return (
      <Box>
        <Heading>Selección de Artículos</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>Cargando artículos asociados al negocio...</Text>
        </Flex>
      </Box>
    );
  }

  return (
    <Box>
      <Heading>Selección de Artículos</Heading>

      <Flex direction="column" gap="medium">
        <Text>Busque y seleccione los productos para la cotización:</Text>

        <Flex direction="row" gap="small" align="end">
          <Box flex={1}>
            <SearchInput
              name="buscar-articulo"
              label="Buscar Artículo"
              placeholder=""
              value={searchValue}
              onChange={(value) => setSearchValue(value)}
              description="Busque productos por nombre"
            />
          </Box>
        </Flex>

        {error && (
          <Alert
            title={submitting ? "Error al guardar" : "Error"}
            variant="error"
          >
            {error}
          </Alert>
        )}

        {loading && searchValue && (
          <Flex direction="row" align="center" gap="small">
            <LoadingSpinner size="xs" />
            <Text>Buscando productos...</Text>
          </Flex>
        )}

        {!loading && searchValue && productosFiltrados.length > 0 && (
          <Box>
            <Text format={{ fontWeight: "bold" }}>
              Resultados de búsqueda ({productosFiltrados.length}):
            </Text>
            <Divider size="small" />
            <Flex direction="column" gap="small">
              {productosFiltrados.map((producto, index) => (
                <Box key={producto.id}>
                  <Flex
                    direction="row"
                    justify="space-between"
                    align="start"
                    gap="medium"
                  >
                    <Flex direction="column" gap="extra-small" flex={1}>
                      <Text format={{ fontWeight: "bold" }}>
                        {producto.nombre}
                      </Text>
                      {producto.codigoProveedor && (
                        <Text
                          format={{ fontStyle: "italic", fontSize: "small" }}
                        >
                          Código Proveedor: {producto.codigoProveedor}
                        </Text>
                      )}
                      {producto.pesoNeto > 0 && (
                        <Text format={{ fontSize: "small" }}>
                          Peso neto: {producto.pesoNeto} kg
                        </Text>
                      )}
                    </Flex>
                    <Flex direction="row" align="center" gap="medium">
                      <Button
                        size="xs"
                        variant="secondary"
                        onClick={() => handleSeleccionarArticulo(producto)}
                      >
                        Seleccionar
                      </Button>
                    </Flex>
                  </Flex>
                  {index < productosFiltrados.length - 1 && (
                    <Divider size="small" />
                  )}
                </Box>
              ))}
            </Flex>
          </Box>
        )}

        {!loading &&
          searchValue &&
          productosFiltrados.length === 0 &&
          !error && (
            <Text>
              No se encontraron productos disponibles que coincidan con "{searchValue}". Los artículos ya seleccionados no se muestran en los resultados.
            </Text>
          )}

        <Heading>Artículos Seleccionados</Heading>

        {tipoContenedorOptions &&
          tipoContenedorOptions.length > 0 &&
          articulosSeleccionados.length > 0 && (
            <Box>
              <Select
                name="tipo-contenedor-vista"
                label="Tipo Contenedor"
                value={tipoContenedorVista}
                onChange={(value) => setTipoContenedorVista(value)}
                options={tipoContenedorOptions}
                description="Seleccione el tipo de contenedor para ver capacidades"
              />
            </Box>
          )}

        {articulosSeleccionados.length === 0 ? (
          <EmptyState title="No hay artículos seleccionados" layout="vertical">
            <Text>
              Use el buscador de arriba para añadir productos a la cotización.
            </Text>
          </EmptyState>
        ) : (
          <Table bordered={true}>
            <TableHead>
              <TableRow>
                <TableHeader>Nombre</TableHeader>
                <TableHeader>Código Proveedor</TableHeader>
                <TableHeader align="right">Peso Neto (kg)</TableHeader>
                <TableHeader align="right">
                  Máx. Cajas{" "}
                  {tipoContenedorVista && tipoContenedorOptions
                    ? `(${
                        tipoContenedorOptions.find(
                          (opt) => opt.value === tipoContenedorVista
                        )?.label || tipoContenedorVista
                      })`
                    : ""}
                </TableHeader>
                <TableHeader width="min">Acciones</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {articulosSeleccionados.map((articulo) => (
                <TableRow key={articulo.id}>
                  <TableCell>{articulo.nombre}</TableCell>
                  <TableCell>{articulo.codigoProveedor || "-"}</TableCell>
                  <TableCell align="right">
                    {articulo.pesoNeto ? articulo.pesoNeto.toFixed(2) : "0.00"}
                  </TableCell>
                  <TableCell align="right">
                    {getMaxCajas(articulo, tipoContenedorVista)}
                  </TableCell>
                  <TableCell width="min">
                    <Button
                      size="xs"
                      variant="destructive"
                      onClick={() => handleEliminarArticulo(articulo.id)}
                    >
                      Eliminar
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <Divider size="medium" />

        <Heading>Selección de Proveedores</Heading>

        {articulosSeleccionados.length === 0 ? (
          <Alert title="Seleccione artículos primero" variant="info">
            <Text>
              Debe seleccionar al menos un artículo antes de añadir proveedores.
            </Text>
          </Alert>
        ) : (
          <>
            <Text>
              Busque y seleccione los proveedores para esta cotización:
            </Text>

            <Flex direction="row" gap="small" align="end">
              <Box flex={1}>
                <SearchInput
                  name="buscar-proveedor"
                  label="Buscar Proveedor"
                  placeholder=""
                  value={proveedorSearchValue}
                  onChange={(value) => setProveedorSearchValue(value)}
                  description="Busque proveedores por nombre o código"
                />
              </Box>
            </Flex>
          </>
        )}

        {/* Loading spinner durante búsqueda de proveedores */}
        {articulosSeleccionados.length > 0 &&
          proveedorLoading &&
          proveedorSearchValue && (
            <Flex direction="row" align="center" gap="small">
              <LoadingSpinner size="xs" />
              <Text>Buscando proveedores...</Text>
            </Flex>
          )}

        {articulosSeleccionados.length > 0 &&
          !proveedorLoading &&
          proveedorSearchValue &&
          proveedoresFiltrados.length > 0 && (
            <Box>
              <Text format={{ fontWeight: "bold" }}>
                Resultados de búsqueda ({proveedoresFiltrados.length}):
              </Text>
              <Divider size="small" />
              <Flex direction="column" gap="small">
                {proveedoresFiltrados.map((proveedor) => (
                  <Box key={proveedor.id || proveedor.cardcode}>
                    <Flex
                      direction="row"
                      justify="space-between"
                      align="start"
                      gap="medium"
                    >
                      <Flex direction="column" gap="extra-small" flex={1}>
                        <Text format={{ fontWeight: "bold" }}>
                          {proveedor.cardcode}
                        </Text>
                        {proveedor.name && (
                          <Text format={{ fontSize: "small" }}>
                            {proveedor.name}
                          </Text>
                        )}
                      </Flex>
                      <Button
                        size="xs"
                        variant="secondary"
                        onClick={() => handleSeleccionarProveedor(proveedor)}
                      >
                        Seleccionar
                      </Button>
                    </Flex>
                    <Divider size="small" />
                  </Box>
                ))}
              </Flex>
            </Box>
          )}

        {articulosSeleccionados.length > 0 &&
          !proveedorLoading &&
          proveedorSearchValue &&
          proveedoresFiltrados.length === 0 && (
            <Text>
              No se encontraron proveedores que coincidan con "
              {proveedorSearchValue}"
            </Text>
          )}

        {articulosSeleccionados.length > 0 && (
          <>
            <Heading>Proveedores Seleccionados</Heading>

            {proveedoresSeleccionados.length === 0 ? (
              <EmptyState
                title="No hay proveedores seleccionados"
                layout="vertical"
              >
                <Text>
                  Los proveedores se añadirán automáticamente al seleccionar
                  artículos, o puede buscarlos manualmente arriba.
                </Text>
              </EmptyState>
            ) : (
              <Table bordered={true}>
                <TableHead>
                  <TableRow>
                    <TableHeader>Código Proveedor</TableHeader>
                    <TableHeader>Nombre</TableHeader>
                    <TableHeader>Localidad de Carga</TableHeader>
                    <TableHeader width="min">Acciones</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {proveedoresSeleccionados.map((proveedor) => (
                    <TableRow key={proveedor.cardcode}>
                      <TableCell>{proveedor.cardcode}</TableCell>
                      <TableCell>{proveedor.nombre || "-"}</TableCell>
                      <TableCell>{proveedor.lugarCarga || "-"}</TableCell>
                      <TableCell width="min">
                        <Button
                          size="xs"
                          variant="destructive"
                          onClick={() =>
                            handleEliminarProveedor(proveedor.cardcode)
                          }
                        >
                          Eliminar
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            {proveedoresSeleccionados.length > 1 && (
              <>
                <Divider size="medium" />
                <Alert
                  title="Múltiples Proveedores Detectados"
                  variant="warning"
                >
                  <Flex direction="column" gap="small">
                    <Text>
                      Has seleccionado {proveedoresSeleccionados.length}{" "}
                      proveedores diferentes para esta cotización.
                    </Text>
                    <Text>Esto implica que la ruta logística será:</Text>
                    <Box>
                      <Text format={{ fontWeight: "bold" }}>
                        Fábrica 1, Fábrica 2, ... → Almacén PROEX → Puerto
                        Origen PROEX → Puerto Destino
                      </Text>
                    </Box>
                    <Text>
                      Esta configuración generará un sobrecoste adicional debido
                      a la consolidación de mercancía desde múltiples fábricas.
                    </Text>
                    <Divider size="small" />
                    <Text format={{ fontStyle: "italic" }}>
                      💡 Recomendación: Si es posible, utiliza un único
                      proveedor para optimizar costes y tiempos de envío.
                    </Text>
                  </Flex>
                </Alert>
              </>
            )}
          </>
        )}

        <Select
          label="Localidad de Carga"
          name="lugar-carga"
          description={
            lugarCargaDisabled
              ? "Con múltiples proveedores, la mercancía debe salir del Almacén PROEX"
              : "Seleccione el lugar desde donde se cargará la mercancía"
          }
          placeholder="Seleccionar..."
          required={true}
          value={lugarCarga}
          onChange={(value) => setLugarCarga(value)}
          options={lugarCargaOptions}
          readOnly={lugarCargaDisabled}
          tooltip={
            lugarCargaDisabled
              ? "Este campo está bloqueado porque hay múltiples proveedores. Debe eliminarse hasta dejar solo 1 proveedor para poder cambiar la localidad de carga"
              : "Lugar de origen del envío. Seleccione 'Almacen PROEX' si la mercancía sale del almacén de PROEX"
          }
        />

        {(articulosSeleccionados.length === 0 ||
          proveedoresSeleccionados.length === 0) && (
          <Alert title="Información Requerida" variant="warning">
            <Flex direction="column" gap="small">
              {articulosSeleccionados.length === 0 && (
                <Text>• Debe seleccionar al menos un artículo</Text>
              )}
              {proveedoresSeleccionados.length === 0 && (
                <Text>• Debe seleccionar al menos un proveedor</Text>
              )}
            </Flex>
          </Alert>
        )}

        <ButtonRow>
          <Button
            variant="secondary"
            onClick={() => onNavigate("cliente")}
            disabled={submitting}
          >
            Anterior
          </Button>
          {tieneMultiplesProveedores() ? (
            <Button
              variant="primary"
              disabled={
                articulosSeleccionados.length === 0 ||
                proveedoresSeleccionados.length === 0 ||
                submitting
              }
              overlay={
                <Modal
                  id="modal-multiples-proveedores"
                  title="Confirmación: Múltiples Proveedores"
                  width="md"
                  variant="default"
                >
                  <ModalBody>
                    <Flex direction="column" gap="medium">
                      <Text format={{ fontWeight: "bold" }}>
                        Has seleccionado artículos de múltiples proveedores
                        diferentes.
                      </Text>
                      <Text>Esto implica que la ruta logística será:</Text>
                      <Box>
                        <Text format={{ fontWeight: "demibold" }}>
                          Fábrica 1, Fábrica 2, ... → Almacén PROEX → Puerto
                          Origen PROEX → Puerto Destino
                        </Text>
                      </Box>
                      <Alert title="Sobrecoste Adicional" variant="warning">
                        Esta configuración generará un sobrecoste adicional
                        debido a la consolidación de mercancía desde múltiples
                        fábricas.
                      </Alert>
                      <Text>
                        ¿Deseas continuar de todos modos o prefieres ajustar la
                        selección de proveedores?
                      </Text>
                    </Flex>
                  </ModalBody>
                  <ModalFooter>
                    <ButtonRow>
                      <Button
                        variant="secondary"
                        onClick={() =>
                          actions &&
                          actions.closeOverlay("modal-multiples-proveedores")
                        }
                        disabled={submitting}
                      >
                        Cancelar y Ajustar
                      </Button>
                      <Button
                        variant="primary"
                        onClick={handleContinuarConMultiplesProveedores}
                        disabled={submitting}
                        loading={submitting}
                      >
                        {submitting
                          ? "Guardando..."
                          : "Continuar de Todos Modos"}
                      </Button>
                    </ButtonRow>
                  </ModalFooter>
                </Modal>
              }
            >
              Siguiente
            </Button>
          ) : tieneContenedores ? (
            <Button
              variant="primary"
              disabled={
                articulosSeleccionados.length === 0 ||
                proveedoresSeleccionados.length === 0 ||
                submitting
              }
              overlay={
                <Modal
                  id="modal-contenedores-existentes"
                  title="Advertencia: Contenedores Configurados"
                  width="md"
                  variant="default"
                >
                  <ModalBody>
                    <Flex direction="column" gap="medium">
                      <Alert title="Contenedores Existentes" variant="warning">
                        Hay contenedores configurados en este negocio que serán eliminados.
                      </Alert>
                      <Text format={{ fontWeight: "bold" }}>
                        Al modificar los artículos, se eliminarán todos los contenedores configurados.
                      </Text>
                      <Text>
                        Esto es necesario porque los artículos están vinculados a contenedores específicos.
                        Deberás reconfigurar los contenedores en el siguiente paso.
                      </Text>
                      <Text>
                        ¿Deseas continuar y eliminar la configuración actual de contenedores?
                      </Text>
                    </Flex>
                  </ModalBody>
                  <ModalFooter>
                    <ButtonRow>
                      <Button
                        variant="secondary"
                        onClick={() =>
                          actions &&
                          actions.closeOverlay("modal-contenedores-existentes")
                        }
                        disabled={submitting}
                      >
                        Cancelar
                      </Button>
                      <Button
                        variant="primary"
                        onClick={handleContinuarConContenedoresExistentes}
                        disabled={submitting}
                        loading={submitting}
                      >
                        {submitting
                          ? "Guardando..."
                          : "Continuar y Eliminar Contenedores"}
                      </Button>
                    </ButtonRow>
                  </ModalFooter>
                </Modal>
              }
            >
              Siguiente
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={handleContinuar}
              disabled={
                articulosSeleccionados.length === 0 ||
                proveedoresSeleccionados.length === 0 ||
                submitting
              }
              loading={submitting}
            >
              {submitting ? "Guardando..." : "Siguiente"}
            </Button>
          )}
        </ButtonRow>
      </Flex>
    </Box>
  );
};

export default ArticulosTab;
