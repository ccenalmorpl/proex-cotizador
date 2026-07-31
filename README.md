# Cotizador PROEX

> Configurador de ofertas de import/export: fija cliente, incoterm, IVA, artículos, contenedores, rutas de flete, servicios y márgenes, calcula el precio de venta por caja y publica la oferta como Quote DRAFT y objetos Contenedor/Elemento que alimentan al resto del pipeline.

**Asistente 1 de 4** del flujo PROEX · Card CRM + Microservicio

## 📌 Rol en el flujo PROEX

El Cotizador es la **puerta de entrada** del pipeline comercial de PROEX. Un comercial lo abre como pestaña en un Negocio (Deal) de HubSpot y lo usa para configurar la oferta completa; al publicar, deja el Deal listo para pasar por cobros, convertirse en pedidos y terminar en documentación.

- **Recibe** (del CRM): un Deal con Company obligatoria (sin empresa asociada la card se bloquea) y Contact opcional, más el catálogo de Products, Proveedores (`2-43108800`) y las tablas HubDB de IVA y textos.
- **Produce**:
  - Una **Quote DRAFT** (v3) con los line items copiados del Deal — publicación **síncrona**.
  - Los custom objects **Contenedor** (`2-54989406`) y **Elemento** (`2-54989407`) recreados desde los line items — publicación **asíncrona** (SQS + worker).
- **Entrega al siguiente**: esos Contenedor/Elemento son la fuente que consumen **Pagos** (reparte cobros por contenedor), **Pedidos** (1 Negocio Pedido por contenedor al ganar) y **Documentos** (certificados por contenedor). Cuando la cotización **ya está ganada** y se re-publican los custom objects, el microservicio hace un POST best-effort al webhook `cotizacion-ganada` de Pedidos para reconciliar.

```
   [ CRM: Deal + Company + Products + Proveedores ]
                      |
                      v
        ┌───────────────────────────┐
        │      COTIZADOR  (1)        │  ← ESTÁS AQUÍ
        │  Quote DRAFT + Contenedor  │
        │  + Elemento (custom objs)  │
        └───────────────────────────┘
                      |  Contenedor / Elemento
                      v
   Cobros (2) → Pedidos (3) → Documentación (4)
                      ^
   (al ganar la cotización: webhook cotizacion-ganada → Pedidos)
```

## 🏗️ Arquitectura

Dos piezas con el molde común de PROEX: una **CRM Card** (React UI Extensions) servida por HubSpot y un **microservicio FastAPI** desplegado como Lambda tras API Gateway. La card no toca HubSpot directamente: delega toda la lógica pesada (API HubSpot, HubDB, cálculo definitivo, persistencia) en el microservicio y solo consume unos ~30 endpoints REST vía `hubspot.fetch`.

```
  HubSpot CRM (ficha del Deal)
  ┌────────────────────────────┐
  │  Cotizador Card             │   React 18 + @hubspot/ui-extensions
  │  (8 tabs, estado en         │   TypeScript · platformVersion 2025.2
  │   Cotizador.tsx)            │
  └───────────────┬────────────┘
                  │  hubspot.fetch (HTTPS)  baseURL hardcodeada en config.js
                  v
  API Gateway HTTP API (eu-south-2)
                  │
                  v
  ┌────────────────────────────┐      ┌──────────────────────────┐
  │  Lambda API                 │ SQS │  Lambda worker            │
  │  proex-cotizador            │────>│  proex-jobs-worker        │
  │  FastAPI + Mangum (Py 3.11) │     │  worker_handler.handler   │
  └───────────────┬────────────┘      └────────────┬─────────────┘
                  │                                  │
                  v                                  v
      HubSpot API v3/v4  ·  HubDB          Contenedor/Elemento + POST webhook Pedidos
      (deals, products,                    (DynamoDB proex-jobs guarda estado del job)
       line items, quotes,
       custom objects)
```

- **Card**: React 18 + `@hubspot/ui-extensions` (componentes propietarios: Tabs, Form, Select, Table, StepIndicator, Modal…). Sin Context/Redux: todo el estado vive en `Cotizador.tsx` y baja por props. Prohibido `window.fetch`; todo HTTP pasa por `hubspot.fetch`.
- **Microservicio**: FastAPI 0.115 + Pydantic 2 + Mangum sobre Python 3.11, imagen Docker en ECR. Dos Lambdas con **la misma imagen**: la API (`lambda_handler.handler`) y el worker SQS (`worker_handler.handler`). Cliente HubSpot singleton con `lru_cache`.
- **Async solo cuando hace falta**: `publish` (Quote) es síncrono (~5 s, cabe en el timeout de 15 s de HubSpot UI Extensions); `publish-custom-objects` encola un job en SQS y responde al instante, y la card hace polling a `/custom-objects/status`.

