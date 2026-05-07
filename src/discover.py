"""Descubrimiento de negocios via Google Places API.

Estrategia:
1. Para cada zona, generamos una grilla de celdas.
2. Para cada (celda, categoria), corremos places().
3. Para cada place_id nuevo, traemos place() con detalles (incluyendo website).
4. Upsert en SQLite.
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import googlemaps
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from . import db
from .config import CATEGORIES, GOOGLE_API_KEY, ZONES, Zone, cells_for_zone

console = Console()

# Campos que pedimos en place() — esto define el costo (Place Details Pro SKU).
PLACE_FIELDS = [
    "place_id",
    "name",
    "type",
    "formatted_address",
    "geometry/location",
    "international_phone_number",
    "website",
    "rating",
    "user_ratings_total",
    "price_level",
    "business_status",
    "opening_hours",
    "review",  # hasta 5 reviews recientes, sin costo extra (incluido en Pro SKU)
]


def _places_review_to_db(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza una review de Places API al schema de la tabla `reviews`."""
    from datetime import datetime, timezone

    posted_at = None
    ts = raw.get("time")
    if ts:
        posted_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    return {
        "author": raw.get("author_name"),
        "rating": raw.get("rating"),
        "text": raw.get("text"),
        "language": raw.get("language"),
        "posted_at": posted_at,
    }


def get_client() -> googlemaps.Client:
    if not GOOGLE_API_KEY:
        raise RuntimeError("Falta GOOGLE_MAPS_API_KEY en .env")
    return googlemaps.Client(key=GOOGLE_API_KEY, retry_timeout=30)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _places_nearby(
    gmaps: googlemaps.Client,
    location: tuple[float, float],
    radius: int,
    keyword: str,
) -> Iterable[dict[str, Any]]:
    """Generador que pagina sobre todos los resultados de un nearby search."""
    resp = gmaps.places_nearby(location=location, radius=radius, keyword=keyword)
    for r in resp.get("results", []):
        yield r
    next_token = resp.get("next_page_token")
    # Google requiere ~2s antes de usar el next_page_token.
    while next_token:
        time.sleep(2)
        resp = gmaps.places_nearby(page_token=next_token)
        for r in resp.get("results", []):
            yield r
        next_token = resp.get("next_page_token")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _place_details(gmaps: googlemaps.Client, place_id: str) -> dict[str, Any]:
    return gmaps.place(place_id=place_id, fields=PLACE_FIELDS, language="es").get(
        "result", {}
    )


def _to_business(detail: dict[str, Any], category: str, zone_slug: str) -> dict[str, Any]:
    loc = (detail.get("geometry") or {}).get("location") or {}
    hours = (detail.get("opening_hours") or {}).get("weekday_text") or None
    return {
        "place_id": detail.get("place_id"),
        "name": detail.get("name"),
        "category": category,
        "google_types": detail.get("types") or [],
        "zone": zone_slug,
        "address": detail.get("formatted_address"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "phone": detail.get("international_phone_number"),
        "website": detail.get("website"),
        "rating": detail.get("rating"),
        "reviews_count": detail.get("user_ratings_total"),
        "price_level": detail.get("price_level"),
        "business_status": detail.get("business_status"),
        "hours": hours,
    }


def discover_zone_category(
    zone: Zone,
    category: str,
    *,
    skip_known: bool = True,
) -> dict[str, int]:
    """Barre toda una zona para una categoria. Devuelve contadores."""
    gmaps = get_client()
    db.init_db()
    cells = cells_for_zone(zone)
    seen: set[str] = set()
    new_count = 0
    detail_count = 0

    if skip_known:
        with db.connect() as conn:
            seen = {
                row[0]
                for row in conn.execute("SELECT place_id FROM businesses").fetchall()
            }

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total} celdas"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"{zone.label} / {category}", total=len(cells)
        )
        for lat, lng, radius in cells:
            try:
                results = list(_places_nearby(gmaps, (lat, lng), radius, category))
            except Exception as e:  # pragma: no cover
                console.log(f"[red]nearby error en {zone.slug}/{category}: {e}")
                progress.update(task, advance=1)
                continue

            for r in results:
                pid = r.get("place_id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                try:
                    detail = _place_details(gmaps, pid)
                except Exception as e:  # pragma: no cover
                    console.log(f"[yellow]details fallo {pid}: {e}")
                    continue
                if not detail:
                    continue
                detail_count += 1
                biz = _to_business(detail, category, zone.slug)
                raw_reviews = detail.get("reviews") or []
                reviews_normalized = [
                    _places_review_to_db(r)
                    for r in raw_reviews
                    if (r.get("text") or "").strip()
                ]
                with db.connect() as conn:
                    db.upsert_business(conn, biz)
                    if reviews_normalized:
                        db.insert_reviews(
                            conn,
                            biz["place_id"],
                            reviews_normalized,
                            mark_scraped=False,
                        )
                new_count += 1
            progress.update(task, advance=1)

    return {"new": new_count, "details_calls": detail_count, "cells": len(cells)}


def discover_all(
    zones: list[str] | None = None,
    categories: list[str] | None = None,
) -> None:
    selected_zones = [ZONES[z] for z in (zones or list(ZONES))]
    selected_cats = categories or CATEGORIES

    grand_total = 0
    for zone in selected_zones:
        for cat in selected_cats:
            res = discover_zone_category(zone, cat)
            grand_total += res["new"]
            console.log(
                f"[green]{zone.slug}/{cat}: +{res['new']} negocios "
                f"({res['details_calls']} details calls, {res['cells']} celdas)"
            )
    console.log(f"[bold green]Total nuevos: {grand_total}")
