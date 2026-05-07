# Landing Leads — San Luis · Handover

> Documento de traspaso para un socio que recibe la `leads.db` y el código.
> Si abrís este proyecto en **Claude Code** o un editor con AI, este doc tiene
> todo el contexto para que la IA te asista directo.

---

## TL;DR — qué es esto

Pipeline que **identifica comercios sin landing page** en San Luis (capital, Juana
Koslay, Potrero de los Funes, La Punta, Villa Mercedes, Villa de Merlo) y **analiza
sus reseñas de Google Maps con Claude** para extraer materia prima de venta:
fortalezas, dolores, ángulos copy-ready, tono recomendado, audiencia y keywords SEO.

El objetivo del proyecto es **vender landings** a esos comercios usando esa materia
prima como propuesta inicial.

## Qué recibís

```
leads.db          ← SQLite con 1.261 negocios, 994 leads, 260 análisis completos
src/              ← código del pipeline (Python)
app.py            ← dashboard local en Streamlit
HANDOVER.md       ← este doc
README.md         ← quick reference
```

**Métricas actuales** (snapshot al cierre del primer corrida):

| Métrica | Valor |
|---|---|
| Negocios descubiertos | 1.261 |
| Sin web propia (leads) | 994 (78%) |
| Con Instagram cargado en GBP | ~137 |
| Con Facebook | ~60 |
| Con análisis de Claude | 260 |
| Reviews capturadas (5 por local desde Places API) | ~4.500 |

---

## Quick start — 3 minutos para ver la data

Si solo querés **abrir el dashboard** y revisar los leads (no re-correr el pipeline):

```bash
# 1. Setup (una vez)
cd /ruta/a/Landing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Levantar el dashboard
streamlit run app.py
```

Abrí http://localhost:8501 — esa es la app. No necesitás API keys para esto, la
DB ya tiene toda la data adentro.

> Si tu socio te pasa la DB pero no el código: el repo está en
> `/Users/rodrigodominguez/AndroidStudioProjects/Landing`. Cloneás (o copiás) y
> reemplazás `data/leads.db` por la versión que te llegó.

---

## Cómo usar el dashboard (workflow recomendado)

El dashboard tiene 3 tabs: **Tabla**, **Detalle del lead**, **Mapa**.

### Workflow para revisar leads con tu socio

1. **Sidebar — filtros**
   - "Solo sin web propia" → ON (default)
   - "Tipo de presencia digital" → dejá tildado todo menos `🌐 Web propia`
   - Para arrancar enfocados: filtrá por **una sola categoría** (ej: `hotel`)
   - Si querés los premium: "Min reviews" en 50+

2. **Tab Tabla**: ordená clickeando columnas. La columna **Presencia** te dice de
   un vistazo si el negocio tiene Insta/FB/Linktree/nada — los que tienen Insta
   son leads doraditos porque podés mirar su feed para diseñar la landing.

3. **Tab Detalle del lead**: dropdown para elegir uno → ves el análisis completo
   de Claude (fortalezas, dolores, ángulos, keywords, tono). Si tiene Instagram,
   lo abrís en otra pestaña y mirás el feed mientras leés.

4. **Marcar estado**: en el panel derecho del detalle, cambiás de `pending` a
   `contactado` / `interesado` / `descartado` / `landing_hecha`. Notas para
   acordarte cosas (ej: "llamar lunes a la mañana, hablar con Cristian").
   Click en 💾 Guardar y queda en la DB.

5. **Tab Mapa**: muestra la geolocalización de los filtrados. Útil si querés
   atacar una zona específica un día (ej: solo Villa de Merlo este finde).

### Estados del workflow

| Estado | Significado |
|---|---|
| `pending` | No revisado todavía |
| `contactado` | Ya llamaste/escribiste |
| `interesado` | Mostró interés, hay seguimiento |
| `descartado` | Por algún motivo (ya tiene landing oculta, no atendió, no quiere) |
| `landing_hecha` | Cerraste la venta y armaste la landing |

---

## Arquitectura