⚠️ Las fórmulas de pricing e IVA están **duplicadas a propósito** en ambos lados: `pricing.utils.js` / `iva.utils.js` (card) son espejo de `pricing.py` / `iva_helpers.py` (backend). Cualquier cambio de fórmula por incoterm o de IVA debe hacerse en los DOS lados o los totales divergen.

## 📦 Componentes

### `cotizador-proex-card/` — Tarjeta CRM (frontend)

CRM Card montada como pestaña en la ficha de un **Deal** (`crm.record.tab`, `objectTypes=["deals"]`). Asistente de **8 pestañas** en orden: **cliente** → **articulos** → **contenedores** → **transitario** (título visible "Rutas") → **servicios** → **negociacion** → **documentacion** → **revision** (título visible "Vista previa"). React 18 + TypeScript (entrypoint `Cotizador.tsx`, tabs en `.jsx`). Sin tests; QA vía ESLint + `tsc --noEmit`.

Detalle: [README](./cotizador-proex-card/README.md) · [CLAUDE.md](./cotizador-proex-card/CLAUDE.md) · [UI-DOCS.md](./cotizador-proex-card/UI-DOCS.md) · [AGENTS.md](./cotizador-proex-card/AGENTS.md) · [REFACTORING_SUMMARY.md](./cotizador-proex-card/REFACTORING_SUMMARY.md)

> Nota: la documentación interna de la card está parcialmente **desactualizada** (describe 6 tabs o nombres/orden distintos). La realidad son las 8 tabs listadas arriba.

### `cotizador-proex-microservice/` — Backend

Backend REST FastAPI/Lambda. Centraliza la lógica de la cotización y expone ~35 endpoints agrupados en tres routers (`/api/v1/deals`, `/articles`, `/templates`) más `/health`. Toda la lógica de negocio de la cotización y la publicación vive en `routes/deals.py`; el CRUD/sync de custom objects en `services/custom_objects.py`; el pricing e IVA por concepto en `utils/pricing.py` e `utils/iva_helpers.py`. Un **worker** Lambda separado (`workers/custom_objects_worker.py`) consume la cola SQS para recrear Contenedor/Elemento y reconciliar Pedidos.

Detalle: [README](./cotizador-proex-microservice/README.md) · [CLAUDE.md](./cotizador-proex-microservice/CLAUDE.md) · [DEPLOYMENT.md](./cotizador-proex-microservice/DEPLOYMENT.md) · [scripts/README_CUSTOM_OBJECTS.md](./cotizador-proex-microservice/scripts/README_CUSTOM_OBJECTS.md)

## 🔄 Flujos principales

1. **Configuración de la oferta (tab a tab)**: cliente e incoterm/tipo de contenedor/margen/comisión (`POST /client`) → búsqueda y alta de artículos de catálogo como line items fabricante + proveedores + lugar de carga (`POST /articles`) → composición de contenedores con ocupación 90-103% (`POST /containers`, merge incremental) → ruta/transitario y fletes origen/destino con objetos Ruta (`POST /transit`) → servicios y seguro (`POST /services`) → negociación de precio/margen/IVA por line item (`POST /negociacion`) → documentación y textos (`POST /documentacion`).

2. **Pricing por incoterm con reparto intra-contenedor**: cada contenedor reparte SU flete/servicios/seguro/comisión entre SUS propias cajas. El precio por caja se calcula con `calcular_precio_por_caja_por_incoterm` (EXW/DAP_TERRESTRE/FOB/CFR/CIF/DAP_MARITIMO) y su inversa. Añadir un contenedor NO mueve el precio de los previos, salvo la comisión en modo `importe_por_negocio` (que rediluye por decisión de negocio).

3. **Ancla del tipo de venta**: el precio FINAL por caja tecleado por el comercial queda FIJO (`precio_negociado_tipo_venta` + `incoterm_precio_negociado`); el EXW y el margen se re-derivan con suelo de margen 0 (nunca bajo coste). Si el Deal cambia de incoterm, manda el EXW persistido y se re-ancla el final del nuevo incoterm.

