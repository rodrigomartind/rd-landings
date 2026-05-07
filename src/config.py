"""Zonas geograficas, categorias y parametros de grilla."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)  # .env > shell env (evita que vars vacias del shell pisen)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / os.getenv("DB_PATH", "data/leads.db")

GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_REVIEWS_PER_PLACE = int(os.getenv("MAX_REVIEWS_PER_PLACE", "80"))

# Generacion de landings
LANDING_MODEL = os.getenv("LANDING_MODEL", "claude-opus-4-7")
LANDING_BRAND = os.getenv("LANDING_BRAND", "Rodrigo Domínguez")
LANDING_WHATSAPP = os.getenv("LANDING_WHATSAPP", "")
LANDING_WHATSAPP_DISPLAY = os.getenv("LANDING_WHATSAPP_DISPLAY", "")
CLIENTS_DIR = ROOT / "clients"


@dataclass(frozen=True)
class Zone:
    """Zona geografica a barrer.

    Bounding box (sw_lat, sw_lng, ne_lat, ne_lng) y tamano de celda en metros.
    Celdas mas chicas = mas precision pero mas calls a la API.
    """

    slug: str
    label: str
    sw_lat: float
    sw_lng: float
    ne_lat: float
    ne_lng: float
    cell_meters: int = 1500


ZONES: dict[str, Zone] = {
    "san_luis": Zone(
        slug="san_luis",
        label="San Luis Capital",
        sw_lat=-33.34,
        sw_lng=-66.40,
        ne_lat=-33.27,
        ne_lng=-66.30,
        cell_meters=1200,
    ),
    "juana_koslay": Zone(
        slug="juana_koslay",
        label="Juana Koslay",
        sw_lat=-33.32,
        sw_lng=-66.27,
        ne_lat=-33.29,
        ne_lng=-66.22,
        cell_meters=1500,
    ),
    "potrero": Zone(
        slug="potrero",
        label="Potrero de los Funes",
        sw_lat=-33.25,
        sw_lng=-66.26,
        ne_lat=-33.21,
        ne_lng=-66.21,
        cell_meters=2000,
    ),
    "la_punta": Zone(
        slug="la_punta",
        label="La Punta",
        sw_lat=-33.21,
        sw_lng=-66.34,
        ne_lat=-33.16,
        ne_lng=-66.27,
        cell_meters=1500,
    ),
    "villa_mercedes": Zone(
        slug="villa_mercedes",
        label="Villa Mercedes",
        sw_lat=-33.71,
        sw_lng=-65.50,
        ne_lat=-33.64,
        ne_lng=-65.41,
        cell_meters=1200,
    ),
    "villa_merlo": Zone(
        slug="villa_merlo",
        label="Villa de Merlo",
        sw_lat=-32.39,
        sw_lng=-65.05,
        ne_lat=-32.31,
        ne_lng=-64.97,
        cell_meters=1500,
    ),
}


# Cada categoria es una query libre que mandamos a Places API (Text Search).
# Las queries en castellano funcionan bien para Argentina.
CATEGORIES: list[str] = [
    # Comida y bebida
    "restaurante",
    "cafe",
    "pizzeria",
    "cerveceria",
    "bar",
    "rotiseria",
    "panaderia",
    "heladeria",
    "pasteleria",
    # Comercio de barrio
    "carniceria",
    "verduleria",
    "kiosco",
    "dietetica",
    "ferreteria",
    "libreria",
    "jugueteria",
    "muebleria",
    "tienda de ropa",
    "boutique",
    "zapateria",
    "joyeria",
    "florista",
    # Servicios
    "peluqueria",
    "barberia",
    "centro de estetica",
    "spa",
    "gimnasio",
    "veterinaria",
    "optica",
    "farmacia",
    "taller mecanico",
    "lavadero de autos",
    "gomeria",
    "lavanderia",
    "tintoreria",
    # Salud
    "consultorio odontologico",
    "consultorio medico",
    "kinesiologia",
    "psicologo",
    "nutricionista",
    # Educacion y cultura
    "escuela privada",
    "jardin de infantes",
    "instituto de ingles",
    "academia de musica",
    "academia de baile",
    "centro cultural",
    # Profesionales
    "estudio contable",
    "estudio juridico",
    "inmobiliaria",
    "imprenta",
    "estudio fotografico",
    # Turismo y eventos
    "hotel",
    "hostel",
    "cabanas",
    "agencia de turismo",
    "salon de fiestas",
    "catering",
]


def cells_for_zone(zone: Zone) -> list[tuple[float, float, int]]:
    """Genera centros (lat, lng, radius_m) de la grilla que cubre la zona.

    Usamos celdas cuadradas; el radius para nearbysearch es la mitad de la
    diagonal, para que circulos vecinos se solapen y no queden huecos.
    """
    # 1 grado de latitud ~ 111_000 m. Longitud depende de la latitud.
    mid_lat = (zone.sw_lat + zone.ne_lat) / 2
    meters_per_deg_lat = 111_000
    meters_per_deg_lng = 111_000 * math.cos(math.radians(mid_lat))

    step_lat = zone.cell_meters / meters_per_deg_lat
    step_lng = zone.cell_meters / meters_per_deg_lng
    radius = int(zone.cell_meters * math.sqrt(2) / 2) + 50  # diagonal/2 + margen

    cells: list[tuple[float, float, int]] = []
    lat = zone.sw_lat + step_lat / 2
    while lat < zone.ne_lat:
        lng = zone.sw_lng + step_lng / 2
        while lng < zone.ne_lng:
            cells.append((lat, lng, radius))
            lng += step_lng
        lat += step_lat
    return cells
