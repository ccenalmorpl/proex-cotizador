# PROEX Commercial Quote Template

Plantilla de cotización comercial para HubSpot integrada con el sistema de cotizador PROEX.

## 📋 Descripción

Esta plantilla genera cotizaciones comerciales profesionales en formato PDF directamente desde HubSpot, mostrando:

- **Cabecera**: Logo PROEX, número de cotización, fecha
- **Información de referencia**: Puerto destino, naviera, contenedor, fechas ETD/ETA
- **Datos del cliente**: Empresa, dirección, país
- **Información del exportador/shipper**
- **Tabla de productos**: Código, descripción, cantidad, precio, total
- **Totales por Incoterm**: EXW, FOB, CFR, CIF con desglose
- **Estadísticas**: Total COLIS, pesos, tipo de contenedor, ocupación
- **Pie de página corporativo**: Información legal de PROEX

## 📦 Archivos del Theme

```
cms-quotes-theme-proex/
├── README.md                    # Este archivo
├── mock_payload.json            # Datos de ejemplo para testing
├── theme.json                   # Configuración del theme
├── fields.json                  # Campos personalizables
├── css/
│   └── basic.css               # Estilos de la plantilla
└── templates/
    ├── basic.html              # Template principal
    └── layout/
        └── base.html           # Layout base
```

## 🚀 Instalación en HubSpot

### Opción 1: Subir con HubSpot CLI (Recomendado)

1. **Instalar HubSpot CLI** (si no lo tienes):
   ```bash
   npm install -g @hubspot/cli
   ```

2. **Autenticar con tu cuenta de HubSpot**:
   ```bash
   hs auth
   ```

3. **Subir el theme**:
   ```bash
   cd /Users/traketo/Documents/Proyectos/Pymext/Proex/cotizador
   hs upload cms-quotes-theme-proex cms-quotes-theme-proex
   ```

4. **Verificar** que el theme aparece en:
   Settings → Objects → Quotes → Templates

### Opción 2: Copiar/Pegar Manual

1. Ve a **Settings → Objects → Quotes → Templates**
2. Crea un nuevo template o clona uno existente
3. Copia el contenido de `templates/basic.html` en el editor de HubSpot
4. Copia el contenido de `css/basic.css` en la sección de estilos
5. Guarda el template

## 🔗 Integración con el Backend

El template obtiene los datos del Deal mediante la propiedad `quote_data_payload`.

### Configuración en el Backend

En el archivo `backend/api/deals.py`, el endpoint `/api/v1/deals/{deal_id}/publish` debe:

1. **Almacenar el payload completo** en la propiedad del Deal:
   ```python
   deal_properties = {
       "quote_data_payload": json.dumps(quote_payload)
   }
   ```

2. **Crear la Quote en HubSpot** asociada al Deal:
   ```python
   quote_data = {
       "properties": {
           "hs_title": f"Cotización #{offer_number}",
           "hs_expiration_date": expiration_timestamp,
           "hs_language": language_code,
           "hs_status": "DRAFT"
       },
       "associations": [
           {
               "to": {"id": deal_id},
               "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 64}]
           }
       ]
   }
   ```

3. **Asociar con el template de PROEX**:
   - Busca el ID del template en HubSpot
   - Actualiza el template ID en el backend si es necesario

### Estructura del Payload Esperada

Ver el archivo `mock_payload.json` para la estructura completa. Principales secciones:

```json
{
  "header": {
    "offerNumber": "string",
    "date": "ISO date",
    "destinationPort": "string",
    "vessel": "string",
    "terms": "string (EXW|FOB|CFR|CIF|DAP)",
    "currency": "string (€|$)",
    "container": "string",
    "sealNumber": "string|null",
    "etd": "ISO date|null",
    "eta": "ISO date|null"
  },
  "cliente": { ... },
  "ruta": { ... },
  "lineItems": [ ... ],
  "precios": {
    "totalEXW": number,
    "fobExpenses": number,
    "totalFOB": number,
    "seafreightRate": number,
    "totalCFR": number,
    "insurance": number,
    "totalCIF": number
  },
  "estadisticas": {
    "totalColis": number,
    "netWeightKg": number,
    "grossWeightKg": number,
    "containerType": "string",
    "filledUpTo": number,
    "numContainers": number,
    "totalEuros": number
  },
  "documentacion": {
    "consignee": { ... },
    "shipper": { ... }
  },
  "textos": { ... }
}
```

## 📊 Mapeo de Variables

### Cabecera
| Campo Template | Fuente Payload | Comentario |
|----------------|----------------|------------|
| Número de cotización | `payload.header.offerNumber` | Deal ID |
| Fecha | `payload.header.date` | Formato: dd/mm/yy |
| Puerto destino | `payload.header.destinationPort` | De ruta |
| Naviera | `payload.header.vessel` | Nombre del buque |
| Terms | `payload.header.terms` | Incoterm |
| Contenedor | `payload.header.container` | Tipo de contenedor |
| ETD | `payload.header.etd` | Opcional |
| ETA | `payload.header.eta` | Opcional |

### Productos
| Columna | Fuente | Filtro |
|---------|--------|--------|
| CODE | `item.code` | `type == 'producto'` |
| ITEM | `item.itemName` | Descripción completa |
| COLIS | `item.colis` | Total de bultos |
| QTY | `item.quantity` | Cantidad |
| UNIT | `item.unit` | Unidad de medida |
| PRICE | `item.unitPrice` | Precio unitario |
| TOTAL | `item.total` | Total línea |