4. **Cálculo de `contenedor.price` con IVA por concepto**: base = Σ `round(precio_caja_incoterm, 3) × cajas` por línea fabricante (mismo redondeo combinado que el front); el IVA se descompone POR CONCEPTO (producto, flete, servicios, seguro, comisión), cada uno con su code y con recargo de equivalencia si `company.equ='si'`. Los gates por incoterm deciden qué conceptos entran.

5. **Publicación en dos fases** (`RevisionTab`): `POST /publish` crea la Quote DRAFT + line items (SÍNCRONO) y guarda el payload en `deal.quote_data_payload`; luego `POST /publish-custom-objects` encola el job (ASÍNCRONO). La card hace polling de `/custom-objects/status` (máx. 20 intentos/3 s, maneja `not_started` por consistencia eventual del GSI). `NegociacionTab` bloquea "Editar contenedor" mientras el job está pending/processing.

6. **Sync de custom objects (worker)**: `process_deal_custom_objects_from_line_items` hace un diff incremental crear/actualizar/borrar de Contenedor y Elemento, empareja Elementos por `line_item_id` con guard de LID cruzado (verifica por nombre; fallback por `(nombre_contenedor, nombre)` solo si es único, para proteger la promo 5+1). `PRESERVED_CONT_FIELDS = {importe_total_cobrado, numero_precinto}` nunca se pisan. Al terminar, reconcilia Pedidos si la cotización ya estaba ganada.

## 🌐 API / Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| GET/POST | `/api/v1/deals/{deal_id}/client` | Datos de cliente/empresa/deal + opciones de selects |
| GET/POST | `/api/v1/deals/{deal_id}/articles` | Cargar / crear line items fabricante + proveedores + lugar de carga |
| GET/POST | `/api/v1/deals/{deal_id}/containers` | Composición de contenedores (merge incremental) |
| PATCH | `/api/v1/deals/{deal_id}/containers/{container_line_item_id}` | Edición per-contenedor in situ |
| GET/POST | `/api/v1/deals/{deal_id}/transit` | Fletes origen/destino + objetos Ruta (`2-53295516`) |
| GET | `/api/v1/deals/{deal_id}/transit-options` · `/rutas` | Opciones de tránsito y rutas guardadas |
| GET | `/api/v1/deals/transitarios/search` · `/commission-providers/search` | Búsqueda de transitario / intermediario |
| GET/POST | `/api/v1/deals/{deal_id}/services` | Servicios (comisión/embalaje/seguro) + IVA por fila |
| POST | `/api/v1/deals/{deal_id}/negociacion` | Precio/margen/IVA/ancla por line item; sincroniza Elementos y recalcula contenedores |
| GET/POST | `/api/v1/deals/{deal_id}/documentacion` | Textos, certificado origen, documentos a generar |
| GET | `/api/v1/deals/{deal_id}/empresas-asociadas` | Empresas asociadas ("Proveedor-Cliente") + `address_entrega`, para autorrellenar la dirección de entrega |
| POST | `/api/v1/deals/tipo-iva` | Filas HubDB `tipo_de_iva_soportado` por categoría+residencia (cache) |
| POST | `/api/v1/deals/documentacion/textos-cotizacion` | Textos multiidioma HubDB `textos_cotizaciones` |
| POST | `/api/v1/deals/{deal_id}/publish` | Crea Quote DRAFT + line items (SÍNCRONO) |
| POST | `/api/v1/deals/{deal_id}/publish-custom-objects` | Encola job de Contenedor/Elemento (ASÍNCRONO) |
| GET | `/api/v1/deals/{deal_id}/custom-objects/status` | Estado del job + nº de contenedores/elementos |
| GET | `/api/v1/articles/product/list` · `/services/list` · `/providers/list` | Catálogo de productos, servicios y proveedores |
| GET | `/api/v1/templates` | Plantillas de quote (`quote_template`) |
| GET | `/health` · `/` | Health check / raíz |

## 🗃️ Modelo de datos (HubSpot)

Objetos que este asistente lee/escribe (objectTypeId entre paréntesis):

