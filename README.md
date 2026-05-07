# Landing Leads — San Luis

Pipeline para descubrir comercios sin web en San Luis (capital + alrededores + Villa Mercedes + Villa de Merlo), bajar sus reseñas, analizarlas con Claude y dejar materia prima para armar landings dirigidas.

## Stack

- **Python 3.11+**
- **Google Places API** (descubrimiento + filtro tiene-web)
- **Outscraper** (dump completo de reseñas)
- **Anthropic Claude** (análisis estructurado con prompt caching)
- **SQLite** local (luego se exporta a Supabase)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar las 3 API keys
```

## Uso

```bash
# 1. Descubrimiento: barre la grilla por zona/categoría y guarda negocios
python -m src.pipeline discover --zone san_luis --category peluqueria
python -m src.pipeline discover --all   # todas las zonas y categorias

# 2. Bajar reseñas solo de los que NO tienen web
python -m src.pipeline scrape-reviews --limit 50

# 3. Analizar con Claude
python -m src.pipeline analyze --limit 50

# 4. Ver leads listos
python -m src.pipeline list-leads

# 5. Exportar a CSV o preparar para Supabase
python -m src.pipeline export --format csv
python -m src.pipeline export --format supabase-sql
```

## Estructura

```
src/
  config.py     # zonas, categorias, grilla
  db.py         # SQLite schema + helpers
  discover.py   # Google Places API
  reviews.py    # Outscraper wrapper
  analyze.py    # Claude API
  pipeline.py   # CLI orquestador
  export.py     # CSV + SQL para Supabase
data/
  leads.db      # SQLite local
```

## Costos aproximados

- Places API: ~USD 17 / 1000 detalles
- Outscraper: ~USD 2 / 1000 reseñas
- Claude Sonnet 4.6 con caching: ~USD 0.05 / 100 análisis (cache del system prompt)

Para ~1000 negocios descubiertos, ~300 sin web, análisis completo: estimado USD 25–40.
