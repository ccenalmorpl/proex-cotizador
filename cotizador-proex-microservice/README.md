# Cotizador PROEX Microservice 🚀

> FastAPI microservice backend para el sistema de cotización de PROEX en HubSpot

![Status](https://img.shields.io/badge/status-active-success.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg)
![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange.svg)

---

## 📋 Tabla de Contenido

- [Descripción General](#-descripción-general)
- [Tecnologías y Stack](#-tecnologías-y-stack)
- [Arquitectura](#-arquitectura)
- [Requisitos Previos](#-requisitos-previos)
- [Instalación](#-instalación)
- [Configuración](#-configuración)
- [Uso](#-uso)
- [API Endpoints](#-api-endpoints)
- [Scripts Disponibles](#-scripts-disponibles)
- [Estructura del Proyecto](#-estructura-del-proyecto)
- [Deployment](#-deployment)

---

## 🎯 Descripción General

**Cotizador PROEX Microservice** es un backend API REST desarrollado en FastAPI que actúa como intermediario entre la extensión HubSpot CRM Card y la API de HubSpot. Proporciona endpoints optimizados para gestionar deals, artículos, servicios, contenedores, tránsito y documentación de cotizaciones.

### ¿Qué problema resuelve?

- **Centralización de lógica de negocio**: Toda la lógica compleja de cálculos, validaciones y transformaciones está centralizada en un único servicio.
- **Optimización de llamadas a HubSpot**: Reduce la cantidad de llamadas directas desde el frontend mediante agregación y caching.
- **Gestión de datos complejos**: Maneja relaciones entre deals, line items, custom objects (proveedores, artículos, servicios, contenedores) y HubDB tables.
- **Textos dinámicos de cotización**: Integración con HubDB para obtener plantillas de texto multiidioma.

### Para quién va dirigido

- Equipo comercial de PROEX que utiliza HubSpot CRM
- Desarrolladores que mantienen la extensión HubSpot
- Equipo DevOps para deployment en AWS Lambda

### Contexto

Proyecto desarrollado para **PROEX** (empresa de exportación/importación) como parte de su stack de automatización comercial en HubSpot CRM.

---

## 🛠️ Tecnologías y Stack

### Lenguajes
- **Python 3.11+**

### Frameworks
- **FastAPI 0.115.0** - Web framework moderno y de alto rendimiento
- **Pydantic 2.10.0** - Validación de datos y settings management
- **Uvicorn 0.32.0** - ASGI server

### Integraciones
- **HubSpot API Client 11.0.0** - SDK oficial de HubSpot
- **Requests 2.32.3** - HTTP client para llamadas a HubDB y endpoints REST

### Testing
- **pytest 8.3.3** - Framework de testing
- **httpx 0.27.2** - Cliente HTTP asíncrono para testing

### Infraestructura
- **AWS Lambda** - Serverless compute
- **Amazon ECR** - Container registry
- **Docker** - Containerización
- **Mangum 0.17.0** - Adaptador FastAPI → AWS Lambda

### Herramientas de desarrollo
- **python-dotenv** - Gestión de variables de entorno
- **python-json-logger** - Logging estructurado

---

## 🏗️ Arquitectura

### Patrón de diseño

El microservicio sigue una arquitectura en capas (Layered Architecture):

```
┌─────────────────────────────────────────┐
│         FastAPI Application             │
│              (main.py)                  │
└─────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
   ┌────▼────┐          ┌─────▼─────┐
   │ Routes  │          │  Models   │
   │  Layer  │◄────────►│  (Pydantic)│
   └────┬────┘          └───────────┘
        │
   ┌────▼────────┐
   │  Services   │
   │   Layer     │
   └────┬────────┘
        │
   ┌────▼─────────┐
   │  HubSpot API │
   │   + HubDB    │
   └──────────────┘
```

### Principios

- **Separation of Concerns**: Separación clara entre routes, services, models y utils
- **Dependency Injection**: Uso de FastAPI's dependency injection para services
- **Single Responsibility**: Cada módulo tiene una única responsabilidad
- **DRY (Don't Repeat Yourself)**: Funciones helper reutilizables
- **Type Safety**: Uso exhaustivo de type hints y Pydantic models

### Componentes principales

1. **Routes** (`app/routes/`): Definición de endpoints REST
2. **Models** (`app/models/schemas.py`): Modelos Pydantic para request/response
3. **Services** (`app/services/`): Lógica de negocio y comunicación con APIs externas
4. **Core** (`app/core/`): Configuración centralizada
5. **Utils** (`app/utils/`): Funciones auxiliares (conversiones, property helpers)

---

## ✅ Requisitos Previos

### Software necesario

- **Python 3.11 o superior**
- **pip** (gestor de paquetes Python)
- **Docker** (opcional, para desarrollo containerizado)
- **AWS CLI** (solo para deployment)

### Cuentas y accesos

- **HubSpot Private App Token** con permisos de:
  - CRM Objects (read/write)
  - CRM Associations (read/write)
  - HubDB (read)
- **AWS Account** (solo para deployment en Lambda)

---

## 📦 Instalación

### 1. Clonar el repositorio

```bash
git clone <repository-url>
cd cotizador-proex-microservice
```

### 2. Crear entorno virtual

```bash
python3 -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales
```

---

## ⚙️ Configuración

### Variables de entorno (.env)

Crea un archivo `.env` en la raíz del proyecto con las siguientes variables:

```bash
# HubSpot Configuration
PROEX_ACCESS_TOKEN=pat-eu1-your-private-app-token-here

# CORS Configuration
CORS_ORIGINS=http://localhost:3000,https://app.hubspot.com,https://app-eu1.hubspot.com

# App Configuration
DEBUG=False
APP_NAME="Cotizador Proex Microservice"
VERSION=1.0.0

# Server Configuration (local development)
HOST=0.0.0.0
PORT=8000

# Logging
LOG_LEVEL=INFO

# AWS Configuration (deployment only)
AWS_REGION=eu-west-1
LAMBDA_FUNCTION_NAME=cotizador-proex
AWS_ACCOUNT_ID=your-aws-account-id
ECR_REPO_NAME=cotizador-proex
```

### Archivo de ejemplo

El proyecto incluye un `.env.example` con todas las variables necesarias. Úsalo como plantilla.

---

## 🚀 Uso

### Arrancar en modo desarrollo

```bash
# Activar entorno virtual
source .venv/bin/activate

# Iniciar servidor
python main.py
```

El servidor estará disponible en: `http://localhost:8000`

### Documentación interactiva

FastAPI genera automáticamente documentación interactiva:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Health Check

```bash
curl http://localhost:8000/health
```

Respuesta esperada:
```json
{
  "status": "healthy",
  "version": "1.0.0"
}
```

---

## 📡 API Endpoints

### Deals

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/deals/{deal_id}/client` | Obtener datos del cliente asociado al deal |
| POST | `/deals/{deal_id}/client` | Actualizar datos del cliente |
| GET | `/deals/{deal_id}/documentacion` | Obtener datos de documentación del deal |
| POST | `/deals/{deal_id}/documentacion` | Actualizar datos de documentación |
| GET | `/deals/{deal_id}/articles` | Obtener artículos del deal |
| POST | `/deals/{deal_id}/articles` | Actualizar artículos del deal |
| GET | `/deals/{deal_id}/services` | Obtener servicios del deal |
| POST | `/deals/{deal_id}/services` | Actualizar servicios del deal |
| GET | `/deals/{deal_id}/containers` | Obtener contenedores del deal |
| POST | `/deals/{deal_id}/containers` | Actualizar contenedores del deal |
| GET | `/deals/{deal_id}/transit` | Obtener datos de tránsito del deal |
| POST | `/deals/{deal_id}/transit` | Actualizar datos de tránsito |
| GET | `/deals/{deal_id}/rutas` | Obtener rutas del deal |
| POST | `/deals/{deal_id}/negociacion` | Actualizar datos de negociación |

### Documentación

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| POST | `/documentacion/textos-cotizacion` | Obtener textos de cotización desde HubDB |

#### Ejemplo de uso - Textos de cotización

**Request:**
```bash
curl -X POST http://localhost:8000/documentacion/textos-cotizacion \
  -H "Content-Type: application/json" \
  -d '{
    "idioma": "es",
    "claves": ["oferta_base", "pago_30_70", "banco_la_caixa", "cotizacion_rapida"],
    "grupo": null,
    "subgrupo": null
  }'
```

**Response:**
```json
{
  "success": true,
  "error": null,
  "data": {
    "oferta_base": {
      "grupo": "oferta",
      "subgrupo": "condiciones",
      "idioma": "es",
      "clave": "oferta_base",
      "texto": "Estimado cliente, ..."
    },
    "pago_30_70": {
      "grupo": "pagos",
      "subgrupo": "30_70",
      "idioma": "es",
      "clave": "pago_30_70",
      "texto": "TÉRMINOS DE PAGO.- 30% al confirmar..."
    }
  }
}
```

### Articles

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/articles/search` | Buscar artículos en HubSpot |

---

## 🔧 Scripts Disponibles

### Desarrollo local

```bash
# Iniciar servidor de desarrollo
python main.py

# Ejecutar tests
pytest

# Ejecutar tests con coverage
pytest --cov=app --cov-report=html
```

### Docker

```bash
# Build imagen Docker
docker build -t cotizador-proex:latest .

# Test local de la imagen
./test-docker-build.sh

# Ejecutar contenedor localmente
docker run -p 8000:8000 --env-file .env cotizador-proex:latest
```

### Deployment

```bash
# Deploy completo a AWS Lambda
./deploy.sh

# Build y push a ECR (sin deploy)
./deploy.sh --build-only
```

---

## 📁 Estructura del Proyecto

```
cotizador-proex-microservice/
│
├── app/                          # Código fuente principal
│   ├── __init__.py
│   ├── core/                     # Configuración centralizada
│   │   ├── __init__.py
│   │   └── config.py            # Settings con Pydantic
│   │
│   ├── models/                   # Modelos Pydantic
│   │   ├── __init__.py
│   │   └── schemas.py           # Request/Response models
│   │
│   ├── routes/                   # Endpoints REST
│   │   ├── __init__.py
│   │   ├── deals.py             # Endpoints de deals
│   │   └── articles.py          # Endpoints de artículos
│   │
│   ├── services/                 # Lógica de negocio
│   │   ├── __init__.py
│   │   └── hubspot_client.py    # Cliente HubSpot singleton
│   │
│   ├── utils/                    # Utilidades
│   │   ├── __init__.py
│   │   └── property_helpers.py  # Helpers para properties
│   │
│   └── middleware/               # Middlewares (CORS, logging)
│       └── __init__.py
│
├── main.py                       # Aplicación FastAPI principal
├── lambda_handler.py             # Handler para AWS Lambda
├── requirements.txt              # Dependencias Python
├── Dockerfile                    # Imagen Docker para Lambda
├── deploy.sh                     # Script de deployment
├── test-docker-build.sh          # Test local de Docker
│
├── .env                          # Variables de entorno (no commiteado)
├── .env.example                  # Plantilla de variables
├── .gitignore
│
├── CLAUDE.md                     # Documentación para Claude AI
├── DEPLOYMENT.md                 # Guía de deployment
└── README.md                     # Este archivo
```

### Descripción de directorios críticos

- **`app/core/`**: Contiene `config.py` con toda la configuración de la aplicación usando Pydantic Settings. Centraliza variables de entorno y validaciones.

- **`app/models/`**: Define todos los modelos Pydantic para request/response. Garantiza type safety y validación automática de datos.

- **`app/routes/`**: Contiene la definición de endpoints REST. Cada archivo agrupa endpoints relacionados (deals, articles, etc.).

- **`app/services/`**: Lógica de negocio y comunicación con APIs externas. Incluye el singleton `HubSpotClient`.

- **`app/utils/`**: Funciones auxiliares reutilizables (conversiones de tipos, construcción de properties, parsing, etc.).

---

## 🚢 Deployment

### Deployment a AWS Lambda

El proyecto incluye un script de deployment automatizado:

```bash
./deploy.sh
```

Este script:
1. Valida variables de entorno AWS
2. Construye imagen Docker
3. Autentica con ECR
4. Push de la imagen a ECR
5. Actualiza función Lambda con la nueva imagen

### Requisitos para deployment

- AWS CLI configurado con credenciales válidas
- Permisos IAM para:
  - ECR (push images)
  - Lambda (update function)
- Variables de entorno AWS configuradas en `.env`

Ver [DEPLOYMENT.md](./DEPLOYMENT.md) para instrucciones detalladas.

---

## 📝 Notas adicionales

### Logging

El microservicio utiliza logging estructurado con `python-json-logger`. Los logs incluyen:
- Timestamp
- Level (DEBUG, INFO, WARNING, ERROR)
- Message
- Exception traces (cuando aplica)

### CORS

CORS está configurado para permitir requests desde:
- HubSpot UI (`https://app.hubspot.com`, `https://app-eu1.hubspot.com`)
- Desarrollo local (`http://localhost:3000`)

### Rate Limiting

Las llamadas a HubSpot API están sujetas a rate limits. El servicio maneja estos límites mediante:
- Batch operations donde es posible
- Retry logic con backoff exponencial
- Logging de errores de rate limiting

---

## 🤝 Contribución

Para contribuir al proyecto:

1. Crear una nueva rama desde `main`
2. Realizar cambios
3. Ejecutar tests: `pytest`
4. Crear Pull Request

---

## 📄 Licencia

Proyecto privado - PROEX

---

## 📞 Contacto

Para preguntas o soporte, contactar al equipo de desarrollo de PROEX.

---

**Versión**: 1.0.0
**Última actualización**: Noviembre 2024