- **Deal** (`0-3`) — cotización padre. Props clave: `tipo_de_venta` (incoterm), `tipo_contenedor`, `margen_cliente`, `almacen`, `transitario`, `repercutir_a_cliente`, `tipo_comision`, `importe_comision`, `codigo_intermediario`, `tipo_iva_fletes`, `tipo_iva_comision(_soportado)`, `precio_ruta_hasta_puerto_de_salida/destino`, `documentos_a_generar`, `quote_data_payload`, `preprints_language`, `hs_is_closed_won`, `actualizacion_pedido`.
- **Contenedor** (`2-54989406`) — custom object generado al publicar. Props: `nombre` (`{deal_id}-{NNN}`), `tipo_contenedor`, `numero_container`, `total_cajas`, `peso_neto/peso_bruto`, `price`, `importe_pendiente_cobro`, `importe_total_cobrado` (preservado), `numero_precinto` (preservado), `line_item_id`, `container_config`, `documentos_a_generar`, `actualizacion_pedido`.
- **Elemento** (`2-54989407`) — línea de un contenedor. Props: `tipo_articulo` (fabricante/servicio/flete/intermediario/seguro), `cantidad`, `precio_unitario`, `precio_de_coste`, `precio_total`, `margen_unitario`, `tipo_iva_repercutido/soportado`, `cardcode`, `line_item_id`, `almacen`, `nombre_es/en/fr`, pesos.
- **Proveedor** (`2-43108800`), **Ruta** (`2-53295516`), **Pago/Cobro** (`2-44408688`, se escribe `actualizacion_pedido` como disparador SAP), **Quote** (`0-14`, plantilla fija `530249630794`), **Product** (`0-7`, `hs_price_eur` es la fuente de `precio_de_coste`), **Company** (`0-2`: `residennum`, `equ`), **Line Item** (`0-8`: ancla `precio_negociado_tipo_venta` + `incoterm_precio_negociado`).

Asociaciones (typeIds): Deal→Contenedor `489`, Deal→Elemento `493`, Elemento→Contenedor `491`, Contenedor→Company `513` / Contact `511`; Contenedor→Proveedor fabricante `669` / transitario `671` / intermediario `673` / servicio `675` / seguro `677`; Contenedor→Pago `499`/`500`; Quote→Deal `64` / Line Item `67` / Template `286`.

Detalle completo en [../MODELO-DATOS-HUBSPOT-PROEX.md](../MODELO-DATOS-HUBSPOT-PROEX.md).

## 🔌 Integraciones

- **SAP (indirecta)**: disparador de sincronización vía la propiedad `actualizacion_pedido`, que se escribe en Contenedor, Deal y Pagos (`doctype=contenedor`) mediante `touch_pagos_for_contenedores`; SAP la consume aguas abajo. `precio_de_coste` = `product.hs_price_eur`.
- **Asistente Pedidos**: `pedidos_sync.reconciliar_pedidos_si_ganada` hace `POST` al webhook `cotizacion-ganada` (`PEDIDOS_WEBHOOK_URL`, header `X-Webhook-Token=HUBSPOT_WEBHOOK_SECRET`) cuando la cotización ya está ganada y se re-publican custom objects (idempotente en Pedidos).
- **Asistentes Pagos/Pedidos/Documentos**: consumen los Contenedor/Elemento que este microservicio crea al publicar.
- **HubDB**: `tipo_de_iva_soportado` (cache en memoria por proceso, precargada en el `lifespan` de arranque; EX/SEXN se sintetizan siempre disponibles al 0%) y `textos_cotizaciones` (fetch REST bajo demanda).
- **SQS + DynamoDB**: cola `proex-jobs-queue` con DLQ `proex-jobs-dlq` (el worker reintenta 3 veces antes de caer en DLQ); estado de jobs en tabla `proex-jobs` (GSI `deal_id-job_type-index`, TTL 7 días).
- **ECR + API Gateway HTTP API**: imagen Docker y proxy a la Lambda API.

## 🚀 Despliegue

- **AWS**: región `eu-south-2`, cuenta `095235023391`, perfil `biatic`. Infra aprovisionada **a mano** (sin IaC); IDs de HubSpot hardcodeados.
- **`deploy.sh`**: construye la imagen Docker (`--platform linux/amd64 --no-cache --provenance=false`), la sube a ECR (`proex-cotizador`), crea/actualiza la Lambda API `proex-cotizador` (timeout 30 s, 512 MB, CMD `lambda_handler.handler`) y su API Gateway HTTP API (CORS `*`), y adjunta la IAM policy `proex-jobs-access` (DynamoDB + SQS SendMessage).
- **`deploy-worker.sh`**: reutiliza la MISMA imagen ECR con override de CMD a `worker_handler.handler` para la Lambda `proex-jobs-worker` (timeout 90 s, 512 MB) y crea el event source mapping SQS→worker (batch-size 1) + policy de consumo.
- **Variables de entorno**: `PROEX_ACCESS_TOKEN` (pat-…, requerido), `CORS_ORIGINS`, `JOBS_TABLE_NAME=proex-jobs`, `JOBS_QUEUE_URL/ARN`, `JOBS_DLQ_ARN`; solo en el worker `PEDIDOS_WEBHOOK_URL` + `HUBSPOT_WEBHOOK_SECRET`. `jobs_store`/`jobs_queue` toman `AWS_REGION` con default `eu-south-2`.
- **Card**: no es una Lambda; se sube con HubSpot CLI (`hs project upload` / `hs project dev` / `watch`), `platformVersion 2025.2`, app privada con auth static. La URL del backend NO viene de env: está **hardcodeada** en `src/app/cards/config.js` (`API_CONFIG.baseURL`) y debe estar en `permittedUrls.fetch` del `app-hsmeta.json`.