```
┌──────────────────┐
│ Google Places    │  Discovery: nearby_search por celdas + place_details
│ API (legacy)     │  → guarda negocio + 5 reviews recientes (gratis)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ leads.db         │  SQLite local con 4 tablas:
│ (SQLite)         │  businesses · reviews · analysis · pipeline_status
└────────┬─────────┘
         │
         ├─→ Outscraper API (opcional) — dump completo de reviews
         │   (todavía no usado, esperando activación de cuenta)
         │
         ├─→ Anthropic Claude (claude-sonnet-4-6)
         │   Analiza reviews → JSON estructurado (fortalezas/dolores/ángulos/etc)
         │
         └─→ Streamlit dashboard (app.py)  ← acá entrás vos para revisar
```

### Decisiones de diseño importantes

1. **`website_kind` no es booleano.** Un negocio con Instagram cargado como
   "web" en Google Business Profile **es un lead igual** — no tiene landing
   real. Lo clasificamos como `instagram` y lo dejamos en el universo de leads.
   Solo `real` (web propia) los descarta del pipeline.

2. **Discovery por grilla.** Cada zona se divide en celdas de ~1.2-2km. La API
   de Google devuelve máximo 60 resultados por nearby_search, así que sin
   grilla nos perdíamos negocios en zonas densas.

3. **Reviews gratis primero.** Places API devuelve 5 reviews por local sin
   costo extra (las guardamos durante discovery). Eso ya alcanza para que
   Claude haga un primer análisis. Cuando active Outscraper, bajamos el dump
   completo (~80 reviews) y re-analizamos los mejores leads para profundidad.

4. **Análisis estructurado, no texto libre.** Claude devuelve JSON con schema
   fijo. Esto evita parseos frágiles y hace que el dashboard pueda renderizar
   cada campo como tag/lista/texto.

---

## Estructura del proyecto

```
Landing/
├── HANDOVER.md         ← este doc
├── README.md           ← quick reference
├── app.py              ← dashboard Streamlit
├── requirements.txt    ← deps Python
├── .env.example        ← plantilla de API keys (NO commitear .env real)
├── data/
│   └── leads.db        ← SQLite con todos los datos
├── scripts/
│   └── analyze_top.py  ← analiza top N por categoría (one-off)
└── src/
    ├── config.py       ← zonas, categorías, params
    ├── db.py           ← schema SQLite + helpers + classify_website()
    ├── discover.py     ← Google Places API
    ├── reviews.py      ← Outscraper (opcional, para enriquecer)
    ├── analyze.py      ← Claude API + structured outputs
    ├── pipeline.py     ← CLI orquestador (typer)
    └── export.py       ← CSV / JSON / SQL Supabase
```

---

## Schema de la DB

### `businesses` (lo que descubrimos)

```sql
place_id        TEXT PRIMARY KEY      -- Google Place ID, identificador estable
name            TEXT NOT NULL
category        TEXT NOT NULL         -- nuestra taxonomía: peluqueria, hotel, etc
google_types    TEXT                  -- JSON array de tipos de Google
zone            TEXT NOT NULL         -- san_luis, villa_merlo, etc
address         TEXT
lat, lng        REAL
phone           TEXT
website         TEXT                  -- la URL como vino de Google (puede ser Insta!)
website_kind    TEXT                  -- none | instagram | facebook | tiktok |
                                      -- linktree | whatsapp | directory | real
rating          REAL
reviews_count   INTEGER
business_status TEXT                  -- OPERATIONAL, CLOSED_TEMPORARILY, etc
hours           TEXT                  -- JSON con horarios
discovered_at, updated_at TEXT
```

### `reviews` (reseñas individuales)

```sql
id          INTEGER PRIMARY KEY
place_id    TEXT
author      TEXT
rating      INTEGER  -- 1 a 5
text        TEXT
language    TEXT
posted_at   TEXT
fetched_at  TEXT
```

### `analysis` (lo que extrajo Claude)

