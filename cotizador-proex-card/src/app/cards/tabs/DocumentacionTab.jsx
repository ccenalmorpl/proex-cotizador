import {
  Box,
  Heading,
  Flex,
  Divider,
  ButtonRow,
  Button,
  Input,
  Select,
  TextArea,
  Text,
  Checkbox,
  Alert,
  LoadingSpinner,
  Toggle,
} from "@hubspot/ui-extensions";
import { useState, useEffect, useCallback, useRef } from "react";
import {
  LOADING_MESSAGES,
  ERROR_MESSAGES,
  INITIAL_RENDER_DELAY_MS,
} from "../constants/common.constants";
import {
  fetchFromAPI,
  fetchTextosCotizacion,
  convertTextToHTML,
} from "../utils/api.utils";

const DEFAULT_SHIPPER = `PROEX DRINKS GROUP, S.A\nAvda. Américo Vespucio 5\nEdif. Cartuja, bloque A, 2ª planta\n41092 Sevilla (Sevilla) España\nC.I.F A41992975`;

const BANCO_HUBDB_KEY = {
  "572001": "banco_sabadell",
  "572003": "banco_la_caixa",
};

const getBancoHubDbKey = (value) => {
  if (!value) return null;
  if (value === "cotizacion_rapida") return "cotizacion_rapida";
  return BANCO_HUBDB_KEY[value] || null;
};