⚠️ `README/CLAUDE/DEPLOYMENT` del microservicio mencionan `eu-west-1`/`eu-north-1`; la región real (según `.env`) es `eu-south-2`.

## 🧪 Testing

- **Microservicio**: `pytest 8.3.3` (`pytest` o `pytest --cov=app`). 6 ficheros sobre funciones **puras** sin red (solo se mockea `hubdb_client`): `test_precio_anclado.py`, `test_iva_helpers.py`, `test_hubdb_siempre_disponibles.py`, `test_almacen_helpers.py`, `test_catalogo_huerfanos.py`, `test_tipologia_helpers.py`. Cobertura acotada a pricing/IVA/almacén/tipología; los endpoints y el sync de custom objects NO tienen tests unitarios y se validan con el script E2E `scripts/e2e_line_item_id_stable.py` contra un deal real.
- **Card**: sin tests ni framework; QA = ESLint (`npm run lint`) + type-check (`npx tsc --noEmit`) + verificación manual en un deal de sandbox con `hs project dev`.

## 📁 Estructura de carpetas

```
cotizador/
├── cotizador-proex-card/          # CRM Card (React UI Extensions, TypeScript)
│   └── src/app/cards/
│       ├── Cotizador.tsx          # raíz: navegación de tabs + estado compartido + carga inicial
│       ├── config.js              # API_CONFIG.baseURL del microservicio (hardcodeado)
│       ├── tabs/                  # 8 tabs: cliente, articulos, contenedores, transitario,
│       │                          #         servicios, negociacion, documentacion, revision
│       ├── components/            # EditarContenedorModal.jsx (edición per-contenedor, promo 5+1)
│       └── utils/                 # pricing.utils.js / iva.utils.js (espejo del backend)
└── cotizador-proex-microservice/  # Backend FastAPI + Lambda
    ├── main.py                    # App FastAPI, monta routers + lifespan (precarga cache IVA)
    ├── lambda_handler.py          # Mangum handler (API)
    ├── worker_handler.py          # handler SQS (despacha por job_type)
    ├── deploy.sh / deploy-worker.sh
    └── app/
        ├── routes/                # deals.py (lógica de la cotización), articles.py, templates.py
        ├── services/              # custom_objects.py, quote_helpers.py, jobs_store/queue, pedidos_sync.py
        ├── utils/                 # pricing.py, iva_helpers.py, property_helpers.py, tipologia.py
        ├── shared/hubdb_client.py # cache de la HubDB de IVA
        └── workers/custom_objects_worker.py  # job de publicación + reconciliación de pedidos
```

## 📚 Documentación relacionada

- Card: [README](./cotizador-proex-card/README.md) · [CLAUDE.md](./cotizador-proex-card/CLAUDE.md) · [UI-DOCS.md](./cotizador-proex-card/UI-DOCS.md) · [AGENTS.md](./cotizador-proex-card/AGENTS.md) · [REFACTORING_SUMMARY.md](./cotizador-proex-card/REFACTORING_SUMMARY.md)
- Microservicio: [README](./cotizador-proex-microservice/README.md) · [CLAUDE.md](./cotizador-proex-microservice/CLAUDE.md) · [DEPLOYMENT.md](./cotizador-proex-microservice/DEPLOYMENT.md) · [scripts/README_CUSTOM_OBJECTS.md](./cotizador-proex-microservice/scripts/README_CUSTOM_OBJECTS.md)
- Global del repo: [../HANDOVER-TECNICO-PROEX.md](../HANDOVER-TECNICO-PROEX.md) · [../MODELO-DATOS-HUBSPOT-PROEX.md](../MODELO-DATOS-HUBSPOT-PROEX.md)