### Totales
| Campo | Fuente |
|-------|--------|
| PRECIO EX WORK | `payload.precios.totalEXW` |
| FOB EXPENSES | `payload.precios.fobExpenses` |
| TOTAL FOB | `payload.precios.totalFOB` |
| SEAFREIGHT RATE | `payload.precios.seafreightRate` |
| TOTAL CFR | `payload.precios.totalCFR` |
| INSURANCE | `payload.precios.insurance` |
| TOTAL CIF | `payload.precios.totalCIF` |

## 🎨 Personalización

### Colores Corporativos

Edita en `css/basic.css`:

```css
/* Color de cabecera */
.header-title h1 { color: #606c82; }

/* Color del total general */
.grand-total { background: #606c82; }

/* Botones */
.payment__button--checkout { background-color: #4C76B8; }
```

### Logo

El logo se carga mediante el módulo de HubSpot. Para cambiarlo:
1. Ve al template en HubSpot
2. Edita el módulo "logo"
3. Sube la nueva imagen

### Textos Fijos

El pie de página corporativo está en `templates/basic.html` línea 304-310.

## 🧪 Testing

### 1. Preview Local con Mock Data

Usa el archivo `mock_payload.json` para verificar que todos los campos se muestran correctamente.

### 2. Testing en HubSpot

1. Crea un Deal de prueba
2. Llena el cotizador con datos de ejemplo
3. Publica la cotización
4. Verifica que el PDF se genera correctamente
5. Descarga y revisa:
   - Todos los datos se muestran
   - El formato es correcto
   - La impresión se ve bien

### 3. Casos de Prueba

- [ ] Cotización con pocos productos (1-3)
- [ ] Cotización con muchos productos (>10)
- [ ] Con todos los campos opcionales llenos
- [ ] Con campos opcionales vacíos (ETD, ETA, Seal Number)
- [ ] Diferentes Incoterms (EXW, FOB, CFR, CIF, DAP)
- [ ] Diferentes monedas (€, $)
- [ ] Con intermediario / sin intermediario
- [ ] Diferentes idiomas

## 🔧 Solución de Problemas

### El payload no se muestra

**Problema**: El template muestra datos vacíos o "N/A"

**Solución**:
1. Verifica que `DEAL.quote_data_payload` contiene el JSON
2. En HubSpot, inspecciona la propiedad del Deal
3. Verifica que el JSON es válido (usa jsonlint.com)
4. Confirma que el Deal está asociado a la Quote

### Fechas no se formatean correctamente

**Problema**: Las fechas aparecen como "N/A" o en formato incorrecto

**Solución**:
1. Verifica que las fechas están en formato ISO 8601 en el payload
2. Ejemplo correcto: `"2026-02-11T00:00:00.000Z"`
3. El macro `format_date` usa `strtotime` de HubL

### Productos no se muestran

**Problema**: La tabla de productos está vacía

**Solución**:
1. Verifica que `payload.lineItems` contiene elementos
2. Confirma que los items tienen `type: 'producto'`
3. Revisa el loop en `basic.html` línea 188-200

### Estilos no se aplican

**Problema**: El PDF se ve sin formato

**Solución**:
1. Verifica que `basic.css` está enlazado correctamente
2. Revisa que la clase del body es `.hs-quotes--commercial`
3. Sube de nuevo el CSS a HubSpot

## 📝 Datos Pendientes

Algunos campos no están actualmente en el payload del cotizador:

### Para Agregar en el Futuro:
- **ETD** (Estimated Time of Departure): Agregar al formulario de ruta
- **ETA** (Estimated Time of Arrival): Agregar al formulario de ruta
- **Seal Number**: Agregar al formulario de contenedores
- **Booking Number**: Agregar al formulario de transitario
- **Gross Weight**: Calcular sumando tara del contenedor al net weight

Por ahora, estos campos mostrarán "N/A" si no están disponibles.

## 📞 Soporte

Para preguntas o problemas:
- Revisa este README
- Consulta la documentación de HubSpot Quote Templates
- Revisa los logs del backend en caso de errores

## 🔄 Actualización del Template

Para actualizar el template después de modificaciones:

```bash
# Desde el directorio del proyecto
cd /Users/traketo/Documents/Proyectos/Pymext/Proex/cotizador

# Subir cambios
hs upload cms-quotes-theme-proex cms-quotes-theme-proex --mode=publish
```

O manualmente copiando y pegando los cambios en HubSpot.

## ✅ Checklist de Implementación

- [ ] Theme subido a HubSpot
- [ ] Template visible en Settings → Quotes → Templates
- [ ] Backend configurado para guardar `quote_data_payload`
- [ ] Asociación Deal-Quote funcionando
- [ ] Template ID actualizado en el backend
- [ ] Prueba con datos reales exitosa
- [ ] PDF descargable correctamente
- [ ] Impresión verificada
- [ ] Logo PROEX cargado
- [ ] Texto legal verificado

---

**Versión**: 1.0.0
**Fecha**: 11 de Febrero de 2026
**Autor**: Claude Code (Anthropic)
**Cliente**: PROEX DRINKS GROUP S.A.
