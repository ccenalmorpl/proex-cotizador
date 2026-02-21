import {
  Box,
  Heading,
  Flex,
  Divider,
  ButtonRow,
  Button,
  Select,
  TextArea,
  Text,
  Checkbox,
  Alert,
  LoadingSpinner,
  Toggle,
} from "@hubspot/ui-extensions";
import { useState, useEffect } from "react";
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

const DocumentacionTab = ({
  context,
  onNavigate,
  documentacionData,
  setDocumentacionData,
  articulosSeleccionados = [],
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

      // Verificar si necesitamos cargar texto de banco
      const necesitaTextoBanco = bancoClave && !documentacionData.textoBanco;
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
        const claveConPrefijo =
          bancoClave.startsWith("banco_") || bancoClave === "cotizacion_rapida"
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
          idioma,
          claveConPrefijo,
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

  // Helper para actualizar el estado compartido
  const updateDocumentacionField = (field, value) => {
    setDocumentacionData({
      ...documentacionData,
      [field]: value,
    });
  };

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
        textoInfoExpediente: "",
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
      textoInfoExpediente: documentacionData.textoInfoExpediente || "",
      textoBaseOferta:
        textoBaseOferta || documentacionData.textoBaseOferta || "",
    });

    // Desactivar loading
    setIsLoadingTextoBanco(false);
    setIsLoadingTextoFormaPago(false);
    setIsLoadingTextoBaseOferta(false);
  };

  // Handler para cambio de banco de cobro
  const handleBancoCobroChange = async (value) => {
    // Si hay idioma seleccionado, obtener el texto del banco desde HubDB
    const idioma = documentacionData.idiomaCotizacion?.value;

    if (idioma && value) {
      // Activar loading
      setIsLoadingTextoBanco(true);

      // Agregar prefijo "banco_" a la clave antes de consultar HubDB
      const claveConPrefijo = value.startsWith("banco_")
        ? value
        : `banco_${value}`;

      // Determinar si es cotización rápida y preparar opciones
      let fetchOptions = {};
      if (value === "cotizacion_rapida") {
        fetchOptions = {
          grupo: "bancos",
          subgrupo: "cotizacion_rapida",
        };
      }

      const textoBanco = await fetchTextosCotizacion(
        idioma,
        claveConPrefijo,
        fetchOptions
      );

      // Actualizar ambos campos en una sola llamada para evitar re-renders conflictivos
      setDocumentacionData({
        ...documentacionData,
        bancoCobro: {
          value: value,
          options: documentacionData.bancoCobro?.options || [],
        },
        textoBanco: textoBanco || documentacionData.textoBanco || "",
      });

      // Desactivar loading
      setIsLoadingTextoBanco(false);
    } else if (!value) {
      // Si se limpia el selector, vaciar el texto
      setDocumentacionData({
        ...documentacionData,
        bancoCobro: {
          value: value,
          options: documentacionData.bancoCobro?.options || [],
        },
        textoBanco: "",
      });
    } else {
      // Solo actualizar el banco si no hay idioma
      updateDocumentacionField("bancoCobro", {
        value: value,
        options: documentacionData.bancoCobro?.options || [],
      });
    }
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

          // Documentación Logística
          tramitar_carga: documentacionData.tramitarCarga || null,
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

          // Método de Pago & Textos
          metodo_pago_plantilla:
            documentacionData.metodoPagoPlantilla?.value || null,
          banco_cobro: documentacionData.bancoCobro?.value || null,
          texto_adicional_metodo_pago:
            documentacionData.textoAdicionalMetodoPago || null,
          texto_firma_aceptacion_cliente:
            documentacionData.textoFirmaAceptacionCliente || null,

          // Textos Generados - Convertir a HTML antes de guardar
          texto_banco: documentacionData.textoBanco
            ? convertTextToHTML(documentacionData.textoBanco)
            : null,
          texto_forma_pago: documentacionData.textoFormaPago
            ? convertTextToHTML(documentacionData.textoFormaPago)
            : null,
          texto_informacion_expediente: documentacionData.textoInfoExpediente
            ? convertTextToHTML(documentacionData.textoInfoExpediente)
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

      {/* Mostrar error si existe */}
      {saveError && (
        <Alert title="Error al guardar" variant="error">
          {saveError}
        </Alert>
      )}

      <Flex direction="column" gap="medium">
        {/* Dirección Fiscal */}
        <Box>
          <TextArea
            label="Dirección Fiscal"
            name="direccion_fiscal"
            description="Dirección fiscal completa de la empresa (incluir calle, número, CP, ciudad y país)"
            placeholder="Ej: Calle Principal 123, 28001 Madrid, España"
            rows={3}
            value={documentacionData.direccionFiscal || ""}
            onChange={(value) =>
              updateDocumentacionField("direccionFiscal", value)
            }
            required={true}
          />
        </Box>

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
                label="Tramitar Carga"
                name="tramitar_carga"
                size="sm"
                checked={documentacionData.tramitarCarga || false}
                onChange={(checked) =>
                  updateDocumentacionField("tramitarCarga", checked)
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
                    placeholder="Ej: Frutas y vegetales frescos, envasados en cajas de cartón..."
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
                  placeholder="Observaciones sobre el manejo, almacenaje, etc..."
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
                  placeholder="Instrucciones sobre despacho aduanero, manejo especial, etc..."
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
            {/* Primera fila: Importador | Exportador */}
            <Flex direction="row" gap="small">
              <Box flex={1}>
                <TextArea
                  label="Consignee (Importador)"
                  name="consignee"
                  description="Nombre y dirección completa del destinatario/consignatario"
                  placeholder="Empresa XYZ S.L.&#10;Calle Comercio 45&#10;08001 Barcelona, España"
                  rows={4}
                  value={documentacionData.consignee || ""}
                  onChange={(value) =>
                    updateDocumentacionField("consignee", value)
                  }
                />
              </Box>

              <Box flex={1}>
                <TextArea
                  label="Shipper (Exportador)"
                  name="shipper"
                  description="Nombre y dirección completa del remitente/exportador"
                  placeholder="Proex Foods S.A.&#10;Calle Industria 12&#10;46001 Valencia, España"
                  rows={4}
                  value={documentacionData.shipper || ""}
                  onChange={(value) =>
                    updateDocumentacionField("shipper", value)
                  }
                />
              </Box>
            </Flex>

            {/* Segunda fila: Notificar a | Mercancía CO */}
            <Flex direction="row" gap="small">
              <Box flex={1}>
                <TextArea
                  label="Notify (Notificar a)"
                  name="notify"
                  description="Parte a quien notificar la llegada de la mercancía en destino"
                  placeholder="Empresa de contacto&#10;Persona responsable&#10;Teléfono y email"
                  rows={4}
                  value={documentacionData.notify || ""}
                  onChange={(value) =>
                    updateDocumentacionField("notify", value)
                  }
                />
              </Box>

              <Box flex={1}>
                <TextArea
                  label="Descripción de Mercancía"
                  name="certificado_origen_mercancia"
                  description="Descripción detallada de la mercancía para el certificado de origen"
                  placeholder="Ej: Frutas frescas (naranjas, mandarinas) de origen España, código arancelario 0805..."
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
            </Flex>
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
            {/* Selector Global de Idioma */}
            <Box>
              <Select
                label="Idioma de la Cotización"
                name="idioma_cotizacion"
                description="Seleccione el idioma en el que se generará la documentación comercial"
                placeholder="Seleccione el idioma..."
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
                placeholder="Seleccione un idioma para cargar la plantilla o escriba el texto personalizado..."
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
                placeholder="Seleccione una plantilla de pago..."
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
                placeholder="Seleccione una plantilla para cargar el texto o escriba personalizado..."
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
                placeholder="Ej: Descuentos por pronto pago, penalizaciones por mora, etc..."
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
                placeholder="Seleccione el banco de cobro..."
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
                placeholder="Seleccione un banco para cargar los datos o escriba personalizado..."
                value={documentacionData.textoBanco || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoBanco", value)
                }
                rows={10}
                readOnly={isLoadingTextoBanco}
                required={true}
              />
            </Box>

            <Divider />

            <Box>
              <Heading as="h4">2.1 Textos Complementarios (Opcional)</Heading>
              <Text format={{ fontWeight: "normal" }}>
                Información adicional que puede incluirse en la cotización según
                necesidad
              </Text>
            </Box>

            <Box>
              <TextArea
                label="Información del Expediente"
                name="texto_info_expediente"
                description="Detalles específicos, condiciones particulares o notas sobre este expediente (opcional)"
                placeholder="Ej: Envío urgente, requiere temperatura controlada, mercancía perecedera..."
                value={documentacionData.textoInfoExpediente || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoInfoExpediente", value)
                }
                rows={3}
              />
            </Box>

            <Box>
              <TextArea
                label="Texto de Firma y Aceptación"
                name="texto_firma_aceptacion_cliente"
                description="Cláusula legal para la firma y aceptación del cliente (opcional)"
                placeholder="Ej: El cliente acepta todas las condiciones establecidas en esta oferta..."
                rows={3}
                value={documentacionData.textoFirmaAceptacionCliente || ""}
                onChange={(value) =>
                  updateDocumentacionField("textoFirmaAceptacionCliente", value)
                }
              />
            </Box>
          </Flex>
        </Box>

        {/* Botones de navegación */}
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