```sql
place_id          TEXT PRIMARY KEY
summary           TEXT     -- resumen ejecutivo en 2-3 oraciones
strengths         TEXT     -- JSON array de fortalezas
pains             TEXT     -- JSON array de dolores recurrentes
angles            TEXT     -- JSON array de ángulos copy-ready
tone              TEXT     -- tono recomendado (ej: "cercano y familiar")
target_audience   TEXT     -- audiencia real según reviews
keywords          TEXT     -- JSON array de keywords SEO locales
reviews_analyzed  INTEGER  -- cuántas reviews uso Claude para el análisis
model             TEXT     -- modelo usado (claude-sonnet-4-6)
analyzed_at       TEXT
```

### `pipeline_status` (estado de cada lead)

```sql
place_id        TEXT PRIMARY KEY
has_website     INTEGER   -- 0 o 1. Solo es 1 si website_kind = 'real'
reviews_scraped INTEGER   -- 1 si Outscraper ya bajó las reviews completas
analyzed        INTEGER   -- 1 si Claude ya analizó
status          TEXT      -- pending|contactado|interesado|descartado|landing_hecha
notes           TEXT      -- notas tuyas/de tu socio
updated_at      TEXT
```

---

## Categorías ya cubiertas

```
hotel + hostel + cabanas         (turismo en Merlo, Potrero, La Punta)
salon de fiestas + catering      (eventos, todas las zonas)
centro de estetica + spa         (belleza)
gimnasio                         (boom local en SL)
consultorio odontologico
peluqueria                       (del primer test)
```

**Categorías descartadas explícitamente** (con razones):

- ❌ Cafés y restoranes → ya están en Insta + apps de delivery, ticket bajo
- ❌ Inmobiliarias, veterinarias, tatuajes → fuera del scope inicial elegido

Si querés agregarlas, mirá la sección "Extender el pipeline" más abajo.

---

## Operaciones comunes

### Ver stats de la base

```bash
python -m src.pipeline stats
```

### Listar top leads

```bash
python -m src.pipeline list-leads --limit 20
python -m src.pipeline list-leads --zone villa_merlo --limit 30
```

### Ver detalle de un lead específico

```bash
# Necesitás el place_id (lo ves en el dashboard o con una query SQL)
python -m src.pipeline show ChIJxxxxxxxxxxxxx
```

### Exportar

```bash
python -m src.pipeline export --format csv          # → data/exports/leads.csv
python -m src.pipeline export --format json         # → data/exports/leads.json
python -m src.pipeline export --format supabase-sql # → data/exports/leads.sql
```

El export SQL viene con DDL listo para Supabase/Postgres + INSERTs.

### Query SQL directa

```bash
sqlite3 data/leads.db
.headers on
.mode column

-- Top leads sin web por reviews_count
SELECT b.name, b.category, b.zone, b.rating, b.reviews_count, b.phone, b.website_kind
FROM businesses b
JOIN pipeline_status p ON p.place_id = b.place_id
WHERE p.has_website = 0
ORDER BY b.reviews_count DESC
LIMIT 20;

-- Leads con Instagram (premium para diseño)
SELECT b.name, b.category, b.website
FROM businesses b
WHERE b.website_kind = 'instagram'
ORDER BY b.reviews_count DESC;

-- Leads marcados como interesados
SELECT b.name, b.phone, p.notes
FROM businesses b
JOIN pipeline_status p ON p.place_id = b.place_id
WHERE p.status = 'interesado';
```

---

## Extender el pipeline

Si querés correr nuevo discovery o re-analyze, **necesitás API keys propias**.
Las que el otro socio usó están en su `.env` local.

### API keys necesarias

| Servicio | Para qué | Costo aprox |
|---|---|---|
| **Google Maps API** | Discovery (Places API + Geocoding API) | ~USD 11 cada vez que corras `discover --all`. Tenés USD 200/mes free de Google. |
| **Anthropic** | Análisis con Claude | ~USD 3 por 250 análisis. Mínimo USD 5 de saldo en console.anthropic.com |
| **Outscraper** (opcional) | Reviews completas (>5 por local) | ~USD 2 por 1000 reviews. Free tier de USD 5 |

Configurá en `.env` (copiá de `.env.example`):

```
GOOGLE_MAPS_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-api03-...
OUTSCRAPER_API_KEY=     # opcional
```

### Agregar una nueva categoría

