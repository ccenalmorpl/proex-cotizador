# Cotizador PROEX - HubSpot CRM Card 🎴

> Extensión de HubSpot CRM para gestión avanzada de cotizaciones de exportación/importación

![Status](https://img.shields.io/badge/status-active-success.svg)
![HubSpot](https://img.shields.io/badge/HubSpot-CRM_Card-ff7a59.svg)
![React](https://img.shields.io/badge/React-18.2.0-61dafb.svg)
![TypeScript](https://img.shields.io/badge/TypeScript-5.3.3-blue.svg)

---

## 📋 Tabla de Contenido

- [Descripción General](#-descripción-general)
- [Tecnologías y Stack](#-tecnologías-y-stack)
- [Arquitectura](#-arquitectura)
- [Requisitos Previos](#-requisitos-previos)
- [Instalación](#-instalación)
- [Configuración](#-configuración)
- [Uso](#-uso)
- [Funcionalidades por Tab](#-funcionalidades-por-tab)
- [Scripts Disponibles](#-scripts-disponibles)
- [Estructura del Proyecto](#-estructura-del-proyecto)
- [Desarrollo](#-desarrollo)
- [Deployment](#-deployment)

---

## 🎯 Descripción General

**Cotizador PROEX** es una CRM Card personalizada para HubSpot que permite a los equipos comerciales gestionar cotizaciones complejas de exportación/importación directamente desde el CRM, sin salir del contexto del Deal.

### ¿Qué hace el proyecto?

La extensión proporciona una interfaz multi-pestaña que permite:

- Configurar datos del cliente (contacto, empresa, condiciones comerciales)
- Gestionar artículos de fabricantes y proveedores
- Calcular servicios asociados (comisiones, embalajes, seguros)
- Configurar contenedores y distribución de mercancía
- Definir condiciones de tránsito y rutas
- Gestionar documentación y textos de cotización multiidioma
- Revisar márgenes y negociar precios finales

### ¿Qué problema resuelve?

- **Dispersión de información**: Centraliza toda la información de cotización en un único lugar dentro del Deal
- **Cálculos manuales**: Automatiza cálculos complejos de márgenes, comisiones, servicios y distribución en contenedores
- **Consistencia de datos**: Garantiza que los datos del CRM estén sincronizados con los line items y custom objects
- **Textos multiidioma**: Gestiona plantillas de texto en español, inglés y francés desde HubDB
- **Experiencia de usuario**: Proporciona una UX moderna y fluida sin recargar el navegador

### Para quién va dirigido

- **Equipo comercial de PROEX**: Usuarios principales que crean y gestionan cotizaciones
- **Desarrolladores**: Mantienen y extienden la funcionalidad de la extensión
- **Administradores de HubSpot**: Configuran permisos y propiedades personalizadas

### Contexto

Proyecto desarrollado para **PROEX** (empresa especializada en exportación/importación) como parte de su estrategia de digitalización y automatización de procesos comerciales en HubSpot CRM.

---

## 🛠️ Tecnologías y Stack

### Lenguajes
- **TypeScript 5.3.3** - Type safety en componentes React
- **JavaScript (JSX)** - Componentes React

### Frameworks y Librerías
- **React 18.2.0** - Biblioteca UI
- **HubSpot UI Extensions SDK** - Framework oficial de HubSpot para CRM Cards
  - `hubspot.fetch()` - HTTP client
  - `hubspot.crm.*` - APIs de CRM
  - UI Components propietarios (Button, Input, Table, etc.)

### Herramientas de desarrollo
- **ESLint 9.37.0** - Linting y code quality
- **Vite** - Build tool (integrado en HubSpot CLI)
- **HubSpot CLI** - Herramienta de desarrollo y deployment

### Integraciones
- **Cotizador PROEX Microservice** - Backend API REST en FastAPI
- **HubSpot CRM API** - Acceso a deals, contacts, companies, line items
- **HubSpot Custom Objects API** - Gestión de proveedores, artículos, servicios, contenedores
- **HubDB API** - Textos de cotización multiidioma

---

## 🏗️ Arquitectura

### Patrón de diseño

La extensión sigue el patrón **Component-Based Architecture** con gestión de estado local mediante React hooks:

```
┌────────────────────────────────────────────┐
│         Cotizador.tsx (Main)               │
│      (Tab Navigation & State)              │
└────────────────┬───────────────────────────┘
                 │
     ┌───────────┴───────────┐
     │                       │
┌────▼─────┐          ┌─────▼────────┐
│   Tabs   │          │   Utilities  │
│ (8 tabs) │◄────────►│   & Hooks    │
└────┬─────┘          └──────────────┘
     │
     ├─ ClienteTab.jsx        ┌──────────────┐
     ├─ ArticulosTab.jsx      │  Backend API │
     ├─ ServiciosTab.jsx ────►│  (FastAPI)   │
     ├─ ContenedoresTab.jsx   └──────────────┘
     ├─ TransitarioTab.jsx           ▲
     ├─ DocumentacionTab.jsx         │
     ├─ NegociacionTab.jsx    ┌──────┴────────┐
     └─ RevisionTab.jsx       │  HubSpot CRM  │
                              │   + HubDB     │
                              └───────────────┘
```

### Principios aplicados

- **Component Composition**: Componentes pequeños y reutilizables
- **Single Responsibility**: Cada tab gestiona una única sección de la cotización
- **Separation of Concerns**:
  - UI en componentes
  - Lógica de negocio en utils
  - Constantes separadas
  - Custom hooks para lógica reutilizable
- **DRY (Don't Repeat Yourself)**: Utilidades compartidas para API calls, validación, etc.
- **Optimización de Performance**: `useCallback`, `useMemo`, debouncing

### Flujo de datos

1. Usuario navega entre tabs
2. Al cambiar de tab, se carga data desde el backend/HubSpot
3. Usuario modifica campos
4. Validación en tiempo real
5. Al hacer clic en "Siguiente", se guardan cambios
6. Backend actualiza HubSpot CRM
7. Navegación al siguiente tab

---

## ✅ Requisitos Previos

### Software necesario

- **Node.js 18+** (recomendado 20 LTS)
- **npm** o **yarn**
- **HubSpot CLI**
  ```bash
  npm install -g @hubspot/cli
  ```

### Cuentas y accesos

- **Cuenta HubSpot** (Professional o Enterprise)
- **Private App Token** con permisos:
  - CRM Objects (read/write)
  - CRM Custom Objects (read/write)
  - HubDB (read)
- **Backend API URL** del microservicio FastAPI

### Custom Objects requeridos en HubSpot

La extensión requiere los siguientes custom objects configurados:

1. **Proveedores** (2-43108800)
2. **Artículos** (2-46244677)
3. **Servicios** (2-48120663)
4. **Contenedores** (2-48191945)

### HubDB Tables requeridas

- **textos_cotizaciones** - Tabla con plantillas de texto multiidioma

---

## 📦 Instalación

### 1. Clonar el repositorio

```bash
git clone <repository-url>
cd cotizador-proex-card
```

### 2. Instalar HubSpot CLI

```bash
npm install -g @hubspot/cli
```

### 3. Autenticar con HubSpot

```bash
hs auth
```

Sigue las instrucciones para autenticarte con tu cuenta de HubSpot.

### 4. Instalar dependencias del proyecto

```bash
cd src/app/cards
npm install
```

---

## ⚙️ Configuración

### Configurar API endpoint del backend

Edita `src/app/cards/config.js`:

```javascript
const BACKEND_BASE_URL = "https://your-backend-api.com";

export { BACKEND_BASE_URL };
```

### Configurar hsproject.json

El archivo `hsproject.json` en la raíz define la configuración del proyecto:

```json
{
  "name": "cotizador-proex-card",
  "srcDir": "src",
  "platformVersion": "2025.2"
}
```

### Configurar card metadata

Edita `src/app/cards/card-hsmeta.json` para configurar:
- Título de la card
- Descripciones
- Objeto CRM asociado (deals)

---

## 🚀 Uso

### Modo desarrollo local

```bash
# Desde el directorio raíz del proyecto
hs project dev
```

Esto iniciará:
- Servidor de desarrollo con hot reload
- Proxy para pruebas en HubSpot sandbox

### Acceder a la extensión

1. Ir a HubSpot CRM
2. Abrir cualquier Deal
3. La card "Cotizador PROEX" aparecerá en el panel lateral

### Navegación entre tabs

La extensión tiene 8 pestañas navegables:

1. **Cliente** → Configuración inicial
2. **Artículos** → Selección de productos
3. **Servicios** → Comisiones y servicios adicionales
4. **Contenedores** → Distribución de mercancía
5. **Transitario** → Condiciones de tránsito y rutas
6. **Documentación** → Textos y plantillas
7. **Negociación** → Ajuste de márgenes
8. **Revisión** → Vista final de la cotización

---

## 📑 Funcionalidades por Tab

### 1. Cliente Tab

**Propósito**: Configurar datos del cliente y condiciones comerciales iniciales

**Funcionalidades**:
- Edición de datos de contacto (nombre, apellido, idioma)
- Configuración de empresa (país, canal de origen)
- Selección de Incoterm
- Configuración de lugar de carga
- Datos de comisión (código intermediario, tipo, importe)

**Validaciones**:
- Firstname y lastname requeridos
- Código intermediario requerido si hay comisión

### 2. Artículos Tab

**Propósito**: Gestionar line items de tipo "fabricante"

**Funcionalidades**:
- Tabla de artículos con:
  - Código (cardcode)
  - Nombre
  - Cantidad
  - Precio de compra
  - Margen
  - IVA repercutido
- Asociación con proveedores fabricantes
- Cálculo automático de precios de venta

**Validaciones**:
- Al menos un artículo requerido
- Cantidades > 0
- Precios válidos

### 3. Servicios Tab

**Propósito**: Gestionar servicios adicionales (comisiones, embalajes, seguros, etc.)

**Funcionalidades**:
- Tabla de servicios con:
  - Proveedor
  - Tipo de servicio
  - Cantidad
  - Precio de compra
  - Margen
  - IVA
- Agregar/eliminar servicios
- Cálculo automático de totales

### 4. Contenedores Tab

**Propósito**: Configurar distribución de mercancía en contenedores

**Funcionalidades**:
- Selección de tipos de contenedor (20', 40', 40'HC)
- Configuración de paletizado
- Distribución automática de artículos en contenedores
- Visualización de capacidad y utilización
- Cálculo de costes por contenedor

**Algoritmo**:
- Distribución proporcional por volumen/peso
- Respeto de límites de capacidad
- Optimización de espacio

### 5. Transitario Tab

**Propósito**: Configurar condiciones de tránsito internacional

**Funcionalidades**:
- Selección de país de origen y destino
- Configuración de rutas (marítima, aérea, terrestre)
- Fechas de salida y llegada estimadas
- Condiciones de transporte
- Asociación con transitarios

### 6. Documentación Tab

**Propósito**: Gestionar textos y documentación de la cotización

**Funcionalidades**:
- Selección de idioma de impresión
- Configuración de textos:
  - Texto de oferta base
  - Texto de forma de pago
  - Texto de banco
  - Texto de cotización rápida
- Carga de textos desde HubDB
- Preview de textos seleccionados
- Configuración de documentos a generar

**Integración con HubDB**:
- Consulta de tabla `textos_cotizaciones`
- Filtrado por idioma y clave
- Inserción automática en campos del deal

### 7. Negociación Tab

**Propósito**: Ajustar márgenes finales antes de enviar cotización

**Funcionalidades**:
- Tabla de artículos únicos con:
  - Precio de compra
  - Margen actual
  - Margen negociado (editable)
  - IVA repercutido
- Cálculo de precio de venta final
- Visualización de impacto en margen total
- Guardado masivo de cambios

**Lógica**:
- Actualización de `margen_unitario` en line items
- Recalculo automático de precios de venta
- Propagación a todos los line items del mismo producto

### 8. Revisión Tab

**Propósito**: Vista final de toda la cotización antes de enviar

**Funcionalidades**:
- Resumen de cliente
- Lista de artículos con totales
- Lista de servicios con totales
- Contenedores configurados
- Condiciones de tránsito
- Textos de documentación
- Totales generales:
  - Subtotal
  - IVA
  - Total
  - Margen total

---

## 🔧 Scripts Disponibles

### Desarrollo

```bash
# Iniciar servidor de desarrollo
hs project dev

# Compilar proyecto
hs project build

# Upload a HubSpot
hs project upload

# Modo watch (auto-upload en cambios)
hs project watch
```

### Testing y calidad de código

```bash
# Lint con ESLint
npm run lint

# Fix automático de issues de linting
npm run lint:fix

# Type checking (TypeScript)
npx tsc --noEmit
```

### Deployment

```bash
# Deploy completo a producción
hs project upload --account=production-account-id

# Deploy a sandbox
hs project upload --account=sandbox-account-id
```

---

## 📁 Estructura del Proyecto

```
cotizador-proex-card/
│
├── hsproject.json                # Configuración del proyecto HubSpot
├── src/
│   └── app/
│       └── cards/
│           ├── Cotizador.tsx            # Componente principal
│           ├── card-hsmeta.json         # Metadata de la card
│           ├── config.js                # Configuración (API URL)
│           ├── package.json
│           │
│           ├── tabs/                    # Componentes de pestañas
│           │   ├── ClienteTab.jsx       # Tab 1: Cliente
│           │   ├── ArticulosTab.jsx     # Tab 2: Artículos
│           │   ├── ServiciosTab.jsx     # Tab 3: Servicios
│           │   ├── ContenedoresTab.jsx  # Tab 4: Contenedores
│           │   ├── TransitarioTab.jsx   # Tab 5: Transitario
│           │   ├── DocumentacionTab.jsx # Tab 6: Documentación
│           │   ├── NegociacionTab.jsx   # Tab 7: Negociación
│           │   └── RevisionTab.jsx      # Tab 8: Revisión
│           │
│           ├── utils/                   # Utilidades compartidas
│           │   ├── api.utils.js         # API calls centralizadas
│           │   └── validation.utils.js  # Validaciones
│           │
│           ├── hooks/                   # Custom React hooks
│           │   └── useDebounce.js       # Hook de debouncing
│           │
│           ├── constants/               # Constantes
│           │   ├── common.constants.js  # Constantes comunes
│           │   └── containers.constants.js # Constantes de contenedores
│           │
│           ├── types/                   # TypeScript types
│           │   └── ...
│           │
│           └── dist/                    # Build output (generado)
│
├── CLAUDE.md                     # Documentación para Claude AI
├── REFACTORING_SUMMARY.md        # Resumen de refactoring aplicado
├── UI-DOCS.md                    # Documentación de UI components
└── README.md                     # Este archivo
```

### Descripción de directorios críticos

- **`tabs/`**: Contiene los 8 componentes de pestaña. Cada uno es responsable de una sección de la cotización.

- **`utils/`**: Funciones auxiliares reutilizables:
  - `api.utils.js`: Wrapper de `hubspot.fetch()` con manejo de errores
  - `validation.utils.js`: Funciones de validación de formularios

- **`hooks/`**: Custom React hooks:
  - `useDebounce.js`: Implementa debouncing para inputs

- **`constants/`**: Constantes compartidas (mensajes de error, configuraciones, etc.)

- **`types/`**: Definiciones de tipos TypeScript para type safety

---

## 💻 Desarrollo

### Arquitectura de componentes

Cada tab sigue este patrón:

```jsx
// TabName.jsx
import React, { useState, useEffect, useCallback } from "react";
import { hubspot } from "@hubspot/ui-extensions";
import { fetchFromAPI } from "../utils/api.utils";

const TabName = ({ dealId, onNext, onPrevious }) => {
  // 1. Estado local
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // 2. Carga inicial
  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetchFromAPI(`/deals/${dealId}/endpoint`);
        setData(response.data);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [dealId]);

  // 3. Handlers con useCallback
  const handleSave = useCallback(async () => {
    // Lógica de guardado
  }, [data, dealId]);

  // 4. Render
  return (
    <div>
      {/* UI */}
    </div>
  );
};

export default TabName;
```

### Best practices aplicadas

1. **Imports organizados**: React → UI components → Utils → Constants
2. **Performance**: `useCallback` y `useMemo` donde corresponda
3. **Error handling**: Try-catch en todas las API calls
4. **Loading states**: Feedback visual durante operaciones asíncronas
5. **Validación**: Antes de permitir navegación al siguiente tab
6. **JSDoc comments**: En funciones complejas
7. **Nombres descriptivos**: Variables y funciones auto-documentadas

### Debugging

Para debugging en HubSpot:

```javascript
// Console logs visibles en DevTools
console.log("[ClienteTab] Loading data...", data);

// Logs de errores
console.error("[ClienteTab] Error fetching data:", error);
```

### Testing local

1. Usar `hs project dev` para hot reload
2. Probar en un deal de sandbox
3. Verificar Network tab para API calls
4. Revisar Console para errores

---

## 🚢 Deployment

### Pre-deployment checklist

- [ ] Tests pasados
- [ ] Linting sin errores
- [ ] Configuración de producción en `config.js`
- [ ] Backend API en producción funcionando
- [ ] Custom objects configurados en HubSpot
- [ ] HubDB tables pobladas

### Deploy a producción

```bash
# 1. Build del proyecto
hs project build

# 2. Upload a cuenta de producción
hs project upload --account=production-account-id

# 3. Verificar en HubSpot UI
```

### Rollback

Si algo falla:

```bash
# Revertir a versión anterior
hs project upload --account=production-account-id --version=previous
```

---

## 🎨 UI Components de HubSpot

La extensión utiliza los componentes oficiales de HubSpot UI Extensions:

- `Button` - Botones de acción
- `Input` - Campos de texto
- `Select` - Dropdowns
- `Table` - Tablas de datos
- `Flex` - Layout flexbox
- `Text` - Tipografía
- `Divider` - Separadores
- `Alert` - Mensajes de error/éxito
- `LoadingSpinner` - Indicadores de carga
- `Modal` - Ventanas modales (futuro)

Ver [UI-DOCS.md](./UI-DOCS.md) para documentación completa de componentes.

---

## 📝 Notas adicionales

### Limitaciones conocidas

1. **Rate limiting**: HubSpot API tiene límites de 100 calls/10 segundos
2. **Tamaño de payload**: Máximo 10MB por request
3. **Timeout**: Requests mayores a 30s pueden fallar
4. **Navegador**: Requiere navegadores modernos (Chrome, Firefox, Safari)

### Roadmap

- [ ] Soporte para múltiples monedas
- [ ] Exportación a PDF de cotizaciones
- [ ] Historial de cambios en cotización
- [ ] Notificaciones push en cambios críticos
- [ ] Modo offline con sincronización

---

## 🤝 Contribución

### Flujo de desarrollo

1. Crear rama desde `main`: `git checkout -b feature/nueva-funcionalidad`
2. Desarrollar y commitear cambios
3. Hacer push y crear Pull Request
4. Code review
5. Merge a `main`
6. Deploy automático

### Convenciones de código

- Usar ESLint configuration del proyecto
- Nombres de variables en camelCase
- Nombres de componentes en PascalCase
- Comentarios JSDoc en funciones públicas
- Commits descriptivos en inglés

---

## 📄 Licencia

Proyecto privado - PROEX

---

## 📞 Contacto y soporte

Para preguntas, reportar bugs o solicitar features:

- **Equipo de desarrollo PROEX**
- **Documentación interna**: Ver `CLAUDE.md` y `UI-DOCS.md`

---

**Versión**: 1.0.0
**Última actualización**: Noviembre 2024
**Platform Version**: HubSpot 2025.2