const DocumentacionTab = ({
  context,
  onNavigate,
  documentacionData,
  setDocumentacionData,
  articulosSeleccionados = [],
  contenedoresData,
  transitarioData,
}) => {
  // Estado de carga y errores
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [isLoadingInitial, setIsLoadingInitial] = useState(true);

  // Estados de loading para textos dinámicos
  const [isLoadingTextoBanco, setIsLoadingTextoBanco] = useState(false);
  const [isLoadingTextoFormaPago, setIsLoadingTextoFormaPago] = useState(false);
  const [isLoadingTextoBaseOferta, setIsLoadingTextoBaseOferta] =
    useState(false);

  // Marcar que el render inicial está completo después de un breve delay
  useEffect(() => {
    const timer = setTimeout(() => {
      setIsLoadingInitial(false);
    }, INITIAL_RENDER_DELAY_MS);

    return () => clearTimeout(timer);
  }, []);

  // Cargar textos desde HubDB en la carga inicial si hay selectores con valores pero textos vacíos
  useEffect(() => {
    const cargarTextosIniciales = async () => {
      // Solo ejecutar después del render inicial
      if (isLoadingInitial) return;

      // Preseleccionar "cotizacion_rapida" si transitarioData.cotizacionRapida es true
      if (transitarioData?.cotizacionRapida === true) {
        const bancoCobro = documentacionData.bancoCobro?.value;
        const metodoPago = documentacionData.metodoPagoPlantilla?.value;

        // Solo actualizar si los selectores no tienen valor o son diferentes de cotizacion_rapida
        if (
          bancoCobro !== "cotizacion_rapida" ||
          metodoPago !== "cotizacion_rapida"
        ) {
          setDocumentacionData((prev) => ({
            ...prev,
            bancoCobro: {
              value: "cotizacion_rapida",
              options: prev.bancoCobro?.options || [],
            },
            metodoPagoPlantilla: {
              value: "cotizacion_rapida",
              options: prev.metodoPagoPlantilla?.options || [],
            },
          }));
          return; // Salir para que se ejecute de nuevo con los nuevos valores
        }
      }

      const idioma = documentacionData.idiomaCotizacion?.value;
      const bancoClave = documentacionData.bancoCobro?.value;
      const metodoPagoClave = documentacionData.metodoPagoPlantilla?.value;

      // Si no hay idioma, no podemos cargar textos
      if (!idioma) return;

      // Verificar si necesitamos cargar texto de banco (sólo si el value mapea a una clave HubDB)
      const claveHubDbBanco = getBancoHubDbKey(bancoClave);
      const necesitaTextoBanco =
        !!claveHubDbBanco && !documentacionData.textoBanco;
      // Verificar si necesitamos cargar texto de forma de pago
      const necesitaTextoFormaPago =
        metodoPagoClave && !documentacionData.textoFormaPago;
      // Verificar si necesitamos cargar texto base de oferta
      const necesitaTextoBaseOferta = !documentacionData.textoBaseOferta;

      // Si no hay nada que cargar, salir
      if (
        !necesitaTextoBanco &&
        !necesitaTextoFormaPago &&
        !necesitaTextoBaseOferta
      )
        return;

      // Activar loading para los campos que se van a cargar
      if (necesitaTextoBanco) setIsLoadingTextoBanco(true);
      if (necesitaTextoFormaPago) setIsLoadingTextoFormaPago(true);
      if (necesitaTextoBaseOferta) setIsLoadingTextoBaseOferta(true);

      // Preparar las promesas
      let textoBancoPromise = Promise.resolve("");
      let textoFormaPagoPromise = Promise.resolve("");
      let textoBaseOfertaPromise = Promise.resolve("");

      if (necesitaTextoBanco) {
        const fetchOptions =
          bancoClave === "cotizacion_rapida"
            ? { grupo: "bancos", subgrupo: "cotizacion_rapida" }
            : {};

        textoBancoPromise = fetchTextosCotizacion(
          idioma,
          claveHubDbBanco,
          fetchOptions
        );
      }

      if (necesitaTextoFormaPago) {
        // Determinar si es cotización rápida
        let fetchOptions = {};
        if (metodoPagoClave === "cotizacion_rapida") {
          fetchOptions = {
            grupo: "pagos",
            subgrupo: "cotizacion_rapida",
          };
        }

        textoFormaPagoPromise = fetchTextosCotizacion(
          idioma,
          metodoPagoClave,
          fetchOptions
        );
      }

      if (necesitaTextoBaseOferta) {
        textoBaseOfertaPromise = fetchTextosCotizacion(idioma, "oferta_base");
      }

      // Esperar todas las promesas
      const [textoBanco, textoFormaPago, textoBaseOferta] = await Promise.all([
        textoBancoPromise,
        textoFormaPagoPromise,
        textoBaseOfertaPromise,
      ]);

      // Actualizar solo los campos que se cargaron
      const updates = {};
      if (necesitaTextoBanco && textoBanco) {
        updates.textoBanco = textoBanco;
      }
      if (necesitaTextoFormaPago && textoFormaPago) {
        updates.textoFormaPago = textoFormaPago;
      }
      if (necesitaTextoBaseOferta && textoBaseOferta) {
        updates.textoBaseOferta = textoBaseOferta;
      }

      // Solo actualizar si hay cambios
      if (Object.keys(updates).length > 0) {
        setDocumentacionData({
          ...documentacionData,
          ...updates,
        });
      }

      // Desactivar loading
      setIsLoadingTextoBanco(false);
      setIsLoadingTextoFormaPago(false);
      setIsLoadingTextoBaseOferta(false);
    };

    cargarTextosIniciales();
  }, [
    isLoadingInitial,
    documentacionData.idiomaCotizacion?.value,
    documentacionData.bancoCobro?.value,
    documentacionData.metodoPagoPlantilla?.value,
    transitarioData?.cotizacion_rapida,
  ]);

  // Inicializar shipper con valor por defecto si está vacío
  useEffect(() => {
    if (!isLoadingInitial && documentacionData != null && !documentacionData.shipper) {
      setDocumentacionData((prev) => ({ ...prev, shipper: DEFAULT_SHIPPER }));
    }
  }, [isLoadingInitial, documentacionData?.shipper]);

  // Helper para actualizar el estado compartido
  const updateDocumentacionField = (field, value) => {
    setDocumentacionData({
      ...documentacionData,
      [field]: value,
    });
  };

  // Genera la descripción de mercancía para el certificado de origen
  const generarDescripcionMercancia = useCallback((idioma) => {
    const { contenedoresDefinidos = [], cantidadesContenedores = {} } = contenedoresData || {};
    if (!contenedoresDefinidos.length || !articulosSeleccionados?.length) return "";

    const cajasPorArticulo = {};
    contenedoresDefinidos.forEach((contenedor) => {
      const cantidad = cantidadesContenedores[contenedor.id] || 0;
      (contenedor.articulos || []).forEach((art) => {
        cajasPorArticulo[art.articuloId] = (cajasPorArticulo[art.articuloId] || 0) + art.numCajas * cantidad;
      });
    });

    const idiomaLower = (idioma || "").toLowerCase();
    const cajasText = idiomaLower === "en" ? "CASES" : idiomaLower === "fr" ? "CAISSES" : "CAJAS";

    const lines = articulosSeleccionados
      .filter((art) => cajasPorArticulo[art.id] > 0)
      .map((art) => {
        const nombre =
          idiomaLower === "en" ? (art.name_en || art.nombre) :
          idiomaLower === "fr" ? (art.name_fr || art.nombre) :
          art.nombre;
        return `${cajasPorArticulo[art.id]} ${cajasText} ${(nombre || "").toUpperCase()}`;
      });

    return lines.join("\n");
  }, [contenedoresData, articulosSeleccionados]);

  // Auto-generar descripción de mercancía cuando cambian los contenedores
  const hasMountedContenedoresRef = useRef(false);
  useEffect(() => {
    const { contenedoresDefinidos = [] } = contenedoresData || {};
    if (!contenedoresDefinidos.length || !articulosSeleccionados?.length) return;

    if (!hasMountedContenedoresRef.current) {
      hasMountedContenedoresRef.current = true;
      // Primera carga real: respetar el valor ya guardado
      if (documentacionData.certificadoOrigenMercancia) return;
    }

    const idioma = documentacionData.idiomaCotizacion?.value;
    const text = generarDescripcionMercancia(idioma);
    if (text) updateDocumentacionField("certificadoOrigenMercancia", text);
  }, [contenedoresData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Al cambiar el idioma, se rellenan los textos
  const handleIdiomaChange = async (value) => {
    if (!value) {
      // Si limpias el select, vaciar todos los textos
      setDocumentacionData({
        ...documentacionData,
        idiomaCotizacion: {
          value: value,
          options: documentacionData.idiomaCotizacion?.options || [],
        },
        textoBanco: "",
        textoFormaPago: "",
        textoBaseOferta: "",
      });
      return;
    }

    // Obtener los textos del banco y forma de pago desde HubDB
    const bancoClave = documentacionData.bancoCobro?.value || "";
    const metodoPagoClave = documentacionData.metodoPagoPlantilla?.value || "";

    // Activar loading para los campos que se van a actualizar
    if (bancoClave) setIsLoadingTextoBanco(true);
    if (metodoPagoClave) setIsLoadingTextoFormaPago(true);
    setIsLoadingTextoBaseOferta(true);

    // Preparar las promesas para obtener los textos
    let textoBancoPromise = Promise.resolve("");
    let textoFormaPagoPromise = Promise.resolve("");
    let textoBaseOfertaPromise = fetchTextosCotizacion(value, "oferta_base");

    // Obtener texto del banco si hay banco seleccionado
    if (bancoClave) {
      const claveConPrefijo = bancoClave.startsWith("banco_")
        ? bancoClave
        : `banco_${bancoClave}`;

      // Determinar si es cotización rápida
      let fetchOptions = {};
      if (bancoClave === "cotizacion_rapida") {
        fetchOptions = {
          grupo: "bancos",
          subgrupo: "cotizacion_rapida",
        };
      }

      textoBancoPromise = fetchTextosCotizacion(
        value,
        claveConPrefijo,
        fetchOptions
      );
    }

    // Obtener texto de forma de pago si hay método seleccionado
    if (metodoPagoClave) {
      // Determinar si es cotización rápida
      let fetchOptions = {};
      if (metodoPagoClave === "cotizacion_rapida") {
        fetchOptions = {
          grupo: "pagos",
          subgrupo: "cotizacion_rapida",
        };
      }

      textoFormaPagoPromise = fetchTextosCotizacion(
        value,
        metodoPagoClave,
        fetchOptions
      );
    }

    // Esperar todas las promesas en paralelo
    const [textoBanco, textoFormaPago, textoBaseOferta] = await Promise.all([
      textoBancoPromise,
      textoFormaPagoPromise,
      textoBaseOfertaPromise,
    ]);

    // Actualizar todos los campos en una sola llamada
    setDocumentacionData({
      ...documentacionData,
      idiomaCotizacion: {
        value: value,
        options: documentacionData.idiomaCotizacion?.options || [],
      },
      textoBanco: textoBanco || documentacionData.textoBanco || "",
      textoFormaPago: textoFormaPago || documentacionData.textoFormaPago || "",
      textoBaseOferta:
        textoBaseOferta || documentacionData.textoBaseOferta || "",
      certificadoOrigenMercancia: generarDescripcionMercancia(value) || documentacionData.certificadoOrigenMercancia || "",
    });

    // Desactivar loading
    setIsLoadingTextoBanco(false);
    setIsLoadingTextoFormaPago(false);
    setIsLoadingTextoBaseOferta(false);
  };

  // Handler para cambio de banco de cobro
  const handleBancoCobroChange = async (value) => {
    const idioma = documentacionData.idiomaCotizacion?.value;
    const claveHubDb = getBancoHubDbKey(value);

    if (!value) {
      setDocumentacionData({
        ...documentacionData,
        bancoCobro: {
          value: value,
          options: documentacionData.bancoCobro?.options || [],
        },
        textoBanco: "",
      });
      return;
    }

    if (!claveHubDb) {
      // Value sin plantilla en HubDB: dejar el campo vacío para edición manual
      setDocumentacionData({
        ...documentacionData,
        bancoCobro: {
          value: value,
          options: documentacionData.bancoCobro?.options || [],
        },
        textoBanco: "",
      });
      return;
    }

    if (!idioma) {
      updateDocumentacionField("bancoCobro", {
        value: value,
        options: documentacionData.bancoCobro?.options || [],
      });
      return;
    }

    setIsLoadingTextoBanco(true);

    const fetchOptions =
      value === "cotizacion_rapida"
        ? { grupo: "bancos", subgrupo: "cotizacion_rapida" }
        : {};

    const textoBanco = await fetchTextosCotizacion(
      idioma,
      claveHubDb,
      fetchOptions
    );

    setDocumentacionData({
      ...documentacionData,
      bancoCobro: {
        value: value,
        options: documentacionData.bancoCobro?.options || [],
      },
      textoBanco: textoBanco || "",
    });

    setIsLoadingTextoBanco(false);
  };

  // Handler para cambio de método de pago plantilla
  const handleMetodoPagoChange = async (value) => {
    // Si hay idioma seleccionado, obtener el texto de forma de pago desde HubDB
    const idioma = documentacionData.idiomaCotizacion?.value;

    if (idioma && value) {
      // Activar loading
      setIsLoadingTextoFormaPago(true);

      // Determinar si es cotización rápida y preparar opciones
      let fetchOptions = {};
      if (value === "cotizacion_rapida") {
        fetchOptions = {
          grupo: "pagos",
          subgrupo: "cotizacion_rapida",
        };
      }

      const textoFormaPago = await fetchTextosCotizacion(
        idioma,
        value,
        fetchOptions
      );

      // Actualizar ambos campos en una sola llamada para evitar re-renders conflictivos
      setDocumentacionData({
        ...documentacionData,
        metodoPagoPlantilla: {
          value: value,
          options: documentacionData.metodoPagoPlantilla?.options || [],
        },
        textoFormaPago:
          textoFormaPago || documentacionData.textoFormaPago || "",
      });

      // Desactivar loading
      setIsLoadingTextoFormaPago(false);
    } else if (!value) {
      // Si se limpia el selector, vaciar el texto
      setDocumentacionData({
        ...documentacionData,
        metodoPagoPlantilla: {
          value: value,
          options: documentacionData.metodoPagoPlantilla?.options || [],
        },
        textoFormaPago: "",
      });
    } else {
      // Solo actualizar el método de pago si no hay idioma
      updateDocumentacionField("metodoPagoPlantilla", {
        value: value,
        options: documentacionData.metodoPagoPlantilla?.options || [],
      });
    }
  };

  // Handler para checkboxes de documentos a generar
  const handleDocumentosCheckboxChange = (checkboxValue, isChecked) => {
    const currentValue = documentacionData.documentosAGenerar?.value || "";
    const currentValues = currentValue ? currentValue.split(";") : [];

    let newValues;
    if (isChecked) {
      // Agregar valor si no existe
      if (!currentValues.includes(checkboxValue)) {
        newValues = [...currentValues, checkboxValue];
      } else {
        newValues = currentValues;
      }
    } else {
      // Remover valor
      newValues = currentValues.filter((v) => v !== checkboxValue);
    }

    const newValue = newValues.join(";");
    updateDocumentacionField("documentosAGenerar", {
      value: newValue,
      options: documentacionData.documentosAGenerar?.options || [],
    });
  };

  // Handler para guardar documentación
  const handleGuardarDocumentacion = async () => {
    setIsSubmitting(true);
    setSaveError(null); // Limpiar errores previos

    try {
      // Verificar si BL está seleccionado
      const documentosSeleccionados =
        documentacionData.documentosAGenerar?.value || "";
      const selectedValues = documentosSeleccionados
        ? documentosSeleccionados.split(";")
        : [];
      const isBLSelected =
        selectedValues.includes("bl") || selectedValues.includes("BL");

      const payload = {
        deal: {
          // Idioma global
          preprints_language: documentacionData.idiomaCotizacion?.value || null,

          // Documentación
          direccion_fiscal: documentacionData.direccionFiscal || null,
          direccion_de_entrega: documentacionData.direccionDeEntrega || null,
          cif: documentacionData.cif || null,
          referencia_cotizacion: documentacionData.referenciaCotizacion || null,

          // Documentación Logística
          desagregar_cotizacion: documentacionData.desagregarCotizacion || null,
          documentos_a_generar:
            documentacionData.documentosAGenerar?.value || null,
          texto_en_bl: isBLSelected
            ? documentacionData.textoEnBl || null
            : null,
          comentarios_logistica: documentacionData.comentariosLogistica || null,
          comentarios_transitario:
            documentacionData.comentariosTransitario || null,

          // Certificado de Origen
          consignee: documentacionData.consignee || null,
          shipper: documentacionData.shipper || null,
          notify: documentacionData.notify || null,
          certificado_origen_mercancia:
            documentacionData.certificadoOrigenMercancia || null,
          observaciones: documentacionData.observaciones || null,

          // Método de Pago & Textos
          metodo_pago_plantilla:
            documentacionData.metodoPagoPlantilla?.value || null,
          banco_cobro: documentacionData.bancoCobro?.value || null,
          texto_adicional_metodo_pago:
            documentacionData.textoAdicionalMetodoPago || null,

          // Textos Generados - Convertir a HTML antes de guardar
          texto_banco: documentacionData.textoBanco
            ? convertTextToHTML(documentacionData.textoBanco)
            : null,
          texto_forma_pago: documentacionData.textoFormaPago
            ? convertTextToHTML(documentacionData.textoFormaPago)
            : null,
          texto_base_oferta: documentacionData.textoBaseOferta
            ? convertTextToHTML(documentacionData.textoBaseOferta)
            : null,
        },
      };

      // Llamar al endpoint de guardar documentación
      const response = await fetchFromAPI(
        `/api/v1/deals/${context.crm.objectId}/documentacion`,
        {
          method: "POST",
          body: payload,
        }
      );

      if (response.success) {
        // Navegar al siguiente tab
        onNavigate("revision");
      } else {
        throw new Error(response.error || ERROR_MESSAGES.SAVE_ERROR);
      }
    } catch (error) {
      console.error("Error al guardar documentación:", error);
      setSaveError(error.message || ERROR_MESSAGES.SAVE_ERROR);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Verificar si los datos están listos
  const hasDocumentacionData =
    documentacionData && documentacionData.documentosAGenerar != null;
  const isDataReady = !isLoadingInitial && hasDocumentacionData;

  // Mostrar loading mientras se cargan los datos iniciales
  if (!isDataReady) {
    return (
      <Box>
        <Heading>Documentación y Textos</Heading>
        <Flex direction="column" align="center" gap="medium">
          <LoadingSpinner size="md" />
          <Text>{LOADING_MESSAGES.LOADING_DOCUMENTACION}</Text>
        </Flex>
      </Box>
    );
  }

  // Determinar si el botón "Siguiente" debe estar deshabilitado
  const isNextDisabled =
    isSubmitting ||
    !documentacionData.direccionFiscal ||
    !documentacionData.textoBaseOferta ||
    !documentacionData.metodoPagoPlantilla?.value ||
    !documentacionData.textoFormaPago ||
    !documentacionData.idiomaCotizacion?.value ||
    !documentacionData.bancoCobro?.value ||
    !documentacionData.textoBanco;

  return (
    <Box>
      <Heading>Documentación y Textos</Heading>

      {saveError && (
        <Alert title="Error al guardar" variant="error">
          {saveError}
        </Alert>
      )}

      <Flex direction="column" gap="medium">
        <Flex direction="row" gap="small">
          <Box flex={1}>
            <TextArea
              label="Dirección Fiscal"
              name="direccion_fiscal"
              description="Dirección fiscal completa de la empresa (incluir calle, número, CP, ciudad y país)"
              placeholder=""
              rows={3}
              value={documentacionData.direccionFiscal || ""}
              onChange={(value) =>
                updateDocumentacionField("direccionFiscal", value)
              }
              required={true}
            />
          </Box>
          <Box flex={1}>
            <TextArea
              label="Dirección de Entrega"
              name="direccion_de_entrega"
              description="Dirección donde se entregará la mercancía (si aplica)."
              placeholder=""
              rows={3}
              value={documentacionData.direccionDeEntrega || ""}
              onChange={(value) =>
                updateDocumentacionField("direccionDeEntrega", value)
              }
            />
          </Box>
        </Flex>

        <Box>
          <Input
            label="NIF del Cliente"
            name="cif"
            description="NIF/CIF del cliente para esta cotización (no modifica el NIF de la empresa)"
            placeholder=""
            value={documentacionData.cif || ""}
            onChange={(value) => updateDocumentacionField("cif", value)}
          />
        </Box>

        <Box>
          <Input
            label="Referencia del cliente"
            name="referencia_cotizacion"
            description="Si el cliente facilita una referencia propia, aparecerá en la proforma y demás documentos."
            placeholder=""
            value={documentacionData.referenciaCotizacion || ""}
            onChange={(value) =>
              updateDocumentacionField("referenciaCotizacion", value)
            }
          />
        </Box>

        {/* Shipper | Consignee | Notify */}
        <Flex direction="row" gap="small">
          <Box flex={1}>
            <TextArea
              label="Shipper (Exportador)"
              name="shipper"
              description="Nombre y dirección completa del remitente/exportador"
              placeholder=""
              rows={4}
              value={documentacionData.shipper || ""}
              onChange={(value) =>
                updateDocumentacionField("shipper", value)
              }
            />
          </Box>
          <Box flex={1}>
            <TextArea
              label="Consignee (Importador)"
              name="consignee"
              description="Nombre y dirección completa del destinatario/consignatario"
              placeholder=""
              rows={4}
              value={documentacionData.consignee || ""}
              onChange={(value) =>
                updateDocumentacionField("consignee", value)
              }
            />
          </Box>
          <Box flex={1}>
            <TextArea
              label="Notify (Notificar a)"
              name="notify"
              description="Parte a quien notificar la llegada de la mercancía en destino"
              placeholder=""
              rows={4}
              value={documentacionData.notify || ""}
              onChange={(value) =>
                updateDocumentacionField("notify", value)
              }
            />
          </Box>
        </Flex>

        <Divider />

        {/* ========== SECCIÓN 1: Documentación Logística ========== */}
        <Box>
          <Heading as="h3">1. Documentación Logística</Heading>
          <Text format={{ fontWeight: "normal" }}>
            Configure los documentos de transporte y las instrucciones para el
            envío
          </Text>
          <Flex direction="column" gap="small">
            <Box>
              <Toggle
                label="Desglosar cotización por contenedor"
                name="desagregar_cotizacion"
                size="sm"
                checked={documentacionData.desagregarCotizacion || false}
                onChange={(checked) =>
                  updateDocumentacionField("desagregarCotizacion", checked)
                }
              />
            </Box>

            <Box>
              <Text format={{ fontWeight: "bold" }}>Documentos a Generar</Text>
              <Text format={{ fontWeight: "normal" }}>
                Seleccione los documentos que se generarán para el envío
              </Text>
              <Flex direction="column" gap="extra-small">
                {(documentacionData.documentosAGenerar?.options || []).map(
                  (option) => {
                    const currentValue =
                      documentacionData.documentosAGenerar?.value || "";
                    const selectedValues = currentValue
                      ? currentValue.split(";")
                      : [];
                    const isChecked = selectedValues.includes(option.value);

                    return (
                      <Checkbox
                        key={option.value}
                        name={`doc_${option.value}`}
                        value={option.value}
                        checked={isChecked}
                        onChange={(checked) =>
                          handleDocumentosCheckboxChange(option.value, checked)
                        }
                      >
                        {option.label}
                      </Checkbox>
                    );
                  }
                )}
              </Flex>
            </Box>

            {/* Mostrar "Texto BL" solo si BL está seleccionado */}
            {(() => {
              const currentValue =
                documentacionData.documentosAGenerar?.value || "";
              const selectedValues = currentValue
                ? currentValue.split(";")
                : [];
              const isBLSelected =
                selectedValues.includes("bl") || selectedValues.includes("BL");

              return isBLSelected ? (
                <Box>
                  <TextArea
                    label="Texto para Bill of Lading (BL)"
                    name="texto_bl"
                    description="Descripción de la mercancía que aparecerá en el conocimiento de embarque"
                    placeholder=""
                    rows={2}
                    value={documentacionData.textoEnBl || ""}
                    onChange={(value) =>
                      updateDocumentacionField("textoEnBl", value)
                    }
                  />
                </Box>
              ) : null;
            })()}

            {/* Fila: Comentarios Logística | Comentarios Transitario */}
            <Flex direction="row" gap="small">
              <Box flex={1}>
                <TextArea
                  label="Comentarios de Logística"
                  name="comentarios_logistica"
                  description="Notas internas del departamento de logística"
                  placeholder=""
                  rows={2}
                  value={documentacionData.comentariosLogistica || ""}
                  onChange={(value) =>
                    updateDocumentacionField("comentariosLogistica", value)
                  }
                />
              </Box>

              <Box flex={1}>
                <TextArea
                  label="Instrucciones para el Transitario"
                  name="comentarios_transitario"
                  description="Indicaciones específicas para el agente de transporte"
                  placeholder=""
                  rows={2}
                  value={documentacionData.comentariosTransitario || ""}
                  onChange={(value) =>
                    updateDocumentacionField("comentariosTransitario", value)
                  }
                />
              </Box>
            </Flex>
          </Flex>
        </Box>

        <Divider />

        {/* ========== SECCIÓN 1.1: Certificado de Origen ========== */}
        <Box>
          <Heading as="h3">1.1 Certificado de Origen</Heading>
          <Text format={{ fontWeight: "normal" }}>
            Datos requeridos para el certificado de origen a sellar en la Cámara
            de Comercio
          </Text>
          <Flex direction="column" gap="small">
            <Box>
              <TextArea
                label="Descripción de Mercancía"
                name="certificado_origen_mercancia"
                description="Descripción detallada de la mercancía para el certificado de origen"
                placeholder=""
                rows={4}
                value={documentacionData.certificadoOrigenMercancia || ""}
                onChange={(value) =>
                  updateDocumentacionField(
                    "certificadoOrigenMercancia",
                    value
                  )
                }
              />
            </Box>

            {/* Tercera fila: Observaciones */}
            <Box>
              <TextArea
                label="Observaciones"
                name="observaciones"
                description="Observaciones para el certificado de origen"
                placeholder=""
                rows={3}
                value={documentacionData.observaciones || ""}
                onChange={(value) =>
                  updateDocumentacionField("observaciones", value)
                }
              />
            </Box>
          </Flex>
        </Box>

        <Divider />

        {/* ========== SECCIÓN 2: Método de Pago y Textos ========== */}
        <Box>
          <Heading as="h3">2. Configuración de la Cotización</Heading>
          <Text format={{ fontWeight: "normal" }}>
            Defina el idioma, condiciones de pago y textos legales de la
            cotización
          </Text>
          <Flex direction="column" gap="small">
            <Box>
              <Select
                label="Idioma de la Cotización"
                name="idioma_cotizacion"
                description="Seleccione el idioma en el que se generará la documentación comercial"
                placeholder="Seleccionar..."
                value={documentacionData.idiomaCotizacion?.value || ""}
                onChange={handleIdiomaChange}
                options={documentacionData.idiomaCotizacion?.options || []}
                required={true}
              />
            </Box>

            <Box>
              <TextArea
                label="Texto Base de la Oferta Comercial"
                name="texto_base_oferta"
                description="Introducción y condiciones generales de la oferta (se carga automáticamente al seleccionar idioma)"
                placeholder=""
                value={documentacionData.textoBaseOferta || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoBaseOferta", value)
                }
                rows={10}
                readOnly={isLoadingTextoBaseOferta}
                required={true}
              />
            </Box>

            <Box>
              <Select
                label="Plantilla de Método de Pago"
                name="metodo_pago_plantilla"
                description="Seleccione el tipo de condición de pago (transferencia, L/C, etc.)"
                placeholder="Seleccionar..."
                value={documentacionData.metodoPagoPlantilla?.value || ""}
                onChange={handleMetodoPagoChange}
                options={documentacionData.metodoPagoPlantilla?.options || []}
                required={true}
              />
            </Box>

            <Box>
              <TextArea
                label="Condiciones de Pago"
                name="texto_forma_pago"
                description="Texto con los términos y plazos de pago (se carga automáticamente al seleccionar plantilla)"
                placeholder=""
                value={documentacionData.textoFormaPago || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoFormaPago", value)
                }
                rows={3}
                readOnly={isLoadingTextoFormaPago}
                required={true}
              />
            </Box>

            <Box>
              <TextArea
                label="Información Adicional de Pago"
                name="texto_adicional_metodo_pago"
                description="Notas complementarias o aclaraciones sobre las condiciones de pago (opcional)"
                placeholder=""
                rows={3}
                value={documentacionData.textoAdicionalMetodoPago || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoAdicionalMetodoPago", value)
                }
              />
            </Box>

            <Box>
              <Select
                label="Banco para Recepción de Pagos"
                name="banco_cobro"
                description="Seleccione la cuenta bancaria donde se recibirán los pagos"
                placeholder="Seleccionar..."
                value={documentacionData.bancoCobro?.value || ""}
                onChange={handleBancoCobroChange}
                options={documentacionData.bancoCobro?.options || []}
                required={true}
              />
            </Box>

            <Box>
              <TextArea
                label="Datos Bancarios"
                name="texto_banco"
                description="Información completa de la cuenta bancaria (se carga automáticamente al seleccionar banco)"
                placeholder=""
                value={documentacionData.textoBanco || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoBanco", value)
                }
                rows={10}
                readOnly={isLoadingTextoBanco}
                required={true}
              />
            </Box>

          </Flex>
        </Box>

        <ButtonRow>
          <Button
            variant="secondary"
            onClick={() => onNavigate("servicios")}
            disabled={isSubmitting}
          >
            Anterior
          </Button>
          <Button
            variant="primary"
            onClick={handleGuardarDocumentacion}
            disabled={isNextDisabled}
            loading={isSubmitting}
          >
            {isSubmitting ? LOADING_MESSAGES.SAVING : "Siguiente"}
          </Button>
        </ButtonRow>
      </Flex>
    </Box>
  );
};

export default DocumentacionTab;