Editá [`src/config.py`](src/config.py) → lista `CATEGORIES`. Cualquier query
en castellano funciona (Google maneja sinónimos en Argentina razonable).

```python
CATEGORIES = [
    ...
    "vinoteca",       # nuevo
    "kinesiologia",   # nuevo
]
```

Después corrés solo esas:

```bash
python -m src.pipeline discover --category vinoteca
python -m src.pipeline discover --category kinesiologia
```

### Agregar una nueva zona

Editá [`src/config.py`](src/config.py) → dict `ZONES`. Necesitás bounding box
(esquinas SW y NE en lat/lng) y tamaño de celda en metros.

```python
ZONES["nueva_zona"] = Zone(
    slug="nueva_zona",
    label="Nombre Visible",
    sw_lat=-33.40, sw_lng=-66.50,
    ne_lat=-33.30, ne_lng=-66.40,
    cell_meters=1500,
)
```

### Re-analyze con reviews completas (cuando active Outscraper)

```bash
# 1. Bajar reviews completas (max 80 por local) de los top leads
python -m src.pipeline scrape-reviews --limit 50

# 2. Re-analizar (Claude con más reviews → análisis más profundo)
python -m src.pipeline analyze --limit 50
```

**Importante**: el re-analyze sobreescribe el análisis previo del mismo lead.
Si querés conservar el análisis de Places (5 reviews) Y el de Outscraper
(80 reviews), modificá `db.save_analysis` para versionar.

### Pipeline completo end-to-end

```bash
python -m src.pipeline run-all --limit 100
```

Esto corre: discover → scrape-reviews → analyze, en orden.

---

## Costo histórico (referencia)

| Operación | Costo |
|---|---|
| Discovery completo (9 queries × 6 zonas) | ~USD 11 (gratis con free tier Google) |
| Análisis de 260 leads (5 reviews c/u) | ~USD 3.10 |
| Por análisis individual | ~USD 0.012 |
| **Total para tener la base actual** | **~USD 3.10** (Anthropic) |

---

## Pendientes (en orden de prioridad)

1. **Outscraper se active y re-analyze top 50** → análisis con 80 reviews por local en
   vez de 5, mucho más profundo (~USD 2-3 adicionales en Anthropic)
2. **Limpiar falsos positivos** detectados en el dashboard (Google a veces clasifica
   raro: "Spa - Merlo" con 12k reviews es la ciudad entera, no un spa). Marcarlos
   como `descartado` con nota.
3. **Migrar a Supabase** cuando arrancar a operar el pipeline en serio (export
   ya viene listo con `--format supabase-sql`)
4. **Agregar categorías nuevas** según resultado de las primeras ventas (vinotecas,
   kinesio, talleres especializados, etc.)

---

## Troubleshooting

| Problema | Causa probable | Fix |
|---|---|---|
| `RuntimeError: Falta GOOGLE_MAPS_API_KEY` | No copiaste `.env.example` a `.env` | `cp .env.example .env` y completar |
| `Falta ANTHROPIC_API_KEY` aunque está en `.env` | Tu shell tiene la var seteada vacía | Ya está fixed: `config.py` usa `load_dotenv(override=True)` |
| Streamlit muestra base vacía | `data/leads.db` no está o está corrupta | Pedile la DB a tu socio o re-correr `pipeline init` + `discover` |
| Discovery encuentra muy pocos negocios | Bounding box mal definido | Verificar coordenadas de la zona en Google Maps |
| `TypeError: ... 'proxies'` al instanciar Anthropic | Versión vieja del SDK | `pip install --upgrade anthropic` |

---

## Si abrís este proyecto en Claude Code

Decile a Claude: *"Leé `HANDOVER.md` y armame [lo que necesites]"*. Con este
documento la IA tiene contexto suficiente para:

- Construir un dashboard alternativo (ej: en Next.js si preferís)
- Agregar campos al schema (ej: campo "presupuesto estimado de venta")
- Sumar nuevas categorías o zonas
- Migrar la base a Postgres/Supabase
- Generar landings reales para los leads marcados como `landing_hecha`

---

**Autor original**: Rodrigo + socio · San Luis, Argentina
**Fecha snapshot**: 2026-04-30
